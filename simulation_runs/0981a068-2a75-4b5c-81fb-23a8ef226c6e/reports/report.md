# Evaluation Report — balanced

**simulation_id:** `0981a068-2a75-4b5c-81fb-23a8ef226c6e`
**seed:** `11` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `1762c520a3933a61`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:18:46.503992+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.4727|
| brier | 0.1611|
| calibration_gap | 0.1628|
| accuracy | 0.750|
| precision | 0.000|
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 2 | 0.0115 | 0.1133 |
| MID | 6 | 0.2110 | 0.5924 |
| lifecycle:cold | 3 | 0.1221 | 0.4004 |
| lifecycle:hot | 1 | 0.4637 | 1.1425 |
| lifecycle:warm | 4 | 0.1147 | 0.3594 |
| offer:CORE_BUNDLE | 4 | 0.1563 | 0.4585 |
| offer:PREMIUM | 1 | 0.0074 | 0.0898 |
| offer:SINGLE | 2 | 0.3041 | 0.8044 |
| offer:SMALL_BUNDLE | 1 | 0.0484 | 0.2484 |
| price:HIGH | 2 | 0.0115 | 0.1133 |
| price:MID | 6 | 0.2110 | 0.5924 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.267 |
| fan_recent_rate | 0.267 |
| global_rate | 0.267 |
| offer_type_mean_rate | 0.406 |
| offer_type_most_frequent_rate | 0.500 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9040646 | 0.467 | 1 |
| 9291409 | 0.086 | 1 |
| 9701022 | 0.112 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | 1762c520 | 0.1611 | 0.4727 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `0981a068-2a75-4b5c-81fb-23a8ef226c6e` |
| seed | `11` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `1762c520a3933a61` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
