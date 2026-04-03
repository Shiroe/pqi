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
    table.add_column("Question", style="white", max_width=52)
    table.add_column("Resolves", justify="right", width=9)
    table.add_column("YES", justify="right", width=6)
    table.add_column("NO", justify="right", width=6)
    table.add_column("Volume (USD)", justify="right", width=14)

    for i, m in enumerate(markets, start=1):
        question = m.question if len(m.question) <= 52 else m.question[:49] + "..."
        table.add_row(
            str(i),
            question,
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

    if rec == "BUY YES":
        colour = "green"
        trade_line = f"[bold green]>>> BUY YES @ ${yes_price:.2f}/share <<<[/bold green]"
    elif rec == "BUY NO":
        colour = "red"
        trade_line = f"[bold red]>>> BUY NO @ ${no_price:.2f}/share <<<[/bold red]"
    else:
        colour = "yellow"
        trade_line = "[bold yellow]>>> PASS — no actionable edge <<<[/bold yellow]"

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


async def _run_analysis(market: MarketData) -> CouncilResult:
    """Execute the full analysis pipeline for one market."""
    from pqi.ingestion.synthesizer import build_briefing
    from pqi.agents.council import run_council
    from pqi.scoring import build_council_result

    console.print(f"\n[cyan]Fetching briefing for:[/cyan] {market.question[:80]}")
    briefing = await build_briefing(market)
    console.print(f"[cyan]Briefing ready — {len(briefing.key_facts)} key facts.[/cyan]")

    concurrency_info = f"concurrency={config.LOCAL_LLM_CONCURRENCY}" if config.LLM_BACKEND == "ollama" else "parallel"
    console.print(f"[cyan]Summoning the Council of Seven… ({concurrency_info})[/cyan]")
    verdicts = await run_council(briefing)
    console.print(f"[cyan]Council complete: {len(verdicts)}/7 verdicts.[/cyan]\n")

    return build_council_result(market, verdicts)


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
    resolve_within: Optional[int] = typer.Option(
        None, "--resolve-within", "-r",
        help="Only show markets that resolve within this many hours (e.g. 48).",
        min=1,
    ),
    min_volume: Optional[float] = typer.Option(
        None, "--min-volume", "-v",
        help="Override minimum volume floor in USD (use 0 to see all markets).",
        min=0,
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
            volume_floor_override=min_volume,
        ))
    except CategoryNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if not markets:
        msg = "No markets found matching your filters."
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
    if resolve_within is not None:
        filter_desc.append(f"within {resolve_within}h")
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

    console.print(f"\n[bold]Analysing:[/bold] {market.question}\n")

    result = asyncio.run(_run_analysis(market))
    display_council_result(result)


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


if __name__ == "__main__":
    app()
