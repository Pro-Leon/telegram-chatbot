# Evaluation Report — balanced

**simulation_id:** `bcf512fa-af9d-4388-a83b-bba0165c26d5`
**seed:** `42` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `ef637b989f91b248`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:19:29.310157+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.7973|
| brier | 0.2892|
| calibration_gap | 0.2411|
| accuracy | 0.500|
| precision | 0.667|
| recall | 0.400|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 1 | 0.2974 | 0.7882 |
| LOW | 1 | 0.1859 | 0.5641 |
| MID | 6 | 0.3050 | 0.8377 |
| lifecycle:cold | 2 | 0.1208 | 0.4168 |
| lifecycle:hot | 1 | 0.6427 | 1.6179 |
| lifecycle:warm | 5 | 0.2858 | 0.7855 |
| offer:CORE_BUNDLE | 3 | 0.3541 | 0.9745 |
| offer:PREMIUM | 2 | 0.3492 | 0.9437 |
| offer:SINGLE | 2 | 0.1833 | 0.5019 |
| offer:SMALL_BUNDLE | 1 | 0.1859 | 0.5641 |
| price:HIGH | 1 | 0.2974 | 0.7882 |
| price:LOW | 1 | 0.1859 | 0.5641 |
| price:MID | 6 | 0.3050 | 0.8377 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.467 |
| fan_recent_rate | 0.467 |
| global_rate | 0.467 |
| offer_type_mean_rate | 0.478 |
| offer_type_most_frequent_rate | 0.333 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9516959 | 0.400 | 1 |
| 9344428 | 0.907 | 1 |
| 9258903 | 0.130 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | ef637b98 | 0.2892 | 0.7973 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `bcf512fa-af9d-4388-a83b-bba0165c26d5` |
| seed | `42` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `ef637b989f91b248` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
