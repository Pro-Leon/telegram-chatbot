# Evaluation Report — balanced

**simulation_id:** `84939a91-b5da-4b3a-9ec3-174568e70147`
**seed:** `42` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `4e46e7298fde5120`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `2b2c6f60`
**as_of:** `2026-09-20T21:20:07.912371+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.6931|
| brier | 0.2500|
| calibration_gap | 0.0000|
| accuracy | 0.500|
| precision | 0.500|
| recall | 1.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 3 | 0.2500 | 0.6931 |
| LOW | 2 | 0.2500 | 0.6931 |
| MID | 3 | 0.2500 | 0.6931 |
| lifecycle:cold | 3 | 0.2500 | 0.6931 |
| lifecycle:hot | 1 | 0.2500 | 0.6931 |
| lifecycle:warm | 4 | 0.2500 | 0.6931 |
| offer:CORE_BUNDLE | 2 | 0.2500 | 0.6931 |
| offer:PREMIUM | 1 | 0.2500 | 0.6931 |
| offer:SMALL_BUNDLE | 5 | 0.2500 | 0.6931 |
| price:HIGH | 3 | 0.2500 | 0.6931 |
| price:LOW | 2 | 0.2500 | 0.6931 |
| price:MID | 3 | 0.2500 | 0.6931 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.067 |
| fan_recent_rate | 0.067 |
| global_rate | 0.067 |
| offer_type_mean_rate | 0.058 |
| offer_type_most_frequent_rate | 0.200 |

## Counterfactual (same fan×same context×different offer)

_none (model abstained or not run)_

> EXTRAPOLATIVE / OFF-POLICY ADVISORY: alternatives were not exposed

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | 4e46e729 | 0.2500 | 0.6931 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `84939a91-b5da-4b3a-9ec3-174568e70147` |
| seed | `42` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `2b2c6f60` |
| dataset_hash | `4e46e7298fde5120` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
