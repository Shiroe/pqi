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

from statistics import median
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
