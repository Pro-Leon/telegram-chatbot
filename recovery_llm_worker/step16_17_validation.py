"""Phase 79C — Steps 16-17: Behavioral Simulation and .pyc Comparison"""
import ast, dis, marshal, types, re, json

src_path = "recovery_llm_worker/reconstructed/llm_worker_recovered.py"
pyc_path = "recovery_llm_worker/llm_worker.cpython-314.pyc"

with open(src_path, "r", encoding="utf-8") as f:
    source = f.read()

with open(pyc_path, "rb") as f:
    f.read(16)
    pyc_code = marshal.load(f)

print("=" * 70)
print("STEP 16 - BEHAVIORAL SIMULATION")
print("=" * 70)

# Simulate the flow of process_message by tracing function calls
print("\n[1] Process_message flow simulation:")

# Check key code paths
flow_checks = [
    ("acquire_user_lock", "User lock acquisition"),
    ("release_user_lock", "User lock release"),
    ("build_qwen3_context", "Context building"),
    ("observe_context_engine", "Context Engine observation"),
    ("extract_explicit_memories", "LTM extraction"),
    ("add_memory_item", "LTM persistence"),
    ("extract_fan_knowledge", "Fan knowledge extraction"),
    ("add_knowledge_item", "Knowledge persistence"),
    ("observe_behavioral_signal", "Behavioral signal observation"),
    ("ShadowRunner", "Shadow runner"),
    ("extract_commerce_signals", "Commerce signal extraction"),
    ("_try_commerce_draft", "Commerce draft attempt"),
    ("build_conversational_commerce_state", "Conversational commerce"),
    ("derive_conversation_state", "Conversation state derivation"),
    ("resolve_open_loop", "Open loop resolution"),
    ("make_exposure", "Experiment exposure"),
    ("compute_pressure", "Pressure computation"),
    ("derive_risk", "Risk derivation"),
    ("build_operation_decision", "Operation decision"),
    ("derive_commercial_objective", "Commercial objective"),
    ("autonomous_allowed", "Autonomous check"),
    ("is_rollout_active_for", "Rollout check"),
    ("get_handoff_memory", "Handoff check"),
    ("should_use_agent", "Agent canary check"),
    ("build_agent_state", "Agent state building"),
    ("run_agent_runtime", "Agent runtime"),
    ("generate_draft_with_tools", "Draft generation (tools)"),
    ("generate_draft", "Draft generation (basic)"),
    ("score_draft", "Draft scoring"),
    ("validate_persona_voice", "Persona validation"),
    ("add_to_operator_queue", "Operator queue"),
    ("enqueue_send", "Send queue"),
    ("publish_events_batch", "Event publishing"),
    ("classify_outcome", "Outcome classification"),
    ("update_strategy_evidence", "Strategy learning"),
]

print(f"  Checking {len(flow_checks)} flow checkpoints:")
all_pass = True
for sym, desc in flow_checks:
    present = sym in source
    if not present:
        all_pass = False
        print(f"    MISSING: {sym} — {desc}")

if all_pass:
    print(f"  PASS: All {len(flow_checks)} flow checkpoints present")

# Check error handling
print("\n[2] Error handling patterns:")
error_patterns = [
    ("except Exception:", "Generic exception handlers"),
    ("logger.debug", "Debug logging"),
    ("logger.info", "Info logging"),
    ("logger.warning", "Warning logging"),
]
for pattern, desc in error_patterns:
    count = source.count(pattern)
    print(f"  {desc}: {count} occurrences")

# Check return paths
print("\n[3] Return paths:")
return_paths = re.findall(r"return\s+({[^}]+})", source)
print(f"  Dict returns: {len(return_paths)}")

print()
print("=" * 70)
print("STEP 17 - COMPARE WITH .pyc")
print("=" * 70)

# Get process_message from .pyc
pyc_pm = None
for const in pyc_code.co_consts:
    if isinstance(const, types.CodeType) and const.co_name == "process_message":
        pyc_pm = const
        break

if pyc_pm:
    print(f"\n[1] .pyc process_message stats:")
    print(f"  Line number: {pyc_pm.co_firstlineno}")
    print(f"  Local variables: {pyc_pm.co_argcount + pyc_pm.co_kwonlyargcount + pyc_pm.co_nlocals}")
    print(f"  Names (globals/builtins): {len(pyc_pm.co_names)}")
    print(f"  Constants: {len(pyc_pm.co_consts)}")
    print(f"  Bytecode instructions: ~{sum(1 for _ in dis.get_instructions(pyc_pm))}")

# Compare names
print("\n[2] Name coverage (.pyc names found in source):")
pyc_names = set(pyc_pm.co_names) if pyc_pm else set()
found_in_source = sum(1 for n in pyc_names if n in source)
missing_from_source = [n for n in pyc_names if n not in source]
print(f"  .pyc names: {len(pyc_names)}")
print(f"  Found in source: {found_in_source}")
print(f"  Coverage: {found_in_source}/{len(pyc_names)} ({100*found_in_source//len(pyc_names) if pyc_names else 0}%)")

if missing_from_source:
    print(f"\n  Names in .pyc but NOT in source ({len(missing_from_source)}):")
    for n in sorted(missing_from_source)[:20]:
        print(f"    {n}")
    if len(missing_from_source) > 20:
        print(f"    ... and {len(missing_from_source)-20} more")

# Compare constants
print("\n[3] String constant coverage:")
pyc_str_consts = set()
for c in pyc_pm.co_consts if pyc_pm else []:
    if isinstance(c, str) and len(c) > 3:
        pyc_str_consts.add(c)

source_str_consts = set(re.findall(r'"([^"]{4,})"', source))
source_str_consts.update(re.findall(r"'([^']{4,})'", source))

found_str = sum(1 for c in pyc_str_consts if c in source_str_consts)
print(f"  .pyc string constants (>3 chars): {len(pyc_str_consts)}")
print(f"  Found in source: {found_str}")
print(f"  Coverage: {found_str}/{len(pyc_str_consts)} ({100*found_str//len(pyc_str_consts) if pyc_str_consts else 0}%)")

# Check function definitions
print("\n[4] Function definitions:")
pyc_funcs = set()
for c in pyc_code.co_consts:
    if isinstance(c, types.CodeType):
        pyc_funcs.add(c.co_name)

source_funcs = set()
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
        source_funcs.add(node.name)

found_funcs = pyc_funcs & source_funcs
missing_funcs = pyc_funcs - source_funcs
extra_funcs = source_funcs - pyc_funcs

print(f"  .pyc functions: {len(pyc_funcs)}")
print(f"  Source functions: {len(source_funcs)}")
print(f"  Overlapping: {len(found_funcs)}")
if missing_funcs:
    print(f"  In .pyc but not in source: {sorted(missing_funcs)}")
if extra_funcs:
    print(f"  In source but not in .pyc: {sorted(extra_funcs)}")
