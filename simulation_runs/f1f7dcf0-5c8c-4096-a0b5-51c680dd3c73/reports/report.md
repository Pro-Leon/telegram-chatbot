# Evaluation Report — balanced

**simulation_id:** `f1f7dcf0-5c8c-4096-a0b5-51c680dd3c73`
**seed:** `99` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `0999b485b4296fe7`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:21:26.138672+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 1.1865|
| brier | 0.3413|
| calibration_gap | 0.3090|
| accuracy | 0.625|
| precision | n/a |
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 3 | 0.6170 | 2.2253 |
| MID | 5 | 0.1760 | 0.5633 |
| lifecycle:cold | 2 | 0.0025 | 0.0497 |
| lifecycle:hot | 1 | 0.0377 | 0.2158 |
| lifecycle:warm | 5 | 0.5376 | 1.8354 |
| offer:CORE_BUNDLE | 3 | 0.6180 | 2.2384 |
| offer:PREMIUM | 1 | 0.0377 | 0.2158 |
| offer:SINGLE | 3 | 0.0015 | 0.0376 |
| offer:SMALL_BUNDLE | 1 | 0.8346 | 2.4483 |
| price:HIGH | 3 | 0.6170 | 2.2253 |
| price:MID | 5 | 0.1760 | 0.5633 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.133 |
| fan_recent_rate | 0.133 |
| global_rate | 0.133 |
| offer_type_mean_rate | 0.130 |
| offer_type_most_frequent_rate | 0.333 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9118950 | 0.194 | 1 |
| 9510832 | 0.051 | 1 |
| 9647486 | 0.023 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | 0999b485 | 0.3413 | 1.1865 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `f1f7dcf0-5c8c-4096-a0b5-51c680dd3c73` |
| seed | `99` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `0999b485b4296fe7` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
