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

| Layer         | Choice                 | Why                                     |
| ------------- | ---------------------- | --------------------------------------- |
| Language      | Python 3.11+           | LLM ecosystem, dev speed                |
| Package mgr   | `uv` (or pip)          | Fast, lockfile-based                    |
| CLI           | `typer` + `rich`       | Beautiful terminal output               |
| LLM client    | Abstracted (see below) | Swap Ollama ↔ Anthropic via env var    |
| Dev LLM       | Ollama `llama3.1:8b`   | Free, local, RTX 2070 Super             |
| Prod LLM      | `claude-haiku-3-5`     | ~$0.015/council run, stays under $20/mo |
| Market data   | Polymarket Gamma API   | Free, public, no auth needed            |
| Price history | Polymarket CLOB API    | Free, public, no auth for read-only     |
| News/search   | Tavily API             | Free tier 1k searches/mo                |
| Validation    | Pydantic v2            | All inter-module data                   |
| DB (Phase 2)  | SQLite via `aiosqlite` | Local price history cache               |

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
→ returns List[MarketData] (includes clob_token_ids from clobTokenIds field)
[main.py] Display market list → user picks one (or top by volume)
[synthesizer.py] Given MarketData, run Tavily searches on the event
→ produces BriefingDocument (structured Pydantic model)
[clob.py] Fetch YES + NO token price histories from CLOB API in parallel (1w hourly)
→ compute per-side SidePriceHistory: weighted avg, direction, volatility, sharp moves
→ wrap in PriceHistory(yes=..., no=...) → inject into BriefingDocument.price_history
  (non-fatal; None if unavailable)
[council.py] Send BriefingDocument to ALL 7 agents in parallel (asyncio)
→ agents see MARKET PRICE HISTORY block with trend data
→ each returns AgentVerdict(probability, confidence, rationale)
[scoring.py] Apply confidence-weighted formula:
score = Σ(prob × conf) / Σ(conf)
→ returns CouncilResult with final_score + ranked verdicts
[main.py] Rich table printout of recommendation (includes 7d trend line)

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
    clob_token_ids: list[str] # CLOB token IDs for YES/NO (from Gamma clobTokenIds)

class SidePriceHistory(BaseModel):
    token_id: str             # CLOB token ID for this side
    data_points: int          # number of hourly observations
    weighted_avg: float       # EMA weighted average (half-life 2 days)
    change_1w_pp: float       # net pp change over retrieval window
    change_24h_pp: float      # net pp change last 24h
    volatility_24h: float     # stddev of hourly deltas (pp/hr)
    direction: str            # "RISING" | "FALLING" | "STABLE"
    sharp_moves: list[SharpMove]  # up to 3 recent single-hour moves > 5pp

class PriceHistory(BaseModel):
    yes: SidePriceHistory     # YES-token trend metrics
    no: SidePriceHistory      # NO-token trend metrics (independent — don't sum to 1.0)
    fetched_at: datetime

class BriefingDocument(BaseModel):
    market: MarketData
    key_facts: list[str]      # bullet facts from Tavily search
    news_summary: str         # synthesized narrative
    side_yes_case: list[str]  # evidence/arguments favoring YES outcome
    side_no_case: list[str]   # evidence/arguments favoring NO outcome
    key_context: list[str]    # format, stakes, head-to-head, venue, etc.
    data_freshness: datetime
    price_history: PriceHistory | None  # CLOB trend data (None if fetch failed)

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
    edge: float               # perceived_win_rate - yes_price (WR-based; 0.0 if no WR snapshot)
    no_edge: float            # perceived_win_rate - no_price  (WR-based; 0.0 if no WR snapshot)
    recommendation: str       # "BUY YES" | "BUY NO" | "PASS"
    confidence_level: str     # "HIGH" | "MEDIUM" | "LOW"
    signal_type: str | None   # "HIGH VALUE" | "MODERATE VALUE" | "LOW MARGIN" | "CONTRARIAN" | None
    pass_reason: str          # why PASS (empty for BUY)
    expected_roi: float       # max ROI (1-price)/price of recommended side; 0.0 on PASS

    # Run-time snapshots (session 23) — captured on every result so
    # `history show` can render the WR/Kelly that applied to that run.
    perceived_win_rate_at_run: float | None  # PerceivedTrueWinRate at scoring time; None if EV gate dormant
    kelly_stake_full: float                  # full-Kelly fraction at side_price; 0.0 on PASS / no WR
    kelly_stake_scaled: float                # kelly_stake_full * kelly_multiplier (advisory bankroll fraction)
    kelly_multiplier: float                  # safety scaler used (e.g. 0.5 for half-Kelly); 0.0 if no Kelly

class PerceivedStats(BaseModel):
    # Rolling stats derived from resolved history. Persisted to ~/.pqi/stats.json.
    # Feeds the EV gate (true_win_rate) and the advisory Kelly stake.
    computed_at: datetime
    true_win_rate: float          # correct_buys / (correct_buys + wrong_buys)
    sample_size: int              # denominator (resolved BUYs, non-legacy)
    correct_buys: int
    wrong_buys: int
    kelly_fraction_at_p75: float  # reference Kelly stake at 75¢
    below_min_samples: bool       # True → EV gate stays dormant
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

**Two-sidedness is autonomy, not algebra (critical).** The YES and NO sides
of every event are *autonomous signals about TeamA vs TeamB* — the evidence
base for each is evaluated independently, not derived as the mirror of the
other. Concretely, today:

- **Prices** — `yes_price` and the CLOB NO-token price live on independent
  orderbooks and do **not** sum to 1.0 (spread / vig). Always fetched side-by-side
  (see `pqi/market/clob.py`). Never infer one from the other.
- **Confidences** — `confidence_yes` and `confidence_no` are independent
  fields carrying each side's own evidence weight. An agent can land at
  `(p_yes=0.26, conf_yes=0.72) ∥ (p_no=0.74, conf_no=0.35)` because the
  evidence for TeamA and TeamB is of different depth/quality.
- **Probability** — stored today as a single field `AgentVerdict.probability
  = P(YES)`, with `P(NO)` inferred as `1 − P(YES)` in `scoring.py`. This
  is a **modeling simplification**, not a commitment that the two sides are
  algebraically coupled. Treat YES and NO reasoning as two parallel problems
  with coupled-but-not-identical evidence, even though the code compresses
  them into one field.

When extending the model (asymmetric-confidence work, per-side priors,
future gate changes): per-side confidence is already autonomous and should
be exploited; per-side probability is a deferred extension (see roadmap
Theme A · A.1 "Option B") and must not be assumed to exist yet.

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
# YES-side score
yes_score = Σ(prob_yes × conf_yes) / Σ(conf_yes)

# NO-side score
no_score  = Σ(prob_no × conf_no) / Σ(conf_no)     # prob_no = 1 − prob_yes

# WR-based edges (informational; gated by EV gate, not by edge)
edge      = perceived_win_rate − market.yes_price       # 0.0 when no WR snapshot
no_edge   = perceived_win_rate − (1 − market.yes_price) # 0.0 when no WR snapshot
```

Recommendation logic (ROI-aware, probability-first):

Max ROI of the recommended side: `expected_roi = (1 - side_price) / side_price`.
Purely a function of price — council conviction is gated separately. A 70¢
share has a max ROI of 42.9% (sell at $1 = +30¢/70¢); an 80¢ share caps at
25%; a 90¢ share at 11.1%.

Gate order inside `from_council_verdicts()`:

1. **Price floor**: `side_price < MIN_TRADEABLE_PRICE` (0.09) → PASS
2. **Price ceiling**: `side_price > MAX_TRADEABLE_PRICE` (0.90) → PASS.
   Near-resolved; ROI doesn't justify execution risk.
3. **Contrarian** (council disagrees with market): requires all of
   `council_prob >= MIN_CONTRARIAN_PROBABILITY` (0.70),
   `side_conf >= MIN_CONTRARIAN_CONFIDENCE` (0.80),
   `expected_roi >= MIN_CONTRARIAN_ROI` (0.20). Label: `CONTRARIAN`.
4. **Market-aligned**: requires
   `council_prob >= MIN_COUNCIL_PROBABILITY` (0.60),
   `side_conf >= CONFIDENCE_HIGH_THRESHOLD` (0.67),
   `expected_roi >= MIN_EXPECTED_ROI` (0.10).
5. **EV gate (final)**: after a BUY clears all prior gates, reject it if
   `side_price >= PerceivedTrueWinRate` → PASS with reason "Big Drawdown
   Risk (-EV)". The gate only fires when `EV_GATE_ENABLED=true` AND
   `sample_size >= MIN_WIN_RATE_SAMPLES` (default 20). Below that
   threshold the gate is dormant and behavior matches the pre-EV system.
6. Otherwise → PASS with dynamic pass_reason.

Signal type on market-aligned BUYs is ROI-banded:

- `HIGH VALUE` — expected_roi ≥ 50% (cheap side; price ≤ ~0.667)
- `MODERATE VALUE` — 25% ≤ expected_roi < 50% (price ~0.667–0.80)
- `LOW MARGIN` — 10% ≤ expected_roi < 25% (price ~0.80–0.91)

`EDGE_THRESHOLD` is **deprecated** and unused — kept only so existing
`.env` files don't error on import. The `edge` / `no_edge` fields were
**repurposed in session 23** from `(score − price)` to
`(PerceivedTrueWinRate − side_price)`, so positive edge now means the
side is priced below the historical hit rate (the same condition the
EV gate enforces). They are 0.0 when no WR snapshot exists at run
time. Legacy / pre-session-23 history entries kept the old semantic
and are labeled accordingly in `history show`.

Overall `confidence_level` label uses the higher of the two per-side means.
`CouncilResult.expected_roi` is persisted on every result (0.0 on PASS).

### PerceivedTrueWinRate + Kelly (`pqi/stats.py`)

`pqi/stats.py` owns the EV inputs. Three pure functions:

- `recompute_from_history(min_samples)` — walks `history.jsonl`, counts
  `correct_buy` vs `wrong_buy` (non-legacy, resolved), writes a fresh
  `PerceivedStats` to `~/.pqi/stats.json` (atomic tmp+rename).
- `load_perceived_stats()` — returns `PerceivedStats | None`; tolerates
  missing/malformed file (cold-start safety).
- `kelly_fraction(win_rate, price, multiplier=1.0)` — `f* = (bp - q) / b`.
  Returns 0.0 when -EV, on degenerate prices, or when the scaled result is
  ≤ 0. Apply `KELLY_MULTIPLIER` (default 0.5 = half-Kelly) for the UI stake.

Recompute is triggered after every `history sync` (including `--auto`
background) and at the top of `history stats`. `scoring.py` loads stats
lazily at build time and passes `perceived_win_rate` to the model only
when the gate is active — models.py stays pure.

---

## Coding Conventions

- All inter-module data transfer via Pydantic models (no raw dicts)
- All LLM calls go through `pqi/llm/client.py` — never import anthropic/ollama elsewhere
- All async — use `asyncio.gather()` for parallel council calls
- Errors: log and continue (one agent failure should not crash the council)
- No hardcoded strings — prompts in `personas.py`, endpoints in `config.py`
- `rich.console.print()` for all user-facing output, `logging` for debug

### Attribution rule (STRICT)

Never mention AI, Claude, LLM-assistance, or any form of assistant
authorship in this project. This applies to **all** of: source code,
comments, docstrings, commit messages, PR titles/bodies, and any doc
under `docs/` or the repo root.

- **No** `Co-Authored-By: Claude ...` trailers in commits
- **No** "Generated with Claude Code" / "🤖 Generated with..." footers
- **No** "AI-assisted" / "with help from Claude" notes anywhere
- **No** `# Claude wrote this` or similar attribution comments

If a default template (e.g. system prompt's git commit/PR scaffold)
includes attribution, strip it before writing.

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
- Do not add agent-to-agent communication — they deliberate in parallel, not sequentially
- Do not use JSON mode for agent output — use regex parsing for Ollama compatibility

---

## Extended Reference (read when working on specific areas)

| Topic | File | When to read |
|-------|------|-------------|
| Gamma, CLOB, Tavily API details | [`docs/api-reference.md`](docs/api-reference.md) | Touching polymarket.py, clob.py, synthesizer.py, or config.py |
| Known issues & API quirks | [`docs/known-issues.md`](docs/known-issues.md) | Debugging unexpected API behavior or scoring edge cases |
| Session changelog & implementation details | [`docs/session-log.md`](docs/session-log.md) | Understanding why something was built a certain way |
| Roadmap & future work | [`docs/roadmap.md`](docs/roadmap.md) | Planning next priorities or checking what's been done |
| Architecture diagram (Mermaid) | [`docs/architecture.md`](docs/architecture.md) | High-level system overview |
| Analyze pipeline diagram (Mermaid) | [`docs/analyze-pipeline.md`](docs/analyze-pipeline.md) | Detailed pipeline flow |
| Tier 1 retrospective | [`docs/retrospectives/eval_tier1_roi_2026_04.md`](docs/retrospectives/eval_tier1_roi_2026_04.md) | Bear agent tuning, missed opportunity analysis |

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

**Last updated:** [session 26 — Option B steps 1–4 shipped; shadow data accumulating, no scoring integration yet]

All Phase 1 and Phase 2 features are complete. Phase 3 (continuous-run
TUI + auto-trader) is scoped in `docs/roadmap.md` Theme B but not yet
started. See [`docs/session-log.md`](docs/session-log.md) for
per-session details and [`docs/roadmap.md`](docs/roadmap.md) for the
themed plan (A. Modeling & Calibration, B. TUI + Trading,
C. Operational Tooling).

Session 26 shipped steps 1–4 of the Option B rollout (per
`docs/option_b_migration_plan.md`). Scoring is **not** touched —
aggregation in `CouncilResult.from_council_verdicts()` still uses the
single `probability` field. New data is descriptive / shadow-only.

1. **Step 1 — additive schema + synthetic-mirror backfill.**
   `AgentVerdict` gained `probability_yes`, `probability_no`,
   `probability_source: Literal["independent","synthetic_mirror"]`
   with a `model_validator(mode="after")` that auto-fills from the
   canonical `probability` field when unset. `history.py` applies
   the same backfill on load. `scripts/b_backfill.py` idempotently
   rewrote `~/.pqi/history.jsonl` in place (atomic tmp+rename,
   `.preB.bak` sibling) — 1860 verdicts across 292 rows tagged
   `synthetic_mirror`. Zero API calls.
2. **Step 2 — tolerant parser.** `council._parse_verdict` now reads
   an optional `probability_no_score` int (0–100). When present, the
   verdict is tagged `independent` and a drift warning fires at
   `|p_yes + p_no − 1| > 0.3` (never rejects; lenient-by-design per
   plan §5). Missing / malformed field falls through to synthetic
   mirror — no regression for legacy personas.
3. **Step 3 — persona rollout (all 7 agents).** SHARED_PREAMBLE
   gained a TWO-SIDED PROBABILITY (autonomy) block, conditional on
   the output-format block containing both fields. All 7 personas
   now emit `probability_no_score`, promoted one-per-commit in the
   order `rules_lawyer → statistician → sentiment → specialist →
   contrarian → bull → bear`. Bull/Bear got explicit anti-mirror
   framing (`"100 - probability_score"` warning, asymmetric-evidence
   reminder) because they carry the highest regression risk.
4. **Step 4 — shadow analytics.** `analytics.option_b_overview` and
   `analytics.option_b_per_agent` compute per-agent `|drift|`,
   signed drift, and per-side Brier (YES, NO) over resolved rows,
   filtered on `probability_source == "independent"`. Phase
   thresholds: `phase1 ≥ 10` (sanity), `phase2 ≥ 30` (pre-registered
   bar). `pqi history stats` renders a new panel + per-agent table
   when any independent verdicts exist; silent otherwise. Read-only;
   zero gate impact.

**API cost of the rollout:** per-agent output adds ~1 extra JSON
field (~5–10 tokens). At 7 agents per analyze run and
claude-haiku-4-5 output pricing (~$5/M), that's **≈ $0.00025 per
run** — below the rounding noise of the existing ~$0.015 council
cost. Prompt-cache hits on the preamble further dilute the impact.

**Shadow-data status (at session end):** 9 independent verdicts
across all 7 agents (≤ 0.5% of 1888 total). Max `|drift|` 0.12,
signed drift near zero — autonomy is landing, not collapsing to
`(0.5, 0.5)` or strict mirror. 0 resolved, so per-side Brier not
yet computable. Phase 1 (≥ 10 independent per agent) and the
backtest bar (100–150 total independent rows) remain ahead.

Sessions 22–24 established the pre-EV scoring contract is stable.
Session 25 (A.9 dynamic weights) and session 26 (Option B 1–4)
preserved it: every addition is metadata-only or upstream of the
aggregator. `CouncilResult.from_council_verdicts()` math unchanged
since session 23.

### What's live

- Full analyze pipeline: Gamma fetch → Tavily briefing → CLOB price history → Council → Scoring → CLI output
- 7-agent council with two-sided confidence, quorum gate, retry with exponential backoff
- Probability-first recommendation: BUY YES / BUY NO / PASS with ROI-banded signal types
- **Dynamic EV gate**: `PerceivedTrueWinRate` computed from resolved history
  (`~/.pqi/stats.json`) rejects BUYs where `side_price ≥ win_rate` with reason
  "Big Drawdown Risk (-EV)". Kelly stake surfaced in recommendation panel and
  `history stats` overview. Gate gated by `MIN_WIN_RATE_SAMPLES`.
- **WR-based edges** (session 23): `edge` / `no_edge` repurposed to
  `(PerceivedTrueWinRate − side_price)`, with `perceived_win_rate_at_run`,
  `kelly_stake_full`, `kelly_stake_scaled`, `kelly_multiplier` snapshotted
  on every result. Surfaced in `display_council_result` and `history show <#>`.
- **Dynamic per-agent reliability weights** (session 25,
  `pqi/agent_weights.py`): shrinkage formula, min-samples gate,
  JSON snapshot at `~/.pqi/agent_weights.json`, auto-recompute
  after `history sync`, per-run `agent_weights_snapshot` metadata
  on every `CouncilResult`.
- **Option B shadow layer** (session 26, steps 1–4 of
  `docs/option_b_migration_plan.md`): `AgentVerdict.probability_yes`,
  `probability_no`, `probability_source` persisted; all 7 personas
  emit `probability_no_score`; `history stats` renders Option B
  shadow panel + per-agent drift/Brier table. Descriptive only —
  scoring still aggregates from single `probability` field. Steps 5–6
  (shadow backtest + scoring integration) pending.
- Two-sided CLOB price history enrichment (YES + NO independently, non-fatal)
- History system: full-fidelity persistence, resolution sync, per-agent accuracy, postmortem
- SQLite cache with price-delta invalidation
- CLI commands: `analyze` (with --category, --top, --live, --starts-within, etc.), `categories`, `history` (list/show/sync/stats)

### Environment

- LLM_BACKEND: `anthropic` (claude-haiku-4-5-20251001)
- Ollama: installed, llama3.1:8b as fallback
- API keys: `CLAUDE_API_KEY` + `TAVILY_API_KEY` set in .env

---

## Next Session Prompt

```
Read CLAUDE.md in full, paying close attention to ## Current State.
Also read CTO_Agent.md — it frames the reviewer posture (contract-as-law,
parallelism, bias-as-feature, statistical skepticism) for this project.

Detailed reference material lives in docs/:
- docs/api-reference.md — API endpoints, params, response formats
- docs/known-issues.md — API quirks, edge cases, env setup, persisted state files
- docs/session-log.md — per-session changelog (current through session 26)
- docs/roadmap.md — future work priorities, themed
- docs/option_b_migration_plan.md — full Option B rollout plan (steps 1–4 shipped session 26)

## Session 26 context (completed)
- Option B steps 1–4 shipped — steps 5 (shadow backtest) and 6
  (scoring integration) PENDING. Never wire probability_no into
  the aggregator in CouncilResult.from_council_verdicts() until
  step 5 passes pre-registered acceptance criteria.
- Step 1: AgentVerdict.probability_yes / probability_no /
  probability_source + model_validator auto-fill; history.py
  applies the same fallback on load; scripts/b_backfill.py
  rewrote ~/.pqi/history.jsonl (1860 verdicts → synthetic_mirror,
  .preB.bak retained). Zero API calls.
- Step 2: council._parse_verdict reads optional
  probability_no_score int (0–100). Tags `independent` when
  present, logs drift warning at |p_yes + p_no − 1| > 0.3,
  lenient-by-design (never rejects). Falls back to
  synthetic_mirror on missing/malformed.
- Step 3: SHARED_PREAMBLE TWO-SIDED PROBABILITY block (conditional
  on output-format containing both fields). All 7 personas emit
  probability_no_score. Order: rules_lawyer → statistician →
  sentiment → specialist → contrarian → bull → bear. Bull/Bear
  carry explicit anti-mirror framing.
- Step 4: analytics.option_b_overview / option_b_per_agent
  compute per-agent |drift|, signed drift, YES/NO Brier on
  resolved rows. Surfaced in `pqi history stats` when any
  independent verdicts exist. Phase thresholds: phase1≥10,
  phase2≥30. Read-only.
- API cost impact: ~$0.00025/run (negligible).
- Shadow status at session close: 9 independent verdicts across
  all 7 agents, max |drift| 0.12, 0 resolved. Autonomy landing —
  no mirror collapse, no (0.5, 0.5) degeneracy.

## Background (kept from session 25)
- A.1 Option A closed: probability-transform candidates all
  REJECTED on 228 resolved BUYs. scripts/a1_backtest.py is the
  reference implementation for the pre-registered acceptance
  criteria (ΔBrier ≤ −0.005, ΔNet ≥ −1, bootstrap CI95<0 on
  train+holdout).
- A.9 live: dynamic per-agent reliability weighting via
  pqi/agent_weights.py. Snapshot at ~/.pqi/agent_weights.json,
  CouncilResult.agent_weights_snapshot per run. Config:
  AGENT_WEIGHTS_ENABLED, MIN_AGENT_WEIGHT_SAMPLES (30),
  AGENT_WEIGHT_SHRINK (2.0).

## PRIORITY options (pick one with the user)
- **Option B step 5 — shadow backtest.** GATED: needs ~100–150
  independent resolved rows. At session-26 close we had 0
  resolved. Check `pqi history stats` output; if the per-agent
  `n_res` column is still near-zero, defer. When ready, land
  `scripts/b_shadow_backtest.py` mirroring scripts/a1_backtest.py's
  structure (chronological 70/30 split, bootstrap ΔBrier CI,
  pre-registered criteria). Candidate B-math list lives in
  docs/option_b_migration_plan.md §7.
- Theme A · Modeling & Calibration: A.4 (Bear monitoring, pure
  stats), A.5 (EV gate threshold tuning, stats-only), A.10
  (dynamic-weight feedback-loop monitoring — protects A.9 from
  self-reinforcement; matters more now that A.9 is live).
- Theme C · Operational Tooling: any of C.1–C.6 are isolated QoL
  items.
- Theme B: B.1 (streaming TUI) is the entry point for Phase 3.

## Instructions
- Pick the next priority with the user before starting. If
  Option B step 5 comes up, first run `pqi history stats` and
  read the Option B shadow table — don't start the backtest
  until the independent-sample budget is met.
- Update ## Current State and write a fresh ## Next Session
  Prompt at end of session. Log meaningful decisions in
  docs/session-log.md.
- Don't edit models.py verdict logic (the
  `from_council_verdicts` aggregator) without explicit sign-off.
  The session-23 contract is stable; session 25 (agent_weights_snapshot)
  and session 26 (probability_yes/no/source + model_validator)
  are additive-only and preserve it.
- Option B rollout order is strict per
  docs/option_b_migration_plan.md. Never wire probability_no
  into scoring until step 5 passes pre-registered criteria on
  held-out data.
- STRICT: no AI / Claude / "assisted" attribution in code,
  comments, commits, or docs (see CLAUDE.md "Attribution rule").
- Commit per step, locally. No push without explicit ask.
```
