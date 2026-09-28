# Evaluation Report — balanced

**simulation_id:** `75cd8ecb-a77c-4cda-875e-bfef1153bee6`
**seed:** `42` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `f486ddde16aceb21`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:19:42.989760+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.3241|
| brier | 0.1030|
| calibration_gap | 0.0349|
| accuracy | 0.875|
| precision | n/a |
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 5 | 0.1597 | 0.4679 |
| LOW | 1 | 0.0012 | 0.0351 |
| MID | 2 | 0.0123 | 0.1091 |
| lifecycle:cold | 2 | 0.0281 | 0.1512 |
| lifecycle:hot | 1 | 0.7389 | 1.9630 |
| lifecycle:warm | 5 | 0.0058 | 0.0655 |
| offer:CORE_BUNDLE | 1 | 0.0029 | 0.0552 |
| offer:PREMIUM | 2 | 0.0008 | 0.0270 |
| offer:SINGLE | 3 | 0.2716 | 0.7956 |
| offer:SMALL_BUNDLE | 2 | 0.0024 | 0.0484 |
| price:HIGH | 5 | 0.1597 | 0.4679 |
| price:LOW | 1 | 0.0012 | 0.0351 |
| price:MID | 2 | 0.0123 | 0.1091 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.200 |
| fan_recent_rate | 0.200 |
| global_rate | 0.200 |
| offer_type_mean_rate | 0.155 |
| offer_type_most_frequent_rate | 0.200 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9723810 | 0.145 | 1 |
| 9528204 | 0.035 | 1 |
| 9324108 | 0.036 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | f486ddde | 0.1030 | 0.3241 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `75cd8ecb-a77c-4cda-875e-bfef1153bee6` |
| seed | `42` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `f486ddde16aceb21` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
