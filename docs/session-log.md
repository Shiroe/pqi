# Session Log — Changelog & Implementation Details

Per-session change log with detailed diffs. Referenced from CLAUDE.md.

---

## Session 26 — Option B steps 1–4 shipped (schema, parser, personas, shadow analytics)

Executed steps 1–4 of `docs/option_b_migration_plan.md` in strict
rollout order. Steps 5 (shadow backtest) and 6 (scoring integration)
deferred — shadow data has not accumulated. Per-step local commits
(no push).

Also codified the **Attribution rule (STRICT)** in CLAUDE.md
Coding Conventions: no AI / Claude / "assisted" mentions in source,
comments, commits, PRs, or docs. Durable memory pointer lives at
`feedback_attribution.md`. All session-26 commits honor the rule.

**Step 1 — additive schema + synthetic-mirror backfill** (`05bc2c9`).
- `models.AgentVerdict`: new fields
  `probability_yes: float | None`, `probability_no: float | None`,
  `probability_source: Literal["independent","synthetic_mirror"] = "synthetic_mirror"`.
  `@model_validator(mode="after")` auto-fills from canonical
  `probability` when either per-side field is unset (uses
  `object.__setattr__` for frozen-safety).
- `history.py`: `_backfill_per_side_probability(entry)` helper
  runs on every load after legacy migration — tolerant-read of
  pre-B history without rewriting the file.
- `scripts/b_backfill.py` (NEW): idempotent one-shot rewrite of
  `~/.pqi/history.jsonl`. Atomic tmp+rename, auto-creates
  `~/.pqi/history.jsonl.preB.bak` sibling on first modification,
  counts per action (`already_migrated`, `migrated`,
  `skipped_not_dict`, `skipped_no_probability`). Run output:
  292 rows scanned, 1860 verdicts migrated, all tagged
  `synthetic_mirror`. Zero API calls.
- `scoring.py` / `CouncilResult.from_council_verdicts`: **untouched**.
  Aggregator still consumes `probability` (canonical) so every
  existing metric (lean rate, correct/wrong buy, EV gate, Kelly,
  A.9 weights) is preserved bit-for-bit.

**Step 2 — tolerant parser** (`c9a2bb8`).
- `agents/council._parse_verdict`: reads optional
  `probability_no_score` int (0–100). Logic:
  - Field present and valid → tag `independent`, warn at
    `|p_yes + p_no − 1| > 0.3` (never reject; lenient-by-design
    per plan §5).
  - Field missing/malformed → synthetic_mirror fallback, retain
    the existing retry+backoff path.
- Constructor only passes `probability_yes/no/source` when the
  verdict is genuinely independent — `model_validator` handles
  synthetic rows.

**Step 3 — persona rollout (one-per-commit)** (`e011ec2`,
`949c871`, `39173c5`, `013dd1d`, `af7d924`, `0eb3610`, `e3b93a8`).
- `SHARED_PREAMBLE`: new "TWO-SIDED PROBABILITY (autonomy)" block,
  conditional on the persona's Output Format containing both
  `probability_score` and `probability_no_score`. Other personas
  skip the block.
- Promotion order (lowest structural-bias risk first, then
  Bull/Bear last per plan §6):
  `rules_lawyer → statistician → sentiment → specialist →
   contrarian → bull → bear`.
- Bull and Bear carry explicit anti-mirror framing:
  *"probability_no_score is NOT `100 - probability_score`; the
  evidence base for the opposing side is asymmetric."* This is
  the highest-risk promotion — session-20 Bear rewrite is still
  inside its A.4 monitoring window.
- Each persona's output field docstring is tailored (Rules
  Lawyer: "P(NO outcome condition is met)"; Statistician:
  "anchored on historical base rate for OPPOSITE outcome"; etc.).

**Step 4 — shadow analytics** (`9b3d7f9`).
- `analytics.py`:
  - `OPTION_B_PHASE1_MIN_SAMPLES = 10` (sanity floor).
  - `OPTION_B_PHASE2_MIN_SAMPLES = 30` (pre-registered backtest bar).
  - `_is_independent(v)` — filter predicate on
    `probability_source == "independent"`.
  - `option_b_overview(entries)` — totals: independent count,
    resolved count, fraction, agents-with-any-independent.
  - `option_b_per_agent(entries)` — per-agent `n_ind`, `n_res`,
    `mean_abs_drift`, `mean_signed_drift` (= mean of
    `p_yes + p_no − 1`), `yes_brier`, `no_brier` (per-side Brier
    against `final_yes_price` on resolved markets), `phase`
    classification.
- `main.py history stats`: conditional Option B block —
  panel (totals + agent coverage) + per-agent table with columns
  `Agent | n_ind | n_res | |drift| | signed | YES Brier | NO Brier | Phase`.
  Color-coded phase (green/yellow/dim). Silent when zero
  independent verdicts. Footer explains signed-drift
  interpretation (positive = double-count, negative = ignorance
  mass, near-zero = mirror collapse).
- Descriptive only. Zero gate impact. Scoring unchanged.

**API cost impact.** Per-agent output adds ~1 JSON field
(~5–10 tokens). 7 agents × ~$5/M output (claude-haiku-4-5-20251001)
≈ **$0.00025 per analyze run** — below rounding noise of the
existing ~$0.015 council cost. Anthropic prompt cache further
dilutes the impact on repeated preambles.

**Shadow-data status at session close.** 9 independent verdicts
observed in history (all 7 agents represented, ≤ 0.5% of 1888
total verdicts). Max `|drift|` 0.12, signed drift within
`[-0.03, +0.12]`. No `(0.5, 0.5)` degeneracy, no strict mirrors.
0 resolved → per-side Brier not yet computable. Phase 1
(`n ≥ 10` per agent) and the backtest bar (100–150 independent
rows total) remain ahead; no step 5 action possible yet.

**What stayed off-limits.** `CouncilResult.from_council_verdicts`
aggregation. The session-23 recommendation contract is preserved:
probability → edges → price gates → contrarian/aligned → EV gate.
Option B is upstream metadata; the aggregator sees an unchanged
`probability` field until step 6 explicitly changes it.

---

## Session 25 — A.1 Option A closed, A.9 dynamic per-agent weighting, Option B plan drafted

Three deliverables this session:

**1. A.1 β-phase backtest (Option A: probability-level transforms).**
Extended `scripts/a1_backtest.py` from α (3 candidates) to β (8): added
`SH2_selective` (shrinkage with gap floor), `LO2_mean` (log-odds mean),
`D1_ign` / `D2_gap` / `D3_both` (Dempster with ignorance-ceiling /
mass-gap overrides). Added chronological 70/30 train/holdout split,
bootstrap ΔBrier CI (1000 resamples, seed 42), per-row `correct_buy`
outcome mapping on 228 resolved BUYs. Pre-registered criteria:
`ΔBrier ≤ −0.005`, `ΔNet ≥ −1`, CI95<0 on **both** train and holdout.
**All 8 candidates REJECTED.** Dempster overrides produced identical
numbers to plain Dempster (thresholds never fired on rows that would
BUY). Decision: Option A closed — probability-level transforms
insufficient at this sample size. Option B (per-side probability
autonomy) is the remaining path.

**2. A.9 shipped — dynamic per-agent reliability weights.**
- `pqi/agent_weights.py` (NEW) — mirrors `pqi/stats.py`. Pure
  `compute_weights(lean_rates, min_samples, shrink)` function:
  `weight = max(0.0, 1.0 + shrink * (lean_rate − council_avg))`.
  Agents below `min_samples` clamp to 1.0. `recompute_from_history`
  walks `history.jsonl`, persists `~/.pqi/agent_weights.json`
  atomically. `load_agent_weights` tolerates missing/malformed.
- `pqi/config.py` / `.env.example` — three knobs:
  `AGENT_WEIGHTS_ENABLED=true`, `MIN_AGENT_WEIGHT_SAMPLES=30`,
  `AGENT_WEIGHT_SHRINK=2.0`.
- `pqi/scoring.py` — dynamic weights fully override static
  `RULES_LAWYER_WEIGHT`/`BEAR_WEIGHT` when enabled and a snapshot
  exists (precedence mode α — user-selected over multiply/per-agent).
- `pqi/models.py` — additive only: `CouncilResult.agent_weights_snapshot:
  dict[str, float]` captured on every run. No verdict-logic change.
- `pqi/main.py` — `history sync` and `history stats` trigger a
  recompute alongside `PerceivedStats`. `history show <#>` renders
  a `Wt` column when the snapshot is non-empty.
- Live weights from current history: Sentiment/Contrarian 1.07,
  Rules Lawyer/Specialist 1.06, Statistician 1.05, Bull 0.94,
  Bear 0.74 — matches Tier-1 retrospective lean-rate ranking.
- **Deferred (documented in roadmap A.9):** Bayesian posterior
  weighting `w = (hits + α) / (n + α + β)` — preferred formulation
  once sample size grows beyond ~50/agent. Current shrinkage
  formula is the intentional "for-now" choice.

**3. Option B migration plan drafted** at
`docs/option_b_migration_plan.md`. 10 sections: Goal, new math
enabled (coherence metric, per-side calibration), zero-API-cost
synthetic backfill (`p_no = 1 − p_yes`, preserves lean-outcome /
correct-buy metrics at no cost), files touched (risk-labeled),
open design questions (@computed_field vs rename-sweep, schema
version location, persona framing, parser strictness), 6-step
non-breaking rollout (schema+backfill → tolerant parser →
personas one-at-a-time → shadow analytics → shadow backtest →
scoring integration only after passing), same pre-registered
acceptance criteria as A.1 β, dependencies/risks, go/no-go
decision points. **Plan-only this session — no code changes.**

Also added **A.10** (dynamic-weight feedback-loop monitoring) to
`docs/roadmap.md` to protect A.9 from self-reinforcement: if
agent A gets down-weighted, its signal loses influence on future
BUYs, which can lock in the down-weight. A.10 defines the stats
snapshot + alarm thresholds to detect this before it matters.

---

## Session 24 — Guardrails audit + doc reconciliation

Audit sweep before resuming roadmap work, driven by a perceived win-rate
deterioration over the most recent ~20 resolved entries. Verified the
EV gate / Kelly addition (sessions 22–23) is **purely additive**: the
pre-EV recommendation contract in `CouncilResult.from_council_verdicts`
is preserved line-for-line. EV gate only narrows BUY→PASS, never the
reverse; Kelly fields are metadata-only; both are dormant until
`MIN_WIN_RATE_SAMPLES` is reached.

- `scoring.py` — Deleted dead top-level helpers
  (`confidence_weighted_score_yes/no`, `compute_edge`,
  `determine_confidence_level`, `score_council` alias). All logic has
  lived in `models.py:from_council_verdicts` since session 17; the
  helpers were stale (and `compute_edge` still returned the legacy
  `score − price` semantic, contradicting session 23's edge repurpose).
  Stopped passing the deprecated `edge_threshold` kwarg — the kwarg
  remains on `from_council_verdicts` with a default for back-compat,
  untouched. No verdict-path change.
- `CLAUDE.md` — Reconciled gate defaults with `config.py`:
  `MIN_TRADEABLE_PRICE` 0.05 → 0.09, `MIN_COUNCIL_PROBABILITY` 0.57 →
  0.60. Doc had drifted behind config changes across sessions.
- `README.md` — Same reconciliation: `MIN_TRADEABLE_PRICE` 0.05 →
  0.09, `MIN_EXPECTED_ROI` 0.15 → 0.10, `RULES_LAWYER_WEIGHT` 2 → 1.
- `.env.example` — Fixed stale `# Default: X` comments and a few
  values that diverged from `config.py` defaults
  (`MAX_TRADEABLE_PRICE` 0.91 → 0.90, `MIN_COUNCIL_PROBABILITY` 0.57 →
  0.60). Rule reaffirmed: the KEY=VALUE line and the `# Default:`
  comment in `.env.example` must both equal `config.py`'s default;
  user customisations belong in `.env`.
- `docs/api-reference.md` — Same `MIN_TRADEABLE_PRICE` / `EDGE_THRESHOLD`
  fixes; deprecated-status note added.
- `docs/known-issues.md` — Added stats.json note: deleting
  `~/.pqi/stats.json` silently disables the EV gate until the next
  `history sync` / `history stats` recompute.

## Session 23 — WR-based edges + run-time snapshots

Repurpose the informational `edge` fields to reflect the EV gate's
view of the world, and snapshot the inputs that produced each
recommendation so `history show` can render the exact WR / Kelly that
applied at run time (not the drifting rolling values).

- `models.py` — `CouncilResult.edge` / `no_edge` repurposed from
  `(score − price)` to `(PerceivedTrueWinRate − side_price)`. Both 0.0
  when no WR snapshot exists (EV gate dormant). Added run-time
  snapshot fields: `perceived_win_rate_at_run` (nullable),
  `kelly_stake_full`, `kelly_stake_scaled`, `kelly_multiplier`. Legacy
  pre-session-23 history entries retain the old edge semantic —
  labeled as such in `history show`.
- `scoring.py` — Threads `perceived_win_rate` + `KELLY_MULTIPLIER`
  into `from_council_verdicts` so the classmethod can compute edges
  and Kelly internally. Model stays pure (no I/O).
- `main.py` `display_council_result` — Renders WR ceiling +
  half-Kelly stake on every BUY panel when a snapshot exists.
- `main.py` `history show <#>` — Same block, reading from the
  snapshot fields rather than recomputing against current stats.
- `docs/roadmap.md` — Reorganised into Themes A / B / C (modelling,
  TUI+trading, ops tooling). Existing flat items renumbered with
  "was item #N" breadcrumbs so old notes cross-reference.

## Session 22 — Dynamic EV gate + Kelly Criterion (advisory)

First use of resolved history as a live input to scoring. Rejects
mathematically -EV BUYs by comparing side price to a rolling win-rate
computed from our own correct/wrong BUYs. Kelly fraction is surfaced
as advisory sizing; **no trading code was added** (still advisor-only).

- `models.py` — New `PerceivedStats` model persisted to
  `~/.pqi/stats.json` (computed_at, true_win_rate, sample_size,
  correct_buys, wrong_buys, kelly_fraction_at_p75, below_min_samples).
  `from_council_verdicts` gains two kwargs: `perceived_win_rate:
  float | None` and `kelly_multiplier: float`. **Final** gate added
  after the existing stack (price floor → ceiling → contrarian →
  market-aligned): if `recommendation != "PASS"` and `side_price ≥
  perceived_win_rate`, flip to PASS with reason "Big Drawdown Risk
  (-EV)". Pre-EV gate order is otherwise untouched.
- `stats.py` — NEW. Three pure functions:
  `recompute_from_history(min_samples)` walks `history.jsonl`, counts
  non-legacy `correct_buy` vs `wrong_buy` buckets, writes stats.json
  atomically. `load_perceived_stats()` tolerates missing/malformed
  file (cold-start safety → returns None). `kelly_fraction(win_rate,
  price, multiplier=1.0)` implements `f* = (bp − q) / b`, returning
  0.0 on -EV / degenerate inputs.
- `scoring.py` — Lazy-loads stats at build time. Only feeds
  `perceived_win_rate` into the model when `EV_GATE_ENABLED=true`
  AND `not stats.below_min_samples`. Otherwise gate stays dormant
  and verdicts match the pre-EV system exactly.
- `config.py` / `.env.example` — New knobs: `EV_GATE_ENABLED`
  (default true), `MIN_WIN_RATE_SAMPLES` (default 20 — gate stays
  off until that many resolved BUYs), `KELLY_MULTIPLIER` (default
  0.5 = half-Kelly).
- `main.py` — `history sync` (including `--auto`) and `history stats`
  trigger `recompute_from_history` so stats.json stays fresh.
  `history stats` overview gains a PerceivedStats block (WR, sample,
  Kelly @ 75¢). Recommendation panel on BUY results shows the
  advisory Kelly stake alongside price + expected ROI.
- Cold-start safety: every I/O path (load + scoring) tolerates a
  missing stats.json, so first-ever runs behave identically to
  sessions ≤21.

---

## Session 21 — CLOB price history enrichment (two-sided)

- `models.py` — **New models:** `PricePoint` (timestamp + price), `SharpMove`
  (timestamp + delta_pp), `SidePriceHistory` (per-side computed trend metrics:
  token_id, data_points, weighted_avg, change_1w_pp, change_24h_pp,
  volatility_24h, direction, sharp_moves), `PriceHistory` (wrapper:
  `yes: SidePriceHistory`, `no: SidePriceHistory`, `fetched_at: datetime`).
  YES + NO CLOB prices are independent (don't sum to 1.0 due to spread/vig).
  `MarketData` gains `clob_token_ids: list[str]` (default empty).
  `BriefingDocument` gains `price_history: PriceHistory | None` (default None).
- `market/clob.py` — **NEW.** CLOB API client for two-sided price history.
  `fetch_price_history(market)` fetches YES and NO token prices in parallel via
  `asyncio.gather()`. Requires `len(clob_token_ids) >= 2`. Per-side fetch via
  `_fetch_side(market_id, token_id, label)`. `_compute_side_metrics()` pure
  function: EMA weighted average (half-life 2 days), direction via linear
  regression slope (+-2pp threshold), volatility, sharp moves (>5pp, last 3).
  Non-fatal: returns None if either side fails.
- `market/polymarket.py` — `_parse_market()` now extracts `clobTokenIds` JSON
  string field from Gamma API response into `MarketData.clob_token_ids`.
- `config.py` — New section: Polymarket CLOB API. `CLOB_BASE_URL` code constant.
  Tunables: `PRICE_HISTORY_INTERVAL` (default `1w`), `PRICE_HISTORY_FIDELITY`
  (default `60` minutes).
- `.env.example` — added CLOB price history section.
- `agents/council.py` — `_format_briefing()` renders conditional two-sided
  `MARKET PRICE HISTORY` block between EVENT TIMING and VOLUME.
- `main.py` — `_run_analysis()` fetches CLOB price history between briefing
  build and council summon. `display_council_result()` shows `7d Trend YES`
  and `7d Trend NO` lines.
- `requirements.txt` — added `py-clob-client>=0.1`.

## Session 20 — Bear prompt rewrite + retry logic

- `agents/personas.py` — Bear prompt rewrite. Removed structural-bias phrases.
  Added evidence-binding guards and hard confidence floor (<=30 with <2
  underdog data points). Root cause: audit showed 38% hit rate, 81% NO-lean.
- `config.py` — Dead code removed (`CONSENSUS_YES_FLOOR`, `CONSENSUS_NO_CEILING`).
  New vars: `MIN_COUNCIL_VERDICTS`, `AGENT_MAX_RETRIES`, `BEAR_WEIGHT`.
- `agents/council.py` — Council quorum gate + agent retry with exponential backoff.
- `scoring.py` — Bear weight wired into `agent_weights`.
- `models.py` + `main.py` — Signal type rename: STRONG->HIGH VALUE, CONFIRMED->
  MODERATE VALUE, CAUTIOUS->LOW MARGIN.
- `main.py` — UX color overhaul (all BUY green, signal label colors).

## Session 19 — ROI threshold + retrospective

- `config.py` — `MIN_EXPECTED_ROI` default 0.15 -> 0.10. Widens LOW MARGIN band.
- `docs/retrospectives/eval_tier1_roi_2026_04.md` — Full retrospective report.
- Bear audit findings documented (not acted on until session 20).

## Session 18 — History filtering + signal glyphs

- `main.py` — `history list` filter flags: `--outcome`, `--rec`, `--conf`.
  `_signal_glyph()` + `Sig` column. `_analyzed_market_ids()` + `Seen` column.
  `history stats` council-level lean accuracy.

## Session 17 — History validation system

- `models.py` — `CouncilResult.briefing` field.
- `history.py` — Full rewrite: full-fidelity persistence, legacy migration.
- `resolution.py` — NEW. Fetch resolution from `/markets/{id}`.
- `analytics.py` — NEW. `classify_outcome()`, `agent_accuracy()`,
  `signal_postmortem()`.
- `main.py` — `pqi history` sub-app: list/show/sync/stats commands.
- `_run_analysis()` — shared briefing support for sibling markets.

---

## Implemented & tested (cumulative)

- `agents/personas.py` — all 7 system prompts, AGENT_PERSONAS dict, build_prompt().
  SHARED_PREAMBLE rules: TWO-SIDED MARKET, TWO-SIDED CONFIDENCE, DUAL-PERSPECTIVE
  ANALYSIS, TIME-TO-REVERSAL. Bull/Bear are dynamic (price-aware, favored/underdog).
  Contrarian prioritizes competitively plausible disruptions.
  All 7 output formats use `confidence_yes` + `confidence_no`.
- `config.py` — reads ALL tunables via `os.getenv(KEY, default)`. True code constants
  only: `GAMMA_BASE_URL`, `CLOB_BASE_URL`, `VALID_BACKENDS`.
- `models.py` — all Pydantic v2 models. Two-sided confidence, probability-first
  recommendation logic, ROI-banded signal types.
- `llm/client.py` — Ollama + Anthropic backends, `async def chat()`.
- `ingestion/synthesizer.py` — LLM Search Architect + parallel Tavily execution.
- `agents/council.py` — 7 agents via `asyncio.gather()`. Quorum gate. Retry with
  exponential backoff. Rules Lawyer injection. Side context for Bull/Bear.
- `scoring.py` — Two-sided scoring. Agent weights (Rules Lawyer, Bear).
- `history.py` — Full-fidelity persistence, legacy migration, upsert by market_id.
- `cache.py` — SQLite cache, price-delta invalidation.
- `resolution.py` — Fetch resolution status from Gamma API.
- `analytics.py` — Outcome classification, per-agent accuracy, postmortem.
- `market/polymarket.py` — Unified fetch path, server-side volume filtering,
  category resolution (three-pass), event enrichment.
- `market/clob.py` — Two-sided CLOB price history client.
- `main.py` — Typer CLI: analyze + categories + history (list/show/sync/stats).
  Sibling market discovery, shared briefings, signal glyphs, history filtering.
