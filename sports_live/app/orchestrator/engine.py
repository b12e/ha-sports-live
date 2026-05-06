from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from ..colors.resolver import ColorResolver
from ..effects.catalog import KIND_TO_EFFECT
from ..effects.runtime import EffectRuntime
from ..effects.schemas import Effect
from ..ha_client import HAClient
from ..providers.base import (
    BaseProvider,
    EventKind,
    MatchEvent,
    MatchSummary,
    Side,
)
from .ambient import AmbientChoice, AmbientResolver
from .delay_queue import DelayQueue
from .state_machine import MatchState, apply_event

log = logging.getLogger(__name__)


@dataclass
class OrchestratorStatus:
    running: bool = False
    match_id: str | None = None
    summary: MatchSummary | None = None
    state: MatchState = field(default_factory=MatchState)
    ambient: tuple[int, int, int] | None = None
    last_event: MatchEvent | None = None
    pending_events: int = 0
    tv_delay_s: float = 0.0
    dry_run: bool = False
    failure: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "running": self.running,
            "match_id": self.match_id,
            "phase": self.state.phase.value,
            "score_home": self.state.score_home,
            "score_away": self.state.score_away,
            "ambient": list(self.ambient) if self.ambient else None,
            "pending_events": self.pending_events,
            "tv_delay_s": self.tv_delay_s,
            "dry_run": self.dry_run,
            "failure": self.failure,
        }
        if self.summary is not None:
            out["home"] = {
                "id": self.summary.home.id,
                "name": self.summary.home.name,
                "short_name": self.summary.home.short_name,
            }
            out["away"] = {
                "id": self.summary.away.id,
                "name": self.summary.away.name,
                "short_name": self.summary.away.short_name,
            }
            out["competition"] = self.summary.competition
            out["kickoff_utc"] = self.summary.kickoff_utc.isoformat()
        if self.last_event:
            out["last_event"] = {
                "kind": self.last_event.kind.value,
                "minute": self.last_event.minute,
                "side": self.last_event.side.value if self.last_event.side else None,
            }
        return out


class Orchestrator:
    """Top-level coordinator. One instance per add-on.

    Lifecycle:
        start(provider, summary, lights, tv_delay_s)
            -> capture pre-match scene, subscribe to provider, run delay
               queue worker that dispatches effects, hold ambient.
        stop()
            -> cancel tasks, restore captured scene.
    """

    def __init__(self, ha: HAClient, colors: ColorResolver) -> None:
        self._ha = ha
        self._colors = colors
        self._ambient = AmbientResolver(colors)
        self._effects = EffectRuntime(ha, dry_run=False)
        self._queue: DelayQueue[MatchEvent] = DelayQueue()
        self._provider: BaseProvider | None = None
        self._summary: MatchSummary | None = None
        self._lights: list[str] = []
        self._captured_scene: list[dict[str, Any]] | None = None
        self._state = MatchState()
        self._last_event: MatchEvent | None = None
        self._last_ambient: tuple[int, int, int] | None = None
        self._provider_task: asyncio.Task | None = None
        self._dispatch_task: asyncio.Task | None = None
        self._failure: str | None = None
        self._lock = asyncio.Lock()

    # ---- public API -----------------------------------------------------

    def status(self) -> OrchestratorStatus:
        return OrchestratorStatus(
            running=self._provider is not None,
            match_id=self._summary.id if self._summary else None,
            summary=self._summary,
            state=self._state,
            ambient=self._last_ambient,
            last_event=self._last_event,
            pending_events=len(self._queue.snapshot()),
            tv_delay_s=self._queue.offset_s,
            dry_run=self._effects._dry_run,
            failure=self._failure,
        )

    async def set_tv_delay(self, seconds: float) -> None:
        await self._queue.set_offset(max(0.0, seconds))

    def set_dry_run(self, on: bool) -> None:
        self._effects.set_dry_run(on)

    async def start(
        self,
        provider: BaseProvider,
        summary: MatchSummary,
        lights: list[str],
        *,
        tv_delay_s: float = 0.0,
    ) -> None:
        async with self._lock:
            if self._provider is not None:
                raise RuntimeError("orchestrator already running")
            self._provider = provider
            self._summary = summary
            self._lights = list(lights)
            self._state = MatchState(
                phase=summary.phase,
                score_home=summary.score_home,
                score_away=summary.score_away,
            )
            self._last_event = None
            self._last_ambient = None
            self._failure = None
            await self._queue.set_offset(tv_delay_s)

            log.info("capturing pre-match scene for %d lights", len(self._lights))
            try:
                self._captured_scene = await self._ha.capture_scene(self._lights)
            except Exception as e:  # noqa: BLE001
                log.warning("scene capture failed: %s", e)
                self._captured_scene = []

            await self._reassert_ambient()

            self._provider_task = asyncio.create_task(self._provider_loop())
            self._dispatch_task = asyncio.create_task(self._dispatch_loop())

    async def stop(self, *, restore: bool = True) -> None:
        async with self._lock:
            if self._provider is None:
                return
            log.info("orchestrator stopping (restore=%s)", restore)
            for task in (self._provider_task, self._dispatch_task):
                if task is not None:
                    task.cancel()
            for task in (self._provider_task, self._dispatch_task):
                if task is not None:
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
            self._provider_task = None
            self._dispatch_task = None
            try:
                await self._provider.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._provider = None

            if restore and self._captured_scene:
                try:
                    await self._ha.restore_scene(self._captured_scene)
                except Exception as e:  # noqa: BLE001
                    log.warning("scene restore failed: %s", e)
            self._captured_scene = None
            self._summary = None
            self._lights = []

    # ---- internal loops -------------------------------------------------

    async def _provider_loop(self) -> None:
        assert self._provider is not None
        assert self._summary is not None
        try:
            async for event in self._provider.subscribe(self._summary.id):
                await self._queue.push(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("provider loop crashed")
            self._failure = f"provider: {e}"

    async def _dispatch_loop(self) -> None:
        try:
            while True:
                event = await self._queue.pop_when_due()
                await self._handle_event(event)
        except asyncio.CancelledError:
            raise

    async def _handle_event(self, event: MatchEvent) -> None:
        assert self._summary is not None
        prev_state = self._state
        self._state = apply_event(self._state, event)
        self._last_event = event
        log.info(
            "event %s minute=%s side=%s -> %d-%d phase=%s",
            event.kind.value, event.minute, event.side, self._state.score_home,
            self._state.score_away, self._state.phase.value,
        )

        effect = KIND_TO_EFFECT.get(event.kind)
        if effect is not None:
            await self._run_effect_for(effect, event)

        # Edge-triggered ambient re-assert: only when the desired ambient changes.
        await self._reassert_ambient()

        if self._state.is_terminal and event.kind == EventKind.FT:
            # Let FULLTIME effect finish before restoring (no auto-restore).
            pass

    async def _run_effect_for(self, effect: Effect, event: MatchEvent) -> None:
        assert self._summary is not None
        team_side = event.side or self._side_for_team(event.team_id)
        if team_side is None:
            # Phase events (kickoff/HT/FT) — pick leader if any, else home as neutral.
            team_side = self._state.leading_side or Side.HOME
        team = self._summary.home if team_side == Side.HOME else self._summary.away
        opp = self._summary.away if team_side == Side.HOME else self._summary.home

        def resolver(token: str) -> tuple[int, int, int]:
            if token == "TEAM_COLOR":
                return self._colors.primary(team)
            if token == "OPPONENT_COLOR":
                return self._colors.primary(opp)
            if token == "TEAM_SECONDARY":
                return self._colors.secondary(team)
            return (255, 255, 255)

        await self._effects.run(effect, self._lights, resolver)

    def _side_for_team(self, team_id: str | None) -> Side | None:
        if team_id is None or self._summary is None:
            return None
        if team_id == self._summary.home.id:
            return Side.HOME
        if team_id == self._summary.away.id:
            return Side.AWAY
        return None

    async def _reassert_ambient(self) -> None:
        if not self._summary or not self._lights:
            return
        choice: AmbientChoice = self._ambient.choose(self._state, self._summary)
        if choice.color == self._last_ambient:
            return
        self._last_ambient = choice.color
        log.debug("ambient -> %s", choice.color)
        # Use a low-priority effect so live event flashes can preempt.
        from ..effects.schemas import PRIO_AMBIENT, Effect, Step
        ambient_effect = Effect(
            id="ambient",
            priority=PRIO_AMBIENT,
            coalesce=True,
            restore_after=False,
            steps=[
                Step(
                    color=choice.color,
                    brightness=choice.brightness,
                    transition_ms=int(choice.transition_s * 1000),
                    hold_ms=0,
                ),
            ],
        )
        await self._effects.run(ambient_effect, self._lights, lambda _t: choice.color)
