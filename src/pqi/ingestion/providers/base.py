"""Provider-agnostic structured-match layer.

Defines the `StructuredMatchProvider` adapter interface plus the two normalized
intermediate types every provider maps its raw payload into:

  - `CandidateMatch`  — one schedulable match in the resolution window.
  - `CompletedMatch`  — one finished match, used for H2H + recent-form.

The shared resolution skeleton (fuzzy-match the right candidate, gate on
confidence, fetch H2H + form in parallel, build the `StructuredMatchRecord`)
lives here ONCE as `StructuredMatchProvider.resolve`. Each concrete provider
implements only the divergent hooks (`covers`, `list_candidates`,
`fetch_form`, `fetch_h2h`) and the mapping from its raw HTTP payload into the
two intermediates above.

Adding a provider therefore touches only its own module — no downstream
consumer (synthesizer, council, history, cache) changes, because they all read
the provider-agnostic `StructuredMatchRecord`. This is the OCP/DIP half of the
adapter design described in docs/soccer_structured_data_plan.md.

Convention note (CLAUDE.md: "never pass raw dicts between modules"): raw
provider dicts stay INSIDE each adapter; only the typed `CandidateMatch` /
`CompletedMatch` cross the boundary into this shared skeleton.
"""

from __future__ import annotations

import abc
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Literal

import httpx
from rapidfuzz import fuzz

from pqi.models import H2HMatch, MarketData, StructuredMatchRecord, TeamForm

logger = logging.getLogger(__name__)

MatchStatus = Literal["scheduled", "running", "finished", "canceled"]
MatchResult = Literal["win", "loss", "draw"]


# ---------------------------------------------------------------------------
# Normalized intermediates (the adapter boundary types)
# ---------------------------------------------------------------------------


@dataclass
class CandidateMatch:
    """A single schedulable match returned by a provider's window query,
    normalized across providers. The fuzzy matcher scores `team_a_name` /
    `team_b_name` against the market's two parties; everything else feeds the
    final `StructuredMatchRecord`."""

    provider_match_id: str
    team_a_name: str
    team_b_name: str
    scheduled_at: datetime
    status: MatchStatus
    team_a_id: str | None = None
    team_b_id: str | None = None
    match_format: str | None = None
    tournament_name: str | None = None
    final_score: str | None = None
    winner_name: str | None = None
    game: str | None = None
    """Provider's fine game/competition slug for `StructuredMatchRecord.game`
    (e.g. "csgo", "soccer"). Falls back to the provider name when None."""


@dataclass
class CompletedMatch:
    """A single finished match, normalized across providers. Used to build a
    team's recent-form snapshot and the head-to-head list."""

    provider_match_id: str
    scheduled_at: datetime
    team_a_id: str | None
    team_a_name: str
    team_b_id: str | None
    team_b_name: str
    score_summary: str
    winner_id: str | None = None
    winner_name: str | None = None
    is_draw: bool = False
    tournament_name: str | None = None

    def result_for(self, team_id: str | int | None) -> MatchResult:
        """Outcome of this match from the given team's perspective."""
        if self.is_draw or self.winner_id is None:
            return "draw"
        return "win" if str(self.winner_id) == str(team_id) else "loss"


# ---------------------------------------------------------------------------
# Provider interface + shared resolution skeleton
# ---------------------------------------------------------------------------


class StructuredMatchProvider(abc.ABC):
    """Adapter interface for one structured-data source.

    Concrete providers (`PandaScoreProvider`, `FootballDataProvider`) implement
    the abstract members; the `resolve` template method is shared and must not
    be overridden.
    """

    #: Stored on `StructuredMatchRecord.provider` (stable machine id).
    name: str = "base"
    #: Human label rendered to the council. Kept distinct from `name` so the
    #: esports STRUCTURED MATCH DATA block stays byte-identical ("PandaScore").
    display_name: str = "Structured Data"

    # --- capability / config (implemented by subclasses) -------------------

    @property
    @abc.abstractmethod
    def enabled(self) -> bool:
        """True when this provider is switched on AND has a usable key."""

    @property
    @abc.abstractmethod
    def fuzzy_threshold(self) -> int: ...

    @property
    @abc.abstractmethod
    def window_hours(self) -> float: ...

    @property
    @abc.abstractmethod
    def form_count(self) -> int: ...

    @abc.abstractmethod
    def covers(self, category: str | None) -> str | None:
        """Return an internal routing key (e.g. a game slug, or 'soccer') when
        this provider handles the market's category, else None."""

    @abc.abstractmethod
    def make_client(self) -> httpx.AsyncClient:
        """Return a fresh, auth-configured client. Caller owns `async with`."""

    # --- divergent fetch hooks (implemented by subclasses) -----------------

    @abc.abstractmethod
    async def list_candidates(
        self,
        client: httpx.AsyncClient,
        key: str,
        center: datetime,
        window_hours: float,
    ) -> list[CandidateMatch]:
        """Matches scheduled within ±window_hours of `center` for routing `key`."""

    @abc.abstractmethod
    async def fetch_form(
        self,
        client: httpx.AsyncClient,
        team_id: str | int,
        count: int,
    ) -> list[CompletedMatch]:
        """A team's most recent finished matches (newest first)."""

    @abc.abstractmethod
    async def fetch_h2h(
        self,
        client: httpx.AsyncClient,
        candidate: CandidateMatch,
        count: int,
    ) -> list[CompletedMatch]:
        """Recent finished encounters between the candidate's two parties.
        Providers differ: some key off team ids, some off the match id —
        hence the whole `candidate` is passed."""

    # --- shared template method (DO NOT override) --------------------------

    async def resolve(
        self,
        market: MarketData,
        key: str,
        party_a: str,
        party_b: str,
        on_step: "Callable[[str], None] | None" = None,
    ) -> StructuredMatchRecord | None:
        """Resolve a market to a StructuredMatchRecord via this provider.

        `key` is the value returned by `covers(market.category)` (passed in so
        the orchestrator doesn't recompute it). `market.event_start_time` must
        be set — the caller guarantees this. `on_step`, when given, receives
        short progress strings for the CLI to narrate (cosmetic only). Every
        degradation path returns None; sub-fetch failures degrade to empty
        form/H2H, never to a raise.
        """
        if market.event_start_time is None:  # defensive; orchestrator guards too
            return None

        def _step(text: str) -> None:
            if on_step is not None:
                on_step(text)

        async with self.make_client() as client:
            _step(f"querying {self.display_name}…")
            candidates = await self.list_candidates(
                client, key, market.event_start_time, self.window_hours
            )
            if not candidates:
                logger.info(
                    "%s: no candidates in ±%.1fh of %s for market %s",
                    self.name, self.window_hours,
                    market.event_start_time.isoformat(), market.id,
                )
                return None

            best = self._pick_best_candidate(party_a, party_b, candidates)
            if best is None:
                return None
            cand, score = best
            if score < self.fuzzy_threshold:
                logger.info(
                    "%s: best candidate score %d below threshold %d for market "
                    "%s (parties %r vs %r)",
                    self.name, score, self.fuzzy_threshold,
                    market.id, party_a, party_b,
                )
                return None

            _step("gathering head-to-head + recent form…")
            h2h_raw, a_raw, b_raw = await asyncio.gather(
                self.fetch_h2h(client, cand, 10),
                self._form_or_empty(client, cand.team_a_id),
                self._form_or_empty(client, cand.team_b_id),
            )

        record = self._build_record(cand, score, h2h_raw, a_raw, b_raw)
        logger.info(
            "%s: market %s → %s (%s vs %s, status=%s, fuzzy=%d)",
            self.name, market.id, record.provider_match_id,
            record.team_a_name, record.team_b_name, record.status, score,
        )
        return record

    async def _form_or_empty(
        self, client: httpx.AsyncClient, team_id: str | None
    ) -> list[CompletedMatch]:
        if team_id is None:
            return []
        return await self.fetch_form(client, team_id, self.form_count)

    # --- shared scoring + record building ----------------------------------

    @staticmethod
    def _score_candidate(party_a: str, party_b: str, cand: CandidateMatch) -> int:
        """Fuzzy-match the two market parties against a candidate's two team
        names. Score = min(best_a, best_b) so a single coincidental team match
        can't drag a wrong fixture across the threshold. Order-agnostic, so
        home/away vs YES/NO ordering doesn't matter."""
        names = [cand.team_a_name, cand.team_b_name]
        best_a = max(fuzz.token_set_ratio(party_a, n) for n in names)
        best_b = max(fuzz.token_set_ratio(party_b, n) for n in names)
        return int(round(min(best_a, best_b)))

    def _pick_best_candidate(
        self, party_a: str, party_b: str, candidates: list[CandidateMatch]
    ) -> tuple[CandidateMatch, int] | None:
        scored = [(c, self._score_candidate(party_a, party_b, c)) for c in candidates]
        scored = [s for s in scored if s[1] > 0]
        if not scored:
            return None
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[0]

    @staticmethod
    def _build_team_form(
        team_name: str, completed: list[CompletedMatch], team_id: str | None
    ) -> TeamForm | None:
        """Reduce finished matches to a W/D/L snapshot. None when team_id is
        unknown or no completed matches are usable."""
        if team_id is None or not completed:
            return None
        wins = sum(1 for m in completed if m.result_for(team_id) == "win")
        losses = sum(1 for m in completed if m.result_for(team_id) == "loss")
        draws = sum(1 for m in completed if m.result_for(team_id) == "draw")
        if wins + losses + draws == 0:
            return None
        return TeamForm(
            team_name=team_name,
            matches_considered=wins + losses + draws,
            wins=wins,
            losses=losses,
            draws=draws,
            last_match_at=completed[0].scheduled_at,
        )

    @staticmethod
    def _build_h2h(completed: list[CompletedMatch], limit: int = 10) -> list[H2HMatch]:
        out: list[H2HMatch] = []
        for m in completed:
            out.append(
                H2HMatch(
                    match_id=m.provider_match_id,
                    scheduled_at=m.scheduled_at,
                    winner_name=m.winner_name,
                    score_summary=m.score_summary,
                    tournament_name=m.tournament_name,
                )
            )
            if len(out) >= limit:
                break
        return out

    def _build_record(
        self,
        cand: CandidateMatch,
        score: int,
        h2h_raw: list[CompletedMatch],
        a_raw: list[CompletedMatch],
        b_raw: list[CompletedMatch],
    ) -> StructuredMatchRecord:
        return StructuredMatchRecord(
            provider=self.name,
            provider_match_id=cand.provider_match_id,
            game=self._record_game(cand),
            scheduled_at=cand.scheduled_at,
            status=cand.status,
            team_a_name=cand.team_a_name,
            team_b_name=cand.team_b_name,
            match_format=cand.match_format,
            tournament_name=cand.tournament_name,
            final_score=cand.final_score if cand.status == "finished" else None,
            winner_name=cand.winner_name if cand.status == "finished" else None,
            h2h=self._build_h2h(h2h_raw, limit=10),
            team_a_recent_form=self._build_team_form(
                cand.team_a_name, a_raw, cand.team_a_id
            ),
            team_b_recent_form=self._build_team_form(
                cand.team_b_name, b_raw, cand.team_b_id
            ),
            fuzzy_score=score,
            fetched_at=datetime.now(timezone.utc),
        )

    def _record_game(self, cand: CandidateMatch) -> str:
        """Value for `StructuredMatchRecord.game`: the candidate's fine
        game/competition slug, falling back to the provider name."""
        return cand.game or self.name
