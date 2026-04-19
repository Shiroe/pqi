# Next Session Prompt

> This file is the hand-off note between sessions. It is authoritative for
> "what to do next." `CLAUDE.md` keeps a short pointer; the full instructions
> live here. Update this file at the end of every session.

**Last rewritten:** session 40 (2026-04-19) — Phase 1 Task 1.1 scaffold
(7 `PERSONA_V2_*` flags, dual SHARED_PREAMBLE, anchor + advocacy
snippets, all default OFF) + analytics tagging (`AgentVerdict.prompt_flags`
sparse storage, `flag_label` derived column in DuckDB verdicts view,
one-shot history backfill — 437 rows / 2871 verdicts tagged `{}`).
Baseline AGENT_PERSONAS bit-identical to pre-rewrite; zero aggregator
changes.

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
- `docs/session-log.md` — per-session changelog (current through session 40)
- `docs/roadmap.md` — themed catalog (A/B/C); phase section indexes phases
- `docs/option_b_migration_plan.md` — full Option B rollout plan (steps 1–4 shipped; step 5 blocked on P1 signal quality)
- `docs/models-reference.md` — detailed Pydantic class signatures (extracted from CLAUDE.md)
- `docs/scoring-reference.md` — full gate stack + PerceivedTrueWinRate + Kelly details (extracted from CLAUDE.md)

---

## PRIORITY — Phase 1 rollout (independent flag stages)

Default entry point: `docs/phases/phase_1_signal_quality.md` §Session state.

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

### Session 40 shipped the scaffold — rollout is the next action

- **7 `PERSONA_V2_*` flags** in `config.py` / `.env.example`, all
  default `false`: `PREAMBLE`, `ANCHOR_STATISTICIAN`,
  `ANCHOR_SENTIMENT`, `ANCHOR_SPECIALIST`, `ANCHOR_CONTRARIAN`,
  `BULL_ADVOCACY`, `BEAR_ADVOCACY`.
- **Dual preamble** in `pqi/agents/personas.py` (`_SHARED_PREAMBLE_V1`
  verbatim prior preamble; `_SHARED_PREAMBLE_V2` asymmetric-evidence
  rewrite ~3.7kB). Four inline anchor snippets + two advocacy
  snippets. `_assemble(agent_key)` slots them in front of `Analysis
  Lens:` at import time based on flag state. Rules Lawyer is the only
  persona that *never* gets an anchor or advocacy snippet — only the
  preamble flag ever applies to it.
- **Analytics tagging** on every verdict: `AgentVerdict.prompt_flags`
  (sparse dict, empty ⇔ v1), `phase1_flag_label()` module helper, and
  a derived `flag_label` column on the DuckDB `verdicts` view
  (`"v1"` / `"v2:<sorted csv>"`). `scripts/backfill_prompt_flags.py`
  tagged all 437 existing rows / 2871 verdicts with `{}` — so any
  post-rollout `GROUP BY flag_label` split is clean on day one.

Baseline equivalence: with every flag off, `AGENT_PERSONAS` is
bit-identical to pre-rewrite. The aggregator is untouched.

### Rollout order (each stage is an INDEPENDENT A/B, not cumulative)

Between stages, flip the prior stage's flag OFF unless explicitly
combining. Re-run `python -m scripts.reliability_diagram` after each
stage and compare against the 2026-04-18 baseline
(`~/.pqi/reports/reliability_20260418.md`). The analytics tagging
means every verdict during a stage is stamped with the exact flag set
used, so populations can be compared via `pqi query "SELECT flag_label,
... FROM verdicts WHERE ... GROUP BY flag_label"`.

1. **Preamble alone** — `PERSONA_V2_PREAMBLE=true`. Measures mirror
   collapse delta (criterion 2).
2. **Each anchor alone** — Statistician → Sentiment → Specialist →
   Contrarian. Expect marginal cluster-diversity delta (criterion 1).
3. **Bear alone** (Bull unchanged) — `PERSONA_V2_BEAR_ADVOCACY=true`.
   Highest-leverage single flag per retro §5.3. **Reset
   `~/.pqi/agent_weights.json` before this stage** per Task 1.2
   workflow — pre-rewrite weights absorb Bear miscalibration.
4. **Bull alone** (Bear reverted) — `PERSONA_V2_BULL_ADVOCACY=true`.
   Symmetric test; prevents the Bull case from remaining unvalidated.
5. **Both** if stages 3 and 4 each show no regression independently.

Guard rails:

- Preserve Bull's partial independence and Bear's structural
  independence. The failure mode is "rewrite flattens all 7 agents
  toward one voice" — that would push council Brier upward
  (regression).
- Preamble is ~3.7kB in raw literal form (V2); prompt caching
  (session 27) absorbs the cost — first call warms the cache, rest
  of the 5-min TTL window bills at ~10%. Per-run added cost with all
  flags on: ~$0.002.
- **Do not edit the aggregator** (`scoring.py` / `stats.py`) during
  rollout. Phase 0 §7.2 freeze stands. Aggregator changes require
  explicit sign-off per `CTO_Agent.md` §4.

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

- Default to Phase 1 rollout stage 1 (`PERSONA_V2_PREAMBLE=true`)
  unless the user redirects. Session 40 landed the scaffold — the
  drafted V2 preamble is already in `pqi/agents/personas.py` as
  `_SHARED_PREAMBLE_V2`. Before flipping the flag, **re-read the V2
  preamble and confirm with the user** that the wording is right for
  live measurement. Prompt-level regressions are cheap to revert
  (flip the flag back) but pollute the tagged population.
- Measure delta with `python -m scripts.reliability_diagram` after every
  stage. Compare against the 2026-04-18 baseline in
  `~/.pqi/reports/reliability_20260418.md`. Analytics splits use
  `pqi query "SELECT flag_label, ... FROM verdicts GROUP BY flag_label"`.
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
