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
    event_start_time: datetime | None  # when the real-world event starts
    yes_price: float          # 0.0–1.0 (current market implied prob)
    volume_usd: float
    category: str

class BriefingDocument(BaseModel):
    market: MarketData
    key_facts: list[str]      # bullet facts from Tavily search
    news_summary: str         # synthesized narrative
    side_yes_case: list[str]  # evidence/arguments favoring YES outcome
    side_no_case: list[str]   # evidence/arguments favoring NO outcome
    key_context: list[str]    # format, stakes, head-to-head, venue, etc.
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
    edge: float               # final_score - yes_price (informational)
    no_edge: float            # no_score - no_price (informational)
    recommendation: str       # "BUY YES" | "BUY NO" | "PASS"
    confidence_level: str     # "HIGH" | "MEDIUM" | "LOW"
    signal_type: str | None   # "STRONG" | "CONFIRMED" | "CAUTIOUS" | "CONTRARIAN" | None
    pass_reason: str          # why PASS (empty for BUY)
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
- Dual-perspective: must reason about BOTH sides before setting probability

| Key            | Agent                      | Core Bias                                                                       |
| -------------- | -------------------------- | ------------------------------------------------------------------------------- |
| `rules_lawyer` | The Rules Lawyer           | Resolution criteria only — ignores real-world outcome if oracle condition unmet |
| `bull`         | The Bull (Favorite's Advocate) | Strongest case for the FAVORED side (price-aware, dynamic)                  |
| `bear`         | The Bear (Underdog's Advocate) | Strongest case for the UNDERDOG side (price-aware, dynamic)                 |
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

Recommendation logic (probability-first, session 15):

1. Identify council's favored side (the side with >50% council probability)
2. Check if council agrees with market (market-aligned) or disagrees (contrarian)
3. **Market-aligned**: council prob >= `MIN_COUNCIL_PROBABILITY` (0.60) + confidence HIGH → BUY
4. **Contrarian**: council prob >= `MIN_CONTRARIAN_PROBABILITY` (0.70) + confidence
   >= `MIN_CONTRARIAN_CONFIDENCE` (0.80) → BUY with CONTRARIAN label
5. Otherwise → PASS

Signal type (conviction label on BUY recommendations):
- `STRONG` — positive edge on recommended side (council > market)
- `CONFIRMED` — edge near zero (council ≈ market)
- `CAUTIOUS` — negative edge (council < market but still favors same side)
- `CONTRARIAN` — council goes against market, strict criteria met

Edge is informational (affects signal type label), NOT a gate for recommendations.
Overall `confidence_level` label uses the higher of the two per-side means.

---

## Polymarket Gamma API (`pqi/market/polymarket.py`)

Base URL: `https://gamma-api.polymarket.com`

**Official docs:** `https://docs.polymarket.com/llms.txt` (index of all pages).
Full OpenAPI spec at: `https://docs.polymarket.com/api-reference/search/search-markets-events-and-profiles`
Always check docs before exploring API behavior. Detailed field/param reference in
auto-memory `reference_gamma_api.md`.

Key endpoints:

- `GET /markets?order=volume&active=true&closed=false&limit=N` — unfiltered market list
- `GET /events?order=volume&series_id=ID` — markets filtered by series (server-side)
- `GET /events?order=volume&tag_id=ID` — markets filtered by tag (server-side)
- `GET /events?order=volume&tag_slug=SLUG` — markets filtered by tag slug (server-side, no ID resolution needed)
- `GET /events?order=volume&end_date_min=X&end_date_max=Y` — time-windowed by resolution
- `GET /events?volume_min=N` — server-side volume floor on events
- `GET /markets?volume_num_min=N` — server-side volume floor on markets
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
- `/markets` endpoint has `tag_id` but NOT `series_id`. `/events` has both.

**Event-level live/ended fields:**
- `live: bool`, `ended: bool`, `gameStatus`, `score`, `elapsed`, `period`
- These are populated on `/public-search` and direct `/events/{id}` fetches
- They are always `None` on the `/events` list endpoint (not populated in list responses)
- `acceptingOrders` on markets: set to `False` on some decided markets (inconsistent)
- Our code filters out `ended=True` events and `acceptingOrders=False` markets

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

- [x] SQLite cache — skip re-running council if price moved < 2%
- [x] `--category` filter flag (series slug → series_id lookup; `pqi categories` command)
- [x] Two-sided confidence scoring (confidence_yes / confidence_no per agent)
- [x] Rules Lawyer refocus (outcome condition, not oracle timing)
- [x] Richer briefing data (5 Tavily queries, smarter templates, 5000-char cap)
- [x] Explicit time awareness (EVENT TIMING block in briefing)
- [x] Anthropic backend fix (`claude-haiku-4-5-20251001`) + concurrency bypass
- [x] Rules Lawyer veto weight (2x multiplier config)
- [x] Historical accuracy tracking

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

**Last updated:** [session 15 — Dynamic Bull/Bear, dual-perspective prompts, enriched briefing]

### Implemented & tested ✓

- `agents/personas.py` — all 7 system prompts, AGENT_PERSONAS dict, build_prompt().
  SHARED_PREAMBLE rules: TWO-SIDED MARKET, TWO-SIDED CONFIDENCE, DUAL-PERSPECTIVE
  ANALYSIS (session 15), TIME-TO-REVERSAL.
  Bull rewritten (session 15): "Favorite's Advocate" — dynamically advocates for
  the side currently priced higher. Receives SIDE CONTEXT block identifying
  favored/underdog. Must analyze both sides but builds case for favorite.
  Bear rewritten (session 15): "Underdog's Advocate" — dynamically advocates for
  the side currently priced lower. Receives SIDE CONTEXT block. Must analyze
  both sides but builds case for underdog.
  Rules Lawyer (session 11): focuses on outcome CONDITION, not oracle timing.
  Contrarian rewritten (session 14): prioritizes competitively plausible disruptions
  (roster changes, meta shifts, form trajectories, morale factors) over infrastructure
  failures. Infrastructure failures demoted to tertiary mention.
  All 7 output formats use `confidence_yes` + `confidence_no` (not `confidence_level`).
  Bear output key changed: `primary_blocker` → `primary_upset_factor`.
- `config.py` — reads ALL tunables via `os.getenv(KEY, default)`. True code constants
  only: `GAMMA_BASE_URL`, `VALID_BACKENDS`. `.env.example` is canonical reference.
  Session 14 additions: `RULES_LAWYER_WEIGHT` (default 2), `CACHE_DB_PATH`
  (default `~/.pqi/cache.db`), `CACHE_PRICE_DELTA` (default 0.02).
  `ANTHROPIC_MODEL` default: `claude-haiku-4-5-20251001`.
  Session 15 additions: `MIN_COUNCIL_PROBABILITY` (default 0.60),
  `MIN_CONTRARIAN_PROBABILITY` (default 0.70), `MIN_CONTRARIAN_CONFIDENCE`
  (default 0.80).
- `models.py` — all four Pydantic v2 models.
  `BriefingDocument` (session 15): `green_flags`/`red_flags` replaced with
  `side_yes_case`/`side_no_case` + new `key_context` field.
  `AgentVerdict`: `confidence` split into `confidence_yes` + `confidence_no`.
  `CouncilResult`: `from_council_verdicts()` accepts `agent_weights: dict[str, float] | None`.
  When provided, multiplies agent confidence by weight in the weighted sum (e.g.
  Rules Lawyer at 2× counts double). Two-sided scoring: YES-side and NO-side
  weighted independently. Gates: edge threshold, per-side HIGH confidence,
  median consensus, min_tradeable_price.
  `pass_reason: str` field (session 14): when recommendation is PASS, explains
  which gate(s) blocked the signal. Empty string for BUY YES/BUY NO.
  `signal_type` field (session 15): conviction label for BUY recommendations:
  STRONG (positive edge), CONFIRMED (edge ~0), CAUTIOUS (negative edge),
  CONTRARIAN (council goes against market). None when PASS.
  Recommendation logic rewritten (session 15): probability-first instead of
  edge-gated. Council probability gates the recommendation; edge determines
  signal type. Market-aligned bets use standard thresholds; contrarian bets
  use stricter probability + confidence requirements.
- `llm/client.py` — Ollama + Anthropic backends, `async def chat()` public interface.
- `ingestion/synthesizer.py` — `build_briefing()`: configurable Tavily searches
  (default 5 queries with smarter templates). Snippet cap configurable (default 5000).
  Session 15: entity-specific Tavily queries for competitive markets (splits on
  "vs", "beat", "win against", "defeat" patterns via `_extract_entities()`).
  LLM synthesis now extracts `side_yes_case`, `side_no_case`, `key_context`
  (replaces `green_flags`/`red_flags`).
- `agents/council.py` — `run_council()`: 7 agents via `asyncio.gather()`.
  Semaphore only active for Ollama backend; Anthropic runs all 7 fully parallel.
  Parser extracts `confidence_yes`/`confidence_no` with fallback to legacy
  `confidence_level`. Rules Lawyer resolution_criteria injection in place.
  Session 15: `_build_side_context()` injects SIDE CONTEXT block for bull/bear
  identifying favored/underdog sides. `_format_briefing()` now renders
  CASE FOR YES / CASE FOR NO / KEY CONTEXT sections (replaces GREEN FLAGS / RED FLAGS).
- `scoring.py` — `build_council_result` wires all config thresholds + agent_weights
  (`{"The Rules Lawyer": RULES_LAWYER_WEIGHT}`). Two-sided scoring functions.
  Session 15: now passes `MIN_COUNCIL_PROBABILITY`, `MIN_CONTRARIAN_PROBABILITY`,
  `MIN_CONTRARIAN_CONFIDENCE` to `from_council_verdicts()`.
- `history.py` — **NEW (session 14).** `append_result()` writes one JSON line to
  `~/.pqi/history.jsonl` per analyze run. `load_history()` reads all entries.
  Fields: market_id, question, yes_price_at_run, final_score, no_score, edge,
  no_edge, recommendation, confidence_level, timestamp.
- `cache.py` — **NEW (session 14).** SQLite cache via `aiosqlite`. Table:
  `council_cache(market_id PK, yes_price, result_json, created_at)`.
  `get_cached(market_id, current_yes_price)` returns cached CouncilResult if price
  delta < `CACHE_PRICE_DELTA` (default 2%). `store_result()` upserts.
  DB at `~/.pqi/cache.db` (configurable via `CACHE_DB_PATH`).
- `main.py` — Typer CLI: `analyze` + `categories` + `history` commands.
  Flags: `--category/-c`, `--top-n/-n`, `--top/-t`, `--starts-within/-s`,
  `--resolve-within/-r`, `--min-volume/-v`, `--no-cache`.
  `analyze` checks cache before running council; stores result after.
  `--no-cache` forces a fresh council run. Result appended to history after every run.
  PASS recommendation now shows reason line (e.g. "Edge too small...").
  Sibling market discovery (session 14): after market selection, fetches parent
  event via `fetch_event_markets()`, shows all sibling markets, offers to analyze
  them too. Useful for BO3 events with map 1/map 2/moneyline sub-markets.
  `history` command shows past runs in a Rich table.
- `market/polymarket.py` — three-pass `resolve_category()` with parallel optimization.
  Server-side tag filtering via `tag_id` on `/events`. `CategoryNotFoundError`.
  `fetch_tags()` scans top 200 events for unique tag slugs.
  `fetch_event_markets(market_id)` (session 14): fetches parent event to discover
  all sibling markets. Returns `(event_title, list[MarketData])`. Uses
  `/markets/{id}` → event ID → `/events/{event_id}` to get all markets.
  Session 15: `--starts-within/-s` flag filters by event start time (client-side,
  API ignores event_start_time params). `--live/-l` flag filters to events already
  started but not resolved. Both composable with each other and `--resolve-within`.
  `MarketData.event_start_time` parsed from `eventStartTime` field.
  Market list table shows "Starts" column when event_start_time is available.
  Session 16: filters out `acceptingOrders=False` markets and `ended=True` events
  (event-level field; populated on direct fetch/search but usually None on list endpoint).

### Not started ✗

- /public-search integration (optional — catch ghost events in category queries)

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
  `/markets` has `tag_id` param but NOT `series_id`. `/events` has both `tag_id`
  and `tag_slug` (direct slug filtering, no ID resolution needed). Both endpoints
  have server-side volume filtering (`volume_min` on events, `volume_num_min` on markets).
  Embedded markets in events lack nested `events` key — category from parent event's
  `tags` via `_category_from_event()`.
- **Event live/ended fields:** Events have `live`, `ended`, `gameStatus`, `score`,
  `elapsed`, `period` fields (from OpenAPI spec). These are populated on
  `/public-search` and direct `/events/{id}` but always `None` on `/events` list
  endpoint. Code checks `ended=True` when available but cannot rely on it from lists.
- **Market acceptingOrders:** Set to `False` on some decided markets but inconsistent.
  Markets can show `YES=0.00, acceptingOrders=True` when parent event is `ended=True`.
  Code filters out `acceptingOrders=False` as a first-pass filter.
- **event_start_time query params:** NOT supported on any endpoint. Silently ignored
  (returns 200 but doesn't filter). All event start time filtering is client-side.
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

Session 15 was a major agent + scoring overhaul in three areas:

### A. Agent perspective fixes
1. Dynamic Bull/Bear — Bull = "Favorite's Advocate" (higher-priced side),
   Bear = "Underdog's Advocate" (lower-priced side). SIDE CONTEXT block
   injected in council.py. Both still output probability_score as P(YES).
2. Dual-perspective analysis — SHARED_PREAMBLE rule forcing all agents
   to reason about BOTH sides.
3. Probability direction fix — OUTCOME LABELS block in every briefing
   explicitly states what YES/NO mean. Anti-inversion warning in preamble.
   Fixes bug where agents confused P(favored entity) with P(YES).

### B. Enriched briefing
4. BriefingDocument: green_flags/red_flags → side_yes_case/side_no_case
   + new key_context field. Synthesizer detects entities for competitive
   markets (_extract_entities()) and runs per-entity Tavily queries.
5. Briefing formatter: CASE FOR YES / CASE FOR NO / KEY CONTEXT sections.

### C. Probability-first scoring (biggest change)
6. Recommendation logic rewritten: probability gates, not edge gates.
   - Council favored side (>50%) with prob >= MIN_COUNCIL_PROBABILITY
     (0.60) + HIGH confidence → BUY (market-aligned)
   - Contrarian: prob >= MIN_CONTRARIAN_PROBABILITY (0.70) + confidence
     >= MIN_CONTRARIAN_CONFIDENCE (0.80) → BUY CONTRARIAN
   - Signal type: STRONG / CONFIRMED / CAUTIOUS / CONTRARIAN
   - Edge is informational (sets signal type), not a gate
7. New config: MIN_COUNCIL_PROBABILITY, MIN_CONTRARIAN_PROBABILITY,
   MIN_CONTRARIAN_CONFIDENCE.

## Remaining ideas / future work

### 1. Cache TTL / expiry
Add time-based expiry to cached results (e.g. 24h max age). Currently cache
only invalidates on price movement > 2%.

### 2. History: resolution tracking
Extend `pqi history` to check if markets resolved and show whether the
recommendation was correct (win/loss). Fetch market outcome from Gamma API.

### 3. /public-search integration
Use `/public-search?q=TEXT` as supplementary fetch for ghost events.

### 4. Multi-market batch analysis
Allow `pqi analyze --top 3` to analyze top N markets sequentially.

### 5. Shared briefing for sibling markets
When analyzing siblings from the same event, share the Tavily briefing
(same event = same news) but still run separate councils (different
resolution criteria per market).

### 6. Market type detection (explicit)
If the implicit approach (session 15) proves insufficient, add explicit
market_type detection (head_to_head vs binary_event) for different briefing
templates and prompt wording.

## Instructions
- Do an end-to-end test run to verify all session 15 changes work
- Test on esports (competitive) + binary (crypto/politics) markets
- Verify: probability direction correct, signal_type labels appear,
  Bull/Bear reference correct sides, briefing sections correct
- Read the relevant existing files before writing any new code
- Update ## Current State and ## Next Session Prompt at end of session
```
