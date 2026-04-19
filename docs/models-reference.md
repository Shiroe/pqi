# Models Reference

Full field-level signatures for every Pydantic model in `pqi/models.py` plus
the rolling-stats model in `pqi/stats.py`. These are the **contracts between
modules** — do not skip validation. `CLAUDE.md` keeps a short pointer to
this file; detail lives here so the root context stays lean.

> Source of truth: `pqi/models.py` (import and read directly when changing
> a contract). This doc is the human-readable companion.

---

## Market + briefing

```python
class MarketData(BaseModel):
    id: str
    question: str
    resolution_criteria: str
    end_date: datetime
    event_start_time: datetime | None  # when the real-world event starts
    yes_price: float          # 0.0–1.0 (current market implied prob)
    volume_usd: float
    category: str
    clob_token_ids: list[str] # CLOB token IDs for YES/NO (from Gamma clobTokenIds)

class SidePriceHistory(BaseModel):
    token_id: str             # CLOB token ID for this side
    data_points: int          # number of hourly observations
    weighted_avg: float       # EMA weighted average (half-life 2 days)
    change_1w_pp: float       # net pp change over retrieval window
    change_24h_pp: float      # net pp change last 24h
    volatility_24h: float     # stddev of hourly deltas (pp/hr)
    direction: str            # "RISING" | "FALLING" | "STABLE"
    sharp_moves: list[SharpMove]  # up to 3 recent single-hour moves > 5pp

class PriceHistory(BaseModel):
    yes: SidePriceHistory     # YES-token trend metrics
    no: SidePriceHistory      # NO-token trend metrics (independent — don't sum to 1.0)
    fetched_at: datetime

class BriefingDocument(BaseModel):
    market: MarketData
    key_facts: list[str]      # bullet facts from Tavily search
    news_summary: str         # synthesized narrative
    side_yes_case: list[str]  # evidence/arguments favoring YES outcome
    side_no_case: list[str]   # evidence/arguments favoring NO outcome
    key_context: list[str]    # format, stakes, head-to-head, venue, etc.
    data_freshness: datetime
    price_history: PriceHistory | None  # CLOB trend data (None if fetch failed)
    structured_match: StructuredMatchRecord | None  # PandaScore (esports only; None on fetch fail or non-esports)
```

## PandaScore structured data (esports only)

```python
class TeamForm(BaseModel):
    wins: int                 # last-N completed matches
    losses: int

class H2HMatch(BaseModel):
    match_id: str
    scheduled_at: datetime
    tournament_name: str | None
    final_score: str | None
    winner_name: str | None

class StructuredMatchRecord(BaseModel):
    match_id: str
    team_a_name: str          # canonical PandaScore name
    team_b_name: str
    status: str               # "scheduled" | "running" | "finished"
    scheduled_at: datetime    # authoritative kickoff
    tournament_name: str | None
    match_format: str | None  # e.g. "BO3"
    final_score: str | None   # only when finished
    winner_name: str | None   # only when finished
    team_a_recent_form: TeamForm | None
    team_b_recent_form: TeamForm | None
    h2h: list[H2HMatch]       # prior encounters with stable IDs
    fuzzy_score: int          # 0–100 rapidfuzz min(token_set_ratio_a, token_set_ratio_b)
```

## Council output

```python
class AgentVerdict(BaseModel):
    agent_name: str           # e.g. "Rules Lawyer"
    probability: float        # 0.0–1.0 (P(YES))
    confidence_yes: float     # 0.0–1.0 (confidence in YES estimate)
    confidence_no: float      # 0.0–1.0 (confidence in NO estimate)
    rationale: str            # 2–4 sentence explanation
    key_factor: str           # single most important factor

class CouncilResult(BaseModel):
    market: MarketData
    verdicts: list[AgentVerdict]
    final_score: float        # YES-side confidence-weighted score
    no_score: float           # NO-side confidence-weighted score
    edge: float               # perceived_win_rate - yes_price (WR-based; 0.0 if no WR snapshot)
    no_edge: float            # perceived_win_rate - no_price  (WR-based; 0.0 if no WR snapshot)
    recommendation: str       # "BUY YES" | "BUY NO" | "PASS"
    confidence_level: str     # "HIGH" | "MEDIUM" | "LOW"
    signal_type: str | None   # "HIGH VALUE" | "MODERATE VALUE" | "LOW MARGIN" | "CONTRARIAN" | None
    pass_reason: str          # why PASS (empty for BUY)
    expected_roi: float       # max ROI (1-price)/price of recommended side; 0.0 on PASS

    # Run-time snapshots (session 23) — captured on every result so
    # `history show` can render the WR/Kelly that applied to that run.
    perceived_win_rate_at_run: float | None  # PerceivedTrueWinRate at scoring time; None if EV gate dormant
    kelly_stake_full: float                  # full-Kelly fraction at side_price; 0.0 on PASS / no WR
    kelly_stake_scaled: float                # kelly_stake_full * kelly_multiplier (advisory bankroll fraction)
    kelly_multiplier: float                  # safety scaler used (e.g. 0.5 for half-Kelly); 0.0 if no Kelly
```

## Rolling stats (`pqi/stats.py`)

```python
class PerceivedStats(BaseModel):
    # Rolling stats derived from resolved history. Persisted to ~/.pqi/stats.json.
    # Feeds the EV gate (true_win_rate) and the advisory Kelly stake.
    computed_at: datetime
    true_win_rate: float          # correct_buys / (correct_buys + wrong_buys)
    sample_size: int              # denominator (resolved BUYs, non-legacy)
    correct_buys: int
    wrong_buys: int
    kelly_fraction_at_p75: float  # reference Kelly stake at 75¢
    below_min_samples: bool       # True → EV gate stays dormant
    # band_stats: per-band Beta-binomial smoothed win rates (see docs/scoring-reference.md)
```

---

## Option B shadow fields (session 26; descriptive only, not in scoring)

`AgentVerdict` additionally carries:

- `probability_yes: float | None` — per-side YES probability from the persona
- `probability_no: float | None` — per-side NO probability from the persona
- `probability_source: str` — `"legacy"` (pre-Option-B) | `"persona"` (native dual-emit)

See `docs/option_b_migration_plan.md` for the rollout; do not wire these
into `CouncilResult.from_council_verdicts()` until step 5 passes its
acceptance criteria on held-out data.
