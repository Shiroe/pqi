# pqi/history.py
"""
Historical accuracy tracking — append council results to a JSONL file.

Each analyze run appends one JSON line to ~/.pqi/history.jsonl.
The `pqi history` CLI command reads and displays past runs.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pqi.models import CouncilResult

logger = logging.getLogger(__name__)

HISTORY_DIR = Path.home() / ".pqi"
HISTORY_FILE = HISTORY_DIR / "history.jsonl"


def append_result(result: CouncilResult) -> None:
    """Append a CouncilResult as one JSON line to the history file."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    entry = {
        "market_id": result.market.id,
        "question": result.market.question,
        "yes_price_at_run": result.market.yes_price,
        "final_score": round(result.final_score, 4),
        "no_score": round(result.no_score, 4),
        "edge": round(result.edge, 4),
        "no_edge": round(result.no_edge, 4),
        "recommendation": result.recommendation,
        "confidence_level": result.confidence_level,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    logger.debug("History entry appended for market %s", result.market.id)


def load_history() -> list[dict]:
    """Read all history entries from the JSONL file. Returns empty list if no file."""
    if not HISTORY_FILE.exists():
        return []

    entries = []
    with open(HISTORY_FILE) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed history line %d", line_num)
    return entries
