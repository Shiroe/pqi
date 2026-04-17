"""
Structured-match resolver — turns a Polymarket esports MarketData into a
trusted `StructuredMatchRecord` by querying PandaScore.

Flow:
  1. Map the market's category to a PandaScore videogame slug. If no mapping
     exists, return None (resolver doesn't cover this category).
  2. Extract the two competing party names from the question text.
  3. Fetch matches scheduled within ±PANDASCORE_WINDOW_HOURS of the
     market's `event_start_time` for that videogame.
  4. Fuzzy-match candidates against the question's two party names. The
     winner must clear PANDASCORE_FUZZY_THRESHOLD; otherwise return None.
  5. Sub-fetch H2H + each team's recent form in parallel.
  6. Build and return the StructuredMatchRecord.

Every failure path returns None (logged at WARNING). Callers MUST treat
the resolver as best-effort decoration on the briefing — the rest of the
pipeline must continue to work when this returns None.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Iterable

import httpx
from rapidfuzz import fuzz

from pqi import config
from pqi.ingestion import pandascore
from pqi.models import (
    H2HMatch,
    MarketData,
    StructuredMatchRecord,
    TeamForm,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Game mapping
# ---------------------------------------------------------------------------
#
# Polymarket category → PandaScore videogame slug.
#
# Sources:
#   - PandaScore docs (covered titles enumerated at /videogames endpoint).
#   - Polymarket categories observed in the wild on esports markets.
#
# Notes:
#   - LHS keys are Polymarket categories (lowercase, as observed in the wild).
#     RHS values are PandaScore slugs verified against the `/videogames`
#     endpoint response. Never invent a slug — look it up first.
#   - PandaScore kept the `cs-go` slug through the CS2 transition. All three
#     Polymarket aliases ("cs2", "csgo", "counter-strike") map to it.
#   - League of Legends is `league-of-legends` upstream. PandaScore's
#     `lol-wild-rift` slug is a different game and is NOT mapped here.
#   - Anything NOT in this dict returns None from `_game_slug_for` — the
#     resolver short-circuits and the briefing falls back to Tavily-only.

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
    """Map a Polymarket category to a PandaScore slug, or None if unsupported."""
    if not category:
        return None
    return GAME_MAPPING.get(category.strip().lower())


# ---------------------------------------------------------------------------
# Question parsing
# ---------------------------------------------------------------------------


_VS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s+vs\.?\s+", re.IGNORECASE),
    re.compile(r"\s+versus\s+", re.IGNORECASE),
)

_PREFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^[A-Za-z0-9_+ ]+:\s+"),  # "Valorant: ", "CS2: "
    re.compile(r"^(Will|Does|Do|Can|Is)\s+", re.IGNORECASE),
)

_SUFFIX_TOKENS = re.compile(
    r"\s+(?:in|at|for|on|to|win|wins|beat|beats|defeat|defeats)\s+.*$",
    re.IGNORECASE,
)


def _strip_question_chrome(text: str) -> str:
    """Strip prefixes ('Will ', 'Valorant: '), trailing punctuation, and
    parenthetical qualifiers from a question fragment."""
    out = text.strip()
    for pat in _PREFIX_PATTERNS:
        out = pat.sub("", out)
    out = re.split(r"\s*\(", out, maxsplit=1)[0]
    return out.strip(" ?.!").strip()


def _extract_parties(question: str) -> tuple[str, str] | None:
    """
    Extract (yes_side_party, no_side_party) from a market question.

    Convention on Polymarket: the question text reads "<YES party> vs
    <NO party>" (sometimes prefixed with category and a colon). This is
    a best-effort string parse — returns None if no `vs`/`versus` token
    is found in a recognisable position.
    """
    cleaned = _strip_question_chrome(question)
    for pat in _VS_PATTERNS:
        parts = pat.split(cleaned, maxsplit=1)
        if len(parts) == 2:
            a = _strip_question_chrome(parts[0])
            b = _SUFFIX_TOKENS.sub("", _strip_question_chrome(parts[1])).strip()
            if a and b:
                return a, b
    return None


# ---------------------------------------------------------------------------
# Match scoring
# ---------------------------------------------------------------------------


def _opponent_names(match: dict) -> list[str]:
    """Extract opponent display names from a PandaScore match dict.
    Tolerant of the schema variations PandaScore returns (older endpoints
    nest the opponent under `opponent`, newer ones inline name)."""
    names: list[str] = []
    for op in match.get("opponents") or []:
        if not isinstance(op, dict):
            continue
        opp = op.get("opponent") if isinstance(op.get("opponent"), dict) else op
        n = opp.get("name") or opp.get("acronym")
        if isinstance(n, str) and n.strip():
            names.append(n.strip())
    return names


def _opponent_id(match: dict, *, position: int) -> int | str | None:
    """Return the PandaScore team ID of one side of a match (0 or 1).
    None when the match has fewer opponents recorded."""
    ops = match.get("opponents") or []
    if position >= len(ops):
        return None
    op = ops[position]
    opp = op.get("opponent") if isinstance(op.get("opponent"), dict) else op
    return opp.get("id") if isinstance(opp, dict) else None


def _score_candidate(party_a: str, party_b: str, match: dict) -> int:
    """
    Fuzzy-match the two market parties against a candidate match's
    opponent names and return a confidence score (0–100).

    Uses rapidfuzz.token_set_ratio for tolerance to word order /
    sponsor-prefix differences ("KOI" vs "Movistar KOI").

    Score = min(best_a, best_b) — the WORSE of the two side scores so a
    single-team coincidence (e.g. one well-matched team and one wildcard)
    can't drag a wrong match across the threshold.
    """
    candidate_names = _opponent_names(match)
    if len(candidate_names) < 2:
        return 0
    best_a = max(fuzz.token_set_ratio(party_a, n) for n in candidate_names)
    best_b = max(fuzz.token_set_ratio(party_b, n) for n in candidate_names)
    # rapidfuzz returns float; StructuredMatchRecord.fuzzy_score is int 0-100.
    return int(round(min(best_a, best_b)))


def _pick_best_candidate(
    party_a: str,
    party_b: str,
    candidates: Iterable[dict],
) -> tuple[dict, int] | None:
    """Score each candidate and return (winner_dict, score) or None
    when nothing scores above zero."""
    scored = [(m, _score_candidate(party_a, party_b, m)) for m in candidates]
    scored = [s for s in scored if s[1] > 0]
    if not scored:
        return None
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[0]


# ---------------------------------------------------------------------------
# Recent form + H2H builders
# ---------------------------------------------------------------------------


def _is_completed(match: dict) -> bool:
    """A PandaScore match counts as completed when status == 'finished'."""
    return str(match.get("status", "")).lower() == "finished"


def _winner_for_team(match: dict, team_id: int | str) -> str:
    """Return 'win' / 'loss' / 'draw' for a team in a finished match."""
    winner = match.get("winner") or {}
    winner_id = winner.get("id") if isinstance(winner, dict) else None
    if winner_id is None:
        return "draw"
    return "win" if str(winner_id) == str(team_id) else "loss"


def _build_team_form(
    team_name: str, raw_matches: list[dict], team_id: int | str
) -> TeamForm | None:
    """Reduce a list of recent finished matches to a TeamForm snapshot.
    Returns None when nothing is usable."""
    completed = [m for m in raw_matches if _is_completed(m)]
    if not completed:
        return None
    wins = sum(1 for m in completed if _winner_for_team(m, team_id) == "win")
    losses = sum(1 for m in completed if _winner_for_team(m, team_id) == "loss")
    last_at_str = completed[0].get("scheduled_at")
    last_at: datetime | None = None
    if isinstance(last_at_str, str):
        try:
            last_at = datetime.fromisoformat(last_at_str.replace("Z", "+00:00"))
        except ValueError:
            last_at = None
    return TeamForm(
        team_name=team_name,
        matches_considered=len(completed),
        wins=wins,
        losses=losses,
        last_match_at=last_at,
    )


def _build_h2h(raw_matches: list[dict], limit: int = 10) -> list[H2HMatch]:
    """Convert a list of completed PandaScore matches into our H2HMatch model."""
    out: list[H2HMatch] = []
    for m in raw_matches:
        if not _is_completed(m):
            continue
        scheduled_str = m.get("scheduled_at")
        if not isinstance(scheduled_str, str):
            continue
        try:
            scheduled = datetime.fromisoformat(scheduled_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        winner = m.get("winner") or {}
        winner_name = winner.get("name") if isinstance(winner, dict) else None
        results = m.get("results") or []
        score_summary = "?"
        if isinstance(results, list) and len(results) >= 2:
            try:
                score_summary = f"{int(results[0].get('score', 0))}-{int(results[1].get('score', 0))}"
            except (TypeError, ValueError, AttributeError):
                pass
        tournament = m.get("tournament") or {}
        tournament_name = (
            tournament.get("name") if isinstance(tournament, dict) else None
        )
        out.append(
            H2HMatch(
                match_id=str(m.get("id", "")),
                scheduled_at=scheduled,
                winner_name=winner_name if isinstance(winner_name, str) else None,
                score_summary=score_summary,
                tournament_name=tournament_name
                if isinstance(tournament_name, str)
                else None,
            )
        )
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _parse_scheduled(match: dict) -> datetime | None:
    s = match.get("scheduled_at")
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalise_status(raw: str | None) -> str:
    """Coerce PandaScore's status string into our four-value Literal."""
    s = (raw or "").lower()
    if s in {"running", "live"}:
        return "running"
    if s in {"finished", "ended", "complete"}:
        return "finished"
    if s in {"canceled", "cancelled", "postponed", "forfeit"}:
        return "canceled"
    # Default — covers "not_started", "scheduled", and any unknown value.
    return "scheduled"


async def resolve_match(market: MarketData) -> StructuredMatchRecord | None:
    """
    Resolve a Polymarket market to its PandaScore-canonical match record.

    Returns None when:
      - PandaScore is disabled or no API key is set
      - Market has no `event_start_time` (we need it as the search anchor)
      - Market category isn't covered by GAME_MAPPING
      - Question can't be parsed into two parties
      - No PandaScore match exists in the time window
      - Best fuzzy-match score is below PANDASCORE_FUZZY_THRESHOLD

    Sub-fetches (H2H + each team's form) run in parallel via asyncio.gather.
    Sub-fetch failures degrade to empty list / None TeamForm — they never
    cause the whole resolution to fail.
    """
    if not config.PANDASCORE_ENABLED or not config.PANDASCORE_API_KEY:
        return None
    if market.event_start_time is None:
        logger.debug("resolve_match: %s has no event_start_time — skipping", market.id)
        return None

    slug = _game_slug_for(market.category)
    if slug is None:
        logger.debug(
            "resolve_match: category %r not in GAME_MAPPING — skipping",
            market.category,
        )
        return None

    parties = _extract_parties(market.question)
    if parties is None:
        logger.debug(
            "resolve_match: could not extract parties from question %r",
            market.question[:80],
        )
        return None
    party_a, party_b = parties

    async with pandascore.make_client() as client:
        candidates = await pandascore.list_matches_in_window(
            client,
            slug,
            market.event_start_time,
            config.PANDASCORE_WINDOW_HOURS,
        )
        if not candidates:
            logger.info(
                "resolve_match: no %s matches in ±%.1fh of %s for market %s",
                slug,
                config.PANDASCORE_WINDOW_HOURS,
                market.event_start_time.isoformat(),
                market.id,
            )
            return None

        best = _pick_best_candidate(party_a, party_b, candidates)
        if best is None:
            return None
        match, score = best
        if score < config.PANDASCORE_FUZZY_THRESHOLD:
            logger.info(
                "resolve_match: best candidate score %d below threshold %d "
                "for market %s (parties %r vs %r)",
                score,
                config.PANDASCORE_FUZZY_THRESHOLD,
                market.id,
                party_a,
                party_b,
            )
            return None

        match_id = str(match.get("id", ""))
        scheduled_at = _parse_scheduled(match) or market.event_start_time
        status = _normalise_status(match.get("status"))
        names = _opponent_names(match)
        team_a_name = names[0] if len(names) >= 1 else party_a
        team_b_name = names[1] if len(names) >= 2 else party_b
        team_a_id = _opponent_id(match, position=0)
        team_b_id = _opponent_id(match, position=1)
        match_format = (
            match.get("number_of_games") and f"BO{match.get('number_of_games')}"
        ) or None
        tournament_name = (
            (match.get("tournament") or {}).get("name")
            if isinstance(match.get("tournament"), dict)
            else None
        )
        winner_name = None
        final_score = None
        if status == "finished":
            winner = match.get("winner") or {}
            if isinstance(winner, dict):
                winner_name = (
                    winner.get("name") if isinstance(winner.get("name"), str) else None
                )
            results = match.get("results") or []
            if isinstance(results, list) and len(results) >= 2:
                try:
                    final_score = (
                        f"{int(results[0].get('score', 0))}-"
                        f"{int(results[1].get('score', 0))}"
                    )
                except (TypeError, ValueError, AttributeError):
                    final_score = None

        # Parallel sub-fetches. Skip H2H if either ID is missing.
        h2h_task: asyncio.Future[list[dict]]
        if team_a_id is not None and team_b_id is not None:
            h2h_task = asyncio.ensure_future(
                pandascore.get_h2h(client, team_a_id, team_b_id, count=10),
            )
        else:
            f: asyncio.Future[list[dict]] = asyncio.get_event_loop().create_future()
            f.set_result([])
            h2h_task = f

        form_count = config.PANDASCORE_RECENT_FORM_COUNT
        a_form_task: asyncio.Future[list[dict]] = (
            asyncio.ensure_future(
                pandascore.get_team_past_matches(client, team_a_id, form_count)
            )
            if team_a_id is not None
            else _resolved_future_empty()
        )
        b_form_task: asyncio.Future[list[dict]] = (
            asyncio.ensure_future(
                pandascore.get_team_past_matches(client, team_b_id, form_count)
            )
            if team_b_id is not None
            else _resolved_future_empty()
        )
        h2h_raw, a_recent_raw, b_recent_raw = await asyncio.gather(
            h2h_task,
            a_form_task,
            b_form_task,
        )

    h2h = _build_h2h(h2h_raw, limit=10)
    team_a_form = (
        _build_team_form(team_a_name, a_recent_raw, team_a_id)
        if team_a_id is not None
        else None
    )
    team_b_form = (
        _build_team_form(team_b_name, b_recent_raw, team_b_id)
        if team_b_id is not None
        else None
    )

    record = StructuredMatchRecord(
        provider="pandascore",
        provider_match_id=match_id,
        game=slug,
        scheduled_at=scheduled_at,
        status=status,  # type: ignore[arg-type]
        team_a_name=team_a_name,
        team_b_name=team_b_name,
        match_format=match_format,
        tournament_name=tournament_name if isinstance(tournament_name, str) else None,
        final_score=final_score,
        winner_name=winner_name,
        h2h=h2h,
        team_a_recent_form=team_a_form,
        team_b_recent_form=team_b_form,
        fuzzy_score=score,
        fetched_at=datetime.now(timezone.utc),
    )
    logger.info(
        "resolve_match: %s → PandaScore %s (%s vs %s, status=%s, fuzzy=%d)",
        market.id,
        match_id,
        team_a_name,
        team_b_name,
        status,
        score,
    )
    return record


def _resolved_future_empty() -> asyncio.Future[list[dict]]:
    """Tiny helper: a pre-resolved Future returning [] so we can use it
    in `asyncio.gather` without conditionals at the call site."""
    f: asyncio.Future[list[dict]] = asyncio.get_event_loop().create_future()
    f.set_result([])
    return f
