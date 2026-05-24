"""
One-shot run_context backfill (session 41).

Walks ~/.pqi/history.jsonl and ensures every row carries a top-level
`run_context` block mirroring `pqi.models.RunContext`:

    run_context = {
        "analysis_ran_at":                  <entry.timestamp>,
        "event_start_time":                 <result.market.event_start_time>,
        "time_to_event_seconds":            <signed delta, or null>,
        "pre_or_live":                      <"pre" | "live" | "post" | "unknown">,
        "structured_match_status_at_run":   <"scheduled" | "running" | ...>,
    }

The `pre_or_live` label prefers `briefing.structured_match.status` when
present (authoritative) and falls back to a direct
`analysis_ran_at vs event_start_time` comparison. See
`RunContext.from_history_inputs`.

ZERO API calls — every input is already in the history row.

Idempotent: rows that already carry a `run_context` block are skipped.
Rows missing `timestamp` entirely cannot be anchored and are flagged.

Atomic write: tmp + os.replace (same pattern as `history._rewrite_all`,
`scripts/b_backfill.py`, `scripts/backfill_prompt_flags.py`). A
`.preRunContext.bak` sibling of the original file is created the first
time the script modifies anything, so an immediate revert is one `mv`
away.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

from pqi.models import RunContext

_PQI_DATA_DIR = Path(
    os.getenv("PQI_DATA_DIR", str(Path(__file__).resolve().parent.parent / ".pqi"))
).expanduser()
HISTORY_FILE = _PQI_DATA_DIR / "history.jsonl"
BACKUP_FILE = HISTORY_FILE.with_suffix(".jsonl.preRunContext.bak")


def _parse_iso_ts(raw: str | None):
    from datetime import datetime

    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _migrate_entry(entry: dict) -> str:
    """Mutate in place. Returns the action taken."""
    if entry.get("run_context") is not None:
        return "already_tagged"

    analysis_ran_at = _parse_iso_ts(entry.get("timestamp"))
    if analysis_ran_at is None:
        return "skipped_no_timestamp"

    result = entry.get("result") or {}
    market = result.get("market") or {}
    event_start_time = _parse_iso_ts(market.get("event_start_time"))

    structured_match_status: str | None = None
    briefing = result.get("briefing") or {}
    if isinstance(briefing, dict):
        structured_match = briefing.get("structured_match")
        if isinstance(structured_match, dict):
            status = structured_match.get("status")
            if isinstance(status, str):
                structured_match_status = status

    ctx = RunContext.from_history_inputs(
        analysis_ran_at=analysis_ran_at,
        event_start_time=event_start_time,
        structured_match_status=structured_match_status,
    )
    entry["run_context"] = ctx.model_dump(mode="json")
    return "tagged"


def main() -> int:
    if not HISTORY_FILE.exists():
        print(f"No history file at {HISTORY_FILE} — nothing to do.")
        return 0

    rows: list[dict] = []
    malformed = 0
    with open(HISTORY_FILE) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                malformed += 1
                print(f"  ! malformed line {line_num} skipped", file=sys.stderr)

    actions: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    for entry in rows:
        action = _migrate_entry(entry)
        actions[action] += 1
        if action == "tagged":
            ctx = entry.get("run_context") or {}
            label_counts[str(ctx.get("pre_or_live", "unknown"))] += 1

    print(f"history file:    {HISTORY_FILE}")
    print(f"rows scanned:    {len(rows)} (malformed skipped: {malformed})")
    print(f"row actions:     {dict(actions)}")
    if label_counts:
        print(f"pre_or_live:     {dict(label_counts)}")

    if actions.get("tagged", 0) == 0:
        print("\nNothing to write — every row already carries run_context.")
        return 0

    if not BACKUP_FILE.exists():
        shutil.copy2(HISTORY_FILE, BACKUP_FILE)
        print(f"backup written:  {BACKUP_FILE}")
    else:
        print(f"backup exists:   {BACKUP_FILE} (left untouched)")

    tmp_path = HISTORY_FILE.with_suffix(".jsonl.tmp")
    with open(tmp_path, "w") as f:
        for entry in rows:
            f.write(json.dumps(entry) + "\n")
    os.replace(tmp_path, HISTORY_FILE)
    print("history rewritten atomically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
