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
    event_start_time: datetime | None = None
    """When the real-world event starts (match kickoff, hearing date, etc). None if not set."""
    yes_price: float = Field(..., ge=0.0, le=1.0)
    """Current market-implied probability of YES (0.0–1.0)."""
    volume_usd: float = Field(..., ge=0.0)
    category: str
    clob_token_ids: list[str] = Field(default_factory=list)
    """CLOB token IDs for YES and NO outcomes. Parsed from Gamma API clobTokenIds field."""
    sports_market_type: str | None = None
    """Gamma-API `sportsMarketType` field (e.g. "moneyline", "spread", "total").
    None on non-sports markets or older entries. Used to identify the overall-
    outcome market in a multi-market sports event without relying on title heuristics."""


class PricePoint(BaseModel):
    """A single price observation from the CLOB price history."""

    timestamp: datetime
    price: float = Field(..., ge=0.0, le=1.0)


class SharpMove(BaseModel):
    """A single-hour price move exceeding the sharp-move threshold."""

    timestamp: datetime
    delta_pp: float
    """Signed change in percentage points."""


class SidePriceHistory(BaseModel):
    """Computed trend metrics for one side (YES or NO) from CLOB hourly price data."""

    token_id: str
    """CLOB token ID used to fetch this side's history."""
    data_points: int
    """Number of hourly observations retrieved."""
    weighted_avg: float = Field(..., ge=0.0, le=1.0)
    """Exponential-decay weighted average price (half-life = 2 days)."""
    change_1w_pp: float
    """Net price change over the full retrieval window in percentage points."""
    change_24h_pp: float
    """Net price change over the last 24 hours in percentage points."""
    volatility_24h: float
    """Std dev of hourly price deltas over the last 24 hours (pp/hr)."""
    direction: Literal["RISING", "FALLING", "STABLE"]
    """Trend direction based on linear regression slope (±2pp threshold)."""
    sharp_moves: list[SharpMove] = Field(default_factory=list)
    """Up to 3 most recent single-hour moves exceeding 5pp."""


class PriceHistory(BaseModel):
    """Two-sided price history from CLOB — YES and NO tracked independently."""

    yes: SidePriceHistory
    """YES-side trend metrics."""
    no: SidePriceHistory
    """NO-side trend metrics."""
    fetched_at: datetime
    """UTC timestamp when the CLOB data was fetched."""


class EventGroup(BaseModel):
    """
    A group of sibling markets from the same Polymarket event.

    Example: a BO3 esports match has Moneyline, Map 1, Map 2 as sibling
    markets under one event.  The primary_market property returns the
    highest-volume market (markets are pre-sorted by volume desc).
    """

    event_id: str
    event_title: str
    markets: list[MarketData]
    """Sibling markets, sorted by volume descending."""
    event_start_time: datetime | None = None
    """Real-world event start (kickoff, hearing date, etc). Derived from child markets' eventStartTime."""
    live: bool | None = None
    """Whether the event is currently live. Reliable only after enrichment via /events/{id}."""
    ended: bool | None = None
    """Whether the event has ended. Reliable only after enrichment via /events/{id}."""
    category: str
    total_volume: float = Field(..., ge=0.0)
    """Sum of volume_usd across all child markets."""

    @property
    def primary_market(self) -> MarketData:
        """The highest-volume market in the group (used for headline display)."""
        return self.markets[0]

    @model_validator(mode="after")
    def _check_markets_nonempty(self) -> EventGroup:
        if not self.markets:
            raise ValueError("EventGroup must contain at least one market.")
        return self


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
    side_yes_case: list[str]
    """Evidence and arguments favoring the YES outcome."""
    side_no_case: list[str]
    """Evidence and arguments favoring the NO outcome."""
    key_context: list[str]
    """Contextual factors: format, stakes, head-to-head, venue, tournament stage."""
    prior_match_facts: list[str] = Field(default_factory=list)
    """Facts about prior encounters between the same parties (head-to-head
    history). Surfaced as historical context, NOT as evidence about the
    market's current resolution. Synthesizer routes any snippet describing
    a *completed* encounter that is not the market's specific event into
    this bucket so the council never reads "TeamA beat TeamB 2-0" as proof
    that the upcoming match has already resolved."""
    disambiguation_warnings: list[str] = Field(default_factory=list)
    """Mechanical alerts about temporal ambiguity in the source corpus,
    e.g. "Tavily returned past-tense match coverage for an event that has
    not started yet — verify dates before treating any score as final."
    Generated by post-LLM contamination detection in synthesizer.py."""
    data_freshness: datetime
    """UTC timestamp of when the briefing was assembled."""
    price_history: PriceHistory | None = None
    """CLOB price history with trend metrics. None if fetch failed or unavailable."""


class AgentVerdict(BaseModel):
    """
    One agent's assessment of a market.
    Probability and confidence are stored as 0.0–1.0 floats.
    The council.py parser divides the agent's 0–100 integer output by 100
    before constructing this model.

    Option B schema (session 26+, additive): `probability_yes` and
    `probability_no` are independent first-class fields. Today they
    are auto-derived from `probability` (P(YES)) when omitted, with
    `probability_source = "synthetic_mirror"`. Once personas emit
    `probability_no` independently, the parser will pass both fields
    explicitly and tag `probability_source = "independent"`.

    Aggregation in `CouncilResult.from_council_verdicts` still reads
    `probability` (== probability_yes). The aggregation swap to
    `(probability_yes, probability_no)` is a separate gated change.
    """

    agent_name: str
    """Human-readable display name, e.g. 'The Rules Lawyer'."""
    probability: float = Field(..., ge=0.0, le=1.0)
    """Agent's estimate of the YES probability. Equal to `probability_yes`
    by construction; kept as the canonical field during the Option B
    transition so existing call sites and aggregation logic do not change."""
    probability_yes: float = Field(default=None, ge=0.0, le=1.0)  # type: ignore[assignment]
    """Agent's autonomous estimate of P(YES). Auto-filled from `probability`
    when omitted (legacy / synthetic_mirror path)."""
    probability_no: float = Field(default=None, ge=0.0, le=1.0)  # type: ignore[assignment]
    """Agent's autonomous estimate of P(NO). Auto-filled as `1 - probability`
    when omitted (legacy / synthetic_mirror path). Under full Option B
    autonomy this can differ from `1 - probability_yes`."""
    probability_source: Literal["independent", "synthetic_mirror"] = "synthetic_mirror"
    """Where probability_yes/no came from. `synthetic_mirror` = derived as
    `(p, 1-p)` from a single-source probability (legacy data, or new code
    that hasn't yet emitted both fields). `independent` = the agent emitted
    both fields autonomously. Option B analytics (per-side Brier, coherence,
    side-bias) MUST filter on `probability_source == "independent"`."""
    confidence_yes: float = Field(..., ge=0.0, le=1.0)
    """Agent's confidence in its YES-side estimate."""
    confidence_no: float = Field(..., ge=0.0, le=1.0)
    """Agent's confidence in its NO-side estimate."""
    rationale: str
    """2–4 sentence explanation of the verdict."""
    key_factor: str
    """Single most important factor driving this verdict."""

    @model_validator(mode="after")
    def _backfill_per_side_probability(self) -> AgentVerdict:
        # Auto-fill the new fields from `probability` when callers omit them.
        # This keeps every existing AgentVerdict(...) call site working and
        # tags the result as a synthetic mirror so Option B analytics filter
        # it out. Independent emission must set both fields AND
        # probability_source="independent" explicitly.
        if self.probability_yes is None:
            object.__setattr__(self, "probability_yes", self.probability)
        if self.probability_no is None:
            object.__setattr__(self, "probability_no", 1.0 - self.probability)
        return self


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
    """perceived_win_rate − yes_price (WR-based edge for the YES side).
    Positive = YES price sits below the historical hit rate, so buying YES is
    +EV at this price. 0.0 when PerceivedStats is unavailable or below the
    sample-size threshold. On contrarian BUYs this is still a pure function
    of price vs. the WR ceiling, not of council score."""
    no_edge: float
    """perceived_win_rate − no_price (WR-based edge for the NO side).
    Same semantic as `edge` but for the NO price. 0.0 when PerceivedStats
    is unavailable."""
    recommendation: Literal["BUY YES", "BUY NO", "PASS"]
    confidence_level: Literal["HIGH", "MEDIUM", "LOW"]
    signal_type: (
        Literal["HIGH VALUE", "MODERATE VALUE", "LOW MARGIN", "CONTRARIAN"] | None
    ) = None
    """Conviction label for BUY recommendations. None when PASS."""
    pass_reason: str = ""
    """Why the recommendation is PASS (empty for BUY YES / BUY NO)."""
    expected_roi: float = 0.0
    """Max ROI of the recommended side at run time: (1 - price) / price.
    0.0 when PASS. Independent of council conviction — conviction is gated
    separately by the probability/confidence thresholds."""
    perceived_win_rate_at_run: float | None = None
    """Snapshot of PerceivedTrueWinRate at the moment this run was scored.
    Persisted so `history show` can render the WR ceiling and Kelly stake
    that applied to this specific run, not the current rolling values.
    None when the EV gate was disabled / below min samples at run time."""
    kelly_stake_full: float = 0.0
    """Full Kelly fraction for the recommended side, computed at run time
    using `perceived_win_rate_at_run` and the recommended side's price.
    0.0 on PASS, when no WR snapshot was captured, or when the bet is -EV."""
    kelly_stake_scaled: float = 0.0
    """Scaled Kelly fraction (`kelly_stake_full * kelly_multiplier`).
    This is the advisory bankroll fraction surfaced to the user."""
    kelly_multiplier: float = 0.0
    """The half-Kelly (or other) safety scaler used for `kelly_stake_scaled`
    at run time. 0.0 when no Kelly sizing was computed."""
    agent_weights_snapshot: dict[str, float] = Field(default_factory=dict)
    """Snapshot of the per-agent scoring weights in force at run time.
    Empty dict when no weights were supplied (legacy/static-default). Lets
    `history show <#>` render the weighting regime that produced each
    verdict, not the drifting current values. Does not enter the gate stack
    — metadata-only, same pattern as `kelly_stake_scaled`."""
    briefing: BriefingDocument | None = None
    """Full briefing document handed to the council. Optional for backward compat."""

    # ------------------------------------------------------------------
    # Classmethod factory — keeps scoring logic co-located with the model
    # so that scoring.py can stay thin and call this directly.
    # ------------------------------------------------------------------

    @classmethod
    def from_council_verdicts(
        cls,
        market: MarketData,
        verdicts: list[AgentVerdict],
        edge_threshold: float = 0.07,  # deprecated — informational only
        confidence_high: float = 0.60,
        confidence_medium: float = 0.40,
        min_tradeable_price: float = 0.05,
        max_tradeable_price: float = 0.90,
        min_expected_roi: float = 0.15,
        min_contrarian_roi: float = 0.20,
        min_council_probability: float = 0.60,
        min_contrarian_probability: float = 0.70,
        min_contrarian_confidence: float = 0.80,
        agent_weights: dict[str, float] | None = None,
        perceived_win_rate: float | None = None,
        kelly_multiplier: float = 0.5,
    ) -> CouncilResult:
        """
        Compute all derived fields from raw verdicts and assemble a CouncilResult.

        Probability-first recommendation logic:
          1. Compute two-sided confidence-weighted probabilities (unchanged)
          2. Identify council's favored side (>50% probability)
          3. Check if council agrees with market or goes contrarian
          4. Market-aligned: council prob >= min_council_probability + confidence HIGH
          5. Contrarian: council prob >= min_contrarian_probability + confidence >= min_contrarian_confidence
          6. Signal type: HIGH VALUE (ROI ≥50%) / MODERATE VALUE (25-50%) / LOW MARGIN (10-25%) / CONTRARIAN
        """
        if not verdicts:
            raise ValueError(
                "Cannot build CouncilResult: verdicts list is empty. "
                "At least one AgentVerdict is required."
            )

        # Step 1 — two-sided confidence-weighted probabilities
        weights = agent_weights or {}

        def _w(v: AgentVerdict) -> float:
            return weights.get(v.agent_name, 1.0)

        yes_num = sum(v.probability * v.confidence_yes * _w(v) for v in verdicts)
        yes_den = sum(v.confidence_yes * _w(v) for v in verdicts)
        final_score = yes_num / yes_den if yes_den > 0 else 0.5

        no_num = sum((1.0 - v.probability) * v.confidence_no * _w(v) for v in verdicts)
        no_den = sum(v.confidence_no * _w(v) for v in verdicts)
        no_score = no_num / no_den if no_den > 0 else 0.5

        # Step 2 — WR-based edges (informational, not gates).
        # Edge = PerceivedTrueWinRate − side_price: how much slack each side's
        # price has below the historical hit rate. Zero for both sides when
        # the EV gate is inactive (no samples yet, or gate disabled).
        no_price = 1.0 - market.yes_price
        if perceived_win_rate is not None:
            edge = perceived_win_rate - market.yes_price
            no_edge = perceived_win_rate - no_price
        else:
            edge = 0.0
            no_edge = 0.0

        # Step 3 — confidence levels
        mean_conf_yes = sum(v.confidence_yes for v in verdicts) / len(verdicts)
        mean_conf_no = sum(v.confidence_no for v in verdicts) / len(verdicts)

        best_mean_conf = max(mean_conf_yes, mean_conf_no)
        if best_mean_conf >= confidence_high:
            confidence_level: Literal["HIGH", "MEDIUM", "LOW"] = "HIGH"
        elif best_mean_conf >= confidence_medium:
            confidence_level = "MEDIUM"
        else:
            confidence_level = "LOW"

        # Step 4 — probability-first recommendation
        # Identify council's favored side
        council_favors_yes = final_score > 0.50
        council_favored_prob = final_score if council_favors_yes else no_score
        market_favors_yes = market.yes_price >= 0.50

        # Is this a contrarian call? (council disagrees with market on which side wins)
        is_contrarian = council_favors_yes != market_favors_yes

        # Determine which side to recommend and its confidence
        if council_favors_yes:
            rec_side = "YES"
            rec_edge = edge
            side_conf = mean_conf_yes
            side_price = market.yes_price
        else:
            rec_side = "NO"
            rec_edge = no_edge
            side_conf = mean_conf_no
            side_price = no_price

        # Max ROI of the recommended side: (1 - price) / price. Purely a
        # function of price — council conviction is gated separately.
        expected_roi = ((1.0 - side_price) / side_price) if side_price > 0 else 0.0

        # Apply gates
        recommendation: Literal["BUY YES", "BUY NO", "PASS"]
        signal_type: (
            Literal["HIGH VALUE", "MODERATE VALUE", "LOW MARGIN", "CONTRARIAN"] | None
        )
        pass_reason = ""

        if side_price < min_tradeable_price:
            # Price floor: can't recommend a side trading at near-zero
            recommendation = "PASS"
            signal_type = None
            pass_reason = (
                f"{rec_side} price too low: ${side_price:.2f} "
                f"(need ≥${min_tradeable_price:.2f})"
            )
        elif side_price > max_tradeable_price:
            # Price ceiling: near-resolved; ROI doesn't justify execution risk
            recommendation = "PASS"
            signal_type = None
            pass_reason = (
                f"{rec_side} price too high: ${side_price:.2f} "
                f"(≤${max_tradeable_price:.2f}); near-resolved, max ROI {expected_roi:.1%}"
            )
        elif is_contrarian:
            # Contrarian: stricter gates across the board
            if (
                council_favored_prob >= min_contrarian_probability
                and side_conf >= min_contrarian_confidence
                and expected_roi >= min_contrarian_roi
            ):
                recommendation = f"BUY {rec_side}"  # type: ignore[assignment]
                signal_type = "CONTRARIAN"
            else:
                recommendation = "PASS"
                signal_type = None
                reasons = []
                if council_favored_prob < min_contrarian_probability:
                    reasons.append(
                        f"Contrarian call: council prob {council_favored_prob:.0%} "
                        f"< {min_contrarian_probability:.0%} threshold"
                    )
                if side_conf < min_contrarian_confidence:
                    reasons.append(
                        f"Contrarian call: {rec_side}-side confidence {side_conf:.0%} "
                        f"< {min_contrarian_confidence:.0%} threshold"
                    )
                if expected_roi < min_contrarian_roi:
                    reasons.append(
                        f"Contrarian call: {rec_side} max ROI {expected_roi:.1%} "
                        f"< {min_contrarian_roi:.0%} threshold"
                    )
                pass_reason = "; ".join(reasons)
        elif (
            council_favored_prob >= min_council_probability
            and side_conf >= confidence_high
            and expected_roi >= min_expected_roi
        ):
            # Market-aligned: standard gates
            recommendation = f"BUY {rec_side}"  # type: ignore[assignment]
            # Signal type based on max ROI bands
            if expected_roi >= 0.50:
                signal_type = "HIGH VALUE"
            elif expected_roi >= 0.25:
                signal_type = "MODERATE VALUE"
            else:
                signal_type = "LOW MARGIN"
        else:
            recommendation = "PASS"
            signal_type = None
            reasons = []
            if council_favored_prob < min_council_probability:
                reasons.append(
                    f"Council prob too low: {council_favored_prob:.0%} "
                    f"for {rec_side} (need ≥{min_council_probability:.0%})"
                )
            if side_conf < confidence_high:
                reasons.append(
                    f"{rec_side}-side confidence too low: {side_conf:.0%} "
                    f"(need ≥{confidence_high:.0%})"
                )
            if expected_roi < min_expected_roi:
                reasons.append(
                    f"{rec_side} max ROI too low: {expected_roi:.1%} "
                    f"(need ≥{min_expected_roi:.0%})"
                )
            pass_reason = "; ".join(reasons) if reasons else "No side meets gates"

        # Final gate — EV ceiling from PerceivedTrueWinRate.
        # Applied only after all other gates have cleared a BUY. Rejects buys
        # where the side price is ≥ the historical hit rate: at that price
        # we're mathematically -EV even if every other signal lines up.
        if recommendation != "PASS" and perceived_win_rate is not None:
            if side_price >= perceived_win_rate:
                recommendation = "PASS"
                signal_type = None
                pass_reason = (
                    f"Big Drawdown Risk: {rec_side} price {side_price:.1%} "
                    f"≥ PerceivedTrueWinRate {perceived_win_rate:.1%} (-EV)"
                )

        # ROI is only meaningful for BUY signals — zero it on PASS.
        if recommendation == "PASS":
            expected_roi_final = 0.0
        else:
            expected_roi_final = expected_roi

        # Kelly sizing — advisory only, applied to the recommended side's price.
        # Zero on PASS (nothing to size) or when no WR snapshot is available.
        kelly_full = 0.0
        kelly_scaled = 0.0
        kelly_mult_used = 0.0
        if recommendation != "PASS" and perceived_win_rate is not None:
            if 0.0 < side_price < 1.0 and perceived_win_rate > side_price:
                b = (1.0 - side_price) / side_price
                p = perceived_win_rate
                q = 1.0 - p
                f = (b * p - q) / b
                if f > 0.0:
                    kelly_full = min(1.0, f)
                    kelly_scaled = min(1.0, kelly_full * kelly_multiplier)
                    kelly_mult_used = kelly_multiplier

        return cls(
            market=market,
            verdicts=verdicts,
            final_score=final_score,
            no_score=no_score,
            edge=edge,
            no_edge=no_edge,
            recommendation=recommendation,
            confidence_level=confidence_level,
            signal_type=signal_type,
            pass_reason=pass_reason,
            expected_roi=expected_roi_final,
            perceived_win_rate_at_run=perceived_win_rate,
            kelly_stake_full=kelly_full,
            kelly_stake_scaled=kelly_scaled,
            kelly_multiplier=kelly_mult_used,
            agent_weights_snapshot=dict(weights),
        )


class PerceivedStats(BaseModel):
    """Rolling stats derived from resolved history — source of truth for
    the EV gate (PerceivedTrueWinRate) and the advisory Kelly sizing.

    Persisted to ~/.pqi/stats.json and refreshed after every history sync
    or `history stats` invocation. `below_min_samples` is True until enough
    resolved BUY outcomes have accumulated for the gate to activate.
    """

    computed_at: datetime
    true_win_rate: float = Field(..., ge=0.0, le=1.0)
    """correct_buys / (correct_buys + wrong_buys) across resolved non-legacy
    BUY entries. 0.0 when no samples yet."""
    sample_size: int = Field(..., ge=0)
    """Total resolved BUYs (correct + wrong) — the denominator."""
    correct_buys: int = Field(..., ge=0)
    wrong_buys: int = Field(..., ge=0)
    kelly_fraction_at_p75: float = Field(..., ge=0.0, le=1.0)
    """Advisory Kelly stake at a reference price of $0.75 — lets the
    history overview show a representative sizing number without binding
    to any specific market. 0.0 when -EV at $0.75."""
    below_min_samples: bool = True
    """True when sample_size < MIN_WIN_RATE_SAMPLES — EV gate stays off."""


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
    print(
        f"  id={market.id}  yes_price={market.yes_price}  volume=${market.volume_usd:,.0f}"
    )

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
        key_facts=[
            "Starship IFT-4 completed successfully in June 2024.",
            "FAA license pending for next flight.",
        ],
        news_summary="SpaceX is on track for an orbital attempt in Q1 2025 pending FAA clearance.",
        side_yes_case=[
            "IFT-4 demonstrated full ascent profile.",
            "SpaceX has accelerating launch cadence.",
        ],
        side_no_case=[
            "FAA licensing delays common in 2024.",
            "Orbital attempt requires new trajectory approval.",
        ],
        key_context=[
            "SpaceX targeting Q1 2025 window.",
            "FAA regulatory review in progress.",
        ],
        data_freshness=datetime.now(timezone.utc),
    )
    print(
        f"  market.id={briefing.market.id}  facts={len(briefing.key_facts)}  freshness={briefing.data_freshness.isoformat()[:10]}"
    )

    # ---- AgentVerdict -------------------------------------------------------
    print("\n--- AgentVerdict ---")
    verdicts = [
        AgentVerdict(
            agent_name="The Bull",
            probability=0.80,
            confidence_yes=0.75,
            confidence_no=0.50,
            rationale="Strong momentum.",
            key_factor="IFT-4 success",
        ),
        AgentVerdict(
            agent_name="The Bear",
            probability=0.40,
            confidence_yes=0.60,
            confidence_no=0.70,
            rationale="FAA delays likely.",
            key_factor="Regulatory risk",
        ),
        AgentVerdict(
            agent_name="The Rules Lawyer",
            probability=0.65,
            confidence_yes=0.80,
            confidence_no=0.75,
            rationale="Criteria clear.",
            key_factor="Criteria specificity",
        ),
        AgentVerdict(
            agent_name="The Contrarian",
            probability=0.30,
            confidence_yes=0.40,
            confidence_no=0.50,
            rationale="Black swan possible.",
            key_factor="Unexpected failure",
        ),
        AgentVerdict(
            agent_name="The Statistician",
            probability=0.55,
            confidence_yes=0.65,
            confidence_no=0.55,
            rationale="Base rate ~50%.",
            key_factor="Historical orbital rates",
        ),
        AgentVerdict(
            agent_name="The Sentiment Analyst",
            probability=0.70,
            confidence_yes=0.60,
            confidence_no=0.45,
            rationale="Market under-pricing.",
            key_factor="Crowd apathy",
        ),
        AgentVerdict(
            agent_name="The Domain Specialist",
            probability=0.72,
            confidence_yes=0.70,
            confidence_no=0.60,
            rationale="Engineering on track.",
            key_factor="Raptor engine reliability",
        ),
    ]
    for v in verdicts:
        print(
            f"  {v.agent_name:<28}  p={v.probability:.2f}  cy={v.confidence_yes:.2f}  cn={v.confidence_no:.2f}"
        )

    # Boundary: probability outside [0,1]
    try:
        AgentVerdict(
            agent_name="X",
            probability=1.1,
            confidence_yes=0.5,
            confidence_no=0.5,
            rationale="...",
            key_factor="x",
        )
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
    print(f"  signal_type     = {result.signal_type}")
    if result.pass_reason:
        print(f"  pass_reason     = {result.pass_reason}")

    # Verify computed values manually
    num = sum(v.probability * v.confidence_yes for v in verdicts)
    den = sum(v.confidence_yes for v in verdicts)
    expected_score = num / den
    assert abs(result.final_score - expected_score) < 1e-9, "final_score mismatch"
    # edge is now WR-based: 0.0 when no perceived_win_rate is fed in
    assert result.edge == 0.0 and result.no_edge == 0.0, (
        "edge/no_edge should default to 0.0 when perceived_win_rate is None"
    )

    # Feed a WR to exercise the new edge + Kelly semantics
    result_wr = CouncilResult.from_council_verdicts(
        market=market, verdicts=verdicts, perceived_win_rate=0.70
    )
    assert abs(result_wr.edge - (0.70 - market.yes_price)) < 1e-9, (
        "WR-based YES edge mismatch"
    )
    assert abs(result_wr.no_edge - (0.70 - (1 - market.yes_price))) < 1e-9, (
        "WR-based NO edge mismatch"
    )
    print("  ✓ final_score + WR-based edges match manual calculation")

    # ---- Scenario tests for probability-first logic ----------------------
    print("\n--- Probability-first recommendation scenarios ---")

    def _make_verdicts(prob: float, cy: float, cn: float) -> list[AgentVerdict]:
        """Helper: create 7 uniform verdicts for scenario testing."""
        return [
            AgentVerdict(
                agent_name=f"Agent {i}",
                probability=prob,
                confidence_yes=cy,
                confidence_no=cn,
                rationale="Test.",
                key_factor="Test",
            )
            for i in range(7)
        ]

    # Scenario 1: Market-aligned STRONG — cheap side, high ROI band (≥50%)
    # Market YES=0.55, Council YES≈0.75 → side_price 0.55, max ROI = 81.8%
    m1 = MarketData(**{**market.model_dump(), "yes_price": 0.55})
    r1 = CouncilResult.from_council_verdicts(
        market=m1, verdicts=_make_verdicts(0.75, 0.80, 0.60)
    )
    print(
        f"  S1 (STRONG):      rec={r1.recommendation:<10} signal={r1.signal_type:<12} "
        f"council_yes={r1.final_score:.0%} price={m1.yes_price:.0%} roi={r1.expected_roi:.1%}"
    )
    assert r1.recommendation == "BUY YES" and r1.signal_type == "HIGH VALUE"

    # Scenario 2: Market-aligned CONFIRMED — mid-price, ROI 25%–50%
    # Market NO=0.72 (yes=0.28), Council NO≈0.72 → side_price 0.72, ROI = 38.9%
    m2 = MarketData(**{**market.model_dump(), "yes_price": 0.28})
    r2 = CouncilResult.from_council_verdicts(
        market=m2, verdicts=_make_verdicts(0.28, 0.50, 0.70)
    )
    print(
        f"  S2 (CONFIRMED):   rec={r2.recommendation:<10} signal={r2.signal_type:<12} "
        f"council_no={r2.no_score:.0%} price={1 - m2.yes_price:.0%} roi={r2.expected_roi:.1%}"
    )
    assert r2.recommendation == "BUY NO" and r2.signal_type == "MODERATE VALUE"

    # Scenario 3: Market-aligned CAUTIOUS — pricey side, ROI 15%–25%
    # Market NO=0.82 (yes=0.18), Council NO≈0.75 → side_price 0.82, ROI = 22.0%
    m3 = MarketData(**{**market.model_dump(), "yes_price": 0.18})
    r3 = CouncilResult.from_council_verdicts(
        market=m3, verdicts=_make_verdicts(0.25, 0.45, 0.70)
    )
    print(
        f"  S3 (CAUTIOUS):    rec={r3.recommendation:<10} signal={r3.signal_type:<12} "
        f"council_no={r3.no_score:.0%} price={1 - m3.yes_price:.0%} roi={r3.expected_roi:.1%}"
    )
    assert r3.recommendation == "BUY NO" and r3.signal_type == "LOW MARGIN"

    # Scenario 4: Contrarian — council goes against market, strict criteria met
    # Market: NO=80% (yes=20%), Council: YES≈72% with high confidence
    m4 = MarketData(**{**market.model_dump(), "yes_price": 0.20})
    r4 = CouncilResult.from_council_verdicts(
        market=m4, verdicts=_make_verdicts(0.72, 0.85, 0.50)
    )
    print(
        f"  S4 (CONTRARIAN):  rec={r4.recommendation:<10} signal={r4.signal_type:<12} "
        f"council_yes={r4.final_score:.0%} market_yes={m4.yes_price:.0%}"
    )
    assert r4.recommendation == "BUY YES" and r4.signal_type == "CONTRARIAN"

    # Scenario 5: PASS — council near 50/50
    m5 = MarketData(**{**market.model_dump(), "yes_price": 0.60})
    r5 = CouncilResult.from_council_verdicts(
        market=m5, verdicts=_make_verdicts(0.52, 0.70, 0.70)
    )
    print(
        f"  S5 (PASS 50/50):  rec={r5.recommendation:<10} signal={str(r5.signal_type):<12} "
        f"council_yes={r5.final_score:.0%} reason={r5.pass_reason[:50]}"
    )
    assert r5.recommendation == "PASS"

    # Scenario 6: PASS — contrarian but doesn't meet strict criteria
    m6 = MarketData(**{**market.model_dump(), "yes_price": 0.20})
    r6 = CouncilResult.from_council_verdicts(
        market=m6, verdicts=_make_verdicts(0.62, 0.65, 0.50)
    )
    print(
        f"  S6 (PASS contra): rec={r6.recommendation:<10} signal={str(r6.signal_type):<12} "
        f"council_yes={r6.final_score:.0%} reason={r6.pass_reason[:60]}"
    )
    assert r6.recommendation == "PASS"

    # Scenario 7: PASS — price ceiling. Side priced above MAX_TRADEABLE_PRICE.
    # Market YES=0.95, Council YES≈0.95 → side_price 0.95 > 0.90 → PASS
    m7 = MarketData(**{**market.model_dump(), "yes_price": 0.95})
    r7 = CouncilResult.from_council_verdicts(
        market=m7, verdicts=_make_verdicts(0.95, 0.90, 0.40)
    )
    print(
        f"  S7 (PASS ceil):   rec={r7.recommendation:<10} signal={str(r7.signal_type):<12} "
        f"price={m7.yes_price:.0%} reason={r7.pass_reason[:60]}"
    )
    assert r7.recommendation == "PASS" and "too high" in r7.pass_reason

    # Scenario 8: PASS — ROI gate. Market-aligned with conviction but side too expensive.
    # Market YES=0.88, Council YES≈0.75 → side_price 0.88, ROI = 13.6% < 15% → PASS
    m8 = MarketData(**{**market.model_dump(), "yes_price": 0.88})
    r8 = CouncilResult.from_council_verdicts(
        market=m8, verdicts=_make_verdicts(0.75, 0.80, 0.50)
    )
    print(
        f"  S8 (PASS ROI):    rec={r8.recommendation:<10} signal={str(r8.signal_type):<12} "
        f"council_yes={r8.final_score:.0%} price={m8.yes_price:.0%} reason={r8.pass_reason[:60]}"
    )
    assert r8.recommendation == "PASS" and "ROI too low" in r8.pass_reason

    print("  ✓ All 8 scenarios passed")

    # Empty verdicts must raise
    try:
        CouncilResult.from_council_verdicts(market=market, verdicts=[])
        raise AssertionError("Should have raised ValueError for empty verdicts")
    except ValueError as e:
        print(f"  ✓ Empty verdicts rejected: ValueError")

    # Invalid recommendation literal — Pydantic should reject
    try:
        CouncilResult(
            market=market,
            verdicts=verdicts,
            final_score=0.7,
            edge=0.08,
            recommendation="YOLO",  # type: ignore[arg-type]
            confidence_level="HIGH",
        )
        raise AssertionError(
            "Should have raised ValidationError for bad recommendation"
        )
    except Exception as e:
        print(f"  ✓ Invalid recommendation rejected: {type(e).__name__}")

    print("\n✓ models.py smoke test complete.")
