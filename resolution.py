# pqi/resolution.py
"""
Fetch resolution outcomes from the Polymarket Gamma API.

A market is considered resolved when `closed=True`. Two shapes:

  1. Clean extreme — outcomePrices settled to ["1","0"] (YES won) or
     ["0","1"] (NO won). `winning_side` is set accordingly; these are the
     vast majority of sports/politics markets.

  2. Intermediate — outcomePrices settled to something in between
     (e.g. ["0.5","0.5"] on a declared draw, or anything strictly within
     the extreme thresholds). This is NOT a void: every YES token redeems
     for `final_yes_price` and every NO token for `1 - final_yes_price`,
     so bets still have real PnL. `winning_side` stays None (no clean
     winner declared) but `final_yes_price` is populated — downstream
     analytics derives PnL and per-agent accuracy from that price.

True voids (market cancelled / refunded with no redemption value) come
through with both winning_side=None AND final_yes_price=None; the
analytics layer buckets those as `voided`.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Literal, TypedDict

import httpx

from pqi import config

logger = logging.getLogger(__name__)


class Resolution(TypedDict):
    resolved_at: str | None  # ISO timestamp from market endDate / closed time
    winning_side: Literal["YES", "NO"] | None
    final_yes_price: float | None
    checked_at: str  # ISO timestamp of this fetch
    source: str  # "gamma_api"


async def fetch_resolution(market_id: str) -> Resolution | None:
    """Fetch the current resolution state for a market.

    Returns a Resolution dict if the market is closed (whether settled cleanly
    or voided). Returns None if the market is still open or the fetch fails —
    callers should treat None as "try again later".
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.get(f"{config.GAMMA_BASE_URL}/markets/{market_id}")
            r.raise_for_status()
            raw = r.json()
        except httpx.HTTPError as exc:
            logger.warning("Resolution fetch failed for %s: %s", market_id, exc)
            return None

    closed = bool(raw.get("closed"))
    if not closed:
        return None

    final_yes_price: float | None = None
    winning_side: Literal["YES", "NO"] | None = None
    try:
        prices = json.loads(raw.get("outcomePrices") or "[]")
        if len(prices) >= 2:
            yp = float(prices[0])
            final_yes_price = yp
            if yp >= 0.99:
                winning_side = "YES"
            elif yp <= 0.01:
                winning_side = "NO"
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Failed to parse outcomePrices for %s: %s", market_id, exc)

    end_date = raw.get("endDate")

    return Resolution(
        resolved_at=end_date,
        winning_side=winning_side,
        final_yes_price=final_yes_price,
        checked_at=datetime.now(timezone.utc).isoformat(),
        source="gamma_api",
    )
