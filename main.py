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
from pqi.models import CouncilResult, MarketData

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
    table.add_column("Question", style="white", max_width=48)
    table.add_column("Starts", justify="right", width=9)
    table.add_column("Resolves", justify="right", width=9)
    table.add_column("YES", justify="right", width=6)
    table.add_column("NO", justify="right", width=6)
    table.add_column("Volume (USD)", justify="right", width=14)

    for i, m in enumerate(markets, start=1):
        question = m.question if len(m.question) <= 48 else m.question[:45] + "..."
        starts = _format_end_date(m.event_start_time) if m.event_start_time else "—"
        table.add_row(
            str(i),
            question,
            starts,
            _format_end_date(m.end_date),
            f"{m.yes_price:.0%}",
            f"{1.0 - m.yes_price:.0%}",
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

    # Signal type styling
    _signal_colours = {
        "STRONG": "bold green",
        "CONFIRMED": "bold cyan",
        "CAUTIOUS": "bold yellow",
        "CONTRARIAN": "bold magenta",
    }

    if rec == "BUY YES":
        sig_style = _signal_colours.get(sig, "green")
        colour = "magenta" if sig == "CONTRARIAN" else "green"
        trade_line = f"[{sig_style}]>>> BUY YES @ ${yes_price:.2f}/share — {sig} <<<[/{sig_style}]"
    elif rec == "BUY NO":
        sig_style = _signal_colours.get(sig, "red")
        colour = "magenta" if sig == "CONTRARIAN" else "green"
        trade_line = f"[{sig_style}]>>> BUY NO @ ${no_price:.2f}/share — {sig} <<<[/{sig_style}]"
    else:
        colour = "yellow"
        trade_line = "[bold yellow]>>> PASS — no actionable signal <<<[/bold yellow]"
        if result.pass_reason:
            trade_line += f"\n[dim]{result.pass_reason}[/dim]"

    yes_edge_sign = "+" if result.edge >= 0 else ""
    no_edge_sign = "+" if result.no_edge >= 0 else ""
    summary = (
        f"[bold]Council YES:[/bold]    {result.final_score:.1%}  |  "
        f"[bold]Council NO:[/bold]  {result.no_score:.1%}\n"
        f"[bold]Market Prices:[/bold]  YES = {yes_price:.1%}  |  NO = {no_price:.1%}\n"
        f"[bold]YES Edge:[/bold]       {yes_edge_sign}{result.edge:.1%}  |  "
        f"[bold]NO Edge:[/bold]  {no_edge_sign}{result.no_edge:.1%}\n"
        f"[bold]Confidence:[/bold]     {result.confidence_level}\n\n"
        f"{trade_line}"
    )
    console.print(Panel(summary, title="Recommendation", border_style=colour, expand=False))


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


# ---------------------------------------------------------------------------
# Async pipeline
# ---------------------------------------------------------------------------


async def _run_analysis(market: MarketData, no_cache: bool = False) -> CouncilResult:
    """Execute the full analysis pipeline for one market, with optional cache."""
    from pqi.ingestion.synthesizer import build_briefing
    from pqi.agents.council import run_council
    from pqi.scoring import build_council_result
    from pqi.cache import get_cached, store_result

    if not no_cache:
        cached = await get_cached(market.id, market.yes_price)
        if cached is not None:
            console.print("[green]Cache hit — using previous council result.[/green]")
            return cached

    console.print(f"\n[cyan]Fetching briefing for:[/cyan] {market.question[:80]}")
    briefing = await build_briefing(market)
    console.print(f"[cyan]Briefing ready — {len(briefing.key_facts)} key facts.[/cyan]")

    concurrency_info = f"concurrency={config.LOCAL_LLM_CONCURRENCY}" if config.LLM_BACKEND == "ollama" else "parallel"
    console.print(f"[cyan]Summoning the Council of Seven… ({concurrency_info})[/cyan]")
    verdicts = await run_council(briefing)
    console.print(f"[cyan]Council complete: {len(verdicts)}/7 verdicts.[/cyan]\n")

    result = build_council_result(market, verdicts)
    await store_result(result)
    return result


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@app.command()
def analyze(
    category: Optional[str] = typer.Option(
        None, "--category", "-c",
        help="Filter by series slug or broad tag (e.g. 'counter-strike', 'esports'). Run `pqi categories` to list options.",
    ),
    top_n: int = typer.Option(
        10, "--top-n", "-n", help="Number of markets to show in the list."
    ),
    top: bool = typer.Option(
        False, "--top", "-t", help="Auto-select the top market by volume (skip prompt)."
    ),
    starts_within: Optional[int] = typer.Option(
        None, "--starts-within", "-s",
        help="Only show markets whose event starts within this many hours (e.g. 4). Best for sports/esports.",
        min=1,
    ),
    resolve_within: Optional[int] = typer.Option(
        None, "--resolve-within", "-r",
        help="Only show markets that resolve within this many hours (e.g. 48).",
        min=1,
    ),
    live: bool = typer.Option(
        False, "--live", "-l",
        help="Only show live events (already started, not yet resolved). Composable with --starts-within.",
    ),
    min_volume: Optional[float] = typer.Option(
        None, "--min-volume", "-v",
        help="Override minimum volume floor in USD (use 0 to see all markets).",
        min=0,
    ),
    no_cache: bool = typer.Option(
        False, "--no-cache",
        help="Force a fresh council run, ignoring any cached result.",
    ),
) -> None:
    """
    Fetch active Polymarket markets, run the Council of Seven, and print recommendations.
    """
    from pqi.market.polymarket import fetch_markets, CategoryNotFoundError

    console.print("[bold cyan]PQI — Polymarket Qualitative Intelligence[/bold cyan]\n")

    try:
        markets = asyncio.run(fetch_markets(
            category=category,
            resolve_within_hours=resolve_within,
            starts_within_hours=starts_within,
            live=live,
            volume_floor_override=min_volume,
        ))
    except CategoryNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if not markets:
        msg = "No markets found matching your filters."
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
        console.print(f"[dim]Filters: {', '.join(filter_desc)} → {len(markets)} markets found.[/dim]")

    markets = markets[:top_n]

    if top:
        market = markets[0]
        console.print(f"[dim]Auto-selected top market by volume.[/dim]")
        display_market_list([market])
    else:
        market = prompt_market_selection(markets)

    # Discover sibling markets from the same event
    from pqi.market.polymarket import fetch_event_markets
    event_title, siblings = asyncio.run(fetch_event_markets(market.id))
    # Filter out the selected market from siblings list
    other_siblings = [s for s in siblings if s.id != market.id]

    if other_siblings:
        console.print(f"\n[cyan]Event:[/cyan] {event_title}")
        console.print(f"[cyan]This event has {len(siblings)} markets total:[/cyan]")
        for i, s in enumerate(siblings, 1):
            marker = " [bold green]← selected[/bold green]" if s.id == market.id else ""
            console.print(f"  {i}. {s.question[:60]}  (YES {s.yes_price:.0%}){marker}")

    console.print(f"\n[bold]Analysing:[/bold] {market.question}\n")

    result = asyncio.run(_run_analysis(market, no_cache=no_cache))
    display_council_result(result)

    from pqi.history import append_result
    append_result(result)
    console.print("[dim]Result saved to history.[/dim]")

    # Offer to analyze sibling markets
    if other_siblings:
        console.print(f"\n[cyan]{len(other_siblings)} sibling market(s) available.[/cyan]")
        raw = input("Analyze siblings too? [y/N]: ").strip().lower()
        if raw in ("y", "yes"):
            for sibling in other_siblings:
                console.print(f"\n{'─' * 60}")
                console.print(f"[bold]Analysing:[/bold] {sibling.question}\n")
                sib_result = asyncio.run(_run_analysis(sibling, no_cache=no_cache))
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


@app.command()
def history(
    last_n: int = typer.Option(
        20, "--last", "-n", help="Number of recent entries to show."
    ),
) -> None:
    """
    Show past council runs from ~/.pqi/history.jsonl.
    """
    from pqi.history import load_history

    entries = load_history()
    if not entries:
        console.print("[yellow]No history yet. Run `pqi analyze` first.[/yellow]")
        raise typer.Exit(0)

    entries = entries[-last_n:]

    table = Table(
        title=f"PQI History (last {len(entries)} runs)",
        box=box.ROUNDED,
        show_lines=False,
    )
    table.add_column("#", style="bold cyan", justify="right", width=4)
    table.add_column("Question", style="white", max_width=40)
    table.add_column("Mkt YES", justify="right", width=8)
    table.add_column("Council", justify="right", width=8)
    table.add_column("Edge", justify="right", width=8)
    table.add_column("Rec", justify="center", width=9)
    table.add_column("Conf", justify="center", width=6)
    table.add_column("When", style="dim", width=16)

    for i, e in enumerate(entries, 1):
        question = e.get("question", "?")
        if len(question) > 40:
            question = question[:37] + "..."

        rec = e.get("recommendation", "?")
        if rec == "BUY YES":
            rec_style = "[green]BUY YES[/green]"
            edge_val = e.get("edge", 0)
            council_val = e.get("final_score", 0)
        elif rec == "BUY NO":
            rec_style = "[red]BUY NO[/red]"
            edge_val = e.get("no_edge", 0)
            council_val = e.get("no_score", 0)
        else:
            rec_style = "[yellow]PASS[/yellow]"
            edge_val = max(e.get("edge", 0), e.get("no_edge", 0))
            council_val = e.get("final_score", 0)

        ts = e.get("timestamp", "")[:16].replace("T", " ")

        edge_sign = "+" if edge_val >= 0 else ""
        table.add_row(
            str(i),
            question,
            f"{e.get('yes_price_at_run', 0):.0%}",
            f"{council_val:.0%}",
            f"{edge_sign}{edge_val:.1%}",
            rec_style,
            e.get("confidence_level", "?"),
            ts,
        )

    console.print(table)


if __name__ == "__main__":
    app()
