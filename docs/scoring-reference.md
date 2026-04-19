# Scoring Reference

Detailed gate stack, EV-gate banding, PerceivedTrueWinRate mechanics, and
Kelly sizing. Extracted from `CLAUDE.md` to keep the root context lean.
`CLAUDE.md` has the short-form formula; this file has the full derivation.

> Source of truth: `pqi/scoring.py` and `pqi/stats.py`. This doc is the
> human-readable companion.

---

## Two-sided score (`pqi/scoring.py`)

YES and NO sides are computed independently using their respective
confidence weights. An agent can be highly confident about one side but
uncertain about the other.

```python
# YES-side score
yes_score = Σ(prob_yes × conf_yes) / Σ(conf_yes)

# NO-side score
no_score  = Σ(prob_no × conf_no) / Σ(conf_no)     # prob_no = 1 − prob_yes

# WR-based edges (informational; gated by EV gate, not by edge)
edge      = perceived_win_rate − market.yes_price       # 0.0 when no WR snapshot
no_edge   = perceived_win_rate − (1 − market.yes_price) # 0.0 when no WR snapshot
```

Overall `confidence_level` label uses the higher of the two per-side means.
`CouncilResult.expected_roi` is persisted on every result (0.0 on PASS).

---

## Recommendation logic (ROI-aware, probability-first)

Max ROI of the recommended side: `expected_roi = (1 - side_price) /
side_price`. Purely a function of price — council conviction is gated
separately. A 70¢ share has a max ROI of 42.9% (sell at $1 = +30¢/70¢); an
80¢ share caps at 25%; a 90¢ share at 11.1%.

### Gate order inside `from_council_verdicts()`

1. **Price floor**: `side_price < MIN_TRADEABLE_PRICE` (0.09) → PASS
2. **Price ceiling**: `side_price > MAX_TRADEABLE_PRICE` (0.90) → PASS.
   Near-resolved; ROI doesn't justify execution risk.
3. **Contrarian** (council disagrees with market): requires all of
   - `council_prob >= MIN_CONTRARIAN_PROBABILITY` (0.60, session 32; session
     31's 0.65 was -EV in retrospective, the +$0.081/share signal lives in
     the 0.60–0.65 band),
   - `side_conf >= MIN_CONTRARIAN_CONFIDENCE` (0.80),
   - `expected_roi >= MIN_CONTRARIAN_ROI` (0.20).

   Label: `CONTRARIAN`.
4. **Market-aligned**: requires
   - `council_prob >= MIN_COUNCIL_PROBABILITY` (0.60),
   - `side_conf >= CONFIDENCE_HIGH_THRESHOLD` (0.67),
   - `expected_roi >= MIN_EXPECTED_ROI` (0.10).
5. **EV gate (final, banded — session 31)**: after a BUY clears all prior
   gates, reject it if `side_price >= band_wr` → PASS with reason
   `"Big Drawdown Risk: … ≥ band L-H WR XX.X% [n=K] (-EV)"`. `band_wr` is
   the Beta-binomial smoothed win rate for the price band containing
   `side_price`, drawn from `PerceivedStats.band_stats` and computed
   against the global rate with pseudo-count `BAND_SHRINKAGE_K` (default
   5). Bands: [0.09–0.50, 0.50–0.60, 0.60–0.70, 0.70–0.80, 0.80–0.95].
   When `perceived_stats` is absent or `BAND_SHRINKAGE_K=0`, the gate
   falls back to the scalar `PerceivedTrueWinRate` (legacy behavior).
   The gate only fires when `EV_GATE_ENABLED=true` AND
   `sample_size >= MIN_WIN_RATE_SAMPLES` (default 20). Below that
   threshold the gate is dormant.
6. Otherwise → PASS with dynamic `pass_reason`.

### Signal type on market-aligned BUYs (ROI-banded)

- `HIGH VALUE` — expected_roi ≥ 50% (cheap side; price ≤ ~0.667)
- `MODERATE VALUE` — 25% ≤ expected_roi < 50% (price ~0.667–0.80)
- `LOW MARGIN` — 10% ≤ expected_roi < 25% (price ~0.80–0.91)

### Deprecations + historical notes

`EDGE_THRESHOLD` is **deprecated** (kept so legacy `.env` files don't
error). `edge` / `no_edge` were repurposed in session 23 from
`(score − price)` to `(PerceivedTrueWinRate − side_price)` — positive edge
= side priced below historical hit rate. 0.0 when no WR snapshot.
Pre-session-23 history entries use the old semantic; `history show`
labels them.

---

## PerceivedTrueWinRate + Kelly (`pqi/stats.py`)

`pqi/stats.py` owns the EV inputs. Three pure functions:

- `recompute_from_history(min_samples)` — walks `history.jsonl`, counts
  `correct_buy` vs `wrong_buy` (non-legacy, resolved), computes per-band
  smoothed win rates against the global rate (Beta-binomial, pseudo-count
  `BAND_SHRINKAGE_K`), and writes a fresh `PerceivedStats` — including
  `band_stats` — to `~/.pqi/stats.json` (atomic tmp+rename).
- `load_perceived_stats()` — returns `PerceivedStats | None`; tolerates
  missing/malformed file (cold-start safety).
- `kelly_fraction(win_rate, price, multiplier=1.0)` — `f* = (bp - q) / b`.
  Returns 0.0 when -EV, on degenerate prices, or when the scaled result is
  ≤ 0. Apply `KELLY_MULTIPLIER` (default 0.5 = half-Kelly) for the UI stake.

### Band boundaries are a code constant

Band boundaries `[0.09–0.50, 0.50–0.60, 0.60–0.70, 0.70–0.80, 0.80–0.95]`
are a code constant in `stats.py` — changing them is a re-analysis
decision, not a tuning knob. Smoothing strength is tuned via
`BAND_SHRINKAGE_K` (default 5):

```
smoothed_wr = (wins + k·global_wr) / (n + k)
```

A band with `n=0` falls back to the global rate exactly; thin bands are
pulled toward global; dense bands dominate.

### Recompute trigger + integration

Recompute is triggered after every `history sync` (including `--auto`
background) and at the top of `history stats`. `scoring.py` loads stats
lazily at build time and passes both `perceived_win_rate` (scalar fallback)
and the full `perceived_stats` object to the model only when the gate is
active — `models.py` stays pure. Kelly sizing also uses the banded
threshold so gate-accepted bets always have non-zero advisory stake
(half-Kelly absorbs per-band estimation noise).
