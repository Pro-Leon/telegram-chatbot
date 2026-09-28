# Evaluation Report — balanced

**simulation_id:** `7b0094fd-91a7-4d03-addc-05f6ba1a2c73`
**seed:** `42` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `c35b8252505000a2`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:19:10.626208+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 2.0143|
| brier | 0.4647|
| calibration_gap | 0.4312|
| accuracy | 0.500|
| precision | n/a |
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 2 | 0.0286 | 0.1739 |
| LOW | 1 | 0.7963 | 2.2290 |
| MID | 5 | 0.5728 | 2.7075 |
| lifecycle:cold | 4 | 0.2513 | 1.5085 |
| lifecycle:warm | 4 | 0.6781 | 2.5202 |
| offer:CORE_BUNDLE | 2 | 0.4424 | 1.4202 |
| offer:PREMIUM | 1 | 0.0482 | 0.2480 |
| offer:SINGLE | 2 | 0.9889 | 5.3293 |
| offer:SMALL_BUNDLE | 3 | 0.2689 | 0.7892 |
| price:HIGH | 2 | 0.0286 | 0.1739 |
| price:LOW | 1 | 0.7963 | 2.2290 |
| price:MID | 5 | 0.5728 | 2.7075 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.200 |
| fan_recent_rate | 0.200 |
| global_rate | 0.200 |
| offer_type_mean_rate | 0.240 |
| offer_type_most_frequent_rate | 0.200 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9673067 | 0.108 | 1 |
| 9035932 | 0.095 | 1 |
| 9556193 | 0.220 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | c35b8252 | 0.4647 | 2.0143 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `7b0094fd-91a7-4d03-addc-05f6ba1a2c73` |
| seed | `42` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `c35b8252505000a2` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
