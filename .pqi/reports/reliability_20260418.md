# Reliability diagram — 2026-04-18

**Resolved rows analyzed:** 385 (non-legacy, with both entry and final yes_price).
**Outcome semantics:** directional winner — `final_yes_price > entry_yes_price ⇒ YES`.
**Bands:** EV-gate price bands `[0.09–0.50, 0.50–0.60, 0.60–0.70, 0.70–0.80, 0.80–0.95]` with `[0.00–0.09)` and `[0.95–1.00]` bookends.
**Thin-bin threshold:** flagged when n < 10.

## Per-agent reliability

Read each row as: when this agent predicted P(YES) in this band, the average prediction was `pred_mean`, the realized YES rate was `realized`, and `gap = realized − pred_mean` (negative ⇒ overconfident on YES).

### rules_lawyer

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        4      0.050      0.250   +0.200  [0.046, 0.699]  thin
  [0.09, 0.50)      183      0.325      0.328   +0.003  [0.264, 0.399]  
  [0.50, 0.60)       53      0.551      0.528   -0.022  [0.397, 0.656]  
  [0.60, 0.70)       88      0.643      0.557   -0.086  [0.453, 0.656]  
  [0.70, 0.80)       50      0.740      0.820   +0.080  [0.692, 0.902]  
  [0.80, 0.95)        3      0.847      1.000   +0.153  [0.438, 1.000]  thin
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### bull

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)      100      0.289      0.290   +0.001  [0.210, 0.385]  
  [0.50, 0.60)       60      0.569      0.417   -0.153  [0.301, 0.543]  
  [0.60, 0.70)      136      0.645      0.544   -0.101  [0.460, 0.625]  
  [0.70, 0.80)       69      0.744      0.652   -0.092  [0.534, 0.754]  
  [0.80, 0.95)       16      0.844      0.625   -0.219  [0.386, 0.815]  
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### bear

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        2      0.080      1.000   +0.920  [0.342, 1.000]  thin
  [0.09, 0.50)      351      0.356      0.484   +0.128  [0.433, 0.536]  
  [0.50, 0.60)        7      0.554      0.286   -0.269  [0.082, 0.641]  thin
  [0.60, 0.70)        5      0.632      0.200   -0.432  [0.036, 0.624]  thin
  [0.70, 0.80)        1      0.720      0.000   -0.720  [0.000, 0.793]  thin
  [0.80, 0.95)        0          -          -        -                 -  empty
  [0.95, 1.00)        0          -          -        -                 -  empty
```

### statistician

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        3      0.080      0.000   -0.080  [0.000, 0.562]  thin
  [0.09, 0.50)      177      0.322      0.328   +0.006  [0.263, 0.400]  
  [0.50, 0.60)       72      0.545      0.542   -0.003  [0.427, 0.652]  
  [0.60, 0.70)       83      0.643      0.590   -0.053  [0.483, 0.690]  
  [0.70, 0.80)       47      0.733      0.766   +0.033  [0.628, 0.864]  
  [0.80, 0.95)        2      0.820      1.000   +0.180  [0.342, 1.000]  thin
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### sentiment

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)      182      0.322      0.319   -0.003  [0.255, 0.390]  
  [0.50, 0.60)       60      0.558      0.567   +0.009  [0.441, 0.684]  
  [0.60, 0.70)       85      0.640      0.588   -0.052  [0.482, 0.687]  
  [0.70, 0.80)       52      0.736      0.731   -0.006  [0.597, 0.832]  
  [0.80, 0.95)        4      0.835      1.000   +0.165  [0.510, 1.000]  thin
  [0.95, 1.00)        1      0.990      1.000   +0.010  [0.207, 1.000]  thin
```

### specialist

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        3      0.080      0.000   -0.080  [0.000, 0.562]  thin
  [0.09, 0.50)      177      0.324      0.328   +0.004  [0.263, 0.400]  
  [0.50, 0.60)       57      0.550      0.526   -0.024  [0.399, 0.650]  
  [0.60, 0.70)       83      0.643      0.590   -0.053  [0.483, 0.690]  
  [0.70, 0.80)       59      0.733      0.712   -0.021  [0.586, 0.812]  
  [0.80, 0.95)        4      0.835      1.000   +0.165  [0.510, 1.000]  thin
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### contrarian

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        2      0.080      0.000   -0.080  [0.000, 0.658]  thin
  [0.09, 0.50)      241      0.326      0.365   +0.039  [0.307, 0.428]  
  [0.50, 0.60)       54      0.564      0.667   +0.103  [0.534, 0.778]  
  [0.60, 0.70)       59      0.639      0.627   -0.012  [0.500, 0.739]  
  [0.70, 0.80)       26      0.733      0.846   +0.113  [0.665, 0.939]  
  [0.80, 0.95)        1      0.940      1.000   +0.060  [0.207, 1.000]  thin
  [0.95, 1.00)        0          -          -        -                 -  empty
```

### council

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        0          -          -        -                 -  empty
  [0.09, 0.50)      187      0.354      0.326   -0.028  [0.263, 0.396]  
  [0.50, 0.60)       93      0.553      0.516   -0.037  [0.416, 0.615]  
  [0.60, 0.70)       88      0.642      0.705   +0.063  [0.602, 0.790]  
  [0.70, 0.80)       15      0.734      0.800   +0.066  [0.548, 0.930]  
  [0.80, 0.95)        2      0.849      1.000   +0.151  [0.342, 1.000]  thin
  [0.95, 1.00)        0          -          -        -                 -  empty
```

## Brier scores

```
  agent               n    Brier
  --------------------------------
  rules_lawyer      382   0.2180
  bull              383   0.2370
  bear              366   0.2810
  statistician      385   0.2199
  sentiment         385   0.2196
  specialist        384   0.2195
  contrarian        383   0.2215
  council           385   0.2176
```

## Cross-agent diversity

- Rows used (≥ 2 agents present): **385**
- Mean pairwise Pearson correlation: **0.557**
- Mean per-row stddev (across agents): **0.094**
- Pairs with r ≥ 0.85: **9/21** (43%)

> Partial-collapse signal: a non-trivial fraction of pairs sit at r ≥ 0.85, but the council is not uniformly mirrored. Inspect the per-pair table below to see which agents form the cluster and which retain independence — the latter are where Phase 1 should preserve, not destabilize, the existing signal.

```
  pair                                   r
  ------------------------------------------
  statistician↔specialist            0.918
  statistician↔sentiment             0.911
  rules_lawyer↔statistician          0.911
  rules_lawyer↔sentiment             0.904
  sentiment↔specialist               0.901
  rules_lawyer↔specialist            0.895
  sentiment↔contrarian               0.871
  rules_lawyer↔contrarian            0.866
  statistician↔contrarian            0.862
  specialist↔contrarian              0.804
  rules_lawyer↔bull                  0.628
  bull↔sentiment                     0.621
  bull↔contrarian                    0.603
  bull↔specialist                    0.591
  bull↔statistician                  0.575
  bear↔statistician                  0.039
  bear↔sentiment                     0.027
  bear↔specialist                    0.023
  rules_lawyer↔bear                 -0.020
  bear↔contrarian                   -0.062
  bull↔bear                         -0.170
```
