"""Phase 79C — Steps 5-8: Three-LLM, Context Engine, Commerce, Side-effect"""
import ast

src_path = "recovery_llm_worker/reconstructed/llm_worker_recovered.py"
with open(src_path, "r", encoding="utf-8") as f:
    source = f.read()

print("=" * 70)
print("STEP 5 - THREE-LLM INVARIANT VALIDATION")
print("=" * 70)

llm_checks = {
    "extract_commerce_signals": "LLM #1 — Commerce signal extraction",
    "generate_draft": "LLM #2 — Draft generation (non-tool path)",
    "generate_draft_with_tools": "LLM #2 — Draft generation (tool path)",
    "score_draft": "LLM #3 — Draft scoring",
}

print("\n[1] Three-LLM pipeline symbols:")
for sym, desc in llm_checks.items():
    present = sym in source
    print(f"  {'PASS' if present else 'MISSING'}: {sym} — {desc}")

fourth_llm_indicators = ["llm_4", "fourth_llm", "gpt4", "gpt_4", "claude", "llm_stage_4"]
found_fourth = [ind for ind in fourth_llm_indicators if ind in source.lower()]
if found_fourth:
    print(f"\n  FAIL: Fourth LLM indicators found: {found_fourth}")
else:
    print(f"\n  PASS: No fourth LLM detected")

import re
llm1_calls = re.findall(r"extract_commerce_signals\s*\(", source)
llm2_calls = re.findall(r"generate_draft\s*\(|generate_draft_with_tools\s*\(", source)
llm3_calls = re.findall(r"score_draft\s*\(", source)
print(f"\n[2] Three-LLM invocation pattern:")
print(f"  LLM #1 (extract_commerce_signals) calls: {len(llm1_calls)}")
print(f"  LLM #2 (generate_draft*) calls: {len(llm2_calls)}")
print(f"  LLM #3 (score_draft) calls: {len(llm3_calls)}")
if len(llm1_calls) >= 1 and len(llm2_calls) >= 1 and len(llm3_calls) >= 1:
    print("  PASS: All three LLMs invoked at least once")
else:
    print("  FAIL: Missing LLM invocation")

print()
print("=" * 70)
print("STEP 6 - CONTEXT ENGINE VALIDATION")
print("=" * 70)

ce_checks = [
    ("observe_context_engine", "Context Engine observation call"),
    ("context_engine_observational", "Observational mode flag"),
    ("context_engine_enabled", "Enabled flag"),
    ("context_engine_tokens", "Token budget tracking"),
    ("context_engine_chars", "Character budget tracking"),
    ("context_engine_candidates", "Candidates count"),
    ("context_engine_selected", "Selected count"),
    ("context_engine_dropped", "Dropped count"),
    ("context_engine_ms", "Latency tracking"),
    ("context_engine_failed", "Failure tracking"),
]

print("\n[1] Context Engine telemetry keys:")
for key, desc in ce_checks:
    present = key in source
    print(f"  {'PASS' if present else 'MISSING'}: {key} — {desc}")

print("\n[2] Authority boundary:")
auth_checks = [
    ("OBSERVATIONAL ONLY" in source or "observational" in source.lower(), "OBSERVATIONAL mode"),
    ("context_engine_observational" in source, "observational flag"),
    ("context_engine_enabled" in source, "enabled flag"),
]
for check, desc in auth_checks:
    print(f"  {'PASS' if check else 'FAIL'}: {desc}")

escalation_terms = ["promote", "full_control", "active_control", "override_decision"]
found_escalation = [t for t in escalation_terms if t in source.lower()]
if found_escalation:
    print(f"\n  WARN: Authority escalation terms found: {found_escalation}")
else:
    print(f"  PASS: No authority escalation terms found")

print()
print("=" * 70)
print("STEP 7 - COMMERCE AUTHORITY VALIDATION")
print("=" * 70)

commerce_checks = [
    "derive_persona_behavior_state", "render_persona_behavior_block", "validate_persona_voice",
    "build_conversational_commerce_state", "classify_outcome", "classify_canonical_outcome",
    "update_strategy_evidence", "update_strategy_evidence_extended", "outcome_strength",
    "attribute_purchase", "extract_explicit_memories", "add_memory_item",
    "extract_fan_knowledge", "add_knowledge_item", "get_knowledge_memory",
    "retrieve_relevant_memories", "retrieve_relevant_knowledge", "resolve_open_loop",
    "observe_behavioral_signal", "enrich_telemetry_with_funnel", "make_exposure",
    "persist_exposure", "compute_fatigue", "get_exposures_memory",
    "deterministic_assignment", "assign_variant", "derive_commercial_objective",
    "derive_conversation_state", "compute_pressure", "derive_risk",
    "build_operation_decision", "derive_lifecycle", "stage_for_objective",
    "get_user_profile", "update_user_profile", "get_handoff_memory",
]

print(f"\n[1] Commerce feature symbols ({len(commerce_checks)} checked):")
found = sum(1 for sym in commerce_checks if sym in source)
missing = [sym for sym in commerce_checks if sym not in source]
print(f"  {found}/{len(commerce_checks)} present")
if missing:
    for sym in missing:
        print(f"  MISSING: {sym}")
else:
    print("  PASS: All commerce features present")

print()
print("=" * 70)
print("STEP 8 - SIDE-EFFECT AUDIT")
print("=" * 70)

print("\n[1] Dangerous imports:")
for imp in ["subprocess", "os.system", "shutil.rmtree", "os.remove", "os.unlink"]:
    print(f"  {'WARN' if imp in source else 'PASS'}: {imp}")

print("\n[2] File I/O operations:")
for op in ["open(", "os.path.exists", "os.makedirs"]:
    count = source.count(op)
    print(f"  INFO: {op} — {count} occurrences")

print("\n[3] Network operations:")
for op in ["requests.", "httpx.", "aiohttp.", "urllib.", "socket."]:
    print(f"  {'WARN' if op in source else 'PASS'}: {op}")

print("\n[4] Global state modifications:")
for op in ["global ", "__", "setattr(", "globals().update"]:
    count = source.count(op)
    print(f"  INFO: {op} — {count} occurrences")

print("\n[5] Exit/shutdown operations:")
for op in ["sys.exit", "os._exit", "raise SystemExit"]:
    print(f"  {'WARN' if op in source else 'PASS'}: {op}")

print("\n[6] Random/non-deterministic:")
for op in ["random.", "secrets.", "uuid.uuid4"]:
    count = source.count(op)
    print(f"  INFO: {op} — {count} occurrences")

print("\n[7] Pickle/serialization:")
for op in ["pickle.", "marshal.", "shelve."]:
    print(f"  {'WARN' if op in source else 'PASS'}: {op}")
