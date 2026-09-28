# Evaluation Report — adversarial

**simulation_id:** `414adf7e-d278-4a5d-811d-ebca11ecde99`
**seed:** `42` **scenario:** `adversarial` **scenario_version:** `v1`
**strategy_id:** `adversarial` **dataset_hash:** `079f91e839620c30`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:34:10.353396+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.5923|
| brier | 0.2038|
| calibration_gap | 0.0167|
| accuracy | 0.733|
| precision | n/a |
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 7 | 0.2345 | 0.6625 |
| LOW | 2 | 0.0789 | 0.3166 |
| MID | 6 | 0.2096 | 0.6022 |
| lifecycle:cold | 3 | 0.2867 | 0.7711 |
| lifecycle:warm | 12 | 0.1831 | 0.5475 |
| offer:CORE_BUNDLE | 4 | 0.4026 | 1.0393 |
| offer:PREMIUM | 4 | 0.1004 | 0.3631 |
| offer:SINGLE | 5 | 0.0569 | 0.2642 |
| offer:SMALL_BUNDLE | 2 | 0.3802 | 0.9768 |
| price:HIGH | 7 | 0.2345 | 0.6625 |
| price:LOW | 2 | 0.0789 | 0.3166 |
| price:MID | 6 | 0.2096 | 0.6022 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.267 |
| fan_recent_rate | 0.267 |
| global_rate | 0.267 |
| offer_type_mean_rate | 0.272 |
| offer_type_most_frequent_rate | 0.286 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9824806 | 0.485 | 1 |
| 9468152 | 0.357 | 1 |
| 9772264 | 0.175 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | 079f91e8 | 0.2038 | 0.5923 |
| 90 | 079f91e8 | 0.2038 | 0.5923 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `414adf7e-d278-4a5d-811d-ebca11ecde99` |
| seed | `42` |
| scenario_id | `adversarial` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `079f91e839620c30` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
