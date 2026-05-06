from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ..colors.resolver import ColorResolver
from ..orchestrator.engine import Orchestrator
from ..providers.base import EventKind, MatchEvent, MatchPhase, MatchSummary, Side, Team
from ..providers.mock import MockProvider


@pytest.fixture(autouse=True)
def _isolate_state_store():
    """Avoid touching /data on the dev box — state persistence is exercised elsewhere."""
    with patch("app.orchestrator.engine.state_store") as ss:
        ss.load = MagicMock(return_value={})
        ss.save = MagicMock()
        yield ss


def _summary() -> MatchSummary:
    return MatchSummary(
        id="m1",
        competition="WC2026",
        home=Team(id="H", name="Belgium", primary_color="EF3340"),
        away=Team(id="A", name="France", primary_color="002654"),
        kickoff_utc=datetime.now(UTC),
        status="ongoing",
        phase=MatchPhase.LIVE,
    )


def _ha_mock() -> MagicMock:
    ha = MagicMock()
    ha.turn_on = AsyncMock()
    ha.turn_off = AsyncMock()
    ha.capture_scene = AsyncMock(return_value=[])
    ha.restore_scene = AsyncMock()
    ha.get_state = AsyncMock(return_value={"attributes": {"supported_color_modes": ["rgb"]}})
    return ha


@pytest.mark.asyncio
async def test_start_clears_stale_queue_items():
    """A stale event left in the queue from a prior session must not survive
    into a fresh start()."""
    orch = Orchestrator(_ha_mock(), ColorResolver())
    stale = MatchEvent(id="stale", kind=EventKind.GOAL, side=Side.HOME)
    await orch._queue.push(stale)
    assert len(orch._queue.snapshot()) == 1

    await orch.start(MockProvider(), _summary(), [])
    assert orch._queue.snapshot() == []

    await orch.stop(restore=False)


@pytest.mark.asyncio
async def test_dispatch_loop_survives_handler_crash():
    """If `_handle_event` raises, the dispatch loop must keep draining the
    queue and processing later events. Otherwise one bad payload silences
    the addon for the rest of the match."""
    orch = Orchestrator(_ha_mock(), ColorResolver())
    await orch.start(MockProvider(), _summary(), [])

    crashed_for: list[str] = []
    survived_for: list[str] = []

    original = orch._handle_event

    async def flaky_handle(event: MatchEvent) -> None:
        if event.id == "boom":
            crashed_for.append(event.id)
            raise RuntimeError("synthetic")
        survived_for.append(event.id)
        await original(event)

    orch._handle_event = flaky_handle  # type: ignore[assignment]

    await orch._queue.push(MatchEvent(id="boom", kind=EventKind.GOAL, side=Side.HOME))
    await orch._queue.push(MatchEvent(id="ok", kind=EventKind.YELLOW_CARD, side=Side.AWAY))

    # Give the dispatch loop a moment to drain both events.
    for _ in range(20):
        if "ok" in survived_for:
            break
        await asyncio.sleep(0.05)

    assert crashed_for == ["boom"]
    assert "ok" in survived_for, "loop did not recover after a handler exception"

    await orch.stop(restore=False)
