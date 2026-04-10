# Session Log — Changelog & Implementation Details

Per-session change log with detailed diffs. Referenced from CLAUDE.md.

---

## Session 21 — CLOB price history enrichment (two-sided)

- `models.py` — **New models:** `PricePoint` (timestamp + price), `SharpMove`
  (timestamp + delta_pp), `SidePriceHistory` (per-side computed trend metrics:
  token_id, data_points, weighted_avg, change_1w_pp, change_24h_pp,
  volatility_24h, direction, sharp_moves), `PriceHistory` (wrapper:
  `yes: SidePriceHistory`, `no: SidePriceHistory`, `fetched_at: datetime`).
  YES + NO CLOB prices are independent (don't sum to 1.0 due to spread/vig).
  `MarketData` gains `clob_token_ids: list[str]` (default empty).
  `BriefingDocument` gains `price_history: PriceHistory | None` (default None).
- `market/clob.py` — **NEW.** CLOB API client for two-sided price history.
  `fetch_price_history(market)` fetches YES and NO token prices in parallel via
  `asyncio.gather()`. Requires `len(clob_token_ids) >= 2`. Per-side fetch via
  `_fetch_side(market_id, token_id, label)`. `_compute_side_metrics()` pure
  function: EMA weighted average (half-life 2 days), direction via linear
  regression slope (+-2pp threshold), volatility, sharp moves (>5pp, last 3).
  Non-fatal: returns None if either side fails.
- `market/polymarket.py` — `_parse_market()` now extracts `clobTokenIds` JSON
  string field from Gamma API response into `MarketData.clob_token_ids`.
- `config.py` — New section: Polymarket CLOB API. `CLOB_BASE_URL` code constant.
  Tunables: `PRICE_HISTORY_INTERVAL` (default `1w`), `PRICE_HISTORY_FIDELITY`
  (default `60` minutes).
- `.env.example` — added CLOB price history section.
- `agents/council.py` — `_format_briefing()` renders conditional two-sided
  `MARKET PRICE HISTORY` block between EVENT TIMING and VOLUME.
- `main.py` — `_run_analysis()` fetches CLOB price history between briefing
  build and council summon. `display_council_result()` shows `7d Trend YES`
  and `7d Trend NO` lines.
- `requirements.txt` — added `py-clob-client>=0.1`.

## Session 20 — Bear prompt rewrite + retry logic

- `agents/personas.py` — Bear prompt rewrite. Removed structural-bias phrases.
  Added evidence-binding guards and hard confidence floor (<=30 with <2
  underdog data points). Root cause: audit showed 38% hit rate, 81% NO-lean.
- `config.py` — Dead code removed (`CONSENSUS_YES_FLOOR`, `CONSENSUS_NO_CEILING`).
  New vars: `MIN_COUNCIL_VERDICTS`, `AGENT_MAX_RETRIES`, `BEAR_WEIGHT`.
- `agents/council.py` — Council quorum gate + agent retry with exponential backoff.
- `scoring.py` — Bear weight wired into `agent_weights`.
- `models.py` + `main.py` — Signal type rename: STRONG->HIGH VALUE, CONFIRMED->
  MODERATE VALUE, CAUTIOUS->LOW MARGIN.
- `main.py` — UX color overhaul (all BUY green, signal label colors).

## Session 19 — ROI threshold + retrospective

- `config.py` — `MIN_EXPECTED_ROI` default 0.15 -> 0.10. Widens LOW MARGIN band.
- `docs/retrospectives/eval_tier1_roi_2026_04.md` — Full retrospective report.
- Bear audit findings documented (not acted on until session 20).

## Session 18 — History filtering + signal glyphs

- `main.py` — `history list` filter flags: `--outcome`, `--rec`, `--conf`.
  `_signal_glyph()` + `Sig` column. `_analyzed_market_ids()` + `Seen` column.
  `history stats` council-level lean accuracy.

## Session 17 — History validation system

- `models.py` — `CouncilResult.briefing` field.
- `history.py` — Full rewrite: full-fidelity persistence, legacy migration.
- `resolution.py` — NEW. Fetch resolution from `/markets/{id}`.
- `analytics.py` — NEW. `classify_outcome()`, `agent_accuracy()`,
  `signal_postmortem()`.
- `main.py` — `pqi history` sub-app: list/show/sync/stats commands.
- `_run_analysis()` — shared briefing support for sibling markets.

---

## Implemented & tested (cumulative)

- `agents/personas.py` — all 7 system prompts, AGENT_PERSONAS dict, build_prompt().
  SHARED_PREAMBLE rules: TWO-SIDED MARKET, TWO-SIDED CONFIDENCE, DUAL-PERSPECTIVE
  ANALYSIS, TIME-TO-REVERSAL. Bull/Bear are dynamic (price-aware, favored/underdog).
  Contrarian prioritizes competitively plausible disruptions.
  All 7 output formats use `confidence_yes` + `confidence_no`.
- `config.py` — reads ALL tunables via `os.getenv(KEY, default)`. True code constants
  only: `GAMMA_BASE_URL`, `CLOB_BASE_URL`, `VALID_BACKENDS`.
- `models.py` — all Pydantic v2 models. Two-sided confidence, probability-first
  recommendation logic, ROI-banded signal types.
- `llm/client.py` — Ollama + Anthropic backends, `async def chat()`.
- `ingestion/synthesizer.py` — LLM Search Architect + parallel Tavily execution.
- `agents/council.py` — 7 agents via `asyncio.gather()`. Quorum gate. Retry with
  exponential backoff. Rules Lawyer injection. Side context for Bull/Bear.
- `scoring.py` — Two-sided scoring. Agent weights (Rules Lawyer, Bear).
- `history.py` — Full-fidelity persistence, legacy migration, upsert by market_id.
- `cache.py` — SQLite cache, price-delta invalidation.
- `resolution.py` — Fetch resolution status from Gamma API.
- `analytics.py` — Outcome classification, per-agent accuracy, postmortem.
- `market/polymarket.py` — Unified fetch path, server-side volume filtering,
  category resolution (three-pass), event enrichment.
- `market/clob.py` — Two-sided CLOB price history client.
- `main.py` — Typer CLI: analyze + categories + history (list/show/sync/stats).
  Sibling market discovery, shared briefings, signal glyphs, history filtering.
