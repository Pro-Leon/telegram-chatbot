import subprocess

# Get all unreachable blobs
result = subprocess.run(
    ['git', 'fsck', '--unreachable', '--no-reflogs', '--no-progress'],
    capture_output=True, text=True, encoding='utf-8', errors='replace'
)

blobs = []
for line in result.stdout.splitlines():
    if 'unreachable blob' in line:
        sha = line.split()[-1]
        blobs.append(sha)

print(f'Found {len(blobs)} unreachable blobs')

# Search for worker-related blobs
worker_blobs = []
search_strings = [
    b'workers/llm_worker.py',
    b'build_qwen3_context',
    b'extract_commerce_signals',
    b'generate_draft_with_tools',
    b'publish_events_batch',
    b'observe_canary',
    b'process_message',
    b'_try_commerce_draft',
]

for sha in blobs:
    try:
        result = subprocess.run(
            ['git', 'cat-file', '-p', sha],
            capture_output=True, timeout=5
        )
        content = result.stdout
        if not content:
            continue

        matches = []
        for s in search_strings:
            if s in content:
                matches.append(s.decode('utf-8', errors='replace'))

        if matches:
            try:
                text = content.decode('utf-8')
                lines = text.splitlines()
                worker_blobs.append({
                    'sha': sha,
                    'size': len(content),
                    'lines': len(lines),
                    'matches': matches,
                    'is_text': True,
                })
            except:
                worker_blobs.append({
                    'sha': sha,
                    'size': len(content),
                    'lines': 0,
                    'matches': matches,
                    'is_text': False,
                })
    except:
        pass

print(f'Found {len(worker_blobs)} worker-related blobs')
for b in worker_blobs:
    print(f"  {b['sha'][:12]} size={b['size']} lines={b['lines']} matches={b['matches']}")
