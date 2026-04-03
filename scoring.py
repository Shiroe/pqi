# pqi/scoring.py
"""
Confidence-weighted scoring and recommendation logic.
Operates on 0.0–1.0 floats (agents report 0–100; council.py divides by 100).
"""

import logging
from pqi.models import AgentVerdict, CouncilResult, MarketData
from pqi import config

logger = logging.getLogger(__name__)


def confidence_weighted_score_yes(verdicts: list[AgentVerdict]) -> float:
    """YES-side: Σ(prob_yes × conf_yes) / Σ(conf_yes). Returns 0.5 if total is zero."""
    numerator = sum(v.probability * v.confidence_yes for v in verdicts)
    denominator = sum(v.confidence_yes for v in verdicts)
    return numerator / denominator if denominator > 0 else 0.5


def confidence_weighted_score_no(verdicts: list[AgentVerdict]) -> float:
    """NO-side: Σ(prob_no × conf_no) / Σ(conf_no). Returns 0.5 if total is zero."""
    numerator = sum((1.0 - v.probability) * v.confidence_no for v in verdicts)
    denominator = sum(v.confidence_no for v in verdicts)
    return numerator / denominator if denominator > 0 else 0.5


def compute_edge(final_score: float, market_price: float) -> float:
    """Positive = council thinks this side is underpriced."""
    return final_score - market_price


def determine_confidence_level(verdicts: list[AgentVerdict]) -> str:
    """Map best per-side mean confidence to HIGH / MEDIUM / LOW label."""
    mean_yes = sum(v.confidence_yes for v in verdicts) / len(verdicts)
    mean_no = sum(v.confidence_no for v in verdicts) / len(verdicts)
    best = max(mean_yes, mean_no)
    if best >= config.CONFIDENCE_HIGH_THRESHOLD:
        return "HIGH"
    if best >= config.CONFIDENCE_MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def build_council_result(
    market: MarketData,
    verdicts: list[AgentVerdict],
) -> CouncilResult:
    """
    Orchestrate the full scoring pipeline and return a CouncilResult.
    Delegates to CouncilResult.from_council_verdicts() which owns the logic.
    """
    return CouncilResult.from_council_verdicts(
        market=market,
        verdicts=verdicts,
        edge_threshold=config.EDGE_THRESHOLD,
        confidence_high=config.CONFIDENCE_HIGH_THRESHOLD,
        confidence_medium=config.CONFIDENCE_MEDIUM_THRESHOLD,
        consensus_yes_floor=config.CONSENSUS_YES_FLOOR,
        consensus_no_ceiling=config.CONSENSUS_NO_CEILING,
        min_tradeable_price=config.MIN_TRADEABLE_PRICE,
    )


# Public alias matching the Next Session Prompt spec
score_council = build_council_result
