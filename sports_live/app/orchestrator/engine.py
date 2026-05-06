from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .. import state_store
from ..colors.resolver import ColorResolver
from ..effects.catalog import KIND_TO_EFFECT
from ..effects.runtime import EffectHandle, EffectRuntime
from ..effects.schemas import PRIO_AMBIENT, Effect, Step
from ..ha_client import HAClient
from ..providers.base import (
    BaseProvider,
    EventKind,
    MatchEvent,
    MatchPhase,
    MatchSummary,
    Side,
)
from .ambient import AmbientResolver
from .delay_queue import DelayQueue
from .sides import LightSlot, PhysicalSide, Position, opposite
from .state_machine import MatchState, apply_event

log = logging.getLogger(__name__)

# Events whose visual flash should target only the benefiting team's side
# (plus any "both"-tagged lights). Phase / VAR events always span everything.
_TEAM_SCOPED_KINDS = {
    EventKind.GOAL,
    EventKind.OWN_GOAL,
    EventKind.PENALTY_GOAL,
    EventKind.YELLOW_CARD,
    EventKind.RED_CARD,
    EventKind.PENALTY_AWARDED,
    EventKind.BIG_CHANCE,
}


@dataclass
class OrchestratorStatus:
    running: bool = False
    match_id: str | None = None
    summary: MatchSummary | None = None
    state: MatchState = field(default_factory=MatchState)
    ambient_left: tuple[int, int, int] | None = None
    ambient_right: tuple[int, int, int] | None = None
    ambient_both: tuple[int, int, int] | None = None
    home_side: PhysicalSide = "left"
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
            "home_side": self.home_side,
            "ambient": {
                "left": list(self.ambient_left) if self.ambient_left else None,
                "right": list(self.ambient_right) if self.ambient_right else None,
                "both": list(self.ambient_both) if self.ambient_both else None,
            },
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
    """Top-level coordinator. One instance per add-on."""

    def __init__(self, ha: HAClient, colors: ColorResolver) -> None:
        self._ha = ha
        self._colors = colors
        self._ambient = AmbientResolver(colors)
        self._effects = EffectRuntime(ha, dry_run=False)
        self._queue: DelayQueue[MatchEvent] = DelayQueue()
        self._provider: BaseProvider | None = None
        self._summary: MatchSummary | None = None
        self._lights: list[LightSlot] = []
        self._home_side: PhysicalSide = "left"
        self._auto_swap_at_ht: bool = True
        self._captured_scene: list[dict[str, Any]] | None = None
        self._state = MatchState()
        self._last_event: MatchEvent | None = None
        self._last_ambient_by_pos: dict[Position, tuple[int, int, int] | None] = {
            "left": None, "right": None, "both": None,
        }
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
            ambient_left=self._last_ambient_by_pos["left"],
            ambient_right=self._last_ambient_by_pos["right"],
            ambient_both=self._last_ambient_by_pos["both"],
            home_side=self._home_side,
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

    async def swap_sides(self) -> None:
        """Manually flip which physical side is home's. Auto-fires at second-half kickoff."""
        self._home_side = opposite(self._home_side)
        log.info("home is now playing on the %s", self._home_side)
        # Force ambient to re-render at the new positions.
        self._last_ambient_by_pos = {"left": None, "right": None, "both": None}
        await self._reassert_ambient()

    async def start(
        self,
        provider: BaseProvider,
        summary: MatchSummary,
        lights: list[LightSlot],
        *,
        tv_delay_s: float = 0.0,
        home_side: PhysicalSide = "left",
        auto_swap_at_ht: bool = True,
    ) -> None:
        async with self._lock:
            if self._provider is not None:
                raise RuntimeError("orchestrator already running")
            self._provider = provider
            self._summary = summary
            self._lights = list(lights)
            self._home_side = home_side
            self._auto_swap_at_ht = auto_swap_at_ht
            self._state = MatchState(
                phase=summary.phase,
                score_home=summary.score_home,
                score_away=summary.score_away,
            )
            self._last_event = None
            self._last_ambient_by_pos = {"left": None, "right": None, "both": None}
            self._failure = None
            await self._queue.set_offset(tv_delay_s)

            # Detect which lights accept rgb_color so non-RGB ones get brightness pulses.
            try:
                rgb_capable = await self._discover_rgb_capable(self._all_entities())
            except Exception as e:  # noqa: BLE001
                log.warning("rgb capability probe failed: %s", e)
                rgb_capable = set(self._all_entities())  # assume all capable on failure
            self._effects.set_rgb_capable(rgb_capable)
            log.info(
                "starting: %d lights (home on %s, auto-swap-at-HT=%s, rgb-capable=%d/%d)",
                len(self._lights), self._home_side, self._auto_swap_at_ht,
                len(rgb_capable), len(self._lights),
            )

            try:
                self._captured_scene = await self._ha.capture_scene(self._all_entities())
            except Exception as e:  # noqa: BLE001
                log.warning("scene capture failed: %s", e)
                self._captured_scene = []

            persisted = state_store.load()
            persisted["active"] = {
                "match_id": summary.id,
                "lights": [{"entity_id": s.entity_id, "position": s.position} for s in self._lights],
                "home_side": self._home_side,
                "tv_delay_s": tv_delay_s,
                "captured_scene": self._captured_scene,
                "started_at": datetime.now(UTC).isoformat(),
            }
            state_store.save(persisted)

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
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            self._provider_task = None
            self._dispatch_task = None
            with contextlib.suppress(Exception):
                await self._provider.aclose()
            self._provider = None

            if restore and self._captured_scene:
                try:
                    await self._ha.restore_scene(self._captured_scene)
                except Exception as e:  # noqa: BLE001
                    log.warning("scene restore failed: %s", e)
            persisted = state_store.load()
            if "active" in persisted:
                persisted.pop("active", None)
                state_store.save(persisted)
            self._captured_scene = None
            self._summary = None
            self._lights = []

    # ---- light-grouping helpers ----------------------------------------

    def _entities(self, position: Position) -> list[str]:
        return [s.entity_id for s in self._lights if s.position == position]

    def _all_entities(self) -> list[str]:
        return [s.entity_id for s in self._lights]

    async def _discover_rgb_capable(self, entity_ids: list[str]) -> set[str]:
        """Return the subset of entity_ids whose HA state advertises an RGB-style color mode."""
        rgb_modes = {"rgb", "rgbw", "rgbww", "hs", "xy"}
        capable: set[str] = set()
        for eid in entity_ids:
            st = await self._ha.get_state(eid)
            if st is None:
                continue
            attrs = st.get("attributes") or {}
            modes = attrs.get("supported_color_modes") or []
            if any(m in rgb_modes for m in modes) or "rgb_color" in attrs:
                capable.add(eid)
        return capable

    def _physical_side_for_team(self, team_side: Side) -> PhysicalSide:
        return self._home_side if team_side == Side.HOME else opposite(self._home_side)

    def _benefiting_side(self, event: MatchEvent) -> Side | None:
        """The team that a flash should celebrate / mark.

        For OWN_GOAL the benefiting team is the *opposite* of the player's side.
        For other events: prefer event.side, fall back to team_id lookup.
        """
        if event.kind == EventKind.OWN_GOAL and event.side is not None:
            return Side.AWAY if event.side == Side.HOME else Side.HOME
        if event.side is not None:
            return event.side
        return self._side_for_team(event.team_id)

    def _side_for_team(self, team_id: str | None) -> Side | None:
        if team_id is None or self._summary is None:
            return None
        if team_id == self._summary.home.id:
            return Side.HOME
        if team_id == self._summary.away.id:
            return Side.AWAY
        return None

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
        prev_phase = self._state.phase
        self._state = apply_event(self._state, event)
        self._last_event = event
        log.info(
            "event %s minute=%s side=%s -> %d-%d phase=%s",
            event.kind.value, event.minute, event.side, self._state.score_home,
            self._state.score_away, self._state.phase.value,
        )

        # Teams swap ends at the second-half kickoff (HT -> LIVE transition).
        if prev_phase == MatchPhase.HT and self._state.phase == MatchPhase.LIVE:
            if self._auto_swap_at_ht:
                log.info("HT -> 2nd half: auto-swapping home side")
                await self.swap_sides()
            else:
                log.info(
                    "HT -> 2nd half: auto-swap disabled, keeping home_side=%s",
                    self._home_side,
                )

        effect = KIND_TO_EFFECT.get(event.kind)
        handle: EffectHandle | None = None
        if effect is not None:
            handle = await self._run_effect_for(effect, event)

        # Wait for the event-triggered effect to finish before re-asserting
        # ambient — otherwise ambient (priority 0) is dropped while a higher-
        # priority effect still holds the lights, and the cache update means
        # we never retry. Lights would stick on the effect's last step color.
        if handle is not None:
            with contextlib.suppress(Exception):
                await handle.task

        await self._reassert_ambient()

    async def _run_effect_for(
        self, effect: Effect, event: MatchEvent
    ) -> EffectHandle | None:
        assert self._summary is not None
        benefiting = self._benefiting_side(event)
        team_side = benefiting or self._state.leading_side or Side.HOME
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

        # Decide which lights to target.
        if event.kind in _TEAM_SCOPED_KINDS and benefiting is not None:
            phys = self._physical_side_for_team(benefiting)
            target = self._entities(phys) + self._entities("both")
            log.info(
                "%s by %s -> phys side %s (home_side=%s) -> lights %s",
                effect.id, benefiting.value, phys, self._home_side, target,
            )
        else:
            target = self._all_entities()
            log.info("%s -> all lights %s", effect.id, target)

        if not target:
            return None
        return await self._effects.run(effect, target, resolver)

    async def test_flash(self, side: Side) -> None:
        """Run a GOAL flash for `side` (HOME or AWAY) without going through the
        provider/queue. UI calls this so users can verify their light->side
        mapping at any time."""
        if not self._summary or not self._lights:
            return
        fake = MatchEvent(id="test-flash", kind=EventKind.GOAL, side=side)
        effect = KIND_TO_EFFECT[EventKind.GOAL]
        handle = await self._run_effect_for(effect, fake)
        if handle is not None:
            with contextlib.suppress(Exception):
                await handle.task
        await self._reassert_ambient()

    async def _reassert_ambient(self) -> None:
        if not self._summary or not self._lights:
            return
        plan = self._ambient.choose(self._state, self._summary, home_side=self._home_side)

        for position, choice in (
            ("left", plan.left),
            ("right", plan.right),
            ("both", plan.both),
        ):
            eids = self._entities(position)
            if not eids:
                continue
            if self._last_ambient_by_pos.get(position) == choice.color:
                continue
            log.debug("ambient[%s] -> %s", position, choice.color)
            ambient_effect = Effect(
                id=f"ambient-{position}",
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
            color = choice.color
            handle = await self._effects.run(ambient_effect, eids, lambda _t, c=color: c)
            # Only mark the cache if the dispatch was accepted; if a higher
            # priority effect is still running, leave the cache stale so we
            # retry next time.
            if handle is not None:
                self._last_ambient_by_pos[position] = choice.color
