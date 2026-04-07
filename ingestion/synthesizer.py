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
from pqi.models import BriefingDocument, MarketData

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
Category: {category}
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

_SYNTHESIZER_SYSTEM = (
    "You are a research assistant synthesizing news search results into structured "
    "market intelligence for a prediction market analyst. "
    "Respond with ONLY a valid JSON object — no markdown fences, no explanation."
)

_SYNTHESIZER_USER_TMPL = """\
Market question: {question}

Raw search results:
{snippets}

From the above, extract:
1. news_summary: 3-5 sentence neutral summary of the current situation. \
For competitive markets (e.g. Team A vs Team B), summarize both sides' \
positions, not just one.
2. side_yes_case: up to 5 short strings — evidence and arguments favoring \
the YES outcome. For competitive markets, include BOTH the YES-side's \
strengths AND the NO-side's weaknesses.
3. side_no_case: up to 5 short strings — evidence and arguments favoring \
the NO outcome. For competitive markets, include BOTH the NO-side's \
strengths AND the YES-side's weaknesses.
4. key_context: up to 5 short strings — contextual factors that affect the \
outcome but aren't arguments for either side (e.g. tournament format, \
match stakes, venue, head-to-head record, recent form trajectory, \
game version/patch, regulatory timeline).

Return exactly this JSON (no markdown fences):
{{
  "news_summary": "...",
  "side_yes_case": ["...", "..."],
  "side_no_case": ["...", "..."],
  "key_context": ["...", "..."]
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
) -> tuple[str, list[str], list[str], list[str]]:
    """
    Parse the LLM JSON response into
    (news_summary, side_yes_case, side_no_case, key_context).

    Strips markdown fences for Ollama compatibility. Falls back gracefully
    if JSON parsing fails.
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
        return news_summary, side_yes_case, side_no_case, key_context
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Could not parse LLM JSON response; using raw text as summary")
        return response[:500].strip(), [], [], []


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


def _fallback_queries(market: MarketData) -> list[TavilyQuery]:
    """
    Hardcoded query templates — used when the Search Architect LLM fails.

    This preserves the pre-refactor query logic as a reliable fallback.
    """
    all_queries = [
        TavilyQuery(
            query=f"{market.question} latest news",
            search_depth="advanced",
            topic="news",
            max_results=7,
        ),
        TavilyQuery(
            query=f"{market.question} prediction analysis",
            search_depth="advanced",
            max_results=7,
        ),
        TavilyQuery(
            query=f"{market.question} odds betting",
            search_depth="basic",
            max_results=5,
        ),
    ]

    entities = _extract_entities(market.question)
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


async def _plan_searches(market: MarketData) -> list[TavilyQuery]:
    """
    Use the Search Architect LLM to design optimal Tavily queries for a market.

    Falls back to hardcoded templates if the LLM call or JSON parsing fails.
    """
    event_start_display, time_status = _compute_time_status(market)

    user_msg = _SEARCH_ARCHITECT_USER_TMPL.format(
        question=market.question,
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
            return _fallback_queries(market)

        for tq in queries:
            logger.info("Search Architect query: '%s' [depth=%s, topic=%s, time=%s, domains=%s]",
                        tq.query[:80], tq.search_depth, tq.topic,
                        tq.time_range, tq.include_domains or "any")

        return queries

    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("Search Architect JSON/validation failed (%s); using fallback", exc)
        return _fallback_queries(market)
    except Exception as exc:
        logger.warning("Search Architect LLM call failed (%s); using fallback", exc)
        return _fallback_queries(market)


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

async def build_briefing(market: MarketData) -> BriefingDocument:
    """
    Orchestrate Tavily searches and produce a BriefingDocument.

    Steps:
      1. LLM Search Architect designs optimal queries (with fallback)
      2. Execute all queries in parallel via AsyncTavilyClient
      3. Extract key_facts heuristically from raw search content
      4. Call LLM once to synthesise news_summary, side_yes/no_case, key_context
      5. Assemble and return BriefingDocument

    Raises:
        ValueError:   If TAVILY_API_KEY is not configured.
        RuntimeError: If all Tavily searches return no results.
    """
    if not config.TAVILY_API_KEY:
        raise ValueError(
            "TAVILY_API_KEY is not set. Add it to your .env file."
        )

    # Step 1 — LLM-driven query planning
    queries = await _plan_searches(market)

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

    user_msg = _SYNTHESIZER_USER_TMPL.format(
        question=market.question,
        snippets=raw_snippets,
    )

    logger.debug("Calling LLM for synthesis (snippet_chars=%d)", len(raw_snippets))
    llm_response = await llm_chat(system=_SYNTHESIZER_SYSTEM, user=user_msg)

    news_summary, side_yes_case, side_no_case, key_context = _parse_llm_response(
        llm_response
    )

    # Ensure non-empty fields — fallback if LLM returned empty
    if not news_summary:
        news_summary = " ".join(key_facts[:3]) if key_facts else "No summary available."
    if not side_yes_case:
        side_yes_case = ["Insufficient data to build YES case."]
    if not side_no_case:
        side_no_case = ["Insufficient data to build NO case."]
    if not key_context:
        key_context = ["No additional context available."]

    return BriefingDocument(
        market=market,
        key_facts=key_facts,
        news_summary=news_summary,
        side_yes_case=side_yes_case,
        side_no_case=side_no_case,
        key_context=key_context,
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
