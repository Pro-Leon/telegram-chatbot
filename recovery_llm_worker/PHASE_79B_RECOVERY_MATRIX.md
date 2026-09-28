# PHASE 79B — RECOVERY MATRIX

## 1. .pyc Symbols vs Reconstructed Symbols

| Symbol | .pyc | Reconstructed | Status |
|--------|------|---------------|--------|
| `build_qwen3_context` | ✓ | ✓ L524 | RECOVERED |
| `extract_commerce_signals` | ✓ | ✓ L669 | RECOVERED |
| `generate_draft` | ✓ | ✓ L1105 | RECOVERED |
| `generate_draft_with_tools` | ✓ | ✓ L1087 | RECOVERED |
| `score_draft` | ✓ | ✓ L1112 | RECOVERED |
| `publish_event` | ✓ | ✓ L541,1320,1481 | RECOVERED |
| `publish_events_batch` | ✓ | ✓ L1286 | RECOVERED |
| `observe_context_engine` | ✓ | ✓ L554 | RECOVERED |
| `ShadowRunner` | ✓ | ✓ L652 | RECOVERED |
| `ShadowConfig` | ✓ | ✓ L648 | RECOVERED |
| `derive_persona_behavior_state` | ✓ | ✓ L1050 | RECOVERED |
| `render_persona_behavior_block` | ✓ | ✓ L1070 | RECOVERED |
| `validate_persona_voice` | ✓ | ✓ L1138 | RECOVERED |
| `update_strategy_evidence` | ✓ | ✓ L1401 | RECOVERED |
| `update_strategy_evidence_extended` | ✓ | ✓ L1436 | RECOVERED |
| `classify_outcome` | ✓ | ✓ L1458 | RECOVERED |
| `classify_canonical_outcome` | ✓ | ✓ L1462 | RECOVERED |
| `outcome_strength` | ✓ | ✓ L1466 | RECOVERED |
| `attribute_purchase` | ✓ | ✓ L1470 | RECOVERED |
| `extract_explicit_memories` | ✓ | ✓ L595 | RECOVERED |
| `add_memory_item` | ✓ | ✓ L600 | RECOVERED |
| `extract_fan_knowledge` | ✓ | ✓ L615 | RECOVERED |
| `add_knowledge_item` | ✓ | ✓ L624 | RECOVERED |
| `get_knowledge_memory` | ✓ | ✓ L627 | RECOVERED |
| `retrieve_relevant_memories` | ✓ | ✓ L920 | RECOVERED |
| `retrieve_relevant_knowledge` | ✓ | ✓ L1065 | RECOVERED |
| `resolve_open_loop` | ✓ | ✓ L679 | RECOVERED |
| `observe_behavioral_signal` | ✓ | ✓ L635 | RECOVERED |
| `enrich_telemetry_with_funnel` | ✓ | ✓ L1471 | RECOVERED |
| `build_agent_state` | ✓ | ✓ L990 | RECOVERED |
| `run_agent_runtime` | ✓ | ✓ L996 | RECOVERED |
| `should_use_agent` | ✓ | ✓ L974 | RECOVERED |
| `CanaryConfig` | ✓ | ✓ L977 | RECOVERED |
| `AgentMemory` | ✓ | ✓ L993 | RECOVERED |
| `load_persisted_state` | ✓ | ✓ L1506 | RECOVERED |
| `get_model` | ✓ | ✓ L1518 | RECOVERED |
| `acquire_user_lock` | ✓ | ✓ L490 | RECOVERED |
| `release_user_lock` | ✓ | ✓ L1493 | RECOVERED |
| `enqueue_send` | ✓ | ✓ L1251 | RECOVERED |
| `add_to_operator_queue` | ✓ | ✓ L567,1212,1279 | RECOVERED |
| `is_auto_reply_enabled` | ✓ | ✓ L1200 | RECOVERED |
| `CommerceSelectionResult` | ✓ | ✓ | RECOVERED |
| `ToolAuthContext` | ✓ | ✓ L1077 | RECOVERED |
| `dispatch_tool` | ✓ | ✓ L269 | RECOVERED |
| `build_conversational_commerce_state` | ✓ | ✓ L720 | RECOVERED |
| `compute_pressure` | ✓ | ✓ L811 | RECOVERED |
| `derive_risk` | ✓ | ✓ L818 | RECOVERED |
| `build_operation_decision` | ✓ | ✓ L824 | RECOVERED |
| `derive_lifecycle` | ✓ | ✓ L808 | RECOVERED |
| `autonomous_allowed` | ✓ | ✓ L875 | RECOVERED |
| `is_rollout_active_for` | ✓ | ✓ L882 | RECOVERED |
| `is_commerce_paused` | ✓ | ✓ L860 | RECOVERED |
| `is_reengagement_paused` | ✓ | ✓ L863 | RECOVERED |
| `record_metric` | ✓ | ✓ L889,1162 | RECOVERED |
| `record_audit` | ✓ | ✓ L1175 | RECOVERED |
| `OperationalAuditRecord` | ✓ | ✓ L1172 | RECOVERED |
| `operational_decision` | ✓ | ✓ L930 | RECOVERED |
| `execute_operational_recommendation` | ✓ | ✓ L937 | RECOVERED |
| `evaluate_production_health` | ✓ | ✓ L943 | RECOVERED |
| `MetricWindow` | ✓ | ✓ L944 | RECOVERED |
| `make_exposure` | ✓ | ✓ L700 | RECOVERED |
| `persist_exposure` | ✓ | ✓ L703 | RECOVERED |
| `compute_fatigue` | ✓ | ✓ L706 | RECOVERED |
| `get_exposures_memory` | ✓ | ✓ L709 | RECOVERED |
| `deterministic_assignment` | ✓ | ✓ L712 | RECOVERED |
| `assign_variant` | ✓ | ✓ L715 | RECOVERED |
| `derive_commercial_objective` | ✓ | ✓ L852 | RECOVERED |
| `derive_conversation_state` | ✓ | ✓ L696 | RECOVERED |
| `get_telemetry_collector` | ✓ | ✓ L466 | RECOVERED |
| `start_generation` | ✓ | ✓ L468 | RECOVERED |
| `get_user_profile` | ✓ | ✓ L1386 | RECOVERED |
| `update_user_profile` | ✓ | ✓ L1383 | RECOVERED |
| `get_handoff_memory` | ✓ | ✓ L895 | RECOVERED |
| `stage_for_objective` | ✓ | ✓ L1440 | RECOVERED |

## 2. Missing Feature Blocks

| Block | .pyc Evidence | Reconstructed | Confidence |
|-------|--------------|---------------|------------|
| Context timing | `_context_start`, `_context_end` vars | ✓ | HIGH |
| Persona block extraction | `_persona_block` var | ✓ | HIGH |
| Creator fail-closed | `_fail_closed_creator_unavailable` var | ✓ | HIGH |
| Memory persistence | `add_memory_item` calls | ✓ | HIGH |
| Fan knowledge persistence | `add_knowledge_item`, `get_knowledge_memory` | ✓ | HIGH |
| Behavioral hour tracking | `hour_utc` var | ✓ | HIGH |
| Shadow timing | `_shadow_start`, `_shadow_end` vars | ✓ | HIGH |
| Commerce state derivation | `_conv_state`, `_cstate`, desire/temp/readiness/window | ✓ | HIGH |
| Open loop resolution | `resolve_open_loop` call | ✓ | HIGH |
| Experiment tracking | `make_exposure`, `persist_exposure`, `compute_fatigue` | ✓ | HIGH |
| Pressure/risk/operation | `_pressure`, `_risk`, `_op_dec` | ✓ | HIGH |
| Commercial objective + pause | `_skip_qwen_due_to_pause`, rollout checks | ✓ | HIGH |
| Operational intelligence | `_op_health`, `_op_loops`, `_op_decision` | ✓ | HIGH |
| Agent canary | `_use_agent`, `_canary_config` | ✓ | HIGH |
| Agent runtime | `_agent_state`, `_agent_result` | ✓ | HIGH |
| Persona behavior + knowledge | `_behavior_block`, `retrieve_relevant_knowledge` | ✓ | HIGH |
| Persona validation + audit | `_persona_validation`, `_PcAudit` | ✓ | HIGH |
| Shadow evaluation | `_shadow_result`, `evaluate_shadow_response` | ✓ | HIGH |
| Event batching | `publish_events_batch` | ✓ | HIGH |
| Strategy learning full | `update_strategy_evidence_extended`, `attribute_purchase` | ✓ | HIGH |

## 3. Unresolved Blocks

| Block | Evidence | Status | Impact |
|-------|----------|--------|--------|
| Detailed telemetry field recording | 138 names only in .pyc | PARTIAL | LOW — telemetry is observational |
| Context engine detailed metrics | `context_engine_*` fields | PARTIAL | LOW — observational only |
| Scoring detailed fields | `scoring_*` fields | PARTIAL | LOW — telemetry only |

## 4. Confidence Summary

| Category | Count | Confidence |
|----------|-------|------------|
| Fully recovered blocks | 20 | HIGH |
| Partially recovered | 3 | MEDIUM |
| Not recovered | 0 | — |

## 5. Authority Classification

| Component | Authority | Status |
|-----------|-----------|--------|
| LLM #1 (signals) | AUTHORITATIVE | PRESERVED |
| LLM #2 (draft) | AUTHORITATIVE | PRESERVED |
| LLM #3 (scoring) | AUTHORITATIVE | PRESERVED |
| Commerce decision | DETERMINISTIC | PRESERVED |
| Persona validation | DETERMINISTIC | PRESERVED |
| Context Engine | OBSERVATIONAL | PRESERVED |
| Q1 Shadow | OBSERVATIONAL | PRESERVED |
| Canary | OBSERVATIONAL | PRESERVED |
| Agent runtime | AUTHORIZED | PRESERVED |
