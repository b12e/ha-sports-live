from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Special placeholder colors. Resolved at runtime from match context.
ColorToken = Literal["TEAM_COLOR", "OPPONENT_COLOR", "TEAM_SECONDARY", "WHITE"]


class Step(BaseModel):
    """One frame in an effect sequence.

    `color` is either a concrete (R, G, B) tuple or a runtime token string.
    A `transition_ms` of 0 snaps; otherwise the bulb ramps over that period.
    `hold_ms` is the dwell after the transition completes before moving on.
    """

    color: tuple[int, int, int] | ColorToken | None = None
    brightness: int | None = Field(default=None, ge=0, le=255)
    transition_ms: int = Field(default=0, ge=0, le=10_000)
    hold_ms: int = Field(default=0, ge=0, le=30_000)


class Effect(BaseModel):
    id: str
    priority: int = Field(default=0, ge=0, le=100)
    coalesce: bool = False
    steps: list[Step]
    restore_after: bool = True


# Priority bands.
PRIO_AMBIENT = 0
PRIO_KICKOFF = 20
PRIO_YELLOW = 30
PRIO_BIG_CHANCE = 35
PRIO_PEN_AWARDED = 45
PRIO_GOAL = 50
PRIO_VAR = 55
PRIO_RED = 70
PRIO_FT = 80
