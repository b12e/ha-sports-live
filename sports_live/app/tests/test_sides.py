from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ..colors.resolver import ColorResolver
from ..orchestrator.ambient import AmbientResolver
from ..orchestrator.engine import Orchestrator
from ..orchestrator.sides import LightSlot, opposite
from ..orchestrator.state_machine import MatchState
from ..providers.base import EventKind, MatchEvent, MatchPhase, MatchSummary, Side, Team


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


def test_opposite():
    assert opposite("left") == "right"
    assert opposite("right") == "left"


def test_ambient_split_when_home_attacks_left():
    colors = ColorResolver()
    summ = _summary()
    plan = AmbientResolver(colors).choose(MatchState(phase=MatchPhase.LIVE), summ, home_side="left")
    home_rgb = colors.primary(summ.home)
    away_rgb = colors.primary(summ.away)
    assert plan.left.color == home_rgb
    assert plan.right.color == away_rgb


def test_ambient_split_when_home_attacks_right():
    colors = ColorResolver()
    summ = _summary()
    plan = AmbientResolver(colors).choose(MatchState(phase=MatchPhase.LIVE), summ, home_side="right")
    assert plan.left.color == colors.primary(summ.away)
    assert plan.right.color == colors.primary(summ.home)


def test_ambient_pre_match_warm_white_everywhere():
    colors = ColorResolver()
    plan = AmbientResolver(colors).choose(MatchState(phase=MatchPhase.PRE), _summary(), home_side="left")
    assert plan.left.color == plan.right.color == plan.both.color


def test_both_lights_show_leader_when_someone_leads():
    colors = ColorResolver()
    summ = _summary()
    state = MatchState(phase=MatchPhase.LIVE, score_home=2, score_away=1)
    plan = AmbientResolver(colors).choose(state, summ, home_side="left")
    assert plan.both.color == colors.primary(summ.home)


def test_both_lights_warm_white_on_tie():
    from ..colors.resolver import WARM_WHITE
    colors = ColorResolver()
    state = MatchState(phase=MatchPhase.LIVE, score_home=1, score_away=1)
    plan = AmbientResolver(colors).choose(state, _summary(), home_side="left")
    assert plan.both.color == WARM_WHITE


@pytest.mark.asyncio
async def test_orchestrator_swap_sides_flips_home_side():
    ha = MagicMock()
    ha.turn_on = AsyncMock()
    ha.turn_off = AsyncMock()
    ha.capture_scene = AsyncMock(return_value=[])
    ha.restore_scene = AsyncMock()
    orch = Orchestrator(ha, ColorResolver())
    orch._home_side = "left"
    orch._summary = _summary()
    orch._lights = [LightSlot("light.l", "left"), LightSlot("light.r", "right")]
    await orch.swap_sides()
    assert orch._home_side == "right"
    await orch.swap_sides()
    assert orch._home_side == "left"


def test_benefiting_side_own_goal_credits_other_team():
    ha = MagicMock()
    orch = Orchestrator(ha, ColorResolver())
    orch._summary = _summary()
    own = MatchEvent(id="e", kind=EventKind.OWN_GOAL, side=Side.HOME)  # home player put it in own net
    assert orch._benefiting_side(own) == Side.AWAY
    own_away = MatchEvent(id="e", kind=EventKind.OWN_GOAL, side=Side.AWAY)
    assert orch._benefiting_side(own_away) == Side.HOME


def test_benefiting_side_normal_goal_uses_event_side():
    ha = MagicMock()
    orch = Orchestrator(ha, ColorResolver())
    orch._summary = _summary()
    g = MatchEvent(id="e", kind=EventKind.GOAL, side=Side.HOME)
    assert orch._benefiting_side(g) == Side.HOME


def test_physical_side_for_team_uses_home_side():
    ha = MagicMock()
    orch = Orchestrator(ha, ColorResolver())
    orch._home_side = "left"
    assert orch._physical_side_for_team(Side.HOME) == "left"
    assert orch._physical_side_for_team(Side.AWAY) == "right"
    orch._home_side = "right"
    assert orch._physical_side_for_team(Side.HOME) == "right"
    assert orch._physical_side_for_team(Side.AWAY) == "left"
