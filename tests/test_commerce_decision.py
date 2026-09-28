"""Phase 5.3A - Deterministic Commerce Decision Model tests.

Covers:
- Domain types (enums, context, policy, decision output)
- The deterministic decision engine (every major branch)
- Determinism and purity (no external dependencies)
"""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from commerce.decision import (
    CommerceDecision,
    CommerceDecisionContext,
    CommerceDecisionPolicy,
    CommerceReason,
    decide_commerce_action,
)
from commerce.models import CommerceAction, OfferState, PolicyDecision

pytestmark = [pytest.mark.unit]

ALLOWED = PolicyDecision(allowed=True)


def _ctx(**overrides) -> CommerceDecisionContext:
    base = {"user_id": 9001, "creator_id": 7, "eligibility": ALLOWED}
    base.update(overrides)
    return CommerceDecisionContext(**base)


# ═══════════════════════════════════════════════════════════════════════════
# Checkpoint 2 — Domain types
# ═══════════════════════════════════════════════════════════════════════════


class TestCommerceActionExtension:
    def test_decision_actions_are_stable_codes(self):
        expected = {
            "no_offer",
            "relationship_building",
            "soft_offer",
            "offer_ppv",
            "follow_up",
        }
        actions = {
            a
            for a in (
                CommerceAction.NO_OFFER,
                CommerceAction.RELATIONSHIP_BUILDING,
                CommerceAction.SOFT_OFFER,
                CommerceAction.OFFER_PPV,
                CommerceAction.FOLLOW_UP,
            )
        }
        assert {a.value for a in actions} == expected

    def test_existing_proposal_parsing_still_works(self):
        from commerce.models import CommerceProposal

        p = CommerceProposal.from_dict({"action": "offer_ppv", "ppv_id": 3})
        assert p.action is CommerceAction.OFFER_PPV
        p2 = CommerceProposal.from_dict({"action": "no_offer"})
        assert p2.action is CommerceAction.NO_OFFER


class TestCommerceReasonCodes:
    def test_values_are_stable_snake_case(self):
        for reason in CommerceReason:
            assert reason.value == reason.value.lower()
            assert " " not in reason.value

    def test_hard_policy_codes_exist(self):
        for code in (
            "policy_denied",
            "user_blocked",
            "user_opted_out",
            "creator_not_ready",
            "product_unavailable",
            "product_missing_sales_url",
            "already_purchased",
            "offer_exists",
            "age_verification_required",
        ):
            assert CommerceReason(code) is not None

    def test_relationship_codes_exist(self):
        for code in (
            "insufficient_relationship",
            "recent_purchase",
            "recent_offer",
            "recent_decline",
            "recent_ignore",
            "too_many_offers",
            "too_many_sales_attempts",
            "cooldown_active",
            "no_buying_signal",
            "no_relevant_product",
        ):
            assert CommerceReason(code) is not None

    def test_positive_codes_exist(self):
        for code in (
            "strong_buying_signal",
            "moderate_buying_signal",
            "relationship_ready",
            "follow_up_due",
        ):
            assert CommerceReason(code) is not None


class TestCommerceDecisionContext:
    def test_defaults_are_unknown_safe(self):
        ctx = _ctx()
        assert ctx.relationship_score is None
        assert ctx.buying_intent_score is None
        assert ctx.hours_since_last_offer is None
        assert ctx.recent_offer_count == 0
        assert ctx.has_active_offer is False
        assert ctx.creator_sales_enabled is True
        assert ctx.user_asked_to_buy is False

    def test_eligibility_is_required_and_reused(self):
        ctx = _ctx(eligibility=PolicyDecision(allowed=False, denial_reason="user_blocked"))
        assert ctx.eligibility.denial_reason == "user_blocked"

    def test_is_frozen(self):
        ctx = _ctx()
        with pytest.raises(FrozenInstanceError):
            ctx.recent_offer_count = 5  # type: ignore[misc]


class TestCommerceDecisionPolicy:
    def test_defaults_centralized(self):
        p = CommerceDecisionPolicy()
        assert p.offer_cooldown_hours == 24.0
        assert p.purchase_cooldown_hours == 6.0
        assert p.max_offers_per_24h == 2
        assert p.max_sales_attempts_per_24h == 3
        assert p.minimum_relationship_score == 0.60
        assert p.strong_buying_intent_score == 0.80
        assert p.moderate_buying_intent_score == 0.55

    def test_overrideable(self):
        p = CommerceDecisionPolicy(offer_cooldown_hours=1.0, minimum_relationship_score=0.9)
        assert p.offer_cooldown_hours == 1.0
        assert p.minimum_relationship_score == 0.9


class TestCommerceDecisionOutput:
    def test_carries_no_credentials_fields(self):
        import dataclasses

        fields = {f.name for f in dataclasses.fields(CommerceDecision)}
        assert fields == {
            "action",
            "reason_code",
            "allowed",
            "confidence",
            "requires_human_review",
            "metadata",
        }

    def test_metadata_defaults_empty(self):
        d = CommerceDecision(
            action=CommerceAction.NO_OFFER,
            reason_code=CommerceReason.NO_BUYING_SIGNAL,
            allowed=False,
            confidence=0.5,
        )
        assert d.metadata == {}
        assert d.requires_human_review is False

    def test_future_ai_contract_payload_shape(self):
        d = CommerceDecision(
            action=CommerceAction.OFFER_PPV,
            reason_code=CommerceReason.STRONG_BUYING_SIGNAL,
            allowed=True,
            confidence=0.95,
            requires_human_review=False,
        )
        assert d.action is CommerceAction.OFFER_PPV
        assert d.reason_code is CommerceReason.STRONG_BUYING_SIGNAL
        assert d.allowed is True
        assert d.confidence == 0.95
        assert d.requires_human_review is False


def _suggested_examples():
    """Sanity-check the spec's worked examples resolve as documented."""
    return [
        # low intent, offer 20 minutes ago -> NO_OFFER
        (
            _ctx(
                hours_since_last_offer=20 / 60,
                buying_intent_score=0.2,
                previous_offer_status="pending",
                has_active_offer=False,
            ),
            CommerceAction.NO_OFFER,
        ),
        # high intent + asks price -> PPV_OFFER
        (
            _ctx(user_asked_about_price=True, buying_intent_score=0.9),
            CommerceAction.OFFER_PPV,
        ),
        # previous interest, offer ignored, cooldown expired -> FOLLOW_UP
        (
            _ctx(
                previous_offer_status=OfferState.CLICKED.value,
                hours_since_last_offer=72.0,
                buying_intent_score=0.3,
            ),
            CommerceAction.FOLLOW_UP,
        ),
        # low relationship, no signal -> RELATIONSHIP_BUILDING
        (
            _ctx(relationship_score=0.3, messages_since_last_offer=8),
            CommerceAction.RELATIONSHIP_BUILDING,
        ),
    ]


@pytest.mark.parametrize(
    "context,expected_action",
    _suggested_examples(),
    ids=["recent_offer_low_intent", "explicit_price", "follow_up_due", "relationship_building"],
)
def test_spec_worked_examples(context, expected_action):
    decision = decide_commerce_action(context)
    assert decision.action is expected_action


# ═══════════════════════════════════════════════════════════════════════════
# Checkpoint 3 — Decision engine branches
# ═══════════════════════════════════════════════════════════════════════════


class TestEligibilityBranches:
    @pytest.mark.parametrize(
        "denial,expected_reason",
        [
            ("user_blocked", CommerceReason.USER_BLOCKED),
            ("user_opted_out", CommerceReason.USER_OPTED_OUT),
            ("creator_not_ready", CommerceReason.CREATOR_NOT_READY),
            ("product_unavailable", CommerceReason.PRODUCT_UNAVAILABLE),
            ("product_missing_sales_url", CommerceReason.PRODUCT_MISSING_SALES_URL),
            ("already_purchased", CommerceReason.ALREADY_PURCHASED),
            ("offer_exists", CommerceReason.OFFER_EXISTS),
            ("age_verification_required", CommerceReason.AGE_VERIFICATION_REQUIRED),
        ],
    )
    def test_eligibility_denial_maps_to_reason(self, denial, expected_reason):
        decision = decide_commerce_action(
            _ctx(eligibility=PolicyDecision(allowed=False, denial_reason=denial))
        )
        assert decision.action is CommerceAction.NO_OFFER
        assert decision.reason_code is expected_reason
        assert decision.allowed is False
        assert decision.confidence == 1.0

    def test_unknown_denial_reason_falls_back_to_policy_denied(self):
        decision = decide_commerce_action(
            _ctx(eligibility=PolicyDecision(allowed=False, denial_reason="mystery"))
        )
        assert decision.reason_code is CommerceReason.POLICY_DENIED

    def test_eligibility_denial_beats_everything(self):
        decision = decide_commerce_action(
            _ctx(
                eligibility=PolicyDecision(allowed=False, denial_reason="user_blocked"),
                user_asked_to_buy=True,
                buying_intent_score=0.99,
            )
        )
        assert decision.reason_code is CommerceReason.USER_BLOCKED


class TestSafetyBranches:
    def test_creator_sales_disabled(self):
        decision = decide_commerce_action(_ctx(creator_sales_enabled=False, user_asked_to_buy=True))
        assert decision.action is CommerceAction.NO_OFFER
        assert decision.reason_code is CommerceReason.CREATOR_NOT_READY
        assert decision.allowed is False

    def test_active_offer_blocks_regardless_of_intent(self):
        decision = decide_commerce_action(_ctx(has_active_offer=True, user_asked_to_buy=True))
        assert decision.reason_code is CommerceReason.OFFER_EXISTS
        assert decision.allowed is False

    def test_no_relevant_product(self):
        decision = decide_commerce_action(_ctx(has_relevant_product=False))
        assert decision.reason_code is CommerceReason.NO_RELEVANT_PRODUCT

    def test_purchase_cooldown_blocks_explicit_intent(self):
        decision = decide_commerce_action(
            _ctx(hours_since_last_purchase=2.0, user_asked_to_buy=True)
        )
        assert decision.reason_code is CommerceReason.RECENT_PURCHASE
        assert decision.allowed is False

    def test_purchase_cooldown_cleared(self):
        decision = decide_commerce_action(_ctx(hours_since_last_purchase=48.0))
        assert decision.reason_code is not CommerceReason.RECENT_PURCHASE

    def test_offer_cooldown_blocks(self):
        decision = decide_commerce_action(_ctx(hours_since_last_offer=1.0))
        assert decision.reason_code is CommerceReason.COOLDOWN_ACTIVE

    def test_too_many_offers(self):
        decision = decide_commerce_action(_ctx(recent_offer_count=2))
        assert decision.reason_code is CommerceReason.TOO_MANY_OFFERS

    def test_offer_budget_not_exceeded_serviceable(self):
        # 2 recent offers is the budget cap -> blocked at rule 7; 1 is fine.
        decision = decide_commerce_action(_ctx(recent_offer_count=1))
        assert decision.reason_code is not CommerceReason.TOO_MANY_OFFERS

    def test_too_many_sales_attempts(self):
        decision = decide_commerce_action(_ctx(recent_sales_attempt_count=3))
        assert decision.reason_code is CommerceReason.TOO_MANY_SALES_ATTEMPTS


class TestTimingBranches:
    def test_recent_decline_outcome(self):
        decision = decide_commerce_action(
            _ctx(
                hours_since_last_offer=1.0,
                previous_offer_status=OfferState.DECLINED.value,
            )
        )
        assert decision.reason_code is CommerceReason.RECENT_DECLINE

    def test_recent_ignore_outcome(self):
        decision = decide_commerce_action(
            _ctx(
                hours_since_last_offer=1.0,
                previous_offer_status=OfferState.CLICKED.value,
            )
        )
        assert decision.reason_code is CommerceReason.RECENT_IGNORE

    def test_recent_offer_generic_outcome(self):
        decision = decide_commerce_action(
            _ctx(
                hours_since_last_offer=1.0,
                previous_offer_status=OfferState.PENDING.value,
            )
        )
        assert decision.reason_code is CommerceReason.RECENT_OFFER

    def test_recent_offer_unknown_outcome_cooldown_active(self):
        decision = decide_commerce_action(
            _ctx(hours_since_last_offer=1.0, previous_offer_status=None)
        )
        assert decision.reason_code is CommerceReason.COOLDOWN_ACTIVE


class TestBuyingSignalBranches:
    def test_explicit_buy_request(self):
        decision = decide_commerce_action(_ctx(user_asked_to_buy=True))
        assert decision.action is CommerceAction.OFFER_PPV
        assert decision.reason_code is CommerceReason.STRONG_BUYING_SIGNAL
        assert decision.confidence == 0.95
        assert decision.allowed is True

    def test_explicit_price_question(self):
        decision = decide_commerce_action(_ctx(user_asked_about_price=True))
        assert decision.action is CommerceAction.OFFER_PPV

    def test_explicit_content_request(self):
        # Phase 99 fix: explicit content desire alone must NOT directly authorize PPV.
        # LLM `explicit_content_request` is advisory; routing goes via free-photo
        # deterministic path, not via `user_requested_content -> OFFER_PPV`.
        decision = decide_commerce_action(_ctx(user_requested_content=True))
        assert decision.action is not CommerceAction.OFFER_PPV
        assert decision.allowed is False
        # Should be relationship building / no offer, not commerce PPV
        assert decision.action in (CommerceAction.RELATIONSHIP_BUILDING, CommerceAction.NO_OFFER, CommerceAction.SOFT_OFFER)

    def test_strong_implicit_signal(self):
        decision = decide_commerce_action(_ctx(buying_intent_score=0.85))
        assert decision.action is CommerceAction.OFFER_PPV
        assert decision.reason_code is CommerceReason.STRONG_BUYING_SIGNAL
        assert decision.confidence == 0.85

    def test_strong_signal_boundary(self):
        decision = decide_commerce_action(_ctx(buying_intent_score=0.80))
        assert decision.action is CommerceAction.OFFER_PPV

    def test_moderate_signal(self):
        decision = decide_commerce_action(_ctx(buying_intent_score=0.60))
        assert decision.action is CommerceAction.SOFT_OFFER
        assert decision.reason_code is CommerceReason.MODERATE_BUYING_SIGNAL
        assert decision.confidence == 0.65

    def test_no_buying_signal(self):
        decision = decide_commerce_action(
            _ctx(messages_since_last_offer=0, relationship_score=None)
        )
        assert decision.action is CommerceAction.NO_OFFER
        assert decision.reason_code is CommerceReason.NO_BUYING_SIGNAL


class TestRelationshipBranches:
    def test_insufficient_relationship(self):
        decision = decide_commerce_action(_ctx(relationship_score=0.3, messages_since_last_offer=5))
        assert decision.action is CommerceAction.RELATIONSHIP_BUILDING
        assert decision.reason_code is CommerceReason.INSUFFICIENT_RELATIONSHIP

    def test_relationship_ready(self):
        decision = decide_commerce_action(_ctx(relationship_score=0.65))
        assert decision.action is CommerceAction.SOFT_OFFER
        assert decision.reason_code is CommerceReason.RELATIONSHIP_READY
        assert decision.confidence == 0.60

    def test_relationship_ready_boundary(self):
        decision = decide_commerce_action(_ctx(relationship_score=0.60))
        assert decision.action is CommerceAction.SOFT_OFFER

    def test_relationship_building_no_scores_active_chat(self):
        decision = decide_commerce_action(_ctx(messages_since_last_offer=3))
        assert decision.action is CommerceAction.RELATIONSHIP_BUILDING
        assert decision.reason_code is CommerceReason.NO_BUYING_SIGNAL


class TestFollowUpBranches:
    @pytest.mark.parametrize(
        "status",
        [
            OfferState.DECLINED.value,
            OfferState.REVOKED.value,
            OfferState.CLICKED.value,
            OfferState.EXPIRED.value,
        ],
    )
    def test_follow_up_due_after_outcome(self, status):
        decision = decide_commerce_action(
            _ctx(previous_offer_status=status, hours_since_last_offer=72.0)
        )
        assert decision.action is CommerceAction.FOLLOW_UP
        assert decision.reason_code is CommerceReason.FOLLOW_UP_DUE
        assert decision.confidence == 0.75
        assert decision.allowed is True

    def test_follow_up_blocks_moderate_signal(self):
        """A due follow-up wins over a moderate implicit signal."""
        decision = decide_commerce_action(
            _ctx(
                previous_offer_status=OfferState.CLICKED.value,
                hours_since_last_offer=72.0,
                buying_intent_score=0.60,
            )
        )
        assert decision.action is CommerceAction.FOLLOW_UP

    def test_follow_up_loses_to_explicit_intent(self):
        decision = decide_commerce_action(
            _ctx(
                previous_offer_status=OfferState.CLICKED.value,
                hours_since_last_offer=72.0,
                user_asked_about_price=True,
            )
        )
        assert decision.action is CommerceAction.OFFER_PPV

    def test_follow_up_blocked_by_cooldown(self):
        decision = decide_commerce_action(
            _ctx(
                previous_offer_status=OfferState.DECLINED.value,
                hours_since_last_offer=1.0,
            )
        )
        assert decision.action is CommerceAction.NO_OFFER
        assert decision.reason_code is CommerceReason.RECENT_DECLINE


class TestHumanReview:
    def test_conflicting_signals_explicit_buy_after_decline(self):
        decision = decide_commerce_action(
            _ctx(
                previous_offer_status=OfferState.DECLINED.value,
                hours_since_last_offer=72.0,
                user_asked_to_buy=True,
            )
        )
        assert decision.action is CommerceAction.OFFER_PPV
        assert decision.requires_human_review is True

    def test_purchase_history_inconsistency_flags_review(self):
        """Recent purchases recorded but eligibility allows -> attribution lag
        or data inconsistency; flag review on every sell move."""
        decision = decide_commerce_action(_ctx(recent_purchase_count=1, buying_intent_score=0.9))
        assert decision.action is CommerceAction.OFFER_PPV
        assert decision.requires_human_review is True

    def test_clean_state_does_not_flag_review(self):
        decision = decide_commerce_action(_ctx(buying_intent_score=0.9))
        assert decision.requires_human_review is False


class TestEligibilityIntegration:
    """Phase 5.2 eligibility output feeds the Phase 5.3A decision engine
    without duplicating hard-policy logic."""

    def test_hard_denial_flows_into_no_offer(self):
        from commerce.eligibility import (
            OfferContext,
            ProductEligibilityState,
            UserEligibilityState,
            evaluate_ppv_eligibility,
        )

        eligibility = evaluate_ppv_eligibility(
            UserEligibilityState(),
            ProductEligibilityState(is_accessible=False),
            OfferContext(creator_ready=True),
        )
        assert eligibility.allowed is False

        decision = decide_commerce_action(_ctx(eligibility=eligibility, user_asked_to_buy=True))
        assert decision.action is CommerceAction.NO_OFFER
        assert decision.reason_code is CommerceReason.PRODUCT_UNAVAILABLE

    def test_eligible_fan_with_intent_gets_offer(self):
        from commerce.eligibility import (
            OfferContext,
            ProductEligibilityState,
            UserEligibilityState,
            evaluate_ppv_eligibility,
        )

        eligibility = evaluate_ppv_eligibility(
            UserEligibilityState(),
            ProductEligibilityState(sales_url="https://fangate.test/p/11"),
            OfferContext(creator_ready=True),
        )
        assert eligibility.allowed is True

        decision = decide_commerce_action(
            _ctx(eligibility=eligibility, user_asked_about_price=True)
        )
        assert decision.action is CommerceAction.OFFER_PPV
        assert decision.requires_human_review is False


class TestDeterminism:
    def test_same_input_same_output(self):
        ctx = _ctx(
            user_id=42,
            creator_id=7,
            relationship_score=0.7,
            buying_intent_score=0.4,
            messages_since_last_offer=9,
            hours_since_last_offer=30.0,
            previous_offer_status=OfferState.CLICKED.value,
        )
        first = decide_commerce_action(ctx)
        for _ in range(4):
            again = decide_commerce_action(ctx)
            assert again == first
            assert again is not first  # fresh but identical objects

    def test_policy_change_changes_output_predictably(self):
        strict = CommerceDecisionPolicy(offer_cooldown_hours=72.0)
        lenient = CommerceDecisionPolicy(offer_cooldown_hours=2.0)
        ctx = _ctx(hours_since_last_offer=24.0, buying_intent_score=0.9)
        assert decide_commerce_action(ctx, strict).action is CommerceAction.NO_OFFER
        assert decide_commerce_action(ctx, lenient).action is CommerceAction.OFFER_PPV


class TestPurity:
    def test_no_external_dependencies_in_source(self):
        """The engine module must not touch Redis, PostgreSQL, HTTP, Fangate,
        the event bus, or any LLM client."""
        src = Path(commerce_decision_path()).read_text(encoding="utf-8")
        for forbidden in (
            "redis",
            "postgres",
            "get_pool",
            "httpx",
            "requests",
            "fangate",
            "event_bus",
            "websocket",
            "gemini",
            "openai",
        ):
            assert forbidden not in src, f"decision engine must not reference {forbidden}"

    def test_module_imports_only_domain_modules(self):
        """The decision module must import only stdlib + the domain models."""
        import ast

        tree = ast.parse(Path(commerce_decision_path()).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert imported <= {"enum", "dataclasses", "typing", "commerce.models"}


def commerce_decision_path() -> str:
    import commerce.decision

    return commerce.decision.__file__
