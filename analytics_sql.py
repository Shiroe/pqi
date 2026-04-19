"""
SQL analytics layer over ~/.pqi/history.jsonl via DuckDB.

Exposes a small surface for ad-hoc queries from the `pqi query` CLI and
from scripts. Pre-registered views:

  history    — top-level rows with nested `result` / `resolution` /
               `analysis` / `run_context` accessible via dot notation
               (e.g. `result.market.category`,
               `resolution.winning_side`). Also hoists
               `run_context.{analysis_ran_at, event_start_time,
               time_to_event_seconds, pre_or_live,
               structured_match_status_at_run}` as top-level columns
               for ergonomic filtering / grouping (session 41).
  verdicts   — flattened join of `(run_id, timestamp, category, yes_price,
               recommendation, winner, final_yes_price)` × each agent
               verdict, via `UNNEST(result.verdicts)`. Adds a derived
               `flag_label` column (session 40) mirroring
               `models.phase1_flag_label`:
                 - `v1`          → baseline / legacy
                 - `v2:<keys>`   → sorted comma-joined list of active
                                   Phase 1 flags.
               Also surfaces `prompt_flags` (MAP<VARCHAR,JSON>) for
               ad-hoc filters on individual mechanisms.
  clv_by_row — pre-event close-line-value per resolved BUY
               (`~/.pqi/clv.jsonl`, populated by
               `scripts/backfill_clv.py`). Keyed by `run_id`; joinable
               against `history` / `verdicts`. Registered only when the
               sidecar exists — downstream queries on `clv_by_row`
               raise a clear error otherwise.

Timestamp format is pinned because DuckDB's auto-inference sometimes
samples a nested date field and ends up rejecting top-level timestamps.
`sample_size=-1` forces a full-file schema pass, which costs ~50ms on
the current 13MB file.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from pqi.history import HISTORY_FILE

CLV_FILE = Path.home() / ".pqi" / "clv.jsonl"

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"


def _read_json_clause(history_path: Path) -> str:
    return (
        f"read_json_auto("
        f"'{history_path}', "
        f"format='newline_delimited', "
        f"timestampformat='{_TIMESTAMP_FORMAT}', "
        f"sample_size=-1"
        f")"
    )


def get_connection(
    history_path: Path | None = None,
    clv_path: Path | None = None,
) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB connection with the standard views."""
    path = history_path or HISTORY_FILE
    if not path.exists():
        raise FileNotFoundError(f"history file not found: {path}")

    con = duckdb.connect(":memory:")
    read_clause = _read_json_clause(path)

    con.execute(
        f"""
        CREATE VIEW history AS
        SELECT
            *,
            run_context.analysis_ran_at                  AS analysis_ran_at,
            run_context.event_start_time                 AS event_start_time,
            run_context.time_to_event_seconds            AS time_to_event_seconds,
            run_context.pre_or_live                      AS pre_or_live,
            run_context.structured_match_status_at_run   AS structured_match_status_at_run
        FROM {read_clause}
        """
    )
    con.execute(
        """
        CREATE VIEW verdicts AS
        WITH unnested AS (
            SELECT
                run_id,
                timestamp,
                result.market.category        AS category,
                result.market.question        AS question,
                result.market.yes_price       AS entry_yes_price,
                result.recommendation         AS recommendation,
                result.pass_reason            AS pass_reason,
                result.signal_type            AS signal_type,
                result.final_score            AS final_score,
                result.no_score               AS no_score,
                resolution.winning_side       AS winning_side,
                resolution.final_yes_price    AS final_yes_price,
                UNNEST(result.verdicts)       AS v
            FROM history
            WHERE legacy = false
        )
        SELECT
            *,
            v.prompt_flags                AS prompt_flags,
            CASE
                WHEN v.prompt_flags IS NULL
                  OR len(map_keys(v.prompt_flags)) = 0
                THEN 'v1'
                ELSE 'v2:' || array_to_string(list_sort(map_keys(v.prompt_flags)), ',')
            END                           AS flag_label
        FROM unnested
        """
    )

    clv = clv_path or CLV_FILE
    if clv.exists():
        con.execute(
            f"CREATE VIEW clv_by_row AS SELECT * FROM {_read_json_clause(clv)}"
        )

    return con


def query(
    sql: str, history_path: Path | None = None
) -> duckdb.DuckDBPyRelation:
    """Run `sql` against the history views. Returns a DuckDB relation.

    Callers can `.fetchall()`, `.fetchdf()`, `.pl()`, or pass directly to
    `render_table()` for CLI display.
    """
    con = get_connection(history_path)
    return con.sql(sql)


# ---------------------------------------------------------------------------
# Rich rendering
# ---------------------------------------------------------------------------

def render_table(
    relation: duckdb.DuckDBPyRelation,
    title: str | None = None,
    max_rows: int = 100,
):
    """Convert a DuckDB relation to a rich.Table, truncating at max_rows."""
    from rich.table import Table
    from rich import box

    columns = relation.columns
    rows = relation.fetchmany(max_rows)

    table = Table(title=title, box=box.ROUNDED, show_lines=False)
    for col in columns:
        table.add_column(col, style="white", overflow="fold")

    for row in rows:
        table.add_row(*(_fmt(cell) for cell in row))

    truncated = len(rows) == max_rows
    return table, truncated


def _fmt(cell) -> str:
    """Minimal cell formatter — DuckDB gives back python types already."""
    if cell is None:
        return "[dim]null[/dim]"
    if isinstance(cell, float):
        return f"{cell:.4f}" if abs(cell) < 1000 else f"{cell:,.2f}"
    if isinstance(cell, int):
        return f"{cell:,}"
    return str(cell)
