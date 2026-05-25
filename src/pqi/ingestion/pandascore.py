"""
PandaScore REST client — minimal async wrapper for the structured-data
resolver.

Responsibilities (kept narrow on purpose):
  - Authenticated `httpx.AsyncClient` calls against `api.pandascore.co`
  - One small surface per endpoint we actually use (matches list, single
    match, team search, team's recent matches, head-to-head)
  - Non-fatal errors: every public method returns None (or empty list)
    and logs a warning instead of raising. Callers (`match_resolver.py`)
    decide what to do with degraded data.

What this module does NOT do:
  - Game-slug routing (lives in `match_resolver.py`)
  - Fuzzy matching of market questions against canonical team names
    (lives in `match_resolver.py`)
  - Cache-key invalidation (lives in `cache.py`)
  - Building `StructuredMatchRecord` (lives in `match_resolver.py`)

This split keeps the HTTP layer mockable: tests can patch the four
public functions without touching httpx.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from pqi import config

logger = logging.getLogger(__name__)


# Per-call HTTP timeout. Generous — PandaScore has occasional sub-second
# spikes and we'd rather degrade per-request than fail the whole pipeline.
_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


def _auth_headers() -> dict[str, str]:
    """Bearer-token header. Empty when no key is configured."""
    if not config.PANDASCORE_API_KEY:
        return {}
    return {"Authorization": f"Bearer {config.PANDASCORE_API_KEY}"}


async def _get(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, Any] | None = None,
) -> Any | None:
    """
    Single GET against `{PANDASCORE_BASE_URL}{path}` with auth header.

    Returns the parsed JSON body on success (list or dict, depending on
    endpoint), or None on any HTTP / network / decoding error. Never raises.

    Logs at WARNING with the path so failures are easy to spot in `--verbose`
    runs without leaking the API key.
    """
    url = f"{config.PANDASCORE_BASE_URL}{path}"
    try:
        response = await client.get(url, params=params or {}, headers=_auth_headers())
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.warning("PandaScore GET %s failed: %s", path, exc)
        return None

    if response.status_code == 429:
        logger.warning("PandaScore GET %s rate-limited (429) — degrading", path)
        return None
    if response.status_code >= 400:
        # Don't log the full body — error pages can be HTML, and the status
        # alone is enough for the caller to decide to skip.
        logger.warning(
            "PandaScore GET %s returned HTTP %d", path, response.status_code,
        )
        return None

    try:
        return response.json()
    except ValueError as exc:
        logger.warning("PandaScore GET %s: invalid JSON body (%s)", path, exc)
        return None


# ---------------------------------------------------------------------------
# Public API — one function per endpoint we consume
# ---------------------------------------------------------------------------


async def list_videogames(client: httpx.AsyncClient) -> list[dict] | None:
    """List the games PandaScore covers. Used at module import time by
    `match_resolver.py` to validate the GAME_MAPPING table — if a slug we
    rely on disappears upstream we want a loud warning, not a silent miss.

    Returns the raw list of `{slug, name, ...}` dicts, or None on failure.
    """
    data = await _get(client, "/videogames")
    if not isinstance(data, list):
        return None
    return data


async def list_matches_in_window(
    client: httpx.AsyncClient,
    game_slug: str,
    center: datetime,
    window_hours: float,
    *,
    per_page: int = 50,
) -> list[dict]:
    """
    Fetch matches scheduled within ±window_hours of `center` for `game_slug`.

    Uses PandaScore's range filter on `scheduled_at`. Returns an empty list
    on any error or if the response shape is unexpected.

    `center` MUST be timezone-aware (UTC). Passing naive will raise — we
    treat that as a programming error, not a degradation.
    """
    if center.tzinfo is None:
        raise ValueError("list_matches_in_window: `center` must be tz-aware")
    start = (center - timedelta(hours=window_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (center + timedelta(hours=window_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    path = f"/{game_slug}/matches"
    params = {
        "range[scheduled_at]": f"{start},{end}",
        "sort": "scheduled_at",
        "per_page": per_page,
    }
    data = await _get(client, path, params)
    if not isinstance(data, list):
        return []
    return data


async def get_match(
    client: httpx.AsyncClient,
    match_id: str,
) -> dict | None:
    """
    Fetch a single match by its PandaScore ID. Returns the raw dict or
    None on failure.

    Cross-game endpoint (`/matches/{id}`) — same path regardless of the
    underlying videogame slug.
    """
    data = await _get(client, f"/matches/{match_id}")
    if not isinstance(data, dict):
        return None
    return data


async def get_team_past_matches(
    client: httpx.AsyncClient,
    team_id: int | str,
    count: int,
) -> list[dict]:
    """
    Fetch a team's most recent finished matches. Used to build the
    TeamForm snapshot.

    Returns up to `count` items (PandaScore caps `per_page` at 100), or
    [] on failure.
    """
    path = f"/teams/{team_id}/matches"
    params = {
        "filter[past]": "true",
        "sort": "-scheduled_at",
        "per_page": min(count, 100),
    }
    data = await _get(client, path, params)
    if not isinstance(data, list):
        return []
    return data


async def get_h2h(
    client: httpx.AsyncClient,
    team_a_id: int | str,
    team_b_id: int | str,
    count: int = 10,
) -> list[dict]:
    """
    Fetch the most recent finished encounters between two specific teams.

    PandaScore exposes this via `/teams/{id}/matches/past` filtered by
    opponent_id. We page through team A's past matches and keep only
    those whose `opponents` set includes team B. Capped at `count`.

    Returns [] on any failure or when the two teams have no recorded
    prior meetings.
    """
    # Ask for a generous slice of team A's past matches; we filter client-side
    # by opponent. PandaScore's per_page max is 100; we don't need more than
    # that for H2H purposes.
    raw = await get_team_past_matches(client, team_a_id, count=100)
    if not raw:
        return []

    target_id = str(team_b_id)
    h2h: list[dict] = []
    for match in raw:
        opponents = match.get("opponents") or []
        ids: list[str] = []
        for op in opponents:
            opp = op.get("opponent") if isinstance(op, dict) else None
            if isinstance(opp, dict) and opp.get("id") is not None:
                ids.append(str(opp["id"]))
        if target_id in ids:
            h2h.append(match)
            if len(h2h) >= count:
                break
    return h2h


# ---------------------------------------------------------------------------
# Convenience: short-lived async client
# ---------------------------------------------------------------------------


def make_client() -> httpx.AsyncClient:
    """
    Return a fresh httpx.AsyncClient configured for PandaScore.

    Caller is responsible for `async with` lifecycle — keeping that explicit
    so the resolver can batch multiple GETs over one connection without
    leaking sockets.
    """
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
