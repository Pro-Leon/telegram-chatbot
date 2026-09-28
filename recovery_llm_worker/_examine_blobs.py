import subprocess

# The most promising blobs
blobs = [
    ('cb510837a84a', 'largest, 673 lines, 5 matches'),
    ('61cd426b73c3', '666 lines, 3 matches'),
    ('c69b71bb6725', '663 lines, 3 matches'),
    ('91cae05177d6', '505 lines, 2 matches'),
    ('bc4f0a856c7f', 'known: build_qwen3_context'),
    ('1d434f730eb1', 'binary? 8934 bytes'),
    ('e90e9dfa50b0', 'small, workers/llm_worker.py reference'),
]

for sha, desc in blobs:
    full_sha = subprocess.run(
        ['git', 'fsck', '--unreachable', '--no-reflogs', '--no-progress'],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    # Find full SHA
    result = subprocess.run(
        ['git', 'cat-file', '-p', sha],
        capture_output=True, timeout=5
    )
    content = result.stdout
    
    print(f'\n{"="*80}')
    print(f'BLOB: {sha} ({desc})')
    print(f'Size: {len(content)} bytes')
    
    try:
        text = content.decode('utf-8')
        lines = text.splitlines()
        print(f'Lines: {len(lines)}')
        
        # Show first 30 lines
        print('First 30 lines:')
        for i, line in enumerate(lines[:30]):
            print(f'  {i+1}: {line}')
        
        # Check for key symbols
        for sym in ['def process_message', 'def generate_draft', 'def _try_commerce_draft',
                     'def run_worker', 'def _worker_cleanup', 'def main',
                     'build_qwen3_context', 'extract_commerce_signals',
                     'observe_canary', 'context_engine', 'telemetry',
                     'publish_event', 'score_draft', 'agent']:
            if sym in text:
                # Find line number
                for i, line in enumerate(lines):
                    if sym in line:
                        print(f'  FOUND: {sym} at line {i+1}: {line.strip()[:100]}')
                        break
    except:
        print('Binary content, skipping text analysis')
