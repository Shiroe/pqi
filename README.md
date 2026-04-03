# PQI — Polymarket Qualitative Intelligence

Identifies mispriced odds in Polymarket event-driven prediction markets using a
7-agent LLM council ("The Council of Seven"). **Advisor only — no auto-trading.**

## How it works

```
Polymarket Gamma API
  → Synthesizer (5 Tavily searches → BriefingDocument)
    → Council of Seven (7 parallel LLM calls, two-sided confidence-weighted)
      → Edge calculation vs. market price (YES-side and NO-side independently)
        → CLI recommendation: BUY YES / BUY NO / PASS
```

The council computes two independent scores: a YES-side and NO-side weighted average,
each using per-agent directional confidence. An opportunity is flagged when either
side's edge exceeds the threshold **and** that side's mean confidence is HIGH.

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
| `analyze` (default) | Fetch markets, run the council, print recommendations |
| `categories` | List available tags and series slugs for `--category` |

### Flags (analyze command)

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--top-n` | `-n` | `INT` | `10` | Number of markets to show in the selection list |
| `--category` | `-c` | `TEXT` | _(all)_ | Filter by broad tag (e.g. `esports`) or specific series slug (e.g. `counter-strike`) |
| `--top` | `-t` | flag | off | Skip the selection prompt — auto-pick the highest-volume market |
| `--resolve-within` | `-r` | `INT` | _(all)_ | Only show markets resolving within this many hours |
| `--min-volume` | `-v` | `FLOAT` | _(config)_ | Override minimum volume floor in USD (use `-v 0` to see all) |

All flags can be freely combined.

### Examples

```bash
# Interactive — show 10 markets sorted by volume, pick one
python3 -m pqi.main

# Top 10 esports markets resolving within 4 hours
python3 -m pqi.main -c esports -r 4 -n 10

# Counter-Strike only, next 2 hours, show all regardless of volume
python3 -m pqi.main -c counter-strike -r 2 -v 0

# Non-interactive — top market by volume, any category
python3 -m pqi.main --top

# Today's events, auto-select the highest-volume one
python3 -m pqi.main --resolve-within 24 --top

# List available categories
python3 -m pqi.main categories
```

### What the output looks like

```
Council of Seven — Verdicts
  Agent                          Prob %  Conf Y%  Conf N%   Key Factor
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  The Rules Lawyer                  71%      78%      62%   ...
  The Bull                          84%      85%      72%   ...
  The Bear                          71%      78%      42%   ...
  ...

╭─────────────── Recommendation ───────────────╮
│ Council YES:    75.2%  |  Council NO:  24.3% │
│ Market Prices:  YES = 66.0%  |  NO = 34.0%   │
│ YES Edge:       +9.2%  |  NO Edge:  -9.7%    │
│ Confidence:     HIGH                         │
│                                              │
│ >>> BUY YES @ $0.66/share <<<                │
╰──────────────────────────────────────────────╯
```

---

## Category filter (`--category`)

Two-tier resolution:
1. **Specific series** — exact or partial slug match (e.g. `counter-strike`, `valorant`, `atp`). Server-side filter via Gamma API `series_id`.
2. **Broad tag** — matches event tags across all series (e.g. `esports`, `sports`, `crypto`, `politics`). Server-side filter via Gamma API `tag_id`.

Run `pqi categories` to see available tags and series slugs. If the category doesn't match anything, the command fails loudly with a suggestion.

## Resolve-within filter (`--resolve-within`)

Filters to markets resolving within the next N hours. Uses the Gamma API's
`end_date_min`/`end_date_max` params. The volume floor is automatically relaxed
for short windows (< 4h) to show live matches with low volume.

The `Resolves` column shows time-to-resolution: `45m`, `11h`, `7d`, `~2mo`.

## Volume floor (`--min-volume`)

By default, markets below `MIN_VOLUME_USD` ($10K) are excluded. For short time
windows (< 4h), the floor drops to `MIN_VOLUME_SHORT_WINDOW_USD` ($500).
Use `-v 0` to see all markets regardless of volume.

---

## Tuning

All thresholds are set in `.env` (see `.env.example` for full documentation):

| Variable | Default | Effect |
|----------|---------|--------|
| `EDGE_THRESHOLD` | `0.07` | Minimum edge to issue a BUY |
| `CONFIDENCE_HIGH_THRESHOLD` | `0.70` | Mean confidence required for HIGH |
| `MIN_VOLUME_USD` | `10000` | Volume floor for general queries |
| `MIN_VOLUME_SHORT_WINDOW_USD` | `500` | Volume floor for short time-window queries |
| `LOCAL_LLM_CONCURRENCY` | `1` | Max parallel Ollama calls (no effect on Anthropic) |
| `TAVILY_SEARCHES_PER_MARKET` | `5` | Tavily queries per analysis (3-5) |
| `MIN_TRADEABLE_PRICE` | `0.05` | Minimum per-share price to issue a BUY |

---

## Project structure

```
pqi/
├── main.py              # CLI (typer + rich) — analyze + categories commands
├── config.py            # env var loading and defaults
├── models.py            # Pydantic contracts (MarketData, BriefingDocument, AgentVerdict, CouncilResult)
├── scoring.py           # two-sided confidence-weighted scoring + edge + recommendation
├── agents/
│   ├── personas.py      # all 7 system prompts (AGENT_PERSONAS dict)
│   └── council.py       # parallel agent runner + JSON parser + EVENT TIMING
├── llm/
│   └── client.py        # Ollama / Anthropic abstraction (single chat() entrypoint)
├── market/
│   └── polymarket.py    # Gamma API client — two-tier category resolution, tag filtering
└── ingestion/
    └── synthesizer.py   # Tavily search (5 queries) → BriefingDocument
```

---

## The Council of Seven

| Agent | Bias |
|-------|------|
| **The Rules Lawyer** | Resolution condition analysis — focuses on whether the outcome will occur |
| **The Bull** | Strongest possible YES case from the briefing |
| **The Bear** | Strongest possible NO case, Murphy's Law applied |
| **The Base-Rate Statistician** | Historical base rate, ignores current hype |
| **The Sentiment Analyst** | Crowd psychology vs. current market price |
| **The Domain Specialist** | Field-specific technical nuance |
| **The Contrarian** | Black swan — what single event flips the board |

Each agent outputs a probability (P(YES), 0-100) with **two separate confidence scores**:
`confidence_yes` (how sure about the YES estimate) and `confidence_no` (how sure about
the NO estimate). These can differ when data quality is asymmetric — e.g. strong data
on the favorite but insufficient data on the underdog.

Agents deliberate **in parallel**, not sequentially. Each sees the briefing document
with an EVENT TIMING block indicating urgency (LIVE/IMMINENT, TODAY, THIS WEEK, FUTURE).

---

## Backends

| Backend | When to use | Cost |
|---------|-------------|------|
| `ollama` (default) | Local dev, RTX 2070+ | Free |
| `anthropic` | Production / accuracy | ~$0.015/council run |

Switch by setting `LLM_BACKEND=anthropic` in `.env`. Anthropic runs all 7 agents
fully in parallel (no concurrency throttle needed).
