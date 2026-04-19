# Phase 2c: MFE Listener Prototype

**Status:** deferred — gated on Phase 2b verdict = "chase"
**Estimated effort:** 2–4 weeks once unblocked
**Owner:** TBD
**Last updated:** 2026-04-19 (new phase split from prior `phase_2_execution_edge.md` Tasks 2.2–2.4 per external review v2 §7.6 / §6.4 renumbering; explicit chase/park gate from Phase 2b)

---

## Why this phase exists

If Phase 2b (MFE analysis) finds that take-profit above fee drag is
realistically capturable on held-out data, the next question is
whether a continuous listener can actually *catch* those
excursions in real time. This phase prototypes the read-only
listener + manual review loop, and decides whether to automate the
sell leg (Tier B) or park the experiment.

**Scope separation (new in the 2a / 2b / 2c split):**

- **Tier A auto-BUY** (Phase 2a) automates entry only; holds to
  resolution; independent of this phase.
- **Tier B auto-sell** (downstream of this phase) depends on 2c's
  manual-review P&L showing that take-profit decisions actually
  beat hold-to-resolution for the operator.
- **This phase does not execute trades.** Read-only listener +
  manual review + verdict. Sell-side automation is a separate
  retrospective decision made at the end of this phase.

---

## Entry criteria

- [ ] **Phase 2b verdict = chase** (test-window net P&L ≥ +5%
      over hold-to-resolution after fees on the held-out 30%).
      If 2b verdicts "park" or "iterate," this phase stays
      deferred.
- [ ] Phase 1 status is immaterial to 2c directly, but in practice
      2c only matters if Tier A is shipping or under evaluation —
      running a listener on a manual-advisor-only workflow is
      possible but adds little over the existing CLI.

---

## Goals (measurable)

1. **Listener prototype operational** — continuously monitors open
   positions and emits alerts within ~10 min of TP threshold
   crossing. No trade execution.
2. **Manual review log** — at least 5 TP decisions logged with
   outcomes (taken/held/partial; execution price; final
   resolution).
3. **Retrospective verdict** — automate the sell leg (proceed to
   Theme B.3–B.6 / Tier B auto-sell), keep the prototype as a
   manual-alert system, or park.

---

## Tasks

### Task 2c.1 — Read-only listener prototype

**What:** Background Python process that monitors open BUY
positions and emits local alerts when MFE threshold is crossed.
**Does not execute any trades.**

**Why:** Validates the listener pipeline end-to-end with zero
execution risk. Establishes baseline for "can we actually catch
these alerts in real-time?" Reliability numbers here inform
whether automation in Tier B is viable at all.

**Implementation:**

- Script: `scripts/mfe_listener.py` (standalone — not integrated
  into the monolith yet).
- Input: `~/.pqi/open_positions.jsonl`, a list of held BUYs
  (manually populated from `history.jsonl` rows initially; once
  Phase 2a ships, `fills.jsonl` becomes the authoritative source
  and the listener reads it directly).
- Threshold source of truth: the best-in-train TP threshold locked
  by Phase 2b. Do not re-tune at listener runtime; that would
  restart the lookahead-bias problem.
- Loop: every `LISTENER_POLL_INTERVAL` seconds (default 300):
  - For each open position, fetch current CLOB price for the held
    token.
  - Compare to threshold (absolute TP price or MFE-based).
  - If crossed, emit alert via:
    - Local notification (macOS `osascript`, Linux `notify-send`)
    - Append to `~/.pqi/alerts.jsonl` (audit log)
    - Optional: Slack webhook if `SLACK_WEBHOOK_URL` set
  - Respect Polymarket rate limits (back off on 429).
- **Ghost-event handling:** reuse the `probe_event_by_id` helper
  built in Phase 2a Task 2a.3. For Tier B (continuous position
  monitoring) this is *mandatory* — a ghost event with an open
  position would be a blind spot on live risk (external review v2
  §4.3).
- **Kill switch:** `Ctrl+C` cleanly stops. Process does **not**
  auto-resume on reboot in the prototype — manual restart only.
- **Graceful failures:** if a position's token ID is invalid or
  the event has already resolved, log and skip (don't crash).

**Acceptance criteria:**

- [ ] Listener runs continuously for 1 week with no crashes.
- [ ] Catches at least 1 real alert (requires holding a BUY with
      `event_start_time` in that week).
- [ ] Alert latency <10 min from price crossing threshold.
- [ ] Graceful error handling on CLOB 429 / 5xx / stale tokens.
- [ ] Ghost-event probe verified: synthetic test where the list
      endpoint drops the event but the individual probe succeeds
      produces no false "event gone" alert.

**Files touched:**

- New: `scripts/mfe_listener.py`
- `pqi/config.py` — `LISTENER_POLL_INTERVAL`, `SLACK_WEBHOOK_URL`
- `.env.example` — document new keys
- `known-issues.md` — add `open_positions.jsonl` and
  `alerts.jsonl` to the persisted-state list.

**Risks:**

- **Desktop notifications may not survive screen-lock / sleep.**
  Plan for this: Slack fallback for any critical alerts.
- **Listener dies silently if terminal closes.** Manual-ops
  problem, not blocker — document clearly. Phase 3 containerization
  solves this properly; Phase 2c prototype tolerates it.
- **Position-list maintenance is manual in prototype unless
  Phase 2a has shipped.** If Phase 2a shipped, `fills.jsonl` is
  authoritative and `open_positions.jsonl` is a filtered view.
  If Phase 2a has not shipped, the list is hand-maintained and
  stale state is the main operational weakness — document a clear
  runbook.
- **Threshold drift.** If 2b's train window is stale by the time
  2c runs, the locked threshold may no longer be optimal. Prefer
  re-running 2b on the updated full history before starting 2c —
  but the re-run must itself be chronological train/test, not a
  free re-tune.

---

### Task 2c.2 — Manual review loop

**What:** User reviews alerts, decides whether to take profit
manually on Polymarket, logs decision + outcome.

**Why:** Before automating the sell leg, measure whether the user
agrees with the alerts **and** whether "take profit now" beats
"hold" on a small real sample. This is where execution-edge theory
meets user practice — if the operator consistently declines the
alerts, automating them is premature.

**Implementation:**

- On each alert, user records decision in
  `~/.pqi/tp_decisions.jsonl` (hand-maintained for prototype):
  ```json
  {
    "alert_time": "...",
    "market_id": "...",
    "alert_price": 0.58,
    "entry_price": 0.45,
    "decision": "take" | "hold" | "partial",
    "reason": "...",
    "execution_time": "..." (null if held),
    "execution_price": 0.57 (null if held),
    "final_outcome": "..." (filled in on resolution — yes/no/void),
    "resolution_price": 1.00 | 0.00
  }
  ```
- Goal: 5–10 decisions over 2–4 weeks of prototype operation.
- Retrospective computes: for each `take` decision, what was the
  realized P&L vs the `hold` counterfactual on the same row?

**Acceptance criteria:**

- [ ] At least 5 decisions logged.
- [ ] Each decision has execution outcome (if taken) and final
      resolution.
- [ ] Comparison published: took-profit realized P&L vs
      would-have-held P&L, with Wilson CI on the paired-row delta.

---

### Task 2c.3 — Retrospective & Tier B decision

**What:** Synthesize Tasks 2c.1 + 2c.2 into a retrospective.
Decide: automate the sell leg, keep as manual-alert, or park.

**Implementation:**

- Write `docs/retrospectives/phase_2c_listener_YYYY_MM.md`.
- Required sections:
  - **Listener operational reliability** (from 2c.1 — uptime,
    alert latency, missed alerts, false positives, ghost-event
    events).
  - **Manual-review P&L** (from 2c.2 — took vs held delta on
    paired rows with CI).
  - **Recommendation:** one of three
    1. **Automate (Tier B)** — proceed to Theme B.3–B.6 (auth
       reuse from Phase 2a, CLOB sell client, matched-sell
       sizing, stop-loss watcher). Requires: operator-review P&L
       showing "take" beats "hold" with CI bounded away from zero.
    2. **Keep prototype manual** — listener is useful as-is as a
       manual-alert system; no automation investment. Appropriate
       if operator-review P&L is positive but tight, or if
       Polymarket exit-liquidity is thin enough that automation
       risks more than it saves.
    3. **Park** — MFE capture not useful in practice (alerts
       unreliable, operator declines most alerts, or P&L
       negative). Shut down listener and focus on prediction edge
       work (Phase 1 iteration / Theme A).

**Acceptance criteria:**

- [ ] Retrospective published with explicit verdict.
- [ ] `docs/phases/README.md` and `CLAUDE.md` updated.
- [ ] If verdict = automate, a new Phase 2d (or Theme B.3–B.6
      activation note) is written as a follow-up.

---

## Exit criteria

All of:

- [ ] Listener prototype operational OR documented reason why
      not.
- [ ] At least 5 manual-review decisions logged with outcomes.
- [ ] Retrospective written with automate / manual / park verdict
      and Wilson-CI'd paired-row P&L delta.

---

## Validation: "are we done?"

1. Did the listener catch at least one real alert with <10 min
   latency? (Yes → 2c.1 passed.)
2. Are there ≥5 logged decisions with outcomes? (Yes → 2c.2
   passed.)
3. Is there a written verdict on automation bound to the
   test-window P&L delta? (Yes → 2c.3 passed.)

---

## Risks / decision points

- **MFE is noise, not signal on the listener's horizon.** Phase
  2b's verdict is measured retroactively on hourly CLOB data; the
  listener polls at minute-scale on realtime data. Behavior
  between the two may diverge. Flag in the retrospective; do not
  assume 2b's verdict transfers 1:1 without re-validation.
- **Polymarket fee drag.** ~2% round-trip on the sell leg alone
  (entry was already paid separately in Tier A). Simulated P&L
  must subtract this. A 4pp alert threshold with 2% sell-side
  fees = 2pp realized on the exit.
- **Listener downtime during critical moments.** A prototype has
  no reliability guarantees. If this phase promotes to Tier B
  auto-sell, reliability becomes a hard requirement; until then,
  accept missed alerts as a known limitation.
- **Prediction edge interaction.** If Phase 1 has not landed at
  the time of 2c, the council's BUY picks themselves may be
  marginal-EV and MFE capture on marginal-EV entries is "losing
  less." That's still improvement, but it's not a business — it's
  drag reduction. Flag this in the retrospective.
- **Selection bias in the alert set.** The listener only emits
  alerts when price crosses the threshold upward. A full
  evaluation needs to log the *unalerted* positions' trajectories
  too — otherwise the retrospective is conditioning on alert
  firing and can't distinguish "we catch good exits" from "we
  catch all exits equally well."

---

## Related work

### Roadmap themes

- **Theme B.6** (match watcher with time-based stop-loss) —
  downstream of this phase; activates only on verdict = automate.
- **Theme B.3–B.5** (Polymarket auth, read client, Kelly-sized
  buy) — the auth surface ships with Phase 2a; the sell client is
  the marginal new work for Tier B.
- **Theme B.1** (streaming TUI) — 2c.1 is the first real
  always-on process in the codebase; TUI is an adjacent
  deliverable if the listener graduates to long-running status.

### Files referenced

- `pqi/market/clob.py` — realtime price fetch, reusable.
- `pqi/history.py` — open-position identification fallback if
  Phase 2a hasn't shipped.
- `pqi/market/polymarket.py` — ghost-event probe helper (shared
  with Phase 2a).
- `pqi/config.py` — new listener knobs.
- `requirements.txt` — `py-clob-client` already present (used by
  Phase 2a first; reused here read-only).

### Session logs

- (To be populated as sessions begin work on 2c.)

---

## Session state

**Last work session:** *not yet started (blocked on Phase 2b
verdict = chase)*

**Done:** *nothing yet*

**In-progress:** *nothing*

**Next action:** Wait for Phase 2b retrospective. On resume:

- If 2b verdict = chase, start with 2c.1 (read-only listener).
- If 2b verdict = park, this phase is parked — annotate status
  in [`README.md`](README.md) and move on.
- If 2b verdict = iterate, defer 2c until 2b re-runs after more
  resolved BUYs accumulate. Do not start the listener on an
  iterate-verdict threshold; it would pre-commit the threshold
  to a path the held-out data hasn't yet validated.

**Notes for next session:**

- Position tracking should read `~/.pqi/fills.jsonl` when Phase 2a
  has shipped. Before that, hand-maintain
  `~/.pqi/open_positions.jsonl` and document the runbook in the
  retrospective.
- Ghost-event probe is shared with Phase 2a — do not re-implement;
  import the helper.
- Threshold is sourced from Phase 2b's retrospective (the
  best-in-train locked value). If the retrospective doesn't
  surface a single locked number, fix that in 2b before starting
  2c — the listener is not where threshold selection happens.
