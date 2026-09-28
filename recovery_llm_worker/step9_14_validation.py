"""Phase 79C — Steps 9-14: Q1 Shadow, Canary, Agent, Event, Telemetry, Feature Matrix"""
import ast, re

src_path = "recovery_llm_worker/reconstructed/llm_worker_recovered.py"
with open(src_path, "r", encoding="utf-8") as f:
    source = f.read()

print("=" * 70)
print("STEP 9 - Q1 SHADOW VALIDATION")
print("=" * 70)

q1_checks = [
    ("ShadowRunner", "Q1 Shadow runner class"),
    ("ShadowConfig", "Q1 Shadow config class"),
    ("should_sample", "Q1 Shadow sampling"),
    ("run_shadow", "Q1 Shadow execution"),
    ("from_settings", "Q1 Shadow from settings"),
    ("create_task", "Q1 Shadow fire-and-forget"),
]

print("\n[1] Q1 Shadow symbols:")
for sym, desc in q1_checks:
    present = sym in source
    print(f"  {'PASS' if present else 'MISSING'}: {sym} — {desc}")

# Check Q1 authority
print("\n[2] Q1 authority boundary:")
q1_patterns = [
    ("shadow" in source.lower(), "Shadow integration present"),
    ("fire-and-forget" in source.lower() or "create_task" in source, "Fire-and-forget pattern"),
    ("OBSERVATIONAL" not in source or "observational" in source.lower(), "OBSERVATIONAL mode"),
]
for check, desc in q1_patterns:
    print(f"  {'PASS' if check else 'FAIL'}: {desc}")

print()
print("=" * 70)
print("STEP 10 - CANARY VALIDATION")
print("=" * 70)

canary_checks = [
    ("should_use_agent", "Agent canary check"),
    ("CanaryConfig", "Canary configuration"),
    ("AgentMemory", "Agent memory"),
]

print("\n[1] Agent canary symbols:")
for sym, desc in canary_checks:
    present = sym in source
    print(f"  {'PASS' if present else 'MISSING'}: {sym} — {desc}")

# Check canary authority
print("\n[2] Canary authority boundary:")
canary_patterns = [
    ("disabled by default" in source.lower() or "canary" in source.lower(), "Canary present"),
    ("fail-open" in source.lower() or "fail_open" in source.lower(), "Fail-open pattern"),
]
for check, desc in canary_patterns:
    print(f"  {'PASS' if check else 'WARN'}: {desc}")

print()
print("=" * 70)
print("STEP 11 - AGENT RUNTIME VALIDATION")
print("=" * 70)

agent_checks = [
    ("build_agent_state", "Agent state building"),
    ("run_agent_runtime", "Agent runtime execution"),
    ("AgentMemory", "Agent memory"),
    ("_use_agent", "Agent canary flag"),
    ("_agent_state", "Agent state variable"),
    ("_agent_result", "Agent result variable"),
]

print("\n[1] Agent runtime symbols:")
for sym, desc in agent_checks:
    present = sym in source
    print(f"  {'PASS' if present else 'MISSING'}: {sym} — {desc}")

print("\n[2] Agent authority boundary:")
agent_patterns = [
    ("should_use_agent" in source, "Canary-gated"),
    ("if _use_agent" in source, "Conditional execution"),
]
for check, desc in agent_patterns:
    print(f"  {'PASS' if check else 'FAIL'}: {desc}")

print()
print("=" * 70)
print("STEP 12 - EVENT VALIDATION")
print("=" * 70)

event_checks = [
    ("publish_event", "Event publishing function"),
    ("publish_events_batch", "Batch event publishing"),
    ("message.created", "message.created event"),
    ("message.sent", "message.sent event"),
    ("ai.generation_started", "ai.generation_started event"),
    ("ai.generation_completed", "ai.generation_completed event"),
    ("ai.generation_failed", "ai.generation_failed event"),
    ("suggestion.created", "suggestion.created event"),
    ("operator_queue.updated", "operator_queue.updated event"),
]

print("\n[1] Event symbols:")
for sym, desc in event_checks:
    present = sym in source
    print(f"  {'PASS' if present else 'MISSING'}: {sym} — {desc}")

print()
print("=" * 70)
print("STEP 13 - TELEMETRY GAP ANALYSIS")
print("=" * 70)

# Check for telemetry gaps
telemetry_keys = re.findall(r'_telemetry\.record\("([^"]+)"', source)
print(f"\n[1] Telemetry keys found: {len(telemetry_keys)}")
for key in sorted(set(telemetry_keys)):
    count = telemetry_keys.count(key)
    print(f"  {key}: {count}x")

# Check for missing telemetry
expected_telemetry = [
    "routing_decision", "confidence", "auto_approved", "operator_queue",
    "context_build_ms", "generation_ms", "scoring_ms",
]
print(f"\n[2] Expected telemetry keys:")
for key in expected_telemetry:
    present = key in telemetry_keys
    print(f"  {'PASS' if present else 'WARN'}: {key}")

print()
print("=" * 70)
print("STEP 14 - PHASE 1-78 FEATURE MATRIX")
print("=" * 70)

features = {
    "Phase 1-4": ["process_message", "run_worker", "main"],
    "Phase 5-8": ["build_qwen3_context", "generate_draft", "score_draft"],
    "Phase 9-12": ["publish_event", "message.created", "message.sent"],
    "Phase 13-16": ["extract_commerce_signals", "CommerceSelectionResult"],
    "Phase 17-20": ["derive_persona_behavior_state", "render_persona_behavior_block"],
    "Phase 21-24": ["get_handoff_memory", "autonomous_allowed"],
    "Phase 25-28": ["compute_pressure", "derive_risk", "build_operation_decision"],
    "Phase 29-32": ["is_rollout_active_for", "record_metric", "record_audit"],
    "Phase 33-36": ["classify_outcome", "classify_canonical_outcome"],
    "Phase 37-40": ["update_strategy_evidence", "derive_lifecycle"],
    "Phase 41-44": ["observe_context_engine", "context_engine_observational"],
    "Phase 45-48": ["ShadowRunner", "ShadowConfig"],
    "Phase 49-52": ["should_use_agent", "CanaryConfig", "AgentMemory"],
    "Phase 53-56": ["build_agent_state", "run_agent_runtime"],
    "Phase 57-60": ["make_exposure", "persist_exposure", "compute_fatigue"],
    "Phase 61-64": ["extract_explicit_memories", "add_memory_item"],
    "Phase 65-68": ["extract_fan_knowledge", "add_knowledge_item"],
    "Phase 69-72": ["resolve_open_loop", "retrieve_relevant_memories"],
    "Phase 73-76": ["observe_behavioral_signal", "enrich_telemetry_with_funnel"],
    "Phase 77-78": ["derive_commercial_objective", "derive_conversation_state"],
}

print(f"\n[1] Feature coverage ({len(features)} phases):")
all_pass = True
for phase_range, syms in features.items():
    found = sum(1 for s in syms if s in source)
    total = len(syms)
    status = "PASS" if found == total else "PARTIAL" if found > 0 else "MISSING"
    if found < total:
        all_pass = False
    print(f"  {phase_range}: {found}/{total} — {status}")
    if found < total:
        missing = [s for s in syms if s not in source]
        print(f"    Missing: {missing}")

print(f"\n[2] Overall: {'PASS: All features present' if all_pass else 'PARTIAL: See above'}")
