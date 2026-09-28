# Evaluation Report — balanced

**simulation_id:** `2bde36f7-74f8-4f37-98a4-a2d2849703e9`
**seed:** `42` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `9c272cae6bc3bbae`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:20:58.583133+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.3956|
| brier | 0.1131|
| calibration_gap | 0.2436|
| accuracy | 0.875|
| precision | 0.500|
| recall | 1.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 3 | 0.1423 | 0.4549 |
| LOW | 1 | 0.1080 | 0.3985 |
| MID | 4 | 0.0925 | 0.3505 |
| lifecycle:cold | 2 | 0.2054 | 0.5988 |
| lifecycle:warm | 6 | 0.0823 | 0.3279 |
| offer:CORE_BUNDLE | 2 | 0.0302 | 0.1886 |
| offer:PREMIUM | 1 | 0.1275 | 0.4416 |
| offer:SINGLE | 1 | 0.1080 | 0.3985 |
| offer:SMALL_BUNDLE | 4 | 0.1523 | 0.4870 |
| price:HIGH | 3 | 0.1423 | 0.4549 |
| price:LOW | 1 | 0.1080 | 0.3985 |
| price:MID | 4 | 0.0925 | 0.3505 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.267 |
| fan_recent_rate | 0.267 |
| global_rate | 0.267 |
| offer_type_mean_rate | 0.273 |
| offer_type_most_frequent_rate | 0.010 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9339709 | 0.550 | 1 |
| 9467620 | 0.357 | 1 |
| 9396608 | 0.375 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | 9c272cae | 0.1131 | 0.3956 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `2bde36f7-74f8-4f37-98a4-a2d2849703e9` |
| seed | `42` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `9c272cae6bc3bbae` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
