# Session Log — Changelog & Implementation Details

Per-session change log with detailed diffs. Referenced from CLAUDE.md.

---

## Session 43 — `pqi history equity` (bankroll PnL + drawdown + Sharpe)

New history sub-command that turns the per-BUY PnL math already in
`analytics.py` into a bankroll-denominated equity curve with max
drawdown and a naïve per-trade Sharpe. Pure read-only analytics —
aggregator / pipeline / persisted schema all untouched.

### Motivation

User asked whether drawdown / Sharpe were derivable from current
history. They were — every resolved BUY already has entry price +
final redemption price via `analytics.py::_final_yes_price`, and
every entry has a `timestamp` — but nothing tied them to a bankroll
or reported drawdown. With the Phase 1 rollout on an observe-only
hold, this is a useful parallel surface for answering "what would
this have done with real capital" without touching the aggregator.

### Part A — `analytics.py::equity_curve()` + `equity_metrics()`

Two pure functions.

`equity_curve(entries, bankroll=100.0, stake=5.0, mode="dollars")`:

- Walks resolved BUYs chronologically (by `entry.timestamp`)
- Two sizing modes:
  - `"dollars"` (default): each trade commits a fixed dollar stake,
    `shares = stake / side_entry_price`. Capped to remaining balance
    so a busted curve can't go infinitely negative. This is standard
    portfolio sizing — each bet risks the same capital.
  - `"shares"`: each trade buys a literal fixed share count,
    `cost = shares * side_entry_price`. Matches the user's "5 shares
    at 50c" pedagogical framing.
- Per-row: entry side price, final side price, shares, cost,
  proceeds, pnl, running balance, running peak, drawdown %
- Filters: PASSes, unresolved, voided (no redemption) all excluded

PnL math (identical to existing `analytics.classify_outcome`):

```
BUY YES: cost = N*p_yes,       proceeds = N*final_yes
BUY NO:  cost = N*(1-p_yes),   proceeds = N*(1-final_yes)
pnl = proceeds - cost
```

Hand-verified:
- 5-share BUY YES @ 50c clean win → +$2.50 ✓
- 5-share BUY YES @ 60c clean loss → −$3.00 ✓
- 5-share BUY NO  @ yes=60c (NO=40c) clean NO win → +$3.00 ✓

`equity_metrics(curve, bankroll)`:

- Final bankroll, net PnL ($ + %)
- Peak / trough (balance, trade #, timestamp)
- Max drawdown (% of peak + $)
- Wins / losses / break-evens / win rate
- Best / worst trade
- Mean + stdev of per-trade PnL
- Per-trade Sharpe computed on `pnl_i / cost_i` (unitless returns on
  deployed capital — size-invariant for uniform stakes, which is why
  dollars-mode and shares-mode on the same history produce the same
  Sharpe in practice)

Sharpe is naïve: no annualization (per-trade holding periods vary
hours → weeks; annualizing without that data adds noise), excess-
return baseline = 0 (short-horizon risk-free rate is lost in the
noise). At small n the figure is descriptive, not a decision
threshold — drawdown is the sturdier number for a 65-BUY sample.

### Part B — `pqi history equity` CLI (`main.py`)

New typer command under the `history` sub-app. Flags:

| Flag             | Short | Default   | Note |
| ---------------- | ----- | --------- | ---- |
| `--bankroll`     | `-b`  | `100.0`   |      |
| `--stake`        | `-s`  | `5.0`     | dollars or shares depending on `--mode` |
| `--mode`         | `-m`  | `dollars` | `dollars` \| `shares` |
| `--last`         | `-n`  | `15`      | `0` = all |
| `--no-auto-sync` |       | off       | skips bg sync |

Renders a Rich summary panel (headline metrics, color-coded
positive/negative) plus a detail `Table` with one row per trade
(date, event, side, entry price, final price, shares, cost, PnL,
balance, drawdown %). `Event` column uses `no_wrap=True,
overflow="ellipsis"` so rows stay single-line on standard terminal
widths.

### Part C — First real-history smoke

Against the live history file (84 resolved BUYs as of
session-start):

- Default ($100 bankroll, $5 flat stake): final $117.04, net +17.0%,
  peak $131.68 at trade #43, max drawdown 21.7% ($28.62), win rate
  73.8%, per-trade Sharpe 0.067
- Shares mode (5 shares/trade): final $112.53, net +12.5%, max
  drawdown 16.0%, same win rate (as expected — mode only changes
  sizing, not which side wins), per-trade Sharpe 0.067 (size-
  invariant as predicted)
- Best trade in shares mode: +$2.50 at 5 × 50c — exactly the user's
  pedagogical example

73.8% win rate at +17% net is consistent with the Phase 0 finding
that the council is barely clearing break-even price-weighted — most
BUYs cluster at 0.60–0.85 entry where break-even win rates are 60–85%.

### Files touched

- `analytics.py` — `datetime`, `mean`, `pstdev` imports;
  `_entry_timestamp`, `_entry_title`, `equity_curve`,
  `equity_metrics`
- `main.py` — `@history_app.command("equity")` `history_equity(...)`
- `README.md` — `history equity` row in sub-commands table; new
  `#### history equity flags` section with formula, metric
  definitions, examples

### Part D — `--mode kelly` + operational cost row

User follow-up: add walk-forward half-Kelly sizing that degrades to
`$5` flat for pre-EV-gate trades, plus a row capturing what the
system actually cost to run end-to-end (not just the council).

**Kelly mode is walk-forward by construction.** Every
`CouncilResult` already persists `kelly_stake_scaled` — the half-
Kelly fraction `KELLY_MULTIPLIER × ((b·p − q) / b)` computed at
analysis time from the band-smoothed WR *that existed at that
moment*. Reading that stored snapshot is walk-forward-correct with
no look-ahead bias; no `recompute_from_history` replay needed (the
follow-up note from Part C was too pessimistic).

- `equity_curve()` gained `EquityMode = Literal["dollars", "shares",
  "kelly"]` and a fallback path: when the stored Kelly fraction is
  `≤ 0` (EV gate dormant for that row — `resolved_buys <
  MIN_WIN_RATE_SAMPLES` at the time), stake falls back to
  `min(balance, float(stake))`. Each row tags `sizing_source`
  (`"kelly"` / `"fallback"` / `"dollars"` / `"shares"`) so the panel
  can show the mix.
- `equity_metrics()` surfaces `n_kelly_sized` vs `n_fallback` in
  Kelly mode.
- Detail table gains a `Size` column in Kelly mode: `K7.7%` (magenta,
  Kelly-sized) or `flat` (dim, fallback).

**Operational cost row.** Existing cost attribution only covered
council runs. User asked for the full pipeline: Tavily searches +
Search Architect + Synthesizer + Council, counted across **every**
entry in scope (BUY + PASS, resolved + unresolved — the system paid
for the PASSes too). Added:

- Four env knobs in `config.py` + `.env.example`:
  `TAVILY_COST_PER_SEARCH=0.008`, `SEARCH_ARCHITECT_COST=0.001`,
  `SYNTHESIZER_COST=0.005`, `COUNCIL_COST_PER_RUN=0.015`
- `analytics.operational_cost(entries, …)` returns per-analysis +
  total, split by tavily vs LLM, plus BUY/PASS + resolved/unresolved
  counts
- Panel renders a blank-separated block: Analyses, Per-analysis,
  Total cost, **Net of cost** (equity PnL − infra cost). Upper bound
  — cached re-runs pay $0 in reality but aren't subtracted (we don't
  persist cache-hit flags)

Real-history smoke against 513 entries (84 resolved BUYs):

- Flat $5 / trade: +$17.04 (+17.0%)
- Kelly mode: **+$25.77 (+25.8%)**, 28 Kelly-sized + 56 fallback,
  max DD 25.5% ($39.76) — Kelly tolerates deeper drawdowns than
  flat, as expected
- Operational cost: **513 × $0.0610 = $31.29** ($20.52 tavily +
  $10.77 LLM)
- Net of cost (Kelly): $25.77 − $31.29 = **−$5.53** — at current
  pricing assumptions the system is infrastructure-negative even
  on its best-sized variant. The headline PnL number has always
  been gross; this row makes the drag explicit

Minor polish after first render: the Event column in the detail
table was wrapping across many lines (Rich's default). Fix:
`no_wrap=True, overflow="ellipsis", max_width=36`. Net-of-cost
formatting also switched from `${value:+.2f}` (which rendered
`$-5.53`) to `{sign}${abs(value):.2f}` for consistency with the
`+$25.77` Net PnL row.

### Part E — `--kelly-fallback-pct` flag (fallback scales with bankroll)

Follow-up the same session: the flat $5 Kelly fallback doesn't
scale when `--bankroll` is bumped. At `-b 1000` users still got a
$5 stake per pre-EV-gate BUY, which is a rounding error against the
$10–$100 Kelly-sized rows. Fix: replace the fixed `stake` dollar
amount with `bankroll × kelly_fallback_pct`.

- `equity_curve()` gained `kelly_fallback_pct: float = 0.05` kwarg.
  Validated in (0, 1]. Fallback cost = `min(balance, bankroll *
  kelly_fallback_pct)` (bankroll = starting, not current — keeps the
  fallback a fixed dollar per-trade across the backtest, matching
  the prior mental model; only the dollar figure now scales with
  `--bankroll`).
- `main.py` gained `--kelly-fallback-pct` / `-k` (default 0.05).
- Panel title + Sizing-mix line both reworded to show
  `"fallback 5.0% of bankroll = $5.00"` / `"56 fallback @ 5.0%
  ($5.00)"` so the fraction and the dollar figure are both visible
  at a glance.

Smoke verified across three bankrolls on the same 84-BUY history
(identical PnL % because all sizes scale linearly; only the infra
cost is absolute):

| Bankroll | Fallback $ | Final bankroll | Net PnL     | Net of cost |
| -------- | ---------- | -------------- | ----------- | ----------- |
| $100     | $5.00      | $125.77        | +$25.77     | **−$5.53**  |
| $500     | $25.00     | $628.87        | +$128.87    | +$97.58     |
| $1000    | $50.00     | $1257.67       | +$257.67    | +$226.38    |

The inflection point — where Kelly-sized equity PnL first covers
the $31.29 infra spend — sits around a $121 starting bankroll on
current history. Below that the system is infrastructure-negative;
above it, positive. This is the key framing the new flag unlocks:
infrastructure cost doesn't scale, so capital-efficiency is the
dominant lever, not sizing tweaks.

Also tested a non-default `-k 0.02` at `-b 500` → fallback $10,
final $593.20, Net of cost +$61.90 — confirms the flag is live.

### Part F — `new` keyword in the multi-select prompt

Tiny UX add to close out the session. Before this, incremental
queuing for stage-1 V2 observation meant eyeballing the "Seen"
column (empty cell = fresh) and building the comma-list by hand.
The new `new` keyword resolves to exactly the indices where the
Seen cell is empty — "every event with no prior analysis on any
of its markets" — matching the column's semantic exactly so there
are no surprises about which rows get picked.

Implementation stays scoped: `_parse_selection` is unchanged
(still pure, no I/O); `new` is handled directly in
`prompt_event_multiselect` where the `groups` list and
`_analyzed_market_ids()` are already in scope. Empty unseen set
→ re-prompt with a yellow message rather than silent no-op.
Prompt string updated to show the unseen count inline so the
user can see at a glance whether `new` would pick anything:
`"... 'all', 'new' (3 unseen), Enter for #1"`.

Use case the user called out: stage-1 V2 observation loop. They
can now run `python3 -m pqi.main -c esports -s 24 -j 3
--summary-only` then type `new` — skip anything already in
history, queue only fresh markets for V2 preamble tagging.

### Files touched (cumulative)

- `analytics.py` — `datetime`, `mean`, `pstdev` imports;
  `_entry_timestamp`, `_entry_title`, `_rec_of`, `equity_curve`
  (+ `EquityMode` + Kelly branch + `sizing_source` tagging +
  `kelly_fallback_pct` param), `equity_metrics` (+ Kelly sizing
  mix), `operational_cost`
- `main.py` — `@history_app.command("equity")` `history_equity(...)`
  + `--kelly-fallback-pct` / `-k` flag + Size column + operational
  cost block + percent-aware panel/Sizing-mix text;
  `prompt_event_multiselect` gained the `new` keyword (resolves
  to unseen-event indices; empty-set reprompt guard)
- `config.py` — four cost knobs after `ANALYZE_BATCH_CONCURRENCY`
- `.env.example` — new "Operational cost estimation" section
- `README.md` — `history equity` sub-commands row; `#### history
  equity flags` section (formula, metric definitions, Kelly
  mechanics incl. bankroll scaling, examples for all three modes
  + multi-bankroll + `-k` examples); four cost env vars in the
  Tuning table; Batch-mode selection table gained `new` row +
  matching `new` example in the code block

### Follow-ups (not done this session, consciously scoped out)

- **Per-run cost override.** Today cost is uniform per analysis.
  Sibling re-runs skip Tavily + Synthesizer and only pay the
  council; cached results pay $0. A `cache_hit` / `pipeline_variant`
  tag on `HistoryEntry` would let us subtract those — today it's an
  upper bound, noted in the panel text.
- **No chart.** Rich has sparkline-adjacent primitives; a small
  equity-vs-time sparkline would be nice in the summary panel.
  Low-priority nice-to-have.
- **No per-category slice.** `--category esports` would be trivial
  (filter `entries` before passing to `equity_curve`). Deferred until
  we actually have cross-category history worth comparing.

---

## Session 42 — Multi-run analyze (multi-select + bounded parallel + Live status)

The `analyze` command learned to take a multi-select spec at the
event-selection prompt and run the batch with bounded parallelism and
a Rich Live status table. This is the shipped form of roadmap item
C.3 and directly serves the stage-1 V2 observation loop (queue many
matches, watch them settle, compare populations via
`reliability_diagram --flag`). Zero aggregator touch; zero changes to
`_run_analysis`'s pipeline logic beyond an optional progress callback.

### Part A — Selection parsing (`main.py::_parse_selection`)

New pure helper: `_parse_selection(spec: str, max_n: int) -> list[int]`
that accepts:

- `""` → `[]` (caller falls through to default behavior)
- `"3"` → `[3]`
- `"1,3,5"` → `[1, 3, 5]`
- `"3-7"` → `[3, 4, 5, 6, 7]` (inclusive both ends; reversed ranges
  normalized, so `"7-3"` is the same)
- `"1-3,7,9-11"` → mixed
- `"all"` → `[1, 2, ..., max_n]`

Duplicates collapse, results are sorted ascending, out-of-range or
non-integer inputs raise `ValueError` with a clear message. 17/17
inline smoke tests pass.

### Part B — `on_phase` callback in `_run_analysis`

Signature gained `on_phase: Callable[[str, int], None] | None = None`.
When provided, the function suppresses its `console.print` output
(`verbose = on_phase is None`) to avoid corrupting the Rich Live
display, and emits six phase events:

| Phase                    | Percent |
| ------------------------ | ------: |
| `resolving match`        |   2     |
| `synthesizing briefing`  |   5     |
| `fetching price history` |  50     |
| `council deliberating`   |  55     |
| `scoring`                |  95     |
| `done`                   | 100     |

Cache-hit short-circuit fires `cached` / 100 and returns immediately.
Single-run behavior is unchanged: when `on_phase is None`, all prints
still stream to the console exactly as before.

### Part C — Batch driver (`_run_batch` + `_BatchRow` + Rich Live)

`@dataclass _BatchRow` carries mutable state per event:
`idx, group, market, phase, percent, started_at, finished_at, result,
yes_raw, no_raw, error, run_id`.

`_run_batch(rows, concurrency, no_cache)`:

- `asyncio.Semaphore(concurrency)` limits in-flight work
- Each worker captures its row, sets `starting`/0%, registers
  `_on_phase(label, pct)` that mutates `row.phase` and `row.percent`,
  then awaits `_run_analysis(..., on_phase=_on_phase)`
- Exceptions are caught per-row: `row.error` captured, phase goes to
  `failed` (red), other rows continue
- A `Live` context refreshes a `_render_batch_table(rows, concurrency)`
  at 4 Hz while any worker is still running, then paints one final
  snapshot

`_PHASE_STYLES` maps each phase name to a Rich style
(dim / cyan / yellow / green / red) so the table is readable at a
glance. `_format_elapsed` renders `MM:SS`.

### Part D — Persistence separation

History append (`append_result`) and raw CLOB sidecar
(`append_price_points`) run **sequentially** in the `analyze` command
**after** `_run_batch` returns. Rationale: a full history entry + raw
sidecar entry is larger than `PIPE_BUF` on Linux, so concurrent
`open(..., 'a')` writes can interleave byte-for-byte and corrupt the
JSONL. Single-writer-after-batch is both simpler and correct.

### Part E — `analyze` command rewrite

- Two new flags: `--summary-only` (bool, off by default) and
  `--concurrency/-j` (int, defaults to `ANALYZE_BATCH_CONCURRENCY`)
- Prompt text reads "Pick event(s) — `1`, `1,3-5`, `all`" and feeds
  the input through `_parse_selection`
- `N == 1` → routes through `_prompt_sub_market` (extracted from the
  old `prompt_event_selection`) so sub-market picker + sibling prompt
  are preserved
- `N >= 2` → each selected event resolves to its headline market
  (`_headline_market(group)`), goes through `_run_batch`, then each
  non-failed result is appended to history sequentially
- Per-event result panels render after the batch unless
  `--summary-only`; the final `_render_batch_summary` table always
  renders

### Part F — Config knob

`ANALYZE_BATCH_CONCURRENCY: int = int(os.getenv("ANALYZE_BATCH_CONCURRENCY", "1"))`
added to `config.py` with matching entry + comment in `.env.example`.
Default 1 = strict sequential. Ceiling 3 on Anthropic Tier 2 (1K RPM /
450K input-tok/min / 90K output-tok/min) since a full council +
synthesizer is ~80K input / 6K output per run; 3 in-flight is
~240K input/min, well under the input cap.

### Files touched

- `main.py` — multi-select prompt, `_parse_selection`,
  `_prompt_sub_market`, `_BatchRow`, `_PHASE_STYLES`,
  `_render_batch_table`, `_render_batch_summary`, `_run_batch`,
  `_run_analysis` gained `on_phase` parameter, `analyze` command
  rewrite
- `config.py` — `ANALYZE_BATCH_CONCURRENCY` knob
- `.env.example` — matching entry under new "Analyze batch mode" block
- `README.md` — Batch mode section under Flags, `-j`/`--summary-only`
  rows, `ANALYZE_BATCH_CONCURRENCY` in Tuning table
- `docs/analyze-pipeline.md` — S3 node updated to reference
  `prompt_event_multiselect()` and split single/batch paths
- `docs/roadmap.md` — C.3 marked shipped

### Follow-ups

- Pipeline diagram could grow a "Batch path" subgraph under SELECT
  showing the parallel branch; current edit just updates the S3 node.
  Adequate until batch mode becomes load-bearing for another phase.

---

## Session 41 — Stage 1 live + observation infrastructure

Phase 1 rollout stage 1 went live this session
(`PERSONA_V2_PREAMBLE=true`) after refining the V2 preamble content
and extending `reliability_diagram` to split populations by flag
label. Session 41 also shipped two pieces of observation
infrastructure (run_context persistence + raw CLOB trendlines
sidecar) before the flag flip. Aggregator untouched; baseline AGENT
PERSONAS still bit-identical to pre-rewrite when the flag is flipped
back off.

### Part A — `run_context` persistence (commit `4f3d65f`)

New top-level block on every history entry alongside `resolution` /
`analysis`:

- `analysis_ran_at`, `event_start_time`, `time_to_event_seconds`,
  `pre_or_live`, `structured_match_status_at_run`. Frozen at write
  time — the contract is "context that applied at the moment the
  council ran."
- `pre_or_live` prefers `structured_match.status` when present
  (scheduled / running / finished ⇒ pre / live / post) and falls back
  to `analysis_ran_at` vs `event_start_time`. `"unknown"` when no
  event start time is available.
- `history._new_entry` + upsert path populate the block.
  `load_history` in-memory default-fills for any pre-backfill read.
  `analytics_sql.py` hoists the five fields as top-level SQL columns
  on the `history` view — a future reliability-diagram filter
  `WHERE pre_or_live = 'pre'` needs no JSON extraction.
- `scripts/backfill_run_context.py` (new, zero API calls, atomic
  write, `.preRunContext.bak` sibling) tagged 437 existing rows —
  **287 pre, 125 live, 2 unknown**.

### Part B — Raw CLOB trendlines sidecar (commit `15c8e62`)

Full-fidelity YES/NO price points per analyze run captured to
`~/.pqi/price_points.jsonl` (new sidecar, decoupled from
`history.jsonl` so the hot-path JSONL / `pqi history list` don't
bloat).

- `market/clob.py` — `fetch_price_history` return type changed from
  `PriceHistory | None` to `tuple[PriceHistory, list[PricePoint],
  list[PricePoint]] | None`. Single caller gets raw points alongside
  aggregated metrics in one fetch.
- `main.py` — `_run_analysis` threads raw points out; `analyze`
  calls `sidecar_price_points.append_row` after `append_result`.
  Cache-hit runs skip the sidecar write.
- `history.py` — `append_result` now returns the upserted `run_id`.
- `sidecar_price_points.py` (new) — writer with uniform row schema,
  non-fatal write semantics.
- `scripts/backfill_price_points.py` (new) — atomic-write backfill
  mirroring the `backfill_clv.py` pattern. **303 / 437 rows (69.3%)
  tagged `available=true`.** 121 legacy rows (pre-session-22, no
  `clob_token_ids`) marked `no_tokens` (permanent). 13 transient
  failures (5 `fetch_failed`, 8 `insufficient_points`) will retry
  on next run.
- `analytics_sql.py` — conditional `price_points` DuckDB view
  (parallel to `clv_by_row`). `yes_points` / `no_points`
  unnest-able for per-bucket analytics; joinable to `history` /
  `verdicts` by `run_id`.

Enables future MFE / calibration / web-UI work without touching the
hot path.

### Part C — V2 preamble refinement + flag flip (commit `d0d87c0`)

Before flipping `PERSONA_V2_PREAMBLE=true`, tightened two weaknesses
in the session-40 V2 text:

- **Worked example → three-case spectrum.** Replaced the single
  `probability_score=70, probability_no_score=22` anchor with mild
  (4-pt gap), severe (10-pt gap + lower conf_no), and symmetric
  (small gap + matched confidences) cases. Teaches that ignorance
  mass scales with briefing depth, and explicitly permits honest
  sum≈100 when evidence is symmetric.
- **Anti-mirror check softened.** From hard "if sum == 100, stop
  and recheck" trigger to a signal pattern: sum-near-100 **paired
  with** matched confidences is the mirror-derivation diagnostic.
  Avoids inducing noise-injection on symmetric evidence.

`.env` gained `PERSONA_V2_PREAMBLE=true`. V2 preamble size went
from 4,367 → 5,783 chars. V1 untouched. Stage-1 population rollover
started: 16 V2 runs queued by end of session (112 `v2:preamble`
verdicts in the DuckDB view), 0 resolved yet.

### Part D — `reliability_diagram --flag` filter (commit `b8cc48a`)

With v1 + v2 verdicts landing in the same `history.jsonl`, a single
reliability report mixes populations. Extended the script to split:

- `--flag <label>` — accepts exact matches (`v1`, `v2:preamble`) or
  a trailing-star glob (`v2:*`). Row label is the UNION of each
  verdict's `prompt_flags` mapped through an inlined copy of
  `models.phase1_flag_label` (stdlib-only constraint preserved).
- Every run now prints `resolved rows by flag label: …` to stderr
  before any filter — set of filterable labels visible up front.
- Filtered runs write to `reliability_YYYYMMDD_<slug>.md`
  (e.g. `reliability_20260419_v2-preamble.md`) so unfiltered daily
  files aren't overwritten when producing a split pair.
- Unfiltered path verified bit-identical against the refreshed
  2026-04-19 baseline (409 rows, 100% v1).

### Part E — Documentation (commit `eb6a1fe`)

- `CLAUDE.md` `## Current State` refreshed to session 41; rollout
  observation state (16 queued / 0 resolved); reliability_diagram
  `--flag` added to What's live; aggregator-stability paragraph
  extended.
- `NEXT_SESSION_PROMPT.md` rewritten from "initiate rollout" to
  "observe stage 1." Next action gated on resolution count
  (≥ ~20 V2 resolved before comparison is actionable). Explicit
  guard rail against re-editing `_SHARED_PREAMBLE_V2` during stage
  1 measurement — content changes invalidate the tagged
  `v2:preamble` population.

### Files modified

- `pqi/agents/personas.py` — V2 preamble content refinement (Part C).
- `pqi/history.py`, `pqi/models.py`, `pqi/analytics_sql.py` — run_context
  block (Part A).
- `pqi/main.py`, `pqi/market/clob.py`, `pqi/sidecar_price_points.py` (new) —
  CLOB trendlines sidecar (Part B).
- `scripts/backfill_run_context.py` (new) — Part A backfill.
- `scripts/backfill_price_points.py` (new) — Part B backfill.
- `scripts/reliability_diagram.py` — `--flag` filter (Part D).
- `.env` (not committed; gitignored) — `PERSONA_V2_PREAMBLE=true`.
- `CLAUDE.md`, `NEXT_SESSION_PROMPT.md` — Part E.
- `docs/session-log.md` — this entry.
- `docs/phases/phase_1_signal_quality.md` — Session state updated to
  observe-stage-1 posture.

### Commits

- `4f3d65f` session 41: persist run_context block on every history
  entry.
- `15c8e62` session 41: raw CLOB trendlines sidecar + backfill.
- `d0d87c0` session 41: phase 1 v2 preamble — softer anti-mirror +
  three-case examples.
- `b8cc48a` session 41: reliability_diagram --flag filter for v1/v2
  side-by-side.
- `eb6a1fe` session 41: doc updates — stage 1 live,
  observe-not-escalate hand-off.

### Not touched

- `pqi/scoring.py`, `pqi/stats.py`, `pqi/agent_weights.py`,
  `~/.pqi/agent_weights.json`. Aggregator math stable since sessions
  31–32. Phase 0 §7.2 freeze preserved.
- Persona body texts (anchor snippets, advocacy blocks) — only the
  V2 preamble was re-edited, and only twice (soften + examples).
  Anchor/advocacy blocks remain at their session-40 form for stages
  2–4.
- Aggregator, `probability_source` rollup logic. Option B shadow
  layer still descriptive only; step 5 still gated on Phase 1 unified
  acceptance.

### Observation state at session end

```
$ pqi query "SELECT flag_label, COUNT(*) AS n FROM verdicts GROUP BY flag_label"
 v1             2878
 v2:preamble     112

$ resolved V2 runs / total V2 runs
 0 / 16
```

Next session's first query: check resolution count via
`pqi query "SELECT v.flag_label, COUNT(DISTINCT v.run_id) AS runs,
SUM(CASE WHEN h.resolution IS NOT NULL THEN 1 ELSE 0 END) AS resolved
FROM verdicts v JOIN history h USING (run_id) WHERE v.flag_label LIKE
'v2:%' GROUP BY v.flag_label"`. If `resolved < 20`, wait. If
`resolved >= 20`, run the two-filter pair and evaluate against the
four-criterion unified acceptance.

---

## Session 40 — Phase 1 Task 1.1 scaffold + analytics tagging

First code-touching session since session 32. Ships the Phase 1
rollout **scaffold** and **analytics tagging** — enough plumbing to
flip the four prompt mechanisms on independently and cleanly split
v1 vs v2 populations in history analytics. **Zero prompts in force
yet.** With every new flag default OFF, `AGENT_PERSONAS` is
bit-identical to pre-rewrite; the aggregator (`scoring.py` /
`stats.py`) is untouched.

### Part A — Rollout scaffold (prompt assembly + config flags)

- Seven flags added to `config.py` + `.env.example`, all default `false`:
  - `PERSONA_V2_PREAMBLE` — asymmetric-evidence SHARED_PREAMBLE (Mech 1)
  - `PERSONA_V2_ANCHOR_STATISTICIAN` / `_SENTIMENT` / `_SPECIALIST` /
    `_CONTRARIAN` — persona-specific anchors (Mech 2)
  - `PERSONA_V2_BULL_ADVOCACY` / `_BEAR_ADVOCACY` —
    advocacy-then-probability sequence (Mech 3)
- `pqi/agents/personas.py` rewritten:
  - Dual `_SHARED_PREAMBLE_V1` (verbatim prior preamble) and
    `_SHARED_PREAMBLE_V2` (asymmetric-evidence rewrite, ~3.7kB).
  - Four inline anchor snippets (statistician / sentiment / specialist
    / contrarian) in a `_ANCHORS` dict.
  - Two advocacy-sequence snippets (`_BULL_ADVOCACY_BLOCK` /
    `_BEAR_ADVOCACY_BLOCK`).
  - `_assemble(agent_key) -> str` injects selected snippets in front
    of the persona body's `Analysis Lens:` line based on flag state.
    `AGENT_PERSONAS` is computed once at module import.
- **Rules Lawyer exclusion preserved.** Rules Lawyer receives only
  `PERSONA_V2_PREAMBLE` when on — no anchor, no advocacy — matching
  its resolution-criteria-only discipline.
- Rationale: flags frozen at import time means flag/prompt drift is
  impossible even if `config` mutates mid-process. Baseline
  equivalence proven by assertion in `models.py` smoke test.

### Part B — Analytics tagging

Every verdict gets tagged with the exact flag set that was in force
when it was emitted, so `GROUP BY flag_label` cleanly splits v1 vs
v2 populations without re-running experiments.

- **`AgentVerdict.prompt_flags: dict[str, bool]`** added (sparse — only
  ON flags stored; empty dict ⇔ baseline / v1). Storage choice keeps
  JSONL file size unchanged for baseline rollouts.
- **`models.phase1_flag_label(prompt_flags)`** — module-level helper
  returning stable keys:
  - `{}` → `"v1"`
  - `{"preamble": True, "bear_advocacy": True}` →
    `"v2:bear_advocacy,preamble"` (sorted csv, stable join
    irrespective of dict iteration order).
- **`personas.flags_for_agent(agent_key)`** — returns the per-agent
  snapshot based on frozen `_PREAMBLE_FLAG` + `_AGENT_FLAG_SHORTCODES`
  dict. Rules Lawyer returns `{"preamble": True}` at most; Bull/Bear
  add their `*_advocacy` flag; the other four add their anchor.
- **`agents/council.py`** populates `prompt_flags` at
  `AgentVerdict(...)` construction.
- **`history.py` `_backfill_per_side_probability`** extended to
  default-fill `prompt_flags: {}` in-memory so fresh loads of
  un-backfilled files don't explode in the persistence layer.
- **`analytics_sql.py` verdicts view** restructured using a `WITH
  unnested AS (...)` CTE:
  ```sql
  SELECT *,
      v.prompt_flags AS prompt_flags,
      CASE
          WHEN v.prompt_flags IS NULL
            OR len(map_keys(v.prompt_flags)) = 0
          THEN 'v1'
          ELSE 'v2:' || array_to_string(
              list_sort(map_keys(v.prompt_flags)), ',')
      END AS flag_label
  FROM unnested
  ```
  DuckDB maps `dict[str, bool]` to `MAP<VARCHAR, JSON>`. Derived
  `flag_label` mirrors the Python helper exactly.
- **`scripts/backfill_prompt_flags.py`** (new, zero API calls,
  atomic `tmp + os.replace`, `.prePhase1.bak` sibling on first
  mutation, idempotent on re-run). Executed once in-session:
  **437 rows / 2871 verdicts tagged with `{}`**. All existing
  history now carries `flag_label = 'v1'`.

### Files modified

- `pqi/config.py` — 7 `PERSONA_V2_*` flag lines.
- `.env.example` — 7 `PERSONA_V2_*` lines with plain-English
  comments per configuration rule.
- `pqi/agents/personas.py` — dual preamble, anchor dict, advocacy
  blocks, `_assemble()`, `_PREAMBLE_FLAG`, `_AGENT_FLAG_SHORTCODES`,
  `flags_for_agent()`.
- `pqi/models.py` — `AgentVerdict.prompt_flags` field + module-level
  `phase1_flag_label()` + smoke-test assertions.
- `pqi/agents/council.py` — `flags_for_agent` import + `prompt_flags`
  in `_parse_verdict` kwargs.
- `pqi/history.py` — `_backfill_per_side_probability` extended to
  also fill `prompt_flags: {}`.
- `pqi/analytics_sql.py` — verdicts view rewritten with CTE +
  `prompt_flags` + derived `flag_label`. Module docstring updated
  to document both columns.
- `scripts/backfill_prompt_flags.py` — new.
- `docs/phases/phase_1_signal_quality.md` — Session state rewritten
  with scaffold + analytics tagging bullets and 5-stage
  independent rollout order.
- `CLAUDE.md` — `## Current State` session pointer bumped; scaffold
  + analytics tagging summarised; Aggregator stability section
  notes "Session 40 added Phase 1 rollout scaffold ... with zero
  aggregator changes".
- `NEXT_SESSION_PROMPT.md` — `Last rewritten` updated; priority
  section reworked around the 5-stage independent rollout.
- `docs/session-log.md` — this entry.

### Commits

- `a39b45b` session 40: phase 1 analytics tagging — prompt_flags on
  every verdict.
- (scaffold commit earlier in session — `models.py` /
  `agents/personas.py` / `agents/council.py` / `config.py` /
  `.env.example`)

### Not touched

- `pqi/scoring.py`, `pqi/stats.py`, `pqi/agent_weights.py`,
  `~/.pqi/agent_weights.json`. Aggregator math stable since
  sessions 31–32; Phase 0 §7.2 freeze preserved.
- Backtest pipeline, `scripts/reliability_diagram.py`,
  `scripts/backfill_clv.py`. All existing analytics continue to
  read the same fields plus now see `prompt_flags` / `flag_label`
  as additions.
- None of the V2 prompt mechanisms exercised on a live run. The
  rollout measurement happens in the next session.

---

## Session 39 — Phase 1 acceptance unified; Phase 2 split; CTO + CLAUDE alignments

Documentation-only session. Two-part: (a) applied the
`phase_1_acceptance_criteria_patch.md` drop-in to reconcile the Phase 1
doc's original Goals (per-agent drift only, pre-Phase-0) with Phase 0
retrospective §5.3's cluster-collapse + Bear calibration findings;
(b) following owner approval of the five external-review-v2 owner
decisions, renumbered Phase 2 into 2a/2b/2c, added capacity + recent-
window-skepticism to `CTO_Agent.md`, reconciled `CLAUDE.md` Project
Mission with Tier A intent, and fixed the stale haiku model name in the
tech stack table. No code, no aggregator touches, no knob retunes —
Phase 0 §7.2 freeze preserved throughout.

### Changes to `docs/phases/phase_1_signal_quality.md`

- **Goals section replaced** with unified four-criterion acceptance
  (patch §A):
  1. Cluster-pair r ≥ 0.85 fraction below 0.50 (cross-agent diversity
     — new, from retro §5.3).
  2. Per-agent `|drift|` > 0.10 on ≥2 agents (YES/NO autonomy —
     original Phase 1 criterion preserved).
  3. Bear Brier toward 0.22 with Bear's pairwise r to others below
     ~0.50 (calibration + independence — new).
  4. Council Brier holds or improves from 0.2176 (aggregate regression
     guard — new).
  All four must pass simultaneously. Option B step 5 backtest unblock
  condition updated to reference the four criteria.

- **Task 1.1 Proposed approach extended** from one mechanism
  (asymmetric evidence) to three (patch §B):
  - Mechanism 1 — asymmetric evidence mechanics on SHARED_PREAMBLE
    (targets mirror collapse).
  - Mechanism 2 — persona-specific evidence anchoring per-persona
    body (targets cluster collapse). Initial anchors for Statistician,
    Specialist, Sentiment, Contrarian listed as *first-draft
    suggestions, not settled* (owner note inline) — concept is load
    bearing, exact anchor content to be iterated during prompt
    drafting.
  - Mechanism 3 — Bear probability-after-advocacy sequence, optionally
    symmetric on Bull (targets Bear calibration).
  - Acceptance criteria for Task 1.1 updated to match unified goals.

- **Task 1.2 gains** explicit `~/.pqi/agent_weights.json` reset step
  before rollout (patch §C) — pre-rewrite weights partly absorb Bear
  miscalibration and would anchor the aggregator to a pathology the
  rewrite is attempting to remove.

- **Task 1.3 specification aligned** to retrospective's Pearson metric
  (was Spearman) for apples-to-apples with the baseline (patch §C).
  Acceptance criteria updated: cluster-pair fraction at r ≥ 0.85 below
  0.50, Bear's pairwise r with all other agents below ~0.50.

- **`Last updated:` bumped** to 2026-04-19, `Session state → Next action`
  updated (patch §D) to reference the three mechanisms + the agent
  weights reset + "do not commit until owner review."

### Scope guardrails honoured (patch §F)

- No code changes. Documentation only.
- No aggregator / scoring changes. `scoring.py` / `stats.py` untouched.
- No gate knob retunes. `BAND_SHRINKAGE_K=5`,
  `MIN_CONTRARIAN_PROBABILITY=0.60`, ROI bands, Kelly multiplier,
  `MIN_WIN_RATE_SAMPLES` all stand per Phase 0 §7.2.
- No persona additions or deletions. Seven-agent council preserved.
- No changes to Option B step 5 pre-registered criteria
  (`docs/option_b_migration_plan.md §7`); only the unblock condition
  is reworded.
- No changes to Phase 2, Phase 3, Phase 4, or CLAUDE.md
  "What NOT To Do" / Project Mission. Those are owner-decision items
  flagged separately (external review v2 §6.1 / §6.4 / §6.5).

### Source of change

- External review v2 (`pqi_external_review_2026_04_v2.md`) §3.1.1
  unified acceptance criteria
- Phase 0 retrospective (`docs/retrospectives/phase_0_edge_2026_04.md`)
  §5.3 cluster collapse + Bear miscalibration
- Patch file `phase_1_acceptance_criteria_patch.md`
- Governance: `CTO_Agent.md` §2 (mirror collapse is signal-quality,
  not sample-count), §6 (aggregator stability), §7 (RFC triage)

### Part B — Phase 2 renumbering (external review v2 §7.4 / §7.5 / §7.6)

Owner approved splitting the original `phase_2_execution_edge.md`
(covering MFE measurement, listener prototype, and auto-trading) into
three sub-phases with distinct gates:

- **Phase 2a — Tier A auto-BUY (auto-BUY-and-hold).** New file
  `docs/phases/phase_2a_tier_a_auto_buy.md`. Scope: `pqi buy
  <market_id>` CLI, Kelly-sized stake, pre-trade price recheck
  (default 3% drift abort), idempotency key on
  `(market_id, analysis_run_id)`, structured logging to
  `~/.pqi/fills.jsonl`, interactive 30s countdown on tty + explicit
  `--unattended` + `PQI_TIER_A_ENABLED=true` gate for non-tty, ghost-
  event ID-based probing per external review v2 §4.3(a), first-10-
  fills operator review before `--unattended` becomes acceptable on
  a tier. **Blocked on Phase 1** (cluster-collapsed signal turning
  into automatic fills is the fastest path to auto-loss generator).

- **Phase 2b — MFE analysis with chronological train/test.** Rewrite
  of the prior Task 2.1 into its own phase at
  `docs/phases/phase_2b_mfe_analysis.md` (renamed from
  `phase_2_execution_edge.md`). Key change per external review v2
  §5.3 / §6.4: **chronological train/test discipline promoted from
  risk note to task requirement**. 70/30 split by `event_start_time`;
  threshold swept on train window, locked, evaluated on test window.
  Verdict bands: chase (test net P&L ≥ +5% over hold-to-resolution
  after fees → unblock 2c), iterate ([−2%, +5%] → re-run with more
  data), park (< −2% → skip 2c). **Scoping unblocked in parallel
  with P1**; independent of P1's verdict because measures exit-side
  edge, not prediction edge.

- **Phase 2c — MFE listener prototype (Tier B precursor).** New file
  `docs/phases/phase_2c_mfe_listener.md`, absorbs prior Tasks 2.2–
  2.4 scope (read-only listener, manual review loop, automate/
  manual/park verdict). **Gated on Phase 2b verdict = "chase"**.
  Reuses ghost-event probe helper built in 2a. Sources threshold
  from 2b's retrospective (no re-tuning at listener runtime).

Dependency graph in `docs/phases/README.md` updated to reflect split
with explicit parallelism notes (P0+P1, P1+P2b, P2a blocks on P1 not
P2b, P2c blocks on P2b, P3 can follow P0 even if P2b parks). Anti-
goals list updated: "Auto-trading before MFE validation" split into
"Tier A before Phase 1 unified acceptance" and "Tier B before Phase
2b verdict = chase". Roadmap theme-to-phase map redistributed: B.3–
B.5 land in 2a, B.1 TUI adjacent to 2c, B.6 match watcher activates
only on 2c "automate" verdict. `docs/scaling_assessment.md` §10
auto-trading anti-goal and §11 open-questions reference updated to
name 2b and train/test.

### Part C — Adjacent alignments

- **`CTO_Agent.md` §5 — capacity axis added** (external review v2
  §6.5). New paragraph after "Prediction edge and execution edge are
  orthogonal" declares capacity (fillable liquidity at Kelly-sized
  stake) as the third orthogonal edge axis. Motivated by
  Polymarket Tier 2/3 eSports markets' ≤$500 depth at best bid/ask
  (external review v2 §4.4) — above low-to-mid four figures of
  bankroll, Kelly-optimal stake won't fill without eating spread
  that destroys edge.
- **`CTO_Agent.md` §7(a) — recent-window skepticism added** (external
  review v2 §6.5). New paragraph under "Validate the premise":
  "Look how well it's been doing lately" style claims on small n
  (Wilson half-width ±17pp on a 30-row window at 70% point estimate)
  get extra skepticism. Demand rolling WR with CI, the prior window
  for comparison, and the same metric on pre-registered holdout.
- **`CLAUDE.md` Project Mission reconciled** (external review v2
  §6.1). Advisor-as-default preserved; Tier A and Tier B explicitly
  named as approved future modes, both defaulting to off, each gated
  on its respective phase's acceptance criteria.
- **`CLAUDE.md` tech stack haiku name fix** (external review v2
  §6.2). Line 43 `claude-haiku-3-5` → `claude-haiku-4-5-20251001`
  (matches current production model id).

### Files modified

- `docs/phases/phase_1_signal_quality.md` — Goals section replaced;
  Task 1.1 Proposed approach + Acceptance criteria replaced; Task 1.2
  adds agent_weights.json reset bullet; Task 1.3 Implementation +
  Acceptance criteria updated (Spearman → Pearson); header +
  Session state `Next action` updated.
- `docs/phases/phase_2_execution_edge.md` → renamed to
  `docs/phases/phase_2b_mfe_analysis.md`; content fully rewritten
  to narrow scope to Task 2.1-equivalent with train/test as task
  requirement.
- `docs/phases/phase_2a_tier_a_auto_buy.md` — new.
- `docs/phases/phase_2c_mfe_listener.md` — new (absorbs prior 2.2–
  2.4 content).
- `docs/phases/README.md` — status table replaces single Phase 2
  row with 2a/2b/2c; dependency graph redrawn with parallelism
  notes; anti-goals list auto-trading split; theme-to-phase map
  redistributed.
- `docs/roadmap.md` — phase table Phase 2 row split 2a/2b/2c;
  theme-to-phase map updated (B.1/B.3–B.5/B.6 redistributed).
- `docs/scaling_assessment.md` — §10 auto-trading anti-goal and
  §11 question 2 updated to name 2b + chronological train/test.
- `CLAUDE.md` — `## Current State` session pointer bumped; Phase
  2 row split; Project Mission reconciled with Tier A / Tier B;
  tech stack haiku model id fixed; Last updated line extended.
- `CTO_Agent.md` — §5 capacity axis paragraph added; §7(a) recent-
  window skepticism paragraph added.
- `NEXT_SESSION_PROMPT.md` — `Last rewritten` extended; Phase 2
  reference map split; parallelisable section updated to name 2b
  and name 2a/2c gates; Theme B routing updated.
- `docs/session-log.md` — this entry (extended).

### Not touched

- `pqi/agents/personas.py`, `pqi/scoring.py`, `pqi/stats.py`,
  `pqi/agent_weights.py`, `~/.pqi/agent_weights.json`, `.env`,
  `config.py`, `pqi/market/`, `pqi/ingestion/`, all runtime files.
  Task 1.1 prompt drafting, Phase 2a `pqi buy` implementation, and
  Phase 2b MFE script all start in later sessions after owner
  reviews the expanded phase doc set.

---

## Session 38 — Phase 0 Task 0.5: Edge retrospective & verdict (Phase 0 closed)

Fifth and final Phase 0 workstream. Synthesis-only — no code
changes. Folded the three measurement outputs (Tasks 0.2 CLV,
0.3 reliability, 0.4 per-band calibration) into the retrospective
file `docs/retrospectives/phase_0_edge_2026_04.md`, issued the
phase-closing verdict, and updated `docs/phases/README.md` +
`CLAUDE.md` to reflect Phase 1 handoff.

### Verdict — inconclusive at current n

Three measurement streams align on the same reading:

- **Prediction edge (reliability diagram, Task 0.3):** council
  Brier **0.2176** beats the 0.25 chance baseline by ~3pp
  (n=385, 2,668 per-agent verdicts). Real but small; at a
  bootstrap 95% CI around the Brier on the order of ±0.02,
  the delta is likely real but not sharp. The council is a
  lightly-advantaged predictor — *not* a coin flip.
- **Execution entry quality (pre-event CLV, Task 0.2):** mean
  **−0.0167** on n=44 (67.7% coverage; 21 unavailable rows
  all predate session-22 `clob_token_ids` persistence —
  permanent). Directionally unfavourable (we pay ~1.7pp vig
  on entry) but CI straddles zero. Valorant worst category
  (−0.0318 n=17), LoL neutral (−0.0007 n=14, stddev 0.018).
- **Gate calibration (Task 0.4):** no band clears ≥1 Wilson-CI
  width in either direction on 62 in-band resolved BUYs.
  [0.60, 0.70) n=23 and [0.70, 0.80) n=15 well-calibrated on
  point estimate — also break-even EV on entry.

Netted against realized April PnL of −0.575/share on 65 BUYs
(session-34 finding), the picture is: small prediction edge
being absorbed by entry timing + correlated agents, producing
near-zero realized PnL before fees.

### Most actionable finding: cluster-level mirror collapse

Session 36's reliability diagram already quantified the
correlation structure, but Task 0.5 surfaces it as the primary
Phase 1 driver:

- 9 of 21 agent pairs sit at Pearson r ≥ 0.85.
- The 9 high-correlation pairs form an **identifiable
  5-agent cluster**: rules_lawyer / statistician / sentiment /
  specialist / contrarian. Ten cluster-internal pairs, nine at
  r ≥ 0.85.
- **Bull** sits partially outside (cluster pairs r ~0.58–0.63),
  weakly independent. Bull is also overconfident on
  favourites: [0.80, 0.95) n=16 predicted 0.844 mean, realized
  0.625 — gap −22pp.
- **Bear** is structurally independent (pairwise r ∈ [−0.17,
  +0.04]) but is the only agent with Brier (0.2810) **above**
  the 0.25 chance baseline. Concentrates 96% of its
  predictions in [0.09, 0.50) where it is +0.128
  miscalibrated.

Effective council diversity is therefore ~3 signals, not 7,
and one of those three (Bear) is degrading accuracy. This
refines the session-26/32 "mirror collapse" framing from
"uniform" to "cluster-level." The dynamic-weight layer
(session 25) is doing exactly what it should — council Brier
(0.2176) is marginally below the cluster floor (0.2180
rules_lawyer) because Bear's weight is appropriately reduced.

### Recommended next phase: Phase 1

Retrospective §7 lays out:

- **Primary: Phase 1** (SHARED_PREAMBLE autonomy rewrite) —
  the only measurement-supported high-leverage lever on this
  data. Three measurable post-rewrite checks: (a) cluster-pair
  fraction at r ≥ 0.85 drops below 0.50, (b) Bear Brier falls
  toward the 0.22 cluster without Bear losing independence,
  (c) council Brier holds or improves from 0.2176. Re-run
  `scripts/reliability_diagram.py` after every prompt change.
- **Parallel: Phase 2 MFE analysis** — scoping work unblocked;
  produces the third CTO-§5 edge metric via DuckDB + CLOB
  history, analogous to Task 0.2. Listener / auto-trader still
  deferred.
- **Deferred: Phase 3 deployment portability** — verdict was
  inconclusive, not positive. Revisit after P1 produces a
  measurable Brier improvement.
- **Option B step 5 backtest stays blocked** until P1's three
  checks pass. Running it on a mirror-collapsed signal
  guarantees failure on the pre-registered ΔBrier ≤ −0.005
  criterion.

### Explicitly no knob changes

Retrospective §7.2: `BAND_SHRINKAGE_K=5`,
`MIN_CONTRARIAN_PROBABILITY=0.60`, ROI bands, Kelly
multiplier, `MIN_WIN_RATE_SAMPLES` all stand. Re-tuning on the
same 62 rows that produced `band_stats` is dressed-up
overfitting per `CTO_Agent.md` §2. Revisit K when (a) [0.50,
0.60) reaches n ≥ 20 (currently 8), or (b) a pre-registered
hold-out period is formally defined.

### Files modified

- `docs/retrospectives/phase_0_edge_2026_04.md` — prepended §0
  Executive summary; replaced the Task-0.4 "Open items" block
  with full CLV (§4), reliability (§5), verdict (§6), and
  recommended-next-actions (§7) sections; renumbered Data
  artefacts → §8 and Changelog → §9. Status header flipped
  from "in-progress" to "closed."
- `docs/phases/README.md` — status table: P0 → done (with
  verdict note), P1 → next, P2 MFE scoping unblocked, P3
  deferred. Added a retrospective-link paragraph beneath the
  table.
- `CLAUDE.md` — `## Current State` rewritten to lead with the
  Phase 0 closure + verdict; phase status table updated;
  session 38 added atop the Recent sessions list; `## Next
  Session Prompt` rewritten to make Phase 1 Task 1.1 the
  default entry point (was Phase 0 Task 0.5).
- `docs/phases/phase_0_edge_validation.md` — session state
  updated (below).

### Files unchanged (no code touch)

- `pqi/**` — no source-code changes this session.
- `~/.pqi/stats.json`, `~/.pqi/clv.jsonl`,
  `~/.pqi/reports/reliability_20260418.md` — all read; none
  modified.

### Verification

`pqi query` cross-checks done during retrospective drafting:

- `SELECT COUNT(*), AVG(clv), stddev(clv), MIN(clv), MAX(clv)
  FROM clv_by_row WHERE available = true` → n=44, mean
  −0.0167, stddev 0.0640, min −0.26, max +0.12. Matches CLAUDE
  §Current State.
- `SELECT unavailable_reason, COUNT(*) FROM clv_by_row WHERE
  available = false GROUP BY unavailable_reason` → `no_tokens`
  21 (100% of the 21-row gap is the permanent pre-session-22
  structural hole; no recoverable unavailability).
- Per-category breakdown join (via CAST(run_id AS VARCHAR) on
  both sides of the join): valorant −0.0318 n=17, LoL
  −0.0007 n=14, counter-strike −0.0131 n=8, plus three
  very-thin-n categories. Matches retrospective §4.2.
- `SELECT COUNT(*) FROM history WHERE legacy=false AND
  result.recommendation LIKE 'BUY%' AND resolution.winning_side
  IS NOT NULL` → 63 (the 62 in-band + 1 out-of-band + 2
  intermediate-excluded accounting from Task 0.4 §1 reconciles
  against `stats.json` sample_size=65 — the 2-row delta is the
  intermediate settlements excluded from `band_stats` by
  construction).

### Notes for next session (Phase 1 Task 1.1)

- Task 1.1 is a SHARED_PREAMBLE rewrite — **no commit until
  user review.** Prompt-level changes are cheap to make and
  expensive to regress (council Brier is already only 3pp
  above chance).
- Keep preamble under ~1500 tokens for prompt-cache
  efficiency (session 27).
- The rollout order recommended in `phase_1_signal_quality.md`
  Task 1.2 (rules_lawyer → statistician → sentiment →
  specialist → contrarian → bull → bear) is now even more
  defensible: the first five are the collapsed cluster (so the
  lever applies most directly), Bull/Bear are the two
  structurally-independent agents and need the preamble
  validated on lower-stakes voices first.
- Measure with
  `python -m scripts.reliability_diagram` after every change.
  Baseline to beat: council Brier 0.2176, cluster-pair
  fraction 9/21 = 43%.
- Step 5 backtest (`docs/option_b_migration_plan.md` §7)
  stays blocked — no exceptions.

---

## Session 37 — Phase 0 Task 0.4: Per-band calibration check

Fourth Phase 0 workstream, no-code-change. Joins the EV-gate's
live `band_stats` thresholds against the realized BUY hit rate
per band, with 95% Wilson CIs and delta columns. Result: the
retrospective file `docs/retrospectives/phase_0_edge_2026_04.md`
gains §1–§6 (scope, per-band table, flagged-bands summary, K
recommendation, Task 0.5 open items, data artefacts). No pipeline
/ aggregator / gate changes; the phase doc's task 0.4 acceptance
boxes are ticked.

### What landed

- **`docs/retrospectives/phase_0_edge_2026_04.md`** — new. Task
  0.4 populates the per-band calibration section; Task 0.5 will
  fold in the CLV (Task 0.2) and reliability (Task 0.3) sections
  plus the verdict. File is already structured so the later
  rounds of writing have placeholders to extend rather than
  re-plan.

- **`docs/analytics_queries.md` Q4** — retargeted from
  directional-winner semantics to `winning_side` semantics. The
  reason is apples-to-apples with the gate: `stats.py`'s
  `_side_price_and_won` — which produces `band_stats.smoothed_wr`
  — only counts entries with a clean `resolution.winning_side`.
  Swapping Q4 to match means the table you get from `pqi query`
  aligns row-by-row with `~/.pqi/stats.json` `band_stats`.
  (Directional-winner semantics remain in Q2 and the Task-0.3
  reliability work; the choice is documented inline in Q4.) Also
  added two extra bucket cases so out-of-band prices are visible
  in the result (`[0.00, 0.09)` and `(0.95, 1.00]`).

- **`docs/phases/phase_0_edge_validation.md`** — status line
  flipped to "Tasks 0.1 + 0.2 + 0.3 + 0.4 done; 0.5 next", task
  0.4 acceptance boxes ticked with the actual findings, session
  state "Next action" updated to Task 0.5. "Notes for next
  session" traded the outdated session-35 early-finding bullet
  for the Task 0.4 numbers and the winning_side vs directional
  semantic clarification.

### Numbers (winning_side semantics, matches live gate)

| Band | n | wins | raw WR | smoothed WR (thr) | mean price | Wilson 95% CI | CI width | Δ(real − thr) | Δ(real − price) |
| --- | --: | --: | --: | --: | --: | --- | --: | --: | --: |
| [0.09, 0.50) | 0 | 0 | — | 0.6923 *(global)* | — | — | — | — | — |
| [0.50, 0.60) | 8 | 3 | 0.3750 | 0.4970 | 0.5606 | [0.137, 0.694] | 0.557 | −0.122 | **−0.186** |
| [0.60, 0.70) | 23 | 15 | 0.6522 | 0.6593 | 0.6526 | [0.449, 0.812] | 0.363 | −0.007 | −0.000 |
| [0.70, 0.80) | 15 | 11 | 0.7333 | 0.7231 | 0.7537 | [0.481, 0.891] | 0.411 | +0.010 | −0.020 |
| [0.80, 0.95) | 16 | 15 | 0.9375 | 0.8791 | 0.8384 | [0.717, 0.989] | 0.272 | +0.058 | **+0.099** |

Coverage: 62/65 resolved BUYs (95.4%) land in the 5 bands. 1
row out-of-band (price 0.9995, gated by `MAX_TRADEABLE_PRICE`),
2 intermediate settlements excluded from `band_stats` by
construction (included in `classify_outcome`'s PnL-based
`correct_buy`/`wrong_buy`).

### Reading

- **No band clears the "≥ 1 CI width" flag** in either
  direction. At n=8–23 per occupied band the Wilson CIs (widths
  0.27–0.56) are wide enough that neither a true under-rejection
  nor over-rejection is statistically evident.
- **[0.50, 0.60) point-estimate under-rejects** (realized 0.375
  vs mean price 0.561, Δ −0.186; vs threshold 0.497, Δ −0.122).
  Direction is consistent with session-31's motivation for the
  banded gate. The current threshold (0.497 < band floor 0.500)
  already rejects all future BUYs in this band — so the
  calibration concern is retrospective only. Forward-looking,
  the gate here is maximally protective.
- **[0.60, 0.70) is the canonical well-calibrated band.** n=23
  (densest), realized 0.652 vs threshold 0.659 vs mean price
  0.653 — Δs all within ±0.007. Break-even EV on entry, matches
  session-34's finding of marginal executed-edge at the
  aggregate level.
- **[0.70, 0.80)** on threshold (Δ +0.010), slightly below mean
  price (Δ −0.020). Break-even-ish, within CI.
- **[0.80, 0.95) is the best band on both metrics.** Realized
  0.938 vs threshold 0.879 (Δ +0.058), vs mean price 0.838 (Δ
  +0.099). Point estimate says the gate might be slightly
  over-rejecting; Δ 0.058 ≪ CI width 0.272 so it does not clear
  the flag. Recorded as "directional over-rejection candidate;
  do not retune." This is also the band where we find the
  clearest point-estimate entry-edge (+9.9pp above mean price)
  — consistent with high-conviction favourites being mispriced
  in our favour.

### `BAND_SHRINKAGE_K` recommendation

**Keep K=5.** The phase-doc guardrail is explicit: do not tune
K on the same data used to compute `band_stats`. The math:

- K=5 blends 5 pseudo-observations of the global rate (0.6923)
  into each band's raw rate. For [0.50, 0.60): smoothed =
  (3 + 5·0.6923) / (8+5) = 0.497 (+12.2pp toward global).
- Lowering to K=2 would drop [0.50, 0.60) smoothed to 0.439
  (more protective there) but raise [0.80, 0.95) to 0.909 (less
  protective). Raising to K=10 flips both. The directions are
  opposite because raw WRs straddle the global rate.
- Right answer is "the K that minimises miscalibration on
  **future** BUYs" — and we have no holdout yet. Pick K=5 and
  wait for more data before touching it.

Revisit when either (a) [0.50, 0.60) reaches n≥20 (currently 8
— slow rate since the current gate already rejects most prices
in this band, so accumulation here is artificially constrained),
or (b) a pre-registered hold-out period is defined with enough
BUY landings to tell K=5 from K=3 from K=10.

### Method notes

- Outcome semantics matter more than sample size here. The
  winning_side variant (Task 0.4, this session) matches the
  gate's own book-keeping. The directional variant (Task 0.3)
  treats intermediate settlements as PnL-based wins/losses —
  useful for agent-level calibration because agents score
  probabilities not winning_side, but wrong for gate
  calibration because the gate doesn't see intermediate
  settlements either. The two sets of realized rates differ by
  up to 4pp in the middle bands. Task 0.5 should anchor on
  winning_side and note the directional deltas as a caveat.
- Wilson CI was computed directly (z = 1.959964 for two-sided
  95%). Rule of thumb: for n=10 with p=0.5, Wilson halfwidth is
  ~0.245 — i.e. CI width ~0.49. Our band widths (0.27–0.56)
  are consistent with this; the data just isn't dense enough
  yet to make "≥ 1 CI width" a crossable bar at any band.
- 3 of 65 resolved BUYs sit outside the band denominator: one
  at price 0.9995 (realized win, but outside the 0.95 top of
  the bands — gated by `MAX_TRADEABLE_PRICE`), and two
  intermediate settlements (no clean `winning_side`). All
  accounted for; no silent drops.

### What this does NOT answer

- **Whether the live gate is improving PnL.** Task 0.4 is a
  threshold-calibration check. The "is the gate actually saving
  us money?" question requires a gate-vs-no-gate counterfactual
  on the same market set, which is Task 0.5 territory.
- **Whether the bands themselves are the right bands.** The
  session-31 band boundaries are a code constant — this session
  doesn't reopen that question.
- **Whether the "5-agent mirror collapse" from Task 0.3
  interacts with the gate's hit rate.** That's a signal-quality
  question (Phase 1), not a calibration question.

---

## Session 36 — Phase 0 Task 0.3: Reliability diagram + cluster-collapse diagnostic

Third Phase 0 code workstream. Per-agent + council-aggregate
calibration table over the full non-legacy resolved set, bucketed
into the EV-gate-aligned price bands. Operates over **385 resolved
rows / 2,668 verdicts** — agent-level calibration uses the entire
resolved population, not just the 71 BUY-triggered rows. Output: an
ASCII reliability table to stdout plus a versioned markdown report at
`~/.pqi/reports/reliability_YYYYMMDD.md`. No pipeline / aggregator /
gate changes.

### What landed

- **`scripts/reliability_diagram.py`** — stdlib-only script
  (`json` + `math` + `statistics`); mirrors the style of
  `scripts/backfill_clv.py`. Walks `~/.pqi/history.jsonl` once,
  filtering to non-legacy rows where both `result.market.yes_price`
  and `resolution.final_yes_price` are set. Outcome is the
  **directional winner**: `final > entry ⇒ YES (1)`, else `0`. This
  matches Q2's `_directional_winner` semantics and unlocks the full
  resolved set for agent calibration (vs only the 71 BUYs we'd get
  from `correct_buy / wrong_buy`). Eight calibration rows are
  emitted: `rules_lawyer`, `bull`, `bear`, `statistician`,
  `sentiment`, `specialist`, `contrarian`, `council`. Council uses
  `result.final_score` directly (the YES-side confidence-weighted
  aggregate). `--no-write` skips the file write for quick iteration;
  `--history` lets the script point at a fixture.

- **Bands.** Primary view uses the EV-gate price bands `[0.09, 0.50)
  / [0.50, 0.60) / [0.60, 0.70) / [0.70, 0.80) / [0.80, 0.95)` so
  Task 0.4 can join the per-prediction realisation rate against
  `PerceivedStats.band_stats`. Two bookend bins `[0.00, 0.09)` and
  `[0.95, 1.00]` are appended so no prediction is silently dropped —
  predicted P(YES) can fall anywhere in [0, 1] even though *price*
  bands are gated to [0.09, 0.95]. In practice the bookends are 0–4
  rows for every agent on the current data; they exist as a
  data-loss tripwire.

- **Per-bin metrics.** For every (agent, band) cell: `n`, mean
  prediction, realized YES rate, gap (`realized − pred_mean`,
  negative ⇒ overconfident on YES), Wilson 95% CI on the realized
  rate. Cells with `n < 10` are flagged `thin`. Stddev / Brier are
  computed via `statistics.pstdev` and a manual `(p − y)²` sum (no
  numpy/scipy dependency).

- **Per-agent Brier score** computed across all that agent's
  predictions (not per-band). Brier = mean((p − y)²); chance-baseline
  reference is 0.25 (always-predict-0.5). All scores are written to a
  small ASCII table inside the report.

- **Cross-agent diversity diagnostic.** Per row, computes population
  stddev across the agent predictions; per agent pair, computes the
  Pearson correlation of their joint prediction vectors. Reports both
  the global mean pairwise Pearson and the **fraction of pairs with
  r ≥ 0.85**. The cluster-fraction is the diagnostic — global mean
  alone is misleadingly low when a single anti-correlated outlier
  (Bear, here) drags the mean down. Narrative bands:
  - `≥ 50%` of pairs at r ≥ 0.85 ⇒ "cluster-level mirror collapse"
  - `≥ 25%` ⇒ "partial-collapse signal"
  - else ⇒ "no mirror-collapse signal"

- **`docs/analytics_queries.md`** — added a `## Companion artifacts`
  pointer at the bottom describing the reliability script and its
  output location.

### Headline numbers (385 resolved rows)

**Brier scores:**

```
  agent           n     Brier
  council         385   0.2176   ← best
  rules_lawyer    382   0.2180
  specialist      384   0.2195
  sentiment       385   0.2196
  statistician    385   0.2199
  contrarian      383   0.2215
  bull            383   0.2370
  bear            366   0.2810   ← worse than 0.25 baseline
```

The council aggregate beats every individual agent — confidence
weighting is doing real work. Bear is the lone agent above the
always-0.5 baseline (0.281 vs 0.25), confirming session 34's lean-
rate finding (49.1% on n=342) wasn't a measurement artifact.

**Council reliability** (predicted P(YES) vs realized rate):

```
  band            n    pred_mean   realized   gap
  [0.09, 0.50)   187     0.354      0.326    -0.028   well-calibrated
  [0.50, 0.60)    93     0.553      0.516    -0.037   well-calibrated
  [0.60, 0.70)    88     0.642      0.705    +0.063   underconfident on YES
  [0.70, 0.80)    15     0.734      0.800    +0.066   underconfident; thin
  [0.80, 0.95)     2     0.849      1.000    +0.151   thin
```

**Per-agent overconfidence on favorites.** Bull is overconfident
across the whole upper tail: gap −0.10 to −0.22 in [0.50, 0.95).
Specifically `[0.80, 0.95)` n=16 realized 62.5% on a mean prediction
of 84.4% — a 22pp overconfidence on Bull's most-conviction calls,
n large enough to take seriously. Statistician / sentiment /
specialist / contrarian are all close-to-calibrated. Rules Lawyer
shows mild over-shoot on YES in the `[0.70, 0.80)` band (gap +0.080,
n=50) but is otherwise tight.

**Bear pathology.** Bear concentrates 96% of its predictions in
`[0.09, 0.50)` (n=351 of 366), which itself shows a +0.128 gap
(predicts 36%, realizes 48%). The few times Bear ventures above 0.50
the predictions are wrong in the opposite direction (n=5 in
[0.60, 0.70), realized 20%; n=1 in [0.70, 0.80), realized 0%).
Aggregate Brier sits at 0.281. Bear is functionally a "lean NO"
sticker rather than a calibrated probability estimator, and its
calibration on the dominant band is also 13pp off. Phase 1 priority.

**Cross-agent diversity:**

```
  Mean pairwise Pearson:      0.557
  Mean per-row stddev:        0.094
  Pairs with r ≥ 0.85:        9 / 21  (43%)
```

The global mean (0.557) is misleadingly low — Bear's near-zero and
negative correlations drag it down by about 0.30. The cluster
fraction tells the real story: 9 of 10 cluster-internal pairs from
{rules_lawyer, statistician, sentiment, specialist, contrarian} sit
at r ≥ 0.85, and the remaining odd-one-out (specialist↔contrarian)
is r=0.804. Bull is weakly tied to the cluster (~0.6); Bear is
essentially uncorrelated with everything else (−0.17 to +0.04). This
**partial-collapse** characterisation is more accurate than the
"mirror collapse across all agents" framing from sessions 26 and 32:
2 of 7 agents (Bull, Bear) retain measurable independence, 5 of 7 do
not.

```
  pair                              r
  statistician↔specialist        0.918
  statistician↔sentiment         0.911
  rules_lawyer↔statistician      0.911
  rules_lawyer↔sentiment         0.904
  sentiment↔specialist           0.901
  rules_lawyer↔specialist        0.895
  sentiment↔contrarian           0.871
  rules_lawyer↔contrarian        0.866
  statistician↔contrarian        0.862
  specialist↔contrarian          0.804
  rules_lawyer↔bull              0.628
  bull↔sentiment                 0.621
  bull↔contrarian                0.603
  bull↔specialist                0.591
  bull↔statistician              0.575
  bear↔statistician              0.039
  bear↔sentiment                 0.027
  bear↔specialist                0.023
  rules_lawyer↔bear             -0.020
  bear↔contrarian               -0.062
  bull↔bear                     -0.170
```

### Implications for Phase 1

The Phase-1 SHARED_PREAMBLE rewrite should be evaluated against three
post-rewrite reliability re-runs:

1. Does the cluster-pair fraction at r ≥ 0.85 drop below 0.50?
2. Does Bear's Brier fall toward the 0.22 cluster (without losing
   its independence)?
3. Does council Brier hold or improve from 0.2176?

If (1) and (2) move in the right direction but (3) regresses, the
prompt change destroyed signal *and* signal coherence — bad outcome.
If (1) and (3) improve but (2) gets worse, Bear specifically may
need its own treatment (per-agent prompt, not just SHARED_PREAMBLE).

### Subtle bits

- **Date stamps are UTC.** Filename uses `datetime.now(timezone.utc)`
  → first run produced `reliability_20260418.md` even though the
  user's local date is 2026-04-19 (UTC offset). Re-running on
  another day creates a new file rather than overwriting; same-day
  re-runs replace the file. Matches `clv.jsonl`'s convention.
- **Pearson without scipy.** Implemented directly from the
  `cov / (sx · sy)` formula; returns `None` when `denom == 0`. Good
  enough for the diversity diagnostic; no numpy needed.
- **Wilson CI.** `z = 1.96`; falls back to `(0.0, 0.0)` for `n = 0`.
  Wider than the normal-approximation interval at small `n`, which
  is what we want for the thin bins.
- **Excluded rows.** Legacy rows (no `verdicts`) and rows with
  `resolution = None` are skipped. Rows missing `final_yes_price`
  are skipped (10 of 408 resolved rows). Verdicts whose `agent_name`
  isn't in `AGENT_KEYS` are skipped silently — the seven canonical
  names are enumerated in the script.

### Files touched

- `scripts/reliability_diagram.py` — new (stdlib only).
- `docs/analytics_queries.md` — added `## Companion artifacts`
  section pointing at the script + report path.
- `docs/phases/phase_0_edge_validation.md` — Task 0.3 acceptance
  criteria checked off; status line updated to "Tasks 0.1+0.2+0.3
  done; 0.4 next"; session state extended with the headline-numbers
  table and a Phase-1 evaluation criteria block.
- Runtime: `~/.pqi/reports/reliability_20260418.md` created.

### Non-goals

- No PNG / matplotlib output; ASCII + markdown is sufficient and
  keeps the script dependency-free. A PNG can be added later if the
  retrospective wants it.
- No write-back to `~/.pqi/stats.json`. Task 0.4 reads `band_stats`
  alongside the realisation rates here; tuning `BAND_SHRINKAGE_K`
  is a Task 0.5 recommendation, not an automated feedback loop.
- No coupling into the runtime council. The reliability diagram is
  a measurement artifact, not an input to scoring or sizing.

---

## Session 35 — Phase 0 Task 0.2: Pre-event CLV backfill

Second Phase 0 code workstream. Every resolved BUY in `history.jsonl`
now carries a computable pre-event CLV (close-line value) — the
difference between the CLOB market price at `event_start_time` and
the price we entered at. Positive CLV ⇒ we bought below the pre-event
market mark (entry-quality edge, independent of whether the bet
eventually won). The feature ships as a one-shot backfill script, a
sidecar JSONL, and a DuckDB view. No pipeline / aggregator / gate
changes.

### What landed

- **`scripts/backfill_clv.py`** — one-shot backfill. For every
  non-legacy resolved BUY:
  1. pick the bought-side token from `market.clob_token_ids` (index 0
     for BUY YES, index 1 for BUY NO — matches Gamma's `clobTokenIds`
     order),
  2. fetch CLOB `GET /prices-history?market={token}&interval=max&
     fidelity=60` (inline `httpx`, no coupling to private
     `_fetch_side`),
  3. pick the latest point with `t <= event_start_time` (last-before-
     target — see bucket-policy note below),
  4. compute `clv = bucket_price - entry_price`, where
     `entry_price = yes_price` for BUY YES else `1 - yes_price` for
     BUY NO.
  Uniform-schema output at `~/.pqi/clv.jsonl`, one row per candidate.
  Every row carries `run_id`, `available` (bool),
  `unavailable_reason` ∈ {`no_event_start`, `no_tokens`,
  `fetch_failed`, `no_bucket_before`, null when available},
  `bucket_drift_minutes` (≤ 0 by construction), `bucket_price`,
  `entry_price`, `clv`. Idempotent: a second run preserves cached
  rows that either succeeded or hit a permanent unavailable reason
  (`no_event_start` / `no_tokens`); transient failures
  (`fetch_failed`, `no_bucket_before`) are retried each run.
  `--rebuild` discards the sidecar entirely.

- **`pqi/analytics_sql.py`** — extended to register a third view
  `clv_by_row` reading `~/.pqi/clv.jsonl`. Registered *only* when
  the sidecar exists; if it's missing the view simply isn't created
  and any query against it raises DuckDB's native
  `CatalogException: Table with name clv_by_row does not exist!`.
  `get_connection(clv_path=...)` accepts an override to make the
  missing-sidecar path testable. No change to `history` / `verdicts`
  views.

- **`docs/analytics_queries.md`** — Q5 became the CLV query block
  (distribution, per-category, coverage audit, CLV-vs-outcome join);
  the old "missed opportunities" query is now Q6. Header table,
  column descriptions, and tips section updated to cover
  `clv_by_row` columns and the bucket-policy caveat.

### Bucket policy: last-before-target (not closest)

The CLOB retention probe against token
`23237224170184798654257898614973049777702675268186078584584564326529707151667`
(NO side of run `8735ea38`, event start `2026-04-10T18:00:00Z`)
returned, around the target:

```
[92] 2026-04-10T16:00:59  p=0.8350  drift=-119.0min
[93] 2026-04-10T17:00:06  p=0.8350  drift= -59.9min  ← last-before-target
[94] 2026-04-10T19:00:56  p=0.9995  drift= +60.9min
```

The jump `0.835 → 0.9995` across a single post-event bucket is a
Polymarket resolution spike. A "closest to target" selection policy
would have picked [94] in other runs (whichever side of the target
has a smaller drift), silently leaking post-event information into
the CLV signal. Last-before-target enforces a hard pre-event
anchor; the max drift we'll see is bounded by the point spacing
(~60min at `fidelity=60`, wider when CLOB started recording after
the target). The sidecar records `bucket_drift_minutes` so the
retrospective can caveat runs with big drifts.

`interval=max` is important: CLOB returns the *full market lifecycle*
(e.g. the probed market: 95 points spanning 5.8 days), not a
calendar-relative window. This means even rows with
`event_start_time` > 1 week before backfill can be computed as long
as the token IDs are stored — which was the original worry in the
Task 0.2 risk list.

### Coverage (65 resolved BUYs)

- **Available: 44 (67.7%)** — CLV computed end-to-end.
- **Unavailable: 21 (32.3%)** — all `no_tokens`. These rows predate
  session-22's `clob_token_ids` persistence; there's no way to
  recover them without re-fetching every market from Gamma by ID
  and matching timestamps, which isn't justified for 21 historical
  rows. Future BUYs will all have tokens stored.
- 0 `fetch_failed`, 0 `no_bucket_before`, 0 `no_event_start` on
  BUYs (100% of BUYs have `event_start_time` populated — the
  Gamma `None` worry from the phase doc hasn't materialized on
  esports).

Distribution (n=44):
- mean CLV = −0.0167
- stddev = 0.0640
- min = −0.26 (9f8c4a56, BUY NO), max = +0.12 (71e89320, BUY YES)

Per-category breakdown:
- valorant: n=17, mean −0.0318, stddev 0.0915
- league-of-legends: n=14, mean −0.0007, stddev 0.0181
- counter-strike: n=8, mean −0.0131, stddev 0.0398
- rainbow-six-siege / mobile-legends-bang-bang / dota-2: n ≤ 2, not
  meaningful

**Interpretation (preliminary, to formalize in Task 0.5):** the
aggregate −0.017 mean is slightly unfavorable on entry quality — on
average we're buying ~1.7pp above the pre-event market mark.
LoL stays neutral with low variance; valorant shows bigger
dispersion (one −0.26 outlier dominates). This is a small-sample
signal; the retrospective should re-quote after more BUYs land.

Sanity join against win/loss (n=22 per bucket):
- CLV ≥ 0 (good entry): 63.6% win rate (mean CLV +0.021)
- CLV < 0 (bad entry): 72.7% win rate (mean CLV −0.054)

No monotone correlation at this n. Flagging for Task 0.5; likely
needs 3–5× more data before CLV-as-predictor-of-wins is meaningfully
testable.

### Files touched

- `scripts/backfill_clv.py` — new.
- `pqi/analytics_sql.py` — added `CLV_FILE` constant and the
  conditional `clv_by_row` view; `get_connection` gained an optional
  `clv_path=` parameter.
- `docs/analytics_queries.md` — view table extended to 3 views; Q5
  replaced with CLV queries; old Q5 → Q6; tips section gained CLV
  backfill / interpretation notes.
- `docs/phases/phase_0_edge_validation.md` — Task 0.2 acceptance
  criteria checked off; status line updated; session state appended
  with next-action pointer to Task 0.3.
- `docs/phases/README.md` — status column updated.
- Runtime: `~/.pqi/clv.jsonl` created (65 rows, uniform schema).

### Non-goals

- No Python imports from `scripts/backfill_clv.py` into pipeline
  code; it's intentionally a one-shot operation.
- No automatic re-runs from `history sync`. A run-time CLV capture
  (store `clv` on `CouncilResult` at the moment of a BUY
  recommendation) is an obvious next step but hasn't been scoped —
  the backfill + view is enough to unblock Phase 0 tasks 0.3–0.5.
- No coupling to `pqi.market.clob._fetch_side`. The backfill uses a
  different interval (`max` vs config's `1w`), so reusing the
  private helper would have meant either plumbing through a new
  param or calling it with a different contract than
  `_compute_side_metrics` expects downstream.

---

## Session 34 — Phase 0 Task 0.1: DuckDB analytics layer (`pqi query`)

First code workstream under the Phase 0 plan drafted in session 33.
Added a SQL layer over `~/.pqi/history.jsonl` so ad-hoc analytics stops
requiring a bespoke Python script. No changes to scoring, aggregator,
gate order, persisted state, or any pipeline contract.

### What landed

- **`pqi/analytics_sql.py`** — new module. Opens an in-memory DuckDB
  connection with two pre-registered views:
  - `history` — raw top-level rows; nested fields reached via dot
    notation (`result.market.category`, `resolution.winning_side`).
  - `verdicts` — flattened `(run_id, timestamp, category, question,
    entry_yes_price, recommendation, pass_reason, signal_type,
    final_score, no_score, winning_side, final_yes_price)` × each
    agent verdict under `v.*`, via `UNNEST(result.verdicts)`.
    Non-legacy only.
  Public API: `query(sql, history_path=None) -> DuckDBPyRelation` and
  `render_table(relation, title, max_rows) -> (rich.Table, truncated)`.

- **`pqi query "<SQL>"` CLI subcommand** (`main.py`). Wraps the helper
  + rich rendering. Flags: `--limit/-n` (default 100) for row cap.
  Truncation notice when the cap hits. Errors surface as red
  one-liners with the DuckDB binder message.

- **`docs/analytics_queries.md`** — published the 5 starter queries
  required by Phase 0 Task 0.1:
  1. per-category BUY hit rate,
  2. per-agent lean rate (with 0.45–0.55 dead band),
  3. monthly PnL per share,
  4. realized rate by EV-gate price band,
  5. missed opportunities (PASSes on the eventual winner with council
     ≥ 0.65 conviction).
  Plus a tips section covering nested-field access, legacy filtering,
  directional-winner semantics, and how to audit `stats.json` banded
  thresholds against realized per-band rates.

- **`requirements.txt`** — pinned `duckdb>=1.0`.

### DuckDB read contract (one-time gotcha)

Auto-inference on `read_json_auto` misidentifies our ISO8601 timestamps
(it samples a nested date field with `Z` format, then rejects the
top-level `+00:00` offsets). Fixed by pinning
`timestampformat='%Y-%m-%dT%H:%M:%S.%f%z'` and `sample_size=-1`
(full-file schema pass, ~50ms on the current 13MB file). Captured as
a module-level constant in `analytics_sql.py` so CLI users don't have
to think about it.

### Timing

Full-history aggregations (423 rows, 13MB) land at ~230ms each cold —
well under the Phase 0 <1s acceptance target. Each `query()` call
opens a fresh connection and rereads the JSONL; connection caching
is not warranted until profiling shows it matters.

### Early findings (pre-retrospective, surface-only)

Running the starter queries against current history produced
observations worth Phase 0 task 0.5 (retrospective):

- **Per-band realized rate is non-monotonic against the smoothed
  threshold.** `[0.50, 0.60)` band: 3/9 wins = 33% realized at mean
  price 56%. Either this band's BUYs are genuinely -EV (signal
  quality) or the Beta-binomial shrinkage is over-correcting toward a
  global rate that doesn't apply here. Sample is thin (n=9); flag for
  0.4.
- **Bear agent flat at 49% lean rate** (n=342 with 0.45–0.55 dead
  band) vs other agents 60–67%. Consistent with the Session-32 Option
  B reassessment finding that aggregate |drift| has converged toward
  mirror collapse. Feeds Phase 1 priority.
- **Monthly PnL April: -0.575 total per-share across 65 BUYs** (≈
  -0.9¢/share average). Marginal at best; the edge is small enough
  that gate calibration tuning matters more than adding agents.

These are descriptive; 0.3/0.4/0.5 will do the structured treatment.

### What did NOT change

- `scoring.py`, aggregator, gate order, Kelly formula, Option B
  contract, CLOB reader, PandaScore resolver, cache schema, EV gate,
  persona prompts, env defaults for any gate threshold.
- `history.jsonl` format — read-only analytics layer.

### Files touched

- New: `analytics_sql.py`, `docs/analytics_queries.md`.
- Edit: `main.py` (one new `query` command block), `requirements.txt`
  (one dependency).

### Next for Phase 0

Task 0.2 (pre-event CLV backfill) is the natural next step — the
DuckDB layer is the substrate, CLV is the first real measurement.
Phase doc `docs/phases/phase_0_edge_validation.md` has the
implementation sketch.

---

## Session 32 — Contrarian 0.65 → 0.60 + banded EV gate pass_reason rewrite + Option B shadow reassessment

Two small aggregator-adjacent changes plus a documentation decision on
Option B. No schema or contract changes.

### Contrarian min-prob retuned `0.65 → 0.60`

Session 31 loosened `MIN_CONTRARIAN_PROBABILITY` from 0.70 to 0.65 on
the rationale that the 0.70 gate never fired on resolved history while
rejected-contrarian candidates at prob ≥ 0.60 would have produced
+$0.081/share on 34 rows. User retrospective on more recent data showed
the profitable signal sits specifically in the 0.60–0.65 band — the
0.65 threshold landed above it and was bleeding money on live runs.

Reverted to the value the original retrospective actually validated.
The higher-variance low-probability tail is still guarded by
`MIN_CONTRARIAN_CONFIDENCE=0.80` and `MIN_CONTRARIAN_ROI=0.20`, both
unchanged — the looser probability floor doesn't change the confidence
or ROI bar a candidate must still clear.

Files:

- `config.py` — default `"0.65" → "0.60"`.
- `models.py` — `from_council_verdicts` default `min_contrarian_probability=0.65 → 0.60`.
- `.env.example` — default + rationale comment refreshed.
- `README.md` — env var table updated.

### Big Drawdown Risk `pass_reason` format reworked

The banded EV gate's `pass_reason` string put the threshold value
adjacent to the side price with band metadata trailing in brackets:

```
Big Drawdown Risk: YES price 77.5% ≥ 72.3% [band 0.70-0.80, n=15] (-EV)
```

Read naturally, this reads as `77.5% ≥ 72.3%` with band info as an
aside — easy to misread `72.3%` as the global `PerceivedTrueWinRate`
(which is 69.1% on current history, close enough to the band-specific
72.3% to cause confusion). Reworded so the comparison is unambiguously
against the band's WR:

```
Big Drawdown Risk: YES price 77.5% ≥ band 0.70-0.80 WR 72.3% [n=15] (-EV)
```

Fallback path (when `perceived_stats` is absent or the side price is
outside `[0.09, 0.95]` and no band matches) keeps the literal
`PerceivedTrueWinRate` wording so the two paths remain
distinguishable.

Verified against synthetic `PerceivedStats` with `side_price=0.775` in
band `0.70–0.80 (smoothed_wr=72.3%)` — the recommendation downgrades
to PASS with the new string.

Files:

- `models.py` — single-branch rewrite of the banded `pass_reason`
  format. Fallback string unchanged. No change to `gate_threshold`
  math, no change to `kelly_wr` threading, no change to
  `gate_band` selection.

### Star signal on analyze list view — verification (no code change)

User asked to confirm the analyze list shows ⭐ when the price band's
smoothed WR is above the market's favored-side price — e.g. 72%
favored side with band 0.70–0.80 WR 73% should star.

Current `_signal_glyph` (`main.py:318–336`) already does this:

```python
fav = max(yes_price, 1.0 - yes_price)
ceiling = _star_ceiling_for_price(fav, stats=stats)
if 0.55 <= fav <= ceiling:
    return "⭐"
```

where `_star_ceiling_for_price` → `stats.wr_for_price(fav)` → the
matching band's smoothed WR. All three analyze-list renderers
(`display_market_list`, `display_event_list`, sub-market picker) pass
`stats = _load_perceived_stats_safe()` into `_signal_glyph`. No code
change needed — feature is live since session 31.

### Option B shadow — reassessment

Not a code change, but worth logging. `pqi history stats` now shows
**628 independent verdicts (25.0% of 2507 total; 406 resolved)** with
all 7 agents emitting. Per-agent resolved counts sit at 55–60 — below
the 100–150 target from `docs/option_b_migration_plan.md` §7, but
close.

The blocker surfaced from the shadow panel is not sample size but
**signal quality**:

| Agent | \|drift\| | signed drift | YES Brier | NO Brier |
|---|---|---|---|---|
| Rules Lawyer | 0.01 | −0.00 | 0.249 | 0.254 |
| Bull | 0.03 | −0.01 | 0.246 | 0.246 |
| Bear | 0.06 | −0.03 | 0.285 | 0.290 |
| (others) | 0.02–0.03 | ±0.02 | ~equal | ~equal |

Session 26 close observed max `|drift| = 0.12` on the first 9 rows.
With 628 rows the aggregate drift has **converged toward mirror
collapse** — agents are still internally computing `P(NO) = 1 −
P(YES)` regardless of the autonomy block in `SHARED_PREAMBLE`. YES
and NO Brier values differ by ≤ 0.01 across the board, which means
the pre-registered acceptance criterion (ΔBrier ≤ −0.005, bootstrap
CI95 below 0 on both train and holdout) is mathematically
unreachable with the current signal.

Decision: **do not land `scripts/b_shadow_backtest.py` on the current
persona output.** The blocker is upstream — the autonomy framing in
the persona prompts isn't producing autonomous outputs, and no
aggregation change downstream can recover signal that was never
emitted. Either iterate on the SHARED_PREAMBLE autonomy block (with
worked examples, evidence-quality pre-step, etc.) until aggregate
`|drift|` exceeds ~0.10 for at least two agents, or accept that
Option B stays descriptive-only and the probability field stays
single-sided.

This is consistent with the session-25 framing: probability-transform
candidates on the current schema failed β backtest, Option B was
meant to move the signal upstream by having agents emit independent
pairs, but the agents are not in fact producing independent pairs at
scale. Surfacing it here so a future session doesn't re-run the
backtest on a signal that's guaranteed to fail the bar.

---

## Session 31 — Banded-WR EV gate + Kelly realignment + contrarian 0.70 → 0.65 + PandaScore past-matches fix

First aggregator-touching change since session 23. The single global
`PerceivedTrueWinRate` threshold in the EV gate is replaced with
per-price-band smoothed win rates. Kelly sizing is realigned to the
same banded rate so a BUY the gate rescues gets a non-zero advisory
stake. `MIN_CONTRARIAN_PROBABILITY` default is loosened `0.70 → 0.65`.
A broken PandaScore past-matches endpoint path is also fixed so
team-form lookups stop silently returning empty.

### Motivation

A user retrospective on 55 resolved BUYs surfaced that the global-WR
EV gate is effectively sign-flipped relative to observed performance:

| Band        | n  | raw WR | global WR | gate decision    |
|-------------|----|--------|-----------|------------------|
| 0.50–0.60   | 8  | 0.375  | 0.691     | **allowed** (losing band) |
| 0.60–0.70   | 19 | 0.684  | 0.691     | allowed (fair)   |
| 0.70–0.80   | 15 | 0.733  | 0.691     | allowed (fair)   |
| 0.80–0.95   | 10 | 1.000  | 0.691     | **blocked** (profitable band) |

Global-WR-vs-price is exactly the wrong shape for this data: cheap
favorites underperform the mean and expensive favorites over-perform
it, so a single threshold pinned at the mean blocks the profitable
band and allows the losing one.

The fix is to compare `side_price` against a *band-specific* smoothed
WR rather than the global mean. Smoothing uses Beta-binomial with the
global rate as prior mean and a pseudo-count `BAND_SHRINKAGE_K`:

```
smoothed_wr = (wins + k * global_wr) / (n + k)
```

A band with `n=0` falls back to `global_wr` exactly; thin bands are
pulled toward it; dense bands dominate. `K=5` was the best-observed
tradeoff in the retrospective (K=10 over-shrinks the profitable
0.80+ band; K=20 reproduces the sign-flipped global behavior).

### Bands

Code constant in `stats.py` — changing them is a re-analysis decision,
not a tuning knob:

```python
PRICE_BANDS: list[tuple[float, float]] = [
    (0.09, 0.50),   # cheap underdog region
    (0.50, 0.60),   # flip region — observed underperforming
    (0.60, 0.70),   # near-par
    (0.70, 0.80),   # moderate favorite
    (0.80, 0.95),   # deep favorite — observed over-performing
]
```

Top bound is 0.95 so every tradeable price finds a band;
`MAX_TRADEABLE_PRICE=0.90` has already filtered the rest.

### Files

- `stats.py` — new `PRICE_BANDS` constant; new `_side_price_and_won`
  extractor (mirrors `classify_outcome` per-entry logic);
  `_compute_band_stats` buckets resolved BUYs by side-price, then
  applies Beta-binomial smoothing. `recompute_from_history` now
  populates `band_stats` + `shrinkage_k` on the persisted
  `PerceivedStats`. Disabled when `BAND_SHRINKAGE_K ≤ 0` (gate falls
  back to scalar global WR).
- `models.py` — new `BandStat` model. `PerceivedStats` gains
  `band_stats`, `shrinkage_k`, and `wr_for_price()` (pure lookup with
  global fallback for out-of-band prices). `from_council_verdicts`
  accepts `perceived_stats` alongside the existing scalar
  `perceived_win_rate`; EV gate now compares against
  `gate_threshold = perceived_stats.wr_for_price(side_price)` when
  stats are supplied. `pass_reason` carries the band identifier:
  `"Big Drawdown Risk: YES price 55.0% ≥ 49.7% [band 0.50-0.60, n=8] (-EV)"`.
  Kelly sizing now also uses `gate_threshold` — so a BUY rescued by
  a higher banded rate gets a non-zero advisory stake, and a BUY
  that scraped past a weak band gets a smaller one. Half-Kelly
  (`kelly_multiplier`, default 0.5) absorbs the estimation noise.
- `scoring.py` — threads the loaded `PerceivedStats` through alongside
  the scalar WR. Legacy scalar path is preserved for tests and cold-
  start callers where `perceived_stats=None`.
- `config.py` / `.env.example` — new `BAND_SHRINKAGE_K=5.0`.

### Contrarian tuning

`MIN_CONTRARIAN_PROBABILITY` loosened `0.70 → 0.65`. The CONTRARIAN
branch requires all three of: council prob ≥ threshold, side
confidence ≥ 0.80, expected ROI ≥ 0.20. With only a handful of
resolved contrarian rows in history we're operating on judgment, not
data — 0.70 was tight enough to effectively never trigger; 0.65 is
the loosest value that still enforces "council meaningfully disagrees
with market" and preserves the ROI + confidence guards that actually
do most of the filtering.

### PandaScore endpoint fix

`ingestion/pandascore.py::get_team_past_matches` was hitting
`/teams/{id}/matches/past`. The correct endpoint is
`/teams/{id}/matches?filter[past]=true` — the `/past` suffix variant
returns 404 silently (caught and returned as `[]`), which meant
recent-form counts for both teams were uniformly zero. Fix:

```python
path = f"/teams/{team_id}/matches"
params = {
    "filter[past]": "true",
    "sort": "-scheduled_at",
    "per_page": min(count, 100),
}
```

Non-fatal change: prior runs degraded to empty form; new runs
populate it correctly.

### Contract compatibility

The banded gate still runs *last* in `from_council_verdicts` and still
only converts existing BUY recommendations to PASS — it never creates
a BUY. Upstream gates (price floor, price ceiling, contrarian,
market-aligned) are unchanged. The `PerceivedStats` schema gained
`band_stats` + `shrinkage_k` as optional fields with safe defaults
(`[]` and `0.0`), so `stats.json` files written before session 31
continue to load, just without banding — the fallback in
`wr_for_price()` returns `true_win_rate` in that case.

### Verification

Synthetic end-to-end with the above band stats:

- price=0.80 (banded=89.7%, global=65%) → BUY YES, kelly_scaled=0.225
  (rescued + sized non-zero).
- price=0.80 with `perceived_stats=None` (legacy) → PASS
  ("≥ PerceivedTrueWinRate 65.0% (-EV)").
- price=0.55 (banded=49.7%, global=65%) → PASS
  ("≥ 49.7% [band 0.50-0.60, n=8] (-EV)") — correctly blocks the
  band global was allowing through.

Real `recompute_from_history` on 55 BUYs produced the expected band
thresholds: 0.50–0.60 → 0.497, 0.60–0.70 → 0.686, 0.70–0.80 → 0.723,
0.80–0.95 → 0.897.

### UI follow-up (same session)

After the aggregator change landed we noticed three display surfaces
were still pinned to the global rate, so a user reading the CLI would
see a different story from the one the gate actually told:

- **Analyze list ⭐ signal** — `_star_ceiling()` (scalar) became
  `_star_ceiling_for_price(fav)` backed by `wr_for_price`. The star now
  follows the band specific to each market's favored-side price.
  Concretely: favorites at 0.80–0.89 get stars (banded ceiling 89.7%);
  favorites at 0.52–0.58 no longer do (banded ceiling 49.7%). Reverse
  of the pre-session-31 behavior. Out-of-band prices fall back to the
  global rate via `wr_for_price`.
- **`display_council_result` EV/Kelly panel** — the "True Win Rate"
  line now shows the band-specific ceiling for the trade's side price,
  with the global rate and `(band L-H, n=K)` shown alongside for
  context. The Kelly line stopped recomputing against the global rate
  and now reads `result.kelly_stake_full` / `kelly_stake_scaled`, which
  the aggregator already sizes against the banded threshold. This
  removes the last place display could disagree with the gate decision.
- **`history stats`** — new `Per-price-band win rate` table under the
  Council overview panel renders band, n, wins, losses, raw WR, and
  smoothed WR, plus a qualitative "Gate vs mid-band" hint so the user
  can see at a glance which bands the gate currently allows or blocks.
  Skipped when `band_stats` is empty (banding disabled or cold start).

### Next

- Observe the banded `pass_reason` in live `analyze` output — both a
  rescue (price ≥ 0.80 with a BUY that would have been blocked) and
  a block (price ≈ 0.55 where the gate now PASSes).
- After more resolved rows land, sanity-check `K=5` against `K=3` and
  `K=10` on fresh data — don't tune on the same 55-row slice that
  picked it.

---

## Session 30 — Centralized Anthropic retry (529 OverloadedError + transient 5xx/429/transport)

Reliability patch. A live analyze run crashed mid-pipeline with an
`anthropic.APIStatusError: 529 overloaded_error` ("Overloaded"). The
SDK does retry transient errors by default (2 attempts), but that
budget was insufficient when the overload condition persisted across
both attempts. The exception then bubbled up through
`_run_single_agent` → `_call_llm` → `_chat_anthropic` and took the
council run down with it.

Worth labeling clearly because the two error codes look identical at
a glance:

- **429** = `RateLimitError` — *we* exceeded our quota / RPM cap.
  Backoff is the right response, but so is checking our usage.
- **529** = `OverloadedError` (5xx; surfaces as
  `InternalServerError` in the SDK) — *Anthropic* is shedding load.
  Nothing we can do but back off.

### Diagnosis

Reading the traceback, the SDK had already retried twice (visible
through internal `_retries_taken` / sleep timings ≈ 0.46s + 0.85s).
A two-attempt retry over <2s window has no chance against a
multi-minute overload — Anthropic's `retry-after` header in the 529
response usually points 60s+ out. We need a longer, more elastic
schedule.

### Design

Two design choices, per CTO_Agent.md:

1. **Centralize the retry in `pqi/llm/client.py`, not in
   `agents/council.py`.** The synthesizer + search architect call
   the same `chat()` surface; centralizing means they get the same
   resilience for free. Council's existing per-agent retry stays —
   it handles a different concern (parse failures), and layering
   transport retry beneath it gives a defense-in-depth budget.
2. **Disable the SDK's built-in retry (`max_retries=0`) so this
   layer owns the policy end-to-end.** Otherwise the worst-case
   latency doubles silently and the schedule we expose via env
   stops matching reality.

### Patch

- **`pqi/config.py`** — three new tunables:
  - `ANTHROPIC_MAX_RETRIES: int = int(os.getenv("ANTHROPIC_MAX_RETRIES", "5"))`
  - `ANTHROPIC_RETRY_BASE_DELAY: float = float(os.getenv("ANTHROPIC_RETRY_BASE_DELAY", "1.0"))`
  - `ANTHROPIC_RETRY_MAX_DELAY: float = float(os.getenv("ANTHROPIC_RETRY_MAX_DELAY", "30.0"))`
  Mirrors `AGENT_MAX_RETRIES`/30s cap so council and client share
  worst-case behavior.

- **`.env.example`** — documented all three with the worst-case
  schedule (1, 2, 4, 8, 16, 30s → ~61s) and the explicit "SDK
  built-in retry is disabled" note.

- **`pqi/llm/client.py::_chat_anthropic`** —
  - `client = anthropic.AsyncAnthropic(api_key=..., max_retries=0)`
  - Retryable tuple: `APIConnectionError`, `APITimeoutError`,
    `RateLimitError`, `InternalServerError` (covers 5xx incl. 529).
  - Generic `APIStatusError` arm: retry only when
    `status_code >= 500`; otherwise re-raise immediately so 4xx
    surfaces fast and doesn't burn quota.
  - WARNING log per retry: `anthropic call failed (attempt N/total): … — retrying in Xs`.
  - ERROR log on exhaustion: `anthropic call failed after N attempts: …` precedes the propagated exception.
  - Cache-stats DEBUG line preserved unchanged on success path.
  - Loop body is `for attempt in range(1 + max_retries): try: …; break; except retryable: …; except APIStatusError: …`.

### Layered budgets

The council's existing `AGENT_MAX_RETRIES`-driven loop in
`_run_single_agent` continues to wrap the client. So a single agent
can absorb up to:

```
AGENT_MAX_RETRIES * (1 + ANTHROPIC_MAX_RETRIES) = 5 * 6 = 30 attempts
```

…before being dropped. That's intentional:

- Inner loop (this session): handles **transport** failures —
  retries the same prompt because the failure was network/server.
- Outer loop (existing): handles **parse** failures — also retries
  the LLM call, but the relevant signal is "the model returned
  malformed JSON," which is independent of whether transport was
  healthy.

Sharing one budget would conflate the two.

### Verification

Mock-based functional tests covering all three behaviors:

| Scenario                              | Expected                          | Result |
|---------------------------------------|-----------------------------------|--------|
| `InternalServerError` × 2, then 200   | Returns success on attempt 3      | PASS   |
| `InternalServerError` × 6 (exhaust)   | Raises `InternalServerError`      | PASS   |
| `BadRequestError` (4xx, non-retryable)| Raises immediately on attempt 1   | PASS   |

Backoff was time-shrunk to `0.001s` for the test loop. Production
schedule unchanged.

### Files touched

- `pqi/config.py` (+3 keys)
- `.env.example` (+1 documented section)
- `pqi/llm/client.py` (+`asyncio` import, `max_retries=0`, retry loop, log lines)

### Cost

Zero unless something fails. On a healthy Anthropic the success
path is byte-identical to before — same single `messages.create()`
call, same response object, same cache instrumentation. When a
retry does fire, the cost is one extra request per retry attempt
(billed normally — Anthropic does not refund failed 5xx requests
in their public pricing).

### Rollback

`ANTHROPIC_MAX_RETRIES=0` reduces to one attempt with no SDK
fallback (the SDK is permanently silenced in code). For a full
revert, restore `anthropic.AsyncAnthropic(api_key=...)` (drop
`max_retries=0`) and remove the retry loop in `_chat_anthropic`.

---

## Session 29 — Persistent category-resolution cache

Small QoL pass. Every `analyze -c <category>` run was paying ~3
HTTP calls plus rate-limit sleeps to re-resolve a category string
to a `series_id` or `tag_id` — even though Polymarket's tag and
series IDs are effectively immutable (e.g. `esports` is tag id
`64` and has been for the project's entire life).

### Patch

- **`pqi/config.py`** — new `CATEGORY_CACHE_PATH: str` (default
  `~/.pqi/category_cache.json`).
- **`.env.example`** — documented the key.
- **`pqi/market/polymarket.py`** — four module-level helpers:
  - `_load_category_cache()` — reads JSON, returns `{}` on
    missing/malformed file (logs warning).
  - `_save_category_cache(cache)` — atomic `tmp + replace()`
    write under `mkdir(parents=True, exist_ok=True)`.
  - `_cache_get_category(query_clean)` — returns `CategoryMatch
    | None`. Validates `match_type in {"series","tag"}` and a
    truthy `value`.
  - `_cache_put_category(query_clean, match)` — read-modify-write
    of one entry. Stamps `resolved_at` for human inspection.
- `resolve_category()` short-circuits at the top on cache hit,
  and each of the three live passes (exact series, partial series
  + exact tag in parallel, partial tag pagination) writes its
  positive result back via `_cache_put_category` before returning.
  Misses (the final `return None`) are intentionally NOT cached —
  bad spellings, typos, and not-yet-indexed categories should
  retry on the next run.

### Cache shape

One entry per cleaned-and-lowercased query string:

```json
{
  "esports": {
    "match_type": "tag",
    "value": "64",
    "resolved_at": "2026-04-16T14:14:51.213426+00:00"
  }
}
```

### Verification

```
=== first call: CategoryMatch('tag', '64') in 5924ms ===
=== second call: CategoryMatch('tag', '64') in 0ms ===
```

DEBUG log on the warm path:
`resolve_category('esports'): cache hit → CategoryMatch('tag', '64')`.

### Why no CLI `--refresh-categories` flag

Tag/series IDs don't churn on Polymarket — the only realistic
invalidation reason is "I think Polymarket renamed something."
Deleting the file (`rm ~/.pqi/category_cache.json`) is a single
command and survives just fine without a flag. Adding one would
expand the CLI surface for a near-zero use case.

### Why misses aren't cached

A `None` result usually means a typo or a category that doesn't
exist yet. Caching that result would lock the user out of ever
finding it once Polymarket adds it. Cost of retry on the next
run is the same ~3 calls — bounded — so the asymmetry favors
re-trying.

---

## Session 28 — Temporal disambiguation + PandaScore structured-data integration

Bug-fix session triggered by a critical incident: the council
returned 99% YES at HIGH confidence on the CS market "Will 1WIN
beat Nuclear TigeRES?", asserting that the match had "already
ended 2-0" — the match was actually scheduled 2h40m in the future.
Past-tense H2H scorelines from Tavily snippets were being
paraphrased into `news_summary` / `side_yes_case` as if they were
the current match's outcome. The agents had no temporal anchor
and no authoritative status field to ground against.

Two-step fix shipped end-to-end (15 sub-tasks total, 8 sub-commits).

### Step 1 — Temporal disambiguation (commit `c610e05`)

Mechanical safety net + LLM-side guidance. No new dependencies.

- **Synthesizer system prompt:** explicit "future tense for
  upcoming matches, past tense only for verified completed
  matches" rule with three concrete examples (UPCOMING / LIVE /
  COMPLETED).
- **Synthesizer cross-source contamination tagger** in
  `build_briefing()`: regex scans `news_summary` +
  `side_yes_case` + `side_no_case` for past-tense scoreline
  patterns (e.g. `"won 2-1"`, `"defeated 2-0"`, `"final score
  2-0"`). When `event_start_time > now()`, rewrites the suspect
  bucket with a bracketed `[NOTE: prior encounter, not current
  match]` prefix.
- **Council briefing block:** new EVENT TEMPORAL DISAMBIGUATION
  section in `_format_briefing()` placed first (highest priority).
  Renders `event_start_time`, status (UPCOMING/LIVE/COMPLETED based
  on time), explicit instruction to treat the briefing as
  pre-match analysis when status is UPCOMING.

### Step 2 — PandaScore structured-data integration (commits 2a–2g)

Authoritative ground-truth layer for esports markets. ~1090 LOC
across 8 files. 8 sub-commits in dependency order.

- **2a (`models.py`, commit `0b7fe72`):** new Pydantic contracts:

  ```python
  class TeamForm(BaseModel):
      wins: int
      losses: int

  class H2HMatch(BaseModel):
      match_id: str
      scheduled_at: datetime
      tournament_name: str | None
      final_score: str | None
      winner_name: str | None

  class StructuredMatchRecord(BaseModel):
      match_id: str
      team_a_name: str        # canonical PandaScore name
      team_b_name: str
      status: str             # "scheduled" | "running" | "finished"
      scheduled_at: datetime  # authoritative kickoff
      tournament_name: str | None
      match_format: str | None
      final_score: str | None  # only when finished
      winner_name: str | None  # only when finished
      team_a_recent_form: TeamForm | None
      team_b_recent_form: TeamForm | None
      h2h: list[H2HMatch]
      fuzzy_score: int        # 0–100
  ```

  `BriefingDocument.structured_match: StructuredMatchRecord | None`.
  Additive — non-esports markets always get `None`.

- **2b (`config.py` + `.env.example` + `requirements.txt`, commit
  `a61321c`):** 5 new env vars:

  | Var | Default | Effect |
  |---|---|---|
  | `PANDASCORE_API_KEY` | _(empty)_ | Bearer token. Empty disables resolver entirely. |
  | `PANDASCORE_ENABLED` | `true` | Master switch. False bypasses resolver even when key is present. |
  | `PANDASCORE_FUZZY_THRESHOLD` | `75` | Min `min(token_set_ratio_a, token_set_ratio_b)` to accept a candidate. |
  | `PANDASCORE_WINDOW_HOURS` | `48` | Search window around event time for candidate matches. |
  | `PANDASCORE_RECENT_FORM_COUNT` | `5` | Last-N matches per team for form summary. |

  Literal `PANDASCORE_BASE_URL = "https://api.pandascore.co"` in
  `config.py` only (true code constant, not user-tunable).
  New dep: `rapidfuzz>=3.0`.

- **2c (`pqi/ingestion/pandascore.py`, NEW, commit `3068f45`):**
  ~230 LOC async httpx client. Bearer auth via header. Methods:
  `list_videogames`, `list_matches_in_window(game_id, from_dt,
  to_dt)`, `get_match(match_id)`,
  `get_team_past_matches(team_id, page_size)`, `get_h2h(team_a_id,
  team_b_id)`. All paths gracefully return `[]` / `None` on HTTP
  failure or missing data — non-fatal.

- **2d (`pqi/ingestion/match_resolver.py`, NEW, commit
  `255a732`):** ~490 LOC orchestrator.
  - `GAME_MAPPING` — Polymarket category slug → PandaScore game
    slug. Covers what PQI sees in the wild: `counter-strike` →
    `cs-go`, `valorant`, `league-of-legends`, `dota-2`,
    `rainbow-six-siege`. Returns `None` for unknown categories
    (resolver no-ops on non-esports).
  - `_extract_parties(question)` — splits market question on
    `\s+vs\s+|\s+versus\s+` to get two team names. Strips
    "(BOX)" / "Match" / question marks.
  - `_score_candidate(a, b, cand_a, cand_b)` — symmetric scoring:
    `min(fuzz.token_set_ratio(a, cand_a), fuzz.token_set_ratio(b,
    cand_b))`. Required (vs `max` or sum) so that one side being
    a perfect match doesn't paper over the other side being wrong.
  - `_pick_best_candidate` — sorts above-threshold candidates by
    descending score; returns winner or `None`.
  - `_build_team_form(client, team_id, n)` — fetches last-N
    completed matches for a team, computes wins/losses.
  - `_build_h2h(client, team_a_id, team_b_id)` — fetches direct
    head-to-head with stable match IDs.
  - `_normalise_status` — collapses PandaScore statuses
    (`not_started`, `running`, `finished`, `canceled`, etc.) to
    the three values the rest of the pipeline expects.
  - `resolve_match(market)` — top-level entry. Returns
    `StructuredMatchRecord | None`. Parallel sub-fetches via
    `asyncio.gather` for team form + H2H once a candidate is
    picked.

- **2e (`pqi/ingestion/synthesizer.py`, commit `e6ac7eb`):**
  Threads `structured_match` through `build_briefing()`. Renders
  STRUCTURED MATCH RECORD block in the synth's user prompt with
  explicit "DO NOT paraphrase these fields" instruction.
  Cross-source tagger upgraded — now triggers when
  `structured_match.status in {"scheduled", "running"}` *in
  addition to* the time-based check (cross-source corroboration
  strengthens the signal). Resolver-first H2H dedup in the
  `prior_match_facts` builder: resolver's stable-ID matches go
  first, then synthesizer-snippet H2H is dedup'd via date-signature
  substring match (`sig.split("|", 1)[0] in f`).

- **2f (`pqi/agents/council.py`, commit `05b51cf`):** new
  STRUCTURED MATCH DATA block in `_format_briefing()`, placed
  between disambiguation_block and price_history_block. Renders
  canonical names + format, status (uppercase), scheduled time,
  tournament, conditional final_score+winner OR scheduled/running
  NOTE, recent form summaries, fuzzy confidence. Block omitted
  entirely when `briefing.structured_match is None` (non-esports
  / resolver miss).

- **2g (`pqi/cache.py` + `pqi/main.py`, commit `9a1bc9d`):**
  Cache schema gained `structured_status TEXT` column with
  defensive `ALTER TABLE` migration (catches `OperationalError`
  when column already exists). `get_cached(market_id,
  current_yes_price, current_structured_status)` invalidates on
  status mismatch. NULL == NULL works correctly for non-esports
  markets that never invoke the resolver. `_run_analysis()` now
  calls `resolve_match(market)` before cache lookup, threads
  `structured_match` to `build_briefing` and `structured_status`
  to `get_cached`. Skipped when `shared_briefing is not None`
  (sibling reuse path).

### Block ordering in council briefing

Highest to lowest priority (top to bottom):

1. EVENT TEMPORAL DISAMBIGUATION — anchors what time it is and
   whether the match has started.
2. STRUCTURED MATCH DATA — authoritative identifiers from
   PandaScore.
3. MARKET PRICE HISTORY — CLOB trend metrics.
4. NEWS SUMMARY + KEY FACTS + side cases — synthesized narrative.
5. PRIOR MATCH FACTS — H2H, decay-prone.

Earlier blocks override later ones in the agent's attention.

### Cache invalidation expansion

Previously: price-delta only (>2% YES move → stale). Now also
invalidates on PandaScore status transitions (scheduled → running
→ finished) — high-information events where price often barely
budges through huge state changes (live market thin/volatile).
Non-esports markets are unaffected: both sides are NULL, so the
mismatch check never fires.

### Verification (Task 2h)

End-to-end audit, all 6 checks passed:

- H2H dedup: resolver IEM kept once, BLAST Spring (synth-only)
  survives.
- Contamination warning fires + anchors on PandaScore status (not
  just event_start_time).
- Block order DISAMBIG(837) → STRUCTURED(1182) → NEWS(1764) →
  PRIOR(1993) — measured byte offsets in the assembled briefing.
- Resolver TeamForm renders in council briefing (`1WIN recent
  form: 4W-1L`, `Nuclear TigeRES recent form: 2W-3L`).
- Step-1 regression preserved (no STRUCTURED block, falls back to
  event_start_time anchor when no resolver hit).
- `main._run_analysis` correctly wires resolver + cache key.

### Cost & operational impact

- **API cost:** PandaScore free tier (1k req/hr). Per-analyze
  call: 1 list-matches + 1 get-match + 2 team-form + 1 h2h ≈ 5
  requests. Negligible against the rate limit.
- **Latency:** resolver runs before cache lookup (so it can
  inform invalidation). Adds ~200–500ms per esports analyze when
  PandaScore is up. Non-fatal — failure path adds 0 latency.
- **No behavioral change** for non-esports markets — the resolver
  no-ops on unknown categories.

### Why this design (CTO posture notes)

- **Three-layer defense (Bias-as-Feature applied to data
  quality):** prompts, regex, structured data are independent
  defenses. Any one can fail and the others still catch the
  contamination.
- **Non-fatal everywhere (Parallelism, Statistical Skepticism):**
  any resolver failure → `structured_match=None` and the pipeline
  continues. No new hard gates. The council degrades gracefully
  to the Step-1 system.
- **Symmetric fuzzy scoring (Contract-as-Law):** `min(score_a,
  score_b)` not `max` or sum — required so that "Team Liquid vs
  NRG" doesn't get paired with "Team Liquid vs Cloud9" just
  because the first side matches perfectly.
- **Resolver-first H2H dedup:** PandaScore has stable match IDs;
  Tavily snippets do not. Trusting the structured source first
  and dedup'ing the unstructured second is the correct precedence.

---

## Session 27 — Anthropic prompt caching enabled

Cost-optimization pass triggered by analysis of
`docs/claude_api_tokens_2026_04.csv` (13-day Anthropic console
export). Two findings:

1. **Zero prompt-cache usage.** Every `cache_write_5m`,
   `cache_write_1h`, and `cache_read` cell across 13 days was `0`.
   The council's stable per-persona system prompts and the
   synthesizer's stable system prompt were being re-encoded
   full-price on every API call.
2. **Run-rate drift.** Last 7 days averaged ~1.45M input + ~240K
   output tokens/day → ~$2.65/day = **~$80/mo run-rate, 4× over**
   the CLAUDE.md Tech Stack target of "stays under $20/mo".

### Patch (single-call surface, no caller changes)

- **`pqi/config.py`** — new
  `ANTHROPIC_PROMPT_CACHE_ENABLED: bool = (os.getenv(...,"true").lower() == "true")`.
- **`.env.example`** — documented the key with comment explaining
  TTL, no-op behavior below the min cacheable prefix, and rollback.
- **`pqi/llm/client.py::_chat_anthropic`** — when caching is
  enabled, the `system` argument (formerly a plain string) is
  wrapped as a 1-element list:
  ```python
  [{"type": "text", "text": system,
    "cache_control": {"type": "ephemeral"}}]
  ```
  Default TTL is 5 min (no `ttl` field). After every call, a
  DEBUG log line surfaces the four token counts from
  `response.usage`:
  ```
  anthropic usage: input=… output=… cache_read=… cache_create=…
  ```
  This avoids round-tripping through the dashboard CSV to verify
  the cache engaged.

### Why a single cache breakpoint, not multiple

Considered a 3-layer breakdown (preamble / persona / briefing) but
rejected for now:

- The 7 council agents fire in parallel via `asyncio.gather`. Each
  agent has a different persona body, so any cache key whose prefix
  includes the persona body is unique per agent — no in-run sharing
  beyond the preamble.
- The biggest dollar saving is **across-run** reuse of the full
  per-agent system prompt (preamble + body) when the same agent
  runs again within 5 min. A single cache breakpoint at the end of
  the system message captures that win with zero structural code
  changes elsewhere.
- Multi-breakpoint splits would have required restructuring
  `council._build_user_message` (move the Rules Lawyer's
  resolution-criteria injection to a suffix instead of a prefix,
  same for Bull/Bear's side context). Deferred until we have
  evidence the simple version isn't enough.

### Behavioral guarantee

Caching is server-side and invisible to the model. The model still
receives every token of every request. Same prompt in → same output
distribution out. The only externally observable change is lower
input-token billing on cache hits + slightly lower latency.

### Caveat to monitor

Haiku models historically have a min cacheable prefix of
~1024–2048 tokens. Several council personas (preamble + body) are
right around that boundary. If `cache_creation_input_tokens`
returns `0` after the first analyze run, the prefix is too short —
`cache_control` is silently ignored. Next move would be to either:

- Push the briefing into the system message to enlarge the prefix.
- Restructure with multiple breakpoints so per-agent variable
  context lives in a non-cached suffix.

Verify with the DEBUG log before assuming the cache is hot.

### Verification command

```bash
cd /home/shiroe/personal
LOG_LEVEL=DEBUG python -m pqi.main --top 2>&1 | grep "anthropic usage"
```

Expected: 7 lines per analyze run (one per agent) plus
synthesizer/search-architect lines. First call should show
`cache_create=` non-zero on each unique persona; subsequent calls
within 5 min should show `cache_read=` non-zero.

### Rollback

Single env var: `ANTHROPIC_PROMPT_CACHE_ENABLED=false` in `.env`.
No code revert needed.

### Files touched

- `pqi/config.py` — `ANTHROPIC_PROMPT_CACHE_ENABLED`
- `.env.example` — documentation
- `pqi/llm/client.py` — `_chat_anthropic` body
- `CLAUDE.md` — LLM Client Contract section, Current State,
  Next Session Prompt
- `README.md` — Backends section + Tuning table

### What was NOT changed

- `pqi/agents/council.py` — no signature changes; council still
  builds system prompt via `AGENT_PERSONAS[key]` and passes it as
  a string to `chat()`.
- `pqi/ingestion/synthesizer.py` — no signature changes; both LLM
  calls (Search Architect + synthesis) automatically get caching
  via the same code path.
- `pqi/models.py`, `pqi/scoring.py` — untouched. Aggregation
  contract preserved.

---

## Session 26 — Option B steps 1–4 shipped (schema, parser, personas, shadow analytics)

Executed steps 1–4 of `docs/option_b_migration_plan.md` in strict
rollout order. Steps 5 (shadow backtest) and 6 (scoring integration)
deferred — shadow data has not accumulated. Per-step local commits
(no push).

Also codified the **Attribution rule (STRICT)** in CLAUDE.md
Coding Conventions: no AI / Claude / "assisted" mentions in source,
comments, commits, PRs, or docs. Durable memory pointer lives at
`feedback_attribution.md`. All session-26 commits honor the rule.

**Step 1 — additive schema + synthetic-mirror backfill** (`05bc2c9`).
- `models.AgentVerdict`: new fields
  `probability_yes: float | None`, `probability_no: float | None`,
  `probability_source: Literal["independent","synthetic_mirror"] = "synthetic_mirror"`.
  `@model_validator(mode="after")` auto-fills from canonical
  `probability` when either per-side field is unset (uses
  `object.__setattr__` for frozen-safety).
- `history.py`: `_backfill_per_side_probability(entry)` helper
  runs on every load after legacy migration — tolerant-read of
  pre-B history without rewriting the file.
- `scripts/b_backfill.py` (NEW): idempotent one-shot rewrite of
  `~/.pqi/history.jsonl`. Atomic tmp+rename, auto-creates
  `~/.pqi/history.jsonl.preB.bak` sibling on first modification,
  counts per action (`already_migrated`, `migrated`,
  `skipped_not_dict`, `skipped_no_probability`). Run output:
  292 rows scanned, 1860 verdicts migrated, all tagged
  `synthetic_mirror`. Zero API calls.
- `scoring.py` / `CouncilResult.from_council_verdicts`: **untouched**.
  Aggregator still consumes `probability` (canonical) so every
  existing metric (lean rate, correct/wrong buy, EV gate, Kelly,
  A.9 weights) is preserved bit-for-bit.

**Step 2 — tolerant parser** (`c9a2bb8`).
- `agents/council._parse_verdict`: reads optional
  `probability_no_score` int (0–100). Logic:
  - Field present and valid → tag `independent`, warn at
    `|p_yes + p_no − 1| > 0.3` (never reject; lenient-by-design
    per plan §5).
  - Field missing/malformed → synthetic_mirror fallback, retain
    the existing retry+backoff path.
- Constructor only passes `probability_yes/no/source` when the
  verdict is genuinely independent — `model_validator` handles
  synthetic rows.

**Step 3 — persona rollout (one-per-commit)** (`e011ec2`,
`949c871`, `39173c5`, `013dd1d`, `af7d924`, `0eb3610`, `e3b93a8`).
- `SHARED_PREAMBLE`: new "TWO-SIDED PROBABILITY (autonomy)" block,
  conditional on the persona's Output Format containing both
  `probability_score` and `probability_no_score`. Other personas
  skip the block.
- Promotion order (lowest structural-bias risk first, then
  Bull/Bear last per plan §6):
  `rules_lawyer → statistician → sentiment → specialist →
   contrarian → bull → bear`.
- Bull and Bear carry explicit anti-mirror framing:
  *"probability_no_score is NOT `100 - probability_score`; the
  evidence base for the opposing side is asymmetric."* This is
  the highest-risk promotion — session-20 Bear rewrite is still
  inside its A.4 monitoring window.
- Each persona's output field docstring is tailored (Rules
  Lawyer: "P(NO outcome condition is met)"; Statistician:
  "anchored on historical base rate for OPPOSITE outcome"; etc.).

**Step 4 — shadow analytics** (`9b3d7f9`).
- `analytics.py`:
  - `OPTION_B_PHASE1_MIN_SAMPLES = 10` (sanity floor).
  - `OPTION_B_PHASE2_MIN_SAMPLES = 30` (pre-registered backtest bar).
  - `_is_independent(v)` — filter predicate on
    `probability_source == "independent"`.
  - `option_b_overview(entries)` — totals: independent count,
    resolved count, fraction, agents-with-any-independent.
  - `option_b_per_agent(entries)` — per-agent `n_ind`, `n_res`,
    `mean_abs_drift`, `mean_signed_drift` (= mean of
    `p_yes + p_no − 1`), `yes_brier`, `no_brier` (per-side Brier
    against `final_yes_price` on resolved markets), `phase`
    classification.
- `main.py history stats`: conditional Option B block —
  panel (totals + agent coverage) + per-agent table with columns
  `Agent | n_ind | n_res | |drift| | signed | YES Brier | NO Brier | Phase`.
  Color-coded phase (green/yellow/dim). Silent when zero
  independent verdicts. Footer explains signed-drift
  interpretation (positive = double-count, negative = ignorance
  mass, near-zero = mirror collapse).
- Descriptive only. Zero gate impact. Scoring unchanged.

**API cost impact.** Per-agent output adds ~1 JSON field
(~5–10 tokens). 7 agents × ~$5/M output (claude-haiku-4-5-20251001)
≈ **$0.00025 per analyze run** — below rounding noise of the
existing ~$0.015 council cost. Anthropic prompt cache further
dilutes the impact on repeated preambles.

**Shadow-data status at session close.** 9 independent verdicts
observed in history (all 7 agents represented, ≤ 0.5% of 1888
total verdicts). Max `|drift|` 0.12, signed drift within
`[-0.03, +0.12]`. No `(0.5, 0.5)` degeneracy, no strict mirrors.
0 resolved → per-side Brier not yet computable. Phase 1
(`n ≥ 10` per agent) and the backtest bar (100–150 independent
rows total) remain ahead; no step 5 action possible yet.

**What stayed off-limits.** `CouncilResult.from_council_verdicts`
aggregation. The session-23 recommendation contract is preserved:
probability → edges → price gates → contrarian/aligned → EV gate.
Option B is upstream metadata; the aggregator sees an unchanged
`probability` field until step 6 explicitly changes it.

---

## Session 25 — A.1 Option A closed, A.9 dynamic per-agent weighting, Option B plan drafted

Three deliverables this session:

**1. A.1 β-phase backtest (Option A: probability-level transforms).**
Extended `scripts/a1_backtest.py` from α (3 candidates) to β (8): added
`SH2_selective` (shrinkage with gap floor), `LO2_mean` (log-odds mean),
`D1_ign` / `D2_gap` / `D3_both` (Dempster with ignorance-ceiling /
mass-gap overrides). Added chronological 70/30 train/holdout split,
bootstrap ΔBrier CI (1000 resamples, seed 42), per-row `correct_buy`
outcome mapping on 228 resolved BUYs. Pre-registered criteria:
`ΔBrier ≤ −0.005`, `ΔNet ≥ −1`, CI95<0 on **both** train and holdout.
**All 8 candidates REJECTED.** Dempster overrides produced identical
numbers to plain Dempster (thresholds never fired on rows that would
BUY). Decision: Option A closed — probability-level transforms
insufficient at this sample size. Option B (per-side probability
autonomy) is the remaining path.

**2. A.9 shipped — dynamic per-agent reliability weights.**
- `pqi/agent_weights.py` (NEW) — mirrors `pqi/stats.py`. Pure
  `compute_weights(lean_rates, min_samples, shrink)` function:
  `weight = max(0.0, 1.0 + shrink * (lean_rate − council_avg))`.
  Agents below `min_samples` clamp to 1.0. `recompute_from_history`
  walks `history.jsonl`, persists `~/.pqi/agent_weights.json`
  atomically. `load_agent_weights` tolerates missing/malformed.
- `pqi/config.py` / `.env.example` — three knobs:
  `AGENT_WEIGHTS_ENABLED=true`, `MIN_AGENT_WEIGHT_SAMPLES=30`,
  `AGENT_WEIGHT_SHRINK=2.0`.
- `pqi/scoring.py` — dynamic weights fully override static
  `RULES_LAWYER_WEIGHT`/`BEAR_WEIGHT` when enabled and a snapshot
  exists (precedence mode α — user-selected over multiply/per-agent).
- `pqi/models.py` — additive only: `CouncilResult.agent_weights_snapshot:
  dict[str, float]` captured on every run. No verdict-logic change.
- `pqi/main.py` — `history sync` and `history stats` trigger a
  recompute alongside `PerceivedStats`. `history show <#>` renders
  a `Wt` column when the snapshot is non-empty.
- Live weights from current history: Sentiment/Contrarian 1.07,
  Rules Lawyer/Specialist 1.06, Statistician 1.05, Bull 0.94,
  Bear 0.74 — matches Tier-1 retrospective lean-rate ranking.
- **Deferred (documented in roadmap A.9):** Bayesian posterior
  weighting `w = (hits + α) / (n + α + β)` — preferred formulation
  once sample size grows beyond ~50/agent. Current shrinkage
  formula is the intentional "for-now" choice.

**3. Option B migration plan drafted** at
`docs/option_b_migration_plan.md`. 10 sections: Goal, new math
enabled (coherence metric, per-side calibration), zero-API-cost
synthetic backfill (`p_no = 1 − p_yes`, preserves lean-outcome /
correct-buy metrics at no cost), files touched (risk-labeled),
open design questions (@computed_field vs rename-sweep, schema
version location, persona framing, parser strictness), 6-step
non-breaking rollout (schema+backfill → tolerant parser →
personas one-at-a-time → shadow analytics → shadow backtest →
scoring integration only after passing), same pre-registered
acceptance criteria as A.1 β, dependencies/risks, go/no-go
decision points. **Plan-only this session — no code changes.**

Also added **A.10** (dynamic-weight feedback-loop monitoring) to
`docs/roadmap.md` to protect A.9 from self-reinforcement: if
agent A gets down-weighted, its signal loses influence on future
BUYs, which can lock in the down-weight. A.10 defines the stats
snapshot + alarm thresholds to detect this before it matters.

---

## Session 24 — Guardrails audit + doc reconciliation

Audit sweep before resuming roadmap work, driven by a perceived win-rate
deterioration over the most recent ~20 resolved entries. Verified the
EV gate / Kelly addition (sessions 22–23) is **purely additive**: the
pre-EV recommendation contract in `CouncilResult.from_council_verdicts`
is preserved line-for-line. EV gate only narrows BUY→PASS, never the
reverse; Kelly fields are metadata-only; both are dormant until
`MIN_WIN_RATE_SAMPLES` is reached.

- `scoring.py` — Deleted dead top-level helpers
  (`confidence_weighted_score_yes/no`, `compute_edge`,
  `determine_confidence_level`, `score_council` alias). All logic has
  lived in `models.py:from_council_verdicts` since session 17; the
  helpers were stale (and `compute_edge` still returned the legacy
  `score − price` semantic, contradicting session 23's edge repurpose).
  Stopped passing the deprecated `edge_threshold` kwarg — the kwarg
  remains on `from_council_verdicts` with a default for back-compat,
  untouched. No verdict-path change.
- `CLAUDE.md` — Reconciled gate defaults with `config.py`:
  `MIN_TRADEABLE_PRICE` 0.05 → 0.09, `MIN_COUNCIL_PROBABILITY` 0.57 →
  0.60. Doc had drifted behind config changes across sessions.
- `README.md` — Same reconciliation: `MIN_TRADEABLE_PRICE` 0.05 →
  0.09, `MIN_EXPECTED_ROI` 0.15 → 0.10, `RULES_LAWYER_WEIGHT` 2 → 1.
- `.env.example` — Fixed stale `# Default: X` comments and a few
  values that diverged from `config.py` defaults
  (`MAX_TRADEABLE_PRICE` 0.91 → 0.90, `MIN_COUNCIL_PROBABILITY` 0.57 →
  0.60). Rule reaffirmed: the KEY=VALUE line and the `# Default:`
  comment in `.env.example` must both equal `config.py`'s default;
  user customisations belong in `.env`.
- `docs/api-reference.md` — Same `MIN_TRADEABLE_PRICE` / `EDGE_THRESHOLD`
  fixes; deprecated-status note added.
- `docs/known-issues.md` — Added stats.json note: deleting
  `~/.pqi/stats.json` silently disables the EV gate until the next
  `history sync` / `history stats` recompute.

## Session 23 — WR-based edges + run-time snapshots

Repurpose the informational `edge` fields to reflect the EV gate's
view of the world, and snapshot the inputs that produced each
recommendation so `history show` can render the exact WR / Kelly that
applied at run time (not the drifting rolling values).

- `models.py` — `CouncilResult.edge` / `no_edge` repurposed from
  `(score − price)` to `(PerceivedTrueWinRate − side_price)`. Both 0.0
  when no WR snapshot exists (EV gate dormant). Added run-time
  snapshot fields: `perceived_win_rate_at_run` (nullable),
  `kelly_stake_full`, `kelly_stake_scaled`, `kelly_multiplier`. Legacy
  pre-session-23 history entries retain the old edge semantic —
  labeled as such in `history show`.
- `scoring.py` — Threads `perceived_win_rate` + `KELLY_MULTIPLIER`
  into `from_council_verdicts` so the classmethod can compute edges
  and Kelly internally. Model stays pure (no I/O).
- `main.py` `display_council_result` — Renders WR ceiling +
  half-Kelly stake on every BUY panel when a snapshot exists.
- `main.py` `history show <#>` — Same block, reading from the
  snapshot fields rather than recomputing against current stats.
- `docs/roadmap.md` — Reorganised into Themes A / B / C (modelling,
  TUI+trading, ops tooling). Existing flat items renumbered with
  "was item #N" breadcrumbs so old notes cross-reference.

## Session 22 — Dynamic EV gate + Kelly Criterion (advisory)

First use of resolved history as a live input to scoring. Rejects
mathematically -EV BUYs by comparing side price to a rolling win-rate
computed from our own correct/wrong BUYs. Kelly fraction is surfaced
as advisory sizing; **no trading code was added** (still advisor-only).

- `models.py` — New `PerceivedStats` model persisted to
  `~/.pqi/stats.json` (computed_at, true_win_rate, sample_size,
  correct_buys, wrong_buys, kelly_fraction_at_p75, below_min_samples).
  `from_council_verdicts` gains two kwargs: `perceived_win_rate:
  float | None` and `kelly_multiplier: float`. **Final** gate added
  after the existing stack (price floor → ceiling → contrarian →
  market-aligned): if `recommendation != "PASS"` and `side_price ≥
  perceived_win_rate`, flip to PASS with reason "Big Drawdown Risk
  (-EV)". Pre-EV gate order is otherwise untouched.
- `stats.py` — NEW. Three pure functions:
  `recompute_from_history(min_samples)` walks `history.jsonl`, counts
  non-legacy `correct_buy` vs `wrong_buy` buckets, writes stats.json
  atomically. `load_perceived_stats()` tolerates missing/malformed
  file (cold-start safety → returns None). `kelly_fraction(win_rate,
  price, multiplier=1.0)` implements `f* = (bp − q) / b`, returning
  0.0 on -EV / degenerate inputs.
- `scoring.py` — Lazy-loads stats at build time. Only feeds
  `perceived_win_rate` into the model when `EV_GATE_ENABLED=true`
  AND `not stats.below_min_samples`. Otherwise gate stays dormant
  and verdicts match the pre-EV system exactly.
- `config.py` / `.env.example` — New knobs: `EV_GATE_ENABLED`
  (default true), `MIN_WIN_RATE_SAMPLES` (default 20 — gate stays
  off until that many resolved BUYs), `KELLY_MULTIPLIER` (default
  0.5 = half-Kelly).
- `main.py` — `history sync` (including `--auto`) and `history stats`
  trigger `recompute_from_history` so stats.json stays fresh.
  `history stats` overview gains a PerceivedStats block (WR, sample,
  Kelly @ 75¢). Recommendation panel on BUY results shows the
  advisory Kelly stake alongside price + expected ROI.
- Cold-start safety: every I/O path (load + scoring) tolerates a
  missing stats.json, so first-ever runs behave identically to
  sessions ≤21.

---

## Session 21 — CLOB price history enrichment (two-sided)

- `models.py` — **New models:** `PricePoint` (timestamp + price), `SharpMove`
  (timestamp + delta_pp), `SidePriceHistory` (per-side computed trend metrics:
  token_id, data_points, weighted_avg, change_1w_pp, change_24h_pp,
  volatility_24h, direction, sharp_moves), `PriceHistory` (wrapper:
  `yes: SidePriceHistory`, `no: SidePriceHistory`, `fetched_at: datetime`).
  YES + NO CLOB prices are independent (don't sum to 1.0 due to spread/vig).
  `MarketData` gains `clob_token_ids: list[str]` (default empty).
  `BriefingDocument` gains `price_history: PriceHistory | None` (default None).
- `market/clob.py` — **NEW.** CLOB API client for two-sided price history.
  `fetch_price_history(market)` fetches YES and NO token prices in parallel via
  `asyncio.gather()`. Requires `len(clob_token_ids) >= 2`. Per-side fetch via
  `_fetch_side(market_id, token_id, label)`. `_compute_side_metrics()` pure
  function: EMA weighted average (half-life 2 days), direction via linear
  regression slope (+-2pp threshold), volatility, sharp moves (>5pp, last 3).
  Non-fatal: returns None if either side fails.
- `market/polymarket.py` — `_parse_market()` now extracts `clobTokenIds` JSON
  string field from Gamma API response into `MarketData.clob_token_ids`.
- `config.py` — New section: Polymarket CLOB API. `CLOB_BASE_URL` code constant.
  Tunables: `PRICE_HISTORY_INTERVAL` (default `1w`), `PRICE_HISTORY_FIDELITY`
  (default `60` minutes).
- `.env.example` — added CLOB price history section.
- `agents/council.py` — `_format_briefing()` renders conditional two-sided
  `MARKET PRICE HISTORY` block between EVENT TIMING and VOLUME.
- `main.py` — `_run_analysis()` fetches CLOB price history between briefing
  build and council summon. `display_council_result()` shows `7d Trend YES`
  and `7d Trend NO` lines.
- `requirements.txt` — added `py-clob-client>=0.1`.

## Session 20 — Bear prompt rewrite + retry logic

- `agents/personas.py` — Bear prompt rewrite. Removed structural-bias phrases.
  Added evidence-binding guards and hard confidence floor (<=30 with <2
  underdog data points). Root cause: audit showed 38% hit rate, 81% NO-lean.
- `config.py` — Dead code removed (`CONSENSUS_YES_FLOOR`, `CONSENSUS_NO_CEILING`).
  New vars: `MIN_COUNCIL_VERDICTS`, `AGENT_MAX_RETRIES`, `BEAR_WEIGHT`.
- `agents/council.py` — Council quorum gate + agent retry with exponential backoff.
- `scoring.py` — Bear weight wired into `agent_weights`.
- `models.py` + `main.py` — Signal type rename: STRONG->HIGH VALUE, CONFIRMED->
  MODERATE VALUE, CAUTIOUS->LOW MARGIN.
- `main.py` — UX color overhaul (all BUY green, signal label colors).

## Session 19 — ROI threshold + retrospective

- `config.py` — `MIN_EXPECTED_ROI` default 0.15 -> 0.10. Widens LOW MARGIN band.
- `docs/retrospectives/eval_tier1_roi_2026_04.md` — Full retrospective report.
- Bear audit findings documented (not acted on until session 20).

## Session 18 — History filtering + signal glyphs

- `main.py` — `history list` filter flags: `--outcome`, `--rec`, `--conf`.
  `_signal_glyph()` + `Sig` column. `_analyzed_market_ids()` + `Seen` column.
  `history stats` council-level lean accuracy.

## Session 17 — History validation system

- `models.py` — `CouncilResult.briefing` field.
- `history.py` — Full rewrite: full-fidelity persistence, legacy migration.
- `resolution.py` — NEW. Fetch resolution from `/markets/{id}`.
- `analytics.py` — NEW. `classify_outcome()`, `agent_accuracy()`,
  `signal_postmortem()`.
- `main.py` — `pqi history` sub-app: list/show/sync/stats commands.
- `_run_analysis()` — shared briefing support for sibling markets.

---

## Implemented & tested (cumulative)

- `agents/personas.py` — all 7 system prompts, AGENT_PERSONAS dict, build_prompt().
  SHARED_PREAMBLE rules: TWO-SIDED MARKET, TWO-SIDED CONFIDENCE, DUAL-PERSPECTIVE
  ANALYSIS, TIME-TO-REVERSAL. Bull/Bear are dynamic (price-aware, favored/underdog).
  Contrarian prioritizes competitively plausible disruptions.
  All 7 output formats use `confidence_yes` + `confidence_no`.
- `config.py` — reads ALL tunables via `os.getenv(KEY, default)`. True code constants
  only: `GAMMA_BASE_URL`, `CLOB_BASE_URL`, `VALID_BACKENDS`.
- `models.py` — all Pydantic v2 models. Two-sided confidence, probability-first
  recommendation logic, ROI-banded signal types.
- `llm/client.py` — Ollama + Anthropic backends, `async def chat()`.
- `ingestion/synthesizer.py` — LLM Search Architect + parallel Tavily execution.
- `agents/council.py` — 7 agents via `asyncio.gather()`. Quorum gate. Retry with
  exponential backoff. Rules Lawyer injection. Side context for Bull/Bear.
- `scoring.py` — Two-sided scoring. Agent weights (Rules Lawyer, Bear).
- `history.py` — Full-fidelity persistence, legacy migration, upsert by market_id.
- `cache.py` — SQLite cache, price-delta invalidation.
- `resolution.py` — Fetch resolution status from Gamma API.
- `analytics.py` — Outcome classification, per-agent accuracy, postmortem.
- `market/polymarket.py` — Unified fetch path, server-side volume filtering,
  category resolution (three-pass), event enrichment.
- `market/clob.py` — Two-sided CLOB price history client.
- `main.py` — Typer CLI: analyze + categories + history (list/show/sync/stats).
  Sibling market discovery, shared briefings, signal glyphs, history filtering.
