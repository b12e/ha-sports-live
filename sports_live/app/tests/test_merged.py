from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from ..providers.base import (
    BaseProvider,
    EventKind,
    MatchEvent,
    MatchPhase,
    MatchSummary,
    Side,
    Team,
)
from ..providers.merged import MergedProvider


class _ScriptedProvider(BaseProvider):
    """Yields a pre-baked sequence of events with explicit per-event delays."""

    def __init__(self, script: list[tuple[float, MatchEvent]]) -> None:
        self._script = script
        self.closed = False

    async def search_matches(self, query: str, *, competition: str | None = None):
        return []

    async def get_match(self, match_id: str) -> MatchSummary:
        return MatchSummary(
            id=match_id,
            competition="Test",
            home=Team(id="H", name="Home"),
            away=Team(id="A", name="Away"),
            kickoff_utc=__import__("datetime").datetime.now(__import__("datetime").UTC),
            status="",
            phase=MatchPhase.LIVE,
        )

    async def aclose(self) -> None:
        self.closed = True

    async def subscribe(self, match_id: str) -> AsyncIterator[MatchEvent]:
        for delay, ev in self._script:
            if delay > 0:
                await asyncio.sleep(delay)
            yield ev


def _ev(kind: EventKind, *, side: Side | None = None, minute: int | None = None, eid: str = "") -> MatchEvent:
    return MatchEvent(id=eid or f"{kind.value}-{minute}", kind=kind, side=side, minute=minute)


@pytest.mark.asyncio
async def test_dedup_drops_duplicate_within_window():
    """Same kind+side+minute from both sources within the time window: only the first wins."""
    primary = _ScriptedProvider([(0.0, _ev(EventKind.GOAL, side=Side.HOME, minute=23, eid="p-1"))])
    secondary = _ScriptedProvider([(0.05, _ev(EventKind.GOAL, side=Side.HOME, minute=23, eid="s-1"))])
    merged = MergedProvider(
        primary, secondary,
        primary_match_id="m1", secondary_match_id="m1",
        primary_name="primary", secondary_name="secondary",
    )

    seen: list[MatchEvent] = []
    async for ev in merged.subscribe("m1"):
        seen.append(ev)

    assert len(seen) == 1
    assert seen[0].id == "p-1"


@pytest.mark.asyncio
async def test_secondary_wins_when_first():
    """If the secondary fires before the primary, the secondary's event is the one that's yielded."""
    primary = _ScriptedProvider([(0.1, _ev(EventKind.GOAL, side=Side.AWAY, minute=42, eid="p-1"))])
    secondary = _ScriptedProvider([(0.0, _ev(EventKind.GOAL, side=Side.AWAY, minute=42, eid="s-1"))])
    merged = MergedProvider(
        primary, secondary,
        primary_match_id="m1", secondary_match_id="m1",
        primary_name="primary", secondary_name="secondary",
    )

    seen: list[MatchEvent] = []
    async for ev in merged.subscribe("m1"):
        seen.append(ev)

    assert len(seen) == 1
    assert seen[0].id == "s-1"


@pytest.mark.asyncio
async def test_minute_within_tolerance_is_duplicate():
    """A goal reported at minute 23 by one source and minute 24 by the other is one event."""
    primary = _ScriptedProvider([(0.0, _ev(EventKind.GOAL, side=Side.HOME, minute=23, eid="p-1"))])
    secondary = _ScriptedProvider([(0.05, _ev(EventKind.GOAL, side=Side.HOME, minute=24, eid="s-1"))])
    merged = MergedProvider(
        primary, secondary,
        primary_match_id="m1", secondary_match_id="m1",
        primary_name="primary", secondary_name="secondary",
    )

    seen = [ev async for ev in merged.subscribe("m1")]
    assert len(seen) == 1
    assert seen[0].id == "p-1"


@pytest.mark.asyncio
async def test_different_side_is_not_a_duplicate():
    """Goals by HOME and AWAY at the same minute are two distinct events."""
    primary = _ScriptedProvider([(0.0, _ev(EventKind.GOAL, side=Side.HOME, minute=10, eid="p-1"))])
    secondary = _ScriptedProvider([(0.05, _ev(EventKind.GOAL, side=Side.AWAY, minute=10, eid="s-1"))])
    merged = MergedProvider(
        primary, secondary,
        primary_match_id="m1", secondary_match_id="m1",
        primary_name="primary", secondary_name="secondary",
    )

    seen = [ev async for ev in merged.subscribe("m1")]
    assert {e.id for e in seen} == {"p-1", "s-1"}


@pytest.mark.asyncio
async def test_different_kind_is_not_a_duplicate():
    """A yellow card and a goal at the same minute by the same team don't dedupe."""
    primary = _ScriptedProvider([(0.0, _ev(EventKind.GOAL, side=Side.HOME, minute=15, eid="p-1"))])
    secondary = _ScriptedProvider([
        (0.05, _ev(EventKind.YELLOW_CARD, side=Side.HOME, minute=15, eid="s-1")),
    ])
    merged = MergedProvider(
        primary, secondary,
        primary_match_id="m1", secondary_match_id="m1",
        primary_name="primary", secondary_name="secondary",
    )

    seen = [ev async for ev in merged.subscribe("m1")]
    assert {e.id for e in seen} == {"p-1", "s-1"}


@pytest.mark.asyncio
async def test_streams_interleave_correctly():
    """Multiple events across both sources stream out in time order with no duplicates."""
    primary = _ScriptedProvider([
        (0.00, _ev(EventKind.GOAL, side=Side.HOME, minute=10, eid="p-goal-h-10")),
        (0.05, _ev(EventKind.GOAL, side=Side.AWAY, minute=20, eid="p-goal-a-20")),
    ])
    secondary = _ScriptedProvider([
        (0.02, _ev(EventKind.GOAL, side=Side.HOME, minute=10, eid="s-goal-h-10")),
        (0.10, _ev(EventKind.YELLOW_CARD, side=Side.HOME, minute=25, eid="s-yc-h-25")),
    ])
    merged = MergedProvider(
        primary, secondary,
        primary_match_id="m1", secondary_match_id="m1",
        primary_name="primary", secondary_name="secondary",
    )

    ids = [ev.id async for ev in merged.subscribe("m1")]
    assert ids == ["p-goal-h-10", "p-goal-a-20", "s-yc-h-25"]
