# Option B — Per-Side Probability Autonomy Migration Plan

**Status:** PLAN-ONLY (not yet implemented).
**Author session:** 25 (drafted after A.1 β backtest rejection).
**Trigger:** A.1 Option A's probability-transform candidates all failed
pre-registered acceptance criteria on both train and holdout splits
(see `docs/retrospectives/eval_gate_audit_2026_04_15.md` §7 and the
β backtest artifact at `~/.pqi/backtests/a1_<ts>.json`). The
confidence-asymmetry signal at the council-aggregate level is too weak
to extract via transformation alone — the natural next move is to
change what agents emit upstream.

Session 25's shipped work (A.9 dynamic per-agent reliability weights)
is independent of this plan and keeps running regardless of B's status.

---

## 1. Goal

Let each agent emit `probability_yes` and `probability_no` independently
so that the YES-side and NO-side evidence bases are autonomous signals
rather than algebraically coupled. Today `AgentVerdict.probability` is
a single `P(YES)` and `P(NO)` is inferred as `1 - P(YES)`. Under
Option B, `P(NO)` is a first-class field that can differ from
`1 - P(YES)`.

### Why this matters

The SHARED_PREAMBLE in CLAUDE.md already documents "two-sidedness is
autonomy, not algebra":

- Prices: `yes_price` and the CLOB NO-token price live on independent
  orderbooks and do **not** sum to 1.0 (spread / vig).
- Confidences: `confidence_yes` and `confidence_no` are already
  autonomous; an agent can have high YES-side confidence and low
  NO-side confidence on the same verdict.
- **Probability**: currently compressed to a single field — this is a
  *modeling simplification*, not a commitment.

Option B removes that simplification.

---

## 2. What new math becomes possible

Capabilities enabled by independent `probability_yes` / `probability_no`:

| Capability | Today | Under Option B |
|---|---|---|
| **Coherence metric** `\|p_yes + p_no − 1\|` per agent | Mechanically 0 | Real signal — non-zero = agent is genuinely uncertain or sees the two sides as non-mirror |
| **Per-side Brier calibration** | Single Brier per verdict | Independent YES-Brier and NO-Brier; side-specific miscalibration detectable |
| **Agent side-bias detection** | Not detectable | `p_yes + p_no > 1` across many runs → agent double-counts evidence; `< 1` → agent leaves genuine ignorance mass |
| **Proper Dempster-Shafer belief combination** | Not possible (the β script used a simplified mass math) | Proper belief functions per hypothesis unlock actual DS combination rule |
| **Decoupled advisory signals** | "Strong YES" implies "weak NO" by construction | "Strong YES evidence" and "weak NO evidence" can be independent signals shown in the UI |
| **Per-side information asymmetry aggregation** | `(p, 1-p)` symmetric | `(p_yes, p_no)` with independent confidences — four-dimensional per-agent signal |

These are the candidates that replace the rejected A.1 Option A probability transforms.

---

## 3. Synthetic backfill — zero-API-cost migration

Per user direction: **do not re-run agents on the existing 240 rows.**
Rationale: the personas have evolved (session 20 Bear rewrite, session
25 weighting changes) — re-running would produce a different council
than the one that actually scored the markets. The "new" verdicts
would not be the historical verdicts.

Instead, one-shot migration adds synthetic mirror fields:

```python
for verdict in existing_rows:
    verdict.probability_yes = verdict.probability              # preserve
    verdict.probability_no  = 1 - verdict.probability          # synthetic
    verdict.probability_source = "synthetic_mirror"            # tag
```

**Zero API calls. Preserves all existing verdict-derived metrics
exactly**, because any metric that used `probability` directly now
reads `probability_yes` (which equals the old value).

New live runs emit both fields independently and tag
`probability_source = "independent"`.

### What stays valid after backfill

| Data / metric | Validity | Reason |
|---|---|---|
| Resolution truth (`winning_side`, `final_yes_price`) | ✅ Unchanged | Outcomes don't depend on verdict schema. |
| Market prices at decision time | ✅ Unchanged | Snapshotted in history. |
| Briefing documents | ✅ Unchanged | Persisted since session 17. |
| `recommendation`, `signal_type`, `pass_reason` | ✅ Unchanged | Computed from probabilities; synthetic mirror reproduces originals exactly. |
| `correct_buy` / `wrong_buy` / `missed_opportunity` counts | ✅ Unchanged | Uses `recommendation` + `winning_side`. |
| EV gate `true_win_rate`, Kelly | ✅ Unchanged | Outcome-based. |
| Dynamic per-agent reliability weights (A.9) | ✅ Unchanged | Uses `lean_rate` (direction from `probability > 0.5`); synthetic mirror preserves direction. |
| Per-agent `hit_rate`, `weighted_hit_rate`, `mean_brier` (YES-side) | ✅ Unchanged | Uses `probability` = `probability_yes` post-migration. |

### What becomes synthetic-only (must be filtered)

| Metric | Synthetic behaviour | Filter rule |
|---|---|---|
| Per-side NO Brier | Mechanically `(1 - p_yes - actual)²` — redundant with YES Brier | Skip rows where `probability_source == "synthetic_mirror"` |
| Coherence `\|p_yes + p_no − 1\|` | Always exactly 0 | Skip synthetic |
| Side-bias detection (agent drift in `p_yes + p_no`) | Always exactly 1.0 | Skip synthetic |
| Per-agent ignorance mass | Always 0 | Skip synthetic |
| Proper Dempster-Shafer combination on agent masses | Degenerate | Skip synthetic |

All Option-B-specific analytics functions MUST filter on
`probability_source == "independent"` before aggregating. Aggregates
that predate this field (hit-rate, lean-rate, etc.) stay schema-agnostic.

### Minimum live-sample target before enabling B-math scoring

Option A's β backtest used n=228 (train 159 / holdout 69) and needed
bootstrap CIs that excluded zero to promote a candidate. For B-math we
should hold to the same bar. That means:

- Phase 1 (shadow-only): `~30-50` independent rows, enough to sanity-check
  the new fields are well-formed and coherent.
- Phase 2 (backtest): `~100-150` independent rows before any B-specific
  signal is allowed to enter the gate stack.

At current per-agent throughput (one `analyze` run per market × agents
whose verdicts we persist) this is 3-6 months of normal use. Budget
for it explicitly — don't wire B-math into scoring prematurely.

---

## 4. Files touched

| File | Change | Risk | Notes |
|---|---|---|---|
| `pqi/agents/personas.py` | All 7 output formats: add `probability_no` (integer 0–100, parsed to 0.0–1.0). SHARED_PREAMBLE gets an AUTONOMY block: "P(TeamA) and P(TeamB) do not need to sum to 1.0 — leave ignorance mass when genuinely uncertain on one side." | **MEDIUM** — prompt engineering; per-agent spot-check needed. | Ollama regex parser compatibility must hold. |
| `pqi/agents/council.py` | Parser extracts both probability fields. Fallback: if only legacy `probability` found → `probability_yes = p, probability_no = 1 - p, probability_source = "synthetic_mirror"`. Live parses default to `probability_source = "independent"`. | LOW | Mirrors the two-sided-confidence fallback already in place. |
| `pqi/models.py` | `AgentVerdict` adds `probability_yes: float`, `probability_no: float`, `probability_source: Literal["independent", "synthetic_mirror"]`. Legacy `probability` kept as `@computed_field` returning `probability_yes` — zero call-site churn. | **HIGH — gate on explicit sign-off per stable-model rule.** | `from_council_verdicts` swaps `(v.probability, 1 - v.probability)` aggregation for `(v.probability_yes, v.probability_no)`. Recommendation logic and gate stack stay byte-identical. |
| `pqi/scoring.py` | No change — already passes weights in; aggregation math lives in `models.py`. | LOW | Verify the thread still works after the model swap. |
| `pqi/history.py` | On load: if a verdict lacks `probability_no`, migrate in-place (synthetic mirror + source flag). Atomic write-back once. Schema-version field bumped on the entry level. | LOW | Idempotent. Existing atomic-write machinery reused. |
| `pqi/analytics.py` | New per-side Brier and coherence functions. All B-specific aggregations filter on `probability_source == "independent"`. Existing schema-agnostic functions unchanged. | LOW | |
| `scripts/b_backfill.py` (new) | One-shot CLI: walks `history.jsonl`, adds `probability_no = 1 - probability` + `probability_source = "synthetic_mirror"`. Idempotent. Zero API calls. | LOW | Can be re-run safely. |
| `scripts/b_shadow_backtest.py` (new) | Mirror of `a1_backtest.py` but reading the new fields. Filters on `probability_source == "independent"`. Reports per-side Brier, coherence, side-bias for each agent. Pre-registered acceptance criteria identical in spirit to A.1 β. | LOW | Only runs after Phase 2 sample accumulates. |
| `docs/known-issues.md` | Document `probability_source` flag; list which metrics filter vs not; note that synthetic backfill means `p_yes + p_no = 1` exactly on all rows predating the migration. | LOW | |
| `CLAUDE.md` | Remove the "Probability — stored today as a single field" simplification note; update the two-sidedness block to reflect autonomy at the probability layer too; document `probability_source` flag. | LOW | Doc-only. |
| `README.md` | Update the `AgentVerdict` schema box. No env var changes. | LOW | |
| `.env.example` | No new keys required for the migration itself. Future B-math scoring knobs (e.g. coherence-gate thresholds) added only after Phase 2. | — | |

---

## 5. Open design questions (need sign-off before coding)

### 5.1 Legacy `probability` field: `@computed_field` vs rename-sweep

**Option A — keep `probability` as a `@computed_field` returning `probability_yes`.**
Zero churn across every call site. Back-compat for any external tool
that reads history.jsonl. Mildly messy long-term (two names for the
same thing).

**Option B — rename sweep: `probability` → `probability_yes` everywhere.**
Clean, but hits `analytics.py`, `main.py`'s verdict table rendering,
`agent_accuracy()`, postmortem display, every backtest script,
`history show`, and all the docs.

**Recommendation: Option A (`@computed_field`).** Reserve a future
cleanup session for the rename once the field proves out.

### 5.2 Schema-version placement

- **Per-verdict flag** (`probability_source` on each `AgentVerdict`) —
  most granular; handles future cases where a single row has both
  synthetic and independent verdicts (e.g. a mid-session backfill).
- **Per-row flag** (`schema_version: "B"` on each entry) — simpler
  migration book-keeping; can't express per-verdict heterogeneity.
- **Both** — redundant but informative.

**Recommendation: per-verdict flag only.** Heterogeneity is real
(partial re-runs are conceivable). Per-row version can be inferred
from `all(v.probability_source == "independent") for v in entry.verdicts`.

### 5.3 Persona prompt framing

Two templates under consideration for the preamble change:

- **Additive** — "Also output `probability_no`. It represents P(TeamB
  wins) with your own independent evidence." Minimum prompt delta,
  agents may still anchor on `1 - p_yes` out of habit.
- **Autonomy-first** — "Evaluate P(TeamA wins) and P(TeamB wins) as
  two separate questions. They do not need to sum to 1.0. If you have
  strong evidence on one side and thin evidence on the other, the two
  probabilities can overlap (sum > 1) or leave mass (sum < 1)."

**Recommendation: autonomy-first**, with a guard instruction telling
agents NOT to force normalization. Risk is agent output drift —
mitigate by spot-checking the first 20 live rows per agent and
re-tuning the preamble if outputs look incoherent. Bear/Bull are
highest risk because their dual-perspective rules already encourage
one-sided thinking.

### 5.4 Parser strictness

- **Strict** — reject verdicts that produce `p_yes + p_no > 1.1` or
  `< 0.9` as malformed, force a retry through `AGENT_MAX_RETRIES`.
- **Lenient** — accept whatever the agent emits, log coherence drift
  as a data signal.

**Recommendation: lenient.** Forcing coherence in the parser destroys
the very signal we're trying to extract. Log a warning above a drift
threshold (e.g. `|p_yes + p_no - 1| > 0.3`) but don't reject.

---

## 6. Rollout order (non-breaking, shadow-first)

1. **Schema + backfill (one-shot).**
   - Add fields to `AgentVerdict` with defaults and `@computed_field`
     shim for `probability`.
   - Land `history.py` tolerant-load: reads entries of either shape.
   - Land `scripts/b_backfill.py`, run it once, verify all existing
     rows now have `probability_source = "synthetic_mirror"`.
   - At this point the codebase accepts the new schema but nothing
     uses it yet. Every existing metric still works.
2. **Parser update (tolerant).**
   - Update `council.py` to parse both fields; default `probability_no
     = 1 - probability_yes` + `synthetic_mirror` flag when the agent
     omits the new field.
   - Still no persona changes — agents continue emitting one field,
     parser tolerates it.
3. **Persona rollout one-at-a-time.**
   - Update SHARED_PREAMBLE with the autonomy block.
   - Update one persona (`rules_lawyer` first — least structural-bias
     risk) to emit `probability_no`. Keep others on legacy output.
   - Run 10-20 analyses; spot-check the emitted pairs. Tune preamble
     wording if outputs look incoherent.
   - Promote one persona at a time in the order:
     `rules_lawyer → statistician → sentiment → specialist →
     contrarian → bull → bear`. Bear last because it's the highest-risk
     for regression (session 20 rewrite is still within the monitoring
     window per A.4).
4. **Shadow analytics (read-only).**
   - Land `analytics.py` functions for per-side Brier, coherence,
     side-bias. All filter on `probability_source == "independent"`.
   - Surface in `history stats` as a new block — zero gate impact.
5. **Shadow backtest.**
   - Run `scripts/b_shadow_backtest.py` once we have 100-150
     independent rows. Same pre-registered criteria as A.1 β.
   - If any B-math candidate clears the bar → proceed to (6).
   - If none clear → stop; B-math fields stay descriptive, don't enter
     scoring.
6. **Scoring integration (ONLY after 5 passes).**
   - Propose the specific change the backtest validated (e.g.
     "coherence-gate: PASS if average `|p_yes + p_no - 1| > X`").
   - Gate on explicit user sign-off per the stable-model rule.

---

## 7. Acceptance criteria for B-math scoring integration (pre-registered now)

Identical to A.1 β, documented up front so we don't move the goalposts:

- Brier (whichever side is being backtested) Δ ≤ `-0.005` vs baseline
  on **both** train (chronological first 70%) **and** holdout (last
  30%).
- Net-correct delta ≥ `-1` on both slices (signal strict enough to
  not cost us more correct buys than it saves wrong ones by more than
  one).
- Bootstrap 95% CI on the Brier delta (1000 resamples, seed 42) must
  lie entirely below 0 on **both** slices.
- Minimum sample: 100 independent-rows in train, 40 in holdout.
- If the candidate uses multi-parameter thresholds (e.g. coherence
  cutoff), the threshold must be **pre-committed** in the shadow
  backtest script header before the first run — no post-hoc tuning.

If we can't meet these criteria, B-math stays descriptive only.

---

## 8. Dependencies and risks

### Depends on

- Session 17 briefing persistence (✅ shipped).
- Session 20 Bear rewrite stability (✅ at 50.2% lean, within
  monitoring band per A.4).
- Per-agent reliability weighting (A.9, ✅ shipped) — unaffected by B
  since `lean_rate` is direction-only.

### Risks

- **Prompt drift**: agents may struggle with the autonomy framing and
  emit degenerate pairs (all `(0.5, 0.5)` or strict mirrors). Mitigated
  by rollout step 3 one-at-a-time.
- **Sample accumulation rate**: 100-150 independent rows at ~10-20
  runs/week is 5-15 weeks. Slower than A.1 β (immediate on existing
  data). Budget accordingly.
- **Feedback loop with A.9 weights**: if B-math changes which markets
  become BUYs, the per-agent `lean_rate` estimator sees different
  data, weights shift, which shifts BUY selection further. A.10
  (feedback-loop monitoring) already tracks this.
- **Legacy-read tools**: any script reading history.jsonl needs to
  handle the new field names. Ecosystem is small (all scripts live in
  this repo), but worth an audit pass before landing step 1.

### Not in scope for this plan

- No changes to the 7 persona identities (Bull, Bear, etc. stay as
  they are — just their output format changes).
- No changes to the gate stack (price floor → ceiling → contrarian →
  market-aligned → EV gate) — that's rollout step 6 or never.
- No changes to `RULES_LAWYER_WEIGHT` / `BEAR_WEIGHT` / dynamic
  weights (A.9) — orthogonal.
- No CLOB integration changes — `yes_price` / NO-token price remain
  autonomous market signals as today.

---

## 9. Go / no-go decision points

| Decision | Who | When |
|---|---|---|
| Approve plan as written | User | Before step 1 |
| Approve `@computed_field` vs rename-sweep (§5.1) | User | Before step 1 |
| Approve autonomy-first preamble (§5.3) | User | Before step 3 |
| Approve per-persona rollout order (§6 step 3) | User | Before step 3 |
| Promote from shadow to scoring integration (§6 step 6) | User + passing backtest | After step 5 |

---

## 10. Summary

Option B is a schema change that enables a whole class of new analytics
(coherence, per-side Brier, side-bias, proper Dempster-Shafer) that
probability transforms on the current schema cannot. Synthetic
`p_no = 1 - p_yes` backfill keeps every existing resolution-based
metric valid at zero API cost. New math applies only to rows tagged
`probability_source = "independent"` going forward, which accumulate
naturally as the user runs analyses.

**Nothing enters the gate stack without clearing the same pre-registered
backtest criteria that rejected A.1 Option A.**

The migration is non-breaking at every step. Shadow analytics are read-only.
Scoring integration is gated on user sign-off after a passing backtest.
