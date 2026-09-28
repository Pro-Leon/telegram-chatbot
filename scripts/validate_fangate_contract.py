#!/usr/bin/env python3
"""Fangate contract conformance validator.

Usage:
    python scripts/validate_fangate_contract.py [--json] [--strict]

Exit codes:
    0 — All checks passed (informational findings are OK)
    1 — Hard failure (contract violation detected)
    2 — Contract file error (missing, malformed, hash mismatch)
"""

from __future__ import annotations

import argparse
import json
import sys
import pathlib

# Ensure project root is on path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from integrations.fangate.contract import (
    Classification,
    compare_endpoints,
    compare_schemas,
    compute_sha256,
    extract_operations,
    generate_conformance_report,
    generate_markdown_report,
    get_repo_endpoints,
    load_metadata,
    load_openapi,
    verify_security,
)

_EXIT_OK = 0
_EXIT_HARD_FAILURE = 1
_EXIT_CONTRACT_ERROR = 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Fangate contract conformance")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of markdown")
    parser.add_argument("--strict", action="store_true", help="Treat PARTIAL as hard failures")
    parser.add_argument("--output", type=str, help="Write report to file instead of stdout")
    args = parser.parse_args()

    errors: list[str] = []

    # Step 1: Load and validate OpenAPI
    try:
        spec = load_openapi()
    except Exception as exc:
        print(f"ERROR: Cannot load OpenAPI spec: {exc}", file=sys.stderr)
        return _EXIT_CONTRACT_ERROR

    # Step 2: Load metadata
    try:
        metadata = load_metadata()
    except Exception as exc:
        print(f"ERROR: Cannot load metadata: {exc}", file=sys.stderr)
        return _EXIT_CONTRACT_ERROR

    # Step 3: Verify SHA-256
    actual_sha = compute_sha256()
    expected_sha = metadata.get("sha256", "")
    if actual_sha != expected_sha:
        errors.append(
            f"CONTRACT_HASH_MISMATCH: local={actual_sha} metadata={expected_sha}"
        )

    # Step 4: Extract operations
    operations = extract_operations(spec)
    print(f"Loaded {len(operations)} operations from OpenAPI spec", file=sys.stderr)

    # Step 5: Get repo endpoints
    repo_endpoints = get_repo_endpoints()
    print(f"Loaded {len(repo_endpoints)} repository endpoints", file=sys.stderr)

    # Step 6: Compare
    results = compare_endpoints(operations, repo_endpoints)

    # Step 7: Schema comparison
    schema_diffs = compare_schemas(spec)

    # Step 8: Security verification
    security_info = verify_security(spec)

    # Step 9: Generate report
    report = generate_conformance_report(results, schema_diffs, security_info, spec, metadata)

    # Step 10: Check for hard failures
    for op in report["operations"]:
        if op["classification"] == "PARTIAL" and args.strict:
            for d in op["differences"]:
                if d["field"] in ("method_mismatch", "path_mismatch", "content_type_mismatch"):
                    errors.append(
                        f"CONTRACT_VIOLATION: {op['method']} {op['path']} — {d['description']}"
                    )

    # Output
    if args.json:
        output = json.dumps(report, indent=2, ensure_ascii=False)
    else:
        output = generate_markdown_report(report)

    if args.output:
        pathlib.Path(args.output).write_text(output, encoding="utf-8")
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(output)

    # Print summary to stderr
    s = report["summary"]
    print(
        f"\nSummary: {s['confirmed']} confirmed, {s['partial']} partial, "
        f"{s['unverified']} unverified, {s['missing_from_repo']} missing",
        file=sys.stderr,
    )

    if errors:
        print("\nHARD FAILURES:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return _EXIT_HARD_FAILURE

    if actual_sha != expected_sha:
        print("\nCONTRACT ERROR: SHA-256 hash mismatch", file=sys.stderr)
        return _EXIT_CONTRACT_ERROR

    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
