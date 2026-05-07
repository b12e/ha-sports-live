from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx

from .base import (
    BaseProvider,
    EventKind,
    MatchEvent,
    MatchPhase,
    MatchSummary,
    Side,
    Team,
)

log = logging.getLogger(__name__)

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# Curated list of ESPN league slugs we check when looking up an ESPN match for
# a Sofascore-picked fixture. Keep this aligned with what `_is_primary_competition`
# in the Sofascore provider lets through.
ESPN_LEAGUE_SLUGS = (
    "fifa.world",
    "fifa.worldq.uefa",
    "fifa.worldq.conmebol",
    "fifa.worldq.afc",
    "fifa.worldq.concacaf",
    "uefa.champions",
    "uefa.europa",
    "uefa.europa.conf",
    "uefa.euro",
    "uefa.nations",
    "eng.1",
    "esp.1",
    "ger.1",
    "ita.1",
    "fra.1",
    "ned.1",
    "por.1",
    "bel.1",
)

USER_AGENTS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
)


def _ua() -> str:
    return random.choice(USER_AGENTS)


_TEAM_NORMALIZE_SUFFIX_RE = re.compile(
    r"\s+(fc|cf|ac|ud|sc|sk|bk|kv|cd|club|de futbol)$",
    re.IGNORECASE,
)
_TEAM_NORMALIZE_PUNCT_RE = re.compile(r"[^\w\s]")


def _normalize_team(name: str) -> str:
    """Loose team-name canonicalization for cross-source matching.

    'Paris Saint-Germain' / 'paris saint germain' / 'PSG' all collapse to
    something close enough to compare across providers. Not perfect — known
    failure mode is heavy abbreviation ('PSG' vs 'Paris Saint-Germain').
    """
    s = name.lower().strip()
    s = _TEAM_NORMALIZE_SUFFIX_RE.sub("", s)
    s = _TEAM_NORMALIZE_PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _phase_from_status(status_type: dict) -> MatchPhase:
    state = (status_type.get("state") or "").lower()
    detail = (status_type.get("detail") or "").lower()
    desc = (status_type.get("description") or "").lower()
    text = f"{detail} {desc}"
    if state == "post":
        if "abandon" in text:
            return MatchPhase.ABANDONED
        if "postpon" in text:
            return MatchPhase.POSTPONED
        return MatchPhase.FT
    if state == "in":
        if "halftime" in text or detail.strip() == "ht":
            return MatchPhase.HT
        if "extra" in text or " et" in text:
            return MatchPhase.ET
        if "penalt" in text and "shoot" in text:
            return MatchPhase.PEN
        return MatchPhase.LIVE
    return MatchPhase.PRE


def _summary_from_summary_body(body: dict) -> MatchSummary:
    header = body.get("header") or {}
    comps = (header.get("competitions") or [{}])[0]
    competitors = comps.get("competitors") or []
    home_c = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away_c = next((c for c in competitors if c.get("homeAway") == "away"), {})

    home_team = home_c.get("team") or {}
    away_team = away_c.get("team") or {}
    home = Team(id=str(home_team.get("id") or ""), name=home_team.get("displayName", ""))
    away = Team(id=str(away_team.get("id") or ""), name=away_team.get("displayName", ""))

    status_type = (comps.get("status") or {}).get("type") or {}
    phase = _phase_from_status(status_type)

    score_home = int(home_c.get("score") or 0)
    score_away = int(away_c.get("score") or 0)

    kickoff_str = comps.get("date") or header.get("date") or ""
    try:
        kickoff = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
    except ValueError:
        kickoff = datetime.now(UTC)

    league_obj = (header.get("league") or {})
    competition = league_obj.get("name") or league_obj.get("shortName") or ""

    return MatchSummary(
        id=str(header.get("id") or comps.get("id") or ""),
        competition=competition,
        home=home,
        away=away,
        kickoff_utc=kickoff,
        status=status_type.get("description") or status_type.get("detail") or "",
        phase=phase,
        score_home=score_home,
        score_away=score_away,
    )


def _phase_change_kind(phase: MatchPhase) -> EventKind:
    if phase == MatchPhase.LIVE:
        return EventKind.KICKOFF
    if phase == MatchPhase.HT:
        return EventKind.HT
    if phase == MatchPhase.FT:
        return EventKind.FT
    return EventKind.PHASE_CHANGE


def _kind_from_event_type(event_type: dict, event_text: str = "") -> EventKind | None:
    type_text = (event_type.get("text") or "").lower()
    text = f"{type_text} {event_text}".lower()
    if "own goal" in text:
        return EventKind.OWN_GOAL
    if "penalty" in text and "goal" in text:
        return EventKind.PENALTY_GOAL
    if "goal" in text:
        return EventKind.GOAL
    if "yellow" in text and "card" in text:
        return EventKind.YELLOW_CARD
    if "red" in text and "card" in text:
        return EventKind.RED_CARD
    if "penalty" in text and "miss" not in text:
        return EventKind.PENALTY_AWARDED
    if "var" in text:
        return EventKind.VAR
    return None


def _minute_from_clock(clock: dict | None) -> int | None:
    if not clock:
        return None
    raw = clock.get("displayValue") or clock.get("value")
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return int(raw)
    s = str(raw).strip()
    m = re.match(r"(\d+)", s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


class EspnProvider(BaseProvider):
    """ESPN's hidden site.api endpoint as a low-overhead second source.

    Used in tandem with `SofascoreProvider` via `MergedProvider`. ESPN tends
    to lag Sofascore on top European competitions but occasionally beats it
    when Sofascore's upstream feed is delayed — so racing them yields the
    fastest of the two per-event.
    """

    def __init__(self, league_slug: str, *, poll_interval_s: float = 10.0) -> None:
        self._slug = league_slug
        self._poll = poll_interval_s
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=4.0),
            headers={
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.espn.com/",
                "Origin": "https://www.espn.com",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search_matches(self, query: str, *, competition: str | None = None) -> list[MatchSummary]:
        # Cross-source picker uses Sofascore's search; ESPN is only the second
        # leg attached after the user has already picked a Sofascore match.
        return []

    async def get_match(self, match_id: str) -> MatchSummary:
        r = await self._client.get(
            f"{ESPN_BASE}/{self._slug}/summary",
            params={"event": match_id},
            headers={"User-Agent": _ua()},
        )
        r.raise_for_status()
        return _summary_from_summary_body(r.json())

    async def subscribe(self, match_id: str) -> AsyncIterator[MatchEvent]:
        seen_event_keys: set[str] = set()
        last_phase: MatchPhase | None = None
        consecutive_failures = 0
        backoff = 30.0

        while True:
            try:
                r = await self._client.get(
                    f"{ESPN_BASE}/{self._slug}/summary",
                    params={"event": match_id},
                    headers={"User-Agent": _ua()},
                )
                r.raise_for_status()
                body = r.json()
            except httpx.HTTPError as e:
                log.warning("ESPN summary fetch failed: %s", e)
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    await asyncio.sleep(min(backoff, 900))
                    backoff = min(backoff * 2, 900)
                    continue
                await asyncio.sleep(self._poll)
                continue

            consecutive_failures = 0
            backoff = 30.0

            try:
                summary = _summary_from_summary_body(body)
            except Exception:  # noqa: BLE001
                summary = None

            home_id = summary.home.id if summary else None
            away_id = summary.away.id if summary else None

            if summary is not None:
                if last_phase is not None and summary.phase != last_phase:
                    yield MatchEvent(
                        id=f"espn-phase-{summary.phase.value}-{int(asyncio.get_event_loop().time())}",
                        kind=_phase_change_kind(summary.phase),
                        score_home=summary.score_home,
                        score_away=summary.score_away,
                    )
                last_phase = summary.phase
                if summary.phase in (MatchPhase.FT, MatchPhase.ABANDONED, MatchPhase.POSTPONED):
                    return

            for ev in body.get("keyEvents") or []:
                key = str(ev.get("id") or f"{(ev.get('type') or {}).get('id')}-{(ev.get('clock') or {}).get('value')}-{ev.get('text', '')}")
                if key in seen_event_keys:
                    continue
                seen_event_keys.add(key)

                kind = _kind_from_event_type(ev.get("type") or {}, ev.get("text") or "")
                if kind is None:
                    continue

                team = ev.get("team") or {}
                team_id = str(team.get("id") or "") or None
                side: Side | None = None
                if team_id and home_id and team_id == home_id:
                    side = Side.HOME
                elif team_id and away_id and team_id == away_id:
                    side = Side.AWAY

                yield MatchEvent(
                    id=f"espn:{match_id}:{key}",
                    kind=kind,
                    minute=_minute_from_clock(ev.get("clock")),
                    side=side,
                    score_home=summary.score_home if summary else None,
                    score_away=summary.score_away if summary else None,
                    raw=ev,
                )

            await asyncio.sleep(self._poll)


async def find_match_for_summary(
    summary: MatchSummary,
    *,
    league_slugs: tuple[str, ...] = ESPN_LEAGUE_SLUGS,
) -> tuple[str, str] | None:
    """Look up an ESPN `(league_slug, event_id)` matching `summary`, or None.

    Searches ESPN's per-league scoreboard for the kickoff date and matches
    by normalized home + away team names.
    """
    home_norm = _normalize_team(summary.home.name)
    away_norm = _normalize_team(summary.away.name)
    kickoff_date = summary.kickoff_utc.strftime("%Y%m%d")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(8.0, connect=4.0),
        headers={
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.espn.com/",
        },
    ) as client:
        for slug in league_slugs:
            try:
                r = await client.get(
                    f"{ESPN_BASE}/{slug}/scoreboard",
                    params={"dates": kickoff_date},
                    headers={"User-Agent": _ua()},
                )
                if r.status_code != 200:
                    continue
                body = r.json()
            except httpx.HTTPError:
                continue
            for ev in body.get("events") or []:
                comps = (ev.get("competitions") or [{}])[0]
                competitors = comps.get("competitors") or []
                home_name = next(
                    (c.get("team", {}).get("displayName", "")
                     for c in competitors if c.get("homeAway") == "home"),
                    "",
                )
                away_name = next(
                    (c.get("team", {}).get("displayName", "")
                     for c in competitors if c.get("homeAway") == "away"),
                    "",
                )
                if (
                    _normalize_team(home_name) == home_norm
                    and _normalize_team(away_name) == away_norm
                ):
                    return slug, str(ev.get("id"))
    return None
