"""
Raw CLOB trendline sidecar — session 41.

Persists the full-fidelity YES/NO `PricePoint` lists that the analyze
pipeline fetches from Polymarket's CLOB. Aggregated trend metrics
(`PriceHistory` / `SidePriceHistory` on the briefing) are enough for the
council to decide, but the raw points are what downstream analytics —
MFE curves, calibration against live price drift, future web-UI
diagrams — need.

Storage shape: one JSON line per persisted `(run_id, fetch)` pair at
`~/.pqi/price_points.jsonl`. Decoupled from `history.jsonl` so the
`pqi history list/show` commands stay cheap and the hot-path JSONL
stays small. DuckDB registers a conditional `price_points` view over
this file (see `pqi/analytics_sql.py`).

Row schema::

    {
      "run_id":             "<uuid>",
      "analysis_ran_at":    "<iso8601>",   # mirrors history entry.timestamp
      "captured_at":        "<iso8601>",   # when this CLOB fetch ran
      "market_id":          "0x...",
      "yes_token_id":       "<clob token>",
      "no_token_id":        "<clob token>",
      "interval":           "1w" | "max" | ...,   # CLOB request param used
      "fidelity_minutes":   60,
      "yes_points":         [{"t": <unix>, "p": 0.72}, ...],
      "no_points":          [{"t": <unix>, "p": 0.28}, ...],
      "available":          true,
      "unavailable_reason": null            # set to a tag when available=false
    }

`available=false` rows exist only in the backfill path — the
forward-capture call site (in `pqi.main`) only writes when a fresh
CLOB fetch returned usable points. Unavailable reasons used by the
backfill: `no_tokens`, `fetch_failed`, `insufficient_points`.

ZERO dependency on the pydantic model layer at read time — the
DuckDB view consumes the raw JSONL directly. Writes still validate
shape at the edges (a `PricePoint` coming in must be a pydantic
instance with tz-aware `timestamp`).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pqi import config
from pqi.models import MarketData, PricePoint

logger = logging.getLogger(__name__)

SIDECAR_DIR = config.PQI_DATA_DIR
SIDECAR_FILE = SIDECAR_DIR / "price_points.jsonl"


def _serialize_points(points: list[PricePoint]) -> list[dict]:
    """Render pydantic PricePoints as compact `{t, p}` dicts.

    `t` is unix seconds (int) — matches CLOB's native format and keeps
    the JSONL rows small. `p` is a float in [0, 1].
    """
    return [{"t": int(p.timestamp.timestamp()), "p": float(p.price)} for p in points]


def append_row(
    *,
    run_id: str,
    market: MarketData,
    analysis_ran_at: datetime,
    interval: str,
    fidelity_minutes: int,
    yes_points: list[PricePoint] | None,
    no_points: list[PricePoint] | None,
    unavailable_reason: str | None = None,
    captured_at: datetime | None = None,
) -> None:
    """Append one row to `~/.pqi/price_points.jsonl`.

    Non-fatal: logs and swallows any OS error so a sidecar write
    failure cannot take down the analyze pipeline. The history row is
    already persisted at the call site — backfill can recover anything
    missed here.

    Contract: pass `yes_points` and `no_points` as non-None lists for
    a successful capture. Either being None triggers the `available=False`
    branch and requires `unavailable_reason` to be set.
    """
    available = yes_points is not None and no_points is not None
    if not available and unavailable_reason is None:
        # Defensive: forward-capture should never hit this branch. If it
        # does, log loudly but still write a row so the sidecar stays
        # aligned with history rows.
        logger.warning(
            "price_points.append_row: available=False with no reason (run_id=%s)",
            run_id,
        )
        unavailable_reason = "unknown"

    captured_at = captured_at or datetime.now(timezone.utc)

    yes_token_id = market.clob_token_ids[0] if len(market.clob_token_ids) >= 1 else None
    no_token_id = market.clob_token_ids[1] if len(market.clob_token_ids) >= 2 else None

    row: dict = {
        "run_id": run_id,
        "analysis_ran_at": analysis_ran_at.isoformat(),
        "captured_at": captured_at.isoformat(),
        "market_id": market.id,
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "interval": interval,
        "fidelity_minutes": fidelity_minutes,
        "yes_points": _serialize_points(yes_points) if yes_points else [],
        "no_points": _serialize_points(no_points) if no_points else [],
        "available": available,
        "unavailable_reason": unavailable_reason,
    }

    try:
        SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
        with open(SIDECAR_FILE, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as exc:
        logger.warning(
            "price_points sidecar write failed for run_id=%s: %s", run_id, exc
        )
