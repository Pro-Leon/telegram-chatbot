"""Phase 5.4 Chk 6D tests — state to commerce pipeline composition.

Groups:

A: composition surface (keyword-only API, closed status set 1:1 with 6C).
B: full state mapping (every request field reaches the pipeline verbatim).
C: creator resolution (explicit verify, offer-history derivation, unavailable).
D: product resolution (explicit only, mirror-only, never invented, unavailable).
E: eligibility engine integration (every denial reason via the composition).
F: purchases and offers (previous status, pending offer, already purchased).
G: creator sales flags (active-only enablement).
H: conversation-text authority (price/URL/product text NEVER becomes state).
I: pipeline invocation (called EXACTLY once, correct request + policy).
J: state failure never calls the pipeline (silent surfaces, FAILED).
K: execution authority proof (integration -> pipeline -> orchestrator ->
   execute_ppv, exactly once).
L: determinism (identical reads -> identical outcomes, no clock/randomness).
M: security surface (AST/source: no forbidden imports and call sites).
N: result envelope (sealed pipeline result preserved, closed failure codes).

The real resolver and the REAL pipeline are exercised; only read functions
(db.postgres / db.fangate / commerce.dao) and the LLM/execution touch points
are mocked.
"""

import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from commerce import dao as cdao
from commerce.context import ProductCommerceState, ProductIdentity
from commerce.decision import CommerceDecisionPolicy
from commerce.deepseek_response import CommerceResponse, CommerceResponseStatus
from commerce.execution import ExecutionResult, ExecutionStatus
from commerce.integration import (
    CommerceIntegrationResult,
    CommerceIntegrationStatus,
    resolve_and_run_commerce,
)
from commerce.pipeline import (
    PIPELINE_FAILURE_CODES,
    CommercePipelineRequest,
    CommercePipelineResult,
    CommercePipelineStatus,
)
from commerce.signals import CommerceSignals
from commerce.state import CommerceResolutionStatus, CommerceStateRequest
from db import fangate as fdb
from db import postgres

pytestmark = [pytest.mark.unit]

INTEGRATION_SOURCE = Path("commerce/integration.py").read_text(encoding="utf-8")
INTEGRATION_TREE = ast.parse(INTEGRATION_SOURCE)

# ==============================================================================
# helpers
# ==============================================================================

USER_ID = 42
CREATOR_ID = 7
PRODUCT_ID = 10

_USER_ROW = {"id": USER_ID, "username": "fan", "first_name": "Fan", "is_blocked": False}
_CREATOR_ROW = {"id": CREATOR_ID, "name": "Creator", "display_name": None}
_PRODUCT_ROW = {
    "id": PRODUCT_ID,
    "creator_id": CREATOR_ID,
    "product_type": "video",
    "title": "VIP Video Bundle",
    "preview_url": "https://f.test/pv",
    "price_minor": 4400,
    "in_collection": True,
    "sales_url": "https://f.test/p/10",
    "is_accessible": True,
    "is_verif_age": False,
    "is_adult_content": False,
    "is_downloadable": True,
}
_OFFER_ROW = {
    "id": 101,
    "creator_id": CREATOR_ID,
    "user_id": USER_ID,
    "product_id": PRODUCT_ID,
    "link": "https://f.test/o/101",
    "price_minor": 4400,
    "currency": "USD",
    "state": "pending",
    "created_by": "commerce_pipeline",
}

_MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello!"},
]


def _patch(monkeypatch, **overrides):
    defaults = {
        "get_user": _USER_ROW,
        "is_user_auto_reply_excluded": False,
        "get_creator": _CREATOR_ROW,
        "get_dropfans_integration": {"status": "active"},
        "list_offers_for_user": [],
        "get_fangate_product": None,
        "find_pending_offer_for_product": None,
        "has_purchased_product": False,
        "get_user_persona": None,
    }
    values = {**defaults}
    for name, value in overrides.items():
        if name in defaults:
            values[name] = value
    for name, value in values.items():
        if name == "get_dropfans_integration":
            from db import dropfans as db_dropfans
            if isinstance(value, AsyncMock) or callable(value):
                monkeypatch.setattr(db_dropfans, "get_dropfans_integration", value)
            else:
                monkeypatch.setattr(db_dropfans, "get_dropfans_integration", AsyncMock(return_value=value))
        else:
            module = {
                "get_user": postgres,
                "is_user_auto_reply_excluded": postgres,
                "get_user_persona": postgres,
                "get_creator": fdb,
                "get_fangate_product": fdb,
                "list_offers_for_user": cdao,
                "find_pending_offer_for_product": cdao,
                "has_purchased_product": cdao,
            }[name]
            if isinstance(value, AsyncMock) or callable(value):
                monkeypatch.setattr(module, name, value)
            else:
                monkeypatch.setattr(module, name, AsyncMock(return_value=value))


def _request(**overrides):
    data = {"user_id": USER_ID, "creator_id": CREATOR_ID}
    data.update(overrides)
    return CommerceStateRequest(**data)


def _pipeline_result(**overrides):
    data = {
        "status": CommercePipelineStatus.COMPLETED,
        "user_id": USER_ID,
        "creator_id": CREATOR_ID,
    }
    data.update(overrides)
    return CommercePipelineResult(**data)


def _capture_pipeline(monkeypatch, capture, result=None):
    """Replace run_commerce_pipeline with a recording fake (called once)."""

    async def _fake_run(pipeline_request, signals=None):
        capture.append(pipeline_request)
        return result if result is not None else _pipeline_result()

    monkeypatch.setattr("commerce.pipeline.run_commerce_pipeline", _fake_run)


async def _run(monkeypatch, request=None, policy=None, **patch_overrides):
    if request is None:
        product_id = patch_overrides.pop("product_id", None)
        request = _request(**({"product_id": product_id} if product_id is not None else {}))
    else:
        product_id = request.product_id
    if product_id is not None and "get_fangate_product" not in patch_overrides:
        patch_overrides["get_fangate_product"] = _PRODUCT_ROW
    _patch(monkeypatch, **patch_overrides)
    return await resolve_and_run_commerce(request=request, policy=policy)


# ==============================================================================
# GROUP A — composition surface
# ==============================================================================


class TestCompositionSurface:
    def test_api_is_keyword_only(self):
        params = inspect.signature(resolve_and_run_commerce).parameters
        assert list(params) == ["request", "policy", "signals"]
        assert params["request"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["policy"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["signals"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["policy"].default is None
        assert params["signals"].default is None
        assert inspect.iscoroutinefunction(resolve_and_run_commerce)

    def test_status_set_mirrors_resolution_1_to_1(self):
        statuses = {s.value for s in CommerceIntegrationStatus}
        expected = {
            "completed",
            "failed",
            "eligibility_unavailable",
            *{s.value for s in CommerceResolutionStatus if s is not CommerceResolutionStatus.READY},
        }
        assert statuses == expected

    def test_extras_rejected_on_envelope(self):
        with pytest.raises(ValueError):
            CommerceIntegrationResult(status=CommerceIntegrationStatus.COMPLETED, invented=1)

    def test_result_field_is_optional(self):
        result = CommerceIntegrationResult(status=CommerceIntegrationStatus.STATE_UNAVAILABLE)
        assert result.result is None
        assert result.failure_code is None


# ==============================================================================
# GROUP B — full state mapping
# ==============================================================================


class TestFullStateMapping:
    @pytest.mark.asyncio
    async def test_every_resolved_field_reaches_the_pipeline(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(monkeypatch, request=_request(product_id=PRODUCT_ID, messages=_MESSAGES))
        captured = capture[0]
        assert captured.user_id == USER_ID
        assert captured.creator_id == CREATOR_ID
        assert captured.messages == _MESSAGES
        assert captured.currency is None
        assert captured.persona is None
        assert captured.eligibility.allowed is True
        assert captured.product_identity == ProductIdentity(
            product_id=PRODUCT_ID, title="VIP Video Bundle", available=True
        )
        assert captured.product_state == ProductCommerceState(
            price_minor=4400,
            sales_url="https://f.test/p/10",
            is_accessible=True,
            age_verification_required=False,
        )
        assert captured.previous_offer_status is None
        assert captured.has_active_offer is False
        assert captured.has_relevant_product is True
        assert captured.creator_sales_enabled is True
        assert captured.relationship_score is None
        assert captured.messages_since_last_offer == 0
        assert captured.messages_since_last_purchase == 0
        assert captured.hours_since_last_offer is None
        assert captured.hours_since_last_purchase is None
        assert captured.recent_offer_count == 0
        assert captured.recent_purchase_count == 0
        assert captured.recent_sales_attempt_count == 0

    @pytest.mark.asyncio
    async def test_request_owned_fields_transported_verbatim(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        messages = [
            {"role": "user", "content": "what does this cost?"},
            {"role": "assistant", "content": "let me check!"},
        ]
        request = _request(
            product_id=PRODUCT_ID,
            messages=messages,
            currency="EUR",
            persona="You are a friendly creator.",
        )
        await _run(monkeypatch, request=request)
        captured = capture[0]
        assert captured.messages == messages
        assert captured.currency == "EUR"
        assert captured.persona == "You are a friendly creator."

    @pytest.mark.asyncio
    async def test_persona_falls_back_to_stored_user_persona(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        _patch(monkeypatch, get_user_persona="Stored persona text")
        await resolve_and_run_commerce(request=_request())
        assert capture[0].persona == "Stored persona text"

    @pytest.mark.asyncio
    async def test_previous_offer_status_and_history(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        history = [dict(_OFFER_ROW, state="purchased")]
        await _run(monkeypatch, product_id=PRODUCT_ID, list_offers_for_user=history)
        captured = capture[0]
        assert captured.previous_offer_status == "purchased"
        assert captured.creator_id == CREATOR_ID

    @pytest.mark.asyncio
    async def test_explicit_creator_supplied_is_verified_and_kept(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(monkeypatch, request=_request(creator_id=CREATOR_ID), product_id=PRODUCT_ID)
        assert capture[0].creator_id == CREATOR_ID


# ==============================================================================
# GROUP C — creator resolution cases
# ==============================================================================


class TestCreatorResolution:
    @pytest.mark.asyncio
    async def test_explicit_unknown_creator_is_unavailable(self, monkeypatch):
        _patch(monkeypatch, get_creator=None)
        result = await resolve_and_run_commerce(request=_request(creator_id=999), policy=None)
        assert result.status is CommerceIntegrationStatus.CREATOR_CONTEXT_UNAVAILABLE
        assert result.result is None

    @pytest.mark.asyncio
    async def test_missing_creator_history_is_unavailable(self, monkeypatch):
        result = await _run(monkeypatch, request=_request(creator_id=None), list_offers_for_user=[])
        assert result.status is CommerceIntegrationStatus.CREATOR_CONTEXT_UNAVAILABLE
        assert result.result is None

    @pytest.mark.asyncio
    async def test_offer_history_derives_creator_and_status(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        history = [_OFFER_ROW]
        result = await _run(
            monkeypatch,
            request=_request(creator_id=None),
            product_id=PRODUCT_ID,
            list_offers_for_user=history,
        )
        assert result.status is CommerceIntegrationStatus.COMPLETED
        assert capture[0].creator_id == CREATOR_ID
        assert capture[0].previous_offer_status == "pending"

    @pytest.mark.asyncio
    async def test_creator_row_missing_with_history_is_unavailable(self, monkeypatch):
        _patch(monkeypatch, get_creator=None, list_offers_for_user=[_OFFER_ROW])
        result = await resolve_and_run_commerce(request=_request(creator_id=None), policy=None)
        assert result.status is CommerceIntegrationStatus.CREATOR_CONTEXT_UNAVAILABLE


# ==============================================================================
# GROUP D — product resolution cases
# ==============================================================================


class TestProductResolution:
    @pytest.mark.asyncio
    async def test_missing_product_id_means_no_product_state(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        result = await _run(monkeypatch, product_id=None)
        assert result.status is CommerceIntegrationStatus.COMPLETED
        captured = capture[0]
        assert captured.product_identity is None
        assert captured.product_state is None
        assert captured.has_relevant_product is False

    @pytest.mark.asyncio
    async def test_unresolvable_product_id_is_unavailable(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        result = await _run(
            monkeypatch,
            request=_request(product_id=999),
            get_fangate_product=None,
        )
        assert result.status is CommerceIntegrationStatus.PRODUCT_UNAVAILABLE
        assert result.result is None
        assert not capture

    @pytest.mark.asyncio
    async def test_product_comes_only_from_local_mirror(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        row = dict(_PRODUCT_ROW, title="Mirror Title", price_minor=1100)
        await _run(monkeypatch, product_id=PRODUCT_ID, get_fangate_product=row)
        captured = capture[0]
        assert captured.product_identity.title == "Mirror Title"
        assert captured.product_state.price_minor == 1100

    @pytest.mark.asyncio
    async def test_product_never_selected_from_history(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(monkeypatch, product_id=None, list_offers_for_user=[_OFFER_ROW])
        assert capture[0].product_identity is None
        assert capture[0].product_state is None


# ==============================================================================
# GROUP E — eligibility engine integration
# ==============================================================================


class TestEligibility:
    @pytest.mark.asyncio
    async def test_blocked_user_denied(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        result = await _run(
            monkeypatch, product_id=PRODUCT_ID, get_user=dict(_USER_ROW, is_blocked=True)
        )
        assert result.status is CommerceIntegrationStatus.COMPLETED
        assert capture[0].eligibility.allowed is False
        assert capture[0].eligibility.denial_reason == "user_blocked"

    @pytest.mark.asyncio
    async def test_opted_out_user_denied(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(monkeypatch, product_id=PRODUCT_ID, is_user_auto_reply_excluded=True)
        assert capture[0].eligibility.denial_reason == "user_opted_out"

    @pytest.mark.asyncio
    async def test_inactive_creator_denied(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_dropfans_integration={"status": "error"},
        )
        denied = capture[0].eligibility
        assert denied.allowed is False
        assert denied.denial_reason == "creator_not_ready"

    @pytest.mark.asyncio
    async def test_inaccessible_product_denied(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        row = dict(_PRODUCT_ROW, is_accessible=False)
        await _run(monkeypatch, product_id=PRODUCT_ID, get_fangate_product=row)
        assert capture[0].eligibility.denial_reason == "product_unavailable"

    @pytest.mark.asyncio
    async def test_missing_sales_url_denied(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        row = dict(_PRODUCT_ROW, sales_url=None)
        await _run(monkeypatch, product_id=PRODUCT_ID, get_fangate_product=row)
        assert capture[0].eligibility.denial_reason == "product_missing_sales_url"

    @pytest.mark.asyncio
    async def test_already_purchased_denied(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(
            monkeypatch,
            product_id=PRODUCT_ID,
            has_purchased_product=True,
        )
        assert capture[0].eligibility.denial_reason == "already_purchased"

    @pytest.mark.asyncio
    async def test_pending_offer_denied(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(
            monkeypatch,
            product_id=PRODUCT_ID,
            find_pending_offer_for_product=_OFFER_ROW,
        )
        denied = capture[0].eligibility
        assert denied.allowed is False
        assert denied.denial_reason == "offer_exists"
        assert capture[0].has_active_offer is True

    @pytest.mark.asyncio
    async def test_age_verification_product_denied(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        row = dict(_PRODUCT_ROW, is_verif_age=True)
        await _run(monkeypatch, product_id=PRODUCT_ID, get_fangate_product=row)
        denied = capture[0].eligibility
        assert denied.allowed is False
        assert denied.denial_reason == "age_verification_required"
        assert capture[0].product_state.age_verification_required is True


# ==============================================================================
# GROUP F — purchases and offers
# ==============================================================================


class TestPurchasesAndOffers:
    @pytest.mark.asyncio
    async def test_previous_offer_status_passed_through(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        history = [dict(_OFFER_ROW, state="expired")]
        await _run(monkeypatch, product_id=PRODUCT_ID, list_offers_for_user=history)
        assert capture[0].previous_offer_status == "expired"

    @pytest.mark.asyncio
    async def test_pending_offer_marks_active_offer(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(
            monkeypatch,
            product_id=PRODUCT_ID,
            find_pending_offer_for_product=_OFFER_ROW,
        )
        assert capture[0].has_active_offer is True

    @pytest.mark.asyncio
    async def test_no_request_product_keeps_offer_state_neutral(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(
            monkeypatch,
            product_id=None,
            find_pending_offer_for_product=_OFFER_ROW,
            has_purchased_product=True,
        )
        captured = capture[0]
        assert captured.has_active_offer is False
        assert captured.eligibility.allowed is False


# ==============================================================================
# GROUP G — creator sales flags
# ==============================================================================


class TestCreatorSalesFlags:
    @pytest.mark.asyncio
    async def test_active_integration_enables_sales(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(monkeypatch, product_id=PRODUCT_ID)
        assert capture[0].creator_sales_enabled is True

    @pytest.mark.asyncio
    async def test_error_integration_disables_sales(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_dropfans_integration={"status": "error"},
        )
        assert capture[0].creator_sales_enabled is False

    @pytest.mark.asyncio
    async def test_disconnected_integration_disables_sales(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_dropfans_integration={"status": "disconnected"},
        )
        assert capture[0].creator_sales_enabled is False

    @pytest.mark.asyncio
    async def test_missing_integration_disables_sales(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(monkeypatch, product_id=PRODUCT_ID, get_dropfans_integration=None)
        assert capture[0].creator_sales_enabled is False


# ==============================================================================
# GROUP H — conversation-text authority
# ==============================================================================


class TestConversationTextNeverAuthoritative:
    @pytest.mark.asyncio
    async def test_price_mention_in_text_never_becomes_price(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        _patch(monkeypatch, get_fangate_product=_PRODUCT_ROW)
        request = _request(
            product_id=PRODUCT_ID,
            messages=[{"role": "user", "content": "I saw it for $50, is that right?"}],
        )
        await resolve_and_run_commerce(request=request)
        assert capture[0].product_state.price_minor == 4400
        assert capture[0].product_state.sales_url == "https://f.test/p/10"

    @pytest.mark.asyncio
    async def test_sales_url_in_text_never_becomes_url(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        _patch(monkeypatch)
        request = _request(
            messages=[{"role": "user", "content": "Is https://shop.evil.example/x legit?"}]
        )
        await resolve_and_run_commerce(request=request)
        assert capture[0].product_identity is None
        assert capture[0].product_state is None

    @pytest.mark.asyncio
    async def test_product_name_in_text_never_becomes_identity(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        _patch(monkeypatch)
        request = _request(
            messages=[{"role": "user", "content": "Do you have a VIP Video Bundle?"}]
        )
        await resolve_and_run_commerce(request=request)
        assert capture[0].product_identity is None

    @pytest.mark.asyncio
    async def test_text_never_overrides_mirror_state(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        _patch(monkeypatch, get_fangate_product=_PRODUCT_ROW)
        request = _request(
            product_id=PRODUCT_ID,
            messages=[{"role": "user", "content": "It is free, right? No, $2. I want $1."}],
        )
        await resolve_and_run_commerce(request=request)
        assert capture[0].product_state.price_minor == 4400
        assert capture[0].eligibility.allowed is True

    @pytest.mark.asyncio
    async def test_messages_transported_verbatim_for_pipeline(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        _patch(monkeypatch)
        messages = [{"role": "user", "content": "$50?"}]
        await resolve_and_run_commerce(request=_request(messages=messages))
        assert capture[0].messages == messages


# ==============================================================================
# GROUP I — pipeline invocation
# ==============================================================================


class TestPipelineInvocation:
    @pytest.mark.asyncio
    async def test_called_exactly_once_on_success(self, monkeypatch):
        calls = []
        _capture_pipeline(monkeypatch, calls)
        await _run(monkeypatch, product_id=PRODUCT_ID)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_receives_resolved_pipeline_request(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(monkeypatch, product_id=PRODUCT_ID)
        assert isinstance(capture[0], CommercePipelineRequest)

    @pytest.mark.asyncio
    async def test_policy_override_reaches_pipeline(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        policy = CommerceDecisionPolicy(max_offers_per_24h=5)
        await _run(monkeypatch, request=_request(product_id=PRODUCT_ID), policy=policy)
        assert capture[0].policy is policy

    @pytest.mark.asyncio
    async def test_no_policy_override_keeps_default(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        await _run(monkeypatch, product_id=PRODUCT_ID)
        assert capture[0].policy is None

    @pytest.mark.asyncio
    async def test_pipeline_result_wrapped_with_completed_status(self, monkeypatch):
        capture = []
        canned = _pipeline_result(status=CommercePipelineStatus.COMPLETED)
        _capture_pipeline(monkeypatch, capture, result=canned)
        result = await _run(monkeypatch, product_id=PRODUCT_ID)
        assert result.status is CommerceIntegrationStatus.COMPLETED
        assert result.result is canned
        assert result.failure_code is None

    @pytest.mark.asyncio
    async def test_called_exactly_once_even_when_eligibility_denied(self, monkeypatch):
        calls = []
        _capture_pipeline(monkeypatch, calls)
        await _run(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_user=dict(_USER_ROW, is_blocked=True),
        )
        assert len(calls) == 1


# ==============================================================================
# GROUP J — state failure never calls the pipeline
# ==============================================================================


class TestStateFailureIsolation:
    def _pipeline_must_not_run(self, monkeypatch) -> list:
        calls = []

        async def _fail(*_args):
            calls.append(1)
            raise AssertionError("pipeline must not be called on failure")

        monkeypatch.setattr("commerce.pipeline.run_commerce_pipeline", _fail)
        return calls

    @pytest.mark.asyncio
    async def test_state_unavailable_never_calls_pipeline(self, monkeypatch):
        calls = self._pipeline_must_not_run(monkeypatch)
        result = await _run(monkeypatch, get_user=None)
        assert not calls
        assert result.status is CommerceIntegrationStatus.STATE_UNAVAILABLE
        assert result.result is None
        assert result.failure_code is None

    @pytest.mark.asyncio
    async def test_resolution_failed_never_calls_pipeline(self, monkeypatch):
        calls = self._pipeline_must_not_run(monkeypatch)

        async def _boom(_user_id):
            raise RuntimeError("db down")

        monkeypatch.setattr(postgres, "get_user", _boom)
        result = await resolve_and_run_commerce(request=_request())
        assert not calls
        assert result.status is CommerceIntegrationStatus.RESOLUTION_FAILED
        assert result.result is None

    @pytest.mark.asyncio
    async def test_product_unavailable_never_calls_pipeline(self, monkeypatch):
        calls = self._pipeline_must_not_run(monkeypatch)
        result = await _run(
            monkeypatch,
            request=_request(product_id=999),
            get_fangate_product=None,
        )
        assert not calls
        assert result.status is CommerceIntegrationStatus.PRODUCT_UNAVAILABLE
        assert result.result is None

    @pytest.mark.asyncio
    async def test_creator_unavailable_never_calls_pipeline(self, monkeypatch):
        calls = self._pipeline_must_not_run(monkeypatch)
        result = await _run(monkeypatch, get_creator=None)
        assert not calls
        assert result.status is CommerceIntegrationStatus.CREATOR_CONTEXT_UNAVAILABLE
        assert result.result is None

    @pytest.mark.asyncio
    async def test_raised_resolver_never_calls_pipeline_and_is_failed(self, monkeypatch):
        calls = self._pipeline_must_not_run(monkeypatch)

        async def _boom(_request):
            raise RuntimeError("db down")

        monkeypatch.setattr("commerce.integration.resolve_commerce_state", _boom)
        result = await resolve_and_run_commerce(request=_request())
        assert not calls
        assert result.status is CommerceIntegrationStatus.FAILED
        assert result.failure_code == "unexpected_error"
        assert result.result is None

    @pytest.mark.asyncio
    async def test_raised_pipeline_is_failed_without_internals(self, monkeypatch):
        async def _boom(_request, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("commerce.pipeline.run_commerce_pipeline", _boom)
        _patch(monkeypatch, get_fangate_product=_PRODUCT_ROW)
        result = await resolve_and_run_commerce(request=_request(product_id=PRODUCT_ID))
        assert result.status is CommerceIntegrationStatus.FAILED
        assert result.failure_code == "unexpected_error"
        assert result.result is None

    @pytest.mark.asyncio
    async def test_silent_surface_contains_no_traceback_or_payload(self, monkeypatch):
        async def _boom(_request, **kwargs):
            raise RuntimeError("SecretApiKey leaked")

        monkeypatch.setattr("commerce.pipeline.run_commerce_pipeline", _boom)
        _patch(monkeypatch, get_fangate_product=_PRODUCT_ROW)
        result = await resolve_and_run_commerce(request=_request(product_id=PRODUCT_ID))
        surface = result.model_dump()
        assert set(surface) == {"status", "result", "failure_code"}
        assert "Traceback" not in result.model_dump_json()
        assert "SecretApiKey" not in result.model_dump_json()


# ==============================================================================
# GROUP K — execution authority proof
# ==============================================================================


class TestExecutionAuthority:
    @pytest.mark.asyncio
    async def test_execution_reaches_execute_ppv_exactly_once(self, monkeypatch):
        execute_calls = []

        async def _fake_execute(*args, **kwargs):
            execute_calls.append((args, kwargs))
            return ExecutionResult(status=ExecutionStatus.EXECUTED, offer_id=77)

        async def _fake_signals(_conversation):
            return CommerceSignals.low_information().model_copy(
                update={"explicit_purchase_request": True}
            )

        async def _fake_generate(_input_):
            return CommerceResponse(
                status=CommerceResponseStatus.GENERATED, text="here is your offer"
            )

        monkeypatch.setattr("commerce.pipeline.extract_commerce_signals", _fake_signals)
        monkeypatch.setattr("commerce.orchestrator.execute_ppv", _fake_execute)
        monkeypatch.setattr("commerce.pipeline.generate_commerce_response", _fake_generate)
        _patch(monkeypatch, get_fangate_product=_PRODUCT_ROW)

        result = await resolve_and_run_commerce(request=_request(product_id=PRODUCT_ID))
        assert result.status is CommerceIntegrationStatus.COMPLETED
        assert result.result.status is CommercePipelineStatus.COMPLETED
        assert result.result.execution_result.status is ExecutionStatus.EXECUTED
        assert result.result.execution_result.offer_id == 77
        assert len(execute_calls) == 1

    @pytest.mark.asyncio
    async def test_explicit_purchase_without_product_never_executes(self, monkeypatch):
        execute_calls = []

        async def _fake_execute(*args, **kwargs):
            execute_calls.append(1)
            return ExecutionResult(status=ExecutionStatus.EXECUTED, offer_id=77)

        async def _fake_signals(_conversation):
            return CommerceSignals.low_information().model_copy(
                update={"explicit_purchase_request": True}
            )

        async def _fake_generate(_input_):
            return CommerceResponse(
                status=CommerceResponseStatus.GENERATED, text="no product though"
            )

        monkeypatch.setattr("commerce.pipeline.extract_commerce_signals", _fake_signals)
        monkeypatch.setattr("commerce.orchestrator.execute_ppv", _fake_execute)
        monkeypatch.setattr("commerce.pipeline.generate_commerce_response", _fake_generate)
        _patch(monkeypatch)

        result = await resolve_and_run_commerce(request=_request(product_id=None))
        assert result.status is CommerceIntegrationStatus.COMPLETED
        assert result.result.execution_result is None
        assert not execute_calls
        assert result.result.decision.action.value == "no_offer"

    @pytest.mark.asyncio
    async def test_no_purchase_intent_never_executes(self, monkeypatch):
        execute_calls = []

        async def _fake_execute(*args, **kwargs):
            execute_calls.append(1)
            return ExecutionResult(status=ExecutionStatus.EXECUTED, offer_id=77)

        async def _fake_signals(_conversation):
            return CommerceSignals.low_information()

        async def _fake_generate(_input_):
            return CommerceResponse(status=CommerceResponseStatus.GENERATED, text="happy to help")

        monkeypatch.setattr("commerce.pipeline.extract_commerce_signals", _fake_signals)
        monkeypatch.setattr("commerce.orchestrator.execute_ppv", _fake_execute)
        monkeypatch.setattr("commerce.pipeline.generate_commerce_response", _fake_generate)
        _patch(monkeypatch, get_fangate_product=_PRODUCT_ROW)

        result = await resolve_and_run_commerce(request=_request(product_id=PRODUCT_ID))
        assert result.result.execution_result is None
        assert not execute_calls


# ==============================================================================
# GROUP L — determinism
# ==============================================================================


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_identical_reads_produce_identical_envelopes(self, monkeypatch):
        first_capture, second_capture = [], []
        _capture_pipeline(monkeypatch, first_capture)
        first = await _run(monkeypatch, product_id=PRODUCT_ID)
        first_capture_payload = first_capture[0].model_dump()

        _patch(monkeypatch, get_fangate_product=_PRODUCT_ROW)
        second_capture = []
        _capture_pipeline(monkeypatch, second_capture)
        second = await _run(monkeypatch, product_id=PRODUCT_ID)
        second_capture_payload = second_capture[0].model_dump()

        assert first.status is second.status
        assert first_capture_payload == second_capture_payload
        assert first.model_dump() == second.model_dump()

    @pytest.mark.asyncio
    async def test_no_clock_or_randomness_in_integration(self, monkeypatch):
        for banned in ("datetime", "time", "random"):
            assert banned + "." not in INTEGRATION_SOURCE.replace("    ", " ")
        assert "time.time" not in INTEGRATION_SOURCE
        assert "random." not in INTEGRATION_SOURCE


# ==============================================================================
# GROUP M — security surface
# ==============================================================================


class TestSecuritySurface:
    def _import_names(self):
        names = set()
        for node in ast.walk(INTEGRATION_TREE):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    def _call_names(self):
        names = set()
        for node in ast.walk(INTEGRATION_TREE):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    names.add(node.func.attr)
        return names

    def test_no_forbidden_runtime_imports(self):
        allowed = {"logging", "enum", "typing", "pydantic", "commerce"}
        assert self._import_names() <= allowed

    def test_no_llm_or_fangate_client_imports(self):
        imports = self._import_names()
        assert not (
            imports
            & {
                "workers",
                "event_bus",
                "redis",
                "telegram",
                "httpx",
                "requests",
                "asyncpg",
                "websockets",
                "google",
                "deepseek",
            }
        )

    def test_no_execute_ppv_call_in_integration(self):
        assert "execute_ppv" not in self._call_names()

    def test_no_pipeline_part_calls(self):
        for banned in (
            "orchestrate_commerce",
            "decide_from_signals",
            "build_strategy",
            "generate_commerce_response",
            "extract_commerce_signals",
            "evaluate_ppv_eligibility",
            "verify_product",
        ):
            assert banned not in self._call_names()
            assert banned + "(" not in INTEGRATION_SOURCE

    def test_no_credential_or_secret_fields_on_envelope(self):
        for banned in (
            "api_key",
            "apiKey",
            "token",
            "secret",
            "password",
            "credential",
            "ciphertext",
            "authorization",
        ):
            assert banned not in CommerceIntegrationResult.model_fields

    def test_envelope_fields_are_closed(self):
        assert set(CommerceIntegrationResult.model_fields) == {"status", "result", "failure_code"}

    def test_no_live_http_surface(self):
        for banned in ("httpx", "aiohttp", "open(", "urllib", "socket"):
            assert banned not in INTEGRATION_SOURCE

    def test_no_datetime_or_random_calls(self):
        for banned in ("datetime(", "time(", "random("):
            assert banned not in INTEGRATION_SOURCE


# ==============================================================================
# GROUP N — result envelope
# ==============================================================================


class TestResultEnvelope:
    def test_unknown_status_rejected(self):
        with pytest.raises(ValueError):
            CommerceIntegrationResult(status="made_up")

    def test_result_must_be_pipeline_result(self):
        with pytest.raises(ValueError):
            CommerceIntegrationResult(
                status=CommerceIntegrationStatus.COMPLETED, result="not a pipeline result"
            )

    def test_unknown_failure_code_rejected(self):
        with pytest.raises(ValueError):
            CommerceIntegrationResult(
                status=CommerceIntegrationStatus.COMPLETED, failure_code="made_up"
            )

    def test_known_failure_code_accepted(self):
        for code in PIPELINE_FAILURE_CODES:
            result = CommerceIntegrationResult(
                status=CommerceIntegrationStatus.COMPLETED, failure_code=code
            )
            assert result.failure_code == code

    @pytest.mark.asyncio
    async def test_pipeline_execution_failure_preserved_verbatim(self, monkeypatch):
        canned = _pipeline_result(
            status=CommercePipelineStatus.EXECUTION_FAILED,
            failure_code="execution_failure",
            execution_result=ExecutionResult(
                status=ExecutionStatus.PROVIDER_ERROR, denial_reason="credential_unavailable"
            ),
            response=CommerceResponse(
                status=CommerceResponseStatus.GENERATED, text="sorry, something went wrong"
            ),
        )
        capture = []
        _capture_pipeline(monkeypatch, capture, result=canned)
        result = await _run(monkeypatch, product_id=PRODUCT_ID)
        assert result.status is CommerceIntegrationStatus.COMPLETED
        assert result.failure_code == "execution_failure"
        assert result.result is canned
        assert result.result.execution_result.status is ExecutionStatus.PROVIDER_ERROR
        assert result.result.response.text == "sorry, something went wrong"

    @pytest.mark.asyncio
    async def test_envelope_dump_never_exposes_conversation(self, monkeypatch):
        capture = []
        _capture_pipeline(monkeypatch, capture)
        result = await _run(
            monkeypatch,
            request=_request(messages=[{"role": "user", "content": "secret payload"}]),
        )
        assert set(result.model_dump()) == {"status", "result", "failure_code"}
        assert "secret payload" not in result.model_dump_json()
        assert "messages" not in result.model_dump()
