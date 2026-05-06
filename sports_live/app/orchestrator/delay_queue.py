from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(order=True)
class _Item(Generic[T]):
    play_at: float
    seq: int
    received_at: float = field(compare=False)
    payload: T = field(compare=False)


class DelayQueue(Generic[T]):
    """Priority queue keyed on `play_at = received_at + offset_s`.

    Setting a new `offset_s` re-times every queued item (this is what the
    TV-delay slider in the UI ends up calling).
    """

    def __init__(self, *, offset_s: float = 0.0) -> None:
        self._offset = offset_s
        self._heap: list[_Item[T]] = []
        self._counter = itertools.count()
        self._cv = asyncio.Condition()

    @property
    def offset_s(self) -> float:
        return self._offset

    async def set_offset(self, offset_s: float) -> None:
        async with self._cv:
            delta = offset_s - self._offset
            self._offset = offset_s
            for item in self._heap:
                item.play_at += delta
            heapq.heapify(self._heap)
            self._cv.notify_all()

    async def clear(self) -> None:
        async with self._cv:
            self._heap.clear()
            self._cv.notify_all()

    async def push(self, payload: T, *, at_now: float | None = None) -> None:
        loop = asyncio.get_event_loop()
        now = at_now if at_now is not None else loop.time()
        item = _Item(
            play_at=now + self._offset,
            seq=next(self._counter),
            received_at=now,
            payload=payload,
        )
        async with self._cv:
            heapq.heappush(self._heap, item)
            self._cv.notify_all()

    async def pop_when_due(self) -> T:
        loop = asyncio.get_event_loop()
        async with self._cv:
            while True:
                if not self._heap:
                    await self._cv.wait()
                    continue
                head = self._heap[0]
                wait = head.play_at - loop.time()
                if wait <= 0:
                    heapq.heappop(self._heap)
                    return head.payload
                # Wait either until head is due or until we're notified.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._cv.wait(), timeout=wait)

    def snapshot(self) -> list[float]:
        """Returns the current wall-clock play_at timestamps for each queued item."""
        return [item.play_at for item in self._heap]
