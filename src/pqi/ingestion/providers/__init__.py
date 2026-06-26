"""Structured-match provider adapters.

Public surface:
  - `StructuredMatchProvider` — the adapter interface (base.py).
  - `CandidateMatch` / `CompletedMatch` — normalized boundary types.
  - `get_providers()` — the ordered provider registry the orchestrator iterates.
"""

from __future__ import annotations

from pqi.ingestion.providers.base import (
    CandidateMatch,
    CompletedMatch,
    StructuredMatchProvider,
)
from pqi.ingestion.providers.footballdata_provider import FootballDataProvider
from pqi.ingestion.providers.pandascore_provider import PandaScoreProvider

__all__ = [
    "CandidateMatch",
    "CompletedMatch",
    "StructuredMatchProvider",
    "FootballDataProvider",
    "PandaScoreProvider",
    "get_providers",
    "provider_display_name",
]


def provider_display_name(name: str) -> str:
    """Human label for a provider id (`StructuredMatchRecord.provider`), used in
    the council / synthesizer renders. Derived from the registry so it stays in
    one place; falls back to the raw id for unknown providers."""
    for provider in get_providers():
        if provider.name == name:
            return provider.display_name
    return name


def get_providers() -> list[StructuredMatchProvider]:
    """Registry of structured-match providers in priority order.

    Esports (PandaScore) first, then soccer (football-data). Order only matters
    if two providers ever claim the same category — today their `covers()`
    domains are disjoint, so the first match always wins cleanly. New providers
    are added here and nowhere else.
    """
    return [PandaScoreProvider(), FootballDataProvider()]
