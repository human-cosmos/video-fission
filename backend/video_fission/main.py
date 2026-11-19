from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import container
from .api.auth import router as auth_router
from .api.routes import router
from .config import settings
from .logging_utils import configure_logging, log_context, logging_status, safe_request_id

configure_logging(Path(__file__).resolve().parents[2], force_file=True)
logger = logging.getLogger(__name__)


def _is_expected_client_disconnect(context: dict[str, object]) -> bool:
    exc = context.get("exception")
    if not isinstance(exc, ConnectionResetError):
        return False
    error_code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
    if error_code != 10054:
        return False
    return "_call_connection_lost" in str(context.get("handle") or "")


def _install_asyncio_exception_handler() -> None:
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def handle_exception(_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        if _is_expected_client_disconnect(context):
            logger.debug("asyncio.client_disconnect_ignored")
            return
        if previous is not None:
            previous(_loop, context)
        else:
            _loop.default_exception_handler(context)

    loop.set_exception_handler(handle_exception)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _install_asyncio_exception_handler()
    logger.info("application.starting log_file=%s", logging_status().get("path") or "-")
    container.hub.initialize()
    yield
    container.hub.shutdown()
    logger.info("application.stopped")


app = FastAPI(
    title="Video Fission",
    version="0.1.0",
    description="Standalone Codex conversation hub",
    lifespan=lifespan,
)
allowed_origins = sorted(
    {
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
        "http://127.0.0.1:4173",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:4173",
        f"http://{settings.host}:{settings.port}",
        *settings.allowed_origins,
    }
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _request_scope(path: str) -> tuple[str, str]:
    conversation_id = "-"
    production_id = "-"
    parts = [part for part in path.split("/") if part]
    for index, part in enumerate(parts):
        if index + 1 >= len(parts):
            continue
        if part == "conversations":
            conversation_id = parts[index + 1]
        elif part == "productions" and parts[index + 1].startswith("prd_"):
            production_id = parts[index + 1]
    return conversation_id, production_id


@app.middleware("http")
async def request_logging(request: Request, call_next):
    if not request.url.path.startswith("/api"):
        return await call_next(request)
    request_id = safe_request_id(request.headers.get("x-request-id")) or f"req_{uuid.uuid4().hex[:12]}"
    conversation_id, production_id = _request_scope(request.url.path)
    started = time.perf_counter()
    client = request.client.host if request.client else "-"
    with log_context(
        request_id=request_id,
        conversation_id=conversation_id,
        production_id=production_id,
    ):
        logger.info(
            "api.request_started method=%s path=%s client=%s",
            request.method,
            request.url.path,
            client,
        )
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "api.request_failed method=%s path=%s elapsed_ms=%.1f",
                request.method,
                request.url.path,
                (time.perf_counter() - started) * 1000,
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        log = logger.warning if response.status_code >= 400 else logger.info
        log(
            "api.request_completed method=%s path=%s status=%s elapsed_ms=%.1f bytes=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            response.headers.get("content-length", "-"),
        )
        return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path.startswith("/api"):
        origin = request.headers.get("origin", "").rstrip("/")
        request_origin = str(request.base_url).rstrip("/")
        if origin and origin not in allowed_origins and origin != request_origin:
            return JSONResponse(
                {"detail": "请求来源不受信任"},
                status_code=403,
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                },
            )
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    if request.url.path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "no-store")
    if not request.url.path.startswith(("/api", "/docs", "/openapi")):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'",
        )
    return response


app.include_router(auth_router)
app.include_router(router)


if settings.frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
else:

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def development_root() -> str:
        return """
        <!doctype html><html><head><meta charset="utf-8"><title>Video Fission</title></head>
        <body style="font-family: ui-sans-serif; padding: 48px; background: #10201d; color: #f4f0e4">
          <p>Video Fission API is running.</p>
          <p>Start the frontend with <code>npm run dev</code> in <code>frontend/</code>.</p>
        </body></html>
        """


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "video_fission.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
