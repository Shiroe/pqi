# Roadmap & Future Work

Referenced from CLAUDE.md.

---

## Phase 1 (MVP — COMPLETE)

- [x] `polymarket.py` — fetch and filter markets
- [x] `synthesizer.py` — Tavily search -> BriefingDocument
- [x] `personas.py` — all 7 system prompts
- [x] `client.py` — Ollama/Anthropic abstraction
- [x] `council.py` — parallel async agent calls + verdict parsing + OOM semaphore
- [x] `scoring.py` — weighted score + edge + recommendation
- [x] `main.py` — CLI: `analyze` command, rich table output

## Phase 2 (Post-MVP — mostly complete)

- [x] SQLite cache — skip re-running council if price moved < 2%
- [x] `--category` filter flag (series slug -> series_id lookup; `pqi categories` command)
- [x] Two-sided confidence scoring (confidence_yes / confidence_no per agent)
- [x] Rules Lawyer refocus (outcome condition, not oracle timing)
- [x] Richer briefing data (5 Tavily queries, smarter templates, 5000-char cap)
- [x] Explicit time awareness (EVENT TIMING block in briefing)
- [x] Anthropic backend fix (`claude-haiku-4-5-20251001`) + concurrency bypass
- [x] Rules Lawyer veto weight (2x multiplier config)
- [x] Historical accuracy tracking
- [x] History validation system: full-fidelity persistence, resolution sync,
      per-agent accuracy, postmortem analysis, `pqi history` sub-app
- [x] Shared briefing for sibling markets
- [x] CLOB price history enrichment (two-sided trend data in briefings)

## Not started

- /public-search integration (optional — catch ghost events in category queries)

---

## Future Work

### 1. Tier 2 gate evaluation (DEFERRED)
Lowering CONFIDENCE_HIGH_THRESHOLD 0.67 -> 0.65 would recover 4 wins at cost
of 2 new losses (net +2). Wait for 100+ non-legacy entries for statistical
confidence. See retrospective Section 5.

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

### 8. Price history -> scoring integration (DEFERRED)
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
If Bear hit rate is still below 50%, lower BEAR_WEIGHT to 0.7 in .env.
