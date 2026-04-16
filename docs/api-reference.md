# API Reference — External Services

Details on every external API the project talks to. Referenced from CLAUDE.md.

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
     - Exact tag slug match via `/tags/slug/<query>`
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
`/public-search?q=TEXT` or direct `/events/{id}` fetch. See `docs/known-issues.md`.

---

## Polymarket CLOB API (`pqi/market/clob.py`)

Base URL: `https://clob.polymarket.com` (code constant in `config.py`)

**Official docs:** `https://docs.polymarket.com/trading/orderbook`

Used for **read-only price history** — no auth needed. The `py-clob-client` SDK
is installed for future trading phases but the price history fetch uses raw
`httpx.AsyncClient` (async-native, consistent with the project's pattern).

**Key endpoint:**

- `GET /prices-history?market=TOKEN_ID&interval=1w&fidelity=60`
  - `market` param takes a CLOB token ID (NOT the Gamma market ID)
  - Response: `{"history": [{"t": unix_timestamp, "p": price_float}, ...]}`
  - `interval`: `1d`, `1w`, `1m`, `max` (relative to now)
  - `fidelity`: minutes between data points (60 = hourly)

**Token ID resolution:** Gamma API markets have a `clobTokenIds` field (JSON
string like `["YES_TOKEN", "NO_TOKEN"]`). Extracted during `_parse_market()`
and stored as `MarketData.clob_token_ids`. Index 0 = YES, index 1 = NO.

**Two-sided fetching:** YES and NO tokens are fetched in parallel via
`asyncio.gather()`. Both sides are required — if either fails, returns None.
YES + NO CLOB prices do NOT sum to 1.0 (spread/vig in the orderbook), so
inferring one from the other is incorrect.

**Computed metrics (`SidePriceHistory` model, computed per side):**

- `weighted_avg`: exponential-decay weighted average, half-life 2 days.
  Formula: `Σ(price × e^(-λ × age_hours)) / Σ(e^(-λ × age_hours))` where λ = ln(2)/48.
- `direction`: linear regression slope × window hours, thresholded at ±2pp.
  RISING / FALLING / STABLE.
- `change_1w_pp` / `change_24h_pp`: net price change in percentage points.
- `volatility_24h`: stddev of hourly price deltas over last 24h (pp/hr).
- `sharp_moves`: single-hour moves exceeding 5pp, last 3.

**Non-fatal:** `fetch_price_history()` returns `None` on any failure (< 2 token
IDs, HTTP error on either side, insufficient data < 2 points). The pipeline
continues without trend data. Warning logged.

**Briefing injection:** After briefing build, before council summon. The
`BriefingDocument.price_history` field carries `PriceHistory(yes=..., no=...)`.
In `_format_briefing()`, a conditional `MARKET PRICE HISTORY` block renders
both YES and NO side metrics (direction, weighted avg, changes, volatility,
sharp moves) between EVENT TIMING and VOLUME. Includes a note that sides
don't sum to 100%.

**Display:** Recommendation panel shows `7d Trend YES` and `7d Trend NO` lines
with pp change, direction, 24h change, and volatility when price history is
available.

---

## PandaScore (`pqi/ingestion/pandascore.py`, `pqi/ingestion/match_resolver.py`)

Base URL: `https://api.pandascore.co` (literal in `config.py`)

**Official docs:** `https://developers.pandascore.co`

Used for **structured esports match data** — authoritative team
identifiers, scheduled time, status (scheduled/running/finished),
final score + winner when resolved, recent team form, head-to-head
history. Bearer-token auth via `PANDASCORE_API_KEY`. Free tier:
1000 req/hr.

**Key endpoints used:**

- `GET /videogames` — list supported games (used once at module
  load; result cached implicitly via short-lived client lifetimes).
- `GET /matches?filter[videogame]=<slug>&range[scheduled_at]=<from>,<to>` —
  list matches for a game in a time window.
- `GET /matches/{id}` — single match detail.
- `GET /teams/{id}/matches?per_page=N&filter[finished]=true` —
  team's last-N completed matches (for `TeamForm`).
- `GET /matches?filter[opponent_id]=<id_a>,<id_b>` — direct
  head-to-head between two teams.

**Pipeline integration:** `match_resolver.resolve_match(market)`
is called from `main._run_analysis()` before the cache lookup
(so its status output can inform cache invalidation). Returns
`StructuredMatchRecord | None`. Non-fatal: any failure (HTTP
error, missing key, fuzzy score below threshold, unknown game)
returns `None` and the pipeline continues without structured data.

**Fuzzy matching (rapidfuzz):** `_score_candidate(a, b, cand_a,
cand_b) = min(fuzz.token_set_ratio(a, cand_a),
fuzz.token_set_ratio(b, cand_b))`. Symmetric scoring — a perfect
match on one side cannot rescue a poor match on the other.
Default threshold: `PANDASCORE_FUZZY_THRESHOLD=75`.

**Status mapping:** PandaScore `not_started` / `postponed` →
`scheduled`; `running` → `running`; `finished` / `canceled` →
`finished`.

**Game coverage:** `GAME_MAPPING` dict in `match_resolver.py` —
extend to add new categories. Currently: counter-strike, valorant,
league-of-legends, dota-2, rainbow-six-siege.

**Briefing injection:** the resolved record is stored on
`BriefingDocument.structured_match`. Surfaced in two places:
(1) synthesizer user prompt (STRUCTURED MATCH RECORD block, with
"DO NOT paraphrase" guidance), (2) council briefing (STRUCTURED
MATCH DATA block, second only to EVENT TEMPORAL DISAMBIGUATION
in priority).

---

## Tavily Synthesizer (`pqi/ingestion/synthesizer.py`)

- Use `TAVILY_API_KEY` from env
- **LLM Search Architect** designs queries before searching (category-aware, timing-aware)
  - Generates `TavilyQuery` objects with full param tuning: `topic`, `time_range`,
    `include_domains`, `exclude_domains`, `search_depth`
  - Falls back to hardcoded templates if LLM call fails
- Searches run in **parallel** via `AsyncTavilyClient` + `asyncio.gather()`
- Condense results into `BriefingDocument` via separate LLM synthesis call
- Cap at `TAVILY_SEARCHES_PER_MARKET` queries (default 5)

---

## Environment Variables (`.env`)

LLM_BACKEND=ollama # or "anthropic"
OLLAMA_MODEL=llama3.1:8b
OLLAMA_BASE_URL=http://localhost:11434
LOCAL_LLM_CONCURRENCY=1 # only applies to Ollama; Anthropic runs fully parallel
ANTHROPIC_API_KEY=sk-ant-... # leave blank during dev
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
TAVILY_API_KEY=tvly-...
TAVILY_SEARCHES_PER_MARKET=5 # number of Tavily queries per market (3-5)
TAVILY_SNIPPET_CAP=5000 # max chars of search snippets sent to LLM
PANDASCORE_API_KEY= # PandaScore bearer token (esports structured data; empty disables resolver)
PANDASCORE_ENABLED=true # master switch for the PandaScore resolver
PANDASCORE_FUZZY_THRESHOLD=75 # min min(token_set_ratio_a, token_set_ratio_b) to accept a candidate
PANDASCORE_WINDOW_HOURS=48 # search window around event_start_time for candidate matches
PANDASCORE_RECENT_FORM_COUNT=5 # last-N completed matches per team for form summary
PRICE_HISTORY_INTERVAL=1w # CLOB price history time window (1d, 1w, 1m)
PRICE_HISTORY_FIDELITY=60 # CLOB price history resolution in minutes (60 = hourly)
LOG_LEVEL=INFO
MAX_MARKETS_TO_FETCH=20
MIN_VOLUME_USD=10000 # ignore illiquid markets
EDGE_THRESHOLD=0.09 # deprecated — informational only (no longer a gate)
MIN_TRADEABLE_PRICE=0.09 # min per-share price of recommended side (PASS if below)
SHORT_WINDOW_HOURS=4 # resolve_within_hours <= this triggers relaxed volume floor
MIN_VOLUME_SHORT_WINDOW_USD=500 # volume floor for short-window queries
