# Evaluation Report — balanced

**simulation_id:** `4a4debcb-efb4-41e2-b067-7463f6dc2f58`
**seed:** `123` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `f0ea1194313a5742`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:19:30.785499+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.8591|
| brier | 0.3173|
| calibration_gap | 0.1377|
| accuracy | 0.467|
| precision | 0.333|
| recall | 0.143|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 7 | 0.3532 | 0.9206 |
| MID | 8 | 0.2859 | 0.8054 |
| lifecycle:cold | 6 | 0.1672 | 0.4973 |
| lifecycle:warm | 9 | 0.4173 | 1.1004 |
| offer:CORE_BUNDLE | 1 | 0.0038 | 0.0634 |
| offer:PREMIUM | 3 | 0.2496 | 0.7046 |
| offer:SINGLE | 5 | 0.1690 | 0.5185 |
| offer:SMALL_BUNDLE | 6 | 0.5269 | 1.3529 |
| price:HIGH | 7 | 0.3532 | 0.9206 |
| price:MID | 8 | 0.2859 | 0.8054 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.300 |
| fan_recent_rate | 0.300 |
| global_rate | 0.300 |
| offer_type_mean_rate | 0.302 |
| offer_type_most_frequent_rate | 0.286 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9375773 | 0.239 | 1 |
| 9071086 | 0.489 | 1 |
| 9645653 | 0.270 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | f0ea1194 | 0.3173 | 0.8591 |
| 90 | f0ea1194 | 0.3173 | 0.8591 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `4a4debcb-efb4-41e2-b067-7463f6dc2f58` |
| seed | `123` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `f0ea1194313a5742` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
