# PQI CLI Reference

Full command, flag, and configuration reference. For the quickstart and a
high-level tour, see the [README](../README.md).

All commands below assume the venv is activated (`source .venv/bin/activate`).
If it isn't, prefix each with `uv run` — e.g. `uv run pqi analyze`.

---

## `analyze`

```bash
pqi analyze [OPTIONS]      # `analyze` is the default command, so `pqi …` works too
```

| Flag               | Short | Type    | Default    | Description                                                                                     |
| ------------------ | ----- | ------- | ---------- | ----------------------------------------------------------------------------------------------- |
| `--top-n`          | `-n`  | `INT`   | `10`       | Number of events to show in the selection list                                                  |
| `--category`       | `-c`  | `TEXT`  | _(all)_    | Filter by broad tag (e.g. `esports`) or specific series slug (e.g. `counter-strike`)            |
| `--top`            | `-t`  | flag    | off        | Skip the selection prompt — auto-pick the highest-volume event                                  |
| `--starts-within`  | `-s`  | `INT`   | _(all)_    | Only show events starting within this many hours. Includes live events. Best for sports/esports |
| `--resolve-within` | `-r`  | `INT`   | _(all)_    | Only show events with markets resolving within this many hours                                  |
| `--live`           | `-l`  | flag    | off        | Only show live events (already started, not yet resolved). Composable with `--starts-within`    |
| `--min-volume`     | `-v`  | `FLOAT` | _(config)_ | Override minimum volume floor in USD (use `-v 0` to see all)                                     |
| `--no-cache`       |       | flag    | off        | Force a fresh council run, ignoring any cached result                                            |
| `--concurrency`    | `-j`  | `INT`   | _(config)_ | Max parallel runs in batch mode. Default from `ANALYZE_BATCH_CONCURRENCY` (1). Ceiling 3 on Anthropic Tier 2 |
| `--summary-only`   |       | flag    | off        | Batch mode only — skip per-event result panels, keep the final summary table                    |
| `--verbose`        | `-V`  | flag    | off        | Show the raw `INFO`/HTTP logs underneath the narrated status flow (debugging)                    |

All flags combine freely. e.g. `pqi analyze --live -c esports -v 0` shows all
live esports events regardless of volume.

### Console output

A single analysis narrates its progress as a numbered status flow — e.g.
`🔎 Identifying the fixture` (with provider sub-steps), `📰 Reading the news
room`, `📈 Charting the order book`, `🧠 The Council of Seven deliberates`,
`⚖️ Weighing the verdict` — then prints the recommendation table. By default the
raw `INFO`/`httpx` logs are hidden; **warnings stay visible in orange** (and
errors in red) because they're notices worth seeing. Pass `--verbose` / `-V` to
bring the raw logs back underneath for debugging (honours `LOG_LEVEL`, default
`INFO`). Batch (multi-select) runs use the live summary table instead of the
narrated flow.

### Examples

```bash
# Interactive — show top 10 events sorted by volume, pick one
pqi analyze

# Esports events starting within 4 hours (upcoming + live matches)
pqi -c esports -s 4

# Live esports only — matches in progress right now
pqi -c esports --live

# Counter-Strike only, resolving within 2 hours, show all regardless of volume
pqi -c counter-strike -r 2 -v 0

# Non-interactive — top event by volume, any category
pqi --top

# Upcoming events within 24 hours, auto-select the highest-volume one
pqi --starts-within 24 --top

# Force fresh analysis (skip cache)
pqi --top --no-cache
```

### Batch mode (multi-select at the prompt)

The event-selection prompt accepts a single index **or** a multi-select spec:

| Input         | Selects                                              |
| ------------- | ---------------------------------------------------- |
| `3`           | Event #3 only (single-run UX — sub-market picker + sibling prompt preserved) |
| `1,3,5`       | Events #1, #3, #5                                    |
| `3-7`         | Events #3 through #7 inclusive (reversed ranges normalized) |
| `1-3,7,9-11`  | Mixed list + range                                   |
| `all`         | Every event in the current list                      |
| `new`         | Only events with **no** prior analysis on any of their markets (empty "Seen" column). Re-prompts if the current list has no unseen events |

When the spec resolves to ≥ 2 events, PQI runs them in a batch:

- Each selected event uses its **headline market** (highest-volume market of the `EventGroup`); no per-event sub-prompt.
- Runs execute with bounded parallelism (`-j`, default 1). A Rich Live status table updates at 4 Hz with per-row phase + percent + elapsed time.
- Persistence (history append + price points) runs **sequentially after** the batch completes — JSONL appends larger than `PIPE_BUF` aren't safe to interleave.
- Failures are isolated per-row (`failed` phase, red styling, error in the summary) — other rows continue.
- `--summary-only` prints only the final summary table.

```bash
# Queue a specific list, parallel ×3 (Anthropic Tier 2 ceiling)
pqi -c esports --live -j 3        # ...at prompt: 1,2,5,7,9

# Run every listed event, summary table only
pqi -c esports -s 24 -j 3 --summary-only   # ...at prompt: all

# Only un-analyzed events — incremental queuing, no re-runs
pqi -c esports -s 24 -j 3 --summary-only   # ...at prompt: new
```

---

## `categories`

```bash
pqi categories
```

Lists available tags (broad) and series slugs (specific) for `--category`.
Two-tier resolution:

1. **Specific series** — exact or partial slug match (e.g. `counter-strike`, `valorant`, `atp`). Server-side filter via Gamma `series_id`.
2. **Broad tag** — matches event tags across all series (e.g. `esports`, `sports`, `crypto`, `politics`). Server-side filter via Gamma `tag_id`.

If a category matches nothing, the command fails loudly and suggests running
`pqi categories`. Resolved lookups are cached to `.pqi/category_cache.json`
(no TTL — Polymarket tag/series IDs are effectively immutable; delete the file
to force a refresh, or override with `CATEGORY_CACHE_PATH`).

### Picking the right tag for scheduled matches

Some intuitively-named tags point at **futures/outright** markets, not the
individual scheduled matches you want with `--starts-within` / `--live`:

| You might type | Resolves to | What it actually contains | Use instead |
| -------------- | ----------- | ------------------------- | ----------- |
| `world-cup`    | tag `519`   | "World Cup Winner", "Group X Winner", "Nation to Reach R16", H2H props — **no per-match events** | `fifa-world-cup`, `soccer`, or `sports` |
| `world-series` | tag `100021`| MLB championship futures  | `mlb`, `baseball`, or `sports` |

Polymarket tags individual matches (e.g. `Norway vs. France`) under
`soccer` / `fifa-world-cup` / `games` / `sports`, **not** under the
tournament-name tag. When `-c <cat> -s <hours>` returns nothing for a sport
that clearly has games today, the category is almost always a futures-only
tag — switch to one of the alternatives above (run `pqi categories` to see
which are live). Esports is unaffected: `-c esports -s 4` works directly
because its match events are tagged `esports`.

---

## `query`

```bash
pqi query "<SQL>"
```

Runs ad-hoc DuckDB SQL against the persisted `history.jsonl`. See
[analytics_queries.md](analytics_queries.md) for starter queries.

> Known issue: the `verdicts` view can fail when DuckDB's schema sample lands on
> rows whose `prompt_flags` holds only the `preamble` key (STRUCT-vs-MAP
> inference). `scripts/reliability_diagram.py` reads the JSONL directly and is
> the reliable fallback. See [known-issues.md](known-issues.md).

---

## `history`

`pqi history` is a sub-app — running it bare prints help.

| Sub-command                    | Description                                                                                                                  |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| `pqi history list [flags]`     | Overview table: market YES/NO, council YES/NO, **lean** (council's directional read), **lean✓**, **vs Mkt** (↕ on disagreement), recommendation, **outcome** (✓/✗/MISS/pass/⏳), winning side, postmortem tagline |
| `pqi history show <#\|run_id>` | Detailed view: header, recommendation panel, **per-agent verdict table** (hit/✗ + Brier when resolved), postmortem, briefing recap |
| `pqi history sync [--auto]`    | Fetch resolutions for unresolved entries from the Gamma API. `--auto` only touches entries past their `end_date`            |
| `pqi history stats`            | Council overview (totals, outcome counts, council-level lean accuracy) + per-agent leaderboard (n, hit %, weighted hit %, lean %, Brier, high-conf accuracy %) |
| `pqi history equity [flags]`   | Bankroll-denominated PnL, max drawdown, per-trade Sharpe over resolved BUYs under a buy-and-hold-to-resolution model         |

`list` and `stats` automatically spawn a background sync subprocess for any
unresolved entries past their `end_date` (suppress with `--no-auto-sync`).

History is **upserted keyed on `market.id`** — re-running `analyze` on the same
market fully overwrites the previous entry rather than appending a duplicate.
The `run_id` is preserved across re-runs; `resolution`/`analysis` are cleared
(re-synced on the next `history sync`). Sibling markets under one parent event
stay distinct because the key is market id, not event id.

### `history list` flags

| Flag             | Short | Type   | Default  | Description                                                                                    |
| ---------------- | ----- | ------ | -------- | ---------------------------------------------------------------------------------------------- |
| `--last`         | `-n`  | `INT`  | `20`     | Page size — most recent entries to show. Ignored when `--all` is set                           |
| `--offset`       | `-o`  | `INT`  | `0`      | Skip this many recent entries (paginate backwards)                                             |
| `--all`          | `-a`  | flag   | off      | Show every entry. Numbering starts at `#1` (oldest)                                            |
| `--no-auto-sync` |       | flag   | off      | Skip the background auto-resolve subprocess                                                     |
| `--outcome`      |       | `TEXT` | _(none)_ | Filter by outcome class (values below)                                                         |
| `--rec`          |       | `TEXT` | _(none)_ | Filter by recommendation type (values below)                                                   |
| `--conf`         |       | `TEXT` | _(none)_ | Filter by overall confidence label (values below)                                              |

Filters are independent and combinable — e.g.
`pqi history list --all --conf high --rec pass` shows every HIGH-confidence run
that still PASSed (gate-blocked signals). Filters apply **after** numbering, so
the `#` column is stable and `pqi history show <#>` always refers to the same
entry across filtered views.

#### `--outcome` values

| Value (aliases)                     | Matches                                                                                              |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `correct_buy` (`buy`, `win`)        | BUY whose side gained in price from entry to settlement (PnL > 0, incl. partial settlements)          |
| `wrong_buy` (`wrong`, `loss`)       | BUY whose side lost in price from entry to settlement (PnL < 0)                                       |
| `missed_opportunity` (`miss`)       | PASS where a clean winner was declared and the council favored it with prob ≥ 65%                     |
| `correct_pass` (`pass`)             | PASS where the council was correctly unsure or wrong, plus exact-push BUYs (rare)                     |
| `voided` (`void`)                   | Market cancelled / refunded, no redemption price (true void, not a draw)                              |
| `unresolved` (`pending`)            | Resolution not yet fetched from the Gamma API                                                         |

#### `--rec` values

| Value (aliases)   | Matches                                          |
| ----------------- | ------------------------------------------------ |
| `pass`            | `PASS` recommendations                           |
| `buy`             | Both `BUY YES` and `BUY NO`                      |
| `buy_yes` (`yes`) | `BUY YES` only                                   |
| `buy_no` (`no`)   | `BUY NO` only                                    |

#### `--conf` values

Filters by `CouncilResult.confidence_level` (the higher of the two per-side mean
confidences).

| Value (aliases)       | Matches                                                                  |
| --------------------- | ------------------------------------------------------------------------ |
| `high` (`h`)          | mean confidence ≥ `CONFIDENCE_HIGH_THRESHOLD` (default 0.67) on the favored side |
| `medium` (`med`, `m`) | middle band                                                              |
| `low` (`l`)           | below the medium floor                                                   |

### Insight recipes — useful filter combinations

| Question | Command | How to read it |
| --- | --- | --- |
| **Where are my gates too tight?** | `pqi history list --all --conf high --rec pass` | HIGH-confidence PASSes — the council was sure but a gate blocked it. If most `Lean✓` are `✓`, gates leave money on the table (loosen `MIN_EXPECTED_ROI` / `MAX_TRADEABLE_PRICE`); if `✗`, gates are correctly catching overconfidence. |
| **Were my "missed opportunities" real?** | `pqi history list --all --outcome miss` | Each row is a PASS where the council favored the winner with prob ≥ 65%. Read `pass_reason` via `history show <#>`; a repeating reason is the lever to tune. |
| **When the council bets HIGH, how often is it right?** | `pqi history list --all --conf high --rec buy` | The `✓`/`✗` rate here is your *actionable* accuracy — the population real trading would touch. |
| **Is the council brave enough?** | `pqi history list --all --rec pass --outcome miss` vs `--rec buy --outcome correct_buy` | Many misses + few correct buys = too cautious. Few of either = rarely producing actionable reads. |
| **Where does it blow up loudly?** | `pqi history list --all --outcome wrong_buy --conf high` | Confident BUYs that lost. Open with `history show <#>`; repeated loud agents on the wrong side point at a persona bias. |
| **Does HIGH confidence mean anything?** | `pqi history list --all --conf high` vs `--conf medium` | Compare `Lean✓` rates. If equal, the label is noise — `CONFIDENCE_HIGH_THRESHOLD` may be too low. |
| **Are LOW-conf PASSes correctly cautious?** | `pqi history list --all --conf low --rec pass` | Should be the "I don't know" pile — `Lean✓` near 50%. |
| **Contrarian wins?** | `pqi history list --all --rec buy_no` (and `buy_yes`), watch the `vs Mkt` `↕` markers | Cases where the council disagreed with the market and won — study which agents drove them. |

**Reading tips:**

- `Lean✓` is **gate-independent** — was the raw council read right, regardless of whether it converted to a BUY. The most direct measure of council intelligence.
- `outcome` is **gate-aware** — did the system (council + gates) make the right call.
- `Lean✓ = ✓` but `outcome = MISS` → system failure (council saw it, gates blocked it). Several in a row → relax gates.
- `Lean✓ = ✗` but `outcome = pass` → system success (council wrong, gates stopped you).
- Always pair filter views with `history show <#>` on a few rows — the postmortem lists which agents called the winner, where prompt-tuning leverage lives.

### `history equity`

Walks resolved BUYs chronologically under a buy-and-hold-to-resolution model and
reports a bankroll-denominated equity curve. PASSes, unresolved entries, and
true voids are excluded.

| Flag                   | Short | Type    | Default   | Description                                                                                     |
| ---------------------- | ----- | ------- | --------- | ----------------------------------------------------------------------------------------------- |
| `--bankroll`           | `-b`  | `FLOAT` | `100.0`   | Starting bankroll in dollars                                                                     |
| `--stake`              | `-s`  | `FLOAT` | `5.0`     | Per-trade stake. Dollars in `--mode dollars`, shares in `--mode shares`. Unused in `--mode kelly` |
| `--mode`               | `-m`  | `TEXT`  | `dollars` | `dollars` = fixed-$ stake; `shares` = fixed share count; `kelly` = walk-forward half-Kelly using the `kelly_stake_scaled` fraction frozen on each `CouncilResult` (falls back to `--kelly-fallback-pct` when the EV gate was dormant) |
| `--kelly-fallback-pct` | `-k`  | `FLOAT` | `0.05`    | Fraction of **starting** bankroll used as the flat fallback for pre-EV-gate BUYs in `--mode kelly` (scales with capital). Ignored in other modes |
| `--last`               | `-n`  | `INT`   | `15`      | Show the last N trades in the detail table. `0` shows all                                       |
| `--no-auto-sync`       |       | flag    | off       | Skip background `history sync`                                                                  |

**PnL math (per-share Polymarket mechanics):**

```
BUY YES  shares=N entry=p  →  cost = N * p,       proceeds = N * final_yes_price
BUY NO   shares=N entry=p  →  cost = N * (1-p),   proceeds = N * (1 - final_yes_price)
pnl = proceeds - cost
```

A 5-share BUY YES at 50¢ that wins clean settles at $1.00: cost $2.50, proceeds
$5.00, PnL **+$2.50**. Intermediate settlements (e.g. 0.50 draws) are real PnL at
the settlement price, not voids.

**Reported metrics:** final bankroll / net PnL · peak/trough · max drawdown
(% of peak and $) · wins/losses/win-rate · best/worst trade · mean/stdev PnL ·
**per-trade Sharpe** (`mean(pnl/cost) / stdev(pnl/cost)`, unitless, baseline 0;
descriptive at small n) · **sizing mix** (Kelly mode) · **operational cost** (an
upper-bound pipeline-spend ledger across every in-scope entry; cached re-runs
aren't subtracted; `Net of cost` = equity PnL − infra cost).

**Kelly mode (walk-forward):** each `CouncilResult` carries a `kelly_stake_scaled`
snapshot — the advisory half-Kelly fraction computed at analysis time from the
band-smoothed WR that existed *then*. `--mode kelly` reads it verbatim (no
look-ahead). Stake = `current_balance × kelly_stake_scaled`, capped at balance.
When the EV gate was dormant (`resolved_buys < MIN_WIN_RATE_SAMPLES`) the stored
fraction is `0.0`, so the walker falls back to `bankroll × --kelly-fallback-pct`
(default 5% of starting bankroll). The infra-cost row is *not* bankroll-scaled,
so `Net of cost` flips from negative at low bankrolls to positive above the
PnL/infra break-even.

```bash
pqi history equity                                   # $100 bankroll, $5/trade, last 15
pqi history equity -b 1000 -s 100 -n 0               # larger bankroll, 10% stake, all trades
pqi history equity --mode shares --stake 5           # fixed 5 shares per trade
pqi history equity --mode kelly                      # walk-forward half-Kelly
pqi history equity --mode kelly -b 1000              # fallback auto-scales to $50
pqi history equity --mode kelly -b 500 -k 0.02       # tighter 2% fallback
```

### Per-agent accuracy (`history stats`)

The leaderboard tracks each council member across all resolved, non-legacy runs:

- **Hit %** — fraction of times the agent's probability (>0.5 = YES, <0.5 = NO) matched the winner
- **Weighted Hit %** — same, weighted by the agent's confidence on the side it picked
- **Brier** — mean squared error between probability and outcome (lower is better)
- **HiConf %** — accuracy when the agent committed with confidence ≥ 70% (with raw ✓/✗ counts)

This surfaces *which agents identify the right side most confidently* and *who is
loudest when wrong*.

---

## Event-centric discovery

The CLI shows **events** (not individual markets) as primary rows. A BO3 esports
match typically groups: **Moneyline** (match winner), **Map 1 Winner**, **Map 2
Winner**. Selecting a multi-market event opens a sub-table to pick the specific
market; single-market events skip it.

After analyzing a market, PQI offers to analyze its siblings — **the Tavily
briefing built for the parent is reused** (same event = same news), so the
synthesizer runs once per event. The council still runs per sibling because each
market has its own resolution criteria.

**Parallel enrichment.** The Gamma list endpoint (`/events`) returns `null` for
`live`/`ended`/`score`/`gameStatus`. PQI makes parallel `GET /events/{id}` detail
calls (capped by `EVENT_ENRICHMENT_MAX`, default 30) for reliable live/ended
status and start times — adds ~1–2 s.

**Timing — start vs resolution.** `--starts-within` / `--live` filter on
*event start time* (`Starts` column; no server-side filter, so PQI fetches a
larger batch and filters client-side). `--resolve-within` filters on *resolution
time* (`Resolves` column) via the API's `end_date_min`/`end_date_max`.

**Volume floor.** Events below `MIN_VOLUME_USD` ($10K) are excluded by default;
for short windows (< 4h) / live / `--starts-within`, the floor drops to
`MIN_VOLUME_SHORT_WINDOW_USD` ($500). `-v 0` shows everything.

---

## Caching

Council results are cached in SQLite (`.pqi/cache.db`). A cached result is reused
if the market's YES price moved less than `CACHE_PRICE_DELTA` (default 2%) since
the last run. `--no-cache` forces a fresh run. Path configurable via
`CACHE_DB_PATH`. Category lookups cache to `.pqi/category_cache.json` (see
[`categories`](#categories)).

---

## Backends & reliability

| Backend            | Use for              | Cost                                          |
| ------------------ | -------------------- | --------------------------------------------- |
| `ollama` (default) | Local dev, RTX 2070+ | Free                                          |
| `anthropic`        | Production / accuracy| ~$0.015/council run (`claude-haiku-4-5`, cold cache; lower warm) |

Switch with `LLM_BACKEND=anthropic`. Anthropic runs all 7 agents fully in
parallel; Ollama is throttled by `LOCAL_LLM_CONCURRENCY` (default 1) to prevent
GPU OOM.

### Prompt caching (Anthropic only)

With `ANTHROPIC_PROMPT_CACHE_ENABLED=true` (default), each Anthropic call's system
prompt is sent in a 5-minute ephemeral cache block. Repeat calls within the TTL
whose system prompt matches byte-for-byte bill at ~10% of input price. Caching is
server-side and **does not change model behavior** — same prompt in, same
distribution out. Verify with `LOG_LEVEL=DEBUG`:

```
anthropic usage: input=… output=… cache_read=… cache_create=…
```

`cache_create > 0` on the first call, `cache_read > 0` on later calls within 5
minutes = working. Both `0` = the prefix is below the model's minimum cacheable
size (~1024–2048 tokens) and `cache_control` is silently ignored.

### Retry (Anthropic only)

Every Anthropic call is wrapped in a centralized exponential-backoff retry in
`src/pqi/llm/client.py` (the SDK's built-in auto-retry is disabled so this layer
owns the policy). Retried: **5xx** (incl. `529 OverloadedError`), **429**,
`APIConnectionError`, `APITimeoutError`. Non-retryable 4xx escape immediately.
Schedule `delay = base * 2**attempt` capped at `max_delay`; at defaults
(`ANTHROPIC_MAX_RETRIES=5`, base `1.0`, max `30.0`) the waits are 1, 2, 4, 8, 16,
30 s → ~61 s worst case. The council's per-agent loop (`AGENT_MAX_RETRIES`,
default 5) wraps this, so one agent gets up to 5 × 6 = 30 attempts.

---

## Full tuning reference

Every key below is read from `.env` (copy from
[`.env.example`](../.env.example), which carries an inline comment for each).

| Variable                      | Default | Effect                                                                               |
| ----------------------------- | ------- | ------------------------------------------------------------------------------------ |
| `LLM_BACKEND`                 | `ollama`| `anthropic` (prod) or `ollama` (local dev)                                           |
| `MIN_COUNCIL_PROBABILITY`     | `0.60`  | Minimum council probability to issue a market-aligned BUY                            |
| `MIN_CONTRARIAN_PROBABILITY`  | `0.60`  | Minimum council probability for a contrarian BUY (against market)                    |
| `MIN_CONTRARIAN_CONFIDENCE`   | `0.80`  | Minimum per-side confidence for a contrarian BUY                                     |
| `CONFIDENCE_HIGH_THRESHOLD`   | `0.67`  | Mean confidence required for HIGH label                                              |
| `EDGE_THRESHOLD`              | `0.07`  | **Deprecated and unused** — kept so existing `.env` files don't error on import      |
| `MIN_TRADEABLE_PRICE`         | `0.09`  | Minimum per-share price to issue a BUY (prevents "$0.01 NO" signals)                 |
| `MAX_TRADEABLE_PRICE`         | `0.90`  | Maximum per-share price (near-resolved ceiling)                                      |
| `MIN_EXPECTED_ROI`            | `0.10`  | Minimum max ROI `(1 - price) / price` for a market-aligned BUY (≈ price ≤ 0.91)       |
| `MIN_CONTRARIAN_ROI`          | `0.20`  | Stricter ROI floor for contrarian BUYs (≈ price ≤ 0.833)                             |
| `EV_GATE_ENABLED`             | `true`  | Master switch for the final EV gate (PerceivedTrueWinRate ceiling)                   |
| `MIN_WIN_RATE_SAMPLES`        | `20`    | Resolved-BUY threshold before the EV gate activates                                  |
| `KELLY_MULTIPLIER`            | `0.5`   | Advisory Kelly scaler (0.5 = half-Kelly; 1.0 = full)                                 |
| `MIN_VOLUME_USD`              | `10000` | Volume floor for general queries                                                     |
| `MIN_VOLUME_SHORT_WINDOW_USD` | `500`   | Volume floor for short time-window / live queries                                    |
| `RULES_LAWYER_WEIGHT`         | `1`     | Rules Lawyer weight multiplier (static fallback; overridden by dynamic weights)      |
| `BEAR_WEIGHT`                 | `1.0`   | Bear agent weight (static fallback; overridden by dynamic weights when enabled)      |
| `AGENT_WEIGHTS_ENABLED`       | `true`  | Load dynamic per-agent reliability weights from `.pqi/agent_weights.json`            |
| `MIN_AGENT_WEIGHT_SAMPLES`    | `30`    | Resolved-BUY threshold per agent before its dynamic weight applies (else 1.0)        |
| `AGENT_WEIGHT_SHRINK`         | `2.0`   | Shrinkage strength: `weight = 1 + shrink * (lean_rate − council_avg)`                |
| `MIN_COUNCIL_VERDICTS`        | `5`     | Minimum agents (of 7) that must return valid verdicts; aborts if below               |
| `AGENT_MAX_RETRIES`           | `5`     | Per-agent retries on LLM/parse failure (exponential backoff, 30s cap)                |
| `LOCAL_LLM_CONCURRENCY`       | `1`     | Max parallel Ollama calls (no effect on Anthropic)                                   |
| `ANALYZE_BATCH_CONCURRENCY`   | `1`     | Max parallel runs when `analyze` gets a multi-select spec. Ceiling 3 on Anthropic Tier 2; override with `-j` |
| `ANTHROPIC_PROMPT_CACHE_ENABLED` | `true` | Wrap the Anthropic system prompt in a 5-min ephemeral cache block (no behavior change) |
| `ANTHROPIC_MAX_RETRIES`       | `5`     | Max additional retries per Anthropic call on transient failures (5xx incl. 529, 429, connection/timeout) |
| `ANTHROPIC_RETRY_BASE_DELAY`  | `1.0`   | Base delay (s) for exponential retry backoff (`delay = base * 2**attempt`)           |
| `ANTHROPIC_RETRY_MAX_DELAY`   | `30.0`  | Cap on per-attempt backoff (s); worst-case cumulative wait at defaults ≈ 61s         |
| `PANDASCORE_API_KEY`          | _(empty)_ | PandaScore bearer token. Empty disables the resolver (esports → Tavily-only)       |
| `PANDASCORE_ENABLED`          | `true`  | Master switch for the PandaScore resolver (false bypasses even if key is set)        |
| `PANDASCORE_FUZZY_THRESHOLD`  | `75`    | Min `min(token_set_ratio_a, token_set_ratio_b)` to accept a match candidate          |
| `PANDASCORE_WINDOW_HOURS`     | `48`    | Search window around `event_start_time` for candidate PandaScore matches             |
| `PANDASCORE_RECENT_FORM_COUNT`| `5`     | Last-N completed matches per team for the form summary                               |
| `TAVILY_SEARCHES_PER_MARKET`  | `5`     | Tavily queries per analysis (3-5)                                                    |
| `TAVILY_SNIPPET_CAP`          | `5000`  | Max chars of search snippets sent to the LLM                                         |
| `EVENT_ENRICHMENT_MAX`        | `30`    | Max parallel /events/{id} detail calls for enrichment                                |
| `CACHE_PRICE_DELTA`           | `0.02`  | Price movement threshold to invalidate cached results                                |
| `CATEGORY_CACHE_PATH`         | `.pqi/category_cache.json` | JSON cache of resolved `--category` lookups (only positive hits cached) |
| `PRICE_HISTORY_INTERVAL`      | `1w`    | CLOB price history window (1d, 1w, 1m)                                               |
| `PRICE_HISTORY_FIDELITY`      | `60`    | Price history resolution in minutes (60 = hourly)                                    |
| `TAVILY_COST_PER_SEARCH`      | `0.008` | Estimated $/Tavily search (used by `history equity` cost row; not a spend cap)        |
| `SEARCH_ARCHITECT_COST`       | `0.001` | Estimated $/Search Architect LLM call per analysis                                   |
| `SYNTHESIZER_COST`            | `0.005` | Estimated $/Synthesizer LLM call per analysis                                        |
| `COUNCIL_COST_PER_RUN`        | `0.015` | Estimated $/full 7-agent council run                                                 |
| `PQI_DATA_DIR`                | `<repo>/.pqi` | Root for all persisted state (history, cache, weights, reports). Override to relocate |
