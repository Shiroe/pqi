# Phase 2a: Tier A Auto-BUY (Auto-BUY-and-hold)

**Status:** blocked on Phase 1 (four unified acceptance criteria must pass before Tier A ships)
**Estimated effort:** ~1 week, ≈200–400 LoC plus tests
**Owner:** TBD
**Last updated:** 2026-04-19 (new phase split from prior `phase_2_execution_edge.md` per external review v2 §7.4 and owner clarification on auto-trading intent; renumbered as Phase 2a)

---

## Why this phase exists

Owner clarified in external review v2 that the target auto-trading
mode is **"auto-BUY on actionable → hold to resolution"** — *not*
the continuous listener / take-profit mode. These have very
different carrying costs, different dependencies, and different
risk surfaces. This phase is the auto-BUY half only.

**Tier A** (this phase) makes **entry** decisions automatically,
then **holds** to resolution with no exit path. It does not need
MFE analysis. It does need the council's prediction signal to be
trustworthy — i.e. Phase 1's four unified acceptance criteria must
pass first.

**Tier B** (Phase 2c, gated on Phase 2b MFE verdict) makes **exit**
decisions via a continuous listener. It is a separate workstream.

**Why this split matters:** shipping Tier A inside the old
"execution edge" bundle meant it was implicitly gated on
take-profit validation it does not actually need. Split explicitly
so the critical path is clean.

---

## Entry criteria

- [ ] **Phase 1 unified acceptance criteria all pass on the
      post-rewrite reliability diagram** (cluster-pair r ≥ 0.85
      fraction below 0.50 AND per-agent `|drift|` > 0.10 on ≥2
      agents AND Bear Brier moves toward 0.22 without collapsing
      Bear's independence AND council Brier holds or improves from
      0.2176). Cluster-collapsed council outputs turning into
      automatic fills is the single most expensive failure mode
      for Tier A — see external review v2 §5.2.
- [ ] `py-clob-client` already a declared dependency (confirmed in
      current `requirements.txt`, unused pending this phase).
- [ ] Owner decision: default Tier A to off on first ship.
      `--unattended` and `PQI_TIER_A_ENABLED` should both be
      required before any autonomous fill fires.

---

## Goals (measurable)

1. **`pqi buy <market_id>` command ships** with Kelly-sized stake,
   pre-trade price recheck, idempotency guard, and structured
   logging to `~/.pqi/fills.jsonl`.
2. **Zero double-fills** on any retriggered analysis of the same
   `(market_id, analysis_run_id)` pair. Idempotency verified via
   integration test that replays the same run.
3. **Ghost-event probing** (§4.3 option (a) from external review
   v2): for any `market_id` in a rolling tracked set that does not
   appear in the current `/events` list response, probe
   individually via `/events/{id}` before deciding the event is
   gone.
4. **Interactive mode default, unattended opt-in.** On a tty,
   prompt 30s with a countdown before firing; non-tty requires
   explicit `--unattended` flag and `PQI_TIER_A_ENABLED=true`.
5. **First 10 auto-BUY fills log cleanly** with no retries,
   mis-sizing, or spread abort escapes.

---

## Tasks

### Task 2a.1 — `pqi buy <market_id>` command

**What:** CLI command that, given a market the current analyze
pipeline would recommend as a BUY, posts a Kelly-sized bid via
`py-clob-client` and logs the fill to `~/.pqi/fills.jsonl`.

**Why:** The narrowest possible Tier A surface area. One market,
one decision, no continuous state. Everything bigger (scout,
queue, reconciler) is Phase 4.

**Implementation:**

- New: `pqi/trading/buyer.py` — thin wrapper around
  `py-clob-client` trade function.
- New CLI: `pqi buy <market_id>` — loads the current analyze
  result (or re-runs analyze on the given market), computes
  Kelly-sized stake from the existing `kelly_stake_scaled` field,
  performs pre-trade price recheck, posts the order.
- **Pre-trade price recheck:** re-fetch the CLOB top-of-book for
  the target token just before posting. If the price has moved
  more than `TIER_A_PRICE_DRIFT_ABORT_PCT` (default 3%) from the
  analyzed price, abort with a clear reason string and log the
  abort to `~/.pqi/fills.jsonl` with `status=aborted_price_drift`.
  Rationale: the Kelly stake and ROI assumptions were priced at
  analyze-time; a spread move invalidates them.
- **Idempotency guard:** hash `(market_id, analysis_run_id)` into
  a deterministic idempotency key. Before posting, check
  `~/.pqi/fills.jsonl` for any prior fill with the same key. If
  present, refuse the fill with `status=idempotency_skip`.
- **Structured logging:** append to `~/.pqi/fills.jsonl` (atomic
  tmp+rename per `known-issues.md` persisted-state contract) with
  fields: timestamp, market_id, analysis_run_id, idempotency_key,
  analyzed_price, posted_price, stake_shares, stake_usdc,
  status (`filled`|`aborted_price_drift`|`idempotency_skip`|
  `rejected_by_clob`), broker_response_id, error (nullable).

**Acceptance criteria:**

- [ ] `pqi buy <market_id>` runs end-to-end in a test mode that
      hits Polymarket staging (or uses a recorded-fixture mock)
      without touching live funds.
- [ ] Idempotency verified: second invocation with same
      `(market_id, analysis_run_id)` logs `idempotency_skip`, does
      not hit the CLOB.
- [ ] Pre-trade drift abort verified: synthetic price perturbation
      > `TIER_A_PRICE_DRIFT_ABORT_PCT` logs
      `aborted_price_drift`, does not fill.

**Files touched:**

- New: `pqi/trading/buyer.py`
- New CLI entry in `pqi/main.py`
- New persisted state: `~/.pqi/fills.jsonl`
- `pqi/config.py` — `TIER_A_PRICE_DRIFT_ABORT_PCT` (default 0.03),
  `PQI_TIER_A_ENABLED` (default false)
- `.env.example` — document both knobs
- `known-issues.md` — add `fills.jsonl` to persisted-state list

### Task 2a.2 — Interactive confirmation + unattended mode

**What:** Gate the fill behind a 30s interactive countdown on tty,
require explicit flag + env var on non-tty.

**Why:** First-line safety. A human should see every first-month
fill before it posts. Unattended mode is an explicit opt-in, not
a default.

**Implementation:**

- Before posting: check `sys.stdin.isatty()`.
- If tty: print summary (market, stake, analyzed price, current
  price, ROI band), countdown 30s with `rich`. Any keypress during
  countdown aborts with `status=user_aborted`. Countdown expiry
  proceeds to post.
- If non-tty: require both `--unattended` flag on the command AND
  `PQI_TIER_A_ENABLED=true` in env. If either is missing, abort
  with `status=unattended_refused` and clear error.
- Log every abort path (including user_aborted,
  unattended_refused) to `~/.pqi/fills.jsonl` for auditability.

**Acceptance criteria:**

- [ ] tty invocation shows countdown and aborts on keypress.
- [ ] Non-tty without `--unattended` or env aborts cleanly.
- [ ] Non-tty with both flags proceeds to post after the drift +
      idempotency checks.

### Task 2a.3 — Ghost-event ID-based probing

**What:** For any market the user tracks (passed via command,
stored in a small rolling file, or surfaced from recent
`analyze` runs), if the market does not appear in a current
`/events` list response, probe via `/events/{id}` individually
before deciding the event is gone.

**Why:** `known-issues.md` documents Gamma's intermittent event
dropout from list endpoints. For Tier A specifically, this is
acceptable with documentation (occasional missed opportunities).
For continuous scout / Tier B monitoring it would be mandatory.
This task builds the helper both paths can reuse.

**Implementation:**

- New helper in `pqi/market/polymarket.py`: `probe_event_by_id(id)`
  using the existing `/events/{id}` call. Return `None` if the
  individual fetch also fails (true gone).
- Wire into `pqi buy` path: if the given `market_id` is not in the
  recent list-response cache, try `probe_event_by_id` once before
  posting. If that also returns `None`, abort with
  `status=event_missing`.

**Acceptance criteria:**

- [ ] Synthetic test: list endpoint returns empty for a known-good
      event id, individual probe succeeds, buy proceeds.
- [ ] Synthetic test: both list and individual probe fail, buy
      aborts with `status=event_missing`.

### Task 2a.4 — First-10-fills checklist

**What:** Manual operator gate. First 10 Tier A fills are reviewed
by hand before `--unattended` is considered acceptable on a given
market tier.

**Why:** Even with all checks, live trading has surprise modes
(broker rejects, partial fills, slippage beyond tolerance). The
first 10 fills are the canary; anomalies here are cheap to catch.

**Implementation:**

- Not code — a runbook entry at the bottom of this phase doc
  (below) describing: check each of the first 10 fills against the
  analyzed price, stake size, final resolution. After 10 clean,
  `--unattended` becomes acceptable on the same tier of market;
  re-audit on tier change.

**Acceptance criteria:**

- [ ] 10 fills logged with no anomalies (or anomalies caught and
      fixed before proceeding).
- [ ] Retrospective note appended to
      `docs/retrospectives/phase_2a_tier_a_YYYY_MM.md` covering
      what was observed and whether `--unattended` is approved for
      ongoing use.

---

## Exit criteria

All of:

- [ ] Task 2a.1 ships with tests green and idempotency +
      drift-abort verified.
- [ ] Task 2a.2 interactive + unattended semantics verified.
- [ ] Task 2a.3 ghost-event probing in place; wired into `pqi buy`.
- [ ] First 10 auto-BUY fills reviewed; retrospective written
      with `--unattended` status approved or withheld.

---

## Validation: "are we done?"

1. Does `pqi buy <market_id>` post a Kelly-sized fill with
   idempotency, drift-abort, and structured logging? (Yes →
   2a.1 passed.)
2. Does tty mode prompt and non-tty mode refuse without explicit
   opt-in? (Yes → 2a.2 passed.)
3. Does the buy path survive a ghost-event window? (Yes → 2a.3
   passed.)
4. Has a human operator reviewed the first 10 fills and written
   a retrospective? (Yes → 2a.4 passed.)

---

## Risks / decision points

- **Prediction signal must be trustworthy first.** Tier A
  automates the *entry* — if Phase 1's unified criteria have not
  passed, Tier A is automating fills of a cluster-collapsed
  council with a ~3pp Brier edge half-eaten by CLV. This is the
  single largest risk; shipping Tier A before Phase 1 exits is
  explicitly a trap (external review v2 §5.2).
- **Capacity ceiling.** Per `CTO_Agent.md` §5, Polymarket Tier 2/3
  eSports markets regularly have <$500 depth at best bid/ask.
  Above low-to-mid four figures of bankroll, Kelly-sized stake
  won't fill without eating spread that destroys edge. Tier A must
  log `posted_price` vs `analyzed_price`; any systematic gap
  signals a capacity binding and the abort threshold should
  tighten.
- **Broker auth surface area.** `py-clob-client` needs signer
  credentials. Secrets management lives outside this phase doc —
  use the same env-loaded pattern as existing API keys. Do not
  commit secrets.
- **Fee accounting.** 2% Polymarket round-trip; gas on Polygon.
  Tier A does not exit (holds to resolution), so round-trip is
  effectively one-way cost + resolution settlement. Document
  realistic net in the retrospective.
- **Confirmation escape hatches.** The interactive countdown must
  be robust to signals (Ctrl-C). An abort path that silently
  swallows a SIGINT into a fill would be the worst possible
  behaviour. Test this explicitly.

---

## Related work

- **Phase 1** — blocks this phase. No Tier A fill until the four
  unified acceptance criteria pass. See
  [`phase_1_signal_quality.md`](phase_1_signal_quality.md).
- **Phase 2b** — independent from Tier A. Tier A holds to
  resolution; the exit-side MFE question does not affect entry
  decisions.
- **Phase 2c** — Tier B listener. Depends on Phase 2b verdict =
  chase. Tier A is a prerequisite only in the sense that a Tier B
  listener needs positions to watch, and Tier A is the natural way
  to open them under automation.
- **Theme B.3–B.5** (Polymarket auth, read client, Kelly-sized
  buy) — this phase is the delivery vehicle for those theme items.
- **`CTO_Agent.md` §5** — capacity axis is the third orthogonal
  edge dimension; every bet-size decision here should respect it.

### Files referenced

- `pqi/market/clob.py` — top-of-book price recheck.
- `pqi/market/polymarket.py` — event probe helper (new).
- `pqi/scoring.py` — `kelly_stake_scaled` field already present.
- `requirements.txt` — `py-clob-client` already declared.

### Session logs

- (To be populated as sessions begin work on 2a.)

---

## Session state

**Last work session:** *not yet started (blocked on Phase 1)*

**Done:** *nothing yet*

**In-progress:** *nothing*

**Next action:** Wait for Phase 1 unified acceptance criteria to
pass. On resume, the first task is 2a.1 — `pqi buy <market_id>`
with idempotency + drift-abort + structured logging. Do not start
2a.2/2a.3 until 2a.1 is green.

**Notes for next session:**

- `py-clob-client` is already a declared dependency and unused.
  Read its README before wiring — it may need a signer config
  path that differs from what `.env` expects today.
- `kelly_stake_scaled` already lives on the result object; don't
  recompute Kelly inside the buyer — read it from the existing
  result path.
- `fills.jsonl` is a new persisted-state file. Add it to the
  `known-issues.md` "persisted state files" list in the same
  patch as 2a.1 so the file doesn't become an invisible operator
  liability.
- Default to a small per-fill stake cap (`TIER_A_MAX_STAKE_USDC`,
  perhaps $10) on first ship — a belt-and-braces guard above the
  Kelly scaler. Remove the cap only after the first-10-fills
  audit passes.
