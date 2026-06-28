"""Squad market-value enrichment — public surface + orchestrator.

A parallel enrichment dimension to `ingestion/providers/` (structured match
data). `resolve_squad_valuation` is the thin, priority-ordered fallback loop the
synthesizer calls; concrete sources implement `TeamValuationSource`.

Phase 1 ships one source — the live public-instance Transfermarkt scraper.
Route C's end state prepends a local dataset-snapshot source (Phase 2) as the
primary, with live as the fallback; the registry is the only place that changes.

Design + decisions: docs/squad_value_enrichment_plan.md.
"""

from __future__ import annotations

import logging
from typing import Callable

from pqi.ingestion.valuation.base import (
    TeamValuationSource,
    build_squad_valuation,
    category_is_soccer,
    format_eur,
    render_briefing_line,
)
from pqi.ingestion.valuation.transfermarkt_live import TransfermarktLiveSource
from pqi.models import MarketData, SquadValuation, TeamValuation

logger = logging.getLogger(__name__)

__all__ = [
    "TeamValuationSource",
    "build_squad_valuation",
    "category_is_soccer",
    "format_eur",
    "render_briefing_line",
    "TransfermarktLiveSource",
    "get_valuation_sources",
    "resolve_squad_valuation",
]


def get_valuation_sources() -> list[TeamValuationSource]:
    """Registry of squad-value sources in priority order. Phase 2 prepends the
    dataset-snapshot source here and nowhere else."""
    return [TransfermarktLiveSource()]


async def _resolve_team(
    name: str, sources: list[TeamValuationSource]
) -> TeamValuation | None:
    """First source that returns a team WITH a positive value wins (fallback
    chain). Returns None when every source misses."""
    for source in sources:
        try:
            tv = await source.lookup(name)
        except Exception as exc:  # non-fatal: a broken source must not crash analysis
            logger.warning("valuation source %s failed for %r: %s", source.name, name, exc)
            continue
        if tv is not None and tv.market_value_eur:
            return tv
    return None


async def resolve_squad_valuation(
    market: MarketData,
    matchup: tuple[str, str] | None,
    on_step: Callable[[str], None] | None = None,
) -> SquadValuation | None:
    """Resolve the squad-value comparison for the market's fixture, or None.

    Non-fatal end to end. Returns None when: no matchup is known, the category
    isn't soccer, no source is enabled, or neither side resolves to a value.
    The two team lookups run sequentially to respect the live instance's rate
    limit (the per-team cache makes repeat teams free)."""
    if matchup is None:
        return None
    sources = [
        s for s in get_valuation_sources() if s.enabled and s.covers(market.category)
    ]
    if not sources:
        return None

    name_a, name_b = matchup
    if on_step is not None:
        on_step(f"checking squad market values via {sources[0].display_name}…")

    tv_a = await _resolve_team(name_a, sources)
    tv_b = await _resolve_team(name_b, sources)
    if tv_a is None and tv_b is None:
        return None

    src = (tv_a or tv_b).source  # type: ignore[union-attr]
    tv_a = tv_a or TeamValuation(name=name_a, source=src)
    tv_b = tv_b or TeamValuation(name=name_b, source=src)
    return build_squad_valuation(tv_a, tv_b, source=src)
