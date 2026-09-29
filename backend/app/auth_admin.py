"""Explicit maintenance-only human account provisioning. Never imported by the API."""

import argparse
import hashlib
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from app.auth.service import normalize_email
from app.config import REPO_ROOT


def issue_setup(
    connection: Connection,
    *,
    email: str,
    tenant_id: UUID | None = None,
    name: str | None = None,
    roles: list[str] | None = None,
    reset: bool = False,
) -> dict:
    email = normalize_email(email)
    if reset:
        row = (
            connection.execute(
                text(
                    "SELECT l.*,p.active FROM private.user_logins l JOIN public.principals p ON p.id=l.principal_id WHERE email=:email FOR UPDATE OF l"
                ),
                {"email": email},
            )
            .mappings()
            .one()
        )
        if not row["active"]:
            raise ValueError("Account is revoked; reset does not reactivate identities")
        principal_id, tenant_id = row["principal_id"], row["tenant_id"]
        connection.execute(
            text(
                "UPDATE private.user_logins SET generation=generation+1,password_hash=NULL WHERE principal_id=:id"
            ),
            {"id": principal_id},
        )
        connection.execute(
            text(
                "UPDATE private.browser_sessions SET revoked_at=now() WHERE principal_id=:id AND revoked_at IS NULL"
            ),
            {"id": principal_id},
        )
        connection.execute(
            text(
                "UPDATE private.login_setups SET consumed_at=now() WHERE principal_id=:id AND consumed_at IS NULL"
            ),
            {"id": principal_id},
        )
    else:
        if (
            tenant_id is None
            or not name
            or not 1 <= len(name) <= 120
            or not roles
            or not set(roles) <= {"OPERATOR", "APPROVER", "ADMIN"}
        ):
            raise ValueError("A tenant, name and explicit human role are required")
        principal_id = uuid4()
        # Human logins never disclose or use a timeless legacy bearer credential.
        connection.execute(
            text("INSERT INTO public.principals(id,name,token_hash) VALUES(:id,:name,:hash)"),
            {
                "id": principal_id,
                "name": name,
                "hash": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            },
        )
        connection.execute(
            text(
                "INSERT INTO public.tenant_memberships(tenant_id,principal_id,roles) VALUES(:tenant,:id,:roles)"
            ),
            {"tenant": tenant_id, "id": principal_id, "roles": sorted(set(roles))},
        )
        connection.execute(
            text(
                "INSERT INTO private.user_logins(principal_id,tenant_id,email) VALUES(:id,:tenant,:email)"
            ),
            {"id": principal_id, "tenant": tenant_id, "email": email},
        )
    setup_token = secrets.token_hex(32)
    expires_at = datetime.now(UTC) + timedelta(hours=24)
    connection.execute(
        text(
            "INSERT INTO private.login_setups(token_hash,principal_id,expires_at) VALUES(:hash,:id,:expires)"
        ),
        {
            "hash": hashlib.sha256(setup_token.encode()).hexdigest(),
            "id": principal_id,
            "expires": expires_at,
        },
    )
    connection.execute(
        text(
            "INSERT INTO private.auth_audit(event,outcome,principal_id,tenant_id) VALUES(:event,'SUCCESS',:id,:tenant)"
        ),
        {"event": "RESET" if reset else "INVITE", "id": principal_id, "tenant": tenant_id},
    )
    return {
        "email": email,
        "tenant_id": str(tenant_id),
        "expires_at": expires_at.isoformat(),
        "setup_token": setup_token,
    }


def revoke_account(connection: Connection, email: str) -> None:
    row = (
        connection.execute(
            text("SELECT * FROM private.user_logins WHERE email=:email FOR UPDATE"),
            {"email": normalize_email(email)},
        )
        .mappings()
        .one()
    )
    connection.execute(
        text("UPDATE public.principals SET active=false WHERE id=:id"), {"id": row["principal_id"]}
    )
    connection.execute(
        text("UPDATE private.user_logins SET generation=generation+1 WHERE principal_id=:id"),
        {"id": row["principal_id"]},
    )
    connection.execute(
        text(
            "UPDATE private.browser_sessions SET revoked_at=now() WHERE principal_id=:id AND revoked_at IS NULL"
        ),
        {"id": row["principal_id"]},
    )
    connection.execute(
        text(
            "UPDATE private.login_setups SET consumed_at=now() WHERE principal_id=:id AND consumed_at IS NULL"
        ),
        {"id": row["principal_id"]},
    )
    connection.execute(
        text(
            "INSERT INTO private.auth_audit(event,outcome,principal_id,tenant_id) VALUES('REVOKE','SUCCESS',:id,:tenant)"
        ),
        {"id": row["principal_id"], "tenant": row["tenant_id"]},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("invite", "reset", "revoke"):
        command = commands.add_parser(action)
        command.add_argument("--email", required=True)
        if action != "revoke":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument(
                "--origin", required=True, help="Exact console origin, without path"
            )
        if action == "invite":
            command.add_argument("--tenant", type=UUID, required=True)
            command.add_argument("--name", required=True)
            command.add_argument(
                "--role", action="append", choices=["OPERATOR", "APPROVER", "ADMIN"], required=True
            )
    args = parser.parse_args()
    load_dotenv(REPO_ROOT / ".env")
    if args.action != "revoke":
        origin = urlsplit(args.origin)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.path
            or origin.query
            or origin.fragment
            or (
                origin.scheme == "http" and origin.hostname not in {"127.0.0.1", "localhost", "::1"}
            )
        ):
            parser.error("--origin must be an exact HTTP(S) origin without a path")
        if args.output.exists():
            parser.error("--output already exists; choose a new private file")
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    database = create_engine(os.environ["MIGRATION_DATABASE_URL"], hide_parameters=True)
    try:
        with database.begin() as connection:
            if args.action == "revoke":
                revoke_account(connection, args.email)
            else:
                info = issue_setup(
                    connection,
                    email=args.email,
                    tenant_id=getattr(args, "tenant", None),
                    name=getattr(args, "name", None),
                    roles=getattr(args, "role", None),
                    reset=args.action == "reset",
                )
                info["setup_url"] = args.origin + "/#setup=" + info.pop("setup_token")
                fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump(info, output, indent=2)
                    output.write("\n")
        print(
            "Account revoked."
            if args.action == "revoke"
            else "Private setup instructions saved. No email was sent."
        )
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
