"""
Reliability diagram — Phase 0 Task 0.3.

Bucket every per-agent and council-aggregate predicted P(YES) into the
EV-gate-aligned price bands and compare to the realized YES rate per
bucket. Operates over every non-legacy resolved row in
~/.pqi/history.jsonl (the full ~400-row set), not just the 65
BUY-triggered rows — agent calibration does not require a BUY.

Outcome semantics: directional winner. A row counts as a YES if
`resolution.final_yes_price > result.market.yes_price`. Equivalent to
`winning_side == "YES"` whenever the resolution price is 1.0 / 0.0,
and stays well-defined for the rare 50-50 tie (counted as NO).

Bands: the EV-gate price bands [0.09–0.50, 0.50–0.60, 0.60–0.70,
0.70–0.80, 0.80–0.95] are the primary view (re-used by Task 0.4 for
banded WR comparison). [0.00–0.09) and [0.95–1.00] bookends are
emitted so no prediction is silently dropped — they should be empty
or near-empty in practice.

Phase 1 rollout split: every verdict carries
`AgentVerdict.prompt_flags` (sparse dict, session 40). The row label is
the UNION of flags across that row's 7 verdicts, mapped via the
`phase1_flag_label` convention (inlined here to keep stdlib-only):
empty ⇒ `v1`, one flag ⇒ `v2:<flag>`, many ⇒ `v2:<sorted csv>`.

Outputs:

  - ASCII reliability table per agent + council aggregate to stdout.
  - Brier score per agent + aggregate.
  - Cross-agent diversity diagnostics (mean pairwise Pearson, mean
    per-row stddev) — high correlation / low stddev confirms the
    Phase-1 mirror-collapse hypothesis.
  - Markdown report at ~/.pqi/reports/reliability_YYYYMMDD.md
    (suffixed with `_<flag-slug>` when `--flag` is supplied).

Filter with `--flag v1`, `--flag v2:preamble`, or `--flag 'v2:*'` to
isolate one rollout stage. Without `--flag`, the full resolved set is
aggregated exactly as before.

Stdlib only (json + math); no scipy, no rich. Mirrors the style of
scripts/backfill_clv.py.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

_PQI_DATA_DIR = Path(
    os.getenv("PQI_DATA_DIR", str(Path(__file__).resolve().parent.parent / ".pqi"))
).expanduser()
HISTORY_FILE = _PQI_DATA_DIR / "history.jsonl"
REPORTS_DIR = _PQI_DATA_DIR / "reports"

# EV-gate price bands (session 31). [0.09–0.50, …, 0.80–0.95] is the
# block Task 0.4 will join against. The two bookends keep this script
# lossless — every prediction lands in some bucket.
BANDS: list[tuple[float, float]] = [
    (0.00, 0.09),
    (0.09, 0.50),
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.95),
    (0.95, 1.00),
]

# AgentVerdict.agent_name → persona key. Mirrors AGENT_PERSONAS keys.
AGENT_KEYS: dict[str, str] = {
    "The Rules Lawyer": "rules_lawyer",
    "The Bull": "bull",
    "The Bear": "bear",
    "The Base-Rate Statistician": "statistician",
    "The Sentiment Analyst": "sentiment",
    "The Domain Specialist": "specialist",
    "The Contrarian": "contrarian",
}
COUNCIL_KEY = "council"
ALL_KEYS: list[str] = [
    "rules_lawyer",
    "bull",
    "bear",
    "statistician",
    "sentiment",
    "specialist",
    "contrarian",
    COUNCIL_KEY,
]
THIN_BIN_THRESHOLD = 10


def _band_for(p: float) -> tuple[float, float] | None:
    for lo, hi in BANDS:
        if lo <= p < hi:
            return (lo, hi)
    if p == 1.0:
        return BANDS[-1]
    return None


def _band_label(band: tuple[float, float]) -> str:
    lo, hi = band
    return f"[{lo:.2f}, {hi:.2f})"


def _wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _phase1_label(prompt_flags: dict[str, bool] | None) -> str:
    """Inline copy of `models.phase1_flag_label` (stdlib-only constraint).

    Empty / None ⇒ `"v1"`. Otherwise `"v2:<sorted csv of active flags>"`.
    """
    if not prompt_flags:
        return "v1"
    active = sorted(k for k, v in prompt_flags.items() if v)
    if not active:
        return "v1"
    return "v2:" + ",".join(active)


def _row_label(verdicts: list[dict]) -> str:
    """Row-level flag label: UNION of every verdict's `prompt_flags` in the row,
    mapped through `_phase1_label`. Missing `prompt_flags` on legacy rows
    collapses to `"v1"` cleanly."""
    union: dict[str, bool] = {}
    for v in verdicts:
        pf = v.get("prompt_flags") or {}
        for k, val in pf.items():
            if val:
                union[k] = True
    return _phase1_label(union)


def _matches_flag(row_label: str, pattern: str | None) -> bool:
    """Flag filter matcher. None ⇒ pass everything. A trailing `:*` makes the
    prefix a glob (e.g. `v2:*` matches `v2:preamble`, `v2:preamble,bear_advocacy`)."""
    if pattern is None:
        return True
    if pattern.endswith(":*"):
        return row_label.startswith(pattern[:-1])
    return row_label == pattern


def _iter_resolved_predictions(history_path: Path):
    """Yield (entry_yes_price, outcome, council_p, [(key, p), ...], flag_label)
    per resolved row.

    `outcome` is 1 if final_yes_price > entry_yes_price else 0
    (directional winner; ties counted as NO). Council aggregate uses
    `result.final_score`. Legacy rows (no verdicts) are skipped.
    """
    with open(history_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("legacy"):
                continue
            res = e.get("resolution")
            if res is None:
                continue
            r = e.get("result") or {}
            market = r.get("market") or {}
            entry_p = market.get("yes_price")
            final_p = res.get("final_yes_price")
            if entry_p is None or final_p is None:
                continue
            outcome = 1 if final_p > entry_p else 0
            council_p = r.get("final_score")
            verdicts = r.get("verdicts") or []
            agent_preds: list[tuple[str, float]] = []
            for v in verdicts:
                name = v.get("agent_name")
                key = AGENT_KEYS.get(name)
                p = v.get("probability")
                if key is None or p is None:
                    continue
                agent_preds.append((key, float(p)))
            yield (float(entry_p), outcome, council_p, agent_preds, _row_label(verdicts))


def _accumulate(rows):
    """Fold a collection of resolved-prediction rows into per-agent Brier
    inputs + per-bin counters.

    `rows` is an iterable of tuples in the shape produced by
    `_iter_resolved_predictions`:
        (entry_p, outcome, council_p, agent_preds, flag_label)

    Returns:
        bins:    {key: {band: {"n": int, "yes": int, "p_sum": float}}}
        briers:  {key: list[(p, y)]}  # all predictions for Brier
        per_row: list[dict]           # for cross-agent diversity diagnostics
        n_rows:  int
    """
    bins: dict[str, dict[tuple[float, float], dict[str, float | int]]] = {
        k: {b: {"n": 0, "yes": 0, "p_sum": 0.0} for b in BANDS} for k in ALL_KEYS
    }
    briers: dict[str, list[tuple[float, int]]] = {k: [] for k in ALL_KEYS}
    per_row_predictions: list[dict[str, float]] = []
    n_rows = 0

    for entry_p, outcome, council_p, agent_preds, _label in rows:
        n_rows += 1
        row_preds: dict[str, float] = {}

        if council_p is not None:
            band = _band_for(float(council_p))
            if band is not None:
                cell = bins[COUNCIL_KEY][band]
                cell["n"] += 1
                cell["yes"] += outcome
                cell["p_sum"] += float(council_p)
                briers[COUNCIL_KEY].append((float(council_p), outcome))
                row_preds[COUNCIL_KEY] = float(council_p)

        for key, p in agent_preds:
            band = _band_for(p)
            if band is None:
                continue
            cell = bins[key][band]
            cell["n"] += 1
            cell["yes"] += outcome
            cell["p_sum"] += p
            briers[key].append((p, outcome))
            row_preds[key] = p

        if row_preds:
            per_row_predictions.append(row_preds)

    return bins, briers, per_row_predictions, n_rows


def _brier(samples: list[tuple[float, int]]) -> float:
    if not samples:
        return float("nan")
    return sum((p - y) ** 2 for p, y in samples) / len(samples)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denom = math.sqrt(sxx * syy)
    if denom == 0:
        return None
    return sxy / denom


def _diversity_diagnostics(
    per_row: list[dict[str, float]],
) -> dict[str, float | dict]:
    agents = [k for k in ALL_KEYS if k != COUNCIL_KEY]
    series: dict[str, list[float]] = {a: [] for a in agents}
    pairwise_pool: dict[tuple[str, str], tuple[list[float], list[float]]] = {}
    cross_row_stds: list[float] = []

    for row in per_row:
        present = [a for a in agents if a in row]
        if len(present) < 2:
            continue
        values = [row[a] for a in present]
        cross_row_stds.append(statistics.pstdev(values))
        for a in present:
            series[a].append(row[a])
        for i, a in enumerate(present):
            for b in present[i + 1 :]:
                key = (a, b)
                lst = pairwise_pool.setdefault(key, ([], []))
                lst[0].append(row[a])
                lst[1].append(row[b])

    pairwise_corrs: list[float] = []
    pair_table: dict[str, float] = {}
    for (a, b), (xs, ys) in sorted(pairwise_pool.items()):
        r = _pearson(xs, ys)
        if r is not None:
            pairwise_corrs.append(r)
            pair_table[f"{a}↔{b}"] = r

    return {
        "mean_pairwise_pearson": (
            sum(pairwise_corrs) / len(pairwise_corrs) if pairwise_corrs else float("nan")
        ),
        "mean_per_row_stddev": (
            sum(cross_row_stds) / len(cross_row_stds) if cross_row_stds else float("nan")
        ),
        "n_rows_with_2plus_agents": len(cross_row_stds),
        "pair_table": pair_table,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_agent_table(
    key: str, bands: dict[tuple[float, float], dict[str, float | int]]
) -> list[str]:
    """ASCII reliability table for a single agent. Returns lines."""
    header = f"  {'band':<14}  {'n':>5}  {'pred_mean':>9}  {'realized':>9}  {'gap':>7}  {'95% CI':>16}  flag"
    sep = "  " + "-" * (len(header) - 2)
    lines = [f"### {key}", "", "```", header, sep]
    for band in BANDS:
        cell = bands[band]
        n = int(cell["n"])
        if n == 0:
            lines.append(
                f"  {_band_label(band):<14}  {n:>5}  {'-':>9}  {'-':>9}  {'-':>7}  {'-':>16}  empty"
            )
            continue
        yes = int(cell["yes"])
        p_mean = float(cell["p_sum"]) / n
        realized = yes / n
        gap = realized - p_mean
        lo, hi = _wilson_ci(yes, n)
        flag = "thin" if n < THIN_BIN_THRESHOLD else ""
        lines.append(
            f"  {_band_label(band):<14}  {n:>5}  {p_mean:>9.3f}  {realized:>9.3f}  {gap:>+7.3f}  [{lo:>5.3f}, {hi:>5.3f}]  {flag}"
        )
    lines += ["```", ""]
    return lines


def _render_brier_summary(briers: dict[str, list[tuple[float, int]]]) -> list[str]:
    lines = ["## Brier scores", ""]
    lines += ["```", f"  {'agent':<14}  {'n':>5}  {'Brier':>7}", "  " + "-" * 32]
    for k in ALL_KEYS:
        samples = briers[k]
        b = _brier(samples)
        b_str = "n/a" if math.isnan(b) else f"{b:.4f}"
        lines.append(f"  {k:<14}  {len(samples):>5}  {b_str:>7}")
    lines += ["```", ""]
    return lines


def _render_diversity(diag: dict[str, float | dict]) -> list[str]:
    pearson = diag["mean_pairwise_pearson"]
    stddev = diag["mean_per_row_stddev"]
    n_rows = diag["n_rows_with_2plus_agents"]
    pair_table: dict[str, float] = diag["pair_table"]  # type: ignore[assignment]

    high_pairs = sum(1 for r in pair_table.values() if r >= 0.85)
    total_pairs = len(pair_table)
    pct_high = (high_pairs / total_pairs * 100.0) if total_pairs else 0.0

    lines = [
        "## Cross-agent diversity",
        "",
        f"- Rows used (≥ 2 agents present): **{n_rows}**",
        f"- Mean pairwise Pearson correlation: **{pearson:.3f}**",
        f"- Mean per-row stddev (across agents): **{stddev:.3f}**",
        f"- Pairs with r ≥ 0.85: **{high_pairs}/{total_pairs}** ({pct_high:.0f}%)",
        "",
    ]

    if total_pairs and pct_high >= 50.0:
        lines.append(
            "> **Mirror-collapse signal (cluster-level):** ≥ half of agent "
            "pairs correlate at r ≥ 0.85. Even when the global mean pairwise "
            "is dragged down by a single anti-correlated outlier, a high "
            "fraction of pairs at r ≥ 0.85 is the diagnostic. Confirms the "
            "Option-B shadow finding (sessions 26 + 32). Phase 1 "
            "(SHARED_PREAMBLE autonomy fix) is the unblock."
        )
    elif not math.isnan(pearson) and pearson >= 0.85:
        lines.append(
            "> **Mirror-collapse signal:** mean pairwise correlation ≥ 0.85. "
            "Confirms the Option-B-shadow finding (sessions 26 + 32) — the "
            "council is converging toward one signal in seven hats."
        )
    elif total_pairs and pct_high >= 25.0:
        lines.append(
            "> Partial-collapse signal: a non-trivial fraction of pairs sit "
            "at r ≥ 0.85, but the council is not uniformly mirrored. Inspect "
            "the per-pair table below to see which agents form the cluster "
            "and which retain independence — the latter are where Phase 1 "
            "should preserve, not destabilize, the existing signal."
        )
    else:
        lines.append(
            "> No mirror-collapse signal at the cluster level. Agents retain "
            "measurable independence. Recheck per-agent drift in "
            "`pqi history stats` before drawing a stronger conclusion."
        )
    lines.append("")

    if pair_table:
        lines.append("```")
        lines.append(f"  {'pair':<32}  {'r':>6}")
        lines.append("  " + "-" * 42)
        for pair, r in sorted(pair_table.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {pair:<32}  {r:>6.3f}")
        lines.append("```")
        lines.append("")
    return lines


def render_report(
    bins: dict,
    briers: dict,
    per_row: list[dict[str, float]],
    n_rows: int,
    *,
    flag_filter: str | None,
    label_counts: dict[str, int],
) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filter_tag = f" ({flag_filter})" if flag_filter else ""
    head = [
        f"# Reliability diagram — {today}{filter_tag}",
        "",
        f"**Resolved rows analyzed:** {n_rows} (non-legacy, with both entry "
        "and final yes_price).",
        "**Outcome semantics:** directional winner — "
        "`final_yes_price > entry_yes_price ⇒ YES`.",
        "**Bands:** EV-gate price bands "
        "`[0.09–0.50, 0.50–0.60, 0.60–0.70, 0.70–0.80, 0.80–0.95]` "
        "with `[0.00–0.09)` and `[0.95–1.00]` bookends.",
        f"**Thin-bin threshold:** flagged when n < {THIN_BIN_THRESHOLD}.",
    ]
    if flag_filter is not None:
        head.append(f"**Flag filter:** `{flag_filter}` (row-union label).")
    if label_counts:
        counts_line = ", ".join(
            f"`{lbl}`={n}" for lbl, n in sorted(label_counts.items(), key=lambda kv: -kv[1])
        )
        head.append(f"**Resolved rows by flag label:** {counts_line}.")
    head += [
        "",
        "## Per-agent reliability",
        "",
        "Read each row as: when this agent predicted P(YES) in this band, the "
        "average prediction was `pred_mean`, the realized YES rate was "
        "`realized`, and `gap = realized − pred_mean` (negative ⇒ "
        "overconfident on YES).",
        "",
    ]
    body: list[str] = []
    for k in ALL_KEYS:
        body += _render_agent_table(k, bins[k])
    diag = _diversity_diagnostics(per_row)
    return "\n".join(head + body + _render_brier_summary(briers) + _render_diversity(diag))


def _flag_slug(flag_filter: str) -> str:
    """Filesystem-safe slug for a flag filter. `v2:preamble` → `v2-preamble`,
    `v2:*` → `v2-any`. Keeps the date-only filename when no filter is set."""
    if flag_filter.endswith(":*"):
        return flag_filter[:-2] + "-any"
    return flag_filter.replace(":", "-").replace(",", "+")


def write_report(text: str, flag_filter: str | None = None) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = f"_{_flag_slug(flag_filter)}" if flag_filter else ""
    out = REPORTS_DIR / f"reliability_{today}{suffix}.md"
    out.write_text(text)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Per-agent + council reliability diagram from history.jsonl"
    )
    parser.add_argument(
        "--history",
        type=Path,
        default=HISTORY_FILE,
        help=f"history.jsonl path (default: {HISTORY_FILE})",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Skip writing the markdown report; just print to stdout.",
    )
    parser.add_argument(
        "--flag",
        type=str,
        default=None,
        help=(
            "Filter resolved rows by Phase 1 rollout label. Accepts exact "
            "matches (e.g. `v1`, `v2:preamble`) or a trailing-star prefix "
            "glob (e.g. `v2:*`). Without this flag, all resolved rows are "
            "aggregated as before."
        ),
    )
    args = parser.parse_args()

    if not args.history.exists():
        print(f"No history file at {args.history}", file=sys.stderr)
        return 1

    all_rows = list(_iter_resolved_predictions(args.history))
    label_counts: dict[str, int] = {}
    for row in all_rows:
        lbl = row[4]
        label_counts[lbl] = label_counts.get(lbl, 0) + 1

    if label_counts:
        summary = ", ".join(
            f"{lbl}={n}"
            for lbl, n in sorted(label_counts.items(), key=lambda kv: -kv[1])
        )
        print(f"resolved rows by flag label: {summary}", file=sys.stderr)

    filtered = [row for row in all_rows if _matches_flag(row[4], args.flag)]
    bins, briers, per_row, n_rows = _accumulate(filtered)
    if n_rows == 0:
        msg = "No resolved rows matched"
        if args.flag:
            msg += f" --flag {args.flag!r}."
        else:
            msg += " (no non-legacy rows with both entry and final prices)."
        print(msg)
        return 0

    text = render_report(
        bins, briers, per_row, n_rows,
        flag_filter=args.flag, label_counts=label_counts,
    )
    print(text)
    if not args.no_write:
        path = write_report(text, flag_filter=args.flag)
        print(f"\nwrote {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
