import time
from uuid import uuid4

import pytest

from app.social.files import sign, verify
from app.social.schemas import PublishDecision


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


def test_signed_capability_exact_scope_tenant_expiry():
    key = "a" * 32
    payload = {"tenant": str(uuid4()), "expires": int(time.time()) + 30}
    token = sign(key, "oauth", payload)
    assert verify(key, "oauth", token) == payload
    for purpose, value, secret in (
        ("media", token, key),
        ("oauth", token + "x", key),
        ("oauth", token, "b" * 32),
    ):
        with pytest.raises(PermissionError):
            verify(secret, purpose, value)


@pytest.mark.parametrize(
    "expires", [0, -1, int(time.time()) - 1, int(time.time()) + 7200, "later", True]
)
def test_signed_capability_time_bound(expires):
    token = sign("a" * 32, "media", {"expires": expires})
    with pytest.raises(PermissionError):
        verify("a" * 32, "media", token)


@pytest.mark.parametrize("field", ["reviewed_images", "reviewed_caption", "confirmed_account"])
def test_publish_authorization_requires_all_checks(field):
    value = dict(
        plan_hash="a" * 64,
        decision="AUTHORIZE_PUBLISH",
        reviewed_images=True,
        reviewed_caption=True,
        confirmed_account=True,
        comment=None,
    )
    value[field] = False
    with pytest.raises(ValueError):
        PublishDecision.model_validate(value)
