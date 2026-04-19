# Phase 2b: MFE Analysis

**Status:** MFE scoping unblocked (parallel with P1); listener work lives in Phase 2c
**Estimated effort:** 1–2 weeks
**Owner:** TBD
**Last updated:** 2026-04-19 (split from prior `phase_2_execution_edge.md`; Task 2.1 renamed to Phase 2b; train/test discipline promoted to task requirement per external review v2 §5.3 / §6.4)

---

## Why this phase exists

Polymarket has **no native stop-loss, take-profit, or bracket
orders.** Before any engineering on a continuous listener or
auto-sell path, measure whether MFE (max favorable excursion) on
historical BUYs is large enough — after Polymarket fees and
plausible slippage — to be worth capturing at all.

This phase is the third leg of the CTO-§5 edge triangulation:
**prediction edge** (Phase 0 council Brier), **entry edge**
(Phase 0 pre-event CLV), **exit edge** (Phase 2b MFE). A positive
MFE verdict unblocks Phase 2c (listener prototype); a negative
verdict parks Phase 2c and lets Tier A auto-BUY (Phase 2a) ship
without an exit discipline layer.

**Important framing:** execution edge is **orthogonal to prediction
edge**. Phase 2b can pay off even if Phase 0 prediction edge is
marginal, because MFE capture is about exiting overshoots regardless
of whether the final outcome resolves our way. See
`docs/scaling_assessment.md` §6 and `CTO_Agent.md` §5.

---

## Entry criteria

- [x] Phase 0 complete (CLV + DuckDB analytics infrastructure —
      the MFE script is a direct analogue of the CLV backfill).
- [ ] Phase 1 does **not** need to have passed before starting
      2b — MFE measurement on existing history is independent of
      the SHARED_PREAMBLE rewrite. Running 2b in parallel with 1
      is explicitly acceptable.

---

## Goals (measurable)

1. **MFE distribution known** on historical BUYs: mean, median,
   stddev, by category, by price band, by time-to-resolution.
2. **Positive-EV threshold identified under chronological
   train/test** — best TP threshold found on a pre-registered
   train window, then net PnL measured on a held-out window, after
   Polymarket fees + plausible slippage. Best-in-sample threshold
   alone is rejected as lookahead-biased per `CTO_Agent.md` §7 and
   external review v2 §5.3.
3. **Retrospective verdict:** chase (proceed to Phase 2c listener),
   park (no capturable opportunity above fee drag), or iterate
   (inconclusive at current n; re-run after N more resolved BUYs).

---

## Tasks

### Task 2b.1 — Retroactive MFE analysis with chronological train/test

**What:** For each resolved BUY in history, query CLOB full price
history between entry and resolution, compute MFE and time-of-MFE.
Then evaluate take-profit thresholds under strict chronological
train/test discipline — no post-hoc threshold selection on the
full set.

**Why:** Before building a listener, know whether MFE is big enough
to chase *after fees*, measured in a way that survives out-of-sample.
If the held-out window's best-in-train threshold does not beat
hold-to-resolution, a listener is pointless.

**Implementation:**

- Script: `scripts/mfe_analysis.py` (new; parallels
  `scripts/backfill_clv.py` structurally).
- For each resolved BUY:
  - Query CLOB history for the bought token between entry
    timestamp and event resolution timestamp.
  - Compute `mfe = max(price) - entry_price` (max favorable move).
  - Compute `mfe_time_fraction` (how far through the event the
    MFE occurred — early spike vs late swing has different
    actionability).
  - Persist to `~/.pqi/mfe.jsonl` (atomic tmp+rename per
    `known-issues.md` persisted-state contract).
- Aggregate analysis via DuckDB (register an `mfe_by_row` view
  mirroring the `clv_by_row` pattern when the sidecar exists):
  - MFE histogram
  - MFE by category
  - MFE by price band
  - Correlation: does MFE predict final hit? (If MFE is high, does
    the bet usually resolve YES?)
- **Train/test split (promoted to task requirement per external
  review v2 §5.3 / §6.4):**
  - Chronologically split resolved BUYs 70% train / 30% test by
    `event_start_time`.
  - On the train window, sweep take-profit thresholds
    `{2, 3, 5, 7, 10}pp` and identify the one that maximises net
    realized P&L vs hold-to-resolution, net of 2% round-trip fees.
    Record tie-breaking rule (prefer lower threshold on tie — less
    path-dependent).
  - Lock that threshold. Apply it unchanged to the test window.
  - Report test-window net P&L vs hold-to-resolution as the verdict
    metric. In-sample-only numbers may be reported alongside but
    are not the verdict.
- **Verdict bands:**
  - Test net P&L ≥ +5% over hold-to-resolution **after fees** →
    verdict = chase (unblock Phase 2c).
  - Test net P&L in [−2%, +5%] → verdict = iterate (re-run after
    N more resolved BUYs; park Phase 2c for now).
  - Test net P&L < −2% → verdict = park (no capturable edge above
    fee drag; skip Phase 2c entirely).

**Acceptance criteria:**

- [ ] MFE computed for all resolved BUYs with CLOB coverage; rows
      without coverage logged as unavailable (same contract as CLV
      backfill).
- [ ] Chronological 70/30 split executed; train-window threshold
      locked before test-window evaluation.
- [ ] Test-window net P&L vs hold-to-resolution published with
      Wilson CI on the paired-row delta.
- [ ] Retrospective written at
      `docs/retrospectives/phase_2b_mfe_YYYY_MM.md` with an
      explicit chase/park/iterate verdict bound to the test-window
      metric.

**Files touched:**

- New: `scripts/mfe_analysis.py`
- New persisted state: `~/.pqi/mfe.jsonl`
- Possibly: `pqi/analytics_sql.py` — add conditional `mfe_by_row`
  view registration (parallels `clv_by_row`; only registers when
  the sidecar exists).
- New: `docs/retrospectives/phase_2b_mfe_YYYY_MM.md`

**Risks:**

- **CLOB hourly resolution may miss sharp intra-hour excursions.**
  Retroactive MFE is therefore a *lower bound* on achievable
  realtime MFE. Document the bias in the retrospective; do not
  adjust the verdict threshold to compensate.
- **Polymarket fees (~2% round-trip).** A 4pp MFE with 2% fees =
  2pp realized. Bake fees into every simulated P&L number; do not
  report fee-gross numbers as the headline.
- **Slippage on realistic order sizes.** Assume Kelly-sized bets
  on a small bankroll (small orders → low slippage). Document
  the bankroll assumption. Revisit only if Phase 4 triggers
  multi-user / larger positions where the capacity axis
  (`CTO_Agent.md` §5) starts to bind.
- **MFE-correlated-with-final-hit gotcha.** If high-MFE bets *also*
  tend to resolve YES more often than low-MFE bets, taking profit
  early gives up some of the hit-rate. The simulated P&L compares
  *net* realized P&L against hold-to-resolution P&L on the same
  rows — this accounts for the correlation naturally. Do not
  simulate MFE capture alone without the paired counterfactual.
- **Lookahead bias if the chronological split is skipped.** The
  original Phase 2 doc's Risks section noted this; the external
  review v2 (§5.3) flagged it as misplaced at risk-note severity.
  It is now a task requirement. If the retrospective quotes an
  in-sample-optimal threshold without a test-window number beside
  it, the verdict is invalid.

---

## Exit criteria

All of:

- [ ] `scripts/mfe_analysis.py` produces `~/.pqi/mfe.jsonl` with
      coverage matching the CLV backfill pattern.
- [ ] Chronological train/test split executed; best-in-train
      threshold locked and evaluated on held-out test.
- [ ] Retrospective published with chase/park/iterate verdict.
- [ ] `docs/phases/README.md` and `CLAUDE.md` Current State
      updated with the verdict and resulting status of Phase 2c.

---

## Validation: "are we done?"

1. Do I know the MFE distribution on historical BUYs? (Yes → goal
   1 passed.)
2. Did the chronological train/test split run, and is the verdict
   bound to the held-out test-window number? (Yes → goal 2
   passed.)
3. Is there a written retrospective with an explicit
   chase/park/iterate call? (Yes → goal 3 passed.)
4. Does Phase 2c's status in
   [`docs/phases/README.md`](README.md) reflect the verdict?
   (Yes → exit criteria met.)

---

## Risks / decision points

- **MFE is noise, not signal.** Short-horizon price moves in
  prediction markets can be liquidity events or sentiment flickers
  that reverse before resolution. The train/test split is what
  distinguishes "historical noise pattern" from "exploitable
  signal." If train-window optimal threshold does not beat
  hold-to-resolution on the test window, the verdict is park —
  even if the in-sample number looked great.
- **Fee drag dominates thin edges.** At 2% round-trip, any TP
  threshold below ~4pp is below break-even before slippage. Don't
  over-interpret a "+1pp gross" finding; it's ≈ −1pp net.
- **Phase 2b does not depend on Phase 1.** It is independent
  measurement on existing history. Even if Phase 1's unified
  acceptance criteria have not yet been hit, 2b's verdict is
  still valid — it measures exit-side edge, not council prediction
  edge.

---

## Related work

- **Phase 2a** (Tier A auto-BUY) — does not depend on 2b's verdict.
  Tier A holds to resolution and makes no exit decisions; the only
  execution-edge layer Tier A ignores is the one 2b is measuring.
- **Phase 2c** (MFE listener prototype) — gated on 2b verdict =
  chase. The Tier B take-profit path lives downstream of 2c.
- **CLV backfill pattern** (session 35, `scripts/backfill_clv.py`)
  — structural template for `mfe_analysis.py`. Same JSONL sidecar
  contract, same atomic tmp+rename write, same conditional DuckDB
  view registration.
- **`docs/retrospectives/phase_0_edge_2026_04.md` §4** — pre-event
  CLV write-up is the direct analogue for how 2b's retrospective
  should be structured (histogram, category breakdown, CI on the
  headline number, explicit unavailability accounting).

### Roadmap themes

- **Theme B.6** (match watcher with time-based stop-loss) — lives
  downstream of 2c, not 2b. 2b is pure measurement on historical
  data.

### Files referenced

- `pqi/market/clob.py` — CLOB history fetch, reusable for MFE.
  May need a tiny helper to expose raw hourly points (current
  interface returns `SidePriceHistory` aggregates).
- `pqi/history.py` — resolved-BUY enumeration for the MFE loop.
- `scripts/backfill_clv.py` — structural template.
- `pqi/analytics_sql.py` — view registration for the new sidecar.

### Session logs

- Session 35 — CLV backfill (template).
- Session 38 — Phase 0 retrospective (reference for writeup style).

---

## Session state

**Last work session:** *not yet started*

**Done:** *nothing yet*

**In-progress:** *nothing*

**Next action:** First task on resume is Task 2b.1 — the MFE
analysis script with chronological train/test discipline. Does
**not** require waiting on Phase 1; the two phases touch different
files (2b = scripts + DuckDB views, 1 = `personas.py` prompts)
and measure different things.

**Notes for next session:**

- CLOB history fetch is already reusable from
  `pqi/market/clob.py` — don't rewrite. The current interface
  returns `SidePriceHistory` aggregates; raw hourly points may
  need a small exposure helper.
- Open-position tracking is not needed for 2b — this is historical
  analysis on *resolved* BUYs only. Listener-side position
  tracking lives in Phase 2c.
- Chronological split implementation: sort resolved BUYs by
  `event_start_time`, cut at the 70th percentile. Record the cut
  timestamp in the retrospective so the split is reproducible.
- Do not tune the threshold-sweep set `{2, 3, 5, 7, 10}pp` based
  on early looks at the data. The set is pre-registered here to
  prevent grid-search escalation.
