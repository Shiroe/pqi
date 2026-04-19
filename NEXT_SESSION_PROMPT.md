# Next Session Prompt

> This file is the hand-off note between sessions. It is authoritative for
> "what to do next." `CLAUDE.md` keeps a short pointer; the full instructions
> live here. Update this file at the end of every session.

**Last rewritten:** session 41 (2026-04-19) — Phase 1 rollout STAGE 1
is LIVE. `PERSONA_V2_PREAMBLE=true` set in `.env` after refining the
V2 preamble content (three-case worked-example spectrum + softened
anti-mirror check). `scripts/reliability_diagram.py` gained a
`--flag <label>` filter for v1/v2 side-by-side comparison. 16 V2 runs
queued (112 verdicts tagged `v2:preamble`); 0 resolved yet. Zero
aggregator changes; v1 AGENT_PERSONAS still bit-identical to
pre-rewrite when flag flipped back off.

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

## PRIORITY — observe stage 1, don't escalate yet

Default entry point: `docs/phases/phase_1_signal_quality.md` §Session state.

Phase 1 rollout stage 1 is **live**. `PERSONA_V2_PREAMBLE=true` in
`.env` since session 41. V2 preamble content landed this session:
three-case worked-example spectrum (mild / severe / symmetric
asymmetry) replaced the single-gap anchor so agents learn ignorance
mass scales with briefing depth rather than anchoring on one magic
number; anti-mirror check softened to "sum-near-100 PAIRED WITH
matched confidences" — signal, not trigger. These content changes
pollute the tagged population if changed again, so: leave V2 wording
alone during stage 1 measurement.

### What's in flight

- **16 V2 runs queued**, **0 resolved yet** — `pqi query "SELECT
  flag_label, COUNT(*) FROM verdicts GROUP BY flag_label"` returns
  `v1=2878, v2:preamble=112` as of session 41 end.
- **Refreshed baseline:** `~/.pqi/reports/reliability_20260419.md`
  (409 rows, 100% v1 since 0 V2 has resolved). Council Brier 0.2156,
  Bear Brier 0.2820, cluster r≥0.85 fraction 9/21 = 43%, Bear
  independence still intact.
- **Filtered reports tool:** `python -m pqi.scripts.reliability_diagram
  --flag v1` and `--flag v2:preamble` produce independent diagrams
  with flag-suffixed filenames (`reliability_YYYYMMDD_v1.md`,
  `reliability_YYYYMMDD_v2-preamble.md`). Unfiltered behaviour
  unchanged.

### Next action (gated on data, not calendar)

Check resolution count first: `pqi query "SELECT v.flag_label,
COUNT(DISTINCT v.run_id) AS runs, SUM(CASE WHEN h.resolution IS NOT
NULL THEN 1 ELSE 0 END) AS resolved FROM verdicts v JOIN history h
USING (run_id) WHERE v.flag_label LIKE 'v2:%' GROUP BY v.flag_label"`.

- **If resolved V2 runs < ~20:** do nothing. Keep queuing fresh V2
  analyses on short-horizon markets (esports typically resolves in
  days). Do NOT flip the flag off, do NOT escalate to stage 2.
- **If resolved V2 runs ≥ ~20:** produce the two-filter pair
  (`--flag v1` vs `--flag v2:preamble`) and evaluate the four-criterion
  unified acceptance below. Only after stage 1 has a verdict should
  the preamble flag flip off and stage 2 begin.

### Rollout order (each stage is an INDEPENDENT A/B, not cumulative)

Between stages, flip the prior stage's flag OFF unless explicitly
combining. Each verdict carries `prompt_flags`, so populations stay
cleanly splittable via `flag_label` across the full run.

1. **Preamble alone** — `PERSONA_V2_PREAMBLE=true`. **← current
   stage, live since session 41.** Measures mirror collapse delta
   (criterion 2) + stability of cluster (criterion 1).
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
- V2 preamble is ~5.8kB in raw literal form; prompt caching (session
  27) absorbs the cost — first call warms the cache, rest of the
  5-min TTL window bills at ~10%. Per-run added cost with all flags
  on: ~$0.002.
- **Do not edit the aggregator** (`scoring.py` / `stats.py`) during
  rollout. Phase 0 §7.2 freeze stands. Aggregator changes require
  explicit sign-off per `CTO_Agent.md` §4.
- **Do not re-edit the V2 preamble text during stage 1 measurement.**
  Content changes invalidate the tagged v2:preamble population
  comparison.

### Four unified acceptance criteria (Option B step 5 unblock)

All four must pass **simultaneously** on `--flag v2:preamble` vs
`--flag v1` (same invocation, two filters) once the V2 population is
large enough:

- **(1)** Cluster-pair fraction at r ≥ 0.85 drops below 0.50 (v1
  reference: 9/21 = 43%; Bull and Bear already sit outside — the
  lever destabilises the 5-agent cluster).
- **(2)** Per-agent `|drift|` > 0.10 on at least 2 agents (YES/NO
  autonomy; original Phase 1 criterion preserved).
- **(3)** Bear Brier falls toward the 0.22 cluster floor **without**
  losing Bear's independence (Bear pairwise r with others stays
  below ~0.50).
- **(4)** Council Brier holds or improves from 0.2176 (aggregate
  regression guard).

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

- Default to **observing stage 1**, not initiating it — the flag is
  already on. Run the resolution check query first to decide whether
  measurement is actionable this session. If resolved V2 runs < ~20,
  the answer is "wait, keep queuing," not "act."
- When measurement IS actionable, produce the two-filter pair:
  `python -m pqi.scripts.reliability_diagram --flag v1` and
  `--flag v2:preamble`. They write to
  `~/.pqi/reports/reliability_YYYYMMDD_v1.md` and
  `..._v2-preamble.md` — compare them directly against the four
  criteria, not against the stale 2026-04-18 file.
- **Do not re-edit `_SHARED_PREAMBLE_V2` during stage 1 measurement.**
  Content changes invalidate the tagged `v2:preamble` population and
  waste the 16 queued runs. If wording needs another pass, flip the
  flag off, edit, backfill or discard the tagged population, then flip
  back on.
- Analytics splits use `pqi query "SELECT flag_label, ... FROM verdicts
  GROUP BY flag_label"` for quick population checks.
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
