# pqi/market/polymarket.py
"""
Polymarket Gamma API client.

Fetches and filters active prediction markets from:
  https://gamma-api.polymarket.com

No authentication required. Uses httpx.AsyncClient.
Rate limit: 0.5s delay after each request (config.GAMMA_RATE_LIMIT_DELAY).

Architecture:
  fetch_markets()            — public entry point (flat market list, legacy)
    └── _fetch_and_filter()  — single fetch for all flat-market queries

  fetch_events()             — public entry point (event-centric, grouped)
    └── _fetch_and_filter_groups() — fetch → group → enrich → filter
          ├── _parse_events_to_groups()     — events → EventGroup[]
          ├── _apply_client_filters_groups() — live/timing filters on groups
          └── _enrich_event_groups()        — parallel /events/{id} detail calls

  Shared helpers:
    _build_api_params()      — builds query params (one place)
    _parse_market()          — raw market dict → MarketData
    _category_from_event()   — event dict → category string
"""

import asyncio
import dataclasses
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx

from pqi import config
from pqi.models import EventGroup, MarketData

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal data types
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _ParsedMarket:
    """MarketData + event-level metadata not stored on MarketData."""

    market: MarketData
    event_live: bool | None = None   # from event's `live` field
    event_ended: bool | None = None  # from event's `ended` field


@dataclasses.dataclass
class _ParsedEventGroup:
    """EventGroup + transient metadata for client-side filtering before enrichment."""

    group: EventGroup
    raw_event_live: bool | None = None   # from list endpoint (often None)
    raw_event_ended: bool | None = None  # from list endpoint (often None)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _parse_market(
    raw: dict,
    category_override: str | None = None,
) -> MarketData | None:
    """
    Parse a single raw market dict from the Gamma API into a MarketData model.

    Returns None if required fields are missing or the market is invalid.
    Volume filtering is handled server-side (volume_min / volume_num_min)
    or by the caller — this function only validates field integrity.

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

        # Extract CLOB token IDs for price history fetching
        clob_token_ids: list[str] = []
        clob_raw = raw.get("clobTokenIds")
        if clob_raw:
            try:
                clob_token_ids = json.loads(clob_raw)
            except (json.JSONDecodeError, TypeError):
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
            clob_token_ids=clob_token_ids,
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


def _parse_events_to_markets(raw_events: list[dict]) -> list[_ParsedMarket]:
    """
    Parse raw event dicts into a flat, deduplicated list of _ParsedMarket.

    Filters out:
      - Events with ended=True (API field, populated on sports/esports events)
      - Markets that fail _parse_market() validation (missing fields, acceptingOrders=False)
      - Duplicate market IDs across events
    """
    results: list[_ParsedMarket] = []
    seen_ids: set[str] = set()

    for event in raw_events:
        if event.get("ended") is True:
            continue

        event_category = _category_from_event(event)
        event_live = event.get("live")
        event_ended = event.get("ended")

        for raw_market in event.get("markets") or []:
            market_id = str(raw_market.get("id", ""))
            if market_id in seen_ids:
                continue
            seen_ids.add(market_id)

            parsed = _parse_market(raw_market, category_override=event_category)
            if parsed is not None:
                results.append(_ParsedMarket(
                    market=parsed,
                    event_live=event_live,
                    event_ended=event_ended,
                ))

    return results


def _build_api_params(
    endpoint: str,
    limit: int,
    *,
    series_id: str | None = None,
    tag_id: str | None = None,
    volume_floor: float = 0.0,
    end_date_min: datetime | None = None,
    end_date_max: datetime | None = None,
    has_client_filters: bool = False,
) -> dict[str, str | int]:
    """
    Build query params dict for a Gamma API request.

    TIMING NOTE: end_date_min/max control RESOLUTION time (when markets expire),
    NOT when the real-world event starts. The Gamma API has no server-side filter
    for eventStartTime — all event-start filtering is client-side only.

    Args:
        endpoint:           "events" or "markets"
        limit:              User-requested market count (fetch_limit is scaled up)
        series_id:          Server-side series filter (events endpoint only)
        tag_id:             Server-side tag filter
        volume_floor:       Server-side volume minimum
        end_date_min/max:   Server-side RESOLUTION time window (not event start)
        has_client_filters: If True, fetch more to compensate for client-side filtering
    """
    # Scale up fetch_limit: we always fetch more than requested because
    # events contain multiple markets, some get filtered, etc.
    if has_client_filters:
        fetch_limit = max(limit * 5, 100)
    else:
        fetch_limit = max(limit * 3, 60)

    params: dict[str, str | int] = {
        "active": "true",
        "closed": "false",
        "order": "volume",
        "ascending": "false",
        "limit": fetch_limit,
    }

    if endpoint == "events":
        if series_id:
            params["series_id"] = series_id
        if tag_id:
            params["tag_id"] = tag_id
        if volume_floor > 0:
            params["volume_min"] = volume_floor
    else:
        # /markets endpoint uses different param names
        if tag_id:
            params["tag_id"] = tag_id
        if volume_floor > 0:
            params["volume_num_min"] = volume_floor

    if end_date_min is not None:
        params["end_date_min"] = end_date_min.strftime("%Y-%m-%dT%H:%M:%SZ")
    if end_date_max is not None:
        params["end_date_max"] = end_date_max.strftime("%Y-%m-%dT%H:%M:%SZ")

    return params


def _apply_client_filters(
    markets: list[_ParsedMarket],
    *,
    live: bool = False,
    starts_within_hours: int | None = None,
    resolve_within_hours: int | None = None,
    now: datetime,
) -> list[_ParsedMarket]:
    """
    Apply composable client-side filters to parsed markets.

    Filters:
      - live: keep only markets from live events (API's event.live field,
              fallback to event_start_time < now)
      - starts_within: keep markets whose event starts within N hours
                       (includes live events — no lower bound on start time)
      - resolve_within: belt-and-suspenders check on end_date range
    """
    result = markets

    if live and starts_within_hours is None:
        # --live only: keep markets from events the API marks as live,
        # or (fallback) where event_start_time has passed
        def _is_live(pm: _ParsedMarket) -> bool:
            if pm.event_live is True:
                return True
            if pm.event_live is False:
                return False
            # event_live is None (e.g. /markets path) — fallback to start time
            m = pm.market
            return m.event_start_time is not None and m.event_start_time < now

        result = [pm for pm in result if _is_live(pm)]

    elif starts_within_hours is not None:
        # --starts-within (with or without --live): include live + upcoming
        start_max = now + timedelta(hours=starts_within_hours)

        def _starts_in_window(pm: _ParsedMarket) -> bool:
            m = pm.market
            if m.event_start_time is None:
                return False
            return m.event_start_time <= start_max

        result = [pm for pm in result if _starts_in_window(pm)]

    if resolve_within_hours is not None:
        # Belt-and-suspenders: server already filters end_date, but double-check
        end_max = now + timedelta(hours=resolve_within_hours)
        result = [
            pm for pm in result
            if now <= pm.market.end_date <= end_max
        ]

    return result


# ---------------------------------------------------------------------------
# Core fetch function (replaces _fetch_markets_general + _fetch_markets_by_time_window)
# ---------------------------------------------------------------------------


async def _fetch_and_filter(
    limit: int,
    endpoint: str,
    series_id: str | None,
    tag_id: str | None,
    volume_floor: float,
    *,
    live: bool = False,
    starts_within_hours: int | None = None,
    resolve_within_hours: int | None = None,
) -> list[MarketData]:
    """
    Fetch markets from the Gamma API, parse, filter, sort, and return.

    Single function handling all query types (general, time-windowed, live).
    The endpoint ("events" or "markets") and filter params drive the behavior.
    """
    now = datetime.now(timezone.utc)
    has_client_filters = live or starts_within_hours is not None

    # end_date_min/max = RESOLUTION time bounds (when markets expire).
    # starts_within_hours is purely client-side (API has no eventStartTime filter).
    end_date_min = now  # exclude already-resolved markets
    end_date_max = (
        now + timedelta(hours=resolve_within_hours)
        if resolve_within_hours is not None
        else None
    )

    params = _build_api_params(
        endpoint,
        limit,
        series_id=series_id,
        tag_id=tag_id,
        volume_floor=volume_floor,
        end_date_min=end_date_min,
        end_date_max=end_date_max,
        has_client_filters=has_client_filters,
    )

    logger.debug(
        "_fetch_and_filter: endpoint=%s params=%s",
        endpoint,
        {k: v for k, v in params.items() if k not in ("active", "closed", "ascending")},
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{config.GAMMA_BASE_URL}/{endpoint}", params=params,
        )
        response.raise_for_status()
        raw_data: list[dict] = response.json()

    await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)

    # Parse raw API response into _ParsedMarket list
    if endpoint == "events":
        parsed = _parse_events_to_markets(raw_data)
    else:
        # /markets returns flat list of market dicts (no event wrapper)
        parsed = []
        for raw in raw_data:
            market = _parse_market(raw)
            if market is not None:
                parsed.append(_ParsedMarket(market=market))

    logger.debug(
        "_fetch_and_filter: API returned %d raw items → %d parsed markets",
        len(raw_data), len(parsed),
    )

    # Apply client-side filters (live, starts_within, resolve_within)
    filtered = _apply_client_filters(
        parsed,
        live=live,
        starts_within_hours=starts_within_hours,
        resolve_within_hours=resolve_within_hours,
        now=now,
    )

    # Sort: time-based queries by start time, general by volume
    if has_client_filters:
        filtered.sort(key=lambda pm: pm.market.event_start_time or pm.market.end_date)
    elif resolve_within_hours is not None:
        filtered.sort(key=lambda pm: pm.market.end_date)
    else:
        filtered.sort(key=lambda pm: pm.market.volume_usd, reverse=True)

    filtered = filtered[:limit]

    # Log summary
    filter_desc = []
    if live:
        filter_desc.append("live")
    if starts_within_hours is not None:
        filter_desc.append(f"starts_within={starts_within_hours}h")
    if resolve_within_hours is not None:
        filter_desc.append(f"resolve_within={resolve_within_hours}h")
    label = ", ".join(filter_desc) if filter_desc else "general"
    logger.info(
        "fetch_markets (%s): %d markets after filtering "
        "(api_items=%d, limit=%d, volume_floor=%.0f)",
        label, len(filtered), len(raw_data), limit, volume_floor,
    )

    return [pm.market for pm in filtered]


# ---------------------------------------------------------------------------
# Event-centric fetch pipeline (groups markets by parent event)
# ---------------------------------------------------------------------------


def _parse_events_to_groups(raw_events: list[dict]) -> list[_ParsedEventGroup]:
    """
    Parse raw event dicts into deduplicated EventGroup objects.

    Each event becomes one EventGroup containing its child markets.
    Filters out ended events and invalid markets (same rules as _parse_events_to_markets).

    "True Start Time" heuristic: uses the first non-None event_start_time
    from child markets (eventStartTime is a market-level API field).
    """
    results: list[_ParsedEventGroup] = []
    seen_market_ids: set[str] = set()

    for event in raw_events:
        if event.get("ended") is True:
            continue

        event_id = str(event.get("id", ""))
        if not event_id:
            continue

        event_title = event.get("title") or ""
        event_category = _category_from_event(event)
        event_live = event.get("live")
        event_ended = event.get("ended")

        markets: list[MarketData] = []
        for raw_market in event.get("markets") or []:
            market_id = str(raw_market.get("id", ""))
            if market_id in seen_market_ids:
                continue
            seen_market_ids.add(market_id)

            parsed = _parse_market(raw_market, category_override=event_category)
            if parsed is not None:
                markets.append(parsed)

        if not markets:
            continue

        # Sort by volume descending — primary_market = highest volume
        markets.sort(key=lambda m: m.volume_usd, reverse=True)

        # "True Start Time": first non-None event_start_time from child markets
        group_start_time: datetime | None = None
        for m in markets:
            if m.event_start_time is not None:
                group_start_time = m.event_start_time
                break

        total_volume = sum(m.volume_usd for m in markets)

        group = EventGroup(
            event_id=event_id,
            event_title=event_title,
            markets=markets,
            event_start_time=group_start_time,
            live=event_live,
            ended=event_ended,
            category=event_category,
            total_volume=total_volume,
        )
        results.append(_ParsedEventGroup(
            group=group,
            raw_event_live=event_live,
            raw_event_ended=event_ended,
        ))

    return results


def _apply_client_filters_groups(
    groups: list[_ParsedEventGroup],
    *,
    live: bool = False,
    starts_within_hours: int | None = None,
    resolve_within_hours: int | None = None,
    now: datetime,
) -> list[_ParsedEventGroup]:
    """
    Apply composable client-side filters to parsed event groups.

    Same predicates as _apply_client_filters but operates on _ParsedEventGroup.
    """
    result = groups

    if live and starts_within_hours is None:
        def _is_live(pg: _ParsedEventGroup) -> bool:
            if pg.raw_event_live is True:
                return True
            if pg.raw_event_live is False:
                return False
            # Fallback: check event_start_time
            g = pg.group
            return g.event_start_time is not None and g.event_start_time < now

        result = [pg for pg in result if _is_live(pg)]

    elif starts_within_hours is not None:
        start_max = now + timedelta(hours=starts_within_hours)

        def _starts_in_window(pg: _ParsedEventGroup) -> bool:
            g = pg.group
            if g.event_start_time is None:
                return False
            return g.event_start_time <= start_max

        result = [pg for pg in result if _starts_in_window(pg)]

    if resolve_within_hours is not None:
        end_max = now + timedelta(hours=resolve_within_hours)
        result = [
            pg for pg in result
            if any(now <= m.end_date <= end_max for m in pg.group.markets)
        ]

    return result


async def _enrich_event_groups(
    groups: list[_ParsedEventGroup],
    client: httpx.AsyncClient,
) -> list[_ParsedEventGroup]:
    """
    Enrich event groups with reliable live/ended/event_start_time data
    by fetching individual /events/{id} details in parallel.

    The list endpoint returns None for live/ended fields. Detail fetches
    populate them reliably for sports/esports events.

    Drops groups where enrichment reveals ended=True.
    """
    if not groups:
        return groups

    cap = config.EVENT_ENRICHMENT_MAX
    to_enrich = groups[:cap]

    async def _fetch_detail(pg: _ParsedEventGroup) -> _ParsedEventGroup:
        try:
            r = await asyncio.wait_for(
                client.get(f"{config.GAMMA_BASE_URL}/events/{pg.group.event_id}"),
                timeout=5.0,
            )
            r.raise_for_status()
            data = r.json()

            # Update live/ended from detail response
            if data.get("live") is not None:
                pg.group.live = data["live"]
                pg.raw_event_live = data["live"]
            if data.get("ended") is not None:
                pg.group.ended = data["ended"]
                pg.raw_event_ended = data["ended"]

            # Update event_start_time from detail response's embedded markets
            if pg.group.event_start_time is None:
                for raw_m in data.get("markets") or []:
                    est_raw = raw_m.get("eventStartTime")
                    if est_raw:
                        try:
                            pg.group.event_start_time = datetime.fromisoformat(
                                est_raw.replace("Z", "+00:00")
                            )
                            break
                        except (ValueError, TypeError):
                            pass

        except Exception as exc:
            logger.debug(
                "Enrichment failed for event %s: %s", pg.group.event_id, exc
            )

        return pg

    enriched = await asyncio.gather(*[_fetch_detail(pg) for pg in to_enrich])
    # Combine enriched + any unenriched remainder
    result = list(enriched) + groups[cap:]
    # Drop groups where enrichment revealed ended=True
    result = [pg for pg in result if pg.group.ended is not True]
    return result


async def _fetch_and_filter_groups(
    limit: int,
    endpoint: str,
    series_id: str | None,
    tag_id: str | None,
    volume_floor: float,
    *,
    live: bool = False,
    starts_within_hours: int | None = None,
    resolve_within_hours: int | None = None,
) -> list[EventGroup]:
    """
    Fetch events from the Gamma API, group markets, enrich, filter, and return.

    Flow:
      1. Build params + fetch /events list
      2. Parse into EventGroup objects (_parse_events_to_groups)
      3. Apply client-side filters (live, starts_within, resolve_within)
      4. Sort and truncate to candidates
      5. Enrich via parallel /events/{id} detail calls
      6. Re-filter (drop newly-ended), truncate to final limit
    """
    now = datetime.now(timezone.utc)
    has_client_filters = live or starts_within_hours is not None

    # end_date_min/max control RESOLUTION time bounds, NOT event start time.
    # starts_within_hours is purely client-side (API has no eventStartTime filter).
    end_date_min = now
    end_date_max = (
        now + timedelta(hours=resolve_within_hours)
        if resolve_within_hours is not None
        else None
    )

    params = _build_api_params(
        endpoint,
        limit,
        series_id=series_id,
        tag_id=tag_id,
        volume_floor=volume_floor,
        end_date_min=end_date_min,
        end_date_max=end_date_max,
        has_client_filters=has_client_filters,
    )

    # Override fetch_limit for event-centric queries: need more headroom
    # because events contain multiple markets and client-side filters are lossy.
    params["limit"] = min(limit * 10, 500)

    logger.debug(
        "_fetch_and_filter_groups: endpoint=%s params=%s",
        endpoint,
        {k: v for k, v in params.items() if k not in ("active", "closed", "ascending")},
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{config.GAMMA_BASE_URL}/{endpoint}", params=params,
        )
        response.raise_for_status()
        raw_data: list[dict] = response.json()

        await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)

        # Parse into event groups
        parsed = _parse_events_to_groups(raw_data)

        logger.debug(
            "_fetch_and_filter_groups: API returned %d raw events → %d groups",
            len(raw_data), len(parsed),
        )

        # Apply client-side filters
        filtered = _apply_client_filters_groups(
            parsed,
            live=live,
            starts_within_hours=starts_within_hours,
            resolve_within_hours=resolve_within_hours,
            now=now,
        )

        # Sort: time-based queries by start time, general by volume
        if has_client_filters:
            filtered.sort(
                key=lambda pg: pg.group.event_start_time or pg.group.markets[0].end_date
            )
        elif resolve_within_hours is not None:
            filtered.sort(
                key=lambda pg: min(m.end_date for m in pg.group.markets)
            )
        else:
            filtered.sort(key=lambda pg: pg.group.total_volume, reverse=True)

        # Truncate to candidates before enrichment (generous headroom)
        candidates = filtered[:limit * 2]

        # Enrich with parallel /events/{id} detail calls
        enriched = await _enrich_event_groups(candidates, client)

    # Final truncation
    enriched = enriched[:limit]

    # Log summary
    filter_desc = []
    if live:
        filter_desc.append("live")
    if starts_within_hours is not None:
        filter_desc.append(f"starts_within={starts_within_hours}h")
    if resolve_within_hours is not None:
        filter_desc.append(f"resolve_within={resolve_within_hours}h")
    label = ", ".join(filter_desc) if filter_desc else "general"
    logger.info(
        "fetch_events (%s): %d event groups after filtering+enrichment "
        "(api_events=%d, limit=%d, volume_floor=%.0f)",
        label, len(enriched), len(raw_data), limit, volume_floor,
    )

    return [pg.group for pg in enriched]


# ---------------------------------------------------------------------------
# Category resolution (untouched — well-separated)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Discovery helpers (untouched)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def _resolve_volume_and_category(
    category: str | None,
    volume_floor_override: float | None,
    resolve_within_hours: int | None,
    starts_within_hours: int | None,
    live: bool,
) -> tuple[float, str | None, str | None]:
    """
    Shared setup for fetch_markets() and fetch_events():
    compute volume floor + resolve category → (volume_floor, series_id, tag_id).

    Raises CategoryNotFoundError if --category is set but nothing matches.
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

    # Resolve category → series_id or tag_id
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

    return volume_floor, series_id, tag_id


async def fetch_markets(
    limit: int = config.MAX_MARKETS_TO_FETCH,
    category: str | None = None,
    resolve_within_hours: int | None = None,
    starts_within_hours: int | None = None,
    live: bool = False,
    volume_floor_override: float | None = None,
) -> list[MarketData]:
    """
    Fetch active markets from the Gamma API and return a flat list.

    Legacy entry point — returns individual MarketData objects.
    For event-centric grouped results, use fetch_events() instead.
    """
    volume_floor, series_id, tag_id = await _resolve_volume_and_category(
        category, volume_floor_override, resolve_within_hours, starts_within_hours, live,
    )

    use_events = bool(
        series_id or tag_id
        or resolve_within_hours is not None
        or starts_within_hours is not None
        or live
    )
    endpoint = "events" if use_events else "markets"

    return await _fetch_and_filter(
        limit=limit,
        endpoint=endpoint,
        series_id=series_id,
        tag_id=tag_id,
        volume_floor=volume_floor,
        live=live,
        starts_within_hours=starts_within_hours,
        resolve_within_hours=resolve_within_hours,
    )


async def fetch_events(
    limit: int = config.MAX_MARKETS_TO_FETCH,
    category: str | None = None,
    resolve_within_hours: int | None = None,
    starts_within_hours: int | None = None,
    live: bool = False,
    volume_floor_override: float | None = None,
) -> list[EventGroup]:
    """
    Fetch active events from the Gamma API, grouped by parent event.

    Event-centric entry point — returns EventGroup objects, each containing
    sibling markets sorted by volume. Includes parallel enrichment via
    /events/{id} detail calls for reliable live/ended/start_time data.

    Args:
        limit:                 Max event groups to return.
        category:              Series slug or broad tag (e.g. 'counter-strike', 'esports').
        resolve_within_hours:  Only return events with markets resolving within N hours.
        starts_within_hours:   Only return events starting within N hours (client-side).
        live:                  Only return live events (enriched via /events/{id}).
        volume_floor_override: Override config volume floor (use 0 to see all).

    Returns:
        With time/live filters: sorted by event start time asc.
        Otherwise: sorted by total event volume desc.
    """
    volume_floor, series_id, tag_id = await _resolve_volume_and_category(
        category, volume_floor_override, resolve_within_hours, starts_within_hours, live,
    )

    return await _fetch_and_filter_groups(
        limit=limit,
        endpoint="events",
        series_id=series_id,
        tag_id=tag_id,
        volume_floor=volume_floor,
        live=live,
        starts_within_hours=starts_within_hours,
        resolve_within_hours=resolve_within_hours,
    )


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

    # Reuse shared parsing (no volume floor for sibling discovery)
    parsed = _parse_events_to_markets([event_data])
    siblings = [pm.market for pm in parsed]
    siblings.sort(key=lambda m: m.volume_usd, reverse=True)
    return (event_title, siblings)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


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
