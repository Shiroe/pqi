# Session Log — Changelog & Implementation Details

Per-session change log with detailed diffs. Referenced from CLAUDE.md.

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
