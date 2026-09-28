# Evaluation Report — novelty_heavy

**simulation_id:** `2a598a73-e564-4586-b4b7-06c6d1d5651e`
**seed:** `11` **scenario:** `novelty_heavy` **scenario_version:** `v1`
**strategy_id:** `novelty_heavy` **dataset_hash:** `ea906c6bb2da8e65`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:35:26.338945+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.8047|
| brier | 0.3018|
| calibration_gap | 0.0176|
| accuracy | 0.400|
| precision | 0.250|
| recall | 0.143|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 7 | 0.2265 | 0.6397 |
| LOW | 1 | 0.5752 | 1.4204 |
| MID | 7 | 0.3380 | 0.8817 |
| lifecycle:cold | 7 | 0.3398 | 0.8839 |
| lifecycle:hot | 1 | 0.4415 | 1.0919 |
| lifecycle:warm | 7 | 0.2438 | 0.6843 |
| offer:CORE_BUNDLE | 1 | 0.2491 | 0.6914 |
| offer:PREMIUM | 2 | 0.3079 | 0.8170 |
| offer:SINGLE | 6 | 0.2220 | 0.6376 |
| offer:SMALL_BUNDLE | 6 | 0.3882 | 0.9865 |
| price:HIGH | 7 | 0.2265 | 0.6397 |
| price:LOW | 1 | 0.5752 | 1.4204 |
| price:MID | 7 | 0.3380 | 0.8817 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.383 |
| fan_recent_rate | 0.383 |
| global_rate | 0.383 |
| offer_type_mean_rate | 0.377 |
| offer_type_most_frequent_rate | 0.474 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9554126 | 0.562 | 1 |
| 9649762 | 0.254 | 1 |
| 9453732 | 0.623 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | ea906c6b | 0.3018 | 0.8047 |
| 90 | ea906c6b | 0.3018 | 0.8047 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `2a598a73-e564-4586-b4b7-06c6d1d5651e` |
| seed | `11` |
| scenario_id | `novelty_heavy` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `ea906c6bb2da8e65` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
