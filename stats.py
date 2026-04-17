"""
PerceivedTrueWinRate — rolling win rate over resolved BUY history, used as
the EV ceiling in the scoring gate, plus the Kelly Criterion helper for
advisory bet sizing.

Win rate is computed from `pqi.analytics.classify_outcome` buckets:
  true_win_rate = correct_buys / (correct_buys + wrong_buys)

Only non-legacy resolved entries count. PASS / missed_opportunity / voided
are excluded — we want the rate over bets we actually placed.

Session 31+: we also compute per-price-band smoothed win rates
(`band_stats`). The EV gate reads those in preference to the global
rate because hit rate is strongly price-dependent in our data — cheap
underdog bets under-perform, expensive favorites over-perform. Smoothing
is Beta-binomial with the global rate as prior; a band with n=0 falls
back to global exactly, thin bands get pulled toward it, dense bands
dominate. K is tunable via `config.BAND_SHRINKAGE_K`.

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

from pqi import config
from pqi.history import HISTORY_DIR, load_history
from pqi.analytics import classify_outcome
from pqi.models import BandStat, PerceivedStats

logger = logging.getLogger(__name__)

STATS_FILE = HISTORY_DIR / "stats.json"

# Price bands used by the banded EV gate. Boundaries come from the
# session-31 retrospective on resolved BUY history: these are the slices
# where hit rate diverges meaningfully from the global average. Keep as
# a code constant — changing them is a re-analysis decision, not a tuning
# knob. The top bound is 0.95 to catch everything up to the price ceiling
# (MAX_TRADEABLE_PRICE = 0.90 already filters the rest).
PRICE_BANDS: list[tuple[float, float]] = [
    (0.09, 0.50),
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.95),
]


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


def _side_price_and_won(entry: dict) -> tuple[float, bool] | None:
    """Extract (side_price, won) from a resolved non-legacy BUY entry.
    Returns None when the entry is unusable (missing price, unresolved,
    not a BUY). Mirrors the per-entry logic in classify_outcome so both
    code paths stay in sync."""
    if entry.get("legacy"):
        return None
    res = entry.get("resolution")
    if not res or not res.get("winning_side"):
        return None
    result = entry.get("result") or {}
    rec = result.get("recommendation", "")
    market = result.get("market") or {}
    yes_price = market.get("yes_price")
    if yes_price is None:
        return None
    if rec == "BUY YES":
        side, side_price = "YES", float(yes_price)
    elif rec == "BUY NO":
        side, side_price = "NO", 1.0 - float(yes_price)
    else:
        return None
    return side_price, (res["winning_side"] == side)


def _compute_band_stats(entries: list[dict], global_wr: float, k: float) -> list[BandStat]:
    """Walk resolved BUYs and produce a `BandStat` per price band.

    Uses Beta-binomial smoothing with the global rate as prior:
        smoothed_wr = (wins + k * global_wr) / (n + k)

    When k = 0 we return an empty list — caller signals "banding off"
    and downstream falls back to `true_win_rate`.
    """
    if k <= 0.0:
        return []

    buckets: dict[tuple[float, float], list[bool]] = {b: [] for b in PRICE_BANDS}
    for e in entries:
        sw = _side_price_and_won(e)
        if sw is None:
            continue
        side_price, won = sw
        for band in PRICE_BANDS:
            lo, hi = band
            if lo <= side_price < hi:
                buckets[band].append(won)
                break

    out: list[BandStat] = []
    for (lo, hi), wins_list in buckets.items():
        n = len(wins_list)
        wins = sum(1 for w in wins_list if w)
        smoothed = (wins + k * global_wr) / (n + k)
        smoothed = max(0.0, min(1.0, smoothed))
        raw = (wins / n) if n > 0 else None
        out.append(
            BandStat(
                lo=lo, hi=hi, n=n, wins=wins,
                raw_wr=raw, smoothed_wr=smoothed,
            )
        )
    return out


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

    k = float(config.BAND_SHRINKAGE_K)
    band_stats = _compute_band_stats(entries, global_wr=win_rate, k=k) if not below_min else []

    stats = PerceivedStats(
        computed_at=datetime.now(timezone.utc),
        true_win_rate=win_rate,
        sample_size=total,
        correct_buys=correct,
        wrong_buys=wrong,
        kelly_fraction_at_p75=kelly_fraction(win_rate, 0.75),
        below_min_samples=below_min,
        band_stats=band_stats,
        shrinkage_k=k if band_stats else 0.0,
    )
    _write_atomic(stats)
    logger.debug(
        "PerceivedStats recomputed: n=%d win_rate=%.3f below_min=%s bands=%d k=%.1f",
        total, win_rate, below_min, len(band_stats), k,
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
