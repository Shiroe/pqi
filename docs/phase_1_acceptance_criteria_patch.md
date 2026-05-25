# Phase 1 Acceptance Criteria Patch

**Purpose:** Drop-in replacement for stale sections of
`docs/phases/phase_1_signal_quality.md`. Reconciles the phase doc's
original criteria (pre-Phase-0) with Phase 0 retrospective §5.3's
cluster-collapse finding.

**Apply by:** replacing two sections of
`docs/phases/phase_1_signal_quality.md` as described below, updating
the `Last updated` date, and adding a session entry to
`docs/session-log.md`.

**Source of change:** external review v2 §3.1.1. See
`pqi_external_review_2026_04_v2.md` for the reasoning.

---

## Why this patch exists

Phase 1 doc was written session 32 targeting **per-agent YES/NO drift**
(Option B mirror collapse). Phase 0 retrospective closed session 38
with a different and more severe finding: **cross-agent cluster
collapse + Bear miscalibration** (retro §5.3). These are different
measurements. The SHARED_PREAMBLE rewrite Phase 1 proposes is the
right intervention for both, but the current doc's scoreboard measures
only one. A rewrite could hit the original drift criteria without
improving cluster collapse and still be a Phase 1 failure by
retrospective standards — and vice versa.

Per `CTO_Agent.md §2` ("Mirror collapse is a signal-quality bug, not a
sample-count bug") and §7(d) ("Phase it"), the right fix is to unify
acceptance criteria so the rewrite targets both pathologies from the
start.

This patch does two things:

1. Replaces `## Goals (measurable)` with unified four-criterion
   acceptance (§A below).
2. Extends `## Task 1.1 — SHARED_PREAMBLE autonomy rewrite` with
   **persona-specific evidence anchoring** content that targets
   cluster collapse directly (the original Task 1.1 content targets
   only mirror collapse) (§B below).

Tasks 1.2 (persona-by-persona rollout), 1.3 (pairwise diversity
measurement), and 1.4 (Option B backtest) stay structurally as-is;
measurement outputs are redirected to the unified criteria. Minor edits
to 1.2 and 1.3 in §C.

---

## §A — Replace `## Goals (measurable)`

Find in `docs/phases/phase_1_signal_quality.md`:

```markdown
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
```

Replace with:

```markdown
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
```

---

## §B — Extend `## Task 1.1 — SHARED_PREAMBLE autonomy rewrite`

The original Task 1.1 `Proposed approach` addresses mirror collapse
(criterion 2) via asymmetric-evidence worked examples + forced
evidence-quality pre-step + anti-mirror warning. These mechanisms do
not directly address cluster collapse (criteria 1, 3) or Bear's
prompt-structure miscalibration (criterion 3).

Find in `docs/phases/phase_1_signal_quality.md` under Task 1.1,
`**Proposed approach:**`:

```markdown
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
```

Replace with:

```markdown
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
```

---

## §C — Minor edits to Tasks 1.2 and 1.3

### Task 1.2 additions

Under `Per-persona workflow:`, add the following bullet after the
existing workflow:

```markdown
- After all personas have landed the preamble + anchor changes,
  **reset `~/.pqi/agent_weights.json`** (delete the file; next
  `history sync` or `history stats` regenerates from post-rewrite
  data). Weights from pre-rewrite period partly absorb Bear
  miscalibration; preserving them anchors the aggregator to a
  pathology the rewrite is attempting to remove. See external review
  v2 §3.4 / §5.8.
```

### Task 1.3 additions

Under `Implementation:`, replace the existing Spearman-only
specification with:

```markdown
- Requires Phase 0 Task 0.1 (DuckDB) complete.
- For each pair of agents, compute **Pearson** correlation rho over
  all shared resolved rows' probabilities. Pearson (not Spearman) is
  retro §5.3's reported metric — keep apples-to-apples with the
  baseline.
- Report the 21-pair matrix (7-choose-2) before and after rollout.
- Also report: fraction of pairs at r ≥ 0.85, Bear's row of
  pairwise correlations, and the identified cluster (pairs
  within the cluster with their r values).
```

Update `Acceptance criteria` section:

```markdown
**Acceptance criteria:**

- [ ] Before/after 21-pair Pearson matrix published
- [ ] Cluster-pair fraction at r ≥ 0.85 drops below 0.50 (criterion 1)
- [ ] Bear's pairwise r with all other agents stays below ~0.50
      (criterion 3 independence guard)
```

### Task 1.4 — no structural change

Task 1.4 (conditional Option B backtest) structure stays as-is. Only
the unblock condition changes: "Tasks 1.1–1.3 hit their gates" now
refers to the four unified criteria in §A, not the original two.

---

## §D — Update `Last updated` and `Session state`

At the top of the phase doc:

```markdown
**Last updated:** 2026-04-19 (post-Phase-0 retrospective; acceptance
criteria unified)
```

At the bottom of the phase doc, update `Session state → Next action`:

```markdown
**Next action:** Begin Task 1.1 — draft the new SHARED_PREAMBLE section
(Mechanism 1) + the persona anchor additions (Mechanism 2) + the Bear
probability-after-advocacy sequence (Mechanism 3). **Do not commit the
rewrite until the owner reviews.** Check current total preamble +
persona prompt token count; target per-agent prompt below ~1500
tokens for prompt-cache efficiency per session 27.

Before running task 1.2 (live rollout), reset
`~/.pqi/agent_weights.json` so post-rewrite weights don't inherit
pre-rewrite Bear miscalibration.
```

---

## §E — Session log entry

Add to `docs/session-log.md`:

```markdown
## Session 39 — Phase 1 acceptance criteria unified

- Patched `docs/phases/phase_1_signal_quality.md`: replaced stale
  Goals section (targeting only per-agent YES/NO drift) with unified
  four-criterion acceptance reflecting Phase 0 retrospective §5.3's
  cluster-collapse + Bear calibration findings.
- Extended Task 1.1 Proposed approach from one mechanism
  (asymmetric evidence) to three (adds persona-specific evidence
  anchoring for cluster collapse, Bear probability-after-advocacy
  for calibration).
- Task 1.2 gains explicit agent_weights.json reset step before
  rollout.
- Task 1.3 specification aligned to retrospective's Pearson metric
  (was Spearman) for apples-to-apples with baseline.
- No code changes, no aggregator changes, no knob changes. Gate
  stability preserved per `CTO_Agent.md §6`.
- Source of change: external review v2 §3.1.1.
```

---

## §F — What this patch does NOT change

Explicitly listing the things this patch leaves alone, so the CTO
agent doesn't over-extend scope:

- **No code changes.** Patch is entirely documentation.
- **No aggregator / scoring changes.** `scoring.py` and `stats.py`
  untouched. Gate math frozen per Phase 0 §7.2.
- **No gate knob retunes.** `BAND_SHRINKAGE_K=5`,
  `MIN_CONTRARIAN_PROBABILITY=0.60`, ROI bands, Kelly multiplier,
  `MIN_WIN_RATE_SAMPLES` all stand.
- **No new personas, no persona deletions.** The 7-agent council
  structure is preserved. Mechanism 2 changes per-persona bodies;
  Mechanism 3 changes Bear (and symmetrically Bull). `bull`, `bear`,
  `statistician`, `sentiment`, `specialist`, `contrarian`,
  `rules_lawyer` all remain.
- **No changes to Option B step 5 pre-registered criteria.**
  `docs/option_b_migration_plan.md §7` backtest criteria remain as-is.
  Only the *unblock condition* for running the backtest changes (was
  "original Phase 1 Goals pass," now "unified Phase 1 Goals pass").
- **No changes to Phase 2, Phase 3, or Phase 4.** Those live in
  separate phase docs and reflect external review v2 §5.3 / §7 only
  as subsequent work, not this patch.
- **No changes to `CLAUDE.md` "What NOT To Do" list or Project
  Mission.** The Advisor-vs-Auto-trader reconciliation
  (external review v2 §6.1) is an owner-decision item flagged
  separately and is out of scope for this patch.

---

## §G — Reference

- External review v2: `pqi_external_review_2026_04_v2.md` §3.1.1
  (rationale for unification), §3.2 (Bear-specific reasoning),
  §3.4 / §5.8 (agent_weights.json reset rationale)
- Phase 0 retrospective:
  `docs/retrospectives/phase_0_edge_2026_04.md` §5.1–§5.3 (cluster,
  Bear Brier, calibration gaps), §7.1 (Phase 1 priority rationale)
- Existing phase doc: `docs/phases/phase_1_signal_quality.md`
- Option B authoritative spec:
  `docs/option_b_migration_plan.md §7` (pre-registered backtest
  criteria — unchanged)
- Governance: `CTO_Agent.md` §2 (quantitative guardrails), §6
  (reviewer checklist), §7 (RFC triage)
