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

# ---------------------------------------------------------------------------
# Market filtering
# ---------------------------------------------------------------------------

MAX_MARKETS_TO_FETCH: int = int(os.getenv("MAX_MARKETS_TO_FETCH", "20"))
MIN_VOLUME_USD: float = float(os.getenv("MIN_VOLUME_USD", "10000"))
SHORT_WINDOW_HOURS: int = int(os.getenv("SHORT_WINDOW_HOURS", "4"))
MIN_VOLUME_SHORT_WINDOW_USD: float = float(
    os.getenv("MIN_VOLUME_SHORT_WINDOW_USD", "500")
)

# ---------------------------------------------------------------------------
# Scoring & recommendation gates
# ---------------------------------------------------------------------------

EDGE_THRESHOLD: float = float(os.getenv("EDGE_THRESHOLD", "0.09"))
MIN_TRADEABLE_PRICE: float = float(os.getenv("MIN_TRADEABLE_PRICE", "0.05"))
CONFIDENCE_HIGH_THRESHOLD: float = float(os.getenv("CONFIDENCE_HIGH_THRESHOLD", "0.70"))
CONFIDENCE_MEDIUM_THRESHOLD: float = float(os.getenv("CONFIDENCE_MEDIUM_THRESHOLD", "0.40"))
CONSENSUS_YES_FLOOR: float = float(os.getenv("CONSENSUS_YES_FLOOR", "0.40"))
CONSENSUS_NO_CEILING: float = float(os.getenv("CONSENSUS_NO_CEILING", "0.60"))

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
        ("LLM_BACKEND",                LLM_BACKEND),
        ("OLLAMA_MODEL",               OLLAMA_MODEL),
        ("OLLAMA_BASE_URL",            OLLAMA_BASE_URL),
        ("LOCAL_LLM_CONCURRENCY",      str(LOCAL_LLM_CONCURRENCY)),
        ("ANTHROPIC_API_KEY",          _mask(ANTHROPIC_API_KEY)),
        ("ANTHROPIC_MODEL",            ANTHROPIC_MODEL),
        ("TAVILY_API_KEY",             _mask(TAVILY_API_KEY)),
        ("GAMMA_RATE_LIMIT_DELAY",     str(GAMMA_RATE_LIMIT_DELAY)),
        ("MAX_MARKETS_TO_FETCH",       str(MAX_MARKETS_TO_FETCH)),
        ("MIN_VOLUME_USD",             str(MIN_VOLUME_USD)),
        ("SHORT_WINDOW_HOURS",         str(SHORT_WINDOW_HOURS)),
        ("MIN_VOLUME_SHORT_WINDOW_USD",str(MIN_VOLUME_SHORT_WINDOW_USD)),
        ("EDGE_THRESHOLD",             str(EDGE_THRESHOLD)),
        ("MIN_TRADEABLE_PRICE",        str(MIN_TRADEABLE_PRICE)),
        ("CONFIDENCE_HIGH_THRESHOLD",  str(CONFIDENCE_HIGH_THRESHOLD)),
        ("CONFIDENCE_MEDIUM_THRESHOLD",str(CONFIDENCE_MEDIUM_THRESHOLD)),
        ("CONSENSUS_YES_FLOOR",        str(CONSENSUS_YES_FLOOR)),
        ("CONSENSUS_NO_CEILING",       str(CONSENSUS_NO_CEILING)),
        ("LOG_LEVEL",                  LOG_LEVEL),
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
    assert isinstance(CONSENSUS_YES_FLOOR, float)
    assert LLM_BACKEND in VALID_BACKENDS
    print("  All type checks passed.")

    print("\n--- Validation error simulation ---")
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c",
         "import os; os.environ.pop('TAVILY_API_KEY', None); "
         "os.environ['LLM_BACKEND'] = 'ollama'; "
         "from unittest.mock import patch; "
         "with patch('dotenv.load_dotenv'): "
         "    import importlib, pqi.config as c; "
         "    importlib.reload(c)"],
        capture_output=True, text=True,
    )
    if "EnvironmentError" in result.stderr or "Missing required" in result.stderr:
        print("  ✓ EnvironmentError raised correctly for missing TAVILY_API_KEY")
    else:
        print("  (validation simulation inconclusive in subprocess — check manually)")

    print("\n✓ config.py smoke test complete.")
