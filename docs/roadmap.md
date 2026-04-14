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
- **Dynamic EV gate + Kelly Criterion** (session 22) — `PerceivedTrueWinRate`
  persisted to `~/.pqi/stats.json`, used as the final scoring gate
  (`side_price ≥ win_rate` → PASS "Big Drawdown Risk (-EV)"). Kelly stake
  surfaced in recommendation panel and `history stats` overview. Gate gated
  by `MIN_WIN_RATE_SAMPLES` (default 20) so cold-start runs are unaffected.

---

## Future Work

### 1. Tier 2 gate evaluation (DEFERRED)

Lowering `CONFIDENCE_HIGH_THRESHOLD` 0.67 → 0.65 would recover 4 wins at cost
of 2 new losses (net +2). Wait for 100+ non-legacy entries for statistical
confidence. See `docs/retrospectives/eval_tier1_roi_2026_04.md` Section 5.

### 2. Cache TTL / expiry

Add time-based expiry to cached council results (e.g. 24h max age). Currently
cache only invalidates on price movement > 2%.

### 3. /public-search integration

Use `/public-search?q=TEXT` as supplementary fetch for ghost events that don't
appear in /events list queries.

### 4. Multi-market batch analysis

Allow `pqi analyze --top 3` to analyze top N markets sequentially.

### 5. Per-category accuracy slicing

`pqi history stats --category esports` would let us see which agents are
strong in which domains.

### 6. Postmortem persistence on `list`

`pqi history list` currently calls `signal_postmortem()` for each row but does
not persist (only `show` does). Consider eager persistence.

### 7. Auto-sync race condition

Two consecutive `pqi history list` calls in <2s could spawn two background
syncs. Low impact (atomic rename), but a lockfile would be cleaner.

### 8. Price history → scoring integration (DEFERRED)

Once enough data accumulates to validate that trend-aware council verdicts
improve accuracy, consider using weighted_avg or trend direction as a soft
input to scoring gates (e.g. tighter ROI threshold when trend opposes
the recommendation).

### 9. CLOB trading integration (future phase)

`py-clob-client` SDK is installed. Use it for order placement, position
management, and portfolio tracking when the project moves from Advisor
to Auto-trader.

### 10. Bear accuracy monitoring (ongoing from session 20)

After accumulating 15-20 new resolved entries, run `pqi history stats` and
check Bear agent metrics:

- NO-lean frequency: should drop from 81% toward 50-65%
- Hit rate: should rise from 38% toward 50%+
- Brier score: should improve from 0.302 toward 0.24-0.27
- High-conf miss rate: should drop from 70%

If Bear hit rate is still below 50%, lower `BEAR_WEIGHT` to 0.7 in `.env`.

### 11. EV gate tuning (follow-up to session 22)

Monitor "Big Drawdown Risk" PASSes as new resolutions land. Things to watch:

- Is `MIN_WIN_RATE_SAMPLES=20` the right activation floor? Too low → gate
  whipsaws on small samples; too high → gate sits dormant too long.
- Does the win rate stabilize, drift, or split by category? If it splits,
  per-category win rates (paired with #5) may be a better ceiling.
- Should Kelly feed a future auto-sizing layer when #9 lands?
