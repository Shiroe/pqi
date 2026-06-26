#!/usr/bin/env python
"""Diagnostic for the football-data.org window query — why did /matches return
no candidates for a known World Cup fixture?

Run:  uv run python scripts/diag_footballdata.py [YYYY-MM-DD]

Makes a handful of live calls (respect the ~10/min free limit). Prints the raw
shape so we can see whether the free tier returns World Cup matches from the
cross-competition /matches endpoint or whether we must hit
/competitions/{code}/matches instead.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from pqi import config
from pqi.ingestion import footballdata


async def main() -> None:
    if not config.FOOTBALL_DATA_API_KEY:
        print("FOOTBALL_DATA_API_KEY not set in .env — cannot run.")
        return

    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    date_from = d0.strftime("%Y-%m-%d")
    date_to = (d0 + timedelta(days=2)).strftime("%Y-%m-%d")
    print(f"Querying window {date_from} .. {date_to}\n")

    async with footballdata.make_client() as client:
        # 1) What competitions does this token see?
        comps = await footballdata._get(client, "/competitions")
        if isinstance(comps, dict):
            lst = comps.get("competitions") or []
            print(f"[/competitions] token sees {len(lst)} competitions:")
            for c in lst:
                print(f"    {c.get('code'):6} {c.get('name')}")
        print()

        # 2) Cross-competition /matches with date range (what the adapter uses)
        data = await footballdata._get(
            client, "/matches", {"dateFrom": date_from, "dateTo": date_to}
        )
        if isinstance(data, dict):
            rs = data.get("resultSet") or {}
            matches = data.get("matches") or []
            print(f"[/matches?dateFrom&dateTo] resultSet={rs}  matches={len(matches)}")
            for m in matches[:12]:
                comp = (m.get("competition") or {}).get("name")
                h = (m.get("homeTeam") or {}).get("name")
                a = (m.get("awayTeam") or {}).get("name")
                print(f"    {m.get('utcDate')}  [{comp}]  {h} vs {a}  ({m.get('status')})")
        else:
            print("[/matches] returned non-dict / None")
        print()

        # 3) World Cup competition endpoint, same window
        wc = await footballdata._get(
            client, "/competitions/WC/matches",
            {"dateFrom": date_from, "dateTo": date_to},
        )
        if isinstance(wc, dict):
            matches = wc.get("matches") or []
            print(f"[/competitions/WC/matches] matches={len(matches)}")
            for m in matches[:12]:
                h = (m.get("homeTeam") or {}).get("name")
                a = (m.get("awayTeam") or {}).get("name")
                print(f"    {m.get('utcDate')}  {h} vs {a}  ({m.get('status')})")
        else:
            print("[/competitions/WC/matches] returned non-dict / None (maybe not in free tier under code 'WC')")


if __name__ == "__main__":
    asyncio.run(main())
