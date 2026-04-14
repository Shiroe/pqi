"""
PerceivedTrueWinRate — rolling win rate over resolved BUY history, used as
the EV ceiling in the scoring gate, plus the Kelly Criterion helper for
advisory bet sizing.

Win rate is computed from `pqi.analytics.classify_outcome` buckets:
  true_win_rate = correct_buys / (correct_buys + wrong_buys)

Only non-legacy resolved entries count. PASS / missed_opportunity / voided
are excluded — we want the rate over bets we actually placed.

Persisted to ~/.pqi/stats.json. Readers tolerate a missing/malformed file
(returns None). Writers use the atomic tmp+rename pattern that history.py
already relies on.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from pqi.history import HISTORY_DIR, load_history
from pqi.analytics import classify_outcome
from pqi.models import PerceivedStats

logger = logging.getLogger(__name__)

STATS_FILE = HISTORY_DIR / "stats.json"


def kelly_fraction(win_rate: float, price: float, multiplier: float = 1.0) -> float:
    """Optimal Kelly stake as a fraction of bankroll.

    f* = (b*p - q) / b
      b = (1 - price) / price   (profit per $1 wagered)
      p = win_rate
      q = 1 - win_rate

    Returns 0.0 if the bet is -EV (win_rate <= price) or inputs are degenerate.
    `multiplier` applies a safety scaler (e.g. 0.5 for half-Kelly).
    """
    if price <= 0.0 or price >= 1.0:
        return 0.0
    if win_rate <= price:
        return 0.0
    b = (1.0 - price) / price
    p = win_rate
    q = 1.0 - win_rate
    f = (b * p - q) / b
    if f <= 0.0:
        return 0.0
    return max(0.0, min(1.0, f * multiplier))


def recompute_from_history(min_samples: int) -> PerceivedStats:
    """Scan history.jsonl, compute fresh stats, persist to stats.json, return."""
    entries = load_history()
    correct = wrong = 0
    for e in entries:
        if e.get("legacy"):
            continue
        kind = classify_outcome(e)
        if kind == "correct_buy":
            correct += 1
        elif kind == "wrong_buy":
            wrong += 1

    total = correct + wrong
    win_rate = (correct / total) if total > 0 else 0.0
    below_min = total < min_samples

    stats = PerceivedStats(
        computed_at=datetime.now(timezone.utc),
        true_win_rate=win_rate,
        sample_size=total,
        correct_buys=correct,
        wrong_buys=wrong,
        kelly_fraction_at_p75=kelly_fraction(win_rate, 0.75),
        below_min_samples=below_min,
    )
    _write_atomic(stats)
    logger.debug(
        "PerceivedStats recomputed: n=%d win_rate=%.3f below_min=%s",
        total, win_rate, below_min,
    )
    return stats


def load_perceived_stats() -> PerceivedStats | None:
    """Read stats.json. Returns None if missing or malformed (cold start)."""
    if not STATS_FILE.exists():
        return None
    try:
        with open(STATS_FILE) as f:
            return PerceivedStats.model_validate_json(f.read())
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("Could not load %s: %s", STATS_FILE, exc)
        return None


def _write_atomic(stats: PerceivedStats) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        f.write(stats.model_dump_json(indent=2))
    os.replace(tmp, STATS_FILE)
