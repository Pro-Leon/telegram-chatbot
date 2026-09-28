"""Phase 5.4 Chk 6C tests Ã¢â‚¬â€ read-only commerce application-state resolution.

Groups:

A: request contract (strict, rejection-based, closed types).
B: user state (missing user, blocked, opted-out, DB failure isolation).
C: creator resolution (explicit verify, offer-history derivation, unavailable).
D: integration status semantics (active only -> creator sales enabled).
E: product resolution (explicit only, creator-scoped mirror, unavailable).
F: purchases and offers (already_purchased, active offer, previous status).
G: eligibility engine integration (every denial reason via the resolver).
H: determinism (identical reads -> identical resolutions, no sorting).
I: safety surfaces (imports, no write calls, no clock, closed status set).
J: output surface (READY always a request, credentials never present).

The resolver is exercised with the REAL eligibility engine; only the read
functions (db.postgres / db.fangate / commerce.dao) are mocked.
"""

import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from commerce import dao as cdao
from commerce.context import ProductCommerceState, ProductIdentity
from commerce.eligibility import (
    OfferContext,
    ProductEligibilityState,
    UserEligibilityState,
    evaluate_ppv_eligibility,
)
from commerce.pipeline import (
    PIPELINE_CURRENCY_MAX,
    PIPELINE_MAX_MESSAGES,
    PIPELINE_PERSONA_MAX,
    CommercePipelineRequest,
)
from commerce.state import (
    CommerceResolutionStatus,
    CommerceStateRequest,
    _resolve_creator_relationship,
    resolve_commerce_state,
)
from db import fangate as fdb
from db import postgres

pytestmark = [pytest.mark.unit]


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# helpers
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

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


async def _resolve(monkeypatch, request=None, **patch_overrides):
    product_id = patch_overrides.pop("product_id", None)
    _patch(monkeypatch, **patch_overrides)
    if request is None:
        request = _request(**({"product_id": product_id} if product_id is not None else {}))
    return await resolve_commerce_state(request)


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# GROUP A Ã¢â‚¬â€ request contract
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


class TestRequestContract:
    def test_user_id_must_be_genuine_positive_int(self):
        for bad in (0, -5, "7", 7.5, True):
            with pytest.raises(ValueError):
                CommerceStateRequest(user_id=bad, creator_id=CREATOR_ID)

    def test_creator_id_and_product_id_must_be_genuine_ints(self):
        for field in ("creator_id", "product_id"):
            for bad in ("7", 0, -1, 3.5):
                with pytest.raises(ValueError):
                    CommerceStateRequest(user_id=USER_ID, **{field: bad})

    def test_extras_rejected(self):
        with pytest.raises(ValueError):
            CommerceStateRequest(user_id=USER_ID, creator_id=CREATOR_ID, invented=1)

    def test_messages_shape_enforced(self):
        with pytest.raises(ValueError):
            CommerceStateRequest(user_id=USER_ID, messages=[{"role": "admin", "content": "x"}])
        with pytest.raises(ValueError):
            CommerceStateRequest(user_id=USER_ID, messages=[{"role": "user", "content": 5}])

    def test_messages_bounded_by_pipeline_cap(self):
        too_many = [{"role": "user", "content": "m"} for _ in range(PIPELINE_MAX_MESSAGES + 1)]
        with pytest.raises(ValueError):
            CommerceStateRequest(user_id=USER_ID, messages=too_many)

    def test_currency_bounded(self):
        with pytest.raises(ValueError):
            CommerceStateRequest(user_id=USER_ID, creator_id=CREATOR_ID, currency="US")
        too_long = "X" * (PIPELINE_CURRENCY_MAX + 1)
        with pytest.raises(ValueError):
            CommerceStateRequest(user_id=USER_ID, creator_id=CREATOR_ID, currency=too_long)

    def test_persona_bounded(self):
        with pytest.raises(ValueError):
            CommerceStateRequest(
                user_id=USER_ID, creator_id=CREATOR_ID, persona="p" * (PIPELINE_PERSONA_MAX + 1)
            )

    def test_resolution_status_closed_set(self):
        assert {s.value for s in CommerceResolutionStatus} == {
            "ready",
            "creator_context_unavailable",
            "state_unavailable",
            "product_unavailable",
            "resolution_failed",
        }


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# GROUP B Ã¢â‚¬â€ user state
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


class TestUserState:
    @pytest.mark.asyncio
    async def test_missing_user_is_state_unavailable(self, monkeypatch):
        resolution = await _resolve(monkeypatch, get_user=None)
        assert resolution.status is CommerceResolutionStatus.STATE_UNAVAILABLE
        assert resolution.request is None

    @pytest.mark.asyncio
    async def test_present_user_resolves(self, monkeypatch):
        resolution = await _resolve(monkeypatch)
        assert resolution.status is CommerceResolutionStatus.READY
        assert resolution.request.user_id == USER_ID

    @pytest.mark.asyncio
    async def test_blocked_user_denied_by_eligibility(self, monkeypatch):
        resolution = await _resolve(monkeypatch, get_user={**_USER_ROW, "is_blocked": True})
        assert resolution.status is CommerceResolutionStatus.READY
        assert resolution.request.eligibility.allowed is False
        assert resolution.request.eligibility.denial_reason == "user_blocked"

    @pytest.mark.asyncio
    async def test_opted_out_user_denied_by_eligibility(self, monkeypatch):
        resolution = await _resolve(monkeypatch, is_user_auto_reply_excluded=True)
        assert resolution.status is CommerceResolutionStatus.READY
        assert resolution.request.eligibility.denial_reason == "user_opted_out"

    @pytest.mark.asyncio
    async def test_opted_out_does_not_manufacture_sale(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
            is_user_auto_reply_excluded=True,
        )
        assert resolution.request.eligibility.allowed is False
        assert resolution.request.eligibility.denial_reason == "user_opted_out"

    @pytest.mark.asyncio
    async def test_get_user_failure_is_resolution_failed(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            get_user=AsyncMock(side_effect=RuntimeError("db down")),
        )
        assert resolution.status is CommerceResolutionStatus.RESOLUTION_FAILED
        assert resolution.request is None

    @pytest.mark.asyncio
    async def test_read_failure_never_fabricates_request(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            get_dropfans_integration=AsyncMock(side_effect=RuntimeError("db down")),
        )
        # Dropfans lookup failure: resolution succeeds but creator_sales_enabled=False
        assert resolution.status is CommerceResolutionStatus.READY
        assert resolution.request.creator_sales_enabled is False


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# GROUP C Ã¢â‚¬â€ creator resolution
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


class TestCreatorResolution:
    @pytest.mark.asyncio
    async def test_explicit_creator_verified(self, monkeypatch):
        resolution = await _resolve(monkeypatch, request=_request(creator_id=CREATOR_ID))
        assert resolution.status is CommerceResolutionStatus.READY
        assert resolution.request.creator_id == CREATOR_ID

    @pytest.mark.asyncio
    async def test_explicit_creator_missing_is_unavailable(self, monkeypatch):
        resolution = await _resolve(monkeypatch, get_creator=None)
        assert resolution.status is CommerceResolutionStatus.CREATOR_CONTEXT_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_no_history_and_no_explicit_creator_is_unavailable(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch, request=_request(creator_id=None), list_offers_for_user=[]
        )
        assert resolution.status is CommerceResolutionStatus.CREATOR_CONTEXT_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_offer_history_derives_creator(self, monkeypatch):
        history = [{**_OFFER_ROW, "creator_id": CREATOR_ID}]
        resolution = await _resolve(
            monkeypatch,
            request=_request(creator_id=None),
            list_offers_for_user=history,
        )
        assert resolution.status is CommerceResolutionStatus.READY
        assert resolution.request.creator_id == CREATOR_ID

    @pytest.mark.asyncio
    async def test_derivation_uses_dao_order_not_python_sorting(self, monkeypatch):
        calls = []

        async def _fake_list(user_id, *, creator_id=None, limit=100, offset=0):
            calls.append((user_id, creator_id, limit, offset))
            return [{**_OFFER_ROW, "creator_id": CREATOR_ID, "state": "declined"}]

        _patch(monkeypatch, list_offers_for_user=_fake_list)
        resolution = await resolve_commerce_state(_request(creator_id=None))
        assert resolution.status is CommerceResolutionStatus.READY
        assert (USER_ID, None, 1, 0) in calls
        assert resolution.request.previous_offer_status == "declined"

    @pytest.mark.asyncio
    async def test_derived_creator_missing_row_is_unavailable(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            request=_request(creator_id=None),
            list_offers_for_user=[_OFFER_ROW],
            get_creator=None,
        )
        assert resolution.status is CommerceResolutionStatus.CREATOR_CONTEXT_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_relationship_helper_returns_none_without_evidence(self, monkeypatch):
        monkeypatch.setattr(cdao, "list_offers_for_user", AsyncMock(return_value=[]))
        creator_id, status = await _resolve_creator_relationship(USER_ID, None)
        assert creator_id is None
        assert status is None

    @pytest.mark.asyncio
    async def test_explicit_creator_history_carries_previous_status(self, monkeypatch):
        history = [{**_OFFER_ROW, "state": "expired"}]
        resolution = await _resolve(
            monkeypatch,
            request=_request(creator_id=CREATOR_ID),
            list_offers_for_user=history,
        )
        assert resolution.request.previous_offer_status == "expired"


# ---------------------------------------------------------------------------
# GROUP D Ã¯Â¿Â½ integration status semantics
# ---------------------------------------------------------------------------


class TestIntegrationStatus:
    @pytest.mark.asyncio
    async def test_active_integration_enables_sales(self, monkeypatch):
        resolution = await _resolve(monkeypatch, get_dropfans_integration={"status": "active"})
        assert resolution.status is CommerceResolutionStatus.READY
        assert resolution.request.creator_sales_enabled is True

    @pytest.mark.asyncio
    async def test_missing_integration_disables_sales(self, monkeypatch):
        resolution = await _resolve(monkeypatch, get_dropfans_integration=None)
        assert resolution.request.creator_sales_enabled is False
        assert resolution.request.eligibility.allowed is False
        assert resolution.request.eligibility.denial_reason == "creator_not_ready"

    @pytest.mark.asyncio
    async def test_error_integration_disables_sales(self, monkeypatch):
        resolution = await _resolve(monkeypatch, get_dropfans_integration={"status": "error"})
        assert resolution.request.creator_sales_enabled is False
        assert resolution.request.eligibility.denial_reason == "creator_not_ready"

    @pytest.mark.asyncio
    async def test_disconnected_integration_disables_sales(self, monkeypatch):
        resolution = await _resolve(monkeypatch, get_dropfans_integration={"status": "disconnected"})
        assert resolution.request.creator_sales_enabled is False
        assert resolution.request.eligibility.denial_reason == "creator_not_ready"

    @pytest.mark.asyncio
    async def test_active_status_is_not_a_credential_surface(self, monkeypatch):
        resolution = await _resolve(monkeypatch)
        dumped = resolution.request.model_dump()
        assert "encrypted_api_key" not in str(dumped)
        assert "webhook_secret" not in str(dumped)


# ---------------------------------------------------------------------------
# GROUP E Ã¯Â¿Â½ product resolution
# ---------------------------------------------------------------------------


class TestProductResolution:
    @pytest.mark.asyncio
    async def test_no_product_id_means_no_product_state(self, monkeypatch):
        resolution = await _resolve(monkeypatch, get_fangate_product=None)
        assert resolution.status is CommerceResolutionStatus.READY
        assert resolution.request.product_identity is None
        assert resolution.request.product_state is None
        assert resolution.request.has_relevant_product is False
        assert resolution.request.eligibility.denial_reason == "product_missing_sales_url"

    @pytest.mark.asyncio
    async def test_explicit_product_resolves_identity_and_state(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
        )
        assert resolution.status is CommerceResolutionStatus.READY
        assert resolution.request.product_identity == ProductIdentity(
            product_id=PRODUCT_ID, title="VIP Video Bundle", available=True
        )
        assert resolution.request.product_state == ProductCommerceState(
            price_minor=4400,
            sales_url="https://f.test/p/10",
            is_accessible=True,
            age_verification_required=False,
        )
        assert resolution.request.has_relevant_product is True
        assert resolution.request.eligibility.allowed is True

    @pytest.mark.asyncio
    async def test_missing_product_is_product_unavailable(self, monkeypatch):
        resolution = await _resolve(monkeypatch, product_id=PRODUCT_ID, get_fangate_product=None)
        assert resolution.status is CommerceResolutionStatus.PRODUCT_UNAVAILABLE
        assert resolution.request is None

    @pytest.mark.asyncio
    async def test_cross_creator_product_is_product_unavailable(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=None,
        )
        assert resolution.status is CommerceResolutionStatus.PRODUCT_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_unpriced_product_is_still_resolved(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product={**_PRODUCT_ROW, "price_minor": None},
        )
        assert resolution.status is CommerceResolutionStatus.READY
        assert resolution.request.product_state.price_minor is None
        assert resolution.request.eligibility.allowed is True

    @pytest.mark.asyncio
    async def test_inaccessible_product_denied_by_eligibility(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product={**_PRODUCT_ROW, "is_accessible": False},
        )
        assert resolution.status is CommerceResolutionStatus.READY
        assert resolution.request.product_identity.available is False
        assert resolution.request.product_state.is_accessible is False
        assert resolution.request.eligibility.denial_reason == "product_unavailable"

    @pytest.mark.asyncio
    async def test_product_without_sales_url_denied(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product={**_PRODUCT_ROW, "sales_url": None},
        )
        assert resolution.request.eligibility.denial_reason == "product_missing_sales_url"

    @pytest.mark.asyncio
    async def test_age_verified_flag_never_invented(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product={**_PRODUCT_ROW, "is_verif_age": True},
        )
        assert resolution.request.product_state.age_verification_required is True
        assert resolution.request.product_state.age_verified is False
        assert resolution.request.eligibility.denial_reason == "age_verification_required"

    @pytest.mark.asyncio
    async def test_product_lookup_failure_is_resolution_failed(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=AsyncMock(side_effect=RuntimeError("db down")),
        )
        assert resolution.status is CommerceResolutionStatus.RESOLUTION_FAILED


# ---------------------------------------------------------------------------
# GROUP F Ã¯Â¿Â½ purchases and offers
# ---------------------------------------------------------------------------


class TestPurchaseAndOffers:
    @pytest.mark.asyncio
    async def test_purchased_product_denied(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
            has_purchased_product=True,
        )
        assert resolution.request.has_active_offer is False
        assert resolution.request.eligibility.denial_reason == "already_purchased"

    @pytest.mark.asyncio
    async def test_pending_offer_is_active_and_blocks(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
            find_pending_offer_for_product=_OFFER_ROW,
        )
        assert resolution.request.has_active_offer is True
        assert resolution.request.eligibility.denial_reason == "offer_exists"

    @pytest.mark.asyncio
    async def test_clicked_offer_is_active_and_blocks(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
            find_pending_offer_for_product={**_OFFER_ROW, "state": "clicked"},
        )
        assert resolution.request.has_active_offer is True
        assert resolution.request.eligibility.denial_reason == "offer_exists"

    @pytest.mark.asyncio
    async def test_previous_offer_status_from_history(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            list_offers_for_user=[{**_OFFER_ROW, "state": "declined"}],
        )
        assert resolution.request.previous_offer_status == "declined"

    @pytest.mark.asyncio
    async def test_no_history_leaves_previous_status_neutral(self, monkeypatch):
        resolution = await _resolve(monkeypatch, list_offers_for_user=[])
        assert resolution.request.previous_offer_status is None

    @pytest.mark.asyncio
    async def test_offer_read_failure_is_resolution_failed(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
            find_pending_offer_for_product=AsyncMock(side_effect=RuntimeError("db down")),
        )
        assert resolution.status is CommerceResolutionStatus.RESOLUTION_FAILED

    @pytest.mark.asyncio
    async def test_purchase_read_failure_is_resolution_failed(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
            has_purchased_product=AsyncMock(side_effect=RuntimeError("db down")),
        )
        assert resolution.status is CommerceResolutionStatus.RESOLUTION_FAILED


# ---------------------------------------------------------------------------
# GROUP G Ã¯Â¿Â½ eligibility engine integration
# ---------------------------------------------------------------------------


class TestEligibilityEngine:
    @pytest.mark.asyncio
    async def test_fully_ready_fan_is_allowed(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
        )
        assert resolution.request.eligibility.allowed is True
        assert resolution.request.eligibility.denial_reason == ""

    @pytest.mark.asyncio
    async def test_every_denial_reason_reaches_the_request(self, monkeypatch):
        blocked = await _resolve(
            monkeypatch,
            get_user={**_USER_ROW, "is_blocked": True},
        )
        assert blocked.request.eligibility.denial_reason == "user_blocked"
        opted = await _resolve(monkeypatch, is_user_auto_reply_excluded=True)
        assert opted.request.eligibility.denial_reason == "user_opted_out"
        not_ready = await _resolve(monkeypatch, get_dropfans_integration=None)
        assert not_ready.request.eligibility.denial_reason == "creator_not_ready"
        unavailable = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product={**_PRODUCT_ROW, "is_accessible": False},
        )
        assert unavailable.request.eligibility.denial_reason == "product_unavailable"
        no_link = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product={**_PRODUCT_ROW, "sales_url": None},
        )
        assert no_link.request.eligibility.denial_reason == "product_missing_sales_url"
        purchased = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
            has_purchased_product=True,
        )
        assert purchased.request.eligibility.denial_reason == "already_purchased"
        active = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
            find_pending_offer_for_product=_OFFER_ROW,
        )
        assert active.request.eligibility.denial_reason == "offer_exists"
        age = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product={**_PRODUCT_ROW, "is_verif_age": True},
        )
        assert age.request.eligibility.denial_reason == "age_verification_required"

    @pytest.mark.asyncio
    async def test_resolver_uses_the_existing_engine(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
        )
        expected = evaluate_ppv_eligibility(
            UserEligibilityState(is_blocked=False, do_not_auto_reply=False),
            ProductEligibilityState(
                is_accessible=True, sales_url="https://f.test/p/10", price_minor=4400
            ),
            OfferContext(creator_ready=True),
        )
        assert resolution.request.eligibility == expected
        assert evaluate_ppv_eligibility.__module__ == "commerce.eligibility"

    @pytest.mark.asyncio
    async def test_writes_never_happen_during_eligibility(self, monkeypatch):
        _patch(monkeypatch)
        resolution = await resolve_commerce_state(_request())
        assert resolution.status is CommerceResolutionStatus.READY
        from db import dropfans as db_dropfans
        expected = {
            postgres: ("get_user", "is_user_auto_reply_excluded", "get_user_persona"),
            fdb: ("get_creator",),
            db_dropfans: ("get_dropfans_integration",),
            cdao: ("list_offers_for_user",),
        }
        for module, names in expected.items():
            for name in names:
                getattr(module, name).assert_awaited()


# ---------------------------------------------------------------------------
# GROUP H Ã¯Â¿Â½ determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_identical_reads_produce_identical_resolutions(self, monkeypatch):
        _patch(
            monkeypatch,
            get_fangate_product=_PRODUCT_ROW,
            list_offers_for_user=[_OFFER_ROW],
        )
        request = _request(product_id=PRODUCT_ID)
        first = await resolve_commerce_state(request)
        second = await resolve_commerce_state(request)
        assert first.model_dump() == second.model_dump()
        assert first.status is CommerceResolutionStatus.READY

    @pytest.mark.asyncio
    async def test_tied_offer_timestamps_follow_dao_identity_order(self, monkeypatch):
        history = [
            {**_OFFER_ROW, "id": 9, "state": "revoked"},
            {**_OFFER_ROW, "id": 5, "state": "expired"},
        ]
        resolution = await _resolve(
            monkeypatch,
            request=_request(creator_id=None),
            list_offers_for_user=history,
        )
        assert resolution.request.previous_offer_status == "revoked"

    @pytest.mark.asyncio
    async def test_no_python_level_sorting_of_offers(self):
        source = Path(commerce_state_path()).read_text(encoding="utf-8")
        for token in ("sorted(", ".sort(", "min(", "max(", "random."):
            assert token not in source, f"forbidden deterministic token: {token}"

    @pytest.mark.asyncio
    async def test_no_clock_reads_in_module(self):
        source = Path(commerce_state_path()).read_text(encoding="utf-8")
        for token in (
            "datetime",
            "timezone",
            "utcnow",
            ".now(",
            "monotonic",
            "sleep",
            "perf_counter",
        ):
            assert token not in source, f"forbidden clock token: {token}"


# ---------------------------------------------------------------------------
# GROUP I Ã¯Â¿Â½ safety surfaces
# ---------------------------------------------------------------------------


def commerce_state_path() -> str:
    return str(Path(inspect.getfile(resolve_commerce_state)))


class TestSafetySurfaces:
    def test_imports_are_limited(self):
        source = Path(commerce_state_path()).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(a.name for a in node.names)
        forbidden = (
            "random",
            "time",
            "datetime",
            "google",
            "httpx",
            "requests",
            "aiohttp",
            "redis",
            "asyncpg",
            "core.",
            "workers",
            "chatbotv2",
            "integrations",
            "websockets",
            "telegram",
        )
        for mod in imported:
            if mod != "logging":
                assert mod.startswith(
                    ("enum", "typing", "pydantic", "commerce", "db", "pathlib", "segments")
                ), f"forbidden import: {mod}"
            assert not mod.startswith(forbidden), f"forbidden import: {mod}"

    def test_only_select_style_reads_are_called(self):
        source = Path(commerce_state_path()).read_text(encoding="utf-8")
        tree = ast.parse(source)
        allowed = {
            "get_user",
            "is_user_auto_reply_excluded",
            "get_user_persona",
            "get_creator",
            "get_creator_integration",
            "get_fangate_product",
            "list_offers_for_user",
            "find_pending_offer_for_product",
            "has_purchased_product",
            "get_timing_context",
            "get_dropfans_integration",
            "get_behavioral_feedback_context",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            module = func.value
            if isinstance(module, ast.Name) and module.id in (
                "db_postgres",
                "db_fangate",
                "db_dropfans",
                "commerce_dao",
            ):
                assert func.attr in allowed, (
                    f"unexpected DB call {func.attr!r} at line {node.lineno}"
                )

    def test_no_write_sql_and_no_write_helpers(self):
        source = Path(commerce_state_path()).read_text(encoding="utf-8")
        for token in (
            "INSERT INTO",
            "UPDATE ",
            "DELETE FROM",
            "RETURNING",
            "execute(",
            "create_offer",
            "mark_offer",
            "record_",
            "attach_transaction_user",
            "increment_analytics",
            "execute_ppv",
        ):
            assert token not in source, f"forbidden write token: {token}"

    def test_no_ai_touch_points(self):
        source = Path(commerce_state_path()).read_text(encoding="utf-8")
        for token in (
            "extract_commerce_signals",
            "generate_commerce_response",
            "decide_from_signals",
            "orchestrate_commerce",
            "run_commerce_pipeline",
            "gemini",
            "api_key",
            "encrypted_api_key",
        ):
            assert token not in source, f"forbidden AI/credential token: {token}"

    def test_no_verify_product_live_call(self):
        source = Path(commerce_state_path()).read_text(encoding="utf-8")
        assert "verify_product" not in source
        assert "integrations" not in source


# ---------------------------------------------------------------------------
# GROUP J Ã¯Â¿Â½ output surface
# ---------------------------------------------------------------------------


class TestOutputSurface:
    @pytest.mark.asyncio
    async def test_ready_always_carries_a_pipeline_request(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
        )
        assert resolution.status is CommerceResolutionStatus.READY
        assert isinstance(resolution.request, CommercePipelineRequest)

    @pytest.mark.asyncio
    async def test_non_ready_never_carries_a_request(self, monkeypatch):
        for patch in (
            {"get_user": None},
            {"get_creator": None},
            {"product_id": PRODUCT_ID, "get_fangate_product": None},
        ):
            resolution = await _resolve(monkeypatch, **patch)
            assert resolution.request is None

    @pytest.mark.asyncio
    async def test_pipeline_request_is_strict_boundary(self, monkeypatch):
        resolution = await _resolve(monkeypatch)
        pipeline_request = resolution.request
        assert pipeline_request.eligibility.allowed is False
        assert pipeline_request.eligibility.denial_reason == "product_missing_sales_url"
        assert pipeline_request.user_id == USER_ID
        assert pipeline_request.creator_id == CREATOR_ID

    @pytest.mark.asyncio
    async def test_clock_windowed_fields_stay_neutral(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
            list_offers_for_user=[_OFFER_ROW],
        )
        request = resolution.request
        assert request.hours_since_last_offer is None
        assert request.hours_since_last_purchase is None
        assert request.messages_since_last_offer == 0
        assert request.messages_since_last_purchase == 0
        assert request.recent_offer_count == 0
        assert request.recent_purchase_count == 0
        assert request.recent_sales_attempt_count == 0
        assert request.relationship_score is None

    @pytest.mark.asyncio
    async def test_messages_and_currency_travel_verbatim(self, monkeypatch):
        raw = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}]
        resolution = await _resolve(
            monkeypatch,
            request=_request(messages=raw, currency="USD", persona="assistant core"),
        )
        assert resolution.request.messages == raw
        assert resolution.request.currency == "USD"
        assert resolution.request.persona == "assistant core"

    @pytest.mark.asyncio
    async def test_persona_falls_back_to_stored_value(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            request=_request(persona=None),
            get_user_persona="stored persona",
        )
        assert resolution.request.persona == "stored persona"

    @pytest.mark.asyncio
    async def test_stored_persona_is_bounded_like_the_pipeline(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            request=_request(persona=None),
            get_user_persona="p" * (PIPELINE_PERSONA_MAX + 50),
        )
        assert len(resolution.request.persona) == PIPELINE_PERSONA_MAX

    @pytest.mark.asyncio
    async def test_resolution_dump_contains_no_credentials(self, monkeypatch):
        resolution = await _resolve(
            monkeypatch,
            product_id=PRODUCT_ID,
            get_fangate_product=_PRODUCT_ROW,
        )
        text = str(resolution.model_dump())
        for token in ("api_key", "secret", "token", "ciphertext", "password", "bearer"):
            assert token not in text.lower(), f"credential token leaked: {token}"
