# Next Session Prompt

> This file is the hand-off note between sessions. It is authoritative for
> "what to do next." `CLAUDE.md` keeps a short pointer; the full instructions
> live here. Update this file at the end of every session.

**Last rewritten:** session 44 (2026-04-21) — first stage-1 monitor
pass on `v2:preamble` (read-only, no code change). 38 V2 rows
resolved (well over the ≥20 trigger). Two-filter pair written to
`~/.pqi/reports/reliability_20260420_v1.md` and
`reliability_20260420_v2-preamble.md`. **1 of 4 unified acceptance
criteria pass** at this n. Headline finding is the *opposite*
of intent: cluster-pair r≥0.85 fraction jumped 43% → 71% under v2,
with Bull collapsing into the cluster (4 of its 6 pair-correlations
now ≥ 0.85, vs 0 under v1). Bear remains the only structurally
independent agent. Council Brier improved (0.2147 → 0.1919) but
plausibly because the tighter cluster is more unanimous on a
small/easier 38-row slice — not better calibration. n=38 is small
(every v2 band except [0.09, 0.50) is `thin`) so the trend is
directional, not decisive. **Decision deferred:** ~93 markets pending
resolution in next 24h — recheck then. STAGE 1 stays LIVE
(`PERSONA_V2_PREAMBLE=true`); preamble text untouched this session.

**Known gotcha:** `pqi.analytics_sql.get_connection()` currently
fails when DuckDB's schema sample lands on rows where `prompt_flags`
contains only the `preamble` key — DuckDB infers STRUCT instead of
MAP and `map_keys()` on line 26 of the verdicts view rejects it. So
`pqi query` against `verdicts` is broken right now. Workaround used
this session: `scripts/reliability_diagram.py` reads the JSONL
directly and bypasses DuckDB. Quick fix for next session: cast or
build the map explicitly in the view (e.g. `map_from_entries(…)` or
force-construct from a struct), or set `sample_size=-1` *and* an
explicit MAP column type on the read.

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
- `docs/session-log.md` — per-session changelog (current through session 43)
- `docs/roadmap.md` — themed catalog (A/B/C); phase section indexes phases
- `docs/option_b_migration_plan.md` — full Option B rollout plan (steps 1–4 shipped; step 5 blocked on P1 signal quality)
- `docs/models-reference.md` — detailed Pydantic class signatures (extracted from CLAUDE.md)
- `docs/scoring-reference.md` — full gate stack + PerceivedTrueWinRate + Kelly details (extracted from CLAUDE.md)

---

## PRIORITY — re-run the monitor pass after the ~93 pending resolutions land

Default entry point: `docs/phases/phase_1_signal_quality.md` §Session state.

Phase 1 rollout stage 1 remains **live**. `PERSONA_V2_PREAMBLE=true`.
V2 preamble text untouched since session 41 — keep it that way during
stage 1 measurement.

### Where stage 1 stands (session 44 first monitor pass)

n = 38 v2:preamble resolved vs 414 v1 resolved. Reports at
`~/.pqi/reports/reliability_20260420_v1.md` and
`reliability_20260420_v2-preamble.md`.

| Criterion | v1 (n=414) | v2:preamble (n=38) | Pass? |
|---|---|---|---|
| Cluster r≥0.85 fraction < 0.50 | 9/21 (43%) | 15/21 (71%) | ❌ worse |
| \|drift\| > 0.10 on ≥2 agents | — | max \|drift\|=0.033 | ❌ |
| Bear Brier toward 0.22, indep held | 0.2822 | 0.2851 (Bear still indep) | ❌ |
| Council Brier holds/improves | 0.2147 | 0.1919 | ✅ |

Per-agent Brier deltas (v2 − v1, all negative = improved): rules_lawyer
−0.033, bull −0.024, bear +0.003, statistician −0.016, sentiment −0.026,
specialist −0.022, contrarian −0.014, council −0.023. None exceed the
±0.10 drift threshold.

The diagnostic to watch: under v2, Bull's pair-correlations jumped
from ~0.6 (v1) to ~0.86–0.90 with the 5-agent cluster. The preamble
appears to be increasing shared anchoring, not breaking it. Bear
remains structurally independent (r ≈ −0.17 to 0.02 with all others).

### Why we're waiting, not flipping the flag

n=38 is small. Every v2 band except [0.09, 0.50) is `thin`; the 71%
cluster fraction is a strong directional signal but the per-band
realized rates are noisy. User reports ~93 markets pending resolution
in the next 24h — that should roughly triple the v2 sample to ~100+
resolved rows. Tighter Wilson CIs after that batch lands will either
confirm the regression (→ revert + redesign the preamble) or relax
the reading back toward 43% (→ keep observing / run stage 2).

### Next action this session (after the 24h soak)

1. Re-run both filters against the latest history:
   ```
   python scripts/reliability_diagram.py --flag v1
   python scripts/reliability_diagram.py --flag v2:preamble
   ```
2. Re-evaluate against the four-criterion unified acceptance.
3. Decision tree:
   - **If cluster fraction stays ≥ 60% on n ≥ 100:** flip
     `PERSONA_V2_PREAMBLE=false`, write a brief retro on what about
     the V2 preamble framing concentrated rather than diversified the
     cluster, and redesign before re-flipping.
   - **If cluster fraction drops below 50% AND ≥ 2 agents show
     |drift| > 0.10 AND Bear Brier moves toward 0.22 AND council
     Brier holds:** stage 1 passes. Flip the flag off and proceed to
     stage 2 (anchors).
   - **If neither — mixed/in-between:** keep observing. Don't flip
     either way until one of the above resolves.
4. **Fix the analytics_sql binder bug** if you want `pqi query` back
   on the `verdicts` view. The script-based pipeline is unblocked
   without it.

### Constraints (hard)

- Do **NOT** edit `_SHARED_PREAMBLE_V2` between now and the verdict.
  Content drift invalidates the tagged `v2:preamble` population.
- Do **NOT** retune aggregator knobs based on the v2 slice. The
  Phase 0 freeze stands.
- Do **NOT** advance to stage 2 without a stage-1 verdict.

### Queuing more V2 runs (session 42 tooling)

Multi-run `analyze` shipped this session. To add fresh V2 observations:

```bash
# Bounded-parallel batch over a list of short-horizon esports events
python3 -m pqi.main -c esports -s 24 -j 3
# ... at prompt: 1,2,5,7,9   (or 1-5   or   all)
```

Default concurrency is 1 (sequential). `-j 3` is the practical
ceiling on Anthropic Tier 2. Add `--summary-only` to skip per-event
panels when you just want to seed a lot of V2 verdicts into the log.
See README.md "Batch mode" section.

### Resolution count check (use script, `pqi query` is broken)

`pqi query` on the `verdicts` view raises a DuckDB binder error
right now (analytics_sql.py:99 — STRUCT vs MAP inference on
`prompt_flags`). Until that's fixed, use:

```
python scripts/reliability_diagram.py --no-write 2>&1 | head -2
```

The first stderr line is `resolved rows by flag label: v1=…,
v2:preamble=…`. Session 44 saw `v1=414, v2:preamble=38`. Target
for the next monitor pass is `v2:preamble ≥ 100`.

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
