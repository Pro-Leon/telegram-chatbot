import subprocess

# Extract the largest blob
sha = 'cb510837a84a'
result = subprocess.run(
    ['git', 'cat-file', '-p', sha],
    capture_output=True, timeout=5
)
content = result.stdout.decode('utf-8', errors='replace')

with open('recovery_llm_worker/llm_worker_cb510837a84a.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f'Extracted {sha}: {len(content)} bytes, {len(content.splitlines())} lines')

# Also extract the other promising blobs
for s, name in [
    ('61cd426b73c3', '61cd426b73c3'),
    ('c69b71bb6725', 'c69b71bb6725'),
    ('91cae05177d6', '91cae05177d6'),
]:
    result = subprocess.run(
        ['git', 'cat-file', '-p', s],
        capture_output=True, timeout=5
    )
    content = result.stdout.decode('utf-8', errors='replace')
    with open(f'recovery_llm_worker/llm_worker_{name}.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'Extracted {s}: {len(content)} bytes, {len(content.splitlines())} lines')
