from __future__ import annotations

from datetime import UTC, datetime

from ..orchestrator.state_machine import MatchState, apply_event
from ..providers.base import EventKind, MatchEvent, MatchPhase, Side


def ev(kind: EventKind, **kw) -> MatchEvent:
    return MatchEvent(id=f"t-{kind.value}", kind=kind, received_at=datetime.now(UTC), **kw)


def test_initial_state_is_pre_zero_zero():
    s = MatchState()
    assert s.phase == MatchPhase.PRE
    assert s.score_home == 0 and s.score_away == 0
    assert s.leading_side is None


def test_kickoff_moves_to_live():
    s = MatchState()
    s = apply_event(s, ev(EventKind.KICKOFF))
    assert s.phase == MatchPhase.LIVE


def test_home_goal_increments_home_and_leads():
    s = apply_event(MatchState(phase=MatchPhase.LIVE), ev(EventKind.GOAL, side=Side.HOME))
    assert s.score_home == 1 and s.score_away == 0
    assert s.leading_side == Side.HOME


def test_provider_score_overrides_increment():
    s = apply_event(
        MatchState(phase=MatchPhase.LIVE, score_home=1, score_away=0),
        ev(EventKind.GOAL, side=Side.AWAY, score_home=1, score_away=1),
    )
    assert s.score_home == 1 and s.score_away == 1
    assert s.leading_side is None  # tie


def test_halftime_then_fulltime():
    s = MatchState(phase=MatchPhase.LIVE)
    s = apply_event(s, ev(EventKind.HT))
    assert s.phase == MatchPhase.HT
    s = apply_event(s, ev(EventKind.KICKOFF))
    assert s.phase == MatchPhase.LIVE
    s = apply_event(s, ev(EventKind.FT))
    assert s.phase == MatchPhase.FT
    assert s.is_terminal


def test_phase_event_score_payload_updates_scoreboard():
    s = apply_event(
        MatchState(phase=MatchPhase.LIVE, score_home=1, score_away=1),
        ev(EventKind.HT, score_home=2, score_away=1),
    )
    assert s.phase == MatchPhase.HT
    assert s.score_home == 2 and s.score_away == 1


def test_own_goal_credits_other_side_via_score_payload():
    # OWN_GOAL by HOME-side player credits AWAY; provider sends post-event scores.
    s = apply_event(
        MatchState(phase=MatchPhase.LIVE),
        ev(EventKind.OWN_GOAL, side=Side.HOME, score_home=0, score_away=1),
    )
    assert s.score_home == 0 and s.score_away == 1
    assert s.leading_side == Side.AWAY
