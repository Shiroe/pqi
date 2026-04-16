# pqi/cache.py
"""
SQLite cache for council results.

Stores the full CouncilResult JSON keyed by market_id. On subsequent runs,
the cached result is returned only when the situation is materially
unchanged. "Materially unchanged" today means:
  - YES price has not moved more than CACHE_PRICE_DELTA, AND
  - the PandaScore structured-match status (if any) has not changed.

A status change like SCHEDULED → RUNNING → FINISHED is the most important
event in a match's life cycle; price often barely budges through it (live
market thin / volatile). Invalidating on status change ensures the
briefing's STRUCTURED MATCH DATA + PRIOR MATCH FACTS blocks are recomputed
once the match starts and again once it resolves.
"""

import json
import logging
from pathlib import Path

import aiosqlite

from pqi import config
from pqi.models import CouncilResult

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS council_cache (
    market_id          TEXT PRIMARY KEY,
    yes_price          REAL NOT NULL,
    structured_status  TEXT,
    result_json        TEXT NOT NULL,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# Defensive migration for existing databases that predate the
# structured_status column. ALTER TABLE will raise if the column already
# exists; the caller catches that and proceeds.
_ADD_STRUCTURED_STATUS = (
    "ALTER TABLE council_cache ADD COLUMN structured_status TEXT"
)


async def _get_db() -> aiosqlite.Connection:
    """Open (and lazily create) the cache database."""
    db_path = Path(config.CACHE_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(db_path))
    await db.execute(_CREATE_TABLE)
    try:
        await db.execute(_ADD_STRUCTURED_STATUS)
    except aiosqlite.OperationalError:
        # Column already exists — fresh schema or prior migration.
        pass
    await db.commit()
    return db


async def get_cached(
    market_id: str,
    current_yes_price: float,
    current_structured_status: str | None = None,
) -> CouncilResult | None:
    """
    Return a cached CouncilResult only when materially unchanged.

    Stale conditions (any one fires → return None):
      - YES-price delta exceeds CACHE_PRICE_DELTA.
      - Structured-match status differs from the cached status (e.g.
        scheduled → running, running → finished). NULL status is treated
        as "no change" if both sides are NULL — i.e. cache works the
        same for non-esports markets where the resolver never runs.
    """
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT yes_price, structured_status, result_json "
            "FROM council_cache WHERE market_id = ?",
            (market_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        cached_price, cached_status, result_json = row
        delta = abs(current_yes_price - cached_price)

        if delta > config.CACHE_PRICE_DELTA:
            logger.info(
                "Cache stale for %s: price moved %.1f%% (threshold %.1f%%)",
                market_id, delta * 100, config.CACHE_PRICE_DELTA * 100,
            )
            return None

        if cached_status != current_structured_status:
            logger.info(
                "Cache stale for %s: structured status %r → %r",
                market_id, cached_status, current_structured_status,
            )
            return None

        logger.info("Cache hit for %s (price delta %.1f%%)", market_id, delta * 100)
        data = json.loads(result_json)
        return CouncilResult.model_validate(data)
    finally:
        await db.close()


async def store_result(result: CouncilResult) -> None:
    """Upsert a CouncilResult into the cache."""
    db = await _get_db()
    try:
        result_json = result.model_dump_json()
        # Pull the current structured status off the briefing if present.
        # Older results without a briefing or without a structured_match
        # store NULL — that matches the legacy behavior for everything
        # outside esports.
        structured_status: str | None = None
        if result.briefing is not None and result.briefing.structured_match is not None:
            structured_status = result.briefing.structured_match.status
        await db.execute(
            """
            INSERT INTO council_cache (
                market_id, yes_price, structured_status, result_json
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(market_id) DO UPDATE SET
                yes_price = excluded.yes_price,
                structured_status = excluded.structured_status,
                result_json = excluded.result_json,
                created_at = datetime('now')
            """,
            (result.market.id, result.market.yes_price, structured_status, result_json),
        )
        await db.commit()
        logger.info("Cached result for market %s (status=%r)", result.market.id, structured_status)
    finally:
        await db.close()
