# CLAUDE.md — PQI-MVP (Polymarket Qualitative Intelligence)

## Project Mission

Identify mispriced odds in Polymarket event-driven prediction markets using a
7-agent LLM council ("The Council of Seven"). The system is an **Advisor, not
an Auto-trader**. Output = top 3 ranked opportunities with confidence scores.

---

## Architecture in One Sentence

Polymarket Gamma API → Synthesizer Agent (Briefing Doc) → Council of Seven
(parallel LLM calls) → Confidence-Weighted Score → CLI recommendation printout.

---

## Tech Stack

| Layer        | Choice                 | Why                                     |
| ------------ | ---------------------- | --------------------------------------- |
| Language     | Python 3.11+           | LLM ecosystem, dev speed                |
| Package mgr  | `uv` (or pip)          | Fast, lockfile-based                    |
| CLI          | `typer` + `rich`       | Beautiful terminal output               |
| LLM client   | Abstracted (see below) | Swap Ollama ↔ Anthropic via env var    |
| Dev LLM      | Ollama `llama3.1:8b`   | Free, local, RTX 2070 Super             |
| Prod LLM     | `claude-haiku-3-5`     | ~$0.015/council run, stays under $20/mo |
| Market data  | Polymarket Gamma API   | Free, public, no auth needed            |
| News/search  | Tavily API             | Free tier 1k searches/mo                |
| Validation   | Pydantic v2            | All inter-module data                   |
| DB (Phase 2) | SQLite via `aiosqlite` | Local price history cache               |

---

## LLM Client Contract

**Always use `pqi/llm/client.py` — never call Anthropic or Ollama directly.**

```python
# pqi/llm/client.py exposes one function:
async def chat(system: str, user: str, model_override: str | None = None) -> str:
    ...
```

The active backend is set by `LLM_BACKEND` env var:

- `LLM_BACKEND=ollama` → hits `http://localhost:11434`
- `LLM_BACKEND=anthropic` → hits Anthropic API with `ANTHROPIC_API_KEY`

Default model per backend set in `pqi/config.py`.

---

## Data Flow (step by step)

[main.py] User runs: python -m pqi.main analyze
[polymarket.py] Fetch top N active markets from Gamma API
→ returns List[MarketData]
[main.py] Display market list → user picks one (or top by volume)
[synthesizer.py] Given MarketData, run Tavily searches on the event
→ produces BriefingDocument (structured Pydantic model)
[council.py] Send BriefingDocument to ALL 7 agents in parallel (asyncio)
→ each returns AgentVerdict(probability, confidence, rationale)
[scoring.py] Apply confidence-weighted formula:
score = Σ(prob × conf) / Σ(conf)
→ returns CouncilResult with final_score + ranked verdicts
[main.py] Rich table printout of recommendation

---

## Pydantic Models (`pqi/models.py`)

These are the **contracts between modules**. Do not skip validation.

```python
class MarketData(BaseModel):
    id: str
    question: str
    resolution_criteria: str
    end_date: datetime
    yes_price: float          # 0.0–1.0 (current market implied prob)
    volume_usd: float
    category: str

class BriefingDocument(BaseModel):
    market: MarketData
    key_facts: list[str]      # bullet facts from Tavily search
    news_summary: str         # synthesized narrative
    red_flags: list[str]
    green_flags: list[str]
    data_freshness: datetime

class AgentVerdict(BaseModel):
    agent_name: str           # e.g. "Rules Lawyer"
    probability: float        # 0.0–1.0
    confidence: float         # 0.0–1.0
    rationale: str            # 2–4 sentence explanation
    key_factor: str           # single most important factor

class CouncilResult(BaseModel):
    market: MarketData
    verdicts: list[AgentVerdict]
    final_score: float        # confidence-weighted
    edge: float               # final_score - yes_price (positive = underpriced YES)
    recommendation: str       # "BUY YES" | "BUY NO" | "PASS"
    confidence_level: str     # "HIGH" | "MEDIUM" | "LOW"
```

---

## The Council of Seven (`pqi/agents/personas.py`)

All agent prompts live in `AGENT_PERSONAS` dict. Always access via this dict.
Never hardcode a system prompt outside `personas.py`.

Each prompt is assembled as: `SHARED_PREAMBLE + persona_body` via `build_prompt()`.

The shared preamble enforces across ALL agents:

- Grounding: analysis based only on the Briefing Document
- Uncertainty floor: confidence ≤ 25 if data is insufficient
- Output format: raw JSON only, no markdown fences
- Scale: all scores are integers 0–100

| Key            | Agent                      | Core Bias                                                                       |
| -------------- | -------------------------- | ------------------------------------------------------------------------------- |
| `rules_lawyer` | The Rules Lawyer           | Resolution criteria only — ignores real-world outcome if oracle condition unmet |
| `bull`         | The Bull                   | Strongest possible YES case from briefing data                                  |
| `bear`         | The Bear                   | Strongest possible NO case, Murphy's Law applied                                |
| `statistician` | The Base-Rate Statistician | Historical base rate, ignores current hype                                      |
| `sentiment`    | The Sentiment Analyst      | Crowd psychology vs current price                                               |
| `specialist`   | The Domain Specialist      | Field-specific technical nuance                                                 |
| `contrarian`   | The Contrarian             | Black swan — what flips the board                                               |

**Scale rule (critical):** Agent prompts use integer 0–100.
Parser in `council.py` divides by 100 before storing in `AgentVerdict` (0.0–1.0).
Scoring formula in `scoring.py` operates on 0.0–1.0 floats.

**Rules Lawyer injection rule:** In `council.py`, when building the Rules Lawyer's
user message, inject the `resolution_criteria` field first in a clearly marked block:
RESOLUTION CRITERIA (SOURCE OF TRUTH — THIS OVERRIDES ALL OTHER CONTEXT):
{market.resolution_criteria}
--- FULL BRIEFING DOCUMENT BELOW ---
{briefing_doc}

All other agents receive only the standard briefing document.

---

## Scoring Formula (`pqi/scoring.py`)

```python
def confidence_weighted_score(verdicts: list[AgentVerdict]) -> float:
    numerator = sum(v.probability * v.confidence for v in verdicts)
    denominator = sum(v.confidence for v in verdicts)
    return numerator / denominator if denominator > 0 else 0.5

def compute_edge(final_score: float, market_yes_price: float) -> float:
    return final_score - market_yes_price  # + = underpriced YES, - = overpriced YES
```

Recommendation logic:

- `edge > +0.07` and `confidence_level == HIGH` → `BUY YES`
- `edge < -0.07` and `confidence_level == HIGH` → `BUY NO`
- Otherwise → `PASS`

---

## Polymarket Gamma API (`pqi/market/polymarket.py`)

Base URL: `https://gamma-api.polymarket.com`

Key endpoints:

- `GET /markets?active=true&limit=50` — fetch open markets
- `GET /markets/{id}` — single market detail (includes `resolution_criteria`)
- `GET /markets?category=politics` — filter by category

No auth required. Use `httpx.AsyncClient`. Respect rate limits — add 0.5s delay between calls.

---

## Tavily Synthesizer (`pqi/ingestion/synthesizer.py`)

- Use `TAVILY_API_KEY` from env
- Run 2–3 targeted searches per market (event name + "latest news", event + "prediction", event + "analysis")
- Condense results into `BriefingDocument`
- Cap at 3 searches per market to stay in free tier

---

## Environment Variables (`.env`)

LLM_BACKEND=ollama # or "anthropic"
OLLAMA_MODEL=llama3.1:8b
OLLAMA_BASE_URL=http://localhost:11434
ANTHROPIC_API_KEY=sk-ant-... # leave blank during dev
ANTHROPIC_MODEL=claude-haiku-3-5
TAVILY_API_KEY=tvly-...
LOG_LEVEL=INFO
MAX_MARKETS_TO_FETCH=20
MIN_VOLUME_USD=10000 # ignore illiquid markets
EDGE_THRESHOLD=0.07 # minimum edge to recommend
MIN_TRADEABLE_PRICE=0.05 # min per-share price of recommended side (PASS if below)
SHORT_WINDOW_HOURS=4 # resolve_within_hours <= this triggers relaxed volume floor
MIN_VOLUME_SHORT_WINDOW_USD=500 # volume floor for short-window queries

---

## Phase Roadmap

### Phase 1 (MVP — COMPLETE ✓)

- [x] `polymarket.py` — fetch and filter markets
- [x] `synthesizer.py` — Tavily search → BriefingDocument
- [x] `personas.py` — all 7 system prompts
- [x] `client.py` — Ollama/Anthropic abstraction
- [x] `council.py` — parallel async agent calls + verdict parsing + OOM semaphore
- [x] `scoring.py` — weighted score + edge + recommendation
- [x] `main.py` — CLI: `analyze` command, rich table output

### Phase 2 (Post-MVP)

- [ ] SQLite cache — skip re-running council if price moved < 2%
- [x] `--category` filter flag (series slug → series_id lookup; `pqi categories` command)
- [ ] Rules Lawyer veto weight (2x multiplier config)
- [ ] Historical accuracy tracking per agent

---

## Coding Conventions

- All inter-module data transfer via Pydantic models (no raw dicts)
- All LLM calls go through `pqi/llm/client.py` — never import anthropic/ollama elsewhere
- All async — use `asyncio.gather()` for parallel council calls
- Errors: log and continue (one agent failure should not crash the council)
- No hardcoded strings — prompts in `personas.py`, endpoints in `config.py`
- `rich.console.print()` for all user-facing output, `logging` for debug

### Configuration rule (strict)

Every value a user might tune without touching code must follow this pattern:

| Where | What goes there |
|---|---|
| `.env.example` | Every tunable key, its default, and a plain-English comment explaining what it does |
| `.env` | User's actual values (not committed) |
| `config.py` | `os.getenv("KEY", "default")` — the Python default matches `.env.example`'s shown value |

**True code constants** (API base URLs, valid enum sets) stay as Python literals in
`config.py` and do NOT appear in `.env.example`.

**The test:** if you'd want to change it to adjust behaviour without redeploying code,
it belongs in `.env`. If it would only make sense to change in a code edit, it stays
as a literal.

**When adding any new configurable value:**
1. Add `VAR: type = os.getenv("VAR", "default")` to `config.py`
2. Add the key to `.env.example` with a `# comment` explaining what it does

---

## What NOT To Do

- Do not add a web server, REST API, or database in Phase 1
- Do not use streaming LLM responses (parse full output only)
- Do not call Polymarket CLOB API (that's the trading API — out of scope)
- Do not add agent-to-agent communication — they deliberate in parallel, not sequentially
- Do not use JSON mode for agent output — use regex parsing for Ollama compatibility

---

## Session Protocol

### Rules for Claude Code (read every session)

- At the START of every session, read the `## Current State` section below
  before doing anything else. It is the source of truth for what exists.
- At the END of every session (or when asked), you MUST update `## Current State`
  and write a fresh `## Next Session Prompt` block.
- Never leave a session without doing this. It is as important as the code.

---

## Current State

**Last updated:** [session 10 — --category flag fixed via series_id lookup; pqi categories command added]

### Implemented & tested ✓

- `agents/personas.py` — all 7 system prompts, AGENT_PERSONAS dict, build_prompt();
  SHARED_PREAMBLE includes TWO-SIDED MARKET and TIME-TO-REVERSAL rules
- `config.py` — reads ALL tunables via `os.getenv(KEY, default)`. True code constants
  only: `GAMMA_BASE_URL`, `VALID_BACKENDS`. `.env.example` is canonical reference.
- `models.py` — all four Pydantic v2 models; `CouncilResult.from_council_verdicts()`
  6-gate pipeline: weighted score → edge → confidence label → median consensus gate
  → min_tradeable_price gate → recommendation.
- `llm/client.py` — Ollama + Anthropic backends, `async def chat()` public interface
- `ingestion/synthesizer.py` — `build_briefing()`: 3 Tavily searches, BriefingDocument
- `agents/council.py` — `run_council()`: 7 agents via `asyncio.gather()` + semaphore,
  3-pass JSON parser, Rules Lawyer resolution_criteria injection
- `scoring.py` — `build_council_result` wires all 6 config thresholds
- `main.py` — Typer CLI: `analyze` + `categories` commands; `display_market_list`
  shows YES/NO columns; `display_council_result` shows per-share price + edge label;
  `--category` (series slug), `--top-n`, `--top`, `--resolve-within` flags (all combinable).
  `categories` command lists available slugs sorted by 24h volume.
  Invoked as `python3 -m pqi.main`
- `market/polymarket.py` — complete category-filtering rewrite (session 10):
  - `resolve_series_id(query)`: exact slug match via `/series?slug=<q>`, then partial
    substring match over `/series?limit=200`. Returns str(series.id) or None.
  - `fetch_categories(limit)`: fetches `/series?active=true` batch, sorts by
    `volume24hr` client-side, returns list[dict] for the `categories` CLI command.
  - `fetch_markets()` resolves `category` → `series_id` once via `resolve_series_id`,
    then passes `series_id` (not the raw slug) to both sub-functions.
  - `_fetch_markets_general(series_id)`: when series_id is set uses
    `/events?series_id=<id>&order=volume` (unpacks markets from events); otherwise
    `/markets?order=volume` (faster path).
  - `_fetch_markets_by_time_window(series_id)`: adds `series_id=<id>` param to
    `/events` request when set. Replaces broken `tag_id=<str>` param.
  NOTE: Gamma API `order=end_date` and `order=volume_24hr` return 422 (docs wrong).
  Confirmed working: `order=volume`, `order=liquidity`.

### Implemented but untested ⚠️

- `--category` fix and `categories` command (just implemented, not live-tested yet)

### Not started ✗

- Phase 2 features (see Phase Roadmap — item 4 `--category` now complete)

### Known issues / assumptions

- **Gamma API order params (confirmed 2026-04):** `order=end_date` and
  `order=volume_24hr` return 422 Unprocessable Entity despite docs claiming they work.
  Only `order=volume` and `order=liquidity` are confirmed working. Do not attempt
  other order values.
- **Events vs markets endpoint:** `/events` is used for time-window queries because
  it groups all related markets under one parent (sports match winner + spread + total;
  all BTC 15-min windows in the time range). `/markets` is used for general queries.
  Embedded markets in events do NOT have a nested `events` key — category comes from
  the parent event's `tags` array via `_category_from_event()`.
- **Min-tradeable-price gate (session 9):** BUY signals now require a 4th gate:
  the recommended side must cost ≥ MIN_TRADEABLE_PRICE (default 5¢). This prevents
  "BUY NO at $0.01" on near-over live events where reversal is practically impossible.
  Override in .env with MIN_TRADEABLE_PRICE=0.03 for longer-horizon markets if needed.
- **Consensus gate (session 8):** BUY signals require: edge threshold, HIGH confidence,
  AND median probability (>0.40 for BUY YES, <0.60 for BUY NO). Blocks outlier-driven
  false signals (e.g. one agent giving 100% tautologically).
- **Rules Lawyer tautology bug (known, low priority):** When resolution criteria are simple
  (e.g. 'the game winner'), the RL sometimes interprets 'will the criteria be satisfiable?'
  rather than 'will the specific outcome occur?', outputting 100% probability. The median
  gate catches this. A prompt fix for the RL is Phase 2 work.
- Single-command Typer app: invoke as `python3 -m pqi.main [OPTIONS]`
  (not `python3 -m pqi.main analyze [OPTIONS]`).
- `.env` uses `CLAUDE_API_KEY` instead of `ANTHROPIC_API_KEY`. `config.py` accepts
  either (`ANTHROPIC_API_KEY` checked first, `CLAUDE_API_KEY` as fallback).
- `TAVILY_API_KEY` is validated as required at import time (raises `EnvironmentError`
  if absent, even in ollama mode). Adjust `_validate_env()` if you want lazy validation.
- `OLLAMA_CONCURRENCY=2` (default) prevents OOM on RTX 2070 Super (8GB VRAM).
  With Anthropic backend, set `OLLAMA_CONCURRENCY=7` in .env to restore full parallelism.
  The semaphore lives in `council.py:_llm_semaphore` — module-level, created once.
- Gamma API field notes (confirmed 2026-04):
  - `outcomePrices` is a JSON string: `'["0.73", "0.27"]'` — index 0 = YES price
  - `volumeNum` is a float in USD; `volume` is a string fallback
  - `category` is typically None at market level; falls back to `events[0]["category"]`
    or "general"
  - Must pass `closed=false` in addition to `active=true` to exclude resolved markets
- Council JSON parser notes (critical for Ollama/llama3.1:8b):
  - Pass 1: direct json.loads()
  - Pass 2: strip bare control chars (unescaped \n inside string values)
  - Pass 3: field-by-field regex for models that produce unquoted bullet values
  - All 3 passes needed for reliable 7/7 verdicts with llama3.1:8b

### Environment

- LLM_BACKEND: `ollama` (tested and resolves correctly)
- Ollama: installed, llama3.1:8b pulled and verified
- Anthropic API key: set as `CLAUDE_API_KEY` in .env, resolved correctly by config.py
- Tavily API key: set and resolving correctly
- OLLAMA_CONCURRENCY: 2 (default, set in config.py; override in .env)

---

## Next Session Prompt

```
Read CLAUDE.md in full, paying close attention to ## Current State.

Session 10 completed the --category flag and added the `pqi categories` discovery command.
The --category fix is implemented but not yet live-tested — do a quick smoke test first:
  python3 -m pqi.main categories
  python3 -m pqi.main --category counter-strike -n 5

Then pick 1–2 Phase 2 features from below.

## Phase 2 priorities

### 1. SQLite cache (`pqi/cache.py`)
Skip re-running the full council if yes_price moved < 2% since last run.
- Use `aiosqlite` with a `council_results` table (market_id, yes_price_at_run,
  result_json, created_at).
- On `analyze`, query cache first. If hit and price delta < 0.02, load from cache.
- Otherwise run council and write result back to cache.
- Add `--no-cache` flag to `main.py` to force a fresh run.

### 2. Rules Lawyer veto weight (2× multiplier)
In `scoring.py`, give the Rules Lawyer's confidence double weight in the
confidence-weighted formula. Add `RULES_LAWYER_WEIGHT=2` to config.py.
Update `confidence_weighted_score` to accept per-agent weights.

### 3. Historical accuracy tracking
After each run, append to a JSONL file (`~/.pqi/history.jsonl`):
  {market_id, question, yes_price_at_run, final_score, recommendation, timestamp}
Add a `pqi history` CLI command that prints a table of past runs and
(where markets are resolved) shows whether the recommendation was correct.

## Instructions
- Decide which items to tackle based on complexity vs. value
- Read the relevant existing files before writing any new code
- Update ## Current State and ## Next Session Prompt at end of session
```
