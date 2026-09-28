# Evaluation Report — balanced

**simulation_id:** `7dda7080-2e90-47ad-bce7-edcac6fcaf22`
**seed:** `42` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `929766a7b6c85182`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:19:11.063374+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 0.9421|
| brier | 0.3533|
| calibration_gap | 0.3192|
| accuracy | 0.500|
| precision | 0.429|
| recall | 1.000|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 5 | 0.2977 | 0.8092 |
| MID | 3 | 0.4459 | 1.1635 |
| lifecycle:cold | 5 | 0.4860 | 1.2372 |
| lifecycle:hot | 2 | 0.1135 | 0.4099 |
| lifecycle:warm | 1 | 0.1695 | 0.5305 |
| offer:PREMIUM | 1 | 0.1695 | 0.5305 |
| offer:SINGLE | 4 | 0.3581 | 0.9646 |
| offer:SMALL_BUNDLE | 3 | 0.4081 | 1.0493 |
| price:HIGH | 5 | 0.2977 | 0.8092 |
| price:MID | 3 | 0.4459 | 1.1635 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.467 |
| fan_recent_rate | 0.467 |
| global_rate | 0.467 |
| offer_type_mean_rate | 0.458 |
| offer_type_most_frequent_rate | 0.333 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9540910 | 0.636 | 1 |
| 9435161 | 0.801 | 1 |
| 9254159 | 0.692 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | 929766a7 | 0.3533 | 0.9421 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `7dda7080-2e90-47ad-bce7-edcac6fcaf22` |
| seed | `42` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `929766a7b6c85182` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
