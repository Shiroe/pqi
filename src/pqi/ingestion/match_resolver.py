"""Structured-match orchestrator — turns a Polymarket `MarketData` into a
trusted `StructuredMatchRecord` by delegating to the first registered
provider that covers the market's category.

Provider-agnostic by design (the adapter layer lives in
`pqi/ingestion/providers/`):

  1. Guard: a structured match needs an `event_start_time` anchor.
  2. Extract the two competing party names from the question (esports markets
     embed "A vs B" in the question) — falling back to the event title for
     sports whose per-market question is phrased "Will X win?" (soccer).
  3. Pick the first enabled provider whose `covers(category)` returns a key.
  4. Delegate to that provider's shared `resolve()` skeleton.

Every failure path returns None (logged). Callers MUST treat the resolver as
best-effort decoration — the pipeline continues to work when it returns None.

This module deliberately contains NO provider-specific knowledge (no game
slugs, no PandaScore/football-data response shapes). Those live in the
respective adapters. See docs/soccer_structured_data_plan.md.
"""

from __future__ import annotations

import logging
import re
from typing import Callable

from pqi.ingestion.providers import get_providers
from pqi.models import MarketData, StructuredMatchRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Question / title parsing (provider-agnostic — both parties from one string)
# ---------------------------------------------------------------------------


_VS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s+vs\.?\s+", re.IGNORECASE),
    re.compile(r"\s+versus\s+", re.IGNORECASE),
)

_PREFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^[A-Za-z0-9_+ ]+:\s+"),  # "Valorant: ", "CS2: ", "LoL: "
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


def _parse_parties(text: str) -> tuple[str, str] | None:
    """Extract (party_a, party_b) from an "A vs B" string, or None."""
    cleaned = _strip_question_chrome(text)
    for pat in _VS_PATTERNS:
        parts = pat.split(cleaned, maxsplit=1)
        if len(parts) == 2:
            a = _strip_question_chrome(parts[0])
            b = _SUFFIX_TOKENS.sub("", _strip_question_chrome(parts[1])).strip()
            if a and b:
                return a, b
    return None


def _extract_parties(market: MarketData) -> tuple[str, str] | None:
    """Best-effort extraction of the two competing parties.

    Tries the market question first (esports moneyline questions embed the full
    "A vs B" matchup), then falls back to the event title (soccer per-market
    questions read "Will X win on <date>?", so the matchup only survives on the
    parent event's title, e.g. "Norway vs. France"). Returns None if neither
    yields two parties.
    """
    parties = _parse_parties(market.question)
    if parties is not None:
        return parties
    if market.event_title:
        return _parse_parties(market.event_title)
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def resolve_match(
    market: MarketData,
    on_step: Callable[[str], None] | None = None,
) -> StructuredMatchRecord | None:
    """Resolve a Polymarket market to a provider-canonical match record.

    `on_step`, when given, receives short human-readable progress strings
    ("querying football-data.org…", "gathering head-to-head + recent form…")
    so the CLI can narrate the structured-data step. Purely cosmetic — never
    affects resolution.

    Returns None when:
      - the market has no `event_start_time` (needed as the search anchor),
      - the question/title can't be parsed into two parties,
      - no enabled provider covers the market's category,
      - or the covering provider finds no match above its confidence threshold.
    """
    if market.event_start_time is None:
        logger.info(
            "structured-data: skipped market %s — no event_start_time", market.id
        )
        return None

    parties = _extract_parties(market)
    if parties is None:
        logger.info(
            "structured-data: could not extract parties from %r for market %s",
            market.question[:80],
            market.id,
        )
        return None
    party_a, party_b = parties

    for provider in get_providers():
        if not provider.enabled:
            continue
        key = provider.covers(market.category)
        if key is None:
            continue
        logger.info(
            "structured-data: %s covers category=%r (key=%s) for market %s",
            provider.name, market.category, key, market.id,
        )
        return await provider.resolve(market, key, party_a, party_b, on_step=on_step)

    logger.info(
        "structured-data: no provider covers category=%r for market %s",
        market.category, market.id,
    )
    return None
