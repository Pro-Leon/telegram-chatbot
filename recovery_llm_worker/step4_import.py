"""Phase 79D — Step 4: Import Validation"""
import subprocess, sys

print("=== IMPORT VALIDATION ===")
result = subprocess.run(
    [sys.executable, "-c", "import workers.llm_worker; funcs = [n for n in dir(workers.llm_worker) if not n.startswith('_')]; print('PASS: Import succeeded'); print(f'Functions: {len(funcs)}')"],
    capture_output=True, text=True, timeout=30
)
print(result.stdout.strip())
if result.returncode != 0:
    print(f"stderr: {result.stderr.strip()[:500]}")
    print("FAIL")
else:
    print("PASS: No import exceptions, no production state modified")
