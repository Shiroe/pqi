# PQI External Review — April 2026 (v2)

**Status:** amended
**Supersedes:** `pqi_external_review_2026_04.md` (v1, same session)
**Date:** 2026-04-19

---

## What changed from v1

v1 was written from `CLAUDE.md`, `CTO_Agent.md`, `README.md`, Phase 0
retrospective, and an external ChatGPT review. v2 incorporates
`docs/scoring-reference.md`, `docs/phases/phase_1_signal_quality.md`,
`docs/phases/phase_2_execution_edge.md`, `docs/scaling_assessment.md`,
`docs/known-issues.md`, `NEXT_SESSION_PROMPT.md`, and a clarification
from the project owner on auto-trading intent.

Material amendments:

- **Auto-trading framing** (affects §1, §4, §5, §7). Owner clarified the
  target auto-trading mode is "auto-BUY on actionable → hold to
  resolution," not the continuous-listener/take-profit mode Phase 2 is
  currently scoping. These have very different carrying costs and
  should be separated — renamed here as Tier A (auto-BUY-and-hold) vs
  Tier B (take-profit listener).
- **Phase 1 acceptance-criteria drift** (new §3.1.1). Phase 1 doc's
  original Goals predate the Phase 0 retrospective's cluster-collapse
  finding. The doc targets per-agent mirror collapse (Option B drift);
  Phase 0 §5.3 targets cross-agent cluster collapse + Bear
  miscalibration. These are different measurements. The SHARED_PREAMBLE
  rewrite has to address both, and the scoreboard needs unifying before
  execution. See the separate `phase_1_acceptance_criteria_patch.md`
  for the drop-in replacement.
- **Phase 2 Task 2.1 lookahead bias** (new §5.3). The take-profit
  simulation at thresholds `{2,3,5,7,10}pp` on the historical set is a
  textbook overfit pattern; best-in-sample threshold is nearly
  mechanically positive. Needs chronological train/test split
  promoted from risk note to task requirement.
- **Ghost events + Tier A** (new §4.3). `known-issues.md` documents
  Gamma's intermittent event dropout from list endpoints; this is a
  real concern for any auto-BUY or continuous-scout flow.
- **Confidence asymmetry** (new §7.3). `known-issues.md` has three
  candidate techniques for exploiting `conf_yes`/`conf_no` asymmetry
  that v1 missed. Natural Phase 1.5 candidate.
- **CLAUDE.md "Advisor not Auto-trader" positioning** (new §6.1). If
  Tier A auto-BUY is in scope, the root-level positioning statement in
  CLAUDE.md §Project Mission and the "What NOT To Do" list need
  reconciliation. Flagged for owner decision.

v1's core findings — measurement infrastructure is the durable product
value, cluster collapse is the dominant structural concern, LLM spend
is the binding deployment constraint — all stand unchanged.

---

## 0. Executive summary

**Three things are simultaneously true about PQI:**

1. **The engineering is genuinely good.** Measurement infrastructure,
   Pydantic-contract discipline, session protocol, and CTO-governance
   artefact are rare for a solo project. Durable value even if the
   strategy pivots.

2. **The live trading edge is real but marginal.** Council Brier
   0.2176 beats 0.25 chance baseline by ~3pp; mean CLV −0.0167 eats
   roughly half of that on entry; realized April PnL is ≈ −0.575¢/share
   across 65 BUYs. A signal, not yet *an edge* in the sense that would
   justify aggressive deployment.

3. **The council is structurally less diverse than its name implies.**
   Five of seven agents correlated at r 0.80–0.92; Bull weakly
   independent; Bear independent but miscalibrated. You are paying 7×
   LLM cost for roughly 3× effective voices, and one of those three
   actively degrades council Brier.

**What this means for deployment goals** (home dev machine or
small-medium VPS, optional continuous process, Tier A auto-BUY in scope
eventually):

- **Home/dev case already works.** No infrastructure work needed.
- **VPS continuous-run case is cost-bound by LLM spend rate, not
  infra.** A $5/mo VPS handles the pipeline; API spend scales with
  trigger cadence and can easily exceed the $20/mo ceiling during busy
  weekends. Not a blocker — a knob to tune.
- **Tier A auto-BUY is ~a week of engineering post-Phase-1.** Smaller
  than the current Phase 2 scope, which conflates it with Tier B
  (take-profit listener) that needs MFE validation first. Worth
  separating in the roadmap.

**One-sentence go/no-go:** Execute Phase 1 signal repair on unified
acceptance criteria; then Tier A auto-BUY on actionable results is a
defensible next step; Tier B listener stays gated on MFE analysis.

---

## 1. What actually has product value

Separating durable value from work-in-progress.

### 1.1 Durable value — will still matter in 12 months

**The measurement infrastructure.** Reliability diagram, per-band
realized-vs-threshold tables with Wilson CIs, CLV distributions with
unavailability accounting, per-agent Brier with confidence-weighted
views, outcome classification with selection-bias awareness. Most
retail betting systems run on vibes; yours runs on Brier. If the
trading strategy were replaced tomorrow, this transfers.

**The governance artefact (`CTO_Agent.md`).** "Edge first, scale
second," "parallel is not diverse," "mirror collapse is a
signal-quality bug not a sample-count bug," "win-rate claims are
inadmissible without five numbers." The discipline that prevents
three-weekend features the data doesn't justify. Load-bearing.

**The Pydantic contract discipline.** All inter-module data flow
through models; field-level signatures in `docs/models-reference.md`.
Ordinary software engineering hygiene, but it's what lets you add
Tier-1 deterministic features or a structured extractor without
shotgun-surgery across the pipeline.

**The history system.** JSONL append-only, upsert-keyed on market ID,
DuckDB read-only analytics over it, replay support. Portable,
diffable, backup-trivial. The right database for the problem.

**The audit trail.** Per-agent verdict storage, postmortem generation,
`run_id` stability, outcome classification with dead-band logic. What
lets Phase 0's retrospective exist at all.

**The advisor-not-auto-trader *default posture*.** Correct as a
default given current edge magnitude. See §6.1 for the Tier A vs Tier
B distinction — the posture should evolve, but conservatively.

### 1.2 Work-in-progress value — directional, not yet proven

**The 7-agent council.** Small prediction edge today (~3pp Brier above
chance). Whether it scales depends entirely on Phase 1 outcome.

**The banded EV gate + Kelly sizing.** Architecturally correct (banded
threshold, Beta-binomial smoothing, Kelly inputs matching gate inputs,
half-Kelly default). No band clears ≥1 Wilson-CI flag in either
direction; that's a null, but an honest null at n=62 BUYs across bands.

**The Option B shadow layer.** Correct preparation for per-side
probability; correctly blocked on Phase 1 signal quality rather than
sample count.

**Dynamic agent weights.** Present; needs overfit monitoring. Likely
partly absorbing Bear miscalibration in ways that won't generalize
post-rewrite. Recommend resetting weights to uniform at start of Phase
1 so they re-accumulate on post-rewrite data. See §5.7.

**PandaScore integration.** Real value for structured match status and
temporal disambiguation. Rate limit (1000/hr free) doesn't bind in
normal use. Weakness: no Elo/Glicko; build your own (Phase 1.5
candidate).

### 1.3 Not yet producing measurable value

**Two-sided confidence.** Conceptually correct, actively logged.
Scoring still aggregates a single `probability` field. `known-issues.md`
describes the asymmetric-confidence opportunity with three candidate
techniques (Bayesian shrinkage, log-odds weighted, Dempster-Shafer
ignorance); all plausible Phase 1.5 candidates. See §7.3.

**The disagreement signal.** Cluster-vs-Bear, Bull-vs-cluster, stddev
of agent probs. Your highest-potential unused feature. Logged but not
yet used as gate or feature. First candidate for a meta-model over the
council once Phase 1 stabilizes the signal.

---

## 2. The edge picture, brutally honest

### 2.1 What the data says

**Council Brier 0.2176 vs 0.25 baseline = small, real, fragile.** On
n=385, bootstrap CI around Brier is ~±0.02. "Likely real" is not
"robustly measured" — cluster-corrected council Brier (controlling for
the effective single-vote-counted-five-times structure) might be closer
to 0.23–0.24. You currently cannot measure this because the dynamic
weights already partly absorb the effect.

**Mean CLV −0.0167 eats ~half of the Brier edge.** 1.7pp of adverse
pre-event timing on a system with ~3pp prediction edge leaves ~1.3pp
before other friction:

- Fillable slippage at Kelly-sized stakes (CLV compares to mid/last,
  not to depth-fillable price for your position size)
- Polymarket fees (2% on winnings)
- Polygon gas
- Capacity at best bands (see §4.4)

Realistic realized-edge-after-friction is probably 0–1pp, consistent
with −0.575¢/share April PnL being "noise around zero."

**Per-band story is uninformative at current n.** [0.60, 0.70) n=23,
[0.70, 0.80) n=15, [0.80, 0.95) n=16, [0.50, 0.60) n=8. Wilson
half-widths of 20–30pp mean you cannot statistically distinguish bands
from break-even or each other. Directional reads are interesting but
not actionable.

**Cluster collapse is the most actionable finding.** Three genuinely
distinct voices instead of seven is a quantifiable structural defect
with a testable fix. Highest-leverage next move.

### 2.2 What the data doesn't rule out

- Edge is zero (sits inside CI region)
- Edge comes entirely from gate survivorship / selection bias on
  analyzed markets
- LLM synthesis adds nothing above a cheap Glicko baseline (you
  haven't measured this; Phase 1.5 should)

---

## 3. Structural concerns, ranked by impact

### 3.1 Cluster collapse — Phase 1 priority

Five agents correlated at r 0.80–0.92 because they share a briefing
and a preamble. Plan for persona-by-persona rollout with measurement
between each iteration; a single-shot SHARED_PREAMBLE rewrite has too
many simultaneous constraints to land in one change.

#### 3.1.1 Unified acceptance criteria (new in v2)

**This is the most important delta from v1.** The Phase 1 doc's
current `## Goals (measurable)` section targets per-agent YES/NO drift
(Option B mirror collapse). Phase 0 §5.3 identified the more severe
problem as cross-agent correlation + Bear miscalibration. These are
different measurements and the SHARED_PREAMBLE rewrite has to address
both. A rewrite that fixes drift but not cluster is still a Phase 1
failure by the current retrospective's criteria.

Four criteria unified:

1. **Cluster-pair r ≥ 0.85 fraction below 0.50** *(cross-agent
   diversity — new, from Phase 0 §5.3)*. Currently 9/21 pairs = 43%,
   close to threshold but driven by the 5-agent cluster; the lever is
   destabilising that cluster.
2. **Per-agent `|drift|` > 0.10 on ≥2 agents** *(YES/NO autonomy —
   original Phase 1 criterion)*. The Option B mirror-collapse test.
3. **Bear Brier toward 0.22 with Bear's pairwise r to others still
   below ~0.50** *(calibration + independence — new, from Phase 0
   §5.3)*. Bear is load-bearing on aggregate Brier today (0.2810 is
   above chance baseline). Fixing calibration is high-leverage; doing
   it by collapsing Bear into the cluster is a regression.
4. **Council Brier holds or improves from 0.2176** *(aggregate
   regression guard — new, from Phase 0 §5.3)*.

All four need to pass simultaneously. Fixing any one in a way that
regresses another is not acceptance. This is why the persona-by-persona
rollout discipline from Phase 0 §5.3 is necessary — a single preamble
edit hitting all four in one shot is improbable.

**Implication for Task 1.1 approach:** the current doc proposes
asymmetric-evidence worked examples + a forced evidence-quality
pre-step + an anti-mirror warning. These address **mirror collapse**
(criterion 2). They do not directly address **cluster collapse**
(criteria 1, 3, 4). What addresses cluster collapse is
**persona-specific evidence anchoring** — forcing each persona's
reasoning chain through persona-specific ground:

- Statistician cites base-rate / historical number in reasoning
- Specialist cites a game-mechanics / meta detail
- Sentiment cites a crowd-psychology or social-signal indicator
- Contrarian cites a specific disruption vector
- Rules Lawyer cites the resolution criteria wording directly

When each persona's reasoning passes through different ground, outputs
decorrelate at the source rather than at the aggregator. This is a
content-level change to Task 1.1 beyond what the current doc specifies.

A drop-in replacement for the phase doc's Goals and Task 1.1 sections
is provided separately in `phase_1_acceptance_criteria_patch.md`.

### 3.2 Bear is actively degrading council accuracy

Bear Brier 0.2810 is above 0.25 chance baseline. Bear's sole virtue is
structural independence (r ∈ [−0.17, +0.04] with all others). Killing
Bear would improve aggregate Brier but destroy your only uncorrelated
voice. Fixing calibration while preserving independence is the
highest-value single change.

Diagnostic from Phase 0 §5.2: Bear predicts 0.356 mean, realizes 0.484
on its concentration band [0.09, 0.50) — +0.128 gap on n=351. Bear is
systematically under-predicting YES. This is a prompt-structure bug,
not a calibration-layer bug — Bear is producing P(YES) estimates that
reflect its advocacy role rather than its actual belief.

A prompt rewrite that forces Bear to output probability *after*
constructing its advocacy case, rather than *as an expression of it*,
is likely to fix this without touching independence. Expect pushback:
over-correction collapses Bear into the cluster. Measure pairwise
correlation with the cluster at every iteration.

### 3.3 Narrative bias via shared briefing

All seven agents consume the same Tavily-sourced narrative. Known
failure mode in LLM ensembles, not prompt-engineerable away at the
council layer — fix at the briefing layer.

Two mitigations:

1. **Per-agent retrieval (medium term).** Different personas get
   different Tavily queries tuned to their bias. Increases Tavily
   spend (free tier 1000/mo would bind fast) but decorrelates at
   source.
2. **Structured feature block (low cost).** Tier-1 deterministic
   features (Glicko diff, form, H2H, roster stability,
   days-since-last-match) go in a structured block *above* the
   narrative. Different personas weight it differently by bias.

### 3.4 Dynamic weight overfitting risk

At n=50–100 per agent, weight updates absorb noise. If Phase 1 fixes
Bear, the weights should mostly unwind to near-uniform. If they don't
(Bear stays heavily under-weighted post-rewrite), that's evidence the
weights are overfit to historical Bear miscalibration rather than
tracking real differential accuracy.

**Concrete monitor:** after each Phase 1 prompt iteration, re-run the
reliability script and compare weight-snapshot delta. Big moves on
small post-rewrite n = overfitting.

**Recommend:** reset `~/.pqi/agent_weights.json` at the start of
Phase 1 so post-rewrite weights don't inherit pre-rewrite Bear
miscalibration. The file is a load-bearing persisted state per
`known-issues.md` — deletion reverts scoring to static map on next
run, no crash. Regenerates via `history sync` / `history stats`.

### 3.5 No external probability anchor for Tier 2/3 markets

Pinnacle covers top 20–30% of eSports universe. The rest has no sharp
reference. Current gates treat all tiers uniformly — systematically
over-confidences Tier 2/3 BUYs.

**Mitigation (Phase 1.5 scope, not urgent):** segment the gate. Tier 1
with cross-book coverage keeps current thresholds; Tier 2/3 gets
tighter (higher confidence, higher contrarian probability floor,
smaller Kelly multiplier). `.env`-level change plus a `tier` field on
`MarketData`.

### 3.6 CLV drag is diagnostic, not systemic (yet)

1.7pp mean adverse CLV could be structural slow-information (analyze
at T-4h, market moves at T-1h), selection bias in CLV sample (21
no-token rows pre-date session 22), or category-specific (Valorant
−0.032, LoL −0.001 on thin n). None require Phase 2 work to diagnose;
re-run monthly, let the category breakdown fill in.

---

## 4. Operational analysis — home vs small-medium VPS

### 4.1 What the current architecture needs

- **Python 3.11+**, one process, async-IO, SQLite + JSONL on local
  disk. Outbound HTTPS only (pull-based). `~/.pqi/` at low-GB scale.
- **CPU:** network-bound, not CPU-bound. 1 vCPU sufficient on demand;
  2 vCPU gives headroom for DuckDB aggregation.
- **RAM:** 1GB tight, 2GB comfortable, 4GB plenty.
- **GPU:** only relevant for Ollama dev; not needed on VPS.

**Concrete VPS shape:** $5–10/mo at Hetzner/DigitalOcean/Vultr — 1
vCPU, 2GB RAM, 40GB SSD. Infra isn't the binding cost — LLM spend is.

### 4.2 LLM spend is the binding constraint

Per council run: ~$0.015 with Anthropic prompt caching warm. Run-rate
economics:

| Usage pattern | Analyses/day | Monthly LLM cost | Tavily cost |
|---------------|-------------:|-----------------:|------------:|
| Manual CLI, occasional | 2–5 | $1–3 | free tier fine |
| Manual CLI, daily use | 10–20 | $5–10 | free tier fine |
| Continuous scout, passive | 30–50 | $15–25 | free tier tight |
| Continuous scout, active | 100–200 | $50–100 | paid tier needed |
| Continuous scout, aggressive | 500+ | $250+ | paid tier + concurrency risk |

The CLAUDE.md $20/mo ceiling is an "active continuous scout" budget.
At default 5% trigger threshold with no cooldown, a busy eSports
weekend blows through it.

Levers (preference order):

1. Scout sensitivity + per-market cooldown
2. Prompt cache warmth verification (confirm `cache_read` hits in
   production)
3. Council-size tiering (3-agent for low-conviction, 7-agent for
   high-conviction) — *only after* Phase 1 shows the 3 effective
   voices
4. Haiku vs Sonnet per-persona (Rules Lawyer is the candidate)
5. Ollama fallback for Tier 2/3 (hybrid VPS + home-GPU is fiddly)

### 4.3 Ghost events — real concern for any continuous flow (new in v2)

`known-issues.md` documents Gamma's `/events` list endpoints
intermittently dropping events, confirmed around mid-match settlement
transitions. Example: event 273395 invisible to list endpoints while
individual `/events/{id}` fetch succeeded.

**For Tier A auto-BUY or a continuous scout**, this means you'll
occasionally miss an actionable analysis window because the event is
temporarily invisible to list queries. Options:

- **(a) ID-based probing.** Keep a rolling set of tracked event IDs
  from previous runs. For each tracked ID not returned by list
  queries, probe individually via `/events/{id}`. Cheap insurance;
  code already handles individual event fetches.
- **(b) Accept the gap.** Document completeness limitation. Viable
  for Tier A if you accept occasional missed opportunities.

For Tier A specifically, (a) is worth the ~1 day of engineering. For
Tier B (continuous position monitoring), (a) is mandatory — a ghost
event with an open position would be a blind spot on live risk.

### 4.4 Capacity — the uncomfortable ceiling

Not yet measured: for best bands ([0.80, 0.95) realized +9.9pp vs mean
price), what is fillable liquidity at Kelly-sized stake?

Half-Kelly on 60¢ BUY with 70% WR ≈ 8.3% of bankroll. On $1k, $83/bet;
on $10k, $833/bet. Polymarket eSports markets outside Tier 1 regularly
have <$500 depth at best bid/ask.

**Implication:** above some bankroll (probably low-to-mid four figures
for eSports), Kelly-optimal stake won't fill without eating spread
that destroys edge. Capacity ceiling that informs whether PQI is a
craft/research project or a serious trading system. At current edge,
it's the former with option value on becoming more.

### 4.5 What does NOT need to exist

- No Docker yet (Phase 3 explicitly deferred — correct)
- No Postgres (SQLite + JSONL covers the access patterns;
  scaling_assessment.md §7 argues this well)
- No Kubernetes, message queue, web server, GUI
- No multi-region or HA
- No observability stack beyond `pqi history stats` + periodic cron

### 4.6 Continuous-run shape when ready

Roughly:

- One process (`python -m pqi scout` — roadmap Theme B, not yet built)
- Rate-limit coordinator for Anthropic + Tavily (roadmap flagged it as
  a Phase 3 requirement; genuinely required before parallel workers
  ship)
- Systemd unit with `Restart=on-failure` for persistence (no Docker
  needed)
- `pqi history stats` on weekly cron, output to email/Slack

---

## 5. Traps to avoid (ordered by severity)

### 5.1 Deploying continuous-run before Phase 1 signal repair

Running a continuous Scout at current signal quality spends LLM budget
on a cluster-collapsed council whose Brier edge is ~3pp and half-eaten
by CLV. More API spend, less realized PnL than the same budget spent
on Phase 1 prompt iteration. **Do not stand up continuous runs until
Phase 1's four unified acceptance criteria are hit.**

### 5.2 Auto-trading before unified Phase 1 criteria pass (amended in v2)

v1 said "auto-trader before execution edge validated." That framing
conflated Tier A and Tier B. Corrected:

- **Tier A (auto-BUY and hold):** appropriate after Phase 1 unified
  criteria pass. Independent of execution-edge validation because Tier
  A doesn't make exit decisions. Requires: account auth,
  `py-clob-client` trade function, Kelly-sized BUY, pre-trade price
  recheck, idempotency guard.
- **Tier B (take-profit listener):** stays gated on Phase 2 MFE
  analysis showing capturable opportunity above fee drag. Adds
  continuous listener, position state, exit logic, sell-side slippage
  modeling.

**Trap specifically:** shipping Tier A before Phase 1 criteria pass
turns cluster-collapsed council outputs into automatic fills — the
fastest path from advisor-that-sometimes-misses to auto-loss
generator.

### 5.3 Phase 2 Task 2.1 in-sample threshold selection (new in v2)

Task 2.1 proposes simulating take-profit at thresholds
`{2,3,5,7,10}pp` on the historical set. Best-in-sample threshold is
nearly mechanically positive on any finite sample of noisy price paths
— this is textbook overfit.

Fix already present in the doc at the wrong severity level. Task 2.1's
Risks section notes "simulated P&L must account for MFE correlation
with final hit." Promote to **task requirement**, not risk note:
chronological train/test split (same discipline as
`option_b_migration_plan.md §7`). Find best threshold on earlier 70%,
lock it, measure net PnL on held-out 30%. Without this, Task 2.4's
automate/manual/park verdict is partly selection artifact.

### 5.4 Treating small-n calibration as signal

Phase 0 rules this out explicitly. Restating: [0.80, 0.95) at +9.9pp
realized over mean price is a hypothesis at n=16, not a finding. Do
not concentrate strategy around it until n ≥ 50 in that band.

### 5.5 Retuning gate knobs during Phase 1

Phase 0 §7.2 freezes `BAND_SHRINKAGE_K`, `MIN_CONTRARIAN_PROBABILITY`,
ROI bands, Kelly multiplier, `MIN_WIN_RATE_SAMPLES`. Temptation to
tune during Phase 1 because "the gate might be adapting to new
prompts" is real — resist. Tuning both simultaneously forfeits Brier
delta attribution.

### 5.6 Scraping Pinnacle

Against ToS, blocks aggressively. Use The Odds API (or similar
aggregator) for Pinnacle coverage. Marginal value of direct-scrape is
small; operational risk is large.

### 5.7 Adding a second LLM provider without measuring diversity

Seductive because "multi-model" sounds like diversification.
`CTO_Agent.md` §2 correctly identifies it: "'It's a different vendor'
is not evidence of independence." Test: run second provider on same
briefings as Haiku for N analyses, measure pairwise Spearman. If
r > 0.7 with Haiku, doubled cost and halved diversity — skip.

### 5.8 Letting dynamic weights compound past Phase 1

Shrinkage factor prevents explosion; doesn't eliminate drift. Phase 1
prompt changes invalidate historical per-agent accuracy. **Reset
weights to uniform at Phase 1 start** so they re-accumulate on
post-rewrite data.

### 5.9 Over-engineering structured extractor before Tier 1 basics

Adding a Tier 3 LLM extractor before Tier 1 deterministic features
(Glicko-2, form recency, roster stability) is wrong order. Tier 1
first; Tier 3 only once there's a stable feature vector to extract
into.

### 5.10 CLAUDE.md "Advisor not Auto-trader" drift

See §6.1. Root-level positioning statement in `CLAUDE.md` §Project
Mission and "What NOT To Do" list need reconciliation with Tier A
intent. Not urgent but worth doing when Tier A implementation starts.

### 5.11 Ghost event blindspot (new in v2)

See §4.3. Continuous flow without ID-based probing will silently miss
events that drop from list endpoints. For Tier A, acceptable with
documentation. For Tier B, mandatory to fix.

### 5.12 Building a GUI

Not in roadmap; guarding against impulse. CLI + `rich` tables +
DuckDB SQL are sufficient. If you want better presentation, generated
HTML report (afternoon of work) beats Streamlit/Flask (week of work,
zero edge).

---

## 6. Recalibration notes on current docs

### 6.1 CLAUDE.md — "Advisor not Auto-trader" needs reconciliation (new in v2)

Current state (CLAUDE.md §Project Mission):
> The system is an **Advisor, not an Auto-trader**. Output = top 3
> ranked opportunities with confidence scores.

And "What NOT To Do":
> Do not add a web server, REST API, or database in Phase 1

The Advisor framing is correct as a **current operational mode** but
overly binding as a **long-term positioning statement** given Tier A
intent. Recommended reconciliation (for owner decision, not my call):

- Keep "Advisor is default mode."
- Add "Tier A auto-execution (auto-BUY on actionable → hold to
  resolution) is an approved future mode gated on Phase 1 unified
  acceptance criteria."
- Keep "Tier B auto-execution (continuous listener, take-profit) is
  gated on Phase 2 MFE analysis."

This is a CLAUDE.md edit, not a code change. Worth doing before Tier A
implementation so future sessions don't have to re-litigate the
positioning.

### 6.2 Tech Stack table housekeeping

CLAUDE.md line 43 lists `claude-haiku-3-5` as prod LLM; line 457 and
session-log correctly show `claude-haiku-4-5-20251001`. Owner confirmed
housekeeping drift. Fix in next session.

### 6.3 Phase 1 doc acceptance criteria

Stale relative to Phase 0 §5.3. Drop-in replacement provided in
`phase_1_acceptance_criteria_patch.md`.

### 6.4 Phase 2 Task 2.1 — train/test requirement

Add chronological split to task requirements (§5.3 above).

### 6.5 CTO_Agent.md additions

Two candidates for §5 (Trading Edge & Business Assumptions):

- **Capacity axis.** Add note that capacity is a third, also-orthogonal
  edge axis. Proposals expanding bet size need to demonstrate fillable
  liquidity at target stake, not just theoretical Kelly fraction.
- **Recent-window accuracy skepticism.** Under §7(a) "Validate the
  premise," a good-looking last-30-days WR on small n can always be
  produced by selection variance. "Look how well it's been doing
  lately" proposals should get extra skepticism.

---

## 7. Recommended near-term plan (amended)

### 7.1 Weeks 1–3: Phase 1 signal quality (unified criteria)

SHARED_PREAMBLE rewrite with persona-by-persona rollout, targeting all
four unified criteria simultaneously. Reset agent_weights.json at
start. Re-run reliability_diagram.py after each iteration. See
`phase_1_acceptance_criteria_patch.md` for drop-in criteria + Task 1.1
content additions.

### 7.2 Week 2 parallel: Glicko-2 pipeline

Build offline from PandaScore match history. Per-game ratings with
roster-change RD reset. Validate calibration on held-out month of
resolved markets (compare Glicko-diff-implied P(win) to realized
outcomes). Not yet wired to briefing — that's Phase 1.5.

### 7.3 Phase 1.5 candidates (after Phase 1 acceptance)

Three independent candidates, each measurable via Brier delta on
reliability diagram:

- **Tier 1 structured feature block** in briefing (Glicko diff, form,
  H2H, roster stability).
- **Confidence-asymmetry aggregator.** `known-issues.md` proposes
  three techniques — Bayesian shrinkage, log-odds weighted,
  Dempster-Shafer ignorance. Log-odds weighted is the cleanest fit
  for Polymarket: `score = Σ conf_side × ln(p/(1−p))` per side, sign
  picks lean, magnitude is signal strength. Naturally lets
  high-confidence voice dominate. Backtest as per Option B step 5
  pattern (chronological split, bootstrap ΔBrier CI).
- **Per-agent retrieval** (different Tavily queries per persona).
  Highest expected Brier impact but highest Tavily cost impact. Last
  of the three.

Order: Glicko-2 first, then confidence-asymmetry aggregator (cheap,
code-only), then per-agent retrieval if the first two don't close the
gap.

### 7.4 Phase 2a: Tier A auto-BUY command (new structure)

Unblocks after Phase 1 unified criteria pass. Scope:

- `pqi buy <market_id>` command with Kelly-sized stake
- Pre-trade price recheck (abort if spread moved >X% since analysis)
- Idempotency guard (don't double-fill on retriggered analysis)
- Idempotency key on `market_id + analysis_run_id`
- Logging to `~/.pqi/fills.jsonl` (atomic tmp+rename like other
  persisted state)
- Interactive mode: 30s confirmation prompt
- Unattended mode: auto-fire if `--unattended` and interactive mode
  isn't tty
- Ghost event ID-based probing (§4.3 option a)

Rough: 1 week post-Phase-1, ~200–400 LoC plus tests.

### 7.5 Phase 2b: MFE analysis (deferred; current Phase 2 Task 2.1)

Gated on Phase 1 and on chronological train/test discipline (§5.3).
No infrastructure investment until verdict.

### 7.6 Phase 2c: MFE listener (deferred; current Phase 2 Task 2.2–2.4)

Gated on Phase 2b showing capturable opportunity above fee drag.

### 7.7 Continuous-run VPS deployment — deferred

Re-evaluate after Phase 1 + Phase 1.5 show meaningful Brier
improvement AND either Phase 2a ships (justifying unattended mode) or
Phase 2b identifies Tier B opportunity. Until then, manual CLI on dev
machine is correct operational mode.

### 7.8 Odds API free tier signup — now

Even partial Pinnacle coverage on Tier 1 matches gives external
probability anchor for council spot-check. Not a roadmap item; weekend
integration.

---

## 8. Final framing

The question asked — "what actual product value exists" — has a
specific answer: the measurement infrastructure, the governance
discipline, the contract rigour, and the history/audit system are
durable engineering value that will outlive whatever strategy runs on
top. The live trading edge today is small and fragile and would not
survive deployment pressure without Phase 1 repair.

The question not quite asked but implied — "is this worth continuing"
— has a different answer: the edge is small but *specific and
testable*, the governance prevents self-deception about it, and the
three-voices-not-seven finding is structurally actionable rather than
vague. Green flags for a project that could become real over 3–6
months of disciplined iteration. Not flags for a project ready for
continuous deployment today.

**Amended recommendation:** execute Phase 1 on unified criteria
(`phase_1_acceptance_criteria_patch.md`). Build Glicko-2 in parallel.
Ship Tier A auto-BUY as Phase 2a post-Phase-1. Keep Tier B listener
gated on MFE analysis with chronological train/test discipline. Defer
VPS continuous deployment until either Tier A runs need unattended
mode or Phase 2b identifies real Tier B opportunity. Monthly
reliability diagram as standing practice.

The property that matters most — *knowing instead of guessing* — is
what separates PQI from 90%+ of retail betting systems. Every
amendment in v2 is in service of preserving that property while
executing the next phase cleanly. Don't lose it.
