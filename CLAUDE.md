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

**Last updated:** session 38 — **Phase 0 closed.** Task 0.5
synthesis retrospective at
`docs/retrospectives/phase_0_edge_2026_04.md` extended with
executive summary (§0), pre-event CLV section (§4),
reliability-diagram section (§5), verdict (§6), and recommended
next actions (§7). **Verdict: inconclusive at current n (65
resolved BUYs / 385 reliability rows).** The three streams align
on "mild positive prediction edge (council Brier 0.2176 beats the
0.25 chance baseline by ~3pp), mild negative entry timing (mean
CLV −0.0167 on n=44), no actionable gate miscalibration (no band
clears ≥1 Wilson-CI-width flag)." Realized April PnL marginal at
−0.575/share on 65 BUYs. The council is a lightly-advantaged
predictor whose edge is being absorbed by entry timing +
correlated agents, netting to near zero. Cluster-level mirror
collapse is the single most important Phase 1 finding: 5 of 7
agents (rules_lawyer, statistician, sentiment, specialist,
contrarian) are internally correlated at r 0.80–0.92 (9/10
cluster-internal pairs at r ≥ 0.85); Bull weakly attached (~0.6);
Bear structurally independent but the only agent with Brier above
the 0.25 chance baseline (0.2810). **Highest-leverage next move:
Phase 1 (SHARED_PREAMBLE autonomy rewrite).** Gate knobs
explicitly *not* retuned — `BAND_SHRINKAGE_K=5`,
`MIN_CONTRARIAN_PROBABILITY=0.60`, ROI bands, Kelly multiplier,
`MIN_WIN_RATE_SAMPLES` all stand. Phase 2 (execution edge)
scoping unblocked for MFE analysis in parallel with P1;
listener/auto-trader still deferred. Phase 3 (deployment
portability) deferred — verdict was inconclusive, not positive.
No code changes this session.

Session 37 shipped Phase 0 Task 0.4 — per-band calibration check
(no-code-change measurement task). Realized BUY hit rate per
EV-gate band (winning_side semantics, matching
`stats.py:_side_price_and_won`) joined against the
`band_stats.smoothed_wr` thresholds in `~/.pqi/stats.json`. 95%
Wilson CIs computed. **No band clears the "≥ 1 CI width" flag**
in either direction — at n=8–23 per occupied band the CIs
(widths 0.27–0.56) swallow any true miscalibration signal. The
two dense bands ([0.60, 0.70) n=23 realized 65.2% vs threshold
65.9%; [0.70, 0.80) n=15 realized 73.3% vs threshold 72.3%) are
well-calibrated on point estimate — BUYs in these bands average
break-even EV on entry, consistent with the session-34 aggregate
PnL of −0.575/65 shares. [0.50, 0.60) point-estimate
under-rejects (realized 0.375 on n=8 vs mean price 0.561, Δ
−0.186), but the gate's threshold (0.497 < band floor 0.500)
already rejects all future BUYs in this band — forward-looking
protection is maximal; the calibration miss is retrospective
only. [0.80, 0.95) is the best band on both metrics (realized
0.938 on n=16, +0.099 vs mean price, +0.058 vs threshold —
point-estimate mild over-rejection candidate, not significant).
`BAND_SHRINKAGE_K` recommendation: **keep K=5** per phase-doc
guardrail (no tuning on the same data that produced band_stats);
revisit when [0.50, 0.60) reaches n≥20 or a hold-out period is
defined. Coverage: 62/65 resolved BUYs (95.4%) in 5 bands; 1
out-of-band (price 0.9995, gated by `MAX_TRADEABLE_PRICE`); 2
intermediate settlements excluded from `band_stats` by
construction. Full table + query + flagged-bands summary in
`docs/retrospectives/phase_0_edge_2026_04.md`. Analytics Q4
retargeted from directional-winner to winning_side semantics so
`pqi query` output aligns row-by-row with `band_stats`.

Session 36 shipped Phase 0 Task 0.3 — reliability diagram.
`scripts/reliability_diagram.py` (stdlib only) walks
`~/.pqi/history.jsonl`, buckets per-agent and council-aggregate
predictions into the EV-gate-aligned price bands (with
`[0.00, 0.09)` and `[0.95, 1.00]` bookends), and emits an ASCII
table + markdown report to `~/.pqi/reports/reliability_YYYYMMDD.md`.
Operates over **385 resolved rows / 2,668 verdicts** via
directional-winner outcome semantics. Headline numbers: **Council
Brier 0.2176** (best of any agent — confidence weighting buys ~3pp
over the 0.25 always-0.5 baseline). **Bear Brier 0.2810** (the lone
agent above the chance baseline). Five-agent cluster
{rules_lawyer, statistician, sentiment, specialist, contrarian}
shows pairwise Pearson 0.80–0.92 — **partial mirror collapse,
cluster-level not uniform.** Bull retains weak independence (~0.6
with the cluster); Bear is essentially uncorrelated (−0.17 to
+0.04) but its predictions are also the worst-calibrated. Council
is well-calibrated through `[0.09, 0.70)` and slightly
underconfident on YES in `[0.60, 0.95)`. Bull is overconfident
across the whole upper tail (`[0.80, 0.95)` n=16 realized 62.5%
on a mean prediction of 84.4% — 22pp gap). No pipeline /
aggregator / gate changes.

Session 35 shipped Phase 0 Task 0.2 — pre-event CLV backfill.
`scripts/backfill_clv.py` writes uniform-schema `~/.pqi/clv.jsonl`;
`analytics_sql.py` conditionally registers `clv_by_row`; coverage
44/65 (67.7%); 21 unavailable all permanent `no_tokens`
(pre-session-22 rows); mean CLV −0.0167, stddev 0.064 on n=44.
Bucket policy last-before-target (avoids post-event
resolution-spike leakage — see session-log §35 for the 0.835→0.9995
probe).

Session 34 shipped Phase 0 Task 0.1 — DuckDB analytics layer
(`pqi/analytics_sql.py`) with pre-registered `history` and
`verdicts` views, `pqi query "<SQL>"` CLI subcommand, 5 starter
queries in `docs/analytics_queries.md`, `duckdb>=1.0` pinned.
Full-history aggregations at ~230ms (target <1s). No pipeline,
scoring, aggregator, or contract changes — read-only analytics
surface. Unblocked Task 0.2 and the rest of Phase 0.

Session 33 — planning + documentation only. Triaged a v2.0
architecture RFC (`docs/esports_intel_engine_report.md`) and its
external review (`docs/cto_review_esports_engine.md`); synthesized the
keep/defer decisions into `docs/scaling_assessment.md`; expanded
`CTO_Agent.md` with quantitative/strategic/architectural reviewer
capabilities; and laid out five phase docs in `docs/phases/` to enable
async work.

Session 32 — contrarian min-prob retuned `0.65 → 0.60` (session 31's
0.65 was -EV on the resolved sample; the positive signal sits in the
0.60–0.65 band) and the banded EV gate's `pass_reason` format
reworked to `≥ band L-H WR XX.X% [n=K]`. Session 31 — banded-WR EV
gate + Kelly aligned to the banded threshold + PandaScore
past-matches endpoint fix.

All Phase 1 (MVP) and legacy Phase 2 (Post-MVP) features per the old
roadmap naming are complete. The new phase framing in
`docs/phases/` (sessions 33+) supersedes that naming:

| New phase | Status | Doc |
| --- | --- | --- |
| Phase 0 — edge validation | **done (verdict: inconclusive; retrospective written)** | [`docs/phases/phase_0_edge_validation.md`](docs/phases/phase_0_edge_validation.md) + [`docs/retrospectives/phase_0_edge_2026_04.md`](docs/retrospectives/phase_0_edge_2026_04.md) |
| Phase 1 — signal quality (Option B autonomy fix) | **next — highest-leverage per P0 verdict** | [`docs/phases/phase_1_signal_quality.md`](docs/phases/phase_1_signal_quality.md) |
| Phase 2 — execution edge (MFE, listener) | MFE scoping unblocked (parallel with P1); listener/auto-trader deferred | [`docs/phases/phase_2_execution_edge.md`](docs/phases/phase_2_execution_edge.md) |
| Phase 3 — deployment portability (Docker/VPS) | deferred — P0 verdict inconclusive, not positive; revisit after P1 Brier improvement | [`docs/phases/phase_3_deployment_portability.md`](docs/phases/phase_3_deployment_portability.md) |
| Phase 4 — scaling triggers (Postgres/Redis/microservices) | reactive only | [`docs/phases/phase_4_scaling_triggers.md`](docs/phases/phase_4_scaling_triggers.md) |

See [`docs/session-log.md`](docs/session-log.md) for per-session
details, [`docs/scaling_assessment.md`](docs/scaling_assessment.md)
for the scaling-decision rationale, and
[`docs/roadmap.md`](docs/roadmap.md) for the themed catalog (A.
Modeling & Calibration, B. TUI + Trading, C. Operational Tooling),
which the phases index.

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
behavior is unchanged. Session 34 added a read-only SQL layer
(`analytics_sql.py`, `pqi query`) — no pipeline or aggregator touch.

### Recent sessions (detail in `docs/session-log.md`)

- **Session 38** — Phase 0 Task 0.5 shipped: edge retrospective
  & verdict (no-code-change synthesis). Extended
  `docs/retrospectives/phase_0_edge_2026_04.md` with executive
  summary (§0), pre-event CLV distribution (§4), reliability-
  diagram summary (§5), verdict (§6), and recommended next
  actions (§7). **Verdict: inconclusive at current n (65 resolved
  BUYs / 385 reliability rows).** Three measurement streams align
  on "mild positive prediction edge, mild negative entry timing,
  no actionable gate miscalibration." Phase 0 closed. Phase 1
  (Option B autonomy rewrite) is the highest-leverage next move
  per the cluster-collapse diagnosis in §5.3 — 5-agent cluster
  (rules_lawyer/statistician/sentiment/specialist/contrarian) at
  pairwise r 0.80–0.92, Bull weakly attached (~0.6), Bear
  structurally independent but Brier above the 0.25 chance
  baseline (0.2810). Phase 2 MFE scoping unblocked in parallel
  with P1; P2 listener / P3 deployment deferred pending P1 Brier
  improvement. No gate knobs retuned: `BAND_SHRINKAGE_K=5`,
  `MIN_CONTRARIAN_PROBABILITY=0.60`, ROI bands, Kelly multiplier,
  `MIN_WIN_RATE_SAMPLES` all stand. `docs/phases/README.md`
  status table updated.
- **Session 37** — Phase 0 Task 0.4 shipped: per-band
  calibration check (no-code-change). Realized BUY hit rate per
  EV-gate band (winning_side semantics, matching
  `stats.py:_side_price_and_won`) joined against
  `band_stats.smoothed_wr` thresholds in `~/.pqi/stats.json`;
  Wilson 95% CIs, deltas vs threshold and vs mean price. **No
  band clears the "≥ 1 CI width" flag** in either direction;
  at n=8–23 per occupied band the CIs (widths 0.27–0.56)
  swallow any miscalibration signal. Two dense bands ([0.60,
  0.70) n=23, [0.70, 0.80) n=15) well-calibrated on point
  estimate. [0.50, 0.60) point-estimate under-rejects (realized
  0.375 on n=8 vs mean price 0.561, Δ −0.186); current gate
  already maximally protective (threshold 0.497 < band floor
  0.500), so calibration concern is retrospective. [0.80, 0.95)
  is the best-EV band (realized 0.938, +0.099 vs mean price);
  mild over-rejection candidate on point estimate, not
  statistically significant. `BAND_SHRINKAGE_K` recommendation:
  **keep K=5** per phase-doc guardrail; revisit when [0.50,
  0.60) reaches n≥20 or a hold-out period is defined. Coverage
  62/65 in 5 bands; 1 out-of-band (gated by
  `MAX_TRADEABLE_PRICE`); 2 intermediate settlements excluded
  by construction. Analytics Q4 retargeted from directional to
  winning_side semantics so `pqi query` aligns with `band_stats`.
  Retrospective started at
  `docs/retrospectives/phase_0_edge_2026_04.md` (Task 0.5 will
  fold in CLV + reliability sections + verdict).
- **Session 36** — Phase 0 Task 0.3 shipped: reliability diagram.
  `scripts/reliability_diagram.py` (stdlib only) bins per-agent +
  council-aggregate predictions into the EV-gate-aligned bands plus
  `[0.00, 0.09)` / `[0.95, 1.00]` bookends; outcome via directional-
  winner. 385 resolved rows / 2,668 verdicts. Console + markdown
  report at `~/.pqi/reports/reliability_YYYYMMDD.md`. Headline
  numbers: Council Brier 0.2176 (best — confidence weighting buys
  ~3pp over the 0.25 chance baseline), Bear Brier 0.2810 (the lone
  agent above the chance baseline), Bull overconfident across the
  whole upper tail (`[0.80, 0.95)` 22pp gap on n=16). Cross-agent
  diversity diagnostic: mean pairwise Pearson 0.557 but **9/21 pairs
  at r ≥ 0.85** — partial mirror collapse, cluster-level not uniform.
  The 5-agent cluster {rules_lawyer, statistician, sentiment,
  specialist, contrarian} sits at r 0.80–0.92 internally; Bull is
  weakly attached (~0.6); Bear is uncorrelated (−0.17 to +0.04).
  This refines the session-26/32 "uniform mirror collapse" framing:
  2 of 7 agents retain measurable independence. Phase 1 priority
  amplified — destroying Bull/Bear distinctness without fixing
  Bear's calibration would push council Brier upward. Feeds Task
  0.5 retrospective.
- **Session 35** — Phase 0 Task 0.2 shipped: pre-event CLV
  backfill. `scripts/backfill_clv.py` (stdlib + httpx,
  `main() -> int`, atomic write) fetches CLOB `interval=max` per
  bought-side token and picks the last hourly bucket before
  `event_start_time`; writes uniform-schema `~/.pqi/clv.jsonl`;
  idempotent with permanent-failure caching and `--rebuild` escape.
  `analytics_sql.py` extended with conditional `clv_by_row` view and
  `clv_path=` override for testability. `docs/analytics_queries.md`
  reorganized (3 views documented, Q5 now CLV queries, old Q5 →
  Q6). Bucket policy last-before-target — the alternative
  (closest-overall) would leak the post-event resolution spike. 44/65
  resolved BUYs computed; the 21 gap is all permanent `no_tokens` (no
  way to recover; future BUYs all carry tokens since session 22).
  Preliminary signal: mean CLV −0.0167 aggregate (slightly unfavorable
  entry quality). Feeds Task 0.5 retrospective.
- **Session 34** — Phase 0 Task 0.1 shipped. Added DuckDB analytics
  layer (`analytics_sql.py`) with `history` and `verdicts`
  pre-registered views; `pqi query "<SQL>"` CLI subcommand with rich
  rendering + `--limit/-n` truncation; 5 starter queries documented
  in `docs/analytics_queries.md`; `duckdb>=1.0` pinned. DuckDB needs
  `timestampformat='%Y-%m-%dT%H:%M:%S.%f%z'` + `sample_size=-1` to
  read our JSONL — captured as a module constant. Full-history
  aggregations clock at ~230ms (target <1s). Surface-level findings
  noted in session-log (feed task 0.5 retrospective): `[0.50, 0.60)`
  band realizing 33% on n=9, Bear at 49% lean rate vs other agents
  60–67%, April monthly PnL -0.575/share across 65 BUYs.
- **Session 33** — planning + docs only. Expanded `CTO_Agent.md`
  into a capable reviewer persona (quantitative validation, strategic
  edge, architectural judgment). Triaged the v2.0 RFC and its
  external review; captured keep/defer rationale in
  `docs/scaling_assessment.md`. Wrote 5 phase docs under
  `docs/phases/` designed for async work (entry criteria + task
  acceptance + session state per doc). Phase 0 (DuckDB-driven edge
  validation) is the next code workstream.
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
- **DuckDB analytics layer** (sessions 34–35, `pqi/analytics_sql.py`):
  pre-registered `history` + `verdicts` views over
  `~/.pqi/history.jsonl`, plus conditional `clv_by_row` view over
  `~/.pqi/clv.jsonl` when the sidecar exists. 6 starter queries
  including CLV distribution / per-category / outcome-join in
  `docs/analytics_queries.md`. Read-only; no pipeline coupling.
- **Pre-event CLV backfill** (session 35, `scripts/backfill_clv.py`):
  one-shot stdlib+httpx script. CLOB `interval=max` per bought-side
  token, last-before-target bucket policy, uniform-schema JSONL
  output keyed by `run_id`. Idempotent with `--rebuild` escape.
  Run via `python -m scripts.backfill_clv`.
- **Reliability diagram** (session 36, `scripts/reliability_diagram.py`):
  one-shot stdlib-only script. Buckets per-agent + council
  predictions into the EV-gate bands (with bookends), computes per-
  bin Wilson CIs, per-agent Brier, and a cluster-level mirror-
  collapse diagnostic (fraction of pairs at r ≥ 0.85). Versioned
  markdown report at `~/.pqi/reports/reliability_YYYYMMDD.md`. Run
  via `python -m scripts.reliability_diagram` (or `--no-write` for
  stdout only).
- **Per-band calibration retrospective** (session 37, no code)
  at `docs/retrospectives/phase_0_edge_2026_04.md`. Per-band
  realized-vs-threshold table with Wilson 95% CIs for the live
  `~/.pqi/stats.json` `band_stats`. Task 0.5 will fold in CLV
  + reliability summaries and issue a phase verdict.
- CLI: `analyze` (with `--category`, `--top`, `--live`,
  `--starts-within`, …), `categories`, `query "<SQL>"`, `history`
  (list/show/sync/stats).

### Environment

- LLM_BACKEND: `anthropic` (claude-haiku-4-5-20251001)
- Ollama: installed, llama3.1:8b as fallback
- API keys: `CLAUDE_API_KEY` + `TAVILY_API_KEY` set in .env

---

## Next Session Prompt

```
Read CLAUDE.md in full, paying close attention to ## Current State.
Also read CTO_Agent.md — session-33 rewrite made it a capable
reviewer persona (contract-as-law, quantitative validation, strategic
edge, architectural judgment, plus the original code-review stance).

Detailed reference material lives in docs/:
- docs/scaling_assessment.md — v2.0 RFC triage, prediction vs execution
  edge framework, why DuckDB over Postgres, explicit rejections
- docs/phases/README.md — phase index, dependency graph, status table
- docs/phases/phase_0_edge_validation.md — **done (see retrospective)**
- docs/phases/phase_1_signal_quality.md — **active (Option B autonomy fix — highest leverage per P0 verdict)**
- docs/phases/phase_2_execution_edge.md — MFE scoping unblocked (parallel with P1); listener deferred
- docs/phases/phase_3_deployment_portability.md — deferred (P0 inconclusive, not positive)
- docs/phases/phase_4_scaling_triggers.md — reactive only
- docs/retrospectives/phase_0_edge_2026_04.md — **Phase 0 closed** — executive summary §0, per-band §1–§3, CLV §4, reliability §5, verdict §6, next actions §7
- docs/analytics_queries.md — starter queries for `pqi query` (Q4 uses winning_side semantics)
- docs/api-reference.md — API endpoints, params, response formats (incl. PandaScore)
- docs/known-issues.md — API quirks, edge cases, env setup, persisted state files
- docs/session-log.md — per-session changelog (current through session 38)
- docs/roadmap.md — themed catalog (A/B/C); phase section indexes phases
- docs/option_b_migration_plan.md — full Option B rollout plan (steps 1–4 shipped; step 5 blocked on P1 signal quality)

## PRIORITY — Phase 1: Signal Quality Fix (Option B autonomy)

Default entry point: `docs/phases/phase_1_signal_quality.md`.

Phase 0 closed session 38 with verdict **inconclusive at
current n**, but the retrospective (§5.3) identified cluster-
level mirror collapse as the single most important finding:
5 of 7 agents are correlated at r 0.80–0.92 internally
(rules_lawyer / statistician / sentiment / specialist /
contrarian); Bull weakly attached (~0.6); Bear structurally
independent but has Brier 0.2810 — the only agent above the
0.25 chance baseline. Council Brier 0.2176 is 3pp better than
baseline (real but small), and it is being absorbed by (a) the
correlated cluster limiting effective diversity, (b) Bear's
miscalibration dragging aggregate accuracy, (c) mean pre-event
CLV −0.0167 on entry timing.

**Task 1.1 — SHARED_PREAMBLE autonomy rewrite.** Details in the
phase doc (`docs/phases/phase_1_signal_quality.md` §Task 1.1).
Key scope:
- Current SHARED_PREAMBLE allows agents to shortcut to
  `P(NO) = 1 − P(YES)` because the YES/NO-autonomy framing is
  not structurally reinforced with evidence-quality asymmetry.
  The Phase 1 doc proposes worked examples + a forced
  evidence-quality pre-step + an explicit anti-mirror warning.
- Preserve Bull's partial independence and Bear's structural
  independence. The failure mode is "rewrite flattens all 7
  agents toward one voice" — that would push council Brier
  upward (regression).
- Keep the preamble under ~1500 tokens for prompt-cache
  efficiency (session 27).

**Three measurable post-rewrite checks** (unblock criteria for
Option B step 5 backtest):
- (a) Cluster-pair fraction at r ≥ 0.85 drops below 0.50
  (current 9/21 = 43%; close, but Bull and Bear already sit
  outside the cluster — the lever is destabilising the 5-agent
  cluster).
- (b) Bear Brier falls toward the 0.22 cluster without losing
  Bear's independence (Bear pairwise r with others stays well
  below 0.85).
- (c) Council Brier holds or improves from 0.2176.

Re-run `python -m scripts.reliability_diagram` after every
prompt change. The report is UTC-dated — iteration history is
preserved automatically.

**Parallelisable alongside P1:** Phase 2 MFE analysis
(`docs/phases/phase_2_execution_edge.md`). This is the third
CTO-§5 edge metric (max favourable excursion) — a DuckDB +
CLOB-history query analogous to Task 0.2. Produces the full
edge triangulation (prediction / entry / exit) without
infrastructure investment. P2 listener / auto-trader stays
deferred until P1 produces measurable Brier improvement.

## Phase 0 verdict — anchor reference

From `docs/retrospectives/phase_0_edge_2026_04.md` §6:

- **Prediction edge:** council Brier 0.2176 beats 0.25 chance
  baseline by ~3pp (n=385). Real but small; aggregate is
  fragile because 5 of 7 agents mirror each other.
- **Execution entry quality:** mean pre-event CLV −0.0167 on
  n=44 (67.7% coverage, 21 rows permanently unavailable via
  `no_tokens`). Mildly unfavourable; CI straddles zero.
  Valorant worst (−0.032 n=17), LoL neutral (−0.001 n=14).
- **Gate calibration:** no band clears ≥1 CI width flag in
  either direction. [0.60, 0.70) n=23 and [0.70, 0.80) n=15
  well-calibrated on point estimate (break-even EV on entry).
  [0.80, 0.95) best-EV band (realized 0.938, +9.9pp vs mean
  price). [0.50, 0.60) point-estimate under-rejects (Δ −0.186
  on n=8) but gate threshold 0.497 < band floor 0.500 means
  forward-looking protection is maximal.
- **Realized April PnL:** −0.575/share on 65 BUYs (≈ −0.9¢/
  share). Break-even before fees, slightly negative after.
- **Explicitly no knob changes:** `BAND_SHRINKAGE_K=5`,
  `MIN_CONTRARIAN_PROBABILITY=0.60`, ROI bands, Kelly
  multiplier, `MIN_WIN_RATE_SAMPLES` all stand. Re-tuning on
  the same 62 rows that produced `band_stats` is dressed-up
  overfitting per `CTO_Agent.md` §2.

## Monitoring tasks (low priority; observe, don't code)
These stay open through Phase 1. Pick up if the signal appears
organically.

- **Observe session-32 contrarian 0.60 gate in the wild.** A
  CONTRARIAN BUY fires when council prob ≥ 0.60 AND side
  confidence ≥ 0.80 AND expected ROI ≥ 0.20. Early resolutions
  should skew toward [0.60, 0.65). If first 5–10 trend
  negative PnL, revisit the confidence/ROI guards, not the
  prob floor.
- **Observe session-32 `pass_reason` format.** Next banded-
  EV-gate downgrade should print `"Big Drawdown Risk: … ≥ band
  L-H WR Y% [n=K] (-EV)"`. Scalar `PerceivedTrueWinRate` path
  only fires on absent perceived_stats or exotic prices
  outside [0.09, 0.95].
- **Observe session-30 retry.** Next 529/429 should log
  `anthropic call failed (attempt N/6): … — retrying in Xs`.
  If the exception propagates without that log, extend the
  `retryable` tuple in `_chat_anthropic`.
- **Verify session-28 PandaScore + disambiguation end-to-end.**
  With `PANDASCORE_API_KEY` set, run `LOG_LEVEL=INFO python -m
  pqi.main -c counter-strike --live -v 0`. Confirm cyan
  "PandaScore: …" line, DEBUG briefing with EVENT TEMPORAL
  DISAMBIGUATION + STRUCTURED MATCH DATA blocks, no past-tense
  paraphrases on SCHEDULED matches. Resolver miss →
  `structured_match=None` is the contract.
- **Reliability diagram monthly.** Re-run
  `python -m scripts.reliability_diagram` at the end of each
  calendar month (independent of phase work) to track cluster
  drift / Bear Brier / council Brier over time.

## Deferred / theme-level (picked up via phase docs)

Theme A items (A.1 Option B, A.4 Bear monitoring, A.7
confidence floor, A.8 Bull audit, A.10 dynamic-weight loop
monitoring) all feed Phase 1 work. Theme B items (B.1 TUI,
B.3–B.6 trading) become Phase 2 deliverables once P1 shows
Brier improvement. Theme C items are isolated QoL — pick any
between phases if they bite you.

## Instructions
- Default to Phase 1 Task 1.1 (SHARED_PREAMBLE autonomy rewrite)
  unless the user redirects. Start with a draft of the new
  preamble section — **do not commit the rewrite until the
  user reviews it.** Prompt-level work is cheap; prompt-level
  regressions are not.
- Measure delta with
  `python -m scripts.reliability_diagram` after every change.
  Compare against the 2026-04-18 baseline in
  `~/.pqi/reports/reliability_20260418.md`.
- When reaching for history analytics, use `pqi query "<SQL>"`
  on the `history` / `verdicts` / `clv_by_row` views before
  writing a fresh Python script. See docs/analytics_queries.md
  for patterns. For CLV: `python -m scripts.backfill_clv`
  refreshes the sidecar; the DuckDB view auto-registers when
  it exists.
- Update ## Current State and write a fresh ## Next Session
  Prompt at end of session. Log meaningful decisions in
  docs/session-log.md and the phase doc's "Session state"
  section.
- Aggregator math (scoring formula, gate stack, Kelly formula)
  is stable since sessions 31–32. Phase 0 explicitly produced
  no knob changes. Any aggregator change still requires
  explicit sign-off per `CTO_Agent.md` §4.
- Option B rollout order is strict per
  docs/option_b_migration_plan.md. Step 5 (backtest) stays
  blocked until Phase 1's three measurable checks pass. Never
  wire probability_no into scoring until step 5 passes
  pre-registered criteria on held-out data.
- PandaScore is non-fatal: any resolver failure must leave
  structured_match=None and let the pipeline continue. Don't
  add exceptions or hard gates — degraded behavior is the
  contract.
- STRICT: no AI / Claude / "assisted" attribution in code,
  comments, commits, or docs (see CLAUDE.md "Attribution rule").
- Commit per step, locally. No push without explicit ask.
```
