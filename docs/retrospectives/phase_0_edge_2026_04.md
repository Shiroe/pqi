# Phase 0 Edge Validation Retrospective — April 2026

**Status:** in-progress (Task 0.4 landed; Task 0.5 pending)
**Date started:** 2026-04-19 (session 37)
**Scope:** End-to-end synthesis of Phase 0 measurement work
(DuckDB wire-up → pre-event CLV → reliability diagram →
per-band calibration). A "no code change" document — the only
thing this workstream is allowed to touch is our understanding
of the current edge.

Tasks 0.1 / 0.2 / 0.3 results are summarized in the phase doc
and will be quoted here in Task 0.5. This file is currently
scoped to **Task 0.4 — per-band calibration check** and the
open items that feed the final verdict.

---

## 1. Scope of the per-band calibration check (Task 0.4)

The banded EV gate (session 31) is the final arbiter in
`scoring.py`. It rejects a candidate BUY when
`side_price >= band_wr`, where `band_wr` is the Beta-binomial
smoothed win rate for the price band containing `side_price`,
stored in `~/.pqi/stats.json` under `band_stats` and recomputed
from `history.jsonl` after every `history sync`.

Task 0.4's question: **does the threshold match realization?**
If the 0.60–0.70 band's realized rate is 45% but the gate is
using a 65.9% threshold, the gate is effectively letting −EV
bets through. Conversely, if a band's realized rate is
materially higher than its threshold, the gate is rejecting
bets it didn't need to.

### Live gate state at measurement time

| Field | Value |
| --- | --- |
| `stats.json` computed at | 2026-04-18T21:28:00Z |
| Non-legacy resolved BUYs (`sample_size`) | 65 |
| Correct BUYs / Wrong BUYs | 45 / 20 |
| Global `true_win_rate` | 0.6923 |
| `BAND_SHRINKAGE_K` | 5.0 |
| `MIN_WIN_RATE_SAMPLES` | 20 (gate active) |

`band_stats` uses `winning_side`-matched outcome semantics (only
rows with a clean `resolution.winning_side` count; intermediate
settlements are excluded). To keep realized rate apples-to-apples
with the threshold, the per-band calibration query below uses
the same semantics. A secondary view using directional-winner
semantics (final vs entry price — matches `classify_outcome` and
the agent-calibration work in Task 0.3) is also shown for
cross-reference.

### Coverage accounting (65 resolved BUYs)

| Bucket | n | Where it lands |
| --- | --: | --- |
| Clean winner, inside the 5 EV-gate bands | 62 | Included in table below |
| Clean winner, price outside [0.09, 0.95] | 1 | Out-of-band (price 0.9995, realized 1/1) — gated by `MAX_TRADEABLE_PRICE`; no band threshold applies |
| Intermediate settlement (no clean `winning_side`) | 2 | Excluded from `band_stats` by construction; included in `classify_outcome` |

Band coverage is **62/65 = 95.4%**. The three rows outside
the band-stats denominator are all accounted for (one
price-ceiling row, two intermediate settlements). No silent
drops.

---

## 2. Per-band realized vs threshold (winning_side semantics)

95% Wilson CIs. `Δ(real − thr)` is realized rate minus the
Beta-binomial smoothed gate threshold. `Δ(real − price)` is
realized rate minus the band's mean entry price — positive =
band is +EV on entry; negative = band is −EV on entry (the
"implied-prob floor" check from Task 0.4).

| Band | n | wins | raw WR | smoothed WR (gate thr) | mean price | Wilson 95% CI | CI width | Δ(real − thr) | Δ(real − price) |
| --- | --: | --: | --: | --: | --: | --- | --: | --: | --: |
| [0.09, 0.50) | 0 | 0 | — | 0.6923 *(global fallback)* | — | — | — | — | — |
| [0.50, 0.60) | **8** | 3 | 0.3750 | **0.4970** | 0.5606 | [0.137, 0.694] | 0.557 | **−0.122** | **−0.186** |
| [0.60, 0.70) | 23 | 15 | 0.6522 | 0.6593 | 0.6526 | [0.449, 0.812] | 0.363 | −0.007 | −0.000 |
| [0.70, 0.80) | 15 | 11 | 0.7333 | 0.7231 | 0.7537 | [0.481, 0.891] | 0.411 | +0.010 | −0.020 |
| [0.80, 0.95) | 16 | 15 | 0.9375 | 0.8791 | 0.8384 | [0.717, 0.989] | 0.272 | +0.058 | **+0.099** |

**Thin-sample flag (n<10):** [0.09, 0.50) (n=0), [0.50, 0.60)
(n=8). Read with caveat — Wilson CI widths of ≥0.36 on the
remaining bands are already wide.

### 2.1 Band-by-band reading

- **[0.09, 0.50) — empty. n=0.** No evidence either direction.
  The gate currently falls back to the global WR (0.6923),
  which means any BUY at side_price < 0.6923 would clear the
  gate here. That is the most forgiving fallback and the band
  most likely to admit mistakes at scale. **Decision:** no
  change; flag as "no evidence" rather than tune.

- **[0.50, 0.60) — 8 BUYs, realized 37.5%.** Materially below
  both the gate threshold (0.497) and the mean entry price
  (0.561). Point-estimate says the band is −EV; Wilson CI is
  half-width ~28pp on n=8 (CI [0.137, 0.694]) so the signal
  is noisy but the DIRECTION is consistent with session 31's
  motivation for the banded gate. The current gate at
  smoothed 0.497 would already reject all future prices ≥0.50
  in this band (the band itself starts at 0.50), which is the
  protective outcome — **the gate's live behaviour here is
  already conservative going forward.** The calibration
  concern is retrospective: these 8 BUYs were placed under
  the pre-session-31 scalar-WR regime, so they are not
  evidence that the current banded gate is under-rejecting —
  they are the input that TAUGHT the current gate to reject
  them.

- **[0.60, 0.70) — 23 BUYs, realized 65.2%.** Well-calibrated:
  Δ vs threshold −0.007, Δ vs mean price −0.000 (rounds to
  zero). Realized rate essentially equals mean entry price
  → BUYs in this band break even on EV. This matches the
  session-34 finding of marginal executed-edge and confirms
  that the densest-sampled band is the one where threshold
  and realization most agree. The band carries the most data
  weight; its calibration shapes gate behaviour at the
  threshold boundary (where most candidate BUYs land).

- **[0.70, 0.80) — 15 BUYs, realized 73.3%.** Nominally
  on-threshold (Δ +0.010) and slightly below mean price (Δ
  −0.020). Within CI; not actionable on its own. Edge on
  entry is weak-to-zero on point estimate, consistent with
  the [0.60, 0.70) reading.

- **[0.80, 0.95) — 16 BUYs, realized 93.8%.** Best band on
  both metrics. Δ vs threshold +0.058 (point estimate
  above), Δ vs mean price **+0.099** (strong positive EV on
  entry). CI width 0.272 (tightest of any band); the lower
  bound (0.717) still sits above the threshold's 0.879 was
  only by 0.00 but the realized point estimate is comfortably
  above both. This is the only band where the point-estimate
  evidence says the gate might be slightly **over-rejecting**,
  but Δ +0.058 < CI width 0.272, so it does not clear the
  "≥ 1 CI width" flag from the task brief. Record as
  "directional over-rejection candidate; do not retune."

### 2.2 Cross-check with directional-winner semantics

For reference — the Task 0.3 reliability diagram and the
per-agent Brier work use directional-winner semantics (final
vs entry price), which lets intermediate settlements
contribute to calibration. The per-band realized rates differ
slightly:

| Band | n (winning_side) | realized (WS) | n (directional) | realized (dir) | shift |
| --- | --: | --: | --: | --: | --- |
| [0.50, 0.60) | 8 | 0.375 | 9 | 0.333 | +1 row, worse |
| [0.60, 0.70) | 23 | 0.652 | 23 | 0.652 | identical |
| [0.70, 0.80) | 15 | 0.733 | 16 | 0.688 | +1 row, worse |
| [0.80, 0.95) | 16 | 0.938 | 16 | 0.938 | identical |
| (0.95, 1.00] | 1 | 1.000 | 1 | 1.000 | out-of-band |

Directional semantics pull the two middle bands' realized
rates DOWN by 2–4pp (intermediate settlements in those bands
are pushes or partial YES-gains that didn't match the
declared winner). The gap is immaterial inside the Wilson
CIs. The **gate's own thresholds are frozen to winning_side
semantics**, so the primary table above is the
apples-to-apples view for gate calibration.

### 2.3 Flagged bands (summary)

Flag rules from the phase doc:
- **Under-rejecting (-EV passed through):** realized rate
  below mean entry price by more than the Wilson CI width.
- **Over-rejecting (bets excluded that shouldn't be):**
  realized rate above gate threshold by more than the Wilson
  CI width.

| Band | Δ(real − price) | CI width | Under? | Δ(real − thr) | CI width | Over? |
| --- | --: | --: | --- | --: | --: | --- |
| [0.50, 0.60) | −0.186 | 0.557 | No (|Δ| < CI) | −0.122 | 0.557 | No |
| [0.60, 0.70) | −0.000 | 0.363 | No | −0.007 | 0.363 | No |
| [0.70, 0.80) | −0.020 | 0.411 | No | +0.010 | 0.411 | No |
| [0.80, 0.95) | +0.099 | 0.272 | N/A (+, favourable) | +0.058 | 0.272 | No |

**No band clears the "≥ 1 CI width" flag in either direction.**
The CIs are simply too wide at n=8–23 per band to rule out a
range of true values that overlap the thresholds.

However, **direction** is consistent and worth recording for
Task 0.5:
- **[0.50, 0.60) is directionally under-rejecting** on
  point estimate. The current gate is already protective
  going forward (threshold 0.497 ≤ band floor 0.50), so no
  action is required beyond "monitor as BUYs accumulate."
- **[0.80, 0.95) is directionally over-rejecting** on point
  estimate. Not statistically significant; don't retune.

---

## 3. `BAND_SHRINKAGE_K` recommendation

Per the phase doc: *do not tune K on the same data used to
compute band_stats.* Task 0.4 measures; Task 0.5 recommends.

Keeping that rule strict, the only defensible
recommendation this session is **keep K=5** until either
(a) BUY sample size per band approximately doubles (target:
each occupied band at n≥20), or (b) a pre-registered
hold-out test is designed against future BUYs.

Notes for the Task 0.5 writer to consider (not a tuning
recommendation — a checklist for when K can be revisited):

- With K=5 and global 0.6923, a band with n=8 and raw_wr
  0.375 is pulled to smoothed 0.497 (+12.2pp toward global).
  The pseudo-count is currently ~38% of the evidence mass in
  that band (5/13), which is "moderate shrinkage."
- If we lowered K (say K=2), the [0.50, 0.60) threshold
  would drop to 0.439 — making the gate MORE protective in
  this band (it would reject prices ≥0.44, vs the current
  ≥0.50). But the same K=2 would push [0.80, 0.95)
  smoothed from 0.879 to 0.909, making the gate LESS
  protective in the favourites band. These move in opposite
  directions because the raw WRs straddle the global.
- If we raised K (K=10), the opposite happens: [0.50, 0.60)
  smoothed rises to 0.551 (less protective; gate stops
  rejecting borderline 0.50–0.55 BUYs), and [0.80, 0.95)
  drops to 0.853 (more protective on favourites).
- The "best" K is the one that minimises band-weighted
  realized-loss to threshold miscalibration on **future**
  BUYs. We do not have a future-BUY holdout yet; attempting
  to pick K on the same 62 rows is dressed-up overfitting.

**Decision for Task 0.5:** keep K=5. Note the asymmetry
above as a monitoring item. Revisit when either (a) the
[0.50, 0.60) band reaches n=20 (currently 8, needs 12 more
BUYs to land here — slow rate since the current gate
rejects most prices in this band) OR (b) a pre-registered
hold-out period is defined.

---

## 4. Open items for Task 0.5

Task 0.5 will synthesise:

- **Pre-event CLV distribution** (Task 0.2 output,
  `~/.pqi/clv.jsonl`): mean −0.0167, stddev 0.064,
  coverage 44/65 (67.7%). Per-category gap: valorant
  −0.032 (n=17) worst, league-of-legends −0.001 (n=14)
  neutral, counter-strike −0.013 (n=8). CLV-vs-outcome
  join at n=22/bucket: no monotone gradient yet.
- **Reliability diagram** (Task 0.3 output,
  `~/.pqi/reports/reliability_20260418.md`): council Brier
  0.2176, Bear Brier 0.2810 (only agent above chance
  baseline), 5-agent cluster mirror collapse (9/10
  cluster-internal pairs at r ≥ 0.85), Bull overconfident
  on favourites (22pp gap on n=16 at [0.80, 0.95)).
- **Per-band calibration** (this task, section 2): no
  statistically significant miscalibration; directional
  findings preserved for monitoring.
- **Verdict on edge:** validated / no-edge / inconclusive.
  Preview — the three measurement streams all point to
  **inconclusive at current n**, with the council-level
  Brier (0.2176 vs 0.25 baseline) as the strongest single
  signal of prediction edge and the aggregate CLV
  (−0.0167) as the mildest headwind on execution edge.
- **Recommended next actions:** which phase to prioritise
  (Phase 0's n-accumulation monitor vs Phase 1 signal-
  quality prompt rewrite); which gate knobs (if any) to
  revisit; what additional data to collect.

---

## 5. Data artefacts

### Query — per-band realized vs threshold (winning_side semantics)

```sql
WITH buys AS (
    SELECT
        CASE WHEN result.recommendation = 'BUY YES'
             THEN result.market.yes_price
             ELSE 1.0 - result.market.yes_price
        END AS side_price,
        CASE
            WHEN (result.recommendation = 'BUY YES' AND resolution.winning_side = 'YES')
              OR (result.recommendation = 'BUY NO'  AND resolution.winning_side = 'NO')
            THEN 1 ELSE 0 END AS win
    FROM history
    WHERE legacy = false
      AND result.recommendation LIKE 'BUY%'
      AND resolution.winning_side IS NOT NULL
)
SELECT
    CASE
        WHEN side_price < 0.09 THEN '[0.00, 0.09)'
        WHEN side_price < 0.50 THEN '[0.09, 0.50)'
        WHEN side_price < 0.60 THEN '[0.50, 0.60)'
        WHEN side_price < 0.70 THEN '[0.60, 0.70)'
        WHEN side_price < 0.80 THEN '[0.70, 0.80)'
        WHEN side_price <= 0.95 THEN '[0.80, 0.95]'
        ELSE                          '(0.95, 1.00]'
    END AS price_band,
    COUNT(*)                    AS n,
    SUM(win)                    AS wins,
    ROUND(AVG(win * 1.0), 4)    AS realized_rate,
    ROUND(AVG(side_price), 4)   AS mean_price
FROM buys
GROUP BY price_band
ORDER BY price_band
```

Wilson 95% CIs and deltas are computed from this output in
Python (see `## 2` above). Recipe:

```
z = 1.959964
p = wins / n
denom = 1 + z^2/n
center = (p + z^2/(2n)) / denom
halfwidth = z * sqrt(p(1-p)/n + z^2/(4n^2)) / denom
CI = [center - halfwidth, center + halfwidth]
```

### Paired inputs

- `~/.pqi/stats.json` → `band_stats[].smoothed_wr` is the
  gate threshold for each band.
- `~/.pqi/history.jsonl` → via DuckDB `history` view, pulls
  `result.recommendation`, `result.market.yes_price`,
  `resolution.winning_side`.

---

## 6. Changelog for this file

| When | By | Change |
| --- | --- | --- |
| 2026-04-19 (session 37) | Phase 0 Task 0.4 | Initial per-band calibration table + flagged-bands summary; K recommendation deferred to Task 0.5 per phase-doc guardrail. |
