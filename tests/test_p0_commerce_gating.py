"""P0 gating integration tests — explicit/price AND deterministic verifier.

These tests verify the P0 fix: LLM signals alone cannot authorize PPV
without deterministic raw-text verification. They are additive and do not
modify existing tests' legacy behavior (user_message=None retains legacy).
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from commerce.signals import CommerceSignals, signals_to_context, decide_from_signals
from commerce.models import PolicyDecision
from commerce.pipeline import _apply_signal_flags
from commerce.context import CommerceConversationContext


def _allowed():
    return PolicyDecision(allowed=True, denial_reason="none")

def _make_explicit(price_interest=0.0, requested_price=None, explicit=True, purchase_intent=0.1):
    return CommerceSignals(
        purchase_intent=purchase_intent,
        content_interest=0.0,
        relationship_engagement=0.0,
        price_interest=price_interest,
        explicit_purchase_request=explicit,
        explicit_content_request=False,
        requested_price=requested_price,
        declined_recent_offer=False,
        negative_sentiment=0.0,
        confidence=0.9,
        evidence=[],
        model_uncertainty=0.1,
        primary_intent="purchase_intent" if explicit else "casual_chat",
        intent_tags=["purchase_intent"] if explicit else [],
        negative_intent_tags=[],
        fan_asks_question=False,
    )

def _make_price(price_interest=0.9, requested_price=25.0):
    return CommerceSignals(
        purchase_intent=0.0,
        content_interest=0.0,
        relationship_engagement=0.0,
        price_interest=price_interest,
        explicit_purchase_request=False,
        explicit_content_request=False,
        requested_price=requested_price,
        declined_recent_offer=False,
        negative_sentiment=0.0,
        confidence=0.9,
        evidence=[],
        model_uncertainty=0.1,
        primary_intent="price_inquiry",
        intent_tags=["price_inquiry"],
        negative_intent_tags=[],
        fan_asks_question=False,
    )

pytestmark = [pytest.mark.unit]

# 1
def test_llm_explicit_true_hey_does_not_authorize():
    sig = _make_explicit(explicit=True)
    ctx = signals_to_context(sig, user_id=1, creator_id=2, eligibility=_allowed(), user_message="hey")
    assert ctx.user_asked_to_buy is False

# 2
def test_llm_explicit_true_buy_authorizes():
    sig = _make_explicit(explicit=True)
    ctx = signals_to_context(sig, user_id=1, creator_id=2, eligibility=_allowed(), user_message="I want to buy")
    assert ctx.user_asked_to_buy is True

# 3
def test_price_signal_hey_does_not_authorize():
    sig = _make_price(price_interest=0.9, requested_price=25.0)
    ctx = signals_to_context(sig, user_id=1, creator_id=2, eligibility=_allowed(), user_message="hey")
    assert ctx.user_asked_about_price is False

# 4
def test_price_signal_how_much_authorizes():
    sig = _make_price(price_interest=0.9, requested_price=25.0)
    ctx = signals_to_context(sig, user_id=1, creator_id=2, eligibility=_allowed(), user_message="how much?")
    assert ctx.user_asked_about_price is True

# 5 hallucinated does not reach OFFER_PPV (low purchase_intent case)
def test_hallucinated_purchase_hey_not_ppv():
    sig = _make_explicit(explicit=True, purchase_intent=0.1)
    d = decide_from_signals(sig, user_id=1, creator_id=2, eligibility=_allowed(), user_message="hey", has_relevant_product=True, has_active_offer=False, creator_sales_enabled=True)
    assert d.action.value != "offer_ppv" or d.allowed is False

# 6 genuine reaches OFFER_PPV
def test_genuine_purchase_reaches_ppv():
    sig = _make_explicit(explicit=True, purchase_intent=0.1)
    d = decide_from_signals(sig, user_id=1, creator_id=2, eligibility=_allowed(), user_message="I want to buy this please", has_relevant_product=True, has_active_offer=False, creator_sales_enabled=True)
    assert d.action.value == "offer_ppv" and d.allowed is True

# 7 free-photo behavior intact — is_photo_request still works
def test_free_photo_intact():
    from commerce.free_photo_routing import is_photo_request
    assert is_photo_request("send me a pic", None) is True
    assert is_photo_request("send me a pic and I'll pay", None) is True
    assert is_photo_request("hey", None) is False
    assert is_photo_request("this photo is so cute", None) is False

# also _apply_signal_flags gating
def test_apply_signal_flags_gating():
    base = CommerceConversationContext(user_id=1, creator_id=2, eligibility=_allowed(), messages=[])
    sig = _make_explicit(explicit=True)
    gated = _apply_signal_flags(base, sig, user_message="hey")
    assert gated.user_asked_to_buy is False
    gated2 = _apply_signal_flags(base, sig, user_message="I want to buy")
    assert gated2.user_asked_to_buy is True

# 8 purchase-history DB failure prevents product selection — checked via product_selection unit
@pytest.mark.asyncio
async def test_purchase_history_failure_blocks_selection():
    from commerce.product_selection import resolve_commerce_product_with_history
    mock_products = [
        {"id": 1, "is_accessible": True, "sales_url": "https://example.com/1", "title": "P1", "price_minor": 1000, "raw": {}},
        {"id": 2, "is_accessible": True, "sales_url": "https://example.com/2", "title": "P2", "price_minor": 2000, "raw": {}},
    ]
    with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=mock_products):
        with patch("commerce.product_selection._get_purchased_product_ids", new_callable=AsyncMock, return_value=None):
            res = await resolve_commerce_product_with_history(1, 1)
            assert res is None

# 9 timing failure -> RESOLUTION_FAILED
@pytest.mark.asyncio
async def test_timing_failure_resolution_failed():
    from commerce.state import CommerceStateRequest, resolve_commerce_state
    req = CommerceStateRequest(user_id=123, creator_id=1, product_id=1, messages=[{"role": "user", "content": "hi"}])
    with patch("db.postgres.get_user", new_callable=AsyncMock, return_value={"is_blocked": False}):
        with patch("db.postgres.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False):
            with patch("commerce.state._resolve_creator_relationship", new_callable=AsyncMock, return_value=(1, None)):
                with patch("db.dropfans.get_dropfans_integration", new_callable=AsyncMock, return_value={"status": "active"}):
                    with patch("db.fangate.get_fangate_product", new_callable=AsyncMock, return_value={"id": 1, "is_accessible": True, "sales_url": "https://example.com", "price_minor": 1000, "is_verif_age": False}):
                        with patch("commerce.dao.find_pending_offer_for_product", new_callable=AsyncMock, return_value=None):
                            with patch("commerce.dao.has_purchased_product", new_callable=AsyncMock, return_value=False):
                                with patch("db.postgres.get_user_persona", new_callable=AsyncMock, return_value=None):
                                    with patch("db.segments.list_segments", new_callable=AsyncMock, return_value=[]):
                                        with patch("commerce.dao.get_timing_context", new_callable=AsyncMock, side_effect=Exception("db down")):
                                            res = await resolve_commerce_state(req)
                                            assert res.status.value == "resolution_failed"

# 10 behavioral failure -> RESOLUTION_FAILED
@pytest.mark.asyncio
async def test_behavioral_failure_resolution_failed():
    from commerce.state import CommerceStateRequest, resolve_commerce_state
    req = CommerceStateRequest(user_id=124, creator_id=1, product_id=1, messages=[{"role": "user", "content": "hi"}])
    with patch("db.postgres.get_user", new_callable=AsyncMock, return_value={"is_blocked": False}):
        with patch("db.postgres.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False):
            with patch("commerce.state._resolve_creator_relationship", new_callable=AsyncMock, return_value=(1, None)):
                with patch("db.dropfans.get_dropfans_integration", new_callable=AsyncMock, return_value={"status": "active"}):
                    with patch("db.fangate.get_fangate_product", new_callable=AsyncMock, return_value={"id": 1, "is_accessible": True, "sales_url": "https://example.com", "price_minor": 1000, "is_verif_age": False}):
                        with patch("commerce.dao.find_pending_offer_for_product", new_callable=AsyncMock, return_value=None):
                            with patch("commerce.dao.has_purchased_product", new_callable=AsyncMock, return_value=False):
                                with patch("db.postgres.get_user_persona", new_callable=AsyncMock, return_value=None):
                                    with patch("db.segments.list_segments", new_callable=AsyncMock, return_value=[]):
                                        with patch("commerce.dao.get_timing_context", new_callable=AsyncMock, return_value={"hours_since_last_offer": None, "hours_since_last_purchase": None, "recent_offer_count": 0, "recent_purchase_count": 0, "recent_sales_attempt_count": 0}):
                                            with patch("commerce.dao.get_behavioral_feedback_context", new_callable=AsyncMock, side_effect=Exception("db down beh")):
                                                res = await resolve_commerce_state(req)
                                                assert res.status.value == "resolution_failed"

def test_legacy_none_retains_behavior():
    # user_message=None must retain legacy True
    sig = _make_explicit(explicit=True)
    ctx = signals_to_context(sig, user_id=1, creator_id=2, eligibility=_allowed(), user_message=None)
    assert ctx.user_asked_to_buy is True
    base = CommerceConversationContext(user_id=1, creator_id=2, eligibility=_allowed(), messages=[])
    gated = _apply_signal_flags(base, sig, user_message=None)
    assert gated.user_asked_to_buy is True
