# Evaluation Report — balanced

**simulation_id:** `125d7e10-f701-4626-8b35-c6b64cacd378`
**seed:** `42` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `14504bdc9b7bd28d`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:19:42.860506+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.7128|
| brier | 0.2005|
| calibration_gap | 0.0875|
| accuracy | 0.750|
| precision | n/a |
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 4 | 0.3572 | 1.2189 |
| LOW | 1 | 0.1056 | 0.3929 |
| MID | 3 | 0.0231 | 0.1445 |
| lifecycle:cold | 3 | 0.3146 | 1.1949 |
| lifecycle:hot | 1 | 0.0456 | 0.2403 |
| lifecycle:warm | 4 | 0.1536 | 0.4692 |
| offer:CORE_BUNDLE | 3 | 0.3606 | 1.3725 |
| offer:PREMIUM | 1 | 0.4511 | 1.1136 |
| offer:SINGLE | 2 | 0.0235 | 0.1388 |
| offer:SMALL_BUNDLE | 2 | 0.0119 | 0.0967 |
| price:HIGH | 4 | 0.3572 | 1.2189 |
| price:LOW | 1 | 0.1056 | 0.3929 |
| price:MID | 3 | 0.0231 | 0.1445 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.333 |
| fan_recent_rate | 0.333 |
| global_rate | 0.333 |
| offer_type_mean_rate | 0.333 |
| offer_type_most_frequent_rate | 0.250 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9393147 | 0.186 | 1 |
| 9822315 | 0.325 | 1 |
| 9660446 | 0.029 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | 14504bdc | 0.2005 | 0.7128 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `125d7e10-f701-4626-8b35-c6b64cacd378` |
| seed | `42` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `14504bdc9b7bd28d` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
