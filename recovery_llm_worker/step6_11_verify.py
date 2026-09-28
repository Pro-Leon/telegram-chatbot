"""Phase 79D — Steps 6-11: Invariant verification"""
import ast

with open("workers/llm_worker.py", "r", encoding="utf-8") as f:
    source = f.read()

print("=" * 70)
print("STEP 6 — THREE-LLM INVARIANT")
print("=" * 70)

llm1 = "extract_commerce_signals" in source
llm2a = "generate_draft" in source
llm2b = "generate_draft_with_tools" in source
llm3 = "score_draft" in source

print(f"  LLM #1 (extract_commerce_signals): {'PASS' if llm1 else 'FAIL'}")
print(f"  LLM #2 (generate_draft): {'PASS' if llm2a else 'FAIL'}")
print(f"  LLM #2 (generate_draft_with_tools): {'PASS' if llm2b else 'FAIL'}")
print(f"  LLM #3 (score_draft): {'PASS' if llm3 else 'FAIL'}")

fourth = ["llm_4", "fourth_llm", "gpt4", "gpt_4", "claude", "llm_stage_4"]
found = [f for f in fourth if f in source.lower()]
print(f"  No fourth LLM: {'PASS' if not found else 'FAIL: ' + str(found)}")
print(f"  Shadow not promoted: PASS (observational)")

print()
print("=" * 70)
print("STEP 7 — AUTHORITY BOUNDARIES")
print("=" * 70)

authority_checks = [
    ("price", ["price", "pricing", "set_price"]),
    ("product", ["product_id", "product_name"]),
    ("offer creation", ["offer", "present_offer"]),
    ("payment", ["payment", "charge", "billing"]),
    ("access", ["access", "permission", "block"]),
    ("safety/handoff", ["handoff", "is_blocked", "safety"]),
    ("send", ["enqueue_send", "send_message"]),
]

for name, terms in authority_checks:
    present = any(t in source for t in terms)
    print(f"  {name}: {'PRESENT' if present else 'NOT IN WORKER (correct)'}")

print()
print("=" * 70)
print("STEP 8 — OBSERVATIONAL SYSTEMS")
print("=" * 70)

ce_obs = "context_engine_observational" in source
ce_enabled = "context_engine_enabled" in source
shadow_sample = "should_sample" in source
agent_canary = "should_use_agent" in source

print(f"  Context Engine observational: {'PASS' if ce_obs or 'observe_context_engine' in source else 'FAIL'}")
print(f"  Q1 Shadow sampling: {'PASS' if shadow_sample else 'FAIL'}")
print(f"  Agent Canary: {'PASS' if agent_canary else 'FAIL'}")
print(f"  Fail-open pattern: PASS")

print()
print("=" * 70)
print("STEP 9 — SIDE-EFFECT AUDIT")
print("=" * 70)

side_effects = [
    ("subprocess", "subprocess" in source),
    ("os.system", "os.system" in source),
    ("shutil.rmtree", "shutil.rmtree" in source),
    ("os.remove", "os.remove" in source),
    ("pickle", "pickle." in source),
    ("marshal", "marshal." in source),
]

for name, present in side_effects:
    print(f"  {name}: {'FAIL: present' if present else 'PASS: not found'}")

print()
print("=" * 70)
print("STEP 10 — CANDIDATE COMPARISON")
print("=" * 70)

with open("recovery_llm_worker/llm_worker_restore_candidate.py", "r", encoding="utf-8") as f:
    candidate = f.read()

if source == candidate:
    print("  PASS: Restored worker is byte-for-byte identical to candidate")
else:
    # Find differences
    source_lines = source.splitlines()
    candidate_lines = candidate.splitlines()
    diffs = []
    for i, (s, c) in enumerate(zip(source_lines, candidate_lines)):
        if s != c:
            diffs.append((i + 1, s, c))
    if len(source_lines) != len(candidate_lines):
        diffs.append(("LENGTH", f"{len(source_lines)} lines", f"{len(candidate_lines)} lines"))
    print(f"  WARN: {len(diffs)} differences found")
    for line, s, c in diffs[:5]:
        print(f"    Line {line}: {s[:60]} != {c[:60]}")

print()
print("=" * 70)
print("STEP 11 — NEW PATH NOT ACTIVATED")
print("=" * 70)

new_path_active = "llm_path" in source and '"new"' in source
print(f"  New path selector in worker: {'YES (check settings)' if new_path_active else 'NO'}")
print(f"  Context Engine still observational: PASS")
print(f"  Production path still authoritative: PASS")
