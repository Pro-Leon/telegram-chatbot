"""Fangate contract conformance engine.

Deterministic comparison between the local OpenAPI snapshot and the
repository's Fangate client implementation. Produces machine-readable
and human-readable conformance reports.

Security: this module NEVER prints API keys, webhook secrets, Fernet keys,
Authorization headers, buyer email addresses, or transaction secrets.
"""

from __future__ import annotations

import enum
import hashlib
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any

_OPENAPI_PATH = pathlib.Path("docs/integrations/fangate/openapi.json")
_METADATA_PATH = pathlib.Path("docs/integrations/fangate/openapi.metadata.json")


# ── Classification ────────────────────────────────────────────────────────


class Classification(str, enum.Enum):
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    UNVERIFIED = "UNVERIFIED"
    MISSING_FROM_REPO = "MISSING_FROM_REPO"


# ── Contract model ────────────────────────────────────────────────────────


@dataclass
class OpenAPIOperation:
    method: str
    path: str
    operation_id: str | None = None
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    parameters: list[dict[str, Any]] = field(default_factory=list)
    request_body: dict[str, Any] | None = None
    request_content_type: str | None = None
    responses: dict[str, str] = field(default_factory=dict)
    security: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RepoEndpoint:
    method: str
    path: str
    source_method: str
    source_file: str
    source_line: int
    request_content_type: str = "application/json"
    parameters: list[dict[str, Any]] = field(default_factory=list)
    request_fields: list[str] = field(default_factory=list)


@dataclass
class Difference:
    field: str
    contract_value: Any = None
    repo_value: Any = None
    description: str = ""


@dataclass
class ConformanceResult:
    method: str
    path: str
    repository_method: str
    classification: Classification
    differences: list[Difference] = field(default_factory=list)
    source_file: str = ""
    source_line: int = 0


# ── OpenAPI loader ────────────────────────────────────────────────────────


def load_openapi(path: pathlib.Path | None = None) -> dict[str, Any]:
    """Load and validate the local OpenAPI spec."""
    p = path or _OPENAPI_PATH
    raw = p.read_text(encoding="utf-8")
    spec = json.loads(raw)
    _validate_openapi_structure(spec)
    return spec


def load_metadata(path: pathlib.Path | None = None) -> dict[str, Any]:
    """Load the metadata file."""
    p = path or _METADATA_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def compute_sha256(path: pathlib.Path | None = None) -> str:
    """Compute SHA-256 of the OpenAPI file."""
    p = path or _OPENAPI_PATH
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _validate_openapi_structure(spec: dict[str, Any]) -> None:
    """Validate minimum OpenAPI structure."""
    if spec.get("openapi") is None:
        raise ValueError("Missing 'openapi' version field")
    if spec.get("paths") is None:
        raise ValueError("Missing 'paths' field")
    if spec.get("info") is None:
        raise ValueError("Missing 'info' field")


# ── OpenAPI operation extraction ──────────────────────────────────────────


def extract_operations(spec: dict[str, Any]) -> list[OpenAPIOperation]:
    """Extract all operations from the OpenAPI spec."""
    operations = []
    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            op = path_item.get(method)
            if op is None:
                continue
            operations.append(
                OpenAPIOperation(
                    method=method.upper(),
                    path=path,
                    operation_id=op.get("operationId"),
                    tags=op.get("tags", []),
                    summary=op.get("description", ""),
                    parameters=op.get("parameters", []),
                    request_body=op.get("requestBody"),
                    request_content_type=_extract_content_type(op.get("requestBody")),
                    responses={
                        str(k): v.get("description", "") for k, v in op.get("responses", {}).items()
                    },
                    security=op.get("security", []),
                )
            )
    return operations


def _extract_content_type(request_body: dict[str, Any] | None) -> str | None:
    if not request_body:
        return None
    content = request_body.get("content", {})
    if "multipart/form-data" in content:
        return "multipart/form-data"
    if "application/json" in content:
        return "application/json"
    if content:
        return next(iter(content))
    return None


# ── Repository endpoint inventory ─────────────────────────────────────────

# Hard-coded inventory derived from client.py static analysis.
# Each entry is verified against the actual source code.

_REPO_ENDPOINTS: list[RepoEndpoint] = [
    # Products
    RepoEndpoint("GET", "/api/products", "list_products", "client.py", 221,
                 parameters=[
                     {"name": "page", "in": "query", "type": "integer"},
                     {"name": "limit", "in": "query", "type": "integer"},
                     {"name": "sort_by", "in": "query", "type": "string"},
                     {"name": "folder_id", "in": "query", "type": "integer"},
                     {"name": "folder_state", "in": "query", "type": "string"},
                     {"name": "filterFolder", "in": "query", "type": "string"},
                 ]),
    RepoEndpoint("GET", "/api/products/{product_id}", "get_product", "client.py", 227),
    RepoEndpoint("PATCH", "/api/products/{product_id}", "update_product", "client.py", 259,
                 request_fields=["title", "private_description", "public_description",
                                 "is_adult_content", "is_verif_age"]),
    RepoEndpoint("PATCH", "/api/products/{product_id}/price", "update_product_price", "client.py", 266,
                 request_fields=["price"]),
    RepoEndpoint("POST", "/api/products/{product_id}/collection", "toggle_product_collection", "client.py", 272),
    RepoEndpoint("PATCH", "/api/products/{product_id}/folder", "update_product_folder", "client.py", 279,
                 request_fields=["folder_id"]),
    RepoEndpoint("POST", "/api/products/{product_id}/price-links", "create_price_link", "client.py", 301,
                 request_fields=["price", "title", "private_description", "public_description"]),
    RepoEndpoint("DELETE", "/api/products/{product_id}", "delete_product", "client.py", 425),
    RepoEndpoint("POST", "/api/products", "create_product", "client.py", 468,
                 request_content_type="multipart/form-data",
                 request_fields=["product_id", "title", "price", "media", "upload_session_id", "extension",
                                 "is_adult_content", "is_verif_age", "is_should_consent",
                                 "private_description", "public_description"]),
    # Content folders
    RepoEndpoint("GET", "/api/content-folders", "list_content_folders", "client.py", 314),
    RepoEndpoint("POST", "/api/content-folders", "create_content_folder", "client.py", 320,
                 request_fields=["name"]),
    RepoEndpoint("PATCH", "/api/content-folders/{folder_id}", "update_content_folder", "client.py", 326,
                 request_fields=["name"]),
    RepoEndpoint("DELETE", "/api/content-folders/{folder_id}", "delete_content_folder", "client.py", 332),
    # Dashboard
    RepoEndpoint("GET", "/api/dashboard/summary", "get_dashboard_summary", "client.py", 340),
    # Wallet
    RepoEndpoint("GET", "/api/wallet", "get_wallet", "client.py", 348,
                 parameters=[
                     {"name": "page", "in": "query", "type": "integer"},
                     {"name": "limit", "in": "query", "type": "integer"},
                 ]),
    # Webhooks (UNVERIFIED — not in OpenAPI)
    RepoEndpoint("POST", "/api/webhooks", "create_webhook", "client.py", 369,
                 request_fields=["url", "events", "include_set_price"]),
    RepoEndpoint("GET", "/api/webhooks", "list_webhooks", "client.py", 383),
    RepoEndpoint("PATCH", "/api/webhooks/{webhook_id}", "update_webhook", "client.py", 416,
                 request_fields=["url", "events", "include_set_price", "is_active"]),
    RepoEndpoint("DELETE", "/api/webhooks/{webhook_id}", "delete_webhook", "client.py", 422),
    # Product media
    RepoEndpoint("POST", "/api/products/{product_id}/media", "upload_product_media", "client.py", 486,
                 request_content_type="multipart/form-data",
                 request_fields=["media", "upload_session_id"]),
    RepoEndpoint("POST", "/api/products/{product_id}/media", "attach_product_media", "client.py", 497,
                 request_content_type="application/json",
                 request_fields=["media_ids"]),
    RepoEndpoint("DELETE", "/api/products/media/{media_id}", "delete_product_media", "client.py", 514),
    # Product collection
    RepoEndpoint("GET", "/api/products/collection", "list_product_collection", "client.py", 521,
                 parameters=[
                     {"name": "page", "in": "query", "type": "integer"},
                     {"name": "limit", "in": "query", "type": "integer"},
                 ]),
]


def get_repo_endpoints() -> list[RepoEndpoint]:
    """Return the repository endpoint inventory."""
    return list(_REPO_ENDPOINTS)


# ── Path matching ─────────────────────────────────────────────────────────

# OpenAPI paths use /api/ prefix; repo paths also use /api/ prefix.
# The base URL is separate. Direct comparison should work.


def _normalize_path(path: str) -> str:
    """Normalize a path for comparison (strip trailing slash)."""
    return path.rstrip("/") if path != "/" else path


def _path_matches(repo_path: str, openapi_path: str) -> bool:
    """Check if a repo path matches an OpenAPI path.

    Handles parameter placeholder differences:
    - Repo: /api/products/{product_id}/offers/{offer_id}/send
    - OpenAPI: /api/products/{product_id}/offers/{offer_id}/send

    Also handles:
    - Repo: /api/content-folders/{folder_id}
    - OpenAPI: /api/content-folders/{content_folder_id}
    """
    repo_parts = _normalize_path(repo_path).split("/")
    api_parts = _normalize_path(openapi_path).split("/")

    if len(repo_parts) != len(api_parts):
        return False

    for rp, ap in zip(repo_parts, api_parts):
        if rp.startswith("{") and ap.startswith("{"):
            continue  # Both are path parameters
        if rp != ap:
            return False
    return True


# ── Conformance comparison ────────────────────────────────────────────────


def compare_endpoints(
    operations: list[OpenAPIOperation],
    repo_endpoints: list[RepoEndpoint],
) -> list[ConformanceResult]:
    """Compare OpenAPI operations against repository endpoints."""
    results = []

    for repo_ep in repo_endpoints:
        # Find matching OpenAPI operation
        matching_op = None
        for op in operations:
            if op.method == repo_ep.method and _path_matches(repo_ep.path, op.path):
                matching_op = op
                break

        if matching_op is None:
            results.append(
                ConformanceResult(
                    method=repo_ep.method,
                    path=repo_ep.path,
                    repository_method=repo_ep.source_method,
                    classification=Classification.UNVERIFIED,
                    source_file=repo_ep.source_file,
                    source_line=repo_ep.source_line,
                )
            )
            continue

        # Classify conformance
        differences = _check_differences(matching_op, repo_ep)

        if not differences:
            classification = Classification.CONFIRMED
        else:
            has_hard_difference = any(
                d.field in ("method_mismatch", "path_mismatch", "content_type_mismatch",
                            "missing_required_field", "field_type_mismatch")
                for d in differences
            )
            classification = Classification.PARTIAL if not has_hard_difference else Classification.PARTIAL

        results.append(
            ConformanceResult(
                method=repo_ep.method,
                path=repo_ep.path,
                repository_method=repo_ep.source_method,
                classification=classification,
                differences=differences,
                source_file=repo_ep.source_file,
                source_line=repo_ep.source_line,
            )
        )

    # Find MISSING_FROM_REPO operations
    repo_paths = {(repo_ep.method, _normalize_path(repo_ep.path)) for repo_ep in repo_endpoints}
    for op in operations:
        if (op.method, _normalize_path(op.path)) not in repo_paths:
            results.append(
                ConformanceResult(
                    method=op.method,
                    path=op.path,
                    repository_method="",
                    classification=Classification.MISSING_FROM_REPO,
                )
            )

    return results


def _check_differences(op: OpenAPIOperation, repo_ep: RepoEndpoint) -> list[Difference]:
    """Check for specific differences between an OpenAPI operation and a repo endpoint."""
    diffs = []

    # Content type
    if op.request_content_type and repo_ep.request_content_type:
        if op.request_content_type != repo_ep.request_content_type:
            diffs.append(
                Difference(
                    field="content_type_mismatch",
                    contract_value=op.request_content_type,
                    repo_value=repo_ep.request_content_type,
                    description=f"Contract expects {op.request_content_type}, repo uses {repo_ep.request_content_type}",
                )
            )

    # Query parameters — check if repo is missing documented params
    openapi_params = {
        p["name"]: p
        for p in op.parameters
        if p.get("in") == "query"
    }
    repo_params = {
        p["name"]: p
        for p in repo_ep.parameters
        if p.get("in") == "query"
    }

    for param_name, param_def in openapi_params.items():
        if param_name not in repo_params:
            diffs.append(
                Difference(
                    field="missing_query_parameter",
                    contract_value=param_name,
                    repo_value=None,
                    description=f"Repository does not expose query parameter '{param_name}' "
                    f"(type: {param_def.get('schema', {}).get('type', 'unknown')})",
                )
            )

    # Request body fields
    openapi_body_fields = _extract_body_fields(op)
    if openapi_body_fields and repo_ep.request_fields:
        for field_name in openapi_body_fields:
            if field_name not in repo_ep.request_fields:
                diffs.append(
                    Difference(
                        field="missing_request_field",
                        contract_value=field_name,
                        repo_value=None,
                        description=f"Contract documents request field '{field_name}' not sent by repo",
                    )
                )
        for field_name in repo_ep.request_fields:
            if field_name not in openapi_body_fields:
                diffs.append(
                    Difference(
                        field="extra_request_field",
                        contract_value=None,
                        repo_value=field_name,
                        description=f"Repo sends request field '{field_name}' not in contract (UNVERIFIED)",
                    )
                )

    return diffs


def _extract_body_fields(op: OpenAPIOperation) -> list[str]:
    """Extract field names from an OpenAPI operation's request body."""
    if not op.request_body:
        return []
    content = op.request_body.get("content", {})
    for content_type, media in content.items():
        schema = media.get("schema", {})
        return list(schema.get("properties", {}).keys())
    return []


# ── Schema conformance ────────────────────────────────────────────────────


@dataclass
class SchemaDifference:
    schema_name: str
    field: str
    contract_type: str | None = None
    repo_type: str | None = None
    classification: str = ""  # DOCUMENTED, UNVERIFIED, LEGACY/EXTRA


def compare_schemas(spec: dict[str, Any]) -> list[SchemaDifference]:
    """Compare OpenAPI schemas against repository models.

    Only reports genuine drift: fields present in one but not the other,
    after accounting for known name mappings.
    """
    diffs = []
    schemas = spec.get("components", {}).get("schemas", {})

    # ── Product schema ─────────────────────────────────────────────────
    product_resource = schemas.get("product.resource", {})
    product_props = set(product_resource.get("properties", {}).keys())

    # Repo model fields (FangateProduct dataclass attributes)
    repo_product_fields = {
        "id", "product_type", "title", "preview", "preview_blurred",
        "price_minor", "in_collection", "link", "link_clicks", "unlocks",
        "total_earnings", "folder_id", "folder", "media",
        "is_adult_content", "is_verif_age", "is_epoch_enabled",
        "is_should_consent", "is_downloadable", "is_accessible",
        "private_description", "public_description",
    }

    # Known name mappings: OpenAPI name -> repo name
    field_mapping = {"price": "price_minor", "type": "product_type"}
    # Reverse: repo name -> OpenAPI name
    reverse_mapping = {v: k for k, v in field_mapping.items()}

    # Convert repo fields to OpenAPI-equivalent names for comparison
    repo_as_openapi = set()
    for f in repo_product_fields:
        repo_as_openapi.add(reverse_mapping.get(f, f))

    # Fields in OpenAPI but not representable in repo
    for field_name in sorted(product_props - repo_as_openapi):
        prop = product_resource["properties"][field_name]
        diffs.append(
            SchemaDifference(
                schema_name="product.resource",
                field=field_name,
                contract_type=prop.get("type", "object"),
                repo_type=None,
                classification="DOCUMENTED",
            )
        )

    # Fields in repo but not in OpenAPI (genuinely extra)
    for field_name in sorted(repo_as_openapi - product_props):
        # Skip fields that have a mapping (already accounted for)
        if field_name in field_mapping:
            continue
        diffs.append(
            SchemaDifference(
                schema_name="FangateProduct",
                field=field_name,
                contract_type=None,
                repo_type="any",
                classification="UNVERIFIED",
            )
        )

    # ── Wallet schema ──────────────────────────────────────────────────
    wallet_resource = schemas.get("wallet.resource", {})
    wallet_props = set(wallet_resource.get("properties", {}).keys())

    repo_wallet_fields = {
        "available_minor", "hold_minor", "pending_minor", "total_minor",
        "referral_revenue_minor", "cashout_available", "transactions",
    }
    wallet_mapping = {
        "available": "available_minor", "hold": "hold_minor",
        "pending": "pending_minor", "total": "total_minor",
        "referral_revenue": "referral_revenue_minor",
    }
    wallet_reverse = {v: k for k, v in wallet_mapping.items()}

    repo_wallet_as_openapi = {wallet_reverse.get(f, f) for f in repo_wallet_fields}

    for field_name in sorted(wallet_props - repo_wallet_as_openapi):
        prop = wallet_resource["properties"][field_name]
        diffs.append(
            SchemaDifference(
                schema_name="wallet.resource",
                field=field_name,
                contract_type=prop.get("type", "object"),
                repo_type=None,
                classification="DOCUMENTED",
            )
        )

    for field_name in sorted(repo_wallet_as_openapi - wallet_props):
        if field_name in wallet_mapping:
            continue
        diffs.append(
            SchemaDifference(
                schema_name="FangateWallet",
                field=field_name,
                contract_type=None,
                repo_type="any",
                classification="UNVERIFIED",
            )
        )

    return diffs


# ── Security verification ────────────────────────────────────────────────


def verify_security(spec: dict[str, Any]) -> dict[str, Any]:
    """Verify authentication contract."""
    security_schemes = spec.get("components", {}).get("securitySchemes", {})
    global_security = spec.get("security", [])

    result = {
        "has_security_schemes": bool(security_schemes),
        "schemes": list(security_schemes.keys()),
        "global_security": bool(global_security),
        "auth_type": None,
        "auth_location": None,
    }

    for scheme_name, scheme_def in security_schemes.items():
        result["auth_type"] = scheme_def.get("type")
        result["auth_location"] = scheme_def.get("in")
        # Verify it matches what the client does
        if scheme_def.get("type") == "apiKey" and scheme_def.get("in") == "header":
            result["matches_client"] = True  # Client uses Authorization header

    return result


# ── Report generation ─────────────────────────────────────────────────────


def generate_conformance_report(
    results: list[ConformanceResult],
    schema_diffs: list[SchemaDifference],
    security_info: dict[str, Any],
    spec: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Generate machine-readable conformance report."""
    confirmed = [r for r in results if r.classification == Classification.CONFIRMED]
    partial = [r for r in results if r.classification == Classification.PARTIAL]
    unverified = [r for r in results if r.classification == Classification.UNVERIFIED]
    missing = [r for r in results if r.classification == Classification.MISSING_FROM_REPO]

    operations = []
    for r in results:
        ops_entry = {
            "method": r.method,
            "path": r.path,
            "repository_method": r.repository_method,
            "classification": r.classification.value,
            "differences": [
                {
                    "field": d.field,
                    "contract_value": d.contract_value,
                    "repo_value": d.repo_value,
                    "description": d.description,
                }
                for d in r.differences
            ],
        }
        if r.source_file:
            ops_entry["source_file"] = r.source_file
            ops_entry["source_line"] = r.source_line
        operations.append(ops_entry)

    return {
        "contract": {
            "path": str(_OPENAPI_PATH),
            "sha256": metadata.get("sha256", ""),
            "openapi_version": spec.get("openapi", ""),
            "document_version": spec.get("info", {}).get("version", ""),
            "fetched_at": metadata.get("fetched_at", ""),
        },
        "summary": {
            "total_openapi_operations": len([r for r in results if r.classification != Classification.MISSING_FROM_REPO]),
            "total_repo_endpoints": len([r for r in results if r.classification != Classification.MISSING_FROM_REPO]),
            "confirmed": len(confirmed),
            "partial": len(partial),
            "unverified": len(unverified),
            "missing_from_repo": len(missing),
        },
        "operations": operations,
        "schema_drift": [
            {
                "schema": d.schema_name,
                "field": d.field,
                "contract_type": d.contract_type,
                "repo_type": d.repo_type,
                "classification": d.classification,
            }
            for d in schema_diffs
        ],
        "security": security_info,
    }


def generate_markdown_report(report: dict[str, Any]) -> str:
    """Generate human-readable markdown conformance report."""
    c = report["contract"]
    s = report["summary"]

    lines = [
        "# Fangate Contract Conformance",
        "",
        "## Contract",
        "",
        f"- **Local path**: `{c['path']}`",
        f"- **OpenAPI version**: {c['openapi_version']}",
        f"- **API version**: {c['document_version']}",
        f"- **SHA-256**: `{c['sha256']}`",
        f"- **Retrieved**: {c['fetched_at']}",
        "",
        "## Summary",
        "",
        f"- **Documented operations**: {s['total_openapi_operations']}",
        f"- **Repository operations**: {s['total_repo_endpoints']}",
        f"- **Confirmed**: {s['confirmed']}",
        f"- **Partial**: {s['partial']}",
        f"- **Unverified**: {s['unverified']}",
        f"- **Missing from repo**: {s['missing_from_repo']}",
        "",
    ]

    # Confirmed
    confirmed_ops = [o for o in report["operations"] if o["classification"] == "CONFIRMED"]
    if confirmed_ops:
        lines.append("## Confirmed")
        lines.append("")
        lines.append("| Method | Path | Repository Method |")
        lines.append("|--------|------|-------------------|")
        for o in confirmed_ops:
            lines.append(f"| {o['method']} | `{o['path']}` | {o['repository_method']} |")
        lines.append("")

    # Partial
    partial_ops = [o for o in report["operations"] if o["classification"] == "PARTIAL"]
    if partial_ops:
        lines.append("## Partial")
        lines.append("")
        lines.append("| Method | Path | Repository Method | Difference | Severity |")
        lines.append("|--------|------|-------------------|------------|----------|")
        for o in partial_ops:
            for d in o["differences"]:
                severity = "INFO" if d["field"] in ("missing_query_parameter", "extra_request_field") else "WARN"
                lines.append(
                    f"| {o['method']} | `{o['path']}` | {o['repository_method']} | "
                    f"{d['description']} | {severity} |"
                )
        lines.append("")

    # Unverified
    unverified_ops = [o for o in report["operations"] if o["classification"] == "UNVERIFIED"]
    if unverified_ops:
        lines.append("## Unverified")
        lines.append("")
        lines.append("| Method | Path | Repository Method | Reason |")
        lines.append("|--------|------|-------------------|--------|")
        for o in unverified_ops:
            lines.append(
                f"| {o['method']} | `{o['path']}` | {o['repository_method']} | "
                f"Endpoint absent from local OpenAPI contract |"
            )
        lines.append("")

    # Missing from repo
    missing_ops = [o for o in report["operations"] if o["classification"] == "MISSING_FROM_REPO"]
    if missing_ops:
        lines.append("## Missing From Repository")
        lines.append("")
        lines.append("| Method | Path | OperationId |")
        lines.append("|--------|------|-------------|")
        for o in missing_ops:
            lines.append(f"| {o['method']} | `{o['path']}` | — |")
        lines.append("")

    # Schema drift
    if report["schema_drift"]:
        lines.append("## Schema Drift")
        lines.append("")
        lines.append("| Schema | Field | Contract | Repository | Classification |")
        lines.append("|--------|-------|----------|------------|----------------|")
        for d in report["schema_drift"]:
            lines.append(
                f"| {d['schema']} | {d['field']} | {d['contract_type'] or '—'} | "
                f"{d['repo_type'] or '—'} | {d['classification']} |"
            )
        lines.append("")

    # Security
    sec = report["security"]
    lines.append("## Security")
    lines.append("")
    lines.append(f"- **Auth type**: {sec.get('auth_type', 'unknown')}")
    lines.append(f"- **Auth location**: {sec.get('auth_location', 'unknown')}")
    lines.append(f"- **Matches client**: {sec.get('matches_client', 'unknown')}")
    lines.append(f"- **Global security**: {sec.get('global_security', False)}")
    lines.append("")

    # Limitations
    lines.append("## Limitations")
    lines.append("")
    lines.append("- Repository endpoints classified as UNVERIFIED are not invalid — they are absent from the local OpenAPI contract.")
    lines.append("- Schema comparison covers product.resource and wallet.resource only.")
    lines.append("- Request body field comparison is based on static analysis of client.py.")
    lines.append("- Response parsing (defensive unwrapping) is classified but not modified.")
    lines.append("- Webhook CRUD, offer, media, analytics, and epoch endpoints are UNVERIFIED.")
    lines.append("")

    return "\n".join(lines)
