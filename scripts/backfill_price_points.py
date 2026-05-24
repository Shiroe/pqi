"""
Raw CLOB trendline backfill — session 41.

Walks `~/.pqi/history.jsonl` and, for every row that does not already
have a matching record in `~/.pqi/price_points.jsonl`, fetches the
full YES and NO token lifecycles from Polymarket's CLOB and writes one
sidecar row per history entry. Forward-capture (see `pqi.main`) handles
fresh runs going forward; this script reconstructs the pre-session-41
backlog (plus anything forward-capture could not capture — e.g. CLOB
outages, cache-hit runs whose original fresh run predated this sidecar).

Fetch params: `interval=max, fidelity=60` — the same choice
`scripts/backfill_clv.py` makes. `interval=max` retrieves the entire
market lifecycle in one shot; `fidelity=60` produces hourly buckets,
which is what forward-capture also writes (`PRICE_HISTORY_FIDELITY=60`
default). The two datasets therefore have compatible fidelity; they
differ only in `captured_at` — forward rows were captured at analysis
time, backfill rows were captured post-hoc.

Idempotent: rows already in the sidecar with `available=True` or
`unavailable_reason="no_tokens"` (permanent — the history row has
fewer than 2 stored clob_token_ids, no way to retry) are skipped.
Transient failures (`fetch_failed`, `insufficient_points`) are retried
each run.

`--rebuild` discards the existing sidecar entirely and reprocesses
every history row.

Atomic write: tmp + os.replace (same pattern as `history._rewrite_all`,
`scripts/backfill_clv.py`). ZERO mutation of `history.jsonl`.
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
SIDECAR_FILE = _PQI_DATA_DIR / "price_points.jsonl"

CLOB_BASE_URL = "https://clob.polymarket.com"
CLOB_INTERVAL = "max"
CLOB_FIDELITY = 60

_PERMANENT_UNAVAILABLE = {"no_tokens"}
_INTER_CALL_DELAY = 0.1


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


def _fetch_side(token_id: str, client: httpx.Client) -> list[dict] | None:
    """Return the CLOB history list for one token, or None on failure.

    List items mirror the CLOB response: `{"t": <unix>, "p": <float>}`.
    Empty response is treated as failure (indistinguishable from a
    genuinely absent lifecycle for our purposes — both warrant a retry
    on the next backfill run).
    """
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
        points = data.get("history") or []
    elif isinstance(data, list):
        points = data
    else:
        points = []
    if not points:
        return None
    # Normalize to {"t": int, "p": float} with clipped price.
    out: list[dict] = []
    for pt in points:
        try:
            t = int(pt["t"])
            p = float(pt["p"])
            p = max(0.0, min(1.0, p))
            out.append({"t": t, "p": p})
        except (KeyError, ValueError, TypeError):
            continue
    return out or None


def _empty_row(
    run_id: str,
    timestamp: str | None,
    market_id: str,
    yes_token_id: str | None,
    no_token_id: str | None,
) -> dict:
    """Uniform schema — matches `sidecar_price_points.append_row()`."""
    return {
        "run_id": run_id,
        "analysis_ran_at": timestamp,
        "captured_at": None,
        "market_id": market_id,
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "interval": CLOB_INTERVAL,
        "fidelity_minutes": CLOB_FIDELITY,
        "yes_points": [],
        "no_points": [],
        "available": False,
        "unavailable_reason": None,
    }


def _compute_row(entry: dict, client: httpx.Client) -> dict:
    run_id = entry.get("run_id")
    result = entry.get("result") or {}
    market = result.get("market") or {}
    market_id = market.get("id") or ""
    token_ids = market.get("clob_token_ids") or []
    yes_token = token_ids[0] if len(token_ids) >= 1 else None
    no_token = token_ids[1] if len(token_ids) >= 2 else None

    row = _empty_row(
        run_id=run_id,
        timestamp=entry.get("timestamp"),
        market_id=market_id,
        yes_token_id=yes_token,
        no_token_id=no_token,
    )

    if yes_token is None or no_token is None:
        row["unavailable_reason"] = "no_tokens"
        return row

    yes_points = _fetch_side(yes_token, client)
    # Short gap between the two sides — CLOB rate limits are generous
    # but the backfill walks hundreds of markets and we want to stay
    # polite.
    time.sleep(_INTER_CALL_DELAY)
    no_points = _fetch_side(no_token, client)

    if yes_points is None or no_points is None:
        row["unavailable_reason"] = "fetch_failed"
        return row

    if len(yes_points) < 2 or len(no_points) < 2:
        row["unavailable_reason"] = "insufficient_points"
        return row

    row["yes_points"] = yes_points
    row["no_points"] = no_points
    row["captured_at"] = datetime.now(timezone.utc).isoformat()
    row["available"] = True
    return row


def _is_cached(row: dict) -> bool:
    if row.get("available"):
        return True
    return row.get("unavailable_reason") in _PERMANENT_UNAVAILABLE


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill raw CLOB trendlines into ~/.pqi/price_points.jsonl"
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Recompute every row; ignore any existing price_points.jsonl",
    )
    args = parser.parse_args()

    if not HISTORY_FILE.exists():
        print(f"No history file at {HISTORY_FILE}", file=sys.stderr)
        return 1

    existing: dict[str, dict] = {}
    if SIDECAR_FILE.exists() and not args.rebuild:
        for row in _iter_jsonl(SIDECAR_FILE):
            rid = row.get("run_id")
            if rid:
                existing[rid] = row
        print(f"Loaded {len(existing)} existing rows from {SIDECAR_FILE}")

    candidates: list[dict] = []
    for entry in _iter_jsonl(HISTORY_FILE):
        run_id = entry.get("run_id")
        if run_id is None:
            continue
        candidates.append(entry)

    print(f"History rows to process: {len(candidates)}")
    if not candidates:
        print("Nothing to do.")
        return 0

    results: list[dict] = []
    actions: Counter[str] = Counter()

    with httpx.Client() as client:
        for i, entry in enumerate(candidates, 1):
            run_id = entry["run_id"]

            if run_id in existing and _is_cached(existing[run_id]):
                results.append(existing[run_id])
                actions["cached"] += 1
                continue

            row = _compute_row(entry, client)
            results.append(row)
            if row["available"]:
                actions["computed"] += 1
                tag = (
                    f"YES={len(row['yes_points']):>4}pts "
                    f"NO={len(row['no_points']):>4}pts"
                )
            else:
                actions[f"unavailable_{row['unavailable_reason']}"] += 1
                tag = f"unavailable: {row['unavailable_reason']}"
            short_run = run_id[:8] if run_id else "????????"
            print(f"  [{i:>3}/{len(candidates)}] {short_run}  {tag}")

            # Be polite between markets when the fetch actually ran.
            if row["unavailable_reason"] != "no_tokens":
                time.sleep(_INTER_CALL_DELAY)

    available = sum(1 for r in results if r["available"])
    total = len(candidates)
    coverage_pct = (available / total * 100.0) if total else 0.0

    print()
    print(
        f"Trendlines captured for {available} of {total} history rows — "
        f"{coverage_pct:.1f}% coverage"
    )
    for action in sorted(actions):
        print(f"  {action:<30} {actions[action]}")

    tmp = SIDECAR_FILE.with_suffix(".jsonl.tmp")
    SIDECAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w") as f:
        for row in results:
            f.write(json.dumps(row) + "\n")
    os.replace(tmp, SIDECAR_FILE)
    print(f"wrote {len(results)} rows to {SIDECAR_FILE}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
