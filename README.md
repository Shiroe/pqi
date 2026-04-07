# PQI — Polymarket Qualitative Intelligence

Identifies mispriced odds in Polymarket event-driven prediction markets using a
7-agent LLM council ("The Council of Seven"). **Advisor only — no auto-trading.**

## How it works

```
Polymarket Gamma API (/events endpoint)
  → Event grouping (sibling markets under one event, e.g. Moneyline + Map 1 + Map 2)
    → Parallel enrichment (GET /events/{id} for reliable live/ended/start time)
      → User selects event → picks specific market
        → Synthesizer (5 Tavily searches → BriefingDocument)
          → Council of Seven (7 parallel LLM calls, two-sided confidence)
            → Probability-first recommendation: BUY YES / BUY NO / PASS
```

The council computes two independent scores: a YES-side and NO-side weighted average,
each using per-agent directional confidence. A recommendation is issued when the
council's probability on its favored side clears the threshold and confidence is HIGH.
Edge (council vs market price) determines the signal type (STRONG / CONFIRMED / CAUTIOUS),
but is not a gate for recommendations.

---

## Setup

**1. Clone and create a virtual environment**

```bash
cd /path/to/parent          # one level ABOVE the pqi/ directory
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

**2. Install dependencies**

```bash
pip install -r pqi/requirements.txt
# or with uv:
uv pip install -r pqi/requirements.txt
```

**3. Configure environment**

```bash
cp pqi/.env.example pqi/.env
# Edit pqi/.env — minimum required keys shown below
```

```dotenv
# Required always
TAVILY_API_KEY=tvly-...

# Dev (local Ollama)
LLM_BACKEND=ollama
OLLAMA_MODEL=llama3.1:8b
LOCAL_LLM_CONCURRENCY=1    # keep low on consumer GPUs (RTX 2070 etc.) to prevent OOM

# Prod (Anthropic API) — runs all 7 agents fully in parallel
# LLM_BACKEND=anthropic
# ANTHROPIC_API_KEY=sk-ant-...
# ANTHROPIC_MODEL=claude-haiku-4-5-20251001
```

**4. (Dev only) Start Ollama and pull the model**

```bash
ollama serve                # if not already running as a service
ollama pull llama3.1:8b
```

---

## Usage

Run from the **parent directory** of `pqi/`:

```bash
python3 -m pqi.main [COMMAND] [OPTIONS]
```

### Commands

| Command | Description |
|---------|-------------|
| `analyze` (default) | Fetch events, run the council, print recommendations |
| `categories` | List available tags and series slugs for `--category` |
| `history` | Show past council runs from `~/.pqi/history.jsonl` |

### Flags (analyze command)

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--top-n` | `-n` | `INT` | `10` | Number of events to show in the selection list |
| `--category` | `-c` | `TEXT` | _(all)_ | Filter by broad tag (e.g. `esports`) or specific series slug (e.g. `counter-strike`) |
| `--top` | `-t` | flag | off | Skip the selection prompt — auto-pick the highest-volume event |
| `--starts-within` | `-s` | `INT` | _(all)_ | Only show events starting within this many hours. Includes live events. Best for sports/esports |
| `--resolve-within` | `-r` | `INT` | _(all)_ | Only show events with markets resolving within this many hours |
| `--live` | `-l` | flag | off | Only show live events (already started, not yet resolved). Composable with `--starts-within` |
| `--min-volume` | `-v` | `FLOAT` | _(config)_ | Override minimum volume floor in USD (use `-v 0` to see all) |
| `--no-cache` | | flag | off | Force a fresh council run, ignoring any cached result |

All flags can be freely combined. For example, `--live -c esports -v 0` shows all
live esports events regardless of volume.

### Flags (history command)

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--last` | `-n` | `INT` | `20` | Number of recent entries to show |

### Examples

```bash
# Interactive — show top 10 events sorted by volume, pick one
python3 -m pqi.main

# Esports events starting within 4 hours (upcoming + live matches)
python3 -m pqi.main -c esports -s 4

# Live esports only — matches in progress right now
python3 -m pqi.main -c esports --live

# Counter-Strike only, resolving within 2 hours, show all regardless of volume
python3 -m pqi.main -c counter-strike -r 2 -v 0

# Non-interactive — top event by volume, any category
python3 -m pqi.main --top

# Upcoming events within 24 hours, auto-select the highest-volume one
python3 -m pqi.main --starts-within 24 --top

# Force fresh analysis (skip cache)
python3 -m pqi.main --top --no-cache

# List available categories
python3 -m pqi.main categories

# Show last 10 council runs
python3 -m pqi.main history -n 10
```

---

## Event-centric discovery

The CLI shows **events** (not individual markets) as the primary rows. Each event
groups sibling markets — for example, a BO3 esports match typically has:

- **Moneyline** — who wins the match overall
- **Map 1 Winner** — who wins map 1
- **Map 2 Winner** — who wins map 2

When you select a multi-market event, a sub-table appears to pick which specific
market to analyze. Single-market events skip this step.

After analyzing a market, PQI offers to analyze its siblings too (no extra API call
needed — siblings are already known from the event group).

### Parallel enrichment

The Gamma API's list endpoint (`/events`) returns `null` for `live`, `ended`, `score`,
and `gameStatus` fields. To get reliable data, PQI makes parallel `GET /events/{id}`
detail calls for each candidate event after the initial list fetch. This adds ~1-2
seconds but ensures accurate live/ended status and event start times. The number of
parallel calls is capped by `EVENT_ENRICHMENT_MAX` (default 30).

### Timing: event start vs resolution

PQI strictly distinguishes two times:

- **Event start time** (`Starts` column) — when the real-world event begins (match kickoff,
  hearing date). This is what `--starts-within` and `--live` filter on. The Gamma API
  has no server-side filter for this, so PQI fetches a larger batch and filters client-side.

- **Resolution time** (`Resolves` column) — when the market expires and can settle. This
  is what `--resolve-within` filters on, using the API's `end_date_min`/`end_date_max`
  server-side params.

The "True Start Time" heuristic derives the event start from child markets'
`eventStartTime` field (a market-level API field, not event-level).

---

## What the output looks like

### Event selection table

```
Active Polymarket Events
  #  Event                                       Starts  Mkts  YES    NO     Volume (USD)
 ── ──────────────────────────────────────────── ─────── ───── ────── ────── ──────────────
  1  Team Liquid vs Sentinels LIVE                  2h      3   65%    35%       $67,000
  2  Cloud9 vs NRG                                  5h      3   52%    48%       $43,000
  3  Will BTC hit $100k by July?                    —       1   38%    62%      $890,000
```

### Council verdicts + recommendation

```
Council of Seven — Verdicts
  Agent                          Prob %  Conf Y%  Conf N%   Key Factor
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  The Rules Lawyer                  71%      78%      62%   ...
  The Bull (Favorite's Advocate)    84%      85%      72%   ...
  The Bear (Underdog's Advocate)    38%      60%      78%   ...
  The Base-Rate Statistician        62%      65%      55%   ...
  The Sentiment Analyst             70%      60%      45%   ...
  The Domain Specialist             72%      70%      60%   ...
  The Contrarian                    30%      40%      50%   ...

╭─────────────── Recommendation ───────────────╮
│ Council YES:    75.2%  |  Council NO:  24.3% │
│ Market Prices:  YES = 66.0%  |  NO = 34.0%   │
│ YES Edge:       +9.2%  |  NO Edge:  -9.7%    │
│ Confidence:     HIGH                         │
│                                              │
│ >>> BUY YES @ $0.66/share — STRONG <<<       │
╰──────────────────────────────────────────────╯
```

### Recommendation types

| Recommendation | Meaning |
|----------------|---------|
| **BUY YES — STRONG** | Council favors YES, positive edge (council thinks YES is underpriced) |
| **BUY YES — CONFIRMED** | Council favors YES, edge near zero (council agrees with market) |
| **BUY YES — CAUTIOUS** | Council favors YES, negative edge (council thinks YES is slightly overpriced but still bets) |
| **BUY NO — CONTRARIAN** | Council disagrees with market — stricter probability and confidence gates required |
| **PASS** | No actionable signal. Reason shown (e.g. "Council prob too low", "Contrarian call: confidence below threshold") |

The same labels apply symmetrically to BUY NO.

---

## Category filter (`--category`)

Two-tier resolution:
1. **Specific series** — exact or partial slug match (e.g. `counter-strike`, `valorant`, `atp`). Server-side filter via Gamma API `series_id`.
2. **Broad tag** — matches event tags across all series (e.g. `esports`, `sports`, `crypto`, `politics`). Server-side filter via Gamma API `tag_id`.

Run `pqi categories` to see available tags and series slugs. If the category doesn't
match anything, the command fails loudly with a suggestion to check `pqi categories`.

## Starts-within filter (`--starts-within`)

Filters to events whose real-world start time is within the next N hours. Includes
live events (already started but not resolved). This is the primary filter for
sports/esports — use `-s 4` to see matches starting or in progress within 4 hours.

Since the API has no server-side `eventStartTime` filter, PQI fetches a larger batch
(up to 500 events) and filters client-side. The volume floor is automatically relaxed
for short windows to show low-volume live matches.

## Live filter (`--live`)

Shows only events currently in progress. Uses the `live` field from enriched
`/events/{id}` detail responses. Falls back to `event_start_time < now` when the
API field is unavailable.

Composable with `--starts-within`: `--live -s 8` shows live events plus upcoming
ones within 8 hours.

## Resolve-within filter (`--resolve-within`)

Filters to events with markets resolving within the next N hours. Uses the Gamma API's
`end_date_min`/`end_date_max` server-side params for resolution time (not event start).

## Volume floor (`--min-volume`)

By default, events below `MIN_VOLUME_USD` ($10K total) are excluded. For short time
windows (< 4h), live events, or `--starts-within` queries, the floor drops to
`MIN_VOLUME_SHORT_WINDOW_USD` ($500). Use `-v 0` to see all events regardless of volume.

---

## Tuning

All thresholds are set in `.env` (see `.env.example` for full documentation):

| Variable | Default | Effect |
|----------|---------|--------|
| `MIN_COUNCIL_PROBABILITY` | `0.60` | Minimum council probability to issue a market-aligned BUY |
| `MIN_CONTRARIAN_PROBABILITY` | `0.70` | Minimum council probability for a contrarian BUY (against market) |
| `MIN_CONTRARIAN_CONFIDENCE` | `0.80` | Minimum per-side confidence for a contrarian BUY |
| `CONFIDENCE_HIGH_THRESHOLD` | `0.70` | Mean confidence required for HIGH label |
| `EDGE_THRESHOLD` | `0.07` | Edge magnitude for STRONG vs CONFIRMED vs CAUTIOUS signal type |
| `MIN_TRADEABLE_PRICE` | `0.05` | Minimum per-share price to issue a BUY (prevents "$0.01 NO" signals) |
| `MIN_VOLUME_USD` | `10000` | Volume floor for general queries |
| `MIN_VOLUME_SHORT_WINDOW_USD` | `500` | Volume floor for short time-window / live queries |
| `RULES_LAWYER_WEIGHT` | `2` | Rules Lawyer counts as N agents in weighted scoring |
| `LOCAL_LLM_CONCURRENCY` | `1` | Max parallel Ollama calls (no effect on Anthropic) |
| `TAVILY_SEARCHES_PER_MARKET` | `5` | Tavily queries per analysis (3-5) |
| `TAVILY_SNIPPET_CAP` | `5000` | Max chars of search snippets sent to LLM |
| `EVENT_ENRICHMENT_MAX` | `30` | Max parallel /events/{id} detail calls for enrichment |
| `CACHE_PRICE_DELTA` | `0.02` | Price movement threshold to invalidate cached results |

---

## Caching

Council results are cached in SQLite (`~/.pqi/cache.db`). A cached result is reused
if the market's YES price has moved less than `CACHE_PRICE_DELTA` (default 2%) since
the last run. Use `--no-cache` to force a fresh council run.

Cache path is configurable via `CACHE_DB_PATH`.

---

## Project structure

```
pqi/
├── main.py              # CLI (typer + rich) — analyze, categories, history commands
├── config.py            # env var loading and defaults
├── models.py            # Pydantic contracts: MarketData, EventGroup, BriefingDocument,
│                        #   AgentVerdict, CouncilResult
├── scoring.py           # two-sided confidence-weighted scoring + recommendation logic
├── cache.py             # SQLite result cache (keyed by market_id + price delta)
├── history.py           # JSONL history logger (~/.pqi/history.jsonl)
├── agents/
│   ├── personas.py      # all 7 system prompts (AGENT_PERSONAS dict)
│   └── council.py       # parallel agent runner + JSON parser + event timing
├── llm/
│   └── client.py        # Ollama / Anthropic abstraction (single chat() entrypoint)
├── market/
│   └── polymarket.py    # Gamma API client — event-centric fetch, category resolution,
│                        #   parallel enrichment, client-side timing filters
└── ingestion/
    └── synthesizer.py   # LLM Search Architect → 5 Tavily queries → BriefingDocument
```

---

## The Council of Seven

| Agent | Bias |
|-------|------|
| **The Rules Lawyer** (2x weight) | Resolution condition analysis — focuses on whether the specific outcome condition will be met |
| **The Bull (Favorite's Advocate)** | Builds the strongest case for the market-favored side (price-aware, dynamic) |
| **The Bear (Underdog's Advocate)** | Builds the strongest case for the underdog side (price-aware, dynamic) |
| **The Base-Rate Statistician** | Historical base rate, ignores current hype |
| **The Sentiment Analyst** | Crowd psychology vs. current market price |
| **The Domain Specialist** | Field-specific technical nuance (e.g. game meta, matchup history) |
| **The Contrarian** | Competitively plausible disruptions (roster changes, form, morale) — not infrastructure failures |

Each agent outputs a probability (P(YES), 0-100) with **two separate confidence scores**:
`confidence_yes` (how sure about the YES estimate) and `confidence_no` (how sure about
the NO estimate). These can differ when data quality is asymmetric — e.g. strong data
on the favorite but insufficient data on the underdog.

Agents deliberate **in parallel**, not sequentially. Each sees the briefing document
with an EVENT TIMING block indicating urgency (LIVE/IMMINENT, TODAY, THIS WEEK, FUTURE).

The Rules Lawyer receives an additional injection of the market's resolution criteria
at the top of its briefing, marked as the source of truth.

### Scoring formula

```
YES-side score = sum(prob_yes * conf_yes * weight) / sum(conf_yes * weight)
NO-side score  = sum(prob_no  * conf_no  * weight) / sum(conf_no  * weight)
```

The recommendation is probability-first:
1. Identify which side the council favors (>50% probability)
2. If council agrees with market: BUY if council prob >= 60% and confidence HIGH
3. If council disagrees with market (contrarian): BUY if council prob >= 70% and confidence >= 80%
4. Otherwise: PASS with explanation

---

## Backends

| Backend | When to use | Cost |
|---------|-------------|------|
| `ollama` (default) | Local dev, RTX 2070+ | Free |
| `anthropic` | Production / accuracy | ~$0.015/council run with claude-haiku-4-5 |

Switch by setting `LLM_BACKEND=anthropic` in `.env`. Anthropic runs all 7 agents
fully in parallel (no concurrency throttle needed). Ollama is throttled by
`LOCAL_LLM_CONCURRENCY` (default 1) to prevent GPU OOM on consumer cards.
