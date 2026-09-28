# Evaluation Report — balanced

**simulation_id:** `b4825bcc-a153-42e7-a5e2-0d592777fa64`
**seed:** `11` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `d2ec5bc611e75b9d`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:21:34.564374+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.6931|
| brier | 0.2500|
| calibration_gap | 0.1250|
| accuracy | 0.375|
| precision | 0.375|
| recall | 1.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 2 | 0.2500 | 0.6931 |
| MID | 6 | 0.2500 | 0.6931 |
| lifecycle:cold | 4 | 0.2500 | 0.6931 |
| lifecycle:warm | 4 | 0.2500 | 0.6931 |
| offer:CORE_BUNDLE | 1 | 0.2500 | 0.6931 |
| offer:PREMIUM | 4 | 0.2500 | 0.6931 |
| offer:SINGLE | 2 | 0.2500 | 0.6931 |
| offer:SMALL_BUNDLE | 1 | 0.2500 | 0.6931 |
| price:HIGH | 2 | 0.2500 | 0.6931 |
| price:MID | 6 | 0.2500 | 0.6931 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.067 |
| fan_recent_rate | 0.067 |
| global_rate | 0.067 |
| offer_type_mean_rate | 0.133 |
| offer_type_most_frequent_rate | 0.500 |

## Counterfactual (same fan x same context x different offer)

_none (model abstained or not run)_

> EXTRAPOLATIVE / OFF-POLICY ADVISORY: alternatives were not exposed

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | d2ec5bc6 | 0.2500 | 0.6931 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `b4825bcc-a153-42e7-a5e2-0d592777fa64` |
| seed | `11` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `d2ec5bc611e75b9d` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
