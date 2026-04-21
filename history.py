# pqi/history.py
"""
Historical accuracy tracking — append council results to a JSONL file.

Each analyze run appends one JSON line to ~/.pqi/history.jsonl containing the
full CouncilResult plus a run_id, timestamp, and a `resolution` block (initially
None) and `analysis` block (filled in lazily by the postmortem layer).

Updates to existing entries (resolution sync, analysis write-back) are written
via an atomic read-all → patch → tmp → rename cycle. The file is small enough
that this is acceptable.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from pqi.models import CouncilResult, RunContext

logger = logging.getLogger(__name__)

HISTORY_DIR = Path.home() / ".pqi"
HISTORY_FILE = HISTORY_DIR / "history.jsonl"


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _is_legacy(entry: dict) -> bool:
    """Legacy entries are flat scalars only — no `verdicts` array, no `result`."""
    return "verdicts" not in entry and "result" not in entry


def _parse_iso_ts(raw: str | None) -> datetime | None:
    """Parse a history-entry timestamp string into a tz-aware datetime.

    Tolerates the two forms we've written over the life of the file:
    `...Z` (UTC marker) and `...+00:00` (offset). Returns None on any parse
    failure so the caller can treat the field as unknown rather than
    crashing the load path.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _build_run_context(
    result: CouncilResult, analysis_ran_at: datetime
) -> dict:
    """Build a run_context dict from a CouncilResult at write time.

    Pulls `event_start_time` from `result.market` and
    `structured_match.status` from `result.briefing` when available. Both
    are optional — a None briefing or a missing structured_match yields a
    RunContext with `structured_match_status_at_run=None`, which is the
    correct contract for non-esports markets and resolver misses.
    """
    structured_match_status: str | None = None
    if result.briefing is not None and result.briefing.structured_match is not None:
        structured_match_status = result.briefing.structured_match.status

    ctx = RunContext.from_history_inputs(
        analysis_ran_at=analysis_ran_at,
        event_start_time=result.market.event_start_time,
        structured_match_status=structured_match_status,
    )
    return ctx.model_dump(mode="json")


def _ensure_run_context(entry: dict) -> dict:
    """In-memory default — give old loads a run_context if one is missing.

    No I/O. Idempotent. Pulls `analysis_ran_at` from `entry.timestamp`,
    `event_start_time` from `entry.result.market.event_start_time`, and
    `structured_match_status_at_run` from
    `entry.result.briefing.structured_match.status` (all fields tolerated
    as absent). The persistent rewrite is the job of
    `scripts/backfill_run_context.py`.
    """
    if entry.get("run_context") is not None:
        return entry

    analysis_ran_at = _parse_iso_ts(entry.get("timestamp"))
    if analysis_ran_at is None:
        # Can't anchor the block without a run timestamp — leave absent.
        return entry

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
    return entry


def _backfill_per_side_probability(entry: dict) -> dict:
    """In-memory Option B synthetic mirror + Phase 1 prompt_flags default.

    Ensures every verdict dict carries `probability_yes`, `probability_no`,
    `probability_source`, and `prompt_flags`. Missing fields are populated as
    `(p, 1-p, "synthetic_mirror")` and `{}` respectively so downstream
    consumers (analytics, history show, backtest scripts) see a uniform
    shape regardless of when the entry was written. Idempotent — entries
    with the new fields already set are returned untouched. No I/O; the
    persistent rewrites are the job of `scripts/b_backfill.py` and
    `scripts/backfill_prompt_flags.py`.
    """
    result = entry.get("result") or {}
    verdicts = result.get("verdicts") or []
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        p = v.get("probability")
        if p is None:
            continue
        if "probability_yes" not in v or v["probability_yes"] is None:
            v["probability_yes"] = p
        if "probability_no" not in v or v["probability_no"] is None:
            v["probability_no"] = 1.0 - p
        if "probability_source" not in v or v["probability_source"] is None:
            v["probability_source"] = "synthetic_mirror"
        if "prompt_flags" not in v or v["prompt_flags"] is None:
            v["prompt_flags"] = {}
    return entry


def _migrate_legacy_entry(entry: dict) -> dict:
    """Wrap a legacy flat entry so the rest of the code can treat it uniformly.

    Legacy entries lose per-agent detail (it was never recorded). They are
    flagged so analytics can exclude them from per-agent stats.
    """
    if not _is_legacy(entry):
        return entry
    return {
        "run_id": entry.get("run_id") or f"legacy-{entry.get('market_id', '?')}-{entry.get('timestamp', '')}",
        "timestamp": entry.get("timestamp", ""),
        "legacy": True,
        "result": {
            "market": {
                "id": entry.get("market_id", ""),
                "question": entry.get("question", ""),
                "yes_price": entry.get("yes_price_at_run", 0.0),
            },
            "final_score": entry.get("final_score", 0.0),
            "no_score": entry.get("no_score", 0.0),
            "edge": entry.get("edge", 0.0),
            "no_edge": entry.get("no_edge", 0.0),
            "recommendation": entry.get("recommendation", "PASS"),
            "confidence_level": entry.get("confidence_level", "LOW"),
            "verdicts": [],
            "signal_type": None,
            "pass_reason": "",
            "expected_roi": 0.0,
            "briefing": None,
        },
        "resolution": None,
        "analysis": None,
        "run_context": None,
    }


def _new_entry(result: CouncilResult) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "run_id": str(uuid.uuid4()),
        "timestamp": now.isoformat(),
        "legacy": False,
        "result": result.model_dump(mode="json"),
        "resolution": None,
        "analysis": None,
        "run_context": _build_run_context(result, now),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def append_result(result: CouncilResult) -> str:
    """Upsert a CouncilResult into the history file, keyed by market.id.

    If an entry for this market already exists, its `result` and `timestamp`
    are overwritten in place while the original `run_id`, `resolution`, and
    `analysis` blocks are preserved (so stats and stable references survive
    re-runs). Otherwise a new entry is appended.

    Keyed on market.id — not event.id — so sibling markets under the same
    parent event remain distinct entries.

    Returns the `run_id` of the written-or-updated entry so callers (e.g.
    the sidecar trendline writer) can key their own records off it.
    """
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    market_id = result.market.id
    entries = load_history()

    # Find an existing entry for the same market_id (skip legacy rows — they
    # have no stable per-market key anyway and are read-only for stats).
    existing_idx: int | None = None
    for i, e in enumerate(entries):
        if e.get("legacy"):
            continue
        existing = (e.get("result") or {}).get("market", {}).get("id")
        if existing == market_id:
            existing_idx = i
            break

    if existing_idx is not None:
        old = entries[existing_idx]
        # Full overwrite: the previous council read, resolution snapshot, and
        # postmortem analysis are all derived from now-stale inputs. Drop them.
        # Resolution will be re-fetched by the next `history sync`.
        # run_id is preserved so stable references (e.g. shared links) survive.
        now = datetime.now(timezone.utc)
        run_id = old.get("run_id") or str(uuid.uuid4())
        entries[existing_idx] = {
            "run_id": run_id,
            "timestamp": now.isoformat(),
            "legacy": False,
            "result": result.model_dump(mode="json"),
            "resolution": None,
            "analysis": None,
            "run_context": _build_run_context(result, now),
        }
        _rewrite_all(entries)
        logger.debug("History entry updated (market_id=%s, run_id=%s)", market_id, run_id)
        return run_id

    entry = _new_entry(result)
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    logger.debug("History entry appended (run_id=%s)", entry["run_id"])
    return entry["run_id"]


def load_history() -> list[dict]:
    """Read all history entries. Legacy entries are migrated in-memory.

    Returns entries in file order (oldest first).
    """
    if not HISTORY_FILE.exists():
        return []

    entries: list[dict] = []
    with open(HISTORY_FILE) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed history line %d", line_num)
                continue
            entries.append(
                _ensure_run_context(
                    _backfill_per_side_probability(_migrate_legacy_entry(raw))
                )
            )
    return entries


def iter_unresolved(past_end_only: bool = False) -> Iterator[dict]:
    """Yield entries that have no resolution recorded yet.

    If past_end_only is True, only yield entries whose market.end_date has
    already passed. Used by the auto-sync subprocess.
    """
    now = datetime.now(timezone.utc)
    for entry in load_history():
        if entry.get("resolution") is not None:
            continue
        if entry.get("legacy"):
            # legacy entries can still be resolved if we have a market_id
            market_id = (entry.get("result") or {}).get("market", {}).get("id")
            if not market_id:
                continue
        if past_end_only:
            end_date_raw = (entry.get("result") or {}).get("market", {}).get("end_date")
            if not end_date_raw:
                continue
            try:
                end_date = datetime.fromisoformat(end_date_raw.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if end_date > now:
                continue
        yield entry


def _rewrite_all(entries: list[dict]) -> None:
    """Atomically rewrite the entire history file from a list of entries."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = HISTORY_FILE.with_suffix(".jsonl.tmp")
    with open(tmp_path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    os.replace(tmp_path, HISTORY_FILE)


def _update_entry(run_id: str, patch: dict) -> bool:
    """Find entry by run_id and merge `patch` at the top level. Returns True if found."""
    entries = load_history()
    found = False
    for e in entries:
        if e.get("run_id") == run_id:
            e.update(patch)
            found = True
            break
    if found:
        _rewrite_all(entries)
    return found


def update_resolution(run_id: str, resolution: dict) -> bool:
    """Persist a resolution block onto the entry with the given run_id."""
    return _update_entry(run_id, {"resolution": resolution})


def update_resolutions_batch(resolutions: dict[str, dict]) -> int:
    """Persist many resolutions in a single load → patch → rewrite cycle.

    Safe for use after a parallel fetch pass: the serialized rewrite here
    replaces N overlapping `update_resolution` calls that would otherwise
    race on the jsonl file. Returns the number of entries actually patched.
    """
    if not resolutions:
        return 0
    entries = load_history()
    applied = 0
    for e in entries:
        rid = e.get("run_id")
        if rid in resolutions:
            e["resolution"] = resolutions[rid]
            applied += 1
    if applied:
        _rewrite_all(entries)
    return applied


def update_analysis(run_id: str, analysis: dict) -> bool:
    """Persist an analysis (postmortem) block onto the entry with the given run_id."""
    return _update_entry(run_id, {"analysis": analysis})
