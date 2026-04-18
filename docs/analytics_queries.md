# Analytics Queries — `pqi query`

Ad-hoc SQL over `~/.pqi/history.jsonl` via DuckDB. Added in Phase 0 Task
0.1 to kill the "every metric needs a Python script" tax.

```
pqi query "SELECT <stuff> FROM history WHERE ..."
pqi query "SELECT v.<field> FROM verdicts GROUP BY ..."
```

Three views are pre-registered on every run:

| View | What's in it | Useful for |
|------|--------------|-----------|
| `history` | One row per council run, nested `result` / `resolution` / `analysis`. | Run-level aggregates (per-category hit rate, monthly PnL). |
| `verdicts` | Flattened `run_id × per-agent verdict` via `UNNEST(result.verdicts)`, non-legacy only. Verdict fields live under `v.*`. | Per-agent calibration, lean rate, Option B drift. |
| `clv_by_row` | One row per resolved BUY with pre-event CLV computed from CLOB hourly history (`scripts/backfill_clv.py` → `~/.pqi/clv.jsonl`). Registered only when the sidecar exists. | Entry-quality / prediction-edge analysis independent of win/loss. |

Nested fields in `history` are accessed via dot notation — e.g.
`result.market.category`, `resolution.winning_side`,
`result.recommendation`. The `verdicts` view already flattens the common
run-level fields (`category`, `entry_yes_price`, `recommendation`,
`winning_side`, `final_yes_price`) alongside each `v.*` verdict.

`clv_by_row` columns: `run_id`, `timestamp`, `category`,
`event_start_time`, `recommendation`, `entry_price`, `bought_token_id`,
`bucket_timestamp`, `bucket_drift_minutes` (≤ 0 by construction — we
pick the latest CLOB bucket *before* `event_start_time`), `bucket_price`,
`clv` (= `bucket_price − entry_price`), `available` (bool),
`unavailable_reason` (`no_event_start` / `no_tokens` / `fetch_failed`
/ `no_bucket_before`, null when available). Join to `history` /
`verdicts` on `run_id`.

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

### Q5 — Pre-event CLV distribution (`clv_by_row`)

Sanity-check the backfill: overall distribution, coverage, per-category
breakdown. Run `python -m pqi.scripts.backfill_clv` first if the view
doesn't exist — `~/.pqi/clv.jsonl` is the sidecar.

```sql
SELECT
    COUNT(*)                                     AS n_rows,
    COUNT(*) FILTER (WHERE available)            AS available,
    ROUND(AVG(clv) FILTER (WHERE available), 4)    AS mean_clv,
    ROUND(STDDEV(clv) FILTER (WHERE available), 4) AS stddev_clv,
    ROUND(MIN(clv) FILTER (WHERE available), 4)    AS min_clv,
    ROUND(MAX(clv) FILTER (WHERE available), 4)    AS max_clv
FROM clv_by_row
```

Per-category slice — where is entry quality good vs bad?

```sql
SELECT
    category,
    COUNT(*) FILTER (WHERE available)            AS n,
    ROUND(AVG(clv) FILTER (WHERE available), 4)    AS mean_clv,
    ROUND(STDDEV(clv) FILTER (WHERE available), 4) AS stddev_clv
FROM clv_by_row
GROUP BY category
ORDER BY n DESC
```

Coverage / unavailability audit — make sure the 32% gap is all
`no_tokens` (pre-session-22 rows) and not a silent bug.

```sql
SELECT
    unavailable_reason,
    COUNT(*) AS n
FROM clv_by_row
WHERE NOT available
GROUP BY unavailable_reason
ORDER BY n DESC
```

CLV vs realized outcome — does a good entry correlate with a winning
bet? Small-sample caveat applies until more BUYs land.

```sql
WITH outcomes AS (
    SELECT
        run_id,
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
    CASE WHEN c.clv >= 0 THEN 'CLV >= 0 (good entry)' ELSE 'CLV < 0 (bad entry)' END AS bucket,
    COUNT(*)              AS n,
    SUM(o.win)            AS wins,
    ROUND(AVG(o.win * 1.0), 3) AS win_rate,
    ROUND(AVG(c.clv), 4)  AS mean_clv
FROM clv_by_row c
JOIN outcomes o USING (run_id)
WHERE c.available
GROUP BY bucket
ORDER BY bucket DESC
```

### Q6 — Missed opportunities

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
- `~/.pqi/clv.jsonl` is the pre-event CLV sidecar (session 35, Phase 0
  Task 0.2). Regenerate with `python -m pqi.scripts.backfill_clv` —
  idempotent; pass `--rebuild` to force full recompute. Bucket policy
  is last-before-target (avoids pulling the post-event bucket, which
  contains resolution-side information). The `clv_by_row` DuckDB view
  auto-registers when the sidecar exists; queries against it raise a
  clear error otherwise.
- CLV interpretation: positive = we bought below the pre-event market
  mark (information edge on entry); negative = we bought above mark.
  Independent of whether the bet won. Pair with realized outcome (Q5
  last block) to probe whether entry quality correlates with wins.
- Performance: the 13MB current file aggregates in ~130ms cold. If this
  ever bites, a one-shot flattened sidecar (`~/.pqi/verdicts.jsonl`)
  can be emitted — but don't optimise pre-emptively.

---

## Companion artifacts

- **Reliability diagram** (`python -m scripts.reliability_diagram`,
  Phase 0 Task 0.3, session 36). Per-agent + council reliability table
  bucketed into the EV-gate price bands, plus per-agent Brier and a
  cross-agent correlation diagnostic for the mirror-collapse signal.
  Operates over every non-legacy resolved row (full ~400-row set, not
  just the 65 BUY-triggered ones) using directional-winner outcome
  semantics. Output: ASCII to stdout + markdown to
  `~/.pqi/reports/reliability_YYYYMMDD.md`. Pass `--no-write` to skip
  the file write.
