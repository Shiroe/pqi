# Phase 4: Conditional Scaling (Trigger-Driven)

**Status:** reactive — activates only when triggers fire
**Estimated effort:** variable per trigger
**Owner:** TBD (assigned when a trigger fires)
**Last updated:** 2026-04-18

---

## Why this phase exists

Some infrastructure work is justified *eventually* but not *now*.
Phase 4 is the holding pattern: each sub-item is a potential future
workstream, paired with the **measured trigger** that would activate
it. **No speculative implementation work happens in Phase 4 — only
the work that has been triggered by an observed signal.**

## Principle

From `docs/scaling_assessment.md` and `CTO_Agent.md` §3:

> A migration is justified by a *measured* bottleneck. Name the
> metric, the threshold, and the monitoring in place to detect it.
> Absent that, the migration is speculative rearrangement.

Phase 4 formalizes this. Every item below has:

- A **trigger** (what must be true for this item to activate)
- A **response** (the scope of work once triggered)
- A **non-goal** (what we explicitly will NOT do even if the
  trigger fires)

If a triggered item is promoted to active work, create
`docs/phases/phase_4_N_<name>.md` using the standard phase doc
template.

---

## Deferred items

### 4.1 PostgreSQL migration

**Trigger:** Any of:

- `history.jsonl` grows past 10,000 rows AND DuckDB queries take
  >5 s on common aggregations (measurable via Phase 0 analytics)
- Genuinely concurrent writers appear (e.g. Theme B.2 parallel
  analysis workers writing to the same history file)
- Multi-tenant: more than one user / process writing state

**Response:** Migrate **rollups**, not the raw event log. Keep
`history.jsonl` as the source of truth; Postgres becomes a derived
queryable view for large-scale aggregations.

**Non-goal:** Postgres + JSONB as a replacement for the jsonl. The
jsonl is a cheap, auditable event log; Postgres is the analytical
dimension. Don't collapse them.

---

### 4.2 LiteLLM or equivalent LLM gateway

**Trigger:** Any of:

- Multi-model experiment (a post-Phase-1 follow-up, explicitly
  triggered) shows diversity gain that justifies >1 provider in
  production
- Multi-process producers all hitting a shared Anthropic rate
  limit AND the in-process retry (session 30) can't keep up —
  evidence would be 429/529 error rate spiking with process count

**Response:** Centralize LLM calls through a proxy service with
per-key rate-limit budgeting, unified retry, cost tracking. LiteLLM
is the default choice but not a requirement.

**Non-goal:** LiteLLM as a provider-abstraction layer when we only
use one provider. The existing `pqi/llm/client.py` abstraction is
already adequate; adding LiteLLM as a dependency without a second
provider is complexity without benefit.

---

### 4.3 Redis message broker

**Trigger:** Any of:

- Phase 2 promotes to auto-execution and a separate executor
  service needs an asynchronous order queue from the analyze
  pipeline
- Theme B.2 parallel workers (>3 concurrent) need a coordinator
  that file-locks are too weak for (symptoms: lost writes, race
  conditions in history append)

**Response:** Introduce Redis as a task queue. Start with the
single-producer, single-consumer pattern; grow to pub/sub only if
the workload demands it.

**Non-goal:** Redis as a cache layer. DuckDB + in-memory Python
dicts cover our cache needs; adding Redis for caching is a
different product.

---

### 4.4 Microservices decomposition

**Trigger:** Any of:

- A scraper service needs IP stealth or a different network context
  that the monolith container can't provide (e.g.
  residential-proxy requirement for anti-bot evasion)
- A component's failure mode starts cascading into the rest of
  the pipeline despite in-process isolation attempts
- Deployment targets legitimately diverge (e.g. analyze pipeline
  on one VPS, listener on another for redundancy)

**Response:** Extract **the one** component that has the triggering
requirement. Keep everything else in the monolith. No blanket
decomposition.

**Non-goal:** "Service-per-agent" architectures. The 7-agent
council runs in-process with `asyncio.gather` and should stay that
way — agent parallelism is an asyncio concern, not a service
boundary.

---

### 4.5 Cloud migration (AWS / GCP / Azure)

**Trigger:** Any of:

- Multi-region redundancy required (listener uptime SLA tightens to
  a number a small VPS can't meet)
- Storage / compute exceeds what a small VPS can handle (e.g.
  months of continuous CLOB streaming fills the disk)
- Multi-user SaaS pivot (this project becomes a product)

**Response:** Single-cloud first (whichever the user has
credits/familiarity with). The Phase 3 container is already
portable; migration is "same container, different host."

**Non-goal:** Cloud-native rewrites (serverless, Lambda, Cloud
Run). Stick with containers; containers are portable and run on
any compute substrate.

---

### 4.6 Event-driven ingestion (WebSockets)

**Trigger:** Any of:

- Phase 2 MFE analysis shows measurable PnL lost specifically due
  to 15-min or 5-min polling granularity (evidence: many MFE
  events happen intra-poll-window and revert before next poll)
- Category-specific need — e.g. in-play sports betting where
  prices move in seconds and polling can't keep up

**Response:** Add a WebSocket listener **for the one category with
measured value**, not globally. Polymarket CLOB supports
WebSockets (`py-clob-client` has the plumbing).

**Non-goal:** WebSocket ingestion as the default pattern. Polling
is simpler, more debuggable, and adequate for most markets.
Websocket is a scalpel, not a shotgun.

---

### 4.7 Apache Airflow / Prefect

**Trigger:** Only if the number of independent workflow DAGs grows
past ~5 (e.g. we have analyze + listener + backup + monitor +
backtest + … and each has dependencies on others) AND the
dependency graph genuinely needs a scheduler.

**Response:** If this trigger fires, re-evaluate — it most likely
means the project has grown beyond solo-operator scope, and the
decision has wider implications.

**Non-goal:** Using Airflow for a single `analyze` workflow. Cron
covers that case.

---

## Observability for trigger detection

Phase 4 items need someone watching for their triggers. To not
miss them, the following metrics should be periodically checked
(at end of each sprint or monthly):

| Trigger | Metric to watch | Where |
| --- | --- | --- |
| 4.1 Postgres | `history.jsonl` row count + DuckDB query time | Phase 0 analytics `pqi query` |
| 4.2 LLM gateway | Anthropic monthly spend vs $20 budget; 429/529 rate | Anthropic console; client logs |
| 4.3 Redis | Concurrent-write collision rate; executor-queue backlog | Phase 2 listener logs; post-promotion only |
| 4.4 Microservices | Cascading-failure incidents; IP-stealth asks | Phase 3 deployment logs |
| 4.5 Cloud | Disk/CPU utilization on VPS | `htop` on VPS |
| 4.6 WebSockets | MFE intra-poll-window miss count | Phase 2 retrospective |

If a trigger metric crosses its threshold, note it in
`docs/session-log.md` so the next session can decide whether to
promote the item to an active workstream.

---

## Session state

**Last work session:** *not applicable — phase is reactive*

**Active triggers:** *none as of 2026-04-18*

**Monitored thresholds:** documented above; not yet formalized as
automated checks. Phase 0 analytics will make 4.1's metric easy
to watch.

**Notes:** This phase is a holding pen. Do not implement anything
here without a concrete trigger firing. If a trigger does fire,
promote the specific item to `docs/phases/phase_4_N_<name>.md`
using the standard phase doc template, and move it out of this
deferred list.
