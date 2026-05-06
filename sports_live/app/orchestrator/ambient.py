from __future__ import annotations

from dataclasses import dataclass

from ..colors.resolver import WARM_WHITE, ColorResolver
from ..providers.base import MatchPhase, MatchSummary, Side
from .sides import PhysicalSide
from .state_machine import MatchState

RGB = tuple[int, int, int]


@dataclass
class AmbientChoice:
    color: RGB
    brightness: int = 180
    transition_s: float = 1.0


@dataclass
class AmbientPlan:
    """Per-position ambient — one choice each for the left, right, and both
    light groups. The orchestrator dispatches these only when they change."""
    left: AmbientChoice
    right: AmbientChoice
    both: AmbientChoice


class AmbientResolver:
    """Edge-triggered resolver: produces an `AmbientPlan` for the current state.
    The orchestrator only re-asserts a position when its color has changed."""

    def __init__(self, colors: ColorResolver) -> None:
        self._colors = colors

    def choose(
        self,
        state: MatchState,
        summary: MatchSummary,
        *,
        home_side: PhysicalSide,
    ) -> AmbientPlan:
        # Halftime / pre-match / terminal phases: warm white everywhere.
        if state.is_terminal:
            warm = AmbientChoice(WARM_WHITE, brightness=140, transition_s=2.0)
            return AmbientPlan(warm, warm, warm)
        if state.phase in (MatchPhase.HT, MatchPhase.PRE):
            warm = AmbientChoice(
                WARM_WHITE,
                brightness=140 if state.phase == MatchPhase.HT else 160,
                transition_s=1.5,
            )
            return AmbientPlan(warm, warm, warm)

        home_color = self._colors.primary(summary.home)
        away_color = self._colors.primary(summary.away)

        left_color = home_color if home_side == "left" else away_color
        right_color = home_color if home_side == "right" else away_color

        # "both"-tagged lights show the leader's color (warm white on tie).
        leader = state.leading_side
        if leader == Side.HOME:
            both_color = home_color
        elif leader == Side.AWAY:
            both_color = away_color
        else:
            both_color = WARM_WHITE

        return AmbientPlan(
            left=AmbientChoice(left_color, brightness=200, transition_s=1.0),
            right=AmbientChoice(right_color, brightness=200, transition_s=1.0),
            both=AmbientChoice(both_color, brightness=200, transition_s=1.0),
        )
