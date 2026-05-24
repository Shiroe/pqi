# Reliability diagram — 2026-04-21 (v2:preamble)

**Resolved rows analyzed:** 78 (non-legacy, with both entry and final yes_price).
**Outcome semantics:** directional winner — `final_yes_price > entry_yes_price ⇒ YES`.
**Bands:** EV-gate price bands `[0.09–0.50, 0.50–0.60, 0.60–0.70, 0.70–0.80, 0.80–0.95]` with `[0.00–0.09)` and `[0.95–1.00]` bookends.
**Thin-bin threshold:** flagged when n < 10.
**Flag filter:** `v2:preamble` (row-union label).
**Resolved rows by flag label:** `v1`=414, `v2:preamble`=78.

## Per-agent reliability

Read each row as: when this agent predicted P(YES) in this band, the average prediction was `pred_mean`, the realized YES rate was `realized`, and `gap = realized − pred_mean` (negative ⇒ overconfident on YES).

### rules_lawyer

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)       32      0.311      0.375   +0.064  [0.229, 0.547]  
  [0.50, 0.60)       18      0.557      0.611   +0.054  [0.386, 0.797]  
  [0.60, 0.70)       10      0.654      0.500   -0.154  [0.237, 0.763]  
  [0.70, 0.80)       13      0.738      0.846   +0.108  [0.578, 0.957]  
  [0.80, 0.95)        3      0.853      1.000   +0.147  [0.438, 1.000]  thin
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### bull

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)       35      0.289      0.457   +0.168  [0.305, 0.618]  
  [0.50, 0.60)        8      0.552      0.625   +0.073  [0.306, 0.863]  thin
  [0.60, 0.70)       11      0.653      0.273   -0.380  [0.097, 0.566]  
  [0.70, 0.80)       15      0.752      0.733   -0.019  [0.480, 0.891]  
  [0.80, 0.95)        7      0.850      1.000   +0.150  [0.646, 1.000]  thin
  [0.95, 1.00)        1      0.980      1.000   +0.020  [0.207, 1.000]  thin
```

### bear

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      1.000   +0.920  [0.207, 1.000]  thin
  [0.09, 0.50)       70      0.341      0.571   +0.231  [0.455, 0.681]  
  [0.50, 0.60)        6      0.553      0.333   -0.220  [0.097, 0.700]  thin
  [0.60, 0.70)        1      0.620      0.000   -0.620  [0.000, 0.793]  thin
  [0.70, 0.80)        0          -          -        -                 -  empty
  [0.80, 0.95)        0          -          -        -                 -  empty
  [0.95, 1.00)        0          -          -        -                 -  empty
```

### statistician

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)       34      0.306      0.412   +0.105  [0.264, 0.578]  
  [0.50, 0.60)       13      0.547      0.615   +0.068  [0.355, 0.823]  
  [0.60, 0.70)       13      0.653      0.538   -0.115  [0.291, 0.768]  
  [0.70, 0.80)       12      0.730      0.750   +0.020  [0.468, 0.911]  
  [0.80, 0.95)        4      0.825      1.000   +0.175  [0.510, 1.000]  thin
  [0.95, 1.00)        1      0.980      1.000   +0.020  [0.207, 1.000]  thin
```

### sentiment

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        2      0.080      0.000   -0.080  [0.000, 0.658]  thin
  [0.09, 0.50)       31      0.306      0.419   +0.113  [0.264, 0.592]  
  [0.50, 0.60)       14      0.551      0.643   +0.091  [0.388, 0.837]  
  [0.60, 0.70)       14      0.645      0.357   -0.288  [0.163, 0.612]  
  [0.70, 0.80)       14      0.732      0.929   +0.196  [0.685, 0.987]  
  [0.80, 0.95)        2      0.880      1.000   +0.120  [0.342, 1.000]  thin
  [0.95, 1.00)        1      0.980      1.000   +0.020  [0.207, 1.000]  thin
```

### specialist

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)       32      0.305      0.406   +0.101  [0.255, 0.577]  
  [0.50, 0.60)       11      0.543      0.636   +0.094  [0.354, 0.848]  
  [0.60, 0.70)       16      0.657      0.375   -0.282  [0.185, 0.614]  
  [0.70, 0.80)       12      0.732      0.917   +0.185  [0.646, 0.985]  
  [0.80, 0.95)        5      0.860      1.000   +0.140  [0.566, 1.000]  thin
  [0.95, 1.00)        1      0.950      1.000   +0.050  [0.207, 1.000]  thin
```

### contrarian

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        1      0.080      0.000   -0.080  [0.000, 0.793]  thin
  [0.09, 0.50)       42      0.329      0.476   +0.147  [0.334, 0.623]  
  [0.50, 0.60)        8      0.546      0.375   -0.171  [0.137, 0.694]  thin
  [0.60, 0.70)       16      0.650      0.625   -0.025  [0.386, 0.815]  
  [0.70, 0.80)        6      0.735      0.833   +0.098  [0.436, 0.970]  thin
  [0.80, 0.95)        5      0.872      1.000   +0.128  [0.566, 1.000]  thin
  [0.95, 1.00)        0          -          -        -                 -  empty
```

### council

```
  band                n  pred_mean   realized      gap            95% CI  flag
  ----------------------------------------------------------------------------
  [0.00, 0.09)        0          -          -        -                 -  empty
  [0.09, 0.50)       35      0.317      0.400   +0.083  [0.256, 0.564]  
  [0.50, 0.60)       15      0.544      0.600   +0.056  [0.357, 0.802]  
  [0.60, 0.70)       17      0.656      0.588   -0.068  [0.360, 0.784]  
  [0.70, 0.80)        7      0.730      0.857   +0.127  [0.487, 0.974]  thin
  [0.80, 0.95)        4      0.840      1.000   +0.160  [0.510, 1.000]  thin
  [0.95, 1.00)        0          -          -        -                 -  empty
```

## Brier scores

```
  agent               n    Brier
  --------------------------------
  rules_lawyer       78   0.2093
  bull               78   0.2300
  bear               78   0.3073
  statistician       78   0.2193
  sentiment          78   0.2197
  specialist         78   0.2174
  contrarian         78   0.2274
  council            78   0.2182
```

## Cross-agent diversity

- Rows used (≥ 2 agents present): **78**
- Mean pairwise Pearson correlation: **0.652**
- Mean per-row stddev (across agents): **0.088**
- Pairs with r ≥ 0.85: **15/21** (71%)

> **Mirror-collapse signal (cluster-level):** ≥ half of agent pairs correlate at r ≥ 0.85. Even when the global mean pairwise is dragged down by a single anti-correlated outlier, a high fraction of pairs at r ≥ 0.85 is the diagnostic. Confirms the Option-B shadow finding (sessions 26 + 32). Phase 1 (SHARED_PREAMBLE autonomy fix) is the unblock.

```
  pair                                   r
  ------------------------------------------
  rules_lawyer↔specialist            0.970
  rules_lawyer↔statistician          0.965
  statistician↔specialist            0.962
  statistician↔sentiment             0.962
  sentiment↔specialist               0.956
  rules_lawyer↔sentiment             0.942
  sentiment↔contrarian               0.938
  specialist↔contrarian              0.935
  bull↔contrarian                    0.923
  rules_lawyer↔contrarian            0.914
  bull↔sentiment                     0.913
  statistician↔contrarian            0.910
  bull↔specialist                    0.878
  bull↔statistician                  0.878
  rules_lawyer↔bull                  0.873
  bear↔specialist                    0.030
  rules_lawyer↔bear                  0.023
  bear↔statistician                  0.013
  bear↔sentiment                     0.002
  bear↔contrarian                   -0.121
  bull↔bear                         -0.182
```
