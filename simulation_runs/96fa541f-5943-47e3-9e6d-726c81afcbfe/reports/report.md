# Evaluation Report — balanced

**simulation_id:** `96fa541f-5943-47e3-9e6d-726c81afcbfe`
**seed:** `123` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `5f8e057c8629d22c`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `2b2c6f60`
**as_of:** `2026-09-20T21:20:08.702036+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.6796|
| brier | 0.2460|
| calibration_gap | 0.1884|
| accuracy | 0.600|
| precision | 1.000|
| recall | 0.143|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 12 | 0.2323 | 0.6510 |
| MID | 3 | 0.3006 | 0.7942 |
| lifecycle:cold | 5 | 0.1646 | 0.4949 |
| lifecycle:hot | 1 | 0.1234 | 0.4328 |
| lifecycle:warm | 9 | 0.3048 | 0.8097 |
| offer:CORE_BUNDLE | 6 | 0.2048 | 0.5883 |
| offer:PREMIUM | 5 | 0.3174 | 0.8371 |
| offer:SINGLE | 2 | 0.3321 | 0.8585 |
| offer:SMALL_BUNDLE | 2 | 0.1047 | 0.3811 |
| price:HIGH | 12 | 0.2323 | 0.6510 |
| price:MID | 3 | 0.3006 | 0.7942 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.283 |
| fan_recent_rate | 0.283 |
| global_rate | 0.283 |
| offer_type_mean_rate | 0.275 |
| offer_type_most_frequent_rate | 0.308 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9758664 | 0.351 | 1 |
| 9303968 | 0.153 | 1 |
| 9707744 | 0.299 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | 5f8e057c | 0.2460 | 0.6796 |
| 90 | 5f8e057c | 0.2460 | 0.6796 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `96fa541f-5943-47e3-9e6d-726c81afcbfe` |
| seed | `123` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `2b2c6f60` |
| dataset_hash | `5f8e057c8629d22c` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
