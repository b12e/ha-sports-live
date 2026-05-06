from __future__ import annotations

from dataclasses import dataclass

from ..providers.base import EventKind, MatchEvent, MatchPhase, Side


@dataclass
class MatchState:
    phase: MatchPhase = MatchPhase.PRE
    score_home: int = 0
    score_away: int = 0

    @property
    def leading_side(self) -> Side | None:
        if self.score_home > self.score_away:
            return Side.HOME
        if self.score_away > self.score_home:
            return Side.AWAY
        return None

    @property
    def is_terminal(self) -> bool:
        return self.phase in (MatchPhase.FT, MatchPhase.ABANDONED, MatchPhase.POSTPONED)


def apply_event(state: MatchState, event: MatchEvent) -> MatchState:
    """Pure-ish reducer: returns a new MatchState reflecting the event.

    Score updates: if the provider supplies post-event scores, trust them.
    Otherwise increment based on event side (mock provider relies on this).
    """
    new = MatchState(phase=state.phase, score_home=state.score_home, score_away=state.score_away)

    if event.kind == EventKind.KICKOFF:
        new.phase = MatchPhase.LIVE
    elif event.kind == EventKind.HT:
        new.phase = MatchPhase.HT
    elif event.kind == EventKind.FT:
        new.phase = MatchPhase.FT
    elif event.kind == EventKind.PHASE_CHANGE:
        # Keep current phase; provider will follow up with explicit kicks.
        pass

    if event.kind in (EventKind.GOAL, EventKind.OWN_GOAL, EventKind.PENALTY_GOAL):
        if event.score_home is not None and event.score_away is not None:
            new.score_home = event.score_home
            new.score_away = event.score_away
        elif event.side == Side.HOME:
            new.score_home += 1
        elif event.side == Side.AWAY:
            new.score_away += 1

    return new
