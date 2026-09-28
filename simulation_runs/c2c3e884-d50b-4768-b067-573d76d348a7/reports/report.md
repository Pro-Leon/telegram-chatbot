# Evaluation Report — cheap_single

**simulation_id:** `c2c3e884-d50b-4768-b067-573d76d348a7`
**seed:** `11` **scenario:** `cheap_single` **scenario_version:** `v1`
**strategy_id:** `cheap_single` **dataset_hash:** `f7b5b2bdd78d4aef`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:35:18.832597+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.6078|
| brier | 0.2057|
| calibration_gap | 0.0575|
| accuracy | 0.800|
| precision | 1.000|
| recall | 0.400|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| LOW | 10 | 0.1685 | 0.5290 |
| MID | 5 | 0.2799 | 0.7653 |
| lifecycle:cold | 9 | 0.1867 | 0.5533 |
| lifecycle:hot | 1 | 0.0978 | 0.3750 |
| lifecycle:warm | 5 | 0.2613 | 0.7524 |
| offer:SINGLE | 9 | 0.2165 | 0.6398 |
| offer:SMALL_BUNDLE | 6 | 0.1894 | 0.5597 |
| price:LOW | 10 | 0.1685 | 0.5290 |
| price:MID | 5 | 0.2799 | 0.7653 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.267 |
| fan_recent_rate | 0.267 |
| global_rate | 0.267 |
| offer_type_mean_rate | 0.292 |
| offer_type_most_frequent_rate | 0.333 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9839187 | 0.075 | 1 |
| 9179324 | 0.154 | 1 |
| 9562708 | 0.181 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | f7b5b2bd | 0.2057 | 0.6078 |
| 90 | f7b5b2bd | 0.2057 | 0.6078 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `c2c3e884-d50b-4768-b067-573d76d348a7` |
| seed | `11` |
| scenario_id | `cheap_single` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `f7b5b2bdd78d4aef` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
