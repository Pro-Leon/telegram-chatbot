# Evaluation Report — balanced

**simulation_id:** `bcde21b6-c2f5-4b8d-8b29-ec6a33e1dc76`
**seed:** `123` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `78a884ea023932eb`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:20:59.155518+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.7946|
| brier | 0.2740|
| calibration_gap | 0.0378|
| accuracy | 0.667|
| precision | 0.500|
| recall | 0.400|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 8 | 0.2431 | 0.7232 |
| LOW | 2 | 0.1531 | 0.4962 |
| MID | 5 | 0.3717 | 1.0282 |
| lifecycle:cold | 4 | 0.3599 | 1.0259 |
| lifecycle:hot | 1 | 0.0045 | 0.0696 |
| lifecycle:warm | 10 | 0.2665 | 0.7746 |
| offer:CORE_BUNDLE | 6 | 0.0900 | 0.3286 |
| offer:PREMIUM | 5 | 0.2368 | 0.6817 |
| offer:SINGLE | 2 | 0.7868 | 2.1826 |
| offer:SMALL_BUNDLE | 2 | 0.4058 | 1.0869 |
| price:HIGH | 8 | 0.2431 | 0.7232 |
| price:LOW | 2 | 0.1531 | 0.4962 |
| price:MID | 5 | 0.3717 | 1.0282 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.300 |
| fan_recent_rate | 0.300 |
| global_rate | 0.300 |
| offer_type_mean_rate | 0.303 |
| offer_type_most_frequent_rate | 0.143 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9544613 | 0.382 | 1 |
| 9272879 | 0.022 | 1 |
| 9635358 | 0.052 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | 78a884ea | 0.2740 | 0.7946 |
| 90 | 78a884ea | 0.2740 | 0.7946 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `bcde21b6-c2f5-4b8d-8b29-ec6a33e1dc76` |
| seed | `123` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `78a884ea023932eb` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
