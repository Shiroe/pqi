# Reliability diagram — 2026-04-20 (v1)

**Resolved rows analyzed:** 414 (non-legacy, with both entry and final yes_price).
**Outcome semantics:** directional winner — `final_yes_price > entry_yes_price ⇒ YES`.
**Bands:** EV-gate price bands `[0.09–0.50, 0.50–0.60, 0.60–0.70, 0.70–0.80, 0.80–0.95]` with `[0.00–0.09)` and `[0.95–1.00]` bookends.
**Thin-bin threshold:** flagged when n < 10.
**Flag filter:** `v1` (row-union label).
**Resolved rows by flag label:** `v1`=414, `v2:preamble`=38.

## Per-agent reliability

Read each row as: when this agent predicted P(YES) in this band, the average prediction was `pred_mean`, the realized YES rate was `realized`, and `gap = realized − pred_mean` (negative ⇒ overconfident on YES).

### rules_lawyer

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        4      0.050      0.250   +0.200  [0.046, 0.699]  thin
  [0.09, 0.50)      199      0.322      0.327   +0.005  [0.265, 0.395]  
  [0.50, 0.60)       55      0.552      0.509   -0.043  [0.381, 0.636]  
  [0.60, 0.70)       93      0.643      0.581   -0.062  [0.479, 0.676]  
  [0.70, 0.80)       54      0.739      0.833   +0.094  [0.713, 0.910]  
  [0.80, 0.95)        5      0.840      1.000   +0.160  [0.566, 1.000]  thin
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### bull

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)      110      0.287      0.291   +0.004  [0.214, 0.382]  
  [0.50, 0.60)       60      0.569      0.417   -0.153  [0.301, 0.543]  
  [0.60, 0.70)      142      0.645      0.549   -0.096  [0.467, 0.629]  
  [0.70, 0.80)       75      0.744      0.667   -0.078  [0.554, 0.763]  
  [0.80, 0.95)       23      0.843      0.609   -0.234  [0.408, 0.778]  
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### bear

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        2      0.080      1.000   +0.920  [0.342, 1.000]  thin
  [0.09, 0.50)      379      0.353      0.488   +0.135  [0.438, 0.538]  
  [0.50, 0.60)        8      0.550      0.375   -0.175  [0.137, 0.694]  thin
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
  [0.09, 0.50)      192      0.317      0.328   +0.011  [0.266, 0.397]  
  [0.50, 0.60)       74      0.546      0.527   -0.019  [0.415, 0.637]  
  [0.60, 0.70)       87      0.645      0.609   -0.036  [0.504, 0.705]  
  [0.70, 0.80)       54      0.733      0.778   +0.045  [0.651, 0.868]  
  [0.80, 0.95)        3      0.820      1.000   +0.180  [0.438, 1.000]  thin
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### sentiment

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)      198      0.317      0.318   +0.001  [0.257, 0.386]  
  [0.50, 0.60)       61      0.558      0.557   -0.001  [0.433, 0.675]  
  [0.60, 0.70)       90      0.641      0.600   -0.041  [0.497, 0.695]  
  [0.70, 0.80)       58      0.738      0.759   +0.021  [0.635, 0.850]  
  [0.80, 0.95)        5      0.836      1.000   +0.164  [0.566, 1.000]  thin
  [0.95, 1.00)        1      0.990      1.000   +0.010  [0.207, 1.000]  thin
```

### specialist

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        3      0.080      0.000   -0.080  [0.000, 0.562]  thin
  [0.09, 0.50)      193      0.318      0.326   +0.009  [0.264, 0.395]  
  [0.50, 0.60)       58      0.550      0.517   -0.033  [0.392, 0.641]  
  [0.60, 0.70)       88      0.644      0.602   -0.041  [0.498, 0.698]  
  [0.70, 0.80)       65      0.734      0.738   +0.005  [0.620, 0.830]  
  [0.80, 0.95)        5      0.832      1.000   +0.168  [0.566, 1.000]  thin
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### contrarian

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        2      0.080      0.000   -0.080  [0.000, 0.658]  thin
  [0.09, 0.50)      256      0.321      0.363   +0.042  [0.307, 0.424]  
  [0.50, 0.60)       55      0.564      0.655   +0.091  [0.523, 0.766]  
  [0.60, 0.70)       65      0.640      0.646   +0.006  [0.525, 0.751]  
  [0.70, 0.80)       33      0.732      0.848   +0.116  [0.691, 0.933]  
  [0.80, 0.95)        1      0.940      1.000   +0.060  [0.207, 1.000]  thin
  [0.95, 1.00)        0          -          -        -                 -  empty
```

### council

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        0          -          -        -                 -  empty
  [0.09, 0.50)      202      0.350      0.327   -0.023  [0.266, 0.394]  
  [0.50, 0.60)       95      0.553      0.505   -0.048  [0.406, 0.604]  
  [0.60, 0.70)       95      0.643      0.716   +0.073  [0.618, 0.797]  
  [0.70, 0.80)       20      0.735      0.850   +0.115  [0.640, 0.948]  
  [0.80, 0.95)        2      0.849      1.000   +0.151  [0.342, 1.000]  thin
  [0.95, 1.00)        0          -          -        -                 -  empty
```

## Brier scores

```
  agent               n    Brier
  --------------------------------
  rules_lawyer      411   0.2147
  bull              412   0.2359
  bear              395   0.2822
  statistician      414   0.2169
  sentiment         414   0.2154
  specialist        413   0.2155
  contrarian        412   0.2191
  council           414   0.2147
```

## Cross-agent diversity

- Rows used (≥ 2 agents present): **414**
- Mean pairwise Pearson correlation: **0.560**
- Mean per-row stddev (across agents): **0.096**
- Pairs with r ≥ 0.85: **9/21** (43%)

> Partial-collapse signal: a non-trivial fraction of pairs sit at r ≥ 0.85, but the council is not uniformly mirrored. Inspect the per-pair table below to see which agents form the cluster and which retain independence — the latter are where Phase 1 should preserve, not destabilize, the existing signal.

```
  pair                                   r
  ------------------------------------------
  statistician↔specialist            0.921
  rules_lawyer↔statistician          0.915
  statistician↔sentiment             0.915
  sentiment↔specialist               0.911
  rules_lawyer↔sentiment             0.910
  rules_lawyer↔specialist            0.903
  sentiment↔contrarian               0.876
  statistician↔contrarian            0.875
  rules_lawyer↔contrarian            0.871
  specialist↔contrarian              0.816
  rules_lawyer↔bull                  0.622
  bull↔sentiment                     0.619
  bull↔contrarian                    0.609
  bull↔specialist                    0.592
  bull↔statistician                  0.580
  bear↔statistician                  0.033
  bear↔sentiment                     0.027
  bear↔specialist                    0.022
  rules_lawyer↔bear                 -0.018
  bear↔contrarian                   -0.071
  bull↔bear                         -0.171
```
