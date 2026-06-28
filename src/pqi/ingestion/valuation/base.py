"""Squad market-value enrichment — provider-agnostic core.

A parallel enrichment dimension to the structured-match `providers/` layer.
Where `StructuredMatchProvider` answers "what is the match?", a
`TeamValuationSource` answers "how strong are the two squads, by market value?"
— a team-level, slow-moving attribute, kept separate from the match record
(SRP/DIP).

This module holds the adapter interface plus the pure, network-free helpers
(soccer gate, EUR formatting, ratio/favorite computation, briefing-line render)
so they can be unit-tested without hitting any source. Concrete sources live in
sibling modules (`transfermarkt_live.py`; later `transfermarkt_dataset.py`).

Design + decisions: docs/squad_value_enrichment_plan.md.
"""

from __future__ import annotations

import abc

from pqi.models import SquadValuation, TeamValuation

# Soccer category gate. Intentionally a small local copy rather than importing
# FootballDataProvider's tokens: that set governs *match-data* routing, this one
# governs *valuation* routing — coupling them would make one feature's category
# tweak silently move the other. Keep them independent (KISS over premature DRY).
_SOCCER_CATEGORY_TOKENS = (
    "soccer",
    "football",
    "fifa-world-cup",
    "world-cup",
    "fifwc",
    "premier-league",
    "laliga",
    "la-liga",
    "serie-a",
    "bundesliga",
    "ligue-1",
    "champions-league",
    "euro",
)


def category_is_soccer(category: str | None) -> bool:
    """True when a market category looks like soccer (the only domain
    Transfermarkt covers for national teams today)."""
    if not category:
        return False
    c = category.lower()
    return any(tok in c for tok in _SOCCER_CATEGORY_TOKENS)


class TeamValuationSource(abc.ABC):
    """Adapter interface for one squad-value source.

    Concrete sources own their own client / cache lifecycle inside `lookup`,
    so the orchestrator stays a thin priority-ordered fallback loop.
    """

    #: Stored on `TeamValuation.source` / `SquadValuation.source`.
    name: str = "base"
    #: Human label (currently unused in the briefing line, kept for symmetry
    #: with the structured-match providers and for future render needs).
    display_name: str = "Valuation Source"

    @property
    @abc.abstractmethod
    def enabled(self) -> bool:
        """True when this source is switched on and usable."""

    @property
    @abc.abstractmethod
    def fuzzy_threshold(self) -> int: ...

    def covers(self, category: str | None) -> bool:
        """Whether this source handles the market's category. Default: soccer
        (national teams). Overridable if a future source covers more."""
        return category_is_soccer(category)

    @abc.abstractmethod
    async def lookup(self, team_name: str) -> TeamValuation | None:
        """Resolve one team's squad valuation, or None if not found / below the
        fuzzy threshold / on any source-side failure (non-fatal)."""


# ---------------------------------------------------------------------------
# Pure helpers (network-free, unit-testable)
# ---------------------------------------------------------------------------


def build_squad_valuation(
    team_a: TeamValuation,
    team_b: TeamValuation,
    source: str,
) -> SquadValuation:
    """Assemble the matchup record, computing favorite + ratio.

    `favorite_name` / `value_ratio` are None unless BOTH sides have a positive
    value (a half-imbalance is meaningless and must not be rendered)."""
    va, vb = team_a.market_value_eur, team_b.market_value_eur
    favorite_name: str | None = None
    value_ratio: float | None = None
    if va and vb and va > 0 and vb > 0:
        hi, lo = max(va, vb), min(va, vb)
        value_ratio = round(hi / lo, 2)
        if va != vb:
            favorite_name = team_a.name if va > vb else team_b.name
    return SquadValuation(
        team_a=team_a,
        team_b=team_b,
        favorite_name=favorite_name,
        value_ratio=value_ratio,
        source=source,
    )


def format_eur(value: int | None) -> str:
    """Human-readable EUR amount: €1.22bn / €359M / €4.1M / €900k / unknown."""
    if not value or value <= 0:
        return "unknown"
    if value >= 1_000_000_000:
        return f"€{value / 1_000_000_000:.2f}bn"
    if value >= 1_000_000:
        return f"€{value / 1_000_000:.0f}M"
    if value >= 1_000:
        return f"€{value / 1_000:.0f}k"
    return f"€{value}"


def render_briefing_line(sv: SquadValuation | None) -> str | None:
    """Render the single key_context line for the council, or None to suppress
    (when the ratio is unknown, i.e. a side failed to resolve).

    Deliberately labels the metric a 'noisy proxy' so no agent treats it as
    deterministic — we inform the council, we don't pre-decide for it."""
    if sv is None or sv.value_ratio is None:
        return None
    a, b = sv.team_a, sv.team_b
    fav = (
        f" — favorite {sv.favorite_name} {sv.value_ratio:.1f}x"
        if sv.favorite_name
        else " — evenly matched by value"
    )
    return (
        "Squad market value (Transfermarkt, a noisy strength proxy): "
        f"{a.name} {format_eur(a.market_value_eur)} vs "
        f"{b.name} {format_eur(b.market_value_eur)}{fav}."
    )
