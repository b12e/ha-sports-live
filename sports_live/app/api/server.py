from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..colors.resolver import ColorResolver
from ..ha_client import HAClient
from ..orchestrator.engine import Orchestrator
from ..providers.base import EventKind, MatchEvent, Side
from ..providers.mock import MockProvider
from ..providers.replay import ReplayProvider
from ..providers.sofascore import SofascoreProvider
from .. import state_store
from ..settings import Settings

log = logging.getLogger(__name__)
UI_DIR = Path(__file__).resolve().parent.parent / "ui"


class StartReq(BaseModel):
    match_id: str
    provider: Literal["sofascore", "mock", "replay"] = "sofascore"
    lights: list[str] = Field(default_factory=list)
    tv_delay_s: float = 0.0
    dry_run: bool = False
    replay_path: str | None = None


class ColorOverrideReq(BaseModel):
    team_id: str
    rgb: tuple[int, int, int] | None = None


class InjectReq(BaseModel):
    kind: EventKind
    side: Side | None = None
    minute: int | None = None


def create_app(settings: Settings) -> FastAPI:
    ha = HAClient(settings.supervisor_url, settings.supervisor_token)
    colors = ColorResolver()
    # Restore color overrides if any.
    persisted = state_store.load()
    for tid, rgb in (persisted.get("color_overrides") or {}).items():
        colors.set_override(tid, tuple(rgb))  # type: ignore[arg-type]
    orchestrator = Orchestrator(ha, colors)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("api ready")
        try:
            yield
        finally:
            await orchestrator.stop(restore=True)
            await ha.aclose()

    app = FastAPI(title="Sports Live", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.ha = ha
    app.state.colors = colors
    app.state.orchestrator = orchestrator
    # Active provider instance (for inject API access to MockProvider).
    app.state.provider = None

    # ---- health & static -------------------------------------------------

    @app.get("/api/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")

    # ---- lights ----------------------------------------------------------

    @app.get("/api/lights")
    async def list_lights() -> list[dict[str, Any]]:
        try:
            states = await ha.list_lights()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"HA unreachable: {e}")
        return [
            {
                "entity_id": s["entity_id"],
                "name": (s.get("attributes") or {}).get("friendly_name", s["entity_id"]),
                "state": s["state"],
                "supports_color": "rgb_color" in (s.get("attributes") or {})
                or "color_mode" in (s.get("attributes") or {}),
            }
            for s in states
        ]

    # ---- match search & lifecycle ---------------------------------------

    @app.get("/api/match/search")
    async def search(q: str = "", provider: str = "sofascore") -> list[dict[str, Any]]:
        if not q.strip():
            return []
        prov = _make_provider(provider)
        try:
            results = await prov.search_matches(q)
        finally:
            await prov.aclose()
        return [
            {
                "id": r.id,
                "competition": r.competition,
                "kickoff_utc": r.kickoff_utc.isoformat(),
                "status": r.status,
                "phase": r.phase.value,
                "score_home": r.score_home,
                "score_away": r.score_away,
                "home": {"id": r.home.id, "name": r.home.name, "short": r.home.short_name},
                "away": {"id": r.away.id, "name": r.away.name, "short": r.away.short_name},
            }
            for r in results
        ]

    @app.post("/api/match/start")
    async def start(req: StartReq) -> dict[str, Any]:
        if app.state.provider is not None or orchestrator.status().running:
            raise HTTPException(status_code=409, detail="already running; stop first")
        prov = _make_provider(req.provider, replay_path=req.replay_path)
        try:
            summary = await prov.get_match(req.match_id)
        except Exception as e:  # noqa: BLE001
            await prov.aclose()
            raise HTTPException(status_code=502, detail=f"match lookup failed: {e}")

        orchestrator.set_dry_run(req.dry_run)
        try:
            await orchestrator.start(prov, summary, req.lights, tv_delay_s=req.tv_delay_s)
        except Exception:
            await prov.aclose()
            raise
        app.state.provider = prov

        # Persist last-used selection so a restart can resume.
        persisted = state_store.load()
        persisted["last"] = {
            "match_id": req.match_id,
            "provider": req.provider,
            "lights": req.lights,
            "tv_delay_s": req.tv_delay_s,
            "dry_run": req.dry_run,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        state_store.save(persisted)

        return orchestrator.status().to_dict()

    @app.post("/api/match/stop")
    async def stop(restore: bool = True) -> dict[str, Any]:
        await orchestrator.stop(restore=restore)
        app.state.provider = None
        return {"running": False, "restored": restore}

    @app.get("/api/match/status")
    async def status() -> dict[str, Any]:
        return orchestrator.status().to_dict()

    @app.post("/api/match/tv_delay")
    async def set_tv_delay(seconds: float) -> dict[str, Any]:
        await orchestrator.set_tv_delay(seconds)
        return {"tv_delay_s": orchestrator.status().tv_delay_s}

    # ---- color overrides -------------------------------------------------

    @app.get("/api/colors")
    async def get_overrides() -> dict[str, list[int]]:
        return {tid: list(rgb) for tid, rgb in colors.overrides().items()}

    @app.post("/api/colors")
    async def set_override(req: ColorOverrideReq) -> dict[str, Any]:
        colors.set_override(req.team_id, req.rgb)
        persisted = state_store.load()
        persisted["color_overrides"] = {tid: list(rgb) for tid, rgb in colors.overrides().items()}
        state_store.save(persisted)
        return {"overrides": persisted["color_overrides"]}

    # ---- debug & dev -----------------------------------------------------

    @app.post("/api/debug/inject")
    async def inject(req: InjectReq) -> dict[str, Any]:
        prov = app.state.provider
        if not isinstance(prov, MockProvider):
            raise HTTPException(status_code=400, detail="not running on mock provider")
        ev = MatchEvent(
            id=f"mock-{datetime.now(timezone.utc).timestamp()}",
            kind=req.kind,
            side=req.side,
            minute=req.minute,
        )
        await prov.inject(ev)
        return {"injected": req.kind.value}

    @app.post("/api/debug/dry_run")
    async def set_dry_run(on: bool) -> dict[str, Any]:
        orchestrator.set_dry_run(on)
        return {"dry_run": on}

    return app


def _make_provider(name: str, *, replay_path: str | None = None) -> Any:
    if name == "sofascore":
        return SofascoreProvider()
    if name == "mock":
        return MockProvider()
    if name == "replay":
        if not replay_path:
            raise HTTPException(status_code=400, detail="replay_path required")
        return ReplayProvider(replay_path)
    raise HTTPException(status_code=400, detail=f"unknown provider: {name}")
