# Phase 0: Edge Validation via Analytics Infrastructure

**Status:** active (Tasks 0.1 + 0.2 + 0.3 done; 0.4 next)
**Estimated effort:** 1–2 weeks of focused work
**Owner:** TBD
**Last updated:** 2026-04-19

---

## Why this phase exists

Before committing to any infrastructure scaling, we need to answer:
**do we have real prediction edge, and if so, where does it live?**

Current state:

- ~55 resolved BUYs in `history.jsonl` with `correct_buy / wrong_buy`
  labels.
- ~400 matches × 7 agents ≈ 2800 agent-level directional judgments
  (many PASS, but directional leanings tracked).
- No CLV tracking (markets resolve to 1/0; but pre-event CLV is
  retroactively computable from CLOB hourly history).
- No reliability diagram (predicted probability vs realized
  frequency by bucket).
- **Every new metric requires writing Python** — `history.jsonl`
  is not queryable by SQL, and `history stats` only aggregates a
  fixed set.

This phase creates the **measurement infrastructure** that validates
edge and makes all future analytics cheap. The key architectural
decision: **DuckDB over Postgres**, because the problem is
queryability, not concurrency or scale.

---

## Entry criteria

- [x] `history.jsonl` exists with resolved BUY entries (~55 rows)
- [x] CLOB API reachable (`pqi/market/clob.py` already fetches
      price history)
- [x] Read access to agent verdicts in history rows (stored as
      full-fidelity `CouncilResult`)

No blockers — this phase can start immediately.

---

## Goals (measurable)

1. **SQL-queryable history** — any ad-hoc question about
   `history.jsonl` answerable via DuckDB without writing Python.
2. **Pre-event CLV backfilled** on every resolved BUY, persisted
   (or joinable via DuckDB view).
3. **Reliability diagram** produced: for each agent (and for the
   council aggregate), predicted-probability buckets mapped to
   realized hit frequency.
4. **Per-band calibration report** — for each price band the EV
   gate uses, realized BUY hit rate vs the Beta-binomial smoothed
   threshold.
5. **Edge retrospective** — a short document in
   `docs/retrospectives/` stating one of three verdicts:
   - Edge validated → proceed to Phase 1 + 2
   - No edge → Phase 1 is the only thing that matters (fix prompts)
   - Inconclusive → identify what sample we need and park until
     we have it

---

## Tasks

### Task 0.1 — DuckDB analytics wire-up

**What:** Add DuckDB as an analytics layer over `history.jsonl`,
expose via CLI, document common queries.

**Why:** Unlocks ad-hoc SQL over history with ~half a day of work.
Solves the "I have to write Python for every metric" problem. Zero
ops cost vs Postgres.

**Implementation:**

- Add `duckdb` to `requirements.txt`.
- Create `pqi/analytics_sql.py` (or extend `pqi/analytics.py`) with
  a `query(sql: str)` helper that runs
  `duckdb.sql(sql).pl()` (or `.df()`) against
  `read_json_auto('~/.pqi/history.jsonl')`.
- Add `pqi query "<SQL>"` CLI subcommand that wraps the helper and
  pretty-prints results via `rich`.
- Optional: pre-define common views (`history_flat`, `verdicts_flat`)
  using `UNNEST(verdicts)` to flatten the nested agent records.

**Acceptance criteria:**

- [x] `pqi query "SELECT category, COUNT(*) FROM history GROUP BY
      category"` works end-to-end with nice output (note: uses
      `result.market.category` — nested fields accessed via dot
      notation; documented in `docs/analytics_queries.md`)
- [x] Document at least 5 starter queries in
      `docs/analytics_queries.md`: per-category hit rate, per-agent
      lean rate, monthly PnL, per-band realized rate, missed
      opportunities (PASSes that would have won)
- [x] Query performance acceptable (<1 s for full-history
      aggregations at current size) — ~230ms per cold call on 423
      rows / 13MB

**Files touched:**

- `requirements.txt`
- New: `pqi/analytics_sql.py`
- New: `docs/analytics_queries.md`
- `pqi/main.py` (CLI subcommand registration)

**Risks:**

- `history.jsonl` has nested `verdicts` (list of `AgentVerdict`
  dicts). DuckDB handles this via `UNNEST` on list columns. Verify
  early — run a trivial `SELECT verdicts FROM
  read_json_auto(...)` first and inspect schema.
- If nested reads are awkward, emit a flattened sidecar
  `~/.pqi/verdicts.jsonl` via a one-shot script; DuckDB can read
  both and join them.
- `aiosqlite` is already in the dep set for the price-delta cache —
  DuckDB and SQLite can coexist (different files, different
  purposes).

---

### Task 0.2 — Pre-event CLV backfill

**What:** For each resolved BUY in history, query CLOB hourly price
at `event_start_time`, compute `clv = price_at_start - entry_price`,
persist.

**Why:** Pre-event CLV is the cleanest **prediction-edge** signal we
can extract from current data. Independent of bet outcome (a bet
can lose and still have positive CLV — that is genuine information
about entry quality).

**Implementation:**

- Script: `scripts/backfill_clv.py`
- For each resolved BUY in history:
  - Read `market.clob_token_ids` from stored `MarketData`.
  - Identify the bought side (`recommendation` = "BUY YES" or
    "BUY NO") and its token ID.
  - Query CLOB price history for that token; find the hourly
    bucket containing `event_start_time`.
  - Compute `clv = price_at_event_start - entry_price` where
    `entry_price = market.yes_price` for BUY YES or
    `1 - market.yes_price` for BUY NO (Polymarket NO side is a
    separate orderbook; use the actual NO price if stored,
    otherwise the derived fallback with a flag).
  - Persist to `~/.pqi/clv.jsonl` keyed by history row identifier.
- Expose via DuckDB as a joinable view (`clv_by_row`).

**Acceptance criteria:**

- [x] CLV computed for every resolved BUY with non-null
      `event_start_time` — 44/65 done; the 21 remainder all hit
      `no_tokens` (pre-session-22 rows predating `clob_token_ids`
      persistence), 100% of rows with stored tokens succeeded
- [x] Rows with missing `event_start_time` flagged, not silently
      dropped — uniform-schema sidecar: every row carries `available`
      + `unavailable_reason`
- [x] CLV distribution queryable
      (`SELECT avg(clv), stddev(clv), count(*) FROM clv_by_row`) —
      mean −0.0167, stddev 0.064, min −0.26, max +0.12 on n=44
- [x] Per-category CLV breakdown available — see `docs/analytics_queries.md` Q5
- [x] Coverage rate reported — "CLV computed for 44 of 65 resolved
      BUYs — 67.7% coverage, 21 unavailable (no_tokens)"

**Files touched:**

- New: `scripts/backfill_clv.py`
- New runtime file: `~/.pqi/clv.jsonl`
- `docs/analytics_queries.md` (CLV query examples added)

**Risks:**

- `event_start_time` is sometimes `None` from the Gamma API (see
  `docs/known-issues.md` → "event_start_time query params"). Flag
  as missing; don't fall back to `end_date` (which can be hours or
  days later and muddies the signal).
- CLOB history is hourly; CLV measurement has ±30 min uncertainty.
  Acceptable; document it.
- If a BUY's `event_start_time` is outside the CLOB retrieval
  window (>1 week ago at time of analysis), CLV may be unavailable.
  Document coverage rate; re-running the script after longer price
  history is retained would help but is not blocking.

---

### Task 0.3 — Reliability diagram

**What:** Bucket all agent predictions by predicted-probability
band, compute realized hit rate per bucket. Visualize as ASCII +
optional PNG.

**Why:** Answers "when Agent X predicts Y%, does it resolve Y% of
the time?" — the single most informative calibration check. Uses
the full ~2800-judgment set, not just the 55 resolved BUYs. This is
the highest-data-density signal about council quality.

**Implementation:**

- Script: `scripts/reliability_diagram.py`
- For each agent and for the council aggregate `final_score`:
  - Collect all predictions where the event has resolved.
  - Bucket predicted `P(YES)` into 10 bins (or match EV-gate price
    bands for cross-comparison: `[0.09–0.50, 0.50–0.60, 0.60–0.70,
    0.70–0.80, 0.80–0.95]`).
  - For each bin, compute `realized_yes_rate = sum(yes_outcome) /
    count`.
  - Plot predicted vs realized (ASCII table; optional matplotlib
    PNG saved to `~/.pqi/reports/`).
- Output includes per-bin sample size; bins with n<10 flagged as
  thin.
- Also compute Brier score per agent and per aggregate.

**Acceptance criteria:**

- [x] Reliability diagram produced for all 7 agents + council
      aggregate — `scripts/reliability_diagram.py` (session 36)
- [x] Per-bin n reported, low-n bins flagged (`thin` when n<10)
- [x] Clear visual (or table) showing over/underconfidence by bin —
      Bull is overconfident on favorites (gap −0.10 to −0.22 across
      [0.50, 0.95)); Bear is essentially noise outside [0.09, 0.50);
      Council is well-calibrated through [0.09, 0.70) and slightly
      underconfident on YES in [0.60, 0.95)
- [x] Brier score computed per agent and for aggregate — Council
      0.2176 (best, beats the 0.25 always-0.5 baseline by ~3pp);
      Bear 0.2810 (worst, **above** the chance baseline); five
      others tightly clustered 0.2180–0.2370
- [x] Results saved to `~/.pqi/reports/reliability_YYYYMMDD.md`
      for versioning

**Files touched:**

- New: `scripts/reliability_diagram.py`
- New runtime directory: `~/.pqi/reports/`
- `docs/analytics_queries.md` (pointer to the report)

**Risks:**

- "Resolved event" for agent-level calibration means the event
  resolved, not that a BUY was triggered. Ensure we're reading the
  resolved outcome (`yes_outcome` or equivalent) from the history
  row, not the BUY-outcome field.
- Per-agent lean rates vary (Option B shadow: |drift| 0.01–0.06)
  — curves may be near-identical, which is itself a finding that
  confirms mirror collapse (feeds Phase 1 priority).

---

### Task 0.4 — Per-band calibration check

**What:** For each price band used by the EV gate, compute realized
BUY hit rate and compare to the banded Beta-binomial threshold
currently in `PerceivedStats.band_stats`.

**Why:** The banded EV gate is our final arbiter. We need to know
whether its thresholds actually match realization — if the
0.60–0.70 band's realized rate is 45% but our gate threshold is 62%
(smoothed toward global), we're under-rejecting -EV bets.

**Implementation:**

- Via DuckDB query on history + `~/.pqi/stats.json`
  (`perceived_stats.band_stats`).
- Report per band:
  - `n` (resolved BUYs in that band)
  - Realized hit rate
  - Banded threshold currently in use
  - 95% Wilson CI on realized rate
  - Delta (realized − threshold)
- Flag bands where realized is below the implied-prob floor
  (suggesting -EV at the gate threshold).

**Acceptance criteria:**

- [ ] Per-band realized-vs-threshold table published
- [ ] Low-n bands flagged (n<10 in particular)
- [ ] Recommendation on `BAND_SHRINKAGE_K` (keep, raise, lower)
      captured in the retrospective — **do not tune K on the same
      data used to compute band_stats**; flag if more data is
      needed before reassessing

**Files touched:**

- `docs/analytics_queries.md` (per-band query)
- `docs/retrospectives/phase_0_edge_2026_04.md` (final
  recommendation)

**Risks:**

- n per band is small (55 rows / 5 bands ≈ 11 per band mean, uneven
  distribution). CI will be wide. Report honestly; don't overweight
  thin bands.
- Session 32 already noted this: "don't tune K on the same 55-row
  slice that picked it." Phase 0 measures current calibration;
  doesn't retune K on existing data.

---

### Task 0.5 — Edge retrospective & verdict

**What:** Synthesize outputs of tasks 0.1–0.4 into a retrospective.
Issue a verdict: edge validated / no edge / inconclusive.

**Implementation:**

- Write `docs/retrospectives/phase_0_edge_2026_04.md`.
- Required sections:
  - **Summary:** verdict + one-sentence justification
  - **Pre-event CLV distribution:** mean, stddev, per-category,
    coverage rate
  - **Reliability diagram summary:** per-agent Brier, biggest
    calibration gaps, cross-agent correlation (preview of Phase 1
    diagnosis)
  - **Per-band realized vs threshold:** full table + flagged bands
  - **Recommended next actions:** which phase to move to, which
    knobs to revisit, what additional data to collect
- Update `docs/phases/README.md` status table.
- Update `CLAUDE.md` §Current State.

**Acceptance criteria:**

- [ ] Retrospective published
- [ ] Phase 1 and Phase 2 entry criteria reviewed/updated based on
      findings
- [ ] `docs/phases/README.md` and `CLAUDE.md` updated to reflect
      Phase 0 completion

---

## Exit criteria

All of:

- [ ] DuckDB queryable from CLI (`pqi query "..."` works)
- [ ] CLV backfilled (or explicitly documented as unavailable) for
      all resolved BUYs
- [ ] Reliability diagram produced for 7 agents + aggregate
- [ ] Per-band calibration check complete
- [ ] Retrospective written with verdict

---

## Validation: "are we done?"

Ask these five questions:

1. Can I answer "per-category hit rate since January" without
   writing Python? (Yes → 0.1 passed)
2. Do I know the mean pre-event CLV on our resolved BUYs, with a
   documented coverage rate? (Yes → 0.2 passed)
3. When Bull predicts 70%, what fraction actually resolve YES?
   (Known → 0.3 passed)
4. Is the 0.60–0.70 band threshold matching realization? (Known →
   0.4 passed)
5. Do we have a written verdict on whether edge exists? (Yes →
   0.5 passed)

If any answer is "no," that task is not done.

---

## Risks / decision points for the phase

- **Nested-JSON friction with DuckDB.** If `history.jsonl`'s
  `verdicts` field is too deeply nested for ergonomic DuckDB reads,
  emit a flattened sidecar. Don't abandon DuckDB — the flexibility
  payoff is high and the workaround is cheap.
- **CLV coverage gap.** If many rows have `event_start_time=None`
  (Gamma quirk), coverage will be partial. Report coverage rate;
  consider backfilling `event_start_time` from PandaScore
  (`scheduled_at`) for esports rows — may be worth a one-off
  script.
- **Mirror-collapse confirmed in reliability diagram.** If
  per-agent reliability curves are near-identical across all 7
  agents, that confirms the Phase 1 diagnosis and *raises* Phase 1
  priority. Note this explicitly in the retrospective.
- **"No edge" verdict.** If CLV is consistently negative and
  per-band realized is below thresholds, the council itself is
  -EV. In that case, Phase 1 (prompt fix) becomes the only path
  forward; Phases 2 and 3 park until Phase 1 produces something
  worth deploying.

---

## Related work

### Roadmap themes feeding in

- **Theme A · A.1, A.7, A.8** (calibration-flavored items) will
  have sharper priority after Phase 0's reliability diagram.
- **Theme A · A.9** (dynamic weights, live since session 25) feeds
  Phase 0's DuckDB queries via `agent_weights_snapshot` on every
  `CouncilResult`.
- **Theme C · C.4** (per-category accuracy slicing) is subsumed
  by Task 0.1.
- **Theme C · C.5** (postmortem persistence) becomes trivial once
  SQL is available.

### Files referenced

- `pqi/stats.py` — banded WR source, `PerceivedStats.band_stats`
- `pqi/history.py` — `history.jsonl` schema + append path
- `pqi/analytics.py` — existing analytics module (extend rather
  than replace)
- `pqi/market/clob.py` — CLOB history fetch, reused for CLV
  backfill
- `pqi/main.py` — CLI subcommand registration

### Session log entries to read

- Session 22 — EV gate introduction
- Session 23 — WR-based edges + run-time snapshots
- Session 25 — Dynamic per-agent reliability weights
- Session 31 — Banded WR (Beta-binomial shrinkage)
- Session 32 — Mirror collapse + contrarian retune (current baseline)

---

## Session state

**Last work session:** session 36 (2026-04-19)

**Done:**

- Scaling assessment + phase plan committed (session 33).
- Phase 0 scope approved.
- **Task 0.1 — DuckDB wire-up (session 34).** `analytics_sql.py`
  module landed with `history` + `verdicts` pre-registered views.
  `pqi query "<SQL>"` CLI subcommand works end-to-end via rich
  rendering. 5 starter queries published in
  `docs/analytics_queries.md`. `duckdb>=1.0` pinned in
  `requirements.txt`. Full-history aggregations clock at ~230ms
  (target was <1s). See session-log §Session 34 for the DuckDB
  timestamp-inference gotcha and surface-level findings.
- **Task 0.2 — Pre-event CLV backfill (session 35).**
  `scripts/backfill_clv.py` hits CLOB `interval=max` per bought-side
  token and picks the last hourly bucket before `event_start_time`
  (last-before-target policy; closest-overall would leak post-event
  resolution price). Writes `~/.pqi/clv.jsonl`; idempotent with
  `--rebuild` escape. `analytics_sql.py` registers `clv_by_row` view
  only when the sidecar exists. Coverage: 44/65 resolved BUYs (67.7%);
  21 rows hit permanent `no_tokens` (predate session-22
  `clob_token_ids` persistence); 100% of token-carrying rows succeeded.
  Preliminary distribution: mean CLV −0.0167, stddev 0.064, min −0.26
  (d368cc6f, BUY YES), max +0.12 (71e89320, BUY YES). Per-category:
  valorant −0.032 (n=17), league-of-legends −0.001 (n=14),
  counter-strike −0.013 (n=8). CLV + outcome join shows no monotone
  win-rate gradient at n=22/bucket; re-check at 100+ BUYs.
- **Task 0.3 — Reliability diagram (session 36).**
  `scripts/reliability_diagram.py` (stdlib only) buckets per-agent +
  council `final_score` predictions into the EV-gate-aligned bands
  with `[0.00, 0.09)` and `[0.95, 1.00]` bookends so no prediction is
  silently dropped. Outcome via directional-winner. Operates over 385
  non-legacy resolved rows / 2,668 verdicts. Output: ASCII to stdout +
  markdown at `~/.pqi/reports/reliability_YYYYMMDD.md`. Headline numbers:

  | Metric | Value | Reading |
  | --- | --- | --- |
  | Council Brier | **0.2176** | Best of any agent — confidence-weighted aggregation buys ~3pp over the 0.25 always-0.5 baseline. |
  | Bear Brier | **0.2810** | **Worse than chance.** Consistent with session-34's 49% lean rate finding. |
  | Other agents Brier | 0.2180–0.2370 | Tight cluster: rules_lawyer 0.2180, sentiment 0.2196, specialist 0.2195, statistician 0.2199, contrarian 0.2215, bull 0.2370. |
  | Council [0.09, 0.50) | n=187, realized 33%, mean pred 35% | Well-calibrated. |
  | Council [0.50, 0.60) | n=93, realized 52%, mean pred 55% | Well-calibrated. |
  | Council [0.60, 0.70) | n=88, realized 71%, mean pred 64% | **Underconfident on YES** (gap +0.063). |
  | Bull [0.50, 0.95) | n=145+16, gap −0.10 to −0.22 | **Overconfident on favorites** across the whole upper tail. |
  | Mean pairwise Pearson | 0.557 | Dragged down by Bear; see cluster below. |
  | Pairs with r ≥ 0.85 | **9/21 (43%)** | Mirror collapse is **cluster-level, not uniform**. |

  Cluster diagnosis: rules_lawyer / statistician / sentiment /
  specialist / contrarian form a tightly correlated cluster (9 of 10
  cluster-internal pairs at r ≥ 0.85). Bull retains weak independence
  (~0.6 with the cluster). Bear is essentially uncorrelated
  (−0.17 to +0.04) but its predictions are also the worst-calibrated
  of any agent. Phase 1's SHARED_PREAMBLE autonomy fix should
  preserve Bear's distinctness while improving its calibration —
  destroying Bear's distinctness without fixing its calibration would
  push council Brier upward.

**In-progress:** *nothing*

**Next action:** Task 0.4 — Per-band calibration check. Use
`pqi query` against `verdicts` / `history` and the EV-gate band
boundaries to compute realized BUY hit rate per band (alongside
the per-agent prediction realisation rates already in the
reliability report) and compare against the Beta-binomial
smoothed thresholds in `~/.pqi/stats.json` (`band_stats`).
Report per band: n, realized rate, banded threshold, 95% Wilson
CI, delta. Flag bands where realized < implied-prob floor
(suggesting the gate is under-rejecting). Do **not** retune
`BAND_SHRINKAGE_K` on the same data that produced `band_stats`
— Task 0.4 measures, Task 0.5 recommends.

**Notes for next session:**

- DuckDB's `read_json_auto` needs
  `timestampformat='%Y-%m-%dT%H:%M:%S.%f%z'` + `sample_size=-1`;
  captured as a module constant in `analytics_sql.py`. Don't
  reinvent.
- Nested access in `history`: `result.market.*`, `resolution.*`,
  `analysis.*`. Flat per-verdict access via the `verdicts` view.
- CLV bucket policy matters — one probed run (8735ea38) showed
  0.835 → 0.9995 across a single post-event bucket. Using
  closest-overall would silently leak resolution into CLV for
  markets with tight event→resolution windows. Keep last-before-target.
- `clv_by_row.bucket_drift_minutes` is ≤ 0 by construction; wider
  drifts (e.g. −540min, −1019min) happen when CLOB's first lifecycle
  hour is after the target. Those rows are honest — the sidecar
  reports drift so the retrospective can caveat them.
- Early finding: `[0.50, 0.60)` band realized rate 33% on n=9 —
  flag for Task 0.4 (per-band calibration). Sample is thin; will be
  sharper after more BUYs land there. Note: at the council-prediction
  level, 93 rows in [0.50, 0.60) realized at 51.6% — the 33% number is
  the BUY-survivorship subset, not the underlying calibration. Task
  0.4 will quantify that gap.
- Task 0.5 retrospective should explicitly quote the CLV + band
  numbers once 0.3–0.4 are in. Don't retune `BAND_SHRINKAGE_K` on
  the same data that produced the current band stats.
- Reliability-diagram output is a versioned UTC-dated markdown file —
  re-running on a different day produces a new report rather than
  overwriting. Session-36 file: `~/.pqi/reports/reliability_20260418.md`.
- Phase 1 (signal quality) is now the highest-leverage parallel
  workstream. The 5-agent cluster collapse + Bear pathology are
  signal-quality failures, not modeling failures — the
  SHARED_PREAMBLE rewrite should be evaluated against (a) does
  cluster pairwise drop below 0.85, (b) does Bear Brier fall toward
  the rest of the cluster, (c) does council Brier hold or improve.
  Re-run `scripts/reliability_diagram.py` after any prompt change to
  measure delta.
- Phase 0 and Phase 1 can still run in parallel — Task 0.4 (DuckDB
  query + retrospective) doesn't touch agents, so a Phase 1 prompt
  rewrite can land alongside.
