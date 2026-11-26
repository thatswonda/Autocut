"""FastAPI application."""

from __future__ import annotations

import base64
import logging
import secrets
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from autoedit_pipeline import __version__ as pipeline_version
from autoedit_pipeline.media import require_binaries
from autoedit_motion.storyboard import ai_available
from autoedit_pipeline.plans import billing_enabled

from . import __version__
from .db import create_all, get_engine
from .deps import CurrentUser, DbSession
from .logging import configure_logging
from .queue import ping
from .routes import media, projects, uploads
from .schemas import ApiError, ErrorEnvelope, MeOut
from .services import limits_for, limits_out, projects_this_month
from .settings import get_settings

log = logging.getLogger("autoedit.api")


def _daily_purge() -> None:
    """Single-server deployments have no scheduler: apply the retention policy once a day."""
    from .jobs import purge_expired

    while True:
        try:
            counts = purge_expired()
            if any(counts.values()):
                log.info("retention purge", extra=counts)
        except Exception:
            log.exception("retention purge failed")
        time.sleep(24 * 3600)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    configure_logging()
    settings = get_settings()
    require_binaries()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite"):
        create_all()  # Postgres is migrated by Alembic before the app starts
    if settings.queue_mode == "process":
        from .jobs import unfinished_jobs
        from .queue import enqueue

        pending = unfinished_jobs()
        for kind, obj_id in pending:
            enqueue(kind, obj_id)
        if pending:
            log.info("resumed unfinished jobs", extra={"count": len(pending)})
        threading.Thread(target=_daily_purge, name="purge", daemon=True).start()
    log.info("api started", extra={"env": settings.env, "queue": settings.queue_mode})
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AutoEdit API",
        version=__version__,
        description="AI video editor and motion graphics creator. Every AI decision is an editable document.",
        lifespan=lifespan,
        responses={
            400: {"model": ErrorEnvelope},
            404: {"model": ErrorEnvelope},
            422: {"model": ErrorEnvelope},
        },
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Upload-Offset", "Upload-Length", "X-Request-Id"],
    )

    if settings.access_password:
        expected = settings.access_password

        @app.middleware("http")
        async def password_gate(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            if request.url.path == "/healthz":
                return await call_next(request)
            header = request.headers.get("Authorization", "")
            if header.startswith("Basic "):
                try:
                    _user, _, given = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                except (ValueError, UnicodeDecodeError):
                    given = ""
                if secrets.compare_digest(given.encode(), expected.encode()):
                    return await call_next(request)
            return Response(
                "Password required",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Autocut", charset="UTF-8"'},
            )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
        t0 = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        log.info(
            "request",
            extra={
                "request_id": rid,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            },
        )
        return response

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError) -> JSONResponse:
        body = {"error": {"code": exc.code, "message": exc.message, "details": exc.details}}
        return JSONResponse(status_code=exc.status, content=body)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg", "")} for e in exc.errors()]
        body = {
            "error": {
                "code": "invalid_request",
                "message": "Request validation failed",
                "details": {"errors": errors},
            }
        }
        return JSONResponse(status_code=422, content=body)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "pipeline": pipeline_version}

    @app.get("/readyz", tags=["ops"])
    def readyz() -> JSONResponse:
        checks: dict[str, bool] = {}
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception:
            checks["database"] = False
        checks["queue"] = ping()
        ok = all(checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={"ready": ok, "checks": checks})

    @app.get("/v1/me", response_model=MeOut, tags=["account"])
    def me(db: DbSession, user: CurrentUser) -> MeOut:
        limits = limits_for(user)
        return MeOut(
            id=user.id,
            plan=limits.name,
            billing_enabled=billing_enabled(),
            ai_enabled=ai_available(),
            limits=limits_out(limits),
            projects_this_month=projects_this_month(db, user),
        )

    app.include_router(uploads.router)
    app.include_router(projects.router)
    app.include_router(media.router)
    # The web app (interim test UI until the M9 Next.js app) is served last so API routes win.
    app.mount("/", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="web")
    return app


app = create_app()
