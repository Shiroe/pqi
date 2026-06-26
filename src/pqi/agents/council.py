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
from datetime import datetime, timezone

from pqi.agents.personas import AGENT_NAMES, AGENT_PERSONAS, flags_for_agent
from pqi import config
from pqi.ingestion.providers import provider_display_name
from pqi.llm.client import chat
from pqi.models import AgentVerdict, BriefingDocument, MarketData, TeamForm

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
    "bull": ["primary_catalyst"],
    "bear": ["primary_upset_factor"],
    "statistician": ["historical_baseline"],
    "sentiment": ["market_vibe"],
    "specialist": ["technical_nuance", "domain_identified"],
    "contrarian": ["black_swan_event"],
}


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


def _compute_event_timing(
    end_date: datetime,
    event_start_time: datetime | None = None,
) -> str:
    """
    Compute a human-readable timing block.

    Anchored on `event_start_time` when available (the real-world kickoff),
    falling back to `end_date` (the market resolution deadline) otherwise.
    The anchor matters: a CS match scheduled for "in 3 hours" must NOT be
    described as "resolves in 6 hours" just because the market keeps a
    grace window after the match — that gap is exactly what made past-tense
    Tavily snippets look like reality.

    Returns a multi-line block. The first line shows the active anchor;
    LIVE/IMMINENT also appends an explicit warning that any past-tense
    coverage must describe a *prior* meeting, not this one.
    """
    now = datetime.now(timezone.utc)
    anchor = event_start_time if event_start_time is not None else end_date
    anchor_label = "event start" if event_start_time is not None else "market end"
    delta_seconds = (anchor - now).total_seconds()
    hours = delta_seconds / 3600

    if delta_seconds <= 0:
        # Negative delta on event_start anchor means the event has begun (or
        # finished); on end_date anchor it means the market is past resolution.
        category = "LIVE_OR_PAST" if event_start_time is not None else "EXPIRED"
        if event_start_time is not None:
            elapsed_h = abs(hours)
            if elapsed_h < 1:
                time_str = f"started ~{int(abs(delta_seconds) // 60)} minutes ago"
            elif elapsed_h < 24:
                time_str = f"started ~{elapsed_h:.1f} hours ago"
            else:
                time_str = f"started ~{int(elapsed_h / 24)} days ago"
        else:
            time_str = "already past end date"
    elif hours < 3:
        category = "LIVE/IMMINENT"
        if hours < 1:
            time_str = f"in ~{int(delta_seconds // 60)} minutes"
        else:
            time_str = f"in ~{hours:.1f} hours"
    elif hours < 24:
        category = "TODAY"
        time_str = f"in ~{hours:.0f} hours"
    elif hours < 168:
        days = hours / 24
        category = "THIS WEEK"
        time_str = f"in ~{days:.1f} days"
    else:
        days = hours / 24
        category = "FUTURE"
        time_str = f"in ~{int(days)} days"

    block = f"EVENT TIMING: {category} ({anchor_label} {time_str})"
    if category == "LIVE/IMMINENT":
        block += (
            "\nNOTE: This event is happening NOW or very soon. Price action after "
            "the outcome becomes clear will push shares to ~$0.01/$0.99 before "
            "official resolution.\n"
            "TEMPORAL COHERENCE: Because this specific event has not finished, "
            "any past-tense match coverage in the briefing describes a PRIOR "
            "encounter between the same parties, not this one. Treat it as "
            "head-to-head context, never as the outcome of this market."
        )
    elif category == "TODAY" or category == "THIS WEEK" or category == "FUTURE":
        if event_start_time is not None:
            block += (
                "\nTEMPORAL COHERENCE: This event has not started yet. Any "
                "past-tense scoreline in the briefing describes a PRIOR "
                "encounter — not this market."
            )
    return block


def _extract_outcome_labels(question: str, resolution_criteria: str) -> str:
    """
    Build explicit OUTCOME LABELS block so agents know exactly what YES and NO mean.

    Tries to extract from resolution criteria first (e.g. "Resolves YES if Wolves wins").
    Falls back to a generic label derived from the market question.
    """
    # Try to extract "Resolves YES if <condition>" from resolution criteria
    yes_match = re.search(
        r"[Rr]esolves?\s+YES\s+if\s+(.+?)(?:\.|$)", resolution_criteria
    )
    if yes_match:
        yes_label = yes_match.group(1).strip().rstrip(".")
        return (
            f"OUTCOME LABELS (read carefully — your probability_score = P(YES)):\n"
            f"  YES = {yes_label}\n"
            f"  NO  = the opposite / this does NOT happen\n"
        )

    # Try to extract two entities from "A vs B" pattern in the question
    vs_match = re.search(r"(.+?)\s+(?:vs\.?|versus)\s+(.+?)(?:\s*[\(\-]|$)", question)
    if vs_match:
        entity_a = vs_match.group(1).strip()
        entity_b = vs_match.group(2).strip()
        # Strip common prefixes like "Will", "Valorant:", etc.
        for prefix in ["Will ", "Can ", "Does "]:
            if entity_a.startswith(prefix):
                entity_a = entity_a[len(prefix) :]
        # Strip category prefixes like "Valorant: "
        if ": " in entity_a:
            entity_a = entity_a.split(": ", 1)[1]
        return (
            f"OUTCOME LABELS (read carefully — your probability_score = P(YES)):\n"
            f"  YES = {entity_a} wins → probability_score should be HIGH\n"
            f"  NO  = {entity_b} wins → probability_score should be LOW\n"
        )

    # Fallback: generic labels
    return (
        f"OUTCOME LABELS (read carefully — your probability_score = P(YES)):\n"
        f"  YES = the event described in the question happens\n"
        f"  NO  = it does not happen\n"
    )


def _format_form(f: TeamForm) -> str:
    """Render a recent-form snapshot. W-D-L when draws occur (soccer), W-L
    otherwise — keeping the esports (draws=0) render byte-identical to before."""
    if f.draws:
        return (
            f"{f.wins}W-{f.draws}D-{f.losses}L "
            f"(last {f.matches_considered} matches)"
        )
    return f"{f.wins}W-{f.losses}L (last {f.wins + f.losses} matches)"


def _format_briefing(briefing: BriefingDocument) -> str:
    """
    Serialise a BriefingDocument to a labelled, human-readable string.
    Used as the body of the user message for every agent.
    """
    m = briefing.market
    facts_block = "\n".join(f"- {f}" for f in briefing.key_facts) or "- (none)"
    yes_block = "\n".join(f"- {f}" for f in briefing.side_yes_case) or "- (none)"
    no_block = "\n".join(f"- {f}" for f in briefing.side_no_case) or "- (none)"
    context_block = "\n".join(f"- {f}" for f in briefing.key_context) or "- (none)"

    no_price = 1.0 - m.yes_price
    timing_block = _compute_event_timing(m.end_date, m.event_start_time)

    # Extract outcome labels from resolution criteria for explicit clarity
    outcome_labels = _extract_outcome_labels(m.question, m.resolution_criteria)

    # Optional EVENT START line — only when the API surfaced a start time.
    # When present this is the temporal anchor the agents should reason from,
    # not the END DATE (which is the resolution deadline, often hours later).
    event_start_block = ""
    if m.event_start_time is not None:
        event_start_block = (
            f"EVENT START: {m.event_start_time.strftime('%Y-%m-%d %H:%M UTC')}\n"
        )

    # Optional PRIOR MATCH FACTS — head-to-head history surfaced by the
    # synthesizer when Tavily snippets describe earlier encounters between the
    # same parties. Always rendered under a clearly disambiguating header so
    # the council never reads them as evidence about this market's outcome.
    prior_match_block = ""
    if briefing.prior_match_facts:
        items = "\n".join(f"- {f}" for f in briefing.prior_match_facts)
        prior_match_block = (
            "\nPRIOR MATCH FACTS (HEAD-TO-HEAD HISTORY — NOT THIS EVENT):\n"
            "These describe past encounters between the same parties. "
            "Use them only as form/H2H context. They are NOT proof that "
            "this market's event has resolved.\n"
            f"{items}\n"
        )

    # Optional DISAMBIGUATION WARNINGS — mechanical alerts produced by
    # post-LLM contamination detection (synthesizer.py). Always shown when
    # present; the council MUST treat them as overrides of any conflicting
    # past-tense claim in the news summary.
    disambiguation_block = ""
    if briefing.disambiguation_warnings:
        items = "\n".join(f"- {w}" for w in briefing.disambiguation_warnings)
        disambiguation_block = (
            "\nDISAMBIGUATION WARNINGS (READ FIRST — OVERRIDES NEWS SUMMARY):\n"
            f"{items}\n"
        )

    # Optional STRUCTURED MATCH DATA — authoritative identifiers from the
    # PandaScore match_resolver. When present, the canonical names, status,
    # and form snapshots are far more trustworthy than anything the news
    # summary might imply. Agents should use this to disambiguate team
    # variants in snippets and as a reality-check on the news narrative.
    structured_block = ""
    if briefing.structured_match is not None:
        sm = briefing.structured_match
        provider_label = provider_display_name(sm.provider)
        lines: list[str] = [
            f"\nSTRUCTURED MATCH DATA ({provider_label} — authoritative identifiers):",
            f"- Canonical matchup: {sm.team_a_name} vs {sm.team_b_name}"
            + (f" ({sm.match_format})" if sm.match_format else ""),
            f"- Status: {sm.status.upper()}",
            f"- Scheduled: {sm.scheduled_at.strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        if sm.tournament_name:
            lines.append(f"- Tournament: {sm.tournament_name}")
        if sm.status == "finished":
            if sm.final_score:
                lines.append(f"- Final score: {sm.final_score}")
            if sm.winner_name:
                lines.append(f"- Winner: {sm.winner_name}")
        elif sm.status in {"scheduled", "running"}:
            lines.append(
                f"- NOTE: Match is {sm.status.upper()} — any past-tense "
                "scoreline in the briefing is from a PRIOR encounter."
            )

        if sm.team_a_recent_form is not None:
            lines.append(
                f"- {sm.team_a_name} recent form: {_format_form(sm.team_a_recent_form)}"
            )
        if sm.team_b_recent_form is not None:
            lines.append(
                f"- {sm.team_b_name} recent form: {_format_form(sm.team_b_recent_form)}"
            )
        lines.append(
            f"- Resolver fuzzy confidence: {sm.fuzzy_score}/100"
        )
        structured_block = "\n".join(lines) + "\n"

    # Price history block (conditional — only if CLOB data was fetched)
    price_history_block = ""
    if briefing.price_history is not None:
        ph = briefing.price_history
        lines = [
            f"\nMARKET PRICE HISTORY (last {config.PRICE_HISTORY_INTERVAL}, hourly):",
            f"  YES side ({ph.yes.data_points} points):",
            f"    Weighted Avg (EMA): {ph.yes.weighted_avg:.0%}",
            f"    7-Day Change:       {ph.yes.change_1w_pp:+.1f}pp ({ph.yes.direction})",
            f"    24-Hour Change:     {ph.yes.change_24h_pp:+.1f}pp",
            f"    24-Hour Volatility: {ph.yes.volatility_24h:.1f}pp/hr",
        ]
        if ph.yes.sharp_moves:
            moves_str = "; ".join(
                f"{sm.timestamp.strftime('%b %d %H:%M UTC')} {sm.delta_pp:+.1f}pp"
                for sm in ph.yes.sharp_moves
            )
            lines.append(f"    Sharp Moves:        {moves_str}")
        lines += [
            f"  NO side ({ph.no.data_points} points):",
            f"    Weighted Avg (EMA): {ph.no.weighted_avg:.0%}",
            f"    7-Day Change:       {ph.no.change_1w_pp:+.1f}pp ({ph.no.direction})",
            f"    24-Hour Change:     {ph.no.change_24h_pp:+.1f}pp",
            f"    24-Hour Volatility: {ph.no.volatility_24h:.1f}pp/hr",
        ]
        if ph.no.sharp_moves:
            moves_str = "; ".join(
                f"{sm.timestamp.strftime('%b %d %H:%M UTC')} {sm.delta_pp:+.1f}pp"
                for sm in ph.no.sharp_moves
            )
            lines.append(f"    Sharp Moves:        {moves_str}")
        lines.append(
            f"  NOTE: YES + NO prices may not sum to 100% (spread/vig in orderbook)"
        )
        price_history_block = "\n".join(lines) + "\n"

    return (
        f"MARKET QUESTION: {m.question}\n"
        f"{outcome_labels}"
        f"CURRENT MARKET PRICES: YES = {m.yes_price:.0%}  |  NO = {no_price:.0%}"
        f"  (buying YES costs ${m.yes_price:.2f}/share; buying NO costs ${no_price:.2f}/share)\n"
        f"CATEGORY: {m.category}\n"
        f"{event_start_block}"
        f"END DATE: {m.end_date.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"{timing_block}\n"
        f"{disambiguation_block}"
        f"{structured_block}"
        f"{price_history_block}"
        f"VOLUME (USD): ${m.volume_usd:,.0f}\n"
        f"\nRESOLUTION CRITERIA:\n{m.resolution_criteria}\n"
        f"\nKEY FACTS:\n{facts_block}\n"
        f"\nNEWS SUMMARY:\n{briefing.news_summary}\n"
        f"\nCASE FOR YES:\n{yes_block}\n"
        f"\nCASE FOR NO:\n{no_block}\n"
        f"\nKEY CONTEXT:\n{context_block}\n"
        f"{prior_match_block}"
        f"\nDATA FRESHNESS: {briefing.data_freshness.strftime('%Y-%m-%d %H:%M UTC')}\n"
    )


def _build_side_context(market: MarketData) -> str:
    """
    Build a SIDE CONTEXT block identifying the favored and underdog sides
    based on current market prices. Used by Bull and Bear agents.
    """
    yes_price = market.yes_price
    no_price = 1.0 - yes_price

    if yes_price >= no_price:
        return (
            "SIDE CONTEXT:\n"
            f"FAVORED SIDE: YES (currently priced at {yes_price:.0%})\n"
            f"UNDERDOG SIDE: NO (currently priced at {no_price:.0%})\n"
        )
    else:
        return (
            "SIDE CONTEXT:\n"
            f"FAVORED SIDE: NO (currently priced at {no_price:.0%})\n"
            f"UNDERDOG SIDE: YES (currently priced at {yes_price:.0%})\n"
        )


def _build_user_message(briefing: BriefingDocument, agent_key: str) -> str:
    """
    Build the user message for the given agent.

    The Rules Lawyer receives the resolution_criteria in an explicit injection
    block BEFORE the full briefing, per CLAUDE.md spec.
    Bull and Bear receive a SIDE CONTEXT block identifying favored/underdog.
    All others get the standard briefing document only.
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

    if agent_key == "bull":
        side_ctx = _build_side_context(briefing.market)
        return (
            f"{side_ctx}"
            "You are advocating for the FAVORED side.\n"
            "--- FULL BRIEFING DOCUMENT BELOW ---\n"
            f"{standard}"
        )

    if agent_key == "bear":
        side_ctx = _build_side_context(briefing.market)
        return (
            f"{side_ctx}"
            "You are advocating for the UNDERDOG side.\n"
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
        prob_raw = int(data["probability_score"])
        probability = max(0.0, min(1.0, prob_raw / 100))

        # Option B (session 26): independent per-side probability.
        # If the agent emits `probability_no_score`, parse it as P(NO) and
        # tag the verdict source="independent". Otherwise fall back to a
        # synthetic mirror (probability_no = 1 - probability), which the
        # AgentVerdict validator handles via default values.
        # Lenient policy per Option B plan §5.4: log coherence drift but
        # never reject — the drift is the signal we want to measure.
        probability_no: float | None = None
        probability_source = "synthetic_mirror"
        if "probability_no_score" in data:
            try:
                pn_raw = int(data["probability_no_score"])
                probability_no = max(0.0, min(1.0, pn_raw / 100))
                probability_source = "independent"
                drift = abs(probability + probability_no - 1.0)
                if drift > 0.3:
                    logger.warning(
                        "[%s] Coherence drift |p_yes + p_no - 1| = %.2f "
                        "(p_yes=%.2f, p_no=%.2f) — accepting (lenient)",
                        agent_key,
                        drift,
                        probability,
                        probability_no,
                    )
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "[%s] Malformed probability_no_score (%r) — falling back "
                    "to synthetic mirror: %s",
                    agent_key,
                    data.get("probability_no_score"),
                    exc,
                )
                probability_no = None
                probability_source = "synthetic_mirror"

        # Two-sided confidence: confidence_yes and confidence_no.
        # Fallback: if agent only provided legacy "confidence_level", use it for both.
        if "confidence_yes" in data and "confidence_no" in data:
            conf_yes_raw = int(data["confidence_yes"])
            conf_no_raw = int(data["confidence_no"])
        elif "confidence_level" in data:
            conf_yes_raw = int(data["confidence_level"])
            conf_no_raw = conf_yes_raw
            logger.debug(
                "[%s] Legacy confidence_level fallback → both sides = %d",
                agent_key,
                conf_yes_raw,
            )
        else:
            raise KeyError(
                "Neither confidence_yes/confidence_no nor confidence_level found"
            )

        confidence_yes = max(0.0, min(1.0, conf_yes_raw / 100))
        confidence_no = max(0.0, min(1.0, conf_no_raw / 100))
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

    verdict_kwargs: dict = {
        "agent_name": AGENT_DISPLAY_NAMES.get(agent_key, agent_key),
        "probability": probability,
        "confidence_yes": confidence_yes,
        "confidence_no": confidence_no,
        "rationale": rationale,
        "key_factor": key_factor,
        "prompt_flags": flags_for_agent(agent_key),
    }
    if probability_no is not None:
        # Independent emission — pin both fields and the source flag so the
        # validator does not auto-fill / re-tag as synthetic_mirror.
        verdict_kwargs["probability_yes"] = probability
        verdict_kwargs["probability_no"] = probability_no
        verdict_kwargs["probability_source"] = probability_source
    return AgentVerdict(**verdict_kwargs)


# ---------------------------------------------------------------------------
# Single-agent runner
# ---------------------------------------------------------------------------

# Semaphore limiting concurrent LLM calls — only active for local Ollama to avoid OOM.
# API backends (Anthropic) run all 7 agents fully in parallel.
_llm_semaphore = asyncio.Semaphore(config.LOCAL_LLM_CONCURRENCY)


async def _call_llm(agent_key: str, system_prompt: str, user_msg: str) -> str:
    """
    Call the LLM for a single agent. Ollama uses the semaphore; Anthropic runs free.
    """
    if config.LLM_BACKEND == "ollama":
        async with _llm_semaphore:
            logger.debug("[%s] semaphore acquired — calling LLM", agent_key)
            return await chat(system=system_prompt, user=user_msg, json_mode=True)
    else:
        logger.debug("[%s] calling LLM (no semaphore — API backend)", agent_key)
        return await chat(system=system_prompt, user=user_msg, json_mode=True)


async def _run_single_agent(
    agent_key: str,
    briefing: BriefingDocument,
) -> AgentVerdict | None:
    """
    Run one agent with retry + exponential backoff on failure.

    Retries on both LLM call errors (timeouts, rate limits, 500s) and parse
    failures (malformed JSON). Failed API calls are not billed.

    Returns None only after all retries are exhausted.
    """
    system_prompt = AGENT_PERSONAS[agent_key]
    user_msg = _build_user_message(briefing, agent_key)
    max_retries = config.AGENT_MAX_RETRIES
    max_backoff = 30.0  # seconds cap

    for attempt in range(1 + max_retries):
        # --- LLM call ---
        try:
            raw = await _call_llm(agent_key, system_prompt, user_msg)
            logger.debug("[%s] raw response length=%d", agent_key, len(raw))
        except Exception as exc:
            if attempt < max_retries:
                delay = min(2**attempt, max_backoff)
                logger.warning(
                    "[%s] LLM call failed (attempt %d/%d): %s — retrying in %.0fs",
                    agent_key,
                    attempt + 1,
                    1 + max_retries,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            logger.error(
                "[%s] LLM call failed after %d attempts: %s — agent dropped",
                agent_key,
                1 + max_retries,
                exc,
            )
            return None

        # --- Parse ---
        verdict = _parse_verdict(raw, agent_key)
        if verdict is not None:
            logger.debug(
                "[%s] verdict: p=%.2f cy=%.2f cn=%.2f",
                agent_key,
                verdict.probability,
                verdict.confidence_yes,
                verdict.confidence_no,
            )
            return verdict

        # Parse failed — retry with backoff
        if attempt < max_retries:
            delay = min(2**attempt, max_backoff)
            logger.warning(
                "[%s] Parse failed (attempt %d/%d) — retrying in %.0fs",
                agent_key,
                attempt + 1,
                1 + max_retries,
                delay,
            )
            await asyncio.sleep(delay)
        else:
            logger.error(
                "[%s] Parse failed after %d attempts — agent dropped",
                agent_key,
                1 + max_retries,
            )

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_council(briefing: BriefingDocument) -> list[AgentVerdict]:
    """
    Run all 7 agents concurrently via asyncio.gather() and return their verdicts.

    Each agent retries up to AGENT_MAX_RETRIES times with exponential backoff.
    After all retries, requires at least MIN_COUNCIL_VERDICTS (default 5)
    successful verdicts. Raises RuntimeError if the quorum is not met.
    """
    min_verdicts = config.MIN_COUNCIL_VERDICTS
    logger.info(
        "Council starting for market: %s (min verdicts: %d/7)",
        briefing.market.question[:60],
        min_verdicts,
    )

    coroutines = [_run_single_agent(key, briefing) for key in AGENT_NAMES]
    results = await asyncio.gather(*coroutines)

    verdicts: list[AgentVerdict] = [v for v in results if v is not None]
    failed = len(AGENT_NAMES) - len(verdicts)

    if len(verdicts) < min_verdicts:
        failed_keys = [
            AGENT_DISPLAY_NAMES.get(k, k)
            for k, r in zip(AGENT_NAMES, results)
            if r is None
        ]
        raise RuntimeError(
            f"Council quorum not met: {len(verdicts)}/7 verdicts "
            f"(need {min_verdicts}). Failed agents: {', '.join(failed_keys)}"
        )

    if failed > 0:
        logger.warning(
            "%d/7 agents failed but quorum met (%d ≥ %d) — proceeding",
            failed,
            len(verdicts),
            min_verdicts,
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
            side_yes_case=[
                "Inflation trending downward toward 2% target.",
                "Fed median dot plot projects cuts in 2025.",
                "Slowing GDP growth supports easing bias.",
            ],
            side_no_case=[
                "Core PCE still above target at 2.8%.",
                "Strong labour market reduces urgency to cut.",
                "Trump tariff policy may reignite inflation.",
            ],
            key_context=[
                "Fed cut rates 3 times in 2024 (Sept, Nov, Dec).",
                "Next FOMC meeting: January 2025.",
            ],
            data_freshness=datetime.now(timezone.utc),
        )

        print(f"Market: {market.question}")
        print(f"Running all 7 agents in parallel...\n")

        verdicts = await run_council(briefing)

        print(f"Verdicts received: {len(verdicts)}/7\n")
        print(f"{'Agent':<30} {'Prob':>6} {'CfY':>6} {'CfN':>6}  Key Factor")
        print("-" * 85)
        for v in verdicts:
            print(
                f"{v.agent_name:<30} {v.probability:>5.0%} {v.confidence_yes:>5.0%}"
                f" {v.confidence_no:>5.0%}  {v.key_factor[:40]}"
            )

        assert len(verdicts) >= 5, f"Expected at least 5 verdicts, got {len(verdicts)}"
        for v in verdicts:
            assert 0.0 <= v.probability <= 1.0, f"probability out of range: {v}"
            assert 0.0 <= v.confidence_yes <= 1.0, f"confidence_yes out of range: {v}"
            assert 0.0 <= v.confidence_no <= 1.0, f"confidence_no out of range: {v}"
            assert v.agent_name, "agent_name is empty"
            assert v.rationale, "rationale is empty"
            assert v.key_factor, "key_factor is empty"

        print(f"\n✓ council.py smoke test complete — {len(verdicts)}/7 verdicts.")

    asyncio.run(smoke_test())
