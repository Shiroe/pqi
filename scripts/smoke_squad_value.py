#!/usr/bin/env python
"""Smoke / regression test for the squad market-value enrichment layer.

Run:  uv run python scripts/smoke_squad_value.py

Part A runs WITH NO NETWORK — pure helpers + the senior-team disambiguation
picker + the live source's `lookup` with its HTTP call monkeypatched to a
schema-shaped `/clubs/search` fixture (the field names match the verified public
instance: results[].{id,name,country,squad,marketValue}). It asserts:
  - EUR formatting + ratio/favorite math + briefing-line rendering;
  - a half-imbalance (one side unresolved) suppresses the line;
  - the soccer category gate (soccer in, esports out);
  - youth/women variants are filtered so "Spain" never resolves to "Spain U21";
  - the orchestrator builds the matchup record and respects the gate.

Part B is a LIVE connectivity check that runs only when TRANSFERMARKT_ENABLED
is set — one real lookup per side — no assertions, just a sanity probe.

Design + decisions: docs/squad_value_enrichment_plan.md
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from pqi import config
from pqi.ingestion import valuation
from pqi.ingestion.valuation import transfermarkt_live as tml
from pqi.models import MarketData


def _market(category: str) -> MarketData:
    return MarketData(
        id="smoke",
        question="Will Spain win?",
        resolution_criteria="x",
        end_date=datetime(2026, 6, 27, tzinfo=timezone.utc),
        yes_price=0.7,
        volume_usd=1000.0,
        category=category,
    )


# Schema-shaped /clubs/search fixture (senior + youth variants).
_SEARCH_FIXTURE = {
    "Spain": {"results": [
        {"id": 3375, "name": "Spain", "country": "Spain", "squad": 26, "marketValue": 1_220_000_000},
        {"id": 9567, "name": "Spain U21", "country": "Spain", "squad": 23, "marketValue": 275_700_000},
        {"id": 12609, "name": "Spain U19", "country": "Spain", "squad": 21, "marketValue": 58_200_000},
    ]},
    "Uruguay": {"results": [
        {"id": 3500, "name": "Uruguay", "country": "Uruguay", "squad": 26, "marketValue": 359_300_000},
    ]},
    "Nowhere": {"results": []},
}


class _FakeResp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Monkeypatch target for httpx.AsyncClient inside transfermarkt_live."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, *a, **k):
        team = url.rstrip("/").rsplit("/", 1)[-1].replace("%20", " ")
        return _FakeResp(_SEARCH_FIXTURE.get(team, {"results": []}))


async def part_a() -> None:
    print("=== Part A — offline (helpers + picker + mocked lookup) ===")

    # EUR formatting
    assert valuation.format_eur(1_220_000_000) == "€1.22bn"
    assert valuation.format_eur(359_300_000) == "€359M"
    assert valuation.format_eur(None) == "unknown"

    # ratio / favorite
    from pqi.models import TeamValuation
    a = TeamValuation(name="Spain", market_value_eur=1_220_000_000, source="x")
    b = TeamValuation(name="Uruguay", market_value_eur=359_300_000, source="x")
    sv = valuation.build_squad_valuation(a, b, "x")
    assert sv.value_ratio == 3.4 and sv.favorite_name == "Spain", sv
    line = valuation.render_briefing_line(sv)
    assert line and "noisy strength proxy" in line and "3.4x" in line, line
    print("  [ok] formatting + ratio + briefing line")

    # half-imbalance suppressed
    c = TeamValuation(name="Mystery", market_value_eur=None, source="x")
    sv2 = valuation.build_squad_valuation(a, c, "x")
    assert sv2.value_ratio is None and valuation.render_briefing_line(sv2) is None
    print("  [ok] half-imbalance suppressed (no line)")

    # category gate
    assert valuation.category_is_soccer("fifa-world-cup") and valuation.category_is_soccer("soccer")
    assert not valuation.category_is_soccer("cs2") and not valuation.category_is_soccer(None)
    print("  [ok] soccer category gate")

    # youth filter in the picker
    chosen = tml._pick_senior_team(_SEARCH_FIXTURE["Spain"]["results"], "Spain", 80)
    assert chosen and chosen["name"] == "Spain", chosen
    print("  [ok] senior-team picker excludes U21/U19")

    # mocked live lookup + orchestrator (no network)
    import httpx
    orig = httpx.AsyncClient
    httpx.AsyncClient = _FakeClient  # type: ignore[assignment]
    try:
        # cache off the smoke run so we exercise the parse path, not a stale hit
        cache_path = config.TRANSFERMARKT_VALUE_CACHE_PATH
        config.TRANSFERMARKT_VALUE_CACHE_PATH = "/tmp/_smoke_tm_cache.json"
        import os
        if os.path.exists("/tmp/_smoke_tm_cache.json"):
            os.remove("/tmp/_smoke_tm_cache.json")

        sv3 = await valuation.resolve_squad_valuation(_market("fifa-world-cup"), ("Spain", "Uruguay"))
        assert sv3 and sv3.value_ratio == 3.4 and sv3.favorite_name == "Spain", sv3
        assert sv3.team_a.market_value_eur == 1_220_000_000
        assert sv3.team_b.market_value_eur == 359_300_000
        print("  [ok] orchestrator builds matchup record (mocked)")

        # esports gate → None (no lookups attempted)
        sv4 = await valuation.resolve_squad_valuation(_market("cs2"), ("FaZe", "NAVI"))
        assert sv4 is None
        print("  [ok] esports gate returns None")

        # unknown team → that side unresolved, line suppressed
        sv5 = await valuation.resolve_squad_valuation(_market("soccer"), ("Spain", "Nowhere"))
        assert sv5 is not None and sv5.value_ratio is None, sv5
        print("  [ok] one-side-missing → ratio None")
    finally:
        httpx.AsyncClient = orig  # type: ignore[assignment]
        config.TRANSFERMARKT_VALUE_CACHE_PATH = cache_path

    print("  Part A PASS\n")


async def part_b() -> None:
    print("=== Part B — live probe (public instance) ===")
    if not config.TRANSFERMARKT_ENABLED:
        print("  TRANSFERMARKT_ENABLED is false — skipping live probe.\n")
        return
    src = tml.TransfermarktLiveSource()
    for name in ("France", "Norway"):
        tv = await src.lookup(name)
        if tv is None:
            print(f"  {name:<10} → (no resolution)")
        else:
            print(f"  {name:<10} → {valuation.format_eur(tv.market_value_eur)} (squad {tv.squad_size})")
    print("  Part B done (no assertions).\n")


async def main() -> None:
    print("\n=== pqi squad-value enrichment smoke ===\n")
    await part_a()
    await part_b()
    print("✓ smoke complete.")


if __name__ == "__main__":
    asyncio.run(main())
