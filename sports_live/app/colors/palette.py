"""Hardcoded primary colors for the 32 nations qualified / expected at the
2026 FIFA World Cup. Values are flag-derived and are used only as a
fallback when the data provider does not return team colors."""

from __future__ import annotations

# Indexed by lowercased team name (stripping accents/spaces is the caller's job).
NATIONS_2026: dict[str, tuple[str, str]] = {
    # (primary, secondary) hex, no leading "#"
    "argentina":     ("75AADB", "FFFFFF"),
    "australia":     ("FFCD00", "0033A0"),
    "austria":       ("ED2939", "FFFFFF"),
    "belgium":       ("EF3340", "FFD700"),
    "brazil":        ("FFDF00", "009C3B"),
    "canada":        ("D80621", "FFFFFF"),
    "colombia":      ("FFCD00", "003893"),
    "croatia":       ("FF0000", "0000FF"),
    "denmark":       ("C8102E", "FFFFFF"),
    "ecuador":       ("FFD100", "0033A0"),
    "england":       ("FFFFFF", "CE1124"),
    "france":        ("002654", "ED2939"),
    "germany":       ("000000", "DD0000"),
    "ghana":         ("CE1126", "FCD116"),
    "iran":          ("239F40", "DA0000"),
    "italy":         ("0066CC", "FFFFFF"),
    "ivory coast":   ("FF8200", "009E60"),
    "japan":         ("BC002D", "FFFFFF"),
    "korea republic":("002664", "C60C30"),
    "south korea":   ("002664", "C60C30"),
    "mexico":        ("006847", "CE1126"),
    "morocco":       ("C1272D", "006233"),
    "netherlands":   ("FF6C00", "21468B"),
    "norway":        ("BA0C2F", "00205B"),
    "paraguay":      ("D52B1E", "0038A8"),
    "poland":        ("DC143C", "FFFFFF"),
    "portugal":      ("006600", "FF0000"),
    "qatar":         ("8A1538", "FFFFFF"),
    "saudi arabia":  ("006C35", "FFFFFF"),
    "scotland":      ("0065BD", "FFFFFF"),
    "senegal":       ("00853F", "FDEF42"),
    "serbia":        ("C6363C", "0C4076"),
    "spain":         ("AA151B", "F1BF00"),
    "sweden":        ("006AA7", "FECC00"),
    "switzerland":   ("DA291C", "FFFFFF"),
    "tunisia":       ("E70013", "FFFFFF"),
    "turkey":        ("E30A17", "FFFFFF"),
    "uruguay":       ("4DA8DA", "FFFFFF"),
    "usa":           ("B22234", "3C3B6E"),
    "united states": ("B22234", "3C3B6E"),
    "wales":         ("D30731", "00B140"),
}


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    if len(h) != 6:
        return (255, 255, 255)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def lookup_nation(name: str) -> tuple[str, str] | None:
    return NATIONS_2026.get(name.strip().lower())
