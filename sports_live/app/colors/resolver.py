from __future__ import annotations

from ..providers.base import Team
from .palette import hex_to_rgb, lookup_nation

WARM_WHITE: tuple[int, int, int] = (255, 200, 140)


class ColorResolver:
    """Resolve a team's primary color, with this priority:
        1. user override (by team_id)
        2. provider-supplied teamColors.primary
        3. WC-32 hardcoded palette by team name
        4. fall back to neutral white
    """

    def __init__(self) -> None:
        self._overrides: dict[str, tuple[int, int, int]] = {}

    def set_override(self, team_id: str, rgb: tuple[int, int, int] | None) -> None:
        if rgb is None:
            self._overrides.pop(team_id, None)
        else:
            self._overrides[team_id] = rgb

    def overrides(self) -> dict[str, tuple[int, int, int]]:
        return dict(self._overrides)

    def primary(self, team: Team) -> tuple[int, int, int]:
        if team.id in self._overrides:
            return self._overrides[team.id]
        if team.primary_color:
            try:
                return hex_to_rgb(team.primary_color)
            except ValueError:
                pass
        nation = lookup_nation(team.name)
        if nation:
            return hex_to_rgb(nation[0])
        return (255, 255, 255)

    def secondary(self, team: Team) -> tuple[int, int, int]:
        if team.secondary_color:
            try:
                return hex_to_rgb(team.secondary_color)
            except ValueError:
                pass
        nation = lookup_nation(team.name)
        if nation:
            return hex_to_rgb(nation[1])
        return WARM_WHITE
