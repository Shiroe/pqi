# pqi/analytics.py
"""
Post-hoc analytics over the history file.

Pure functions over the migrated entry dicts produced by `pqi.history.load_history()`.
None of these functions touch I/O directly except `compute_and_persist_postmortem()`,
which writes the result back via history.update_analysis().

Outcome classification (PnL-based):
  - correct_buy        — BUY whose side gained in price from entry to resolution
  - wrong_buy          — BUY whose side lost in price from entry to resolution
  - missed_opportunity — PASS, clean winner declared, council favored it with prob > 0.65
  - correct_pass       — PASS (unsure/wrong) OR exact break-even BUY (rare push)
  - unresolved         — no resolution recorded yet
  - voided             — closed but no numeric redemption price (true refund/cancellation)

Intermediate resolutions (e.g. outcomePrices ["0.5","0.5"]) are NOT voids.
They settle at the final_yes_price, so each BUY has real PnL:
  BUY YES pnl = final_yes_price - entry_yes_price
  BUY NO  pnl = entry_yes_price - final_yes_price
Voided only covers cases where final_yes_price is missing/unparseable — i.e.
the market cancelled and there is no redemption value at all.

Per-agent stats:
  - hit_rate          — fraction of resolved entries where the agent's prob (>0.5)
                        matched the winning side
  - weighted_hit_rate — same, weighted by the agent's confidence on the side it picked
  - mean_brier        — mean (probability − actual)^2 across resolved entries
  - high_conf_hits / high_conf_misses — counts where the chosen side conf was >= 0.70
"""

from __future__ import annotations

from datetime import datetime
from statistics import mean, median, pstdev
from typing import Literal

from pqi.history import update_analysis

# Tunable: a missed opportunity requires the council to have been *clearly* on
# the winning side. Coin-flips that happened to land are not "missed".
MISSED_OPPORTUNITY_PROB_FLOOR = 0.65

OutcomeKind = Literal[
    "correct_buy",
    "wrong_buy",
    "missed_opportunity",
    "correct_pass",
    "unresolved",
    "voided",
]


# ---------------------------------------------------------------------------
# Entry shape helpers
# ---------------------------------------------------------------------------

def _result(entry: dict) -> dict:
    return entry.get("result") or {}


def _market(entry: dict) -> dict:
    return _result(entry).get("market") or {}


def _verdicts(entry: dict) -> list[dict]:
    return _result(entry).get("verdicts") or []


def _winner(entry: dict) -> Literal["YES", "NO"] | None:
    """The side Polymarket declared as the clean winner (final price at 0.99+/0.01-).
    Returns None for intermediate resolutions (draws, partial settlements)
    and for unresolved / truly voided entries."""
    res = entry.get("resolution") or {}
    w = res.get("winning_side")
    return w if w in ("YES", "NO") else None


def _final_yes_price(entry: dict) -> float | None:
    """The settled YES redemption price. None when truly voided (no price) or unresolved."""
    res = entry.get("resolution") or {}
    fy = res.get("final_yes_price")
    if fy is None:
        return None
    try:
        return float(fy)
    except (TypeError, ValueError):
        return None


def _directional_winner(entry: dict) -> Literal["YES", "NO"] | None:
    """The side whose price *gained* from entry to resolution.

    Matches `_winner` at clean extremes. For intermediate resolutions
    (e.g. final 0.50 from entry 0.40) returns the side that made money
    — this is what drives per-agent stats and PnL-based outcome buckets.
    Returns None if final price is missing or exactly equal to entry
    (exact push — excluded from stats so it doesn't distort averages)."""
    final_yes = _final_yes_price(entry)
    if final_yes is None:
        return None
    entry_yes = float(_market(entry).get("yes_price", 0.5))
    if final_yes > entry_yes:
        return "YES"
    if final_yes < entry_yes:
        return "NO"
    return None


def _council_favored_side(entry: dict) -> Literal["YES", "NO"]:
    r = _result(entry)
    return "YES" if (r.get("final_score", 0.5)) > 0.5 else "NO"


def _council_favored_prob(entry: dict) -> float:
    r = _result(entry)
    fs = r.get("final_score", 0.5)
    return fs if fs > 0.5 else r.get("no_score", 0.5)


# ---------------------------------------------------------------------------
# Outcome classification
# ---------------------------------------------------------------------------

def classify_outcome(entry: dict) -> OutcomeKind:
    res = entry.get("resolution")
    if res is None:
        return "unresolved"

    # True void — market closed but no redemption value (refund / cancellation).
    # Intermediate settlements (e.g. 0.50/0.50) DO have a redemption price and
    # are handled below as real PnL.
    final_yes = _final_yes_price(entry)
    if final_yes is None:
        return "voided"

    rec = _result(entry).get("recommendation", "PASS")
    entry_yes = float(_market(entry).get("yes_price", 0.5))

    if rec.startswith("BUY"):
        # PnL per share = final redemption price of the bought side minus entry price.
        if "YES" in rec:
            pnl = final_yes - entry_yes
        else:  # BUY NO
            pnl = entry_yes - final_yes
        if pnl > 0:
            return "correct_buy"
        if pnl < 0:
            return "wrong_buy"
        # Exact break-even — rare floating-point tie; treat as a correct pass
        # so it doesn't bias the win-rate denominator.
        return "correct_pass"

    # PASS — missed_opportunity only applies when there was a *clean* declared
    # winner the council strongly favored. Intermediate resolutions don't have
    # a winner to have "missed"; they fall through to correct_pass.
    winner = _winner(entry)
    if winner is None:
        return "correct_pass"
    council_side = _council_favored_side(entry)
    council_prob = _council_favored_prob(entry)
    if council_side == winner and council_prob >= MISSED_OPPORTUNITY_PROB_FLOOR:
        return "missed_opportunity"
    return "correct_pass"


# ---------------------------------------------------------------------------
# Per-agent accuracy
# ---------------------------------------------------------------------------

class AgentStats(dict):
    """Just a dict subclass for ergonomics."""


def agent_accuracy(entries: list[dict]) -> dict[str, AgentStats]:
    """Aggregate per-agent accuracy across all resolved, non-legacy entries."""
    stats: dict[str, dict] = {}

    for entry in entries:
        if entry.get("legacy"):
            continue
        # Use the directional winner so intermediate resolutions (e.g. 0.50
        # from entry 0.40) still contribute to agent stats. Returns None
        # only on true voids, unresolved entries, and exact-push settlements.
        winner = _directional_winner(entry)
        if winner is None:
            continue
        # Brier actual uses the settled redemption price directly so partial
        # settlements score calibration correctly (saying 50% on a 0.50 market
        # should be ~0 error, not penalized against a forced 0/1 actual).
        final_yes = _final_yes_price(entry)
        actual = float(final_yes) if final_yes is not None else (
            1.0 if winner == "YES" else 0.0
        )

        for v in _verdicts(entry):
            name = v.get("agent_name", "?")
            s = stats.setdefault(name, {
                "n": 0, "hits": 0, "wtd_hits": 0.0, "wtd_total": 0.0,
                "brier_sum": 0.0, "high_conf_hits": 0, "high_conf_misses": 0,
                "lean_n": 0, "lean_hits": 0,
            })
            prob = float(v.get("probability", 0.5))
            picked_yes = prob > 0.5
            picked_side = "YES" if picked_yes else "NO"
            side_conf = float(v.get("confidence_yes" if picked_yes else "confidence_no", 0.0))
            hit = picked_side == winner

            s["n"] += 1
            s["brier_sum"] += (prob - actual) ** 2
            s["wtd_total"] += side_conf
            if hit:
                s["hits"] += 1
                s["wtd_hits"] += side_conf
                if side_conf >= 0.70:
                    s["high_conf_hits"] += 1
            else:
                if side_conf >= 0.70:
                    s["high_conf_misses"] += 1

            # Lean accuracy — directional read with a 0.45–0.55 dead band so
            # coin-flip reads don't get credit/blame either way.
            if prob > 0.55 or prob < 0.45:
                s["lean_n"] += 1
                lean_side = "YES" if prob > 0.55 else "NO"
                if lean_side == winner:
                    s["lean_hits"] += 1

    out: dict[str, AgentStats] = {}
    for name, s in stats.items():
        n = s["n"]
        out[name] = AgentStats(
            n=n,
            hit_rate=(s["hits"] / n) if n else 0.0,
            weighted_hit_rate=(s["wtd_hits"] / s["wtd_total"]) if s["wtd_total"] > 0 else 0.0,
            mean_brier=(s["brier_sum"] / n) if n else 0.0,
            high_conf_hits=s["high_conf_hits"],
            high_conf_misses=s["high_conf_misses"],
            lean_n=s["lean_n"],
            lean_rate=(s["lean_hits"] / s["lean_n"]) if s["lean_n"] else None,
        )
    return out


# ---------------------------------------------------------------------------
# Postmortem for a single entry
# ---------------------------------------------------------------------------

def signal_postmortem(entry: dict) -> dict:
    """Build a structured postmortem for one resolved entry.

    Returns a dict the CLI can render. Safe to call on unresolved entries
    (returns a stub with kind=unresolved).
    """
    kind = classify_outcome(entry)
    if kind == "unresolved":
        return {"kind": kind, "summary": "Not yet resolved.", "agents_with_winner": [],
                "agents_against_winner": [], "median_prob": None, "coin_flip": False}

    if entry.get("legacy"):
        return {"kind": kind, "summary": "Legacy entry — no per-agent detail.",
                "agents_with_winner": [], "agents_against_winner": [],
                "median_prob": None, "coin_flip": False}

    # For the postmortem, the "winner" is directional — the side that gained
    # in price from entry to resolution. Matches the clean winner at extremes
    # and gives intermediate settlements a meaningful winning side.
    clean_winner = _winner(entry)
    winner = _directional_winner(entry) or clean_winner
    final_yes = _final_yes_price(entry)
    entry_yes = float(_market(entry).get("yes_price", 0.5))
    intermediate = clean_winner is None and final_yes is not None
    verdicts = _verdicts(entry)
    if not verdicts or winner is None:
        return {"kind": kind, "summary": "Insufficient data.", "agents_with_winner": [],
                "agents_against_winner": [], "median_prob": None, "coin_flip": False}

    with_winner: list[dict] = []
    against_winner: list[dict] = []
    probs_yes = []

    for v in verdicts:
        prob = float(v.get("probability", 0.5))
        probs_yes.append(prob)
        picked_yes = prob > 0.5
        picked_side = "YES" if picked_yes else "NO"
        side_conf = float(v.get("confidence_yes" if picked_yes else "confidence_no", 0.0))
        record = {
            "agent": v.get("agent_name", "?"),
            "prob": prob,
            "side": picked_side,
            "side_conf": side_conf,
            "key_factor": v.get("key_factor", ""),
        }
        if picked_side == winner:
            with_winner.append(record)
        else:
            against_winner.append(record)

    with_winner.sort(key=lambda r: r["side_conf"], reverse=True)
    against_winner.sort(key=lambda r: r["side_conf"], reverse=True)

    med_yes = median(probs_yes)
    coin_flip = 0.45 <= med_yes <= 0.55

    r = _result(entry)
    rec = r.get("recommendation", "PASS")
    council_prob = _council_favored_prob(entry)
    pass_reason = r.get("pass_reason", "")

    n_total = len(verdicts)
    n_with = len(with_winner)

    # PnL string for intermediate resolutions — shown alongside BUY summaries
    # to make clear that the market settled at a middle price rather than 0/1.
    pnl_tag = ""
    if intermediate and rec.startswith("BUY"):
        if "YES" in rec:
            pnl = final_yes - entry_yes
        else:
            pnl = entry_yes - final_yes
        sign = "+" if pnl >= 0 else ""
        pnl_tag = (
            f" — market settled at ${final_yes:.2f} (entry ${entry_yes:.2f} "
            f"for {'YES' if 'YES' in rec else 'NO'} side), "
            f"{sign}{pnl * 100:.1f}¢/share"
        )

    if kind == "correct_buy":
        top = with_winner[0] if with_winner else None
        summary = (
            f"Correct {rec} — {n_with}/{n_total} agents called {winner}"
            + (f"; highest conviction: {top['agent']} ({top['side_conf']:.0%})" if top else "")
            + (" — coin-flip market in truth" if coin_flip else "")
            + pnl_tag
        )
    elif kind == "wrong_buy":
        worst = against_winner[0] if against_winner else None
        summary = (
            f"Wrong {rec} — only {n_with}/{n_total} agents called {winner} (winner)"
            + (f"; loudest wrong voice: {worst['agent']} ({worst['side_conf']:.0%})" if worst else "")
            + (" — market was a near coin-flip" if coin_flip else "")
            + pnl_tag
        )
    elif kind == "missed_opportunity":
        high_conf_with = [r for r in with_winner if r["side_conf"] >= 0.70]
        summary = (
            f"Missed opportunity — council favored {winner} at {council_prob:.0%} "
            f"({n_with}/{n_total} agents, {len(high_conf_with)} high-conv) but PASSed. "
            f"Reason: {pass_reason or 'gates blocked'}"
        )
    elif kind == "correct_pass":
        if intermediate:
            summary = (
                f"Correct PASS — market settled at ${final_yes:.2f} "
                f"(no clean winner; council median prob {med_yes:.0%})"
            )
        else:
            summary = (
                f"Correct PASS — council was unsure"
                + (" (coin-flip market)" if coin_flip else f", median prob {med_yes:.0%}")
                + f"; winner was {winner}"
            )
    else:  # voided
        summary = "Market cancelled / refunded — no redemption price available."

    return {
        "kind": kind,
        "summary": summary,
        "agents_with_winner": with_winner,
        "agents_against_winner": against_winner,
        "median_prob": med_yes,
        "coin_flip": coin_flip,
    }


def compute_and_persist_postmortem(entry: dict) -> dict:
    """Compute postmortem and write it back to the history file (idempotent)."""
    existing = entry.get("analysis")
    if existing:
        return existing
    pm = signal_postmortem(entry)
    if pm["kind"] != "unresolved":
        run_id = entry.get("run_id")
        if run_id:
            update_analysis(run_id, pm)
    return pm


# ---------------------------------------------------------------------------
# Option B shadow analytics (session 26+) — read-only descriptive metrics
# ---------------------------------------------------------------------------
#
# Coherence + side-bias work on ANY independent verdict (resolved or not).
# Per-side Brier requires resolution. ALL metrics filter on
# probability_source == "independent" — synthetic_mirror rows would
# mechanically zero them out.
#
# These functions are descriptive ONLY. Nothing in scoring.py reads them.
# Promotion to a gate input requires a passing backtest per the migration
# plan §6 step 6.

# Sample target for "this agent has enough independent data to be worth
# backtesting against." Below this we flag the agent as Phase 1 only.
OPTION_B_PHASE1_MIN_SAMPLES = 10
OPTION_B_PHASE2_MIN_SAMPLES = 30


def _is_independent(verdict: dict) -> bool:
    return verdict.get("probability_source") == "independent"


def option_b_overview(entries: list[dict]) -> dict:
    """Flat counts across the whole history file. Always cheap to call."""
    total_verdicts = 0
    independent = 0
    synthetic = 0
    independent_resolved = 0
    agents_with_any: set[str] = set()

    for entry in entries:
        winner = _directional_winner(entry)
        for v in _verdicts(entry):
            total_verdicts += 1
            src = v.get("probability_source")
            if src == "independent":
                independent += 1
                agents_with_any.add(v.get("agent_name", "?"))
                if winner is not None:
                    independent_resolved += 1
            elif src == "synthetic_mirror":
                synthetic += 1

    return {
        "total_verdicts": total_verdicts,
        "independent": independent,
        "synthetic": synthetic,
        "independent_resolved": independent_resolved,
        "fraction_independent": (independent / total_verdicts) if total_verdicts else 0.0,
        "agents_with_any_independent": sorted(agents_with_any),
    }


def option_b_per_agent(entries: list[dict]) -> dict[str, dict]:
    """Per-agent shadow metrics, filtered to probability_source == "independent".

    Returns dict keyed by agent_name with:
      - n_independent           — count of independent verdicts (any resolution status)
      - n_resolved              — independent verdicts on resolved markets
      - mean_abs_drift          — mean |p_yes + p_no − 1| (coherence)
      - mean_signed_drift       — mean (p_yes + p_no − 1) (side-bias direction)
      - yes_brier               — per-side Brier on YES (resolved only)
      - no_brier                — per-side Brier on NO (resolved only)
      - phase                   — "phase1" / "phase2" / "below_phase1" by sample count
    """
    agg: dict[str, dict] = {}

    for entry in entries:
        if entry.get("legacy"):
            continue
        winner = _directional_winner(entry)
        final_yes = _final_yes_price(entry)
        for v in _verdicts(entry):
            if not _is_independent(v):
                continue
            name = v.get("agent_name", "?")
            s = agg.setdefault(name, {
                "n_independent": 0, "n_resolved": 0,
                "abs_drift_sum": 0.0, "signed_drift_sum": 0.0,
                "yes_brier_sum": 0.0, "no_brier_sum": 0.0,
            })

            p_yes_raw = v.get("probability_yes")
            p_no_raw = v.get("probability_no")
            if p_yes_raw is None or p_no_raw is None:
                continue
            p_yes = float(p_yes_raw)
            p_no = float(p_no_raw)

            s["n_independent"] += 1
            drift = p_yes + p_no - 1.0
            s["abs_drift_sum"] += abs(drift)
            s["signed_drift_sum"] += drift

            if winner is not None and final_yes is not None:
                actual_yes = float(final_yes)
                actual_no = 1.0 - actual_yes
                s["yes_brier_sum"] += (p_yes - actual_yes) ** 2
                s["no_brier_sum"] += (p_no - actual_no) ** 2
                s["n_resolved"] += 1

    out: dict[str, dict] = {}
    for name, s in agg.items():
        n = s["n_independent"]
        n_res = s["n_resolved"]
        if n >= OPTION_B_PHASE2_MIN_SAMPLES:
            phase = "phase2"
        elif n >= OPTION_B_PHASE1_MIN_SAMPLES:
            phase = "phase1"
        else:
            phase = "below_phase1"
        out[name] = {
            "n_independent": n,
            "n_resolved": n_res,
            "mean_abs_drift": (s["abs_drift_sum"] / n) if n else 0.0,
            "mean_signed_drift": (s["signed_drift_sum"] / n) if n else 0.0,
            "yes_brier": (s["yes_brier_sum"] / n_res) if n_res else None,
            "no_brier": (s["no_brier_sum"] / n_res) if n_res else None,
            "phase": phase,
        }
    return out


# ---------------------------------------------------------------------------
# Equity curve — bankroll-denominated PnL / drawdown / per-trade Sharpe
# ---------------------------------------------------------------------------
#
# Walks resolved BUYs chronologically under a buy-and-hold-to-resolution
# model. Per-share Polymarket mechanics (entry=p, win=1.0, lose=0.0):
#
#   BUY YES shares=N entry=p → cost = N*p, proceeds = N*final_yes_price
#   BUY NO  shares=N entry=p → cost = N*(1-p), proceeds = N*(1-final_yes_price)
#   pnl = proceeds - cost
#
# A clean 5-share win at 50c → cost $2.50, proceeds $5.00, pnl +$2.50.
# A clean 5-share loss at 60c → cost $3.00, proceeds $0.00, pnl -$3.00.
#
# Intermediate settlements (e.g. 0.50/0.50 draws) are real PnL — they
# settle at the non-extreme final price.


def _entry_timestamp(entry: dict) -> datetime | None:
    raw = entry.get("timestamp")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _entry_title(entry: dict) -> str:
    m = _market(entry)
    return m.get("question") or m.get("title") or f"market {m.get('id', '?')}"


EquityMode = Literal["dollars", "shares", "kelly"]


def equity_curve(
    entries: list[dict],
    bankroll: float = 100.0,
    stake: float = 5.0,
    mode: EquityMode = "dollars",
    kelly_fallback_pct: float = 0.05,
) -> list[dict]:
    """Walk resolved BUYs chronologically and return a list of trade rows.

    Modes:

    - `"dollars"` (default): each trade commits a fixed dollar `stake`
      (capped to remaining balance). Shares = stake / entry_price_on_side.
    - `"shares"`: each trade buys a literal `stake` share count of the
      chosen side; cost = shares * entry_price_on_side. No balance cap —
      the curve can go negative, which surfaces "you would have been wiped
      out here" visually.
    - `"kelly"`: each trade sizes via `balance * kelly_stake_scaled` where
      `kelly_stake_scaled` is the half-Kelly (or whatever scaler was in
      force at run time) fraction persisted on the `CouncilResult`. This
      reads a frozen snapshot, so the backtest is walk-forward-legitimate
      — each trade is sized against the band-smoothed WR that existed
      **at the moment of the bet**, not today's rolling number. For BUYs
      with `kelly_stake_scaled == 0.0` (pre-EV-gate history, or rows where
      Kelly wasn't computed) the trade falls back to a flat dollar stake
      of `bankroll * kelly_fallback_pct` (default 5% of the starting
      bankroll, so the fallback scales with capital instead of staying
      pinned at $5). Each row carries `sizing_source="kelly"` or
      `"fallback"` in this mode so the summary can report both counts.

    PASSes, unresolved entries, and true voids are excluded. Each row
    carries running `balance`, `peak`, and `drawdown_pct` (fraction off peak).
    """
    if mode not in ("dollars", "shares", "kelly"):
        raise ValueError(
            f"mode must be 'dollars', 'shares', or 'kelly'; got {mode!r}"
        )
    if bankroll <= 0 or stake <= 0:
        raise ValueError("bankroll and stake must be positive")
    if kelly_fallback_pct <= 0 or kelly_fallback_pct > 1:
        raise ValueError("kelly_fallback_pct must be in (0, 1]")

    kelly_fallback_dollars = bankroll * kelly_fallback_pct

    trades: list[tuple[datetime | None, dict, str, float, float, float]] = []
    for e in entries:
        r = _result(e)
        rec = r.get("recommendation", "PASS")
        if not rec.startswith("BUY"):
            continue
        final_yes = _final_yes_price(e)
        if final_yes is None:
            continue
        entry_yes = float(_market(e).get("yes_price", 0.5))
        kelly_scaled = float(r.get("kelly_stake_scaled", 0.0) or 0.0)
        trades.append(
            (_entry_timestamp(e), e, rec, entry_yes, final_yes, kelly_scaled)
        )

    trades.sort(key=lambda t: (t[0] is None, t[0] or datetime.max))

    rows: list[dict] = []
    balance = bankroll
    peak = bankroll

    for idx, (ts, entry, rec, entry_yes, final_yes, kelly_scaled) in enumerate(
        trades, start=1
    ):
        buy_yes = "YES" in rec
        side_entry = entry_yes if buy_yes else (1.0 - entry_yes)
        side_final = final_yes if buy_yes else (1.0 - final_yes)
        side_entry = max(1e-4, min(0.9999, side_entry))
        side_final = max(0.0, min(1.0, side_final))

        sizing_source = mode
        if mode == "shares":
            shares = float(stake)
            cost = shares * side_entry
        elif mode == "kelly":
            if kelly_scaled > 0 and balance > 0:
                cost = min(balance, balance * kelly_scaled)
                sizing_source = "kelly"
            else:
                # Pre-EV-gate history (or PASS-sized rows that still settled
                # a BUY). Fall back to kelly_fallback_pct of the starting
                # bankroll so the fallback scales with capital instead of
                # staying pinned at $5.
                cost = (
                    min(balance, kelly_fallback_dollars)
                    if balance > 0
                    else kelly_fallback_dollars
                )
                sizing_source = "fallback"
            shares = cost / side_entry if side_entry > 0 else 0.0
        else:  # dollars
            cost = min(balance, float(stake)) if balance > 0 else float(stake)
            shares = cost / side_entry

        proceeds = shares * side_final
        pnl = proceeds - cost
        balance += pnl
        peak = max(peak, balance)
        dd_pct = ((peak - balance) / peak) if peak > 0 else 0.0

        rows.append({
            "idx": idx,
            "timestamp": ts,
            "title": _entry_title(entry),
            "recommendation": rec,
            "entry_price_yes": entry_yes,
            "final_price_yes": final_yes,
            "side_entry_price": side_entry,
            "side_final_price": side_final,
            "kelly_stake_scaled": kelly_scaled,
            "sizing_source": sizing_source,
            "shares": shares,
            "cost": cost,
            "proceeds": proceeds,
            "pnl": pnl,
            "balance": balance,
            "peak": peak,
            "drawdown_pct": dd_pct,
        })

    return rows


def equity_metrics(curve: list[dict], bankroll: float) -> dict:
    """Summarize an equity curve produced by `equity_curve()`.

    Per-trade Sharpe is computed from `pnl / cost` returns (unitless, the
    return on capital actually deployed that trade). Excess-return baseline
    is 0 — short-horizon event markets settle in hours-to-weeks and the
    risk-free rate over those intervals is noise. Annualization would
    require per-trade holding periods, which are not currently tracked.
    """
    if not curve:
        return {
            "n_trades": 0,
            "final_bankroll": bankroll,
            "net_pnl": 0.0,
            "net_pnl_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "max_drawdown_dollar": 0.0,
            "peak_balance": bankroll,
            "peak_idx": None,
            "peak_timestamp": None,
            "trough_balance": bankroll,
            "trough_idx": None,
            "trough_timestamp": None,
            "n_wins": 0,
            "n_losses": 0,
            "n_break_even": 0,
            "win_rate": 0.0,
            "best_trade": None,
            "worst_trade": None,
            "mean_pnl": 0.0,
            "stdev_pnl": 0.0,
            "sharpe_per_trade": None,
        }

    final_bankroll = curve[-1]["balance"]
    net_pnl = final_bankroll - bankroll
    net_pnl_pct = (net_pnl / bankroll) if bankroll > 0 else 0.0

    peak_row = max(curve, key=lambda r: r["balance"])
    trough_row = min(curve, key=lambda r: r["balance"])

    max_dd_pct = max((r["drawdown_pct"] for r in curve), default=0.0)
    max_dd_dollar = max((r["peak"] - r["balance"] for r in curve), default=0.0)

    wins = sum(1 for r in curve if r["pnl"] > 0)
    losses = sum(1 for r in curve if r["pnl"] < 0)
    break_even = sum(1 for r in curve if r["pnl"] == 0)

    best = max(curve, key=lambda r: r["pnl"])
    worst = min(curve, key=lambda r: r["pnl"])

    returns = [(r["pnl"] / r["cost"]) if r["cost"] > 0 else 0.0 for r in curve]
    mean_r = mean(returns)
    std_r = pstdev(returns) if len(returns) >= 2 else 0.0
    sharpe = (mean_r / std_r) if std_r > 0 else None

    pnl_list = [r["pnl"] for r in curve]
    mean_pnl = mean(pnl_list)
    std_pnl = pstdev(pnl_list) if len(pnl_list) >= 2 else 0.0

    n_kelly_sized = sum(1 for r in curve if r.get("sizing_source") == "kelly")
    n_fallback = sum(1 for r in curve if r.get("sizing_source") == "fallback")

    return {
        "n_trades": len(curve),
        "final_bankroll": final_bankroll,
        "net_pnl": net_pnl,
        "net_pnl_pct": net_pnl_pct,
        "max_drawdown_pct": max_dd_pct,
        "max_drawdown_dollar": max_dd_dollar,
        "peak_balance": peak_row["balance"],
        "peak_idx": peak_row["idx"],
        "peak_timestamp": peak_row["timestamp"],
        "trough_balance": trough_row["balance"],
        "trough_idx": trough_row["idx"],
        "trough_timestamp": trough_row["timestamp"],
        "n_wins": wins,
        "n_losses": losses,
        "n_break_even": break_even,
        "n_kelly_sized": n_kelly_sized,
        "n_fallback": n_fallback,
        "win_rate": (wins / len(curve)),
        "best_trade": best,
        "worst_trade": worst,
        "mean_pnl": mean_pnl,
        "stdev_pnl": std_pnl,
        "sharpe_per_trade": sharpe,
    }


# ---------------------------------------------------------------------------
# Operational cost — what we paid for running the pipeline, pass or buy
# ---------------------------------------------------------------------------
#
# Every analyze run (regardless of BUY / PASS / unresolved outcome) costs
# the same pipeline: one Search Architect LLM call + N Tavily searches
# + one Synthesizer LLM call + one Council run (7 parallel LLM calls).
#
# Cached re-runs cost $0 but we don't currently persist a cache-hit flag,
# so the figure this function returns is an upper bound. For a user who
# mostly runs each market once (typical), the bound is tight.


def _rec_of(entry: dict) -> str:
    return _result(entry).get("recommendation", "PASS")


def operational_cost(
    entries: list[dict],
    tavily_searches_per_analysis: int,
    tavily_cost_per_search: float,
    search_architect_cost: float,
    synthesizer_cost: float,
    council_cost_per_run: float,
) -> dict:
    """Aggregate operational cost over ALL history entries.

    Counts every entry as a full fresh pipeline run (an upper bound —
    cached re-runs don't have a persisted flag yet). Breakdown by
    recommendation class lets the CLI show "you paid for N PASSes that
    didn't generate any equity on top of M BUYs."
    """
    from collections import Counter

    tavily_per = tavily_searches_per_analysis * tavily_cost_per_search
    llm_per = search_architect_cost + synthesizer_cost + council_cost_per_run
    per_analysis = tavily_per + llm_per

    rec_counts: Counter[str] = Counter()
    for e in entries:
        rec = _rec_of(e)
        # Collapse specific BUY kinds into a single bucket for the summary.
        if rec.startswith("BUY"):
            rec_counts["BUY"] += 1
        else:
            rec_counts["PASS"] += 1

    # Secondary slice: resolution state. Useful for "X BUYs are still
    # pending — they'll settle later but we paid for them already."
    n_total = len(entries)
    n_resolved = sum(
        1
        for e in entries
        if (e.get("resolution") or {}).get("final_yes_price") is not None
    )
    n_unresolved = n_total - n_resolved

    return {
        "n_total": n_total,
        "n_buy": rec_counts.get("BUY", 0),
        "n_pass": rec_counts.get("PASS", 0),
        "n_resolved": n_resolved,
        "n_unresolved": n_unresolved,
        "tavily_searches_per_analysis": tavily_searches_per_analysis,
        "tavily_cost_per_search": tavily_cost_per_search,
        "tavily_per_analysis": tavily_per,
        "llm_per_analysis": llm_per,
        "search_architect_cost": search_architect_cost,
        "synthesizer_cost": synthesizer_cost,
        "council_cost_per_run": council_cost_per_run,
        "per_analysis": per_analysis,
        "total_tavily": n_total * tavily_per,
        "total_llm": n_total * llm_per,
        "total": n_total * per_analysis,
    }
