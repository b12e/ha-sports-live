from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from .base import (
    BaseProvider,
    EventKind,
    MatchEvent,
    MatchPhase,
    MatchSummary,
    Side,
    Team,
)


class ReplayProvider(BaseProvider):
    """Replay a JSONL of `{ts_offset_s, kind, side?, minute?, score_home?, score_away?}`
    records at a configurable speed. Used for end-to-end testing without a live match.
    """

    def __init__(self, path: Path | str, *, speed: float = 1.0) -> None:
        self._path = Path(path)
        self._speed = max(0.001, speed)
        self._summary = MatchSummary(
            id=f"replay:{self._path.name}",
            competition="Replay",
            home=Team(id="H", name="Replay Home", primary_color="#e60012"),
            away=Team(id="A", name="Replay Away", primary_color="#003399"),
            kickoff_utc=datetime.now(UTC),
            status="replay",
            phase=MatchPhase.PRE,
        )

    async def search_matches(self, query: str, *, competition: str | None = None) -> list[MatchSummary]:
        return [self._summary]

    async def get_match(self, match_id: str) -> MatchSummary:
        return self._summary

    async def subscribe(self, match_id: str) -> AsyncIterator[MatchEvent]:
        with self._path.open() as fh:
            records = [json.loads(line) for line in fh if line.strip()]

        start = asyncio.get_event_loop().time()
        for i, rec in enumerate(records):
            target = start + (rec.get("ts_offset_s", 0.0) / self._speed)
            now = asyncio.get_event_loop().time()
            if target > now:
                await asyncio.sleep(target - now)
            kind = EventKind(rec["kind"])
            side_raw = rec.get("side")
            side = Side(side_raw) if side_raw else None
            yield MatchEvent(
                id=f"{self._summary.id}:{i}",
                kind=kind,
                minute=rec.get("minute"),
                side=side,
                score_home=rec.get("score_home"),
                score_away=rec.get("score_away"),
                raw=rec,
            )
