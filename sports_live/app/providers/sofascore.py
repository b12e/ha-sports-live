from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

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

API_BASE = "https://api.sofascore.com/api/v1"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
]

# Sofascore status codes -> our phases. See https://www.sofascore.com event payloads.
_STATUS_TO_PHASE = {
    "notstarted": MatchPhase.PRE,
    "delayed": MatchPhase.PRE,
    "1st half": MatchPhase.LIVE,
    "2nd half": MatchPhase.LIVE,
    "halftime": MatchPhase.HT,
    "awaiting extra time": MatchPhase.HT,
    "extra time": MatchPhase.ET,
    "1st extra": MatchPhase.ET,
    "2nd extra": MatchPhase.ET,
    "awaiting penalties": MatchPhase.ET,
    "penalties": MatchPhase.PEN,
    "ended": MatchPhase.FT,
    "ap": MatchPhase.FT,  # after penalties
    "aet": MatchPhase.FT,
    "postponed": MatchPhase.POSTPONED,
    "canceled": MatchPhase.ABANDONED,
    "abandoned": MatchPhase.ABANDONED,
    "interrupted": MatchPhase.LIVE,
}

# Tournaments matching this pattern are filtered out of the live picker even
# if their priority is high enough — youth, reserve and academy fixtures
# generally aren't what the user wants to react to in their living room.
_MINOR_COMPETITION_RE = re.compile(
    r"\b(?:U-?\d{2}|youth|academy|reserves?|primavera|junior|juvenil|veterans?)\b",
    re.IGNORECASE,
)

# Sofascore's `tournament.priority` field. Top continental + national leagues
# (UCL, EPL, La Liga, Bundesliga, Serie A, Ligue 1, Eredivisie, Belgian Pro
# League, World Cup, Euro …) sit at 250+, while regional 4th-5th tier and
# youth competitions are mostly <100.
_MIN_TOURNAMENT_PRIORITY = 250


def _is_primary_competition(event_payload: dict) -> bool:
    tournament = event_payload.get("tournament") or {}
    name = tournament.get("name") or ""
    if _MINOR_COMPETITION_RE.search(name):
        return False
    priority = tournament.get("priority") or 0
    return priority >= _MIN_TOURNAMENT_PRIORITY


_INCIDENT_KIND_MAP = {
    "goal": EventKind.GOAL,
    "owngoal": EventKind.OWN_GOAL,
    "penalty": EventKind.PENALTY_GOAL,
    "penaltyMissed": EventKind.PENALTY_AWARDED,
    "card": None,  # resolved via card subtype
    "varDecision": EventKind.VAR,
    "period": None,  # phase change; mapped separately
    "substitution": None,
    "injuryTime": None,
}


def _ua() -> str:
    return random.choice(USER_AGENTS)


def _ja_jitter(base: float, frac: float = 0.2) -> float:
    """Return base * (1 ± frac)."""
    return base * (1.0 + random.uniform(-frac, frac))


def _phase(status_str: str) -> MatchPhase:
    return _STATUS_TO_PHASE.get(status_str.lower(), MatchPhase.LIVE)


def _team_color(team_payload: dict) -> tuple[str | None, str | None]:
    colors = team_payload.get("teamColors") or {}
    primary = colors.get("primary")
    secondary = colors.get("secondary") or colors.get("text")
    return primary, secondary


def _to_team(payload: dict) -> Team:
    primary, secondary = _team_color(payload)
    return Team(
        id=str(payload.get("id")),
        name=payload.get("name", ""),
        short_name=payload.get("shortName", "") or payload.get("nameCode", ""),
        primary_color=primary,
        secondary_color=secondary,
    )


def _to_summary(event_payload: dict) -> MatchSummary:
    home = _to_team(event_payload["homeTeam"])
    away = _to_team(event_payload["awayTeam"])
    status = (event_payload.get("status") or {}).get("description", "") or (
        event_payload.get("status") or {}
    ).get("type", "")
    return MatchSummary(
        id=str(event_payload["id"]),
        competition=(event_payload.get("tournament") or {}).get("name", ""),
        home=home,
        away=away,
        kickoff_utc=datetime.fromtimestamp(event_payload["startTimestamp"], tz=UTC),
        status=status,
        phase=_phase(status),
        score_home=(event_payload.get("homeScore") or {}).get("current", 0) or 0,
        score_away=(event_payload.get("awayScore") or {}).get("current", 0) or 0,
    )


class SofascoreProvider(BaseProvider):
    """Polls api.sofascore.com.

    Adaptive cadence:
      - idle       -> ~poll_idle_s
      - live       -> ~poll_live_s
      - burst      -> ~poll_burst_s for 60s after a goal/card
    Backs off exponentially on 403/429 (Cloudflare).
    """

    def __init__(
        self,
        *,
        poll_idle_s: int = 10,
        poll_live_s: int = 8,
        poll_burst_s: int = 2,
        burst_window_s: int = 60,
    ) -> None:
        self._poll_idle = poll_idle_s
        self._poll_live = poll_live_s
        self._poll_burst = poll_burst_s
        self._burst_window = burst_window_s
        self._client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(8.0, connect=4.0),
            headers={
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.sofascore.com/",
                "Origin": "https://www.sofascore.com",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, *, etag: str | None = None) -> tuple[int, dict | None, str | None]:
        headers = {"User-Agent": _ua()}
        if etag:
            headers["If-None-Match"] = etag
        r = await self._client.get(f"{API_BASE}{path}", headers=headers)
        new_etag = r.headers.get("ETag")
        if r.status_code == 304:
            return 304, None, new_etag
        if r.status_code in (403, 429):
            log.warning("Sofascore %s -> %d", path, r.status_code)
            return r.status_code, None, new_etag
        r.raise_for_status()
        return r.status_code, r.json(), new_etag

    async def search_matches(self, query: str, *, competition: str | None = None) -> list[MatchSummary]:
        # Sofascore's open search endpoint. We filter to event-type results.
        from urllib.parse import quote
        _, body, _ = await self._get(f"/search/all?q={quote(query)}")
        if not body:
            return []
        out: list[MatchSummary] = []
        for entry in body.get("results", []):
            if entry.get("type") != "event":
                continue
            ev = entry.get("entity")
            if not ev:
                continue
            try:
                out.append(_to_summary(ev))
            except (KeyError, TypeError):
                continue
        return out

    async def get_match(self, match_id: str) -> MatchSummary:
        _, body, _ = await self._get(f"/event/{match_id}")
        return _to_summary(body["event"])

    async def live_and_upcoming(self, *, within_min: int = 240) -> list[MatchSummary]:
        """Return football matches that are either currently live or kicking
        off within the next `within_min` minutes. Sorted live-first, then by
        kickoff time."""
        out: dict[str, MatchSummary] = {}

        try:
            _, body, _ = await self._get("/sport/football/events/live")
            for ev in (body or {}).get("events", []) or []:
                if not _is_primary_competition(ev):
                    continue
                try:
                    s = _to_summary(ev)
                    out[s.id] = s
                except (KeyError, TypeError):
                    continue
        except httpx.HTTPError as e:
            log.warning("live football fetch failed: %s", e)

        now = datetime.now(UTC)
        horizon = now + timedelta(minutes=within_min)
        # Sofascore exposes scheduled events one date at a time; check today
        # and tomorrow so we cover matches that cross midnight UTC.
        for delta_days in (0, 1):
            date = (now + timedelta(days=delta_days)).strftime("%Y-%m-%d")
            try:
                _, body, _ = await self._get(f"/sport/football/scheduled-events/{date}")
            except httpx.HTTPError as e:
                log.warning("scheduled (%s) fetch failed: %s", date, e)
                continue
            for ev in (body or {}).get("events", []) or []:
                if not _is_primary_competition(ev):
                    continue
                try:
                    s = _to_summary(ev)
                except (KeyError, TypeError):
                    continue
                if s.id in out:
                    continue  # already in live list
                if s.kickoff_utc <= now or s.kickoff_utc > horizon:
                    continue
                if s.phase in (MatchPhase.FT, MatchPhase.ABANDONED, MatchPhase.POSTPONED):
                    continue
                out[s.id] = s

        live_phases = (MatchPhase.LIVE, MatchPhase.HT, MatchPhase.ET, MatchPhase.PEN)
        return sorted(
            out.values(),
            key=lambda s: (0 if s.phase in live_phases else 1, s.kickoff_utc),
        )

    async def fetch_replay_records(self, match_id: str) -> list[dict]:
        """Pull a finished match's incidents and return replay-format records.

        Past matches don't carry per-incident wall-clock timestamps, so we fake
        them from the minute marker (`time` + `addedTime`). At replay speed=1.0
        the timeline is ~95 minutes long; speed>1 compresses it. We synthesize
        kickoff, halftime, second-half kickoff and fulltime markers around the
        real incidents.
        """
        _, ev_body, _ = await self._get(f"/event/{match_id}")
        summary = _to_summary(ev_body["event"])
        _, inc_body, _ = await self._get(f"/event/{match_id}/incidents")
        incidents = (inc_body or {}).get("incidents") or []

        recs: list[dict] = []
        for inc in reversed(incidents):  # Sofascore returns newest-first
            mev = _incident_to_event(inc, str(match_id), summary)
            if mev is None:
                continue
            minute = inc.get("time") or 0
            added = inc.get("addedTime") or 0
            ts = float((minute + added) * 60)
            rec: dict = {"ts_offset_s": ts, "kind": mev.kind.value, "minute": minute}
            if mev.side:
                rec["side"] = mev.side.value
            if mev.score_home is not None:
                rec["score_home"] = mev.score_home
            if mev.score_away is not None:
                rec["score_away"] = mev.score_away
            recs.append(rec)

        # Synthesize phase markers.
        recs.insert(0, {"ts_offset_s": 0.0, "kind": "kickoff"})
        # Halftime + 2nd half kickoff at the 45' boundary.
        ht_at = 45 * 60 + 30
        ko2_at = ht_at + 30
        # Insert in chronological order.
        recs.append({"ts_offset_s": float(ht_at), "kind": "halftime"})
        recs.append({"ts_offset_s": float(ko2_at), "kind": "kickoff"})
        last_ts = max((r["ts_offset_s"] for r in recs), default=0.0)
        recs.append({"ts_offset_s": last_ts + 30.0, "kind": "fulltime"})
        recs.sort(key=lambda r: r["ts_offset_s"])
        return recs

    async def subscribe(self, match_id: str) -> AsyncIterator[MatchEvent]:
        seen_incident_ids: set[str] = set()
        last_phase: MatchPhase | None = None
        last_summary: MatchSummary | None = None
        burst_until = 0.0
        consecutive_failures = 0
        backoff = 30.0
        event_etag: str | None = None
        inc_etag: str | None = None

        # Initial unconditional /incidents call seeds `seen_incident_ids` with
        # everything that already happened in the match. Without this, the
        # first 200 response inside the loop would yield every past incident,
        # firing flash effects for every old goal/card.
        try:
            _, init_inc_body, inc_etag = await self._get(f"/event/{match_id}/incidents")
            if init_inc_body and "incidents" in init_inc_body:
                for inc in init_inc_body["incidents"]:
                    ev = _incident_to_event(inc, str(match_id), None)
                    if ev is not None:
                        seen_incident_ids.add(ev.id)
            log.info(
                "subscribe(%s): catch-up silenced %d pre-existing incidents",
                match_id, len(seen_incident_ids),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("incidents catch-up failed: %s", e)

        while True:
            now = asyncio.get_event_loop().time()
            in_burst = now < burst_until

            # 1) Pull match summary (score, phase). When /event returns 304
            # we keep using the last good summary so step 2 still runs.
            try:
                code, body, event_etag = await self._get(f"/event/{match_id}", etag=event_etag)
            except httpx.HTTPError as e:
                log.warning("event fetch failed: %s", e)
                code, body = 0, None

            summary: MatchSummary | None = last_summary
            if body and "event" in body:
                summary = _to_summary(body["event"])
                last_summary = summary
                if summary.phase != last_phase:
                    yield MatchEvent(
                        id=f"phase-{summary.phase.value}-{int(now)}",
                        kind=_phase_change_kind(summary.phase),
                        minute=None,
                        score_home=summary.score_home,
                        score_away=summary.score_away,
                    )
                    last_phase = summary.phase
                if summary.phase in (MatchPhase.FT, MatchPhase.ABANDONED, MatchPhase.POSTPONED):
                    return

            # 2) Pull incidents (goals/cards/etc).
            if summary and summary.phase in (MatchPhase.LIVE, MatchPhase.ET, MatchPhase.PEN, MatchPhase.HT):
                try:
                    code, inc_body, inc_etag = await self._get(
                        f"/event/{match_id}/incidents", etag=inc_etag
                    )
                except httpx.HTTPError as e:
                    log.warning("incidents fetch failed: %s", e)
                    inc_body = None
                    code = 0

                if code in (403, 429):
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                if inc_body and "incidents" in inc_body:
                    # Sofascore returns newest-first; reverse for chronological.
                    for inc in reversed(inc_body["incidents"]):
                        ev = _incident_to_event(inc, match_id, summary)
                        if ev is None:
                            continue
                        if ev.id in seen_incident_ids:
                            continue
                        seen_incident_ids.add(ev.id)
                        yield ev
                        if ev.kind in (
                            EventKind.GOAL,
                            EventKind.OWN_GOAL,
                            EventKind.PENALTY_GOAL,
                            EventKind.YELLOW_CARD,
                            EventKind.RED_CARD,
                            EventKind.PENALTY_AWARDED,
                        ):
                            burst_until = asyncio.get_event_loop().time() + self._burst_window

            # 3) Adaptive sleep.
            if consecutive_failures >= 3:
                wait = min(backoff, 900)
                log.warning("sofascore degrading; sleeping %.1fs", wait)
                backoff = min(backoff * 2, 900)
                await asyncio.sleep(_ja_jitter(wait))
                continue

            backoff = 30.0
            if not summary:
                base = self._poll_idle
            elif in_burst:
                base = self._poll_burst
            elif summary.phase in (MatchPhase.LIVE, MatchPhase.ET, MatchPhase.PEN):
                base = self._poll_live
            else:
                base = self._poll_idle
            await asyncio.sleep(_ja_jitter(base))


def _phase_change_kind(phase: MatchPhase) -> EventKind:
    if phase == MatchPhase.LIVE:
        return EventKind.KICKOFF
    if phase == MatchPhase.HT:
        return EventKind.HT
    if phase == MatchPhase.FT:
        return EventKind.FT
    return EventKind.PHASE_CHANGE


def _incident_to_event(inc: dict, match_id: str, summary: MatchSummary | None) -> MatchEvent | None:
    inc_type = inc.get("incidentType")
    inc_class = inc.get("incidentClass")
    inc_id = str(inc.get("id") or f"{inc_type}-{inc.get('time')}-{inc.get('addedTime')}")
    minute = inc.get("time")
    side = None
    is_home = inc.get("isHome")
    if is_home is True:
        side = Side.HOME
    elif is_home is False:
        side = Side.AWAY

    home_score = inc.get("homeScore")
    away_score = inc.get("awayScore")

    if inc_type == "goal":
        if inc_class == "penalty":
            kind = EventKind.PENALTY_GOAL
        elif inc_class == "owngoal":
            kind = EventKind.OWN_GOAL
        else:
            kind = EventKind.GOAL
        return MatchEvent(
            id=f"{match_id}:{inc_id}",
            kind=kind,
            minute=minute,
            side=side,
            score_home=home_score,
            score_away=away_score,
            player=(inc.get("player") or {}).get("name"),
            raw=inc,
        )
    if inc_type == "card":
        sub = (inc.get("incidentClass") or "").lower()
        if "yellow" in sub and "red" not in sub:
            kind = EventKind.YELLOW_CARD
        elif "red" in sub:
            kind = EventKind.RED_CARD
        else:
            return None
        return MatchEvent(
            id=f"{match_id}:{inc_id}",
            kind=kind,
            minute=minute,
            side=side,
            player=(inc.get("player") or {}).get("name"),
            raw=inc,
        )
    if inc_type == "penaltyShot" and inc_class == "missed":
        return None  # noisy; skip
    if inc_type == "varDecision":
        return MatchEvent(
            id=f"{match_id}:{inc_id}",
            kind=EventKind.VAR,
            minute=minute,
            side=side,
            raw=inc,
        )
    return None
