"""Browser credentials remain opaque; PostgreSQL owns identity and session validity."""

import re
from datetime import datetime
from functools import lru_cache
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from sqlalchemy import create_engine, text
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.config import get_settings
from app.db.repository import engine


@lru_cache
def login_engine():
    # Expensive unauthenticated password verification cannot occupy the business
    # connection pool. Limits persist in PostgreSQL across process replicas.
    return create_engine(
        get_settings().database_url.get_secret_value(),
        hide_parameters=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=2,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def _timeouts(connection) -> None:
    connection.execute(text("SET LOCAL lock_timeout='3s'"))
    connection.execute(text("SET LOCAL statement_timeout='5s'"))


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(
        r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}", value
    ):
        raise ValueError("A valid email address is required")
    return value


class PasswordInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    password: SecretStr

    @field_validator("password")
    @classmethod
    def bounded_password(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if "\x00" in raw or len(raw) < 15 or len(raw.encode("utf-8")) > 72:
            raise ValueError("Use at least 15 characters, at most 72 UTF-8 bytes, without NUL")
        return value


class LoginInput(PasswordInput):
    email: str

    @field_validator("email")
    @classmethod
    def email_address(cls, value: str) -> str:
        return normalize_email(value)


class SetupInput(PasswordInput):
    token: SecretStr

    @field_validator("token")
    @classmethod
    def bounded_token(cls, value: SecretStr) -> SecretStr:
        if not re.fullmatch(r"[0-9a-f]{64}", value.get_secret_value()):
            raise ValueError("Invalid setup token")
        return value


class SessionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    tenant_id: UUID
    tenant_name: str
    roles: list[Literal["OPERATOR", "APPROVER", "ADMIN"]]
    expires_at: datetime


def cookie_name() -> str:
    return "__Host-mediaos_session" if secure_cookie() else "mediaos_session"


def secure_cookie() -> bool:
    return get_settings().auth_public_origin.startswith("https://")


def require_same_origin(request: Request) -> None:
    if request.headers.get("origin") != get_settings().auth_public_origin:
        raise HTTPException(403, "Same-origin request required")
    if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
        raise HTTPException(403, "Same-origin request required")


def cookie_token(request: Request) -> str:
    value = request.cookies.get(cookie_name(), "")
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise HTTPException(401, "Sign in required")
    return value


def session_info(token: str) -> SessionInfo:
    with engine().begin() as connection:
        _timeouts(connection)
        result = connection.execute(
            text("SELECT browser_session_info(:token)"), {"token": token}
        ).scalar_one()
    if result is None:
        raise HTTPException(401, "Session expired or signed out")
    return SessionInfo.model_validate(result)


def establish(
    action: Literal["login", "setup"], identifier: str, password: str
) -> tuple[str, SessionInfo]:
    statement = {
        "login": "SELECT browser_login(:identifier,:password)",
        "setup": "SELECT browser_setup(:identifier,:password)",
    }[action]
    # Commit all outcomes before raising HTTP errors, preserving rate limits/audit.
    try:
        with login_engine().begin() as connection:
            _timeouts(connection)
            result = connection.execute(
                text(statement), {"identifier": identifier, "password": password}
            ).scalar_one()
    except PoolTimeout:
        raise HTTPException(
            503, "Sign-in is busy. Try again shortly.", headers={"Retry-After": "3"}
        ) from None
    if result["status"] == "THROTTLED":
        raise HTTPException(
            429,
            "Too many attempts. Try again later.",
            headers={"Retry-After": str(result["retry_after"])},
        )
    if result["status"] != "SUCCESS":
        raise HTTPException(401, "Invalid sign-in details or expired setup link")
    return result["token"], SessionInfo.model_validate(result["session"])


def logout(token: str) -> None:
    with engine().begin() as connection:
        _timeouts(connection)
        connection.execute(text("SELECT browser_logout(:token)"), {"token": token})
