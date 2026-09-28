# Evaluation Report — temporal_drift

**simulation_id:** `8e75b233-044b-4275-a41f-0563ff622a63`
**seed:** `42` **scenario:** `temporal_drift` **scenario_version:** `v1`
**strategy_id:** `temporal_drift` **dataset_hash:** `01243a53914888b9`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:34:36.217695+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.5669|
| brier | 0.1859|
| calibration_gap | 0.0746|
| accuracy | 0.800|
| precision | 1.000|
| recall | 0.250|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 6 | 0.1960 | 0.6052 |
| MID | 9 | 0.1791 | 0.5414 |
| lifecycle:cold | 5 | 0.3204 | 0.9190 |
| lifecycle:warm | 10 | 0.1186 | 0.3909 |
| offer:CORE_BUNDLE | 5 | 0.3087 | 0.8794 |
| offer:PREMIUM | 5 | 0.0161 | 0.1331 |
| offer:SINGLE | 2 | 0.0844 | 0.3274 |
| offer:SMALL_BUNDLE | 3 | 0.3317 | 0.9288 |
| price:HIGH | 6 | 0.1960 | 0.6052 |
| price:MID | 9 | 0.1791 | 0.5414 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.250 |
| fan_recent_rate | 0.250 |
| global_rate | 0.250 |
| offer_type_mean_rate | 0.237 |
| offer_type_most_frequent_rate | 0.200 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9252250 | 0.285 | 1 |
| 9457452 | 0.116 | 1 |
| 9201136 | 0.374 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | 01243a53 | 0.1859 | 0.5669 |
| 90 | 01243a53 | 0.1859 | 0.5669 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `8e75b233-044b-4275-a41f-0563ff622a63` |
| seed | `42` |
| scenario_id | `temporal_drift` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `01243a53914888b9` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
