"""
Phase 1 — one-shot prompt_flags backfill.

Walks ~/.pqi/history.jsonl and ensures every per-agent verdict carries an
explicit `prompt_flags` key. Every existing verdict pre-dates the Phase 1
rollout, so the correct semantic value is an empty dict:

    prompt_flags = {}     # ⇔ baseline / v1

which `models.phase1_flag_label` maps to `"v1"`. Once the Phase 1 rollout
starts, fresh verdicts will carry a non-empty sparse dict (e.g.
`{"preamble": True, "bear_advocacy": True}`) and the analytics layer can
group by the derived label.

Idempotent: verdicts that already carry `prompt_flags` are left untouched.
Counts per outcome are reported.

Atomic write: tmp + os.replace (same pattern as `history._rewrite_all` and
`scripts/b_backfill.py`). A `.prePhase1.bak` sibling of the original file
is created the first time the script modifies anything, so an immediate
revert is one `mv` away.

ZERO API calls.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

_PQI_DATA_DIR = Path(
    os.getenv("PQI_DATA_DIR", str(Path(__file__).resolve().parent.parent / ".pqi"))
).expanduser()
HISTORY_FILE = _PQI_DATA_DIR / "history.jsonl"
BACKUP_FILE = HISTORY_FILE.with_suffix(".jsonl.prePhase1.bak")


def _migrate_verdict(v: dict) -> str:
    """Mutate a verdict dict in place. Returns the action taken."""
    if not isinstance(v, dict):
        return "skipped_not_dict"
    if "prompt_flags" in v:
        return "already_tagged"
    v["prompt_flags"] = {}
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
    rows_touched = 0
    for entry in rows:
        result = entry.get("result") or {}
        verdicts = result.get("verdicts") or []
        row_changed = False
        for v in verdicts:
            action = _migrate_verdict(v)
            actions[action] += 1
            if action == "tagged":
                row_changed = True
        if row_changed:
            rows_touched += 1

    print(f"history file:    {HISTORY_FILE}")
    print(f"rows scanned:    {len(rows)} (malformed skipped: {malformed})")
    print(f"verdict actions: {dict(actions)}")
    print(f"rows touched:    {rows_touched}")

    if actions.get("tagged", 0) == 0:
        print("\nNothing to write — every verdict already carries prompt_flags.")
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
