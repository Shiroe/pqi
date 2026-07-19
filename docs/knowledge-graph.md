# Repo Knowledge Graph (`graphify-out/`)

A navigable knowledge graph of the whole repo — code (AST-extracted) plus
all docs and reliability reports (semantically extracted) — built with
[graphify](https://github.com/safishamsi/graphify) (PyPI package
`graphifyy`). Built 2026-07-18 from 94 files / ~240k words.

**Graph stats:** 921 nodes · 1,708 edges · 65 labeled communities
(727 AST nodes + 194 semantic). Extraction cost: ~476k input tokens
(tracked cumulatively in `graphify-out/cost.json`).

## Artifacts (committed)

| File | What it is |
| --- | --- |
| `graphify-out/graph.html` | Interactive visualization — open directly in a browser, no server needed. Nodes colored by community. |
| `graphify-out/graph.json` | Raw graph data (node-link JSON + communities + hyperedges). GraphRAG-ready. |
| `graphify-out/GRAPH_REPORT.md` | Audit report: god nodes, surprising connections, per-community cohesion scores, suggested questions, token cost. |
| `graphify-out/manifest.json` | File-hash manifest — lets `graphify update` re-extract only new/changed files. |
| `graphify-out/cache/` | AST + semantic extraction cache — a rebuild on another machine reuses this instead of re-extracting unchanged files. |
| `graphify-out/cost.json` | Cumulative token-cost tracker across runs. |

Machine-specific sidecars (`.graphify_python`, `.graphify_root`) are
git-ignored; the tooling regenerates them on first use per machine.

## Setup on a new machine

```bash
uv tool install graphifyy       # provides the `graphify` CLI
```

That's it — the committed `graphify-out/` carries the graph, manifest, and
extraction cache, so nothing needs rebuilding to query.

## Using the graph

```bash
# Ask questions — BFS traversal over the graph, answers cite file:line
graphify query "What consumes MarketData across the pipeline?"
graphify query "How does the structured-match adapter layer work?" --dfs

# Shortest path between two concepts
graphify path "MarketData" "CouncilResult"

# Plain-language explanation of one node
graphify explain "StructuredMatchProvider"

# Refresh after code changes (incremental — only new/changed files)
graphify update

# Regenerate the HTML view
graphify export html
```

Or open `graphify-out/graph.html` in any browser and explore visually.

## Notable findings (from the initial build)

- **`MarketData` is the system's universal input contract** — 52 edges
  crossing 20 of the 65 communities, in exact pipeline order (Gamma fetch →
  selection UI → match resolver → synthesizer/valuation → council → scoring
  → sidecar). This is the CLAUDE.md "all inter-module data via Pydantic
  models" rule made visible.
- **The provider-adapter layer is sealed**: `CandidateMatch` /
  `CompletedMatch` have edges only inside
  `src/pqi/ingestion/providers/`; downstream code sees only
  `StructuredMatchRecord`. Wide contract in, narrow contract out.
- Top god nodes: `MarketData` (52), `StructuredMatchProvider` (26),
  `CompletedMatch` (21), `build_briefing()` (20), `_run_analysis()` (19).

## Known caveats

- The initial build logged a health warning: **262 dangling-endpoint
  edges** (semantic doc→code references whose IDs don't exactly match
  AST-generated ones) and ~70 collapsed parallel edges (expected in the
  undirected build). The graph is fully usable; some doc↔code
  cross-references simply didn't land as edges.
- `.pqi/` runtime JSON files are excluded from extraction (they produce
  zero nodes by design).
- The graph is a **snapshot** — run `graphify update` after significant
  code changes to keep it current.
