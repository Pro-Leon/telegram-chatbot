"""Phase 5.2 - Deterministic PPV eligibility tests.

Covers the pure rules engine in commerce/eligibility.py: every denial rule,
rule ordering, and the allowed path.
"""

import pytest

from commerce.eligibility import (
    OfferContext,
    ProductEligibilityState,
    UserEligibilityState,
    evaluate_ppv_eligibility,
)

pytestmark = [pytest.mark.unit]


def _default_user(**overrides) -> UserEligibilityState:
    return UserEligibilityState(**overrides)


def _default_product(**overrides) -> ProductEligibilityState:
    base = {"sales_url": "https://fangate.test/p/11"}
    base.update(overrides)
    return ProductEligibilityState(**base)


def _ready_ctx(**overrides) -> OfferContext:
    base = {"creator_ready": True}
    base.update(overrides)
    return OfferContext(**base)


class TestEligibilityAllowed:
    def test_fully_ready_fan_is_eligible(self):
        decision = evaluate_ppv_eligibility(_default_user(), _default_product(), _ready_ctx())
        assert decision.allowed is True
        assert decision.denial_reason == ""

    def test_unpriced_product_still_eligible(self):
        product = _default_product(price_minor=None, sales_url="https://fangate.test/p/9")
        decision = evaluate_ppv_eligibility(_default_user(), product, _ready_ctx())
        assert decision.allowed is True


class TestEligibilityDenials:
    def test_blocked_user_denied(self):
        decision = evaluate_ppv_eligibility(
            _default_user(is_blocked=True), _default_product(), _ready_ctx()
        )
        assert decision.allowed is False
        assert decision.denial_reason == "user_blocked"

    def test_opt_out_user_denied(self):
        decision = evaluate_ppv_eligibility(
            _default_user(do_not_auto_reply=True), _default_product(), _ready_ctx()
        )
        assert decision.allowed is False
        assert decision.denial_reason == "user_opted_out"

    def test_unintegrated_creator_denied(self):
        decision = evaluate_ppv_eligibility(
            _default_user(),
            _default_product(),
            _ready_ctx(creator_ready=False),
        )
        assert decision.allowed is False
        assert decision.denial_reason == "creator_not_ready"

    def test_inaccessible_product_denied(self):
        decision = evaluate_ppv_eligibility(
            _default_user(), _default_product(is_accessible=False), _ready_ctx()
        )
        assert decision.allowed is False
        assert decision.denial_reason == "product_unavailable"

    def test_missing_sales_url_denied(self):
        decision = evaluate_ppv_eligibility(
            _default_user(), _default_product(sales_url=None), _ready_ctx()
        )
        assert decision.allowed is False
        assert decision.denial_reason == "product_missing_sales_url"

    def test_purchased_product_denied(self):
        decision = evaluate_ppv_eligibility(
            _default_user(),
            _default_product(),
            _ready_ctx(already_purchased=True),
        )
        assert decision.allowed is False
        assert decision.denial_reason == "already_purchased"

    def test_existing_offer_denied(self):
        decision = evaluate_ppv_eligibility(
            _default_user(),
            _default_product(),
            _ready_ctx(has_active_offer=True),
        )
        assert decision.allowed is False
        assert decision.denial_reason == "offer_exists"

    def test_age_verification_required_denied(self):
        decision = evaluate_ppv_eligibility(
            _default_user(),
            _default_product(),
            _ready_ctx(enforce_age_verification=True, age_verified=False),
        )
        assert decision.allowed is False
        assert decision.denial_reason == "age_verification_required"

    def test_age_verified_allows(self):
        decision = evaluate_ppv_eligibility(
            _default_user(),
            _default_product(),
            _ready_ctx(enforce_age_verification=True, age_verified=True),
        )
        assert decision.allowed is True

    def test_first_denial_wins_in_fixed_order(self):
        """Blocked user beats every other rule regardless of context."""
        decision = evaluate_ppv_eligibility(
            _default_user(is_blocked=True, do_not_auto_reply=True),
            _default_product(is_accessible=False),
            _ready_ctx(creator_ready=False),
        )
        assert decision.denial_reason == "user_blocked"

    def test_no_credentials_or_urls_nowhere_in_decision(self):
        """The decision carries no price, URL, or secret data."""
        import dataclasses

        from commerce.models import PolicyDecision

        fields = {f.name for f in dataclasses.fields(PolicyDecision)}
        assert fields == {"allowed", "denial_reason"}
