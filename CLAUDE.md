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
    probability: float        # 0.0–1.0 (P(YES))
    confidence_yes: float     # 0.0–1.0 (confidence in YES estimate)
    confidence_no: float      # 0.0–1.0 (confidence in NO estimate)
    rationale: str            # 2–4 sentence explanation
    key_factor: str           # single most important factor

class CouncilResult(BaseModel):
    market: MarketData
    verdicts: list[AgentVerdict]
    final_score: float        # YES-side confidence-weighted score
    no_score: float           # NO-side confidence-weighted score
    edge: float               # final_score - yes_price (positive = underpriced YES)
    no_edge: float            # no_score - no_price (positive = underpriced NO)
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

**Two-sided confidence (critical):** Agents output `confidence_yes` and
`confidence_no` as separate 0–100 integers. These CAN differ — an agent may
have high confidence in the YES estimate but low confidence in the NO estimate
(e.g. insufficient data on the underdog). Parser has backward fallback: if only
legacy `confidence_level` is found, both sides get the same value.

**Rules Lawyer injection rule:** In `council.py`, when building the Rules Lawyer's
user message, inject the `resolution_criteria` field first in a clearly marked block:
RESOLUTION CRITERIA (SOURCE OF TRUTH — THIS OVERRIDES ALL OTHER CONTEXT):
{market.resolution_criteria}
--- FULL BRIEFING DOCUMENT BELOW ---
{briefing_doc}

All other agents receive only the standard briefing document.

---

## Scoring Formula (`pqi/scoring.py`)

Two-sided scoring: YES and NO sides are computed independently using their
respective confidence weights. An agent can be highly confident about one
side but uncertain about the other.

```python
# YES-side
yes_score = Σ(prob_yes × conf_yes) / Σ(conf_yes)
yes_edge  = yes_score − market.yes_price

# NO-side
no_score  = Σ(prob_no × conf_no) / Σ(conf_no)     # prob_no = 1 − prob_yes
no_edge   = no_score − (1 − market.yes_price)
```

Recommendation logic (each side evaluated independently):

- `yes_edge > +0.07` and YES-side mean confidence HIGH and consensus gate → `BUY YES`
- `no_edge > +0.07` and NO-side mean confidence HIGH and consensus gate → `BUY NO`
- Otherwise → `PASS`

Overall `confidence_level` label uses the higher of the two per-side means.

---

## Polymarket Gamma API (`pqi/market/polymarket.py`)

Base URL: `https://gamma-api.polymarket.com`

Key endpoints:

- `GET /markets?order=volume&active=true&closed=false&limit=N` — unfiltered market list
- `GET /events?order=volume&series_id=ID` — markets filtered by series (server-side)
- `GET /events?order=volume&tag_id=ID` — markets filtered by tag (server-side)
- `GET /events?order=volume&end_date_min=X&end_date_max=Y` — time-windowed queries
- `GET /series?slug=X` — resolve series slug → series_id
- `GET /series?limit=200` — list all series (for discovery)
- `GET /tags/slug/<slug>` — resolve tag slug → tag object with id
- `GET /tags?limit=200&offset=N` — paginated tag listing (2000+ tags, no filtering)
- `GET /public-search?q=TEXT` — full-text search across events/markets (finds ghost events)
- `GET /markets/{id}` — single market detail

No auth required. Use `httpx.AsyncClient`. Respect rate limits — 0.5s delay.

**Category resolution (three-pass, parallelized):**
- `resolve_category(query)` tries:
  1. Exact series slug match via `/series?slug=<query>`
  2. (parallel) Partial series slug/title match via `/series?limit=200`
     + Exact tag slug match via `/tags/slug/<query>`
  3. Partial tag match via paginated `/tags` (fallback, caps at 600 tags)
- Series match → `series_id` param on `/events` (server-side filter)
- Tag match → `tag_id` param on `/events` (server-side filter)
- `/markets` endpoint has NO series_id or tag_id params — always use `/events` for filtering

**Event tags:** Events have a `tags` array with `slug`, `label`, and `id` fields.
Example: `[{"id": "64", "slug": "esports"}, {"id": "101672", "slug": "valorant"}]`
Key tag IDs: esports=64, sports=1, valorant=101672, counter-strike-2=100780, video-games=3.

**Ghost events:** Some events are invisible to all `/events` list queries but findable via
`/public-search?q=TEXT` or direct `/events/{id}` fetch. See Known Issues.

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
LOCAL_LLM_CONCURRENCY=1 # only applies to Ollama; Anthropic runs fully parallel
ANTHROPIC_API_KEY=sk-ant-... # leave blank during dev
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
TAVILY_API_KEY=tvly-...
TAVILY_SEARCHES_PER_MARKET=5 # number of Tavily queries per market (3–5)
TAVILY_SNIPPET_CAP=5000 # max chars of search snippets sent to LLM
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
- [x] Two-sided confidence scoring (confidence_yes / confidence_no per agent)
- [x] Rules Lawyer refocus (outcome condition, not oracle timing)
- [x] Richer briefing data (5 Tavily queries, smarter templates, 5000-char cap)
- [x] Explicit time awareness (EVENT TIMING block in briefing)
- [x] Anthropic backend fix (`claude-haiku-4-5-20251001`) + concurrency bypass
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

**Last updated:** [session 13 — API refactor: server-side tag filtering, ghost event investigation]

### Implemented & tested ✓

- `agents/personas.py` — all 7 system prompts, AGENT_PERSONAS dict, build_prompt().
  SHARED_PREAMBLE rules: TWO-SIDED MARKET, TWO-SIDED CONFIDENCE (confidence_yes /
  confidence_no guidance), TIME-TO-REVERSAL (references EVENT TIMING field).
  Rules Lawyer rewritten (session 11): focuses on outcome CONDITION, not oracle timing.
  All 7 output formats use `confidence_yes` + `confidence_no` (not `confidence_level`).
- `config.py` — reads ALL tunables via `os.getenv(KEY, default)`. True code constants
  only: `GAMMA_BASE_URL`, `VALID_BACKENDS`. `.env.example` is canonical reference.
  Session 11 additions: `TAVILY_SEARCHES_PER_MARKET`, `TAVILY_SNIPPET_CAP`,
  `LOCAL_LLM_CONCURRENCY` (renamed from `OLLAMA_CONCURRENCY`).
  `ANTHROPIC_MODEL` default updated to `claude-haiku-4-5-20251001`.
  `MIN_VOLUME_SHORT_WINDOW_USD` default lowered to 500 (was 10000).
- `models.py` — all four Pydantic v2 models.
  `AgentVerdict`: `confidence` split into `confidence_yes` + `confidence_no`.
  `CouncilResult`: added `no_score` and `no_edge` fields. `from_council_verdicts()`
  computes two-sided scoring: YES-side and NO-side weighted independently.
  Gates: edge threshold, per-side HIGH confidence, median consensus, min_tradeable_price.
- `llm/client.py` — Ollama + Anthropic backends, `async def chat()` public interface.
  Anthropic model fixed to `claude-haiku-4-5-20251001`.
- `ingestion/synthesizer.py` — `build_briefing()`: configurable Tavily searches
  (default 5 queries with smarter templates: news, analysis, odds, stats, expert).
  First 2 queries use `search_depth="advanced"` with 7 results. Snippet cap configurable
  (default 5000 chars). Query count controlled by `TAVILY_SEARCHES_PER_MARKET`.
- `agents/council.py` — `run_council()`: 7 agents via `asyncio.gather()`.
  Semaphore only active for Ollama backend (LOCAL_LLM_CONCURRENCY); Anthropic runs
  all 7 fully parallel. Parser extracts `confidence_yes`/`confidence_no` with fallback
  to legacy `confidence_level`. `_format_briefing()` includes EVENT TIMING block
  (LIVE/IMMINENT <3h, TODAY <24h, THIS WEEK <168h, FUTURE) with contextual notes.
  Rules Lawyer resolution_criteria injection still in place.
- `scoring.py` — `build_council_result` wires all config thresholds.
  `confidence_weighted_score_yes()` and `confidence_weighted_score_no()` for two-sided.
  `determine_confidence_level()` uses best per-side mean.
- `main.py` — Typer CLI: `analyze` + `categories` commands.
  Flags: `--category/-c`, `--top-n/-n`, `--top/-t`, `--resolve-within/-r`, `--min-volume/-v`.
  All flags compose freely. Verdict table shows Conf Y% / Conf N% columns.
  Recommendation panel shows both YES and NO edges (Council YES / Council NO scores).
  Filter summary line shows active filters. `CategoryNotFoundError` caught with
  helpful error message. Empty results message suggests `-v 0`.
  `categories` command shows broad tags table + specific series table.
- `market/polymarket.py` — **Major CLI overhaul (session 11) + API refactor (session 13):**
  - `resolve_category()`: three-pass with parallel optimization:
    Pass 1: exact series slug via `/series?slug=<query>`.
    Pass 2+3a (parallel): partial series match via `/series?limit=200`
    + exact tag slug via `/tags/slug/<query>` (fast single-request lookup).
    Pass 3b: partial tag match via paginated `/tags` (caps at 600, fallback).
    Returns `CategoryMatch(type, value)` — `"series"` (series_id) or `"tag"` (tag_id).
  - **Server-side tag filtering** via `tag_id` param on `/events` endpoint.
    Replaced client-side `_event_matches_tag()` approach (removed).
  - `CategoryNotFoundError`: raised when `-c` matches nothing (loud failure).
  - `fetch_markets()` gains `volume_floor_override` param (wired to `--min-volume/-v`).
  - `fetch_tags()`: scans top 200 events for unique tag slugs with counts.
  - Both `_fetch_markets_general()` and `_fetch_markets_by_time_window()` accept
    `tag_id` param for server-side filtering via `/events?tag_id=<id>`.

### Not started ✗

- Phase 2 remaining: SQLite cache, Rules Lawyer veto weight, historical accuracy tracking

### Known issues / assumptions

- **Gamma API ghost events (confirmed 2026-04-03):** Some events exist (fetchable
  by direct ID: `/events/{id}`) but are invisible to ALL list queries — not returned
  by `/events?series_id=...`, `/events?end_date_min=...&end_date_max=...`,
  `/events?tag=...`, or unfiltered `/events`. Their markets are also absent from
  `/markets` list queries. Example: event 273395 (Valorant TH vs NAVI BO3) had
  `active=True, closed=False` with valid markets but was invisible to list endpoints.
  This is a Gamma API indexing bug. No workaround exists on our end — markets in
  ghost events simply won't appear in results. May be related to mid-match settlement
  transitions (de-indexed when some sub-markets start resolving).
- **Gamma API order params (confirmed 2026-04):** Only `order=volume` and
  `order=liquidity` work. `order=end_date` and `order=volume_24hr` return 422.
- **Events vs markets endpoint:** `/events` for time-window and category queries
  (groups related markets, has tags); `/markets` for unfiltered general queries.
  Embedded markets in events lack nested `events` key — category from parent event's
  `tags` via `_category_from_event()`.
- **Two-tier category resolution:** Specific series slugs (e.g. "counter-strike")
  use server-side `series_id` filter on `/events`. Broad tags (e.g. "esports") use
  server-side `tag_id` filter on `/events`. Tag resolution uses `/tags/slug/<slug>`
  for fast exact lookup, falling back to paginated `/tags` for partial matches.
- **Volume floor behavior:** General queries use `MIN_VOLUME_USD` ($10K default).
  Short time windows (≤ `SHORT_WINDOW_HOURS`, default 4h) use
  `MIN_VOLUME_SHORT_WINDOW_USD` ($500 default). `--min-volume 0` disables all filtering.
- **Min-tradeable-price gate:** Recommended side must cost ≥ MIN_TRADEABLE_PRICE
  (default 5¢). Prevents "BUY NO at $0.01" on near-over events.
- **Consensus gate:** Median probability must pass (>0.40 for BUY YES, <0.60 for
  BUY NO). Blocks outlier-driven false signals.
- **Two-sided confidence:** Agents output separate confidence_yes and confidence_no.
  Parser falls back to legacy confidence_level (both sides equal) if agent doesn't
  produce the new fields. Scoring evaluates BUY YES from yes_edge + yes-side
  confidence, BUY NO from no_edge + no-side confidence independently.
- **LOCAL_LLM_CONCURRENCY:** Only applies to Ollama backend. Anthropic calls skip
  the semaphore entirely. Default 1 for consumer GPUs.
- `.env` uses `CLAUDE_API_KEY` instead of `ANTHROPIC_API_KEY`. `config.py` accepts
  either (`ANTHROPIC_API_KEY` checked first, `CLAUDE_API_KEY` as fallback).
- `TAVILY_API_KEY` is validated as required at import time.
- Gamma API field notes: `outcomePrices` is JSON string `'["0.73", "0.27"]'`;
  `volumeNum` is float USD; must pass `closed=false` + `active=true`.
- Council JSON parser: Pass 1 direct json.loads(), Pass 2 strip control chars.

### Environment

- LLM_BACKEND: `anthropic` (tested, working with claude-haiku-4-5-20251001)
- Ollama: installed, llama3.1:8b available as fallback
- Anthropic API key: set as `CLAUDE_API_KEY` in .env, resolved correctly by config.py
- Tavily API key: set and resolving correctly
- LOCAL_LLM_CONCURRENCY: 1 (only affects Ollama; Anthropic runs fully parallel)

---

## Next Session Prompt

```
Read CLAUDE.md in full, paying close attention to ## Current State.

Session 13 refactored polymarket.py to use proper Gamma API endpoints discovered
from official docs (https://docs.polymarket.com/market-data/fetching-markets.md):

- `tag_id` param on `/events` for server-side tag filtering (replaces client-side
  `_event_matches_tag()` which is now removed)
- `/tags/slug/<slug>` for fast exact tag lookup (single request vs paginating 2000 tags)
- Parallel Pass 2+3a in resolve_category() — series listing + tag slug run concurrently
- Documented `/public-search?q=TEXT` as the only endpoint that finds ghost events

Ghost event investigation confirmed: event 273395 invisible to ALL list endpoints
(`/events`, `/markets`, with any filter combination) but findable via `/public-search`
and direct `/events/{id}`. No workaround for broad category browsing.

Additional API endpoints documented but not yet used:
- `GET /tags` — full paginated tag list (2000+ tags, used as fallback for partial match)
- `GET /sports` — sports metadata with tag IDs, images, resolution sources
- `GET /public-search?q=TEXT` — full-text search across events/markets/profiles

## Phase 2 remaining priorities

### 1. SQLite cache (`pqi/cache.py`)
Skip re-running the full council if yes_price moved < 2% since last run.
- Use `aiosqlite` with a `council_results` table (market_id, yes_price_at_run,
  result_json, created_at).
- On `analyze`, query cache first. If hit and price delta < 0.02, load from cache.
- Otherwise run council and write result back to cache.
- Add `--no-cache` flag to `main.py` to force a fresh run.

### 2. Rules Lawyer veto weight (2× multiplier)
In `scoring.py`, give the Rules Lawyer's confidence_yes/confidence_no double
weight in the weighted formula. Add `RULES_LAWYER_WEIGHT=2` to config.py.
Update scoring to accept per-agent weights.

### 3. Historical accuracy tracking
After each run, append to a JSONL file (`~/.pqi/history.jsonl`):
  {market_id, question, yes_price_at_run, final_score, no_score,
   recommendation, timestamp}
Add a `pqi history` CLI command that prints a table of past runs and
(where markets are resolved) shows whether the recommendation was correct.

### 4. /public-search integration (optional)
Use `/public-search?q=TEXT` as a supplementary fetch when a category query
returns fewer results than expected, to catch ghost events. Only useful if the
user's category query provides searchable text.

## Instructions
- Decide which items to tackle based on complexity vs. value
- Read the relevant existing files before writing any new code
- Update ## Current State and ## Next Session Prompt at end of session
```
