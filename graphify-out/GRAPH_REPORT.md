# Graph Report - .  (2026-07-18)

## Corpus Check
- 94 files · ~239,629 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 921 nodes · 1708 edges · 65 communities (57 shown, 8 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 165 edges (avg confidence: 0.69)
- Token cost: 475,928 input · 0 output

## Community Hubs (Navigation)
- Structured Match Adapter Base
- Agent Weights & Backfills
- Squad Value Smoke Test
- Polymarket Gamma Fetching
- History Analytics & Postmortem
- Agent Personas & Prompt Assembly
- Option B Backtest Script
- Reliability Diagram Script
- Council Orchestration
- Football-Data Provider
- PandaScore Client
- Phase 1 Signal Quality Docs
- Briefing Synthesizer
- Phase 0 & Scaling Docs
- CLI Commands & History
- CLV Backfill Script
- Phase 2-3 Roadmap Docs
- DuckDB Analytics Layer
- Market Selection UI
- API & Model Reference Docs
- Perceived Stats & Kelly
- Reliability Reports & Monitor
- Architecture Concepts
- Squad Valuation Enrichment
- Pydantic Models Core
- Tier A Auto-Buy Docs
- Soccer & Squad Value Plans
- Search Planning & MarketData
- Analyze Run & Phase Reporter
- Price-Points Backfill
- Event Group Fetching
- Verdict Scoring & AgentVerdict
- Session Log & Monitoring Docs
- Gate Audit Retrospectives
- CLOB Price History
- Option B Autonomy Concepts
- Match Resolver
- Batch Analyze Mode
- Moneyline Sorting
- CTO Review & Principles
- Scoring Reference Docs
- EV Gate & CLV Concepts
- Console Logging
- Structured Match Formatting
- Known Issues & Gamma Docs
- Providers Smoke Test
- Price-Points Sidecar
- Market List Display
- Verdict Backfill Script
- Prompt Flags Backfill
- Resolution Fetching
- Config Validation
- Bull Persona
- Contrarian Persona
- Rules Lawyer Persona
- Sentiment Persona
- Specialist Persona
- Statistician Persona
- Package Root

## God Nodes (most connected - your core abstractions)
1. `MarketData` - 52 edges
2. `StructuredMatchProvider` - 26 edges
3. `CompletedMatch` - 21 edges
4. `build_briefing()` - 20 edges
5. `_run_analysis()` - 19 edges
6. `EventGroup` - 19 edges
7. `CandidateMatch` - 18 edges
8. `load_history()` - 17 edges
9. `StructuredMatchRecord` - 16 edges
10. `TeamValuation` - 15 edges

## Surprising Connections (you probably didn't know these)
- `7-Agent MoE (Quant-Mental Pipeline)` --semantically_similar_to--> `The Council of Seven`  [INFERRED] [semantically similar]
  docs/esports_intel_engine_report.md → CLAUDE.md
- `run()` --calls--> `load_history()`  [INFERRED]
  scripts/a1_backtest.py → src/pqi/history.py
- `_FakeResp` --uses--> `MarketData`  [INFERRED]
  scripts/smoke_squad_value.py → src/pqi/models.py
- `_FakeClient` --uses--> `MarketData`  [INFERRED]
  scripts/smoke_squad_value.py → src/pqi/models.py
- `part_a()` --calls--> `_format_form()`  [INFERRED]
  scripts/smoke_structured_providers.py → src/pqi/agents/council.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **The Seven Council Personas** — claude_council_of_seven, claude_rules_lawyer, claude_bull, claude_bear, claude_statistician, claude_sentiment, claude_specialist, claude_contrarian [EXTRACTED 1.00]
- **Phase 1 Stage-1 Monitor A/B (v1 vs v2:preamble)** — _pqi_reports_reliability_20260420_v1_report, _pqi_reports_reliability_20260420_v2_preamble_report, _pqi_reports_reliability_20260421_v1_report, _pqi_reports_reliability_20260421_v2_preamble_report, next_session_prompt_stage1_monitor, next_session_prompt_four_criteria, claude_shared_preamble_v2 [INFERRED 0.95]
- **External Data Sources of the Analyze Pipeline** — docs_api_reference_gamma_api, docs_api_reference_clob_api, docs_api_reference_tavily_api, docs_api_reference_pandascore_api [INFERRED 0.85]
- **CTO §5 Edge Triangulation (Prediction / Entry / Exit)** — docs_retrospectives_phase_0_edge_2026_04_council_brier, docs_retrospectives_phase_0_edge_2026_04_pre_event_clv, docs_phases_phase_2b_mfe_analysis_mfe [EXTRACTED 1.00]
- **Phase 1 Unified Four-Criterion Acceptance Group** — docs_phase_1_acceptance_criteria_patch_unified_four_criterion_acceptance, docs_phases_phase_1_signal_quality_cluster_collapse, docs_phases_phase_1_signal_quality_mirror_collapse, docs_retrospectives_phase_0_edge_2026_04_bear_miscalibration, docs_retrospectives_phase_0_edge_2026_04_council_brier [EXTRACTED 1.00]
- **Provider-Adapter Pattern Family** — docs_soccer_structured_data_plan_structured_match_provider_abc, docs_soccer_structured_data_plan_pandascore_provider, docs_soccer_structured_data_plan_football_data_provider, docs_squad_value_enrichment_plan_team_valuation_source, docs_squad_value_enrichment_plan_transfermarkt_live_source [INFERRED 0.85]

## Communities (65 total, 8 thin omitted)

### Community 0 - "Structured Match Adapter Base"
Cohesion: 0.05
Nodes (45): MatchResult, CandidateMatch, CompletedMatch, AsyncClient, datetime, Provider-agnostic structured-match layer.  Defines the `StructuredMatchProvider`, Adapter interface for one structured-data source.      Concrete providers (`Pand, True when this provider is switched on AND has a usable key. (+37 more)

### Community 1 - "Agent Weights & Backfills"
Cohesion: 0.05
Nodes (55): Connection, main(), _migrate_entry(), _parse_iso_ts(), One-shot run_context backfill (session 41).  Walks ~/.pqi/history.jsonl and ensu, Mutate in place. Returns the action taken., _avg_qualifying(), compute_weights() (+47 more)

### Community 2 - "Squad Value Smoke Test"
Cohesion: 0.07
Nodes (30): _FakeClient, _FakeResp, main(), _market(), part_a(), part_b(), Monkeypatch target for httpx.AsyncClient inside transfermarkt_live., category_is_soccer() (+22 more)

### Community 3 - "Polymarket Gamma Fetching"
Cohesion: 0.08
Nodes (41): _apply_client_filters(), _build_api_params(), _cache_get_category(), _cache_put_category(), _category_from_event(), CategoryMatch, _event_level_start(), _fetch_and_filter() (+33 more)

### Community 4 - "History Analytics & Postmortem"
Cohesion: 0.10
Nodes (40): dict, EquityMode, OutcomeKind, agent_accuracy(), AgentStats, classify_outcome(), compute_and_persist_postmortem(), _council_favored_prob() (+32 more)

### Community 5 - "Agent Personas & Prompt Assembly"
Cohesion: 0.08
Nodes (30): _assemble(), build_prompt(), flags_for_agent(), _inject_before(), Prepend the active shared preamble to any agent persona., Insert `snippet` before `marker` in `body`. The marker must appear     exactly o, Apply Mechanism 2 / 3 insertions to `body` then wrap with the active     preambl, Sparse flag snapshot for the given agent.      Returns only the v2 flags that we (+22 more)

### Community 6 - "Option B Backtest Script"
Cohesion: 0.11
Nodes (26): _bootstrap_brier_delta_ci(), _evaluate_rows(), _is_legacy(), logodds_mean_scores(), _override_dempster_both(), _override_dempster_gap(), _override_dempster_ign(), Any (+18 more)

### Community 7 - "Reliability Diagram Script"
Cohesion: 0.12
Nodes (27): _accumulate(), _band_for(), _band_label(), _brier(), _diversity_diagnostics(), _flag_slug(), _iter_resolved_predictions(), main() (+19 more)

### Community 8 - "Council Orchestration"
Cohesion: 0.13
Nodes (24): _build_side_context(), _build_user_message(), _call_llm(), _compute_event_timing(), _extract_outcome_labels(), _format_briefing(), _format_form(), _parse_verdict() (+16 more)

### Community 9 - "Football-Data Provider"
Cohesion: 0.15
Nodes (15): FootballDataProvider, _full_time(), _normalise_status(), _parse_utc(), AsyncClient, datetime, MatchStatus, football-data.org provider adapter (soccer).  Wraps the low-level `pqi.ingestion (+7 more)

### Community 10 - "PandaScore Client"
Cohesion: 0.16
Nodes (20): _auth_headers(), _get(), get_h2h(), get_match(), get_team_past_matches(), list_matches_in_window(), list_videogames(), make_client() (+12 more)

### Community 11 - "Phase 1 Signal Quality Docs"
Cohesion: 0.17
Nodes (20): Option B Pre-Registered Backtest Criteria (§7), agent_weights.json Reset Before Rollout, Bear Probability-After-Advocacy (Mechanism 3), Persona-Specific Evidence Anchoring (Mechanism 2), Phase 1 Acceptance Criteria Patch, Unified Four-Criterion Acceptance, Cluster Collapse, Mirror Collapse (+12 more)

### Community 12 - "Briefing Synthesizer"
Cohesion: 0.18
Nodes (18): AsyncTavilyClient, build_briefing(), _compute_time_status(), _execute_search(), _extract_key_facts(), _matchup_line(), _parse_llm_response(), _plan_searches() (+10 more)

### Community 13 - "Phase 0 & Scaling Docs"
Cohesion: 0.12
Nodes (18): Task 0.1 DuckDB Analytics Wire-Up, Task 0.5 Edge Retrospective and Verdict, Phase 0: Edge Validation via Analytics Infrastructure, Task 0.2 Pre-Event CLV Backfill, Task 1.3 Pairwise Diversity Measurement, Max Favorable Excursion (MFE), Deferred: LLM Gateway (LiteLLM), Deferred: Microservices Decomposition (+10 more)

### Community 14 - "CLI Commands & History"
Cohesion: 0.17
Nodes (17): categories(), display_council_result(), history_equity(), history_list(), history_show(), history_sync(), _outcome_glyph(), Table (+9 more)

### Community 15 - "CLV Backfill Script"
Cohesion: 0.21
Nodes (16): _bought_token(), _compute_row(), _empty_row(), _entry_price(), _fetch_clob(), _find_bucket_before(), _is_cached(), _iter_jsonl() (+8 more)

### Community 16 - "Phase 2-3 Roadmap Docs"
Cohesion: 0.19
Nodes (16): Chase / Park / Iterate Verdict, Phase 2b: MFE Analysis, Read-Only MFE Listener, Phase 2c: MFE Listener Prototype, Tier B Automation Decision, Phase 3: Deployment Portability, VPS Deployment Runbook, Phase 4: Conditional Scaling (Trigger-Driven) (+8 more)

### Community 17 - "DuckDB Analytics Layer"
Cohesion: 0.17
Nodes (15): DuckDBPyConnection, DuckDBPyRelation, _fmt(), get_connection(), Path, query(), SQL analytics layer over ~/.pqi/history.jsonl via DuckDB.  Exposes a small surfa, Run `sql` against the history views. Returns a DuckDB relation.      Callers can (+7 more)

### Community 18 - "Market Selection UI"
Cohesion: 0.17
Nodes (16): _analyzed_market_ids(), display_event_list(), _load_perceived_stats_safe(), _parse_selection(), prompt_event_multiselect(), _prompt_sub_market(), Parse a multi-select spec into a sorted unique 1-indexed list.      Accepted for, Set of market IDs that already have a (non-legacy) entry in history.      Used t (+8 more)

### Community 19 - "API & Model Reference Docs"
Cohesion: 0.17
Nodes (15): Phase 2b — MFE Analysis, PQI — Polymarket Qualitative Intelligence, Squad Market-Value Enrichment (Transfermarkt), Structured-Match Resolver (Provider Adapter Layer), Polymarket CLOB API, PandaScore API, Tavily Search API, Max Favorable Excursion (MFE) (+7 more)

### Community 20 - "Perceived Stats & Kelly"
Cohesion: 0.19
Nodes (14): PerceivedStats, Rolling stats derived from resolved history — source of truth for     the EV gat, _compute_band_stats(), kelly_fraction(), load_perceived_stats(), PerceivedTrueWinRate — rolling win rate over resolved BUY history, used as the E, Walk resolved BUYs and produce a `BandStat` per price band.      Uses Beta-binom, Scan history.jsonl, compute fresh stats, persist to stats.json, return. (+6 more)

### Community 21 - "Reliability Reports & Monitor"
Cohesion: 0.29
Nodes (14): Reliability Diagram 2026-04-18 (n=385), Reliability Diagram 2026-04-19 (n=409), Reliability Diagram 2026-04-20 v1 (n=414), Reliability Diagram 2026-04-20 v2:preamble (n=38), Reliability Diagram 2026-04-21 v1 (n=414), Reliability Diagram 2026-04-21 v2:preamble (n=78), The Bear (Underdog's Advocate), Option B Shadow Layer (+6 more)

### Community 22 - "Architecture Concepts"
Cohesion: 0.20
Nodes (14): Anthropic Prompt Caching, Centralized Anthropic Retry, Confidence-Weighted Two-Sided Scoring, The Council of Seven, Dynamic Per-Agent Reliability Weights, LLM Client Contract (pqi/llm/client.py chat()), Analyze Pipeline Diagram (Mermaid), System Architecture Diagram (Mermaid) (+6 more)

### Community 23 - "Squad Valuation Enrichment"
Cohesion: 0.19
Nodes (13): Non-fatal wrapper around the squad-value resolver. Any failure → None;     squad, _safe_resolve_valuation(), build_squad_valuation(), format_eur(), Squad market-value enrichment — provider-agnostic core.  A parallel enrichment d, Human-readable EUR amount: €1.22bn / €359M / €4.1M / €900k / unknown., Render the single key_context line for the council, or None to suppress     (whe, Assemble the matchup record, computing favorite + ratio.      `favorite_name` / (+5 more)

### Community 24 - "Pydantic Models Core"
Cohesion: 0.19
Nodes (13): BandStat, phase1_flag_label(), PriceHistory, BaseModel, A single-hour price move exceeding the sharp-move threshold., Computed trend metrics for one side (YES or NO) from CLOB hourly price data., Per-price-band rolling hit-rate slice of resolved BUY history.      The EV gate, Metadata about an analyze run: WHEN we ran, relative to the event.      Lives at (+5 more)

### Community 25 - "Tier A Auto-Buy Docs"
Cohesion: 0.17
Nodes (13): First-10-Fills Checklist, Ghost-Event ID-Based Probing, Idempotency Guard, Phase 2a: Tier A Auto-BUY, pqi buy Command, Pre-Trade Price Drift Abort, Manual Review Loop, Capacity Ceiling (+5 more)

### Community 26 - "Soccer & Squad Value Plans"
Cohesion: 0.23
Nodes (13): Session 48: Soccer Structured-Data Adapter, Session 49: Squad Market-Value Enrichment, FootballDataProvider (football-data.org), PandaScoreProvider (Esports Adapter), Soccer Structured-Data Plan, StructuredMatchProvider ABC (Adapter Pattern), Transfermarkt Parked (Session 48), key_context Briefing-Line Injection (+5 more)

### Community 27 - "Search Planning & MarketData"
Cohesion: 0.18
Nodes (12): _extract_entities(), _fallback_queries(), BaseModel, Attempt to extract two competing entities from a market question.      Looks for, A single search query with full Tavily parameter tuning., Hardcoded query templates — used when the Search Architect LLM fails.      This, Wrapper for LLM-generated search plan., SearchPlan (+4 more)

### Community 28 - "Analyze Run & Phase Reporter"
Cohesion: 0.21
Nodes (8): _PhaseReporter, A dim, in-progress sub-step (escaped — may come from providers)., A highlighted result sub-line. `markup` is trusted rich markup., Coloured ▲/▼/— for a weekly price change in percentage points., Narrated, numbered status flow for a single (non-batch) analysis.      Renders `, Execute the full analysis pipeline for one market, with optional cache.      If, _run_analysis(), _trend_arrow()

### Community 29 - "Price-Points Backfill"
Cohesion: 0.27
Nodes (11): _compute_row(), _empty_row(), _fetch_side(), _is_cached(), _iter_jsonl(), main(), Client, Path (+3 more)

### Community 30 - "Event Group Fetching"
Cohesion: 0.21
Nodes (12): _apply_client_filters_groups(), _enrich_event_groups(), _fetch_and_filter_groups(), fetch_events(), _ParsedEventGroup, Fetch active events from the Gamma API, grouped by parent event.      Event-cent, EventGroup + transient metadata for client-side filtering before enrichment., Apply composable client-side filters to parsed event groups.      Same predicate (+4 more)

### Community 31 - "Verdict Scoring & AgentVerdict"
Cohesion: 0.18
Nodes (8): AgentVerdict, _make_verdicts(), Helper: create 7 uniform verdicts for scenario testing., One agent's assessment of a market.     Probability and confidence are stored as, Compute all derived fields from raw verdicts and assemble a CouncilResult., Return the WR threshold the EV gate should compare against for a         given `, build_council_result(), Orchestrate the full scoring pipeline and return a CouncilResult.     Delegates

### Community 32 - "Session Log & Monitoring Docs"
Cohesion: 0.24
Nodes (11): Task 0.3 Reliability Diagram, Stage 1 Monitor (PERSONA_V2_PREAMBLE), Containerized Monolith, Council Brier 0.2176, 68% WR Decomposition, DuckDB STRUCT-vs-MAP prompt_flags Bug, Sessions 46-47: PQI_DATA_DIR + src Layout, Session 40: prompt_flags Analytics Tagging (+3 more)

### Community 33 - "Gate Audit Retrospectives"
Cohesion: 0.25
Nodes (11): Bear Rewrite Validation (Session 20), BUY YES vs BUY NO Asymmetry, Confidence Gate Dominance, EV Gate Historical Opportunity Cost, Gate and EV Audit (Session 24), Bear NO-Lean Bias Diagnosis, Tier 1 ROI Threshold Adjustment, A.1 Confidence Asymmetry (Option A vs B) (+3 more)

### Community 34 - "CLOB Price History"
Cohesion: 0.29
Nodes (10): _compute_direction(), _compute_side_metrics(), fetch_price_history(), _fetch_side(), Fetch and parse price history for a single token.     Returns a sorted list of P, Compute all trend metrics for one side from a sorted list of price points., Determine trend direction via linear regression slope.      If slope × window_ho, Fetch YES and NO token price history from the CLOB API in parallel     and compu (+2 more)

### Community 35 - "Option B Autonomy Concepts"
Cohesion: 0.24
Nodes (10): Two-Sidedness Is Autonomy, Not Algebra, Two-Sided Confidence Asymmetry (Open Question), AgentVerdict, Pre-Registered B-Math Acceptance Criteria, Coherence Metric |p_yes + p_no − 1|, Dempster-Shafer Explicit Ignorance, Option B — Per-Side Probability Autonomy, probability_source Flag (+2 more)

### Community 36 - "Match Resolver"
Cohesion: 0.27
Nodes (9): _extract_parties(), _parse_parties(), Structured-match orchestrator — turns a Polymarket `MarketData` into a trusted `, Resolve a Polymarket market to a provider-canonical match record.      `on_step`, Strip prefixes ('Will ', 'Valorant: '), trailing punctuation, and     parentheti, Extract (party_a, party_b) from an "A vs B" string, or None., Best-effort extraction of the two competing parties.      Tries the market quest, resolve_match() (+1 more)

### Community 37 - "Batch Analyze Mode"
Cohesion: 0.29
Nodes (10): analyze(), _BatchRow, _format_elapsed(), datetime, Fetch active Polymarket events, run the Council of Seven, and print recommendati, Mutable per-event state for the batch-mode Live display.      Populated by `_run, Run each row's analysis under a shared `asyncio.Semaphore`.      Live status tab, _render_batch_summary() (+2 more)

### Community 38 - "Moneyline Sorting"
Cohesion: 0.22
Nodes (10): _headline_market(), _is_moneyline(), _moneyline_score(), _moneyline_sort_key(), Score how likely a market is the moneyline (overall outcome) market.      Higher, True when the Gamma-API `sportsMarketType` flags this as the moneyline.      Aut, Composite sort key that promotes moneyline markets to the top.      Ranks by (is, Return markets sorted with the moneyline (overall outcome) first. (+2 more)

### Community 39 - "CTO Review & Principles"
Cohesion: 0.31
Nodes (9): Edge First, Scale Second, Monolith-First, Containers Later, Parallel Is Not Diverse, CTO Agent Reviewer Persona, Modular Monolith Recommendation, CTO Review: eSports Predictive Engine v2.0, 68% Win Rate Claim, Model Mixing for Divergence (+1 more)

### Community 40 - "Scoring Reference Docs"
Cohesion: 0.33
Nodes (9): Task 0.4 Per-Band Calibration Check, MIN_EXPECTED_ROI 0.15 to 0.10, BAND_SHRINKAGE_K: Keep K=5, Banded EV Gate, Beta-Binomial Band Smoothing, Recommendation Gate Order, PerceivedTrueWinRate, Scoring Reference (+1 more)

### Community 41 - "EV Gate & CLV Concepts"
Cohesion: 0.32
Nodes (8): Banded EV Gate, Kelly Sizing (Half-Kelly Advisory), clv_by_row View (Pre-Event CLV Sidecar), DuckDB Views (history / verdicts / clv_by_row), Analytics Starter Queries, Capacity as Third Edge Axis, Pre-Event CLV, PerceivedStats

### Community 42 - "Console Logging"
Cohesion: 0.25
Nodes (7): LogRecord, _configure_logging(), _ConsoleLogHandler, Render log records through the rich console with level-based colour.      INFO/D, Install the console log handler and set the level. Default = WARNING     (clean), CategoryNotFoundError, Raised when --category matches no series or tag.

### Community 43 - "Structured Match Formatting"
Cohesion: 0.29
Nodes (8): provider_display_name(), Human label for a provider id (`StructuredMatchRecord.provider`), used in     th, _detect_completion_contamination(), _format_structured_block(), Mechanical safety net for the temporal coherence rule in     `_SYNTHESIZER_SYSTE, Render the optional `STRUCTURED MATCH RECORD` segment for the     synthesizer us, Trusted, structured-data view of the current market's specific event,     plus c, StructuredMatchRecord

### Community 44 - "Known Issues & Gamma Docs"
Cohesion: 0.29
Nodes (7): Live-Event Grace (LIVE_EVENT_GRACE_HOURS), Polymarket Gamma API, history equity Command, Ghost Events (Gamma Indexing Bug), Known Issues & Assumptions, CouncilResult, MarketData

### Community 45 - "Providers Smoke Test"
Cohesion: 0.62
Nodes (6): _aret(), main(), _market(), part_a(), part_b(), part_c()

### Community 46 - "Price-Points Sidecar"
Cohesion: 0.38
Nodes (6): append_row(), datetime, Raw CLOB trendline sidecar — session 41.  Persists the full-fidelity YES/NO `Pri, Render pydantic PricePoints as compact `{t, p}` dicts.      `t` is unix seconds, Append one row to `~/.pqi/price_points.jsonl`.      Non-fatal: logs and swallows, _serialize_points()

### Community 47 - "Market List Display"
Cohesion: 0.33
Nodes (6): display_market_list(), _format_end_date(), prompt_market_selection(), Return a human-readable time-to-resolution string., Render a numbered Rich table of available markets., Display the market list and prompt the user to pick one (default = #1).

### Community 48 - "Verdict Backfill Script"
Cohesion: 0.50
Nodes (4): main(), _migrate_verdict(), Option B — one-shot synthetic-mirror backfill.  Walks ~/.pqi/history.jsonl and e, Mutate a verdict dict in place. Returns the action taken.

### Community 49 - "Prompt Flags Backfill"
Cohesion: 0.50
Nodes (4): main(), _migrate_verdict(), Phase 1 — one-shot prompt_flags backfill.  Walks ~/.pqi/history.jsonl and ensure, Mutate a verdict dict in place. Returns the action taken.

### Community 50 - "Resolution Fetching"
Cohesion: 0.50
Nodes (4): fetch_resolution(), Fetch the current resolution state for a market.      Returns a Resolution dict, Resolution, TypedDict

## Knowledge Gaps
- **26 isolated node(s):** `pqi`, `The Rules Lawyer`, `The Bull (Favorite's Advocate)`, `The Base-Rate Statistician`, `The Sentiment Analyst` (+21 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MarketData` connect `Search Planning & MarketData` to `Structured Match Adapter Base`, `Squad Value Smoke Test`, `Polymarket Gamma Fetching`, `Council Orchestration`, `Briefing Synthesizer`, `Market Selection UI`, `Squad Valuation Enrichment`, `Pydantic Models Core`, `Analyze Run & Phase Reporter`, `Event Group Fetching`, `Verdict Scoring & AgentVerdict`, `CLOB Price History`, `Match Resolver`, `Batch Analyze Mode`, `Moneyline Sorting`, `Console Logging`, `Structured Match Formatting`, `Providers Smoke Test`, `Price-Points Sidecar`, `Market List Display`?**
  _High betweenness centrality (0.140) - this node is a cross-community bridge._
- **Why does `CompletedMatch` connect `Structured Match Adapter Base` to `Structured Match Formatting`, `Search Planning & MarketData`, `Football-Data Provider`?**
  _High betweenness centrality (0.042) - this node is a cross-community bridge._
- **Why does `CandidateMatch` connect `Structured Match Adapter Base` to `Structured Match Formatting`, `Search Planning & MarketData`, `Football-Data Provider`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._
- **Are the 14 inferred relationships involving `MarketData` (e.g. with `_FakeClient` and `_FakeResp`) actually correct?**
  _`MarketData` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `StructuredMatchProvider` (e.g. with `H2HMatch` and `MarketData`) actually correct?**
  _`StructuredMatchProvider` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `CompletedMatch` (e.g. with `H2HMatch` and `MarketData`) actually correct?**
  _`CompletedMatch` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `build_briefing()` (e.g. with `render_briefing_line()` and `ValueError`) actually correct?**
  _`build_briefing()` has 3 INFERRED edges - model-reasoned connections that need verification._