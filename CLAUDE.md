# CLAUDE.md — PQI-MVP (Polymarket Qualitative Intelligence)

## Size budget (read first)

**This file must stay under 40k characters (~8k tokens).** It loads into
every session's context. Detail belongs in `docs/`; the session-by-session
hand-off lives in `NEXT_SESSION_PROMPT.md`; per-session history lives in
`docs/session-log.md`. Before committing an update, run `wc -c CLAUDE.md`
and if you're approaching the budget, **extract a section to `docs/` and
leave a pointer here.**

Rule of thumb: if content would still make sense six months from now with
no session numbers attached, it belongs in CLAUDE.md. Session narrative,
per-task retrospectives, and moment-in-time tuning rationale belong in
`docs/session-log.md` or the relevant phase doc.

---

## Project Mission

Identify mispriced odds in Polymarket event-driven prediction markets using a
7-agent LLM council ("The Council of Seven"). Default operational mode is
**Advisor**: output = top 3 ranked opportunities with confidence scores; a
human reviews and decides whether to trade.

**Approved future modes** (both default to off; each gated on explicit
acceptance criteria):

- **Tier A auto-execution** — auto-BUY on actionable signal, hold to
  resolution. Gated on Phase 1's four-criterion unified acceptance (see
  [`docs/phases/phase_1_signal_quality.md`](docs/phases/phase_1_signal_quality.md)).
  Does not make exit decisions.
- **Tier B auto-execution** — continuous listener with take-profit sells.
  Gated on Phase 2b MFE analysis showing capturable opportunity above fee
  drag (see Phase 2 docs).

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
| Prod LLM      | `claude-haiku-4-5-20251001` | ~$0.015/council run, stays under $20/mo |
| Market data   | Polymarket Gamma API   | Free, public, no auth needed            |
| Price history | Polymarket CLOB API    | Free, public, no auth for read-only     |
| News/search   | Tavily API             | Free tier 1k searches/mo                |
| Esports data  | PandaScore API         | Free tier 1k req/hr; structured match status / scoreline / form (optional, esports only) |
| Fuzzy match   | `rapidfuzz`            | Team-name resolution (Polymarket → PandaScore) |
| Validation    | Pydantic v2            | All inter-module data                   |
| DB (Phase 2)  | SQLite via `aiosqlite` | Local price history cache               |
| Analytics     | DuckDB                 | Read-only SQL over JSONL history        |

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

**Anthropic prompt caching** is on by default
(`ANTHROPIC_PROMPT_CACHE_ENABLED=true`): system prompt wrapped in one
`cache_control: ephemeral` block (5-min TTL). Output is bit-identical; below
the model's min cacheable prefix (~1024–2048 tokens) it's a silent no-op.
See `docs/session-log.md` §Session 27 for cost analysis.

**Centralized Anthropic retry:** every Anthropic call goes through an
exponential-backoff loop in `_chat_anthropic` (SDK auto-retry disabled).
Retryable: 5xx (incl. **529 OverloadedError**), 429, `APIConnectionError`,
`APITimeoutError`. Non-retryable 4xx escapes immediately. Tunables:
`ANTHROPIC_MAX_RETRIES` (5), `ANTHROPIC_RETRY_BASE_DELAY` (1.0s),
`ANTHROPIC_RETRY_MAX_DELAY` (30.0s). The council's `AGENT_MAX_RETRIES` loop
still wraps this. See `docs/session-log.md` §Session 30.

---

## Data Flow (step by step)

```
[main.py] User runs: python -m pqi.main analyze
[polymarket.py] Fetch top N active markets from Gamma API
    → returns List[MarketData] (includes clob_token_ids from clobTokenIds)
[main.py] Display market list → user picks one (or top by volume)
[match_resolver.py] (esports only, non-fatal) Resolve the market to a
    PandaScore match via rapidfuzz fuzzy team-name matching →
    StructuredMatchRecord with authoritative status / scheduled_at /
    final_score / form / H2H. Pipeline continues with structured_match=None
    on any failure.
[synthesizer.py] Given MarketData (+ optional StructuredMatchRecord), run
    Tavily searches → produces BriefingDocument with structured_match
    field + cross-source contamination tagger anchored on PandaScore status.
[clob.py] Fetch YES + NO token price histories from CLOB API in parallel
    (1w hourly) → compute per-side SidePriceHistory → wrap in
    PriceHistory(yes=..., no=...) → inject into
    BriefingDocument.price_history (non-fatal; None if unavailable).
[council.py] Send BriefingDocument to ALL 7 agents in parallel (asyncio).
    Each returns AgentVerdict(probability, confidence, rationale).
[scoring.py] Apply confidence-weighted formula → CouncilResult with
    final_score + ranked verdicts.
[main.py] Rich table printout of recommendation (includes 7d trend line).
```

---

## Pydantic Models (`pqi/models.py`)

These are the **contracts between modules**. Do not skip validation.

Full field-level signatures live in [`docs/models-reference.md`](docs/models-reference.md).
Quick map:

| Model                    | Purpose                                                   |
| ------------------------ | --------------------------------------------------------- |
| `MarketData`             | Gamma market row (id, prices, volume, CLOB token ids)     |
| `SidePriceHistory`       | One side's CLOB trend (avg, direction, volatility, moves) |
| `PriceHistory`           | `yes` + `no` sides (**independent orderbooks**)           |
| `TeamForm`, `H2HMatch`   | PandaScore leaf types                                     |
| `StructuredMatchRecord`  | PandaScore canonical match (esports only)                 |
| `BriefingDocument`       | Synthesizer output: facts, cases, price history, structured match |
| `AgentVerdict`           | One agent's `probability` + `confidence_yes` + `confidence_no` + rationale |
| `CouncilResult`          | Aggregated score, edges, recommendation, pass_reason, Kelly/WR snapshots |
| `PerceivedStats` (stats.py) | Rolling win-rate + per-band stats, feeds EV gate + Kelly |

**Rule:** never pass raw dicts between modules. When adding a field, update
the class, its usage sites, **and** `docs/models-reference.md`.

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
have high confidence in the YES estimate but low confidence in the NO
estimate (e.g. insufficient data on the underdog). Parser has backward
fallback: if only legacy `confidence_level` is found, both sides get the
same value.

**Two-sidedness is autonomy, not algebra (critical).** The YES and NO sides
of every event are *autonomous signals about TeamA vs TeamB* — the evidence
base for each is evaluated independently, not derived as the mirror of the
other. Concretely, today:

- **Prices** — `yes_price` and the CLOB NO-token price live on independent
  orderbooks and do **not** sum to 1.0 (spread / vig). Always fetched
  side-by-side (see `pqi/market/clob.py`). Never infer one from the other.
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
Theme A · A.1 "Option B" and `docs/option_b_migration_plan.md`) and must
not be assumed to exist yet.

**Rules Lawyer injection rule:** In `council.py`, when building the Rules
Lawyer's user message, inject the `resolution_criteria` field first in a
clearly marked block:

```
RESOLUTION CRITERIA (SOURCE OF TRUTH — THIS OVERRIDES ALL OTHER CONTEXT):
{market.resolution_criteria}
--- FULL BRIEFING DOCUMENT BELOW ---
{briefing_doc}
```

All other agents receive only the standard briefing document.

---

## Scoring Formula (`pqi/scoring.py`)

Full gate stack, EV-gate banding, and Kelly details live in
[`docs/scoring-reference.md`](docs/scoring-reference.md). Short-form:

```python
# YES-side score
yes_score = Σ(prob_yes × conf_yes) / Σ(conf_yes)

# NO-side score
no_score  = Σ(prob_no × conf_no) / Σ(conf_no)     # prob_no = 1 − prob_yes

# WR-based edges (informational; gated by EV gate, not by edge)
edge      = perceived_win_rate − market.yes_price
no_edge   = perceived_win_rate − (1 − market.yes_price)
```

Max ROI: `expected_roi = (1 - side_price) / side_price` — a 70¢ share caps
at +42.9%, 80¢ at +25%, 90¢ at +11.1%.

Gate order in `from_council_verdicts()`:

1. Price floor (< `MIN_TRADEABLE_PRICE` 0.09) → PASS
2. Price ceiling (> `MAX_TRADEABLE_PRICE` 0.90) → PASS
3. **Contrarian** (council disagrees with market): `prob ≥ 0.60 &
   conf ≥ 0.80 & ROI ≥ 0.20` → label `CONTRARIAN`
4. **Market-aligned**: `prob ≥ 0.60 & conf ≥ 0.67 & ROI ≥ 0.10` → label
   `HIGH/MODERATE/LOW VALUE` by ROI band
5. **EV gate (banded, last)**: reject BUYs where `side_price ≥ band_wr`
   (Beta-binomial smoothed rate for the price band, `BAND_SHRINKAGE_K=5`
   default). Dormant when `sample_size < MIN_WIN_RATE_SAMPLES` (20).
6. Otherwise → PASS with dynamic `pass_reason`.

Signal types (market-aligned): `HIGH VALUE` (ROI ≥ 50%), `MODERATE VALUE`
(25–50%), `LOW MARGIN` (10–25%). Plus `CONTRARIAN` label from gate 3.

`EDGE_THRESHOLD` is **deprecated** (kept so legacy `.env` files don't
error). `edge`/`no_edge` were repurposed in session 23 from `(score −
price)` to `(PerceivedTrueWinRate − side_price)`; pre-session-23 history
entries use the old semantic (`history show` labels them).

---

## Coding Conventions

- All inter-module data transfer via Pydantic models (no raw dicts)
- All LLM calls go through `pqi/llm/client.py` — never import
  anthropic/ollama elsewhere
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

**True code constants** (API base URLs, valid enum sets) stay as Python
literals in `config.py` and do NOT appear in `.env.example`.

**The test:** if you'd want to change it to adjust behaviour without
redeploying code, it belongs in `.env`. If it would only make sense to
change in a code edit, it stays as a literal.

**When adding any new configurable value:**

1. Add `VAR: type = os.getenv("VAR", "default")` to `config.py`
2. Add the key to `.env.example` with a `# comment` explaining what it does

---

## What NOT To Do

- Do not add a web server, REST API, or database in Phase 1
- Do not use streaming LLM responses (parse full output only)
- Do not add agent-to-agent communication — they deliberate in parallel,
  not sequentially
- Do not use JSON mode for agent output — use regex parsing for Ollama
  compatibility

---

## Extended Reference (read when working on specific areas)

| Topic | File | When to read |
|-------|------|-------------|
| **Detailed Pydantic model signatures** | [`docs/models-reference.md`](docs/models-reference.md) | Changing a model contract or adding a field |
| **Full scoring gate stack + Kelly + EV gate details** | [`docs/scoring-reference.md`](docs/scoring-reference.md) | Touching `scoring.py` / `stats.py` / gate tuning |
| **Next-session hand-off (priorities, monitoring, instructions)** | [`NEXT_SESSION_PROMPT.md`](NEXT_SESSION_PROMPT.md) | Start of every session |
| Gamma, CLOB, Tavily, PandaScore API details | [`docs/api-reference.md`](docs/api-reference.md) | Touching polymarket.py, clob.py, synthesizer.py, match_resolver.py, config.py |
| Known issues & API quirks | [`docs/known-issues.md`](docs/known-issues.md) | Debugging unexpected API behavior or scoring edge cases |
| Session changelog & implementation details | [`docs/session-log.md`](docs/session-log.md) | Understanding why something was built a certain way |
| Roadmap & future work (themed catalog A/B/C) | [`docs/roadmap.md`](docs/roadmap.md) | Planning next priorities or checking what's been done |
| Phase index & status | [`docs/phases/README.md`](docs/phases/README.md) | Pick up a phase task / check dependencies |
| Option B rollout plan | [`docs/option_b_migration_plan.md`](docs/option_b_migration_plan.md) | Any work touching `probability_no` / per-side probability |
| Scaling assessment (v2.0 RFC triage) | [`docs/scaling_assessment.md`](docs/scaling_assessment.md) | Architecture / infra decisions |
| Architecture diagram (Mermaid) | [`docs/architecture.md`](docs/architecture.md) | High-level system overview |
| Analyze pipeline diagram (Mermaid) | [`docs/analyze-pipeline.md`](docs/analyze-pipeline.md) | Detailed pipeline flow |
| Analytics starter queries | [`docs/analytics_queries.md`](docs/analytics_queries.md) | Using `pqi query "<SQL>"` |
| Phase 0 retrospective | [`docs/retrospectives/phase_0_edge_2026_04.md`](docs/retrospectives/phase_0_edge_2026_04.md) | Reference for P0 verdict (council Brier, CLV, per-band) |

---

## Session Protocol

### Rules for Claude Code (read every session)

- At the START of every session, read:
  1. `## Current State` in this file — source of truth for what exists.
  2. [`NEXT_SESSION_PROMPT.md`](NEXT_SESSION_PROMPT.md) — detailed hand-off
     with priorities, monitoring tasks, and instructions.
- At the END of every session (or when asked), you MUST:
  1. Update `## Current State` below.
  2. Refresh `NEXT_SESSION_PROMPT.md` so the next session has the latest
     priority + monitoring list.
  3. Log the session entry in `docs/session-log.md` and the active phase
     doc's "Session state" section.
  4. **Check `wc -c CLAUDE.md` is under 40k.** If over, extract to `docs/`
     or `NEXT_SESSION_PROMPT.md` and leave a pointer here. Session detail
     goes in `docs/session-log.md`, not Current State.

Never leave a session without doing this. It is as important as the code.

---

## Current State

**Last updated:** session 41 — **Phase 1 rollout stage 1 LIVE**
(`PERSONA_V2_PREAMBLE=true` in `.env`). V2 preamble text refined this
session before flipping: the single worked example became a three-case
spectrum (mild / severe / symmetric evidence asymmetry) and the
anti-mirror check was softened from a hard trigger to a signal pattern
(sum-near-100 PAIRED WITH matched confidences — not the sum alone).
`scripts/reliability_diagram.py` gained a `--flag <label>` argument
(`v1`, `v2:preamble`, `v2:*` glob) so v1 and v2 populations render as
independent diagrams with flag-suffixed filenames. Session 41 earlier
also shipped `run_context` persistence on every history entry and a
raw CLOB-trendlines sidecar + backfill — both infrastructure, no
pipeline or aggregator touch.

Rollout observation state: **16 V2 runs queued** (112 `v2:preamble`
verdicts in DuckDB), **0 resolved** yet. The refreshed 2026-04-19
baseline report is 100% v1 (409 rows): council Brier 0.2156, Bear
Brier 0.2820, cluster r≥0.85 fraction 9/21 (43%), Bear still
structurally independent (r≈0 with the 5-agent cluster). V2
measurement waits on resolutions.

Phase 0 verdict still stands: inconclusive at current n (65 resolved
BUYs / 385 original reliability rows). Council Brier 0.2176 beats 0.25
chance baseline by ~3pp; mean pre-event CLV −0.0167 on n=44; no band
cleared ≥1 Wilson-CI-width flag. Realized April PnL −0.575/share. Full
retrospective:
[`docs/retrospectives/phase_0_edge_2026_04.md`](docs/retrospectives/phase_0_edge_2026_04.md).

**Highest-leverage next move: observe stage 1, don't escalate.** Let
resolutions accumulate until ≥20 V2 rows resolve, then run
`python -m pqi.scripts.reliability_diagram --flag v1` and
`--flag v2:preamble` side-by-side against the four-criterion unified
acceptance. Only flip `PERSONA_V2_PREAMBLE=false` and move to stage 2
after stage 1 has a verdict — not before. Gate knobs explicitly *not*
retuned — `BAND_SHRINKAGE_K=5`, `MIN_CONTRARIAN_PROBABILITY=0.60`,
ROI bands, Kelly multiplier, `MIN_WIN_RATE_SAMPLES` all stand.

Four-criterion acceptance (all must pass simultaneously vs the v1
frame): cluster-pair r ≥ 0.85 fraction below 0.50; per-agent `|drift|`
> 0.10 on ≥2 agents; Bear Brier toward 0.22 with independence
preserved; council Brier holds or improves from 0.2176.

Phase 2 split into three sub-phases (session 39): Phase 2a (Tier A
auto-BUY, gated on P1), Phase 2b (MFE analysis, parallel-able with P1),
Phase 2c (MFE listener, gated on P2b verdict = "chase"). Phase 3
(deployment portability) deferred — P0 verdict inconclusive, not
positive. Detailed priorities, acceptance criteria, and monitoring tasks
live in [`NEXT_SESSION_PROMPT.md`](NEXT_SESSION_PROMPT.md).

### Phase status

| New phase | Status | Doc |
| --- | --- | --- |
| Phase 0 — edge validation | **done (verdict: inconclusive; retrospective written)** | [`docs/phases/phase_0_edge_validation.md`](docs/phases/phase_0_edge_validation.md) |
| Phase 1 — signal quality (Option B autonomy fix) | **next — highest-leverage per P0 verdict** | [`docs/phases/phase_1_signal_quality.md`](docs/phases/phase_1_signal_quality.md) |
| Phase 2a — Tier A auto-BUY (auto-BUY-and-hold) | blocked on Phase 1 unified acceptance | [`docs/phases/phase_2a_tier_a_auto_buy.md`](docs/phases/phase_2a_tier_a_auto_buy.md) |
| Phase 2b — MFE analysis (retroactive + chronological train/test) | scoping unblocked (parallel with P1) | [`docs/phases/phase_2b_mfe_analysis.md`](docs/phases/phase_2b_mfe_analysis.md) |
| Phase 2c — MFE listener prototype (Tier B precursor) | deferred — gated on P2b verdict = "chase" | [`docs/phases/phase_2c_mfe_listener.md`](docs/phases/phase_2c_mfe_listener.md) |
| Phase 3 — deployment portability (Docker/VPS) | deferred — P0 inconclusive, not positive | [`docs/phases/phase_3_deployment_portability.md`](docs/phases/phase_3_deployment_portability.md) |
| Phase 4 — scaling triggers | reactive only | [`docs/phases/phase_4_scaling_triggers.md`](docs/phases/phase_4_scaling_triggers.md) |

### Aggregator stability

Sessions 22–24 established the pre-EV scoring contract as stable. Sessions
25 (dynamic weights), 26 (Option B 1–4), 28 (PandaScore + temporal
disambiguation), 29 (category cache), 30 (Anthropic retry) preserved it.
Session 31 was the first aggregator-touching change since session 23: the
EV gate's threshold became band-specific and Kelly was realigned to the
banded rate. Session 32 retuned one gate knob and reworded a user-facing
string, but kept the aggregator math identical. Sessions 34–39 were
read-only analytics / measurement / synthesis / documentation with no
pipeline or aggregator touch. Session 40 added Phase 1 rollout scaffold
(prompt assembly, flags, analytics tagging on `AgentVerdict`) with zero
aggregator changes. Session 41 refined `_SHARED_PREAMBLE_V2`, flipped
`PERSONA_V2_PREAMBLE=true`, persisted `run_context` per history row,
added a raw CLOB-trendlines sidecar, and extended `reliability_diagram`
with `--flag` — still zero aggregator changes; scoring still reads
`probability` + the two `confidence_*` fields exactly as before. See
`docs/session-log.md` for per-session detail.

**Option B shadow layer** (session 26, steps 1–4): `AgentVerdict.probability_yes`
/ `probability_no` / `probability_source` persisted; all 7 personas emit
`probability_no_score`; `pqi history stats` renders a shadow panel + per-agent
drift/Brier table. **Descriptive only — scoring still aggregates from the
single `probability` field.** Step 5 is blocked on **signal quality** not
sample count — fix the SHARED_PREAMBLE autonomy framing in Phase 1 before
running the backtest. See
[`docs/option_b_migration_plan.md`](docs/option_b_migration_plan.md).

### What's live

- Full analyze pipeline: Gamma → Tavily briefing → CLOB price history →
  Council → Scoring → CLI output.
- 7-agent council with two-sided confidence, quorum gate, retry with
  exponential backoff.
- Probability-first recommendation: BUY YES / BUY NO / PASS with ROI-banded
  signal types.
- **Banded EV gate** (session 31) + **WR-based edges** (session 23) +
  **Kelly sizing** (half-Kelly default) — full detail in
  [`docs/scoring-reference.md`](docs/scoring-reference.md).
- **Dynamic per-agent reliability weights** (session 25,
  `pqi/agent_weights.py`) — snapshot at `~/.pqi/agent_weights.json`.
- **Option B shadow layer** (session 26) — descriptive only.
- **Anthropic prompt caching** (session 27) + **centralized retry** (session 30).
- **Temporal disambiguation + PandaScore** (session 28, esports only,
  non-fatal).
- **Category-resolution cache** (session 29, `~/.pqi/category_cache.json`).
- Two-sided CLOB price history enrichment (YES + NO, non-fatal).
- History system: persistence, resolution sync, per-agent accuracy, postmortem.
- SQLite cache with price-delta + structured-status invalidation.
- **DuckDB analytics layer** (sessions 34–35, `pqi/analytics_sql.py`):
  `history` + `verdicts` views, plus conditional `clv_by_row` view over
  `~/.pqi/clv.jsonl`.
- **Pre-event CLV backfill** (session 35, `scripts/backfill_clv.py`).
- **Reliability diagram** (session 36, `scripts/reliability_diagram.py`):
  versioned markdown report at `~/.pqi/reports/reliability_YYYYMMDD.md`.
  `--flag <label>` (session 41) filters resolved rows by Phase 1 rollout
  label (`v1`, `v2:preamble`, `v2:*`); flag-suffixed output filenames.
- **Per-band calibration retrospective** (session 37, no code) and
  **Phase 0 verdict synthesis** (session 38, no code) in
  `docs/retrospectives/phase_0_edge_2026_04.md`.
- CLI: `analyze` (with `--category`, `--top`, `--live`, `--starts-within`,
  …), `categories`, `query "<SQL>"`, `history` (list/show/sync/stats).

### Environment

- LLM_BACKEND: `anthropic` (claude-haiku-4-5-20251001)
- Ollama: installed, llama3.1:8b as fallback
- API keys: `CLAUDE_API_KEY` + `TAVILY_API_KEY` set in .env
- Optional: `PANDASCORE_API_KEY` for esports structured data

---

## Next Session Prompt

The full hand-off note — priorities, monitoring tasks, instructions — lives
in [`NEXT_SESSION_PROMPT.md`](NEXT_SESSION_PROMPT.md). Read it at the start
of every session and refresh it at the end.
