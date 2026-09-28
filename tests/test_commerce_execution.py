"""Phase 5.3C — deterministic PPV commerce execution tests.

Covers the full 5.3C contract:
- execution result abstraction (ten stable states, safe metadata)
- pre-execution authority gate (decision, creator, integration, vault, fan,
  local product, eligibility, live Fangate verification)
- product/price authority (local mirror vs live catalog, refusal on mismatch)
- idempotency (deterministic identity + advisory-lock serialized creation)
- failure semantics (4xx/5xx/transport/timeout mapping, no blind replay)
- persistence ordering, rollup failure isolation, determinism
- security (no secrets in logs/results, restricted import surface,
  execute_ppv takes NO price/link parameters)

All DB/Fangate interaction is mocked — no live credentials.
"""

import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

EXECUTION_PATH = Path(__file__).parent.parent / "commerce" / "execution.py"

_CREATOR = {"id": 1, "name": "Test Creator"}
_INTEGRATION = {
    "id": 1,
    "creator_id": 1,
    "encrypted_api_key": "gAAAAA-fake-ciphertext",
    "api_key_name": "crm",
    "webhook_id": 99,
    "encrypted_webhook_secret": "gAAAAA-secret-ciphertext",
    "status": "active",
}
_FAN = {"id": 5, "username": "fan", "is_blocked": False, "do_not_auto_reply": False}
_LOCAL_PRODUCT = {
    "id": 5155,
    "creator_id": 1,
    "is_accessible": True,
    "sales_url": "https://fangate.info/5155x",
    "price_minor": 4400,
    "is_verif_age": False,
}
_LOCAL_PRODUCT_ROW = {
    "id": 5155,
    "creator_id": 1,
    "is_accessible": True,
    "sales_url": "https://fangate.info/5155x",
    "price_minor": 4400,
    "is_verif_age": False,
    "raw": {"dropfans_product_id": "df_prod_abc123", "vaultItemIds": ["v1", "v2"]},
}
_REMOTE_PRODUCT = {
    "id": 5155,
    "type": "image",
    "title": "Campaign set",
    "preview": "https://fangate.info/storage/preview.png",
    "preview_blurred": "https://fangate.info/storage/preview-blurred.png",
    "price": 4400,
    "in_collection": False,
    "link": "https://fangate.info/5155x",
    "link_clicks": 3,
    "unlocks": 1,
    "total_earnings": 0,
    "folder_id": "12",
    "folder": {"id": "12", "name": "Campaigns"},
    "media": [],
    "is_adult_content": True,
    "is_verif_age": False,
    "is_epoch_enabled": True,
    "is_should_consent": False,
    "is_downloadable": True,
    "is_accessible": True,
    "private_description": None,
    "public_description": "",
}
_OFFER_ROW = {
    "id": 10,
    "creator_id": 1,
    "user_id": 5,
    "product_id": 5155,
    "link": "https://fangate.info/5155x",
    "state": "pending",
    "price_minor": 4400,
    "currency": None,
    "reason": "ppv_execution",
}


def _decision():
    from commerce.decision import CommerceDecision, CommerceReason
    from commerce.models import CommerceAction

    return CommerceDecision(
        action=CommerceAction.OFFER_PPV,
        reason_code=CommerceReason.STRONG_BUYING_SIGNAL,
        allowed=True,
        confidence=1.0,
    )


def _remote_product(**overrides):
    from integrations.fangate.models import FangateProduct

    data = dict(_REMOTE_PRODUCT)
    data.update(overrides)
    return FangateProduct.from_api(data)


def _patch_ppv(monkeypatch):
    """Install AsyncMock replacements for every backend execute_ppv touches.

    Returns {name: AsyncMock} for per-test overrides and assertions.
    """
    from unittest.mock import AsyncMock, MagicMock

    from commerce import execution as ex
    from db import dropfans as ddb
    from integrations.dropfans import service as dservice

    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(return_value=_LOCAL_PRODUCT_ROW)
    mock_pool.execute = AsyncMock(return_value="UPDATE 1")

    # Live price mock: Drop price 44.00 USD == 4400 cents matches local (equal case) by default
    async def _mock_get_drop(creator_id, drop_id):
        return {"price": 44.0, "currency": "USD", "buyUrl": "https://www.dropfans.io/buy/df_prod_abc123", "mediaCount": 0, "media": []}

    mocks = {
        "get_creator": AsyncMock(return_value=_CREATOR),
        "get_integration": AsyncMock(return_value=_INTEGRATION),
        "get_user": AsyncMock(return_value=_FAN),
        "find_pending": AsyncMock(return_value=None),
        "has_purchased": AsyncMock(return_value=False),
        "create_serialized": AsyncMock(return_value=(dict(_OFFER_ROW), True)),
        "record_transition": AsyncMock(return_value=None),
        "get_pool": AsyncMock(return_value=mock_pool),
        "get_drop": AsyncMock(side_effect=_mock_get_drop),
    }
    monkeypatch.setattr(ddb, "get_dropfans_integration", mocks["get_integration"])
    monkeypatch.setattr(dservice, "get_drop", mocks["get_drop"])
    monkeypatch.setattr(ex, "get_user", mocks["get_user"])
    monkeypatch.setattr(ex, "find_pending_offer_for_product", mocks["find_pending"])
    monkeypatch.setattr(ex, "has_purchased_product", mocks["has_purchased"])
    monkeypatch.setattr(ex, "create_offer_serialized", mocks["create_serialized"])
    monkeypatch.setattr(ex, "record_offer_transition", mocks["record_transition"])
    monkeypatch.setattr(ex, "decrypt_secret", lambda token: "fg_sk_live_test")
    # P3.2C F1: tests declare the P3.2 safety schema PRESENT (no live DB).
    monkeypatch.setattr(
        ex,
        "check_commerce_schema_ready",
        AsyncMock(return_value=(True, "commerce_schema_present")),
    )
    monkeypatch.setattr("db.postgres.get_pool", mocks["get_pool"])
    return mocks


async def _run(
    monkeypatch, *, mocks=None, decision=None, created_by="decision_engine", age_verified=False
):
    """Run execute_ppv once with the standard happy-path environment."""
    if mocks is None:
        mocks = _patch_ppv(monkeypatch)
    from commerce.execution import execute_ppv

    result = await execute_ppv(
        creator_id=1,
        user_id=5,
        product_id=5155,
        decision=decision if decision is not None else _decision(),
        created_by=created_by,
        age_verified=age_verified,
    )
    return result, mocks


# â”€â”€ Result contract â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestExecutionStatus:
    def test_ten_states_match_contract(self):
        from commerce.execution import ExecutionStatus

        expected = [
            "executed",
            "already_executed",
            "denied",
            "product_unavailable",
            "eligibility_denied",
            "creator_not_ready",
            "provider_error",
            "persistence_failed",
            "execution_conflict",
            "requires_manual_review",
        ]
        assert [s.value for s in ExecutionStatus] == expected

    def test_status_values_are_stable_lowercase(self):
        from commerce.execution import ExecutionStatus

        for status in ExecutionStatus:
            assert isinstance(status.value, str)
            assert status.value == status.value.lower()

    def test_only_executed_counts_as_created(self):
        from commerce.execution import ExecutionResult, ExecutionStatus

        for status in ExecutionStatus:
            result = ExecutionResult(status=status)
            assert result.created is (status is ExecutionStatus.EXECUTED)

    def test_result_is_frozen_and_metadata_defaults_empty(self):
        from commerce.execution import ExecutionResult, ExecutionStatus

        result = ExecutionResult(status=ExecutionStatus.DENIED)
        with pytest.raises(AttributeError):
            result.status = ExecutionStatus.EXECUTED
        assert result.metadata == {}


# â”€â”€ Decision authority â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestDecisionAuthority:
    @pytest.mark.asyncio
    async def test_non_offer_action_denied(self, monkeypatch):
        from commerce.decision import CommerceDecision, CommerceReason
        from commerce.execution import ExecutionStatus
        from commerce.models import CommerceAction

        decision = CommerceDecision(
            action=CommerceAction.FOLLOW_UP,
            reason_code=CommerceReason.FOLLOW_UP_DUE,
            allowed=True,
            confidence=0.75,
        )
        result, _ = await _run(monkeypatch, decision=decision)
        assert result.status is ExecutionStatus.DENIED
        assert result.denial_reason == "decision_not_authorized"
        assert result.metadata["action"] == "follow_up"
        assert result.offer_id is None

    @pytest.mark.asyncio
    async def test_disallowed_offer_action_denied(self, monkeypatch):
        from commerce.decision import CommerceDecision, CommerceReason
        from commerce.execution import ExecutionStatus
        from commerce.models import CommerceAction

        decision = CommerceDecision(
            action=CommerceAction.OFFER_PPV,
            reason_code=CommerceReason.INSUFFICIENT_RELATIONSHIP,
            allowed=False,
            confidence=0.5,
        )
        result, _ = await _run(monkeypatch, decision=decision)
        assert result.status is ExecutionStatus.DENIED

    @pytest.mark.asyncio
    async def test_denied_never_touches_backends(self, monkeypatch):
        from commerce.decision import CommerceDecision, CommerceReason
        from commerce.execution import ExecutionStatus
        from commerce.models import CommerceAction

        decision = CommerceDecision(
            action=CommerceAction.NO_OFFER,
            reason_code=CommerceReason.NO_BUYING_SIGNAL,
            allowed=False,
            confidence=0.5,
        )
        result, mocks = await _run(monkeypatch, decision=decision)
        assert result.status is ExecutionStatus.DENIED
        for name, mock in mocks.items():
            assert mock.assert_not_awaited() is None, name


# â”€â”€ Creator / integration / vault authority â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestCreatorAuthority:
    @pytest.mark.asyncio
    async def test_missing_creator(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["get_integration"].return_value = None
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.CREATOR_NOT_READY
        assert result.denial_reason == "integration_not_ready"

    @pytest.mark.asyncio
    async def test_missing_integration(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["get_integration"].return_value = None
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.CREATOR_NOT_READY
        assert result.denial_reason == "integration_not_ready"

    @pytest.mark.asyncio
    async def test_inactive_integration(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["get_integration"].return_value = dict(_INTEGRATION, status="disconnected")
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.CREATOR_NOT_READY
        assert result.denial_reason == "integration_not_ready"
        assert result.metadata["integration_status"] == "disconnected"

    @pytest.mark.asyncio
    async def test_undecryptable_credential(self, monkeypatch):
        from commerce import execution as ex
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        monkeypatch.setattr(
            ex, "decrypt_secret", lambda token: (_ for _ in ()).throw(RuntimeError())
        )
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.CREATOR_NOT_READY
        assert result.denial_reason == "credential_unavailable"


# â”€â”€ Fan authority â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestFanAuthority:
    @pytest.mark.asyncio
    async def test_unknown_user_denied(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["get_user"].return_value = None
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.ELIGIBILITY_DENIED
        assert result.denial_reason == "user_unknown"

    @pytest.mark.parametrize(
        "flags,expected_reason",
        [
            ({"is_blocked": True}, "user_blocked"),
            ({"do_not_auto_reply": True}, "user_opted_out"),
        ],
    )
    @pytest.mark.asyncio
    async def test_blocked_or_opted_out(self, monkeypatch, flags, expected_reason):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["get_user"].return_value = dict(_FAN, **flags)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.ELIGIBILITY_DENIED
        assert result.denial_reason == expected_reason


# â”€â”€ Local product authority â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestLocalProductAuthority:
    @pytest.mark.asyncio
    async def test_missing_local_product(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["get_pool"].return_value.fetchrow = AsyncMock(return_value=None)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.PRODUCT_UNAVAILABLE
        assert result.denial_reason == "product_missing"

    @pytest.mark.asyncio
    async def test_local_lookup_db_failure(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["get_pool"].return_value.fetchrow = AsyncMock(side_effect=Exception("db failed"))
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.PRODUCT_UNAVAILABLE
        assert result.denial_reason == "product_lookup_failed"

    @pytest.mark.asyncio
    async def test_inaccessible_product_denied(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        row = dict(_LOCAL_PRODUCT_ROW, is_accessible=False)
        mocks["get_pool"].return_value.fetchrow = AsyncMock(return_value=row)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.ELIGIBILITY_DENIED
        assert result.denial_reason == "product_unavailable"

    @pytest.mark.asyncio
    async def test_missing_sales_url_denied(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        row = dict(_LOCAL_PRODUCT_ROW, sales_url=None)
        mocks["get_pool"].return_value.fetchrow = AsyncMock(return_value=row)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.ELIGIBILITY_DENIED
        assert result.denial_reason == "product_missing_sales_url"

    @pytest.mark.asyncio
    async def test_age_verified_passes(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        result, _ = await _run(monkeypatch, mocks=mocks, age_verified=True)
        assert result.status is ExecutionStatus.EXECUTED


# â”€â”€ Existing offer / purchase authority â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestExistingStateAuthority:
    @pytest.mark.parametrize("state", ["pending", "clicked"])
    @pytest.mark.asyncio
    async def test_redeemable_offer_means_already_executed(self, monkeypatch, state):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["find_pending"].return_value = dict(_OFFER_ROW, state=state, id=7)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.ALREADY_EXECUTED
        assert result.offer_id == 7
        assert result.offer_state == state

    @pytest.mark.asyncio
    async def test_purchased_product_denied(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["has_purchased"].return_value = True
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.ELIGIBILITY_DENIED
        assert result.denial_reason == "already_purchased"


# â”€â”€ Live Fangate verification â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestRemoteVerificationAuthority:
    """REMOVED: Remote verification was Fangate-specific. Dropfans uses local product authority only."""

    @pytest.mark.asyncio
    async def test_dropfans_uses_local_product_authority(self, monkeypatch):
        """Dropfans execution uses local product data, no remote verification."""
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.EXECUTED


# â”€â”€ Product / price authority â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestProductPriceAuthority:
    @pytest.mark.asyncio
    async def test_missing_link_and_price_fails(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        row = dict(_LOCAL_PRODUCT_ROW, sales_url=None, price_minor=None)
        mocks["get_pool"].return_value.fetchrow = AsyncMock(return_value=row)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.ELIGIBILITY_DENIED
        assert result.denial_reason == "product_missing_sales_url"

    @pytest.mark.asyncio
    async def test_authoritative_payload_passed_to_creation(self, monkeypatch):
        mocks = _patch_ppv(monkeypatch)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status.value == "executed"
        call = mocks["create_serialized"].await_args
        kwargs = call.kwargs
        assert kwargs["link"] == "https://fangate.info/5155x"
        assert kwargs["price_minor"] == 4400
        assert kwargs["reason"] == "ppv_execution"
        assert kwargs["created_by"] == "decision_engine"
        assert kwargs["creator_id"] == 1
        assert kwargs["user_id"] == 5
        assert kwargs["product_id"] == 5155

    @pytest.mark.asyncio
    async def test_consistent_pay_what_you_want_price_ok(self, monkeypatch):
        mocks = _patch_ppv(monkeypatch)
        row = dict(_LOCAL_PRODUCT_ROW, price_minor=None)
        mocks["get_pool"].return_value.fetchrow = AsyncMock(return_value=row)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status.value == "executed"


# â”€â”€ Idempotency + persistence â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestIdempotencyAndPersistence:
    @pytest.mark.asyncio
    async def test_second_creation_reports_already_executed(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["create_serialized"].return_value = (dict(_OFFER_ROW, id=12), False)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.ALREADY_EXECUTED
        assert result.offer_id == 12
        assert result.offer_state == "pending"

    @pytest.mark.asyncio
    async def test_ambiguous_db_failure_recovers_existing_offer(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["create_serialized"].side_effect = RuntimeError("connection lost")
        mocks["find_pending"].side_effect = [None, dict(_OFFER_ROW, id=9)]
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.ALREADY_EXECUTED
        assert result.offer_id == 9
        assert result.metadata["recovered_after"] == "persistence_ambiguity"

    @pytest.mark.asyncio
    async def test_ambiguous_db_failure_unrecovered_is_persistence_failed(self, monkeypatch):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["create_serialized"].side_effect = RuntimeError("connection lost")
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.PERSISTENCE_FAILED
        assert result.denial_reason == "RuntimeError"

    @pytest.mark.asyncio
    async def test_rollup_failure_does_not_flip_execution(self, monkeypatch, caplog):
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        mocks["record_transition"].side_effect = RuntimeError("rollup boom")
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.EXECUTED
        assert result.created is True
        assert "rollup failed" in caplog.text

    @pytest.mark.asyncio
    async def test_successful_execution_records_pending_rollup(self, monkeypatch):
        mocks = _patch_ppv(monkeypatch)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status.value == "executed"
        assert result.created is True
        assert result.offer_id == 10
        assert result.offer_state == "pending"
        mocks["record_transition"].assert_awaited_once()
        kwargs = mocks["record_transition"].await_args.kwargs
        assert kwargs["state"] == "pending"
        assert kwargs["creator_id"] == 1
        assert kwargs["product_id"] == 5155

    @pytest.mark.asyncio
    async def test_persistence_order_insert_after_url_build(self, monkeypatch):
        """Verify offer is created after checkout URL is resolved."""
        from commerce.execution import ExecutionStatus

        mocks = _patch_ppv(monkeypatch)
        call_order = []
        original_create = mocks["create_serialized"]

        async def tracking_create(**kwargs):
            call_order.append("create")
            return await original_create(**kwargs)

        mocks["create_serialized"] = tracking_create
        monkeypatch.setattr(
            "commerce.execution.create_offer_serialized",
            tracking_create,
        )
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status is ExecutionStatus.EXECUTED
        assert "create" in call_order


# â”€â”€ Determinism / authority surface / security â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestDeterminismAndAuthoritySurface:
    def test_execute_ppv_never_accepts_price_or_link(self):
        from commerce.execution import execute_ppv

        signature = inspect.signature(execute_ppv)
        params = set(signature.parameters)
        assert params & {"price", "price_minor", "link", "currency"} == set()
        assert signature.parameters["product_id"].kind is inspect.Parameter.KEYWORD_ONLY

    @pytest.mark.asyncio
    async def test_same_inputs_produce_identical_results(self, monkeypatch):
        mocks = _patch_ppv(monkeypatch)
        first, _ = await _run(monkeypatch, mocks=mocks)
        second, _ = await _run(monkeypatch, mocks=mocks)
        assert first == second

    def test_import_scope_is_restricted(self):
        tree = ast.parse(EXECUTION_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        allowed = {
            "commerce",
            "core",  # observability: commerce.offer_created via core.event_bus
            "dataclasses",
            "datetime",
            "db",
            "decimal",  # deterministic USD dollars → cents conversion
            "enum",
            "integrations",
            "json",
            "logging",
            "typing",
        }
        unexpected = imported - allowed
        assert unexpected == set(), f"Unexpected imports: {unexpected}"
        assert "random" not in imported
        assert "time" not in imported

    def test_no_event_worker_or_transport_surface(self):
        tree = ast.parse(EXECUTION_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in {
                        "event_bus",
                        "ws_manager",
                        "event_subscriber",
                        "redis",
                        "realtime",
                        "llm_worker",
                        "send_worker",
                        "telethon",
                    }, alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in {
                    "event_bus",
                    "ws_manager",
                    "event_subscriber",
                    "redis",
                    "realtime",
                    "llm_worker",
                    "send_worker",
                    "telethon",
                }, node.module


class TestSecurity:
    @pytest.mark.asyncio
    async def test_logs_never_contain_credentials(self, monkeypatch, caplog):
        from commerce import execution as ex

        caplog.set_level("WARNING")
        mocks = _patch_ppv(monkeypatch)
        mocks["get_integration"].return_value = dict(
            _INTEGRATION, encrypted_api_key="gAAAAA-SECRET-CIPHERTEXT"
        )
        monkeypatch.setattr(
            ex, "decrypt_secret", lambda token: (_ for _ in ()).throw(RuntimeError())
        )
        await _run(monkeypatch, mocks=mocks)
        assert "gAAAAA-SECRET-CIPHERTEXT" not in caplog.text
        assert "fg_sk_live_test" not in caplog.text

    @pytest.mark.asyncio
    async def test_result_metadata_never_contains_secrets(self, monkeypatch):
        mocks = _patch_ppv(monkeypatch)
        result, _ = await _run(monkeypatch, mocks=mocks)
        assert result.status.value == "executed"
        # P3.2: metadata carries sealed execution-time commercial facts
        # (verified price/link/CUID/snapshot hash) — never credentials.
        blob = repr(result.metadata).lower()
        assert "gAAAAA" not in repr(result.metadata)
        assert "fg_sk_live_test" not in blob
        assert "bearer" not in blob
        assert "authorization" not in blob
        assert result.metadata.get("verified_price_minor") == 4400
        assert result.metadata.get("dropfans_product_id") == "df_prod_abc123"


# â”€â”€ DAO: serialized creation + purchased check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _patch_pool(mock_conn):
    """Same pool-mocking convention as test_commerce_domain (local copy)."""
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_ctx)
    patcher = patch("commerce.dao.get_pool", new_callable=AsyncMock, return_value=mock_pool)
    patcher.start()
    _ACTIVE_PATCHERS.append(patcher)


_ACTIVE_PATCHERS: list = []


@pytest.fixture(autouse=True)
def _stop_patches():
    yield
    while _ACTIVE_PATCHERS:
        _ACTIVE_PATCHERS.pop().stop()


def _txn_ctx(mock_conn):
    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=txn)
    txn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=txn)


class TestSerializedOfferDao:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_serialized_create_locks_rechecks_and_inserts(self):
        mock_conn = AsyncMock()
        _patch_pool(mock_conn)
        _txn_ctx(mock_conn)
        row = dict(_OFFER_ROW)
        mock_conn.fetchrow = AsyncMock(side_effect=[None, row])

        from commerce.dao import create_offer_serialized

        result, created = await create_offer_serialized(
            creator_id=1,
            user_id=5,
            product_id=5155,
            link="https://fangate.info/5155x",
            price_minor=4400,
            created_by="decision_engine",
            dropfans_product_id="df_prod_abc123",
            vault_item_ids=["v1", "v2"],
        )
        assert created is True
        assert result["id"] == 10
        lock_args = mock_conn.execute.call_args[0]
        assert "pg_advisory_xact_lock" in lock_args[0]
        assert "hashtextextended" in lock_args[0]
        assert lock_args[1] == "ppv_offer:1:5:5155"
        calls = mock_conn.fetchrow.call_args_list
        assert len(calls) == 2
        assert "INSERT INTO commerce_offers" in calls[1][0][0]

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_serialized_create_returns_existing_without_insert(self):
        mock_conn = AsyncMock()
        _patch_pool(mock_conn)
        _txn_ctx(mock_conn)
        existing = dict(_OFFER_ROW, id=7, state="clicked")
        mock_conn.fetchrow = AsyncMock(side_effect=[existing])

        from commerce.dao import create_offer_serialized

        row, created = await create_offer_serialized(
            creator_id=1, user_id=5, product_id=5155, link="x", created_by="engine",
            dropfans_product_id="df_prod_abc123", vault_item_ids=["v1"],
        )
        assert created is False
        assert row["id"] == 7
        sql = mock_conn.fetchrow.call_args_list[0][0][0]
        assert "INSERT" not in sql
        assert "state IN ('pending', 'clicked')" in sql

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_has_purchased_product_shape_and_result(self):
        mock_conn = AsyncMock()
        _patch_pool(mock_conn)
        mock_conn.fetchrow = AsyncMock(return_value={"x": 1})

        from commerce.dao import has_purchased_product

        assert await has_purchased_product(1, 5, 5155) is True
        sql, *params = mock_conn.fetchrow.call_args[0]
        assert "state = 'purchased'" in sql
        assert "transaction_id IS NOT NULL" in sql
        assert params == [1, 5, 5155]

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_has_purchased_product_false_when_absent(self):
        mock_conn = AsyncMock()
        _patch_pool(mock_conn)
        mock_conn.fetchrow = AsyncMock(return_value=None)

        from commerce.dao import has_purchased_product

        assert await has_purchased_product(1, 5, 5155) is False


# â”€â”€ Service: live verify_product â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _mock_fangate_db(monkeypatch):
    """Minimal AsyncMock replacements for the db.fangate functions that
    ``verify_product`` (via _run_scoped / _get_client) touches."""
    from db import fangate as fdb

    mocks = {
        "get_creator_integration": AsyncMock(return_value=_INTEGRATION),
        "record_integration_success": AsyncMock(return_value=None),
        "record_integration_error": AsyncMock(return_value=None),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(fdb, name, mock)
    monkeypatch.setattr("integrations.fangate.service.decrypt_secret", lambda _: "fg_test_key")
    return mocks


class TestVerifyProductService:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_success_returns_authoritative_product(self, monkeypatch):
        from integrations.fangate.service import verify_product

        mocks = _mock_fangate_db(monkeypatch)

        class FakeClient:
            async def get_product(self, product_id):
                return _remote_product()

            async def close(self):
                pass

        with patch("integrations.fangate.service.FangateClient", return_value=FakeClient()):
            product = await verify_product(7, 5155)

        assert product.id == 5155
        assert product.link == "https://fangate.info/5155x"
        assert product.price_minor == 4400
        mocks["record_integration_success"].assert_awaited_once_with(7)

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_remote_missing_raises_fangate_not_found(self, monkeypatch):
        from integrations.fangate.errors import FangateNotFoundError
        from integrations.fangate.service import verify_product

        mocks = _mock_fangate_db(monkeypatch)

        class FakeClient:
            async def get_product(self, product_id):
                raise FangateNotFoundError("GET /products/9999", status_code=404)

            async def close(self):
                pass

        with (
            patch("integrations.fangate.service.FangateClient", return_value=FakeClient()),
            pytest.raises(FangateNotFoundError),
        ):
            await verify_product(7, 9999)
        mocks["record_integration_error"].assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_integration_raises(self, monkeypatch):
        from integrations.fangate.service import IntegrationNotFoundError, verify_product

        mocks = _mock_fangate_db(monkeypatch)
        mocks["get_creator_integration"].return_value = None

        with pytest.raises(IntegrationNotFoundError):
            await verify_product(7, 5155)
