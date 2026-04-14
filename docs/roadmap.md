# Roadmap & Future Work

Referenced from CLAUDE.md.

---

## Phase 1 (MVP) — COMPLETE

All core pipeline modules shipped: `polymarket.py`, `synthesizer.py`,
`personas.py`, `client.py`, `council.py`, `scoring.py`, `main.py` (CLI).

## Phase 2 (Post-MVP) — COMPLETE

- SQLite cache with price-delta invalidation
- Category filtering (`--category`, `pqi categories`)
- Two-sided confidence scoring + Rules Lawyer refocus
- Richer briefing (5 Tavily queries, 5000-char cap, EVENT TIMING block)
- Anthropic backend (`claude-haiku-4-5-20251001`) + per-backend concurrency
- Rules Lawyer & Bear weight knobs
- History validation: full-fidelity persistence, resolution sync, per-agent
  accuracy, postmortem analysis, `pqi history` sub-app
- Shared briefing for sibling markets
- CLOB price history enrichment (two-sided trends in briefings)
- **Dynamic EV gate + Kelly Criterion** (session 22) —
  `PerceivedTrueWinRate` persisted to `~/.pqi/stats.json`, used as the
  final scoring gate (`side_price ≥ win_rate` → PASS "Big Drawdown Risk
  (-EV)"). Kelly stake surfaced in recommendation panel and `history
  stats` overview. Gated by `MIN_WIN_RATE_SAMPLES` (default 20).
- **WR-based edges + run-time snapshots** (session 23) — `edge` /
  `no_edge` repurposed as `(PerceivedTrueWinRate − side_price)` and
  snapshotted on every result alongside `perceived_win_rate_at_run`,
  `kelly_stake_full`, `kelly_stake_scaled`, `kelly_multiplier`. Surfaced
  in `display_council_result` and `history show <#>`.

---

## Future Plans

Future work is organized into three themes. Each item lists its
reference files and any blockers documented in `known-issues.md`. Items
inherited from earlier flat-list versions of this roadmap are flagged
inline so cross-references in old notes still work.

| Theme                                         | Scope                                                                  |
| --------------------------------------------- | ---------------------------------------------------------------------- |
| **A. Modeling & Calibration**                 | Improve council inference, weighting, and gate thresholds              |
| **B. Continuous-Run TUI + Trading (Phase 3)** | Move from one-shot CLI advisor toward a streaming TUI and auto-trader  |
| **C. Operational Tooling**                    | Smaller quality-of-life and pipeline robustness items                  |

---

## Theme A — Modeling & Calibration

Reference files:

| File                                            | When to read                                                  |
| ----------------------------------------------- | ------------------------------------------------------------- |
| `pqi/scoring.py`, `pqi/models.py`               | Anything touching the recommendation gates or edge semantics  |
| `pqi/agents/personas.py`                        | Prompt-level changes (e.g. asking for separate explanations)  |
| `pqi/stats.py`                                  | EV gate and Kelly inputs                                      |
| `docs/known-issues.md` (Asymmetry section)      | Background on the asymmetric-confidence question              |
| `docs/retrospectives/eval_tier1_roi_2026_04.md` | Bear agent tuning + missed-opportunity analysis               |

### A.1 Data asymmetry of confidence (NEW)

Today the scoring formula treats `confidence_yes` and `confidence_no`
symmetrically — each side gets its own weighted average and the
recommendation lands on whichever side won. But when an agent is e.g.
72% confident in YES and only 35% confident in NO, the high-confidence
side carries far more signal than the algebraic mirror; we should
consider techniques that explicitly anchor on the higher-confidence
voice. Candidates documented in `docs/known-issues.md` →
"Asymmetry of two-sided confidence":

- Credibility-weighted shrinkage onto the higher-confidence side.
- Log-odds weighted by confidence.
- Dempster-Shafer style explicit "ignorance" mass for
  `1 - max(c_yes, c_no)`.

**Plan:**

1. Add a parallel `inference_score` (computed but not gated on) to
   `CouncilResult` so we can backtest each candidate against history.
2. After ~50 more resolved entries, compare each candidate's
   sign/magnitude against the actual winners in `pqi history list`.
3. Promote the best performer into the gate stack (likely as a
   tiebreaker between BUY and PASS, not a replacement).

Blockers: none. Pure offline modeling work.

### A.2 Briefing data-point weight audit (NEW)

The `BriefingDocument` mixes Tavily news, key facts, side cases, key
context, and a `PriceHistory` block (CLOB trends, sharp moves,
volatility). We don't currently know how each block influences the
council's verdicts — and intuitively some blocks should weigh less:

- Price-history points captured **after** `event_start_time` are noisier
  (live in-game odds swing fast and often resolve back).
- Sharp single-hour moves near resolution may reflect liquidity events,
  not new information.
- Tavily snippets older than the matchday probably restate already-priced
  context.

**Plan:**

1. Instrumentation pass: log per-section presence/absence and let the
   council scoring loop record a counterfactual run with each section
   blanked (cheap on Ollama, expensive on Anthropic — start local).
2. Compare per-section ablation deltas on the same market across
   resolved history; surface the dominant influencers.
3. Add weight knobs to `pqi/ingestion/synthesizer.py` and
   `pqi/market/clob.py`:
   - Time-decay factor for price points after `event_start_time`
     (e.g. exponential half-life of 30 min once the match starts).
   - Per-section truncation budgets in the briefing assembly.
4. Re-run ablation to confirm the weights move the verdict in the
   intended direction.

Blockers:

- `event_start_time` is sometimes `None` from the Gamma API
  (`docs/known-issues.md` → "event_start_time query params") — degrade
  to "no time-decay" rather than skipping the section entirely.

### A.3 Tier 2 gate evaluation (DEFERRED — was item #1)

Lowering `CONFIDENCE_HIGH_THRESHOLD` 0.67 → 0.65 would recover 4 wins
at cost of 2 new losses (net +2). Wait for 100+ non-legacy entries for
statistical confidence. See
`docs/retrospectives/eval_tier1_roi_2026_04.md` Section 5.

### A.4 Bear accuracy monitoring (was item #10)

After 15-20 new resolved entries, run `pqi history stats` and check
Bear metrics:

- NO-lean frequency: should drop from 81% toward 50-65%
- Hit rate: should rise from 38% toward 50%+
- Brier score: should improve from 0.302 toward 0.24-0.27
- High-conf miss rate: should drop from 70%

If Bear hit rate is still below 50%, lower `BEAR_WEIGHT` to 0.7 in `.env`.

### A.5 EV gate tuning (was item #11)

Monitor "Big Drawdown Risk" PASSes as new resolutions land:

- Is `MIN_WIN_RATE_SAMPLES=20` the right activation floor?
- Does the win rate stabilize, drift, or split by category? If it splits,
  per-category win rates (paired with C.4) may be a better ceiling.
- Should Kelly feed a future auto-sizing layer when B.4 lands?

### A.6 Price history → scoring integration (DEFERRED — was item #8)

Once enough data accumulates to validate that trend-aware council
verdicts improve accuracy, consider using `weighted_avg` or trend
direction as a soft input to scoring gates (e.g. tighter ROI threshold
when trend opposes the recommendation). Likely subsumed by A.2 once
the briefing weight audit lands — re-evaluate then.

---

## Theme B — Continuous-Run TUI + Trading Automation (Phase 3)

This is the next major phase: move from a one-shot CLI advisor to a
persistent TUI that streams candidate events, with optional background
analysis workers and an opt-in auto-trader. The pieces stack: B.1
gives us the loop, B.2 gives us scale, B.3–B.6 add execution.

Reference files:

| File                                                  | When to read                                          |
| ----------------------------------------------------- | ----------------------------------------------------- |
| `pqi/main.py`                                         | CLI structure to extend with the TUI command          |
| `pqi/market/polymarket.py`                            | Event fetching, rate-limit handling                   |
| `pqi/llm/client.py`, `pqi/config.py`                  | Backend concurrency + Anthropic rate limits           |
| `docs/known-issues.md` (Gamma API, Environment)       | Ghost events, list-endpoint live/ended `None`, etc.   |
| `requirements.txt` (`py-clob-client` already present) | Trading SDK already installed but unused              |

Cross-cutting blockers from `known-issues.md`:

- **Ghost events** — some events exist by ID but never appear in list
  queries. A streaming poller will miss them. Mitigation: optional
  user-supplied event-ID watchlist (B.1 sub-task).
- **Live/ended `None` on /events list** — forces parallel
  `/events/{id}` enrichment. Each TUI tick costs N×1 enrichment calls;
  budget against `EVENT_ENRICHMENT_MAX` and Gamma's
  `GAMMA_RATE_LIMIT_DELAY`.
- **Anthropic rate limits** — parallel background analyzers can starve
  the foreground. Need a global token-bucket in `llm/client.py`, not
  just `LOCAL_LLM_CONCURRENCY` (Ollama-only today).

### B.1 Streaming TUI (NEW)

Persistent terminal app (likely `textual` or a hand-rolled `rich.live`
loop) that:

- Polls events at a configurable cadence (e.g. every 60–120 s) using
  the existing `polymarket.fetch_events_grouped()` filters.
- Surfaces at the top the events the existing scoring pre-filter would
  flag as ⭐ (favored side in `[0.55, PerceivedTrueWinRate]`) and the
  ones whose `event_start_time` is imminent.
- Shows a side-pane "watchlist" of explicit event IDs the user pinned.
- Exposes one-key actions: `a` = analyze selected event in foreground,
  `A` = enqueue background analysis (B.2), `b` = buy with current
  Kelly stake (B.5, gated behind auth and explicit confirm), `w` =
  watch the match orderbook (B.6).

Blockers / dependencies:

- Ghost events (above). Watchlist is the workaround.
- Gamma rate limit. Default poll cadence should respect
  `GAMMA_RATE_LIMIT_DELAY` and back off on 429.

### B.2 Background analysis workers with rate limiting (NEW)

When the user fires multiple analyses from the TUI (or opts into
auto-analyze for top-K events), each one runs in a separate process so
the TUI stays responsive. Constraints:

- **Anthropic shared budget** — every backend call goes through a
  process-shared token bucket (file-locked counter or a small Unix
  socket coordinator) so N parallel workers can't exceed
  `ANTHROPIC_RPM` / `ANTHROPIC_TPM`.
- **Tavily shared budget** — same pattern; free tier is 1k searches/mo
  shared across processes.
- **History upsert** — append happens via `history.append_result()`
  (atomic tmp+rename today). Two simultaneous writes need a lockfile
  in `~/.pqi/` rather than relying on rename atomicity alone — see C.6.
- **Result delivery** — workers write to `history.jsonl`; the TUI
  watches for file changes (or a lightweight notification socket) and
  refreshes the relevant rows.

Blockers: A global rate-limit coordinator is new infrastructure but
local-only — no external services involved.

### B.3 Polymarket account auth (subcomponent of B.5/B.6) (NEW)

Use `py-clob-client` (already in `requirements.txt`) to authenticate
with the user's Polymarket wallet for trade execution and order
inspection. Authentication assets (private key / API creds) live in
`.env` under fresh keys (`POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`,
`POLYMARKET_API_PASSPHRASE`, `POLYMARKET_FUNDER_ADDRESS`,
`POLYMARKET_PRIVATE_KEY`). Add `pqi auth status` to verify the keys
load and the SDK can fetch the user's balance — purely read-only first.

Blockers:

- Real money. Default to `--dry-run` for everything that calls a write
  endpoint until the user explicitly opts out per-command.
- Secret hygiene: `.env` is already gitignored; document loud warnings
  in README before this lands.

### B.4 CLOB trading integration (was item #9)

Wire `py-clob-client` for read endpoints first (positions, balances,
open orders, recent fills) so the TUI can display portfolio context.
Foundation for B.5 and B.6. Phase ordering:

1. Read-only client wrapper in `pqi/market/clob_trade.py` — never
   imported from the analysis pipeline.
2. `pqi positions` and `pqi balance` CLI sub-apps for verification.
3. Defer write endpoints to B.5.

Blockers: depends on B.3 auth.

### B.5 Kelly-sized buy command (NEW)

`pqi buy <market_id>` (or the TUI `b` action) places a CLOB order at
the current ask for the recommended side, sized to
`kelly_stake_scaled × bankroll`. Bankroll source = the wallet balance
returned by B.4. Safeguards:

- Refuse to fire if the latest cached/historical `CouncilResult` is
  not a BUY recommendation, the EV gate would now reject the trade, or
  Kelly stake is 0.
- Require `--confirm` (or a typed confirmation in the TUI) on every
  call — never silent.
- Honor `MIN_TRADEABLE_PRICE` and `MAX_TRADEABLE_PRICE` even if the
  cached result is older.
- Log every order to `~/.pqi/orders.jsonl` with the snapshot of the
  result and stats that produced it (mirrors history.jsonl format).

Blockers: depends on B.3 auth + B.4 read client.

### B.6 Match watcher with time-based stop-loss (NEW)

After a buy lands (B.5) on a multi-stage event (e.g. BO3 esports
match), spawn a background watcher that:

- Subscribes to the CLOB orderbook for the position's token
  (`py-clob-client` supports websockets).
- Monitors `event_start_time` and stage progression — for a BO3,
  estimate "third map starts" using the original event's child markets
  (Map 1 / Map 2 / Map 3 sub-markets often expose this implicitly).
- Fires a stop-loss sell when:
  - Time-based: a configurable threshold past a stage transition
    (e.g. "30 minutes into Map 3" or "matchend - 10 min").
  - Price-based: position drops below a configurable PnL floor.
- Records every action (poll, decision, order) in
  `~/.pqi/watcher.jsonl` for postmortem.

Blockers:

- Stage-transition detection is heuristic; rely on the existing sibling
  market price moves until a cleaner signal exists.
- Watcher process must survive TUI exit — use `nohup`-style detach or
  systemd-user units. Document the chosen approach when implementing.
- Real money risk. Stop-loss ceilings and time windows must be loud
  (warn in TUI, log to disk, refuse silently-bad config).

---

## Theme C — Operational Tooling

Small, mostly-independent improvements. Each can ship in isolation.

Reference files:

| File                                        | When to read                                  |
| ------------------------------------------- | --------------------------------------------- |
| `pqi/cache.py`                              | C.1 — TTL semantics                           |
| `pqi/market/polymarket.py`                  | C.2 — `/public-search` integration            |
| `pqi/main.py`                               | C.3, C.4 — CLI surface area                   |
| `pqi/analytics.py`                          | C.4, C.5 — slicing + postmortem persistence   |
| `pqi/history.py`                            | C.6 — concurrent-write lockfile               |

### C.1 Cache TTL / expiry (was item #2)

Add time-based expiry to cached council results (e.g. 24h max age).
Currently cache only invalidates on price movement > 2%.

### C.2 `/public-search` integration (was item #3)

Use `/public-search?q=TEXT` as supplementary fetch for ghost events
that don't appear in `/events` list queries. See known-issues.md
"Ghost events" — this is the closest available workaround.

### C.3 Multi-market batch analysis (was item #4)

Allow `pqi analyze --top 3` to analyze top N markets sequentially.
(Subsumed by B.2 once that lands; keep as a standalone interim
deliverable.)

### C.4 Per-category accuracy slicing (was item #5)

`pqi history stats --category esports` would let us see which agents
are strong in which domains. Feeds A.5 (per-category EV gate).

### C.5 Postmortem persistence on `list` (was item #6)

`pqi history list` currently calls `signal_postmortem()` for each row
but does not persist (only `show` does). Consider eager persistence.

### C.6 Auto-sync race condition + concurrent-write lockfile (was item #7)

Two consecutive `pqi history list` calls in <2 s could spawn two
background syncs. Low impact today (atomic rename), but B.2 turns this
into a real concurrency story — promote to "lockfile in `~/.pqi/`"
when implementing B.2.

---

## Cross-reference quick map

| If you want to work on…                | Start with…                                                                 |
| -------------------------------------- | --------------------------------------------------------------------------- |
| Smarter scoring                        | Theme A · A.1 (asymmetry), A.2 (briefing weights)                           |
| Continuous-run UX                      | Theme B · B.1 (TUI), then B.2 (parallel workers)                            |
| Real trading                           | Theme B · B.3 → B.4 → B.5 → B.6, in that order                              |
| Reducing wrong BUYs                    | Theme A · A.4 (Bear), A.5 (EV gate tuning)                                  |
| Recovering missed opportunities        | Theme A · A.3 (Tier 2 gate), and `pqi history list --all --outcome miss`    |
| Cache / pipeline fixes                 | Theme C — pick whichever item bites you first                               |
