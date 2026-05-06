from __future__ import annotations

from ..providers.base import EventKind
from .schemas import (
    PRIO_BIG_CHANCE,
    PRIO_FT,
    PRIO_GOAL,
    PRIO_KICKOFF,
    PRIO_PEN_AWARDED,
    PRIO_RED,
    PRIO_VAR,
    PRIO_YELLOW,
    Effect,
    Step,
)

GOAL = Effect(
    id="goal",
    priority=PRIO_GOAL,
    coalesce=False,
    steps=[
        Step(color=(255, 255, 255), brightness=255, transition_ms=80, hold_ms=200),
        Step(color="TEAM_COLOR",    brightness=255, transition_ms=80, hold_ms=400),
        Step(color=(255, 255, 255), brightness=255, transition_ms=80, hold_ms=200),
        Step(color="TEAM_COLOR",    brightness=255, transition_ms=80, hold_ms=400),
        Step(color=(255, 255, 255), brightness=255, transition_ms=80, hold_ms=200),
        Step(color="TEAM_COLOR",    brightness=255, transition_ms=200, hold_ms=1500),
    ],
)

YELLOW_CARD = Effect(
    id="yellow",
    priority=PRIO_YELLOW,
    coalesce=True,
    steps=[
        Step(color=(255, 200, 0), brightness=255, transition_ms=100, hold_ms=600),
    ],
)

RED_CARD = Effect(
    id="red",
    priority=PRIO_RED,
    steps=[
        Step(color=(255, 0, 0), brightness=255, transition_ms=80, hold_ms=400),
        Step(color=(0, 0, 0),   brightness=0,   transition_ms=80, hold_ms=200),
        Step(color=(255, 0, 0), brightness=255, transition_ms=80, hold_ms=800),
    ],
)

PENALTY_AWARDED = Effect(
    id="penalty_awarded",
    priority=PRIO_PEN_AWARDED,
    steps=[
        Step(color="TEAM_COLOR", brightness=255, transition_ms=150, hold_ms=400),
        Step(color=(255, 255, 255), brightness=255, transition_ms=150, hold_ms=400),
        Step(color="TEAM_COLOR", brightness=255, transition_ms=150, hold_ms=600),
    ],
)

VAR_REVIEW = Effect(
    id="var",
    priority=PRIO_VAR,
    coalesce=True,
    steps=[
        Step(color=(180, 0, 255), brightness=200, transition_ms=200, hold_ms=600),
        Step(color=(255, 255, 255), brightness=200, transition_ms=200, hold_ms=400),
        Step(color=(180, 0, 255), brightness=200, transition_ms=200, hold_ms=600),
    ],
)

BIG_CHANCE = Effect(
    id="big_chance",
    priority=PRIO_BIG_CHANCE,
    coalesce=True,
    steps=[
        Step(color="TEAM_COLOR", brightness=255, transition_ms=120, hold_ms=300),
        Step(color="TEAM_SECONDARY", brightness=200, transition_ms=120, hold_ms=300),
    ],
)

KICKOFF = Effect(
    id="kickoff",
    priority=PRIO_KICKOFF,
    coalesce=True,
    steps=[
        Step(color=(255, 255, 255), brightness=200, transition_ms=400, hold_ms=400),
        Step(color="TEAM_COLOR", brightness=255, transition_ms=400, hold_ms=600),
        Step(color="OPPONENT_COLOR", brightness=255, transition_ms=400, hold_ms=600),
    ],
)

HALFTIME = Effect(
    id="halftime",
    priority=PRIO_KICKOFF,
    coalesce=True,
    restore_after=False,  # halftime hands over to ambient
    steps=[
        Step(color=(255, 200, 140), brightness=120, transition_ms=1000, hold_ms=400),
    ],
)

FULLTIME = Effect(
    id="fulltime",
    priority=PRIO_FT,
    coalesce=True,
    restore_after=False,  # winner-color hold; orchestrator restores captured scene later
    steps=[
        Step(color="TEAM_COLOR", brightness=255, transition_ms=400, hold_ms=2000),
        Step(color=(255, 255, 255), brightness=255, transition_ms=400, hold_ms=600),
        Step(color="TEAM_COLOR", brightness=255, transition_ms=400, hold_ms=4000),
    ],
)


KIND_TO_EFFECT: dict[EventKind, Effect] = {
    EventKind.GOAL: GOAL,
    EventKind.OWN_GOAL: GOAL,
    EventKind.PENALTY_GOAL: GOAL,
    EventKind.YELLOW_CARD: YELLOW_CARD,
    EventKind.RED_CARD: RED_CARD,
    EventKind.PENALTY_AWARDED: PENALTY_AWARDED,
    EventKind.VAR: VAR_REVIEW,
    EventKind.BIG_CHANCE: BIG_CHANCE,
    EventKind.KICKOFF: KICKOFF,
    EventKind.HT: HALFTIME,
    EventKind.FT: FULLTIME,
}
