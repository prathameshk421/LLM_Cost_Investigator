# Tier C failure analysis (draft, auto-generated)

Datasets: 240. Crashes: 0. Confident wrong (non-ambiguous): 0% [0-2]. Pass: True.

| case | n | crash | hit / silent ok | miss | confident wrong | unconfident wrong | why (count) |
|---|---|---|---|---|---|---|---|
| c01_slow_ramp | 20 | 0 | 95% [76-99] | 5% [1-24] | 0% [0-16] | 0% [0-16] | flagged but labelled no_strong_signal (1) |
| c02_first_hour | 20 | 0 | 0% [0-16] | 100% [84-100] | 0% [0-16] | 0% [0-16] | no window flagged (15); flagged but not investigated (max_anomalies cap) (4); flagged but labelled no_strong_signal (1) |
| c03_retry_new_ids | 20 | 0 | 0% [0-16] | 100% [84-100] | 0% [0-16] | 0% [0-16] | flagged but labelled no_strong_signal (11); no window flagged (9) |
| c04_bloat_no_parent | 20 | 0 | 0% [0-16] | 100% [84-100] | 0% [0-16] | 0% [0-16] | flagged but labelled no_strong_signal (20) |
| c05_unknown_model | 20 | 0 | 100% [84-100] | 0% [0-16] | 0% [0-16] | 0% [0-16] | - |
| c06_three_causes | 20 | 0 | 100% [84-100] | 0% [0-16] | 0% [0-16] | 0% [0-16] | - |
| c07_dupes_shuffle | 20 | 0 | 100% [84-100] | 0% [0-16] | 0% [0-16] | 0% [0-16] | - |
| c08_new_feature | 20 | 0 | 100% [84-100] silent | - | 0% [0-16] | 0% [0-16] | - |
| c09_planned_upgrade (ambiguous) | 20 | 0 | 0% [0-16] silent | - | 100% [84-100] | 0% [0-16] | - |
| c10_cache_miss | 20 | 0 | 100% [84-100] silent | - | 0% [0-16] | 0% [0-16] | - |
| c11_surge_retry | 20 | 0 | 90% [70-97] | 10% [3-30] | 0% [0-16] | 0% [0-16] | flagged but labelled no_strong_signal (2) |
| c12_hourly | 20 | 0 | 100% [84-100] | 0% [0-16] | 0% [0-16] | 0% [0-16] | - |

Intervals are Wilson 95%. Confident means root cause confidence >= 0.60.
Ambiguous cases are excluded from the pass rate.
