# Evaluation Report — balanced

**simulation_id:** `89234a5e-ce35-42ae-b889-918b59cfe914`
**seed:** `11` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `2ef1aa6ceac4f101`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:19:26.455482+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 1.2395|
| brier | 0.4211|
| calibration_gap | 0.2655|
| accuracy | 0.500|
| precision | n/a |
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 1 | 0.5181 | 1.2722 |
| LOW | 3 | 0.3285 | 0.8953 |
| MID | 4 | 0.4664 | 1.4894 |
| lifecycle:cold | 3 | 0.6770 | 1.8492 |
| lifecycle:hot | 2 | 0.1698 | 0.5265 |
| lifecycle:warm | 3 | 0.3328 | 1.1051 |
| offer:CORE_BUNDLE | 1 | 0.2383 | 0.6698 |
| offer:PREMIUM | 2 | 0.4517 | 1.4733 |
| offer:SINGLE | 2 | 0.3097 | 0.8278 |
| offer:SMALL_BUNDLE | 3 | 0.5360 | 1.5480 |
| price:HIGH | 1 | 0.5181 | 1.2722 |
| price:LOW | 3 | 0.3285 | 0.8953 |
| price:MID | 4 | 0.4664 | 1.4894 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.133 |
| fan_recent_rate | 0.133 |
| global_rate | 0.133 |
| offer_type_mean_rate | 0.133 |
| offer_type_most_frequent_rate | 0.010 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9571397 | 0.072 | 1 |
| 9163425 | 0.062 | 1 |
| 9124011 | 0.154 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | 2ef1aa6c | 0.4211 | 1.2395 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `89234a5e-ce35-42ae-b889-918b59cfe914` |
| seed | `11` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `2ef1aa6ceac4f101` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
