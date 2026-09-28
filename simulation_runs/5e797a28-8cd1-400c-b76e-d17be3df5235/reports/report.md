# Evaluation Report — balanced

**simulation_id:** `5e797a28-8cd1-400c-b76e-d17be3df5235`
**seed:** `11` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `b4d9ea765fcb3880`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:20:58.045456+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.4746|
| brier | 0.1561|
| calibration_gap | 0.0686|
| accuracy | 0.750|
| precision | 0.000|
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 2 | 0.1901 | 0.5417 |
| LOW | 2 | 0.0059 | 0.0784 |
| MID | 4 | 0.2143 | 0.6392 |
| lifecycle:hot | 3 | 0.1519 | 0.4566 |
| lifecycle:warm | 5 | 0.1587 | 0.4855 |
| offer:CORE_BUNDLE | 4 | 0.2786 | 0.7713 |
| offer:PREMIUM | 1 | 0.0282 | 0.1837 |
| offer:SINGLE | 2 | 0.0487 | 0.2144 |
| offer:SMALL_BUNDLE | 1 | 0.0090 | 0.0995 |
| price:HIGH | 2 | 0.1901 | 0.5417 |
| price:LOW | 2 | 0.0059 | 0.0784 |
| price:MID | 4 | 0.2143 | 0.6392 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.200 |
| fan_recent_rate | 0.200 |
| global_rate | 0.200 |
| offer_type_mean_rate | 0.190 |
| offer_type_most_frequent_rate | 0.167 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9019252 | 0.089 | 1 |
| 9803227 | 0.099 | 1 |
| 9782956 | 0.095 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | b4d9ea76 | 0.1561 | 0.4746 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `5e797a28-8cd1-400c-b76e-d17be3df5235` |
| seed | `11` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `b4d9ea765fcb3880` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
