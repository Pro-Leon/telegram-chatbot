"""Phase 8 unit tests: commerce READ/REQUEST/CONFIRMATION + isolation.

No commerce imports (fake ports). Covers COMMERCE_CONTRACT.md operations,
06_COMMERCE_BOUNDARY.md isolation probes, FAILURE_MODES commerce mapping.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from relationship_v2.domain.commerce import StrategyAction
from relationship_v2.domain.commerce_ref import CommerceActionResultCode
from relationship_v2.services.commerce_adapter import (
    COMMERCE_SNAPSHOT_TTL_SECONDS,
    build_action_request,
    build_purchase_feedback,
    is_snapshot_stale,
    map_confirmation,
    map_error_to_strategy,
    read_context,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


async def _elig_ok(c, u):
    return {"eligible": True, "reason": "ok"}


async def _elig_no(c, u):
    return {"eligible": False, "reason": "offer_exists"}


async def _opp(c, u):
    return {"active_offer": False, "opportunities": ["ppv_candidate"], "cooldowns": {}}


async def _purch(c, u):
    return {"status": "none", "count": 0, "owned_refs": [], "aftercare": "none"}


async def _boom(c, u):
    raise TimeoutError("commerce timeout")


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_read_builds_normalized_context() -> None:
    ctx = _run(read_context(1, 2, _elig_ok, _opp, _purch))
    assert ctx.purchase_count == 0
    assert ctx.available_opportunities == ["ppv_candidate"]
    assert ctx.deterministic_constraints == []


def test_ineligibility_surfaces_as_constraint() -> None:
    ctx = _run(read_context(1, 2, _elig_no, _opp, _purch))
    assert any("offer_exists" in c for c in ctx.deterministic_constraints)


def test_timeout_is_unknown_never_confirmed() -> None:
    with pytest.raises(TimeoutError):
        _run(read_context(1, 2, _boom, _opp, _purch))


def test_stale_snapshot_blocks_offer() -> None:
    ctx = _run(read_context(1, 2, _elig_ok, _opp, _purch))
    future = ctx.as_of + timedelta(seconds=COMMERCE_SNAPSHOT_TTL_SECONDS + 1)
    assert is_snapshot_stale(ctx, future) is True
    assert is_snapshot_stale(ctx, ctx.as_of) is False


def test_request_carries_idempotency_and_owner() -> None:
    req = build_action_request(1, 2, "evaluate", "idem-1")
    assert req.idempotency_key == "idem-1"
    assert req.owner == "relationship_v2"


def test_confirmation_mapping_and_unknown_code() -> None:
    req = build_action_request(1, 2, "evaluate", "idem-2")
    ok = map_confirmation(req, "confirmed", datetime.now(UTC))
    assert ok.code == CommerceActionResultCode.CONFIRMED
    weird = map_confirmation(req, "made_up_code")
    assert weird.code == CommerceActionResultCode.VERIFICATION_FAILED
    with pytest.raises(ValueError):
        map_confirmation(req, "confirmed", None)


def test_error_to_strategy_never_invents_success() -> None:
    assert map_error_to_strategy(CommerceActionResultCode.DENIED) == StrategyAction.HANDOFF
    assert map_error_to_strategy(CommerceActionResultCode.INELIGIBLE) == StrategyAction.SUPPRESS
    assert map_error_to_strategy(CommerceActionResultCode.PROVIDER_ERROR) == StrategyAction.RETRY


def test_purchase_feedback_requires_reconciliation() -> None:
    with pytest.raises(ValueError):
        build_purchase_feedback(
            {"creator_id": 1, "user_id": 2, "summary": "bought X", "provenance": "t"}
        )
    fb = build_purchase_feedback(
        {
            "creator_id": 1,
            "user_id": 2,
            "summary": "purchased content",
            "provenance": "commerce webhook",
            "reconciled": True,
        }
    )
    assert fb.episode_type == "purchase"
    assert fb.reconciled is True


def test_post_purchase_buyer_context_flows() -> None:
    async def _purch_buyer(c, u):
        return {"status": "repeat", "count": 3, "owned_refs": ["vault:a"], "aftercare": "active"}

    ctx = _run(read_context(1, 2, _elig_ok, _opp, _purch_buyer))
    assert ctx.purchase_count == 3
    assert ctx.aftercare_state == "active"


def test_phase8_isolation_no_commerce_writes_or_calls() -> None:
    import ast

    target = REPO_ROOT / "relationship_v2" / "services" / "commerce_adapter.py"
    tree = ast.parse(target.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for mod in mods:
            if mod.startswith("relationship_v2") or mod in ("db.postgres", "db"):
                continue
            assert not (mod == "commerce" or mod.startswith("commerce.")), mod
            assert "integrations.dropfans" not in mod, mod
            assert "integrations.fangate" not in mod, mod
    text = target.read_text(encoding="utf-8")
    for forbidden in (
        "commerce_offers",
        "fangate_transactions",
        "vault_media_deliveries",
        "execute_ppv",
        "uuid_generate",
        "INSERT INTO commerce",
        "UPDATE commerce",
    ):
        assert forbidden not in text, forbidden
