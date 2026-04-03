# PQI — Polymarket Qualitative Intelligence

Identifies mispriced odds in Polymarket event-driven prediction markets using a
7-agent LLM council ("The Council of Seven"). **Advisor only — no auto-trading.**

## How it works

```
Polymarket Gamma API
  → Synthesizer (3 Tavily searches → BriefingDocument)
    → Council of Seven (7 parallel LLM calls, confidence-weighted)
      → Edge calculation vs. market price
        → CLI recommendation: BUY YES / BUY NO / PASS
```

The council's final score is a confidence-weighted average of all 7 agents' probability
estimates. An opportunity is flagged only when `|edge| > 7%` **and** mean agent confidence
is HIGH (≥ 70%). Everything else is a PASS.

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
OLLAMA_CONCURRENCY=2       # keep low on consumer GPUs (RTX 2070 etc.) to prevent OOM

# Prod (Anthropic API)
# LLM_BACKEND=anthropic
# ANTHROPIC_API_KEY=sk-ant-...
# OLLAMA_CONCURRENCY=7     # Anthropic handles full parallelism fine
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
python3 -m pqi.main [OPTIONS]
```

### Flags

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--top-n` | `-n` | `INT` | `10` | Number of markets to show in the selection list |
| `--category` | `-c` | `TEXT` | _(all)_ | Filter markets by category before listing |
| `--top` | `-t` | flag | off | Skip the selection prompt — auto-pick the highest-volume market |
| `--resolve-within` | `-r` | `INT` | _(all)_ | Only show markets that resolve within this many hours |

All flags can be freely combined.

### Examples

```bash
# Interactive — show 10 markets sorted by volume, pick one
python3 -m pqi.main

# Interactive — show only 5 markets
python3 -m pqi.main --top-n 5

# Non-interactive — immediately analyse the #1 market by volume
python3 -m pqi.main --top

# Non-interactive — top market in the "politics" category
python3 -m pqi.main --top --category politics

# Short form of the above
python3 -m pqi.main -t -c politics

# Today's events (NCAA, football, esports), pick interactively
python3 -m pqi.main --resolve-within 24

# Today's events, auto-select the highest-volume one
python3 -m pqi.main --resolve-within 24 --top

# Top 5 sports league markets resolving within ~3 months, interactive
python3 -m pqi.main --resolve-within 2000 --top-n 5

# Live/near-live events only — esports/crypto markets resolving in under 2h
python3 -m pqi.main --resolve-within 2 --top-n 10
```

### What the output looks like

```
Active Polymarket Markets
╭───┬─────────────────────────────────────────────┬───────────┬────────────┬────────────────╮
│ # │ Question                                    │  Resolves │  Yes Price │   Volume (USD) │
├───┼─────────────────────────────────────────────┼───────────┼────────────┼────────────────┤
│ 1 │ Will X win the 2026 Stanley Cup?            │      ~3mo │      19.2% │    $13,346,053 │
│ 2 │ GTA VI released before June 2026?           │      ~1mo │       1.1% │    $13,076,439 │
│ 3 │ Will Harvey Weinstein be sentenced to no…   │   expired │      30.4% │       $296,877 │
╰───┴─────────────────────────────────────────────┴───────────┴────────────┴────────────────╯

Select a market [1–3, Enter for #1]:
```

After selection, the council runs and prints:

```
Council of Seven — Verdicts
  Agent                          Prob %   Conf %   Key Factor
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  The Rules Lawyer                  75%      80%   Resolution ambiguity risk
  The Bull                          85%      95%   Recent team dominance
  The Bear                          55%      42%   Inconsistent big-game performance
  The Base-Rate Statistician        42%      75%   ~10% historical base rate per team
  The Sentiment Analyst             75%      90%   Crowd under-pricing momentum
  The Domain Specialist             72%      90%   Playoff seeding advantage
  The Contrarian                    10%      60%   Key player injury risk

╭─── Recommendation ───╮
│ Final Score:  62.7%  │
│ Market Price: 19.2%  │
│ Edge:         +43.5% │
│ Confidence:   HIGH   │
│                      │
│ >>> BUY YES <<<      │  ← green = BUY YES, red = BUY NO, yellow = PASS
╰──────────────────────╯
```

---

## Filtering notes

### Category filter (`--category`)

The Gamma API supports category as a query parameter, but **very few markets have
a category set** — most return as `"general"`. Useful values seen in practice:

| Value | Notes |
|-------|-------|
| `politics` | Political events and elections |
| `crypto` | Crypto price / protocol markets |
| `sports` | Sports outcomes |

If `--category` returns too few (or zero) results, drop the flag and browse all markets.

### Resolve-within filter (`--resolve-within`)

Filters to markets whose real-world event ends within the next N hours —
e.g. a football match in 11h, an NCAA game in 21h, an esports bracket match in 2h.
Filtering is done **server-side** via the Gamma API's `end_date_min`/`end_date_max`
params, so `--top-n 20 --resolve-within 24` gives you the genuine top-20 by volume
within that window, not the top-20 overall filtered down.

Expired markets (event already ended, oracle not yet settled) are excluded — the
filter window is strictly in the future.

The `Resolves` column in the market list shows time-to-resolution at a glance:
`45m`, `11h`, `7d`, `~2mo`. Sub-hour precision is shown in minutes.

```bash
python3 -m pqi.main --resolve-within 2        # next 2 hours (live/near-live events)
python3 -m pqi.main --resolve-within 24       # today's events
python3 -m pqi.main --resolve-within 168      # this week
python3 -m pqi.main --resolve-within 720      # this month
```

If no markets fall within the window, PQI exits with a descriptive error suggesting
a larger value.

---

## Tuning

All thresholds are set in `.env` (or `config.py` defaults):

| Variable | Default | Effect |
|----------|---------|--------|
| `EDGE_THRESHOLD` | `0.07` | Minimum \|edge\| to issue a BUY (raise to be more selective) |
| `CONFIDENCE_HIGH_THRESHOLD` | `0.70` | Mean agent confidence required for HIGH rating |
| `CONFIDENCE_MEDIUM_THRESHOLD` | `0.40` | Floor for MEDIUM (below = LOW, always PASS) |
| `MIN_VOLUME_USD` | `10000` | Markets below this volume are excluded |
| `MAX_MARKETS_TO_FETCH` | `20` | Raw fetch limit sent to the Gamma API |
| `OLLAMA_CONCURRENCY` | `2` | Max parallel LLM calls (keep low on consumer GPUs) |

---

## Project structure

```
pqi/
├── main.py              # CLI (typer + rich)
├── config.py            # env var loading and defaults
├── models.py            # Pydantic contracts between modules
├── scoring.py           # confidence-weighted scoring + edge + recommendation
├── agents/
│   ├── personas.py      # all 7 system prompts (AGENT_PERSONAS dict)
│   └── council.py       # parallel agent runner + 3-pass JSON verdict parser
├── llm/
│   └── client.py        # Ollama / Anthropic abstraction (single chat() entrypoint)
├── market/
│   └── polymarket.py    # Gamma API client — fetch_markets(), fetch_market_detail()
└── ingestion/
    └── synthesizer.py   # Tavily search → BriefingDocument
```

---

## The Council of Seven

| Agent | Bias |
|-------|------|
| **The Rules Lawyer** | Resolution criteria only — ignores real-world outcome if oracle condition unmet |
| **The Bull** | Strongest possible YES case from the briefing |
| **The Bear** | Strongest possible NO case, Murphy's Law applied |
| **The Base-Rate Statistician** | Historical base rate, ignores current hype |
| **The Sentiment Analyst** | Crowd psychology vs. current market price |
| **The Domain Specialist** | Field-specific technical nuance |
| **The Contrarian** | Black swan — what single event flips the board |

Agents deliberate **in parallel**, not sequentially. Each agent sees only the
briefing document (the Rules Lawyer additionally gets the resolution criteria
injected first as the explicit source of truth).

---

## Backends

| Backend | When to use | Cost |
|---------|-------------|------|
| `ollama` (default) | Local dev, RTX 2070+ | Free |
| `anthropic` | Production / accuracy | ~$0.015/council run |

Switch by setting `LLM_BACKEND=anthropic` in `.env`. When switching to Anthropic,
also set `OLLAMA_CONCURRENCY=7` to restore full parallel council execution.
