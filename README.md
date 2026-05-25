# PQI — Polymarket Qualitative Intelligence

> Finds mispriced odds in Polymarket event-driven prediction markets with a
> 7-agent LLM council — **"The Council of Seven."**

**Advisor only.** PQI ranks opportunities and explains its reasoning; a human
decides whether to trade. No auto-execution.

```
Polymarket Gamma API ─▶ Synthesizer (Tavily briefing) ─▶ CLOB price history
        ─▶ Council of Seven (7 parallel LLM calls) ─▶ confidence-weighted score
        ─▶ recommendation:  BUY YES · BUY NO · PASS
```

The council computes two independent scores — a YES-side and a NO-side
confidence-weighted average — using each agent's *per-side* confidence. It
recommends a side only when its probability clears the threshold and confidence
is HIGH; the price's max ROI sets the signal label (HIGH VALUE / MODERATE /
LOW MARGIN) but never gates the call.

---

## Quick start

Requires **Python ≥ 3.11** and [**uv**](https://docs.astral.sh/uv/). Everything
lives inside the repo — deps in `.venv/`, state in `.pqi/`, keys in `.env`.

```bash
git clone <repo-url> pqi && cd pqi
uv sync                       # installs dependencies + pqi (editable) into .venv/
cp .env.example .env          # then add your keys (see below)
uv run pqi analyze            # interactive: top events by volume, pick one
```

Minimum keys in `.env`:

```dotenv
TAVILY_API_KEY=tvly-...           # required — news/search for the briefing

LLM_BACKEND=anthropic             # 'anthropic' (prod) or 'ollama' (local dev)
ANTHROPIC_API_KEY=sk-ant-...      # required when LLM_BACKEND=anthropic

# PANDASCORE_API_KEY=...          # optional — structured esports data (free tier)
```

> **Tip:** activate the venv (`source .venv/bin/activate`) and you can drop the
> `uv run` prefix — just `pqi analyze`, `pqi history stats`, etc.
>
> **Local/free dev:** set `LLM_BACKEND=ollama`, then `ollama serve` and
> `ollama pull llama3.1:8b`. Keep `LOCAL_LLM_CONCURRENCY=1` on consumer GPUs.

---

## Commands

| Command            | What it does                                                       |
| ------------------ | ------------------------------------------------------------------ |
| `pqi analyze`      | Fetch events, run the council, print ranked recommendations (default) |
| `pqi categories`   | List tags + series slugs you can pass to `--category`              |
| `pqi history`      | Inspect past runs: `list` · `show` · `sync` · `stats` · `equity`   |
| `pqi query "<SQL>"`| Ad-hoc DuckDB SQL over the persisted run history                   |

Prefix any of these with `uv run` if the venv isn't activated.

```bash
uv run pqi                          # top 10 events by volume, interactive
uv run pqi -c esports -s 4          # esports starting or live within 4h
uv run pqi -c esports --live        # live esports only
uv run pqi -c counter-strike -r 2 -v 0   # CS markets resolving within 2h, any volume
uv run pqi --top                    # non-interactive: highest-volume event
uv run pqi --top --no-cache         # force a fresh council run
uv run pqi history list -n 10       # last 10 runs with outcomes
uv run pqi history stats            # per-agent leaderboard + win-rate snapshot
uv run pqi history equity --mode kelly   # bankroll equity curve + drawdown
```

At the event-selection prompt you can also enter a **multi-select** spec —
`1,3,5`, `3-7`, `all`, or `new` (only un-analyzed events) — to batch-run several
events with bounded parallelism. Full flag reference, batch mode, history
sub-commands, and analysis recipes live in **[docs/cli-reference.md](docs/cli-reference.md)**.

---

## What you get

**Event selection** — events (not raw markets) are the rows; siblings (Moneyline,
Map 1, Map 2…) group under one event:

```
Active Polymarket Events
  #  Event                          Starts  Mkts  YES   NO   Sig  Seen  Volume
  1  Team Liquid vs Sentinels LIVE    2h      3   65%  35%   ⭐    ●    $67,000
  2  Cloud9 vs NRG                    5h      3   52%  48%              $43,000
  3  Will BTC hit $100k by July?      —       1   38%  62%   ⭐         $890,000
```

`Sig`: ⭐ favored side priced at/below our historical hit-rate (real edge) ·
🔻 ≥ 99% (effectively resolved) · blank = coin-flip or above the edge ceiling.
`Seen`: ● if any market in the event was analyzed before.

**Recommendation** — two-sided scores, edge vs. historical win-rate, max ROI,
and an advisory Kelly stake:

```
╭──────────────────── Recommendation ────────────────────╮
│ Council YES:   75.2%  |  Council NO:  24.3%             │
│ Market Prices: YES = 66.0%  |  NO = 34.0%               │
│ 7d Trend YES:  +5.2pp RISING  |  24h: +1.8pp            │
│ YES Edge vs WR:+8.4%  |  NO Edge vs WR: -25.6%  (WR 74.4%) │
│ Max ROI (YES): +51.5%   |   Kelly: full 12.1% · @0.5x 6.0% │
│ Confidence:    HIGH                                     │
│ >>> BUY YES @ $0.66/share — HIGH VALUE <<<              │
╰────────────────────────────────────────────────────────╯
```

| Recommendation        | Meaning                                                            |
| --------------------- | ------------------------------------------------------------------ |
| **BUY — HIGH VALUE**  | Cheap side, max ROI ≥ 50% (price ≤ ~0.667)                         |
| **BUY — MODERATE**    | Mid-price, max ROI 25–50%                                           |
| **BUY — LOW MARGIN**  | Pricey side, max ROI 10–25% (near the ceiling)                     |
| **BUY — CONTRARIAN**  | Council disagrees with the market — stricter prob/conf/ROI gates   |
| **PASS**              | No actionable signal; the reason is printed                        |

---

## The Council of Seven

Seven personas deliberate **in parallel** (never sequentially) over the same
briefing. Each returns `P(YES)` plus *two* confidence scores — `confidence_yes`
and `confidence_no` — which can differ when evidence is asymmetric (strong data
on the favorite, thin data on the underdog).

| Agent                              | Bias                                                              |
| ---------------------------------- | ----------------------------------------------------------------- |
| **The Rules Lawyer** (2× weight)   | Resolution criteria only — will the exact oracle condition be met |
| **The Bull**                       | Strongest case for the market-favored side                        |
| **The Bear**                       | Strongest case for the underdog side                              |
| **The Base-Rate Statistician**     | Historical base rate, ignores hype                                |
| **The Sentiment Analyst**          | Crowd psychology vs. current price                                |
| **The Domain Specialist**          | Field-specific technical nuance                                   |
| **The Contrarian**                 | Plausible disruptions that flip the board                         |

The Rules Lawyer additionally receives the market's resolution criteria injected
as the source of truth. Scoring is two-sided and confidence-weighted; see
**[docs/scoring-reference.md](docs/scoring-reference.md)** for the full gate
stack, EV gate, and Kelly math.

---

## Configuration

Every tunable is an environment variable read from the repo-root `.env`.
**[`.env.example`](.env.example) documents every key, its default, and what it
does** — copy it to `.env` and edit. The most commonly adjusted:

| Variable                     | Default | Effect                                              |
| ---------------------------- | ------- | --------------------------------------------------- |
| `LLM_BACKEND`                | `ollama`| `anthropic` (prod) or `ollama` (local dev)          |
| `MIN_COUNCIL_PROBABILITY`    | `0.60`  | Min council probability for a market-aligned BUY    |
| `MIN_CONTRARIAN_PROBABILITY` | `0.60`  | Min probability for a contrarian BUY                |
| `CONFIDENCE_HIGH_THRESHOLD`  | `0.67`  | Mean confidence required for the HIGH label         |
| `MIN_EXPECTED_ROI`           | `0.10`  | Min max-ROI `(1-price)/price` for a market BUY      |
| `MAX_TRADEABLE_PRICE`        | `0.90`  | Near-resolved ceiling; above it, PASS               |
| `EV_GATE_ENABLED`            | `true`  | Final EV gate vs. PerceivedTrueWinRate ceiling      |
| `KELLY_MULTIPLIER`           | `0.5`   | Advisory Kelly scaler (0.5 = half-Kelly)            |
| `PANDASCORE_API_KEY`         | _(none)_| Enables structured esports data (esports only)      |

Persisted state (history, cache, weights, reports) lives in `.pqi/` at the repo
root and is git-tracked, so a clone travels with its data. Override the location
with `PQI_DATA_DIR`. The full tuning table is in
**[docs/cli-reference.md](docs/cli-reference.md)**.

---

## Backends

| Backend            | Use for              | Cost                                          |
| ------------------ | -------------------- | --------------------------------------------- |
| `ollama` (default) | Local dev, free      | $0 — runs `llama3.1:8b` on a local GPU        |
| `anthropic`        | Production / accuracy| ~$0.015 / council run (`claude-haiku-4-5`)    |

Anthropic runs all 7 agents fully in parallel, with **prompt caching** (5-min
ephemeral) and a **centralized exponential-backoff retry** (handles 429 / 5xx /
529 / timeouts). Ollama is throttled by `LOCAL_LLM_CONCURRENCY` to avoid GPU OOM.
Backend internals are documented in
[docs/cli-reference.md](docs/cli-reference.md#backends--reliability).

---

## Project layout

`src/` layout — the `pqi` package is installed **editable** by `uv sync`, so
`uv run pqi …` works from anywhere with no `PYTHONPATH` juggling.

```
pqi/
├── src/pqi/            # the package (import name `pqi`; installed by `uv sync`)
│   ├── main.py         #   Typer CLI: analyze · categories · history · query
│   ├── config.py       #   env-var config (loads the repo-root .env)
│   ├── models.py       #   Pydantic contracts shared between modules
│   ├── scoring.py      #   two-sided confidence-weighted scoring + gates
│   ├── stats.py        #   PerceivedTrueWinRate + Kelly helpers
│   ├── history.py      #   JSONL run log + resolution sync
│   ├── analytics.py    #   outcome classification, per-agent accuracy
│   ├── agents/         #   personas.py (7 prompts) + council.py (parallel runner)
│   ├── llm/            #   client.py — single Ollama/Anthropic chat() entrypoint
│   ├── market/         #   polymarket.py (Gamma) + clob.py (price history)
│   └── ingestion/      #   synthesizer.py · pandascore.py · match_resolver.py
├── scripts/            # backfills + reliability diagram (run via `uv run python …`)
├── docs/               # architecture, scoring, API, roadmap, CLI reference
├── .pqi/               # persisted state (history, cache, weights) — git-tracked
├── pyproject.toml      # deps + hatchling build + the `pqi` console script
├── uv.lock             # pinned, reproducible dependency set
└── .env                # your keys (gitignored — copy from .env.example)
```

---

## Documentation

| Doc | Read it for |
| --- | --- |
| **[docs/cli-reference.md](docs/cli-reference.md)** | Every flag, batch mode, history recipes, equity math, full tuning table, backend internals |
| [docs/scoring-reference.md](docs/scoring-reference.md) | Gate stack, banded EV gate, Kelly sizing |
| [docs/models-reference.md](docs/models-reference.md) | Pydantic contracts between modules |
| [docs/api-reference.md](docs/api-reference.md) | Gamma · CLOB · Tavily · PandaScore APIs |
| [docs/architecture.md](docs/architecture.md) | System diagram (Mermaid) |
| [docs/analyze-pipeline.md](docs/analyze-pipeline.md) | Detailed pipeline flow |
| [docs/known-issues.md](docs/known-issues.md) | API quirks + gotchas |
| [docs/roadmap.md](docs/roadmap.md) | Where this is going |

---

## Roadmap

`docs/roadmap.md` is the source of truth. Work is organized into three themes:

- **A · Modeling & Calibration** — data-asymmetry handling for the two-sided
  confidence pair, briefing-block attribution, Bear + EV-gate monitoring.
- **B · Continuous-run TUI + Trading Automation** — streaming TUI, background
  analysis workers with a shared rate-limit coordinator, `py-clob-client` auth,
  Kelly-sized buys, and a PnL/time stop-loss watcher.
- **C · Operational Tooling** — cache TTL, ghost-event search, per-category
  accuracy slicing.

Phase status and acceptance criteria live in
[docs/phases/README.md](docs/phases/README.md).
