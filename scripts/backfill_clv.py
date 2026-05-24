"""
Pre-event CLV backfill — Phase 0 Task 0.2.

For every resolved BUY in ~/.pqi/history.jsonl this script fetches the
CLOB price history for the bought-side token and extracts the price in
the hourly bucket immediately *preceding* the market's event_start_time.

    CLV = bucket_price - entry_price

Positive CLV ⇒ we bought below the pre-event market mark (information
edge on entry quality, independent of whether the bet eventually won).

Output lives at ~/.pqi/clv.jsonl keyed by run_id. One row per resolved
BUY; rows are uniform-schema so DuckDB can register a clean view.

Idempotent: a second run skips rows whose CLV is already computed or
whose unavailability is permanent (no tokens stored, no event_start_time
from Gamma). Transient failures (network, missing CLOB bucket) are
retried each run. `--rebuild` discards the existing sidecar entirely.

Unavailable reasons (flagged, never silently dropped):

    no_event_start   Gamma returned None at analyze time
    no_tokens        pre-token-persistence history row (predates session 22)
    fetch_failed     CLOB returned an error or empty history
    no_bucket_before CLOB lifecycle started *after* event_start_time

Bucket policy: last-before-target. Picking closest-overall would risk
pulling the 60min-post-event bucket, which contains resolution-side
information. See Phase 0 Task 0.2 retention probe for the concrete
example (session 35).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

_PQI_DATA_DIR = Path(
    os.getenv("PQI_DATA_DIR", str(Path(__file__).resolve().parent.parent / ".pqi"))
).expanduser()
HISTORY_FILE = _PQI_DATA_DIR / "history.jsonl"
CLV_FILE = _PQI_DATA_DIR / "clv.jsonl"

CLOB_BASE_URL = "https://clob.polymarket.com"
CLOB_INTERVAL = "max"
CLOB_FIDELITY = 60

_PERMANENT_UNAVAILABLE = {"no_event_start", "no_tokens"}
_INTER_CALL_DELAY = 0.1


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _iter_jsonl(path: Path):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _bought_token(recommendation: str, token_ids: list[str]) -> str | None:
    if not isinstance(token_ids, list) or len(token_ids) < 2:
        return None
    if recommendation == "BUY YES":
        return token_ids[0]
    if recommendation == "BUY NO":
        return token_ids[1]
    return None


def _entry_price(recommendation: str, yes_price: float | None) -> float | None:
    if yes_price is None:
        return None
    if recommendation == "BUY YES":
        return yes_price
    if recommendation == "BUY NO":
        return 1.0 - yes_price
    return None


def _find_bucket_before(
    points: list[dict], target: datetime
) -> tuple[datetime, float] | None:
    """Return the latest (ts, price) with ts <= target, or None."""
    target_ts = target.timestamp()
    best: dict | None = None
    for pt in points:
        t = pt.get("t")
        if t is None or t > target_ts:
            continue
        if best is None or t > best["t"]:
            best = pt
    if best is None:
        return None
    return datetime.fromtimestamp(best["t"], tz=timezone.utc), float(best["p"])


def _fetch_clob(
    token_id: str, client: httpx.Client
) -> list[dict] | None:
    try:
        resp = client.get(
            f"{CLOB_BASE_URL}/prices-history",
            params={
                "market": token_id,
                "interval": CLOB_INTERVAL,
                "fidelity": CLOB_FIDELITY,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
    except Exception:
        return None
    data = resp.json()
    if isinstance(data, dict):
        return data.get("history") or []
    if isinstance(data, list):
        return data
    return []


def _empty_row(
    run_id: str,
    timestamp: str | None,
    category: str | None,
    event_start_time: str | None,
    recommendation: str,
    entry_price: float | None,
    bought_token_id: str | None,
) -> dict:
    """Uniform schema — all CLV rows share these keys."""
    return {
        "run_id": run_id,
        "timestamp": timestamp,
        "category": category,
        "event_start_time": event_start_time,
        "recommendation": recommendation,
        "entry_price": entry_price,
        "bought_token_id": bought_token_id,
        "bucket_timestamp": None,
        "bucket_drift_minutes": None,
        "bucket_price": None,
        "clv": None,
        "available": False,
        "unavailable_reason": None,
    }


def _compute_row(entry: dict, client: httpx.Client) -> dict:
    run_id = entry.get("run_id")
    result = entry.get("result") or {}
    market = result.get("market") or {}
    recommendation = result.get("recommendation") or ""
    yes_price = market.get("yes_price")
    token_ids = market.get("clob_token_ids") or []
    event_start_str = market.get("event_start_time")
    event_start = _parse_ts(event_start_str)
    entry_price = _entry_price(recommendation, yes_price)
    token_id = _bought_token(recommendation, token_ids)

    row = _empty_row(
        run_id=run_id,
        timestamp=entry.get("timestamp"),
        category=market.get("category"),
        event_start_time=event_start_str,
        recommendation=recommendation,
        entry_price=entry_price,
        bought_token_id=token_id,
    )

    if event_start is None:
        row["unavailable_reason"] = "no_event_start"
        return row

    if token_id is None:
        row["unavailable_reason"] = "no_tokens"
        return row

    points = _fetch_clob(token_id, client)
    if not points:
        row["unavailable_reason"] = "fetch_failed"
        return row

    bucket = _find_bucket_before(points, event_start)
    if bucket is None:
        row["unavailable_reason"] = "no_bucket_before"
        return row

    bucket_ts, bucket_price = bucket
    drift_minutes = (bucket_ts - event_start).total_seconds() / 60.0
    clv = bucket_price - entry_price if entry_price is not None else None

    row["bucket_timestamp"] = bucket_ts.isoformat()
    row["bucket_drift_minutes"] = round(drift_minutes, 2)
    row["bucket_price"] = round(bucket_price, 4)
    row["clv"] = round(clv, 4) if clv is not None else None
    row["available"] = True
    return row


def _is_cached(row: dict) -> bool:
    if row.get("available"):
        return True
    return row.get("unavailable_reason") in _PERMANENT_UNAVAILABLE


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill pre-event CLV into ~/.pqi/clv.jsonl"
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Recompute every row; ignore any existing clv.jsonl",
    )
    args = parser.parse_args()

    if not HISTORY_FILE.exists():
        print(f"No history file at {HISTORY_FILE}", file=sys.stderr)
        return 1

    existing: dict[str, dict] = {}
    if CLV_FILE.exists() and not args.rebuild:
        for row in _iter_jsonl(CLV_FILE):
            rid = row.get("run_id")
            if rid:
                existing[rid] = row
        print(f"Loaded {len(existing)} existing rows from {CLV_FILE}")

    candidates: list[dict] = []
    for entry in _iter_jsonl(HISTORY_FILE):
        if entry.get("legacy"):
            continue
        result = entry.get("result") or {}
        resolution = entry.get("resolution") or {}
        rec = result.get("recommendation") or ""
        if not rec.startswith("BUY"):
            continue
        if resolution.get("final_yes_price") is None:
            continue
        candidates.append(entry)

    print(f"Resolved BUYs to process: {len(candidates)}")
    if not candidates:
        print("Nothing to do.")
        return 0

    results: list[dict] = []
    actions: Counter[str] = Counter()

    with httpx.Client() as client:
        for i, entry in enumerate(candidates, 1):
            run_id = entry.get("run_id")
            if run_id is None:
                actions["skipped_no_run_id"] += 1
                continue

            if run_id in existing and _is_cached(existing[run_id]):
                results.append(existing[run_id])
                actions["cached"] += 1
                continue

            row = _compute_row(entry, client)
            results.append(row)
            if row["available"]:
                actions["computed"] += 1
                tag = f"CLV={row['clv']:+.4f}  drift={row['bucket_drift_minutes']:+.0f}min"
            else:
                actions[f"unavailable_{row['unavailable_reason']}"] += 1
                tag = f"unavailable: {row['unavailable_reason']}"
            print(f"  [{i:>3}/{len(candidates)}] {run_id[:8]}  {row['recommendation']:<8}  {tag}")

            if row["unavailable_reason"] not in _PERMANENT_UNAVAILABLE and row["unavailable_reason"] != "no_tokens":
                time.sleep(_INTER_CALL_DELAY)

    available = sum(1 for r in results if r["available"])
    total = len(candidates)
    coverage_pct = (available / total * 100.0) if total else 0.0

    print()
    print(f"CLV computed for {available} of {total} resolved BUYs — {coverage_pct:.1f}% coverage")
    for action in sorted(actions):
        print(f"  {action:<30} {actions[action]}")

    tmp = CLV_FILE.with_suffix(".jsonl.tmp")
    CLV_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w") as f:
        for row in results:
            f.write(json.dumps(row) + "\n")
    os.replace(tmp, CLV_FILE)
    print(f"wrote {len(results)} rows to {CLV_FILE}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
