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
    expected_roi: float       # max ROI (1-price)/price of recommended side; 0.0 on PASS
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

| Key            | Agent                          | Core Bias                                                                       |
| -------------- | ------------------------------ | ------------------------------------------------------------------------------- |
| `rules_lawyer` | The Rules Lawyer               | Resolution criteria only — ignores real-world outcome if oracle condition unmet |
| `bull`         | The Bull (Favorite's Advocate) | Strongest case for the FAVORED side (price-aware, dynamic)                      |
| `bear`         | The Bear (Underdog's Advocate) | Strongest case for the UNDERDOG side (price-aware, dynamic)                     |
| `statistician` | The Base-Rate Statistician     | Historical base rate, ignores current hype                                      |
| `sentiment`    | The Sentiment Analyst          | Crowd psychology vs current price                                               |
| `specialist`   | The Domain Specialist          | Field-specific technical nuance                                                 |
| `contrarian`   | The Contrarian                 | Black swan — what flips the board                                               |

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

Recommendation logic (ROI-aware, probability-first):

Max ROI of the recommended side: `expected_roi = (1 - side_price) / side_price`.
Purely a function of price — council conviction is gated separately. A 70¢
share has a max ROI of 42.9% (sell at $1 = +30¢/70¢); an 80¢ share caps at
25%; a 90¢ share at 11.1%.

Gate order inside `from_council_verdicts()`:

1. **Price floor**: `side_price < MIN_TRADEABLE_PRICE` (0.05) → PASS
2. **Price ceiling**: `side_price > MAX_TRADEABLE_PRICE` (0.90) → PASS.
   Near-resolved; ROI doesn't justify execution risk.
3. **Contrarian** (council disagrees with market): requires all of
   `council_prob >= MIN_CONTRARIAN_PROBABILITY` (0.70),
   `side_conf >= MIN_CONTRARIAN_CONFIDENCE` (0.80),
   `expected_roi >= MIN_CONTRARIAN_ROI` (0.20). Label: `CONTRARIAN`.
4. **Market-aligned**: requires
   `council_prob >= MIN_COUNCIL_PROBABILITY` (0.57),
   `side_conf >= CONFIDENCE_HIGH_THRESHOLD` (0.67),
   `expected_roi >= MIN_EXPECTED_ROI` (0.10).
5. Otherwise → PASS with dynamic pass_reason.

Signal type on market-aligned BUYs is ROI-banded:

- `STRONG` — expected_roi ≥ 50% (cheap side; price ≤ ~0.667)
- `CONFIRMED` — 25% ≤ expected_roi < 50% (price ~0.667–0.80)
- `CAUTIOUS` — 10% ≤ expected_roi < 25% (price ~0.80–0.91)

`EDGE_THRESHOLD` is **deprecated** — `edge` is still computed and displayed
for historical continuity but is no longer consumed as a gate or label.
Overall `confidence_level` label uses the higher of the two per-side means.
`CouncilResult.expected_roi` is persisted on every result (0.0 on PASS).

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
`/public-search?q=TEXT` or direct `/events/{id}` fetch. See Known Issues.

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
- [x] History validation system: full-fidelity persistence, resolution sync,
      per-agent accuracy, postmortem analysis, `pqi history` sub-app
      (list/show/sync/stats)
- [x] Shared briefing for sibling markets (skip synthesizer when analyzing
      siblings from the same event)

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

| Where          | What goes there                                                                         |
| -------------- | --------------------------------------------------------------------------------------- |
| `.env.example` | Every tunable key, its default, and a plain-English comment explaining what it does     |
| `.env`         | User's actual values (not committed)                                                    |
| `config.py`    | `os.getenv("KEY", "default")` — the Python default matches `.env.example`'s shown value |

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

**Last updated:** [session 19 — gate retrospective, Tier 1 ROI 0.15→0.10, Bear bias audit]

### Session 19 additions

- `config.py` — `MIN_EXPECTED_ROI` code default changed from 0.15 to 0.10.
  `.env.example` updated to match. `.env` already had 0.10 from prior adjustment.
  Widens the eligible price band for CAUTIOUS signals from ≤$0.87 to ≤$0.91.
  Zero historical wrong buys added (replayed all 60 non-legacy entries).
- `docs/retrospectives/eval_tier1_roi_2026_04.md` — **NEW.** Full retrospective
  report covering: dataset context (88 runs, 60 non-legacy), command log,
  all 4 non-legacy missed opportunities with root cause breakdown, financial
  impact analysis, Tier 2 simulation results (deferred), and Bear agent
  performance/bias audit.
- **Bear audit findings (documented, not acted on):** The Bear has 38% hit rate
  (worst of all 7 agents), leans NO 81% of the time, and is the "loudest wrong
  voice" on 43% of its contested leans. Root cause: persona wording ("aggressively
  skeptical", "apply Murphy's Law") creates structural NO-bias. 4 remediation
  options documented in retrospective; prompt change not yet implemented.

### Session 18 additions

- `main.py` — `history list` filter flags: `--outcome`, `--rec`, `--conf` with
  alias maps (`_OUTCOME_ALIASES`, `_REC_ALIASES`, `_CONF_ALIASES`). Filters
  apply after numbering so absolute `#` IDs stay stable across filtered views.
  Loud failure on typos (prints valid set). `--all` flag default changed from
  True → False (was a bug, `--all` is now opt-in). Numbering bug fixed: when
  `--all` is set, IDs go 1..total (was incorrectly starting at total-1).
- `main.py` — `_signal_glyph(yes_price)`: ⭐ when favored side is in [55%, 91%)
  (actionable band), 🔻 when favored ≥ 99% (effectively resolved, noise).
  New `Sig` column added to all three market/event selection tables: event list,
  single-market list, and sibling sub-table.
- `main.py` — `_analyzed_market_ids()`: loads history once per render, returns
  set of non-legacy market IDs. New `Seen` column (cyan `●`) in all three
  selection tables. Event-level table shows `●` if _any_ sibling market has
  been analyzed. Failure is non-fatal (logs debug, returns empty set).
- `main.py` — `history stats` overview panel now shows **council-level lean
  accuracy** across all resolved entries (same formula as list's Lean✓ column:
  `final_score` with 0.45–0.55 dead band). Also shows explicit `(M resolved,
K non-legacy)` counts so it's clear what each downstream stat covers.
  Per-agent Lean % (per-verdict) unchanged and still shown separately.

### Session 17 additions

- `models.py` — `CouncilResult.briefing: BriefingDocument | None` field added so
  the full briefing handed to the council is persisted with each result.
- `history.py` — full rewrite. Each entry is now `{run_id, timestamp, legacy,
result, resolution, analysis}`. `result` is the entire CouncilResult.
  Legacy flat-schema entries are migrated in-memory by `_migrate_legacy_entry()`
  and tagged `legacy: True` so analytics can exclude them. New helpers:
  `iter_unresolved(past_end_only=False)`, `update_resolution(run_id, ...)`,
  `update_analysis(run_id, ...)`, `_rewrite_all()` (atomic tmp+rename).
- `resolution.py` — **NEW.** `fetch_resolution(market_id) -> Resolution | None`
  hits `/markets/{id}`, parses `closed` + `outcomePrices` to determine winner.
  Returns None for still-open markets, dict with `winning_side` (YES/NO/None),
  `final_yes_price`, `resolved_at`, `checked_at`, `source`.
- `analytics.py` — **NEW.** Pure functions over migrated history entries:
  - `classify_outcome(entry)` → correct_buy / wrong_buy / missed_opportunity /
    correct_pass / unresolved / voided. Missed opportunity = PASS where
    council favored the winning side with prob ≥ 0.65 (`MISSED_OPPORTUNITY_PROB_FLOOR`).
  - `agent_accuracy(entries)` → per-agent dict with hit_rate, weighted_hit_rate
    (weighted by chosen-side confidence), mean_brier, high_conf_hits/misses.
  - `signal_postmortem(entry)` → structured dict (kind, summary, agents_with_winner,
    agents_against_winner, median_prob, coin_flip flag).
  - `compute_and_persist_postmortem(entry)` writes the analysis back to history
    via `update_analysis()` (idempotent).
- `main.py` — `pqi history` is now a Typer sub-app. Bare `pqi history` shows
  help (no_args_is_help=True). Sub-commands:
  - `pqi history list` — overview table. Columns: Mkt Y/N + Cncl Y/N (both
    sides shown), Lean (council's directional read with 0.45–0.55 dead band),
    Lean✓ (did the lean match the winner — independent of recommendation
    gates), vs Mkt (↕ when council and market lean opposite directions),
    Rec, Conf, Outcome glyph, Won side, Verdict tagline. Spawns auto-sync
    subprocess. The Lean/Lean✓/vs Mkt trio surfaces cases where the council's
    raw read was right but recommendation gates blocked the bet (or vice
    versa).
  - `pqi history show <#|run_id>` — header + recommendation + per-agent verdict
    table (with hit/✗ + Brier when resolved) + postmortem panel + briefing recap.
    First call also persists the postmortem into the entry.
  - `pqi history sync [--auto]` — fetches resolutions for unresolved entries.
    `--auto` filters to entries past their `end_date` and suppresses output;
    used by the background subprocess spawned from `list`/`stats`.
  - `pqi history stats` — overview counter + per-agent leaderboard sorted by
    weighted hit rate. Columns: n, Hit %, Wtd Hit %, Lean % (directional read
    accuracy with 0.45–0.55 dead band, mirrors the list view's Lean✓), Brier,
    HiConf %, HiConf ✓/✗. Footer row shows micro-averaged totals across all
    agents (Wtd Hit % is macro-averaged since raw weights aren't exposed at
    this layer).
- `_run_analysis()` — accepts optional `shared_briefing: BriefingDocument | None`.
  When provided (sibling-analysis loop), the synthesizer step is skipped and the
  briefing's market is rebound via `model_copy()` so the council still sees the
  correct resolution criteria. Also now attaches `briefing` to the returned
  CouncilResult.
- Sibling-analysis loop in `analyze` passes `result.briefing` to subsequent
  sibling runs — one Tavily call per event, separate council per market.

### End-to-end verified (session 17)

- `pqi analyze --top --no-cache` runs full pipeline, persists 7 verdicts +
  briefing in new history schema.
- `pqi history sync` fetched 27 resolutions from 31 entries.
- `pqi history list` shows outcome glyphs and verdict taglines for legacy +
  new entries.
- `pqi history show <#>` renders header / recommendation / per-agent table /
  postmortem / briefing recap panels correctly.
- `pqi history stats` aggregates correctly (5 correct buys / 1 wrong / 3
  missed / 17 correct pass / 4 unresolved / 1 voided in test data). Per-agent
  table is empty until non-legacy resolved entries accumulate.

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
  min_tradeable_price, min_council_probability.
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
- `ingestion/synthesizer.py` — `build_briefing()`: LLM-driven search query generation.
  **Session 17: Search Architect refactor.** Added LLM "Search Architect" step that
  analyzes MarketData (question, category, timing, prices) and designs optimal Tavily
  queries with full parameter tuning: `topic` (news/general/finance), `time_range`
  (day/week/month), `include_domains` (category-specific authoritative sources),
  `exclude_domains`, `search_depth`. Internal `TavilyQuery` and `SearchPlan` Pydantic
  models validate LLM output. Falls back to hardcoded templates (`_fallback_queries()`)
  on LLM failure. Switched from sync `TavilyClient` + sequential `asyncio.to_thread()`
  to `AsyncTavilyClient` + `asyncio.gather()` for parallel search execution.
  `_extract_entities()` preserved for fallback path. Snippet cap configurable (default 5000).
  LLM synthesis extracts `side_yes_case`, `side_no_case`, `key_context`.
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
- `history.py` — **NEW (session 14).** `append_result()` upserts into
  `~/.pqi/history.jsonl` keyed on `market.id` (NOT event.id, so sibling
  markets under the same parent event stay distinct). If an entry for the
  same market already exists, it is fully overwritten: fresh result +
  timestamp, and `resolution`/`analysis` are cleared (they're derived from
  the now-stale inputs and will be re-synced by the next `history sync`).
  `run_id` is preserved across re-runs so stable references survive. Only
  non-legacy entries participate in upsert matching. `load_history()` reads
  all entries and migrates legacy rows in-memory.
- `cache.py` — **NEW (session 14).** SQLite cache via `aiosqlite`. Table:
  `council_cache(market_id PK, yes_price, result_json, created_at)`.
  `get_cached(market_id, current_yes_price)` returns cached CouncilResult if price
  delta < `CACHE_PRICE_DELTA` (default 2%). `store_result()` upserts.
  DB at `~/.pqi/cache.db` (configurable via `CACHE_DB_PATH`).
- `main.py` — Typer CLI: `analyze` + `categories` + `history` commands.
  Analyze flags: `--category/-c`, `--top-n/-n`, `--top/-t`, `--starts-within/-s`,
  `--resolve-within/-r`, `--live/-l`, `--min-volume/-v`, `--no-cache`.
  `analyze` checks cache before running council; stores result after.
  `--no-cache` forces a fresh council run. Result appended to history after every run.
  PASS recommendation now shows reason line (e.g. "Edge too small...").
  Sibling market discovery (session 14): after market selection, fetches parent
  event via `fetch_event_markets()`, shows all sibling markets, offers to analyze
  them too. Useful for BO3 events with map 1/map 2/moneyline sub-markets.
  Market/event selection tables include `Sig` column (⭐ actionable band, 🔻
  resolved noise) and `Seen` column (cyan ● if any market in the event has a
  non-legacy history entry). `_signal_glyph()` and `_analyzed_market_ids()` helpers.
  History list flags (session 18): `--all/-a` (opt-in, shows all entries 1..N),
  `--outcome` (filter by outcome class with aliases), `--rec` (filter by
  recommendation type), `--conf` (filter by confidence level). Filters apply
  after absolute numbering so `#` IDs are stable. History stats (session 18):
  overview panel shows council-level lean accuracy (all resolved entries,
  dead band 0.45–0.55) alongside the existing per-agent leaderboard.
- `market/polymarket.py` — **Refactored session 16.** Architecture:
  `fetch_markets()` → `_fetch_and_filter()` → `_build_api_params()` +
  `_parse_events_to_markets()` + `_apply_client_filters()`.
  Eliminated duplicated `_fetch_markets_general` / `_fetch_markets_by_time_window`
  (were ~25 lines of shared code). Single fetch path for all query types.
  `_ParsedMarket` dataclass carries event-level `live`/`ended` through pipeline.
  **Server-side volume filtering**: `volume_min` on /events, `volume_num_min` on /markets.
  **API live/ended fields**: uses `event.live` for `--live` flag (not `event_start_time < now`),
  filters out `event.ended=True` events. Confirmed populated on list endpoint for sports/esports.
  **Fetch limit scaling**: `limit * 5` when client-side filters active (was `limit * 2`,
  missed low-volume live events at rank 47+).
  `resolve_category()`: three-pass with parallel optimization (untouched).
  `fetch_event_markets()`: reuses `_parse_events_to_markets()` for shared parsing.
  `--live/-l`, `--starts-within/-s`, `--resolve-within/-r` all composable.
  `MarketData.event_start_time` parsed from `eventStartTime` field.
  Market list table shows "Starts" column when event_start_time is available.

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
- **Council quorum gate:** At least `MIN_COUNCIL_VERDICTS` (default 5) of 7 agents
  must return valid verdicts. Below this the analysis aborts. Agents retry up to
  `AGENT_MAX_RETRIES` times with exponential backoff (cap 30s) before being dropped.
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

Session 19: gate retrospective analysis and Tier 1 optimization.
- MIN_EXPECTED_ROI default changed 0.15 → 0.10 in config.py + .env.example
- Full retrospective report: docs/retrospectives/eval_tier1_roi_2026_04.md
- Bear agent audit: 38% hit rate, 81% NO-lean bias, 43% loudest-wrong-voice.
  4 remediation options documented but not implemented.

## Remaining ideas / future work

### 1. Bear prompt engineering (HIGH PRIORITY from session 19 audit)
The Bear has the worst accuracy of all 7 agents (38% hit rate, 0.302 Brier).
It leans NO 81% of the time and was the sole cause of 2 missed opportunities
(#82 MANA, #87 T1 Academy) by dragging mean confidence below the 67% gate.
See docs/retrospectives/eval_tier1_roi_2026_04.md Section 6d for 4 options.
Recommended: soften "aggressively skeptical" language + add confidence floor
for data-poor underdogs.

### 2. Tier 2 gate evaluation (DEFERRED)
Lowering CONFIDENCE_HIGH_THRESHOLD 0.67 → 0.65 would recover 4 wins at cost
of 2 new losses (net +2). Wait for 100+ non-legacy entries for statistical
confidence. See retrospective Section 5.

### 3. Cache TTL / expiry
Add time-based expiry to cached council results (e.g. 24h max age). Currently
cache only invalidates on price movement > 2%.

### 4. /public-search integration
Use `/public-search?q=TEXT` as supplementary fetch for ghost events that don't
appear in /events list queries.

### 5. Multi-market batch analysis
Allow `pqi analyze --top 3` to analyze top N markets sequentially.

### 6. Per-category accuracy slicing
`pqi history stats --category esports` would let us see which agents are
strong in which domains.

### 7. Postmortem persistence on `list`
`pqi history list` currently calls `signal_postmortem()` for each row but does
not persist (only `show` does). Consider eager persistence.

### 8. Auto-sync race condition
Two consecutive `pqi history list` calls in <2s could spawn two background
syncs. Low impact (atomic rename), but a lockfile would be cleaner.

## Instructions
- Pick the next priority with the user before starting
- Read the relevant existing files before writing any new code
- Update ## Current State and ## Next Session Prompt at end of session
```
