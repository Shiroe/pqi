# Phase 1: Signal Quality Fix (Option B Autonomy)

**Status:** not started (can parallelize with Phase 0)
**Estimated effort:** 2–4 weeks
**Owner:** TBD
**Last updated:** 2026-04-18

---

## Why this phase exists

The Option B shadow layer (session 26) was supposed to unlock
per-side probability autonomy — agents emit independent `P(YES)` and
`P(NO)` estimates instead of the algebraic mirror
`P(NO) = 1 − P(YES)`. By the session-32 reassessment, the signal has
**mirror-collapsed**:

- Per-agent `|drift|` is 0.01–0.06 (signed ±0.02)
- YES/NO Brier scores differ by ≤0.01 across all 7 agents
- The pre-registered ΔBrier ≤ -0.005 CI95<0 acceptance criterion is
  **mathematically unreachable** on the current output

This is a **signal-quality bug, not a sample-count bug.** More data
will not help. The SHARED_PREAMBLE autonomy framing needs sharpening:
agents are reasoning as if YES and NO are algebraic mirrors, not as
if they're parallel problems with coupled-but-not-identical evidence.

**Until Phase 1 is done, running Option B step 5 (the backtest) is a
waste** — you will reject a degenerate signal and learn nothing
about B-math. See `docs/session-log.md` §Session 32 for the full
analysis.

---

## Entry criteria

- [ ] Phase 0 complete **or** explicit decision to parallel-track
- [ ] Baseline reliability diagram exists (Phase 0 Task 0.3) — or
      accept working without a comparison baseline

**Note on parallelism:** Phase 1 and Phase 0 can genuinely run in
parallel. They touch different files (Phase 0 = analytics SQL +
scripts, Phase 1 = `pqi/agents/personas.py`). Phase 0's reliability
diagram gives a much sharper before/after picture of Phase 1's
impact but is not a hard blocker.

---

## Goals (measurable)

1. **Aggregate `|drift|` > 0.10 on at least 2 agents** —
   threshold for "Option B backtest is worth running."
2. **Pairwise Spearman of agent `P(YES)` drops by ≥0.05** after
   rollout — evidence that agents are actually diverging, not just
   adding noise.
3. **Option B backtest runs and produces a clean verdict** — either
   passes pre-registered criteria on held-out data (→ step 6
   integration planning) or fails cleanly and we park Option B as
   descriptive-only.

---

## Tasks

### Task 1.1 — SHARED_PREAMBLE autonomy rewrite

**What:** Rewrite the YES/NO-autonomy section of SHARED_PREAMBLE in
`pqi/agents/personas.py` to produce genuinely independent reasoning.

**Why:** Current framing allows agents to shortcut to
`P(NO) = 1 − P(YES)` because the instruction to "estimate both" is
not structurally reinforced with evidence-quality asymmetry. The
model naturally collapses two independent problems into one.

**Proposed approach:**

- Add **worked examples** showing asymmetric evidence depth:
  > "Example: when TeamA has extensive recent match data but TeamB
  > is a new lineup, you might report `p_yes=0.70, p_no=0.25` —
  > reflecting high confidence in TeamA's capability estimate and
  > low confidence (with 0.05 mass reserved for unknowns) on TeamB."
- Add a **forced evidence-quality pre-step**: before emitting
  probabilities, require the agent to summarize "TeamA evidence
  depth" and "TeamB evidence depth" as separate paragraphs. These
  paragraphs don't need to appear in the final JSON — but the
  reasoning chain has to pass through them.
- Add an **explicit anti-mirror warning**:
  > "If your `p_yes` and `p_no` sum to exactly 1.0, recheck — you
  > have likely skipped the independent reasoning step. They should
  > sum to less than 1.0 when either side's evidence is thin
  > (reserve mass for ignorance)."

**Acceptance criteria:**

- [ ] New SHARED_PREAMBLE committed
- [ ] Manual review of 5 test briefings shows agents emit
      asymmetric probabilities at least sometimes (not every run —
      on truly-symmetric evidence, mirrors are correct)
- [ ] No regression in single-run council output parseability
      (JSON still validates)

**Files touched:**

- `pqi/agents/personas.py` (SHARED_PREAMBLE)

**Risks:**

- Heavier preamble = more input tokens per agent. Rough estimate:
  +200–400 tokens × 7 agents × 1 call per analyze = ~2k tokens.
  At Haiku 4.5's $0.80/MTok input, that's ~$0.002 per run
  additional. Acceptable given prompt caching (session 27) —
  cached reads bill at ~10%.
- Over-long preamble can degrade response quality. Iterate if
  Brier or parseability regresses.

---

### Task 1.2 — Persona-by-persona rollout

**What:** Apply Task 1.1's new preamble, measure aggregate `|drift|`
on live runs, iterate per-persona.

**Why:** Different personas may respond differently to the same
preamble change. Measure and tune per-persona to avoid over-fitting
the preamble to one voice.

**Rollout order (per session-32 recommendation):**

`rules_lawyer → statistician → sentiment → specialist → contrarian
→ bull → bear`

Order rationale:

- **Rules Lawyer** and **Statistician** are most structurally narrow
  — they *should* diverge **least**; if they move significantly,
  the preamble is trying too hard.
- **Bull / Bear** are most prone to mirror-collapse because their
  bias is directly about YES vs NO; they come last after the
  preamble is validated on lower-stakes agents.

**Per-persona workflow:**

- Run 10–20 live analyses with the new preamble active for that
  persona only (temporarily wrap other personas with the old
  preamble via a config flag; remove flag after rollout).
- Measure that persona's `|drift|` on those runs.
- If drift moves meaningfully, move to next persona.
- If drift doesn't move, iterate on the preamble for that persona
  or mark it as resistant and continue.

**Acceptance criteria:**

- [ ] All 7 personas tested with the new preamble
- [ ] Per-persona drift measured and logged to a retrospective
- [ ] At least 2 personas show aggregate `|drift|` > 0.10

**Files touched:**

- `pqi/agents/personas.py` (persona-specific additions if any)
- `pqi/config.py` (temporary rollout flag, removed after phase
  completes)
- New: `docs/retrospectives/phase_1_drift_rollout_YYYY_MM.md`

**Risks:**

- Live-run experiment cost: 70–140 analyses × ~$0.015 = $1–2.
  Cheap.
- **Selection effect on drift measurement.** The set of markets
  the user manually picks to `analyze` may not represent the set
  where Option B would diverge most. Mitigation: run against a
  pre-registered market list (e.g. top 10 by volume from each
  category) to avoid user-choice bias.

---

### Task 1.3 — Pairwise diversity measurement

**What:** For the post-rollout verdict set, compute pairwise Spearman
correlation of `P(YES)` across agents. Compare to pre-rollout
baseline.

**Why:** `|drift|` is the individual-agent metric; pairwise Spearman
is the cross-agent diversity metric. **Both need to move.** An
agent can have high per-row drift and still be perfectly correlated
with every other agent — that's not diversity, that's coordinated
noise.

**Implementation:**

- Requires Phase 0 Task 0.1 (DuckDB) complete.
- For each pair of agents, compute Spearman rho over all shared
  resolved rows' probabilities.
- Report the 21-pair matrix (7-choose-2) before and after rollout.

**Acceptance criteria:**

- [ ] Before/after Spearman matrix published
- [ ] Mean pairwise Spearman dropped by ≥0.05
- [ ] At least 3 pairs show Spearman < 0.6 (indicating meaningfully
      independent signal)

**Files touched:**

- New: `scripts/agent_pairwise_diversity.py`
- `docs/retrospectives/phase_1_drift_rollout_YYYY_MM.md` (updated)

---

### Task 1.4 — Option B backtest (conditional)

**What:** If Tasks 1.1–1.3 hit their gates, run the pre-registered
Option B backtest per `docs/option_b_migration_plan.md` §7.

**Why:** Option B's value proposition (per-side probability in
scoring) is only testable once per-side signal is real. Before, the
backtest just confirms mirror collapse.

**Implementation:**

- Follow `docs/option_b_migration_plan.md` §7 exactly — do not
  re-invent the criteria:
  - Chronological 70/30 train/holdout split
  - Bootstrap ΔBrier CI (1000 resamples, seed 42)
  - Pre-registered acceptance: ΔBrier ≤ -0.005, ΔNet ≥ -1, CI95<0
    on both train and holdout slices
- Script target: `scripts/b_shadow_backtest.py` (per the existing
  migration plan spec).

**Acceptance criteria:**

- [ ] Backtest runs to completion on post-rollout data
- [ ] Verdict: pass (→ step 6 integration planning) or fail (→
      park Option B as descriptive-only)
- [ ] Retrospective published in
      `docs/retrospectives/phase_1_option_b_verdict_YYYY_MM.md`

**Decision at the end:**

- **Pass:** Plan Option B step 6 (scoring integration). This
  touches `pqi/scoring.py` aggregator — requires explicit sign-off
  per `CTO_Agent.md` §6 (aggregator-stability rule).
- **Fail:** Option B parks as descriptive-only. Remove the backtest
  from priorities. Shadow fields stay in place for potential future
  use if we revisit with different models / different data.

**Files touched:**

- New: `scripts/b_shadow_backtest.py` (per migration plan)
- New: `docs/retrospectives/phase_1_option_b_verdict_YYYY_MM.md`

---

## Exit criteria

Either:

- **Success path:** Aggregate `|drift|` > 0.10 on ≥2 agents,
  pairwise Spearman dropped ≥0.05, Option B backtest verdict issued
  (pass or fail). Retrospective written.
- **Partial path:** Drift/diversity gates not met despite iteration
  — the retrospective documents "prompt work alone cannot break
  mirror-collapse," and Phase 1 is done. The next logical
  experiment is model-mixing (a separate, explicitly triggered
  workstream — do **not** ship multi-model as part of Phase 1).

---

## Validation: "are we done?"

1. Can I look at aggregate drift per agent after the rollout?
   (Yes → 1.2 passed)
2. Did pairwise Spearman drop? (Measured → 1.3 passed)
3. Is there a written verdict on Option B step 5? (Yes → 1.4
   passed or explicitly skipped)
4. Is `docs/option_b_migration_plan.md` updated to reflect the
   phase's conclusion (step 5 outcome)?

---

## Risks / decision points

- **Prompt changes can regress accuracy.** Monitor council hit rate
  (Phase 0 Task 0.3 diagram) before/after each rollout step. If
  council aggregate Brier worsens by >0.02, roll back and iterate.
- **Structural agents may not diverge even with a perfect
  preamble.** Rules Lawyer ("does resolution criteria say YES?")
  and Statistician ("what does the base rate say?") are close to
  algebraically symmetric by design. Accept low drift on these;
  target the agents where divergence is expected (Bull/Bear/
  Specialist/Contrarian).
- **Cost creep.** Heavier preamble + more live runs = higher
  monthly spend. Phase 1 one-off budget: ~$5–10 (140 experimental
  runs + ~5% ongoing preamble inflation). Flag if it goes higher.
- **"Prompt work alone can't break mirror-collapse" outcome.** If
  this is the verdict, Option B is a descriptive-only feature
  permanently. That's a real outcome; document it clearly rather
  than retrying indefinitely.

---

## Related work

### Roadmap themes

- Directly executes **Theme A → A.1 → Option B steps 4→5**.

### Authoritative specs

- `docs/option_b_migration_plan.md` — rollout plan, pre-registered
  criteria, file responsibilities. Read first.

### Session logs

- Session 26 — Option B steps 1–4 (schema, parser, personas
  emitting `probability_no_score`, shadow analytics)
- Session 32 — Mirror-collapse diagnosis, autonomy framing
  priority

### Files

- `pqi/agents/personas.py` — SHARED_PREAMBLE lives here
- `pqi/agents/council.py` — parser handles both `probability` and
  `probability_no_score`
- `pqi/models.py` — `AgentVerdict.probability_yes`,
  `probability_no`, `probability_source`

---

## Session state

**Last work session:** *not yet started*

**Done:**

- Option B steps 1–4 shipped in session 26 (schema, parser, all 7
  personas emitting `probability_no_score`, shadow analytics in
  `history stats`).
- Session-32 reassessment diagnosed mirror collapse and flagged
  autonomy framing as the root cause.

**In-progress:** *nothing*

**Next action:** Begin Task 1.1 — draft the new SHARED_PREAMBLE
autonomy section. Include worked examples and the forced
evidence-quality pre-step.

**Notes for next session:**

- Re-read `docs/option_b_migration_plan.md` §7 for the exact
  pre-registered criteria — don't reinvent them.
- Check current SHARED_PREAMBLE length before adding; keep total
  preamble under ~1500 tokens if possible for prompt-cache
  efficiency (session 27 caching pays for itself best when the
  cached prefix is stable).
- Option B already has a `probability_source` field on
  `AgentVerdict` — new-preamble runs should tag verdicts (e.g.
  `"option_b_v2"`) so Phase 0 analytics can cleanly separate
  pre/post distributions.
- If starting this phase in parallel with Phase 0, do Task 1.1
  first (pure text change, no analytics dependency); defer Task 1.3
  until DuckDB is available.
