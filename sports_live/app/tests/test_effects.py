from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from ..effects.catalog import GOAL, RED_CARD, YELLOW_CARD
from ..effects.runtime import EffectRuntime


def _ha_mock() -> MagicMock:
    ha = MagicMock()
    ha.turn_on = AsyncMock()
    ha.turn_off = AsyncMock()
    return ha


@pytest.mark.asyncio
async def test_goal_runs_steps_in_order():
    ha = _ha_mock()
    rt = EffectRuntime(ha)
    handle = await rt.run(GOAL, ["light.test"], lambda _t: (255, 0, 0))
    assert handle is not None
    await handle.task
    # GOAL has 6 steps -> 6 turn_on calls (none turn_off).
    assert ha.turn_on.await_count == len(GOAL.steps)
    assert ha.turn_off.await_count == 0


@pytest.mark.asyncio
async def test_red_card_preempts_yellow():
    ha = _ha_mock()
    rt = EffectRuntime(ha)
    yh = await rt.run(YELLOW_CARD, ["light.test"], lambda _t: (255, 255, 0))
    assert yh is not None
    # Allow yellow to start its first step.
    await asyncio.sleep(0.02)
    rh = await rt.run(RED_CARD, ["light.test"], lambda _t: (255, 0, 0))
    assert rh is not None
    await rh.task
    # Yellow's task should have terminated (cancelled by token bump).
    yh.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await yh.task


@pytest.mark.asyncio
async def test_lower_priority_dropped_when_higher_running():
    ha = _ha_mock()
    rt = EffectRuntime(ha)
    rh = await rt.run(GOAL, ["light.test"], lambda _t: (0, 255, 0))
    assert rh is not None
    # While GOAL is running, a YELLOW arriving must be dropped.
    yh = await rt.run(YELLOW_CARD, ["light.test"], lambda _t: (255, 255, 0))
    assert yh is None
    rh.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await rh.task


@pytest.mark.asyncio
async def test_dry_run_does_not_call_ha():
    ha = _ha_mock()
    rt = EffectRuntime(ha, dry_run=True)
    h = await rt.run(GOAL, ["light.test"], lambda _t: (0, 0, 255))
    assert h is not None
    await h.task
    assert ha.turn_on.await_count == 0
    assert ha.turn_off.await_count == 0


@pytest.mark.asyncio
async def test_no_lights_yields_no_handle():
    ha = _ha_mock()
    rt = EffectRuntime(ha)
    h = await rt.run(GOAL, [], lambda _t: (0, 0, 0))
    assert h is None
