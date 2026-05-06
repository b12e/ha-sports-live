from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from .base import (
    BaseProvider,
    EventKind,
    MatchEvent,
    MatchPhase,
    MatchSummary,
    Team,
)


class MockProvider(BaseProvider):
    """Provider whose events are pushed in via `inject()` from the debug API.

    Used for UI demos and stress-tests without hitting a real data source.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[MatchEvent | None] = asyncio.Queue()
        self._summary = MatchSummary(
            id="mock-match",
            competition="Mock",
            home=Team(id="H", name="Home", short_name="HOM", primary_color="#e60012"),
            away=Team(id="A", name="Away", short_name="AWY", primary_color="#003399"),
            kickoff_utc=datetime.now(UTC),
            status="mock",
            phase=MatchPhase.LIVE,
        )

    async def search_matches(self, query: str, *, competition: str | None = None) -> list[MatchSummary]:
        return [self._summary]

    async def get_match(self, match_id: str) -> MatchSummary:
        return self._summary

    async def subscribe(self, match_id: str) -> AsyncIterator[MatchEvent]:
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev

    async def inject(self, ev: MatchEvent) -> None:
        if ev.kind in (EventKind.GOAL, EventKind.OWN_GOAL, EventKind.PENALTY_GOAL):
            if ev.side and ev.side.value == "home":
                self._summary.score_home += 1
            elif ev.side and ev.side.value == "away":
                self._summary.score_away += 1
            ev.score_home = self._summary.score_home
            ev.score_away = self._summary.score_away
        await self._queue.put(ev)

    async def stop(self) -> None:
        await self._queue.put(None)
