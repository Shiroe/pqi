# pqi/cache.py
"""
SQLite cache for council results.

Stores the full CouncilResult JSON keyed by market_id. On subsequent runs,
if the market's YES price hasn't moved more than CACHE_PRICE_DELTA, the
cached result is returned — skipping the briefing + council pipeline.
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
    market_id   TEXT PRIMARY KEY,
    yes_price   REAL NOT NULL,
    result_json TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


async def _get_db() -> aiosqlite.Connection:
    """Open (and lazily create) the cache database."""
    db_path = Path(config.CACHE_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(db_path))
    await db.execute(_CREATE_TABLE)
    await db.commit()
    return db


async def get_cached(market_id: str, current_yes_price: float) -> CouncilResult | None:
    """
    Return a cached CouncilResult if the price hasn't moved beyond the threshold.
    Returns None on cache miss or stale entry.
    """
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT yes_price, result_json FROM council_cache WHERE market_id = ?",
            (market_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        cached_price, result_json = row
        delta = abs(current_yes_price - cached_price)

        if delta > config.CACHE_PRICE_DELTA:
            logger.info(
                "Cache stale for %s: price moved %.1f%% (threshold %.1f%%)",
                market_id, delta * 100, config.CACHE_PRICE_DELTA * 100,
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
        await db.execute(
            """
            INSERT INTO council_cache (market_id, yes_price, result_json)
            VALUES (?, ?, ?)
            ON CONFLICT(market_id) DO UPDATE SET
                yes_price = excluded.yes_price,
                result_json = excluded.result_json,
                created_at = datetime('now')
            """,
            (result.market.id, result.market.yes_price, result_json),
        )
        await db.commit()
        logger.info("Cached result for market %s", result.market.id)
    finally:
        await db.close()
