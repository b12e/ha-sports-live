from __future__ import annotations

from ..colors.palette import hex_to_rgb, lookup_nation
from ..colors.resolver import ColorResolver
from ..providers.base import Team


def test_hex_to_rgb_basic():
    assert hex_to_rgb("FFFFFF") == (255, 255, 255)
    assert hex_to_rgb("#000000") == (0, 0, 0)
    assert hex_to_rgb("ED2939") == (237, 41, 57)


def test_lookup_nation_returns_two_colors():
    pair = lookup_nation("Belgium")
    assert pair is not None and len(pair) == 2


def test_resolver_user_override_wins():
    r = ColorResolver()
    team = Team(id="42", name="Belgium", primary_color="FF00FF")
    r.set_override("42", (10, 20, 30))
    assert r.primary(team) == (10, 20, 30)


def test_resolver_provider_color_used_when_no_override():
    r = ColorResolver()
    team = Team(id="42", name="Belgium", primary_color="ED2939")
    assert r.primary(team) == (237, 41, 57)


def test_resolver_falls_back_to_nation_palette():
    r = ColorResolver()
    team = Team(id="x", name="France")  # no provider color
    assert r.primary(team) == hex_to_rgb("002654")


def test_resolver_white_fallback_for_unknown_team():
    r = ColorResolver()
    team = Team(id="x", name="Unknown FC")
    assert r.primary(team) == (255, 255, 255)
