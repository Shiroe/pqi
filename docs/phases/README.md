# Phase Plans — Index

The phase plans document the strategic execution order for PQI's
evolution. Each phase is a self-contained workstream with entry
criteria, goals, tasks, and exit criteria — designed so work can be
paused and resumed without losing context.

**Context:** These phases came out of the `docs/scaling_assessment.md`
recalibration of the external v2.0 RFC. They're the **execution
layer**; `docs/roadmap.md` is the **catalog** of individual work
items (Themes A/B/C) that the phases draw from.

---

## Current status

| Phase | Title | Status | Estimated effort |
| --- | --- | --- | --- |
| 0 | Edge Validation via Analytics Infrastructure | **done (verdict: inconclusive at current n; see retrospective)** | 1–2 weeks (actual: sessions 34–38) |
| 1 | Signal Quality Fix (Option B Autonomy) | **next — highest-leverage lever per Phase 0 verdict** | 2–4 weeks |
| 2 | Execution Edge Prototype | scoping unblocked (MFE analysis in parallel with P1 ok); listener/auto-trader still deferred pending P1 Brier improvement | 3–6 weeks |
| 3 | Deployment Portability | deferred — P0 verdict was inconclusive, not positive; revisit after P1 | 3–5 days |
| 4 | Conditional Scaling | trigger-driven, indefinite | — |

Update this table whenever a phase's status changes, and also
update `CLAUDE.md` §Current State so session handoff stays coherent.

**Phase 0 retrospective:**
[`docs/retrospectives/phase_0_edge_2026_04.md`](../retrospectives/phase_0_edge_2026_04.md).
Verdict summary: mild positive prediction edge (council Brier
0.2176 beats 0.25 chance baseline), mild negative entry timing
(CLV −0.0167 on n=44), no actionable gate miscalibration. 5 of
7 agents are correlated at r ≥ 0.85 internally (cluster-level
mirror collapse, not uniform), Bear has Brier above chance,
Bull is overconfident on favourites. Phase 1 is the primary
next action; gate knobs are explicitly *not* retuned.

---

## How to use these plans

1. **Read the phase plan end-to-end before starting work in it.**
   Each plan's "Why this phase exists" section frames the
   trade-off; entry/exit criteria define the contract.
2. **Check entry criteria.** Don't start a phase before them —
   skipping is how priorities drift.
3. **Work task-by-task.** Each task has acceptance criteria — that's
   how you know you can move on to the next task.
4. **Update the "Session state" section** at the end of each work
   session so the next session can pick up cleanly.
5. **Exit criteria are the gate for moving to the next phase.** If
   they can't be met, write a retrospective documenting what you
   learned and decide: iterate or park.

---

## Dependency graph

```
Phase 0 ──────────────► Phase 1  (can parallelize after P0 starts)
   │
   ├───────────► Phase 2 ───────► Phase 3  (execution-edge path)
   │
   └─────────────────────────────► Phase 3  (advisor-only deployment; skips P2)

Phase 4 ← triggered reactively by 0/1/2/3 hitting scale thresholds
```

**Parallelism notes:**

- **P0 + P1 in parallel:** they touch different files (P0 =
  analytics/queries, P1 = `personas.py`). P0 gives P1 a sharper
  before/after baseline but is not a hard blocker.
- **P2 requires P0:** MFE analysis is a DuckDB + CLOB-history
  query — Phase 0's infrastructure is the foundation.
- **P3 can run after P0 even if P2 parks:** a VPS-deployed advisor
  is useful without the execution-edge listener.

---

## Anti-goals (explicit rejections from the scaling assessment)

From `docs/scaling_assessment.md` §10. If a new proposal resurfaces
one of these without a Phase 4 trigger firing, that's a Technical
Debt Alert per `CTO_Agent.md` §7.

- Microservices decomposition
- PostgreSQL migration (DuckDB covers the real problem)
- Redis message broker (`asyncio.gather` suffices in-process)
- Apache Airflow / Prefect
- Multi-model MoE as a day-one change (Phase 1 fixes prompts first)
- Cloud migration (AWS / GCP / Azure) for a single user
- Auto-trading before MFE validation

---

## Relationship to `docs/roadmap.md` themes

- **Theme A (Modeling & Calibration)** → most items are consumed
  by Phases 0 and 1. A.1/A.7/A.8 sharpen after Phase 0's
  reliability diagram; A.9 (dynamic weights) is already live and
  feeds into Phase 0's DuckDB queries.
- **Theme B (TUI + Trading)** → Phase 2 does the measurement
  subset (MFE analysis + read-only listener); full Theme B
  (B.3–B.6: auth, CLOB trade integration, Kelly buy, match
  watcher) activates only after Phase 2 promotes.
- **Theme C (Operational Tooling)** → partially subsumed by
  Phase 0's DuckDB wire-up (several C items become trivial once
  SQL is available: C.4 per-category slicing, C.5 postmortem
  persistence). Remaining C items (C.1 cache TTL, C.6 concurrent-
  write lock) are independent quality-of-life picks.

---

## Template for new phases

When a Phase 4 trigger fires and promotes an item to its own phase,
create `docs/phases/phase_4_N_<name>.md` using the same structure
as existing phase docs:

1. Status + effort + owner + last-updated
2. Why this phase exists
3. Entry criteria
4. Goals (measurable)
5. Tasks (each with acceptance criteria + files touched + risks)
6. Exit criteria
7. Validation: "are we done?"
8. Risks / decision points
9. Related work
10. Session state (updated every work session)
