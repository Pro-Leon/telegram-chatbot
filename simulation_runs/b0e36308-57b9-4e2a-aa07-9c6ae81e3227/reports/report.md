# Evaluation Report — balanced

**simulation_id:** `b0e36308-57b9-4e2a-aa07-9c6ae81e3227`
**seed:** `42` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `fce3ab46175e1ffc`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:21:36.545283+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.6439|
| brier | 0.2132|
| calibration_gap | 0.2884|
| accuracy | 0.750|
| precision | 0.333|
| recall | 1.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 5 | 0.3404 | 1.0133 |
| MID | 3 | 0.0011 | 0.0283 |
| lifecycle:cold | 3 | 0.2685 | 0.7098 |
| lifecycle:hot | 2 | 0.4482 | 1.4685 |
| lifecycle:warm | 3 | 0.0011 | 0.0283 |
| offer:CORE_BUNDLE | 1 | 0.0035 | 0.0614 |
| offer:PREMIUM | 2 | 0.4428 | 1.4276 |
| offer:SINGLE | 4 | 0.2010 | 0.5296 |
| offer:SMALL_BUNDLE | 1 | 0.0121 | 0.1163 |
| price:HIGH | 5 | 0.3404 | 1.0133 |
| price:MID | 3 | 0.0011 | 0.0283 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.267 |
| fan_recent_rate | 0.267 |
| global_rate | 0.267 |
| offer_type_mean_rate | 0.350 |
| offer_type_most_frequent_rate | 0.200 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9322374 | 0.048 | 1 |
| 9708370 | 0.759 | 1 |
| 9625809 | 0.001 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | fce3ab46 | 0.2132 | 0.6439 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `b0e36308-57b9-4e2a-aa07-9c6ae81e3227` |
| seed | `42` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `fce3ab46175e1ffc` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
