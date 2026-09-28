# Evaluation Report — balanced

**simulation_id:** `0455232e-6101-441b-acfe-a6aaaa0d2c1e`
**seed:** `42` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `8f4f442e8f150c26`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:20:58.465083+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 1.6957|
| brier | 0.5014|
| calibration_gap | 0.5928|
| accuracy | 0.500|
| precision | 0.000|
| recall | n/a |
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 6 | 0.6403 | 2.1671 |
| LOW | 1 | 0.0013 | 0.0362 |
| MID | 1 | 0.1679 | 0.5272 |
| lifecycle:cold | 1 | 0.9362 | 3.4285 |
| lifecycle:warm | 7 | 0.4393 | 1.4482 |
| offer:CORE_BUNDLE | 1 | 0.9261 | 3.2798 |
| offer:PREMIUM | 2 | 0.4006 | 1.1315 |
| offer:SINGLE | 2 | 0.1246 | 0.3626 |
| offer:SMALL_BUNDLE | 3 | 0.6782 | 2.4326 |
| price:HIGH | 6 | 0.6403 | 2.1671 |
| price:LOW | 1 | 0.0013 | 0.0362 |
| price:MID | 1 | 0.1679 | 0.5272 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.333 |
| fan_recent_rate | 0.333 |
| global_rate | 0.333 |
| offer_type_mean_rate | 0.294 |
| offer_type_most_frequent_rate | 0.333 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9788210 | 0.968 | 1 |
| 9339072 | 0.009 | 1 |
| 9702008 | 0.498 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | 8f4f442e | 0.5014 | 1.6957 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `0455232e-6101-441b-acfe-a6aaaa0d2c1e` |
| seed | `42` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `8f4f442e8f150c26` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
