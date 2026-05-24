# Reliability diagram — 2026-04-20 (v2:preamble)

**Resolved rows analyzed:** 38 (non-legacy, with both entry and final yes_price).
**Outcome semantics:** directional winner — `final_yes_price > entry_yes_price ⇒ YES`.
**Bands:** EV-gate price bands `[0.09–0.50, 0.50–0.60, 0.60–0.70, 0.70–0.80, 0.80–0.95]` with `[0.00–0.09)` and `[0.95–1.00]` bookends.
**Thin-bin threshold:** flagged when n < 10.
**Flag filter:** `v2:preamble` (row-union label).
**Resolved rows by flag label:** `v1`=414, `v2:preamble`=38.

## Per-agent reliability

Read each row as: when this agent predicted P(YES) in this band, the average prediction was `pred_mean`, the realized YES rate was `realized`, and `gap = realized − pred_mean` (negative ⇒ overconfident on YES).

### rules_lawyer

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)       12      0.290      0.167   -0.123  [0.047, 0.448]  
  [0.50, 0.60)        9      0.558      0.556   -0.002  [0.267, 0.811]  thin
  [0.60, 0.70)        6      0.650      0.500   -0.150  [0.188, 0.812]  thin
  [0.70, 0.80)        6      0.740      0.667   -0.073  [0.300, 0.903]  thin
  [0.80, 0.95)        3      0.853      1.000   +0.147  [0.438, 1.000]  thin
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### bull

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)       14      0.274      0.286   +0.012  [0.117, 0.546]  
  [0.50, 0.60)        5      0.540      0.800   +0.260  [0.376, 0.964]  thin
  [0.60, 0.70)        4      0.650      0.000   -0.650  [0.000, 0.490]  thin
  [0.70, 0.80)        8      0.747      0.500   -0.247  [0.215, 0.785]  thin
  [0.80, 0.95)        5      0.862      1.000   +0.138  [0.566, 1.000]  thin
  [0.95, 1.00)        1      0.980      1.000   +0.020  [0.207, 1.000]  thin
```

### bear

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      1.000   +0.920  [0.207, 1.000]  thin
  [0.09, 0.50)       34      0.331      0.471   +0.140  [0.315, 0.633]  
  [0.50, 0.60)        3      0.547      0.333   -0.213  [0.061, 0.792]  thin
  [0.60, 0.70)        0          -          -        -                 -  empty
  [0.70, 0.80)        0          -          -        -                 -  empty
  [0.80, 0.95)        0          -          -        -                 -  empty
  [0.95, 1.00)        0          -          -        -                 -  empty
```

### statistician

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)       13      0.278      0.231   -0.048  [0.082, 0.503]  
  [0.50, 0.60)        8      0.545      0.625   +0.080  [0.306, 0.863]  thin
  [0.60, 0.70)        5      0.664      0.400   -0.264  [0.118, 0.769]  thin
  [0.70, 0.80)        7      0.737      0.571   -0.166  [0.250, 0.842]  thin
  [0.80, 0.95)        3      0.827      1.000   +0.173  [0.438, 1.000]  thin
  [0.95, 1.00)        1      0.980      1.000   +0.020  [0.207, 1.000]  thin
```

### sentiment

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        2      0.080      0.000   -0.080  [0.000, 0.658]  thin
  [0.09, 0.50)       12      0.292      0.250   -0.042  [0.089, 0.532]  
  [0.50, 0.60)        8      0.550      0.625   +0.075  [0.306, 0.863]  thin
  [0.60, 0.70)        6      0.657      0.167   -0.490  [0.030, 0.564]  thin
  [0.70, 0.80)        7      0.737      0.857   +0.120  [0.487, 0.974]  thin
  [0.80, 0.95)        2      0.880      1.000   +0.120  [0.342, 1.000]  thin
  [0.95, 1.00)        1      0.980      1.000   +0.020  [0.207, 1.000]  thin
```

### specialist

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)       12      0.281      0.167   -0.114  [0.047, 0.448]  
  [0.50, 0.60)        7      0.539      0.714   +0.176  [0.359, 0.918]  thin
  [0.60, 0.70)        9      0.667      0.333   -0.333  [0.121, 0.646]  thin
  [0.70, 0.80)        4      0.730      0.750   +0.020  [0.301, 0.954]  thin
  [0.80, 0.95)        4      0.872      1.000   +0.128  [0.510, 1.000]  thin
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### contrarian

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)       18      0.329      0.444   +0.116  [0.246, 0.663]  
  [0.50, 0.60)        5      0.540      0.200   -0.340  [0.036, 0.624]  thin
  [0.60, 0.70)        7      0.646      0.429   -0.217  [0.158, 0.750]  thin
  [0.70, 0.80)        3      0.733      0.667   -0.067  [0.208, 0.939]  thin
  [0.80, 0.95)        4      0.885      1.000   +0.115  [0.510, 1.000]  thin
  [0.95, 1.00)        0          -          -        -                 -  empty
```

### council

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        0          -          -        -                 -  empty
  [0.09, 0.50)       14      0.285      0.214   -0.071  [0.076, 0.476]  
  [0.50, 0.60)        8      0.533      0.625   +0.092  [0.306, 0.863]  thin
  [0.60, 0.70)        8      0.647      0.375   -0.272  [0.137, 0.694]  thin
  [0.70, 0.80)        4      0.725      0.750   +0.025  [0.301, 0.954]  thin
  [0.80, 0.95)        4      0.840      1.000   +0.160  [0.510, 1.000]  thin
  [0.95, 1.00)        0          -          -        -                 -  empty
```

## Brier scores

```
  agent               n    Brier
  --------------------------------
  rules_lawyer       38   0.1820
  bull               38   0.2118
  bear               38   0.2851
  statistician       38   0.2006
  sentiment          38   0.1891
  specialist         38   0.1932
  contrarian         38   0.2049
  council            38   0.1919
```

## Cross-agent diversity

- Rows used (≥ 2 agents present): **38**
- Mean pairwise Pearson correlation: **0.650**
- Mean per-row stddev (across agents): **0.098**
- Pairs with r ≥ 0.85: **15/21** (71%)

> **Mirror-collapse signal (cluster-level):** ≥ half of agent pairs correlate at r ≥ 0.85. Even when the global mean pairwise is dragged down by a single anti-correlated outlier, a high fraction of pairs at r ≥ 0.85 is the diagnostic. Confirms the Option-B shadow finding (sessions 26 + 32). Phase 1 (SHARED_PREAMBLE autonomy fix) is the unblock.

```
  pair                                   r
  ------------------------------------------
  rules_lawyer↔specialist            0.984
  statistician↔sentiment             0.979
  rules_lawyer↔statistician          0.965
  statistician↔specialist            0.965
  sentiment↔specialist               0.953
  statistician↔contrarian            0.949
  rules_lawyer↔sentiment             0.949
  sentiment↔contrarian               0.947
  specialist↔contrarian              0.946
  rules_lawyer↔contrarian            0.924
  bull↔sentiment                     0.902
  bull↔statistician                  0.897
  bull↔contrarian                    0.889
  bull↔specialist                    0.864
  rules_lawyer↔bull                  0.864
  bear↔specialist                    0.023
  rules_lawyer↔bear                  0.012
  bear↔statistician                 -0.037
  bear↔sentiment                    -0.041
  bear↔contrarian                   -0.106
  bull↔bear                         -0.171
```
