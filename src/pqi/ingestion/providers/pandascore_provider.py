"""PandaScore provider adapter (esports).

Wraps the unchanged low-level `pqi.ingestion.pandascore` HTTP client and maps
its raw match dicts into the normalized `CandidateMatch` / `CompletedMatch`
intermediates. All the esports-specific knowledge that used to live in
`match_resolver.py` — the game-slug table, opponent/score/winner parsing, the
status vocabulary — now lives here, behind the `StructuredMatchProvider`
interface.

The mapping is a verbatim port of the previous `match_resolver` logic so a
known esports market resolves to a byte-identical `StructuredMatchRecord`
(regression-guarded — see scripts/smoke_structured_providers.py).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

import httpx

from pqi import config
from pqi.ingestion import pandascore
from pqi.ingestion.providers.base import (
    CandidateMatch,
    CompletedMatch,
    MatchStatus,
    StructuredMatchProvider,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Game mapping — Polymarket category → PandaScore videogame slug.
# (Moved verbatim from match_resolver.py; RHS values verified against the
# PandaScore /videogames endpoint. Never invent a slug — look it up first.)
# ---------------------------------------------------------------------------

csgo = "csgo"
lol = "lol"
lolWildRift = "lol-wild-rift"
mlbb = "mlbb"
dota2 = "dota2"
valorant = "valorant"
r6siege = "r6siege"
overwatch = "ow"
pubg = "pubg"
rocketleague = "rl"
kingOfGlory = "kog"
starcraft2 = "starcraft-2"
starcraft1 = "starcraft-brood-war"
fifa = "fifa"
codMW = "codmw"

GAME_MAPPING: dict[str, str] = {
    # Counter-Strike (PandaScore slug kept as `cs-go` post-CS2 transition)
    "cs2": csgo,
    "csgo": csgo,
    "counter-strike": csgo,
    # League of Legends
    "lol": lol,
    "league of legends": lol,
    # Valorant
    "valorant": valorant,
    "val": valorant,
    # Dota 2
    "dota2": dota2,
    "dota": dota2,
    "dota 2": dota2,
    # Rainbow Six Siege
    "r6": r6siege,
    "r6siege": r6siege,
    "rainbow six siege": r6siege,
    "rainbow six": r6siege,
    # Mobile Legends: Bang Bang
    "mlbb": mlbb,
    "mobile legends": mlbb,
    "mobile legends bang bang": mlbb,
    # Overwatch
    "overwatch": overwatch,
    # PUBG
    "pubg": pubg,
    # Rocket League
    "rocket-league": rocketleague,
    # King of glory
    "king-of-glory": kingOfGlory,
    # Starcraft 2
    "starcraft-2": starcraft2,
    # Starcraft: Brood war
    "starcraft-brood-war": starcraft1,
    # FIFA
    "fifa": fifa,
    # Call of Duty: Modern Warfare
    "codmw": codMW,
}


def _game_slug_for(category: str | None) -> str | None:
    """Map a Polymarket category to a PandaScore slug, or None if unsupported.

    Exact-match first; on miss, tokenise both the category and each mapping key
    on `[-_\\s]+` and look for the key's tokens as a contiguous window inside
    the category's tokens. First match wins (dict insertion order), matching
    Polymarket's convention of placing the game prefix at the start of slugs.
    """
    if not category:
        return None
    cleaned = category.strip().lower()
    if cleaned in GAME_MAPPING:
        return GAME_MAPPING[cleaned]
    tokens = [t for t in re.split(r"[-_\s]+", cleaned) if t]
    if not tokens:
        return None
    for key, slug in GAME_MAPPING.items():
        key_tokens = [t for t in re.split(r"[-_\s]+", key) if t]
        if not key_tokens:
            continue
        n = len(key_tokens)
        for i in range(len(tokens) - n + 1):
            if tokens[i : i + n] == key_tokens:
                return slug
    return None


# ---------------------------------------------------------------------------
# Raw-dict parsing helpers (verbatim port from match_resolver.py)
# ---------------------------------------------------------------------------


def _opponent_names(match: dict) -> list[str]:
    names: list[str] = []
    for op in match.get("opponents") or []:
        if not isinstance(op, dict):
            continue
        opp = op.get("opponent") if isinstance(op.get("opponent"), dict) else op
        n = opp.get("name") or opp.get("acronym")
        if isinstance(n, str) and n.strip():
            names.append(n.strip())
    return names


def _opponent_id(match: dict, *, position: int) -> str | None:
    ops = match.get("opponents") or []
    if position >= len(ops):
        return None
    op = ops[position]
    opp = op.get("opponent") if isinstance(op.get("opponent"), dict) else op
    raw = opp.get("id") if isinstance(opp, dict) else None
    return str(raw) if raw is not None else None


def _normalise_status(raw: str | None) -> MatchStatus:
    s = (raw or "").lower()
    if s in {"running", "live"}:
        return "running"
    if s in {"finished", "ended", "complete"}:
        return "finished"
    if s in {"canceled", "cancelled", "postponed", "forfeit"}:
        return "canceled"
    return "scheduled"


def _parse_scheduled(match: dict) -> datetime | None:
    s = match.get("scheduled_at")
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _score_summary(match: dict) -> str:
    results = match.get("results") or []
    if isinstance(results, list) and len(results) >= 2:
        try:
            return f"{int(results[0].get('score', 0))}-{int(results[1].get('score', 0))}"
        except (TypeError, ValueError, AttributeError):
            pass
    return "?"


def _winner(match: dict) -> tuple[str | None, str | None]:
    """Return (winner_id, winner_name) — both None on draw/forfeit/unknown."""
    winner = match.get("winner") or {}
    if not isinstance(winner, dict):
        return None, None
    wid = winner.get("id")
    wname = winner.get("name")
    return (
        str(wid) if wid is not None else None,
        wname if isinstance(wname, str) else None,
    )


def _tournament_name(match: dict) -> str | None:
    t = match.get("tournament") or {}
    name = t.get("name") if isinstance(t, dict) else None
    return name if isinstance(name, str) else None


def _to_completed(match: dict) -> CompletedMatch | None:
    """Map a finished PandaScore match dict to a CompletedMatch, or None if it
    isn't finished / lacks a scheduled time."""
    if _normalise_status(match.get("status")) != "finished":
        return None
    scheduled = _parse_scheduled(match)
    if scheduled is None:
        return None
    names = _opponent_names(match)
    wid, wname = _winner(match)
    return CompletedMatch(
        provider_match_id=str(match.get("id", "")),
        scheduled_at=scheduled,
        team_a_id=_opponent_id(match, position=0),
        team_a_name=names[0] if len(names) >= 1 else "",
        team_b_id=_opponent_id(match, position=1),
        team_b_name=names[1] if len(names) >= 2 else "",
        score_summary=_score_summary(match),
        winner_id=wid,
        winner_name=wname,
        is_draw=wid is None,
        tournament_name=_tournament_name(match),
    )


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class PandaScoreProvider(StructuredMatchProvider):
    name = "pandascore"
    display_name = "PandaScore"

    @property
    def enabled(self) -> bool:
        return bool(config.PANDASCORE_ENABLED and config.PANDASCORE_API_KEY)

    @property
    def fuzzy_threshold(self) -> int:
        return config.PANDASCORE_FUZZY_THRESHOLD

    @property
    def window_hours(self) -> float:
        return config.PANDASCORE_WINDOW_HOURS

    @property
    def form_count(self) -> int:
        return config.PANDASCORE_RECENT_FORM_COUNT

    def covers(self, category: str | None) -> str | None:
        return _game_slug_for(category)

    def make_client(self) -> httpx.AsyncClient:
        return pandascore.make_client()

    async def list_candidates(
        self,
        client: httpx.AsyncClient,
        key: str,
        center: datetime,
        window_hours: float,
    ) -> list[CandidateMatch]:
        raw = await pandascore.list_matches_in_window(client, key, center, window_hours)
        out: list[CandidateMatch] = []
        for m in raw:
            names = _opponent_names(m)
            if len(names) < 2:  # can't be the matchup; mirrors old ≥2 filter
                continue
            ngames = m.get("number_of_games")
            wid, wname = _winner(m)
            out.append(
                CandidateMatch(
                    provider_match_id=str(m.get("id", "")),
                    team_a_name=names[0],
                    team_b_name=names[1],
                    scheduled_at=_parse_scheduled(m) or center,
                    status=_normalise_status(m.get("status")),
                    team_a_id=_opponent_id(m, position=0),
                    team_b_id=_opponent_id(m, position=1),
                    match_format=f"BO{ngames}" if ngames else None,
                    tournament_name=_tournament_name(m),
                    final_score=_score_summary(m) if _normalise_status(m.get("status")) == "finished" else None,
                    winner_name=wname,
                    game=key,
                )
            )
        return out

    async def fetch_form(
        self, client: httpx.AsyncClient, team_id: str | int, count: int
    ) -> list[CompletedMatch]:
        raw = await pandascore.get_team_past_matches(client, team_id, count)
        return [c for c in (_to_completed(m) for m in raw) if c is not None]

    async def fetch_h2h(
        self, client: httpx.AsyncClient, candidate: CandidateMatch, count: int
    ) -> list[CompletedMatch]:
        if candidate.team_a_id is None or candidate.team_b_id is None:
            return []
        raw = await pandascore.get_h2h(
            client, candidate.team_a_id, candidate.team_b_id, count=count
        )
        return [c for c in (_to_completed(m) for m in raw) if c is not None]
