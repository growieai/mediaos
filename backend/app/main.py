import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app.api.routes import router
from app.config import get_settings
from app.delivery.routes import router as delivery_router
from app.intelligence.http import SourcePolicyError, SourceUnavailable
from app.intelligence.routes import router as intelligence_router
from app.metrics.routes import router as metrics_router
from app.observability import configure_logging, request_id
from app.rendering.routes import router as rendering_router
from app.services.workflows import ConflictError, SkillFailed

configure_logging()


@asynccontextmanager
async def lifespan(app):
    get_settings()
    yield


app = FastAPI(title="Growie Media OS", version="0.2.0", lifespan=lifespan)


class RequestBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        body = bytearray()
        maximum = get_settings().max_request_bytes
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > maximum:
                response = JSONResponse({"detail": "Request body too large"}, status_code=413)
                return await response(scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body"):
                break
        correlation = uuid4()
        scope.setdefault("state", {})["correlation_id"] = correlation
        token = request_id.set(str(correlation))
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def with_headers(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append(
                    (b"x-request-id", str(correlation).encode())
                )
                message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        try:
            await self.app(scope, bounded_receive, with_headers)
        finally:
            request_id.reset(token)


app.add_middleware(RequestBoundary)


@app.exception_handler(DBAPIError)
async def database_error(request: Request, exc: DBAPIError):
    code = str(getattr(exc.orig, "sqlstate", "unknown"))
    logging.getLogger("mediaos").warning(
        "database_rejected", extra={"error_category": type(exc.orig).__name__ + ":" + code}
    )
    status = {
        "28000": 401,
        "42501": 403,
        "P0002": 404,
        "23503": 409,
        "23505": 409,
        "23514": 409,
    }.get(code, 503)
    return JSONResponse(
        {
            "detail": {
                401: "Invalid identity or tenant",
                403: "Permission denied",
                404: "Not found",
                409: "Database invariant rejected the operation",
                503: "Database unavailable or operation failed",
            }[status]
        },
        status_code=status,
    )


@app.exception_handler(LookupError)
async def not_found(request: Request, exc: LookupError):
    return JSONResponse({"detail": "Not found"}, status_code=404)


@app.exception_handler(PermissionError)
async def forbidden(request: Request, exc: PermissionError):
    return JSONResponse({"detail": "Permission denied"}, status_code=403)


@app.exception_handler(ConflictError)
async def conflict(request: Request, exc: ConflictError):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(SkillFailed)
async def failed(request: Request, exc: SkillFailed):
    return JSONResponse(
        {"detail": "Workflow execution failed; inspect persisted attempts"}, status_code=409
    )


app.include_router(router)
app.include_router(intelligence_router)
app.include_router(rendering_router)
app.include_router(delivery_router)
app.include_router(metrics_router)


@app.exception_handler(SourceUnavailable)
async def source_unavailable(request: Request, exc: SourceUnavailable):
    return JSONResponse(
        {"detail": "Official source unavailable; inspect persisted ingestion attempts"},
        status_code=503,
    )


@app.exception_handler(SourcePolicyError)
async def source_policy(request: Request, exc: SourcePolicyError):
    return JSONResponse({"detail": "Source access policy rejected the request"}, status_code=409)
