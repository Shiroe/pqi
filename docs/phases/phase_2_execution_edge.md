# Phase 2: Execution Edge Prototype

**Status:** blocked on Phase 0
**Estimated effort:** 3–6 weeks (can overlap with Phase 1 post-P0)
**Owner:** TBD
**Last updated:** 2026-04-18

---

## Why this phase exists

Polymarket has **no native stop-loss, take-profit, or bracket
orders.** Even if the council's prediction edge is marginal,
exit-discipline alpha on favorable mid-event excursions is its own
source of edge. This phase:

1. Measures whether MFE (max favorable excursion) on historical
   BUYs is large enough to be worth capturing.
2. Prototypes a continuous listener that alerts on MFE thresholds
   for open positions.
3. Runs a manual review loop (user decides whether to take profit
   on each alert) before any auto-sell.
4. Decides whether to promote to auto-execution (Theme B items
   B.3–B.6 become active).

**Important framing:** this phase is about **execution edge**,
which is **orthogonal to prediction edge**. Phase 2 can pay off even
if Phase 0 shows weak prediction edge, because MFE capture is about
exiting overshoots regardless of whether we're right about the
final outcome. See `docs/scaling_assessment.md` §6.

---

## Entry criteria

- [ ] Phase 0 complete (CLV + analytics infrastructure needed for
      MFE analysis; CLOB history fetch is reusable but DuckDB is
      what makes aggregation tractable)
- [ ] Decision made: is auto-trading in scope within the next 6
      months? Phase 2 prototype stops at manual review by design;
      promotion to auto-execution requires Theme B full-stack work
      (auth, safeguards, risk controls). If auto-trading is
      out-of-scope, Phase 2 still produces a useful
      manual-alert system.

---

## Goals (measurable)

1. **MFE distribution known** on historical BUYs: mean, median,
   stddev, by category, by price band, by time-to-resolution.
2. **Positive-EV threshold identified** — at what TP threshold does
   "take profit if MFE crosses X" beat hold-to-resolution on the
   historical set, *after* Polymarket fees + slippage.
3. **Listener prototype operational** — continuously monitors open
   positions and emits alerts within N minutes of TP threshold
   crossing, no trade execution.
4. **Manual review log** — at least 5 decisions logged with
   outcomes.

---

## Tasks

### Task 2.1 — Retroactive MFE analysis

**What:** For each resolved BUY in history, query CLOB full price
history between entry and resolution, compute MFE and time-of-MFE.

**Why:** Before building a listener, know whether MFE is big enough
to chase. If the median BUY has MFE < 3pp (below fee cost), a
listener is pointless.

**Implementation:**

- Script: `scripts/mfe_analysis.py`
- For each resolved BUY:
  - Query CLOB history for the bought token between entry timestamp
    and event resolution timestamp.
  - Compute `mfe = max(price) - entry_price`
    (= max favorable move).
  - Compute `mfe_time_fraction` (how far through the event the MFE
    occurred — early spike vs late swing has different actionability).
  - Persist to `~/.pqi/mfe.jsonl`.
- Aggregate analysis via DuckDB:
  - MFE histogram
  - MFE by category
  - MFE by price band
  - Correlation: does MFE predict final hit? (If MFE is high, does
    the bet usually resolve YES?)
  - **Simulated take-profit P&L** at thresholds `{2, 3, 5, 7, 10}`pp
    vs hold-to-resolution baseline, after 2% round-trip fees.

**Acceptance criteria:**

- [ ] MFE computed for all resolved BUYs with CLOB coverage
- [ ] Simulated take-profit table published showing
      `realized_p&l` at each threshold vs baseline
- [ ] Clear verdict: is there a TP threshold that beats
      hold-to-resolution by ≥5% after fees?

**Files touched:**

- New: `scripts/mfe_analysis.py`
- New runtime file: `~/.pqi/mfe.jsonl`
- New: `docs/retrospectives/phase_2_mfe_analysis_YYYY_MM.md`

**Risks:**

- CLOB hourly resolution may miss sharp intra-hour excursions.
  Actual realtime MFE may be *higher* than retroactive estimate.
  Document the bias — the simulated P&L is a lower bound on
  achievable execution edge.
- **Polymarket fees:** ~2% maker/taker spread on exits. Include in
  simulated P&L; a 4pp MFE threshold with 2% fees = 2pp realized.
- **Slippage on realistic order sizes.** Kelly-sized bets on a
  small bankroll → small orders → low slippage. Document the
  bankroll assumption. Revisit slippage only if Phase 4 triggers
  multi-user / larger positions.

---

### Task 2.2 — Read-only listener prototype

**What:** Background Python process that monitors open BUY
positions and emits local alerts when MFE threshold is crossed.
**Does not execute any trades.**

**Why:** Validates the listener pipeline end-to-end with zero
execution risk. Establishes baseline for "can we actually catch
these alerts in real-time?"

**Implementation:**

- Script: `scripts/mfe_listener.py` (standalone — not integrated
  into the monolith yet)
- Input: `~/.pqi/open_positions.jsonl`, a list of held BUYs (manually
  populated from `history.jsonl` rows initially; eventual Phase 3
  work can auto-populate)
- Loop: every `LISTENER_POLL_INTERVAL` seconds (default 300):
  - For each open position, fetch current CLOB price for the held
    token
  - Compare to threshold (absolute TP price or MFE-based)
  - If crossed, emit alert via:
    - Local notification (macOS `osascript`, Linux `notify-send`)
    - Append to `~/.pqi/alerts.jsonl` (audit log)
    - Optional: Slack webhook if `SLACK_WEBHOOK_URL` set
  - Respect Polymarket rate limits (back off on 429)
- **Kill switch:** `Ctrl+C` cleanly stops. Process does **not**
  auto-resume on reboot in the prototype — manual restart only.
- **Graceful failures:** if a position's token ID is invalid or
  the event has already resolved, log and skip (don't crash).

**Acceptance criteria:**

- [ ] Listener runs continuously for 1 week with no crashes
- [ ] Catches at least 1 real alert (requires holding a BUY with
      `event_start_time` in that week)
- [ ] Alert latency <10 min from price crossing threshold
- [ ] Graceful error handling on CLOB 429 / 5xx / stale tokens

**Files touched:**

- New: `scripts/mfe_listener.py`
- `pqi/config.py` — add `LISTENER_POLL_INTERVAL`, `SLACK_WEBHOOK_URL`
- `.env.example` — document new keys

**Risks:**

- **Desktop notifications may not survive screen-lock / sleep.**
  Plan for this: Slack fallback for any critical alerts.
- **Listener dies silently if terminal closes.** Manual-ops
  problem, not blocker — document clearly. Phase 3 containerization
  solves this properly; Phase 2 prototype tolerates it.
- **Position-list maintenance is manual in prototype.** State
  management is the main operational weakness. Phase 3 can move
  the position list to a shared volume; for now, document a clear
  "how to add/remove a position from the list" runbook.

---

### Task 2.3 — Manual review loop

**What:** User reviews alerts, decides whether to take profit
manually on Polymarket, logs decision + outcome.

**Why:** Before automating the sell leg, measure whether the user
agrees with the alerts **and** whether "take profit now" beats
"hold" on a small real sample. This is where execution-edge theory
meets user practice.

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
  realized P&L vs the `hold` counterfactual?

**Acceptance criteria:**

- [ ] At least 5 decisions logged
- [ ] Each decision has execution outcome (if taken) and final
      resolution
- [ ] Comparison published: took-profit realized P&L vs
      would-have-held P&L

---

### Task 2.4 — Retrospective & decision

**What:** Synthesize Tasks 2.1–2.3 into a retrospective. Decide:
automate or park.

**Implementation:**

- Write `docs/retrospectives/phase_2_execution_edge_YYYY_MM.md`
- Required sections:
  - **MFE distribution summary** (from 2.1)
  - **Listener operational reliability** (from 2.2 — uptime, alert
    latency, missed alerts, false positives)
  - **Manual-review P&L** (from 2.3 — took vs held delta)
  - **Recommendation:** one of three
    1. **Automate** — proceed to Theme B B.3–B.6 (auth, CLOB trade
       client, Kelly-sized buy, watcher stop-loss)
    2. **Keep prototype manual** — listener is useful as-is as a
       manual-alert system; no automation investment
    3. **Park** — MFE too small, decisions unprofitable, or
       reliability too poor; shut down listener and focus on
       prediction edge (Phase 1 / Theme A)

**Acceptance criteria:**

- [ ] Retrospective published with explicit verdict
- [ ] `docs/phases/README.md` and `CLAUDE.md` updated

---

## Exit criteria

All of:

- [ ] MFE analysis complete with verdict on positive-EV threshold
- [ ] Listener prototype operational OR documented reason why not
- [ ] At least 5 manual-review decisions logged
- [ ] Retrospective written with automate/manual/park verdict

---

## Validation: "are we done?"

1. Do I know the MFE distribution on historical BUYs? (Yes →
   2.1 passed)
2. Did the listener catch at least one real alert? (Yes → 2.2
   passed)
3. Are there ≥5 logged decisions with outcomes? (Yes → 2.3
   passed)
4. Is there a written verdict on automation? (Yes → 2.4 passed)

---

## Risks / decision points

- **MFE is noise, not signal.** Short-horizon price moves in
  prediction markets can be liquidity events or sentiment flickers
  that reverse before resolution. Phase 2's whole point is to
  measure whether MFE is *predictive* of favorable exit. If
  not — park.
- **Listener downtime during critical moments.** A prototype has
  no reliability guarantees. If this phase promotes to
  auto-execution (Theme B), reliability becomes a hard requirement;
  until then, accept missed alerts as a known limitation.
- **Polymarket fee drag.** ~2% round-trip. Simulated P&L must
  subtract this. A 4pp MFE with 2% fees = 2pp realized — tight
  margin.
- **Phase 2 and prediction edge interaction.** If Phase 0 shows no
  prediction edge, the council's BUY picks themselves are -EV,
  and MFE capture on -EV entries is "losing less." That's still
  improvement, but it's not a business — it's a drag reduction.
  Phase 1 remains necessary to produce real alpha. Flag this in
  the retrospective.
- **MFE-correlated-with-final-hit gotcha.** If high-MFE bets *also*
  tend to resolve YES more often than low-MFE bets, taking profit
  early gives up some of the hit-rate. The simulated P&L in 2.1
  must account for this — compare *net* P&L, not just excursion
  capture.

---

## Related work

### Roadmap themes

- **Theme B.6** (match watcher with time-based stop-loss) — Phase 2
  does the measurement + prototype; Theme B.6 does the full
  auto-execution stack (if Phase 2 recommends automate).
- **Theme B.3–B.5** (Polymarket auth, read client, Kelly-sized buy)
  — activates only if Phase 2 recommends automate.

### Files referenced

- `pqi/market/clob.py` — CLOB history fetch, reusable for MFE
- `pqi/history.py` — open-position identification
- `pqi/config.py` — new knobs for listener
- `requirements.txt` — `py-clob-client` already present (unused
  until Theme B.3+)

### Session logs

- Session 22 — Kelly sizing (relevant for TP order sizing in
  future auto-execution)

---

## Session state

**Last work session:** *not yet started (blocked on Phase 0)*

**Done:** *nothing yet*

**In-progress:** *nothing*

**Next action:** Wait for Phase 0 completion. First task on resume
is Task 2.1 — MFE analysis script. Do not start Task 2.2 until 2.1
says MFE is worth chasing.

**Notes for next session:**

- CLOB history fetch is already reusable from `pqi/market/clob.py`
  — don't rewrite. Check the interface: it returns a
  `SidePriceHistory` today; for MFE analysis, we need raw hourly
  data points, which are consumed internally but may not be
  exposed — may need a tiny helper function.
- Open-position tracking needs design — currently `history.jsonl`
  has `recommendation` but no `position_status` (open/closed). May
  need to either add a field or infer from `end_date` for
  prototype purposes.
- If Phase 0's verdict is "no edge," re-evaluate whether Phase 2 is
  still worth pursuing. Execution edge on -EV entries is drag
  reduction, not business.
