# pqi/main.py
"""
CLI entry point — `python -m pqi.main analyze`

Commands:
  analyze   Fetch markets, let user pick one, run the council, print results.

Uses typer for CLI parsing and rich for all terminal output.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import typer
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich import box

from pqi import config
from pqi.models import BriefingDocument, CouncilResult, EventGroup, MarketData, PricePoint

app = typer.Typer(help="PQI — Polymarket Qualitative Intelligence advisor.")
console = Console()


# ---------------------------------------------------------------------------
# Logging → console presentation
# ---------------------------------------------------------------------------
# Default runs are clean: INFO / httpx chatter is hidden and the analysis flow
# is narrated by the pretty phase reporter below. WARNINGs and above always
# show (orange / red) because they're notices worth seeing. `--verbose` drops
# the level back to INFO so the raw logs reappear underneath for debugging.


class _ConsoleLogHandler(logging.Handler):
    """Render log records through the rich console with level-based colour.

    INFO/DEBUG are dim and compact (only surfaced when --verbose lowers the
    level); WARNING is orange and ERROR/CRITICAL red, each with a glyph, so
    important notices stay legible amid the designed output. Messages are
    escaped — some carry literal ``[brackets]`` (e.g. ``[bear]``) that rich
    would otherwise treat as markup."""

    _LEVEL_STYLE: dict[int, tuple[str, str]] = {
        logging.WARNING: ("dark_orange", "⚠"),
        logging.ERROR: ("red", "✖"),
        logging.CRITICAL: ("bold red", "✖"),
    }

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = escape(record.getMessage())
        except Exception:  # never let logging crash the run
            return
        if record.levelno >= logging.WARNING:
            style, glyph = self._LEVEL_STYLE.get(record.levelno, ("red", "✖"))
            console.print(f"[bold {style}]{glyph}[/]  [{style}]{msg}[/]")
        else:
            console.print(f"[grey42]{record.levelname:<5} {record.name}  {msg}[/grey42]")


def _configure_logging(verbose: bool = False) -> None:
    """Install the console log handler and set the level. Default = WARNING
    (clean); verbose = config.LOG_LEVEL (INFO unless overridden)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(_ConsoleLogHandler())
    root.setLevel(config.LOG_LEVEL if verbose else logging.WARNING)


# Quiet by default at import time; the `analyze --verbose` flag re-configures.
_configure_logging(verbose=False)
logger = logging.getLogger(__name__)


class _PhaseReporter:
    """Narrated, numbered status flow for a single (non-batch) analysis.

    Renders ``[n/N] 🔎 Title…`` phase headers with indented ``↳`` sub-steps,
    in the warm narrative style chosen for the default UX. No-ops in batch mode
    (``enabled=False``) where the Live summary table owns the display, so the
    same call sites work for both paths."""

    def __init__(self, total: int, *, enabled: bool) -> None:
        self.total = total
        self.enabled = enabled
        self.n = 0

    def phase(self, emoji: str, title: str) -> None:
        if not self.enabled:
            return
        self.n += 1
        console.print(f"\n[grey42][{self.n}/{self.total}][/grey42] {emoji}  [bold]{title}[/bold]")

    def step(self, text: str) -> None:
        """A dim, in-progress sub-step (escaped — may come from providers)."""
        if self.enabled:
            console.print(f"      [grey42]↳[/grey42] [dim]{escape(text)}[/dim]")

    def detail(self, markup: str) -> None:
        """A highlighted result sub-line. `markup` is trusted rich markup."""
        if self.enabled:
            console.print(f"      [grey42]↳[/grey42] {markup}")

    def cached(self) -> None:
        if self.enabled:
            console.print("\n[bold yellow]⚡[/bold yellow]  [yellow]Loaded from cache "
                          "— reusing the previous council verdict.[/yellow]")


def _trend_arrow(change_pp: float) -> str:
    """Coloured ▲/▼/— for a weekly price change in percentage points."""
    if change_pp > 0.5:
        return "[green]▲[/green]"
    if change_pp < -0.5:
        return "[red]▼[/red]"
    return "[grey42]—[/grey42]"


# ---------------------------------------------------------------------------
# Rich display helpers
# ---------------------------------------------------------------------------


def _format_end_date(end_date: datetime) -> str:
    """Return a human-readable time-to-resolution string."""
    now = datetime.now(timezone.utc)
    total_seconds = (end_date - now).total_seconds()
    if total_seconds < 0:
        return "expired"
    if total_seconds < 3600:
        return f"{int(total_seconds // 60)}m"
    total_hours = total_seconds / 3600
    if total_hours < 24:
        return f"{total_hours:.0f}h"
    days = int(total_seconds // 86400)
    if days < 30:
        return f"{days}d"
    months = days // 30
    return f"~{months}mo"


def display_market_list(markets: list[MarketData]) -> None:
    """Render a numbered Rich table of available markets."""
    table = Table(
        title="Active Polymarket Markets",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("#", style="bold cyan", justify="right", width=4)
    table.add_column("Question", style="white", no_wrap=False)
    table.add_column("Starts", justify="right", width=9)
    table.add_column("Resolves", justify="right", width=9)
    table.add_column("YES", justify="right", width=6)
    table.add_column("NO", justify="right", width=6)
    table.add_column("Sig", justify="center", width=4)
    table.add_column("Seen", justify="center", width=5)
    table.add_column("Volume (USD)", justify="right", width=14)

    analyzed = _analyzed_market_ids()
    stats = _load_perceived_stats_safe()

    for i, m in enumerate(markets, start=1):
        question = m.question
        starts = _format_end_date(m.event_start_time) if m.event_start_time else "—"
        table.add_row(
            str(i),
            question,
            starts,
            _format_end_date(m.end_date),
            f"{m.yes_price:.0%}",
            f"{1.0 - m.yes_price:.0%}",
            _signal_glyph(m.yes_price, stats=stats),
            "[cyan]●[/cyan]" if str(m.id) in analyzed else "",
            f"${m.volume_usd:,.0f}",
        )

    console.print(table)


def display_council_result(result: CouncilResult) -> None:
    """Render the full CouncilResult — verdict table + recommendation panel."""
    console.print(f"\n[bold]Market:[/bold] {result.market.question}\n")

    # Per-agent verdict table
    verdict_table = Table(
        title="Council of Seven — Verdicts",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
    )
    verdict_table.add_column("Agent", style="bold", width=28)
    verdict_table.add_column("Prob %", justify="right", width=8)
    verdict_table.add_column("Conf Y%", justify="right", width=8)
    verdict_table.add_column("Conf N%", justify="right", width=8)
    verdict_table.add_column("Key Factor", style="dim", max_width=50)

    for v in result.verdicts:
        verdict_table.add_row(
            v.agent_name,
            f"{v.probability:.0%}",
            f"{v.confidence_yes:.0%}",
            f"{v.confidence_no:.0%}",
            v.key_factor,
        )

    console.print(verdict_table)

    # Recommendation colour + trade description
    rec = result.recommendation
    yes_price = result.market.yes_price
    no_price = 1.0 - yes_price
    sig = result.signal_type or ""

    # Signal label styling — only the signal label varies; BUY action is always bold green
    _signal_label_style = {
        "HIGH VALUE": "bold green",
        "MODERATE VALUE": "cyan",
        "LOW MARGIN": "dim cyan",
        "CONTRARIAN": "bold magenta",
    }

    if rec in ("BUY YES", "BUY NO"):
        side = "YES" if rec == "BUY YES" else "NO"
        price = yes_price if rec == "BUY YES" else no_price
        sig_label_style = _signal_label_style.get(sig, "green")
        colour = "magenta" if sig == "CONTRARIAN" else "green"
        trade_line = (
            f"[bold green]>>> BUY {side} @ ${price:.2f}/share — "
            f"[/bold green][{sig_label_style}]{sig}[/{sig_label_style}]"
            f"[bold green] <<<[/bold green]"
        )
    else:
        colour = "yellow"
        trade_line = "[bold yellow]>>> PASS — no actionable signal <<<[/bold yellow]"
        if result.pass_reason:
            trade_line += f"\n[dim]{result.pass_reason}[/dim]"

    # Edges are WR-based: (PerceivedTrueWinRate − side_price). Only meaningful
    # when a WR snapshot was captured at run time; otherwise we hide the line.
    has_wr_edge = result.perceived_win_rate_at_run is not None
    yes_edge_sign = "+" if result.edge >= 0 else ""
    no_edge_sign = "+" if result.no_edge >= 0 else ""
    # Show max ROI for whichever side the council leaned, even on PASS —
    # it's context for why a near-resolved side was rejected or what a
    # borderline call would have yielded.
    roi_side_price = yes_price if result.final_score >= result.no_score else no_price
    roi_val = ((1.0 - roi_side_price) / roi_side_price) if roi_side_price > 0 else 0.0
    roi_label = "YES" if result.final_score >= result.no_score else "NO"
    roi_line = (
        f"[bold]Max ROI ({roi_label}):[/bold]  +{roi_val:.1%}  (if that side wins)\n"
    )

    # EV / Kelly lines — shown when we have persisted stats. For BUY results
    # the Kelly number is the one the aggregator actually computed from the
    # banded threshold (so display always matches the gate decision).
    ev_line = ""
    from pqi.stats import load_perceived_stats

    perceived = load_perceived_stats()
    if perceived is not None and perceived.sample_size > 0:
        global_wr = perceived.true_win_rate
        gate_note = (
            " [dim](gate inactive — need more samples)[/dim]"
            if perceived.below_min_samples
            else ""
        )
        # Banded ceiling for the recommended side's price (falls back to the
        # global rate when `band_stats` is empty or no side is recommended).
        band_price = yes_price if rec == "BUY YES" else (
            no_price if rec == "BUY NO" else roi_side_price
        )
        band_wr = perceived.wr_for_price(band_price)
        band_suffix = ""
        for band in perceived.band_stats:
            if band.lo <= band_price < band.hi:
                band_suffix = (
                    f" [dim](band {band.lo:.2f}-{band.hi:.2f}, n={band.n})[/dim]"
                )
                break
        ev_line = (
            f"[bold]True Win Rate:[/bold] "
            f"[magenta]{band_wr:.1%}[/magenta] "
            f"[dim](global {global_wr:.1%}, n={perceived.sample_size}, EV ceiling)[/dim]"
            f"{band_suffix}{gate_note}\n"
        )
        if rec in ("BUY YES", "BUY NO"):
            # Use the persisted Kelly the aggregator computed against the
            # same banded threshold the gate used.
            ev_line += (
                f"[bold]Kelly stake:[/bold]   full {result.kelly_stake_full:.1%} · "
                f"@{config.KELLY_MULTIPLIER:g}x {result.kelly_stake_scaled:.1%} of bankroll\n"
            )
    # Trend line (conditional — only if price history was fetched)
    trend_line = ""
    if result.briefing and result.briefing.price_history:
        ph = result.briefing.price_history
        trend_line = (
            f"[bold]7d Trend YES:[/bold]   {ph.yes.change_1w_pp:+.1f}pp {ph.yes.direction}  |  "
            f"24h: {ph.yes.change_24h_pp:+.1f}pp  |  "
            f"Vol: {ph.yes.volatility_24h:.1f}pp/hr\n"
            f"[bold]7d Trend NO:[/bold]    {ph.no.change_1w_pp:+.1f}pp {ph.no.direction}  |  "
            f"24h: {ph.no.change_24h_pp:+.1f}pp  |  "
            f"Vol: {ph.no.volatility_24h:.1f}pp/hr\n"
        )

    if has_wr_edge:
        wr_at_run = result.perceived_win_rate_at_run or 0.0
        edge_line = (
            f"[bold]YES Edge vs WR:[/bold] {yes_edge_sign}{result.edge:.1%}  |  "
            f"[bold]NO Edge vs WR:[/bold] {no_edge_sign}{result.no_edge:.1%}  "
            f"[dim](WR {wr_at_run:.1%})[/dim]\n"
        )
    else:
        edge_line = (
            "[bold]Edge vs WR:[/bold]     [dim]n/a (need more resolved history)[/dim]\n"
        )

    summary = (
        f"[bold]Council YES:[/bold]    {result.final_score:.1%}  |  "
        f"[bold]Council NO:[/bold]  {result.no_score:.1%}\n"
        f"[bold]Market Prices:[/bold]  YES = {yes_price:.1%}  |  NO = {no_price:.1%}\n"
        f"{trend_line}"
        f"{edge_line}"
        f"{roi_line}"
        f"{ev_line}"
        f"[bold]Confidence:[/bold]     {result.confidence_level}\n\n"
        f"{trade_line}"
    )
    console.print(
        Panel(summary, title="Recommendation", border_style=colour, expand=False)
    )


def prompt_market_selection(markets: list[MarketData]) -> MarketData:
    """Display the market list and prompt the user to pick one (default = #1)."""
    display_market_list(markets)
    while True:
        raw = input(f"\nSelect a market [1–{len(markets)}, Enter for #1]: ").strip()
        if raw == "":
            return markets[0]
        try:
            idx = int(raw)
            if 1 <= idx <= len(markets):
                return markets[idx - 1]
        except ValueError:
            pass
        console.print(f"[red]Please enter a number between 1 and {len(markets)}.[/red]")


def _parse_selection(spec: str, max_n: int) -> list[int]:
    """Parse a multi-select spec into a sorted unique 1-indexed list.

    Accepted forms (comma-separated, whitespace tolerant):
      ""            → [1]                 (matches Enter-on-empty)
      "1"           → [1]
      "1,3,5"       → [1, 3, 5]
      "3-7"         → [3, 4, 5, 6, 7]
      "1-3,7,9-11"  → [1, 2, 3, 7, 9, 10, 11]
      "all"         → [1, …, max_n]

    Reversed ranges (e.g. `5-3`) are normalized. Raises ValueError with a
    user-readable message on malformed input or out-of-bound index.
    """
    s = spec.strip().lower()
    if s == "":
        return [1]
    if s == "all":
        return list(range(1, max_n + 1))

    picks: set[int] = set()
    for token in s.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo_str, _, hi_str = token.partition("-")
            try:
                lo = int(lo_str)
                hi = int(hi_str)
            except ValueError:
                raise ValueError(
                    f"'{token}' is not a valid range (use e.g. 3-7)."
                )
            if lo > hi:
                lo, hi = hi, lo
            if lo < 1 or hi > max_n:
                raise ValueError(f"Range {lo}-{hi} is outside 1-{max_n}.")
            picks.update(range(lo, hi + 1))
        else:
            try:
                idx = int(token)
            except ValueError:
                raise ValueError(f"'{token}' is not a number.")
            if idx < 1 or idx > max_n:
                raise ValueError(f"{idx} is outside 1-{max_n}.")
            picks.add(idx)
    if not picks:
        raise ValueError("No items selected.")
    return sorted(picks)


def _analyzed_market_ids() -> set[str]:
    """Set of market IDs that already have a (non-legacy) entry in history.

    Used to flag previously-analyzed markets in the selection tables so the
    user can skip re-running the council on them.
    """
    try:
        from pqi.history import load_history

        ids: set[str] = set()
        for e in load_history():
            if e.get("legacy"):
                continue
            mid = ((e.get("result") or {}).get("market") or {}).get("id")
            if mid:
                ids.add(str(mid))
        return ids
    except Exception as exc:
        logger.debug("Could not load history for analyzed-markers: %s", exc)
        return set()


def _load_perceived_stats_safe():
    """Best-effort loader for the persisted PerceivedStats snapshot. Returns
    None when the file is missing, malformed, below the sample threshold,
    or the EV gate is disabled — callers fall back to static defaults in
    those cases so cold-start behavior stays unchanged."""
    if not config.EV_GATE_ENABLED:
        return None
    try:
        from pqi.stats import load_perceived_stats

        stats = load_perceived_stats()
    except Exception:
        return None
    if stats is None or stats.below_min_samples:
        return None
    return stats


def _star_ceiling_for_price(fav_price: float, stats=None) -> float:
    """Upper price bound for the ⭐ signal at a specific favored-side price.

    The ceiling is the per-price-band smoothed win rate (`wr_for_price`)
    when `PerceivedStats` is active — so a market in the 0.80–0.95 band
    gets a higher ceiling than one in the 0.50–0.60 band, mirroring the
    banded EV gate. Falls back to the static 0.91 ROI-gate ceiling when
    stats are missing or below the sample-size threshold.
    """
    if stats is None:
        stats = _load_perceived_stats_safe()
    if stats is None:
        return 0.91
    return stats.wr_for_price(fav_price)


def _signal_glyph(yes_price: float, stats=None) -> str:
    """Tradeable-zone signal for the market list.

    ⭐ — favored side is in the actionable band [0.55, band_ceiling]: the
         council has room to confirm/contradict and the price is at or
         below the band-specific EV ceiling (PerceivedStats.wr_for_price
         when active, else the static 0.91 ROI-gate ceiling).
    🔻 — favored side is at 100% (or ≥99%): market is effectively resolved,
         no edge available, listed only as noise.
    (blank) — coin-flip zone (favored < 0.55) or above the band ceiling
              but < 1.0. Not actionable but worth glancing at.
    """
    fav = max(yes_price, 1.0 - yes_price)
    if fav >= 0.99:
        return "🔻"
    ceiling = _star_ceiling_for_price(fav, stats=stats)
    if 0.55 <= fav <= ceiling:
        return "⭐"
    return ""


def _moneyline_score(question: str, event_title: str) -> float:
    """Score how likely a market is the moneyline (overall outcome) market.

    Higher score = more likely moneyline.  Uses word overlap between the market
    question and the event title — moneyline questions typically restate the
    event title as a question (e.g. "Will T1 beat Gen.G?").
    """
    q_words = set(question.lower().split())
    t_words = set(event_title.lower().split())
    if not t_words:
        return 0.0
    # Jaccard-like: fraction of event-title words present in the question
    overlap = len(q_words & t_words)
    return overlap / len(t_words)


def _is_moneyline(m: MarketData) -> bool:
    """True when the Gamma-API `sportsMarketType` flags this as the moneyline.

    Authoritative for sports/esports events. Non-sports markets have
    `sports_market_type=None` and this returns False — callers fall back
    to the title-overlap heuristic in that case.
    """
    return m.sports_market_type == "moneyline"


def _moneyline_sort_key(m: MarketData, event_title: str) -> tuple[int, float, float]:
    """Composite sort key that promotes moneyline markets to the top.

    Ranks by (is_moneyline, title_overlap_score, volume) — all descending.
    The sportsMarketType flag is the authoritative signal when present;
    the title-overlap score is the fallback for non-sports events and the
    tiebreaker when multiple markets share the same flag value.
    """
    return (
        1 if _is_moneyline(m) else 0,
        _moneyline_score(m.question, event_title),
        m.volume_usd,
    )


def _sort_moneyline_first(
    markets: list[MarketData], event_title: str
) -> list[MarketData]:
    """Return markets sorted with the moneyline (overall outcome) first."""
    return sorted(
        markets,
        key=lambda m: _moneyline_sort_key(m, event_title),
        reverse=True,
    )


def _headline_market(group: EventGroup) -> MarketData:
    """The market whose YES/NO price best represents the event as a whole.

    Priority: explicit `sportsMarketType == "moneyline"` (authoritative from
    the Gamma API) → highest title-overlap score (heuristic fallback for
    non-sports events) → highest volume (tiebreaker). For single-market
    events or events without a title, returns the primary market directly.
    """
    if len(group.markets) <= 1:
        return group.primary_market
    # Direct hit on the API flag — skip the heuristic entirely.
    for m in group.markets:
        if _is_moneyline(m):
            return m
    if not group.event_title:
        return group.primary_market
    return _sort_moneyline_first(group.markets, group.event_title)[0]


def display_event_list(groups: list[EventGroup]) -> None:
    """Render a numbered Rich table of event groups."""
    table = Table(
        title="Active Polymarket Events",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("#", style="bold cyan", justify="right", width=4)
    table.add_column("Event", style="white", no_wrap=False)
    table.add_column("Starts", justify="right", width=9)
    table.add_column("Mkts", justify="center", width=5)
    table.add_column("YES", justify="right", width=6)
    table.add_column("NO", justify="right", width=6)
    table.add_column("Sig", justify="center", width=4)
    table.add_column("Seen", justify="center", width=5)
    table.add_column("Volume (USD)", justify="right", width=14)

    analyzed = _analyzed_market_ids()
    stats = _load_perceived_stats_safe()

    for i, g in enumerate(groups, start=1):
        # Single-market events show the market question; multi-market show event title
        if len(g.markets) == 1 or not g.event_title:
            label = g.primary_market.question
        else:
            label = g.event_title
        # No truncation — let Rich wrap long labels naturally

        starts = _format_end_date(g.event_start_time) if g.event_start_time else "—"
        # Headline prices track the moneyline / overall-outcome market,
        # not the highest-volume sub-market (Map 1 etc.). Sig/YES/NO all
        # reference this market so the event-level signal is consistent.
        pm = _headline_market(g)

        live_marker = " [green]LIVE[/green]" if g.live else ""

        table.add_row(
            str(i),
            f"{label}{live_marker}",
            starts,
            str(len(g.markets)),
            f"{pm.yes_price:.0%}",
            f"{1.0 - pm.yes_price:.0%}",
            _signal_glyph(pm.yes_price, stats=stats),
            ("[cyan]●[/cyan]" if any(str(m.id) in analyzed for m in g.markets) else ""),
            f"${g.total_volume:,.0f}",
        )

    console.print(table)


def _prompt_sub_market(group: EventGroup) -> MarketData:
    """Sub-select one market from a multi-market event.

    Single-market events return `group.markets[0]` directly. Moneyline
    (overall-outcome) is ordered first — matches `_sort_moneyline_first`.
    """
    if len(group.markets) == 1:
        return group.markets[0]

    sorted_markets = _sort_moneyline_first(group.markets, group.event_title)

    console.print(f"\n[cyan]Event:[/cyan] {group.event_title}")
    console.print(f"[cyan]{len(sorted_markets)} markets in this event:[/cyan]")

    sub_table = Table(box=box.SIMPLE, show_lines=False)
    sub_table.add_column("#", style="bold cyan", justify="right", width=4)
    sub_table.add_column("Market", style="white", no_wrap=False)
    sub_table.add_column("YES", justify="right", width=6)
    sub_table.add_column("NO", justify="right", width=6)
    sub_table.add_column("Sig", justify="center", width=4)
    sub_table.add_column("Seen", justify="center", width=5)
    sub_table.add_column("Volume", justify="right", width=12)

    analyzed = _analyzed_market_ids()
    stats = _load_perceived_stats_safe()

    for i, m in enumerate(sorted_markets, start=1):
        sub_table.add_row(
            str(i),
            m.question,
            f"{m.yes_price:.0%}",
            f"{1.0 - m.yes_price:.0%}",
            _signal_glyph(m.yes_price, stats=stats),
            "[cyan]●[/cyan]" if str(m.id) in analyzed else "",
            f"${m.volume_usd:,.0f}",
        )

    console.print(sub_table)

    while True:
        raw = input(
            f"\nSelect a market [1–{len(sorted_markets)}, Enter for #1]: "
        ).strip()
        if raw == "":
            return sorted_markets[0]
        try:
            idx = int(raw)
            if 1 <= idx <= len(sorted_markets):
                return sorted_markets[idx - 1]
        except ValueError:
            pass
        console.print(
            f"[red]Please enter a number between 1 and {len(sorted_markets)}.[/red]"
        )


def prompt_event_multiselect(
    groups: list[EventGroup],
) -> list[tuple[EventGroup, MarketData]]:
    """Event-list prompt that accepts single or multi-select input.

    Accepted input forms are documented on `_parse_selection`: an index
    (`3`), comma list (`1,3,5`), range (`3-11`), mixed (`1-3,7,9-11`),
    literal `all`, or Enter-on-empty → `[1]`. In addition, the literal
    `new` selects only events where no market has been analyzed before
    (empty "Seen" column) — handled here rather than in the parser so it
    can read the analyzed-market-id set in context.

    For a single-index selection the user also gets the sub-market prompt
    (moneyline + siblings) — this matches the pre-multi-select UX.
    Multi-index selections skip that prompt and pick the moneyline /
    headline market of each event automatically, which is the typical
    one-per-event workflow.
    """
    display_event_list(groups)

    analyzed = _analyzed_market_ids()
    unseen_indices = [
        i
        for i, g in enumerate(groups, start=1)
        if not any(str(m.id) in analyzed for m in g.markets)
    ]

    while True:
        raw = input(
            f"\nSelect events [1–{len(groups)}, '1,3', '3-7', 'all', "
            f"'new' ({len(unseen_indices)} unseen), Enter for #1]: "
        ).strip()
        if raw.lower() == "new":
            if not unseen_indices:
                console.print(
                    "[yellow]Every event in the list has been analyzed "
                    "before — no unseen events to pick.[/yellow]"
                )
                continue
            indices = unseen_indices
            break
        try:
            indices = _parse_selection(raw, len(groups))
            break
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")

    if len(indices) == 1:
        group = groups[indices[0] - 1]
        return [(group, _prompt_sub_market(group))]

    return [
        (groups[i - 1], _headline_market(groups[i - 1])) for i in indices
    ]


# ---------------------------------------------------------------------------
# Batch mode state + rendering
# ---------------------------------------------------------------------------


@dataclass
class _BatchRow:
    """Mutable per-event state for the batch-mode Live display.

    Populated by `_run_batch`: `phase` / `percent` advance through
    `_run_analysis`'s phase callback; `result` / `yes_raw` / `no_raw` are
    filled on success; `error` on failure. Persistence (history / sidecar
    append) is deferred to the caller so all file writes stay sequential.
    """
    idx: int
    group: EventGroup
    market: MarketData
    phase: str = "not started"
    percent: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result: Optional[CouncilResult] = None
    yes_raw: Optional[list[PricePoint]] = field(default=None)
    no_raw: Optional[list[PricePoint]] = field(default=None)
    error: Optional[str] = None
    run_id: Optional[str] = None


_PHASE_STYLES: dict[str, str] = {
    "not started": "dim",
    "starting": "dim",
    "resolving match": "cyan",
    "synthesizing briefing": "cyan",
    "fetching price history": "cyan",
    "council deliberating": "yellow",
    "scoring": "yellow",
    "done": "green",
    "cached": "green",
    "failed": "red",
}


def _format_elapsed(start: Optional[datetime], end: Optional[datetime]) -> str:
    if start is None:
        return "—"
    tip = end or datetime.now(timezone.utc)
    secs = max(0, int((tip - start).total_seconds()))
    return f"{secs // 60:02d}:{secs % 60:02d}"


def _render_batch_table(rows: list[_BatchRow], concurrency: int) -> Table:
    completed = sum(1 for r in rows if r.finished_at is not None)
    table = Table(
        title=(
            f"Batch Analyze — {completed}/{len(rows)} complete  ·  "
            f"concurrency={concurrency}"
        ),
        box=box.ROUNDED,
        show_lines=False,
    )
    table.add_column("#", style="bold cyan", justify="right", width=4)
    table.add_column("Event", style="white", no_wrap=False)
    table.add_column("Status", width=24)
    table.add_column("%", justify="right", width=5)
    table.add_column("Elapsed", justify="right", width=8)
    for r in rows:
        label = r.group.event_title or r.market.question
        if len(label) > 70:
            label = label[:69] + "…"
        style = _PHASE_STYLES.get(r.phase, "white")
        table.add_row(
            str(r.idx),
            label,
            f"[{style}]{r.phase}[/{style}]",
            f"{r.percent}%",
            _format_elapsed(r.started_at, r.finished_at),
        )
    return table


def _render_batch_summary(rows: list[_BatchRow]) -> None:
    table = Table(title="Batch Summary", box=box.ROUNDED, show_lines=False)
    table.add_column("#", style="bold cyan", justify="right", width=4)
    table.add_column("Event", style="white", no_wrap=False)
    table.add_column("Result", width=16)
    table.add_column("Signal", width=18)
    table.add_column("Price", justify="right", width=8)
    table.add_column("Max ROI", justify="right", width=9)
    table.add_column("Elapsed", justify="right", width=8)
    for r in rows:
        label = r.group.event_title or r.market.question
        if len(label) > 55:
            label = label[:54] + "…"
        if r.result is None:
            result_cell = "[red]FAILED[/red]"
            err = r.error or "—"
            signal_cell = err if len(err) <= 18 else err[:17] + "…"
            price_cell = "—"
            roi_cell = "—"
        else:
            res = r.result
            rec = res.recommendation
            yes_p = res.market.yes_price
            no_p = 1.0 - yes_p
            if rec == "BUY YES":
                result_cell = "[green]BUY YES[/green]"
                price = yes_p
            elif rec == "BUY NO":
                result_cell = "[green]BUY NO[/green]"
                price = no_p
            else:
                result_cell = "[yellow]PASS[/yellow]"
                price = yes_p if res.final_score >= res.no_score else no_p
            sig = res.signal_type or res.pass_reason or ""
            if len(sig) > 18:
                sig = sig[:17] + "…"
            signal_cell = sig
            price_cell = f"${price:.2f}"
            roi = (1.0 - price) / price if price > 0 else 0.0
            roi_cell = f"+{roi:.0%}"
        table.add_row(
            str(r.idx),
            label,
            result_cell,
            signal_cell,
            price_cell,
            roi_cell,
            _format_elapsed(r.started_at, r.finished_at),
        )
    console.print(table)


async def _run_batch(
    rows: list[_BatchRow], concurrency: int, no_cache: bool
) -> None:
    """Run each row's analysis under a shared `asyncio.Semaphore`.

    Live status table refreshes 4 Hz off the shared row states — workers
    mutate their `phase` / `percent` via the `on_phase` callback threaded
    into `_run_analysis`. Exceptions are captured per-row (not re-raised)
    so one failed event does not abort the batch.
    """
    sem = asyncio.Semaphore(concurrency)

    async def _work(row: _BatchRow) -> None:
        async with sem:
            row.started_at = datetime.now(timezone.utc)
            row.phase = "starting"
            row.percent = 0

            def _on_phase(label: str, pct: int) -> None:
                row.phase = label
                row.percent = pct

            try:
                res, yes_pts, no_pts = await _run_analysis(
                    row.market, no_cache=no_cache, on_phase=_on_phase
                )
                row.result = res
                row.yes_raw = yes_pts
                row.no_raw = no_pts
            except Exception as exc:
                row.error = str(exc) or exc.__class__.__name__
                row.phase = "failed"
                row.percent = 100
                logger.exception("Batch run failed for %s", row.market.id)
            finally:
                row.finished_at = datetime.now(timezone.utc)

    # Silence httpx/httpcore/anthropic HTTP traces for the duration of the
    # Live panel — stray HTTP request logs otherwise break the table
    # mid-render before the next 4 Hz tick redraws it. A handler-level
    # Filter is used (not `setLevel`) so the silencing is immune to any
    # downstream code that adjusts logger levels mid-run. Single-analyze
    # paths don't go through `_run_batch`, so request traces remain
    # visible there.
    _noisy_prefixes = ("httpx", "httpcore", "anthropic")

    class _DropNoisyHTTP(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return not record.name.startswith(_noisy_prefixes)

    _filter = _DropNoisyHTTP()
    _filtered_handlers: list[logging.Handler] = []
    for h in logging.getLogger().handlers:
        h.addFilter(_filter)
        _filtered_handlers.append(h)
    try:
        workers = [asyncio.create_task(_work(r)) for r in rows]

        with Live(
            _render_batch_table(rows, concurrency),
            console=console,
            refresh_per_second=4,
            transient=False,
        ) as live:
            while not all(t.done() for t in workers):
                await asyncio.sleep(0.25)
                live.update(_render_batch_table(rows, concurrency))
            live.update(_render_batch_table(rows, concurrency))

        # Surface stray exceptions at debug level — `_work` already captured
        # anything user-facing into `row.error`.
        for t in workers:
            if t.exception() is not None:
                logger.debug(
                    "Worker task exception (already captured): %s", t.exception()
                )
    finally:
        for h in _filtered_handlers:
            h.removeFilter(_filter)


# ---------------------------------------------------------------------------
# Async pipeline
# ---------------------------------------------------------------------------


async def _run_analysis(
    market: MarketData,
    no_cache: bool = False,
    shared_briefing: "BriefingDocument | None" = None,
    on_phase: Callable[[str, int], None] | None = None,
) -> "tuple[CouncilResult, list[PricePoint] | None, list[PricePoint] | None]":
    """Execute the full analysis pipeline for one market, with optional cache.

    If shared_briefing is provided (e.g. for sibling markets from the same event),
    the synthesizer step is skipped. The briefing's market is rebound to the
    current market so the council sees the right resolution criteria.

    `on_phase(label, percent)` is called at each pipeline checkpoint when
    provided — used by the batch-mode Live status table to report
    per-row progress. When `on_phase` is None this function behaves
    exactly as before, including all `console.print` status lines. When
    provided the prints are suppressed so the Live display stays clean.

    Returns `(result, yes_raw_points, no_raw_points)`. The two point lists
    are the raw CLOB fetch output threaded out to the caller for the
    `sidecar_price_points` writer. Both are None on a cache hit (CLOB
    fetch was skipped) or on any CLOB-side failure.
    """
    from pqi.ingestion.synthesizer import build_briefing
    from pqi.ingestion.match_resolver import resolve_match
    from pqi.ingestion.valuation import format_eur
    from pqi.agents.council import run_council
    from pqi.scoring import build_council_result
    from pqi.cache import get_cached, store_result

    verbose = on_phase is None

    def _emit(label: str, pct: int) -> None:
        if on_phase is not None:
            on_phase(label, pct)

    reporter = _PhaseReporter(total=5, enabled=verbose)

    _emit("resolving match", 2)
    reporter.phase("🔎", "Identifying the fixture…")

    # Resolve the structured match record FIRST so it can both inform the
    # cache invalidation key (status changes invalidate even when price
    # barely moves) and feed the synthesizer / council. The resolver picks a
    # provider by category (PandaScore for esports, football-data for soccer);
    # it returns None for uncovered categories, missing keys, low fuzzy
    # confidence, or any provider-side failure — the pipeline is unchanged in
    # those cases (Tavily-only briefing, no STRUCTURED MATCH DATA block).
    structured_match = None
    if shared_briefing is None:
        try:
            structured_match = await resolve_match(
                market, on_step=reporter.step if verbose else None
            )
        except Exception as exc:
            logger.warning("Match resolver failed for %s: %s", market.id, exc)
        if structured_match is not None:
            from pqi.ingestion.providers import provider_display_name
            sport_emoji = "⚽" if structured_match.provider == "football-data" else "🎮"
            reporter.detail(
                f"{sport_emoji} [bold]{escape(structured_match.team_a_name)} vs "
                f"{escape(structured_match.team_b_name)}[/bold]  [dim]· "
                f"{provider_display_name(structured_match.provider)} · "
                f"{structured_match.status}[/dim]"
            )
        else:
            reporter.step("no structured-data match — using news research only")
    structured_status = structured_match.status if structured_match else None

    if not no_cache:
        cached = await get_cached(market.id, market.yes_price, structured_status)
        if cached is not None:
            reporter.cached()
            _emit("cached", 100)
            return cached, None, None

    _emit("synthesizing briefing", 5)
    reporter.phase("📰", "Reading the news room…")

    if shared_briefing is not None:
        briefing = shared_briefing.model_copy(update={"market": market})
        reporter.step("reusing the shared event briefing")
    else:
        briefing = await build_briefing(market, structured_match=structured_match)
    reporter.detail(
        f"[dim]briefing ready · {len(briefing.key_facts)} key facts[/dim]"
    )
    sv = briefing.squad_valuation
    if sv is not None and sv.value_ratio is not None:
        fav = (
            f"  [dim]· favorite {escape(sv.favorite_name)} {sv.value_ratio:.1f}×[/dim]"
            if sv.favorite_name
            else "  [dim]· evenly matched[/dim]"
        )
        reporter.detail(
            f"💰 [bold]{escape(sv.team_a.name)}[/bold] {format_eur(sv.team_a.market_value_eur)} "
            f"vs [bold]{escape(sv.team_b.name)}[/bold] {format_eur(sv.team_b.market_value_eur)}{fav}"
        )

    _emit("fetching price history", 50)
    reporter.phase("📈", "Charting the order book…")

    # Enrich briefing with CLOB price history (non-fatal). The raw point
    # lists are threaded out to the caller for the full-fidelity sidecar
    # — aggregated metrics still live on the briefing as before.
    from pqi.market.clob import fetch_price_history

    yes_raw_points: list[PricePoint] | None = None
    no_raw_points: list[PricePoint] | None = None
    try:
        fetch_result = await fetch_price_history(market)
        if fetch_result:
            price_history, yes_raw_points, no_raw_points = fetch_result
            briefing = briefing.model_copy(update={"price_history": price_history})
            reporter.detail(
                f"YES {_trend_arrow(price_history.yes.change_1w_pp)} "
                f"{price_history.yes.change_1w_pp:+.1f}pp/wk  [grey42]·[/grey42]  "
                f"NO {_trend_arrow(price_history.no.change_1w_pp)} "
                f"{price_history.no.change_1w_pp:+.1f}pp/wk"
            )
        else:
            reporter.step("price history unavailable — proceeding without trend data")
    except Exception as exc:
        logger.warning("Price history enrichment failed: %s", exc)
        reporter.step("price history fetch failed — proceeding without trend data")

    _emit("council deliberating", 55)
    reporter.phase("🧠", "The Council of Seven deliberates…")
    verdicts = await run_council(briefing)
    reporter.detail(f"[dim]{len(verdicts)}/7 agents returned a verdict[/dim]")

    _emit("scoring", 95)
    reporter.phase("⚖️", "Weighing the verdict…")

    result = build_council_result(market, verdicts)
    result.briefing = briefing
    await store_result(result)

    _emit("done", 100)
    return result, yes_raw_points, no_raw_points


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@app.command()
def analyze(
    category: Optional[str] = typer.Option(
        None,
        "--category",
        "-c",
        help="Filter by series slug or broad tag (e.g. 'counter-strike', 'esports'). Run `pqi categories` to list options.",
    ),
    top_n: int = typer.Option(
        10, "--top-n", "-n", help="Number of markets to show in the list."
    ),
    top: bool = typer.Option(
        False, "--top", "-t", help="Auto-select the top market by volume (skip prompt)."
    ),
    starts_within: Optional[int] = typer.Option(
        None,
        "--starts-within",
        "-s",
        help="Only show markets whose event starts within this many hours (e.g. 4). Best for sports/esports.",
        min=1,
    ),
    resolve_within: Optional[int] = typer.Option(
        None,
        "--resolve-within",
        "-r",
        help="Only show markets that resolve within this many hours (e.g. 48).",
        min=1,
    ),
    live: bool = typer.Option(
        False,
        "--live",
        "-l",
        help="Only show live events (already started, not yet resolved). Composable with --starts-within.",
    ),
    min_volume: Optional[float] = typer.Option(
        None,
        "--min-volume",
        "-v",
        help="Override minimum volume floor in USD (use 0 to see all markets).",
        min=0,
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Force a fresh council run, ignoring any cached result.",
    ),
    summary_only: bool = typer.Option(
        False,
        "--summary-only",
        help="Batch mode only: skip per-event detail panels; show just the summary table.",
    ),
    concurrency: Optional[int] = typer.Option(
        None,
        "--concurrency",
        "-j",
        help="Max parallel runs in batch mode. Overrides ANALYZE_BATCH_CONCURRENCY.",
        min=1,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-V",
        help="Show the raw INFO/HTTP logs underneath the status flow (debugging).",
    ),
) -> None:
    """
    Fetch active Polymarket events, run the Council of Seven, and print recommendations.

    Event selection accepts a single index (`3`), a comma list (`1,3,5`),
    a range (`3-7`), mixed (`1-3,7,9-11`), or `all`. Multi-index
    selections run in batch mode with a live progress table; single-index
    selections keep the sub-market and sibling prompts.
    """
    from pqi.market.polymarket import fetch_events, CategoryNotFoundError

    _configure_logging(verbose=verbose)
    console.print("[bold cyan]PQI — Polymarket Qualitative Intelligence[/bold cyan]\n")

    try:
        groups = asyncio.run(
            fetch_events(
                category=category,
                resolve_within_hours=resolve_within,
                starts_within_hours=starts_within,
                live=live,
                volume_floor_override=min_volume,
            )
        )
    except CategoryNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if not groups:
        msg = "No events found matching your filters."
        if live:
            msg += " No live events found."
        if starts_within is not None:
            msg += f" No events start within {starts_within}h."
        if resolve_within is not None:
            msg += f" No markets resolve within {resolve_within}h."
        if category:
            msg += f" Category '{category}' may have no active markets."
        msg += " Try relaxing filters or use -v 0 to disable volume floor."
        console.print(f"[red]{msg}[/red]")
        raise typer.Exit(1)

    filter_desc = []
    if category:
        filter_desc.append(f"category={category}")
    if live:
        filter_desc.append("live")
    if starts_within is not None:
        filter_desc.append(f"starts within {starts_within}h")
    if resolve_within is not None:
        filter_desc.append(f"resolves within {resolve_within}h")
    if min_volume is not None:
        filter_desc.append(f"min_vol=${min_volume:,.0f}")
    if filter_desc:
        console.print(
            f"[dim]Filters: {', '.join(filter_desc)} → {len(groups)} events found.[/dim]"
        )

    groups = groups[:top_n]

    from pqi.history import append_result
    from pqi.sidecar_price_points import append_row as append_price_points

    # --- Selection -----------------------------------------------------------
    if top:
        group = groups[0]
        market = group.primary_market
        console.print("[dim]Auto-selected top event by volume.[/dim]")
        display_event_list([group])
        selections: list[tuple[EventGroup, MarketData]] = [(group, market)]
    else:
        selections = prompt_event_multiselect(groups)

    # --- Single-event path (preserves sub-select + sibling prompt) -----------
    if len(selections) == 1:
        group, market = selections[0]
        other_siblings = [m for m in group.markets if m.id != market.id]

        if other_siblings:
            console.print(f"\n[cyan]Event:[/cyan] {group.event_title}")
            console.print(
                f"[cyan]This event has {len(group.markets)} markets total:[/cyan]"
            )
            for i, s in enumerate(group.markets, 1):
                marker = (
                    " [bold green]← selected[/bold green]"
                    if s.id == market.id
                    else ""
                )
                console.print(
                    f"  {i}. {s.question[:60]}  (YES {s.yes_price:.0%}){marker}"
                )

        console.print(f"\n[bold]Analysing:[/bold] {market.question}\n")

        result, yes_raw_points, no_raw_points = asyncio.run(
            _run_analysis(market, no_cache=no_cache)
        )
        display_council_result(result)

        run_id = append_result(result)
        console.print("[dim]Result saved to history.[/dim]")
        # Sidecar write fires only when a fresh CLOB fetch returned usable
        # points. Cache-hit runs reuse an already-persisted history row
        # whose sidecar entry (if any) lives from the original fresh run.
        # Missing sidecar rows are the backfill script's job.
        if yes_raw_points is not None and no_raw_points is not None:
            append_price_points(
                run_id=run_id,
                market=market,
                analysis_ran_at=datetime.now(timezone.utc),
                interval=config.PRICE_HISTORY_INTERVAL,
                fidelity_minutes=config.PRICE_HISTORY_FIDELITY,
                yes_points=yes_raw_points,
                no_points=no_raw_points,
            )

        # Offer to analyze sibling markets (no extra API call).
        if other_siblings:
            console.print(
                f"\n[cyan]{len(other_siblings)} sibling market(s) available.[/cyan]"
            )
            raw = input("Analyze siblings too? [y/N]: ").strip().lower()
            if raw in ("y", "yes"):
                shared = result.briefing
                for sibling in other_siblings:
                    console.print(f"\n{'─' * 60}")
                    console.print(f"[bold]Analysing:[/bold] {sibling.question}\n")
                    sib_result, sib_yes_points, sib_no_points = asyncio.run(
                        _run_analysis(
                            sibling,
                            no_cache=no_cache,
                            shared_briefing=shared,
                        )
                    )
                    display_council_result(sib_result)
                    sib_run_id = append_result(sib_result)
                    console.print("[dim]Result saved to history.[/dim]")
                    if sib_yes_points is not None and sib_no_points is not None:
                        append_price_points(
                            run_id=sib_run_id,
                            market=sibling,
                            analysis_ran_at=datetime.now(timezone.utc),
                            interval=config.PRICE_HISTORY_INTERVAL,
                            fidelity_minutes=config.PRICE_HISTORY_FIDELITY,
                            yes_points=sib_yes_points,
                            no_points=sib_no_points,
                        )
        return

    # --- Batch path (N ≥ 2 events, moneyline-only, no sibling prompt) --------
    effective_concurrency = concurrency or config.ANALYZE_BATCH_CONCURRENCY
    rows = [
        _BatchRow(idx=i + 1, group=g, market=m)
        for i, (g, m) in enumerate(selections)
    ]
    console.print(
        f"\n[bold]Batch analysing {len(rows)} event(s), "
        f"concurrency={effective_concurrency}…[/bold]\n"
    )

    asyncio.run(_run_batch(rows, effective_concurrency, no_cache))

    # Persist sequentially — append_result / append_price_points append to
    # JSONL sidecars and parallel writes can interleave lines larger than
    # PIPE_BUF. Results are written in original selection order.
    saved = 0
    for r in rows:
        if r.result is None:
            continue
        r.run_id = append_result(r.result)
        saved += 1
        if r.yes_raw is not None and r.no_raw is not None:
            append_price_points(
                run_id=r.run_id,
                market=r.market,
                analysis_ran_at=datetime.now(timezone.utc),
                interval=config.PRICE_HISTORY_INTERVAL,
                fidelity_minutes=config.PRICE_HISTORY_FIDELITY,
                yes_points=r.yes_raw,
                no_points=r.no_raw,
            )
    failed = sum(1 for r in rows if r.result is None)
    console.print(
        f"[dim]{saved}/{len(rows)} saved to history"
        + (f"  ·  {failed} failed" if failed else "")
        + ".[/dim]\n"
    )

    # Per-event detail panels (skipped with --summary-only).
    if not summary_only:
        for r in rows:
            console.rule(f"#{r.idx} — {r.market.question[:80]}")
            if r.result is not None:
                display_council_result(r.result)
            else:
                console.print(f"[red]Analysis failed:[/red] {r.error}\n")

    _render_batch_summary(rows)


@app.command()
def categories(
    top_n: int = typer.Option(20, "--top-n", "-n", help="Number of series to show."),
) -> None:
    """
    List available tags (broad) and series (specific) you can pass to --category.
    """
    from pqi.market.polymarket import fetch_categories, fetch_tags

    console.print("[bold cyan]PQI — Available Categories[/bold cyan]\n")

    # Fetch both in parallel-ish (sequential but fast)
    tags = asyncio.run(fetch_tags())
    series_list = asyncio.run(fetch_categories(limit=top_n))

    # Tags table (broad categories)
    if tags:
        tag_table = Table(
            title="Broad Tags (match multiple series)",
            box=box.ROUNDED,
            show_lines=False,
        )
        tag_table.add_column("Tag", style="bold green", max_width=24)
        tag_table.add_column("Events", justify="right", width=8)
        for tag_name, count in tags:
            tag_table.add_row(tag_name, str(count))
        console.print(tag_table)
        console.print()

    # Series table (specific)
    if not series_list:
        console.print("[red]No active series found.[/red]")
        raise typer.Exit(1)

    table = Table(
        title="Specific Series (exact match with --category)",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("#", style="bold cyan", justify="right", width=4)
    table.add_column("Slug", style="bold white", max_width=36)
    table.add_column("Title", style="white", max_width=40)
    table.add_column("24h Volume (USD)", justify="right", width=18)

    for i, s in enumerate(series_list, start=1):
        table.add_row(
            str(i),
            s["slug"],
            s["title"],
            f"${s['volume24hr']:,.0f}",
        )

    console.print(table)
    console.print("\n[dim]Usage: pqi analyze --category <tag-or-slug>[/dim]")


# ---------------------------------------------------------------------------
# `pqi query` — SQL over history.jsonl via DuckDB
# ---------------------------------------------------------------------------


@app.command()
def query(
    sql: str = typer.Argument(..., help="SQL to run against the `history` / `verdicts` views."),
    limit: int = typer.Option(
        100, "--limit", "-n", help="Max rows to render in the table output."
    ),
) -> None:
    """
    Run an ad-hoc SQL query against the persisted history.jsonl.

    Two views are pre-registered:

      history   — raw top-level rows (nested: result.*, resolution.*, analysis.*)
      verdicts  — UNNESTed per-agent rows (non-legacy only); access verdict
                  fields via v.* (e.g. v.agent_name, v.probability,
                  v.probability_source).

    Examples:

      pqi query "SELECT category, COUNT(*) FROM history GROUP BY category"
      pqi query "SELECT v.agent_name, AVG(v.probability) FROM verdicts GROUP BY v.agent_name"

    See docs/analytics_queries.md for a starter set.
    """
    from pqi.analytics_sql import query as run_query, render_table

    try:
        rel = run_query(sql)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    except Exception as exc:  # DuckDB errors are varied; surface them
        console.print(f"[red]Query failed:[/red] {exc}")
        raise typer.Exit(1)

    table, truncated = render_table(rel, max_rows=limit)
    console.print(table)
    if truncated:
        console.print(
            f"[dim]Output truncated at --limit {limit}. "
            f"Add LIMIT or raise -n to see more.[/dim]"
        )


# ---------------------------------------------------------------------------
# `pqi history` sub-app
# ---------------------------------------------------------------------------

history_app = typer.Typer(
    help="Inspect past council runs and validation analytics.",
    no_args_is_help=True,
)
app.add_typer(history_app, name="history")


def _spawn_auto_sync() -> None:
    """Fire-and-forget subprocess that syncs unresolved entries past their end_date."""
    import subprocess, sys

    try:
        subprocess.Popen(
            [sys.executable, "-m", "pqi.main", "history", "sync", "--auto"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        logger.debug("Auto-sync subprocess failed to spawn: %s", exc)


def _short_question(q: str, n: int = 50) -> str:
    return q if len(q) <= n else q[: n - 3] + "..."


def _outcome_glyph(kind: str) -> str:
    return {
        "correct_buy": "[green]✓[/green]",
        "wrong_buy": "[red]✗[/red]",
        "missed_opportunity": "[bold cyan]MISS[/bold cyan]",
        "correct_pass": "[dim]pass[/dim]",
        "voided": "[dim]void[/dim]",
        "unresolved": "[yellow]⏳[/yellow]",
    }.get(kind, "?")


_OUTCOME_ALIASES = {
    "correct_buy": "correct_buy",
    "buy": "correct_buy",
    "win": "correct_buy",
    "wrong_buy": "wrong_buy",
    "wrong": "wrong_buy",
    "loss": "wrong_buy",
    "missed_opportunity": "missed_opportunity",
    "miss": "missed_opportunity",
    "missed": "missed_opportunity",
    "correct_pass": "correct_pass",
    "pass": "correct_pass",
    "voided": "voided",
    "void": "voided",
    "unresolved": "unresolved",
    "pending": "unresolved",
}

_REC_ALIASES = {
    "pass": {"PASS"},
    "buy": {"BUY YES", "BUY NO"},
    "buy_yes": {"BUY YES"},
    "yes": {"BUY YES"},
    "buy_no": {"BUY NO"},
    "no": {"BUY NO"},
}

_CONF_ALIASES = {
    "high": "HIGH",
    "h": "HIGH",
    "medium": "MEDIUM",
    "med": "MEDIUM",
    "m": "MEDIUM",
    "low": "LOW",
    "l": "LOW",
}


@history_app.command("list")
def history_list(
    last_n: int = typer.Option(20, "--last", "-n", help="Number of entries per page."),
    offset: int = typer.Option(
        0,
        "--offset",
        "-o",
        help="Skip this many of the most recent entries (paginate backwards).",
    ),
    no_auto_sync: bool = typer.Option(
        False, "--no-auto-sync", help="Skip background sync."
    ),
    show_all: bool = typer.Option(
        False, "--all", "-a", help="Show all results without pagination"
    ),
    outcome_filter: Optional[str] = typer.Option(
        None,
        "--outcome",
        help="Filter by outcome: correct_buy|wrong_buy|miss|correct_pass|voided|unresolved",
    ),
    rec_filter: Optional[str] = typer.Option(
        None,
        "--rec",
        help="Filter by recommendation: pass|buy|buy_yes|buy_no",
    ),
    conf_filter: Optional[str] = typer.Option(
        None,
        "--conf",
        help="Filter by confidence level: high|medium|low",
    ),
) -> None:
    """Overview table of past council runs."""
    from pqi.history import load_history
    from pqi.analytics import classify_outcome, signal_postmortem

    entries = load_history()
    if not entries:
        console.print("[yellow]No history yet. Run `pqi analyze` first.[/yellow]")
        raise typer.Exit(0)

    if not no_auto_sync:
        _spawn_auto_sync()

    # Resolve filter values up front (loud failure on typo).
    want_outcome: Optional[str] = None
    if outcome_filter:
        want_outcome = _OUTCOME_ALIASES.get(outcome_filter.lower())
        if want_outcome is None:
            console.print(
                f"[red]Unknown --outcome '{outcome_filter}'. Valid: {sorted(set(_OUTCOME_ALIASES))}[/red]"
            )
            raise typer.Exit(1)

    want_recs: Optional[set[str]] = None
    if rec_filter:
        want_recs = _REC_ALIASES.get(rec_filter.lower())
        if want_recs is None:
            console.print(
                f"[red]Unknown --rec '{rec_filter}'. Valid: {sorted(_REC_ALIASES)}[/red]"
            )
            raise typer.Exit(1)

    want_conf: Optional[str] = None
    if conf_filter:
        want_conf = _CONF_ALIASES.get(conf_filter.lower())
        if want_conf is None:
            console.print(
                f"[red]Unknown --conf '{conf_filter}'. Valid: high|medium|low[/red]"
            )
            raise typer.Exit(1)

    total = len(entries)

    # Build the visible window FIRST (preserves stable absolute IDs)…
    if show_all:
        windowed = list(enumerate(entries, start=1))  # (abs_id, entry)
        title = f"PQI History (all {total})"
    else:
        end = total - offset
        start = max(0, end - last_n)
        if end <= 0 or start >= end:
            console.print(
                f"[yellow]Offset {offset} is past the start of history ({total} entries).[/yellow]"
            )
            raise typer.Exit(0)
        windowed = [(start + 1 + i, e) for i, e in enumerate(entries[start:end])]
        title = f"PQI History (#{start + 1}–{end} of {total})"
        if offset > 0:
            title += f" [offset {offset}]"

    # …then apply filters (absolute IDs survive).
    def _matches(entry: dict) -> bool:
        r = entry.get("result") or {}
        if want_recs is not None and r.get("recommendation") not in want_recs:
            return False
        if want_conf is not None and r.get("confidence_level") != want_conf:
            return False
        if want_outcome is not None and classify_outcome(entry) != want_outcome:
            return False
        return True

    filtered = [(i, e) for i, e in windowed if _matches(e)]

    if not filtered:
        active = [
            f"outcome={outcome_filter}" if outcome_filter else None,
            f"rec={rec_filter}" if rec_filter else None,
            f"conf={conf_filter}" if conf_filter else None,
        ]
        active_str = ", ".join(x for x in active if x) or "(none)"
        console.print(f"[yellow]No entries match filters: {active_str}[/yellow]")
        raise typer.Exit(0)

    if outcome_filter or rec_filter or conf_filter:
        title += f"  [{len(filtered)} match]"
    table = Table(
        title=title,
        box=box.ROUNDED,
        show_lines=False,
    )
    table.add_column("#", style="bold cyan", justify="right", width=3)
    table.add_column("Question", style="white", max_width=42)
    table.add_column("Mkt Y", justify="right", width=5)
    table.add_column("Mkt N", justify="right", width=5)
    table.add_column("Cncl Y", justify="right", width=6)
    table.add_column("Cncl N", justify="right", width=6)
    table.add_column("Lean", justify="center", width=4)
    table.add_column("Lean✓", justify="center", width=5)
    table.add_column("vs Mkt", justify="center", width=6)
    table.add_column("Rec", justify="center", width=9)
    table.add_column("Conf", justify="center", width=6)
    table.add_column("Outcome", justify="center", width=8)
    table.add_column("Won", justify="center", width=4)
    table.add_column("Verdict", style="dim", max_width=44)
    table.add_column("When", style="dim", width=10)

    for i, entry in filtered:
        r = entry.get("result") or {}
        market = r.get("market") or {}
        rec = r.get("recommendation", "?")
        if rec in ("BUY YES", "BUY NO"):
            rec_style = f"[bold green]{rec}[/bold green]"
        else:
            rec_style = "[yellow]PASS[/yellow]"

        mkt_yes = market.get("yes_price", 0) or 0
        mkt_no = 1 - mkt_yes
        cncl_yes = r.get("final_score", 0) or 0
        cncl_no = r.get("no_score")
        if cncl_no is None:
            cncl_no = 1 - cncl_yes

        # Council lean — directional read independent of recommendation gates.
        if cncl_yes > 0.55:
            lean = "Y"
        elif cncl_yes < 0.45:
            lean = "N"
        else:
            lean = "·"

        winning_side = (entry.get("resolution") or {}).get("winning_side")
        if lean in ("Y", "N") and winning_side in ("YES", "NO"):
            lean_check = (
                "[green]✓[/green]" if lean == winning_side[0] else "[red]✗[/red]"
            )
        else:
            lean_check = "—"

        if mkt_yes > 0.5:
            mkt_lean = "Y"
        elif mkt_yes < 0.5:
            mkt_lean = "N"
        else:
            mkt_lean = "·"
        vs_mkt = "[magenta]↕[/magenta]" if {lean, mkt_lean} == {"Y", "N"} else ""

        kind = classify_outcome(entry)
        winner = (entry.get("resolution") or {}).get("winning_side") or "—"
        # Verdict tagline — use cached analysis if present, otherwise quick-compute
        analysis = entry.get("analysis") or signal_postmortem(entry)
        tagline = (
            analysis.get("summary", "")[:80]
            if not entry.get("legacy")
            else "[dim]legacy[/dim]"
        )

        ts = (entry.get("timestamp") or "")[:10]

        table.add_row(
            str(i),
            _short_question(market.get("question", "?"), 42),
            f"{mkt_yes:.0%}",
            f"{mkt_no:.0%}",
            f"{cncl_yes:.0%}",
            f"{cncl_no:.0%}",
            lean,
            lean_check,
            vs_mkt,
            rec_style,
            r.get("confidence_level", "?"),
            _outcome_glyph(kind),
            winner,
            tagline,
            ts,
        )

    console.print(table)
    console.print(
        "[dim]Tip: `pqi history show <#>` for detail (IDs are stable across pagination), "
        "`-o N` to page backwards, `pqi history sync` to refresh, `pqi history stats` for leaderboard.[/dim]"
    )


@history_app.command("show")
def history_show(
    index: str = typer.Argument(
        ...,
        help="Absolute ID from `history list` (# column, stable across pagination) or a run_id.",
    ),
) -> None:
    """Detailed view of a single past run + per-agent breakdown + postmortem."""
    from pqi.history import load_history
    from pqi.analytics import compute_and_persist_postmortem, classify_outcome

    entries = load_history()
    if not entries:
        console.print("[yellow]No history yet.[/yellow]")
        raise typer.Exit(0)

    # Resolve index — absolute 1-based position in full history, then run_id fallback.
    entry: dict | None = None
    if index.isdigit():
        idx = int(index) - 1
        if 0 <= idx < len(entries):
            entry = entries[idx]
    if entry is None:
        for e in entries:
            if e.get("run_id") == index:
                entry = e
                break
    if entry is None:
        console.print(f"[red]No entry matching '{index}'.[/red]")
        raise typer.Exit(1)

    if entry.get("legacy"):
        console.print("[yellow]Legacy entry — only summary fields available.[/yellow]")

    r = entry.get("result") or {}
    market = r.get("market") or {}
    res = entry.get("resolution") or {}

    # Header panel
    header_lines = [
        f"[bold]{market.get('question', '?')}[/bold]",
        f"[dim]run_id:[/dim] {entry.get('run_id', '?')}",
        f"[dim]run at:[/dim] {entry.get('timestamp', '?')}",
        f"[dim]market id:[/dim] {market.get('id', '?')}  [dim]category:[/dim] {market.get('category', '?')}",
        "",
        f"Market YES at run: [cyan]{market.get('yes_price', 0):.0%}[/cyan]",
    ]
    if res:
        header_lines.append(
            f"Final YES: [cyan]{res.get('final_yes_price', 0):.0%}[/cyan]   "
            f"Winner: [bold]{res.get('winning_side') or 'voided'}[/bold]   "
            f"Resolved: [dim]{res.get('resolved_at', '?')}[/dim]"
        )
    else:
        header_lines.append(
            "[yellow]Unresolved — run `pqi history sync` to fetch outcome.[/yellow]"
        )
    console.print(Panel("\n".join(header_lines), border_style="cyan"))

    # Recommendation panel
    rec = r.get("recommendation", "?")
    rec_color = "green" if rec in ("BUY YES", "BUY NO") else "yellow"
    rec_lines = [
        f"[{rec_color}]{rec}[/{rec_color}]   "
        f"signal: [bold]{r.get('signal_type') or '—'}[/bold]   "
        f"confidence: [bold]{r.get('confidence_level', '?')}[/bold]",
        f"Council YES: {r.get('final_score', 0):.1%}   "
        f"Council NO: {r.get('no_score', 0):.1%}",
    ]

    # Edge line — adapts to the semantic the entry was written with:
    # - New entries carry `perceived_win_rate_at_run` and edges are WR-based.
    # - Entries written before the EV gate (or with gate dormant) have
    #   edge/no_edge = 0.0 and no snapshot key — we render them as n/a.
    # - Legacy entries predate the WR semantic and stored (score − price),
    #   flagged explicitly so the user isn't misled.
    has_wr_key = "perceived_win_rate_at_run" in r
    wr_at_run = r.get("perceived_win_rate_at_run")
    edge_v = r.get("edge", 0) or 0
    no_edge_v = r.get("no_edge", 0) or 0
    if entry.get("legacy") or not has_wr_key:
        if edge_v or no_edge_v:
            rec_lines.append(
                f"[dim]Edge YES (legacy score−price): {edge_v:+.1%}   "
                f"Edge NO (legacy score−price): {no_edge_v:+.1%}[/dim]"
            )
        else:
            rec_lines.append(
                "[dim]Edge vs WR: n/a (no PerceivedTrueWinRate snapshot at run time)[/dim]"
            )
    elif wr_at_run is None:
        rec_lines.append(
            "[dim]Edge vs WR: n/a (EV gate inactive at run time — not enough samples yet)[/dim]"
        )
    else:
        rec_lines.append(
            f"Edge YES vs WR: {edge_v:+.1%}   Edge NO vs WR: {no_edge_v:+.1%}   "
            f"[dim](WR at run: {wr_at_run:.1%})[/dim]"
        )

    roi_val = r.get("expected_roi", 0) or 0
    if roi_val > 0:
        rec_lines.append(f"[bold]Expected ROI:[/bold] +{roi_val:.1%}")

    # Kelly snapshot at run time (advisory sizing). Hidden on PASS and on
    # legacy / pre-gate entries where the stakes were never computed.
    k_full = r.get("kelly_stake_full", 0) or 0
    k_scaled = r.get("kelly_stake_scaled", 0) or 0
    k_mult = r.get("kelly_multiplier", 0) or 0
    if rec in ("BUY YES", "BUY NO") and (k_full > 0 or k_scaled > 0):
        mult_label = f"@{k_mult:g}x" if k_mult else "scaled"
        rec_lines.append(
            f"[bold]Kelly stake at run:[/bold] full {k_full:.1%} · "
            f"{mult_label} {k_scaled:.1%} of bankroll"
        )

    if r.get("pass_reason"):
        rec_lines.append(f"[dim]Pass reason:[/dim] {r['pass_reason']}")
    console.print(
        Panel("\n".join(rec_lines), title="Recommendation", border_style=rec_color)
    )

    # Per-agent verdict table
    verdicts = r.get("verdicts") or []
    weights_snap = r.get("agent_weights_snapshot") or {}
    if verdicts:
        winner = res.get("winning_side")
        vtable = Table(title="Council Verdicts", box=box.SIMPLE)
        vtable.add_column("Agent", style="cyan")
        vtable.add_column("Prob YES", justify="right")
        vtable.add_column("Conf YES", justify="right")
        vtable.add_column("Conf NO", justify="right")
        vtable.add_column("Called", justify="center")
        if weights_snap:
            vtable.add_column("Wt", justify="right")
        if winner:
            vtable.add_column("✓", justify="center")
            vtable.add_column("Brier", justify="right")
        vtable.add_column("Key Factor", style="dim", max_width=40)

        for v in verdicts:
            prob = float(v.get("probability", 0.5))
            picked = "YES" if prob > 0.5 else "NO"
            row = [
                v.get("agent_name", "?"),
                f"{prob:.0%}",
                f"{float(v.get('confidence_yes', 0)):.0%}",
                f"{float(v.get('confidence_no', 0)):.0%}",
                picked,
            ]
            if weights_snap:
                w = float(weights_snap.get(v.get("agent_name", ""), 1.0))
                row.append(f"{w:.2f}")
            if winner:
                hit = picked == winner
                actual = 1.0 if winner == "YES" else 0.0
                row.append("[green]✓[/green]" if hit else "[red]✗[/red]")
                row.append(f"{(prob - actual) ** 2:.2f}")
            row.append(_short_question(v.get("key_factor", ""), 40))
            vtable.add_row(*row)
        console.print(vtable)

    # Postmortem panel
    pm = compute_and_persist_postmortem(entry)
    kind = pm.get("kind", "unresolved")
    pm_color = {
        "correct_buy": "green",
        "wrong_buy": "red",
        "missed_opportunity": "yellow",
        "correct_pass": "blue",
        "voided": "white",
        "unresolved": "dim",
    }.get(kind, "white")
    console.print(
        Panel(
            pm.get("summary", ""),
            title=f"Postmortem — {kind}",
            border_style=pm_color,
        )
    )

    # Briefing recap (if persisted)
    briefing = r.get("briefing")
    if briefing:
        recap_lines = []
        if briefing.get("key_facts"):
            recap_lines.append("[bold]Key facts:[/bold]")
            recap_lines.extend(f"  • {f}" for f in briefing["key_facts"][:5])
        if briefing.get("side_yes_case"):
            recap_lines.append("\n[bold green]Case for YES:[/bold green]")
            recap_lines.extend(f"  • {f}" for f in briefing["side_yes_case"][:4])
        if briefing.get("side_no_case"):
            recap_lines.append("\n[bold red]Case for NO:[/bold red]")
            recap_lines.extend(f"  • {f}" for f in briefing["side_no_case"][:4])
        if recap_lines:
            console.print(
                Panel(
                    "\n".join(recap_lines), title="Briefing recap", border_style="dim"
                )
            )


@history_app.command("sync")
def history_sync(
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Background mode: only sync entries past their end_date, no output.",
    ),
) -> None:
    """Fetch resolutions for unresolved entries from the Polymarket Gamma API."""
    import math
    from pqi.history import iter_unresolved, update_resolutions_batch
    from pqi.resolution import fetch_resolution

    # Heuristic pacing derived from GAMMA_RATE_LIMIT_DELAY:
    # treat `60 / delay` as the implicit requests-per-minute budget, reserve
    # a 20% headroom, and size a bounded worker pool to hide worst-case
    # ~1.5s Gamma call latency. No new knob — if GAMMA_RATE_LIMIT_DELAY
    # moves, pacing and concurrency move with it.
    _delay = max(config.GAMMA_RATE_LIMIT_DELAY, 0.01)
    _budget_per_min = 60.0 / _delay
    _effective_per_min = _budget_per_min * 0.8
    _min_interval = 60.0 / _effective_per_min  # == _delay / 0.8
    _workers = max(2, min(8, math.ceil(1.5 / _min_interval)))

    async def _run() -> tuple[int, int, int]:
        targets = list(iter_unresolved(past_end_only=auto))
        if not targets:
            return 0, 0, 0

        sem = asyncio.Semaphore(_workers)
        pace_lock = asyncio.Lock()
        loop = asyncio.get_event_loop()
        next_dispatch = loop.time()

        async def _one(entry: dict) -> tuple[str, str, dict | None]:
            nonlocal next_dispatch
            r = entry.get("result") or {}
            market_id = (r.get("market") or {}).get("id")
            run_id = entry.get("run_id") or ""
            if not market_id:
                return run_id, "skipped", None
            async with sem:
                # Global pacer: enforce min_interval between successive
                # request starts across all workers.
                async with pace_lock:
                    now = loop.time()
                    wait = next_dispatch - now
                    if wait > 0:
                        await asyncio.sleep(wait)
                        now = loop.time()
                    next_dispatch = now + _min_interval
                try:
                    res = await fetch_resolution(market_id)
                except Exception as exc:
                    if not auto:
                        console.print(
                            f"[red]error syncing {market_id}: {exc}[/red]"
                        )
                    return run_id, "error", None
            if res is None:
                return run_id, "pending", None
            return run_id, "resolved", dict(res)

        if not auto:
            console.print(
                f"[dim]Syncing {len(targets)} entries "
                f"(×{_workers} workers, ~{_effective_per_min:.0f} req/min cap)…[/dim]"
            )

        results = await asyncio.gather(*(_one(t) for t in targets))

        patches = {
            rid: res
            for rid, status, res in results
            if status == "resolved" and res is not None
        }
        if patches:
            update_resolutions_batch(patches)

        resolved = sum(1 for _, s, _ in results if s == "resolved")
        pending = sum(1 for _, s, _ in results if s == "pending")
        errors = sum(1 for _, s, _ in results if s == "error")
        return resolved, pending, errors

    resolved, pending, errors = asyncio.run(_run())

    # Refresh PerceivedTrueWinRate whenever new resolutions might have landed.
    # Cheap — it's a single pass over history.jsonl. Runs in auto mode too so
    # the EV gate picks up new samples without the user running `history stats`.
    if resolved > 0 or not auto:
        from pqi.stats import recompute_from_history

        try:
            recompute_from_history(config.MIN_WIN_RATE_SAMPLES)
        except Exception as exc:
            logger.debug("PerceivedStats recompute failed: %s", exc)

        # Same hook for dynamic agent weights — also a pure pass over history.
        if config.AGENT_WEIGHTS_ENABLED:
            from pqi.agent_weights import recompute_from_history as _recompute_weights

            try:
                _recompute_weights(
                    min_samples=config.MIN_AGENT_WEIGHT_SAMPLES,
                    shrink=config.AGENT_WEIGHT_SHRINK,
                )
            except Exception as exc:
                logger.debug("AgentWeights recompute failed: %s", exc)

    if not auto:
        console.print(
            f"[green]✓ {resolved} resolved[/green], "
            f"[yellow]{pending} still pending[/yellow]"
            + (f", [red]{errors} errors[/red]" if errors else "")
        )


@history_app.command("stats")
def history_stats(
    no_auto_sync: bool = typer.Option(
        False, "--no-auto-sync", help="Skip background sync."
    ),
) -> None:
    """Per-agent accuracy leaderboard across resolved history."""
    from pqi.history import load_history
    from pqi.analytics import agent_accuracy, classify_outcome
    from collections import Counter

    entries = load_history()
    if not entries:
        console.print("[yellow]No history yet.[/yellow]")
        raise typer.Exit(0)

    if not no_auto_sync:
        _spawn_auto_sync()

    # Recompute the EV-gate inputs up front so the numbers shown in the
    # overview are the same ones the next `analyze` run will use.
    from pqi.stats import recompute_from_history, kelly_fraction as _kelly

    try:
        perceived = recompute_from_history(config.MIN_WIN_RATE_SAMPLES)
    except Exception as exc:
        logger.debug("PerceivedStats recompute failed: %s", exc)
        perceived = None

    # Refresh dynamic agent weights in the same hook so `history stats`
    # always shows leaderboard numbers consistent with what the next
    # `analyze` run will score against.
    if config.AGENT_WEIGHTS_ENABLED:
        from pqi.agent_weights import recompute_from_history as _recompute_weights

        try:
            _recompute_weights(
                min_samples=config.MIN_AGENT_WEIGHT_SAMPLES,
                shrink=config.AGENT_WEIGHT_SHRINK,
            )
        except Exception as exc:
            logger.debug("AgentWeights recompute failed: %s", exc)

    counts = Counter(classify_outcome(e) for e in entries)
    stats = agent_accuracy(entries)

    # Council-level lean accuracy — same formula as `history list`'s Lean✓
    # column, aggregated over ALL resolved entries (legacy + non-legacy).
    # This is what makes the list view and stats view directly comparable.
    council_lean_n = council_lean_hits = 0
    for e in entries:
        win = (e.get("resolution") or {}).get("winning_side")
        if win not in ("YES", "NO"):
            continue
        cy = (e.get("result") or {}).get("final_score")
        if cy is None:
            continue
        if 0.45 <= cy <= 0.55:
            continue  # dead band
        council_lean_n += 1
        lean_yes = cy > 0.55
        if (lean_yes and win == "YES") or (not lean_yes and win == "NO"):
            council_lean_hits += 1
    lean_pct_str = (
        f"{council_lean_hits}/{council_lean_n} ({council_lean_hits / council_lean_n:.0%})"
        if council_lean_n
        else "n/a"
    )

    resolved_total = sum(
        1
        for e in entries
        if (e.get("resolution") or {}).get("winning_side") in ("YES", "NO")
    )
    nonlegacy_resolved = sum(
        1
        for e in entries
        if not e.get("legacy")
        and (e.get("resolution") or {}).get("winning_side") in ("YES", "NO")
    )

    summary = (
        f"[bold]{len(entries)}[/bold] runs total  "
        f"([dim]{resolved_total} resolved, {nonlegacy_resolved} non-legacy[/dim])\n"
        f"[green]{counts.get('correct_buy', 0)} correct buys[/green]   "
        f"[red]{counts.get('wrong_buy', 0)} wrong buys[/red]   "
        f"[yellow]{counts.get('missed_opportunity', 0)} missed[/yellow]   "
        f"[blue]{counts.get('correct_pass', 0)} correct passes[/blue]   "
        f"[dim]{counts.get('unresolved', 0)} unresolved   "
        f"{counts.get('voided', 0)} voided[/dim]\n"
        f"[bold]Council lean accuracy[/bold] (all resolved, dead band 0.45–0.55): "
        f"[cyan]{lean_pct_str}[/cyan]"
    )

    if perceived is not None:
        gate_state = (
            f"[dim](gate inactive: need {config.MIN_WIN_RATE_SAMPLES} samples)[/dim]"
            if perceived.below_min_samples
            else "[bold green](EV gate active)[/bold green]"
        )
        # Reference price 5pp below the win rate — always +EV, so the number
        # shown is the Kelly stake the user would actually take at their
        # typical-edge price. $0.75 is a canonical comparator (roadmap spec).
        ref_price = max(0.10, min(0.90, perceived.true_win_rate - 0.05))
        k_ref_full = _kelly(perceived.true_win_rate, ref_price)
        k_ref_half = _kelly(perceived.true_win_rate, ref_price, config.KELLY_MULTIPLIER)
        k_75_full = _kelly(perceived.true_win_rate, 0.75)
        summary += (
            f"\n[bold]PerceivedTrueWinRate:[/bold] "
            f"[magenta]{perceived.true_win_rate:.1%}[/magenta]  "
            f"([green]{perceived.correct_buys}[/green] correct / "
            f"[red]{perceived.wrong_buys}[/red] wrong, "
            f"n={perceived.sample_size})  {gate_state}\n"
            f"[bold]Kelly stake:[/bold] "
            f"@ ${ref_price:.2f} ref → full {k_ref_full:.1%} · "
            f"@{config.KELLY_MULTIPLIER:g}x {k_ref_half:.1%}  "
            f"[dim](@ $0.75: {k_75_full:.1%})[/dim]"
        )

    console.print(Panel(summary, title="Council overview", border_style="cyan"))

    # Per-price-band hit-rate breakdown (session 31+). These are the actual
    # thresholds the EV gate compares `side_price` against, so users can see
    # why a given BUY got rescued or blocked.
    if perceived is not None and perceived.band_stats:
        band_table = Table(
            title=(
                f"Per-price-band win rate  "
                f"[dim](K={perceived.shrinkage_k:g}, global {perceived.true_win_rate:.1%})[/dim]"
            ),
            box=box.ROUNDED,
        )
        band_table.add_column("Band", style="cyan")
        band_table.add_column("n", justify="right")
        band_table.add_column("Wins", justify="right", style="green")
        band_table.add_column("Losses", justify="right", style="red")
        band_table.add_column("Raw WR", justify="right")
        band_table.add_column("Smoothed WR", justify="right", style="magenta")
        band_table.add_column("Gate vs mid-band", justify="center")

        for band in perceived.band_stats:
            losses = band.n - band.wins
            raw_str = f"{band.raw_wr:.1%}" if band.raw_wr is not None else "—"
            # Qualitative hint for the mid-point of the band: does a bet
            # priced at the band's midpoint clear the smoothed threshold?
            mid = (band.lo + band.hi) / 2.0
            if mid < band.smoothed_wr:
                gate_hint = "[green]✓ allowed[/green]"
            else:
                gate_hint = "[red]✗ blocked[/red]"
            band_table.add_row(
                f"{band.lo:.2f}–{band.hi:.2f}",
                str(band.n),
                str(band.wins),
                str(losses),
                raw_str,
                f"{band.smoothed_wr:.1%}",
                gate_hint,
            )
        console.print(band_table)

    if not stats:
        console.print("[yellow]No resolved entries with per-agent detail yet.[/yellow]")
        return

    table = Table(title="Per-agent accuracy", box=box.ROUNDED, show_footer=True)
    table.add_column("Agent", style="cyan", footer="[bold]Average[/bold]")
    table.add_column("n", justify="right")
    table.add_column("Hit %", justify="right")
    table.add_column("Wtd Hit %", justify="right")
    table.add_column("Lean %", justify="right")
    table.add_column("Brier", justify="right")
    table.add_column("HiConf %", justify="right")
    table.add_column("HiConf ✓", justify="right", style="green")
    table.add_column("HiConf ✗", justify="right", style="red")

    sorted_stats = sorted(
        stats.items(), key=lambda kv: kv[1]["weighted_hit_rate"], reverse=True
    )

    # Totals for the footer row — micro-averages weighted by sample counts.
    tot_n = 0
    tot_hits = 0.0
    tot_wtd_num = 0.0
    tot_wtd_den = 0.0
    tot_lean_n = 0
    tot_lean_hits = 0.0
    tot_brier = 0.0
    tot_hc_hits = 0
    tot_hc_misses = 0

    for name, s in sorted_stats:
        hc_total = s["high_conf_hits"] + s["high_conf_misses"]
        hc_pct = (s["high_conf_hits"] / hc_total) if hc_total else None
        lean_pct = s.get("lean_rate")
        table.add_row(
            name,
            str(s["n"]),
            f"{s['hit_rate']:.0%}",
            f"{s['weighted_hit_rate']:.0%}",
            f"{lean_pct:.0%}" if lean_pct is not None else "—",
            f"{s['mean_brier']:.3f}",
            f"{hc_pct:.0%}" if hc_pct is not None else "—",
            str(s["high_conf_hits"]),
            str(s["high_conf_misses"]),
        )

        n = s["n"]
        tot_n += n
        tot_hits += s["hit_rate"] * n
        # Reconstruct weighted numerator/denominator from rate (we don't have raw).
        # Approximate by treating each agent equally if denominator unknown — but
        # since weighted_hit_rate = wtd_hits / wtd_total, and we only have the
        # ratio, fall back to a simple mean across agents below.
        tot_brier += s["mean_brier"] * n
        tot_hc_hits += s["high_conf_hits"]
        tot_hc_misses += s["high_conf_misses"]
        lean_n = s.get("lean_n", 0)
        tot_lean_n += lean_n
        if lean_pct is not None:
            tot_lean_hits += lean_pct * lean_n

    # Macro-average across agents for weighted_hit_rate (no raw access).
    avg_wtd = (
        sum(s["weighted_hit_rate"] for _, s in sorted_stats) / len(sorted_stats)
        if sorted_stats
        else 0.0
    )
    avg_hit = (tot_hits / tot_n) if tot_n else 0.0
    avg_brier = (tot_brier / tot_n) if tot_n else 0.0
    avg_lean = (tot_lean_hits / tot_lean_n) if tot_lean_n else None
    hc_total_all = tot_hc_hits + tot_hc_misses
    avg_hc = (tot_hc_hits / hc_total_all) if hc_total_all else None

    table.columns[1].footer = f"[bold]{tot_n}[/bold]"
    table.columns[2].footer = f"[bold]{avg_hit:.0%}[/bold]"
    table.columns[3].footer = f"[bold]{avg_wtd:.0%}[/bold]"
    table.columns[4].footer = (
        f"[bold]{avg_lean:.0%}[/bold]" if avg_lean is not None else "—"
    )
    table.columns[5].footer = f"[bold]{avg_brier:.3f}[/bold]"
    table.columns[6].footer = (
        f"[bold]{avg_hc:.0%}[/bold]" if avg_hc is not None else "—"
    )
    table.columns[7].footer = f"[bold]{tot_hc_hits}[/bold]"
    table.columns[8].footer = f"[bold]{tot_hc_misses}[/bold]"

    console.print(table)

    # ----- Option B shadow analytics (descriptive only) ---------------------
    from pqi.analytics import (
        OPTION_B_PHASE1_MIN_SAMPLES,
        OPTION_B_PHASE2_MIN_SAMPLES,
        option_b_overview,
        option_b_per_agent,
    )

    ov = option_b_overview(entries)
    if ov["independent"] == 0:
        console.print(
            "\n[dim]Option B shadow: no independent-probability verdicts yet "
            "(personas still warming up; new emissions tag as 'independent' "
            "after the step-3 persona rollout).[/dim]"
        )
    else:
        ob_summary = (
            f"[bold]{ov['independent']}[/bold] independent verdicts  "
            f"([dim]{ov['fraction_independent']:.1%} of {ov['total_verdicts']} total; "
            f"{ov['independent_resolved']} resolved[/dim])\n"
            f"[bold]Agents with any independent emission:[/bold] "
            f"{len(ov['agents_with_any_independent'])}/7"
        )
        console.print(
            Panel(
                ob_summary,
                title="Option B shadow (per-side probability autonomy)",
                border_style="magenta",
            )
        )

        ob_table = Table(
            title=(
                f"Per-agent Option B metrics  "
                f"(phase1≥{OPTION_B_PHASE1_MIN_SAMPLES}, phase2≥{OPTION_B_PHASE2_MIN_SAMPLES})"
            ),
            box=box.ROUNDED,
        )
        ob_table.add_column("Agent", style="cyan")
        ob_table.add_column("n_ind", justify="right")
        ob_table.add_column("n_res", justify="right")
        ob_table.add_column("|drift|", justify="right", style="dim")
        ob_table.add_column("signed", justify="right", style="dim")
        ob_table.add_column("YES Brier", justify="right")
        ob_table.add_column("NO Brier", justify="right")
        ob_table.add_column("Phase", justify="right")

        agent_rows = option_b_per_agent(entries)
        for name in sorted(agent_rows, key=lambda k: agent_rows[k]["n_independent"], reverse=True):
            r = agent_rows[name]
            phase_str = {
                "phase2": "[bold green]phase2[/bold green]",
                "phase1": "[yellow]phase1[/yellow]",
                "below_phase1": "[dim]below[/dim]",
            }[r["phase"]]
            yb = f"{r['yes_brier']:.3f}" if r["yes_brier"] is not None else "—"
            nb = f"{r['no_brier']:.3f}" if r["no_brier"] is not None else "—"
            ob_table.add_row(
                name,
                str(r["n_independent"]),
                str(r["n_resolved"]),
                f"{r['mean_abs_drift']:.2f}",
                f"{r['mean_signed_drift']:+.2f}",
                yb,
                nb,
                phase_str,
            )
        console.print(ob_table)
        console.print(
            "[dim]signed drift = mean(p_yes + p_no − 1). "
            "Positive = agent double-counts shared evidence; "
            "negative = agent leaves ignorance mass; "
            "near-zero = effectively mirrored (autonomy not landing).[/dim]"
        )


@history_app.command("equity")
def history_equity(
    bankroll: float = typer.Option(
        100.0, "--bankroll", "-b",
        help="Starting bankroll in dollars. Default $100.",
    ),
    stake: float = typer.Option(
        5.0, "--stake", "-s",
        help="Per-trade stake. Dollars in --mode dollars, shares in "
             "--mode shares. Unused in --mode kelly (see "
             "--kelly-fallback-pct).",
    ),
    mode: str = typer.Option(
        "dollars", "--mode", "-m",
        help="Sizing mode: 'dollars' (fixed-$ stake; default), 'shares' "
             "(fixed share count), or 'kelly' (use the half-Kelly fraction "
             "persisted at bet time; falls back to a "
             "--kelly-fallback-pct share of the bankroll for pre-EV-gate "
             "BUYs where kelly_stake_scaled was 0).",
    ),
    kelly_fallback_pct: float = typer.Option(
        0.05, "--kelly-fallback-pct", "-k",
        help="Fraction of the starting bankroll used as the flat fallback "
             "stake for pre-EV-gate BUYs in --mode kelly (default 0.05 = "
             "5%%, i.e. $5 at a $100 bankroll, $50 at $1000). Ignored in "
             "other modes.",
    ),
    last: int = typer.Option(
        15, "--last", "-n",
        help="Show the last N trades in the detail table (0 = all).",
    ),
    no_auto_sync: bool = typer.Option(
        False, "--no-auto-sync", help="Skip background sync.",
    ),
) -> None:
    """Equity curve: bankroll PnL, max drawdown, per-trade Sharpe.

    Walks resolved BUYs chronologically under a buy-and-hold-to-resolution
    model. PASSes, unresolved entries, and true voids are excluded.

    Per-trade Sharpe is naïve (unitless, no annualization, excess-return
    baseline = 0). At small n it's a descriptive number, not a decision
    threshold — the drawdown figure is the sturdier one to lean on.
    """
    from pqi.history import load_history
    from pqi.analytics import equity_curve, equity_metrics, operational_cost

    if mode not in ("dollars", "shares", "kelly"):
        console.print(
            f"[red]--mode must be 'dollars', 'shares', or 'kelly' "
            f"(got {mode!r})[/red]"
        )
        raise typer.Exit(1)
    if bankroll <= 0 or stake <= 0:
        console.print("[red]--bankroll and --stake must be positive.[/red]")
        raise typer.Exit(1)
    if not (0 < kelly_fallback_pct <= 1):
        console.print(
            "[red]--kelly-fallback-pct must be in (0, 1] "
            "(e.g. 0.05 for 5%).[/red]"
        )
        raise typer.Exit(1)

    entries = load_history()
    if not entries:
        console.print("[yellow]No history yet.[/yellow]")
        raise typer.Exit(0)

    if not no_auto_sync:
        _spawn_auto_sync()

    curve = equity_curve(
        entries,
        bankroll=bankroll,
        stake=stake,
        mode=mode,
        kelly_fallback_pct=kelly_fallback_pct,
    )
    metrics = equity_metrics(curve, bankroll=bankroll)

    if metrics["n_trades"] == 0:
        console.print(
            "[yellow]No resolved BUYs in history yet — nothing to compute. "
            "Run `pqi history sync` first if you think some should be "
            "resolved.[/yellow]"
        )
        raise typer.Exit(0)

    if mode == "dollars":
        stake_desc = f"${stake:.2f} flat stake / trade"
    elif mode == "shares":
        stake_desc = f"{stake:g} shares / trade"
    else:  # kelly
        fallback_dollars = bankroll * kelly_fallback_pct
        stake_desc = (
            f"half-Kelly sizing (fallback {kelly_fallback_pct*100:.1f}% "
            f"of bankroll = ${fallback_dollars:.2f} for pre-EV-gate rows)"
        )

    pnl = metrics["net_pnl"]
    pnl_color = "green" if pnl > 0 else ("red" if pnl < 0 else "white")
    pnl_sign = "+" if pnl >= 0 else ""
    pct_sign = "+" if metrics["net_pnl_pct"] >= 0 else ""

    best = metrics["best_trade"]
    worst = metrics["worst_trade"]
    sharpe = metrics["sharpe_per_trade"]
    sharpe_str = (
        f"{sharpe:.3f}" if sharpe is not None
        else "n/a (stdev = 0 or n < 2)"
    )

    peak_ts = (
        metrics["peak_timestamp"].strftime("%Y-%m-%d")
        if metrics["peak_timestamp"] else "—"
    )
    trough_ts = (
        metrics["trough_timestamp"].strftime("%Y-%m-%d")
        if metrics["trough_timestamp"] else "—"
    )

    lines = [
        f"Resolved BUYs:      [bold]{metrics['n_trades']}[/bold]",
        f"Starting bankroll:  ${bankroll:.2f}",
        f"Final bankroll:     [{pnl_color}]${metrics['final_bankroll']:.2f}[/{pnl_color}]",
        (
            f"Net PnL:            [{pnl_color}]{pnl_sign}${pnl:.2f} "
            f"({pct_sign}{metrics['net_pnl_pct'] * 100:.1f}%)[/{pnl_color}]"
        ),
        (
            f"Peak:               ${metrics['peak_balance']:.2f} "
            f"at trade #{metrics['peak_idx']} ({peak_ts})"
        ),
        (
            f"Trough:             ${metrics['trough_balance']:.2f} "
            f"at trade #{metrics['trough_idx']} ({trough_ts})"
        ),
        (
            f"Max drawdown:       [red]{metrics['max_drawdown_pct'] * 100:.1f}%[/red] "
            f"([red]${metrics['max_drawdown_dollar']:.2f}[/red])"
        ),
        (
            f"Wins / Losses:      [green]{metrics['n_wins']}[/green] / "
            f"[red]{metrics['n_losses']}[/red]"
            + (
                f" / [dim]{metrics['n_break_even']} push[/dim]"
                if metrics['n_break_even'] else ""
            )
            + f"  ([cyan]{metrics['win_rate'] * 100:.1f}%[/cyan] win rate)"
        ),
        (
            f"Best trade:         [green]+${best['pnl']:.2f}[/green] — "
            f"{(best['title'] or '')[:56]}"
        ),
        (
            f"Worst trade:        [red]-${abs(worst['pnl']):.2f}[/red] — "
            f"{(worst['title'] or '')[:56]}"
        ),
        (
            f"Mean PnL / trade:   "
            f"{'+' if metrics['mean_pnl'] >= 0 else ''}${metrics['mean_pnl']:.3f}"
        ),
        f"Stdev PnL / trade:  ${metrics['stdev_pnl']:.3f}",
        (
            f"Per-trade Sharpe:   [magenta]{sharpe_str}[/magenta] "
            f"[dim](pnl/cost returns, no annualization)[/dim]"
        ),
    ]

    if mode == "kelly":
        fallback_dollars = bankroll * kelly_fallback_pct
        lines.append(
            f"Sizing mix:         "
            f"[magenta]{metrics['n_kelly_sized']}[/magenta] Kelly-sized · "
            f"[dim]{metrics['n_fallback']} fallback "
            f"@ {kelly_fallback_pct*100:.1f}% (${fallback_dollars:.2f})[/dim]"
        )

    # ----- Operational cost (all analyses, not just resolved BUYs) ---------
    costs = operational_cost(
        entries,
        tavily_searches_per_analysis=config.TAVILY_SEARCHES_PER_MARKET,
        tavily_cost_per_search=config.TAVILY_COST_PER_SEARCH,
        search_architect_cost=config.SEARCH_ARCHITECT_COST,
        synthesizer_cost=config.SYNTHESIZER_COST,
        council_cost_per_run=config.COUNCIL_COST_PER_RUN,
    )
    net_of_cost = metrics["final_bankroll"] - bankroll - costs["total"]
    net_color = "green" if net_of_cost > 0 else ("red" if net_of_cost < 0 else "white")

    lines.extend([
        "",  # blank separator
        (
            f"[bold]Operational cost[/bold] [dim](all pipeline runs, "
            f"BUY + PASS; cached re-runs not subtracted)[/dim]"
        ),
        (
            f"Analyses:           [bold]{costs['n_total']}[/bold] "
            f"([green]{costs['n_buy']} BUY[/green] · "
            f"[yellow]{costs['n_pass']} PASS[/yellow])"
        ),
        (
            f"Per-analysis:       ${costs['per_analysis']:.4f} "
            f"[dim](${costs['tavily_per_analysis']:.4f} tavily · "
            f"${costs['llm_per_analysis']:.4f} LLM)[/dim]"
        ),
        (
            f"Total cost:         [red]${costs['total']:.2f}[/red] "
            f"[dim](${costs['total_tavily']:.2f} tavily · "
            f"${costs['total_llm']:.2f} LLM)[/dim]"
        ),
        (
            f"Net of cost:        [{net_color}]"
            f"{'+$' if net_of_cost >= 0 else '-$'}{abs(net_of_cost):.2f}"
            f"[/{net_color}] "
            f"[dim](equity PnL − infra cost)[/dim]"
        ),
    ])

    console.print(
        Panel(
            "\n".join(lines),
            title=(
                f"[bold]Equity Curve[/bold] · bankroll ${bankroll:.2f} · "
                f"{stake_desc}\n[dim]Buy-and-hold to resolution · PASSes / "
                "unresolved / voided excluded from PnL[/dim]"
            ),
            border_style="cyan",
        )
    )

    # ----- Per-trade detail ------------------------------------------------
    to_show = curve if last == 0 else curve[-last:]
    title = (
        f"All {len(curve)} trades"
        if last == 0 or len(curve) <= last
        else f"Last {len(to_show)} of {len(curve)} trades"
    )
    tbl = Table(title=title, box=box.SIMPLE, show_edge=False)
    tbl.add_column("#", justify="right", style="dim")
    tbl.add_column("Date", style="dim")
    tbl.add_column("Event", overflow="ellipsis", max_width=36, no_wrap=True)
    tbl.add_column("Side", justify="center")
    tbl.add_column("Entry", justify="right")
    tbl.add_column("Final", justify="right")
    if mode == "kelly":
        tbl.add_column("Size", justify="center", style="dim")
    tbl.add_column("Shares", justify="right", style="dim")
    tbl.add_column("Cost", justify="right", style="dim")
    tbl.add_column("PnL", justify="right")
    tbl.add_column("Balance", justify="right")
    tbl.add_column("DD%", justify="right", style="red")

    for r in to_show:
        p = r["pnl"]
        pnl_style = "green" if p > 0 else ("red" if p < 0 else "dim")
        pnl_str = (
            f"[{pnl_style}]{'+' if p >= 0 else ''}${p:.2f}[/{pnl_style}]"
        )
        date_str = (
            r["timestamp"].strftime("%Y-%m-%d") if r["timestamp"] else "—"
        )
        side = "YES" if "YES" in r["recommendation"] else "NO"
        side_style = "green" if side == "YES" else "red"
        dd = r["drawdown_pct"]
        dd_str = f"{dd * 100:.1f}%" if dd > 0.0005 else "—"

        row_cells = [
            str(r["idx"]),
            date_str,
            r["title"] or "—",
            f"[{side_style}]{side}[/{side_style}]",
            f"{r['side_entry_price']:.2f}",
            f"{r['side_final_price']:.2f}",
        ]
        if mode == "kelly":
            src = r.get("sizing_source", "")
            if src == "kelly":
                size_cell = f"[magenta]K{r['kelly_stake_scaled'] * 100:.1f}%[/magenta]"
            else:
                size_cell = "[dim]flat[/dim]"
            row_cells.append(size_cell)
        row_cells.extend([
            f"{r['shares']:.2f}",
            f"${r['cost']:.2f}",
            pnl_str,
            f"${r['balance']:.2f}",
            dd_str,
        ])
        tbl.add_row(*row_cells)

    console.print(tbl)


if __name__ == "__main__":
    app()
