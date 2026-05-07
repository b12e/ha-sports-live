from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from .base import BaseProvider, MatchEvent, MatchSummary

log = logging.getLogger(__name__)

# Two events are considered "the same" if they share kind + side and their
# minute markers are within this many minutes of each other (or one/both
# unknown), AND they were received within the time window below.
_DEDUP_MINUTE_TOLERANCE = 1
_DEDUP_TIME_WINDOW_S = 60.0


class _Source:
    def __init__(self, name: str, provider: BaseProvider, match_id: str) -> None:
        self.name = name
        self.provider = provider
        self.match_id = match_id


class MergedProvider(BaseProvider):
    """Race two `BaseProvider`s for the same match and yield each event from
    whichever source sees it first; suppress duplicates from the other source.

    The user-visible identity of the merged stream is the *primary* provider —
    `search_matches` and `get_match` delegate there.
    """

    def __init__(
        self,
        primary: BaseProvider,
        secondary: BaseProvider,
        *,
        primary_match_id: str,
        secondary_match_id: str,
        primary_name: str = "primary",
        secondary_name: str = "secondary",
    ) -> None:
        self._primary = _Source(primary_name, primary, primary_match_id)
        self._secondary = _Source(secondary_name, secondary, secondary_match_id)

    async def search_matches(
        self, query: str, *, competition: str | None = None
    ) -> list[MatchSummary]:
        return await self._primary.provider.search_matches(query, competition=competition)

    async def get_match(self, match_id: str) -> MatchSummary:
        return await self._primary.provider.get_match(match_id)

    async def aclose(self) -> None:
        await asyncio.gather(
            self._primary.provider.aclose(),
            self._secondary.provider.aclose(),
            return_exceptions=True,
        )

    async def subscribe(self, match_id: str) -> AsyncIterator[MatchEvent]:
        # `match_id` is the primary's ID; we already remembered the secondary's
        # at construction time.
        del match_id
        queue: asyncio.Queue[tuple[str, MatchEvent] | tuple[str, None]] = asyncio.Queue()

        async def feed(src: _Source) -> None:
            try:
                async for ev in src.provider.subscribe(src.match_id):
                    await queue.put((src.name, ev))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("merged: source %s crashed", src.name)
            finally:
                await queue.put((src.name, None))

        feeders = [
            asyncio.create_task(feed(self._primary), name=f"merged-feed-{self._primary.name}"),
            asyncio.create_task(feed(self._secondary), name=f"merged-feed-{self._secondary.name}"),
        ]

        # Recently-seen events for dedup. Each entry: (kind, side, minute, ts).
        recent: list[tuple[str, str | None, int | None, float]] = []
        finished = 0

        try:
            while finished < len(feeders):
                source, ev = await queue.get()
                if ev is None:
                    finished += 1
                    log.info("merged: source %s exhausted", source)
                    continue

                now = asyncio.get_event_loop().time()
                # Drop expired dedup entries.
                cutoff = now - _DEDUP_TIME_WINDOW_S
                recent = [r for r in recent if r[3] >= cutoff]

                kind_v = ev.kind.value
                side_v = ev.side.value if ev.side else None
                minute_v = ev.minute

                duplicate = False
                for r_kind, r_side, r_minute, _r_ts in recent:
                    if r_kind != kind_v or r_side != side_v:
                        continue
                    if r_minute is None or minute_v is None:
                        duplicate = True
                        break
                    if abs(r_minute - minute_v) <= _DEDUP_MINUTE_TOLERANCE:
                        duplicate = True
                        break

                if duplicate:
                    log.info(
                        "merged: drop dup %s side=%s minute=%s from %s",
                        kind_v, side_v, minute_v, source,
                    )
                    continue

                recent.append((kind_v, side_v, minute_v, now))
                log.info(
                    "merged: %s side=%s minute=%s won by %s",
                    kind_v, side_v, minute_v, source,
                )
                yield ev
        finally:
            for t in feeders:
                t.cancel()
            await asyncio.gather(*feeders, return_exceptions=True)
