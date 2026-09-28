# Evaluation Report — whales

**simulation_id:** `5d7b3990-2323-4794-9c77-ab57ef92a783`
**seed:** `42` **scenario:** `whales` **scenario_version:** `v1`
**strategy_id:** `whales` **dataset_hash:** `993667db1243608a`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:33:04.917763+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.5476|
| brier | 0.1820|
| calibration_gap | 0.0208|
| accuracy | 0.800|
| precision | n/a |
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 5 | 0.1429 | 0.4272 |
| MID | 10 | 0.2016 | 0.6078 |
| lifecycle:cold | 4 | 0.1091 | 0.3830 |
| lifecycle:hot | 1 | 0.6764 | 1.7285 |
| lifecycle:warm | 10 | 0.1617 | 0.4954 |
| offer:CORE_BUNDLE | 4 | 0.0163 | 0.1311 |
| offer:PREMIUM | 3 | 0.2895 | 0.8375 |
| offer:SINGLE | 2 | 0.1633 | 0.5092 |
| offer:SMALL_BUNDLE | 6 | 0.2450 | 0.6932 |
| price:HIGH | 5 | 0.1429 | 0.4272 |
| price:MID | 10 | 0.2016 | 0.6078 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.267 |
| fan_recent_rate | 0.267 |
| global_rate | 0.267 |
| offer_type_mean_rate | 0.271 |
| offer_type_most_frequent_rate | 0.211 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9136599 | 0.103 | 1 |
| 9508368 | 0.121 | 1 |
| 9551211 | 0.106 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | 993667db | 0.1820 | 0.5476 |
| 90 | 993667db | 0.1820 | 0.5476 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `5d7b3990-2323-4794-9c77-ab57ef92a783` |
| seed | `42` |
| scenario_id | `whales` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `993667db1243608a` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
