"""Phase 5.3B tests — CommerceSignals typed model + pure conversion.

Chk 1: CommerceSignals strict validation (bounds, types, extras, evidence).
Chk 5: signals_to_context application-state-wins conversion.
Chk 6: decide_from_signals always routes through the deterministic engine.
Chk 11 (partial): signals.py must not touch forbidden surfaces.
"""

import pytest
from pydantic import ValidationError

from commerce.decision import CommerceReason
from commerce.eligibility import PolicyDecision
from commerce.models import CommerceAction
from commerce.signals import (
    PRICE_ASK_THRESHOLD,
    CommerceSignals,
    decide_from_signals,
    signals_to_context,
)

pytestmark = [pytest.mark.unit]


def _payload(**overrides):
    """Raw dict payload (for validation tests)."""
    base = {
        "purchase_intent": 0.0,
        "content_interest": 0.0,
        "relationship_engagement": 0.0,
        "price_interest": 0.0,
        "explicit_purchase_request": False,
        "explicit_content_request": False,
        "requested_price": None,
        "declined_recent_offer": False,
        "accepted_recent_offer": False,
        "asks_for_free_content": False,
        "negative_sentiment": 0.0,
        "conversation_relevance": 0.0,
        "confidence": 0.0,
        "evidence": [],
        "model_uncertainty": 0.0,
    }
    base.update(overrides)
    return base


def make_signals(**overrides) -> CommerceSignals:
    """Valid CommerceSignals model (for conversion/decision tests)."""
    return CommerceSignals(**_payload(**overrides))


def allowed_eligibility():
    return PolicyDecision(allowed=True, denial_reason=None)


# ═══════════════════════════════════════════════════════════════════════════
# GROUP A — Required fields / strict presence
# ═══════════════════════════════════════════════════════════════════════════


class TestRequiredFields:
    def test_all_fields_required_except_price_and_evidence(self):
        """Missing any required field is a hard error."""
        payload = _payload()
        del payload["purchase_intent"]
        with pytest.raises(ValidationError):
            CommerceSignals(**payload)

    def test_model_uncertainty_must_be_present(self):
        """model_uncertainty is required; do not default to 0."""
        payload = _payload()
        del payload["model_uncertainty"]
        with pytest.raises(ValidationError):
            CommerceSignals(**payload)

    def test_defaults_on_price_and_evidence_only(self):
        """requested_price defaults to None, evidence may be empty."""
        s = CommerceSignals(**_payload())
        assert s.requested_price is None
        assert s.evidence == []
        assert isinstance(s.evidence, list)

    def test_empty_dict_rejected(self):
        with pytest.raises(ValidationError):
            CommerceSignals()


# ═══════════════════════════════════════════════════════════════════════════
# GROUP B — Bounded floats
# ═══════════════════════════════════════════════════════════════════════════


class TestBoundedFloats:
    @pytest.mark.parametrize(
        "field",
        [
            "purchase_intent",
            "content_interest",
            "relationship_engagement",
            "price_interest",
            "negative_sentiment",
            "conversation_relevance",
            "confidence",
            "model_uncertainty",
        ],
    )
    def test_bounds_are_strict(self, field):
        """Values outside [0.0, 1.0] are REJECTED (never clamped)."""
        for bad in (-0.01, 1.01, 2.5, -5.0):
            with pytest.raises(ValidationError):
                CommerceSignals(**_payload(**{field: bad}))

    @pytest.mark.parametrize(
        "field",
        [
            "purchase_intent",
            "content_interest",
            "relationship_engagement",
            "price_interest",
            "negative_sentiment",
            "conversation_relevance",
            "confidence",
            "model_uncertainty",
        ],
    )
    def test_boundary_values_accepted(self, field):
        s = CommerceSignals(**_payload(**{field: 1.0}))
        assert getattr(s, field) == 1.0
        s = CommerceSignals(**_payload(**{field: 0.0}))
        assert getattr(s, field) == 0.0

    def test_int_coercion_is_fine(self):
        """JSON ints coerced to floats; 0/1 are in bounds."""
        s = CommerceSignals(**_payload(purchase_intent=1))
        assert s.purchase_intent == 1.0

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_rejected(self, bad):
        with pytest.raises(ValidationError):
            CommerceSignals(**_payload(purchase_intent=bad))

    @pytest.mark.parametrize("bad", ["0.5", None, [], {}, True])
    def test_wrong_type_rejected(self, bad):
        with pytest.raises(ValidationError):
            CommerceSignals(**_payload(purchase_intent=bad))


# ═══════════════════════════════════════════════════════════════════════════
# GROUP C — Boolean fields are strict booleans
# ═══════════════════════════════════════════════════════════════════════════


class TestStrictBooleans:
    @pytest.mark.parametrize(
        "field",
        [
            "explicit_purchase_request",
            "explicit_content_request",
            "declined_recent_offer",
            "accepted_recent_offer",
            "asks_for_free_content",
        ],
    )
    def test_only_real_booleans(self, field):
        """Strings, numbers, null are rejected for boolean fields."""
        for bad in ("true", "false", 1, 0, None, "yes", []):
            with pytest.raises(ValidationError):
                CommerceSignals(**_payload(**{field: bad}))
        s = CommerceSignals(**_payload(**{field: True}))
        assert getattr(s, field) is True
        s = CommerceSignals(**_payload(**{field: False}))
        assert getattr(s, field) is False


# ═══════════════════════════════════════════════════════════════════════════
# GROUP D — requested_price
# ═══════════════════════════════════════════════════════════════════════════


class TestRequestedPrice:
    def test_none_accepted(self):
        assert CommerceSignals(**_payload()).requested_price is None

    def test_positive_accepted(self):
        s = CommerceSignals(**_payload(requested_price=50.0))
        assert s.requested_price == 50.0

    @pytest.mark.parametrize("bad", [0, -1, -0.01, float("nan"), float("inf"), "10"])
    def test_non_positive_or_non_finite_rejected(self, bad):
        with pytest.raises(ValidationError):
            CommerceSignals(**_payload(requested_price=bad))


# ═══════════════════════════════════════════════════════════════════════════
# GROUP E — evidence bounds + payment-data guard
# ═══════════════════════════════════════════════════════════════════════════


class TestEvidence:
    def test_max_five_items(self):
        payload = _payload(evidence=["a"] * 5)
        assert len(CommerceSignals(**payload).evidence) == 5
        with pytest.raises(ValidationError):
            CommerceSignals(**_payload(evidence=["a"] * 6))

    def test_item_length_bounded(self):
        too_long = "x" * 241
        with pytest.raises(ValidationError):
            CommerceSignals(**_payload(evidence=[too_long]))
        ok = "y" * 240
        assert CommerceSignals(**_payload(evidence=[ok])).evidence == [ok]

    @pytest.mark.parametrize("bad", [None, 42, {"k": "v"}, True])
    def test_non_string_items_rejected(self, bad):
        with pytest.raises(ValidationError):
            CommerceSignals(**_payload(evidence=[bad]))

    @pytest.mark.parametrize(
        "bad_evidence",
        [
            ["my card number is 4111111111111111"],
            ["cvv provided: 123"],
            ["the pan: is secret"],
            ["use payment method via link"],
            ["cardno available"],
        ],
    )
    def test_payment_data_is_hard_error(self, bad_evidence):
        """Payment data in evidence must fail validation (privacy guard)."""
        with pytest.raises(ValidationError, match="payment data"):
            CommerceSignals(**_payload(evidence=bad_evidence))

    def test_plain_digits_are_allowed(self):
        """Ordinary numbers (prices, amounts) are NOT payment data."""
        s = CommerceSignals(**_payload(evidence=["willing to pay 50", "saw it for $30"]))
        assert len(s.evidence) == 2


# ═══════════════════════════════════════════════════════════════════════════
# GROUP F — extra fields / action leakage
# ═══════════════════════════════════════════════════════════════════════════


class TestExtraFieldsForbidden:
    @pytest.mark.parametrize(
        "extra",
        [
            {"action": "offer_ppv"},
            {"allowed": True},
            {"send_ppv": "yes"},
            {"authorization": "granted"},
            {"commerce_action": "offer_ppv"},
            {"random_field": 1},
        ],
    )
    def test_extra_fields_rejected(self, extra):
        """No action/authorization/decision fields may leak into signals."""
        with pytest.raises(ValidationError):
            CommerceSignals(**_payload(**extra))

    def test_model_dump_roundtrip_stable(self):
        """dump -> reparse yields an identical model (determinism)."""
        s = CommerceSignals(
            **_payload(
                purchase_intent=0.9,
                explicit_purchase_request=True,
                requested_price=25.0,
                evidence=["fan asked: how much"],
            )
        )
        s2 = CommerceSignals(**s.model_dump())
        assert s2 == s


# ═══════════════════════════════════════════════════════════════════════════
# GROUP G — low_information fallback
# ═══════════════════════════════════════════════════════════════════════════


class TestLowInformation:
    def test_all_neutral_and_deterministic(self):
        s1 = CommerceSignals.low_information()
        s2 = CommerceSignals.low_information()
        assert s1 == s2
        assert s1.purchase_intent == 0.0
        assert s1.content_interest == 0.0
        assert s1.relationship_engagement == 0.0
        assert s1.price_interest == 0.0
        assert s1.explicit_purchase_request is False
        assert s1.explicit_content_request is False
        assert s1.requested_price is None
        assert s1.declined_recent_offer is False
        assert s1.accepted_recent_offer is False
        assert s1.asks_for_free_content is False
        assert s1.negative_sentiment == 0.0
        assert s1.conversation_relevance == 0.0
        assert s1.confidence == 0.0
        assert s1.evidence == []
        assert s1.model_uncertainty == 1.0

    def test_fallback_never_implies_interest(self):
        s = CommerceSignals.low_information()
        context = signals_to_context(
            s,
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
        )
        assert context.user_asked_to_buy is False
        assert context.user_asked_about_price is False
        assert context.user_requested_content is False
        assert context.buying_intent_score == 0.0
        assert context.relationship_score == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# GROUP H — signals_to_context (application state wins)
# ═══════════════════════════════════════════════════════════════════════════


class TestSignalsToContext:
    def test_eligibility_passes_through_untouched(self):
        denied = PolicyDecision(allowed=False, denial_reason="user_blocked")
        context = signals_to_context(
            make_signals(explicit_purchase_request=True),
            user_id=1,
            creator_id=2,
            eligibility=denied,
        )
        assert context.eligibility is denied
        assert context.eligibility.allowed is False

    def test_explicit_purchase_maps_to_buy_request(self):
        context = signals_to_context(
            make_signals(explicit_purchase_request=True),
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
        )
        assert context.user_asked_to_buy is True
        assert context.user_asked_about_price is False
        assert context.user_requested_content is False

    def test_quoted_price_maps_to_price_ask(self):
        context = signals_to_context(
            make_signals(requested_price=20.0),
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
        )
        assert context.user_asked_about_price is True
        assert context.user_asked_to_buy is False

    def test_high_price_interest_maps_to_price_ask(self):
        context = signals_to_context(
            make_signals(price_interest=PRICE_ASK_THRESHOLD),
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
        )
        assert context.user_asked_about_price is True

    def test_moderate_price_interest_does_not_map_to_price_ask(self):
        context = signals_to_context(
            make_signals(price_interest=0.5),
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
        )
        assert context.user_asked_about_price is False

    def test_explicit_content_maps_to_content_request(self):
        context = signals_to_context(
            make_signals(explicit_content_request=True),
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
        )
        assert context.user_requested_content is True

    def test_purchase_intent_feeds_buying_score(self):
        context = signals_to_context(
            make_signals(purchase_intent=0.87),
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
        )
        assert context.buying_intent_score == 0.87

    def test_app_relationship_score_wins(self):
        context = signals_to_context(
            make_signals(relationship_engagement=0.95),
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
            relationship_score=0.4,
        )
        assert context.relationship_score == 0.4

    def test_signal_relationship_used_when_app_absent(self):
        context = signals_to_context(
            make_signals(relationship_engagement=0.72),
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
        )
        assert context.relationship_score == 0.72

    def test_cooldown_state_passes_through(self):
        context = signals_to_context(
            None,
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
            hours_since_last_purchase=1.0,
            hours_since_last_offer=None,
            recent_offer_count=2,
            recent_purchase_count=0,
            recent_sales_attempt_count=2,
            previous_offer_status="declined",
            has_active_offer=True,
            has_relevant_product=True,
            creator_sales_enabled=True,
            messages_since_last_offer=3,
            messages_since_last_purchase=0,
        )
        assert context.hours_since_last_purchase == 1.0
        assert context.recent_offer_count == 2
        assert context.recent_purchase_count == 0
        assert context.recent_sales_attempt_count == 2
        assert context.previous_offer_status == "declined"
        assert context.has_active_offer is True
        assert context.has_relevant_product is True
        assert context.creator_sales_enabled is True
        assert context.messages_since_last_offer == 3

    def test_none_signals_produce_no_intent(self):
        context = signals_to_context(
            None,
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
        )
        assert context.user_asked_to_buy is False
        assert context.user_asked_about_price is False
        assert context.user_requested_content is False
        assert context.buying_intent_score is None

    def test_declined_recent_offer_is_observational_only(self):
        """Signal flags never override app-owned offer status."""
        context = signals_to_context(
            make_signals(declined_recent_offer=True),
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
            previous_offer_status="expired",
        )
        assert context.previous_offer_status == "expired"


# ═══════════════════════════════════════════════════════════════════════════
# GROUP I — decide_from_signals routes through the deterministic engine
# ═══════════════════════════════════════════════════════════════════════════


class TestDecideFromSignals:
    def test_returns_engine_decision_type(self):
        decision = decide_from_signals(
            make_signals(),
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
        )
        from commerce.decision import CommerceDecision

        assert isinstance(decision, CommerceDecision)
        assert decision.action in CommerceAction
        assert decision.reason_code in CommerceReason

    def test_eligibility_denial_wins_over_any_signal(self):
        denied = PolicyDecision(allowed=False, denial_reason="creator_not_ready")
        decision = decide_from_signals(
            make_signals(
                explicit_purchase_request=True,
                purchase_intent=0.99,
                requested_price=100.0,
            ),
            user_id=1,
            creator_id=2,
            eligibility=denied,
        )
        assert decision.allowed is False
        assert decision.action == CommerceAction.NO_OFFER
        assert decision.reason_code == CommerceReason.CREATOR_NOT_READY
        assert decision.confidence == 1.0

    def test_signal_can_reach_engine_not_override_it(self):
        """A strong signal may only produce an offer THROUGH engine rules."""
        decision = decide_from_signals(
            make_signals(purchase_intent=0.95, explicit_purchase_request=True),
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
        )
        assert decision.action == CommerceAction.OFFER_PPV
        assert decision.reason_code == CommerceReason.STRONG_BUYING_SIGNAL
        assert decision.confidence == 0.95

    def test_fallback_signals_never_produce_an_offer(self):
        decision = decide_from_signals(
            None,
            user_id=1,
            creator_id=2,
            eligibility=allowed_eligibility(),
        )
        assert decision.allowed is False
        assert decision.action in (CommerceAction.NO_OFFER, CommerceAction.RELATIONSHIP_BUILDING)

    def test_deterministic_for_same_input(self):
        kwargs = {
            "user_id": 1,
            "creator_id": 2,
            "eligibility": allowed_eligibility(),
        }
        d1 = decide_from_signals(make_signals(purchase_intent=0.9, price_interest=0.85), **kwargs)
        d2 = decide_from_signals(make_signals(purchase_intent=0.9, price_interest=0.85), **kwargs)
        assert d1.action == d2.action
        assert d1.reason_code == d2.reason_code
        assert d1.confidence == d2.confidence
        assert d1.allowed == d2.allowed
        assert d1.requires_human_review == d2.requires_human_review


# ═══════════════════════════════════════════════════════════════════════════
# GROUP J — no forbidden surfaces (checkpoint 11 guard)
# ═══════════════════════════════════════════════════════════════════════════


class TestNoForbiddenSurfaces:
    def test_signals_module_does_not_import_forbidden_surfaces(self):
        """signals.py must not touch workers, event bus, ws, or db."""
        import ast
        import inspect

        from commerce import signals

        source = inspect.getsource(signals)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module is None or not node.module.startswith(
                    (
                        "workers",
                        "core.event_bus",
                        "chatbotv2.dashboard",
                        "db",
                        "integrations",
                        "httpx",
                        "requests",
                    )
                ), f"forbidden import: {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(
                        (
                            "workers",
                            "core.event_bus",
                            "chatbotv2.dashboard",
                            "db",
                            "integrations",
                            "httpx",
                            "requests",
                        )
                    ), f"forbidden import: {alias.name}"

    def test_signals_module_has_no_send_or_execution_code(self):
        import inspect

        from commerce import signals

        source = inspect.getsource(signals)
        for forbidden in (
            "enqueue_send",
            "publish_event",
            "send_ppv",
            "create_offer",
            "move_to_dlq",
            "asyncpg",
            "redis",
        ):
            assert forbidden not in source

    def test_no_math_random_or_clock_in_signals(self):
        """Pure module: no randomness or clock reads."""
        import ast
        import inspect

        from commerce import signals

        tree = ast.parse(inspect.getsource(signals))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
                )
                for name in names:
                    if name:
                        assert "random" not in name and name != "time", f"forbidden: {name}"
