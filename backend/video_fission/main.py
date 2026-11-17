from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import settings
from .container import hub

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    hub.initialize()
    yield
    hub.shutdown()


app = FastAPI(
    title="Video Fission",
    version="0.1.0",
    description="Standalone Codex conversation hub",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        settings.host,
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
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
