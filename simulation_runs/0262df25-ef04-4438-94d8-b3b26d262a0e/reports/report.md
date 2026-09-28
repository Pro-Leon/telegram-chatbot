# Evaluation Report — freebie_heavy

**simulation_id:** `0262df25-ef04-4438-94d8-b3b26d262a0e`
**seed:** `42` **scenario:** `freebie_heavy` **scenario_version:** `v1`
**strategy_id:** `freebie_heavy` **dataset_hash:** `6726c5bd047be8f3`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:34:01.123466+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.8280|
| brier | 0.3076|
| calibration_gap | 0.1771|
| accuracy | 0.533|
| precision | 0.375|
| recall | 0.600|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| LOW | 10 | 0.2923 | 0.7985 |
| MID | 5 | 0.3381 | 0.8871 |
| lifecycle:cold | 11 | 0.3740 | 0.9782 |
| lifecycle:hot | 1 | 0.0137 | 0.1246 |
| lifecycle:warm | 3 | 0.1621 | 0.5119 |
| offer:CORE_BUNDLE | 2 | 0.0753 | 0.2933 |
| offer:SINGLE | 11 | 0.3621 | 0.9538 |
| offer:SMALL_BUNDLE | 2 | 0.2399 | 0.6709 |
| price:LOW | 10 | 0.2923 | 0.7985 |
| price:MID | 5 | 0.3381 | 0.8871 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.383 |
| fan_recent_rate | 0.383 |
| global_rate | 0.383 |
| offer_type_mean_rate | 0.362 |
| offer_type_most_frequent_rate | 0.455 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9778201 | 0.385 | 1 |
| 9757802 | 0.653 | 1 |
| 9203475 | 0.385 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | 6726c5bd | 0.3076 | 0.8280 |
| 90 | 6726c5bd | 0.3076 | 0.8280 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `0262df25-ef04-4438-94d8-b3b26d262a0e` |
| seed | `42` |
| scenario_id | `freebie_heavy` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `6726c5bd047be8f3` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
