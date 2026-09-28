# Evaluation Report — balanced

**simulation_id:** `85d80444-2ba0-41f7-916f-1756a28e2ddd`
**seed:** `11` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `7b6b261fd9437c3e`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`
**as_of:** `2026-09-20T21:17:51.931707+03:00`

**Split:** train `15` valid `7` test `8` (chronological 60/15/15)

## Metrics (past→future)

| metric | value |
|---|---|
| log_loss | 1.1145|
| brier | 0.3559|
| calibration_gap | 0.1805|
| accuracy | 0.625|
| precision | 0.667|
| recall | 0.500|
| coverage | 1.000|

## By Cohort

| cohort | n | brier | log_loss |
|---|---|---|---|
| HIGH | 6 | 0.4601 | 1.4153 |
| MID | 2 | 0.0433 | 0.2118 |
| lifecycle:cold | 2 | 0.3909 | 1.0305 |
| lifecycle:hot | 1 | 0.9285 | 3.3131 |
| lifecycle:warm | 5 | 0.2274 | 0.7083 |
| offer:CORE_BUNDLE | 2 | 0.0433 | 0.2118 |
| offer:PREMIUM | 2 | 0.4915 | 1.4367 |
| offer:SINGLE | 2 | 0.4155 | 1.0805 |
| offer:SMALL_BUNDLE | 2 | 0.4733 | 1.7288 |
| price:HIGH | 6 | 0.4601 | 1.4153 |
| price:MID | 2 | 0.0433 | 0.2118 |

## Baselines (5)

| baseline | rate |
|---|---|
| creator_rate | 0.267 |
| fan_recent_rate | 0.267 |
| global_rate | 0.267 |
| offer_type_mean_rate | 0.272 |
| offer_type_most_frequent_rate | 0.400 |

## Counterfactual (same fan×same context×different offer)

is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exp...

| opp | primary_prob | alternatives |
|---|---|---|
| 9669288 | 0.786 | 1 |
| 9747673 | 0.280 | 1 |
| 9264680 | 0.135 | 1 |

## Stress (n→Brier)

| n | dataset_hash | brier | log_loss |
|---|---|---|---|
| 30 | 7b6b261f | 0.3559 | 1.1145 |

## Reproducibility

| field | value |
|---|---|
| simulation_id | `85d80444-2ba0-41f7-916f-1756a28e2ddd` |
| seed | `11` |
| scenario_id | `balanced` |
| scenario_version | `v1` |
| behavior_model_version | v1 |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `7b6b261fd9437c3e` |
| FEATURE_SCHEMA_VERSION | `p356.features.v1` |
