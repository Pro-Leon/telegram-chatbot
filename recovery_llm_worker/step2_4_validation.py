"""Phase 79C — Steps 2-4: Static, Import, and Symbol Validation"""
import ast, py_compile, sys, os, importlib.util

print("=" * 70)
print("STEP 2 - STATIC VALIDATION")
print("=" * 70)

src_path = "recovery_llm_worker/reconstructed/llm_worker_recovered.py"
with open(src_path, "r", encoding="utf-8") as f:
    source = f.read()

# 1. Compile check
print("\n[1] Compile check...")
try:
    py_compile.compile(src_path, doraise=True)
    print("  PASS: File compiles without errors")
except py_compile.PyCompileError as e:
    print(f"  FAIL: {e}")

# 2. AST parse
print("\n[2] AST parse...")
try:
    tree = ast.parse(source)
    print("  PASS: AST parse successful")
except SyntaxError as e:
    print(f"  FAIL: SyntaxError at line {e.lineno}: {e.msg}")
    tree = None

if tree:
    # 3. Function inventory
    print("\n[3] Function inventory:")
    funcs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            funcs.append((node.name, node.lineno, isinstance(node, ast.AsyncFunctionDef)))
    for name, line, is_async in sorted(funcs, key=lambda x: x[1]):
        prefix = "async " if is_async else ""
        print(f"  {prefix}def {name}() @ line {line}")

    # 4. Duplicate check
    print("\n[4] Duplicate definition check:")
    names = [f[0] for f in funcs]
    dupes = [n for n in names if names.count(n) > 1]
    if dupes:
        print(f"  WARN: Duplicates found: {set(dupes)}")
    else:
        print("  PASS: No duplicate definitions")

    # 5. Import check
    print("\n[5] Import analysis:")
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(("import", alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(("from", f"{module}.{alias.name}", node.lineno))

    suspicious = [i for i in imports if "git" in i[1].lower() or "subprocess" in i[1].lower() or "os.system" in i[1].lower()]
    if suspicious:
        print(f"  WARN: Suspicious imports: {suspicious}")
    else:
        print("  PASS: No suspicious imports")
    print(f"  Total imports: {len(imports)}")

    # 6. Async/sync classification
    async_funcs = [f for f in funcs if f[2]]
    sync_funcs = [f for f in funcs if not f[2]]
    print(f"\n[6] Async functions: {len(async_funcs)}, Sync functions: {len(sync_funcs)}")

    # 7. Line count
    line_count = source.count("\n") + 1
    print(f"\n[7] Total lines: {line_count}")

    # 8. Unresolved names check
    print("\n[8] Checking for obviously undefined names...")
    all_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            all_names.add(node.id)
    expected = ["logger", "_settings", "build_qwen3_context", "score_draft", "publish_event"]
    missing = [n for n in expected if n not in all_names]
    if missing:
        print(f"  WARN: Expected names not found as Name nodes: {missing}")
    else:
        print("  PASS: All expected names found")

print()
print("=" * 70)
print("STEP 3 - IMPORT VALIDATION")
print("=" * 70)

print("\n[1] Checking import module paths exist...")
warnings = 0
for imp_type, imp_name, line in imports[:50]:
    module_name = imp_name.split(".")[0]
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            print(f"  WARN: Module {module_name} not found (line {line})")
            warnings += 1
    except Exception:
        pass
if warnings == 0:
    print("  PASS: All checked modules found")
else:
    print(f"  {warnings} warnings (see above)")

print()
print("=" * 70)
print("STEP 4 - CRITICAL SYMBOL VALIDATION")
print("=" * 70)

critical_symbols = {
    "build_qwen3_context": "Context building",
    "extract_commerce_signals": "LLM #1 commerce signals",
    "generate_draft": "LLM #2 draft generation",
    "generate_draft_with_tools": "LLM #2 with tools",
    "score_draft": "LLM #3 scoring",
    "publish_event": "Event publishing",
    "publish_events_batch": "Batch event publishing",
    "observe_context_engine": "Context Engine observation",
    "ShadowRunner": "Q1 Shadow runner",
    "ShadowConfig": "Q1 Shadow config",
    "derive_persona_behavior_state": "Persona behavior",
    "render_persona_behavior_block": "Persona behavior rendering",
    "validate_persona_voice": "Persona validation",
    "update_strategy_evidence": "Strategy learning",
    "update_strategy_evidence_extended": "Extended strategy learning",
    "classify_outcome": "Conversation outcomes",
    "classify_canonical_outcome": "Canonical outcome classification",
    "outcome_strength": "Outcome strength",
    "attribute_purchase": "Purchase attribution",
    "extract_explicit_memories": "Long-term memory extraction",
    "add_memory_item": "Memory persistence",
    "extract_fan_knowledge": "Fan knowledge extraction",
    "add_knowledge_item": "Knowledge persistence",
    "get_knowledge_memory": "Knowledge retrieval",
    "retrieve_relevant_memories": "Memory retrieval",
    "retrieve_relevant_knowledge": "Knowledge retrieval",
    "resolve_open_loop": "Open loop resolution",
    "observe_behavioral_signal": "Behavioral signals",
    "enrich_telemetry_with_funnel": "Telemetry enrichment",
    "build_agent_state": "Agent runtime state",
    "run_agent_runtime": "Agent runtime execution",
    "should_use_agent": "Agent canary check",
    "CanaryConfig": "Canary configuration",
    "AgentMemory": "Agent memory",
    "load_persisted_state": "Production control state",
    "get_model": "Embedding model",
    "acquire_user_lock": "User locking",
    "release_user_lock": "User unlocking",
    "enqueue_send": "Send queue",
    "add_to_operator_queue": "Operator queue",
    "is_auto_reply_enabled": "Auto-reply check",
    "CommerceSelectionResult": "Commerce selection",
    "ToolAuthContext": "Tool authorization",
    "dispatch_tool": "Tool dispatch",
    "build_conversational_commerce_state": "Conversational commerce",
    "compute_pressure": "Pressure calculation",
    "derive_risk": "Risk derivation",
    "build_operation_decision": "Operation decision",
    "derive_lifecycle": "Lifecycle derivation",
    "autonomous_allowed": "Autonomous check",
    "is_rollout_active_for": "Rollout check",
    "is_commerce_paused": "Commerce pause check",
    "is_reengagement_paused": "Reengagement pause check",
    "record_metric": "Production metrics",
    "record_audit": "Audit recording",
    "OperationalAuditRecord": "Audit record type",
    "operational_decision": "Operational intelligence",
    "execute_operational_recommendation": "Operational execution",
    "evaluate_production_health": "Health evaluation",
    "MetricWindow": "Metric window enum",
    "make_exposure": "Experiment exposure",
    "persist_exposure": "Exposure persistence",
    "compute_fatigue": "Fatigue computation",
    "get_exposures_memory": "Exposure memory",
    "deterministic_assignment": "Deterministic assignment",
    "assign_variant": "Variant assignment",
    "derive_commercial_objective": "Commercial objective",
    "derive_conversation_state": "Conversation state",
    "get_telemetry_collector": "Telemetry collector",
    "start_generation": "Generation telemetry",
    "get_user_profile": "Profile retrieval",
    "update_user_profile": "Profile update",
    "get_handoff_memory": "Handoff memory",
    "stage_for_objective": "Stage for objective",
}

print(f"\nChecking {len(critical_symbols)} critical symbols:")
found = 0
missing_syms = []
for sym, desc in sorted(critical_symbols.items()):
    if sym in source:
        found += 1
    else:
        missing_syms.append(sym)
        print(f"  MISSING: {sym} ({desc})")

print(f"\nResult: {found}/{len(critical_symbols)} symbols present")
if not missing_syms:
    print("PASS: All critical symbols present")
else:
    print(f"FAIL: {len(missing_syms)} symbols missing")
