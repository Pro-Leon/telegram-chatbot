# Evaluation Report — balanced

**simulation_id:** `ae68fa24-c24b-4172-b5f9-0c93ec9390a8`
**seed:** `11` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `8399c697ab0bd787`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:20:07.604293+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.5931|
| brier | 0.1929|
| calibration_gap | 0.0928|
| accuracy | 0.750|
| precision | n/a |
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 4 | 0.1510 | 0.4457 |
| LOW | 1 | 0.0168 | 0.1389 |
| MID | 3 | 0.3074 | 0.9410 |
| lifecycle:cold | 5 | 0.0272 | 0.1586 |
| lifecycle:hot | 1 | 0.0371 | 0.2139 |
| lifecycle:warm | 2 | 0.6850 | 1.8689 |
| offer:CORE_BUNDLE | 2 | 0.2769 | 0.7233 |
| offer:PREMIUM | 2 | 0.0252 | 0.1681 |
| offer:SINGLE | 1 | 0.0915 | 0.3601 |
| offer:SMALL_BUNDLE | 3 | 0.2825 | 0.8672 |
| price:HIGH | 4 | 0.1510 | 0.4457 |
| price:LOW | 1 | 0.0168 | 0.1389 |
| price:MID | 3 | 0.3074 | 0.9410 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.133 |
| fan_recent_rate | 0.133 |
| global_rate | 0.133 |
| offer_type_mean_rate | 0.172 |
| offer_type_most_frequent_rate | 0.167 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9762775 | 0.193 | 1 |
| 9557862 | 0.115 | 1 |
| 9620899 | 0.302 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | 8399c697 | 0.1929 | 0.5931 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `ae68fa24-c24b-4172-b5f9-0c93ec9390a8` |
| seed | `11` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `8399c697ab0bd787` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
