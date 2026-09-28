# Evaluation Report — balanced

**simulation_id:** `956ee1f3-8dcb-4292-bbed-e36e0f4ee7cb`
**seed:** `123` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `ef1185cfc6feb7a8`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:19:43.547031+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.4200|
| brier | 0.1323|
| calibration_gap | 0.0469|
| accuracy | 0.867|
| precision | 1.000|
| recall | 0.333|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 7 | 0.0831 | 0.2841 |
| LOW | 2 | 0.1948 | 0.5811 |
| MID | 6 | 0.1690 | 0.5249 |
| lifecycle:cold | 6 | 0.0915 | 0.3278 |
| lifecycle:hot | 2 | 0.0713 | 0.2940 |
| lifecycle:warm | 7 | 0.1848 | 0.5351 |
| offer:CORE_BUNDLE | 3 | 0.0353 | 0.1991 |
| offer:PREMIUM | 4 | 0.1985 | 0.5665 |
| offer:SINGLE | 3 | 0.1234 | 0.4300 |
| offer:SMALL_BUNDLE | 5 | 0.1430 | 0.4295 |
| price:HIGH | 7 | 0.0831 | 0.2841 |
| price:LOW | 2 | 0.1948 | 0.5811 |
| price:MID | 6 | 0.1690 | 0.5249 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.267 |
| fan_recent_rate | 0.267 |
| global_rate | 0.267 |
| offer_type_mean_rate | 0.266 |
| offer_type_most_frequent_rate | 0.214 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9749024 | 0.510 | 1 |
| 9572997 | 0.258 | 1 |
| 9863948 | 0.380 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | ef1185cf | 0.1323 | 0.4200 |
| 90 | ef1185cf | 0.1323 | 0.4200 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `956ee1f3-8dcb-4292-bbed-e36e0f4ee7cb` |
| seed | `123` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `ef1185cfc6feb7a8` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
