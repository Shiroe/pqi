# pqi/scoring.py
"""
Confidence-weighted scoring and recommendation logic.
Operates on 0.0–1.0 floats (agents report 0–100; council.py divides by 100).
"""

import logging
from pqi.models import AgentVerdict, CouncilResult, MarketData
from pqi import config

logger = logging.getLogger(__name__)


def confidence_weighted_score(verdicts: list[AgentVerdict]) -> float:
    """Σ(prob × conf) / Σ(conf). Returns 0.5 if total confidence is zero."""
    numerator = sum(v.probability * v.confidence for v in verdicts)
    denominator = sum(v.confidence for v in verdicts)
    return numerator / denominator if denominator > 0 else 0.5


def compute_edge(final_score: float, market_yes_price: float) -> float:
    """Positive = council thinks YES is underpriced; negative = overpriced."""
    return final_score - market_yes_price


def determine_confidence_level(verdicts: list[AgentVerdict]) -> str:
    """Map mean agent confidence to HIGH / MEDIUM / LOW label."""
    mean_conf = sum(v.confidence for v in verdicts) / len(verdicts)
    if mean_conf >= config.CONFIDENCE_HIGH_THRESHOLD:
        return "HIGH"
    if mean_conf >= config.CONFIDENCE_MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def make_recommendation(edge: float, confidence_level: str) -> str:
    """Apply edge + confidence threshold rules to produce a recommendation."""
    if edge > config.EDGE_THRESHOLD and confidence_level == "HIGH":
        return "BUY YES"
    if edge < -config.EDGE_THRESHOLD and confidence_level == "HIGH":
        return "BUY NO"
    return "PASS"


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
