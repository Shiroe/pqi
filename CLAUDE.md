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
| Esports data  | PandaScore API         | Free tier 1k req/hr; structured match status / scoreline / form (optional, esports only) |
| Fuzzy match   | `rapidfuzz`            | Team-name resolution (Polymarket → PandaScore) |
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

**Anthropic prompt caching:** when `ANTHROPIC_PROMPT_CACHE_ENABLED=true`
(default) the system prompt is sent as a single `cache_control: ephemeral`
block (5-minute TTL). Output is bit-identical; cache hits bill at ~10%,
writes at 1.25×. DEBUG log `anthropic usage: input=… cache_read=…
cache_create=…` confirms engagement. Below the model's min cacheable
prefix (~1024–2048 tokens) it's a silent no-op. Kill switch:
`ANTHROPIC_PROMPT_CACHE_ENABLED=false`. See `docs/session-log.md`
§Session 27 for cost analysis and the Haiku min-prefix caveat.

**Centralized Anthropic retry:** every Anthropic call goes through an
exponential-backoff retry loop in `_chat_anthropic`. SDK auto-retry is
disabled (`max_retries=0`) so this layer owns the policy. Retryable:
`InternalServerError` (5xx incl. **529 OverloadedError**),
`RateLimitError` (429), `APIConnectionError`, `APITimeoutError`, plus
`APIStatusError` with `status_code >= 500`. Non-retryable 4xx escapes
immediately. Defaults (5 retries, 1s base, 30s cap) → wait sequence
1, 2, 4, 8, 16, 30s (~61s worst case). Tunables:
`ANTHROPIC_MAX_RETRIES`, `ANTHROPIC_RETRY_BASE_DELAY`,
`ANTHROPIC_RETRY_MAX_DELAY`. The council's `AGENT_MAX_RETRIES` loop
still wraps this (two budgets per agent before it's dropped). See
`docs/session-log.md` §Session 30.

---

## Data Flow (step by step)

[main.py] User runs: python -m pqi.main analyze
[polymarket.py] Fetch top N active markets from Gamma API
→ returns List[MarketData] (includes clob_token_ids from clobTokenIds field)
[main.py] Display market list → user picks one (or top by volume)
[match_resolver.py] (esports only, non-fatal) Resolve the market to a PandaScore
  match via rapidfuzz fuzzy team-name matching → StructuredMatchRecord with
  authoritative status (scheduled/running/finished), scheduled_at, final_score,
  team form, H2H. Pipeline continues with structured_match=None on any failure.
[synthesizer.py] Given MarketData (+ optional StructuredMatchRecord), run Tavily
  searches on the event → produces BriefingDocument with structured_match field
  + cross-source contamination tagger that anchors on PandaScore status
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
    structured_match: StructuredMatchRecord | None  # PandaScore (esports only; None on fetch fail or non-esports)

class TeamForm(BaseModel):
    wins: int                 # last-N completed matches
    losses: int

class H2HMatch(BaseModel):
    match_id: str
    scheduled_at: datetime
    tournament_name: str | None
    final_score: str | None
    winner_name: str | None

class StructuredMatchRecord(BaseModel):
    match_id: str
    team_a_name: str          # canonical PandaScore name
    team_b_name: str
    status: str               # "scheduled" | "running" | "finished"
    scheduled_at: datetime    # authoritative kickoff
    tournament_name: str | None
    match_format: str | None  # e.g. "BO3"
    final_score: str | None   # only when finished
    winner_name: str | None   # only when finished
    team_a_recent_form: TeamForm | None
    team_b_recent_form: TeamForm | None
    h2h: list[H2HMatch]       # prior encounters with stable IDs
    fuzzy_score: int          # 0–100 rapidfuzz min(token_set_ratio_a, token_set_ratio_b)

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
   `council_prob >= MIN_CONTRARIAN_PROBABILITY` (0.60, session 32;
   session 31's 0.65 was -EV in retrospective, the +$0.081/share
   signal lives in the 0.60–0.65 band),
   `side_conf >= MIN_CONTRARIAN_CONFIDENCE` (0.80),
   `expected_roi >= MIN_CONTRARIAN_ROI` (0.20). Label: `CONTRARIAN`.
4. **Market-aligned**: requires
   `council_prob >= MIN_COUNCIL_PROBABILITY` (0.60),
   `side_conf >= CONFIDENCE_HIGH_THRESHOLD` (0.67),
   `expected_roi >= MIN_EXPECTED_ROI` (0.10).
5. **EV gate (final, banded — session 31)**: after a BUY clears all
   prior gates, reject it if `side_price >= band_wr` → PASS with reason
   `"Big Drawdown Risk: … ≥ band L-H WR XX.X% [n=K] (-EV)"`. `band_wr`
   is the Beta-binomial smoothed win rate for the price band containing
   `side_price`, drawn from `PerceivedStats.band_stats` and computed
   against the global rate with pseudo-count `BAND_SHRINKAGE_K` (default
   5). Bands: [0.09–0.50, 0.50–0.60, 0.60–0.70, 0.70–0.80, 0.80–0.95].
   When `perceived_stats` is absent or `BAND_SHRINKAGE_K=0`, the gate
   falls back to the scalar `PerceivedTrueWinRate` (legacy behavior).
   The gate only fires when `EV_GATE_ENABLED=true` AND
   `sample_size >= MIN_WIN_RATE_SAMPLES` (default 20). Below that
   threshold the gate is dormant.
6. Otherwise → PASS with dynamic pass_reason.

Signal type on market-aligned BUYs is ROI-banded:

- `HIGH VALUE` — expected_roi ≥ 50% (cheap side; price ≤ ~0.667)
- `MODERATE VALUE` — 25% ≤ expected_roi < 50% (price ~0.667–0.80)
- `LOW MARGIN` — 10% ≤ expected_roi < 25% (price ~0.80–0.91)

`EDGE_THRESHOLD` is **deprecated** (kept so legacy `.env` files don't
error). `edge` / `no_edge` were repurposed in session 23 from
`(score − price)` to `(PerceivedTrueWinRate − side_price)` — positive
edge = side priced below historical hit rate. 0.0 when no WR snapshot.
Pre-session-23 history entries use the old semantic; `history show`
labels them.

Overall `confidence_level` label uses the higher of the two per-side means.
`CouncilResult.expected_roi` is persisted on every result (0.0 on PASS).

### PerceivedTrueWinRate + Kelly (`pqi/stats.py`)

`pqi/stats.py` owns the EV inputs. Three pure functions:

- `recompute_from_history(min_samples)` — walks `history.jsonl`, counts
  `correct_buy` vs `wrong_buy` (non-legacy, resolved), computes per-band
  smoothed win rates against the global rate (Beta-binomial, pseudo-count
  `BAND_SHRINKAGE_K`), and writes a fresh `PerceivedStats` — including
  `band_stats` — to `~/.pqi/stats.json` (atomic tmp+rename).
- `load_perceived_stats()` — returns `PerceivedStats | None`; tolerates
  missing/malformed file (cold-start safety).
- `kelly_fraction(win_rate, price, multiplier=1.0)` — `f* = (bp - q) / b`.
  Returns 0.0 when -EV, on degenerate prices, or when the scaled result is
  ≤ 0. Apply `KELLY_MULTIPLIER` (default 0.5 = half-Kelly) for the UI stake.

Band boundaries `[0.09–0.50, 0.50–0.60, 0.60–0.70, 0.70–0.80, 0.80–0.95]`
are a code constant in `stats.py` — changing them is a re-analysis
decision, not a tuning knob. Smoothing strength is tuned via
`BAND_SHRINKAGE_K` (default 5): `smoothed_wr = (wins + k·global_wr) /
(n + k)`. A band with `n=0` falls back to the global rate exactly; thin
bands are pulled toward global; dense bands dominate.

Recompute is triggered after every `history sync` (including `--auto`
background) and at the top of `history stats`. `scoring.py` loads stats
lazily at build time and passes both `perceived_win_rate` (scalar
fallback) and the full `perceived_stats` object to the model only when
the gate is active — models.py stays pure. Kelly sizing also uses the
banded threshold so gate-accepted bets always have non-zero advisory
stake (half-Kelly absorbs per-band estimation noise).

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
| Option B rollout plan | [`docs/option_b_migration_plan.md`](docs/option_b_migration_plan.md) | Any work touching `probability_no` / per-side probability |
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

**Last updated:** session 32 — contrarian min-prob retuned `0.65 →
0.60` (session 31's 0.65 was -EV on the resolved sample; the positive
signal sits in the 0.60–0.65 band) and the banded EV gate's
`pass_reason` format reworked to `≥ band L-H WR XX.X% [n=K]` so the
comparison is unambiguously against the band WR (not the global
`PerceivedTrueWinRate`).

Session 31 — banded-WR EV gate (Beta-binomial smoothed per-price-band
thresholds) + Kelly aligned to the banded threshold + PandaScore
`/teams/{id}/matches/past` endpoint fix.

All Phase 1 and Phase 2 features are complete. Phase 3 (continuous-run
TUI + auto-trader) is scoped in `docs/roadmap.md` Theme B but not yet
started. See [`docs/session-log.md`](docs/session-log.md) for
per-session details and [`docs/roadmap.md`](docs/roadmap.md) for the
themed plan (A. Modeling & Calibration, B. TUI + Trading,
C. Operational Tooling).

**Option B shadow layer** (session 26, steps 1–4 per
[`docs/option_b_migration_plan.md`](docs/option_b_migration_plan.md)):
`AgentVerdict.probability_yes` / `probability_no` / `probability_source`
persisted; all 7 personas emit `probability_no_score`; `pqi history
stats` renders a shadow panel + per-agent drift/Brier table.
**Descriptive only — scoring still aggregates from the single
`probability` field.** Never wire `probability_no` into
`CouncilResult.from_council_verdicts()` until step 5 passes the
pre-registered acceptance criteria on held-out data. Session-32
reassessment (628 independent, 406 resolved, per-agent `|drift|`
0.01–0.06): step 5 is blocked on **signal quality** not sample
count — aggregate autonomy has converged toward mirror collapse,
so the pre-registered ΔBrier ≤ −0.005 criterion is mathematically
unreachable on the current persona output. Fix the SHARED_PREAMBLE
autonomy framing before running the backtest.

Sessions 22–24 established the pre-EV scoring contract as stable.
Sessions 25 (A.9 dynamic weights), 26 (Option B 1–4), 28 (PandaScore
+ temporal disambiguation), 29 (category cache), 30 (Anthropic retry)
preserved it — every addition was metadata-only or upstream of the
aggregator. Session 31 was the first aggregator-touching change since
session 23: the EV gate's threshold became band-specific and Kelly
was realigned to the banded rate. Session 32 retuned one gate knob
(`MIN_CONTRARIAN_PROBABILITY` back to 0.60) and reworded a
user-facing string, but kept the aggregator math identical. The EV
gate still runs last and still only converts existing BUY
recommendations to PASS (it never creates a BUY), so upstream
behavior is unchanged.

### Recent sessions (detail in `docs/session-log.md`)

- **Session 32** — contrarian min-prob retuned `0.65 → 0.60` (session
  31's 0.65 was -EV on resolved history; the profitable signal sits
  in the 0.60–0.65 band). Big Drawdown Risk `pass_reason` format
  reworked to `≥ band L-H WR X% [n=K] (-EV)` so the comparison is
  unambiguously against the band WR rather than the global
  `PerceivedTrueWinRate`. Star signal in analyze list verified
  (already banded since session 31). Option B shadow reassessed:
  signal has converged toward mirror collapse, backtest blocked on
  signal quality not sample count.
- **Session 31** — banded EV gate replaces the single global
  `PerceivedTrueWinRate` threshold with per-price-band smoothed rates
  (Beta-binomial with pseudo-count `BAND_SHRINKAGE_K`, default 5).
  Bands `[0.09–0.50, 0.50–0.60, 0.60–0.70, 0.70–0.80, 0.80–0.95]`
  are a code constant. Kelly sizing now uses the same banded rate so
  rescued bets get non-zero advisory stake. Contrarian min probability
  loosened `0.70 → 0.65` (confidence 0.80 and ROI 0.20 unchanged).
  PandaScore past-matches endpoint fixed from
  `/teams/{id}/matches/past` to `/teams/{id}/matches?filter[past]=true`.
  New env: `BAND_SHRINKAGE_K` (5.0).
- **Session 30** — centralized exponential-backoff retry for Anthropic
  calls; SDK auto-retry disabled; catches 529 / 5xx / 429 / transport.
  New env: `ANTHROPIC_MAX_RETRIES` (5), `ANTHROPIC_RETRY_BASE_DELAY`
  (1.0s), `ANTHROPIC_RETRY_MAX_DELAY` (30.0s).
- **Session 29** — persistent category-resolution cache at
  `~/.pqi/category_cache.json`; eliminates ~3 redundant Gamma calls
  per warm `analyze -c <category>` run. Override path:
  `CATEGORY_CACHE_PATH`.
- **Session 28** — temporal disambiguation + PandaScore structured-data
  layer. Council briefing order: DISAMBIG → STRUCTURED → PRICE_HISTORY
  → NEWS → PRIOR. Esports only; non-fatal everywhere; opt out via
  `PANDASCORE_ENABLED=false`. 5 new env vars (`PANDASCORE_*`). Cache
  schema gained `structured_status` for status-aware invalidation.
- **Session 27** — Anthropic prompt caching live; system prompt wrapped
  in `cache_control: ephemeral` (5-min TTL) when
  `ANTHROPIC_PROMPT_CACHE_ENABLED=true`. Zero behavioral change to
  outputs; DEBUG `anthropic usage: …` confirms cache engagement.

### What's live

- Full analyze pipeline: Gamma → Tavily briefing → CLOB price history
  → Council → Scoring → CLI output.
- 7-agent council with two-sided confidence, quorum gate, retry with
  exponential backoff.
- Probability-first recommendation: BUY YES / BUY NO / PASS with
  ROI-banded signal types (HIGH VALUE / MODERATE VALUE / LOW MARGIN /
  CONTRARIAN).
- **Banded EV gate** (session 31, extends session 22): rejects BUYs
  where `side_price ≥ band_wr`, with `band_wr` the Beta-binomial
  smoothed rate for the price band containing `side_price`
  (`BAND_SHRINKAGE_K` pseudo-count, default 5). Falls back to the
  scalar `PerceivedTrueWinRate` when `perceived_stats` is absent or
  `BAND_SHRINKAGE_K=0`. Kelly uses the same banded threshold so the
  gate and the advisory stake stay aligned. Gated by
  `MIN_WIN_RATE_SAMPLES`.
- **WR-based edges** (session 23): `edge` / `no_edge` =
  `PerceivedTrueWinRate − side_price`; per-run snapshots of WR /
  Kelly persisted on every `CouncilResult`.
- **Dynamic per-agent reliability weights** (session 25,
  `pqi/agent_weights.py`): shrinkage formula, min-samples gate, JSON
  snapshot at `~/.pqi/agent_weights.json`, auto-recompute after
  `history sync`, `agent_weights_snapshot` on every run.
- **Option B shadow layer** (session 26) — descriptive only, see above.
- **Anthropic prompt caching** (session 27) — see LLM Client Contract.
- **Temporal disambiguation + PandaScore** (session 28) — see above.
- **Category-resolution cache** (session 29).
- **Centralized Anthropic retry** (session 30) — see LLM Client Contract.
- Two-sided CLOB price history enrichment (YES + NO, non-fatal).
- History system: persistence, resolution sync, per-agent accuracy,
  postmortem.
- SQLite cache with price-delta + structured-status invalidation.
- CLI: `analyze` (with `--category`, `--top`, `--live`,
  `--starts-within`, …), `categories`, `history` (list/show/sync/stats).

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
- docs/api-reference.md — API endpoints, params, response formats (incl. PandaScore)
- docs/known-issues.md — API quirks, edge cases, env setup, persisted state files
- docs/session-log.md — per-session changelog (current through session 32)
- docs/roadmap.md — future work priorities, themed
- docs/option_b_migration_plan.md — full Option B rollout plan (steps 1–4 shipped)

## PRIORITY options (pick one with the user)
- **Observe session-32 contrarian 0.60 gate in the wild.** After the
  loosening, a CONTRARIAN BUY only fires when council prob ≥ 0.60 AND
  side confidence ≥ 0.80 AND expected ROI ≥ 0.20 (the latter two
  unchanged). Watch the first few CONTRARIAN BUYs that land — they
  should skew toward the 0.60–0.65 band where the +$0.081/share
  signal was observed. If the next 5–10 resolutions are trending
  negative PnL, revisit the confidence (0.80) and ROI (0.20) gates
  rather than the prob floor — those are the risk-side guards.
- **Observe session-32 new `pass_reason` format.** Next BUY that the
  banded EV gate downgrades to PASS should print
  `"Big Drawdown Risk: YES price X% ≥ band L-H WR Y% [n=K] (-EV)"`
  in the recommendation panel. The only path that still uses the
  `PerceivedTrueWinRate` literal is when `perceived_stats` is absent
  or the side price is outside `[0.09, 0.95]` — effectively cold-start
  or an exotic price.
- **Do NOT land `scripts/b_shadow_backtest.py` yet.** The session-32
  shadow reassessment found that Option B's per-agent drift has
  converged toward mirror collapse (|drift| 0.01–0.06, signed ±0.02).
  YES/NO Brier differ by ≤ 0.01 across every agent — the
  pre-registered ΔBrier ≤ −0.005 CI95<0 criterion is mathematically
  unreachable with the current signal. Running the backtest now
  will produce a rejection on a degenerate signal, not a real test
  of B-math. Fix the signal first (next priority) or accept that
  Option B stays descriptive-only.
- **Iterate on Option B autonomy framing.** If the plan is to make
  Option B earn its way into scoring, the SHARED_PREAMBLE autonomy
  block needs sharper instruction — worked examples (`p_yes=0.70,
  p_no=0.25 when TeamA evidence is deep and TeamB is sparse`) and
  possibly a forced evidence-quality pre-step. Roll out per §6 step
  3: `rules_lawyer → statistician → sentiment → specialist →
  contrarian → bull → bear`, 10–20 analyses per persona, re-check
  aggregate `|drift|` in `history stats`. Gate the backtest on
  aggregate `|drift| > 0.10` for at least two agents.
- **Retrospective: sanity-check `BAND_SHRINKAGE_K=5`.** After a
  handful of additional resolved rows, rerun the comparison between
  K=3 / K=5 / K=10 against realized outcomes. Only adjust K with
  fresh data — don't tune on the same 55-row slice that picked it.
- **Observe session-30 retry in the wild.** Next 529/429 should log
  `anthropic call failed (attempt N/6): … — retrying in Xs` and the
  council should still complete. If the exception propagates without
  that log line, the type isn't in our `retryable` tuple — extend it
  in `_chat_anthropic`.
- **Verify session-28 PandaScore + temporal disambiguation
  end-to-end.** With `PANDASCORE_API_KEY` set, run
  `LOG_LEVEL=INFO python -m pqi.main -c counter-strike --live -v 0`.
  Confirm (a) cyan "PandaScore: TeamA vs TeamB (status, fuzzy=NN)"
  line, (b) briefing in DEBUG includes EVENT TEMPORAL DISAMBIGUATION
  + STRUCTURED MATCH DATA blocks, (c) SCHEDULED matches don't get
  past-tense paraphrases. The session-31 past-matches endpoint fix
  (`filter[past]=true`) means team form should now be populated for
  both teams when their names resolve — previously a broken path
  silently returned empty form. Resolver miss is fine — pipeline
  degrades to `structured_match=None`.
- **Theme A · Modeling & Calibration:** A.4 (Bear monitoring, pure
  stats), A.10 (dynamic-weight feedback-loop monitoring — protects
  A.9 from self-reinforcement; matters more now that A.9 is live).
- **Theme C · Operational Tooling:** C.1–C.6 are isolated QoL items.
- **Theme B:** B.1 (streaming TUI) is the Phase 3 entry point.

## Instructions
- Pick the next priority with the user before starting. If the
  conversation turns to Option B step 5, reference the session-32
  shadow reassessment in docs/session-log.md first — the backtest
  cannot pass the pre-registered criteria on the current signal.
- Update ## Current State and write a fresh ## Next Session Prompt
  at end of session. Log meaningful decisions in docs/session-log.md.
- Session 31 touched the aggregator for the first time since session
  23 (banded threshold + Kelly realignment). Session 32 adjusted a
  gate knob (`MIN_CONTRARIAN_PROBABILITY`) and a user-facing string
  but kept the aggregator math identical. Any further aggregator
  changes still need explicit sign-off.
- Option B rollout order is strict per
  docs/option_b_migration_plan.md. Never wire probability_no into
  scoring until step 5 passes pre-registered criteria on held-out
  data — and step 5 is blocked on the signal-quality gap, not the
  sample-count gap.
- PandaScore is non-fatal: any resolver failure must leave
  structured_match=None and let the pipeline continue. Don't add
  exceptions or hard gates — degraded behavior is the contract.
- STRICT: no AI / Claude / "assisted" attribution in code,
  comments, commits, or docs (see CLAUDE.md "Attribution rule").
- Commit per step, locally. No push without explicit ask.
```
