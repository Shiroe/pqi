# Phase 0 Edge Validation Retrospective — April 2026

**Status:** closed (Tasks 0.1–0.5 done)
**Date started:** 2026-04-19 (session 37)
**Date closed:** 2026-04-19 (session 38)
**Scope:** End-to-end synthesis of Phase 0 measurement work
(DuckDB wire-up → pre-event CLV → reliability diagram →
per-band calibration). A "no code change" document — the only
thing this workstream is allowed to touch is our understanding
of the current edge.

---

## 0. Executive summary

**Verdict: inconclusive at current n (65 resolved BUYs in
`stats.json`; 385 resolved rows / 2,668 verdicts in the
reliability sample).** The three measurement streams align on
"mild positive prediction edge, mild negative execution entry
quality, no actionable gate miscalibration" — none of the three
clears a statistically significant threshold on its own, and
realized April PnL is marginal (≈ −0.9¢/share on 65 BUYs).

- **Prediction edge (reliability):** Council Brier 0.2176 beats
  the 0.25 chance baseline by ~3pp — the strongest single
  signal of real edge. But the council aggregate is fragile:
  5 of 7 agents form a tight correlation cluster (r 0.80–0.92),
  Bull is overconfident on favourites (−22pp gap at [0.80,
  0.95)), and Bear — the only agent breaking the cluster — is
  the only agent with Brier **above** the chance baseline
  (0.2810). Prediction edge at aggregate level exists but
  collapses if any one of those three pathologies gets worse.
- **Execution entry quality (CLV):** Mean pre-event CLV
  −0.0167 on n=44 (67.7% coverage; 21 rows blocked by permanent
  `no_tokens` that predate session-22 `clob_token_ids`
  persistence). Slightly unfavourable entry — roughly 1.7pp
  above the pre-event mark on average, with per-category
  asymmetry (valorant −0.032 worst, LoL −0.001 neutral).
  Directionally consistent with "we buy a bit late" but the CI
  is wide at n=44.
- **Gate calibration (per-band):** No band clears the "≥ 1
  Wilson-CI-width" flag in either direction. Two dense bands
  ([0.60, 0.70) n=23, [0.70, 0.80) n=15) are well-calibrated
  on point estimate — and realizing close to entry price, i.e.
  break-even EV on entry. [0.50, 0.60) point-estimate under-
  rejects (realized 0.375 vs mean price 0.561, Δ −0.186 on
  n=8), but the gate's live threshold (0.497) already sits
  below the band floor (0.500), so the protective outcome is
  already maximally achieved going forward. [0.80, 0.95) is
  the best-EV band (realized 0.938, +9.9pp vs mean price),
  mild over-rejection candidate — not statistically
  significant.

**One-sentence justification:** the council has a small but
real prediction edge that is actively being eaten by entry
timing and a cluster of correlated agents, leaving realized
PnL near zero; the highest-leverage next move is fixing the
signal, not tuning the gate.

**Highest-leverage next phase: Phase 1** (Option B autonomy
fix). Rationale below in §7.

**Guardrail honoured:** `BAND_SHRINKAGE_K` recommendation from
Task 0.4 stands — **keep K=5**. Do not re-tune K, ROI bands,
`MIN_CONTRARIAN_PROBABILITY`, or the gate stack in Phase 1;
this retrospective produces no knob changes.

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

## 4. Pre-event CLV distribution (Task 0.2)

Source: `~/.pqi/clv.jsonl` (44 rows with `available=true`, 21
rows flagged `unavailable_reason='no_tokens'`). Pre-event CLV
is defined per `CTO_Agent.md` §5: price at the last hourly
CLOB bucket *before* `event_start_time` minus our entry price
(positive CLV = we beat pre-event consensus). Bucket policy is
last-before-target; the alternative closest-overall leaks the
post-event resolution spike (see session-log §35 for the
0.835 → 0.9995 probe that validated the choice).

### 4.1 Aggregate distribution

| Metric | Value |
| --- | --: |
| n (available) | 44 |
| n (unavailable, `no_tokens`) | 21 |
| Coverage rate | 67.7% |
| Mean CLV | **−0.0167** |
| Stddev | 0.0640 |
| Min | −0.26 (`d368cc6f`, BUY YES) |
| Max | +0.12 (`71e89320`, BUY YES) |

Reading: the average bought side was priced about 1.7pp *above*
the pre-event market on entry. Direction is mildly unfavourable —
we pay ~2pp vig on entry on average — but at stddev 0.064 and
n=44, the 95% CI on the mean is roughly ±0.019, which brackets
zero. *Direction* is real-enough to note; *magnitude* is not
tight enough to claim "we always enter late."

The coverage gap is structural, not recoverable: the 21
unavailable rows all predate session 22 (before we began
persisting `clob_token_ids` on `MarketData`), so CLOB lookup
has no token to query. Every BUY entered since session 22
carries tokens and the script succeeds for 100% of those. The
coverage rate will climb monotonically as new BUYs accumulate;
no action required.

### 4.2 Per-category breakdown

| Category | n | Mean CLV | Stddev |
| --- | --: | --: | --: |
| valorant | 17 | **−0.0318** | 0.0915 |
| league-of-legends | 14 | −0.0007 | 0.0181 |
| counter-strike | 8 | −0.0131 | 0.0398 |
| rainbow-six-siege | 2 | +0.0050 | 0.0141 |
| mobile-legends-bang-bang | 2 | +0.0200 | 0.0495 |
| dota-2 | 1 | −0.1300 | — |

The valorant / LoL gap is the clearest per-category signal.
Valorant worst (−0.032 ≈ 3.2pp adverse entry, stddev 0.092 —
wide), LoL neutral (−0.001, stddev 0.018 — tight; nearly every
LoL BUY lands on the pre-event mark within a few tenths of a
point). Both sample sizes are too thin to be load-bearing for a
category-specific policy change, but the pattern is worth
tracking: LoL markets are either deeper (less slippage on entry)
or our LoL synthesis happens to sit closer to consensus. The
thin-n categories (≤2) are informational only.

### 4.3 CLV-vs-outcome (what entry quality buys us)

A CLV-vs-outcome join at n=22/bucket showed **no monotone
gradient** between CLV bucket and realized win rate. In other
words: BUYs with favourable pre-event CLV did not resolve YES
materially more often than BUYs with unfavourable CLV. Two
readings of that null:

- **Prosaic:** n=22/bucket is too thin to reject a flat
  gradient. 95% CIs on bucket win rates are roughly ±21pp —
  any underlying gradient shallower than that is invisible.
  Re-check at 100+ BUYs per bucket.
- **Interesting if it persists:** if the null survives to
  larger n, it means pre-event CLV is a measure of *execution*
  quality (did we get a good price?) that is decoupled from
  *prediction* quality on this dataset. That would be
  consistent with CTO §5's framing: CLV and calibration measure
  different things. Do not combine them into a single score
  until the gradient question is answered at scale.

### 4.4 CLV caveats to carry forward

- **Polymarket ≠ closing line.** Event markets resolve 1.00/0.00;
  there is no mid-price close. Pre-event CLV uses the last
  hourly bucket before `event_start_time` as the "consensus"
  reference — this is the cleanest analog available but still a
  proxy. CTO §5 documents three complementary metrics
  (pre-event CLV, MFE, Brier); do not treat pre-event CLV alone
  as sufficient.
- **Hourly resolution = ±30 min uncertainty.** CLOB history is
  bucketed hourly. Per-row CLV has ~±30 min slop on when the
  "pre-event" snapshot was taken.
- **Bucket-drift caveat.** `clv_by_row.bucket_drift_minutes` is
  ≤ 0 by construction (last-before-target). Rows with drift
  below ~−60 min indicate CLOB's first lifecycle hour landed
  after our target; those rows are honest — the drift is
  reported per-row so analyses can filter.

---

## 5. Reliability diagram summary (Task 0.3)

Source: `~/.pqi/reports/reliability_20260418.md` (385
non-legacy resolved rows, 2,668 per-agent verdicts; outcome
via directional-winner, `final_yes_price > entry_yes_price ⇒
YES`). Bucketed into the EV-gate-aligned bands with
`[0.00, 0.09)` and `[0.95, 1.00]` bookends so no prediction
is silently dropped.

### 5.1 Brier scores

| Agent | n | Brier | Reading |
| --- | --: | --: | --- |
| council | 385 | **0.2176** | Best; beats 0.25 chance baseline by ~3pp. Confidence-weighted aggregation is buying real (if small) signal. |
| rules_lawyer | 382 | 0.2180 | Cluster floor; tracks council. |
| specialist | 384 | 0.2195 | Cluster. |
| sentiment | 385 | 0.2196 | Cluster. |
| statistician | 385 | 0.2199 | Cluster. |
| contrarian | 383 | 0.2215 | Cluster. |
| bull | 383 | 0.2370 | Cluster edge — overconfident on favourites (see §5.2). |
| bear | 366 | **0.2810** | **Only agent above the 0.25 chance baseline.** |

Five agents are tightly clustered around 0.22 (range 0.2180–
0.2215); Bull is ~1.5pp worse; Bear is ~6pp worse. A naive
always-0.5 predictor would score 0.25 on this set — so Bear is
actively *degrading* council accuracy if equally weighted. The
dynamic-weight layer (session 25) partially compensates, which
is why council Brier (0.2176) is marginally below the cluster
floor — weighting is working as designed.

### 5.2 Biggest calibration gaps

Full per-band detail in the source report; headline signals:

- **Bull, [0.80, 0.95): predicted 0.844 mean, realized 0.625,
  gap −0.219 on n=16.** Bull *systematically overprices
  favourites*. This is material — n=16 is not thin, and −22pp
  is a real miss. It also aligns with the per-band observation
  from Task 0.4 that [0.80, 0.95) is actually the best-EV band
  on realized rate (0.938); the council as a whole captures
  that, but Bull alone drags upward.
- **Bear, [0.09, 0.50): predicted 0.356 mean, realized 0.484,
  gap +0.128 on n=351.** Bear's concentration band (96% of
  its predictions live here) is systematically *under*-
  predicting YES. Bear's overall Brier of 0.2810 is almost
  entirely driven by this miss.
- **Council, [0.60, 0.70): predicted 0.642, realized 0.705,
  gap +0.063 on n=88.** Council is *underconfident* on
  moderately-high YES predictions — the aggregator is
  over-shrinking on conviction calls. Not significant at
  95% (Wilson CI 0.602–0.790 straddles 0.642), but directional
  and on the largest-n band.
- **Council, [0.70, 0.80): predicted 0.734, realized 0.800,
  gap +0.066 on n=15.** Same direction as [0.60, 0.70); thinner
  sample but consistent story.
- **Council, [0.09, 0.70): well-calibrated.** The full lower
  half of the distribution is tight. The council has no
  material bias on PASSes / NO-favouring predictions.

Bull's favourite-overconfidence and council's
YES-underconfidence in the conviction band are not
contradictions — Bull is one of seven voices; the council's
aggregate shrinks through the other six, which over-corrects
in the opposite direction.

### 5.3 Cross-agent correlation — cluster collapse is partial, not uniform

| Metric | Value |
| --- | --: |
| Mean pairwise Pearson (all 21 pairs) | 0.557 |
| Mean per-row stddev across agents | 0.094 |
| Pairs with r ≥ 0.85 | **9 / 21 (43%)** |

The 9 high-correlation pairs are not random — they form an
identifiable cluster:

```
rules_lawyer ↔ statistician    0.911
rules_lawyer ↔ sentiment       0.904
rules_lawyer ↔ specialist      0.895
rules_lawyer ↔ contrarian      0.866
statistician ↔ specialist      0.918
statistician ↔ sentiment       0.911
statistician ↔ contrarian      0.862
sentiment    ↔ specialist      0.901
sentiment    ↔ contrarian      0.871
specialist   ↔ contrarian      0.804    ← just below r ≥ 0.85
```

**Cluster: `rules_lawyer / statistician / sentiment / specialist
/ contrarian`.** Ten cluster-internal pairs, nine at r ≥ 0.85.
Bull sits partially outside (pairs with cluster at r ~0.58–0.63);
Bear is effectively uncorrelated with everyone (r ∈ [−0.17,
+0.04]).

This **refines** the session-32 "mirror collapse" framing. It
is not that all seven agents have converged to one signal; it
is that **five have converged to one signal**, Bull provides a
second weakly-independent signal, and Bear provides a third
independent-but-poorly-calibrated signal. So of the seven
intended voices, approximately *three* are measurably distinct,
and one of those three (Bear) is degrading aggregate accuracy.

This is the single most important finding for Phase 1 priority.
The SHARED_PREAMBLE autonomy fix must (a) destabilise the
5-agent cluster without (b) destroying Bull's partial
independence or (c) destroying Bear's structural independence
while (d) fixing Bear's calibration. Three constraints — more
than a single preamble rewrite can usually deliver. Phase 1
should plan for persona-by-persona rollout, not a single-shot
change, and should measure all four constraints before signing
off.

---

## 6. Verdict — inconclusive at current n, with nuance

**Inconclusive.** All three measurement streams (prediction
edge via Brier, execution-entry via CLV, gate calibration via
per-band realized-vs-threshold) produce pointers rather than
conclusions at the current sample sizes. None of the three
fails a sharp test; none passes one either.

What the data *does* say, with the caveats:

1. **There is mild positive prediction edge at council
   aggregate.** Council Brier 0.2176 vs 0.25 chance baseline
   is the strongest positive signal in the retrospective. The
   gap is ~3pp; at n=385 the bootstrap 95% CI around the
   Brier is on the order of ±0.02, so this delta is likely
   real but small. The council is *not* a coin flip; it is a
   lightly-advantaged predictor whose advantage is actively
   being absorbed by downstream frictions.
2. **Execution entry is mildly unfavourable but not
   decisively bad.** Mean CLV −0.0167 on n=44 is directionally
   adverse (we pay ~1.7pp vig on entry) but the CI straddles
   zero and category variance (valorant −0.032 vs LoL −0.001)
   is high relative to the aggregate. Calling this an
   "execution edge deficit" would be an overclaim on this
   sample.
3. **The banded EV gate is not measurably miscalibrated.** On
   the 62 resolved BUYs that fall inside its bands, no band
   clears the ≥1 CI width flag in either direction. The gate
   is neither leaking -EV bets nor excluding +EV bets in any
   statistically significant way on current data.
4. **Realized PnL is marginal.** April PnL of −0.575 per-share
   across 65 BUYs (session 34 finding, confirmed via `pqi
   query`) reads as "break-even before fees, slightly negative
   after." Consistent with the three readings above — small
   positive prediction edge, small negative entry timing,
   no-signal gate — netting to near zero.

What the data *does not* say:

- It does not say the edge does not exist. Council Brier 0.2176
  is not a null result.
- It does not say the gate is mis-tuned. No band flags.
- It does not say the council is well-diversified. The
  reliability diagram makes it clear that the apparent
  "7 agents" is closer to "3 effective signals, one of which
  is miscalibrated."

The phase's original goal was to answer: *do we have prediction
edge, and if so where?* The answer is: **yes (marginally),
concentrated in the council aggregate, fragile because the
agent diversity that is supposed to produce that aggregate is
only partly real.** That is a genuine finding, not a null.

---

## 7. Recommended next actions

### 7.1 Phase sequencing

**Primary: Phase 1 (Signal Quality Fix) — begin immediately.**
The single highest-leverage lever identified by this
retrospective is the 5-agent cluster collapse + Bear
miscalibration. `docs/phases/phase_1_signal_quality.md` was
written for exactly this problem. Phase 1's pre-rewrite
entry-criteria notes "can parallelise with Phase 0" — Phase 0
is now closed, so Phase 1 proceeds without that caveat.

The three measurable post-rewrite checks (from §5.3 and the
Phase 1 doc) are:

- **(a) Cluster-pair fraction at r ≥ 0.85 drops below 0.50.**
  Current 9/21 = 43% — close to the threshold, so small
  improvement may hit the flag; any regression is blocking.
- **(b) Bear Brier falls toward the 0.22 cluster while Bear's
  independence is preserved** (Bear pairwise r stays well
  below 0.85).
- **(c) Council Brier holds or improves from 0.2176.** If the
  prompt rewrite destabilises the cluster without preserving
  signal, council Brier will *rise* — a clear regression.

Re-run `scripts/reliability_diagram.py` after every prompt
change to measure delta; the report is versioned by UTC date
so iteration history is retained.

**Secondary: Option B step 5 backtest stays blocked** until
§5.3's cluster-pair fraction metric crosses below 0.50.
Running the pre-registered ΔBrier ≤ −0.005 CI95<0 test on a
mirror-collapsed sample is mathematically guaranteed to fail;
re-reading the cluster matrix above confirms that Option B's
"autonomy" signal is absorbed by the aggregate cluster.

**Phase 2 (Execution Edge / MFE listener):** originally blocked
on P0 verdict. The "inconclusive but mild positive prediction
edge" verdict unblocks P2 *for scoping work*, but not for
heavy infrastructure investment. Realized PnL is marginal; any
execution-edge investment should be sized against how much it
can move PnL given a prediction edge that is itself small.
Recommendation: P2 MFE *analysis* (a DuckDB-only measurement
task analogous to Task 0.2) is reasonable to do in parallel
with P1; P2 listener/auto-trader is premature.

**Phase 3 (Deployment Portability):** originally blocked on a
positive P0 verdict. The current verdict is inconclusive, not
positive; defer P3 until P1 produces a measurable Brier
improvement *or* an explicit product decision is made to ship
the advisor at current edge.

**Phase 4 (Scaling Triggers):** stays reactive-only, no
change.

### 7.2 Gate knobs — do not retune

Explicitly: **no gate/scoring knob changes fall out of this
retrospective.** Specifically:

- `BAND_SHRINKAGE_K` stays at **5**. Task 0.4's K-asymmetry
  note (K↓ helps [0.50, 0.60) and hurts [0.80, 0.95), K↑ does
  the reverse) makes this a two-sided tradeoff that cannot be
  resolved on the same 62 rows that produced the current
  `band_stats`. Revisit when either (a) the [0.50, 0.60) band
  reaches n ≥ 20 — currently 8, and the gate rejects most
  future BUYs there, so growth will be slow — or (b) a
  pre-registered hold-out period is formally defined.
- `MIN_CONTRARIAN_PROBABILITY` stays at **0.60** (session
  32). The monitoring task from sessions 32+ ("first 5–10
  resolved CONTRARIAN BUYs") is still open; do not retune on
  < 5 resolved bets.
- ROI bands, Kelly multiplier, `MIN_WIN_RATE_SAMPLES`:
  unchanged. The aggregator has been stable since sessions
  31–32 and this retrospective provides no evidence to move
  it.

### 7.3 Additional data to collect (background, no blocker)

- **Reliability diagram on a monthly cadence.** Re-run
  `scripts/reliability_diagram.py` at the end of each calendar
  month (UTC-dated filename → no overwrites). Lets us track
  (a) cluster drift, (b) Bear Brier, (c) council Brier over
  time independent of phase work.
- **CLV coverage grows organically.** All BUYs since session
  22 carry tokens → future runs will increase the 67.7%
  coverage. No action; just re-run `python -m
  scripts.backfill_clv` between major analyses (idempotent —
  re-processes only new rows).
- **Per-band n accumulation.** The [0.50, 0.60) band is the
  highest-value thin band (material point-estimate gap, but
  CI-swallowed). Track; revisit K when it reaches n ≥ 20.
- **MFE analysis** (if P2 scoping is pursued in parallel with
  P1): DuckDB + CLOB history, no infrastructure investment.
  Analogous to Task 0.2. Produces the third CTO-§5 metric
  (max favourable excursion) so the edge triangulation is
  complete before anyone asks for a listener.

### 7.4 Monitoring carried over

From the session 37 prompt, still open:

- Observe session-32 contrarian 0.60 gate in the wild
  (skew toward [0.60, 0.65) band; revisit confidence/ROI
  guards before probability floor if first 5–10 trend
  negative).
- Observe session-32 `pass_reason` format on banded-EV-gate
  downgrades.
- Observe session-30 retry logging on next 529/429.
- Verify session-28 PandaScore + disambiguation end-to-end
  on the next `-c counter-strike --live` run.

---

## 8. Data artefacts

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

## 9. Changelog for this file

| When | By | Change |
| --- | --- | --- |
| 2026-04-19 (session 37) | Phase 0 Task 0.4 | Initial per-band calibration table + flagged-bands summary; K recommendation deferred to Task 0.5 per phase-doc guardrail. |
| 2026-04-19 (session 38) | Phase 0 Task 0.5 | Folded in pre-event CLV (§4) and reliability-diagram (§5) summaries; wrote verdict (§6) and next-actions (§7); added executive summary (§0); renumbered Data artefacts → §8, Changelog → §9. Phase 0 closed. |
