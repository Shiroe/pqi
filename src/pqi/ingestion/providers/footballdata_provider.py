"""football-data.org provider adapter (soccer).

Wraps the low-level `pqi.ingestion.footballdata` client and maps its v4 Match
schema into the normalized `CandidateMatch` / `CompletedMatch` intermediates.

Schema mapping (verified against the football-data.org v4 docs):
  - status: SCHEDULED/TIMED → scheduled · LIVE/IN_PLAY/PAUSED → running ·
            FINISHED/AWARDED → finished · POSTPONED/SUSPENDED/CANCELLED → canceled
  - utcDate (ISO 8601)            → scheduled_at
  - homeTeam{id,name}            → team_a · awayTeam{id,name} → team_b
  - score.fullTime.{home,away}   → final_score "h-a"
  - score.winner ∈ HOME_TEAM | AWAY_TEAM | DRAW
  - competition.name             → tournament_name

H2H is keyed off the match id (`/matches/{id}/head2head`), not team ids — which
is why the shared `fetch_h2h` hook receives the whole CandidateMatch.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from pqi import config
from pqi.ingestion import footballdata
from pqi.ingestion.providers.base import (
    CandidateMatch,
    CompletedMatch,
    MatchStatus,
    StructuredMatchProvider,
)

logger = logging.getLogger(__name__)

# Polymarket categories on soccer markets are series slugs prefixed `soccer-`
# (e.g. `soccer-fifwc`, `soccer-epl`) or broad tags. Routing matches any of
# these tokens as a substring of the (lowercased) category.
_SOCCER_TOKENS = ("soccer", "football", "fifa-world-cup", "world-cup", "premier-league")

_STATUS_MAP: dict[str, MatchStatus] = {
    "SCHEDULED": "scheduled",
    "TIMED": "scheduled",
    "LIVE": "running",
    "IN_PLAY": "running",
    "PAUSED": "running",
    "FINISHED": "finished",
    "AWARDED": "finished",
    "POSTPONED": "canceled",
    "SUSPENDED": "canceled",
    "CANCELLED": "canceled",
    "CANCELED": "canceled",
}


def _normalise_status(raw: str | None) -> MatchStatus:
    return _STATUS_MAP.get((raw or "").upper(), "scheduled")


def _parse_utc(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _team(side: dict | None) -> tuple[str | None, str]:
    """(team_id, team_name) from a homeTeam/awayTeam object."""
    if not isinstance(side, dict):
        return None, ""
    tid = side.get("id")
    name = side.get("name") or side.get("shortName") or side.get("tla") or ""
    return (str(tid) if tid is not None else None), name


def _full_time(match: dict) -> tuple[int | None, int | None]:
    score = match.get("score") or {}
    ft = score.get("fullTime") if isinstance(score, dict) else None
    if not isinstance(ft, dict):
        return None, None
    return ft.get("home"), ft.get("away")


def _score_summary(match: dict) -> str:
    h, a = _full_time(match)
    if h is None or a is None:
        return "?"
    return f"{int(h)}-{int(a)}"


def _winner(match: dict) -> tuple[str | None, str | None, bool]:
    """Return (winner_id, winner_name, is_draw) for a finished match."""
    score = match.get("score") or {}
    w = score.get("winner") if isinstance(score, dict) else None
    home_id, home_name = _team(match.get("homeTeam"))
    away_id, away_name = _team(match.get("awayTeam"))
    if w == "HOME_TEAM":
        return home_id, home_name, False
    if w == "AWAY_TEAM":
        return away_id, away_name, False
    if w == "DRAW":
        return None, None, True
    return None, None, False


def _tournament(match: dict) -> str | None:
    comp = match.get("competition") or {}
    name = comp.get("name") if isinstance(comp, dict) else None
    return name if isinstance(name, str) else None


def _to_completed(match: dict) -> CompletedMatch | None:
    if _normalise_status(match.get("status")) != "finished":
        return None
    scheduled = _parse_utc(match.get("utcDate"))
    if scheduled is None:
        return None
    home_id, home_name = _team(match.get("homeTeam"))
    away_id, away_name = _team(match.get("awayTeam"))
    wid, wname, is_draw = _winner(match)
    return CompletedMatch(
        provider_match_id=str(match.get("id", "")),
        scheduled_at=scheduled,
        team_a_id=home_id,
        team_a_name=home_name,
        team_b_id=away_id,
        team_b_name=away_name,
        score_summary=_score_summary(match),
        winner_id=wid,
        winner_name=wname,
        is_draw=is_draw,
        tournament_name=_tournament(match),
    )


class FootballDataProvider(StructuredMatchProvider):
    name = "football-data"
    display_name = "football-data.org"

    @property
    def enabled(self) -> bool:
        return bool(config.FOOTBALL_DATA_ENABLED and config.FOOTBALL_DATA_API_KEY)

    @property
    def fuzzy_threshold(self) -> int:
        return config.FOOTBALL_DATA_FUZZY_THRESHOLD

    @property
    def window_hours(self) -> float:
        return config.FOOTBALL_DATA_WINDOW_HOURS

    @property
    def form_count(self) -> int:
        return config.FOOTBALL_DATA_RECENT_FORM_COUNT

    def covers(self, category: str | None) -> str | None:
        if not category:
            return None
        cleaned = category.strip().lower()
        if any(tok in cleaned for tok in _SOCCER_TOKENS):
            return "soccer"
        return None

    def make_client(self) -> httpx.AsyncClient:
        return footballdata.make_client()

    async def list_candidates(
        self,
        client: httpx.AsyncClient,
        key: str,
        center: datetime,
        window_hours: float,
    ) -> list[CandidateMatch]:
        raw = await footballdata.list_matches_in_window(client, center, window_hours)
        cutoff = timedelta(hours=window_hours)
        out: list[CandidateMatch] = []
        for m in raw:
            scheduled = _parse_utc(m.get("utcDate"))
            if scheduled is None:
                continue
            # The endpoint filters by DATE; narrow to the exact ±hour window so
            # we don't pull an unrelated same-day fixture into scoring.
            if abs(scheduled - center) > cutoff:
                continue
            home_id, home_name = _team(m.get("homeTeam"))
            away_id, away_name = _team(m.get("awayTeam"))
            if not home_name or not away_name:
                continue
            status = _normalise_status(m.get("status"))
            wid, wname, _ = _winner(m)
            out.append(
                CandidateMatch(
                    provider_match_id=str(m.get("id", "")),
                    team_a_name=home_name,
                    team_b_name=away_name,
                    scheduled_at=scheduled,
                    status=status,
                    team_a_id=home_id,
                    team_b_id=away_id,
                    match_format=None,  # N/A for soccer
                    tournament_name=_tournament(m),
                    final_score=_score_summary(m) if status == "finished" else None,
                    winner_name=wname,
                    game=key,
                )
            )
        return out

    async def fetch_form(
        self, client: httpx.AsyncClient, team_id: str | int, count: int
    ) -> list[CompletedMatch]:
        raw = await footballdata.get_team_matches(client, team_id, count)
        completed = [c for c in (_to_completed(m) for m in raw) if c is not None]
        # API returns ascending by date; we want newest-first for the snapshot.
        completed.sort(key=lambda c: c.scheduled_at, reverse=True)
        return completed[:count]

    async def fetch_h2h(
        self, client: httpx.AsyncClient, candidate: CandidateMatch, count: int
    ) -> list[CompletedMatch]:
        raw = await footballdata.get_head2head(
            client, candidate.provider_match_id, count=count
        )
        completed = [c for c in (_to_completed(m) for m in raw) if c is not None]
        completed.sort(key=lambda c: c.scheduled_at, reverse=True)
        return completed[:count]
