from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class MatchPhase(str, Enum):
    PRE = "pre"
    LIVE = "live"
    HT = "halftime"
    ET = "extra_time"
    PEN = "penalty_shootout"
    FT = "fulltime"
    ABANDONED = "abandoned"
    POSTPONED = "postponed"


class EventKind(str, Enum):
    KICKOFF = "kickoff"
    GOAL = "goal"
    OWN_GOAL = "own_goal"
    PENALTY_GOAL = "penalty_goal"
    YELLOW_CARD = "yellow_card"
    RED_CARD = "red_card"
    PENALTY_AWARDED = "penalty_awarded"
    VAR = "var_review"
    BIG_CHANCE = "big_chance"
    HT = "halftime"
    FT = "fulltime"
    PHASE_CHANGE = "phase_change"


class Side(str, Enum):
    HOME = "home"
    AWAY = "away"


@dataclass
class Team:
    id: str
    name: str
    short_name: str = ""
    primary_color: str | None = None  # "#RRGGBB" if known
    secondary_color: str | None = None


@dataclass
class MatchSummary:
    id: str
    competition: str
    home: Team
    away: Team
    kickoff_utc: datetime
    status: str  # raw provider string
    phase: MatchPhase
    score_home: int = 0
    score_away: int = 0


@dataclass
class MatchEvent:
    id: str
    kind: EventKind
    minute: int | None = None
    side: Side | None = None
    team_id: str | None = None
    player: str | None = None
    score_home: int | None = None  # post-event score
    score_away: int | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw: dict | None = None


class BaseProvider(ABC):
    """Provider contract: search live/upcoming matches and stream events for one match."""

    @abstractmethod
    async def search_matches(self, query: str, *, competition: str | None = None) -> list[MatchSummary]: ...

    @abstractmethod
    async def get_match(self, match_id: str) -> MatchSummary: ...

    @abstractmethod
    async def subscribe(self, match_id: str) -> AsyncIterator[MatchEvent]:
        """Yield MatchEvent objects in real time until the match reaches a terminal phase."""
        ...

    async def aclose(self) -> None:
        """Optional cleanup hook."""
        return None
