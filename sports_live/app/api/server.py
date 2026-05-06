from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..settings import Settings

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Sports Live", version=__version__)
    app.state.settings = settings

    @app.get("/api/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")

    return app
