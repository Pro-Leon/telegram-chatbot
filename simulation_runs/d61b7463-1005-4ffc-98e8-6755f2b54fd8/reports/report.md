# Evaluation Report — balanced

**simulation_id:** `d61b7463-1005-4ffc-98e8-6755f2b54fd8`
**seed:** `42` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `2b39fdff9a1be3f3`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:21:36.429033+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.6302|
| brier | 0.1988|
| calibration_gap | 0.0467|
| accuracy | 0.750|
| precision | 0.667|
| recall | 0.667|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 5 | 0.2859 | 0.8523 |
| LOW | 1 | 0.0390 | 0.2199 |
| MID | 2 | 0.0610 | 0.2800 |
| lifecycle:cold | 2 | 0.1307 | 0.4292 |
| lifecycle:hot | 2 | 0.0276 | 0.1814 |
| lifecycle:warm | 4 | 0.3185 | 0.9550 |
| offer:CORE_BUNDLE | 1 | 0.2216 | 0.6364 |
| offer:PREMIUM | 3 | 0.0509 | 0.2511 |
| offer:SINGLE | 2 | 0.1728 | 0.5026 |
| offer:SMALL_BUNDLE | 2 | 0.4352 | 1.3232 |
| price:HIGH | 5 | 0.2859 | 0.8523 |
| price:LOW | 1 | 0.0390 | 0.2199 |
| price:MID | 2 | 0.0610 | 0.2800 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.267 |
| fan_recent_rate | 0.267 |
| global_rate | 0.267 |
| offer_type_mean_rate | 0.311 |
| offer_type_most_frequent_rate | 0.010 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9676405 | 0.089 | 1 |
| 9500224 | 0.567 | 1 |
| 9277848 | 0.197 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | 2b39fdff | 0.1988 | 0.6302 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `d61b7463-1005-4ffc-98e8-6755f2b54fd8` |
| seed | `42` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `2b39fdff9a1be3f3` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
