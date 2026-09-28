# Evaluation Report — premium_mix

**simulation_id:** `813d185b-22dc-4824-afc6-12adbd4b47de`
**seed:** `11` **scenario:** `premium_mix` **scenario_version:** `v1`
**strategy_id:** `premium_mix` **dataset_hash:** `50bb2fa674941db1`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:32:56.277745+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.7178|
| brier | 0.2563|
| calibration_gap | 0.0755|
| accuracy | 0.600|
| precision | 0.500|
| recall | 0.167|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 12 | 0.2763 | 0.7624 |
| MID | 3 | 0.1762 | 0.5391 |
| lifecycle:cold | 7 | 0.2730 | 0.7331 |
| lifecycle:hot | 2 | 0.6817 | 1.7577 |
| lifecycle:warm | 6 | 0.0949 | 0.3532 |
| offer:CORE_BUNDLE | 4 | 0.1744 | 0.5194 |
| offer:PREMIUM | 8 | 0.2814 | 0.7643 |
| offer:SINGLE | 1 | 0.0288 | 0.1861 |
| offer:SMALL_BUNDLE | 2 | 0.4335 | 1.1941 |
| price:HIGH | 12 | 0.2763 | 0.7624 |
| price:MID | 3 | 0.1762 | 0.5391 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.283 |
| fan_recent_rate | 0.283 |
| global_rate | 0.283 |
| offer_type_mean_rate | 0.273 |
| offer_type_most_frequent_rate | 0.364 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9032151 | 0.170 | 1 |
| 9081755 | 0.170 | 1 |
| 9642260 | 0.520 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | 50bb2fa6 | 0.2563 | 0.7178 |
| 90 | 50bb2fa6 | 0.2563 | 0.7178 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `813d185b-22dc-4824-afc6-12adbd4b47de` |
| seed | `11` |
| scenario_id | `premium_mix` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `50bb2fa674941db1` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
