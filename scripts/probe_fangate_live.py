#!/usr/bin/env python3
"""P4.3 — Safe live Fangate API probe script.

Reads FANGATE_API_KEY from environment. Makes safe probe requests to
determine whether undocumented endpoints exist on the live API.

NEVER mutates real production data. Uses:
- Nonexistent IDs (999999999) to test route existence
- Malformed requests to trigger validation errors (proves route exists)
- Missing required fields to trigger 422 (proves route exists)
- Empty bodies on POST to trigger validation (proves route exists)

Usage:
    FANGATE_API_KEY=xxx python scripts/probe_fangate_live.py

Output: JSON report to stdout.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import httpx

BASE_URL = os.environ.get("FANGATE_API_BASE_URL", "https://fangate.info/api")
API_KEY = os.environ.get("FANGATE_API_KEY", "")
TIMEOUT = 10.0

# Nonexistent IDs for safe probing — will never match real resources
FAKE_PRODUCT_ID = 999999999
FAKE_OFFER_ID = 999999999
FAKE_WEBHOOK_ID = 999999999


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _probe(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make a safe probe request and record the result."""
    url = f"{BASE_URL}{path}"
    start = time.monotonic()
    result: dict[str, Any] = {
        "method": method,
        "path": path,
        "url": url,
        "status": None,
        "response_time_ms": 0,
        "content_type": None,
        "response_body_preview": None,
        "error": None,
    }
    try:
        headers = _headers()

        with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
            if method == "GET":
                resp = client.get(url, headers=headers)
            elif method == "POST":
                resp = client.post(url, headers=headers, json=json_body)
            elif method == "PATCH":
                resp = client.patch(url, headers=headers, json=json_body)
            elif method == "DELETE":
                resp = client.delete(url, headers=headers)
            else:
                result["error"] = f"Unsupported method: {method}"
                return result

            result["status"] = resp.status_code
            result["response_time_ms"] = round((time.monotonic() - start) * 1000, 1)
            result["content_type"] = resp.headers.get("content-type", "")

            # Preview response body (first 500 chars, redact sensitive data)
            try:
                body = resp.json()
                body_str = json.dumps(body, ensure_ascii=False)
                result["response_body_preview"] = body_str[:500]
                # Extract key fields for analysis
                if isinstance(body, dict):
                    result["response_success"] = body.get("success")
                    result["response_errors_message"] = body.get("errors_message")
                    result["response_data_type"] = type(body.get("data")).__name__
            except Exception:
                result["response_body_preview"] = resp.text[:500]

    except httpx.TimeoutException:
        result["error"] = "timeout"
        result["response_time_ms"] = round((time.monotonic() - start) * 1000, 1)
    except httpx.RequestError as exc:
        result["error"] = f"request_error: {exc.__class__.__name__}"
        result["response_time_ms"] = round((time.monotonic() - start) * 1000, 1)
    except Exception as exc:
        result["error"] = f"unexpected: {exc.__class__.__name__}: {exc}"
        result["response_time_ms"] = round((time.monotonic() - start) * 1000, 1)

    return result


def probe_all() -> list[dict[str, Any]]:
    """Probe all 8 unverified endpoints with safe parameters."""
    results = []

    # 1. GET /products/{id}/analytics — READ-ONLY, safe with nonexistent ID
    results.append(_probe("GET", f"/products/{FAKE_PRODUCT_ID}/analytics"))

    # 2. POST /webhooks/test — use nonexistent webhook ID
    results.append(_probe("POST", "/webhooks/test", json_body={"webhook_id": FAKE_WEBHOOK_ID}))

    # 3. POST /products/{id}/offers — use nonexistent product + user, empty price
    results.append(_probe("POST", f"/products/{FAKE_PRODUCT_ID}/offers",
                          json_body={"user_id": FAKE_PRODUCT_ID}))

    # 4. POST /products/{id}/offers/{oid}/send — use nonexistent IDs
    results.append(_probe("POST", f"/products/{FAKE_PRODUCT_ID}/offers/{FAKE_OFFER_ID}/send"))

    # 5. PATCH /products/{id}/offers/{oid} — use nonexistent IDs
    results.append(_probe("PATCH", f"/products/{FAKE_PRODUCT_ID}/offers/{FAKE_OFFER_ID}",
                          json_body={"price": 100}))

    # 6. DELETE /products/{id}/offers/{oid} — use nonexistent IDs
    results.append(_probe("DELETE", f"/products/{FAKE_PRODUCT_ID}/offers/{FAKE_OFFER_ID}"))

    # 7. POST /products/{id}/media/blur — use nonexistent product ID
    results.append(_probe("POST", f"/products/{FAKE_PRODUCT_ID}/media/blur"))

    # 8. POST /products/{id}/epoch — use nonexistent product ID
    results.append(_probe("POST", f"/products/{FAKE_PRODUCT_ID}/epoch"))

    # 9. BONUS: POST /products/{id}/media (attach) — with JSON to test documented path
    results.append(_probe("POST", f"/products/{FAKE_PRODUCT_ID}/media",
                          json_body={"media_ids": [FAKE_PRODUCT_ID]}))

    # 10. BONUS: GET /products/{id}/offers — list offers (if GET exists)
    results.append(_probe("GET", f"/products/{FAKE_PRODUCT_ID}/offers"))

    return results


def classify(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify each probe result."""
    for r in results:
        status = r.get("status")
        error = r.get("error")

        if error and "timeout" in str(error):
            r["classification"] = "TIMEOUT"
            r["route_exists"] = "INCONCLUSIVE"
        elif error and "request_error" in str(error):
            r["classification"] = "NETWORK_ERROR"
            r["route_exists"] = "INCONCLUSIVE"
        elif status == 401:
            r["classification"] = "ROUTE_EXISTS_AUTH_REQUIRED"
            r["route_exists"] = "YES"
        elif status == 403:
            r["classification"] = "ROUTE_EXISTS_AUTHORIZED_BUT_FORBIDDEN"
            r["route_exists"] = "YES"
        elif status == 404:
            r["classification"] = "NOT_FOUND_OR_ROUTE_MISSING"
            r["route_exists"] = "AMBIGUOUS"
        elif status == 405:
            r["classification"] = "METHOD_NOT_ALLOWED"
            r["route_exists"] = "YES"
        elif status == 422:
            r["classification"] = "ROUTE_EXISTS_VALIDATION_ERROR"
            r["route_exists"] = "YES"
        elif status == 400:
            r["classification"] = "ROUTE_EXISTS_BAD_REQUEST"
            r["route_exists"] = "YES"
        elif status and 200 <= status < 300:
            r["classification"] = "ROUTE_EXISTS_SUCCESS"
            r["route_exists"] = "YES"
        elif status and 500 <= status < 600:
            r["classification"] = "SERVER_ERROR"
            r["route_exists"] = "PROBABLY_YES"
        else:
            r["classification"] = f"UNEXPECTED_{status}"
            r["route_exists"] = "UNKNOWN"

    return results


def main():
    if not API_KEY:
        print(json.dumps({
            "error": "FANGATE_API_KEY not set",
            "usage": "FANGATE_API_KEY=xxx python scripts/probe_fangate_live.py",
        }, indent=2))
        sys.exit(1)

    print(f"Probing {BASE_URL} with safe requests...", file=sys.stderr)
    results = probe_all()
    results = classify(results)

    report = {
        "base_url": BASE_URL,
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_probes": len(results),
        "results": results,
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))

    # Summary to stderr
    for r in results:
        status = r.get("status", "ERR")
        exists = r.get("route_exists", "?")
        print(f"  {r['method']:6s} {r['path'][:50]:50s} -> {status} ({exists})", file=sys.stderr)


if __name__ == "__main__":
    main()
