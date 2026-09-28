# Evaluation Report — balanced

**simulation_id:** `ae3f0a61-0463-4fa9-be59-c2ea7c38f691`
**seed:** `42` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `38c2d0187052e548`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:19:29.646903+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 1.3860|
| brier | 0.4312|
| calibration_gap | 0.4270|
| accuracy | 0.500|
| precision | n/a |
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 5 | 0.1912 | 0.7165 |
| MID | 3 | 0.8311 | 2.5020 |
| lifecycle:cold | 1 | 0.0117 | 0.1145 |
| lifecycle:hot | 2 | 0.9044 | 3.0441 |
| lifecycle:warm | 5 | 0.3258 | 0.9771 |
| offer:CORE_BUNDLE | 2 | 0.3669 | 0.9835 |
| offer:PREMIUM | 2 | 0.9024 | 3.0264 |
| offer:SINGLE | 2 | 0.4412 | 1.4074 |
| offer:SMALL_BUNDLE | 2 | 0.0143 | 0.1268 |
| price:HIGH | 5 | 0.1912 | 0.7165 |
| price:MID | 3 | 0.8311 | 2.5020 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.133 |
| fan_recent_rate | 0.133 |
| global_rate | 0.133 |
| offer_type_mean_rate | 0.130 |
| offer_type_most_frequent_rate | 0.010 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9045997 | 0.108 | 1 |
| 9241483 | 0.061 | 1 |
| 9762727 | 0.037 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | 38c2d018 | 0.4312 | 1.3860 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `ae3f0a61-0463-4fa9-be59-c2ea7c38f691` |
| seed | `42` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `38c2d0187052e548` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
