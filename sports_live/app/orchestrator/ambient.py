from __future__ import annotations

import logging
from dataclasses import dataclass

from ..colors.resolver import WARM_WHITE, ColorResolver
from ..providers.base import MatchSummary, Side
from .state_machine import MatchState

log = logging.getLogger(__name__)

RGB = tuple[int, int, int]


@dataclass
class AmbientChoice:
    color: RGB
    brightness: int = 180
    transition_s: float = 1.0


class AmbientResolver:
    """Edge-triggered: returns the desired ambient color for a (state, summary).
    The orchestrator only re-asserts when the resolver's output changes.
    """

    def __init__(self, colors: ColorResolver) -> None:
        self._colors = colors

    def choose(self, state: MatchState, summary: MatchSummary) -> AmbientChoice:
        # Pre-match / halftime / post-match neutral
        if state.is_terminal:
            # FT handled by FULLTIME effect; leave as neutral if we get here.
            return AmbientChoice(color=WARM_WHITE, brightness=140, transition_s=2.0)
        if state.phase.value == "halftime":
            return AmbientChoice(color=WARM_WHITE, brightness=140, transition_s=1.5)
        if state.phase.value == "pre":
            return AmbientChoice(color=WARM_WHITE, brightness=160, transition_s=1.5)

        leader = state.leading_side
        if leader is None:
            return AmbientChoice(color=WARM_WHITE, brightness=180, transition_s=1.0)
        team = summary.home if leader == Side.HOME else summary.away
        return AmbientChoice(color=self._colors.primary(team), brightness=200, transition_s=1.0)
