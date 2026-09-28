# Evaluation Report — balanced

**simulation_id:** `fc182e42-c70e-41d9-a444-fd6c5fedb8af`
**seed:** `123` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `1432277b99e2ea66`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:21:37.114968+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.6078|
| brier | 0.2059|
| calibration_gap | 0.0612|
| accuracy | 0.733|
| precision | 0.000|
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 5 | 0.1903 | 0.5615 |
| MID | 10 | 0.2137 | 0.6310 |
| lifecycle:cold | 4 | 0.1902 | 0.5662 |
| lifecycle:hot | 1 | 0.0368 | 0.2130 |
| lifecycle:warm | 10 | 0.2291 | 0.6639 |
| offer:CORE_BUNDLE | 4 | 0.0561 | 0.2518 |
| offer:PREMIUM | 4 | 0.2770 | 0.7577 |
| offer:SINGLE | 5 | 0.3369 | 0.9342 |
| offer:SMALL_BUNDLE | 2 | 0.0358 | 0.2037 |
| price:HIGH | 5 | 0.1903 | 0.5615 |
| price:MID | 10 | 0.2137 | 0.6310 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.300 |
| fan_recent_rate | 0.300 |
| global_rate | 0.300 |
| offer_type_mean_rate | 0.290 |
| offer_type_most_frequent_rate | 0.167 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9651847 | 0.232 | 1 |
| 9715125 | 0.202 | 1 |
| 9705009 | 0.323 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | 1432277b | 0.2059 | 0.6078 |
| 90 | 1432277b | 0.2059 | 0.6078 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `fc182e42-c70e-41d9-a444-fd6c5fedb8af` |
| seed | `123` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `1432277b99e2ea66` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
