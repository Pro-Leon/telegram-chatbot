# Evaluation Report — balanced

**simulation_id:** `f9436ee1-9394-49e1-9fce-2974fcce75cf`
**seed:** `11` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `9d096d9a8ae0832c`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:34:44.433344+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.6654|
| brier | 0.2222|
| calibration_gap | 0.0235|
| accuracy | 0.733|
| precision | n/a |
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 4 | 0.3993 | 1.1585 |
| LOW | 4 | 0.2097 | 0.6130 |
| MID | 7 | 0.1281 | 0.4137 |
| lifecycle:cold | 5 | 0.3352 | 0.9999 |
| lifecycle:hot | 2 | 0.2634 | 0.6709 |
| lifecycle:warm | 8 | 0.1412 | 0.4550 |
| offer:CORE_BUNDLE | 4 | 0.2263 | 0.6511 |
| offer:PREMIUM | 3 | 0.2629 | 0.7357 |
| offer:SINGLE | 2 | 0.0567 | 0.2713 |
| offer:SMALL_BUNDLE | 6 | 0.2542 | 0.7713 |
| price:HIGH | 4 | 0.3993 | 1.1585 |
| price:LOW | 4 | 0.2097 | 0.6130 |
| price:MID | 7 | 0.1281 | 0.4137 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.317 |
| fan_recent_rate | 0.317 |
| global_rate | 0.317 |
| offer_type_mean_rate | 0.327 |
| offer_type_most_frequent_rate | 0.417 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9569587 | 0.130 | 1 |
| 9589298 | 0.410 | 1 |
| 9268300 | 0.323 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | 9d096d9a | 0.2222 | 0.6654 |
| 300 | 9d096d9a | 0.2222 | 0.6654 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `f9436ee1-9394-49e1-9fce-2974fcce75cf` |
| seed | `11` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `9d096d9a8ae0832c` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
