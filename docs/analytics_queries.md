# Analytics Queries — `pqi query`

Ad-hoc SQL over `~/.pqi/history.jsonl` via DuckDB. Added in Phase 0 Task
0.1 to kill the "every metric needs a Python script" tax.

```
pqi query "SELECT <stuff> FROM history WHERE ..."
pqi query "SELECT v.<field> FROM verdicts GROUP BY ..."
```

Two views are pre-registered on every run:

| View | What's in it | Useful for |
|------|--------------|-----------|
| `history` | One row per council run, nested `result` / `resolution` / `analysis`. | Run-level aggregates (per-category hit rate, monthly PnL). |
| `verdicts` | Flattened `run_id × per-agent verdict` via `UNNEST(result.verdicts)`, non-legacy only. Verdict fields live under `v.*`. | Per-agent calibration, lean rate, Option B drift. |

Nested fields in `history` are accessed via dot notation — e.g.
`result.market.category`, `resolution.winning_side`,
`result.recommendation`. The `verdicts` view already flattens the common
run-level fields (`category`, `entry_yes_price`, `recommendation`,
`winning_side`, `final_yes_price`) alongside each `v.*` verdict.

Pipe `| less -R` for multi-row outputs; the default `--limit` is 100.

---

## Starter queries

### Q1 — Per-category BUY hit rate

How often each category's BUYs resolve on the correct side (directional,
based on final vs entry price).

```sql
SELECT
    result.market.category AS category,
    COUNT(*) AS n_buys,
    SUM(CASE
        WHEN (result.recommendation = 'BUY YES' AND resolution.final_yes_price > result.market.yes_price)
          OR (result.recommendation = 'BUY NO'  AND resolution.final_yes_price < result.market.yes_price)
        THEN 1 ELSE 0 END) AS correct_buys,
    ROUND(
        SUM(CASE
            WHEN (result.recommendation = 'BUY YES' AND resolution.final_yes_price > result.market.yes_price)
              OR (result.recommendation = 'BUY NO'  AND resolution.final_yes_price < result.market.yes_price)
            THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 3
    ) AS hit_rate
FROM history
WHERE legacy = false
  AND result.recommendation LIKE 'BUY%'
  AND resolution.final_yes_price IS NOT NULL
GROUP BY category
ORDER BY n_buys DESC
```

### Q2 — Per-agent lean rate

Directional hit rate per agent with a 0.45–0.55 dead band (coin-flip
reads don't get credit). Uses `_directional_winner` semantics —
intermediate resolutions (e.g. final 0.50 from entry 0.40) count as
YES because YES gained money.

```sql
SELECT
    v.agent_name,
    COUNT(*) FILTER (WHERE v.probability > 0.55 OR v.probability < 0.45) AS lean_n,
    SUM(CASE
        WHEN v.probability > 0.55 AND final_yes_price > entry_yes_price THEN 1
        WHEN v.probability < 0.45 AND final_yes_price < entry_yes_price THEN 1
        ELSE 0 END) AS lean_hits,
    ROUND(
        SUM(CASE
            WHEN v.probability > 0.55 AND final_yes_price > entry_yes_price THEN 1
            WHEN v.probability < 0.45 AND final_yes_price < entry_yes_price THEN 1
            ELSE 0 END) * 1.0 /
        NULLIF(COUNT(*) FILTER (WHERE v.probability > 0.55 OR v.probability < 0.45), 0),
        3
    ) AS lean_rate
FROM verdicts
WHERE final_yes_price IS NOT NULL
  AND final_yes_price <> entry_yes_price
GROUP BY v.agent_name
ORDER BY v.agent_name
```

### Q3 — Monthly PnL per share

Sum of BUY outcomes per calendar month. Positive = edge; negative =
anti-edge.

```sql
SELECT
    strftime(timestamp, '%Y-%m') AS month,
    COUNT(*) FILTER (WHERE result.recommendation LIKE 'BUY%') AS n_buys,
    ROUND(SUM(CASE
        WHEN result.recommendation = 'BUY YES' THEN resolution.final_yes_price - result.market.yes_price
        WHEN result.recommendation = 'BUY NO'  THEN result.market.yes_price - resolution.final_yes_price
        ELSE 0 END), 3) AS total_pnl_per_share
FROM history
WHERE legacy = false
  AND result.recommendation LIKE 'BUY%'
  AND resolution.final_yes_price IS NOT NULL
GROUP BY month
ORDER BY month
```

### Q4 — Realized rate by price band (EV gate bands)

Realized BUY hit rate per EV-gate band. Compare against
`PerceivedStats.band_stats` (in `~/.pqi/stats.json`) to see whether the
gate's Beta-binomial smoothed thresholds match realization. Bands match
the scoring-layer constants.

```sql
WITH buys AS (
    SELECT
        CASE WHEN result.recommendation = 'BUY YES' THEN result.market.yes_price
             ELSE 1.0 - result.market.yes_price END AS side_price,
        CASE
            WHEN (result.recommendation = 'BUY YES' AND resolution.final_yes_price > result.market.yes_price)
              OR (result.recommendation = 'BUY NO'  AND resolution.final_yes_price < result.market.yes_price)
            THEN 1 ELSE 0 END AS win
    FROM history
    WHERE legacy = false
      AND result.recommendation LIKE 'BUY%'
      AND resolution.final_yes_price IS NOT NULL
)
SELECT
    CASE
        WHEN side_price < 0.50 THEN '[0.09, 0.50)'
        WHEN side_price < 0.60 THEN '[0.50, 0.60)'
        WHEN side_price < 0.70 THEN '[0.60, 0.70)'
        WHEN side_price < 0.80 THEN '[0.70, 0.80)'
        ELSE                          '[0.80, 0.95]'
    END AS price_band,
    COUNT(*) AS n,
    SUM(win) AS wins,
    ROUND(AVG(win * 1.0), 3) AS realized_rate,
    ROUND(AVG(side_price), 3) AS mean_price
FROM buys
GROUP BY price_band
ORDER BY price_band
```

### Q5 — Missed opportunities

PASSes on high-conviction events that resolved on the council's
favoured side — i.e. the gate kept us out of a winner. The
`pass_reason` column tells you which gate actually fired.

```sql
SELECT
    SUBSTRING(run_id, 1, 8) AS run,
    result.market.category AS category,
    ROUND(result.market.yes_price, 3) AS entry_yes,
    ROUND(result.final_score, 3) AS council_yes,
    ROUND(result.no_score, 3) AS council_no,
    resolution.winning_side AS winner,
    result.pass_reason AS pass_reason
FROM history
WHERE legacy = false
  AND result.recommendation = 'PASS'
  AND resolution.winning_side IS NOT NULL
  AND (
    (resolution.winning_side = 'YES' AND result.final_score > 0.65)
    OR
    (resolution.winning_side = 'NO' AND result.no_score > 0.65)
  )
ORDER BY GREATEST(result.final_score, result.no_score) DESC
LIMIT 20
```

---

## Tips

- `history` has nested structs — quote paths with dots, e.g.
  `result.market.yes_price`. DuckDB resolves these natively; no JSON
  extraction functions needed.
- `verdicts` already flattens `(run_id, timestamp, category, question,
  entry_yes_price, recommendation, pass_reason, signal_type,
  final_score, no_score, winning_side, final_yes_price)` plus the full
  verdict under `v.*` — reach for this view when you want per-agent
  aggregates without writing `UNNEST` yourself.
- Filter on `legacy = false` in `history` whenever you care about the
  modern schema (pre-session-4 rows have only flat scalars). The
  `verdicts` view already excludes them.
- Directional winner: `final_yes_price > entry_yes_price` means YES
  gained. Use this instead of `resolution.winning_side` when you want
  intermediate settlements (e.g. 0.50 final from 0.40 entry) to count
  toward agent-calibration metrics. Exact ties
  (`final_yes_price = entry_yes_price`) should be excluded from lean
  rates to avoid skew.
- `~/.pqi/stats.json` holds the banded-WR thresholds that the EV gate
  actually uses. Pair Q4 with `cat ~/.pqi/stats.json | jq
  '.band_stats'` when auditing gate calibration.
- Performance: the 13MB current file aggregates in ~130ms cold. If this
  ever bites, a one-shot flattened sidecar (`~/.pqi/verdicts.jsonl`)
  can be emitted — but don't optimise pre-emptively.
