from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.routes import body_as
from app.auth.service import (
    LoginInput,
    SessionInfo,
    SetupInput,
    cookie_name,
    cookie_token,
    establish,
    logout,
    require_same_origin,
    secure_cookie,
    session_info,
)

router = APIRouter(prefix="/v1/auth", tags=["authentication"])


def set_session(response: Response, token: str) -> None:
    response.set_cookie(
        cookie_name(),
        token,
        max_age=8 * 60 * 60,
        httponly=True,
        secure=secure_cookie(),
        samesite="lax",
        path="/",
    )


@router.post("/login", response_model=SessionInfo)
async def login_route(request: Request, response: Response):
    require_same_origin(request)
    data = await body_as(request, LoginInput)
    token, info = await run_in_threadpool(
        establish, "login", data.email, data.password.get_secret_value()
    )
    set_session(response, token)
    return info


@router.post("/setup", response_model=SessionInfo)
async def setup_route(request: Request, response: Response):
    require_same_origin(request)
    data = await body_as(request, SetupInput)
    token, info = await run_in_threadpool(
        establish, "setup", data.token.get_secret_value(), data.password.get_secret_value()
    )
    set_session(response, token)
    return info


@router.get("/session", response_model=SessionInfo)
def current_session(request: Request):
    return session_info(cookie_token(request))


@router.post("/logout")
def logout_route(request: Request, response: Response):
    require_same_origin(request)
    # An expired or already-revoked well-formed cookie may always be cleared.
    token = request.cookies.get(cookie_name())
    if token:
        logout(token)
    response.delete_cookie(
        cookie_name(),
        httponly=True,
        secure=secure_cookie(),
        samesite="lax",
        path="/",
    )
    return {"status": "signed_out"}
