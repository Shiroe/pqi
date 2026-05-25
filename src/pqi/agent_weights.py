"""
Dynamic per-agent weights derived from resolved history.

Each agent's scoring weight is shrunk around 1.0 by the amount its lean-hit
rate deviates from the council average:

    weight = 1.0 + shrink * (agent_lean_rate - council_avg_lean_rate)

This rewards demonstrably-accurate agents (Rules Lawyer, Sentiment) and
discounts coin-flip agents (Bear) without zeroing any voice. Weights stay
anchored near 1.0 so the confidence-weighted denominator in scoring keeps
the same scale as the static-weights path.

Cold-start safety: agents with fewer than `min_samples` resolved lean
verdicts fall back to 1.0. A missing/malformed weights file also yields
None from `load_agent_weights()` — scoring.py then uses static config.

Persisted to ~/.pqi/agent_weights.json. Atomic tmp+rename writer.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from pqi.analytics import agent_accuracy
from pqi.history import HISTORY_DIR, load_history

logger = logging.getLogger(__name__)

WEIGHTS_FILE = HISTORY_DIR / "agent_weights.json"


def compute_weights(
    lean_rates: dict[str, tuple[float, int]],
    min_samples: int,
    shrink: float,
) -> dict[str, float]:
    """Pure function: lean_rates → weight per agent.

    lean_rates maps agent_name -> (lean_rate, lean_n). Agents with lean_n
    below `min_samples` get weight 1.0. Council average is computed only
    from qualifying agents so a single cold-start agent doesn't pull the
    anchor.
    """
    qualifying = {
        name: rate
        for name, (rate, n) in lean_rates.items()
        if n >= min_samples and rate is not None
    }
    if not qualifying:
        return {name: 1.0 for name in lean_rates}

    avg = sum(qualifying.values()) / len(qualifying)
    out: dict[str, float] = {}
    for name, (rate, n) in lean_rates.items():
        if n < min_samples or rate is None:
            out[name] = 1.0
        else:
            w = 1.0 + shrink * (rate - avg)
            out[name] = max(0.0, w)  # clamp to non-negative; k=2.0 + avg=0.5 keeps all positive
    return out


def recompute_from_history(
    min_samples: int,
    shrink: float,
) -> dict[str, float]:
    """Walk history.jsonl, compute + persist weights, return the dict."""
    entries = load_history()
    stats = agent_accuracy(entries)
    lean_rates = {
        name: (s.get("lean_rate"), s.get("lean_n", 0))
        for name, s in stats.items()
    }
    weights = compute_weights(lean_rates, min_samples=min_samples, shrink=shrink)

    payload = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "min_samples": min_samples,
        "shrink": shrink,
        "council_avg_lean_rate": _avg_qualifying(lean_rates, min_samples),
        "weights": weights,
        "sources": {
            name: {"lean_rate": r, "lean_n": n}
            for name, (r, n) in lean_rates.items()
        },
    }
    _write_atomic(payload)
    logger.debug("AgentWeights recomputed: %s", weights)
    return weights


def load_agent_weights() -> dict[str, float] | None:
    """Read agent_weights.json. Returns None if missing/malformed."""
    if not WEIGHTS_FILE.exists():
        return None
    try:
        with open(WEIGHTS_FILE) as f:
            payload = json.load(f)
        w = payload.get("weights")
        if not isinstance(w, dict):
            return None
        return {str(k): float(v) for k, v in w.items()}
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("Could not load %s: %s", WEIGHTS_FILE, exc)
        return None


def _avg_qualifying(
    lean_rates: dict[str, tuple[float, int]], min_samples: int
) -> float | None:
    quals = [r for r, n in lean_rates.values() if r is not None and n >= min_samples]
    return sum(quals) / len(quals) if quals else None


def _write_atomic(payload: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = WEIGHTS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    os.replace(tmp, WEIGHTS_FILE)
