# pqi/ingestion/synthesizer.py
"""
Tavily-powered research synthesizer.

Given a MarketData object, runs targeted Tavily searches (configurable via
TAVILY_SEARCHES_PER_MARKET, default 5) and condenses the results into a
BriefingDocument for the Council of Seven.

Search queries are designed by an LLM "Search Architect" that analyzes the
market data and generates category-aware, timing-aware queries with optimized
Tavily parameters (topic, time_range, include_domains, etc.).
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from tavily import AsyncTavilyClient

from pqi import config
from pqi.llm.client import chat as llm_chat
from pqi.ingestion.providers import provider_display_name
from pqi.models import BriefingDocument, MarketData, StructuredMatchRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal models (not cross-module contracts — stay in synthesizer.py)
# ---------------------------------------------------------------------------

class TavilyQuery(BaseModel):
    """A single search query with full Tavily parameter tuning."""
    query: str
    search_depth: Literal["basic", "advanced"] = "advanced"
    topic: Literal["general", "news", "finance"] = "general"
    max_results: int = Field(default=5, ge=1, le=10)
    time_range: Literal["day", "week", "month", "year"] | None = None
    include_domains: list[str] = Field(default_factory=list)
    exclude_domains: list[str] = Field(default_factory=list)


class SearchPlan(BaseModel):
    """Wrapper for LLM-generated search plan."""
    queries: list[TavilyQuery]


# ---------------------------------------------------------------------------
# Search Architect prompts
# ---------------------------------------------------------------------------

_SEARCH_ARCHITECT_SYSTEM = """\
You are a Search Architect for a prediction market intelligence system. \
Given a market, design optimal web search queries to gather intelligence \
for analysts evaluating whether to buy YES or NO shares.

Each query specifies: query (text), search_depth (basic|advanced), \
topic (general|news|finance), max_results (1-10), \
time_range (day|week|month|year|null), include_domains (list), \
exclude_domains (list).

AUTHORITATIVE SOURCES BY CATEGORY:
- Esports: hltv.org (CS2), vlr.gg (Valorant), liquipedia.net, dust2.us, over.gg
- Sports: espn.com, theathletic.com, transfermarkt.com, flashscore.com
- Crypto/DeFi: defillama.com, coingecko.com, theblock.co, decrypt.co
- Politics: fivethirtyeight.com, politico.com, realclearpolitics.com
- Finance: bloomberg.com, reuters.com, ft.com

TIMING STRATEGY:
- LIVE/IMMINENT (within 24h): time_range="day", topic="news", breaking developments
- THIS WEEK: time_range="week", mix news + analysis
- FUTURE: no time_range, focus on analysis/predictions/historical data

QUERY DESIGN RULES:
1. First query: direct market question + latest developments (advanced, high max_results)
2. For competitive markets (A vs B): include entity-specific queries for EACH side's recent form
3. Include one query targeting expert analysis or odds/predictions
4. Include one query focused on the specific resolution condition/criteria
5. Diversify keywords — don't repeat the same terms across queries
6. Use topic="finance" for crypto/finance, "news" for current events, "general" for analysis
7. Only set include_domains when the category clearly maps to known authoritative sources
8. Keep query text under 400 characters

Respond with ONLY valid JSON — no markdown fences, no explanation."""

_SEARCH_ARCHITECT_USER_TMPL = """\
Market question: {question}
{matchup_line}Category: {category}
Resolution criteria: {resolution_criteria}
Current YES price: {yes_price:.0%}
Volume: ${volume_usd:,.0f}
End date: {end_date}
Event start: {event_start_display}
Time status: {time_status}

Generate exactly {num_queries} search queries as JSON:
{{"queries": [{{"query": "...", "search_depth": "advanced", "topic": "news", \
"max_results": 7, "time_range": "week", "include_domains": [], \
"exclude_domains": []}}]}}"""


# ---------------------------------------------------------------------------
# LLM prompts for synthesis (unchanged)
# ---------------------------------------------------------------------------

_SYNTHESIZER_SYSTEM = """\
You are a research assistant synthesizing news search results into structured \
market intelligence for a prediction market analyst.

TEMPORAL COHERENCE — READ FIRST:
Search engines surface past coverage of recurring matchups (Team A vs Team B \
played multiple times over a season). When a snippet describes a *completed* \
encounter that does not match the upcoming event under analysis, you must NOT \
present it as evidence about how the upcoming event resolved.

Rules:
- The user message gives you TODAY (current UTC date) and the EVENT START \
time / status. Trust those over any date a snippet implies.
- If the event has NOT yet started (status FUTURE / THIS WEEK / IMMINENT) and \
a snippet contains a final scoreline (e.g. "Team A won 2-0", "Team A defeated \
Team B"), that snippet describes a PRIOR meeting. Route it to \
`prior_match_facts`. Do NOT route it to `news_summary`, `side_yes_case`, \
or `side_no_case` as evidence about this market's resolution.
- If the event status is LIVE and a snippet shows a partial score, you may \
treat it as live state — but only if the snippet is dated within the event \
window. Anything older is prior-match history.
- It is ALWAYS safe to treat scoreline snippets as prior context. The cost \
of a false positive is small (an extra H2H bullet); the cost of a false \
negative (treating an old result as the current outcome) is catastrophic.

DEDUPLICATION:
A single underlying fact often appears across multiple snippets in slightly \
different wording. Do not restate the same fact in multiple buckets. If a \
fact belongs in `prior_match_facts`, do not also place a paraphrase of it \
in `key_context` or `side_*_case`. Each bucket should add new information.

Respond with ONLY a valid JSON object — no markdown fences, no explanation."""

_SYNTHESIZER_USER_TMPL = """\
TODAY (UTC): {today}
EVENT START: {event_start_display}
EVENT STATUS: {time_status}
{structured_block}\
Market question: {question}
{matchup_line}
Raw search results:
{snippets}

From the above, extract:
1. news_summary: 3-5 sentence neutral summary of the current situation. \
For competitive markets (e.g. Team A vs Team B), summarize both sides' \
positions, not just one. If the event has not started yet, summarize the \
*setup* — do NOT narrate a result.
2. side_yes_case: up to 5 short strings — evidence and arguments favoring \
the YES outcome. For competitive markets, include BOTH the YES-side's \
strengths AND the NO-side's weaknesses. Do not cite past final scorelines \
as evidence here unless this market itself is about a past event.
3. side_no_case: up to 5 short strings — evidence and arguments favoring \
the NO outcome. Same rule on past scorelines applies.
4. key_context: up to 5 short strings — contextual factors that affect the \
outcome but aren't arguments for either side (e.g. tournament format, \
match stakes, venue, recent form trajectory, game version/patch, \
regulatory timeline). Do NOT duplicate prior_match_facts here.
5. prior_match_facts: up to 5 short strings — completed prior encounters \
between the same parties, each tagged with its date when known \
(e.g. "2024-09-12 — TeamA beat TeamB 2-0 at IEM Cologne"). EMPTY LIST if \
this market refers to its own past resolution, or if no prior matchups \
appear in the snippets.

Return exactly this JSON (no markdown fences):
{{
  "news_summary": "...",
  "side_yes_case": ["...", "..."],
  "side_no_case": ["...", "..."],
  "key_context": ["...", "..."],
  "prior_match_facts": ["...", "..."]
}}"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_key_facts(search_results: list[dict]) -> list[str]:
    """
    Extract concise sentence-level facts from raw Tavily result dicts.

    Each result's "content" field is split into sentences. We keep sentences
    that are >= 40 characters and are not bare URLs. Deduplicates case-insensitively.
    Returns up to 10 facts.
    """
    seen: set[str] = set()
    facts: list[str] = []

    for result in search_results:
        content = result.get("content", "")
        # Split on sentence boundaries (. ! ?) followed by whitespace
        sentences = re.split(r"(?<=[.!?])\s+", content)
        for sentence in sentences:
            sentence = sentence.strip()
            key = sentence.lower()
            if (
                len(sentence) >= 40
                and not sentence.startswith("http")
                and key not in seen
            ):
                seen.add(key)
                facts.append(sentence)
            if len(facts) >= 10:
                return facts

    return facts


def _parse_llm_response(
    response: str,
) -> tuple[str, list[str], list[str], list[str], list[str]]:
    """
    Parse the LLM JSON response into
    (news_summary, side_yes_case, side_no_case, key_context, prior_match_facts).

    Strips markdown fences for Ollama compatibility. Falls back gracefully
    if JSON parsing fails. `prior_match_facts` is empty-list when the LLM
    omits the key (e.g. legacy / non-competitive markets).
    """
    # Strip markdown fences (```json ... ```) if the model adds them
    text = re.sub(r"```(?:json)?\s*|\s*```", "", response).strip()

    try:
        data = json.loads(text)
        news_summary = str(data.get("news_summary", "")).strip()
        side_yes_case = [
            s.strip() for s in data.get("side_yes_case", []) if str(s).strip()
        ]
        side_no_case = [
            s.strip() for s in data.get("side_no_case", []) if str(s).strip()
        ]
        key_context = [
            s.strip() for s in data.get("key_context", []) if str(s).strip()
        ]
        prior_match_facts = [
            s.strip() for s in data.get("prior_match_facts", []) if str(s).strip()
        ]
        return news_summary, side_yes_case, side_no_case, key_context, prior_match_facts
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Could not parse LLM JSON response; using raw text as summary")
        return response[:500].strip(), [], [], [], []


# Past-tense scoreline / completion patterns. Conservative — must contain a
# clear final-result verb AND either a numeric scoreline (2-0, 16-13) or a
# past-tense win/defeat verb. Tuned to avoid matching "Team A beat the spread"
# or "Team A defeated expectations".
_COMPLETION_PATTERNS = [
    re.compile(r"\bwon\s+\d+[-:]\d+", re.IGNORECASE),
    re.compile(r"\bbeat\s+\w[\w\s]{0,30}?\s+\d+[-:]\d+", re.IGNORECASE),
    re.compile(r"\bdefeated\s+\w[\w\s]{0,30}?\s+\d+[-:]\d+", re.IGNORECASE),
    re.compile(r"\b\d+[-:]\d+\s+(?:victory|win|loss|defeat)\b", re.IGNORECASE),
    re.compile(r"\bfinal\s+score\b", re.IGNORECASE),
    re.compile(r"\bsweep(?:ed|s)\s+\w[\w\s]{0,30}?\s+\d+[-:]\d+", re.IGNORECASE),
]


def _detect_completion_contamination(
    market: MarketData,
    news_summary: str,
    side_yes_case: list[str],
    side_no_case: list[str],
    structured_match: StructuredMatchRecord | None = None,
) -> list[str]:
    """
    Mechanical safety net for the temporal coherence rule in
    `_SYNTHESIZER_SYSTEM`. Returns a list of disambiguation warnings.

    Trigger conditions (any one fires):
      A. `event_start_time` is in the future AND a bucket carries past-tense
         final-result language. (Step 1 trigger.)
      B. `structured_match.status` is "scheduled" or "running" AND a bucket
         carries past-tense final-result language. (Step 2 cross-source
         confirmation — when PandaScore says the match isn't finished, any
         past-tense scoreline in the synthesis is contamination regardless
         of how the market's event_start_time looks.)

    Returns empty list when the event is in progress / ended (and no
    structured contradiction exists) or when no completion patterns hit.
    """
    now = datetime.now(timezone.utc)
    structured_says_unfinished = (
        structured_match is not None
        and structured_match.status in {"scheduled", "running"}
    )
    event_in_future = (
        market.event_start_time is not None and market.event_start_time > now
    )
    if not (event_in_future or structured_says_unfinished):
        return []

    haystacks: list[tuple[str, str]] = [("news_summary", news_summary)]
    haystacks.extend((f"side_yes_case[{i}]", s) for i, s in enumerate(side_yes_case))
    haystacks.extend((f"side_no_case[{i}]", s) for i, s in enumerate(side_no_case))

    hits: list[tuple[str, str]] = []
    for label, text in haystacks:
        if not text:
            continue
        for pat in _COMPLETION_PATTERNS:
            m = pat.search(text)
            if m:
                snippet = text[max(0, m.start() - 20) : m.end() + 20]
                hits.append((label, snippet.strip()))
                break  # one hit per haystack is enough for the warning

    if not hits:
        return []

    # Anchor sentence prioritises the structured source when present (it's
    # more authoritative than the market's event_start_time, which can be
    # missing or stale on Polymarket's side).
    if structured_match is not None and structured_says_unfinished:
        anchor = (
            f"{provider_display_name(structured_match.provider)} reports this "
            f"match as {structured_match.status} "
            f"(scheduled {structured_match.scheduled_at.strftime('%Y-%m-%d %H:%M UTC')})"
        )
    elif market.event_start_time is not None:
        anchor = (
            f"this event starts at "
            f"{market.event_start_time.strftime('%Y-%m-%d %H:%M UTC')} "
            f"(in the future)"
        )
    else:
        anchor = "the event has not started yet"

    locations = ", ".join(label for label, _ in hits)
    sample = hits[0][1]
    warning = (
        f"Past-tense match-result language detected in {locations} despite "
        f"{anchor}. Sample: \"{sample}\". Treat any final scoreline as "
        f"a PRIOR encounter, not this market's outcome."
    )
    return [warning]


def _format_structured_block(
    structured_match: StructuredMatchRecord | None,
) -> str:
    """Render the optional `STRUCTURED MATCH RECORD` segment for the
    synthesizer user prompt.

    When present, the synthesizer is told to (a) trust the canonical names
    + status as authoritative and (b) NOT paraphrase the H2H list back into
    `prior_match_facts` — the resolver-supplied list will already populate
    that bucket on the briefing without going through the LLM. Only NEW
    H2H from the snippets should make it into prior_match_facts.

    Returns empty string when structured_match is None — the template line
    becomes a single newline, which is fine.
    """
    if structured_match is None:
        return ""
    sm = structured_match
    h2h_lines = (
        "\n".join(
            f"  - {h.scheduled_at.strftime('%Y-%m-%d')} — "
            f"winner: {h.winner_name or 'unknown'} ({h.score_summary})"
            for h in sm.h2h[:5]
        )
        or "  - (none)"
    )
    return (
        f"\nSTRUCTURED MATCH RECORD ({provider_display_name(sm.provider)} — authoritative):\n"
        f"  Match status: {sm.status}\n"
        f"  Canonical teams: {sm.team_a_name} vs {sm.team_b_name}"
        f"{f' ({sm.match_format})' if sm.match_format else ''}\n"
        f"  Tournament: {sm.tournament_name or '(unknown)'}\n"
        f"  Resolver H2H (already attached to briefing — DO NOT paraphrase \n"
        f"  back into prior_match_facts; only ADD new H2H from snippets if any):\n"
        f"{h2h_lines}\n"
    )


def _extract_entities(question: str) -> tuple[str, str] | None:
    """
    Attempt to extract two competing entities from a market question.

    Looks for patterns like "Will X beat Y", "X vs Y", "X win against Y".
    Returns (entity_a, entity_b) or None if no match.
    """
    # Strip common prefixes and game/category labels like "Valorant: ", "CS2: "
    q = re.sub(r"^[A-Za-z0-9]+:\s+", "", question)
    q = re.sub(r"^(Will|Do|Does|Can|Is)\s+", "", q, flags=re.IGNORECASE)

    # Try splitting on competitive keywords
    for pattern in [
        r"\s+(?:vs\.?|versus)\s+",
        r"\s+beat\s+",
        r"\s+win\s+against\s+",
        r"\s+defeat\s+",
    ]:
        parts = re.split(pattern, q, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            a = parts[0].strip().rstrip("?").strip()
            b = parts[1].strip().rstrip("?").strip()
            # Clean trailing qualifiers like "in Map 1" or "(BO3)"
            b = re.split(r"\s+(?:in|at|\()", b, maxsplit=1)[0].strip()
            if a and b:
                return (a, b)

    return None


def _resolve_matchup(
    market: MarketData,
    structured_match: StructuredMatchRecord | None = None,
) -> tuple[str, str] | None:
    """Best-effort (team_a, team_b) for the market's SPECIFIC fixture.

    Prefers the structured record's canonical names; else parses the market
    question (esports embeds "A vs B"); else the parent event title. Soccer
    per-market questions read "Will X win on <date>?" — a single competitor —
    so without this the opponent never reaches the search/synthesis prompts and
    the corpus drifts to a different fixture involving the same team (e.g. a run
    on "Will France win?" pulling France-vs-Iraq coverage). Anchoring on the
    pairing keeps the briefing on the correct match."""
    if structured_match is not None:
        return structured_match.team_a_name, structured_match.team_b_name
    parsed = _extract_entities(market.question)
    if parsed is not None:
        return parsed
    if market.event_title:
        return _extract_entities(market.event_title)
    return None


def _matchup_line(matchup: tuple[str, str] | None) -> str:
    """Render the directive matchup-anchor line for the search / synthesis
    prompts. Empty string when the pairing is unknown (non-competitive
    markets) so the template collapses cleanly."""
    if matchup is None:
        return ""
    a, b = matchup
    return (
        f"SPECIFIC MATCHUP (anchor every search and all analysis to THIS exact "
        f"pairing — never research one competitor without the opponent): "
        f"{a} vs {b}\n"
    )


# ---------------------------------------------------------------------------
# Search Architect — LLM-driven query planning
# ---------------------------------------------------------------------------

def _compute_time_status(market: MarketData) -> tuple[str, str]:
    """
    Compute event timing display and status for the Search Architect prompt.

    Returns (event_start_display, time_status).
    """
    now = datetime.now(timezone.utc)

    if market.event_start_time is None:
        return "Unknown", "UNKNOWN — no event start time available"

    delta = market.event_start_time - now
    if delta < timedelta(0):
        # Event already started
        return (
            market.event_start_time.strftime("%Y-%m-%d %H:%M UTC"),
            "LIVE — event already started",
        )
    elif delta < timedelta(hours=24):
        hours = delta.total_seconds() / 3600
        return (
            market.event_start_time.strftime("%Y-%m-%d %H:%M UTC"),
            f"IMMINENT — starts in {hours:.1f} hours",
        )
    elif delta < timedelta(days=7):
        days = delta.days
        return (
            market.event_start_time.strftime("%Y-%m-%d %H:%M UTC"),
            f"THIS WEEK — starts in {days} day{'s' if days != 1 else ''}",
        )
    else:
        return (
            market.event_start_time.strftime("%Y-%m-%d"),
            f"FUTURE — event on {market.event_start_time.strftime('%Y-%m-%d')}",
        )


def _fallback_queries(
    market: MarketData,
    matchup: tuple[str, str] | None = None,
) -> list[TavilyQuery]:
    """
    Hardcoded query templates — used when the Search Architect LLM fails.

    This preserves the pre-refactor query logic as a reliable fallback. When a
    `matchup` is known, the matchup string ("A vs B") is used as the search
    subject so the fallback stays anchored on the specific fixture even for
    single-competitor questions ("Will France win?").
    """
    subject = f"{matchup[0]} vs {matchup[1]}" if matchup else market.question
    all_queries = [
        TavilyQuery(
            query=f"{subject} latest news",
            search_depth="advanced",
            topic="news",
            max_results=7,
        ),
        TavilyQuery(
            query=f"{subject} prediction analysis",
            search_depth="advanced",
            max_results=7,
        ),
        TavilyQuery(
            query=f"{subject} odds betting",
            search_depth="basic",
            max_results=5,
        ),
    ]

    entities = matchup or _extract_entities(market.question)
    if entities:
        e1, e2 = entities
        all_queries.extend([
            TavilyQuery(
                query=f"{e1} recent form results stats 2026",
                search_depth="basic",
                max_results=5,
            ),
            TavilyQuery(
                query=f"{e2} recent form results stats 2026",
                search_depth="basic",
                max_results=5,
            ),
        ])
    else:
        all_queries.extend([
            TavilyQuery(
                query=f"{market.question} stats form results",
                search_depth="basic",
                max_results=5,
            ),
            TavilyQuery(
                query=f"{market.question} expert preview",
                search_depth="basic",
                max_results=5,
            ),
        ])

    return all_queries[:config.TAVILY_SEARCHES_PER_MARKET]


async def _plan_searches(
    market: MarketData,
    structured_match: StructuredMatchRecord | None = None,
) -> list[TavilyQuery]:
    """
    Use the Search Architect LLM to design optimal Tavily queries for a market.

    Falls back to hardcoded templates if the LLM call or JSON parsing fails.
    `structured_match` (when resolved) supplies the canonical pairing so the
    queries are anchored on the exact fixture rather than one competitor.
    """
    event_start_display, time_status = _compute_time_status(market)
    matchup = _resolve_matchup(market, structured_match)

    user_msg = _SEARCH_ARCHITECT_USER_TMPL.format(
        question=market.question,
        matchup_line=_matchup_line(matchup),
        category=market.category,
        resolution_criteria=market.resolution_criteria,
        yes_price=market.yes_price,
        volume_usd=market.volume_usd,
        end_date=market.end_date.strftime("%Y-%m-%d %H:%M UTC"),
        event_start_display=event_start_display,
        time_status=time_status,
        num_queries=config.TAVILY_SEARCHES_PER_MARKET,
    )

    try:
        logger.debug("Search Architect: planning %d queries for '%s'",
                      config.TAVILY_SEARCHES_PER_MARKET, market.question[:80])
        raw = await llm_chat(
            system=_SEARCH_ARCHITECT_SYSTEM,
            user=user_msg,
            json_mode=True,
        )

        # Strip markdown fences if present
        text = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        plan = SearchPlan.model_validate_json(text)
        queries = plan.queries[:config.TAVILY_SEARCHES_PER_MARKET]

        if not queries:
            logger.warning("Search Architect returned empty query list; using fallback")
            return _fallback_queries(market, matchup)

        for tq in queries:
            logger.info("Search Architect query: '%s' [depth=%s, topic=%s, time=%s, domains=%s]",
                        tq.query[:80], tq.search_depth, tq.topic,
                        tq.time_range, tq.include_domains or "any")

        return queries

    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("Search Architect JSON/validation failed (%s); using fallback", exc)
        return _fallback_queries(market, matchup)
    except Exception as exc:
        logger.warning("Search Architect LLM call failed (%s); using fallback", exc)
        return _fallback_queries(market, matchup)


# ---------------------------------------------------------------------------
# Tavily search execution
# ---------------------------------------------------------------------------

async def _execute_search(
    client: AsyncTavilyClient, tq: TavilyQuery,
) -> list[dict]:
    """Execute a single TavilyQuery and return results, or empty list on failure."""
    try:
        kwargs: dict = {
            "query": tq.query,
            "search_depth": tq.search_depth,
            "topic": tq.topic,
            "max_results": tq.max_results,
        }
        if tq.time_range:
            kwargs["time_range"] = tq.time_range
        if tq.include_domains:
            kwargs["include_domains"] = tq.include_domains
        if tq.exclude_domains:
            kwargs["exclude_domains"] = tq.exclude_domains

        response = await client.search(**kwargs)
        results: list[dict] = response.get("results", [])
        logger.debug(
            "Tavily '%s' [%s/%s] → %d results",
            tq.query[:60], tq.search_depth, tq.topic, len(results),
        )
        return results
    except Exception as exc:
        logger.warning("Tavily query failed ('%s'): %s", tq.query[:60], exc)
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def build_briefing(
    market: MarketData,
    structured_match: StructuredMatchRecord | None = None,
) -> BriefingDocument:
    """
    Orchestrate Tavily searches and produce a BriefingDocument.

    Steps:
      1. LLM Search Architect designs optimal queries (with fallback)
      2. Execute all queries in parallel via AsyncTavilyClient
      3. Extract key_facts heuristically from raw search content
      4. Call LLM once to synthesise news_summary, side_yes/no_case, key_context
      5. Assemble and return BriefingDocument

    `structured_match` (optional) is the trusted match record from
    `match_resolver.resolve_match`. When present:
      - The synthesizer LLM sees a STRUCTURED MATCH RECORD block and is
        told to treat it as authoritative (and NOT to paraphrase the
        resolver's H2H into prior_match_facts).
      - Its `h2h` list is post-merged into BriefingDocument.prior_match_facts
        so the council always sees those past encounters even if the
        synthesizer skips them.
      - Its `status` strengthens the contamination tagger.
      - It is persisted on the BriefingDocument so the council can render
        the STRUCTURED MATCH DATA block (Task 2f).

    Raises:
        ValueError:   If TAVILY_API_KEY is not configured.
        RuntimeError: If all Tavily searches return no results.
    """
    if not config.TAVILY_API_KEY:
        raise ValueError(
            "TAVILY_API_KEY is not set. Add it to your .env file."
        )

    # Step 1 — LLM-driven query planning
    queries = await _plan_searches(market, structured_match)

    # Step 2 — parallel search execution
    tavily = AsyncTavilyClient(api_key=config.TAVILY_API_KEY)
    results_per_query = await asyncio.gather(
        *[_execute_search(tavily, tq) for tq in queries]
    )
    all_results = [r for batch in results_per_query for r in batch]

    if not all_results:
        raise RuntimeError(
            f"All {len(queries)} Tavily searches returned no results for: {market.question!r}"
        )

    logger.info(
        "Tavily: %d total results across %d queries for market %s",
        len(all_results), len(queries), market.id,
    )

    # Step 3 — heuristic key_facts extraction
    key_facts = _extract_key_facts(all_results)

    # Step 4 — build snippet text for LLM, capped to avoid token blowout
    raw_snippets = "\n\n".join(
        f"[{r.get('title', 'No title')}]\n{r.get('content', '')}"
        for r in all_results
    )
    raw_snippets = raw_snippets[:config.TAVILY_SNIPPET_CAP]

    # Reuse the same temporal anchors the Search Architect saw, so the
    # synthesizer is reading from the same clock the agents will read from.
    event_start_display, time_status = _compute_time_status(market)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    structured_block = _format_structured_block(structured_match)
    matchup_line = _matchup_line(_resolve_matchup(market, structured_match))

    user_msg = _SYNTHESIZER_USER_TMPL.format(
        today=today_str,
        event_start_display=event_start_display,
        time_status=time_status,
        structured_block=structured_block,
        question=market.question,
        matchup_line=matchup_line,
        snippets=raw_snippets,
    )

    logger.debug("Calling LLM for synthesis (snippet_chars=%d)", len(raw_snippets))
    llm_response = await llm_chat(system=_SYNTHESIZER_SYSTEM, user=user_msg)

    (
        news_summary,
        side_yes_case,
        side_no_case,
        key_context,
        prior_match_facts,
    ) = _parse_llm_response(llm_response)

    # Ensure non-empty fields — fallback if LLM returned empty
    if not news_summary:
        news_summary = " ".join(key_facts[:3]) if key_facts else "No summary available."
    if not side_yes_case:
        side_yes_case = ["Insufficient data to build YES case."]
    if not side_no_case:
        side_no_case = ["Insufficient data to build NO case."]
    if not key_context:
        key_context = ["No additional context available."]
    # prior_match_facts: empty-list is the correct default — many markets
    # have no prior encounters between named parties. Do not synthesize one.

    disambiguation_warnings = _detect_completion_contamination(
        market, news_summary, side_yes_case, side_no_case,
        structured_match=structured_match,
    )
    if disambiguation_warnings:
        logger.warning(
            "Disambiguation: %d warning(s) emitted for market %s",
            len(disambiguation_warnings), market.id,
        )

    # Merge resolver-supplied H2H into prior_match_facts. The resolver's
    # records are stable-ID and dated, so they go FIRST; the synthesizer's
    # snippet-derived items follow. Deduplicate naively against the resolver
    # date tag — synthesizer items repeating the same date+winner are dropped
    # to avoid showing the same encounter twice.
    if structured_match is not None and structured_match.h2h:
        resolver_lines: list[str] = []
        resolver_signatures: set[str] = set()
        for h in structured_match.h2h:
            line = (
                f"{h.scheduled_at.strftime('%Y-%m-%d')} — "
                f"winner: {h.winner_name or 'unknown'} ({h.score_summary})"
                f"{f' @ {h.tournament_name}' if h.tournament_name else ''}"
            )
            resolver_lines.append(line)
            sig = f"{h.scheduled_at.strftime('%Y-%m-%d')}|{(h.winner_name or '').lower()}"
            resolver_signatures.add(sig)
        deduped_synth = [
            f for f in prior_match_facts
            if not any(sig.split("|", 1)[0] in f for sig in resolver_signatures)
        ]
        prior_match_facts = resolver_lines + deduped_synth

    return BriefingDocument(
        market=market,
        key_facts=key_facts,
        news_summary=news_summary,
        side_yes_case=side_yes_case,
        side_no_case=side_no_case,
        key_context=key_context,
        prior_match_facts=prior_match_facts,
        disambiguation_warnings=disambiguation_warnings,
        structured_match=structured_match,
        data_freshness=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    import logging
    from datetime import datetime, timezone

    logging.basicConfig(level=logging.INFO)

    async def smoke_test() -> None:
        print("=== pqi/ingestion/synthesizer.py smoke test ===\n")

        # Hardcoded market — no polymarket.py call needed
        market = MarketData(
            id="smoke-001",
            question="Will the Federal Reserve cut interest rates in 2025?",
            resolution_criteria=(
                "Resolves YES if the Federal Reserve announces at least one "
                "federal funds rate cut at any FOMC meeting in calendar year 2025."
            ),
            end_date=datetime(2025, 12, 31, tzinfo=timezone.utc),
            yes_price=0.72,
            volume_usd=500_000.0,
            category="economics",
        )

        print(f"Market: {market.question}")
        print(f"yes_price={market.yes_price}  volume=${market.volume_usd:,.0f}\n")

        print("Running build_briefing()...")
        briefing = await build_briefing(market)

        print("\n--- key_facts ---")
        for i, fact in enumerate(briefing.key_facts, 1):
            print(f"  {i}. {fact[:100]}")

        print("\n--- news_summary ---")
        print(f"  {briefing.news_summary}")

        print("\n--- side_yes_case ---")
        for item in briefing.side_yes_case:
            print(f"  + {item}")

        print("\n--- side_no_case ---")
        for item in briefing.side_no_case:
            print(f"  - {item}")

        print("\n--- key_context ---")
        for item in briefing.key_context:
            print(f"  * {item}")

        print(f"\n--- data_freshness ---")
        print(f"  {briefing.data_freshness.isoformat()}")

        # Assertions
        assert briefing.market.id == market.id, "market id mismatch"
        assert len(briefing.key_facts) > 0, "key_facts is empty"
        assert len(briefing.news_summary) > 0, "news_summary is empty"
        assert len(briefing.side_yes_case) > 0, "side_yes_case is empty"
        assert len(briefing.side_no_case) > 0, "side_no_case is empty"
        assert len(briefing.key_context) > 0, "key_context is empty"
        assert briefing.data_freshness is not None, "data_freshness is None"

        print("\n✓ synthesizer.py smoke test complete.")

    asyncio.run(smoke_test())
