"""Phase 1 unit tests: domain invariants + schema/ownership static checks.

No external infra required (no live PostgreSQL/Redis).
"""

from __future__ import annotations

import pathlib
from datetime import UTC

import pytest
from pydantic import ValidationError

from relationship_v2.domain.commerce_ref import (
    CommerceActionRequest,
    CommerceActionResult,
    CommerceActionResultCode,
    CommerceContextRef,
)
from relationship_v2.domain.conversation import (
    ConversationLifecycle,
    is_valid_conversation_transition,
)
from relationship_v2.domain.escalation import EscalationStage
from relationship_v2.domain.identity import FanScope
from relationship_v2.domain.memory import MemoryFact, MemoryFactStatus
from relationship_v2.domain.relationship import (
    RelationshipLifecycle,
    is_valid_relationship_transition,
)
from relationship_v2.persistence.owners import TABLE_OWNERS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = REPO_ROOT / "relationship_v2" / "persistence" / "schema.sql"


def _dt():
    from datetime import datetime

    return datetime.now(UTC)


def test_fan_scope_fail_closed() -> None:
    with pytest.raises(ValidationError):
        FanScope(creator_id=0, user_id=1)
    with pytest.raises(ValidationError):
        FanScope(creator_id=1, user_id=-5)
    s = FanScope(creator_id=1, user_id=2)
    assert s.as_tuple() == (1, 2)


def test_relationship_transitions_guarded() -> None:
    assert is_valid_relationship_transition(
        RelationshipLifecycle.NEW, RelationshipLifecycle.WARMING
    )
    # Skipping evidence is invalid: new -> deep without milestones.
    assert not is_valid_relationship_transition(
        RelationshipLifecycle.NEW, RelationshipLifecycle.DEEP
    )
    assert is_valid_relationship_transition(
        RelationshipLifecycle.DORMANT, RelationshipLifecycle.REACTIVATED
    )


def test_conversation_transitions_guarded() -> None:
    assert is_valid_conversation_transition(
        ConversationLifecycle.STARTED, ConversationLifecycle.ACTIVE
    )
    # Closed -> active without resumed is invalid.
    assert not is_valid_conversation_transition(
        ConversationLifecycle.CLOSED, ConversationLifecycle.ACTIVE
    )
    assert is_valid_conversation_transition(
        ConversationLifecycle.PAUSED, ConversationLifecycle.RESUMED
    )


def test_memory_fact_temporal_rules() -> None:
    from uuid import uuid4

    now = _dt()
    # Superseded without effective_to is rejected.
    with pytest.raises(ValidationError):
        MemoryFact(
            id=uuid4(),
            creator_id=1,
            user_id=2,
            relationship_id=uuid4(),
            category="occupation",
            memory_key="job",
            value="photographer",
            status=MemoryFactStatus.SUPERSEDED,
            confidence=0.9,
            effective_from=now,
            provenance="test",
            created_at=now,
            updated_at=now,
        )


def test_escalation_defaults_safe() -> None:
    # PRESENT_OFFER must never be the default; commerce eligibility defaults False.
    from uuid import uuid4

    from relationship_v2.domain.escalation import EscalationState

    st = EscalationState(relationship_id=uuid4(), creator_id=1, user_id=2, as_of=_dt())
    assert st.stage == EscalationStage.RELATIONSHIP
    assert st.commerce_eligible is False


def test_commerce_ref_is_cache_only() -> None:
    from uuid import uuid4

    now = _dt()
    ref = CommerceContextRef(
        id=uuid4(),
        creator_id=1,
        user_id=2,
        relationship_id=uuid4(),
        commerce_request_id="req_1",
        payload_hash="abc",
        confirmed_at=now,
    )
    assert ref.commerce_request_id == "req_1"
    req = CommerceActionRequest(
        commerce_request_id="req_1",
        creator_id=1,
        user_id=2,
        action="evaluate",
        idempotency_key="idem_1",
        owner="relationship_v2",
    )
    assert req.idempotency_key == "idem_1"
    res = CommerceActionResult(commerce_request_id="req_1", code=CommerceActionResultCode.DENIED)
    assert res.code == CommerceActionResultCode.DENIED


def test_schema_has_all_owned_tables() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    for table in TABLE_OWNERS:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, table
    assert len(TABLE_OWNERS) == 12
    expected = {
        "v2_relationships",
        "v2_memory_facts",
        "v2_memory_episodes",
        "v2_conversations",
        "v2_conversation_turns",
        "v2_events",
        "v2_commerce_refs",
        "v2_relationship_snapshots",
        "v2_engagement_signals",
        "v2_processed_events",
        "v2_open_loops",
        "v2_intimate_history",
    }
    assert set(TABLE_OWNERS) == expected
    # Commerce authority: V2 schema must not create commerce truth tables.
    for forbidden in (
        "commerce_offers",
        "fangate_transactions",
        "vault_media_deliveries",
        "fangate_products",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {forbidden}" not in sql


def _imported_modules(path) -> set[str]:
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_no_v1_or_commerce_leakage_in_v2_package() -> None:
    pkg = REPO_ROOT / "relationship_v2"
    forbidden_prefixes = (
        "commerce",
        "memory",
        "context_engine",
        "integrations.dropfans",
        "integrations.fangate",
    )
    forbidden_modules = {
        "core.one_call",
        "core.one_call_pipeline",
        "core.routing",
        "core.scoring",
        "core.scoring_deterministic",
    }
    files = list(pkg.rglob("*.py"))
    assert files, "V2 package must contain modules"
    for f in files:
        # Exempt: relationship_v2/integration/ is the sanctioned commerce
        # boundary (COMMERCE_CONTRACT + 10_LEGACY_ISOLATION allowed
        # dependency V2 -> Commerce Adapter -> Preserved Commerce). Its own
        # allow-list test (test_relationship_v2_commerce_ports) restricts it
        # to preserved truth reads.
        if "integration" in f.parts:
            continue
        for mod in _imported_modules(f):
            assert mod not in forbidden_modules, f"{f.name}: {mod}"
            for prefix in forbidden_prefixes:
                assert not (mod == prefix or mod.startswith(prefix + ".")), f"{f.name}: {mod}"
