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

        # Skip markets where the API has stopped accepting orders (effectively resolved)
        if raw.get("acceptingOrders") is False:
            logger.debug("Market %s has acceptingOrders=False — skipping", market_id)
            return None

        # Parse optional eventStartTime (when the real-world event begins)
        event_start_time = None
        est_raw = raw.get("eventStartTime")
        if est_raw:
            try:
                event_start_time = datetime.fromisoformat(est_raw.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        return MarketData(
            id=market_id,
            question=question,
            resolution_criteria=resolution_criteria,
            end_date=end_date,
            event_start_time=event_start_time,
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


class CategoryMatch:
    """Result of resolve_category(): either a series_id or a tag_id for server-side filtering."""

    def __init__(self, match_type: str, value: str):
        self.match_type = match_type  # "series" or "tag"
        self.value = value            # series_id (str) or tag_id (str)

    @property
    def is_series(self) -> bool:
        return self.match_type == "series"

    @property
    def is_tag(self) -> bool:
        return self.match_type == "tag"

    def __repr__(self) -> str:
        return f"CategoryMatch({self.match_type!r}, {self.value!r})"


async def resolve_category(query: str) -> CategoryMatch | None:
    """
    Resolve a user-supplied category string to either a series_id or a tag_id.

    Strategy (three passes):
      1. Exact series slug match via GET /series?slug=<query>
      2. Partial series slug/title match via GET /series?limit=200
      3. Tag match via GET /tags (paginated) — exact then partial slug/label match.
         Returns tag_id for server-side filtering via /events?tag_id=<id>.

    Returns CategoryMatch or None if nothing matches.
    """
    query_clean = query.strip().lower()
    query_norm = query_clean.replace(" ", "-")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Pass 1: exact series slug match
        r = await client.get(
            f"{config.GAMMA_BASE_URL}/series",
            params={"slug": query_clean, "limit": "1"},
        )
        if r.status_code == 200:
            results = r.json()
            if results and results[0].get("slug", "").lower() == query_clean:
                series_id = str(results[0]["id"])
                logger.debug("resolve_category(%r): exact series match → id=%s", query, series_id)
                return CategoryMatch("series", series_id)

        await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)

        # Pass 2 + 3a: run in parallel — partial series match vs exact tag slug.
        # Series match takes priority if both hit.
        async def _try_partial_series() -> CategoryMatch | None:
            r2 = await client.get(
                f"{config.GAMMA_BASE_URL}/series",
                params={"limit": "200"},
            )
            if r2.status_code == 200:
                for s in r2.json():
                    slug = s.get("slug", "").lower()
                    title = s.get("title", "").lower()
                    if query_norm in slug or query_clean in title:
                        sid = str(s["id"])
                        logger.debug("resolve_category(%r): partial series %r → id=%s",
                                     query, s.get("slug"), sid)
                        return CategoryMatch("series", sid)
            return None

        async def _try_exact_tag() -> CategoryMatch | None:
            for slug_variant in (query_clean, query_norm):
                r3 = await client.get(
                    f"{config.GAMMA_BASE_URL}/tags/slug/{slug_variant}",
                )
                if r3.status_code == 200:
                    tag_data = r3.json()
                    if tag_data and tag_data.get("id"):
                        tid = str(tag_data["id"])
                        logger.debug("resolve_category(%r): exact tag slug → id=%s",
                                     query, tid)
                        return CategoryMatch("tag", tid)
            return None

        series_result, tag_result = await asyncio.gather(
            _try_partial_series(), _try_exact_tag(),
        )
        # Series takes priority over tag
        if series_result:
            return series_result
        if tag_result:
            return tag_result

        await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)

        # Pass 3b: partial tag match via paginated /tags (slower fallback).
        # Caps at 600 tags (3 pages) to keep latency reasonable.
        partial_tag_match: dict | None = None
        for offset in range(0, 600, 200):
            r3b = await client.get(
                f"{config.GAMMA_BASE_URL}/tags",
                params={"limit": "200", "offset": str(offset)},
            )
            if r3b.status_code != 200:
                break
            batch = r3b.json()
            if not batch:
                break
            for tag in batch:
                tag_slug = (tag.get("slug") or "").lower()
                tag_label = (tag.get("label") or "").lower()
                if query_clean in tag_slug or query_clean in tag_label:
                    partial_tag_match = tag
                    break
            if partial_tag_match:
                break
            await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)

        if partial_tag_match:
            tag_id = str(partial_tag_match["id"])
            logger.debug(
                "resolve_category(%r): partial tag match %r (id=%s)",
                query, partial_tag_match.get("slug"), tag_id,
            )
            return CategoryMatch("tag", tag_id)

    logger.warning("resolve_category(%r): no match found", query)
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


async def fetch_tags() -> list[tuple[str, int]]:
    """
    Discover broad tag categories by scanning active events for tags with counts.

    Uses the /events endpoint (with tags embedded) rather than /tags, because the
    /tags endpoint returns 2000+ tags with no activity data. Scanning events gives
    us only tags that appear on current active events, with usage counts.

    Returns list of (tag_slug, event_count) sorted by count descending.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{config.GAMMA_BASE_URL}/events",
            params={"active": "true", "closed": "false", "limit": "200",
                    "order": "volume", "ascending": "false"},
        )
        r.raise_for_status()
        raw_events = r.json()

    await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)

    tag_counts: dict[str, int] = {}
    for event in raw_events:
        for tag in event.get("tags") or []:
            slug = (tag.get("slug") or "").lower()
            if slug:
                tag_counts[slug] = tag_counts.get(slug, 0) + 1

    # Sort by count descending, return as list of tuples
    return sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)


class CategoryNotFoundError(ValueError):
    """Raised when --category matches no series or tag."""
    pass


async def fetch_markets(
    limit: int = config.MAX_MARKETS_TO_FETCH,
    category: str | None = None,
    resolve_within_hours: int | None = None,
    starts_within_hours: int | None = None,
    live: bool = False,
    volume_floor_override: float | None = None,
) -> list[MarketData]:
    """
    Fetch active markets from the Gamma API and return parsed, filtered results.

    Category filtering (two-tier):
      1. Exact/partial series slug match → server-side filter via series_id on /events
      2. Broad tag match (e.g. "esports") → client-side filter on event tags
      Raises CategoryNotFoundError if --category is set but nothing matches.

    Args:
        limit:                 Max markets to return after filtering.
        category:              Series slug or broad tag (e.g. "counter-strike", "esports").
        resolve_within_hours:  Only return markets resolving within this many hours.
        starts_within_hours:   Only return markets whose event starts within this many hours.
                               Includes live events (already started, not resolved).
        live:                  Only return live events (started but not yet resolved).
                               Composable with starts_within (live + upcoming).
        volume_floor_override: If set, overrides the config-based volume floor (use 0 to see all).

    Returns:
        With time filters: sorted by start time asc. Otherwise by volume desc.
    """
    # Volume floor: override > config
    if volume_floor_override is not None:
        volume_floor = volume_floor_override
    else:
        any_time_filter = resolve_within_hours or starts_within_hours
        is_short_window = (
            any_time_filter is not None
            and any_time_filter <= config.SHORT_WINDOW_HOURS
        ) or live
        volume_floor = (
            config.MIN_VOLUME_SHORT_WINDOW_USD if is_short_window else config.MIN_VOLUME_USD
        )

    # Resolve category → series_id or tag slug
    cat_match: CategoryMatch | None = None
    if category:
        cat_match = await resolve_category(category)
        if cat_match is None:
            raise CategoryNotFoundError(
                f"No category matching '{category}'. Run 'pqi categories' to see options."
            )
        logger.info("Category resolved: %s", cat_match)

    series_id = cat_match.value if (cat_match and cat_match.is_series) else None
    tag_id = cat_match.value if (cat_match and cat_match.is_tag) else None

    if resolve_within_hours is not None or starts_within_hours is not None or live:
        return await _fetch_markets_by_time_window(
            limit=limit,
            series_id=series_id,
            tag_id=tag_id,
            resolve_within_hours=resolve_within_hours,
            starts_within_hours=starts_within_hours,
            live=live,
            volume_floor=volume_floor,
        )
    else:
        return await _fetch_markets_general(
            limit=limit,
            series_id=series_id,
            tag_id=tag_id,
            volume_floor=volume_floor,
        )


async def _fetch_markets_general(
    limit: int,
    series_id: str | None,
    tag_id: str | None,
    volume_floor: float,
) -> list[MarketData]:
    """
    Fetch markets without a time filter, sorted by volume descending.

    Three modes:
      - series_id set:  GET /events?series_id=<id>&order=volume (server-side filter)
      - tag_id set:     GET /events?tag_id=<id>&order=volume (server-side tag filter)
      - neither:        GET /markets?order=volume (fastest, all markets)
    """
    fetch_limit = max(limit * 3, 30)

    markets: list[MarketData] = []

    if series_id or tag_id:
        # Both paths use /events with server-side filtering
        params: dict[str, str | int] = {
            "active": "true",
            "closed": "false",
            "order": "volume",
            "ascending": "false",
            "limit": fetch_limit,
        }
        if series_id:
            params["series_id"] = series_id
        if tag_id:
            params["tag_id"] = tag_id

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{config.GAMMA_BASE_URL}/events", params=params)
            response.raise_for_status()
            raw_events: list[dict] = response.json()

        await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)
        logger.debug(
            "_fetch_markets_general (series_id=%s, tag_id=%s): API returned %d events",
            series_id, tag_id, len(raw_events),
        )

        seen_ids: set[str] = set()
        for event in raw_events:
            if event.get("ended") is True:
                continue
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
    tag_id: str | None,
    resolve_within_hours: int | None,
    starts_within_hours: int | None,
    live: bool,
    volume_floor: float,
) -> list[MarketData]:
    """
    Fetch markets within a time window via /events endpoint.

    Both filters are composable — when both are set, markets must pass BOTH.
    Server-side: uses event_start_time_min/max and/or end_date_min/max on /events API.
    Client-side: double-checks parsed market times since API may include edge cases.

    Sort order: starts_within → by event_start_time asc; resolve_within only → by end_date asc.
    """
    now = datetime.now(timezone.utc)
    fetch_limit = max(limit * 2, 40)

    params: dict[str, str | int] = {
        "active": "true",
        "closed": "false",
        "order": "volume",
        "ascending": "false",
        "limit": fetch_limit,
    }
    if series_id:
        params["series_id"] = series_id
    if tag_id:
        params["tag_id"] = tag_id

    # Server-side time filters — only end_date_min/max works server-side.
    # event_start_time filtering is client-side (API ignores those params).
    end_max: datetime | None = None
    start_max: datetime | None = None

    if resolve_within_hours is not None:
        end_max = now + timedelta(hours=resolve_within_hours)
        params["end_date_min"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        params["end_date_max"] = end_max.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        # Even without resolve_within, exclude already-resolved events
        params["end_date_min"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    if starts_within_hours is not None:
        start_max = now + timedelta(hours=starts_within_hours)
        # No server-side param — filtered client-side below

    logger.debug(
        "_fetch_markets_by_time_window: params=%s (volume_floor=%.0f)",
        {k: v for k, v in params.items() if "date" in k or "time" in k or "id" in k},
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
        # Skip events the API has marked as ended (field populated on direct
        # fetches and /public-search; usually None on list endpoint, but check anyway)
        if event.get("ended") is True:
            continue

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

            # Client-side time checks (belt-and-suspenders with API filtering)
            if end_max is not None:
                if parsed.end_date < now or parsed.end_date > end_max:
                    continue

            # Event start time filtering (all client-side — API ignores these params)
            if live and starts_within_hours is None:
                # --live only: must have started already
                if parsed.event_start_time is None or parsed.event_start_time >= now:
                    continue
            elif starts_within_hours is not None:
                # --starts-within (with or without --live): include live + upcoming
                if parsed.event_start_time is None:
                    continue
                if parsed.event_start_time > start_max:
                    continue

            markets.append(parsed)

    # Sort: prefer event_start_time when available, fall back to end_date
    if starts_within_hours is not None or live:
        markets.sort(key=lambda m: m.event_start_time or m.end_date)
    else:
        markets.sort(key=lambda m: m.end_date)

    markets = markets[:limit]

    filter_desc = []
    if live:
        filter_desc.append("live")
    if starts_within_hours is not None:
        filter_desc.append(f"starts_within={starts_within_hours}h")
    if resolve_within_hours is not None:
        filter_desc.append(f"resolve_within={resolve_within_hours}h")
    logger.info(
        "fetch_markets (%s): %d markets after filtering "
        "(events_fetched=%d, limit=%d, volume_floor=%.0f)",
        ", ".join(filter_desc), len(markets), len(raw_events), limit, volume_floor,
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


async def fetch_event_markets(market_id: str) -> tuple[str, list[MarketData]]:
    """
    Fetch all sibling markets from the same event as the given market.

    Returns (event_title, list_of_markets). The list includes the original market.
    Returns ("", []) if the market has no parent event or the fetch fails.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: get the market to find its parent event ID
        try:
            r = await client.get(f"{config.GAMMA_BASE_URL}/markets/{market_id}")
            r.raise_for_status()
            market_raw = r.json()
        except httpx.HTTPError as exc:
            logger.warning("Failed to fetch market %s: %s", market_id, exc)
            return ("", [])

        events = market_raw.get("events") or []
        if not events or not events[0].get("id"):
            return ("", [])

        event_id = events[0]["id"]
        event_title = events[0].get("title", "")

        await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)

        # Step 2: fetch the full event with all its markets
        try:
            r2 = await client.get(f"{config.GAMMA_BASE_URL}/events/{event_id}")
            r2.raise_for_status()
            event_data = r2.json()
        except httpx.HTTPError as exc:
            logger.warning("Failed to fetch event %s: %s", event_id, exc)
            return ("", [])

    event_category = _category_from_event(event_data)
    raw_markets = event_data.get("markets") or []

    siblings: list[MarketData] = []
    for raw in raw_markets:
        parsed = _parse_market(raw, volume_floor=0, category_override=event_category)
        if parsed is not None:
            siblings.append(parsed)

    siblings.sort(key=lambda m: m.volume_usd, reverse=True)
    return (event_title, siblings)


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
