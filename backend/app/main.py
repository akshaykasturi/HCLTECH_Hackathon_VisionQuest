"""FastAPI entry point.

Run from the backend folder:
    .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Swagger UI: http://127.0.0.1:8000/docs

Every response is {ok, data, error} and carries an X-Request-ID header.

  /health        200 whenever the API runs: {api, database, warmup, warmup_detail}
  /health/ready  200 only after every warm-up function has finished and the audit database is up; else 503
  /api/audit/*   verify the audit chain, read saved results, add reviewer feedback (app/common/api.py)

Warm-up: slow things (building the doc search index, loading a model) run once at startup, in the
background, so /health answers at once and /health/ready says when we can really serve. Add them to
WARMUPS, e.g. `from app.common.doc_search import get_index` then `WARMUPS.append(get_index)`.

Environment variables (backend/.env is read at startup; see .env.example):
    CORS_ORIGINS  comma-separated exact origins (default http://127.0.0.1:5173,http://localhost:5173)
    AUDIT_DB      SQLite audit file (default backend/data/audit/audit.db)
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common import audit
from app.common.api import ApiError, audit_router, error_response, is_valid_request_id, ok
from app.common.env import load_dotenv
from app.common.redact import mask
from app.common.trace import optional
from app.tickets.similar import get_ticket_index

logger = logging.getLogger("app.main")
REQUEST_ID_HEADER = "X-Request-ID"
DEFAULT_CORS = "http://127.0.0.1:5173,http://localhost:5173"
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_HTTP_CODES = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
               405: "METHOD_NOT_ALLOWED", 413: "PAYLOAD_TOO_LARGE", 415: "UNSUPPORTED_MEDIA_TYPE",
               429: "TOO_MANY_REQUESTS"}

# Functions run once at startup, in order, in a background thread. Add yours here (see the docstring).
WARMUPS: list[Callable[[], object]] = [get_ticket_index]
# Teammates' warm-ups, added only once their module exists.
WARMUPS += [fn for fn in (optional("app.tickets.classify", "load_classifier"),
                          optional("app.tickets.kb", "get_kb_index")) if fn is not None]


def cors_origins() -> list[str]:
    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", DEFAULT_CORS).split(",") if o.strip()]
    if "*" in origins:
        raise ValueError("CORS_ORIGINS must list exact origins, not '*'")
    return origins


def configure_logging() -> None:
    """Add the current request id to every log record; set a simple format if nothing is configured."""
    if not getattr(logging, "_app_request_id_factory", False):
        base_factory = logging.getLogRecordFactory()

        def factory(*args, **kwargs):
            record = base_factory(*args, **kwargs)
            record.request_id = _request_id.get()
            return record

        logging.setLogRecordFactory(factory)
        logging._app_request_id_factory = True  # type: ignore[attr-defined]
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")


def _validation_message(exc: RequestValidationError) -> str:
    """Field locations and reasons only; the submitted values are never echoed back."""
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body") or "request"
        parts.append(f"{loc}: {err.get('msg', 'invalid value')}")
    return "; ".join(parts) or "The request is not valid."


class Warmup:
    """Runs the warm-up functions once and remembers how it went."""

    def __init__(self, functions: list[Callable[[], object]]) -> None:
        self.functions = list(functions)
        self.state = "warming"  # warming | ready | failed
        self.detail = "starting"

    def run(self) -> None:
        for fn in self.functions:
            name = getattr(fn, "__qualname__", repr(fn))
            self.detail = f"running {name}"
            started = time.perf_counter()
            try:
                fn()
            except Exception as exc:  # keep serving; readiness reports the failure
                self.state = "failed"
                self.detail = mask(f"{name} failed ({exc.__class__.__name__})")
                logger.error("warm-up %s", self.detail)
                return
            logger.info("warm-up %s done in %.0f ms", name, (time.perf_counter() - started) * 1000)
        self.state = "ready"
        self.detail = f"{len(self.functions)} warm-up step(s) done" if self.functions else "nothing to warm up"


def create_app(audit_db_path: Optional[Path] = None, warmups: Optional[list[Callable[[], object]]] = None) -> FastAPI:
    """Build the app. Tests pass a temporary audit_db_path and their own warm-ups."""
    load_dotenv()
    configure_logging()
    warmup = Warmup(WARMUPS if warmups is None else warmups)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            audit.init_db(app.state.audit_db_path)
        except audit.AuditError as exc:  # keep serving; /health reports database down
            logger.error("audit database not available at startup (%s)", exc.__class__.__name__)
        task = asyncio.create_task(asyncio.to_thread(warmup.run))
        app.state.warmup_task = task
        yield
        if not task.done():
            task.cancel()

    app = FastAPI(title="Hackathon API", version="0.1.0", lifespan=lifespan,
                  description="Every response is {ok, data, error}; errors carry {code, message}. "
                              "Each response has an X-Request-ID header.")
    app.state.audit_db_path = audit_db_path or audit.default_db_path()
    app.state.warmup = warmup

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if is_valid_request_id(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        token = _request_id.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:  # last line of defence: no stack trace leaves the server
            logger.error("unhandled error on %s %s (%s)", request.method, request.url.path, exc.__class__.__name__)
            request.state.error_code = "INTERNAL_ERROR"
            response = error_response(500, "INTERNAL_ERROR", "An unexpected error occurred.")
        try:
            response.headers[REQUEST_ID_HEADER] = request_id
            error_code = getattr(request.state, "error_code", None)
            logger.info("%s %s -> %d in %.1f ms%s", request.method, request.url.path, response.status_code,
                        (time.perf_counter() - started) * 1000, f" error={error_code}" if error_code else "")
        finally:
            _request_id.reset(token)
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        request.state.error_code = exc.code
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError):
        request.state.error_code = "VALIDATION_ERROR"
        return error_response(422, "VALIDATION_ERROR", _validation_message(exc))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        request.state.error_code = code
        message = exc.detail if isinstance(exc.detail, str) and exc.status_code < 500 else "Request failed."
        return error_response(exc.status_code, code, message)

    def status() -> dict[str, str]:
        try:
            audit.init_db(app.state.audit_db_path)
            database = "up"
        except audit.AuditError:
            database = "down"
        return {"api": "up", "database": database, "warmup": warmup.state, "warmup_detail": warmup.detail}

    @app.get("/health", tags=["health"], summary="Liveness and component status")
    def health():
        return ok(status())

    @app.get("/health/ready", tags=["health"], summary="Ready to serve: warm-up done and database up")
    def ready(request: Request):
        current = status()
        if current["warmup"] != "ready":
            code = "WARMUP_FAILED" if current["warmup"] == "failed" else "WARMING_UP"
            request.state.error_code = code
            return error_response(503, code, f"Not ready: {current['warmup_detail']}.")
        if current["database"] != "up":
            request.state.error_code = "DATABASE_UNAVAILABLE"
            return error_response(503, "DATABASE_UNAVAILABLE", "The audit database is not available.")
        return ok(current)

    app.include_router(audit_router)
    return app


app = create_app()
