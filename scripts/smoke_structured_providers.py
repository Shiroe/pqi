#!/usr/bin/env python
"""Smoke / regression test for the structured-match provider adapter layer.

Run:  uv run python scripts/smoke_structured_providers.py

Part A + B run WITH NO NETWORK: the low-level HTTP clients are monkeypatched
with schema-shaped fixtures (mirroring the verified PandaScore and
football-data.org v4 field names — not fabricated data, just representative
payloads), and the full `resolve_match` path is exercised end-to-end. This
asserts:
  - the esports (PandaScore) record is unchanged by the refactor — regression
    guard for the clean-adapter migration;
  - the soccer (football-data) record maps correctly, including DRAWS in the
    form snapshot and the W-D-L semantics.

Part C is a LIVE connectivity check that runs only when the relevant API key
is configured in the environment / .env. It makes one real window query per
configured provider and prints what came back — no assertions, just a sanity
probe you can run once a key lands.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from pqi import config
from pqi.ingestion import footballdata, pandascore
from pqi.models import MarketData


# ---------------------------------------------------------------------------
# Schema-shaped fixtures (field names per each provider's documented schema)
# ---------------------------------------------------------------------------

_CENTER = datetime(2026, 6, 26, 19, 0, tzinfo=timezone.utc)

# --- PandaScore (esports) — shape per ingestion/pandascore.py expectations ---
PS_SCHEDULED = {
    "id": 99001,
    "status": "not_started",
    "scheduled_at": "2026-06-26T19:00:00Z",
    "number_of_games": 3,
    "opponents": [
        {"opponent": {"id": 1, "name": "Team Alpha"}},
        {"opponent": {"id": 2, "name": "Team Beta"}},
    ],
    "tournament": {"name": "Test Masters"},
    "results": [{"score": 0, "team_id": 1}, {"score": 0, "team_id": 2}],
    "winner": None,
}
PS_FINISHED_A = {  # Alpha beat Beta 2-1
    "id": 98001,
    "status": "finished",
    "scheduled_at": "2026-06-20T19:00:00Z",
    "opponents": [
        {"opponent": {"id": 1, "name": "Team Alpha"}},
        {"opponent": {"id": 2, "name": "Team Beta"}},
    ],
    "tournament": {"name": "Prior Cup"},
    "results": [{"score": 2, "team_id": 1}, {"score": 1, "team_id": 2}],
    "winner": {"id": 1, "name": "Team Alpha"},
}

# --- football-data.org (soccer) — shape per v4 Match schema -----------------
FD_SCHEDULED = {
    "id": 555001,
    "utcDate": "2026-06-26T19:00:00Z",
    "status": "TIMED",
    "competition": {"name": "FIFA World Cup"},
    "homeTeam": {"id": 770, "name": "Norway"},
    "awayTeam": {"id": 773, "name": "France"},
    "score": {"winner": None, "fullTime": {"home": None, "away": None}},
}
FD_FINISHED_HOME = {  # Norway won 2-1
    "id": 554001,
    "utcDate": "2026-06-18T19:00:00Z",
    "status": "FINISHED",
    "competition": {"name": "UEFA Nations League"},
    "homeTeam": {"id": 770, "name": "Norway"},
    "awayTeam": {"id": 773, "name": "France"},
    "score": {"winner": "HOME_TEAM", "fullTime": {"home": 2, "away": 1}},
}
FD_FINISHED_DRAW = {  # 1-1 draw
    "id": 554002,
    "utcDate": "2026-06-10T19:00:00Z",
    "status": "FINISHED",
    "competition": {"name": "Friendly"},
    "homeTeam": {"id": 770, "name": "Norway"},
    "awayTeam": {"id": 773, "name": "France"},
    "score": {"winner": "DRAW", "fullTime": {"home": 1, "away": 1}},
}


def _aret(value):
    async def _coro(*_a, **_k):
        return value
    return _coro


def _market(*, question: str, category: str, event_title: str | None) -> MarketData:
    return MarketData(
        id="m-test",
        question=question,
        resolution_criteria="",
        end_date=_CENTER,
        event_start_time=_CENTER,
        yes_price=0.5,
        volume_usd=1000.0,
        category=category,
        event_title=event_title,
    )


# ---------------------------------------------------------------------------
# Part A — esports regression
# ---------------------------------------------------------------------------


async def part_a() -> None:
    import pqi.ingestion.match_resolver as mr

    config.PANDASCORE_API_KEY = "test"
    config.PANDASCORE_ENABLED = True
    pandascore.list_matches_in_window = _aret([PS_SCHEDULED])  # type: ignore[assignment]
    pandascore.get_team_past_matches = _aret([PS_FINISHED_A])  # type: ignore[assignment]
    pandascore.get_h2h = _aret([PS_FINISHED_A])  # type: ignore[assignment]

    market = _market(
        question="LoL: Team Alpha vs Team Beta (BO3) - Test Masters",
        category="lol-test-masters-2026",
        event_title="LoL: Team Alpha vs Team Beta (BO3) - Test Masters",
    )
    rec = await mr.resolve_match(market)
    assert rec is not None, "esports resolution returned None"
    assert rec.provider == "pandascore", rec.provider
    assert rec.game == "lol", rec.game
    assert rec.team_a_name == "Team Alpha" and rec.team_b_name == "Team Beta"
    assert rec.status == "scheduled", rec.status
    assert rec.match_format == "BO3", rec.match_format
    assert rec.fuzzy_score == 100, rec.fuzzy_score
    assert rec.team_a_recent_form is not None
    fa = rec.team_a_recent_form
    assert (fa.wins, fa.draws, fa.losses) == (1, 0, 0), (fa.wins, fa.draws, fa.losses)
    assert fa.matches_considered == 1
    assert len(rec.h2h) == 1 and rec.h2h[0].score_summary == "2-1"

    # Council render must stay byte-identical for esports (W-L, no draws).
    from pqi.agents.council import _format_form
    assert _format_form(fa) == "1W-0L (last 1 matches)", _format_form(fa)
    print("Part A (esports regression): PASS")


# ---------------------------------------------------------------------------
# Part B — soccer mapping incl. draws
# ---------------------------------------------------------------------------


async def part_b() -> None:
    import pqi.ingestion.match_resolver as mr

    config.FOOTBALL_DATA_API_KEY = "test"
    config.FOOTBALL_DATA_ENABLED = True
    footballdata.list_matches_in_window = _aret([FD_SCHEDULED])  # type: ignore[assignment]
    footballdata.get_team_matches = _aret(  # type: ignore[assignment]
        [FD_FINISHED_HOME, FD_FINISHED_DRAW]
    )
    footballdata.get_head2head = _aret(  # type: ignore[assignment]
        [FD_FINISHED_HOME, FD_FINISHED_DRAW]
    )

    # Soccer per-market question carries only one team — parties must come from
    # the event title fallback.
    market = _market(
        question="Will Norway win on 2026-06-26?",
        category="soccer-fifwc",
        event_title="Norway vs. France",
    )
    rec = await mr.resolve_match(market)
    assert rec is not None, "soccer resolution returned None"
    assert rec.provider == "football-data", rec.provider
    assert rec.game == "soccer", rec.game
    assert rec.team_a_name == "Norway" and rec.team_b_name == "France"
    assert rec.status == "scheduled", rec.status
    assert rec.match_format is None
    assert rec.tournament_name == "FIFA World Cup", rec.tournament_name
    assert rec.fuzzy_score == 100, rec.fuzzy_score

    # Form: Norway has 1 win + 1 draw across the two fixtures → 1W-1D-0L.
    fa = rec.team_a_recent_form
    assert fa is not None
    assert (fa.wins, fa.draws, fa.losses) == (1, 1, 0), (fa.wins, fa.draws, fa.losses)
    assert fa.matches_considered == 2
    # France: 1 loss + 1 draw → 0W-1D-1L.
    fb = rec.team_b_recent_form
    assert fb is not None
    assert (fb.wins, fb.draws, fb.losses) == (0, 1, 1), (fb.wins, fb.draws, fb.losses)

    # H2H includes the draw, rendered with a null winner.
    draw_h2h = [h for h in rec.h2h if h.score_summary == "1-1"]
    assert draw_h2h and draw_h2h[0].winner_name is None

    from pqi.agents.council import _format_form
    assert _format_form(fa) == "1W-1D-0L (last 2 matches)", _format_form(fa)
    print("Part B (soccer mapping + draws): PASS")


# ---------------------------------------------------------------------------
# Part C — live connectivity (only when a key is configured)
# ---------------------------------------------------------------------------


async def part_c() -> None:
    import importlib

    importlib.reload(config)  # pick up real .env values (Parts A/B mutated config)
    now = datetime.now(timezone.utc)

    if config.FOOTBALL_DATA_API_KEY:
        async with footballdata.make_client() as client:
            matches = await footballdata.list_matches_in_window(client, now, 36.0)
        print(f"Part C (football-data live): {len(matches)} matches in ±36h window")
        for m in matches[:5]:
            h = (m.get("homeTeam") or {}).get("name")
            a = (m.get("awayTeam") or {}).get("name")
            print(f"   {m.get('utcDate')}  {h} vs {a}  [{m.get('status')}]")
    else:
        print("Part C (football-data live): SKIPPED — FOOTBALL_DATA_API_KEY not set")


async def main() -> None:
    await part_a()
    await part_b()
    await part_c()
    print("\nAll structured-provider smoke checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
