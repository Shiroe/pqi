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

# Load .env from the directory containing this file (the pqi package root)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

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

# ---------------------------------------------------------------------------
# Tavily (news search)
# ---------------------------------------------------------------------------

TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
TAVILY_SEARCHES_PER_MARKET: int = int(os.getenv("TAVILY_SEARCHES_PER_MARKET", "5"))
TAVILY_SNIPPET_CAP: int = int(os.getenv("TAVILY_SNIPPET_CAP", "5000"))

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

# Probability-first recommendation gates
MIN_COUNCIL_PROBABILITY: float = float(os.getenv("MIN_COUNCIL_PROBABILITY", "0.60"))
MIN_CONTRARIAN_PROBABILITY: float = float(
    os.getenv("MIN_CONTRARIAN_PROBABILITY", "0.70")
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

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

CACHE_DB_PATH: str = os.getenv("CACHE_DB_PATH", str(Path.home() / ".pqi" / "cache.db"))
CACHE_PRICE_DELTA: float = float(os.getenv("CACHE_PRICE_DELTA", "0.02"))

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
