"""
A.1 Option A — offline backtest harness for asymmetric-confidence techniques.

β phase: original 3 candidates + 5 reformulations, train/holdout split, and
bootstrap CI on the Brier delta. Pre-registered exit criteria are committed
as module constants below — a candidate is PROMOTE only if it clears them on
BOTH the chronological train slice AND the held-out tail.

Walks ~/.pqi/history.jsonl, filters to non-legacy resolved rows with
per-agent verdicts, replays each candidate at per-agent level, and reports
Brier / net-correct / PASS / missed-op deltas vs baseline.

Original candidates (α phase):
  - shrinkage: credibility-weighted anchor on the higher-confidence side.
  - logodds:   confidence-weighted log-odds aggregate (max-conf weight).
  - dempster:  explicit ignorance mass; renormalized mass-fraction.

β reformulations:
  - SH2_selective: shrinkage only when |c_yes - c_no| >= 0.20, else identity.
  - LO2_mean:      log-odds with mean(c_yes, c_no) weight (vs max).
  - D1_ign:        dempster + force-PASS when ignorance > 0.45.
  - D2_gap:        dempster + force-PASS when |yes_mass - no_mass| < 0.15.
  - D3_both:       dempster + both D1 and D2 PASS overrides.

Output:
  - Rich tables to stdout (train + holdout, verdict column)
  - JSON artifact at ~/.pqi/backtests/a1_<utc_ts>.json

NON-production: this script does not touch pqi/ package code. Decision gate
is a simplified subset (price floor/ceiling + probability + per-side mean
confidence + ROI) — the full gate stack (contrarian, EV) is deliberately
skipped for this first-pass isolation of probability-level effects.

Run:
  python3 /home/shiroe/personal/pqi/scripts/a1_backtest.py [--verbose]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Make the `pqi` package importable when this script is run directly. Points at
# the src/ layout; with the editable install (`uv sync`) pqi is already on the
# path, so this insert is just a no-op fallback.
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from pqi import config  # noqa: E402
from pqi.history import load_history  # noqa: E402

CONSOLE = Console()

# ---- Gate thresholds used for the simplified decision simulation. ---------
PROB_MIN = 0.60
CONF_MIN = 0.67
ROI_MIN = 0.10
PRICE_FLOOR = 0.09
PRICE_CEILING = 0.90
LOGIT_EPS = 0.01

# ---- Pre-registered acceptance criteria (β phase). ------------------------
# A candidate is PROMOTE only if BOTH train and holdout clear all three:
#   (1) Brier delta vs baseline ≤ BRIER_DELTA_MAX
#   (2) Net-correct delta vs baseline ≥ NET_CORRECT_DELTA_MIN
#   (3) Bootstrap 95% CI on Brier delta excludes 0 (lies entirely below 0)
# Otherwise: REJECT (any criterion failed) or NOISE (delta < |0.001|).
BRIER_DELTA_MAX = -0.005
NET_CORRECT_DELTA_MIN = -1
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 42
CI_LOW_PCT = 2.5
CI_HIGH_PCT = 97.5
TRAIN_FRAC = 0.70

# ---- Dempster decision-override thresholds. -------------------------------
DEMPSTER_IGNORANCE_CEIL = 0.45    # force PASS if ignorance > this
DEMPSTER_MASS_GAP_MIN = 0.15      # force PASS if |yes_mass - no_mass| < this

# ---- Selective shrinkage threshold. ---------------------------------------
SHRINK_GAP_MIN = 0.20             # apply shrinkage only when |c_yes - c_no| >= this


# ---- Data filters ----------------------------------------------------------

def _is_legacy(row: dict) -> bool:
    if row.get("legacy"):
        return True
    return row.get("result", {}).get("signal_type") in ("CAUTIOUS", "CONFIRMED", "STRONG")


def _resolved_winner(row: dict) -> str | None:
    res = row.get("resolution")
    if not res:
        return None
    w = res.get("winning_side")
    return w if w in ("YES", "NO") else None


def _verdicts(row: dict) -> list[dict]:
    return row.get("result", {}).get("verdicts", []) or []


def _yes_price(row: dict) -> float:
    return float(row.get("result", {}).get("market", {}).get("yes_price", 0.5))


def _row_ts(row: dict) -> str:
    return str(row.get("timestamp", ""))


# ---- Candidate scoring techniques -----------------------------------------

def baseline_scores(verdicts: list[dict]) -> tuple[float, float, dict]:
    num_y = den_y = num_n = den_n = 0.0
    for v in verdicts:
        p = float(v["probability"])
        cy = float(v.get("confidence_yes", 0.5))
        cn = float(v.get("confidence_no", 0.5))
        num_y += p * cy
        den_y += cy
        num_n += (1 - p) * cn
        den_n += cn
    ys = num_y / den_y if den_y else 0.5
    ns = num_n / den_n if den_n else 0.5
    return ys, ns, {}


def shrinkage_scores(verdicts: list[dict]) -> tuple[float, float, dict]:
    num_y = den = 0.0
    for v in verdicts:
        p_yes = float(v["probability"])
        cy = float(v.get("confidence_yes", 0.5))
        cn = float(v.get("confidence_no", 0.5))
        if cy >= cn:
            p_adj = p_yes * cy + (1 - p_yes) * (1 - cy)
        else:
            p_adj_no = (1 - p_yes) * cn + p_yes * (1 - cn)
            p_adj = 1 - p_adj_no
        w = max(cy, cn)
        num_y += p_adj * w
        den += w
    ys = num_y / den if den else 0.5
    return ys, 1 - ys, {}


def shrinkage_selective_scores(verdicts: list[dict]) -> tuple[float, float, dict]:
    """SH2: apply shrinkage only when |c_yes - c_no| ≥ SHRINK_GAP_MIN."""
    num_y = den = 0.0
    triggered = 0
    for v in verdicts:
        p_yes = float(v["probability"])
        cy = float(v.get("confidence_yes", 0.5))
        cn = float(v.get("confidence_no", 0.5))
        if abs(cy - cn) >= SHRINK_GAP_MIN:
            triggered += 1
            if cy >= cn:
                p_adj = p_yes * cy + (1 - p_yes) * (1 - cy)
            else:
                p_adj_no = (1 - p_yes) * cn + p_yes * (1 - cn)
                p_adj = 1 - p_adj_no
        else:
            p_adj = p_yes
        w = max(cy, cn)
        num_y += p_adj * w
        den += w
    ys = num_y / den if den else 0.5
    return ys, 1 - ys, {"shrink_triggered": triggered}


def logodds_scores(verdicts: list[dict]) -> tuple[float, float, dict]:
    num = den = 0.0
    for v in verdicts:
        p = max(LOGIT_EPS, min(1 - LOGIT_EPS, float(v["probability"])))
        cy = float(v.get("confidence_yes", 0.5))
        cn = float(v.get("confidence_no", 0.5))
        w = max(cy, cn)
        num += w * math.log(p / (1 - p))
        den += w
    if den == 0:
        return 0.5, 0.5, {"logodds": 0.0}
    L = num / den
    ys = 1.0 / (1.0 + math.exp(-L))
    return ys, 1 - ys, {"logodds": L}


def logodds_mean_scores(verdicts: list[dict]) -> tuple[float, float, dict]:
    """LO2: log-odds aggregation with mean(c_yes, c_no) per-agent weight."""
    num = den = 0.0
    for v in verdicts:
        p = max(LOGIT_EPS, min(1 - LOGIT_EPS, float(v["probability"])))
        cy = float(v.get("confidence_yes", 0.5))
        cn = float(v.get("confidence_no", 0.5))
        w = (cy + cn) / 2
        num += w * math.log(p / (1 - p))
        den += w
    if den == 0:
        return 0.5, 0.5, {"logodds": 0.0}
    L = num / den
    ys = 1.0 / (1.0 + math.exp(-L))
    return ys, 1 - ys, {"logodds": L}


def dempster_scores(verdicts: list[dict]) -> tuple[float, float, dict]:
    yes_mass = no_mass = ign = 0.0
    n = len(verdicts)
    for v in verdicts:
        p_yes = float(v["probability"])
        cy = float(v.get("confidence_yes", 0.5))
        cn = float(v.get("confidence_no", 0.5))
        yes_mass += cy * p_yes
        no_mass += cn * (1 - p_yes)
        ign += 1 - max(cy, cn)
    if n == 0 or (yes_mass + no_mass) == 0:
        return 0.5, 0.5, {"ignorance": 0.0, "yes_mass": 0.0, "no_mass": 0.0}
    total = yes_mass + no_mass
    return (
        yes_mass / total,
        no_mass / total,
        {
            "ignorance": ign / n,
            "yes_mass": yes_mass / n,   # per-agent normalized so threshold is comparable
            "no_mass": no_mass / n,
        },
    )


# ---- Decision overrides (post-score, pre-gate). ----------------------------

def _override_dempster_ign(extras: dict) -> bool:
    return extras.get("ignorance", 0.0) > DEMPSTER_IGNORANCE_CEIL


def _override_dempster_gap(extras: dict) -> bool:
    return abs(extras.get("yes_mass", 0.0) - extras.get("no_mass", 0.0)) < DEMPSTER_MASS_GAP_MIN


def _override_dempster_both(extras: dict) -> bool:
    return _override_dempster_ign(extras) or _override_dempster_gap(extras)


# ---- Candidate registry. --------------------------------------------------

CANDIDATES: dict[str, dict[str, Any]] = {
    "baseline":       {"score": baseline_scores,            "force_pass": None},
    "shrinkage":      {"score": shrinkage_scores,           "force_pass": None},
    "logodds":        {"score": logodds_scores,             "force_pass": None},
    "dempster":       {"score": dempster_scores,            "force_pass": None},
    "SH2_selective":  {"score": shrinkage_selective_scores, "force_pass": None},
    "LO2_mean":       {"score": logodds_mean_scores,        "force_pass": None},
    "D1_ign":         {"score": dempster_scores,            "force_pass": _override_dempster_ign},
    "D2_gap":         {"score": dempster_scores,            "force_pass": _override_dempster_gap},
    "D3_both":        {"score": dempster_scores,            "force_pass": _override_dempster_both},
}


# ---- Simplified decision + outcome classification --------------------------

def simulated_recommendation(
    yes_s: float,
    no_s: float,
    verdicts: list[dict],
    yes_price: float,
    extras: dict,
    force_pass: Callable[[dict], bool] | None,
) -> str:
    if force_pass is not None and force_pass(extras):
        return "PASS"

    cy_mean = sum(float(v.get("confidence_yes", 0.5)) for v in verdicts) / len(verdicts)
    cn_mean = sum(float(v.get("confidence_no", 0.5)) for v in verdicts) / len(verdicts)

    if yes_s >= no_s:
        side, sp, prob, conf = "YES", yes_price, yes_s, cy_mean
    else:
        side, sp, prob, conf = "NO", 1 - yes_price, no_s, cn_mean

    if sp < PRICE_FLOOR or sp > PRICE_CEILING:
        return "PASS"
    if prob < PROB_MIN:
        return "PASS"
    if conf < CONF_MIN:
        return "PASS"
    roi = (1 - sp) / sp if sp > 0 else 0.0
    if roi < ROI_MIN:
        return "PASS"
    return f"BUY {side}"


def sim_outcome(
    sim_rec: str, winner: str, yes_s: float, no_s: float
) -> str:
    if sim_rec == "BUY YES":
        return "correct_buy" if winner == "YES" else "wrong_buy"
    if sim_rec == "BUY NO":
        return "correct_buy" if winner == "NO" else "wrong_buy"
    council_favored = "YES" if yes_s >= no_s else "NO"
    return "missed_opportunity" if council_favored == winner else "correct_pass"


# ---- Per-row evaluation (precomputed once, reused across splits + bootstrap)

def _evaluate_rows(eligible: list[dict]) -> list[dict]:
    """Return one dict per row: {row_meta, per_candidate: {brier, outcome}}."""
    out: list[dict] = []
    for row in eligible:
        verdicts = _verdicts(row)
        winner = _resolved_winner(row)
        yes_price = _yes_price(row)
        actual = 1.0 if winner == "YES" else 0.0

        per_cand: dict[str, dict] = {}
        for name, cfg in CANDIDATES.items():
            ys, ns, extras = cfg["score"](verdicts)
            sim_rec = simulated_recommendation(
                ys, ns, verdicts, yes_price, extras, cfg["force_pass"]
            )
            per_cand[name] = {
                "yes_score": ys,
                "no_score": ns,
                "brier": (ys - actual) ** 2,
                "sim_rec": sim_rec,
                "outcome": sim_outcome(sim_rec, winner, ys, ns),
                "extras": extras,
            }

        out.append({
            "run_id": row.get("run_id", ""),
            "timestamp": _row_ts(row),
            "question": row.get("result", {}).get("market", {}).get("question", "")[:60],
            "winner": winner,
            "yes_price": yes_price,
            "baseline_rec": row.get("result", {}).get("recommendation", ""),
            "techniques": per_cand,
        })
    return out


# ---- Aggregation -----------------------------------------------------------

def _summarize(eval_rows: list[dict]) -> dict[str, dict[str, Any]]:
    if not eval_rows:
        return {name: {"n": 0} for name in CANDIDATES}

    summary: dict[str, dict[str, Any]] = {}
    for name in CANDIDATES:
        brier_sum = 0.0
        outcomes = {"correct_buy": 0, "wrong_buy": 0, "missed_opportunity": 0, "correct_pass": 0}
        pass_total = 0
        ign_sum = 0.0
        logodds_abs_sum = 0.0
        shrink_trig_sum = 0
        for r in eval_rows:
            t = r["techniques"][name]
            brier_sum += t["brier"]
            outcomes[t["outcome"]] = outcomes.get(t["outcome"], 0) + 1
            if t["sim_rec"] == "PASS":
                pass_total += 1
            ex = t.get("extras", {})
            if "ignorance" in ex:
                ign_sum += ex["ignorance"]
            if "logodds" in ex:
                logodds_abs_sum += abs(ex["logodds"])
            if "shrink_triggered" in ex:
                shrink_trig_sum += ex["shrink_triggered"]
        n = len(eval_rows)
        correct = outcomes["correct_buy"]
        wrong = outcomes["wrong_buy"]
        summary[name] = {
            "n": n,
            "brier_mean": brier_sum / n,
            "correct_buy": correct,
            "wrong_buy": wrong,
            "net_correct": correct - wrong,
            "missed_opportunity": outcomes["missed_opportunity"],
            "correct_pass": outcomes["correct_pass"],
            "pass_total": pass_total,
            "buy_wr": correct / max(correct + wrong, 1),
            "ignorance_mean": (ign_sum / n) if name in ("dempster", "D1_ign", "D2_gap", "D3_both") else None,
            "logodds_abs_mean": (logodds_abs_sum / n) if name in ("logodds", "LO2_mean") else None,
            "shrink_triggered_mean": (shrink_trig_sum / n) if name == "SH2_selective" else None,
        }
    base = summary["baseline"]
    for name, s in summary.items():
        if name == "baseline":
            s["delta_vs_baseline"] = None
            continue
        s["delta_vs_baseline"] = {
            "brier": s["brier_mean"] - base["brier_mean"],
            "correct_buy": s["correct_buy"] - base["correct_buy"],
            "wrong_buy": s["wrong_buy"] - base["wrong_buy"],
            "net_correct": s["net_correct"] - base["net_correct"],
            "missed_op": s["missed_opportunity"] - base["missed_opportunity"],
            "pass_total": s["pass_total"] - base["pass_total"],
            "buy_wr": s["buy_wr"] - base["buy_wr"],
        }
    return summary


# ---- Bootstrap CI on Brier delta vs baseline ------------------------------

def _bootstrap_brier_delta_ci(
    eval_rows: list[dict], name: str, n_resamples: int = BOOTSTRAP_N
) -> tuple[float, float]:
    if name == "baseline" or not eval_rows:
        return (0.0, 0.0)
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(eval_rows)
    pairs = [
        (r["techniques"][name]["brier"], r["techniques"]["baseline"]["brier"])
        for r in eval_rows
    ]
    deltas: list[float] = []
    for _ in range(n_resamples):
        s_cand = 0.0
        s_base = 0.0
        for _i in range(n):
            c, b = pairs[rng.randrange(n)]
            s_cand += c
            s_base += b
        deltas.append((s_cand - s_base) / n)
    deltas.sort()
    lo = deltas[int(n_resamples * CI_LOW_PCT / 100)]
    hi = deltas[int(n_resamples * CI_HIGH_PCT / 100)]
    return (lo, hi)


# ---- Verdict per pre-registered criteria ----------------------------------

def _verdict(
    train_summary: dict[str, Any],
    holdout_summary: dict[str, Any],
    train_ci: tuple[float, float],
    holdout_ci: tuple[float, float],
) -> str:
    td = train_summary.get("delta_vs_baseline") or {}
    hd = holdout_summary.get("delta_vs_baseline") or {}
    if not td or not hd:
        return "—"

    train_brier_ok = td["brier"] <= BRIER_DELTA_MAX
    holdout_brier_ok = hd["brier"] <= BRIER_DELTA_MAX
    train_net_ok = td["net_correct"] >= NET_CORRECT_DELTA_MIN
    holdout_net_ok = hd["net_correct"] >= NET_CORRECT_DELTA_MIN
    train_ci_ok = train_ci[1] < 0
    holdout_ci_ok = holdout_ci[1] < 0

    if all([train_brier_ok, holdout_brier_ok, train_net_ok, holdout_net_ok,
            train_ci_ok, holdout_ci_ok]):
        return "PROMOTE"

    if abs(td["brier"]) < 0.001 and abs(hd["brier"]) < 0.001:
        return "NOISE"
    return "REJECT"


# ---- Output ---------------------------------------------------------------

def _render_table(
    title: str,
    summary: dict[str, dict[str, Any]],
    cis: dict[str, tuple[float, float]],
    verdicts: dict[str, str] | None = None,
) -> Table:
    table = Table(title=title)
    cols = ["Technique", "n", "Brier↓", "ΔBrier", "CI95%", "Correct", "Wrong",
            "Net", "ΔNet", "PASS", "Missed", "BUY WR", "Extra"]
    if verdicts is not None:
        cols.append("Verdict")
    for c in cols:
        table.add_column(c, justify="right" if c != "Technique" else "left")

    for name in CANDIDATES:
        s = summary.get(name)
        if not s or s["n"] == 0:
            continue
        d = s["delta_vs_baseline"] or {}
        ci = cis.get(name, (0.0, 0.0))
        ci_str = "—" if name == "baseline" else f"[{ci[0]:+.4f}, {ci[1]:+.4f}]"
        extra = ""
        if name in ("logodds", "LO2_mean") and s.get("logodds_abs_mean") is not None:
            extra = f"|L|={s['logodds_abs_mean']:.2f}"
        elif name in ("dempster", "D1_ign", "D2_gap", "D3_both") and s.get("ignorance_mean") is not None:
            extra = f"ign={s['ignorance_mean']:.3f}"
        elif name == "SH2_selective" and s.get("shrink_triggered_mean") is not None:
            extra = f"trig={s['shrink_triggered_mean']:.2f}/agent"

        row = [
            name,
            str(s["n"]),
            f"{s['brier_mean']:.4f}",
            f"{d.get('brier', 0):+.4f}" if d else "—",
            ci_str,
            str(s["correct_buy"]),
            str(s["wrong_buy"]),
            str(s["net_correct"]),
            f"{d.get('net_correct', 0):+d}" if d else "—",
            str(s["pass_total"]),
            str(s["missed_opportunity"]),
            f"{s['buy_wr']:.1%}",
            extra,
        ]
        if verdicts is not None:
            v = verdicts.get(name, "—")
            color = {"PROMOTE": "green", "REJECT": "red", "NOISE": "yellow"}.get(v, "")
            row.append(f"[{color}]{v}[/{color}]" if color else v)
        table.add_row(*row)
    return table


def _round_summary(summary: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Round floats for JSON readability."""
    out: dict[str, dict[str, Any]] = {}
    for name, s in summary.items():
        rs = {}
        for k, v in s.items():
            if isinstance(v, float):
                rs[k] = round(v, 4)
            elif isinstance(v, dict) and v is not None:
                rs[k] = {kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items()}
            else:
                rs[k] = v
        out[name] = rs
    return out


# ---- Main harness ---------------------------------------------------------

def run(verbose: bool = False) -> dict[str, Any]:
    rows = load_history()
    eligible: list[dict] = [
        r for r in rows
        if not _is_legacy(r) and _verdicts(r) and _resolved_winner(r) is not None
    ]
    eligible.sort(key=lambda r: _row_ts(r))

    n = len(eligible)
    split_idx = int(n * TRAIN_FRAC)
    train_rows = eligible[:split_idx]
    holdout_rows = eligible[split_idx:]

    CONSOLE.print(
        f"[bold]A.1 backtest β[/bold] — {n} eligible rows "
        f"(non-legacy, resolved, with per-agent verdicts)\n"
        f"  train: {len(train_rows)} rows "
        f"({train_rows[0]['timestamp'][:10] if train_rows else '—'} → "
        f"{train_rows[-1]['timestamp'][:10] if train_rows else '—'})\n"
        f"  holdout: {len(holdout_rows)} rows "
        f"({holdout_rows[0]['timestamp'][:10] if holdout_rows else '—'} → "
        f"{holdout_rows[-1]['timestamp'][:10] if holdout_rows else '—'})\n"
        f"  pre-registered criteria: ΔBrier ≤ {BRIER_DELTA_MAX}, "
        f"ΔNet ≥ {NET_CORRECT_DELTA_MIN}, CI95%(ΔBrier) entirely < 0 — "
        f"on BOTH train and holdout."
    )

    train_eval = _evaluate_rows(train_rows)
    holdout_eval = _evaluate_rows(holdout_rows)
    full_eval = train_eval + holdout_eval

    train_summary = _summarize(train_eval)
    holdout_summary = _summarize(holdout_eval)
    full_summary = _summarize(full_eval)

    train_cis = {name: _bootstrap_brier_delta_ci(train_eval, name) for name in CANDIDATES}
    holdout_cis = {name: _bootstrap_brier_delta_ci(holdout_eval, name) for name in CANDIDATES}
    full_cis = {name: _bootstrap_brier_delta_ci(full_eval, name) for name in CANDIDATES}

    verdicts = {
        name: _verdict(train_summary[name], holdout_summary[name],
                       train_cis[name], holdout_cis[name])
        for name in CANDIDATES
    }

    CONSOLE.print(_render_table("Train slice (chronological first 70%)", train_summary, train_cis))
    CONSOLE.print(_render_table("Holdout slice (chronological last 30%)", holdout_summary, holdout_cis))
    CONSOLE.print(_render_table("Full sample (reference only — not used for verdict)", full_summary, full_cis, verdicts))

    CONSOLE.print(
        "\n[dim]Notes: Brier lower = better calibrated; ΔBrier negative = improvement. "
        "CI95% from 1000 bootstrap resamples (seed=42). "
        "PROMOTE requires all three criteria to hold on BOTH train and holdout. "
        "missed_op = PASS where the council's favored side actually won.[/dim]"
    )

    if verbose:
        CONSOLE.print("\n[bold]Holdout per-row detail (first 20):[/bold]")
        for e in holdout_eval[:20]:
            CONSOLE.print(
                f"  [{e['winner']}] ${e['yes_price']:.2f} | "
                f"baseline={e['baseline_rec']} | "
                + " | ".join(
                    f"{n}={e['techniques'][n]['sim_rec']}({e['techniques'][n]['outcome']})"
                    for n in ["SH2_selective", "LO2_mean", "D3_both"]
                )
                + f" | {e['question']}"
            )

    out_dir = config.PQI_DATA_DIR / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = {
        "generated_at": ts,
        "phase": "beta",
        "eligible_rows": n,
        "split": {
            "train_n": len(train_rows),
            "holdout_n": len(holdout_rows),
            "train_frac": TRAIN_FRAC,
        },
        "criteria": {
            "brier_delta_max": BRIER_DELTA_MAX,
            "net_correct_delta_min": NET_CORRECT_DELTA_MIN,
            "bootstrap_n": BOOTSTRAP_N,
            "ci_pct": [CI_LOW_PCT, CI_HIGH_PCT],
        },
        "gate_thresholds": {
            "prob_min": PROB_MIN, "conf_min": CONF_MIN, "roi_min": ROI_MIN,
            "price_floor": PRICE_FLOOR, "price_ceiling": PRICE_CEILING,
            "logit_eps": LOGIT_EPS,
            "dempster_ignorance_ceil": DEMPSTER_IGNORANCE_CEIL,
            "dempster_mass_gap_min": DEMPSTER_MASS_GAP_MIN,
            "shrink_gap_min": SHRINK_GAP_MIN,
        },
        "notes": [
            "Decision gate simplified: price/prob/conf/ROI only. Contrarian and EV gates skipped.",
            "missed_op: PASS where the council's favored side (yes_score vs no_score) won.",
            "Per-agent application: each candidate transforms each agent's verdict before council aggregation.",
            "Train/holdout split is chronological by timestamp.",
        ],
        "verdicts": verdicts,
        "train_summary": _round_summary(train_summary),
        "holdout_summary": _round_summary(holdout_summary),
        "full_summary": _round_summary(full_summary),
        "train_ci95_brier_delta": {k: [round(v[0], 4), round(v[1], 4)] for k, v in train_cis.items()},
        "holdout_ci95_brier_delta": {k: [round(v[0], 4), round(v[1], 4)] for k, v in holdout_cis.items()},
        "full_ci95_brier_delta": {k: [round(v[0], 4), round(v[1], 4)] for k, v in full_cis.items()},
    }
    out_path = out_dir / f"a1_{ts}.json"
    out_path.write_text(json.dumps(artifact, indent=2, default=str))
    CONSOLE.print(f"\n[green]✓[/green] artifact written: {out_path}")

    promoted = [k for k, v in verdicts.items() if v == "PROMOTE"]
    if promoted:
        CONSOLE.print(f"\n[bold green]PROMOTE candidates: {', '.join(promoted)}[/bold green]")
    else:
        CONSOLE.print("\n[bold yellow]No candidate cleared all pre-registered criteria.[/bold yellow]")

    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="print per-row detail")
    args = parser.parse_args()
    run(verbose=args.verbose)
