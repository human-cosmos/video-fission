from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import container
from .api.auth import router as auth_router
from .api.routes import router
from .config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    container.hub.initialize()
    yield
    container.hub.shutdown()


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
