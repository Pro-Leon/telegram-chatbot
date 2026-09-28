# Evaluation Report — no_signal

**simulation_id:** `e135afef-f7bb-4e2c-9a58-6ead14038452`
**seed:** `42` **scenario:** `no_signal` **scenario_version:** `v1`
**strategy_id:** `no_signal` **dataset_hash:** `0520215136adf084`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:33:12.964130+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.7042|
| brier | 0.2532|
| calibration_gap | 0.1529|
| accuracy | 0.600|
| precision | 0.556|
| recall | 0.714|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 9 | 0.3009 | 0.8150 |
| LOW | 3 | 0.0186 | 0.1455 |
| MID | 3 | 0.3447 | 0.9302 |
| lifecycle:cold | 4 | 0.1659 | 0.5120 |
| lifecycle:hot | 2 | 0.2994 | 0.7905 |
| lifecycle:warm | 9 | 0.2817 | 0.7704 |
| offer:CORE_BUNDLE | 4 | 0.3342 | 0.8929 |
| offer:PREMIUM | 4 | 0.2377 | 0.6682 |
| offer:SINGLE | 3 | 0.1215 | 0.4190 |
| offer:SMALL_BUNDLE | 4 | 0.2865 | 0.7654 |
| price:HIGH | 9 | 0.3009 | 0.8150 |
| price:LOW | 3 | 0.0186 | 0.1455 |
| price:MID | 3 | 0.3447 | 0.9302 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.500 |
| fan_recent_rate | 0.500 |
| global_rate | 0.500 |
| offer_type_mean_rate | 0.506 |
| offer_type_most_frequent_rate | 0.542 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9233262 | 0.886 | 1 |
| 9254000 | 0.488 | 1 |
| 9638414 | 0.414 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | 05202151 | 0.2532 | 0.7042 |
| 90 | 05202151 | 0.2532 | 0.7042 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `e135afef-f7bb-4e2c-9a58-6ead14038452` |
| seed | `42` |
| scenario_id | `no_signal` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `0520215136adf084` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
