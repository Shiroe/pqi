"""
Option B — one-shot synthetic-mirror backfill.

Walks ~/.pqi/history.jsonl and ensures every per-agent verdict carries the
new Option B fields:

    probability_yes    = probability       (preserve original P(YES))
    probability_no     = 1 - probability   (synthetic mirror)
    probability_source = "synthetic_mirror"

Idempotent: verdicts that already carry the fields are left untouched.
Counts per outcome are reported.

Atomic write: tmp + os.replace (same pattern as history._rewrite_all).
A .bak sibling of the original file is created the first time the script
modifies anything, so an immediate revert is one mv away.

ZERO API calls. The synthetic mirror preserves every existing
resolution-based metric exactly (lean rate, correct/wrong buy, EV gate,
Kelly, A.9 reliability weights). Option B analytics (per-side Brier,
coherence, side-bias) MUST filter on probability_source == "independent"
to ignore these synthesized rows.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

HISTORY_FILE = Path.home() / ".pqi" / "history.jsonl"
BACKUP_FILE = HISTORY_FILE.with_suffix(".jsonl.preB.bak")


def _migrate_verdict(v: dict) -> str:
    """Mutate a verdict dict in place. Returns the action taken."""
    if not isinstance(v, dict):
        return "skipped_not_dict"
    p = v.get("probability")
    if p is None:
        return "skipped_no_probability"

    has_yes = v.get("probability_yes") is not None
    has_no = v.get("probability_no") is not None
    has_src = v.get("probability_source") is not None

    if has_yes and has_no and has_src:
        return "already_migrated"

    if not has_yes:
        v["probability_yes"] = p
    if not has_no:
        v["probability_no"] = 1.0 - p
    if not has_src:
        v["probability_source"] = "synthetic_mirror"
    return "migrated"


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
    rows_touched = 0
    for entry in rows:
        result = entry.get("result") or {}
        verdicts = result.get("verdicts") or []
        row_changed = False
        for v in verdicts:
            action = _migrate_verdict(v)
            actions[action] += 1
            if action == "migrated":
                row_changed = True
        if row_changed:
            rows_touched += 1

    print(f"history file:    {HISTORY_FILE}")
    print(f"rows scanned:    {len(rows)} (malformed skipped: {malformed})")
    print(f"verdict actions: {dict(actions)}")
    print(f"rows touched:    {rows_touched}")

    if actions.get("migrated", 0) == 0:
        print("\nNothing to write — every verdict already carries the Option B fields.")
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
    print(f"history rewritten atomically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
