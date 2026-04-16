# Known Issues & Assumptions

Runtime quirks, API limitations, and design decisions to be aware of.
Referenced from CLAUDE.md.

---

## Gamma API

- **Ghost events (confirmed 2026-04-03):** Some events exist (fetchable
  by direct ID: `/events/{id}`) but are invisible to ALL list queries — not returned
  by `/events?series_id=...`, `/events?end_date_min=...&end_date_max=...`,
  `/events?tag=...`, or unfiltered `/events`. Their markets are also absent from
  `/markets` list queries. Example: event 273395 (Valorant TH vs NAVI BO3) had
  `active=True, closed=False` with valid markets but was invisible to list endpoints.
  This is a Gamma API indexing bug. No workaround exists on our end — markets in
  ghost events simply won't appear in results. May be related to mid-match settlement
  transitions (de-indexed when some sub-markets start resolving).
- **Order params (confirmed 2026-04):** Only `order=volume` and
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
- Field notes: `outcomePrices` is JSON string `'["0.73", "0.27"]'`;
  `volumeNum` is float USD; must pass `closed=false` + `active=true`.

## PandaScore API (`pqi/ingestion/pandascore.py`, `pqi/ingestion/match_resolver.py`)

- **Auth:** Bearer token via `PANDASCORE_API_KEY`. Free tier is
  1000 req/hr — comfortably above PQI's per-analyze footprint
  (~5 requests). Empty key disables resolver; `PANDASCORE_ENABLED=false`
  also bypasses without needing to clear the key.
- **Game coverage:** `GAME_MAPPING` in `match_resolver.py` is the
  closed list of categories the resolver knows about (currently
  `counter-strike` → `cs-go`, `valorant`, `league-of-legends`,
  `dota-2`, `rainbow-six-siege`). Unknown categories silently
  return `structured_match=None` — pipeline continues, council
  briefing falls back to event-time-only disambiguation. Add a
  game by extending the dict.
- **Symmetric fuzzy scoring (intentional):** `_score_candidate`
  uses `min(token_set_ratio_a, token_set_ratio_b)` not `max` or
  sum. A perfect match on one side cannot paper over a wrong
  match on the other. Default threshold `PANDASCORE_FUZZY_THRESHOLD=75`
  is conservative — tune per category if you see false negatives.
- **Status normalisation:** PandaScore exposes `not_started`,
  `running`, `finished`, `canceled`, `postponed`. `_normalise_status`
  collapses these to the three values the rest of the pipeline
  expects: `scheduled` (covers not_started + postponed), `running`,
  `finished` (covers finished + canceled). `canceled` mapped to
  `finished` is intentional — cache should refresh once the match
  is no longer pending, regardless of whether it actually played.
- **Search window:** `PANDASCORE_WINDOW_HOURS=48` is centered on
  `event_start_time` when present, else `now()`. Wider windows
  catch late-rescheduled matches but increase the false-match
  surface.
- **Non-fatal contract:** every resolver path gracefully returns
  `[]` / `None` on HTTP 4xx/5xx, missing fields, parse errors.
  Pipeline must never see a raised exception from the resolver —
  if it does, `_run_analysis()` catches and logs at WARN, and the
  briefing is built with `structured_match=None`. Don't add
  exceptions or hard gates here.
- **Cache invalidation:** `cache.py` invalidates when the
  PandaScore status differs from the cached row. Status
  transitions (scheduled → running → finished) are the most
  important events in a match's life cycle and price often barely
  moves through them. NULL == NULL works correctly for
  non-esports markets that never invoke the resolver.

## CLOB API

- **Response format:** `{"history": [{t, p}]}` (dict with `history` key, not a
  bare list). `t` is unix timestamp (int), `p` is float (not string).
- Very short-lived markets (5-min crypto windows) may return only 1 data point —
  handled by the <2 guard.
- `clobTokenIds` on the Gamma API is a JSON string (needs `json.loads()`),
  index 0 = YES token, index 1 = NO token.
- YES + NO CLOB prices don't sum to 1.0 (spread/vig) — both sides fetched independently.
- **Price history is informational only:** The weighted average and trend data
  enrich the briefing for agents. Scoring gates (ROI, price floor/ceiling) still
  use the real `yes_price` snapshot since that's the actual tradeable price.

## Scoring & Council

- **Volume floor behavior:** General queries use `MIN_VOLUME_USD` ($10K default).
  Short time windows (≤ `SHORT_WINDOW_HOURS`, default 4h) use
  `MIN_VOLUME_SHORT_WINDOW_USD` ($500 default). `--min-volume 0` disables all filtering.
- **Min-tradeable-price gate:** Recommended side must cost >= MIN_TRADEABLE_PRICE
  (default 5c). Prevents "BUY NO at $0.01" on near-over events.
- **Council quorum gate:** At least `MIN_COUNCIL_VERDICTS` (default 5) of 7 agents
  must return valid verdicts. Below this the analysis aborts. Agents retry up to
  `AGENT_MAX_RETRIES` times with exponential backoff (cap 30s) before being dropped.
- **Two-sided confidence:** Agents output separate confidence_yes and confidence_no.
  Parser falls back to legacy confidence_level (both sides equal) if agent doesn't
  produce the new fields. Scoring evaluates BUY YES from yes_edge + yes-side
  confidence, BUY NO from no_edge + no-side confidence independently.
- **LOCAL_LLM_CONCURRENCY:** Only applies to Ollama backend. Anthropic calls skip
  the semaphore entirely. Default 1 for consumer GPUs.
- Council JSON parser: Pass 1 direct json.loads(), Pass 2 strip control chars.

### Asymmetry of two-sided confidence (open question, not a bug)

Since agents return `confidence_yes` and `confidence_no` independently, a
single verdict can land at e.g. `p_yes=26% with conf_yes=72%` and
`p_no=74% with conf_no=35%`. Mathematically `p_yes + p_no = 1.0`, but the
*confidence* gap is real signal: the high-confidence side is a tight
cluster of evidence, the low-confidence side is noise.

Today we feed both into the scoring formula symmetrically (independent
weighted averages per side, recommendation picks the higher-confidence
side via `confidence_level`). That is defensible but probably leaves
information on the table — a high-confidence "Team A almost certainly
loses" verdict is a much stronger signal than the same agent's
low-confidence "Team B probably wins" mirror, even though the two are
algebraically equivalent.

Candidate techniques to evaluate (see `docs/roadmap.md` Theme A · Data
asymmetry of confidence):

- **Credibility-weighted probability (Bayesian shrinkage)** — anchor on
  the higher-confidence side and shrink the other toward it:
  `p_adj = p_high * c_high + p_low * (1 - c_high)`.
- **Log-odds weighted by confidence** — `Σ c_side * ln(p / (1-p))` per
  side; sign of the sum picks the lean, magnitude is a "strength of
  signal" score that naturally lets the high-confidence voice dominate.
- **Dempster-Shafer style explicit ignorance** — model the
  `1 - max(c_yes, c_no)` slice as "I don't know" rather than forcing it
  into one of the two sides.

Defer until we have more resolved samples to backtest against — the
roadmap item tracks this.

---

## Environment

- `.env` uses `CLAUDE_API_KEY` instead of `ANTHROPIC_API_KEY`. `config.py` accepts
  either (`ANTHROPIC_API_KEY` checked first, `CLAUDE_API_KEY` as fallback).
- `TAVILY_API_KEY` is validated as required at import time.
- LLM_BACKEND: `anthropic` (tested, working with claude-haiku-4-5-20251001)
- Ollama: installed, llama3.1:8b available as fallback
- LOCAL_LLM_CONCURRENCY: 1 (only affects Ollama; Anthropic runs fully parallel)

## Persisted state files (load-bearing)

- `~/.pqi/history.jsonl` — full-fidelity run history. Source of truth for
  per-agent accuracy, outcome classification, and `PerceivedStats`. Deleting
  it wipes all calibration inputs. `.bak` sibling is created by legacy
  migration; safe to keep.
- `~/.pqi/cache.db` — SQLite council cache. Keyed by `market_id`,
  invalidated on >2% price delta **or** PandaScore status change
  (session 28). Schema gained a `structured_status TEXT` column
  with a defensive `ALTER TABLE` migration so existing databases
  upgrade in place on next open. Deleting it only costs
  re-analysis cost, not correctness.
- **`~/.pqi/stats.json` — EV gate input.** Persisted `PerceivedStats`
  snapshot (true_win_rate, sample_size, kelly_fraction_at_p75, below_min_samples).
  Refreshed by `history sync` / `history stats` via `stats.recompute_from_history`.
  **Deleting it silently disables the EV gate** until the next sync/stats
  call rewrites it — `scoring.py` tolerates a missing file (returns `None`,
  gate stays dormant) so no crash, just a behavioural regression to the
  pre-session-22 system. If you want a hard reset of the gate, delete this
  file AND run `pqi history stats` to recompute from current history.
- **`~/.pqi/agent_weights.json` — dynamic per-agent scoring weights
  (session 25).** Payload: `computed_at`, `min_samples`, `shrink`,
  `council_avg_lean_rate`, `weights: {agent_name: float}`, `sources:
  {agent_name: {hits, n, lean_rate}}`. Refreshed by `history sync` /
  `history stats` via `agent_weights.recompute_from_history`. Overrides
  static `RULES_LAWYER_WEIGHT` / `BEAR_WEIGHT` when
  `AGENT_WEIGHTS_ENABLED=true` and the file exists (precedence mode α).
  Deleting it reverts scoring to the static map on the next run — no
  crash. Agents below `MIN_AGENT_WEIGHT_SAMPLES` (default 30) are
  clamped to 1.0 inside `compute_weights`, so the file is always
  complete for the 7 agents even at low sample counts. Set
  `AGENT_WEIGHTS_ENABLED=false` to force-disable without deleting.
- Atomic writes: all four writers use the `tmp + os.replace()` pattern,
  so crashes mid-write leave the prior file intact. No lockfile yet —
  concurrent writers are a B.2 / C.6 concern (see roadmap).
