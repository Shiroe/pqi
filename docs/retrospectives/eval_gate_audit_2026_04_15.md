# PQI Gate & EV Audit — Session 24 Retrospective

**Date:** 2026-04-15
**Scope:** Full history audit (279 runs / 240 non-legacy) after sessions 22-23
added the EV gate + Kelly, session 20 rewrote the Bear, and session 19 loosened
the ROI floor.
**Trigger:** Perceived win-rate deterioration over the most recent ~20 resolved
entries — need to distinguish "new features broke something" from "sample size
grew and regressed to the true mean."
**Change implemented:** None yet. This is an observation-only audit. Per user
instruction the model layer (verdict math, gate stack) is not touched in this
session — findings are recorded as inputs to Theme A (Modeling & Calibration)
roadmap items.

---

## 1. Context

| Metric | Value |
|--------|-------|
| Total runs | 279 |
| Legacy runs (pre-session-17 schema, no persisted verdicts) | 39 |
| Non-legacy runs (full-fidelity, resolved per-agent verdicts) | 240 |
| Resolved BUYs (non-legacy, gate-relevant) | 50 |
| Correct BUYs | 35 |
| Wrong BUYs | 15 |
| **True win rate (what stats.json feeds into the EV gate)** | **70.0%** |
| Missed opportunities (non-legacy PASSes where the blocked side won) | 46 |
| Correct PASSes (non-legacy) | ~160 |
| EV gate state | Active (n=50 ≥ 20 threshold) |
| Kelly @ 75¢ with WR=0.70 | 0.0 (-EV) |

Tier-1 retrospective (2026-04-10) had 60 non-legacy / 10 resolved BUYs at 80%
WR. This audit has 240 non-legacy / 50 resolved BUYs at 70% WR — **5× the
BUY sample**. The WR drop is not a regression; it is the sample growing large
enough to stop being flattered by a handful of lucky early bets.

### Gate thresholds in force during this window

| Gate | Value | Session |
|------|-------|---------|
| `MIN_TRADEABLE_PRICE` | 0.09 | 24 (reconciled) |
| `MAX_TRADEABLE_PRICE` | 0.90 | 24 (reconciled) |
| `MIN_EXPECTED_ROI` | 0.10 | 19 (loosened from 0.15) |
| `MIN_CONTRARIAN_ROI` | 0.20 | baseline |
| `CONFIDENCE_HIGH_THRESHOLD` | 0.67 | baseline |
| `MIN_COUNCIL_PROBABILITY` | 0.60 | 24 (reconciled) |
| `MIN_CONTRARIAN_PROBABILITY` | 0.70 | baseline |
| `MIN_CONTRARIAN_CONFIDENCE` | 0.80 | baseline |
| `EV_GATE_ENABLED` | true | 22 |
| `MIN_WIN_RATE_SAMPLES` | 20 | 22 |
| `KELLY_MULTIPLIER` | 0.5 | 22 |
| `BEAR_WEIGHT` | 1.0 | 20 |
| `RULES_LAWYER_WEIGHT` | 1 | 24 (reconciled, was 2 in docs) |

---

## 2. Command / Script Log

| Command | Purpose |
|---------|---------|
| `pqi history stats` | Overall + per-agent leaderboard |
| Direct `~/.pqi/stats.json` inspection | Verify live EV-gate inputs |
| Custom Python: load `history.jsonl` + `classify_outcome()` | Aggregate outcomes per side, signal, price bucket |
| `pqi.stats.recompute_from_history(20)` | Cross-check stats.json against ground truth |
| Per-agent lean-hit computation (manual) | Agent leaderboard excluding legacy |
| EV-gate replay at WR=0.70 | Simulate what the gate would have done on historical BUYs |
| Price-bucket histograms on correct vs wrong BUYs | Identify whether mispricing correlates with cheapness |
| `pass_reason` tokenization on misses | Identify which gate blocks the most good reads |

Read for context: `scoring.py`, `models.py` (gate stack only — not modified),
`stats.py`, `analytics.py`.

---

## 3. Headline Findings

### 3.1 BUY YES vs BUY NO asymmetry is real and large

| Side | Correct | Wrong | WR | Avg price |
|------|:-------:|:-----:|:--:|:---------:|
| BUY YES | 26 | 14 | **65.0%** | — |
| BUY NO  | 9 | 1 | **90.0%** | — |

Non-legacy, resolved only. NO-side calls hit at ~1.4× the YES rate. With n=10
NO-side BUYs this is still noisy but consistent with the council's structural
priors — when the council disagrees *enough to flip against an implied
favorite*, it's disproportionately right. YES-side BUYs include many
market-aligned "agree with favorite" reads where the favorite upsets.

**Observation, not action:** if future sessions introduce side-specific
agent weights or gate thresholds, BUY NO should carry the hypothesis-priori
boost, not BUY YES.

### 3.2 ROI-banded signal types invert the intuition

| Signal | Correct | Wrong | WR | Avg price |
|--------|:-------:|:-----:|:--:|:---------:|
| HIGH VALUE (ROI ≥ 50%) | 8 | 6 | **57%** | $0.61 |
| MODERATE VALUE (25-50%) | 10 | 5 | **67%** | $0.75 |
| LOW MARGIN (10-25%) | 5 | 0 | **100%** (n=5) | $0.83 |

The *cheap* BUYs (biggest upside, HIGH VALUE) have the *lowest* hit rate.
The *expensive* BUYs (LOW MARGIN, ≤ 25% upside) are near-perfect but the
sample is too small to trust. MODERATE VALUE is the stable middle.

This flips the session-18 signal-type hierarchy if taken at face value —
cheap sides being less reliable is expected at the tails (55-65¢ sides are
genuine coin-flips) but 57% WR at an average ROI of 65%+ still produces
positive expected value, so the band isn't *wrong*, it's just less certain
than its name implies.

### 3.3 The EV gate's current effect on historical data is **net negative**

Replaying the current WR=0.70 gate against the 50 resolved BUYs:

| Outcome | Baseline | Post-EV-gate | Δ |
|---------|:--------:|:------------:|:--:|
| Correct BUYs | 35 | 13 | **-22** |
| Wrong BUYs | 15 | 10 | -5 |
| WR | 70.0% | **56.5%** | -13.5pp |

The gate would have blocked **22 correct BUYs to avoid 5 wrong ones** — a 4.4×
opportunity cost. Post-gate WR drops because correct BUYs historically cluster
at *higher* prices (correct MODERATE/LOW MARGIN BUYs sit at $0.75-$0.85,
above the WR ceiling of $0.70).

Two interpretations:

- **Optimistic:** the pattern "correct BUYs at high prices" is small-sample
  luck from betting favorites who converted. With more data, high-priced BUYs
  will regress to a hit rate near their price (mathematically fair) and the
  gate will look prescient.
- **Pessimistic:** the council has genuine skill at picking expensive-but-right
  sides, and the EV gate is throwing that skill away because it treats "our
  historical WR" as a universal ceiling regardless of the specific bet's
  price.

Both can be true simultaneously. The EV gate is mathematically correct in
aggregate (never bet when price ≥ long-run hit rate) but is applied
uniformly across heterogeneous bets. Roadmap A.5 (EV gate tuning) should
decide between: (a) keep uniform; (b) per-signal-type gate; (c) abandon in
favor of Kelly sizing alone.

**Do not change the gate this session.** The 50-sample WR is still noisy
and the gate is tested mathematics. Monitor for another ~20-30 resolved
BUYs and re-evaluate whether the opportunity cost pattern persists.

### 3.4 Bear rewrite (session 20) validated

Bear lean-hit on all 217 non-legacy resolved verdicts: **50.2% (109/217)**,
mean confidence 0.55. Pre-session-20 audit showed 38% lean-hit with 81% NO-bias
and confidence floor violations. The rewrite:

- Brought the hit rate to coin-flip (the structural target for a dedicated
  underdog advocate).
- Kept the agent's role intact — Bear is *supposed* to be contrarian brake,
  not predictor. 50% is healthy.
- Mean confidence 0.55 is appropriately modest for an advocate role.

Bear is no longer a degenerate voice dragging the council's weighted score.
Session 20's guards worked.

### 3.5 Rules Lawyer leads the leaderboard

Per-agent lean-hit (non-legacy, resolved):

| Agent | Hit | N | Hit rate | Mean conf |
|-------|:---:|:-:|:--------:|:---------:|
| The Rules Lawyer | 151 | 220 | **68.6%** | 0.53 |
| The Sentiment Analyst | 152 | 223 | 68.2% | 0.56 |
| The Base-Rate Statistician | 147 | 217 | 67.7% | 0.51 |
| The Domain Specialist | 148 | 220 | 67.3% | 0.57 |
| The Contrarian | 151 | 227 | 66.5% | 0.56 |
| The Bull | 137 | 226 | 60.6% | **0.66** |
| The Bear | 109 | 217 | 50.2% | 0.55 |

Rules Lawyer edges out Sentiment by 0.4pp, both above the council average.
Historical `RULES_LAWYER_WEIGHT=2` is supported by this data but session 24
reconciled the doc to `=1`. The top-to-bottom spread (Rules Lawyer 68.6%
→ Bear 50.2%) is ~18pp and large enough that differential weighting would
measurably move the aggregate — but this is a roadmap A-theme decision,
not a session 24 change.

The Bull's high mean confidence (0.66) paired with the second-lowest hit
rate (60.6%) is the mirror of the Bear's old problem — the Bull overstates
confidence on favorites who then upset. Worth flagging for Theme A review,
not worth tuning now.

### 3.6 Missed opportunities are dominated by the confidence gate

Of 46 missed opportunities:

| Blocking gate | Count | Share |
|---------------|:-----:|:-----:|
| `CONFIDENCE` (67% per-side floor) | 45 | **98%** |
| `CONTRARIAN_CONFIDENCE` (80% floor) | 1 | 2% |
| `PROBABILITY`, `ROI`, `EV`, `PRICE` gates | 0 | 0% |

The 67¢ confidence floor is doing essentially all the "no, we're not sure
enough" work. Neither the EV gate nor the ROI gate is the primary blocker
of good BUYs in history. This matters for roadmap prioritization:

- Loosening the confidence floor (e.g. to 65%) would recover more misses
  than any other gate adjustment.
- Tightening the confidence floor would sacrifice relatively little in
  missed-bad-BUYs (because CONF_GATE already catches those too).
- The EV gate and ROI gate are currently "redundant safety nets" in the
  sense that almost nothing reaches them to be blocked.

### 3.7 HIGH-confidence PASSes rarely miss

Only **2 of ~150** HIGH-confidence PASSes became missed opportunities
(<2%). When the council is confident a side isn't worth it, the council is
right. This is the most-validated part of the pipeline and the single
strongest reason not to loosen the confidence floor reflexively based on
finding 3.6 — the floor is expensive in misses but cheap in false BUYs.

---

## 4. Cross-references against the Tier-1 retrospective

The 2026-04-10 report noted 4 near-miss confidence-gate misses (2× 1pp gap,
1× 3pp gap). Five days later the pattern has scaled up: CONF_GATE is now
the dominant blocker at 45/46 missed opportunities. The Tier-1 "loosen
ROI" fix (0.15 → 0.10) was the right call but addressed a gate that was
never the bottleneck.

The Tier-1 findings about Bear bias (38% hit, 81% NO-lean) have been
resolved by session 20. The new Bear leaderboard position (50.2%) is the
post-fix baseline.

---

## 5. Recommendations (no session-24 code changes)

Flagged for roadmap Theme A — Modeling & Calibration:

1. **A.1 — Data asymmetry of confidence.** The BUY NO 90% WR vs BUY YES
   65% WR gap (finding 3.1) is strong evidence that per-side treatment is
   already implicit in the data. This roadmap item should explicitly model
   the high-confidence side as dominant (Bayesian shrinkage / log-odds
   weighting) rather than letting symmetric scoring paper over it.
2. **A.4 — Bear monitoring.** Bear hit rate at 50.2% stable across 217
   verdicts. Next review gate: if it drops below 45% over the next 50
   resolved verdicts, re-open the persona. Otherwise leave alone.
3. **A.5 — EV gate tuning.** Current gate on historical data: blocks 4.4×
   more correct than wrong BUYs (finding 3.3). Three concrete options to
   evaluate (do not pick yet):
   - Keep as-is; wait for 20-30 more resolved BUYs to see if the "correct
     at high prices" pattern regresses.
   - Switch to per-signal-type gate (HIGH VALUE uses the 57% band's WR,
     MODERATE uses 67%, LOW MARGIN is ungated) — more targeted but more
     overfitting risk.
   - Abandon hard gate; rely on Kelly=0 at -EV prices for advisory UI only.
4. **A.7 (NEW) — Confidence floor calibration.** CONF_GATE blocks 98% of
   missed opportunities (finding 3.6). HIGH-conf PASS false-miss rate is
   <2% (finding 3.7). A precision/recall study on the 67¢ threshold would
   quantify the tradeoff — may justify a drop to 0.63-0.65. **Do not
   change the value** until that study is run. (Subsumes A.3 "Tier 2 gate
   evaluation" — same knob, more data now available.)
5. **A.8 (NEW) — Bull confidence overstatement.** Bull mean confidence
   0.66 (highest in council) paired with second-lowest hit rate (60.6%) is
   the inverse of the old Bear problem. Audit the Bull persona for
   confidence-inflation patterns in the same way session 20 audited Bear.

Reconciliation items already done in session 24:

- Doc ↔ config defaults reconciled across CLAUDE.md / README.md /
  .env.example / docs/api-reference.md.
- Dead helpers purged from `scoring.py` (session 24 changelog).
- `stats.json` documented as load-bearing in `docs/known-issues.md`.

---

## 6. What this audit deliberately did NOT do

- No change to `scoring.py` gate logic, `models.py` recommendation math,
  or any agent persona. Per user instruction: "the models before the EV
  gate and kelly were at a good point so i feel that shouldnt be touched
  just yet since it might affect the verdicts."
- No change to `.env` defaults. Doc reconciliation only.
- No retroactive re-classification of resolved outcomes. Used
  `classify_outcome()` output as-is.
- No head-to-head per-market inspection of the 22 "correct BUYs that the
  EV gate would have blocked" — the aggregate pattern is sufficient to
  defer the decision. Per-market inspection belongs in the A.5 workup.

---

## 7. Open questions for next session

- Does the "correct BUYs cluster at high prices" pattern hold for the
  next 20-30 resolved BUYs, or does it regress? (Determines A.5 outcome.)
- Is the 65% BUY YES WR vs 90% BUY NO WR gap stable or an artifact of
  n=10 NO-side sample? Watch as NO-side count grows. (Feeds A.1.)
- Does Bull over-confidence show up as a measurable drag on the
  confidence-weighted score, or is it absorbed by the other 6 agents?
  (A.7 scoping.)
