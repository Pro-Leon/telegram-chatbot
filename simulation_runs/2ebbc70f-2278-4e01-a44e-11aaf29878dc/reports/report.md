# Evaluation Report — balanced

**simulation_id:** `2ebbc70f-2278-4e01-a44e-11aaf29878dc`
**seed:** `11` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `d0c56cc3eb9bb348`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:25:25.760797+03:00`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past->future)

| metric | value |
|---|---|
| log_loss | 0.6421|
| brier | 0.2201|
| calibration_gap | 0.0991|
| accuracy | 0.667|
| precision | n/a |
| recall | 0.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 5 | 0.2411 | 0.6723 |
| LOW | 3 | 0.2911 | 0.7465 |
| MID | 7 | 0.1747 | 0.5758 |
| lifecycle:cold | 5 | 0.1496 | 0.4680 |
| lifecycle:hot | 2 | 0.3237 | 0.8589 |
| lifecycle:warm | 8 | 0.2382 | 0.6968 |
| offer:CORE_BUNDLE | 2 | 0.0119 | 0.1098 |
| offer:PREMIUM | 4 | 0.2250 | 0.7164 |
| offer:SINGLE | 4 | 0.2080 | 0.5878 |
| offer:SMALL_BUNDLE | 5 | 0.3091 | 0.8391 |
| price:HIGH | 5 | 0.2411 | 0.6723 |
| price:LOW | 3 | 0.2911 | 0.7465 |
| price:MID | 7 | 0.1747 | 0.5758 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.283 |
| fan_recent_rate | 0.283 |
| global_rate | 0.283 |
| offer_type_mean_rate | 0.287 |
| offer_type_most_frequent_rate | 0.278 |

## Counterfactual (same fan x same context x different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9731140 | 0.138 | 1 |
| 9427586 | 0.360 | 1 |
| 9033572 | 0.069 | 1 |

## Stress (n->Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 60 | d0c56cc3 | 0.2201 | 0.6421 |
| 90 | d0c56cc3 | 0.2201 | 0.6421 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `2ebbc70f-2278-4e01-a44e-11aaf29878dc` |
| seed | `11` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `d0c56cc3eb9bb348` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
