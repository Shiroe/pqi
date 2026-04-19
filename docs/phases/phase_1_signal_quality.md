# Phase 1: Signal Quality Fix (Option B Autonomy)

**Status:** not started (can parallelize with Phase 0)
**Estimated effort:** 2–4 weeks
**Owner:** TBD
**Last updated:** 2026-04-19 (post-Phase-0 retrospective; acceptance
criteria unified)

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

Phase 0 retrospective §5.3 identified two pathologies the original
Phase 1 scope did not address: cross-agent cluster collapse (5-agent
correlated cluster at r 0.80–0.92) and Bear calibration drift (Bear
Brier 0.2810, above 0.25 chance baseline). The SHARED_PREAMBLE rewrite
must address all of mirror collapse, cluster collapse, and Bear
calibration simultaneously — a rewrite that fixes one while regressing
another is not acceptance.

All four criteria below must pass **simultaneously** on the
post-rewrite reliability diagram (`python -m scripts.reliability_diagram`,
UTC-dated output at `~/.pqi/reports/reliability_YYYYMMDD.md`) compared
against the 2026-04-18 baseline.

### 1. Cluster-pair r ≥ 0.85 fraction below 0.50 (cross-agent diversity)

Current: 9 of 21 pairs (43%) at r ≥ 0.85 per retro §5.3, forming a
5-agent cluster (rules_lawyer / statistician / sentiment / specialist
/ contrarian). Bull weakly attached (~0.6); Bear structurally
independent.

Target: fraction drops below 0.50 while Bear's independence is
preserved (Bear pairwise r with all others stays below ~0.50). This is
"three distinct voices become four or more distinct voices," not "all
seven collapse together."

Measured by: `scripts/agent_pairwise_diversity.py` (Task 1.3) over all
resolved rows' agent probabilities, reported as 21-pair Pearson matrix.

### 2. Per-agent `|drift|` > 0.10 on at least 2 agents (YES/NO autonomy)

Original Phase 1 criterion preserved. The Option B mirror-collapse
test: agents should be reasoning about YES and NO as parallel problems
with coupled-but-not-identical evidence, not algebraic mirrors.

Current: aggregate `|drift|` 0.01–0.06 (signed ±0.02) across all seven
agents, per session-32 shadow analytics.

Target: at least two agents show aggregate `|drift|` > 0.10 on the
post-rewrite sample.

Measured by: existing shadow analytics in `pqi history stats` (Option
B steps 1–4 shipped session 26); per-agent drift and Brier computed
from `probability_yes` vs `probability_no` fields on `AgentVerdict`.

### 3. Bear Brier toward 0.22 with independence preserved (calibration + independence)

Bear is the most load-bearing single agent on this phase's success.
Bear's Brier 0.2810 is above the 0.25 chance baseline; Bear's pairwise
r with all other agents is in [−0.17, +0.04], i.e. structurally
uncorrelated. The failure mode on Bear is collapse into the cluster.

Target: Bear Brier falls toward the cluster floor (0.22 range) AND
Bear's pairwise r with any other agent stays below ~0.50. Either
condition alone is insufficient.

Diagnostic from retro §5.2: Bear predicts mean 0.356, realizes 0.484 on
its concentration band [0.09, 0.50) — +0.128 gap on n=351. Bear is
systematically under-predicting YES. The rewrite approach (§B below)
forces probability output *after* the advocacy reasoning, not *as an
expression of it*.

Measured by: Bear's row in the per-agent Brier table
(`scripts/reliability_diagram.py`) AND Bear's column in the pairwise
matrix (`scripts/agent_pairwise_diversity.py`).

### 4. Council Brier holds or improves from 0.2176 (aggregate regression guard)

The aggregate regression guard. If the rewrite destabilises the cluster
without preserving signal, or if it breaks the aggregator's current
weighting, council Brier will rise. This criterion exists to prevent
"looks better on diversity, worse on prediction" outcomes.

Target: post-rewrite council Brier ≤ 0.2176. Strict inequality is
aspirational; equality acceptance is acceptable because the other
three criteria are the primary gains.

Measured by: council row in per-agent Brier table
(`scripts/reliability_diagram.py`).

### Option B step 5 unblock criterion

All four criteria pass → Option B backtest (Task 1.4) is worth running.
Until all four pass, running the backtest on a still-collapsed signal
is pre-registered to fail per `docs/option_b_migration_plan.md §7`.

---

## Tasks

### Task 1.1 — SHARED_PREAMBLE autonomy rewrite

**What:** Rewrite the YES/NO-autonomy section of SHARED_PREAMBLE in
`pqi/agents/personas.py` to produce genuinely independent reasoning.

**Why:** Current framing allows agents to shortcut to
`P(NO) = 1 − P(YES)` because the instruction to "estimate both" is
not structurally reinforced with evidence-quality asymmetry. The
model naturally collapses two independent problems into one.

**Proposed approach — three complementary mechanisms:**

The rewrite must address mirror collapse, cluster collapse, and Bear
calibration simultaneously. Three mechanisms, each targeting a
different failure mode:

### Mechanism 1 — Asymmetric evidence mechanics (targets mirror collapse, criterion 2)

Applied to **SHARED_PREAMBLE**, all agents.

- **Worked examples** showing asymmetric evidence depth:
  > "Example: when TeamA has extensive recent match data but TeamB
  > is a new lineup, you might report `p_yes=0.70, p_no=0.25` —
  > reflecting high confidence in TeamA's capability estimate and
  > low confidence (with 0.05 mass reserved for unknowns) on TeamB."
- **Forced evidence-quality pre-step:** before emitting probabilities,
  require the agent to summarize "TeamA evidence depth" and "TeamB
  evidence depth" as separate paragraphs. Reasoning chain must pass
  through them; paragraphs don't need to appear in final JSON.
- **Explicit anti-mirror warning:**
  > "If your `p_yes` and `p_no` sum to exactly 1.0, recheck — you
  > have likely skipped the independent reasoning step. They should
  > sum to less than 1.0 when either side's evidence is thin
  > (reserve mass for ignorance)."

### Mechanism 2 — Persona-specific evidence anchoring (targets cluster collapse, criteria 1, 4)

Applied to **per-persona body** in `AGENT_PERSONAS`, not SHARED_PREAMBLE.

The cluster collapse finding (retro §5.3) — 5 agents correlated at
r 0.80–0.92 — is a signal that agents are reasoning over the same
narrative substrate and arriving at similar conclusions. Asymmetric
evidence mechanics alone don't fix this because the evidence shape
stays shared across agents; only the internal weighting differs.

Fix: force each persona's reasoning chain through persona-specific
ground. Each persona must cite a persona-specific anchor in reasoning
before emitting probability. The anchors are deliberately chosen so
different personas weight different parts of the briefing:

- **Statistician:** must cite a base-rate or historical frequency
  number from the briefing (e.g. "TeamA won 62% of their last 13
  Bo3s against top-10 opposition").
- **Specialist:** must cite a game-mechanics / meta / matchup detail
  (e.g. "TeamA's Ban-phase prioritisation leaves them vulnerable on
  Mirage, which TeamB has permaban preference for").
- **Sentiment:** must cite a specific crowd-psychology or social
  signal (e.g. "Twitter lean toward TeamA is at 73% with 4× normal
  volume — crowd-heavy on favourite, potential reversal signal").
- **Contrarian:** must cite a specific disruption vector (e.g.
  "Roster instability — IGL still new after 6-week stand-in period").
- **Rules Lawyer:** already anchored via
  `resolution_criteria` injection (`council.py`); preserve exactly.
- **Bull / Bear:** see Mechanism 3.

> **Owner note (2026-04-19):** the specific per-persona anchors above
> are *first-draft suggestions, not settled.* The **concept** —
> forcing each persona's reasoning through persona-specific ground so
> outputs decorrelate at the source — is the load-bearing mechanism.
> The **exact anchor content** (what counts as "a base-rate," whether
> Specialist's anchor should be matchup-specific vs meta-level, etc.)
> will be iterated during Task 1.1 prompt drafting in a later session.
> Treat this list as the direction, not the text.

The anchor citation appears in the agent's reasoning field and is
**not** optional. When the briefing lacks evidence for a persona's
anchor type, the persona must explicitly state that and reflect it in
a low-confidence output — not substitute a different anchor type.

### Mechanism 3 — Bear probability-after-advocacy (targets Bear calibration, criterion 3)

Applied specifically to **Bear persona body**, optionally applied to
Bull symmetrically.

Bear's Brier 0.2810 comes from systematic under-prediction of YES: Bear
predicts mean 0.356, realizes 0.484 on its concentration band
(retro §5.2). This is a prompt-structure bug — Bear is producing
probabilities *as an expression of* its advocacy role, not *after*
reasoning about truth.

Fix: force explicit sequence in the Bear prompt body:

1. "First, construct the strongest case for the underdog side. Do
   this fully before touching probability."
2. "Then, step outside the advocacy role and ask: given the case
   you just built, plus the rest of the briefing, what is the
   actual probability of each outcome?"
3. "Your probability output must reflect your honest estimate after
   considering the full evidence, not a continuation of the
   advocacy."

Apply the symmetric change to Bull for consistency (Bull's Brier 0.2370
is less severe than Bear's but shows similar overconfidence-on-favourites
pattern per retro §5.2 — −0.219 gap on [0.80, 0.95) band).

---

**Acceptance criteria — updated to match unified goals:**

- [ ] New SHARED_PREAMBLE committed with Mechanism 1 changes
- [ ] Per-persona bodies updated with Mechanism 2 anchors (Statistician,
      Specialist, Sentiment, Contrarian — Rules Lawyer preserved)
- [ ] Bear and Bull persona bodies updated with Mechanism 3 sequence
- [ ] Manual review of 5 test briefings shows agents cite
      persona-specific anchors
- [ ] No regression in single-run council output parseability (JSON
      still validates)
- [ ] Post-rewrite reliability diagram runs clean (task 1.2)

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
- After all personas have landed the preamble + anchor changes,
  **reset `~/.pqi/agent_weights.json`** (delete the file; next
  `history sync` or `history stats` regenerates from post-rewrite
  data). Weights from pre-rewrite period partly absorb Bear
  miscalibration; preserving them anchors the aggregator to a
  pathology the rewrite is attempting to remove. See external review
  v2 §3.4 / §5.8.

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

**What:** For the post-rollout verdict set, compute pairwise Pearson
correlation of `P(YES)` across agents. Compare to pre-rollout
baseline.

**Why:** `|drift|` is the individual-agent metric; pairwise Pearson
is the cross-agent diversity metric. **Both need to move.** An
agent can have high per-row drift and still be perfectly correlated
with every other agent — that's not diversity, that's coordinated
noise.

**Implementation:**

- Requires Phase 0 Task 0.1 (DuckDB) complete.
- For each pair of agents, compute **Pearson** correlation rho over
  all shared resolved rows' probabilities. Pearson (not Spearman) is
  retro §5.3's reported metric — keep apples-to-apples with the
  baseline.
- Report the 21-pair matrix (7-choose-2) before and after rollout.
- Also report: fraction of pairs at r ≥ 0.85, Bear's row of
  pairwise correlations, and the identified cluster (pairs
  within the cluster with their r values).

**Acceptance criteria:**

- [ ] Before/after 21-pair Pearson matrix published
- [ ] Cluster-pair fraction at r ≥ 0.85 drops below 0.50 (criterion 1)
- [ ] Bear's pairwise r with all other agents stays below ~0.50
      (criterion 3 independence guard)

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

- **Success path:** All four unified acceptance criteria pass
  simultaneously (cluster-pair r ≥ 0.85 fraction below 0.50;
  per-agent `|drift|` > 0.10 on ≥2 agents; Bear Brier toward 0.22
  with independence preserved; council Brier ≤ 0.2176), Option B
  backtest verdict issued (pass or fail). Retrospective written.
- **Partial path:** Drift/diversity gates not met despite iteration
  — the retrospective documents "prompt work alone cannot break
  mirror-collapse," and Phase 1 is done. The next logical
  experiment is model-mixing (a separate, explicitly triggered
  workstream — do **not** ship multi-model as part of Phase 1).

---

## Validation: "are we done?"

1. Can I look at aggregate drift per agent after the rollout?
   (Yes → 1.2 passed)
2. Did cluster-pair fraction at r ≥ 0.85 drop below 0.50 and Bear's
   pairwise r with others stay below ~0.50? (Measured → 1.3 passed)
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

**Last work session:** session 39 (2026-04-19) — doc-only patch
unifying acceptance criteria per external review v2. Phase 1 execution
(Task 1.1 prompt drafting) still pending owner review of this doc.

**Done:**

- Option B steps 1–4 shipped in session 26 (schema, parser, all 7
  personas emitting `probability_no_score`, shadow analytics in
  `history stats`).
- Session-32 reassessment diagnosed mirror collapse and flagged
  autonomy framing as the root cause.
- Session 39 (doc-only): Goals unified to four-criterion acceptance;
  Task 1.1 Proposed approach extended to three mechanisms (asymmetric
  evidence, persona-specific anchoring, Bear probability-after-
  advocacy); Task 1.2 gains agent_weights.json reset; Task 1.3
  realigned to Pearson. Source: `phase_1_acceptance_criteria_patch.md`
  / external review v2 §3.1.1.

**In-progress:** *nothing*

**Next action:** Begin Task 1.1 — draft the new SHARED_PREAMBLE section
(Mechanism 1) + the persona anchor additions (Mechanism 2) + the Bear
probability-after-advocacy sequence (Mechanism 3). **Do not commit the
rewrite until the owner reviews.** Check current total preamble +
persona prompt token count; target per-agent prompt below ~1500
tokens for prompt-cache efficiency per session 27.

Before running task 1.2 (live rollout), reset
`~/.pqi/agent_weights.json` so post-rewrite weights don't inherit
pre-rewrite Bear miscalibration.

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
