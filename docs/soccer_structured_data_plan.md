# Soccer structured-data support — design & decision log

**Status:** decisions LOCKED + **implemented** (session 48); pending one live
test once the API key lands. This doc is the lockstep record of *why* we're
doing this, *what* was decided, and *what* shipped (§9).

**Locked decisions:**
- **D1 Provider:** football-data.org as the primary fixtures provider.
- **Player data:** Transfermarkt parked as a *future* enrichment adapter
  (separate gated phase — it adds player fields + new council inputs).
- **D2 Key:** user registers a free key and adds `FOOTBALL_DATA_API_KEY` to
  `.env`; build proceeds against the verified schema, live test once key lands.
- **D3 Refactor depth:** clean adapter — refactor PandaScore into a
  `PandaScoreProvider`, add `FootballDataProvider`, share one skeleton, guard
  with a regression check that a known esports market yields an identical record.
- **D4 Config knobs:** parallel `FOOTBALL_DATA_*` knobs with the same defaults
  as the `PANDASCORE_*` analogues.

**Goal:** extend the structured-match layer (today PandaScore/esports only) to
also cover **soccer matches** — major leagues + the World Cup — so the council
gets authoritative status / kickoff / score / form / H2H for football the same
way it does for esports. Keep changes delicate; protect the working esports
reasoning path; follow SOLID / DRY / KISS; use an **adapter** so we have one
unified layer to pull structured match data from and inject into the briefing.

This work depends on the session-48 start-time fix (event-level `startTime`
parsing in `polymarket.py`) which is what makes soccer match events show up in
`analyze -s` at all. See `docs/session-log.md` (session 48) / `CLAUDE.md`.

---

## 1. Why PandaScore can't do this

PandaScore is **esports-only** (verified against their current docs/pricing —
LoL, CS2, Dota 2, …; no football product). So a second provider is required.
The good news: `StructuredMatchRecord` was already written **provider-agnostic**
(see its docstring in `models.py`), and `BriefingDocument.structured_match`,
the synthesizer render, the council render, history, and cache all consume that
neutral shape. The output contract does **not** need to change for soccer. The
coupling that needs breaking is upstream: `match_resolver.py` is currently
hard-wired to PandaScore's *request/response shape*.

## 2. Provider candidates (verified)

Coverage requirement (per product decision): **major leagues + World Cup only.**
Obscure friendlies are explicitly out of scope, which loosens the bar.

### football-data.org — *recommended*

Verified from their official API reference (`/documentation/api`):

- **Match status enum:** `SCHEDULED`, `LIVE`, `IN_PLAY`, `PAUSED`, `FINISHED`,
  `POSTPONED`, `SUSPENDED`, `CANCELLED`.
- **Match fields:** `utcDate` (ISO 8601), `homeTeam{id,name}`,
  `awayTeam{id,name}`, `score.fullTime.{home,away}`,
  `score.winner` ∈ `HOME_TEAM | AWAY_TEAM | DRAW`, `competition`, `matchday`.
- **Endpoints:** `/matches?dateFrom=&dateTo=` (window list, all free comps),
  `/competitions/{id}/matches`, `/teams/{id}/matches` (form),
  `/matches/{id}` (includes a `head2head` object).
- **Free tier:** confirmed to include **World Cup**, Champions League, the big-5
  leagues (PL, La Liga, Serie A, Bundesliga, Ligue 1), Championship, Eredivisie,
  Primeira Liga, Brazil Série A, Euros — 12 competitions. Auth header
  `X-Auth-Token`. Rate limit ~10 requests/min (no hard daily cap).

**H2H + form schema — now verified from docs (no key needed):**
`GET /matches/{id}/head2head` returns aggregates (`numberOfMatches`,
`totalGoals`, per-team `wins/draws/losses`) **and** a `matches` list of prior
fixtures (last 10 detailed) — so we get a dated H2H list directly, no
client-side intersection required. `GET /teams/{id}/matches` returns the
standard `{filters, resultSet, matches[]}` envelope (filterable by `status`
and date range) for the form snapshot. Only thing still requiring the live key:
real per-minute rate-limit behavior under batch, and the key itself for the
resolver to function.

### API-Football (API-SPORTS) — viable, not recommended

Richer coverage (1,200+ comps incl. WC 2026), has `/fixtures/headtohead` and
`/teams/statistics` (form). **But the free plan is ~100 requests/day.** One
soccer resolution costs ~3 calls (window list + 2× form); batch `analyze`
across a slate would exhaust the daily budget fast. For our "major leagues + WC"
scope football-data.org's per-minute (not per-day) limit is the better fit.

### Transfermarkt — evaluated (user-raised), not recommended as *primary*

Strong reputation and uniquely deep **player-level** data (market values,
transfer history, career stats, squad/injury context). But for *this* job it
is a poor primary fit:

- **No official public API.** Programmatic access means a third-party
  unofficial wrapper or self-hosted scraper (e.g. `felipeall/transfermarkt-api`
  FastAPI, `dcaribou/transfermarkt-scraper`, or paid RapidAPI/Apify/ScrapingBee
  wrappers). That is the *opposite* of "least friction": ToS/legal ambiguity,
  no stability/SLA contract (breaks when the site's HTML changes), and either a
  service we must run/host or a paid scraping middleman.
- **Convention mismatch.** Every existing data source in this project is an
  official, public, no-auth-or-keyed REST API (Gamma, CLOB, Tavily,
  PandaScore). A scraper breaks that pattern.
- **Shape mismatch with what the council consumes today.** Transfermarkt's
  value is player-level signals, which `StructuredMatchRecord` does not model
  (no player fields). The match-level fields we actually need (status / score /
  H2H / form) are exactly what a fixtures API gives cleanly and a scraper gives
  awkwardly.

**Where Transfermarkt *does* fit — and why the adapter pattern pays off:**
its player depth (key-player injuries, squad market-value gaps, availability)
is a genuinely valuable *future enrichment* signal — the kind the Contrarian /
Specialist personas could exploit (roster/injury vectors, §6). That would be a
**second adapter** added later behind the same `StructuredMatchProvider`
interface, layering player context onto a fixtures-API base — an explicit
argument *for* the adapter design. It is a scope expansion that adds new model
fields and new council inputs (touches the reasoning machine), so it is
deliberately **out of scope for the initial parity ship** and parked here.

**Recommendation: football-data.org as the primary fixtures provider**, on
rate-limit + free-official-API + least-friction grounds; Transfermarkt
reconsidered later as an optional player-context enrichment adapter.
Coverage gaps (only 12 comps) are acceptable given the stated scope.

## 3. Architecture — adapter pattern

There **is** enough request/response diversity to justify an adapter
(PandaScore: `/{game}/matches`, `opponents[].opponent`, `results[].score`,
`winner.id`, BO-format · football-data: `/matches?dateFrom`, `homeTeam`/
`awayTeam`, `score.fullTime`, `score.winner`, draws, no BO). Proposed shape:

```
match_resolver.py  (orchestrator, provider-agnostic)
  ├── resolve_match(market) -> StructuredMatchRecord | None
  │     1. extract parties from question        (SHARED — exists today)
  │     2. pick the provider that covers(category)
  │     3. delegate to provider, apply fuzzy threshold gate (SHARED)
  │     4. build StructuredMatchRecord           (SHARED)
  │
  providers/
    base.py        StructuredMatchProvider (ABC) + normalized intermediates
    pandascore.py  PandaScoreProvider  (wraps existing ingestion/pandascore.py)
    football.py    FootballDataProvider (new HTTP client + mapping)
```

**Template-Method split (DRY):** the base class owns the shared skeleton
(window-list → fuzzy-score → threshold gate → parallel form/H2H → build record);
each provider implements only the divergent hooks and **maps its raw payload to
two normalized typed intermediates** at its own boundary:

- `CandidateMatch` — provider_match_id, team_a/b name+id, scheduled_at,
  normalized `status`, match_format|None, tournament|None, final_score|None,
  winner_name|None.
- `CompletedMatch` — for form/H2H: the two teams (id+name), scheduled_at,
  `result_for(team_id) -> "win"|"loss"|"draw"`, score_summary, tournament.

Provider hooks: `covers(category)`, `list_candidates(window)`,
`fetch_form(team_id, n)`, `fetch_h2h(a_id, b_id, n)`. New provider = implement
four hooks + the two mappers. This is OCP (add a provider without touching the
skeleton) and DIP (orchestrator depends on the ABC, not on httpx shapes).

**Convention note / standards check:** CLAUDE.md says *never pass raw dicts
between modules*. Today `ingestion/pandascore.py` returns raw dicts to
`match_resolver`. The adapter design **improves** adherence — raw dicts stay
inside each adapter; only typed `CandidateMatch`/`CompletedMatch` cross the
boundary. The existing PandaScore HTTP module is reused unchanged; only the
parsing that currently lives in `match_resolver` moves into its adapter.

**Delicacy / friction trade-off (decision point D3).** The clean version
refactors the esports parsing into `PandaScoreProvider`. A lower-friction
alternative leaves PandaScore exactly as-is and adds a *self-contained* soccer
resolver alongside, accepting some duplication of the fuzzy/threshold skeleton.
Recommended: the clean adapter, guarded by a regression check that a known
esports market still produces a byte-identical `StructuredMatchRecord`.

## 4. Model & config changes (all additive, esports-safe)

1. **`TeamForm.draws: int = 0`** (additive). Soccer draws are ~25% of matches;
   today `matches_considered` can exceed `wins + losses` and the council render
   `total = wins + losses` undercounts. Default `0` keeps every existing
   PandaScore record valid. Council render becomes `{W}-{D}-{L}` when `draws`
   is meaningful. `H2HMatch` already supports draws (`winner_name=None`,
   `score_summary="1-1"`), no change there.
2. **Provider-agnostic rendering.** Two hardcoded "PandaScore" labels —
   `agents/council.py::_format_briefing` and
   `ingestion/synthesizer.py::_format_structured_block` (+ the temporal anchor
   line) — should read `structured_match.provider` instead. Pure DRY cleanup.
3. **`StructuredMatchRecord.game` semantics for soccer.** Reuse the field for
   the competition slug (e.g. `fifa-world-cup`) or the literal `soccer`. No
   schema change; documented convention only. `match_format` stays `None`.
4. **Config (new keys, follow the existing `.env.example` + `config.py`
   pattern, mirroring the PandaScore block):** `FOOTBALL_DATA_API_KEY`,
   `FOOTBALL_DATA_ENABLED` (default true), `FOOTBALL_DATA_BASE_URL` (code
   constant), and reuse/parallel the fuzzy-threshold / window / form-count
   knobs (either share the existing `*_FUZZY_THRESHOLD` semantics or add
   `FOOTBALL_DATA_*` analogues — see D4).

## 5. Category routing

`fetch_events -c soccer | fifa-world-cup | sports` already surfaces the matches
(post session-48 fix). The resolver dispatch keys off `market.category`:

- esports game slugs (existing `GAME_MAPPING`) → `PandaScoreProvider`.
- soccer tags (`soccer`, `fifa-world-cup`, `2026-fifa-world-cup`, and the major
  league slugs) → `FootballDataProvider`.
- neither → `None` (Tavily-only, exactly as today).

Mapping soccer **competition → football-data competition id** is the soccer
adapter's `covers()` job (e.g. `WC`, `CL`, `PL`, `PD`, `SA`, `BL1`, `FL1`). The
window-list endpoint `/matches?dateFrom&dateTo` already spans all free comps, so
`covers()` only needs to recognize "this is soccer we support," not resolve the
exact competition up front.

## 6. Agent / council prompt assessment (DOCUMENTED — NO ACTION)

Per instruction: **no council changes now.** Findings for a future, separately
gated change (do not touch the reasoning machine yet):

- **Rules Lawyer, Bull, Bear, Statistician, Sentiment** — domain-neutral. Fine
  for soccer as-is.
- **Domain Specialist** — auto-identifies the domain from the question, so it
  will self-select "Association Football / tactics." Broad enough. The
  `_SPECIALIST_ANCHOR` *examples* ("map pool asymmetry, patch-dependent
  composition") lean esports but the requirement (name a specific field detail)
  generalizes. Optional future tweak: add a sport-neutral example (e.g.
  "set-piece dependency, xG over/under-performance, fixture congestion").
- **Contrarian** — the disruption taxonomy is esports-flavored: items 1
  (roster/injury), 3 (form), 4 (morale/motivation), and "scheduling fatigue
  from back-to-back matches" map cleanly to soccer; item 2 ("meta/patch shift")
  and item 5 ("map pool mismatch") do not. `_CONTRARIAN_ANCHOR` cites "upsets
  occur in esports" as a bad-example phrasing. Optional future tweak: make the
  taxonomy sport-neutral or branch a soccer variant (e.g. red-card risk,
  rotation before a bigger fixture, weather, derby intensity, manager out).

**Recommendation for later:** these are *quality* refinements, not correctness
blockers — soccer will reason acceptably on the current prompts. If/when we do
touch them, treat it as a Phase-1-style gated rollout (A/B against the v1 frame)
so we can measure that broadening doesn't regress esports calibration. **Not in
scope for the initial soccer ship.**

## 7. Verification plan

- Verified now (from docs): football-data.org Match schema (§2).
- Needs a live key before wiring the specific calls: `head2head` sub-shape,
  `/teams/{id}/matches` form payload, real rate-limit behavior under batch.
- A reusable on-file smoke script (`scripts/`) — given a known World Cup match
  (e.g. a fixture from `analyze -c fifa-world-cup -s 8`), assert the adapter
  produces a sane `StructuredMatchRecord` (correct teams, status, form W-D-L).
- Regression: a known esports market must still produce the same record
  through the refactored `PandaScoreProvider`.

## 8. Open decision points

- **D1 — Provider.** football-data.org (recommended) vs API-Football vs other.
- **D2 — API key.** A free key is required both to verify the remaining schema
  bits and for the feature to function. Who registers it / where does it live
  (`.env` as `FOOTBALL_DATA_API_KEY`)?
- **D3 — Refactor depth.** Clean adapter (refactor PandaScore into an adapter,
  recommended) vs minimal-friction (leave PandaScore untouched, add a
  self-contained soccer resolver).
- **D4 — Config knob sharing.** Reuse the existing `PANDASCORE_*` fuzzy/window/
  form knobs for soccer, or add parallel `FOOTBALL_DATA_*` knobs (more flexible,
  more surface). Recommended: parallel knobs with the same defaults.
- **D5 — `TeamForm.draws`.** Confirm the additive field is acceptable (it is the
  one model change the esports path also compiles against, default 0).
  → **Accepted & shipped** (default 0, esports render unchanged).

## 9. Implementation (session 48) — what shipped

Clean adapter (D3), football-data primary (D1), Transfermarkt parked.

**New modules**
- `ingestion/providers/base.py` — `StructuredMatchProvider` ABC + the shared
  `resolve()` template method (fuzzy-gate → parallel form/H2H → build record) +
  the normalized `CandidateMatch` / `CompletedMatch` intermediates + shared
  build helpers (`_build_team_form` now counts draws).
- `ingestion/providers/pandascore_provider.py` — esports adapter; the game-slug
  table + all PandaScore dict-parsing moved here verbatim from `match_resolver`.
- `ingestion/providers/footballdata_provider.py` — soccer adapter; maps the v4
  Match schema (status enum, `utcDate`, home/away, `score.fullTime`,
  `score.winner` incl. `DRAW`). H2H keyed off the match id.
- `ingestion/footballdata.py` — low-level v4 HTTP client (mirrors
  `pandascore.py`'s non-fatal contract; `X-Auth-Token`).
- `ingestion/providers/__init__.py` — `get_providers()` registry +
  `provider_display_name()`.
- `scripts/smoke_structured_providers.py` — no-network regression + soccer
  mapping test (+ a live connectivity probe gated on the key).

**Refactored / extended**
- `ingestion/match_resolver.py` — now a thin provider-agnostic orchestrator
  (guards → party extraction → pick covering provider → delegate). `resolve_match`
  signature unchanged.
- Party extraction gained an **event-title fallback** (soccer per-market
  questions read "Will X win?"; the "A vs B" pairing only survives on the title).
- `models.py` — additive `TeamForm.draws` and `MarketData.event_title`.
- `market/polymarket.py` — populates `MarketData.event_title` during parsing.
- `agents/council.py` + `ingestion/synthesizer.py` — provider-agnostic labels
  via `provider_display_name()`; council form render is W-D-L when draws occur,
  **byte-identical W-L for esports** (draws=0).
- `config.py` + `.env.example` — `FOOTBALL_DATA_*` knobs.

**Routing.** Soccer events carry `category` like `soccer-fifwc` (Polymarket
prefixes soccer series `soccer-`); `FootballDataProvider.covers()` matches the
`soccer`/`football`/`world-cup` tokens, PandaScore matches the esports
`GAME_MAPPING`. Domains are disjoint.

**Verification (no network — `scripts/smoke_structured_providers.py`):**
- Part A esports regression: PandaScore record identical post-refactor
  (BO3, fuzzy 100, 1W-0L, H2H "2-1"); council form render byte-identical.
- Part B soccer: football-data record correct, draws counted (1W-1D-0L /
  0W-1D-1L), H2H draw with null winner, event-title party fallback works.
- Graceful degradation: no key → `resolve_match` returns None (Tavily-only),
  no error — current real-world state is unchanged.

**Remaining (needs the key):** add `FOOTBALL_DATA_API_KEY` to `.env`, run
`scripts/smoke_structured_providers.py` (Part C goes live), then a real
`analyze -c fifa-world-cup -s 8` to confirm end-to-end. Pin live rate-limit
behavior under batch.

## 9b. Post-integration fixes (first live run)

The first live soccer run (`Will France win on 2026-06-26?`, event
`Norway vs. France`) surfaced two bugs, both now fixed + verified live:

1. **football-data window returned nothing.** `/matches?dateFrom&dateTo` treats
   the dates as **inclusive instants at 00:00 UTC**, so `dateFrom == dateTo`
   (the common case when the ±window stays inside one calendar day) returns
   **zero** matches. Fix in `footballdata.list_matches_in_window`: set
   `dateTo = window_end_date + 1 day`; the caller still re-narrows to the exact
   ±hour window. Verified: Norway vs France now resolves (status scheduled,
   fuzzy 100, forms fetched). Diagnostic: `scripts/diag_footballdata.py`.

2. **Opponent-blind search → wrong-fixture contamination.** Soccer per-market
   questions are single-competitor ("Will France win on <date>?"), so Tavily
   searched France in isolation and the corpus drifted to France-vs-Iraq
   coverage — the council reasoned about the wrong opponent. Fix: a
   `_resolve_matchup()` helper (structured record → question → event title)
   feeds a directive `SPECIFIC MATCHUP: A vs B` line into BOTH the Search
   Architect and the synthesizer prompts, and the fallback queries use the
   "A vs B" string as their subject. Non-competitive markets (crypto, etc.)
   yield no matchup and the line collapses to empty — no behavior change.
   Verified live: the briefing now summarizes Norway vs France correctly.

   Note these compound: fix (1) makes `structured_match` resolve, which both
   injects the authoritative pairing/status into the council block and powers
   the contamination tagger; fix (2) additionally anchors the *search* itself.

## 10. Council prompt work — still DEFERRED (no action taken)

§6 stands unchanged: the Specialist/Contrarian esports-vocabulary refinements
are documented for a *future, separately-gated* change and were **not** touched.
The reasoning machine is unaffected — the only council-render edits were the
provider label (esports → still "PandaScore") and the draws-aware form line
(esports → still "W-L").
