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
| 2a | Tier A Auto-BUY (auto-BUY-and-hold) | blocked on Phase 1 four unified acceptance criteria | ~1 week, 200–400 LoC + tests |
| 2b | MFE Analysis (retroactive + chronological train/test) | MFE scoping unblocked; parallel-able with P1 | 1–2 weeks |
| 2c | MFE Listener Prototype (Tier B precursor) | deferred — gated on Phase 2b verdict = "chase" | 2–4 weeks |
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
Phase 0 ──────────────► Phase 1 ─────────► Phase 2a  (Tier A auto-BUY)
   │                                          │
   ├────────────────► Phase 2b ───► Phase 2c ─┘  (chase verdict → listener)
   │                                          │
   └──────────────────────────────────────────┴─► Phase 3  (deployment portability)

Phase 4 ← triggered reactively by 0/1/2a/2b/2c/3 hitting scale thresholds
```

**Parallelism notes:**

- **P0 + P1 in parallel:** they touch different files (P0 =
  analytics/queries, P1 = `personas.py`). P0 gives P1 a sharper
  before/after baseline but is not a hard blocker.
- **P1 + P2b in parallel:** also different files (P1 =
  `personas.py` prompts, P2b = `scripts/mfe_analysis.py` +
  DuckDB views). P2b verdict is valid regardless of whether P1
  has passed.
- **P2a blocks on P1, not P2b:** Tier A auto-BUY depends on the
  signal being trustworthy (Phase 1), not on exit discipline
  (which Tier A skips by design).
- **P2c blocks on P2b:** listener is only worth building if MFE
  analysis says there's capturable opportunity above fee drag on
  held-out data.
- **P3 can run after P0 even if P2b parks:** a VPS-deployed
  advisor (or Tier A auto-BUY) is useful without the MFE
  listener.

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
- Tier A auto-BUY before Phase 1 unified acceptance criteria pass
- Tier B auto-sell before Phase 2b MFE verdict = "chase"

---

## Relationship to `docs/roadmap.md` themes

- **Theme A (Modeling & Calibration)** → most items are consumed
  by Phases 0 and 1. A.1/A.7/A.8 sharpen after Phase 0's
  reliability diagram; A.9 (dynamic weights) is already live and
  feeds into Phase 0's DuckDB queries.
- **Theme B (TUI + Trading)** → split across the 2a/2b/2c phases.
  Phase 2a ships the auto-BUY delivery vehicle for B.3–B.5
  (auth, CLOB read/trade client, Kelly buy). Phase 2b does pure
  retrospective measurement. Phase 2c does the read-only listener
  and manual review loop; the Tier B sell-side stack (B.6 match
  watcher + full stop-loss automation) activates only after 2c's
  retrospective verdicts "automate."
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
