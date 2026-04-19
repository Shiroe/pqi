# Next Session Prompt

> This file is the hand-off note between sessions. It is authoritative for
> "what to do next." `CLAUDE.md` keeps a short pointer; the full instructions
> live here. Update this file at the end of every session.

**Last rewritten:** session 39 (2026-04-19) — Phase 1 doc acceptance
criteria unified; Phase 2 split 2a/2b/2c; CTO_Agent.md +capacity/recent-window;
CLAUDE.md Tier A/B reconciliation; haiku model name fix (all doc-only, no code).

---

## Kickoff checklist (paste into the next session)

```
Read CLAUDE.md in full, paying close attention to ## Current State.
Then read NEXT_SESSION_PROMPT.md for the detailed hand-off.
Also read CTO_Agent.md — session-33 rewrite made it a capable
reviewer persona (contract-as-law, quantitative validation, strategic
edge, architectural judgment, plus the original code-review stance).
```

## Reference map (read on demand)

- `docs/scaling_assessment.md` — v2.0 RFC triage, prediction vs execution
  edge framework, why DuckDB over Postgres, explicit rejections
- `docs/phases/README.md` — phase index, dependency graph, status table
- `docs/phases/phase_0_edge_validation.md` — **done** (see retrospective)
- `docs/phases/phase_1_signal_quality.md` — **active** (Option B autonomy fix — highest leverage per P0 verdict)
- `docs/phases/phase_2a_tier_a_auto_buy.md` — Tier A auto-BUY; blocked on Phase 1 unified acceptance
- `docs/phases/phase_2b_mfe_analysis.md` — MFE analysis with chronological train/test (parallel-able with P1)
- `docs/phases/phase_2c_mfe_listener.md` — MFE listener prototype; gated on P2b verdict = "chase"
- `docs/phases/phase_3_deployment_portability.md` — deferred (P0 inconclusive, not positive)
- `docs/phases/phase_4_scaling_triggers.md` — reactive only
- `docs/retrospectives/phase_0_edge_2026_04.md` — **Phase 0 closed** — executive summary §0, per-band §1–§3, CLV §4, reliability §5, verdict §6, next actions §7
- `docs/analytics_queries.md` — starter queries for `pqi query` (Q4 uses winning_side semantics)
- `docs/api-reference.md` — API endpoints, params, response formats (incl. PandaScore)
- `docs/known-issues.md` — API quirks, edge cases, env setup, persisted state files
- `docs/session-log.md` — per-session changelog (current through session 39)
- `docs/roadmap.md` — themed catalog (A/B/C); phase section indexes phases
- `docs/option_b_migration_plan.md` — full Option B rollout plan (steps 1–4 shipped; step 5 blocked on P1 signal quality)
- `docs/models-reference.md` — detailed Pydantic class signatures (extracted from CLAUDE.md)
- `docs/scoring-reference.md` — full gate stack + PerceivedTrueWinRate + Kelly details (extracted from CLAUDE.md)

---

## PRIORITY — Phase 1: Signal Quality Fix (Option B autonomy)

Default entry point: `docs/phases/phase_1_signal_quality.md`.

Phase 0 closed session 38 with verdict **inconclusive at current n**, but the
retrospective (§5.3) identified cluster-level mirror collapse as the single
most important finding: 5 of 7 agents are correlated at r 0.80–0.92
internally (rules_lawyer / statistician / sentiment / specialist /
contrarian); Bull weakly attached (~0.6); Bear structurally independent but
has Brier 0.2810 — the only agent above the 0.25 chance baseline. Council
Brier 0.2176 is 3pp better than baseline (real but small), and it is being
absorbed by (a) the correlated cluster limiting effective diversity, (b)
Bear's miscalibration dragging aggregate accuracy, (c) mean pre-event CLV
−0.0167 on entry timing.

### Task 1.1 — SHARED_PREAMBLE autonomy rewrite (three mechanisms)

Details in the phase doc (`docs/phases/phase_1_signal_quality.md` §Task 1.1).
Session 39 unified the approach to three complementary mechanisms:

- **Mechanism 1 — asymmetric evidence on SHARED_PREAMBLE** (targets
  mirror collapse). Worked examples of asymmetric evidence depth +
  forced evidence-quality pre-step + explicit anti-mirror warning.
- **Mechanism 2 — persona-specific evidence anchoring per-persona
  body** (targets cluster collapse). Each of Statistician, Specialist,
  Sentiment, Contrarian must cite a persona-specific anchor
  (base-rate / game-mechanics / crowd-signal / disruption-vector
  respectively) before emitting probability. Rules Lawyer already
  anchored via `resolution_criteria` injection. **Owner note: specific
  anchors in the phase doc are first-draft suggestions, not settled —
  concept is load-bearing, exact content iterates during prompt
  drafting.**
- **Mechanism 3 — Bear probability-after-advocacy** (targets Bear
  calibration, +0.128 gap on [0.09, 0.50)). Force explicit sequence in
  Bear prompt body: construct advocacy → step outside advocacy role →
  emit honest probability. Optionally symmetric on Bull.

Guard rails:

- Preserve Bull's partial independence and Bear's structural
  independence. The failure mode is "rewrite flattens all 7 agents
  toward one voice" — that would push council Brier upward
  (regression).
- Keep the preamble under ~1500 tokens for prompt-cache efficiency
  (session 27).
- **Reset `~/.pqi/agent_weights.json` before Task 1.2 live rollout**
  (per patched Task 1.2 workflow) — pre-rewrite weights partly absorb
  Bear miscalibration.

### Four unified acceptance criteria (Option B step 5 unblock)

All four must pass **simultaneously** on the post-rewrite reliability
diagram vs the 2026-04-18 baseline (`~/.pqi/reports/reliability_20260418.md`):

- **(1)** Cluster-pair fraction at r ≥ 0.85 drops below 0.50 (current
  9/21 = 43%; close, but Bull and Bear already sit outside the
  cluster — the lever is destabilising the 5-agent cluster).
- **(2)** Per-agent `|drift|` > 0.10 on at least 2 agents (YES/NO
  autonomy; original Phase 1 criterion preserved).
- **(3)** Bear Brier falls toward the 0.22 cluster floor **without**
  losing Bear's independence (Bear pairwise r with others stays
  below ~0.50).
- **(4)** Council Brier holds or improves from 0.2176 (aggregate
  regression guard).

Re-run `python -m scripts.reliability_diagram` after every prompt change.
The report is UTC-dated — iteration history is preserved automatically.

### Parallelisable alongside P1

Phase 2b MFE analysis (`docs/phases/phase_2b_mfe_analysis.md`). This is the
third CTO-§5 edge metric (max favourable excursion) — a DuckDB + CLOB-history
query analogous to Task 0.2, now with chronological train/test promoted to
task requirement (external review v2 §5.3). Produces the full edge
triangulation (prediction / entry / exit) without infrastructure investment.
Phase 2a (Tier A auto-BUY) stays blocked on Phase 1 unified acceptance;
Phase 2c (MFE listener) stays gated on Phase 2b verdict = "chase".

---

## Phase 0 verdict — anchor reference

From `docs/retrospectives/phase_0_edge_2026_04.md` §6:

- **Prediction edge:** council Brier 0.2176 beats 0.25 chance baseline by
  ~3pp (n=385). Real but small; aggregate is fragile because 5 of 7 agents
  mirror each other.
- **Execution entry quality:** mean pre-event CLV −0.0167 on n=44 (67.7%
  coverage, 21 rows permanently unavailable via `no_tokens`). Mildly
  unfavourable; CI straddles zero. Valorant worst (−0.032 n=17), LoL neutral
  (−0.001 n=14).
- **Gate calibration:** no band clears ≥1 CI width flag in either
  direction. [0.60, 0.70) n=23 and [0.70, 0.80) n=15 well-calibrated on
  point estimate (break-even EV on entry). [0.80, 0.95) best-EV band
  (realized 0.938, +9.9pp vs mean price). [0.50, 0.60) point-estimate
  under-rejects (Δ −0.186 on n=8) but gate threshold 0.497 < band floor
  0.500 means forward-looking protection is maximal.
- **Realized April PnL:** −0.575/share on 65 BUYs (≈ −0.9¢/share).
  Break-even before fees, slightly negative after.
- **Explicitly no knob changes:** `BAND_SHRINKAGE_K=5`,
  `MIN_CONTRARIAN_PROBABILITY=0.60`, ROI bands, Kelly multiplier,
  `MIN_WIN_RATE_SAMPLES` all stand. Re-tuning on the same 62 rows that
  produced `band_stats` is dressed-up overfitting per `CTO_Agent.md` §2.

---

## Monitoring tasks (low priority; observe, don't code)

These stay open through Phase 1. Pick up if the signal appears organically.

- **Observe session-32 contrarian 0.60 gate in the wild.** A CONTRARIAN BUY
  fires when council prob ≥ 0.60 AND side confidence ≥ 0.80 AND expected
  ROI ≥ 0.20. Early resolutions should skew toward [0.60, 0.65). If first
  5–10 trend negative PnL, revisit the confidence/ROI guards, not the prob
  floor.
- **Observe session-32 `pass_reason` format.** Next banded-EV-gate downgrade
  should print `"Big Drawdown Risk: … ≥ band L-H WR Y% [n=K] (-EV)"`.
  Scalar `PerceivedTrueWinRate` path only fires on absent perceived_stats
  or exotic prices outside [0.09, 0.95].
- **Observe session-30 retry.** Next 529/429 should log `anthropic call
  failed (attempt N/6): … — retrying in Xs`. If the exception propagates
  without that log, extend the `retryable` tuple in `_chat_anthropic`.
- **Verify session-28 PandaScore + disambiguation end-to-end.** With
  `PANDASCORE_API_KEY` set, run `LOG_LEVEL=INFO python -m pqi.main -c
  counter-strike --live -v 0`. Confirm cyan "PandaScore: …" line, DEBUG
  briefing with EVENT TEMPORAL DISAMBIGUATION + STRUCTURED MATCH DATA
  blocks, no past-tense paraphrases on SCHEDULED matches. Resolver miss →
  `structured_match=None` is the contract.
- **Reliability diagram monthly.** Re-run
  `python -m scripts.reliability_diagram` at the end of each calendar month
  (independent of phase work) to track cluster drift / Bear Brier / council
  Brier over time.

---

## Deferred / theme-level (picked up via phase docs)

Theme A items (A.1 Option B, A.4 Bear monitoring, A.7 confidence floor, A.8
Bull audit, A.10 dynamic-weight loop monitoring) all feed Phase 1 work.
Theme B items split across 2a/2b/2c: B.3–B.5 (auth, read/trade client,
Kelly buy) ship with Phase 2a; B.1 TUI is adjacent to the 2c listener;
B.6 match watcher + sell-side stack activates only after 2c verdicts
"automate." Theme C items are isolated QoL — pick any between phases if
they bite you.

---

## Instructions

- Default to Phase 1 Task 1.1 (SHARED_PREAMBLE autonomy rewrite) unless the
  user redirects. Start with a draft of the new preamble section — **do not
  commit the rewrite until the user reviews it.** Prompt-level work is
  cheap; prompt-level regressions are not.
- Measure delta with `python -m scripts.reliability_diagram` after every
  change. Compare against the 2026-04-18 baseline in
  `~/.pqi/reports/reliability_20260418.md`.
- When reaching for history analytics, use `pqi query "<SQL>"` on the
  `history` / `verdicts` / `clv_by_row` views before writing a fresh Python
  script. See `docs/analytics_queries.md` for patterns. For CLV: `python -m
  scripts.backfill_clv` refreshes the sidecar; the DuckDB view auto-registers
  when it exists.
- Update `CLAUDE.md ## Current State` AND this file at end of session. Log
  meaningful decisions in `docs/session-log.md` and the phase doc's
  "Session state" section.
- Aggregator math (scoring formula, gate stack, Kelly formula) is stable
  since sessions 31–32. Phase 0 explicitly produced no knob changes. Any
  aggregator change still requires explicit sign-off per `CTO_Agent.md` §4.
- Option B rollout order is strict per `docs/option_b_migration_plan.md`.
  Step 5 (backtest) stays blocked until Phase 1's three measurable checks
  pass. Never wire `probability_no` into scoring until step 5 passes
  pre-registered criteria on held-out data.
- PandaScore is non-fatal: any resolver failure must leave
  `structured_match=None` and let the pipeline continue. Don't add
  exceptions or hard gates — degraded behavior is the contract.
- STRICT: no AI / Claude / "assisted" attribution in code, comments,
  commits, or docs (see `CLAUDE.md` "Attribution rule").
- Commit per step, locally. No push without explicit ask.
- **Keep `CLAUDE.md` under 40k characters.** If a new rule genuinely belongs
  there (not a session detail, not a doc), trim an equivalent amount
  elsewhere — otherwise route the content to the appropriate `docs/` file
  or this file.
