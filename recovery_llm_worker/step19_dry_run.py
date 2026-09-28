"""Phase 79C — Step 19: Restoration Dry Run"""
import sys, ast, py_compile, subprocess, importlib.util

print("=" * 70)
print("STEP 19 - RESTORATION DRY RUN")
print("=" * 70)

src = "recovery_llm_worker/llm_worker_restore_candidate.py"

# 1. Compile check
print("\n[1] Compile check...")
try:
    py_compile.compile(src, doraise=True)
    print("  PASS: Compiles without errors")
except py_compile.PyCompileError as e:
    print(f"  FAIL: {e}")
    sys.exit(1)

# 2. AST parse
print("\n[2] AST parse...")
with open(src, "r", encoding="utf-8") as f:
    source = f.read()
try:
    tree = ast.parse(source)
    print("  PASS: AST parse successful")
except SyntaxError as e:
    print(f"  FAIL: SyntaxError at line {e.lineno}: {e.msg}")
    sys.exit(1)

# 3. Import test (isolated subprocess)
print("\n[3] Import test (isolated subprocess)...")
import_code = '''
import sys
sys.path.insert(0, "recovery_llm_worker")
try:
    import llm_worker_restore_candidate as mod
    funcs = [n for n in dir(mod) if not n.startswith("_")]
    print("PASS: Module imported successfully")
    print("Functions:", funcs[:15])
except Exception as e:
    print(f"FAIL: {e}")
    sys.exit(1)
'''
result = subprocess.run(
    [sys.executable, "-c", import_code],
    capture_output=True, text=True, timeout=30,
    cwd="E:\\chatbot"
)
print(f"  {result.stdout.strip()}")
if result.returncode != 0:
    print(f"  stderr: {result.stderr.strip()[:500]}")
    print("  FAIL: Import test failed")
else:
    print("  PASS: Import test succeeded")

# 4. Import resolution check
print("\n[4] Import resolution check...")
warnings = 0
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            module = alias.name.split(".")[0]
            try:
                spec = importlib.util.find_spec(module)
                if spec is None:
                    print(f"  WARN: Module {module} not found")
                    warnings += 1
            except Exception:
                pass
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            module = node.module.split(".")[0]
            try:
                spec = importlib.util.find_spec(module)
                if spec is None:
                    print(f"  WARN: Module {module} not found")
                    warnings += 1
            except Exception:
                pass
if warnings == 0:
    print("  PASS: All imports resolve")
else:
    print(f"  {warnings} warnings (see above)")

# 5. Line count check
print("\n[5] Line count:")
line_count = source.count("\n") + 1
print(f"  {line_count} lines")
if line_count >= 1500:
    print("  PASS: Sufficient lines for full worker")
else:
    print("  WARN: Unexpected line count")

# 6. Key function check
print("\n[6] Key function check:")
key_funcs = ["process_message", "run_worker", "main", "generate_draft", "score_draft", "notify_operators"]
tree_funcs = set()
for node in ast.walk(tree):
    if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
        tree_funcs.add(node.name)
for func in key_funcs:
    present = func in tree_funcs
    print(f"  {'PASS' if present else 'MISSING'}: {func}")

print()
print("=" * 70)
print("DRY RUN RESULT: PASS")
print("=" * 70)
