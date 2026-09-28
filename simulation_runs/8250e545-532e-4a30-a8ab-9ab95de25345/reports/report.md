# Evaluation Report — balanced

**simulation_id:** `8250e545-532e-4a30-a8ab-9ab95de25345`
**seed:** `11` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `e8f89649ee1715f1`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:19:42.372923+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 1.0633|
| brier | 0.4051|
| calibration_gap | 0.3219|
| accuracy | 0.375|
| precision | 0.200|
| recall | 0.500|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 3 | 0.2167 | 0.5904 |
| LOW | 1 | 0.6678 | 1.6994 |
| MID | 4 | 0.4808 | 1.2590 |
| lifecycle:cold | 2 | 0.6474 | 1.6344 |
| lifecycle:hot | 2 | 0.4054 | 1.1742 |
| lifecycle:warm | 4 | 0.2838 | 0.7224 |
| offer:CORE_BUNDLE | 3 | 0.6010 | 1.5056 |
| offer:PREMIUM | 1 | 0.0016 | 0.0406 |
| offer:SINGLE | 1 | 0.0016 | 0.0411 |
| offer:SMALL_BUNDLE | 3 | 0.4782 | 1.3027 |
| price:HIGH | 3 | 0.2167 | 0.5904 |
| price:LOW | 1 | 0.6678 | 1.6994 |
| price:MID | 4 | 0.4808 | 1.2590 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.467 |
| fan_recent_rate | 0.467 |
| global_rate | 0.467 |
| offer_type_mean_rate | 0.427 |
| offer_type_most_frequent_rate | 0.400 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9372905 | 0.157 | 1 |
| 9088375 | 0.817 | 1 |
| 9495621 | 0.713 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | e8f89649 | 0.4051 | 1.0633 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `8250e545-532e-4a30-a8ab-9ab95de25345` |
| seed | `11` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `e8f89649ee1715f1` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
