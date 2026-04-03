# pqi/agents/council.py
"""
The Council of Seven — runs all 7 agents in parallel and collects verdicts.

Key responsibilities:
  - Build per-agent user messages (Rules Lawyer gets resolution_criteria injected first)
  - Call pqi.llm.client.chat() concurrently via asyncio.gather()
  - Parse raw JSON responses into AgentVerdict objects (divide 0–100 scores by 100)
  - Log and skip any agent that errors; the council continues with remaining verdicts
"""

import asyncio
import json
import logging
import re

from pqi.agents.personas import AGENT_NAMES, AGENT_PERSONAS
from pqi import config
from pqi.llm.client import chat
from pqi.models import AgentVerdict, BriefingDocument

logger = logging.getLogger(__name__)

# Display names map: persona key → human-readable name shown in AgentVerdict
AGENT_DISPLAY_NAMES: dict[str, str] = {
    "rules_lawyer": "The Rules Lawyer",
    "bull": "The Bull",
    "bear": "The Bear",
    "statistician": "The Base-Rate Statistician",
    "sentiment": "The Sentiment Analyst",
    "specialist": "The Domain Specialist",
    "contrarian": "The Contrarian",
}

# Persona-specific keys that map to AgentVerdict.key_factor, in priority order.
# The first key found in the parsed JSON wins.
_KEY_FACTOR_FIELDS: dict[str, list[str]] = {
    "rules_lawyer": ["key_technical_risk"],
    "bull":         ["primary_catalyst"],
    "bear":         ["primary_blocker"],
    "statistician": ["historical_baseline"],
    "sentiment":    ["market_vibe"],
    "specialist":   ["technical_nuance", "domain_identified"],
    "contrarian":   ["black_swan_event"],
}


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def _format_briefing(briefing: BriefingDocument) -> str:
    """
    Serialise a BriefingDocument to a labelled, human-readable string.
    Used as the body of the user message for every agent.
    """
    m = briefing.market
    facts_block = "\n".join(f"- {f}" for f in briefing.key_facts) or "- (none)"
    green_block = "\n".join(f"- {f}" for f in briefing.green_flags) or "- (none)"
    red_block   = "\n".join(f"- {f}" for f in briefing.red_flags) or "- (none)"

    no_price = 1.0 - m.yes_price
    return (
        f"MARKET QUESTION: {m.question}\n"
        f"CURRENT MARKET PRICES: YES = {m.yes_price:.0%}  |  NO = {no_price:.0%}"
        f"  (buying YES costs ${m.yes_price:.2f}/share; buying NO costs ${no_price:.2f}/share)\n"
        f"CATEGORY: {m.category}\n"
        f"END DATE: {m.end_date.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"VOLUME (USD): ${m.volume_usd:,.0f}\n"
        f"\nRESOLUTION CRITERIA:\n{m.resolution_criteria}\n"
        f"\nKEY FACTS:\n{facts_block}\n"
        f"\nNEWS SUMMARY:\n{briefing.news_summary}\n"
        f"\nGREEN FLAGS (signals for YES):\n{green_block}\n"
        f"\nRED FLAGS (signals for NO):\n{red_block}\n"
        f"\nDATA FRESHNESS: {briefing.data_freshness.strftime('%Y-%m-%d %H:%M UTC')}\n"
    )


def _build_user_message(briefing: BriefingDocument, agent_key: str) -> str:
    """
    Build the user message for the given agent.

    The Rules Lawyer receives the resolution_criteria in an explicit injection
    block BEFORE the full briefing, per CLAUDE.md spec.  All others get the
    standard briefing document only.
    """
    standard = _format_briefing(briefing)

    if agent_key == "rules_lawyer":
        return (
            "RESOLUTION CRITERIA "
            "(SOURCE OF TRUTH — THIS OVERRIDES ALL OTHER CONTEXT):\n"
            f"{briefing.market.resolution_criteria}\n"
            "--- FULL BRIEFING DOCUMENT BELOW ---\n"
            f"{standard}"
        )

    return standard


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_verdict(raw: str, agent_key: str) -> AgentVerdict | None:
    """
    Parse an AgentVerdict from a raw LLM response.

    With json_mode=True the Ollama backend guarantees valid JSON, so a direct
    json.loads() is sufficient. We still strip markdown fences as a safety net
    (the Anthropic backend may add them even when prompted not to).

    Returns None and logs a warning on any parse failure.
    """
    text = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

    try:
        data: dict = json.loads(text)
    except json.JSONDecodeError:
        # Safety net: json_mode=True isn't a 100% guarantee with all models.
        # Strip bare control characters (unescaped \n/\t inside string values)
        # and retry once — structural whitespace is unaffected.
        sanitised = re.sub(r"(?<!\\)[\x00-\x1f]", " ", text)
        try:
            data = json.loads(sanitised)
        except json.JSONDecodeError as exc:
            logger.warning("[%s] JSON parse error: %s", agent_key, exc)
            return None

    try:
        prob_raw  = int(data["probability_score"])
        conf_raw  = int(data["confidence_level"])
        probability = max(0.0, min(1.0, prob_raw / 100))
        confidence  = max(0.0, min(1.0, conf_raw / 100))
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("[%s] Missing/invalid score fields: %s", agent_key, exc)
        return None

    reasoning_raw = data.get("reasoning", "")
    if isinstance(reasoning_raw, list):
        rationale = " ".join(str(s).strip() for s in reasoning_raw if s)
    else:
        rationale = str(reasoning_raw).strip()
    rationale = rationale or "No rationale provided."

    # Find the key_factor from the persona-specific priority list
    key_factor = ""
    for field in _KEY_FACTOR_FIELDS.get(agent_key, []):
        val = data.get(field)
        if val:
            key_factor = str(val).strip()
            break
    if not key_factor:
        # Fallback: first non-score, non-reasoning value in the JSON
        skip = {"probability_score", "confidence_level", "reasoning"}
        for k, v in data.items():
            if k not in skip and v:
                key_factor = str(v).strip()
                break
    if not key_factor:
        key_factor = "Not specified."

    return AgentVerdict(
        agent_name=AGENT_DISPLAY_NAMES.get(agent_key, agent_key),
        probability=probability,
        confidence=confidence,
        rationale=rationale,
        key_factor=key_factor,
    )


# ---------------------------------------------------------------------------
# Single-agent runner
# ---------------------------------------------------------------------------

# Semaphore limiting concurrent LLM calls — critical for local Ollama to avoid OOM.
# OLLAMA_CONCURRENCY defaults to 2 for consumer GPUs; Anthropic backend can use 7.
_llm_semaphore = asyncio.Semaphore(config.OLLAMA_CONCURRENCY)


async def _run_single_agent(
    agent_key: str,
    briefing: BriefingDocument,
) -> AgentVerdict | None:
    """
    Run one agent: build its message, call chat(), parse the response.
    Acquires _llm_semaphore before calling the LLM to cap concurrent requests.
    Returns None if the LLM call or parse step raises — council continues.
    """
    system_prompt = AGENT_PERSONAS[agent_key]
    user_msg      = _build_user_message(briefing, agent_key)

    try:
        async with _llm_semaphore:
            logger.debug("[%s] semaphore acquired — calling LLM", agent_key)
            raw = await chat(system=system_prompt, user=user_msg, json_mode=True)
        logger.debug("[%s] raw response length=%d", agent_key, len(raw))
    except Exception as exc:
        logger.warning("[%s] LLM call failed: %s", agent_key, exc)
        return None

    verdict = _parse_verdict(raw, agent_key)
    if verdict is None:
        logger.warning("[%s] Verdict parse failed — agent skipped", agent_key)
    else:
        logger.debug(
            "[%s] verdict: p=%.2f c=%.2f",
            agent_key, verdict.probability, verdict.confidence,
        )
    return verdict


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def run_council(briefing: BriefingDocument) -> list[AgentVerdict]:
    """
    Run all 7 agents concurrently via asyncio.gather() and return their verdicts.

    Filters out None results (agents that failed).
    Logs a warning if fewer than 4 verdicts are returned (unreliable council).
    Raises RuntimeError if no verdicts at all.
    """
    logger.info(
        "Council starting for market: %s", briefing.market.question[:60]
    )

    coroutines = [_run_single_agent(key, briefing) for key in AGENT_NAMES]
    results = await asyncio.gather(*coroutines)

    verdicts: list[AgentVerdict] = [v for v in results if v is not None]

    if not verdicts:
        raise RuntimeError(
            f"Council produced zero verdicts for market: {briefing.market.question!r}"
        )

    if len(verdicts) < 4:
        logger.warning(
            "Only %d/7 agents returned verdicts — council result is unreliable",
            len(verdicts),
        )

    logger.info("Council complete: %d/7 agents returned verdicts", len(verdicts))
    return verdicts


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    import logging
    from datetime import datetime, timezone

    from pqi.models import BriefingDocument, MarketData

    logging.basicConfig(level=logging.INFO)

    async def smoke_test() -> None:
        print("=== pqi/agents/council.py smoke test ===\n")

        market = MarketData(
            id="smoke-001",
            question="Will the Federal Reserve cut interest rates in 2025?",
            resolution_criteria=(
                "Resolves YES if the Federal Reserve announces at least one "
                "federal funds rate cut at any FOMC meeting in calendar year 2025, "
                "as confirmed by the official Federal Reserve press release."
            ),
            end_date=datetime(2025, 12, 31, tzinfo=timezone.utc),
            yes_price=0.72,
            volume_usd=500_000.0,
            category="economics",
        )

        briefing = BriefingDocument(
            market=market,
            key_facts=[
                "The Fed cut rates by 25bps in September, November, and December 2024.",
                "Inflation fell to 2.7% in November 2024, still above the 2% target.",
                "Fed officials projected 2 cuts in 2025 at the December 2024 meeting.",
                "Labour market remains resilient with unemployment at 4.2%.",
                "Markets are pricing a 72% probability of at least one 2025 cut.",
            ],
            news_summary=(
                "The Federal Reserve concluded 2024 with three consecutive rate cuts, "
                "bringing the fed funds rate to 4.25–4.50%. Fed Chair Powell signalled "
                "a more cautious approach for 2025 given persistent inflation. "
                "Market consensus leans toward one or two cuts in 2025, contingent on "
                "incoming inflation and employment data."
            ),
            green_flags=[
                "Inflation trending downward toward 2% target.",
                "Fed median dot plot projects cuts in 2025.",
                "Slowing GDP growth supports easing bias.",
            ],
            red_flags=[
                "Core PCE still above target at 2.8%.",
                "Strong labour market reduces urgency to cut.",
                "Trump tariff policy may reignite inflation.",
            ],
            data_freshness=datetime.now(timezone.utc),
        )

        print(f"Market: {market.question}")
        print(f"Running all 7 agents in parallel...\n")

        verdicts = await run_council(briefing)

        print(f"Verdicts received: {len(verdicts)}/7\n")
        print(f"{'Agent':<30} {'Prob':>6} {'Conf':>6}  Key Factor")
        print("-" * 80)
        for v in verdicts:
            print(
                f"{v.agent_name:<30} {v.probability:>5.0%} {v.confidence:>5.0%}"
                f"  {v.key_factor[:45]}"
            )

        assert len(verdicts) >= 5, (
            f"Expected at least 5 verdicts, got {len(verdicts)}"
        )
        for v in verdicts:
            assert 0.0 <= v.probability <= 1.0, f"probability out of range: {v}"
            assert 0.0 <= v.confidence  <= 1.0, f"confidence out of range: {v}"
            assert v.agent_name, "agent_name is empty"
            assert v.rationale,  "rationale is empty"
            assert v.key_factor, "key_factor is empty"

        print(f"\n✓ council.py smoke test complete — {len(verdicts)}/7 verdicts.")

    asyncio.run(smoke_test())
