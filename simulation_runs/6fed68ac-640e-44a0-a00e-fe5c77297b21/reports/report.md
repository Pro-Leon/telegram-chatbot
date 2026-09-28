# Evaluation Report — baseline

**simulation_id:** `6fed68ac-640e-44a0-a00e-fe5c77297b21`
**seed:** `11` **scenario:** `baseline` **scenario_version:** `v1`
**strategy_id:** `baseline` **dataset_hash:** `5041856bcd798299`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:35:10.948372+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.5471|
| brier | 0.1748|
| calibration_gap | 0.0016|
| accuracy | 0.800|
| precision | n/a |
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| MID | 15 | 0.1748 | 0.5471 |
| lifecycle:cold | 3 | 0.2485 | 0.7082 |
| lifecycle:hot | 1 | 0.0082 | 0.0951 |
| lifecycle:warm | 11 | 0.1698 | 0.5442 |
| offer:SMALL_BUNDLE | 15 | 0.1748 | 0.5471 |
| price:MID | 15 | 0.1748 | 0.5471 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.200 |
| fan_recent_rate | 0.200 |
| global_rate | 0.200 |
| offer_type_mean_rate | 0.200 |
| offer_type_most_frequent_rate | 0.200 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9175402 | 0.241 | 1 |
| 9151529 | 0.270 | 1 |
| 9787270 | 0.202 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | 5041856b | 0.1748 | 0.5471 |
| 90 | 5041856b | 0.1748 | 0.5471 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `6fed68ac-640e-44a0-a00e-fe5c77297b21` |
| seed | `11` |
| scenario_id | `baseline` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `5041856bcd798299` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
