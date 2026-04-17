# pqi/main.py
"""
CLI entry point — `python -m pqi.main analyze`

Commands:
  analyze   Fetch markets, let user pick one, run the council, print results.

Uses typer for CLI parsing and rich for all terminal output.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from pqi import config
from pqi.models import BriefingDocument, CouncilResult, EventGroup, MarketData

app = typer.Typer(help="PQI — Polymarket Qualitative Intelligence advisor.")
console = Console()

logging.basicConfig(level=config.LOG_LEVEL)
logger = logging.getLogger(__name__)


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


def prompt_event_selection(groups: list[EventGroup]) -> tuple[EventGroup, MarketData]:
    """
    Display the event list and prompt the user to select an event, then a market.

    Single-market events skip the sub-selection step.
    Returns (selected_group, selected_market).
    """
    display_event_list(groups)

    # Step 1: pick an event group
    while True:
        raw = input(f"\nSelect an event [1–{len(groups)}, Enter for #1]: ").strip()
        if raw == "":
            group = groups[0]
            break
        try:
            idx = int(raw)
            if 1 <= idx <= len(groups):
                group = groups[idx - 1]
                break
        except ValueError:
            pass
        console.print(f"[red]Please enter a number between 1 and {len(groups)}.[/red]")

    # Step 2: if single market, return directly
    if len(group.markets) == 1:
        return group, group.markets[0]

    # Step 3: sub-select from sibling markets
    # Sort moneyline (overall outcome) market first — highest similarity to event title,
    # then by volume descending as tiebreaker.
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
            return group, sorted_markets[0]
        try:
            idx = int(raw)
            if 1 <= idx <= len(sorted_markets):
                return group, sorted_markets[idx - 1]
        except ValueError:
            pass
        console.print(
            f"[red]Please enter a number between 1 and {len(sorted_markets)}.[/red]"
        )


# ---------------------------------------------------------------------------
# Async pipeline
# ---------------------------------------------------------------------------


async def _run_analysis(
    market: MarketData,
    no_cache: bool = False,
    shared_briefing: "BriefingDocument | None" = None,
) -> CouncilResult:
    """Execute the full analysis pipeline for one market, with optional cache.

    If shared_briefing is provided (e.g. for sibling markets from the same event),
    the synthesizer step is skipped. The briefing's market is rebound to the
    current market so the council sees the right resolution criteria.
    """
    from pqi.ingestion.synthesizer import build_briefing
    from pqi.ingestion.match_resolver import resolve_match
    from pqi.agents.council import run_council
    from pqi.scoring import build_council_result
    from pqi.cache import get_cached, store_result

    # Resolve the structured match record FIRST so it can both inform the
    # cache invalidation key (status changes invalidate even when price
    # barely moves) and feed the synthesizer / council. Resolver returns
    # None for non-esports markets, missing keys, low fuzzy confidence,
    # or any provider-side failure — the pipeline is unchanged in those
    # cases (Tavily-only briefing, no STRUCTURED MATCH DATA block).
    structured_match = None
    if shared_briefing is None:
        try:
            structured_match = await resolve_match(market)
        except Exception as exc:
            logger.warning("Match resolver failed for %s: %s", market.id, exc)
        if structured_match is not None:
            console.print(
                f"[cyan]PandaScore: {structured_match.team_a_name} vs "
                f"{structured_match.team_b_name} ({structured_match.status}, "
                f"fuzzy={structured_match.fuzzy_score})[/cyan]"
            )
    structured_status = structured_match.status if structured_match else None

    if not no_cache:
        cached = await get_cached(market.id, market.yes_price, structured_status)
        if cached is not None:
            console.print("[green]Cache hit — using previous council result.[/green]")
            return cached

    if shared_briefing is not None:
        console.print(
            f"\n[cyan]Reusing shared event briefing for:[/cyan] {market.question[:80]}"
        )
        briefing = shared_briefing.model_copy(update={"market": market})
    else:
        console.print(f"\n[cyan]Fetching briefing for:[/cyan] {market.question[:80]}")
        briefing = await build_briefing(market, structured_match=structured_match)
    console.print(f"[cyan]Briefing ready — {len(briefing.key_facts)} key facts.[/cyan]")

    # Enrich briefing with CLOB price history (non-fatal)
    from pqi.market.clob import fetch_price_history

    try:
        price_history = await fetch_price_history(market)
        if price_history:
            briefing = briefing.model_copy(update={"price_history": price_history})
            console.print(
                f"[cyan]Price history: YES {price_history.yes.data_points}pts "
                f"{price_history.yes.direction} ({price_history.yes.change_1w_pp:+.1f}pp/wk)  |  "
                f"NO {price_history.no.data_points}pts "
                f"{price_history.no.direction} ({price_history.no.change_1w_pp:+.1f}pp/wk)[/cyan]"
            )
        else:
            console.print(
                "[dim]Price history unavailable — proceeding without trend data.[/dim]"
            )
    except Exception as exc:
        logger.warning("Price history enrichment failed: %s", exc)
        console.print(
            "[dim]Price history fetch failed — proceeding without trend data.[/dim]"
        )

    concurrency_info = (
        f"concurrency={config.LOCAL_LLM_CONCURRENCY}"
        if config.LLM_BACKEND == "ollama"
        else "parallel"
    )
    console.print(f"[cyan]Summoning the Council of Seven… ({concurrency_info})[/cyan]")
    verdicts = await run_council(briefing)
    console.print(f"[cyan]Council complete: {len(verdicts)}/7 verdicts.[/cyan]\n")

    result = build_council_result(market, verdicts)
    result.briefing = briefing
    await store_result(result)
    return result


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
) -> None:
    """
    Fetch active Polymarket events, run the Council of Seven, and print recommendations.
    """
    from pqi.market.polymarket import fetch_events, CategoryNotFoundError

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

    if top:
        group = groups[0]
        market = group.primary_market
        console.print(f"[dim]Auto-selected top event by volume.[/dim]")
        display_event_list([group])
    else:
        group, market = prompt_event_selection(groups)

    # Show sibling markets if this is a multi-market event
    other_siblings = [m for m in group.markets if m.id != market.id]

    if other_siblings:
        console.print(f"\n[cyan]Event:[/cyan] {group.event_title}")
        console.print(
            f"[cyan]This event has {len(group.markets)} markets total:[/cyan]"
        )
        for i, s in enumerate(group.markets, 1):
            marker = " [bold green]← selected[/bold green]" if s.id == market.id else ""
            console.print(f"  {i}. {s.question[:60]}  (YES {s.yes_price:.0%}){marker}")

    console.print(f"\n[bold]Analysing:[/bold] {market.question}\n")

    result = asyncio.run(_run_analysis(market, no_cache=no_cache))
    display_council_result(result)

    from pqi.history import append_result

    append_result(result)
    console.print("[dim]Result saved to history.[/dim]")

    # Offer to analyze sibling markets (already known from EventGroup — no extra API call)
    if other_siblings:
        console.print(
            f"\n[cyan]{len(other_siblings)} sibling market(s) available.[/cyan]"
        )
        raw = input("Analyze siblings too? [y/N]: ").strip().lower()
        if raw in ("y", "yes"):
            # Reuse the parent market's briefing — same event, same news.
            # Council still runs separately per sibling (different resolution criteria).
            shared = result.briefing
            for sibling in other_siblings:
                console.print(f"\n{'─' * 60}")
                console.print(f"[bold]Analysing:[/bold] {sibling.question}\n")
                sib_result = asyncio.run(
                    _run_analysis(
                        sibling,
                        no_cache=no_cache,
                        shared_briefing=shared,
                    )
                )
                display_council_result(sib_result)
                append_result(sib_result)
                console.print("[dim]Result saved to history.[/dim]")


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
    from pqi.history import iter_unresolved, update_resolution
    from pqi.resolution import fetch_resolution

    async def _run() -> tuple[int, int, int]:
        resolved = pending = errors = 0
        targets = list(iter_unresolved(past_end_only=auto))
        for entry in targets:
            r = entry.get("result") or {}
            market_id = (r.get("market") or {}).get("id")
            if not market_id:
                continue
            if not auto:
                console.print(f"[dim]Syncing {market_id}…[/dim]")
            try:
                res = await fetch_resolution(market_id)
            except Exception as exc:
                errors += 1
                if not auto:
                    console.print(f"[red]  error: {exc}[/red]")
                await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)
                continue
            if res is None:
                pending += 1
            else:
                update_resolution(entry["run_id"], dict(res))
                resolved += 1
            await asyncio.sleep(config.GAMMA_RATE_LIMIT_DELAY)
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


if __name__ == "__main__":
    app()
