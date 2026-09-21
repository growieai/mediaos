import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.ai.policy import TextAIPolicy


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


def policy():
    now = datetime.now(UTC)
    return {
        "schema_version": 1,
        "enabled": True,
        "model": "administrator-selected-model",
        "max_input_bytes": 65536,
        "max_output_tokens": 1024,
        "max_calls_per_run": 2,
        "max_calls_per_day": 100,
        "input_usd_per_million_tokens": "1",
        "output_usd_per_million_tokens": "2",
        "per_run_usd": "1",
        "per_day_usd": "10",
        "price_reference": "test rate card only",
        "price_checked_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
    }


def test_policy_round_trip_without_implicit_model_or_prices():
    expected = policy()
    parsed = TextAIPolicy.model_validate_json(json.dumps(expected), strict=True)
    assert TextAIPolicy.model_validate_json(parsed.model_dump_json()) == parsed
    for key in ("model", "per_run_usd", "price_reference", "input_usd_per_million_tokens"):
        with pytest.raises(ValidationError):
            TextAIPolicy.model_validate_json(
                json.dumps({k: v for k, v in expected.items() if k != key})
            )


@pytest.mark.parametrize(
    "key,value",
    [
        ("enabled", "true"),
        ("model", "untrusted\nmodel"),
        ("per_day_usd", 1),
        ("per_run_usd", "0"),
        ("input_usd_per_million_tokens", "-1"),
        ("output_usd_per_million_tokens", "0.0000001"),
        ("max_calls_per_run", 4),
        ("max_output_tokens", 8193),
        ("max_input_bytes", 131073),
        ("price_checked_at", "2026-01-01T00:00:00"),
        ("expires_at", "2099-01-01T00:00:00Z"),
    ],
)
def test_invalid_policy_is_rejected(key, value):
    data = policy()
    data[key] = value
    with pytest.raises(ValidationError):
        TextAIPolicy.model_validate_json(json.dumps(data), strict=True)
