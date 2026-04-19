# v2.0 Scaling Assessment — April 2026

This document captures the external-proposal triage that preceded
the Phase 0–4 execution plan (`docs/phases/`). It preserves the
brainstorming inputs (RFC + review) and the recalibration so future
sessions understand why specific priorities were chosen over the
obvious alternatives.

**Date:** 2026-04-18
**Status:** snapshot (do not mutate — changes to the plan live in
`docs/phases/` or later assessments)

**Inputs:**
- `docs/esports_intel_engine_report.md` — brainstorm RFC proposing
  Dockerized microservices, PostgreSQL, Airflow/Prefect, Redis,
  multi-model MoE, WebSocket market listener, entity-resolver
  container.
- `docs/cto_review_esports_engine.md` — external reviewer's
  response (approve Postgres + async + Redis + evaluation
  framework; delay microservices and multi-model).
- Live project state at session 32 (see `CLAUDE.md` §Current State
  and `docs/session-log.md` for the full changelog).

---

## 1. Summary of conclusion

The RFC's direction is reasonable for a production enterprise
system but wrong-sized for this project.

- **~40% of the proposed "v2.0" is already shipped** (async, fuzzy
  entity resolution, Brier-style dynamic weights, centralized
  retry, prompt caching, PandaScore structured data, banded EV
  gate, Kelly sizing).
- **~60% is either premature** (no measured bottleneck) **or the
  wrong tool for the actual problem** (e.g. Postgres when the real
  need is ad-hoc SQL analytics — DuckDB fits better).
- **Both documents accepted the headline "68% WR" claim without
  validation.** Decomposed, the number splits into two distinct
  metrics, neither of which on its own validates edge.
- **The replacement plan is five phases, edge-first**, each with
  explicit entry/exit criteria. See `docs/phases/README.md`.

---

## 2. Input 1 — the RFC

Proposes transitioning PQI from a local monolithic script to a
distributed Dockerized microservice architecture on Fedora.

### Proposed components

| Component | RFC framing |
| --- | --- |
| **The Scout** (WebSocket listener) | Polymarket price-volatility detection; triggers out-of-band analysis on >5% moves |
| **The Resolver** (entity mapper) | `thefuzz` + LLM validation for Polymarket ↔ PandaScore ID mapping, Postgres-cached |
| **The Historian** (structured ETL) | PandaScore + Playwright stealth scrapers |
| **The Journalist** (unstructured ETL) | Tavily "Search Architect" |
| **The Syndicate** (async MoE) | `asyncio` fan-out; model-mixing across Claude / Gemini / GPT |
| **The Bookie** (execution gate) | +EV, Brier weights from Postgres, Half-Kelly sizing, Buy/Pass signal |

### Infrastructure

- PostgreSQL + JSONB for briefings and time-series
- Apache Airflow or Prefect for orchestration
- Redis for LLM task queuing
- Docker on Fedora, with potential migration to AWS ECS / GCP Cloud Run

### Stated motivation

"~68% global win rate on Half-Kelly stakes" validates the core
model; the current JSON/synchronous stack is the bottleneck
limiting alpha capture speed.

### Factual inaccuracies in the RFC

1. **"7 agents" list is wrong two ways.** The RFC lists "The Judge
   (Synthesizer)" as the seventh agent. The real council is
   `rules_lawyer / bull / bear / statistician / sentiment /
   specialist / contrarian` — 7 agents with the Statistician as the
   base-rate voice. The Synthesizer is an *upstream briefing-builder*
   (`pqi/ingestion/synthesizer.py`), not an aggregator; aggregation
   happens in `pqi/scoring.py` via confidence-weighted scoring.
   (User flagged this — said it was an oversight in the conversation
   that produced the RFC.)
2. **Credit for already-shipped work.** The RFC treats several
   items as forward work that already exist:

   | RFC treats as new | Already shipped |
   | --- | --- |
   | Async MoE fan-out | `pqi/agents/council.py` `asyncio.gather` since day 1 |
   | Fuzzy entity resolution | `pqi/ingestion/match_resolver.py` (rapidfuzz), session 28 |
   | Brier-weighted agents | `pqi/agent_weights.py`, session 25 |
   | PandaScore structured data | `pqi/ingestion/pandascore.py`, session 28 |
   | Exponential backoff on 429/529 | `pqi/llm/client.py` `_chat_anthropic`, session 30 |
   | +EV / Half-Kelly | `pqi/scoring.py` + `pqi/stats.py`, session 22; banded, session 31 |
   | Temporal disambiguation | Session 28 briefing block |
   | Prompt caching | `pqi/llm/client.py` ephemeral `cache_control`, session 27 |
   | Category-resolution cache | `~/.pqi/category_cache.json`, session 29 |

---

## 3. Input 2 — external review

Directionally correct but project-agnostic. Approves things that
already exist ("async execution," "evaluation framework") without
cross-checking against the current codebase.

Key positions from the review:

- **Approve:** PostgreSQL + JSONB, async execution, Redis queues,
  event-driven ingestion, centralized LLM gateway.
- **Delay:** microservices (suggests modular monolith first),
  multi-model MoE expansion, heavy orchestration tools.
- **Other calls:** hybrid cloud/bare-metal, 95/85 entity-resolution
  thresholds, 2 models max initially.

The review's framing of "modular monolith first" is the single
most useful position in the document — it matches this project's
actual trajectory and anchored the Phase 3 "container the monolith,
don't decompose it" recommendation.

---

## 4. The "68% WR" decomposition

Both external documents accepted "~68% global win rate" as a single
validated metric. When queried, the user clarified it is actually
two different numbers:

| Metric | Sample | What it measures | Interpretation |
| --- | --- | --- | --- |
| `correct_buy / wrong_buy` on resolved `history.jsonl` | ~55 resolved BUYs | P&L-relevant: did gate-cleared BUYs pay? | 95% CI roughly ±13pp. Useful for gate calibration; not sufficient for edge claim. |
| Agent directional "leaning" hit rate | ~400 matches × 7 agents ≈ 2800 judgments | Prediction calibration: when the council leans YES, does YES resolve? | More statistical power. **Not** a betting win rate — no price, gate-survivorship, or sizing accounted for. |

The agent-level 68% is the more valuable signal (larger sample,
cleaner interpretation) **but only meaningful if broken down by
predicted-probability bucket.** A model that predicts 70%
accurately 70% of the time has edge; a model that predicts 70%
accurately 55% of the time at prices ≥0.75 is losing money. The
reliability diagram that would resolve this is cheap to produce
but not yet produced — Phase 0 task.

---

## 5. The CLV framing correction

Initial draft of the updated `CTO_Agent.md` used a sportsbook
framing of CLV ("closed at 0.60" after buying at 0.45). This is
**incorrect for Polymarket**: event-driven markets resolve to 1.00
(winning side) or 0.00 (losing side). There is no mid-price close.

Corrected framing (now in `CTO_Agent.md` §5):

- **Pre-event CLV.** Price at `event_start_time` vs entry price.
  Captures prediction edge; retroactively measurable from CLOB
  hourly history.
- **Max favorable excursion (MFE).** Highest price the position
  traded at before resolution. Measures opportunity cost of not
  taking profit; capturable retroactively, realizable only with a
  continuous listener.
- **Calibration / Brier.** Council probability vs outcome. Highest
  data density (uses full verdict set, not just resolved BUYs).

These are three distinct measurements of different types of edge
and must not be conflated.

---

## 6. Prediction edge vs execution edge

Critical insight from the CLV discussion: there are two orthogonal
edge sources in Polymarket.

- **Prediction edge** — the council predicts P(YES) more accurately
  than the market. Captured by buying at the better price. Validated
  via pre-event CLV, calibration, Brier.
- **Execution edge** — exit discipline on favorable mid-event
  excursions. Captured by selling before the market reverses.
  Requires continuous monitoring; Polymarket has no native
  stop-loss, take-profit, or bracket orders.

A "continuous listener" proposal sits differently depending on which
edge it serves:

- The RFC's Scout framing (faster entry, new-market detection) is
  **prediction edge** — and depends on prediction edge being
  validated first.
- A take-profit monitor on held positions (faster exit) is
  **execution edge** — and can pay off independently of prediction
  edge.

Phase 2 of the execution plan is scoped to the **execution-edge**
framing deliberately, because that's the defensible prototype.

---

## 7. Why DuckDB over PostgreSQL

The RFC and the review both landed on Postgres + JSONB. The actual
problem the user reported is not concurrency, scale, or OLTP/OLAP
mixing — it is **queryability**: "most of the analytics are very
hardcoded ... I have to write something in Python in order to see
any statistic."

| Solution | Fit |
| --- | --- |
| **PostgreSQL + JSONB** | Enterprise-grade. Requires server process, auth, backups, migrations. Solves concurrency + OLTP/OLAP mix. Neither problem applies here. |
| **DuckDB** | Embedded (like SQLite). Reads `history.jsonl` natively (`SELECT * FROM read_json_auto(...)`). Full analytical SQL. Zero ops. Half-day wire-up. |
| SQLite (already present for cache) | Adequate for simple queries, weaker on analytics (no window functions, less ergonomic for nested JSON). |

**DuckDB wins for a solo-operator analytics workload.** Postgres is
the answer when concurrency or data volume outgrow embedded
engines; neither trigger has fired.

---

## 8. The continuous-listener framing

The user's end-state goal — "the system running on its own, not
requiring me to be at the terminal, easy to move to a small VPS" —
is a **deployment** requirement, not a **scale** requirement. Two
consequences:

1. **Containerization value is portability**, not scale-out. A
   single Docker container wrapping the monolith costs ~days of
   work and unlocks VPS deployment. Microservices are not needed.
2. **The continuous listener's most defensible version is exit
   discipline on existing positions** (execution edge), not
   new-market discovery and auto-analyze (which stacks on prediction
   edge that is not yet validated).

---

## 9. Recalibrated priorities

Five phases, edge-first, with explicit triggers. Full detail in
`docs/phases/`.

| Phase | Title | Why | Estimated effort |
| --- | --- | --- | --- |
| **0** | Edge Validation via Analytics Infrastructure | Unlocks ad-hoc SQL over history (DuckDB), backfills pre-event CLV, builds reliability diagram, per-band calibration. Answers: do we have edge? | 1–2 weeks |
| **1** | Signal Quality Fix (Option B Autonomy) | SHARED_PREAMBLE autonomy rewrite to break Option B mirror collapse. Runs Option B backtest if drift gate met. | 2–4 weeks |
| **2** | Execution Edge Prototype | Retroactive MFE analysis, read-only listener for open positions, manual review loop, decision on automation. | 3–6 weeks |
| **3** | Deployment Portability | Single Docker container, docker-compose, VPS deploy. | 3–5 days |
| **4** | Conditional Scaling | Trigger-driven: Postgres if DuckDB bottlenecks, LiteLLM if multi-model ships with proven signal gain, Redis if auto-trader queue needs it. | — |

Phases 0 and 1 can parallelize (they touch different files: analytics
vs personas). Phases 2 and 3 depend on Phase 0. Phase 4 is purely
reactive.

---

## 10. Explicit rejections (and why)

These are the v2.0 proposals that are deliberately **not** being
done. Any future proposal that resurfaces one without a Phase 4
trigger firing is a Technical Debt Alert per `CTO_Agent.md` §7.

- **Apache Airflow / Prefect.** No multi-DAG, no multi-team. Cron +
  the monolith's CLI covers everything we do. Reconsider if N>5
  workflows emerge with genuine dependency graphs.
- **Redis message broker.** `asyncio.gather` handles fan-out
  in-process; we have one producer and one consumer. Reconsider if
  Phase 4 auto-trader needs a cross-process order queue.
- **Multi-model MoE (day one).** The correlated-hallucination
  problem is at the prompt level per session-32 Option B shadow
  analysis; model-mixing multiplies failure modes (different rate
  limits, different prompt-caching semantics, different latency
  tails) without addressing the root cause. Phase 1 fixes the cheap
  cause first.
- **Cloud migration (AWS / GCP / Azure).** Single user, one machine.
  Egress and storage costs don't exist. Reconsider at Phase 4 if
  auto-trading and multi-tenant needs emerge.
- **PostgreSQL migration (now).** Wrong tool for the queryability
  problem. DuckDB covers 100% of Phase 0's analytics needs with 0%
  of Postgres's ops cost. Reconsider if concurrency or data volume
  triggers fire.
- **Microservices decomposition.** The monolith has clean module
  boundaries already (`market/`, `ingestion/`, `agents/`,
  `scoring.py`, `stats.py`, `history.py`); splitting into services
  is a deployment cost without a capability gain. Container the
  monolith first.
- **Full auto-trading on day one.** Split into Tier A (auto-BUY and
  hold, Phase 2a) and Tier B (continuous listener + take-profit,
  Phase 2c). Tier A gates on Phase 1 unified acceptance criteria;
  Tier B gates on Phase 2b MFE verdict = "chase." Neither ships
  before its respective gate; shipping Tier A before Phase 1
  passes is the fastest path from advisor to auto-loss generator.

---

## 11. Open questions flagged during the assessment

These are the unanswered questions that Phase 0/1/2 are designed to
answer. Do **not** treat them as already answered.

1. **Where does the "68% leaning" number come from exactly, and
   what is its distribution across predicted-probability buckets?**
   Phase 0 Task 0.3.
2. **What is the MFE distribution on historical BUYs? Is MFE
   capture positive-EV after Polymarket fees + slippage on
   held-out data?** Phase 2b Task 2b.1 (with chronological
   train/test promoted from risk note to task requirement per
   external review v2 §5.3).
3. **How much does the current Option B mirror-collapse cost us
   specifically — small drift or large?** Phase 1 measures this;
   if the fix doesn't move the needle meaningfully, Option B parks
   as descriptive-only.

---

## 12. Related documents

- `CTO_Agent.md` — reviewer posture; updated in this session with
  CLV correction + prediction-vs-execution-edge framing.
- `docs/roadmap.md` — theme-organized catalog of work items.
  Phases reference specific theme items.
- `docs/phases/README.md` — phase plan index + dependency graph.
- `docs/session-log.md` — session-by-session code changelog.
- `docs/option_b_migration_plan.md` — authoritative rollout for
  per-side probability; Phase 1 unblocks step 5 (backtest).
- `docs/known-issues.md` — API quirks and environment notes.
