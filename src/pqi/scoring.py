# pqi/scoring.py
"""
Thin orchestration layer: wires config + live PerceivedStats into
CouncilResult.from_council_verdicts(), which owns the actual scoring and
recommendation logic.

All math lives in models.py. Keep this file free of formulas — if a gate
or score needs a new input, thread it through here, not reimplement.
"""

import logging

from pqi import config
from pqi.models import AgentVerdict, CouncilResult, MarketData

logger = logging.getLogger(__name__)


def build_council_result(
    market: MarketData,
    verdicts: list[AgentVerdict],
) -> CouncilResult:
    """
    Orchestrate the full scoring pipeline and return a CouncilResult.
    Delegates to CouncilResult.from_council_verdicts() which owns the logic.
    """
    # Start with the static config weights (back-compat path).
    agent_weights: dict[str, float] = {
        "The Rules Lawyer": config.RULES_LAWYER_WEIGHT,
        "The Bear": config.BEAR_WEIGHT,
    }

    # Dynamic per-agent reliability weights fully override the static map
    # when enabled and a snapshot exists (precedence mode α). Agents without
    # enough samples were already clamped to 1.0 in agent_weights.py, so the
    # dynamic map is always complete.
    if config.AGENT_WEIGHTS_ENABLED:
        from pqi.agent_weights import load_agent_weights

        dynamic = load_agent_weights()
        if dynamic:
            agent_weights = dynamic

    # EV gate input — only fed in when enabled AND we have enough samples.
    # Otherwise the gate stays dormant and behavior matches pre-EV scoring.
    #
    # Two pieces are threaded through:
    #   perceived_win_rate — scalar global rate. Retained as the legacy
    #     fallback threshold when `perceived_stats` is absent (tests /
    #     cold-start callers). Also recorded on the result as the
    #     `perceived_win_rate_at_run` snapshot.
    #   perceived_stats    — full stats object. from_council_verdicts uses
    #     its band_stats to pick the band-specific threshold for the gate
    #     AND for Kelly sizing, so both decisions stay aligned.
    perceived_win_rate: float | None = None
    perceived_stats = None
    if config.EV_GATE_ENABLED:
        from pqi.stats import load_perceived_stats

        loaded = load_perceived_stats()
        if loaded is not None and not loaded.below_min_samples:
            perceived_win_rate = loaded.true_win_rate
            perceived_stats = loaded

    return CouncilResult.from_council_verdicts(
        market=market,
        verdicts=verdicts,
        confidence_high=config.CONFIDENCE_HIGH_THRESHOLD,
        confidence_medium=config.CONFIDENCE_MEDIUM_THRESHOLD,
        min_tradeable_price=config.MIN_TRADEABLE_PRICE,
        max_tradeable_price=config.MAX_TRADEABLE_PRICE,
        min_expected_roi=config.MIN_EXPECTED_ROI,
        min_contrarian_roi=config.MIN_CONTRARIAN_ROI,
        min_council_probability=config.MIN_COUNCIL_PROBABILITY,
        min_contrarian_probability=config.MIN_CONTRARIAN_PROBABILITY,
        min_contrarian_confidence=config.MIN_CONTRARIAN_CONFIDENCE,
        agent_weights=agent_weights,
        perceived_win_rate=perceived_win_rate,
        perceived_stats=perceived_stats,
        kelly_multiplier=config.KELLY_MULTIPLIER,
    )
