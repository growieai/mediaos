import hashlib
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from conftest import headers, request_data
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.auth.service import cookie_name, login_engine
from app.auth_admin import issue_setup, revoke_account
from app.config import get_settings
from app.db.repository import engine, transaction

PASSWORD = "a unique testing passphrase!"
ORIGIN = {"Origin": "http://127.0.0.1:3000"}


@pytest.fixture
def account(database, identities):
    with database.begin() as connection:
        # Test isolation only: production counters remain persisted across requests.
        connection.execute(text("DELETE FROM private.login_throttles"))
        info = issue_setup(
            connection,
            email=f"user-{uuid4()}@example.test",
            tenant_id=UUID(identities[0]["tenant_id"]),
            name="Invited operator",
            roles=["OPERATOR"],
        )
    return info


def setup(client, account, **kwargs):
    return client.post(
        "/v1/auth/setup",
        headers=ORIGIN,
        json={"token": account["setup_token"], "password": PASSWORD, **kwargs},
    )


def login(client, account, password=PASSWORD, headers=ORIGIN):
    return client.post(
        "/v1/auth/login", headers=headers, json={"email": account["email"], "password": password}
    )


def test_setup_cookie_session_and_existing_tenant_context(client, account, identities, database):
    result = setup(client, account)
    assert result.status_code == 200, result.text
    assert set(result.json()) == {"email", "tenant_id", "tenant_name", "roles", "expires_at"}
    assert result.json()["roles"] == ["OPERATOR"]
    cookie = result.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Domain=" not in cookie and "Max-Age=28800" in cookie
    assert client.get("/v1/auth/session").json() == result.json()
    assert client.get("/v1/context").json()["tenant_id"] == identities[0]["tenant_id"]
    token = client.cookies.get(cookie_name())
    assert token not in result.text and account["setup_token"] not in result.text
    with database.connect() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM private.user_logins WHERE email=:email"),
                {"email": account["email"]},
            )
            .mappings()
            .one()
        )
        assert row["password_hash"].startswith("$2a$12$") and PASSWORD not in row["password_hash"]
        assert (
            connection.execute(
                text("SELECT count(*) FROM private.browser_sessions WHERE token_hash=:hash"),
                {"hash": hashlib.sha256(token.encode()).hexdigest()},
            ).scalar_one()
            == 1
        )


def test_cookie_business_requests_and_legacy_bearer_coexist(client, account, identities):
    assert setup(client, account).status_code == 200
    result = client.post("/v1/workflow-runs", headers=ORIGIN, json=request_data(identities[0]))
    assert result.status_code == 200, result.text
    assert (
        client.get("/v1/context", headers=headers(identities[1])).json()["tenant_id"]
        == identities[1]["tenant_id"]
    )


def test_missing_identity_is_401_before_cookie_csrf_validation(client, identities):
    assert client.get("/v1/context").status_code == 401
    assert client.post("/v1/workflow-runs", json=request_data(identities[0])).status_code == 401


def test_cookie_cannot_select_other_tenant(client, account, identities):
    assert setup(client, account).status_code == 200
    assert (
        client.get("/v1/context", headers={"X-Tenant-ID": identities[1]["tenant_id"]}).status_code
        == 403
    )
    with (
        pytest.raises(DBAPIError),
        transaction(UUID(identities[1]["tenant_id"]), client.cookies.get(cookie_name())),
    ):
        pass


@pytest.mark.parametrize(
    "origin", [None, "https://evil.example", "http://127.0.0.1:3000.evil.example", "null"]
)
def test_auth_and_cookie_writes_require_exact_origin(client, account, identities, origin):
    h = {} if origin is None else {"Origin": origin}
    assert login(client, account, headers=h).status_code == 403
    assert setup(client, account).status_code == 200
    assert (
        client.post("/v1/workflow-runs", headers=h, json=request_data(identities[0])).status_code
        == 403
    )
    assert client.post("/v1/auth/logout", headers=h).status_code == 403


def test_cross_site_fetch_metadata_rejected_even_with_claimed_origin(client, account):
    assert (
        login(client, account, headers={**ORIGIN, "Sec-Fetch-Site": "cross-site"}).status_code
        == 403
    )


def test_invalid_credentials_are_generic_and_rate_limits_commit(client, account, database):
    assert setup(client, account).status_code == 200
    client.cookies.clear()
    for _ in range(10):
        result = login(client, account, "a different wrong passphrase")
        assert result.status_code == 401
    assert login(client, account).status_code == 429
    with database.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM private.auth_audit WHERE event='LOGIN' AND outcome='THROTTLED'"
                )
            ).scalar_one()
            > 0
        )
    unknown = login(
        client, {"email": f"absent-{uuid4()}@example.test"}, "a different wrong passphrase"
    )
    assert unknown.status_code == 401 and unknown.json() == result.json()


def test_login_rotates_session_and_logout_invalidates_old_cookie(client, account):
    assert setup(client, account).status_code == 200
    original = client.cookies.get(cookie_name())
    assert client.post("/v1/auth/logout", headers=ORIGIN).status_code == 200
    assert client.get("/v1/auth/session").status_code == 401
    client.cookies.set(cookie_name(), original)
    assert client.get("/v1/context").status_code == 401
    client.cookies.clear()
    assert login(client, account).status_code == 200
    assert client.cookies.get(cookie_name()) != original


def test_setup_link_is_single_use_and_does_not_accept_arbitrary_role(client, account):
    assert setup(client, account, roles=["ADMIN"]).status_code == 422
    assert setup(client, account).status_code == 200
    assert setup(client, account).status_code == 401


def test_expired_setup_cannot_set_password(client, account, database):
    with database.begin() as connection:
        connection.execute(
            text(
                "UPDATE private.login_setups SET created_at=now()-interval '2 days',expires_at=now()-interval '1 day' WHERE token_hash=:hash"
            ),
            {"hash": hashlib.sha256(account["setup_token"].encode()).hexdigest()},
        )
    assert setup(client, account).status_code == 401


@pytest.mark.parametrize("change", ["expire", "generation", "disable", "membership"])
def test_session_rechecks_expiry_generation_identity_and_membership(
    client, account, database, change
):
    assert setup(client, account).status_code == 200
    token = client.cookies.get(cookie_name())
    with database.begin() as connection:
        principal = connection.execute(
            text("SELECT principal_id FROM private.user_logins WHERE email=:email"),
            {"email": account["email"]},
        ).scalar_one()
        if change == "expire":
            connection.execute(
                text(
                    "UPDATE private.browser_sessions SET created_at=now()-interval '2 days',expires_at=now()-interval '1 day' WHERE principal_id=:id"
                ),
                {"id": principal},
            )
        elif change == "generation":
            connection.execute(
                text(
                    "UPDATE private.user_logins SET generation=generation+1 WHERE principal_id=:id"
                ),
                {"id": principal},
            )
        elif change == "disable":
            connection.execute(
                text("UPDATE public.principals SET active=false WHERE id=:id"), {"id": principal}
            )
        else:
            # Membership deletion itself is prevented by ownership FKs; changing
            # current permissions must be reflected immediately instead.
            connection.execute(
                text(
                    "UPDATE public.tenant_memberships SET roles=ARRAY['APPROVER'] WHERE principal_id=:id"
                ),
                {"id": principal},
            )
    if change == "membership":
        assert client.get("/v1/auth/session").json()["roles"] == ["APPROVER"]
    else:
        assert client.get("/v1/context").status_code == 401
        with pytest.raises(DBAPIError), transaction(UUID(account["tenant_id"]), token):
            pass


def test_admin_reset_revokes_all_sessions_and_old_password(client, account, database):
    assert setup(client, account).status_code == 200
    with database.begin() as connection:
        fresh = issue_setup(connection, email=account["email"], reset=True)
    assert client.get("/v1/auth/session").status_code == 401
    assert login(client, account).status_code == 401
    assert setup(client, fresh, password="a changed testing passphrase").status_code == 200
    assert login(client, account).status_code == 401
    assert login(client, account, "a changed testing passphrase").status_code == 200


def test_admin_revoke_disables_login_and_sessions(client, account, database):
    assert setup(client, account).status_code == 200
    with database.begin() as connection:
        revoke_account(connection, account["email"])
    assert client.get("/v1/context").status_code == 401
    assert login(client, account).status_code == 401
    with pytest.raises(ValueError), database.begin() as connection:
        issue_setup(connection, email=account["email"], reset=True)


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM private.user_logins",
        "SELECT * FROM private.login_setups",
        "SELECT * FROM private.browser_sessions",
        "SELECT * FROM private.auth_audit",
        "INSERT INTO private.browser_sessions(token_hash) VALUES('fake')",
        "UPDATE private.user_logins SET password_hash=NULL",
        "SELECT private.new_browser_session(gen_random_uuid())",
        "SELECT private.consume_login_limit('GLOBAL',1000000,1)",
    ],
)
def test_runtime_cannot_inspect_credentials_or_mint_sessions(statement):
    with pytest.raises(DBAPIError), engine().begin() as connection:
        connection.execute(text(statement))


def test_concurrent_setup_only_one_committed_session(account, database):
    def attempt():
        with engine().begin() as connection:
            return connection.execute(
                text("SELECT browser_setup(:token,:password)"),
                {"token": account["setup_token"], "password": PASSWORD},
            ).scalar_one()["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == ["DENIED", "SUCCESS"]
    with database.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM private.browser_sessions s JOIN private.user_logins l USING(principal_id) WHERE l.email=:email"
                ),
                {"email": account["email"]},
            ).scalar_one()
            == 1
        )


def test_session_roles_enforce_operator_cannot_approve(client, account, identities):
    assert setup(client, account).status_code == 200
    with transaction(UUID(account["tenant_id"]), client.cookies.get(cookie_name())) as repo:
        repo.require("OPERATOR")
        with pytest.raises(PermissionError):
            repo.require("APPROVER")


def test_production_cookie_security_flags(client, account, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_public_origin", "https://studio.example.test")
    result = client.post(
        "/v1/auth/setup",
        headers={"Origin": "https://studio.example.test"},
        json={"token": account["setup_token"], "password": PASSWORD},
    )
    assert result.status_code == 200
    cookie = result.headers["set-cookie"]
    assert (
        cookie.startswith("__Host-mediaos_session=") and "Secure" in cookie and "HttpOnly" in cookie
    )
    assert "Domain=" not in cookie


def test_password_restrictions_are_also_database_enforced(account):
    for password in ("short", "x" * 73, "😀" * 19):
        with engine().begin() as connection:
            result = connection.execute(
                text("SELECT browser_setup(:token,:password)"),
                {"token": account["setup_token"], "password": password},
            ).scalar_one()
        assert result["status"] == "DENIED"


def test_valid_login_email_normalizes_and_no_password_in_response(client, account):
    assert setup(client, account).status_code == 200
    upper = {**account, "email": " " + account["email"].upper() + " "}
    result = login(client, upper)
    assert result.status_code == 200
    assert result.json()["email"] == account["email"] and PASSWORD not in result.text


def test_unknown_setup_has_generic_failure(client, account):
    assert setup(client, {**account, "setup_token": "a" * 64}).status_code == 401


def test_global_limit_precedes_new_arbitrary_email_buckets(client, account, database):
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO private.login_throttles VALUES('GLOBAL',120,now()+interval '1 minute')"
            )
        )
    assert login(client, account).status_code == 429
    with database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM private.login_throttles WHERE bucket LIKE 'EMAIL:%'")
            ).scalar_one()
            == 0
        )


def test_exhausted_login_pool_does_not_block_authenticated_business_reads(client, account):
    assert setup(client, account).status_code == 200
    with login_engine().connect(), login_engine().connect():
        assert client.get("/v1/context").status_code == 200
        result = login(client, account)
        assert result.status_code == 503 and result.headers["Retry-After"] == "3"
        assert client.get("/v1/context").status_code == 200


def test_already_throttled_requests_do_not_amplify_audit_storage(client, account, database):
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO private.login_throttles VALUES('SETUP_GLOBAL',30,now()+interval '1 minute')"
            )
        )
        before = connection.execute(
            text(
                "SELECT count(*) FROM private.auth_audit WHERE event='SETUP' AND outcome='THROTTLED'"
            )
        ).scalar_one()
    for _ in range(3):
        assert setup(client, account).status_code == 429
    with database.connect() as connection:
        after = connection.execute(
            text(
                "SELECT count(*) FROM private.auth_audit WHERE event='SETUP' AND outcome='THROTTLED'"
            )
        ).scalar_one()
    assert after == before + 1
