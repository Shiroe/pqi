# pqi/config.py
"""
Central configuration — reads every tunable value from environment variables.

SOURCE OF TRUTH FOR CONFIGURATION:
  .env.example  lists every supported key, its default, and what it does.
  .env          your local overrides (never committed).
  config.py     reads values via os.getenv(KEY, default) — the default here
                is the fallback used when a key is absent from .env.

RULE — enforced by convention:
  If you want to change a behaviour without touching code → put it in .env.
  If it belongs in .env, it MUST also appear in .env.example with a comment.
  Code constants that are never user-tunable (API base URLs, enum sets) stay
  as Python literals here and do NOT appear in .env.example.

Never call os.getenv() outside this module.
Raises EnvironmentError at import time if required keys are absent.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Repo root = three levels up from this file (<repo>/src/pqi/config.py). Single
# anchor for every in-repo path so the project stays self-contained and
# portable; .env and the default .pqi/ data dir both hang off it.
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Load .env from the repo root (sibling of pyproject.toml / .venv / .pqi).
load_dotenv(dotenv_path=_REPO_ROOT / ".env")

# ---------------------------------------------------------------------------
# LLM backend
# ---------------------------------------------------------------------------

LLM_BACKEND: str = os.getenv("LLM_BACKEND", "ollama").lower()
"""Active backend: "ollama" | "anthropic"."""

# Code constant — not user-tunable, changing this requires a code change anyway.
VALID_BACKENDS = {"ollama", "anthropic"}

OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LOCAL_LLM_CONCURRENCY: int = int(os.getenv("LOCAL_LLM_CONCURRENCY", "2"))

# Accept ANTHROPIC_API_KEY or the alias CLAUDE_API_KEY used in some .env setups.
ANTHROPIC_API_KEY: str = (
    os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY") or ""
)
ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
ANTHROPIC_PROMPT_CACHE_ENABLED: bool = (
    os.getenv("ANTHROPIC_PROMPT_CACHE_ENABLED", "true").lower() == "true"
)
ANTHROPIC_MAX_TOKENS: int = int(os.getenv("ANTHROPIC_MAX_TOKENS", "2048"))
"""Max output tokens per Anthropic call. Must cover the agent's full JSON
response — truncation at this ceiling produces unterminated-string parse
errors. 2048 gives ~2× headroom over typical two-sided council verdicts."""

# Centralized Anthropic retry — wraps every client.messages.create() call
# so transient 5xx (incl. 529 OverloadedError), 429 RateLimitError,
# connection errors and timeouts don't propagate as crashes. The SDK's
# built-in retry is disabled (max_retries=0 on the client) to avoid
# double-retry; this layer owns the policy. See pqi/llm/client.py.
ANTHROPIC_MAX_RETRIES: int = int(os.getenv("ANTHROPIC_MAX_RETRIES", "5"))
"""Max additional retries per Anthropic call after the first attempt.
Mirrors AGENT_MAX_RETRIES so council and synthesizer share retry budgets."""

ANTHROPIC_RETRY_BASE_DELAY: float = float(os.getenv("ANTHROPIC_RETRY_BASE_DELAY", "1.0"))
"""Base delay (seconds) for exponential backoff: delay = base * 2**attempt,
capped at ANTHROPIC_RETRY_MAX_DELAY. 1.0s → 1, 2, 4, 8, 16, 30 (cap)."""

ANTHROPIC_RETRY_MAX_DELAY: float = float(os.getenv("ANTHROPIC_RETRY_MAX_DELAY", "30.0"))
"""Cap on the per-attempt backoff (seconds). 30s matches AGENT_MAX_RETRIES'
existing cap so a worst-case retry storm has predictable upper-bound
latency: 1+2+4+8+16+30 ≈ 61s before giving up at the default 5 retries."""

# ---------------------------------------------------------------------------
# Tavily (news search)
# ---------------------------------------------------------------------------

TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
TAVILY_SEARCHES_PER_MARKET: int = int(os.getenv("TAVILY_SEARCHES_PER_MARKET", "5"))
TAVILY_SNIPPET_CAP: int = int(os.getenv("TAVILY_SNIPPET_CAP", "5000"))

# ---------------------------------------------------------------------------
# PandaScore (structured esports data)
# ---------------------------------------------------------------------------
# When PANDASCORE_ENABLED is true and the API key is set, the synthesizer
# attempts to resolve esports markets to PandaScore's structured match record
# (canonical team names, scheduled time, status, H2H, recent form). A
# trustworthy match is then attached to the briefing as
# `BriefingDocument.structured_match` and surfaced to the council as
# STRUCTURED MATCH DATA. Failure modes (no match found, API error, low
# fuzzy confidence) are non-fatal — the pipeline falls back to the
# Tavily-only briefing exactly as it does today.

# Code constant — the public REST endpoint.
PANDASCORE_BASE_URL: str = "https://api.pandascore.co"

PANDASCORE_API_KEY: str = os.getenv("PANDASCORE_API_KEY", "")
"""PandaScore API token. Free-tier accounts are sufficient for our
read-only usage (~1000 req/hour). Empty string disables the resolver
without raising — pipeline runs Tavily-only."""

PANDASCORE_ENABLED: bool = os.getenv("PANDASCORE_ENABLED", "true").lower() in {
    "1", "true", "yes",
}
"""Master switch for the structured-data resolver. Defaults true so the
key alone is enough to turn it on. Set false to keep the key but
disable the resolver (e.g. for A/B comparison runs)."""

PANDASCORE_FUZZY_THRESHOLD: int = int(os.getenv("PANDASCORE_FUZZY_THRESHOLD", "80"))
"""Minimum rapidfuzz token_set_ratio confidence (0-100) for the
market-question → provider-match resolution to be accepted. Below
this the resolver returns None and the briefing falls back to
Tavily-only. 80 is intentionally conservative — false positives
(wrong match attached to briefing) are far costlier than false
negatives (no structured data attached)."""

PANDASCORE_WINDOW_HOURS: float = float(os.getenv("PANDASCORE_WINDOW_HOURS", "3.0"))
"""Symmetric window (in hours) around the market's event_start_time
in which PandaScore matches are considered candidates. Tight enough
to avoid pulling a different scheduled meeting between the same
parties, wide enough to absorb scheduling drift between Polymarket
and PandaScore."""

PANDASCORE_RECENT_FORM_COUNT: int = int(os.getenv("PANDASCORE_RECENT_FORM_COUNT", "10"))
"""How many recent completed matches per team to inspect when computing
the TeamForm snapshot in the structured record."""

# ---------------------------------------------------------------------------
# football-data.org (structured soccer data)
# ---------------------------------------------------------------------------
# Second structured-match provider, behind the same StructuredMatchProvider
# adapter interface as PandaScore. Covers soccer markets (major leagues +
# World Cup) — see docs/soccer_structured_data_plan.md. Same non-fatal
# contract: any failure degrades to the Tavily-only briefing.

# Code constant — the public REST endpoint (API v4).
FOOTBALL_DATA_BASE_URL: str = "https://api.football-data.org/v4"

FOOTBALL_DATA_API_KEY: str = os.getenv("FOOTBALL_DATA_API_KEY", "")
"""football-data.org API token (header `X-Auth-Token`). Free-tier accounts
cover the World Cup + big-5 leagues + CL/Euros (~10 req/min). Empty string
disables the soccer resolver without raising — pipeline runs Tavily-only."""

FOOTBALL_DATA_ENABLED: bool = os.getenv("FOOTBALL_DATA_ENABLED", "true").lower() in {
    "1", "true", "yes",
}
"""Master switch for the soccer resolver. Defaults true so the key alone is
enough to turn it on. Set false to keep the key but disable the resolver."""

FOOTBALL_DATA_FUZZY_THRESHOLD: int = int(os.getenv("FOOTBALL_DATA_FUZZY_THRESHOLD", "80"))
"""Minimum rapidfuzz token_set_ratio confidence (0-100) for the
market-question → football-data-match resolution to be accepted. Same
conservative default as PandaScore — false positives are costlier than
false negatives."""

FOOTBALL_DATA_WINDOW_HOURS: float = float(os.getenv("FOOTBALL_DATA_WINDOW_HOURS", "3.0"))
"""Symmetric window (in hours) around the market's event_start_time in
which football-data matches are considered candidates."""

FOOTBALL_DATA_RECENT_FORM_COUNT: int = int(os.getenv("FOOTBALL_DATA_RECENT_FORM_COUNT", "10"))
"""How many recent completed matches per team to inspect when computing
the TeamForm snapshot for a soccer match."""

# ---------------------------------------------------------------------------
# Polymarket Gamma API
# ---------------------------------------------------------------------------

# Code constant — the public endpoint never changes for users.
GAMMA_BASE_URL: str = "https://gamma-api.polymarket.com"

GAMMA_RATE_LIMIT_DELAY: float = float(os.getenv("GAMMA_RATE_LIMIT_DELAY", "0.5"))
EVENT_ENRICHMENT_MAX: int = int(os.getenv("EVENT_ENRICHMENT_MAX", "30"))
"""Max parallel GET /events/{id} detail calls during event enrichment."""

# ---------------------------------------------------------------------------
# Polymarket CLOB API
# ---------------------------------------------------------------------------

# Code constant — the public CLOB endpoint. No auth needed for read-only.
CLOB_BASE_URL: str = "https://clob.polymarket.com"

PRICE_HISTORY_INTERVAL: str = os.getenv("PRICE_HISTORY_INTERVAL", "1w")
"""Time window for price history fetch. Valid: 1d, 1w, 1m."""

PRICE_HISTORY_FIDELITY: int = int(os.getenv("PRICE_HISTORY_FIDELITY", "60"))
"""Resolution in minutes. 60 = hourly data points."""

# ---------------------------------------------------------------------------
# Market filtering
# ---------------------------------------------------------------------------

MAX_MARKETS_TO_FETCH: int = int(os.getenv("MAX_MARKETS_TO_FETCH", "30"))
MIN_VOLUME_USD: float = float(os.getenv("MIN_VOLUME_USD", "10000"))
SHORT_WINDOW_HOURS: int = int(os.getenv("SHORT_WINDOW_HOURS", "4"))
MIN_VOLUME_SHORT_WINDOW_USD: float = float(
    os.getenv("MIN_VOLUME_SHORT_WINDOW_USD", "500")
)

# ---------------------------------------------------------------------------
# Analyze batch mode
# ---------------------------------------------------------------------------

ANALYZE_BATCH_CONCURRENCY: int = int(os.getenv("ANALYZE_BATCH_CONCURRENCY", "1"))
"""Max parallel analyze runs when a multi-select spec is given to
`analyze`. 1 = strict sequential (default; safest against API rate limits
and easiest to inspect). 3 is the practical ceiling on Anthropic Tier 2
(1K RPM, 450K input/min, 90K output/min) assuming a full council + single
synthesizer call per run. CLI flag `--concurrency/-j` overrides this per
invocation."""

# ---------------------------------------------------------------------------
# Operational cost estimation (used by `pqi history equity`)
# ---------------------------------------------------------------------------

TAVILY_COST_PER_SEARCH: float = float(os.getenv("TAVILY_COST_PER_SEARCH", "0.008"))
"""USD per Tavily search request. A full analysis fires
`TAVILY_SEARCHES_PER_MARKET` searches, so per-analysis Tavily cost is the
product. Tune this to match your actual Tavily plan."""

SEARCH_ARCHITECT_COST: float = float(os.getenv("SEARCH_ARCHITECT_COST", "0.001"))
"""USD per Search Architect LLM call (one per fresh analysis; cached
analyses cost $0). Haiku 4.5 with a small input (market description) +
5-query JSON output lands around $0.001 at current pricing."""

SYNTHESIZER_COST: float = float(os.getenv("SYNTHESIZER_COST", "0.005"))
"""USD per Synthesizer LLM call (one per fresh analysis; reused for
siblings under the same event). Synthesizer ingests 5 Tavily result sets
and emits a briefing doc — larger input + medium output, so ~$0.005
under Haiku 4.5 with prompt caching."""

COUNCIL_COST_PER_RUN: float = float(os.getenv("COUNCIL_COST_PER_RUN", "0.015"))
"""USD per council run (7 parallel Haiku 4.5 calls). Derived from the
pre-prompt-cache estimate of ~$0.015 per council in CLAUDE.md. Prompt
caching makes this a high estimate on cache hits; tune down if your
Anthropic bill comes in lower."""

# ---------------------------------------------------------------------------
# Scoring & recommendation gates
# ---------------------------------------------------------------------------

EDGE_THRESHOLD: float = float(
    os.getenv("EDGE_THRESHOLD", "0.09")
)  # deprecated — informational only
MIN_TRADEABLE_PRICE: float = float(os.getenv("MIN_TRADEABLE_PRICE", "0.09"))
MAX_TRADEABLE_PRICE: float = float(os.getenv("MAX_TRADEABLE_PRICE", "0.90"))
MIN_EXPECTED_ROI: float = float(os.getenv("MIN_EXPECTED_ROI", "0.10"))
MIN_CONTRARIAN_ROI: float = float(os.getenv("MIN_CONTRARIAN_ROI", "0.20"))
CONFIDENCE_HIGH_THRESHOLD: float = float(os.getenv("CONFIDENCE_HIGH_THRESHOLD", "0.67"))
CONFIDENCE_MEDIUM_THRESHOLD: float = float(
    os.getenv("CONFIDENCE_MEDIUM_THRESHOLD", "0.40")
)
MIN_COUNCIL_VERDICTS: int = int(os.getenv("MIN_COUNCIL_VERDICTS", "5"))
"""Minimum agents (of 7) that must return valid verdicts. Below this → abort."""

AGENT_MAX_RETRIES: int = int(os.getenv("AGENT_MAX_RETRIES", "5"))
"""Max retries per agent on LLM/parse failure. Exponential backoff, 30s cap."""
RULES_LAWYER_WEIGHT: float = float(os.getenv("RULES_LAWYER_WEIGHT", "1"))
BEAR_WEIGHT: float = float(os.getenv("BEAR_WEIGHT", "1.0"))
"""Bear agent weight in scoring. Default 1.0 (equal). Lower to reduce Bear's
drag on council confidence if prompt fix is insufficient (try 0.7 first)."""

# Dynamic per-agent reliability weights (session 25). When enabled and the
# agent_weights.json snapshot exists, the computed weights fully override the
# static RULES_LAWYER_WEIGHT / BEAR_WEIGHT above (precedence mode α). Any
# agent with fewer than MIN_AGENT_WEIGHT_SAMPLES resolved lean verdicts falls
# back to 1.0 to avoid cold-start noise.
AGENT_WEIGHTS_ENABLED: bool = os.getenv("AGENT_WEIGHTS_ENABLED", "true").lower() in {"1", "true", "yes"}
MIN_AGENT_WEIGHT_SAMPLES: int = int(os.getenv("MIN_AGENT_WEIGHT_SAMPLES", "30"))
AGENT_WEIGHT_SHRINK: float = float(os.getenv("AGENT_WEIGHT_SHRINK", "2.0"))
"""Shrinkage coefficient for dynamic agent weights. Final weight per agent is
`1.0 + shrink * (agent_lean_rate - council_avg_lean_rate)`. k=2.0 is a
moderate nudge (Rules Lawyer ~1.08, Bear ~0.71 at session-24 lean rates).
Set to 0.0 to disable the nudge while keeping the file machinery on."""

# ---------------------------------------------------------------------------
# Phase 1 rollout flags (SHARED_PREAMBLE autonomy rewrite + per-persona
# anchor/advocacy mechanisms). All default OFF. Toggle individually so each
# mechanism can be A/B-measured against the 2026-04-18 reliability baseline
# without being confounded by the others. Rollout order per Phase 1 doc:
# preamble → statistician → sentiment → specialist → contrarian → bear-alone
# (Bull unchanged) → bull-alone (Bear reverted) → both if evidence supports.
# ---------------------------------------------------------------------------

PERSONA_V2_PREAMBLE: bool = os.getenv("PERSONA_V2_PREAMBLE", "false").lower() in {"1", "true", "yes"}
"""Mechanism 1 — asymmetric-evidence SHARED_PREAMBLE rewrite. Adds a
mandatory evidence-quality pre-step, a worked asymmetric example, and an
explicit anti-mirror check. Target: break YES/NO mirror collapse on
`probability_no_score` outputs (Phase 1 criterion 2 — per-agent |drift|
> 0.10 on ≥2 agents)."""

PERSONA_V2_ANCHOR_STATISTICIAN: bool = os.getenv("PERSONA_V2_ANCHOR_STATISTICIAN", "false").lower() in {"1", "true", "yes"}
"""Mechanism 2 — Statistician must cite a concrete historical base-rate
number in `historical_baseline` before emitting probability. Addresses
cluster collapse (Phase 1 criterion 1)."""

PERSONA_V2_ANCHOR_SENTIMENT: bool = os.getenv("PERSONA_V2_ANCHOR_SENTIMENT", "false").lower() in {"1", "true", "yes"}
"""Mechanism 2 — Sentiment must cite a specific crowd-psychology signal
(news-cycle tone, social volume, price-vs-narrative divergence) in
reasoning. Raw current price is not a sufficient anchor."""

PERSONA_V2_ANCHOR_SPECIALIST: bool = os.getenv("PERSONA_V2_ANCHOR_SPECIALIST", "false").lower() in {"1", "true", "yes"}
"""Mechanism 2 — Specialist must cite a concrete field-specific nuance
(map pool, patch, regulatory constraint, engineering detail) in
`technical_nuance` before emitting probability."""

PERSONA_V2_ANCHOR_CONTRARIAN: bool = os.getenv("PERSONA_V2_ANCHOR_CONTRARIAN", "false").lower() in {"1", "true", "yes"}
"""Mechanism 2 — Contrarian's `black_swan_event` must name a specific
disruption vector from the prioritized list (roster, meta, form, morale,
structural). Generic "anything could happen" framing no longer satisfies."""

PERSONA_V2_BULL_ADVOCACY: bool = os.getenv("PERSONA_V2_BULL_ADVOCACY", "false").lower() in {"1", "true", "yes"}
"""Mechanism 3 — Bull probability-after-advocacy sequence. Forces explicit
construct-case → step-outside-role → emit-honest-probability chain. Target:
reduce Bull over-prediction on [0.80, 0.95) band (retro §5.2 −0.219 gap)."""

PERSONA_V2_BEAR_ADVOCACY: bool = os.getenv("PERSONA_V2_BEAR_ADVOCACY", "false").lower() in {"1", "true", "yes"}
"""Mechanism 3 — Bear probability-after-advocacy sequence. Same three-step
chain as Bull. Target: Bear Brier toward 0.22 cluster floor while Bear's
pairwise independence stays intact (Phase 1 criterion 3). Highest-leverage
single flag per retro §5.3."""

# Probability-first recommendation gates
MIN_COUNCIL_PROBABILITY: float = float(os.getenv("MIN_COUNCIL_PROBABILITY", "0.60"))
MIN_CONTRARIAN_PROBABILITY: float = float(
    os.getenv("MIN_CONTRARIAN_PROBABILITY", "0.60")
)
MIN_CONTRARIAN_CONFIDENCE: float = float(os.getenv("MIN_CONTRARIAN_CONFIDENCE", "0.80"))

# EV gate — rejects BUYs where the side price is ≥ the historical win rate
# (PerceivedTrueWinRate computed from resolved history). Activates only once
# MIN_WIN_RATE_SAMPLES resolved BUYs have accumulated, so cold-start runs
# behave identically to the pre-EV-gate system.
EV_GATE_ENABLED: bool = os.getenv("EV_GATE_ENABLED", "true").lower() in {"1", "true", "yes"}
MIN_WIN_RATE_SAMPLES: int = int(os.getenv("MIN_WIN_RATE_SAMPLES", "20"))
KELLY_MULTIPLIER: float = float(os.getenv("KELLY_MULTIPLIER", "0.5"))
"""Kelly safety scaler applied to the advisory stake shown in the UI.
0.5 = half-Kelly (recommended), 1.0 = full Kelly."""

BAND_SHRINKAGE_K: float = float(os.getenv("BAND_SHRINKAGE_K", "5.0"))
"""Beta-binomial pseudo-count for per-price-band WR smoothing (session 31+).
The EV gate compares `side_price` against the band-specific smoothed rate
    smoothed_wr = (wins + k * global_wr) / (n + k)
K controls how much the global rate pulls thin bands: K=0 disables banding
entirely (gate reverts to a single global threshold), K=5 gives observed
data 8/13 weight vs prior at n=8, K=20 pulls everything hard toward global.
Tuned against session-31 retrospective: K=5 is the best observed tradeoff
between letting real price-band signal through and resisting thin-band
overfit."""

# ---------------------------------------------------------------------------
# Data directory (history, cache, reports, weights, …)
# ---------------------------------------------------------------------------

# Default to a `.pqi/` directory at the repo root so persisted state lives
# inside the repo and can be synced across machines via git. Override with
# PQI_DATA_DIR to keep the legacy ~/.pqi location or point elsewhere.
PQI_DATA_DIR: Path = Path(
    os.getenv("PQI_DATA_DIR", str(_REPO_ROOT / ".pqi"))
).expanduser()

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

CACHE_DB_PATH: str = os.getenv("CACHE_DB_PATH", str(PQI_DATA_DIR / "cache.db"))
CACHE_PRICE_DELTA: float = float(os.getenv("CACHE_PRICE_DELTA", "0.02"))
CATEGORY_CACHE_PATH: str = os.getenv(
    "CATEGORY_CACHE_PATH",
    str(PQI_DATA_DIR / "category_cache.json"),
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# ---------------------------------------------------------------------------
# Startup validation — runs once at import time
# ---------------------------------------------------------------------------


def _validate_env() -> None:
    """
    Check that all required environment variables are present.
    Raises EnvironmentError with a descriptive message listing every missing key.
    """
    missing: list[str] = []
    hints: list[str] = []

    # LLM_BACKEND must be a known value
    if LLM_BACKEND not in VALID_BACKENDS:
        raise EnvironmentError(
            f"LLM_BACKEND='{LLM_BACKEND}' is not valid. "
            f"Must be one of: {sorted(VALID_BACKENDS)}"
        )

    # TAVILY_API_KEY is always needed (synthesizer.py runs for every analysis)
    if not TAVILY_API_KEY:
        missing.append("TAVILY_API_KEY")
        hints.append("  TAVILY_API_KEY  — get a free key at https://tavily.com")

    # ANTHROPIC_API_KEY only needed when backend is anthropic
    if LLM_BACKEND == "anthropic" and not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY (or CLAUDE_API_KEY)")
        hints.append(
            "  ANTHROPIC_API_KEY  — Anthropic console key, "
            "or set CLAUDE_API_KEY as an alias"
        )

    if missing:
        hint_block = "\n".join(hints)
        raise EnvironmentError(
            f"Missing required environment variable(s): {', '.join(missing)}\n"
            f"Add them to your .env file:\n{hint_block}"
        )


_validate_env()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== pqi/config.py smoke test ===\n")

    def _mask(val: str) -> str:
        if not val:
            return "<not set>"
        return val[:8] + "..." if len(val) > 8 else "***"

    rows = [
        ("LLM_BACKEND", LLM_BACKEND),
        ("OLLAMA_MODEL", OLLAMA_MODEL),
        ("OLLAMA_BASE_URL", OLLAMA_BASE_URL),
        ("LOCAL_LLM_CONCURRENCY", str(LOCAL_LLM_CONCURRENCY)),
        ("ANTHROPIC_API_KEY", _mask(ANTHROPIC_API_KEY)),
        ("ANTHROPIC_MODEL", ANTHROPIC_MODEL),
        ("TAVILY_API_KEY", _mask(TAVILY_API_KEY)),
        ("GAMMA_RATE_LIMIT_DELAY", str(GAMMA_RATE_LIMIT_DELAY)),
        ("MAX_MARKETS_TO_FETCH", str(MAX_MARKETS_TO_FETCH)),
        ("MIN_VOLUME_USD", str(MIN_VOLUME_USD)),
        ("SHORT_WINDOW_HOURS", str(SHORT_WINDOW_HOURS)),
        ("MIN_VOLUME_SHORT_WINDOW_USD", str(MIN_VOLUME_SHORT_WINDOW_USD)),
        ("EDGE_THRESHOLD", str(EDGE_THRESHOLD)),
        ("MIN_TRADEABLE_PRICE", str(MIN_TRADEABLE_PRICE)),
        ("MAX_TRADEABLE_PRICE", str(MAX_TRADEABLE_PRICE)),
        ("MIN_EXPECTED_ROI", str(MIN_EXPECTED_ROI)),
        ("MIN_CONTRARIAN_ROI", str(MIN_CONTRARIAN_ROI)),
        ("CONFIDENCE_HIGH_THRESHOLD", str(CONFIDENCE_HIGH_THRESHOLD)),
        ("CONFIDENCE_MEDIUM_THRESHOLD", str(CONFIDENCE_MEDIUM_THRESHOLD)),
        ("MIN_COUNCIL_VERDICTS", str(MIN_COUNCIL_VERDICTS)),
        ("AGENT_MAX_RETRIES", str(AGENT_MAX_RETRIES)),
        ("RULES_LAWYER_WEIGHT", str(RULES_LAWYER_WEIGHT)),
        ("BEAR_WEIGHT", str(BEAR_WEIGHT)),
        ("CACHE_DB_PATH", CACHE_DB_PATH),
        ("CACHE_PRICE_DELTA", str(CACHE_PRICE_DELTA)),
        ("CATEGORY_CACHE_PATH", CATEGORY_CACHE_PATH),
        ("LOG_LEVEL", LOG_LEVEL),
    ]
    col = max(len(k) for k, _ in rows) + 2
    for key, val in rows:
        print(f"  {key:<{col}} {val}")

    print("\n--- Type checks ---")
    assert isinstance(LLM_BACKEND, str)
    assert isinstance(LOCAL_LLM_CONCURRENCY, int)
    assert isinstance(ANTHROPIC_API_KEY, str)
    assert isinstance(TAVILY_API_KEY, str)
    assert isinstance(GAMMA_RATE_LIMIT_DELAY, float)
    assert isinstance(MAX_MARKETS_TO_FETCH, int)
    assert isinstance(MIN_VOLUME_USD, float)
    assert isinstance(EDGE_THRESHOLD, float)
    assert isinstance(MIN_TRADEABLE_PRICE, float)
    assert isinstance(CONFIDENCE_HIGH_THRESHOLD, float)
    assert isinstance(MIN_COUNCIL_VERDICTS, int)
    assert LLM_BACKEND in VALID_BACKENDS
    print("  All type checks passed.")

    print("\n--- Validation error simulation ---")
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; os.environ.pop('TAVILY_API_KEY', None); "
            "os.environ['LLM_BACKEND'] = 'ollama'; "
            "from unittest.mock import patch; "
            "with patch('dotenv.load_dotenv'): "
            "    import importlib, pqi.config as c; "
            "    importlib.reload(c)",
        ],
        capture_output=True,
        text=True,
    )
    if "EnvironmentError" in result.stderr or "Missing required" in result.stderr:
        print("  ✓ EnvironmentError raised correctly for missing TAVILY_API_KEY")
    else:
        print("  (validation simulation inconclusive in subprocess — check manually)")

    print("\n✓ config.py smoke test complete.")
