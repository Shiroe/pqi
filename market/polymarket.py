# pqi/market/polymarket.py
"""
Polymarket Gamma API client.

Fetches and filters active prediction markets from:
  https://gamma-api.polymarket.com

No authentication required. Uses httpx.AsyncClient.
Rate limit: 0.5s delay after each request (config.GAMMA_RATE_LIMIT_DELAY).
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx

from pqi import config
from pqi.models import MarketData

logger = logging.getLogger(__name__)


def _parse_market(
    raw: dict,
    volume_floor: float = config.MIN_VOLUME_USD,
    category_override: str | None = None,
) -> MarketData | None:
    """
    Parse a single raw market dict from the Gamma API into a MarketData model.

    Returns None if required fields are missing or volume_usd < volume_floor.

    Args:
        raw:               Raw market dict from /markets or embedded in /events.
        volume_floor:      Minimum volume_usd to include. Pass
                           MIN_VOLUME_SHORT_WINDOW_USD for time-filtered queries.
        category_override: When parsing markets embedded in events, the category
                           comes from the parent event's tags, not a nested
                           "events" key (which is absent for embedded markets).
                           Pass a non-None string to bypass the nested lookup.

    Field mappings (verified against live API 2026-04):
      id            → raw["id"]
      question      → raw["question"]
      resolution_criteria → raw["description"]
      end_date      → raw["endDate"]  (ISO string)
      yes_price     → raw["outcomePrices"][0]  (JSON string, already 0.0-1.0)
      volume_usd    → raw["volumeNum"]  (float, USD)
      category      → category_override if provided
                      else raw["events"][0]["category"] (standalone /markets)
                      else "general"
    """
    try:
        market_id = str(raw["id"])
        question = raw.get("question") or ""
        if not question:
            logger.warning("Market %s has no question field — skipping", market_id)
            return None

        resolution_criteria = raw.get("description") or ""

        # Parse end_date from ISO string
        end_date_raw = raw.get("endDate")
        if not end_date_raw:
            logger.warning("Market %s has no endDate — skipping", market_id)
            return None
        end_date = datetime.fromisoformat(end_date_raw.replace("Z", "+00:00"))

        # Parse yes_price from outcomePrices JSON string: e.g. '["0.73", "0.27"]'
        outcome_prices_raw = raw.get("outcomePrices")
        if not outcome_prices_raw:
            logger.warning("Market %s has no outcomePrices — skipping", market_id)
            return None
        outcome_prices = json.loads(outcome_prices_raw)
        yes_price = float(outcome_prices[0])
        # Clamp to valid range in case of minor float drift
        yes_price = max(0.0, min(1.0, yes_price))

        # volumeNum is already a float in USD; fall back to parsing "volume" string
        volume_usd = raw.get("volumeNum")
        if volume_usd is None:
            volume_str = raw.get("volume", "0")
            volume_usd = float(volume_str)

        # Filter illiquid markets before constructing the model
        if volume_usd < volume_floor:
            logger.debug(
                "Market %s below volume threshold (%.0f < %.0f) — skipping",
                market_id, volume_usd, volume_floor,
            )
            return None

        # Category resolution:
        #   1. Explicit override (from parent event's tags, for /events responses)
        #   2. Nested "events" key (standalone /markets responses)
        #   3. Fallback: "general"
        if category_override is not None:
            category = category_override
        else:
            nested_events = raw.get("events") or []
            category = "general"
            if nested_events and nested_events[0].get("category"):
                category = nested_events[0]["category"]

        return MarketData(
            id=market_id,
            question=question,
            resolution_criteria=resolution_criteria,
            end_date=end_date,
            yes_price=yes_price,
            volume_usd=volume_usd,
            category=category,
        )

    except (KeyError, IndexError, ValueError, TypeError) as exc:
        market_id = raw.get("id", "<unknown>")
        logger.warning("Failed to parse market %s: %s", market_id, exc)
        return None


def _category_from_event(event: dict) -> str:
    """
    Extract a category string from a raw event dict (from the /events endpoint).
    Prefers seriesSlug (most specific), then first tag label, then "general".
    """
    series_slug = event.get("seriesSlug")
    if series_slug:
        return series_slug
    tags = event.get("tags") or []
    for tag in tags:
        label = tag.get("label") or tag.get("slug")
        if label:
            return label.lower()
    return "general"


async def resolve_series_id(query: str) -> str | None:
    """
    Resolve a user-supplied category string to a Gamma API series_id.

    Strategy:
      1. Exact slug match via GET /series?slug=<query>  (fast, single result)
      2. Partial substring match: fetch /series?limit=200 and search slug/title
         (handles "btc", "bitcoin", "counter strike" → "counter-strike", etc.)

    Returns the series id as a string, or None if no match found.
    Used by fetch_markets() when --category is supplied.
    """
    query_clean = query.strip().lower()

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Pass 1: exact slug match
        r = await client.get(
            f"{config.GAMMA_BASE_URL}/series",
            params={"slug": query_clean, "limit": "1"},
        )
        if r.status_code == 200:
            results = r.json()
            if results and results[0].get("slug", "").lower() == query_clean:
                series_id = str(results[0]["id"])
                logger.debug(
                    "resolve_series_id(%r): exact slug match → id=%s", query, series_id
                )
                return series_id

        # Pass 2: partial substring match across a wider set
        r2 = await client.get(
            f"{config.GAMMA_BASE_URL}/series",
            params={"limit": "200"},
        )
        if r2.status_code != 200:
            logger.warning("resolve_series_id: /series fetch failed (%d)", r2.status_code)
            return None

        series_list = r2.json()
        # Normalise query for substring matching (spaces → hyphens covers "counter strike")
        query_norm = query_clean.replace(" ", "-")
        for s in series_list:
            slug = s.get("slug", "").lower()
            title = s.get("title", "").lower()
            if query_norm in slug or query_clean in title:
                series_id = str(s["id"])
                logger.debug(
                    "resolve_series_id(%r): partial match %r → id=%s",
                    query, s.get("slug"), series_id,
                )
                return series_id

    logger.warning("resolve_series_id(%r): no match found", query)
    return None


async def fetch_categories(limit: int = 20) -> list[dict]:
    """
    Return the top active series sorted by 24-hour volume, for the `categories`
    CLI command.  Each dict has: id, slug, title, volume24hr, recurrence.

    The /series endpoint does not support server-side sort by volume24hr, so we
    fetch a larger batch and sort client-side.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{config.GAMMA_BASE_URL}/series",
            params={"active": "true", "limit": max(limit * 5, 100)},
        )
        r.raise_for_status()
        series_list = r.json()

    await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)

    results = []
    for s in series_list:
        v24 = s.get("volume24hr") or 0.0
        if v24 <= 0:
            continue
        results.append({
            "id": s.get("id"),
            "slug": s.get("slug", ""),
            "title": s.get("title", ""),
            "volume24hr": v24,
            "recurrence": s.get("recurrence", ""),
        })

    results.sort(key=lambda x: x["volume24hr"], reverse=True)
    return results[:limit]


async def fetch_markets(
    limit: int = config.MAX_MARKETS_TO_FETCH,
    category: str | None = None,
    resolve_within_hours: int | None = None,
) -> list[MarketData]:
    """
    Fetch active markets from the Gamma API and return parsed, filtered results.

    Endpoint strategy (confirmed against live API 2026-04):
      - Time-filtered queries  (resolve_within_hours set):
            GET /events?order=volume&end_date_min=...&end_date_max=...
          The /events endpoint groups related markets per event; fresh windows
          in recurring series (next BTC 15-min slot) appear naturally.
          Volume floor is relaxed (MIN_VOLUME_SHORT_WINDOW_USD).
          Results sorted by end_date ascending (soonest first).

      - General queries (no time filter):
            GET /markets?order=volume
          Consistent server-side ordering. Results sorted by volume_usd desc.

    Category filtering:
      `category` is a series slug (e.g. "counter-strike", "league-of-legends",
      "atp", "bitcoin-multi-strikes-weekly").  When provided, `resolve_series_id`
      translates it to a numeric series_id via GET /series?slug=<slug>, then
      passes series_id=<id> to the events endpoint.  Both exact and partial
      slug/title matches are tried.  Use `pqi categories` to list available slugs.

    NOTE: order=end_date and order=volume_24hr return 422 (docs are wrong).
    Confirmed working: order=volume, order=liquidity.

    Args:
        limit:                 Max markets to return after filtering.
        category:              Series slug filter (e.g. "counter-strike", "atp").
        resolve_within_hours:  Only return markets resolving within this many hours.

    Returns:
        With resolve_within_hours: sorted by end_date asc. Otherwise by volume desc.
        Both capped at `limit`.
    """
    is_short_window = (
        resolve_within_hours is not None
        and resolve_within_hours <= config.SHORT_WINDOW_HOURS
    )
    volume_floor = (
        config.MIN_VOLUME_SHORT_WINDOW_USD if is_short_window else config.MIN_VOLUME_USD
    )

    # Resolve category slug → series_id once, shared by both sub-functions
    series_id: str | None = None
    if category:
        series_id = await resolve_series_id(category)
        if series_id is None:
            logger.warning(
                "fetch_markets: no series found for category %r — ignoring filter", category
            )

    if resolve_within_hours is not None:
        return await _fetch_markets_by_time_window(
            limit=limit,
            series_id=series_id,
            resolve_within_hours=resolve_within_hours,
            volume_floor=volume_floor,
        )
    else:
        return await _fetch_markets_general(
            limit=limit,
            series_id=series_id,
            volume_floor=volume_floor,
        )


async def _fetch_markets_general(
    limit: int,
    series_id: str | None,
    volume_floor: float,
) -> list[MarketData]:
    """
    Fetch markets without a time filter, sorted by volume descending.

    Without series filter: GET /markets?order=volume (fastest, returns all markets)
    With series filter:    GET /events?series_id=<id>&order=volume (only way to filter
                          by series — /markets has no series_id param)

    Returns results sorted by volume_usd descending.
    """
    # Fetch 3× more than needed: the API sorts by the "volume" text field
    # while we filter by "volumeNum" (float).  These can diverge, so top-N
    # by API volume is not guaranteed to be top-N by volumeNum.
    fetch_limit = max(limit * 3, 30)

    markets: list[MarketData] = []

    if series_id:
        # /events?series_id=<id> is the only supported series filter
        params: dict[str, str | int] = {
            "active": "true",
            "closed": "false",
            "order": "volume",
            "ascending": "false",
            "limit": fetch_limit,
            "series_id": series_id,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{config.GAMMA_BASE_URL}/events", params=params)
            response.raise_for_status()
            raw_events: list[dict] = response.json()

        await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)
        logger.debug(
            "_fetch_markets_general (series_id=%s): API returned %d events",
            series_id, len(raw_events),
        )

        seen_ids: set[str] = set()
        for event in raw_events:
            event_category = _category_from_event(event)
            for raw_market in event.get("markets") or []:
                market_id = str(raw_market.get("id", ""))
                if market_id in seen_ids:
                    continue
                seen_ids.add(market_id)
                parsed = _parse_market(raw_market, volume_floor=volume_floor,
                                       category_override=event_category)
                if parsed is not None:
                    markets.append(parsed)
    else:
        params = {
            "active": "true",
            "closed": "false",
            "order": "volume",
            "ascending": "false",
            "limit": fetch_limit,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{config.GAMMA_BASE_URL}/markets", params=params)
            response.raise_for_status()
            raw_markets: list[dict] = response.json()

        await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)
        logger.debug("_fetch_markets_general: API returned %d raw markets", len(raw_markets))

        for raw in raw_markets:
            parsed = _parse_market(raw, volume_floor=volume_floor)
            if parsed is not None:
                markets.append(parsed)

    markets.sort(key=lambda m: m.volume_usd, reverse=True)
    markets = markets[:limit]
    logger.info(
        "fetch_markets (general): %d markets after filtering (limit=%d, volume_floor=%.0f)",
        len(markets), limit, volume_floor,
    )
    return markets


async def _fetch_markets_by_time_window(
    limit: int,
    series_id: str | None,
    resolve_within_hours: int,
    volume_floor: float,
) -> list[MarketData]:
    """
    Fetch markets within a time window via /events?order=volume&ascending=false.

    Uses the events endpoint because:
    - It returns ALL markets for an event (e.g. all questions for a football match,
      all BTC 15-min windows in the range) in one response.
    - Fresh windows in recurring series (next BTC 5/15-min slot) appear alongside
      the current window, whereas /markets sorted by all-time volume buries them.

    The volume_floor is intentionally relaxed (MIN_VOLUME_SHORT_WINDOW_USD) so
    newly-opened recurring-market windows aren't silently dropped.

    Returns markets sorted by end_date ascending (soonest resolving first).
    """
    now = datetime.now(timezone.utc)
    end_max = now + timedelta(hours=resolve_within_hours)

    # Fetch more events than needed to ensure we have enough after market-level
    # volume filtering.  2× limit is sufficient now that the volume floor is low.
    fetch_limit = max(limit * 2, 40)

    params: dict[str, str | int] = {
        "active": "true",
        "closed": "false",
        "order": "volume",
        "ascending": "false",
        "limit": fetch_limit,
        "end_date_min": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_date_max": end_max.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if series_id:
        params["series_id"] = series_id

    logger.debug(
        "_fetch_markets_by_time_window: end_date window %s → %s (volume_floor=%.0f)",
        params["end_date_min"],
        params["end_date_max"],
        volume_floor,
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{config.GAMMA_BASE_URL}/events", params=params)
        response.raise_for_status()
        raw_events: list[dict] = response.json()

    await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)
    logger.debug(
        "_fetch_markets_by_time_window: API returned %d events", len(raw_events)
    )

    markets: list[MarketData] = []
    seen_ids: set[str] = set()

    for event in raw_events:
        event_category = _category_from_event(event)
        for raw_market in event.get("markets") or []:
            market_id = str(raw_market.get("id", ""))
            if market_id in seen_ids:
                continue
            seen_ids.add(market_id)

            parsed = _parse_market(
                raw_market,
                volume_floor=volume_floor,
                category_override=event_category,
            )
            if parsed is None:
                continue
            # Secondary time guard: ensure market end_date is within the requested window
            # (event end_date may differ from individual market end_date)
            if parsed.end_date < now or parsed.end_date > end_max:
                continue
            markets.append(parsed)

    # Sort soonest-resolving first — most actionable for time-bounded queries
    markets.sort(key=lambda m: m.end_date)
    markets = markets[:limit]
    logger.info(
        "fetch_markets (time_window=%dh): %d markets after filtering "
        "(events_fetched=%d, limit=%d, volume_floor=%.0f)",
        resolve_within_hours, len(markets), len(raw_events), limit, volume_floor,
    )
    return markets


async def fetch_market_detail(market_id: str) -> MarketData | None:
    """
    Fetch a single market's full detail, including resolution_criteria.

    Endpoint: GET /markets/{market_id}
    Useful when the list endpoint omits the resolution_criteria field.

    Returns None on HTTP error or parse failure.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{config.GAMMA_BASE_URL}/markets/{market_id}",
            )
            response.raise_for_status()
            raw = response.json()
        except httpx.HTTPError as exc:
            logger.warning("HTTP error fetching market %s: %s", market_id, exc)
            return None

    return _parse_market(raw)


if __name__ == "__main__":
    import asyncio
    import logging

    logging.basicConfig(level=logging.INFO)

    async def smoke_test() -> None:
        print("=== pqi/market/polymarket.py smoke test ===\n")

        markets = await fetch_markets()

        print(f"Markets returned: {len(markets)}")
        assert len(markets) >= 1, "Expected at least 1 market"

        print("\nTop 3 by volume:")
        for i, m in enumerate(markets[:3], 1):
            print(f"  {i}. [{m.volume_usd:>12,.0f} USD]  yes={m.yes_price:.2f}  {m.question[:80]}")

        print("\nField validation (all fields non-None):")
        for m in markets:
            assert m.id, f"Market missing id: {m}"
            assert m.question, f"Market missing question: {m.id}"
            assert m.resolution_criteria is not None, f"Market missing resolution_criteria: {m.id}"
            assert m.end_date, f"Market missing end_date: {m.id}"
            assert 0.0 <= m.yes_price <= 1.0, f"yes_price out of range: {m.id}"
            assert m.volume_usd >= config.MIN_VOLUME_USD, f"volume below threshold: {m.id}"
            assert m.category, f"Market missing category: {m.id}"
        print(f"  All {len(markets)} markets passed field validation.")

        # Spot-check a single market detail fetch
        top_id = markets[0].id
        print(f"\nFetching single market detail: id={top_id}")
        detail = await fetch_market_detail(top_id)
        assert detail is not None, "fetch_market_detail returned None for a known good id"
        assert detail.id == top_id
        print(f"  id={detail.id}  question={detail.question[:60]}")
        print("  fetch_market_detail PASS")

        print("\n✓ polymarket.py smoke test complete.")

    asyncio.run(smoke_test())
