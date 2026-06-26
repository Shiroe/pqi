"""football-data.org REST client — minimal async wrapper for the soccer
structured-data provider.

Mirrors `pqi/ingestion/pandascore.py`: a thin, mockable HTTP surface, one
function per endpoint we consume, every public method non-fatal (returns None
or [] and logs a warning instead of raising). Game-slug routing, fuzzy
matching, and record building live in the provider adapter / shared skeleton,
not here.

API v4 reference: https://docs.football-data.org/general/v4/resources.html
Auth: `X-Auth-Token` header. Free tier ~10 req/min over 12 competitions
(World Cup + big-5 + CL/Euros). 429 → degrade.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from pqi import config

logger = logging.getLogger(__name__)

# Generous per-call timeout — degrade per-request rather than fail the pipeline.
_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


def make_client() -> httpx.AsyncClient:
    """Fresh client with the auth header pre-attached. Caller owns lifecycle."""
    headers = (
        {"X-Auth-Token": config.FOOTBALL_DATA_API_KEY}
        if config.FOOTBALL_DATA_API_KEY
        else {}
    )
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers)


async def _get(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, Any] | None = None,
) -> Any | None:
    """Single GET against `{FOOTBALL_DATA_BASE_URL}{path}`. Returns parsed JSON
    on success, or None on any HTTP / network / decoding error. Never raises."""
    url = f"{config.FOOTBALL_DATA_BASE_URL}{path}"
    try:
        response = await client.get(url, params=params or {})
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.warning("football-data GET %s failed: %s", path, exc)
        return None

    if response.status_code == 429:
        logger.warning("football-data GET %s rate-limited (429) — degrading", path)
        return None
    if response.status_code >= 400:
        logger.warning(
            "football-data GET %s returned HTTP %d", path, response.status_code
        )
        return None

    try:
        return response.json()
    except ValueError as exc:
        logger.warning("football-data GET %s: invalid JSON body (%s)", path, exc)
        return None


async def list_matches_in_window(
    client: httpx.AsyncClient,
    center: datetime,
    window_hours: float,
) -> list[dict]:
    """Matches across the free-tier competitions whose UTC date intersects the
    ±window_hours span around `center`. The endpoint filters by DATE
    (`dateFrom`/`dateTo`, YYYY-MM-DD); the caller narrows to the exact hour
    window. Returns `[]` on any error.

    `center` MUST be tz-aware (UTC) — naive input is a programming error.
    """
    if center.tzinfo is None:
        raise ValueError("list_matches_in_window: `center` must be tz-aware")
    # football-data treats dateFrom/dateTo as inclusive instants at 00:00 UTC,
    # so a match at 19:00 on day D is only returned when dateTo >= D+1 (a query
    # with dateFrom == dateTo returns nothing). Push dateTo one day past the
    # window-end date; the caller re-narrows to the exact ±hour window.
    date_from = (center - timedelta(hours=window_hours)).strftime("%Y-%m-%d")
    window_end = (center + timedelta(hours=window_hours)).date()
    date_to = (window_end + timedelta(days=1)).strftime("%Y-%m-%d")
    data = await _get(
        client, "/matches", {"dateFrom": date_from, "dateTo": date_to}
    )
    if not isinstance(data, dict):
        return []
    matches = data.get("matches")
    return matches if isinstance(matches, list) else []


async def get_team_matches(
    client: httpx.AsyncClient,
    team_id: str | int,
    count: int,
) -> list[dict]:
    """A team's most recent FINISHED matches (for the form snapshot). Returns
    up to `count` items, or `[]` on failure."""
    data = await _get(
        client,
        f"/teams/{team_id}/matches",
        {"status": "FINISHED", "limit": count},
    )
    if not isinstance(data, dict):
        return []
    matches = data.get("matches")
    return matches if isinstance(matches, list) else []


async def get_head2head(
    client: httpx.AsyncClient,
    match_id: str | int,
    count: int = 10,
) -> list[dict]:
    """Recent prior encounters between the two parties of `match_id`, via
    `/matches/{id}/head2head` (returns aggregates + a dated `matches` list).
    Returns the match list (`[]` on failure)."""
    data = await _get(
        client, f"/matches/{match_id}/head2head", {"limit": count}
    )
    if not isinstance(data, dict):
        return []
    matches = data.get("matches")
    return matches if isinstance(matches, list) else []
