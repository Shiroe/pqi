# Reliability diagram — 2026-04-19

**Resolved rows analyzed:** 409 (non-legacy, with both entry and final yes_price).
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
  [0.09, 0.50)      197      0.322      0.330   +0.008  [0.268, 0.398]  
  [0.50, 0.60)       54      0.551      0.519   -0.033  [0.389, 0.646]  
  [0.60, 0.70)       92      0.643      0.576   -0.067  [0.474, 0.672]  
  [0.70, 0.80)       53      0.739      0.830   +0.091  [0.708, 0.908]  
  [0.80, 0.95)        5      0.840      1.000   +0.160  [0.566, 1.000]  thin
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### bull

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)      108      0.287      0.296   +0.010  [0.218, 0.388]  
  [0.50, 0.60)       60      0.569      0.417   -0.153  [0.301, 0.543]  
  [0.60, 0.70)      141      0.645      0.553   -0.091  [0.471, 0.633]  
  [0.70, 0.80)       74      0.744      0.662   -0.082  [0.549, 0.760]  
  [0.80, 0.95)       22      0.844      0.591   -0.253  [0.387, 0.767]  
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### bear

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        2      0.080      1.000   +0.920  [0.342, 1.000]  thin
  [0.09, 0.50)      374      0.353      0.489   +0.136  [0.439, 0.540]  
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
  [0.09, 0.50)      190      0.317      0.332   +0.015  [0.269, 0.401]  
  [0.50, 0.60)       73      0.545      0.534   -0.011  [0.421, 0.644]  
  [0.60, 0.70)       87      0.645      0.609   -0.036  [0.504, 0.705]  
  [0.70, 0.80)       52      0.732      0.769   +0.037  [0.639, 0.863]  
  [0.80, 0.95)        3      0.820      1.000   +0.180  [0.438, 1.000]  thin
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### sentiment

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)      196      0.317      0.321   +0.004  [0.260, 0.390]  
  [0.50, 0.60)       61      0.558      0.557   -0.001  [0.433, 0.675]  
  [0.60, 0.70)       89      0.641      0.607   -0.035  [0.503, 0.702]  
  [0.70, 0.80)       56      0.736      0.750   +0.014  [0.623, 0.845]  
  [0.80, 0.95)        5      0.836      1.000   +0.164  [0.566, 1.000]  thin
  [0.95, 1.00)        1      0.990      1.000   +0.010  [0.207, 1.000]  thin
```

### specialist

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        3      0.080      0.000   -0.080  [0.000, 0.562]  thin
  [0.09, 0.50)      191      0.318      0.330   +0.012  [0.267, 0.399]  
  [0.50, 0.60)       58      0.550      0.517   -0.033  [0.392, 0.641]  
  [0.60, 0.70)       87      0.644      0.609   -0.035  [0.504, 0.705]  
  [0.70, 0.80)       63      0.733      0.730   -0.003  [0.610, 0.824]  
  [0.80, 0.95)        5      0.832      1.000   +0.168  [0.566, 1.000]  thin
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### contrarian

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        2      0.080      0.000   -0.080  [0.000, 0.658]  thin
  [0.09, 0.50)      254      0.322      0.366   +0.045  [0.309, 0.427]  
  [0.50, 0.60)       55      0.564      0.655   +0.091  [0.523, 0.766]  
  [0.60, 0.70)       64      0.640      0.656   +0.016  [0.534, 0.761]  
  [0.70, 0.80)       31      0.733      0.839   +0.106  [0.674, 0.929]  
  [0.80, 0.95)        1      0.940      1.000   +0.060  [0.207, 1.000]  thin
  [0.95, 1.00)        0          -          -        -                 -  empty
```

### council

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        0          -          -        -                 -  empty
  [0.09, 0.50)      200      0.350      0.330   -0.020  [0.269, 0.398]  
  [0.50, 0.60)       95      0.553      0.505   -0.048  [0.406, 0.604]  
  [0.60, 0.70)       94      0.643      0.723   +0.080  [0.626, 0.804]  
  [0.70, 0.80)       18      0.736      0.833   +0.097  [0.608, 0.942]  
  [0.80, 0.95)        2      0.849      1.000   +0.151  [0.342, 1.000]  thin
  [0.95, 1.00)        0          -          -        -                 -  empty
```

## Brier scores

```
  agent               n    Brier
  --------------------------------
  rules_lawyer      406   0.2154
  bull              407   0.2370
  bear              390   0.2820
  statistician      409   0.2179
  sentiment         409   0.2164
  specialist        408   0.2166
  contrarian        407   0.2201
  council           409   0.2156
```

## Cross-agent diversity

- Rows used (≥ 2 agents present): **409**
- Mean pairwise Pearson correlation: **0.560**
- Mean per-row stddev (across agents): **0.096**
- Pairs with r ≥ 0.85: **9/21** (43%)

> Partial-collapse signal: a non-trivial fraction of pairs sit at r ≥ 0.85, but the council is not uniformly mirrored. Inspect the per-pair table below to see which agents form the cluster and which retain independence — the latter are where Phase 1 should preserve, not destabilize, the existing signal.

```
  pair                                   r
  ------------------------------------------
  statistician↔specialist            0.920
  rules_lawyer↔statistician          0.915
  statistician↔sentiment             0.914
  rules_lawyer↔sentiment             0.910
  sentiment↔specialist               0.910
  rules_lawyer↔specialist            0.903
  sentiment↔contrarian               0.873
  statistician↔contrarian            0.873
  rules_lawyer↔contrarian            0.871
  specialist↔contrarian              0.812
  rules_lawyer↔bull                  0.618
  bull↔sentiment                     0.613
  bull↔contrarian                    0.602
  bull↔specialist                    0.584
  bull↔statistician                  0.573
  bear↔statistician                  0.043
  bear↔sentiment                     0.037
  bear↔specialist                    0.033
  rules_lawyer↔bear                 -0.011
  bear↔contrarian                   -0.061
  bull↔bear                         -0.164
```
