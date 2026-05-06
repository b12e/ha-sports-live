from __future__ import annotations

import asyncio

import pytest

from ..orchestrator.delay_queue import DelayQueue


@pytest.mark.asyncio
async def test_zero_offset_pops_immediately():
    q: DelayQueue[str] = DelayQueue(offset_s=0.0)
    await q.push("a")
    got = await asyncio.wait_for(q.pop_when_due(), timeout=0.5)
    assert got == "a"


@pytest.mark.asyncio
async def test_offset_delays_until_due():
    q: DelayQueue[str] = DelayQueue(offset_s=0.3)
    t0 = asyncio.get_event_loop().time()
    await q.push("late")
    got = await q.pop_when_due()
    elapsed = asyncio.get_event_loop().time() - t0
    assert got == "late"
    assert elapsed >= 0.25  # honored the delay (with small slack)


@pytest.mark.asyncio
async def test_set_offset_retimes_queued_items():
    """Increasing offset after push should push items further out;
    decreasing should make them due sooner."""
    q: DelayQueue[str] = DelayQueue(offset_s=2.0)
    await q.push("x")
    await q.set_offset(0.0)
    got = await asyncio.wait_for(q.pop_when_due(), timeout=0.5)
    assert got == "x"


@pytest.mark.asyncio
async def test_priority_ordering_by_play_at():
    q: DelayQueue[str] = DelayQueue(offset_s=0.0)
    loop = asyncio.get_event_loop()
    now = loop.time()
    # Push three items: middle has the smallest play_at.
    await q.push("first", at_now=now - 1.0)
    await q.push("third", at_now=now + 0.05)
    await q.push("second", at_now=now)
    seq = []
    for _ in range(3):
        seq.append(await asyncio.wait_for(q.pop_when_due(), timeout=1.0))
    assert seq == ["first", "second", "third"]
