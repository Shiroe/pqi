# pqi/ingestion/synthesizer.py
"""
Tavily-powered research synthesizer.

Given a MarketData object, runs targeted Tavily searches (configurable via
TAVILY_SEARCHES_PER_MARKET, default 5) and condenses the results into a
BriefingDocument for the Council of Seven.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from tavily import TavilyClient

from pqi import config
from pqi.llm.client import chat as llm_chat
from pqi.models import BriefingDocument, MarketData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM prompts (kept short — this is data-cleaning, not a council agent)
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
1. news_summary: 3-5 sentence neutral summary of the current situation.
2. green_flags: up to 5 short strings — signals suggesting YES resolution.
3. red_flags: up to 5 short strings — signals suggesting NO resolution.

Return exactly this JSON (no markdown fences):
{{
  "news_summary": "...",
  "green_flags": ["...", "..."],
  "red_flags": ["...", "..."]
}}"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_key_facts(search_results: list[dict]) -> list[str]:
    """
    Extract concise sentence-level facts from raw Tavily result dicts.

    Each result's "content" field is split into sentences. We keep sentences
    that are ≥ 40 characters and are not bare URLs. Deduplicates case-insensitively.
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


def _parse_llm_response(response: str) -> tuple[str, list[str], list[str]]:
    """
    Parse the LLM JSON response into (news_summary, green_flags, red_flags).

    Strips markdown fences for Ollama compatibility. Falls back gracefully
    if JSON parsing fails.
    """
    # Strip markdown fences (```json ... ```) if the model adds them
    text = re.sub(r"```(?:json)?\s*|\s*```", "", response).strip()

    try:
        data = json.loads(text)
        news_summary = str(data.get("news_summary", "")).strip()
        green_flags = [
            s.strip() for s in data.get("green_flags", []) if str(s).strip()
        ]
        red_flags = [
            s.strip() for s in data.get("red_flags", []) if str(s).strip()
        ]
        return news_summary, green_flags, red_flags
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Could not parse LLM JSON response; using raw text as summary")
        return response[:500].strip(), [], []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def build_briefing(market: MarketData) -> BriefingDocument:
    """
    Orchestrate Tavily searches and produce a BriefingDocument.

    Steps:
      1. Run 3 targeted Tavily searches via asyncio.to_thread (client is sync)
      2. Extract key_facts heuristically from raw search content
      3. Call LLM once to synthesise news_summary, green_flags, red_flags
      4. Assemble and return BriefingDocument

    Raises:
        ValueError:   If TAVILY_API_KEY is not configured.
        RuntimeError: If all 3 Tavily searches return no results.
    """
    if not config.TAVILY_API_KEY:
        raise ValueError(
            "TAVILY_API_KEY is not set. Add it to your .env file."
        )

    tavily = TavilyClient(api_key=config.TAVILY_API_KEY)

    # Query templates — first N are used based on TAVILY_SEARCHES_PER_MARKET config.
    all_query_templates = [
        (f"{market.question} latest news",       "advanced", 7),
        (f"{market.question} prediction analysis","advanced", 7),
        (f"{market.question} odds betting",       "basic",   5),
        (f"{market.question} stats form results", "basic",   5),
        (f"{market.question} expert preview",     "basic",   5),
    ]
    queries = all_query_templates[:config.TAVILY_SEARCHES_PER_MARKET]

    all_results: list[dict] = []
    for query_text, depth, max_res in queries:
        try:
            response = await asyncio.to_thread(
                lambda q=query_text, d=depth, m=max_res: tavily.search(
                    q, max_results=m, search_depth=d
                )
            )
            results: list[dict] = response.get("results", [])
            if results:
                all_results.extend(results)
                logger.debug("Query '%s' [%s] → %d results", query_text, depth, len(results))
            else:
                logger.warning("Tavily returned no results for query: %s", query_text)
        except Exception as exc:
            logger.warning("Tavily query failed ('%s'): %s", query_text, exc)

    if not all_results:
        raise RuntimeError(
            f"All {len(queries)} Tavily searches returned no results for: {market.question!r}"
        )

    logger.info(
        "Tavily: %d total results across %d queries for market %s",
        len(all_results), len(queries), market.id,
    )

    # Step 2 — heuristic key_facts extraction
    key_facts = _extract_key_facts(all_results)

    # Step 3 — build snippet text for LLM, capped at 3000 chars to avoid token blowout
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

    news_summary, green_flags, red_flags = _parse_llm_response(llm_response)

    # Ensure non-empty fields — fallback if LLM returned empty
    if not news_summary:
        news_summary = " ".join(key_facts[:3]) if key_facts else "No summary available."
    if not green_flags:
        green_flags = ["Insufficient data to identify green flags."]
    if not red_flags:
        red_flags = ["Insufficient data to identify red flags."]

    return BriefingDocument(
        market=market,
        key_facts=key_facts,
        news_summary=news_summary,
        red_flags=red_flags,
        green_flags=green_flags,
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

        print("\n--- green_flags ---")
        for flag in briefing.green_flags:
            print(f"  + {flag}")

        print("\n--- red_flags ---")
        for flag in briefing.red_flags:
            print(f"  - {flag}")

        print(f"\n--- data_freshness ---")
        print(f"  {briefing.data_freshness.isoformat()}")

        # Assertions
        assert briefing.market.id == market.id, "market id mismatch"
        assert len(briefing.key_facts) > 0, "key_facts is empty"
        assert len(briefing.news_summary) > 0, "news_summary is empty"
        assert len(briefing.green_flags) > 0, "green_flags is empty"
        assert len(briefing.red_flags) > 0, "red_flags is empty"
        assert briefing.data_freshness is not None, "data_freshness is None"

        print("\n✓ synthesizer.py smoke test complete.")

    asyncio.run(smoke_test())
