# Squad market-value enrichment — design & plan

**Status:** **Phase 1 implemented** (2026-06-27). Live (public-instance)
Transfermarkt source shipping a single `key_context` briefing line for soccer
national-team fixtures; non-fatal; persisted on the briefing. Phase 0 decisions
locked. Phase 2 (dataset-primary + live fallback) is the next milestone.
**Scope (locked by `/goal`):** Route C (hybrid), **national teams only**,
**briefing-line only** (no scoring / no council-prompt change), live leg on the
**public test instance** for now (documented as a risk). Plus: settle whether
Transfermarkt belongs in the Tavily query params.
**Standards:** SOLID / DRY / KISS + [`docs/CTO_Agent.md`](CTO_Agent.md).
Mirrors the adapter design already shipped in
[`docs/soccer_structured_data_plan.md`](soccer_structured_data_plan.md).

---

## 1. The idea (user)

In a tournament like the World Cup, matchups can be hugely imbalanced in squad
market value (e.g. France ≈ €1.5bn vs Iran ≈ €32M). Market value is a *noisy*
proxy — it doesn't decide a match — but a **large** imbalance carries
information about the likely result. Surface that imbalance to the council as a
qualitative fact and let the seven agents weigh it.

This is explicitly the enrichment that session 48 **parked**: *"Transfermarkt
evaluated + parked (no official API) as a future player-context enrichment
adapter."*

---

## 2. Findings (empirical — all from live calls, June 2026)

### 2.1 football-data.org carries **zero** value data — confirmed

Probed with our key at every level (`/competitions/WC/teams`, `/teams/{id}`,
`/persons/{id}`). A squad player exposes exactly:

```
{id, name, position, dateOfBirth, nationality}
```

No `value` / `marketValue` / `worth` / `price` / `salary` anywhere in the team
or person payload. **So the signal cannot come from our current soccer
provider.** (One adjacent freebie exists — `/competitions/{code}/standings`
gives `points / goalDifference / form / position` for WC groups and the big-5 —
but it is *result-derived* and noisy across three group games, so it does not
capture latent talent gaps. Out of scope here; noted as a possible future
complement.)

### 2.2 The data lives at Transfermarkt — two viable routes, both verified live

**Route A — live FastAPI scraper** (`felipeall/transfermarkt-api`; public test
instance `https://transfermarkt-api.fly.dev`, or self-hostable via Docker).
National teams are modeled as "clubs", and the **aggregate squad value is in the
search result itself** — no per-player summing required:

| `/clubs/search/{name}` result | `squad` | `marketValue` |
|---|---|---|
| Spain (senior) | 26 | 1,220,000,000 |
| Spain U21 | 23 | 275,700,000 |
| Spain U19 | 21 | 58,200,000 |

Endpoints used: `/clubs/search/{name}` (→ id + `marketValue` + `squad` +
`country`), `/clubs/{id}/profile`, `/clubs/{id}/players` (per-player MV),
`/players/{id}/market_value`. Search-by-name maps cleanly onto our existing
rapidfuzz flow.

Live imbalance signal (probed against the current WC slate):

| Matchup | MV | Ratio |
|---|---|---|
| Spain €1,220M vs Uruguay €359M | — | 3.4× |
| France €1,520M vs Norway €590M | — | 2.6× |
| France vs Iran (€32M) | — | **47×** |
| Saudi Arabia €41M vs Cabo Verde €54M | — | 0.7× (even underdog clash) |

⚠️ **Risks (Route A):** it's scraping (Transfermarkt ToS-gray; HTML changes
break it); rate-limited (~2 req / 3s); the hosted instance is explicitly
*"for testing purposes"* — production-grade use implies self-hosting a
container. `/clubs/{id}/players` returned `[]` for the national team on the test
instance during probing — **we do not need it** (the aggregate is in the search
result), but it confirms the instance is imperfect.

**Route B — static dataset** (`dcaribou/transfermarkt-datasets`; weekly,
published to Cloudflare R2 + Kaggle, available as a **DuckDB file** — which we
already depend on). Tables: `clubs` (aggregate MV), `players`
(`market_value_in_eur`), `player_valuations` (historical), and **`national_teams`
with explicit World Cup / Euro / Copa coverage**. Pros: no live scrape in the
hot path; robust; reuses our DuckDB analytics layer; historical values enable
backtests. Cons: ~weekly lag (irrelevant — national-squad value is near-static);
needs a periodic download + a team-name → club-id mapping step.

### 2.3 Why national-team value isn't a Tavily job (answers the Tavily question)

`transfermarkt.com` is **already** a suggested sports domain in the Search
Architect prompt (`src/pqi/ingestion/synthesizer.py:68`), so Tavily can already
scope to it for *qualitative* context (injury/lineup coverage) when the Architect
chooses to. **For the value *number*, Tavily is the wrong tool:**

- The structured API returns a clean, deterministic integer (`€1.22bn`). A Tavily
  scrape of a TM page for that number is lossy, **non-deterministic** (violates
  CTO §2 reproducibility), and costs a search.
- It would duplicate the structured provider — a DRY violation.
- CTO §5: public APIs aren't the moat; the *synthesis* is. A clean structured
  fact injected into the briefing is strictly better than a fuzzy web scrape of
  the same fact.

**Conclusion: no new Tavily work.** TM stays available to the Architect as a
qualitative-context domain (already true); the value comes from the structured
provider. Recorded as Decision D5 below.

---

## 3. Where it belongs (architecture)

Squad value is **team-level and slow-moving** — a different concern from the
**match-level** `StructuredMatchRecord` (status / score / form / H2H). Bolting it
onto `FootballDataProvider` or `StructuredMatchRecord` would conflate two
responsibilities and couple Transfermarkt to the match provider (SRP/DIP
violation). It is a **parallel enrichment dimension**, designed to mirror the
existing `providers/` adapter so the "esports equivalent later" the user
mentioned slots in without reshaping anything.

### 3.1 Pipeline placement

```
Gamma → match_resolver (StructuredMatchRecord, existing)
      → valuation resolver (SquadValuation, NEW, parallel & non-fatal)
      → Synthesizer (BriefingDocument: + squad_valuation, + one key_context line)
      → Council (UNCHANGED — reads the briefing it always reads)
      → Scoring (UNCHANGED)
```

The valuation resolver reuses the **canonical team names** already produced for
the matchup (`structured_match` names → `_resolve_matchup` question/event_title
fallback — session 48), so name extraction is not re-implemented (DRY).

### 3.2 New module (mirrors `src/pqi/ingestion/providers/`)

```
src/pqi/ingestion/valuation/
  __init__.py           registry: get_valuation_sources() + resolve_matchup_value()
  base.py               TeamValuationSource ABC + shared resolve skeleton
  transfermarkt_live.py  live public-instance source  (Phase 1)
  transfermarkt_dataset.py  local DuckDB snapshot source (Phase 2 — Route C primary)
```

- `TeamValuationSource` (ABC): `enabled`, `name`, `display_name`,
  `fuzzy_threshold`; hooks `lookup_team(name) -> TeamValuation | None`. The shared
  skeleton does fuzzy senior-team selection + caching + builds the matchup record.
- Boundary type stays internal (a `@dataclass` like `CandidateMatch`); only the
  **Pydantic** record crosses into the briefing (CLAUDE.md: never pass raw dicts;
  CTO §"contract is law").

### 3.3 Pydantic contract (the load-bearing wall)

Add to `src/pqi/models.py` (+ `docs/models-reference.md`):

```python
class TeamValuation(BaseModel):           # one team
    name: str
    market_value_eur: int | None          # aggregate squad value
    squad_size: int | None
    source: str                            # "transfermarkt-live" | "transfermarkt-dataset"
    as_of: datetime | None                 # snapshot/scrape time (reproducibility)

class SquadValuation(BaseModel):          # the matchup-level record
    team_a: TeamValuation
    team_b: TeamValuation
    favorite_name: str | None             # higher-value side, None if tie/missing
    value_ratio: float | None             # max/min, None if either missing/zero
    source: str
```

`BriefingDocument` gains:

```python
squad_valuation: SquadValuation | None = None   # None when disabled / unresolved
```

### 3.4 Injection (briefing-line only — preserves the reasoning machine)

The synthesizer, after resolving, both:
1. sets `BriefingDocument.squad_valuation` (structured, for observability), and
2. appends **one rendered line** to `key_context` (which the council already
   serializes into every agent's briefing), e.g.:

   > `Squad market value (Transfermarkt, noisy proxy): Spain €1.22bn vs Uruguay €359M — favorite 3.4×.`

This means **zero `council.py` change and zero prompt change** — the strongest
possible read of "don't affect the reasoning machine." (Alternative: a dedicated
render block in `council.py` like `structured_match` has — rejected for v1 as an
unnecessary touch of the council path. See Decision D2.)

> Note we deliberately label it "noisy proxy" in the line so no agent treats it
> as deterministic — consistent with the user's own framing and CTO §"bias as a
> feature" (we inform the agents, we don't pre-decide for them).

---

## 4. Graceful degradation (CTO §4 — a contract, not an accident)

Every failure path → `squad_valuation = None`, pipeline continues, council still
runs:

- `TRANSFERMARKT_ENABLED=false` → skipped.
- Public instance down / 429 / timeout → `None` (non-fatal, logged at warning).
- Team not found / below fuzzy threshold / only youth teams match → that side
  `market_value_eur=None`; if either side is missing, `value_ratio=None` and the
  line is suppressed (we never print a half-imbalance).
- Non-soccer / non-national-team market → resolver no-ops (covered-category gate,
  same pattern as structured providers).

---

## 5. Senior-national-team disambiguation

`/clubs/search/{name}` returns youth/women variants (U21/U19/U17/U16/U23, W).
Selection rule (Phase 1):

1. Filter to results whose `country` is non-null (national teams).
2. Drop names matching age/gender suffixes (`U\d{2}`, ` W`, `Women`, `U23`).
3. Among survivors, prefer exact normalized-name match; tie-break by largest
   `squad` / `marketValue` (the senior side dominates both).
4. Gate on fuzzy score ≥ `TRANSFERMARKT_FUZZY_THRESHOLD`.

Cache the resolved `name → {club_id, value, squad, as_of}` so disambiguation runs
once per team (national-team value is near-static).

---

## 6. Config (CTO §4 config-strictness; pattern from football-data)

| Key | Default | Notes |
|---|---|---|
| `TRANSFERMARKT_ENABLED` | `true` | Master switch (non-fatal). |
| `TRANSFERMARKT_API_BASE` | `https://transfermarkt-api.fly.dev` | **Env-overridable** *on purpose* — the swap to a self-hosted instance is a config change, not a code change (see D3). Documented public-instance dependency. |
| `TRANSFERMARKT_FUZZY_THRESHOLD` | `80` | Mirrors `FOOTBALL_DATA_FUZZY_THRESHOLD`. |
| `TRANSFERMARKT_RATE_LIMIT_DELAY` | `1.6` | ≥ the instance's ~2-req/3s limit. |
| `TRANSFERMARKT_CACHE_TTL_DAYS` | `7` | Value is near-static; long TTL is safe. |
| `TRANSFERMARKT_VALUE_CACHE_PATH` | `<PQI_DATA_DIR>/transfermarkt_value_cache.json` | Same persistence pattern as `category_cache.json`. |

No scoring-affecting knobs (no "min ratio" threshold) — that would be premature
tuning with no data (CTO §2). Render raw numbers + ratio; the agents judge.

---

## 7. Phased plan (each step ships value & is reversible — CTO §7d)

**Phase 0 — this doc + decisions.** (current)

**Phase 1 — live leg, briefing-line only (the realistic goal). ✅ DONE.**
- ✅ `models.py`: `TeamValuation` + `SquadValuation` + `BriefingDocument.squad_valuation` (+ models-reference.md).
- ✅ `src/pqi/ingestion/valuation/` module: `base.py` (`TeamValuationSource` ABC + soccer gate + pure helpers), `transfermarkt_live.py` (public instance + persistent TTL cache + senior-team disambiguation), `__init__.py` (registry + `resolve_squad_valuation` orchestrator).
- ✅ Synthesizer: non-fatal resolve run **concurrently** with the synthesis LLM call; `key_context` line injection at front; reuses `_resolve_matchup` names; sets `squad_valuation`.
- ✅ Config keys + `.env.example` (incl. `TRANSFERMARKT_API_BASE` knob, `TRANSFERMARKT_VALUE_CACHE_PATH`).
- ✅ `scripts/smoke_squad_value.py`: offline (mocked HTTP) assertions + live probe.
- ✅ `main.py`: a `💰` phase-reporter detail line (console observability).
- ✅ Observability (D4): `SquadValuation` rides on `result.briefing` → persisted per run via `append_result` (no `history.py` change needed).
- ✅ **No** `council.py` / `scoring.py` touch. Flag-off ⇒ identical prior behavior.
- Verified live: England €1.36bn vs Panama €35M (39.4×) injected as `key_context[0]`; esports/economics markets correctly skip (gate → None); structured-provider regression smoke still passes.

**Phase 2 — Route C completion (dataset primary + live fallback) + observability.**
- `transfermarkt_dataset.py` reading a local DuckDB snapshot; `scripts/refresh_squad_values.py` downloads/refreshes it; registry orders dataset-first, live-fallback.
- Persist the resolved `SquadValuation` per run (history `run_context` or a sidecar) so we can later correlate **value-imbalance vs realized outcome** — the CTO §9 "did it help?" hook. (Deferred from Phase 1 to keep v1 minimal; flagged here so it isn't forgotten.)

**Phase 3 — DEFERRED, separate sign-off.** Only if Phase 2 observability shows
the imbalance correlates with outcomes beyond price: consider an *explicit*
council-prompt change (Statistician/Specialist weigh it) or a statistical prior.
This touches the reasoning machine and is **out of scope** until the data earns
it.

**Future — esports equivalent.** The `TeamValuationSource` ABC supports it, but
Transfermarkt has no esports coverage; a different value source (roster/prize
value) would be needed. Parked.

---

## 8. SOLID / DRY / KISS scorecard

- **SRP** — valuation is its own module; match-resolver untouched.
- **OCP / DIP** — new source = new class in the registry; consumers depend on the
  Pydantic `SquadValuation`, not on Transfermarkt (mirrors the structured-match
  adapter).
- **DRY** — reuses canonical matchup names (`_resolve_matchup`) and the
  rapidfuzz/cache patterns already in the codebase; the aggregate value is taken
  from the search result (no per-player summing).
- **KISS** — briefing-line only; no scoring/prompt change; one line, two numbers,
  one ratio.
- **Contract is law** — only typed models cross module boundaries.
- **Graceful degradation** — every failure → `None`, pipeline continues.

---

## 9. Decisions (resolved 2026-06-27)

- **D1 — Phase-1 source scope → LOCKED: live leg first.** Phase 1 ships the
  public-instance live source to validate the signal; the DuckDB dataset leg
  lands in Phase 2 as the Route C primary (live becomes fallback).
- **D2 — Injection mechanism → LOCKED: `key_context` line, no `council.py`
  touch.** Synthesizer appends one rendered line + sets the structured field.
  Zero council/prompt change.
- **D3 — `TRANSFERMARKT_API_BASE` → LOCKED: env-overridable knob** (default the
  public instance), so the self-host swap is config-only.
- **D4 — Observability → LOCKED: Phase 1.** Persist the per-run `SquadValuation`
  from day one so value-imbalance vs outcome is measurable retroactively
  (CTO §9). Analytics surfacing deferred.
- **D5 — Tavily/Transfermarkt → LOCKED: no new Tavily work.** Value comes from
  the structured provider (clean, deterministic, DRY); TM remains an
  Architect-selectable qualitative domain, which it already is
  (`synthesizer.py:68`).
- **D6 — Disambiguation → LOCKED: senior-national-team only** (exclude
  U21/U19/U17/U16/U23 + women's). Club-league coverage out of scope.
```
