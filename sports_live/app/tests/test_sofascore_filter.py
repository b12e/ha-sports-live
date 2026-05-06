from __future__ import annotations

from ..providers.sofascore import _is_primary_competition


def _ev(name: str, priority: int) -> dict:
    return {"tournament": {"name": name, "priority": priority}}


def test_top_continental_leagues_pass():
    assert _is_primary_competition(_ev("UEFA Champions League", 800))
    assert _is_primary_competition(_ev("Premier League 25/26", 617))
    assert _is_primary_competition(_ev("La Liga", 617))
    assert _is_primary_competition(_ev("Eredivisie", 311))


def test_world_cup_passes():
    assert _is_primary_competition(_ev("FIFA World Cup", 1000))
    assert _is_primary_competition(_ev("UEFA European Championship", 950))


def test_youth_competitions_filtered_even_with_priority():
    # An accidentally high-priority youth tournament must still be hidden.
    assert not _is_primary_competition(_ev("Iceland U19 League A", 600))
    assert not _is_primary_competition(_ev("Premier League U21", 500))
    assert not _is_primary_competition(_ev("Primavera 1", 400))


def test_reserves_and_academies_filtered():
    assert not _is_primary_competition(_ev("Bayern Munich II Reserves", 300))
    assert not _is_primary_competition(_ev("Some Academy League", 300))


def test_low_priority_regional_filtered():
    # Real Sofascore tournaments from the user's screenshot.
    assert not _is_primary_competition(_ev("Moscow Championship - Division A", 25))
    assert not _is_primary_competition(_ev("V. Liga - Mazowiecka 1", 5))
    assert not _is_primary_competition(_ev("Guernsey FA Cup", 0))
    assert not _is_primary_competition(_ev("Leinster Senior League Senior Division", 30))


def test_missing_priority_treated_as_zero():
    assert not _is_primary_competition({"tournament": {"name": "Some Random League"}})
    assert not _is_primary_competition({"tournament": {}})
    assert not _is_primary_competition({})
