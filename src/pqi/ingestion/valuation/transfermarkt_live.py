"""Live Transfermarkt squad-value source.

Resolves a national team's aggregate squad market value via a community
Transfermarkt API service (default: the public test instance — see
`config.TRANSFERMARKT_API_BASE`). The aggregate value is available directly on
the `/clubs/search/{name}` hit, so no per-player summing is needed.

Non-fatal by contract: any failure (instance down, 429, team not found, only
youth/women variants matched, below fuzzy threshold) returns None and the
pipeline continues with no `squad_valuation`.

Resolved values are cached to a persistent JSON file (same pattern as the
category cache) with a multi-day TTL — national-team squad value is near-static,
and the public instance is rate-limited.

⚠️ The default instance scrapes Transfermarkt and is explicitly "for testing".
Self-host (felipeall/transfermarkt-api) and repoint `TRANSFERMARKT_API_BASE`
for anything beyond signal validation. See docs/squad_value_enrichment_plan.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from rapidfuzz import fuzz

from pqi import config
from pqi.ingestion.valuation.base import TeamValuationSource
from pqi.models import TeamValuation

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

# Youth / reserve / women / Olympic variants that `/clubs/search` returns
# alongside the senior side — excluded so "Spain" never resolves to "Spain U21".
_NON_SENIOR_RE = re.compile(
    r"\b(?:U\d{2}|U23|Olympic|Women|Wome?ns?|Ladies|Fem|Reserve|Youth)\b|\bW$",
    re.IGNORECASE,
)


def _normalize(name: str) -> str:
    return " ".join(name.lower().split())


# ---------------------------------------------------------------------------
# Persistent value cache (mirrors polymarket._load/_save_category_cache)
# ---------------------------------------------------------------------------


def _load_cache() -> dict:
    path = Path(config.TRANSFERMARKT_VALUE_CACHE_PATH)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("transfermarkt value cache unreadable (%s); ignoring", exc)
        return {}


def _save_cache(cache: dict) -> None:
    path = Path(config.TRANSFERMARKT_VALUE_CACHE_PATH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
        tmp.replace(path)
    except OSError as exc:
        logger.warning("could not persist transfermarkt value cache: %s", exc)


def _cache_get(key: str) -> TeamValuation | None:
    entry = _load_cache().get(key)
    if not isinstance(entry, dict):
        return None
    as_of_raw = entry.get("as_of")
    try:
        as_of = datetime.fromisoformat(as_of_raw) if as_of_raw else None
    except (ValueError, TypeError):
        as_of = None
    if as_of is None:
        return None
    age_days = (datetime.now(timezone.utc) - as_of).total_seconds() / 86400
    if age_days > config.TRANSFERMARKT_CACHE_TTL_DAYS:
        return None
    return TeamValuation(
        name=entry.get("name", key),
        market_value_eur=entry.get("market_value_eur"),
        squad_size=entry.get("squad_size"),
        source="transfermarkt-live",
        as_of=as_of,
    )


def _cache_put(key: str, tv: TeamValuation) -> None:
    cache = _load_cache()
    cache[key] = {
        "name": tv.name,
        "market_value_eur": tv.market_value_eur,
        "squad_size": tv.squad_size,
        "as_of": (tv.as_of or datetime.now(timezone.utc)).isoformat(),
    }
    _save_cache(cache)


# ---------------------------------------------------------------------------
# Senior-national-team disambiguation (pure)
# ---------------------------------------------------------------------------


def _pick_senior_team(results: list[dict], query: str, threshold: int) -> dict | None:
    """Choose the senior national team from `/clubs/search` results.

    1. national teams only (`country` present)
    2. drop youth / women / reserve variants
    3. fuzzy-gate the name against the query
    4. prefer exact normalized match; tie-break by market value then squad size
       (the senior side dominates both).
    """
    q = _normalize(query)
    scored: list[tuple[bool, int, int, dict]] = []
    for r in results:
        name = r.get("name") or ""
        if not r.get("country") or not name:
            continue
        if _NON_SENIOR_RE.search(name):
            continue
        score = max(
            fuzz.token_sort_ratio(q, _normalize(name)),
            fuzz.ratio(q, _normalize(name)),
        )
        if score < threshold:
            continue
        exact = _normalize(name) == q
        scored.append((exact, int(r.get("marketValue") or 0), int(r.get("squad") or 0), r))
    if not scored:
        return None
    # exact match wins; then highest market value; then largest squad.
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return scored[0][3]


class TransfermarktLiveSource(TeamValuationSource):
    name = "transfermarkt-live"
    display_name = "Transfermarkt"

    @property
    def enabled(self) -> bool:
        return config.TRANSFERMARKT_ENABLED and bool(config.TRANSFERMARKT_API_BASE)

    @property
    def fuzzy_threshold(self) -> int:
        return config.TRANSFERMARKT_FUZZY_THRESHOLD

    async def lookup(self, team_name: str) -> TeamValuation | None:
        if not team_name or not team_name.strip():
            return None
        key = _normalize(team_name)

        cached = _cache_get(key)
        if cached is not None:
            logger.debug("transfermarkt cache hit for %r", team_name)
            return cached

        url = f"{config.TRANSFERMARKT_API_BASE.rstrip('/')}/clubs/search/{quote(team_name)}"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
            await asyncio.sleep(config.TRANSFERMARKT_RATE_LIMIT_DELAY)
            if resp.status_code == 429:
                logger.warning("transfermarkt rate-limited (429) for %r — degrading", team_name)
                return None
            if resp.status_code >= 400:
                logger.warning("transfermarkt search %r → HTTP %d", team_name, resp.status_code)
                return None
            results = resp.json().get("results", [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("transfermarkt lookup failed for %r: %s", team_name, exc)
            return None

        chosen = _pick_senior_team(results, team_name, self.fuzzy_threshold)
        if chosen is None:
            logger.debug("transfermarkt: no senior national team for %r", team_name)
            return None

        tv = TeamValuation(
            name=chosen.get("name") or team_name,
            market_value_eur=(int(chosen["marketValue"]) if chosen.get("marketValue") else None),
            squad_size=(int(chosen["squad"]) if chosen.get("squad") else None),
            source="transfermarkt-live",
            as_of=datetime.now(timezone.utc),
        )
        if tv.market_value_eur:
            _cache_put(key, tv)
        return tv
