# pqi/models.py
"""
Pydantic v2 models — the contracts between every module in this codebase.
Never pass raw dicts between modules. Always use these models.

Import order matters: MarketData → BriefingDocument → AgentVerdict → CouncilResult.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class MarketData(BaseModel):
    """A single Polymarket prediction market fetched from the Gamma API."""

    id: str
    question: str
    resolution_criteria: str
    end_date: datetime
    yes_price: float = Field(..., ge=0.0, le=1.0)
    """Current market-implied probability of YES (0.0–1.0)."""
    volume_usd: float = Field(..., ge=0.0)
    category: str


class BriefingDocument(BaseModel):
    """
    Synthesized research package produced by synthesizer.py.
    Handed verbatim to all seven council agents.
    """

    market: MarketData
    key_facts: list[str]
    """Concise bullet facts extracted from Tavily search results."""
    news_summary: str
    """3–5 sentence narrative synthesised from recent coverage."""
    red_flags: list[str]
    """Signals that argue against a YES resolution."""
    green_flags: list[str]
    """Signals that argue for a YES resolution."""
    data_freshness: datetime
    """UTC timestamp of when the briefing was assembled."""


class AgentVerdict(BaseModel):
    """
    One agent's assessment of a market.
    Probability and confidence are stored as 0.0–1.0 floats.
    The council.py parser divides the agent's 0–100 integer output by 100
    before constructing this model.
    """

    agent_name: str
    """Human-readable display name, e.g. 'The Rules Lawyer'."""
    probability: float = Field(..., ge=0.0, le=1.0)
    """Agent's estimate of the YES probability."""
    confidence_yes: float = Field(..., ge=0.0, le=1.0)
    """Agent's confidence in its YES-side estimate."""
    confidence_no: float = Field(..., ge=0.0, le=1.0)
    """Agent's confidence in its NO-side estimate."""
    rationale: str
    """2–4 sentence explanation of the verdict."""
    key_factor: str
    """Single most important factor driving this verdict."""


class CouncilResult(BaseModel):
    """
    Final aggregated output from the Council of Seven for one market.
    Produced by scoring.py (or via the from_council_verdicts() classmethod).
    """

    market: MarketData
    verdicts: list[AgentVerdict]
    final_score: float = Field(..., ge=0.0, le=1.0)
    """Confidence-weighted probability of YES across all agents (YES-side)."""
    no_score: float = Field(..., ge=0.0, le=1.0)
    """Confidence-weighted probability of NO across all agents (NO-side)."""
    edge: float
    """final_score − yes_price.  Positive = underpriced YES."""
    no_edge: float
    """no_score − no_price.  Positive = underpriced NO."""
    recommendation: Literal["BUY YES", "BUY NO", "PASS"]
    confidence_level: Literal["HIGH", "MEDIUM", "LOW"]

    # ------------------------------------------------------------------
    # Classmethod factory — keeps scoring logic co-located with the model
    # so that scoring.py can stay thin and call this directly.
    # ------------------------------------------------------------------

    @classmethod
    def from_council_verdicts(
        cls,
        market: MarketData,
        verdicts: list[AgentVerdict],
        edge_threshold: float = 0.07,
        confidence_high: float = 0.60,
        confidence_medium: float = 0.40,
        consensus_yes_floor: float = 0.40,
        consensus_no_ceiling: float = 0.60,
        min_tradeable_price: float = 0.05,
    ) -> CouncilResult:
        """
        Compute all derived fields from raw verdicts and assemble a CouncilResult.

        Two-sided scoring: YES and NO sides are scored independently using
        their respective confidence weights. An agent can be highly confident
        about one side but uncertain about the other.

        Scoring pipeline:
          1. yes_score = Σ(prob_yes × conf_yes) / Σ(conf_yes)
             no_score  = Σ(prob_no  × conf_no)  / Σ(conf_no)
          2. yes_edge = yes_score − market.yes_price
             no_edge  = no_score  − (1 − market.yes_price)
          3. yes_confidence_level / no_confidence_level from mean per-side confidence
          4. median_prob = median of agent probabilities (consensus gate)
          5. BUY YES evaluated from yes_edge + yes_confidence
             BUY NO  evaluated from no_edge  + no_confidence (independent)
        """
        if not verdicts:
            raise ValueError(
                "Cannot build CouncilResult: verdicts list is empty. "
                "At least one AgentVerdict is required."
            )

        # Step 1 — two-sided confidence-weighted probabilities
        yes_num = sum(v.probability * v.confidence_yes for v in verdicts)
        yes_den = sum(v.confidence_yes for v in verdicts)
        final_score = yes_num / yes_den if yes_den > 0 else 0.5

        no_num = sum((1.0 - v.probability) * v.confidence_no for v in verdicts)
        no_den = sum(v.confidence_no for v in verdicts)
        no_score = no_num / no_den if no_den > 0 else 0.5

        # Step 2 — two-sided edges
        no_price = 1.0 - market.yes_price
        edge = final_score - market.yes_price
        no_edge = no_score - no_price

        # Step 3 — per-side confidence levels
        mean_conf_yes = sum(v.confidence_yes for v in verdicts) / len(verdicts)
        mean_conf_no = sum(v.confidence_no for v in verdicts) / len(verdicts)

        # Overall confidence label uses the higher of the two sides
        best_mean_conf = max(mean_conf_yes, mean_conf_no)
        if best_mean_conf >= confidence_high:
            confidence_level: Literal["HIGH", "MEDIUM", "LOW"] = "HIGH"
        elif best_mean_conf >= confidence_medium:
            confidence_level = "MEDIUM"
        else:
            confidence_level = "LOW"

        # Per-side HIGH checks for gate logic
        yes_conf_high = mean_conf_yes >= confidence_high
        no_conf_high = mean_conf_no >= confidence_high

        # Step 4 — median probability (consensus gate, outlier-resistant)
        sorted_probs = sorted(v.probability for v in verdicts)
        n = len(sorted_probs)
        if n % 2 == 1:
            median_prob = sorted_probs[n // 2]
        else:
            median_prob = (sorted_probs[n // 2 - 1] + sorted_probs[n // 2]) / 2

        # Step 5 — recommendation (each side evaluated independently)
        if (edge > edge_threshold
                and yes_conf_high
                and median_prob > consensus_yes_floor
                and market.yes_price >= min_tradeable_price):
            recommendation: Literal["BUY YES", "BUY NO", "PASS"] = "BUY YES"
        elif (no_edge > edge_threshold
                and no_conf_high
                and median_prob < consensus_no_ceiling
                and no_price >= min_tradeable_price):
            recommendation = "BUY NO"
        else:
            recommendation = "PASS"

        return cls(
            market=market,
            verdicts=verdicts,
            final_score=final_score,
            no_score=no_score,
            edge=edge,
            no_edge=no_edge,
            recommendation=recommendation,
            confidence_level=confidence_level,
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from datetime import timezone

    print("=== pqi/models.py smoke test ===\n")

    # ---- MarketData --------------------------------------------------------
    print("--- MarketData ---")
    market = MarketData(
        id="mkt-001",
        question="Will SpaceX Starship complete an orbital flight by June 2025?",
        resolution_criteria="Resolves YES if Starship reaches orbital altitude per SpaceX official announcement.",
        end_date=datetime(2025, 6, 30, tzinfo=timezone.utc),
        yes_price=0.62,
        volume_usd=450_000.0,
        category="technology",
    )
    print(f"  id={market.id}  yes_price={market.yes_price}  volume=${market.volume_usd:,.0f}")

    # Boundary: yes_price must be 0.0–1.0
    try:
        MarketData(**{**market.model_dump(), "yes_price": 1.5})
        raise AssertionError("Should have raised ValidationError for yes_price > 1.0")
    except Exception as e:
        print(f"  ✓ yes_price > 1.0 rejected: {type(e).__name__}")

    try:
        MarketData(**{**market.model_dump(), "yes_price": -0.1})
        raise AssertionError("Should have raised ValidationError for yes_price < 0.0")
    except Exception as e:
        print(f"  ✓ yes_price < 0.0 rejected: {type(e).__name__}")

    # ---- BriefingDocument --------------------------------------------------
    print("\n--- BriefingDocument ---")
    briefing = BriefingDocument(
        market=market,
        key_facts=["Starship IFT-4 completed successfully in June 2024.", "FAA license pending for next flight."],
        news_summary="SpaceX is on track for an orbital attempt in Q1 2025 pending FAA clearance.",
        red_flags=["FAA licensing delays common in 2024."],
        green_flags=["IFT-4 demonstrated full ascent profile."],
        data_freshness=datetime.now(timezone.utc),
    )
    print(f"  market.id={briefing.market.id}  facts={len(briefing.key_facts)}  freshness={briefing.data_freshness.isoformat()[:10]}")

    # ---- AgentVerdict -------------------------------------------------------
    print("\n--- AgentVerdict ---")
    verdicts = [
        AgentVerdict(agent_name="The Bull",        probability=0.80, confidence_yes=0.75, confidence_no=0.50, rationale="Strong momentum.", key_factor="IFT-4 success"),
        AgentVerdict(agent_name="The Bear",        probability=0.40, confidence_yes=0.60, confidence_no=0.70, rationale="FAA delays likely.", key_factor="Regulatory risk"),
        AgentVerdict(agent_name="The Rules Lawyer",probability=0.65, confidence_yes=0.80, confidence_no=0.75, rationale="Criteria clear.", key_factor="Criteria specificity"),
        AgentVerdict(agent_name="The Contrarian",  probability=0.30, confidence_yes=0.40, confidence_no=0.50, rationale="Black swan possible.", key_factor="Unexpected failure"),
        AgentVerdict(agent_name="The Statistician",probability=0.55, confidence_yes=0.65, confidence_no=0.55, rationale="Base rate ~50%.", key_factor="Historical orbital rates"),
        AgentVerdict(agent_name="The Sentiment Analyst", probability=0.70, confidence_yes=0.60, confidence_no=0.45, rationale="Market under-pricing.", key_factor="Crowd apathy"),
        AgentVerdict(agent_name="The Domain Specialist",  probability=0.72, confidence_yes=0.70, confidence_no=0.60, rationale="Engineering on track.", key_factor="Raptor engine reliability"),
    ]
    for v in verdicts:
        print(f"  {v.agent_name:<28}  p={v.probability:.2f}  cy={v.confidence_yes:.2f}  cn={v.confidence_no:.2f}")

    # Boundary: probability outside [0,1]
    try:
        AgentVerdict(agent_name="X", probability=1.1, confidence_yes=0.5, confidence_no=0.5, rationale="...", key_factor="x")
        raise AssertionError("Should have raised ValidationError for probability > 1.0")
    except Exception as e:
        print(f"  ✓ probability > 1.0 rejected: {type(e).__name__}")

    # ---- CouncilResult via from_council_verdicts ---------------------------
    print("\n--- CouncilResult.from_council_verdicts() ---")
    result = CouncilResult.from_council_verdicts(market=market, verdicts=verdicts)
    print(f"  final_score     = {result.final_score:.4f}")
    print(f"  edge            = {result.edge:+.4f}  (yes_price={market.yes_price})")
    print(f"  confidence_level= {result.confidence_level}")
    print(f"  recommendation  = {result.recommendation}")

    # Verify computed values manually
    num = sum(v.probability * v.confidence_yes for v in verdicts)
    den = sum(v.confidence_yes for v in verdicts)
    expected_score = num / den
    assert abs(result.final_score - expected_score) < 1e-9, "final_score mismatch"
    assert abs(result.edge - (expected_score - market.yes_price)) < 1e-9, "edge mismatch"
    print("  ✓ final_score and edge match manual calculation")

    # Empty verdicts must raise
    try:
        CouncilResult.from_council_verdicts(market=market, verdicts=[])
        raise AssertionError("Should have raised ValueError for empty verdicts")
    except ValueError as e:
        print(f"  ✓ Empty verdicts rejected: ValueError")

    # Invalid recommendation literal — Pydantic should reject
    try:
        CouncilResult(
            market=market, verdicts=verdicts,
            final_score=0.7, edge=0.08,
            recommendation="YOLO",   # type: ignore[arg-type]
            confidence_level="HIGH",
        )
        raise AssertionError("Should have raised ValidationError for bad recommendation")
    except Exception as e:
        print(f"  ✓ Invalid recommendation rejected: {type(e).__name__}")

    print("\n✓ models.py smoke test complete.")
