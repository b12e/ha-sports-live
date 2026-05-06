from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__, state_store
from ..colors.resolver import ColorResolver
from ..ha_client import HAClient
from ..orchestrator.engine import Orchestrator
from ..orchestrator.sides import LightSlot
from ..providers.base import EventKind, MatchEvent, Side
from ..providers.mock import MockProvider
from ..providers.replay import InMemoryReplayProvider, ReplayProvider
from ..providers.sofascore import SofascoreProvider
from ..settings import Settings

log = logging.getLogger(__name__)
UI_DIR = Path(__file__).resolve().parent.parent / "ui"


class LightSlotReq(BaseModel):
    entity_id: str
    position: Literal["left", "right", "both"] = "both"


class StartReq(BaseModel):
    match_id: str
    provider: Literal["sofascore", "mock", "replay", "sofascore_replay"] = "sofascore"
    lights: list[LightSlotReq] = Field(default_factory=list)
    home_side: Literal["left", "right"] = "left"
    auto_swap_at_ht: bool = True
    tv_delay_s: float = 0.0
    dry_run: bool = False
    replay_path: str | None = None
    replay_speed: float = 1.0


class TestFlashReq(BaseModel):
    side: Literal["home", "away"]


class ReplayPreviewReq(BaseModel):
    event_id: str


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
        # Boot-time recovery: if the previous run died without graceful shutdown,
        # `active` will still be present in /data/state.json. Restore those lights
        # to their pre-match state and clear the record. We don't auto-resume
        # match tracking — the user reselects via the UI.
        recovered = state_store.load()
        active = recovered.get("active")
        if active and active.get("captured_scene"):
            log.info(
                "recovering: restoring %d lights captured at %s",
                len(active["captured_scene"]),
                active.get("started_at"),
            )
            try:
                await ha.restore_scene(active["captured_scene"])
            except Exception as e:  # noqa: BLE001
                log.warning("recovery restore failed: %s", e)
            recovered.pop("active", None)
            state_store.save(recovered)
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
            raise HTTPException(status_code=502, detail=f"HA unreachable: {e}") from e
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

    @app.get("/api/match/live")
    async def live_matches() -> list[dict[str, Any]]:
        """Football matches currently live or kicking off in the next ~4h."""
        sofa = SofascoreProvider()
        try:
            results = await sofa.live_and_upcoming()
        finally:
            await sofa.aclose()
        return [
            {
                "id": s.id,
                "competition": s.competition,
                "kickoff_utc": s.kickoff_utc.isoformat(),
                "status": s.status,
                "phase": s.phase.value,
                "score_home": s.score_home,
                "score_away": s.score_away,
                "home": {
                    "id": s.home.id,
                    "name": s.home.name,
                    "short": s.home.short_name,
                    "color": (f"#{s.home.primary_color}" if s.home.primary_color else None),
                },
                "away": {
                    "id": s.away.id,
                    "name": s.away.name,
                    "short": s.away.short_name,
                    "color": (f"#{s.away.primary_color}" if s.away.primary_color else None),
                },
            }
            for s in results
        ]

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

        if req.provider == "sofascore_replay":
            sofa = SofascoreProvider()
            try:
                summary = await sofa.get_match(req.match_id)
                records = await sofa.fetch_replay_records(req.match_id)
            except Exception as e:  # noqa: BLE001
                await sofa.aclose()
                raise HTTPException(status_code=502, detail=f"sofascore fetch failed: {e}") from e
            await sofa.aclose()
            prov = InMemoryReplayProvider(records, summary, speed=req.replay_speed)
        else:
            prov = _make_provider(
                req.provider, replay_path=req.replay_path, replay_speed=req.replay_speed
            )
            try:
                summary = await prov.get_match(req.match_id)
            except Exception as e:  # noqa: BLE001
                await prov.aclose()
                raise HTTPException(status_code=502, detail=f"match lookup failed: {e}") from e

        light_slots = [LightSlot(entity_id=s.entity_id, position=s.position) for s in req.lights]
        orchestrator.set_dry_run(req.dry_run)
        try:
            await orchestrator.start(
                prov, summary, light_slots,
                tv_delay_s=req.tv_delay_s,
                home_side=req.home_side,
                auto_swap_at_ht=req.auto_swap_at_ht,
            )
        except Exception:
            await prov.aclose()
            raise
        app.state.provider = prov

        # Persist last-used selection so a restart can resume.
        persisted = state_store.load()
        persisted["last"] = {
            "match_id": req.match_id,
            "provider": req.provider,
            "lights": [{"entity_id": s.entity_id, "position": s.position} for s in req.lights],
            "home_side": req.home_side,
            "auto_swap_at_ht": req.auto_swap_at_ht,
            "tv_delay_s": req.tv_delay_s,
            "dry_run": req.dry_run,
            "started_at": datetime.now(UTC).isoformat(),
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

    @app.post("/api/match/swap_sides")
    async def swap_sides() -> dict[str, Any]:
        await orchestrator.swap_sides()
        return {"home_side": orchestrator.status().home_side}

    @app.get("/api/config/last")
    async def get_last_config() -> dict[str, Any]:
        """Return the most recently used light selection / positions /
        home_side / TV-delay so the UI can pre-fill the setup form."""
        last = state_store.load().get("last") or {}
        return {
            "lights": last.get("lights") or [],
            "home_side": last.get("home_side") or "left",
            "tv_delay_s": last.get("tv_delay_s") or 0,
            "auto_swap_at_ht": last.get("auto_swap_at_ht", True),
            "provider": last.get("provider"),
        }

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
            id=f"mock-{datetime.now(UTC).timestamp()}",
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

    @app.post("/api/debug/test_flash")
    async def test_flash(req: TestFlashReq) -> dict[str, Any]:
        if not orchestrator.status().running:
            raise HTTPException(status_code=400, detail="not running")
        side = Side.HOME if req.side == "home" else Side.AWAY
        await orchestrator.test_flash(side)
        return {"flashed": req.side}

    @app.post("/api/replay/preview")
    async def preview_replay(req: ReplayPreviewReq) -> dict[str, Any]:
        """Fetch a Sofascore match by ID and return its converted replay records
        without starting the orchestrator. Used by the UI's replay flow."""
        sofa = SofascoreProvider()
        try:
            summary = await sofa.get_match(req.event_id)
            records = await sofa.fetch_replay_records(req.event_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"sofascore fetch failed: {e}") from e
        finally:
            await sofa.aclose()
        return {
            "match_id": summary.id,
            "competition": summary.competition,
            "home": {"id": summary.home.id, "name": summary.home.name},
            "away": {"id": summary.away.id, "name": summary.away.name},
            "kickoff_utc": summary.kickoff_utc.isoformat(),
            "final_score": [summary.score_home, summary.score_away],
            "records": records,
        }

    return app


def _make_provider(
    name: str,
    *,
    replay_path: str | None = None,
    replay_speed: float = 1.0,
) -> Any:
    if name == "sofascore":
        return SofascoreProvider()
    if name == "mock":
        return MockProvider()
    if name == "replay":
        if not replay_path:
            raise HTTPException(status_code=400, detail="replay_path required")
        return ReplayProvider(replay_path, speed=replay_speed)
    raise HTTPException(status_code=400, detail=f"unknown provider: {name}")
