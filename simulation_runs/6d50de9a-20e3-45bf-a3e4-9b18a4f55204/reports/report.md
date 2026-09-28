# Evaluation Report — balanced

**simulation_id:** `6d50de9a-20e3-45bf-a3e4-9b18a4f55204`
**seed:** `123` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `fb61105bb63a8d2a`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:18:49.757320+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.6843|
| brier | 0.2444|
| calibration_gap | 0.2009|
| accuracy | 0.533|
| precision | 0.000|
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 11 | 0.2442 | 0.6880 |
| LOW | 1 | 0.4545 | 1.1213 |
| MID | 3 | 0.1749 | 0.5248 |
| lifecycle:cold | 6 | 0.2696 | 0.7269 |
| lifecycle:hot | 1 | 0.6850 | 1.7583 |
| lifecycle:warm | 8 | 0.1704 | 0.5181 |
| offer:CORE_BUNDLE | 5 | 0.2416 | 0.6928 |
| offer:PREMIUM | 2 | 0.0532 | 0.2597 |
| offer:SINGLE | 5 | 0.3152 | 0.8313 |
| offer:SMALL_BUNDLE | 3 | 0.2584 | 0.7081 |
| price:HIGH | 11 | 0.2442 | 0.6880 |
| price:LOW | 1 | 0.4545 | 1.1213 |
| price:MID | 3 | 0.1749 | 0.5248 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.300 |
| fan_recent_rate | 0.300 |
| global_rate | 0.300 |
| offer_type_mean_rate | 0.294 |
| offer_type_most_frequent_rate | 0.368 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9551447 | 0.320 | 1 |
| 9116062 | 0.626 | 1 |
| 9821323 | 0.186 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | fb61105b | 0.2444 | 0.6843 |
| 90 | fb61105b | 0.2444 | 0.6843 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `6d50de9a-20e3-45bf-a3e4-9b18a4f55204` |
| seed | `123` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `fb61105bb63a8d2a` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
