from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..ha_client import HAClient
from .schemas import Effect, Step

log = logging.getLogger(__name__)

MIN_HOLD_MS = 150  # smallest dwell that real bulb meshes can reliably honor
RGB = tuple[int, int, int]
Resolver = Callable[[str], RGB]


@dataclass
class _LightState:
    token: int = 0  # bumped on every new effect; running effects abort if mismatch
    current_effect_id: str | None = None
    current_priority: int = -1


@dataclass
class EffectHandle:
    effect_id: str
    task: asyncio.Task
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    def cancel(self) -> None:
        self.cancel_event.set()


class EffectRuntime:
    """Runs effects on a set of light entities with per-light priority + token.

    A new effect arriving at a higher priority cancels the running one (per-light).
    A lower-priority effect arriving while a higher one runs is dropped.
    Same-priority arrivals: dropped if `coalesce` else replace.
    """

    def __init__(
        self,
        ha: HAClient,
        *,
        dry_run: bool = False,
        on_finish: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._ha = ha
        self._dry_run = dry_run
        self._on_finish = on_finish
        self._states: dict[str, _LightState] = {}
        self._rgb_capable: set[str] = set()
        self._lock = asyncio.Lock()

    def set_dry_run(self, on: bool) -> None:
        self._dry_run = on

    def set_rgb_capable(self, entities: set[str]) -> None:
        """Tell the runtime which entity_ids accept rgb_color. Others get
        brightness-only pulses so non-color lights still flash visibly."""
        self._rgb_capable = set(entities)

    def _state_for(self, eid: str) -> _LightState:
        st = self._states.get(eid)
        if st is None:
            st = _LightState()
            self._states[eid] = st
        return st

    async def run(
        self,
        effect: Effect,
        lights: list[str],
        resolver: Resolver,
    ) -> EffectHandle | None:
        if not lights:
            return None
        async with self._lock:
            # Decide whether this effect supersedes whatever is currently running.
            elig = []
            for eid in lights:
                st = self._state_for(eid)
                if effect.priority > st.current_priority:
                    elig.append(eid)
                elif effect.priority == st.current_priority:
                    if effect.coalesce and st.current_effect_id == effect.id:
                        continue  # drop duplicate
                    elig.append(eid)
                else:
                    pass  # lower priority -> drop
            if not elig:
                log.debug("effect %s dropped (lower priority)", effect.id)
                return None
            for eid in elig:
                st = self._state_for(eid)
                st.token += 1
                st.current_effect_id = effect.id
                st.current_priority = effect.priority
            tokens = {eid: self._state_for(eid).token for eid in elig}

        cancel_event = asyncio.Event()
        task = asyncio.create_task(self._run_effect(effect, elig, tokens, resolver, cancel_event))
        return EffectHandle(effect_id=effect.id, task=task, cancel_event=cancel_event)

    async def _run_effect(
        self,
        effect: Effect,
        lights: list[str],
        tokens: dict[str, int],
        resolver: Resolver,
        cancel_event: asyncio.Event,
    ) -> None:
        try:
            for step in effect.steps:
                active = [eid for eid, t in tokens.items() if self._state_for(eid).token == t]
                if not active:
                    return
                if cancel_event.is_set():
                    return
                color = self._resolve_color(step, resolver)
                trans_s = max(step.transition_ms, 0) / 1000.0
                if not self._dry_run:
                    if color is None and step.brightness == 0:
                        await self._ha.turn_off(active, transition_s=trans_s)
                    else:
                        # Split by RGB capability: color-capable lights take rgb,
                        # everything else gets a brightness-only pulse so non-RGB
                        # bulbs still flash in sync.
                        if self._rgb_capable:
                            rgb_lights = [e for e in active if e in self._rgb_capable]
                            plain_lights = [e for e in active if e not in self._rgb_capable]
                        else:
                            rgb_lights, plain_lights = list(active), []
                        if rgb_lights:
                            await self._ha.turn_on(
                                rgb_lights,
                                rgb=color,
                                brightness=step.brightness,
                                transition_s=trans_s,
                            )
                        if plain_lights:
                            await self._ha.turn_on(
                                plain_lights,
                                brightness=step.brightness,
                                transition_s=trans_s,
                            )
                else:
                    log.info(
                        "[dry-run] %s step lights=%s color=%s b=%s t=%dms hold=%dms",
                        effect.id, active, color, step.brightness, step.transition_ms, step.hold_ms,
                    )
                # Honor transition + hold (clamped).
                wait_ms = step.transition_ms + max(step.hold_ms, MIN_HOLD_MS)
                try:
                    await asyncio.wait_for(cancel_event.wait(), timeout=wait_ms / 1000.0)
                    return  # cancelled mid-step
                except TimeoutError:
                    pass
        finally:
            async with self._lock:
                for eid, t in tokens.items():
                    st = self._state_for(eid)
                    if st.token == t:
                        st.current_effect_id = None
                        st.current_priority = -1
            if self._on_finish:
                try:
                    await self._on_finish(effect.id)
                except Exception:  # noqa: BLE001
                    log.exception("on_finish callback failed")

    def _resolve_color(self, step: Step, resolver: Resolver) -> RGB | None:
        c = step.color
        if c is None:
            return None
        if isinstance(c, tuple):
            return c
        return resolver(c)
