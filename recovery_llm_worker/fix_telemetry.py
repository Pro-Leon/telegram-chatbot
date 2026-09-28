"""Phase 79D — Fix telemetry API calls in restored worker"""
import re

src_path = "workers/llm_worker.py"
with open(src_path, "r", encoding="utf-8") as f:
    source = f.read()

# Find all _telemetry.record("key", value) patterns
# and convert to _telemetry_data.key = value pattern
pattern = r'_telemetry\.record\("([^"]+)",\s*([^)]+)\)'
matches = re.findall(pattern, source)

print(f"Found {len(matches)} _telemetry.record() calls to fix")

# Replace with attribute assignment
def replace_record(match):
    key = match.group(1)
    value = match.group(2).strip()
    return f"_telemetry_data.{key} = {value}"

new_source = re.sub(pattern, replace_record, source)

# Count replacements
new_matches = re.findall(r'_telemetry\.record\(', new_source)
print(f"Remaining _telemetry.record() calls: {len(new_matches)}")

# Write back
with open(src_path, "w", encoding="utf-8") as f:
    f.write(new_source)

print(f"Fixed {len(matches)} telemetry calls")
print("Written to workers/llm_worker.py")
