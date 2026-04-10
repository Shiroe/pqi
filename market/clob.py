# pqi/market/clob.py
"""
Polymarket CLOB API client — price history enrichment.

Fetches hourly YES-token price data and computes trend metrics
(weighted average, direction, volatility, sharp moves).

Uses httpx (async) for the HTTP call. py-clob-client is installed
for future trading phases but not used here.

Non-fatal: all public functions return None on failure and log warnings.
"""

import logging
import math
from datetime import datetime, timezone
from statistics import stdev

import httpx

from pqi import config
from pqi.models import MarketData, PriceHistory, PricePoint, SharpMove

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SHARP_MOVE_THRESHOLD_PP: float = 5.0  # percentage points
SHARP_MOVES_MAX: int = 3
EMA_HALF_LIFE_HOURS: float = 48.0  # 2 days
_DIRECTION_THRESHOLD_PP: float = 2.0  # slope × window_hours must exceed ±2pp


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def fetch_price_history(market: MarketData) -> PriceHistory | None:
    """
    Fetch YES-token price history from the CLOB API and compute trend metrics.

    Returns None (and logs a warning) if:
      - market has no clob_token_ids
      - the HTTP request fails
      - the response contains insufficient data (< 2 points)
    """
    if not market.clob_token_ids:
        logger.warning(
            "Market %s has no CLOB token IDs — cannot fetch price history",
            market.id,
        )
        return None

    token_id = market.clob_token_ids[0]  # YES token

    try:
        url = f"{config.CLOB_BASE_URL}/prices-history"
        params = {
            "market": token_id,
            "interval": config.PRICE_HISTORY_INTERVAL,
            "fidelity": config.PRICE_HISTORY_FIDELITY,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()

        raw_data = resp.json()

        # Response format: {"history": [{t: int, p: float}, ...]}
        if isinstance(raw_data, dict):
            history_list = raw_data.get("history", [])
        elif isinstance(raw_data, list):
            history_list = raw_data
        else:
            history_list = []

        if len(history_list) < 2:
            logger.warning(
                "CLOB price history for %s returned %d points — need ≥2",
                market.id,
                len(history_list),
            )
            return None

        # Parse [{t: unix_timestamp, p: price}, ...]
        points: list[PricePoint] = []
        for entry in history_list:
            try:
                ts = datetime.fromtimestamp(int(entry["t"]), tz=timezone.utc)
                price = float(entry["p"])
                price = max(0.0, min(1.0, price))
                points.append(PricePoint(timestamp=ts, price=price))
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("Skipping malformed CLOB data point: %s", exc)
                continue

        if len(points) < 2:
            logger.warning(
                "CLOB price history for %s: only %d valid points after parsing",
                market.id,
                len(points),
            )
            return None

        # Sort by timestamp ascending (should already be, but be safe)
        points.sort(key=lambda p: p.timestamp)

        return _compute_metrics(points, token_id)

    except httpx.HTTPStatusError as exc:
        logger.warning(
            "CLOB price history HTTP error for %s: %s %s",
            market.id,
            exc.response.status_code,
            exc.response.text[:200],
        )
        return None
    except Exception as exc:
        logger.warning(
            "CLOB price history fetch failed for %s: %s",
            market.id,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Metric computation (pure functions)
# ---------------------------------------------------------------------------


def _compute_metrics(points: list[PricePoint], token_id: str) -> PriceHistory:
    """Compute all trend metrics from a sorted list of price points."""
    now = datetime.now(timezone.utc)
    latest = points[-1]
    earliest = points[0]

    # --- Exponential-decay weighted average (half-life = 2 days) ---
    decay_lambda = math.log(2) / EMA_HALF_LIFE_HOURS
    w_sum = 0.0
    wp_sum = 0.0
    for p in points:
        age_hours = (now - p.timestamp).total_seconds() / 3600.0
        w = math.exp(-decay_lambda * max(age_hours, 0.0))
        w_sum += w
        wp_sum += w * p.price
    weighted_avg = wp_sum / w_sum if w_sum > 0 else latest.price
    weighted_avg = max(0.0, min(1.0, weighted_avg))

    # --- Net changes ---
    change_1w_pp = (latest.price - earliest.price) * 100.0

    # Find point closest to 24h ago
    target_24h = now.timestamp() - 86400
    point_24h = min(points, key=lambda p: abs(p.timestamp.timestamp() - target_24h))
    change_24h_pp = (latest.price - point_24h.price) * 100.0

    # --- Volatility (stddev of hourly deltas over last 24h) ---
    cutoff_24h = now.timestamp() - 86400
    recent_points = [p for p in points if p.timestamp.timestamp() >= cutoff_24h]
    hourly_deltas: list[float] = []
    for i in range(1, len(recent_points)):
        delta = (recent_points[i].price - recent_points[i - 1].price) * 100.0
        hourly_deltas.append(delta)

    volatility_24h = stdev(hourly_deltas) if len(hourly_deltas) >= 2 else 0.0

    # --- Direction (linear regression slope over all points) ---
    direction = _compute_direction(points)

    # --- Sharp moves (hourly deltas > threshold, last 3) ---
    sharp_moves: list[SharpMove] = []
    for i in range(1, len(points)):
        delta_pp = (points[i].price - points[i - 1].price) * 100.0
        if abs(delta_pp) >= SHARP_MOVE_THRESHOLD_PP:
            sharp_moves.append(
                SharpMove(timestamp=points[i].timestamp, delta_pp=delta_pp)
            )
    sharp_moves = sharp_moves[-SHARP_MOVES_MAX:]

    return PriceHistory(
        token_id=token_id,
        data_points=len(points),
        weighted_avg=weighted_avg,
        change_1w_pp=change_1w_pp,
        change_24h_pp=change_24h_pp,
        volatility_24h=volatility_24h,
        direction=direction,
        sharp_moves=sharp_moves,
        fetched_at=now,
    )


def _compute_direction(
    points: list[PricePoint],
) -> str:
    """
    Determine trend direction via linear regression slope.

    If slope × window_hours > +2pp → RISING
    If slope × window_hours < -2pp → FALLING
    Else → STABLE
    """
    n = len(points)
    if n < 2:
        return "STABLE"

    # Use hours since first point as x-axis
    t0 = points[0].timestamp.timestamp()
    xs = [(p.timestamp.timestamp() - t0) / 3600.0 for p in points]
    ys = [p.price * 100.0 for p in points]  # work in percentage points

    x_mean = sum(xs) / n
    y_mean = sum(ys) / n

    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    den = sum((x - x_mean) ** 2 for x in xs)

    if den == 0:
        return "STABLE"

    slope = num / den  # pp per hour
    window_hours = xs[-1] - xs[0] if xs[-1] != xs[0] else 1.0
    total_change = slope * window_hours

    if total_change > _DIRECTION_THRESHOLD_PP:
        return "RISING"
    elif total_change < -_DIRECTION_THRESHOLD_PP:
        return "FALLING"
    return "STABLE"
