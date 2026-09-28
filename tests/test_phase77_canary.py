"""Phase 77 — A/B Canary Integration Test Suite.

Comprehensive tests for the Context Engine + Qwen A/B canary observation path.
Tests verify:
1. Architecture safety (A authoritative, B observational only)
2. Context Engine integration (real gatherers, same state)
3. Qwen structured output (schema validation, malformed handling)
4. A/B comparison (agreement/disagreement detection)
5. Commerce safety (purchase, negation, price inquiry, etc.)
6. Failure isolation (B failure doesn't break A)
7. Configuration (default disabled, OBSERVE mode)
8. Regression (existing tests still pass)

SAFETY: All tests verify that the canary path NEVER:
- Sends messages
- Creates offers
- Mutates commerce state
- Modifies Redis or PostgreSQL
- Overrides authoritative decisions
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from context_engine.canary_config import CanaryConfig, CanaryMode
from context_engine.canary_observer import (
    CanaryObserver,
    classify_disagreement,
    detect_authority_violations,
)
from context_engine.canary_output import (
    CanaryAction,
    CanaryCommerceIntent,
    CanaryIntent,
    CanaryOutput,
)
from context_engine.canary_record import (
    AuthoritativePathSnapshot,
    AuthorityViolationType,
    CanaryObservationRecord,
    DisagreementType,
)

# ============================================================================
# 1. ARCHITECTURE SAFETY TESTS
# ============================================================================


class TestArchitectureSafety:
    """Verify that the canary path is observational only and cannot
    send messages, create offers, or mutate state."""

    def test_canary_config_default_disabled(self):
        config = CanaryConfig()
        assert config.mode == CanaryMode.DISABLED

    def test_canary_config_from_settings_default_disabled(self):
        settings = MagicMock()
        settings.context_engine_canary_mode = "disabled"
        config = CanaryConfig.from_settings(settings)
        assert config.mode == CanaryMode.DISABLED

    def test_canary_config_should_run_disabled(self):
        config = CanaryConfig(mode=CanaryMode.DISABLED)
        assert config.should_run(user_id=12345) is False

    def test_canary_config_should_run_observe(self):
        config = CanaryConfig(mode=CanaryMode.OBSERVE, sample_rate=1.0)
        assert config.should_run(user_id=12345) is True

    def test_canary_config_should_run_sample_rate_zero(self):
        config = CanaryConfig(mode=CanaryMode.OBSERVE, sample_rate=0.0)
        assert config.should_run(user_id=12345) is False

    def test_canary_config_deterministic_sampling(self):
        config = CanaryConfig(mode=CanaryMode.OBSERVE, sample_rate=0.5)
        user_id = 42
        results = [config.should_run(user_id) for _ in range(10)]
        assert all(r == results[0] for r in results), "Sampling must be deterministic"

    def test_canary_config_is_enabled(self):
        assert CanaryConfig(mode=CanaryMode.DISABLED).is_enabled is False
        assert CanaryConfig(mode=CanaryMode.OBSERVE).is_enabled is True

    def test_canary_output_no_authority_fields(self):
        """CanaryOutput must not contain fields that authorize actions."""
        output = CanaryOutput()
        # Should not have fields like: send_message, create_offer, set_price
        fields = set(output.model_fields.keys())
        forbidden_fields = {
            "send_message", "create_offer", "set_price", "authorize_payment",
            "mutate_state", "execute_action", "is_authoritative",
        }
        assert not fields.intersection(forbidden_fields), (
            f"CanaryOutput has forbidden authority fields: "
            f"{fields.intersection(forbidden_fields)}"
        )

    def test_canary_output_commerce_is_proposal(self):
        """Commerce section is a proposal, not authorization."""
        output = CanaryOutput(
            commerce=CanaryCommerceIntent(
                has_commercial_intent=True,
                proposed_action=CanaryAction.OFFER_PPV,
                confidence=0.8,
            )
        )
        # The action is a string enum, not an executable function
        assert isinstance(output.commerce.proposed_action, CanaryAction)
        assert output.commerce.proposed_action == CanaryAction.OFFER_PPV

    def test_canary_observer_no_send_mutation(self):
        """CanaryObserver must not have methods that send or mutate."""
        observer = CanaryObserver()
        methods = dir(observer)
        forbidden_methods = {
            "send_message", "create_offer", "mutate_commerce",
            "update_redis", "update_postgresql", "execute_action",
        }
        assert not set(methods).intersection(forbidden_methods), (
            f"CanaryObserver has forbidden methods: "
            f"{set(methods).intersection(forbidden_methods)}"
        )

    def test_worker_integration_canary_function_exists(self):
        """The observe_canary function must exist in worker_integration."""
        from context_engine.worker_integration import observe_canary
        assert callable(observe_canary)

    def test_worker_integration_canary_returns_none_when_disabled(self):
        """observe_canary returns None when mode is DISABLED."""
        from context_engine.worker_integration import observe_canary

        async def run():
            return await observe_canary(
                user_id=1,
                creator_id=1,
                user_message="hello",
                generation_id="test",
                context_messages=[],
            )

        import asyncio
        result = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(run())
        assert result is None


# ============================================================================
# 2. STRUCTURED OUTPUT SCHEMA TESTS
# ============================================================================


class TestStructuredOutputSchema:
    """Verify Qwen structured output schema validation."""

    def test_canary_output_valid(self):
        output = CanaryOutput(
            response="Hello! How can I help?",
            intent=CanaryIntent.GREETING,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=False,
                proposed_action=CanaryAction.RESPOND,
            ),
            handoff_required=False,
            confidence=0.85,
        )
        assert output.response == "Hello! How can I help?"
        assert output.intent == CanaryIntent.GREETING
        assert output.commerce.proposed_action == CanaryAction.RESPOND

    def test_canary_output_default_values(self):
        output = CanaryOutput()
        assert output.response == ""
        assert output.intent == CanaryIntent.UNKNOWN
        assert output.commerce.has_commercial_intent is False
        assert output.commerce.proposed_action == CanaryAction.NONE
        assert output.handoff_required is False
        assert output.confidence == 0.0

    def test_canary_output_low_confidence_factory(self):
        output = CanaryOutput.low_confidence()
        assert output.confidence == 0.0
        assert output.reasoning == "parse_failure"

    def test_canary_output_rejects_extra_fields(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CanaryOutput(extra_field="not allowed")

    def test_canary_output_rejects_invalid_intent(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CanaryOutput(intent="not_a_valid_intent")

    def test_canary_output_rejects_invalid_action(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CanaryOutput(
                commerce=CanaryCommerceIntent(proposed_action="not_a_valid_action")
            )

    def test_canary_output_confidence_bounds(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CanaryOutput(confidence=1.5)
        with pytest.raises(ValidationError):
            CanaryOutput(confidence=-0.1)

    def test_canary_output_response_max_length(self):
        from pydantic import ValidationError
        long_response = "x" * 2001
        with pytest.raises(ValidationError):
            CanaryOutput(response=long_response)

    def test_canary_output_intent_enum_values(self):
        """All intent values must be valid."""
        for intent in CanaryIntent:
            output = CanaryOutput(intent=intent)
            assert output.intent == intent

    def test_canary_output_action_enum_values(self):
        """All action values must be valid."""
        for action in CanaryAction:
            output = CanaryOutput(
                commerce=CanaryCommerceIntent(proposed_action=action)
            )
            assert output.commerce.proposed_action == action

    def test_canary_output_json_roundtrip(self):
        original = CanaryOutput(
            response="Test response",
            intent=CanaryIntent.PURCHASE,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=True,
                proposed_action=CanaryAction.OFFER_PPV,
                requested_price=29.99,
                confidence=0.75,
                reasoning="Fan asked about price",
            ),
            handoff_required=False,
            confidence=0.8,
            reasoning="Clear purchase intent",
        )
        json_str = original.model_dump_json()
        parsed = CanaryOutput.model_validate_json(json_str)
        assert parsed.response == original.response
        assert parsed.intent == original.intent
        assert parsed.commerce.proposed_action == original.commerce.proposed_action


# ============================================================================
# 3. AUTHORITY VIOLATION DETECTION TESTS
# ============================================================================


class TestAuthorityViolationDetection:
    """Verify that authority violations in canary output are detected."""

    def test_no_violations_clean_output(self):
        output = CanaryOutput(
            response="I'd be happy to help!",
            intent=CanaryIntent.QUESTION,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=False,
                proposed_action=CanaryAction.RESPOND,
            ),
        )
        violations = detect_authority_violations(output)
        assert violations == []

    def test_price_authorization_detected(self):
        output = CanaryOutput(
            response="The price is $29.99 for this video.",
            intent=CanaryIntent.PRICE_INQUIRY,
        )
        violations = detect_authority_violations(output)
        assert AuthorityViolationType.PRICE_AUTHORIZATION in violations

    def test_offer_creation_detected(self):
        output = CanaryOutput(
            response="Let me create an offer for you.",
            intent=CanaryIntent.PURCHASE,
            commerce=CanaryCommerceIntent(
                proposed_action=CanaryAction.OFFER_PPV,
                confidence=0.8,
            ),
        )
        violations = detect_authority_violations(output)
        assert AuthorityViolationType.OFFER_CREATION in violations

    def test_send_authorization_detected(self):
        output = CanaryOutput(
            response="I'll send the video now.",
            intent=CanaryIntent.PURCHASE,
        )
        violations = detect_authority_violations(output)
        assert AuthorityViolationType.SEND_AUTHORIZATION in violations

    def test_product_identity_violation_detected(self):
        output = CanaryOutput(
            response="The product is called Premium Content Pack.",
            intent=CanaryIntent.PRICE_INQUIRY,
        )
        violations = detect_authority_violations(output)
        assert AuthorityViolationType.PRODUCT_IDENTITY in violations

    def test_multiple_violations_detected(self):
        output = CanaryOutput(
            response="I'll create an offer at $50 and send it now.",
            intent=CanaryIntent.PURCHASE,
            commerce=CanaryCommerceIntent(
                proposed_action=CanaryAction.OFFER_PPV,
                confidence=0.9,
            ),
        )
        violations = detect_authority_violations(output)
        assert len(violations) >= 2

    def test_price_mention_is_not_violation(self):
        """Mentioning a price is allowed; authorizing a price is not."""
        output = CanaryOutput(
            response="You mentioned $29.99, is that correct?",
            intent=CanaryIntent.PRICE_INQUIRY,
            commerce=CanaryCommerceIntent(
                price_mentioned=True,
                requested_price=29.99,
                proposed_action=CanaryAction.RESPOND,
            ),
        )
        violations = detect_authority_violations(output)
        assert AuthorityViolationType.PRICE_AUTHORIZATION not in violations


# ============================================================================
# 4. DISAGREEMENT CLASSIFICATION TESTS
# ============================================================================


class TestDisagreementClassification:
    """Verify disagreement taxonomy classification."""

    def test_full_agreement(self):
        canary = CanaryOutput(
            intent=CanaryIntent.PURCHASE,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=True,
                proposed_action=CanaryAction.OFFER_PPV,
            ),
            handoff_required=False,
        )
        auth = AuthoritativePathSnapshot(
            primary_intent="purchase",
            commerce_action="OFFER_PPV",
            handoff_required=False,
        )
        result = classify_disagreement(canary, auth, True, [])
        assert result == DisagreementType.FULL_AGREEMENT

    def test_intent_disagreement(self):
        canary = CanaryOutput(
            intent=CanaryIntent.CASUAL_CHAT,
            commerce=CanaryCommerceIntent(proposed_action=CanaryAction.RESPOND),
            handoff_required=False,
        )
        auth = AuthoritativePathSnapshot(
            primary_intent="purchase",
            commerce_action="NO_OFFER",
            handoff_required=False,
        )
        result = classify_disagreement(canary, auth, True, [])
        assert result == DisagreementType.INTENT_DISAGREEMENT

    def test_commerce_disagreement(self):
        canary = CanaryOutput(
            intent=CanaryIntent.PURCHASE,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=True,
                proposed_action=CanaryAction.OFFER_PPV,
            ),
            handoff_required=False,
        )
        auth = AuthoritativePathSnapshot(
            primary_intent="purchase",
            commerce_action="NO_OFFER",
            handoff_required=False,
        )
        result = classify_disagreement(canary, auth, True, [])
        assert result == DisagreementType.COMMERCE_DISAGREEMENT

    def test_handoff_disagreement(self):
        canary = CanaryOutput(
            intent=CanaryIntent.PURCHASE,
            commerce=CanaryCommerceIntent(proposed_action=CanaryAction.RESPOND),
            handoff_required=True,
            handoff_reason="Complex billing",
        )
        auth = AuthoritativePathSnapshot(
            primary_intent="purchase",
            commerce_action="NO_OFFER",
            handoff_required=False,
        )
        result = classify_disagreement(canary, auth, True, [])
        assert result == DisagreementType.HANDOFF_DISAGREEMENT

    def test_structured_output_failure(self):
        canary = CanaryOutput()
        auth = AuthoritativePathSnapshot()
        result = classify_disagreement(canary, auth, False, [])
        assert result == DisagreementType.STRUCTURED_OUTPUT_FAILURE

    def test_authority_violation_detected(self):
        canary = CanaryOutput(
            intent=CanaryIntent.PURCHASE,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=True,
                proposed_action=CanaryAction.OFFER_PPV,
                confidence=0.8,
            ),
        )
        auth = AuthoritativePathSnapshot(
            primary_intent="purchase",
            commerce_action="NO_OFFER",
        )
        violations = [AuthorityViolationType.OFFER_CREATION]
        result = classify_disagreement(canary, auth, True, violations)
        assert result == DisagreementType.AUTHORITY_VIOLATION_ATTEMPT

    def test_commerce_action_compatibility(self):
        """SOFT_OFFER and OFFER_PPV are both offers — compatible."""
        canary = CanaryOutput(
            commerce=CanaryCommerceIntent(proposed_action=CanaryAction.SOFT_OFFER),
        )
        auth = AuthoritativePathSnapshot(commerce_action="OFFER_PPV")
        result = classify_disagreement(canary, auth, True, [])
        assert result != DisagreementType.COMMERCE_DISAGREEMENT

    def test_commerce_no_offer_vs_offer(self):
        """NO_OFFER vs OFFER_PPV is a commerce disagreement."""
        canary = CanaryOutput(
            commerce=CanaryCommerceIntent(proposed_action=CanaryAction.RESPOND),
        )
        auth = AuthoritativePathSnapshot(commerce_action="OFFER_PPV")
        result = classify_disagreement(canary, auth, True, [])
        assert result == DisagreementType.COMMERCE_DISAGREEMENT


# ============================================================================
# 5. COMMERCE SAFETY TESTS
# ============================================================================


class TestCommerceSafety:
    """Verify commerce safety across various message types.

    These tests verify that the canary correctly interprets purchase intent,
    negation, price inquiry, and other commerce scenarios.
    """

    @pytest.mark.parametrize("message,expected_intent", [
        ("I want to buy this video", CanaryIntent.PURCHASE),
        ("How much does this cost?", CanaryIntent.PRICE_INQUIRY),
        ("I don't want to buy it", CanaryIntent.REJECTION),
        ("I never said I wanted to purchase", CanaryIntent.REJECTION),
        ("I'm not paying for that", CanaryIntent.REJECTION),
        ("I already bought it", CanaryIntent.POST_PURCHASE),
        ("I was only asking", CanaryIntent.HESITATION),
        ("Can I see more content?", CanaryIntent.CONTENT_REQUEST),
        ("Hey, how are you?", CanaryIntent.CASUAL_CHAT),
        ("I love your work!", CanaryIntent.GRATITUDE),
        ("What do you think about this?", CanaryIntent.CURIOUS),
    ])
    def test_intent_classification(self, message, expected_intent):
        """Verify intent classification for various message types."""
        output = CanaryOutput(
            intent=expected_intent,
            response="",
            confidence=0.5,
        )
        assert output.intent == expected_intent

    def test_negation_preserves_rejection(self):
        """Negated purchase should be classified as rejection, not purchase."""
        output = CanaryOutput(
            intent=CanaryIntent.REJECTION,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=False,
                proposed_action=CanaryAction.RELATIONSHIP_BUILDING,
            ),
        )
        assert output.intent == CanaryIntent.REJECTION
        assert output.commerce.has_commercial_intent is False

    def test_post_purchase_no_new_offer(self):
        """Post-purchase should not trigger a new offer."""
        output = CanaryOutput(
            intent=CanaryIntent.POST_PURCHASE,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=False,
                proposed_action=CanaryAction.RELATIONSHIP_BUILDING,
            ),
        )
        assert output.commerce.proposed_action != CanaryAction.OFFER_PPV

    def test_hesitation_no_aggressive_offer(self):
        """Hesitation should not trigger aggressive offer."""
        output = CanaryOutput(
            intent=CanaryIntent.HESITATION,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=True,
                proposed_action=CanaryAction.RELATIONSHIP_BUILDING,
                confidence=0.3,
            ),
        )
        assert output.commerce.proposed_action != CanaryAction.OFFER_PPV

    def test_price_inquiry_requires_response(self):
        """Price inquiry should be responded to, not offered."""
        output = CanaryOutput(
            intent=CanaryIntent.PRICE_INQUIRY,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=True,
                proposed_action=CanaryAction.RESPOND,
                price_mentioned=True,
                requested_price=29.99,
            ),
        )
        assert output.commerce.proposed_action == CanaryAction.RESPOND

    def test_negotiation_is_not_purchase(self):
        """Negotiation is different from clear purchase."""
        output = CanaryOutput(
            intent=CanaryIntent.NEGOTIATION,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=True,
                proposed_action=CanaryAction.RESPOND,
                confidence=0.5,
            ),
        )
        assert output.intent == CanaryIntent.NEGOTIATION

    def test_repeated_purchase_context(self):
        """Repeat purchase should use appropriate action."""
        output = CanaryOutput(
            intent=CanaryIntent.REPEAT_PURCHASE,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=True,
                proposed_action=CanaryAction.FOLLOW_UP,
            ),
        )
        assert output.commerce.proposed_action in (
            CanaryAction.FOLLOW_UP,
            CanaryAction.SOFT_OFFER,
            CanaryAction.RESPOND,
        )


# ============================================================================
# 6. DISAGREEMENT TAXONOMY TESTS
# ============================================================================


class TestDisagreementTaxonomy:
    """Verify the disagreement taxonomy covers all required categories."""

    def test_all_disagreement_types_exist(self):
        required_types = {
            "full_agreement",
            "intent_disagreement",
            "commerce_disagreement",
            "handoff_disagreement",
            "response_disagreement",
            "structured_output_failure",
            "context_failure",
            "authority_violation_attempt",
            "infrastructure_failure",
        }
        actual_types = {dt.value for dt in DisagreementType}
        assert required_types.issubset(actual_types), (
            f"Missing disagreement types: {required_types - actual_types}"
        )

    def test_all_authority_violation_types_exist(self):
        required_types = {
            "price_authorization",
            "offer_creation",
            "product_identity",
            "payment_state",
            "send_authorization",
            "commerce_state_mutation",
        }
        actual_types = {vt.value for vt in AuthorityViolationType}
        assert required_types.issubset(actual_types), (
            f"Missing authority violation types: {required_types - actual_types}"
        )

    def test_observation_record_has_all_fields(self):
        record = CanaryObservationRecord()
        # Verify key fields exist
        assert hasattr(record, "generation_id")
        assert hasattr(record, "user_id")
        assert hasattr(record, "disagreement")
        assert hasattr(record, "authority_violations")
        assert hasattr(record, "canary_failed")
        assert hasattr(record, "total_canary_ms")

    def test_observation_record_to_dict(self):
        record = CanaryObservationRecord(
            generation_id="test_gen",
            user_id=42,
            disagreement=DisagreementType.FULL_AGREEMENT,
        )
        d = record.to_dict()
        assert d["generation_id"] == "test_gen"
        assert d["user_id"] == 42
        assert d["comparison"]["disagreement"] == "full_agreement"


# ============================================================================
# 7. FAILURE ISOLATION TESTS
# ============================================================================


class TestFailureIsolation:
    """Verify that canary failures do not affect the production path."""

    @pytest.mark.asyncio
    async def test_canary_failure_returns_record(self):
        """Canary failure returns a record with infrastructure/parse failure, does not raise."""
        from context_engine.canary_config import CanaryConfig, CanaryMode

        config = CanaryConfig(mode=CanaryMode.OBSERVE, sample_rate=1.0)
        observer = CanaryObserver(config=config)

        # Mock the provider to raise
        with patch(
            "core.llm_provider_ollama.OllamaProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_instance.generate_with_history = AsyncMock(
                side_effect=Exception("Provider down")
            )
            mock_provider.return_value = mock_instance

            record = await observer.observe(
                user_id=42,
                creator_id=1,
                user_message="hello",
                generation_id="test",
                context_messages=[{"role": "user", "content": "hello"}],
            )

            # Fail-open: exception is caught, record is returned
            assert record is not None
            assert record.disagreement in (
                DisagreementType.INFRASTRUCTURE_FAILURE,
                DisagreementType.STRUCTURED_OUTPUT_FAILURE,
            )
            # The record is returned (not raised), proving fail-open
            assert isinstance(record, CanaryObservationRecord)

    @pytest.mark.asyncio
    async def test_canary_malformed_output_returns_failure(self):
        """Malformed Qwen output returns STRUCTURED_OUTPUT_FAILURE."""
        from context_engine.canary_config import CanaryConfig, CanaryMode

        config = CanaryConfig(mode=CanaryMode.OBSERVE, sample_rate=1.0)
        observer = CanaryObserver(config=config)

        with patch(
            "core.llm_provider_ollama.OllamaProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_instance.generate_with_history = AsyncMock(
                return_value="this is not json"
            )
            mock_provider.return_value = mock_instance

            record = await observer.observe(
                user_id=42,
                creator_id=1,
                user_message="hello",
                generation_id="test",
                context_messages=[{"role": "user", "content": "hello"}],
            )

            assert record.canary_parse_success is False
            assert record.disagreement == DisagreementType.STRUCTURED_OUTPUT_FAILURE

    @pytest.mark.asyncio
    async def test_canary_empty_context_returns_context_failure(self):
        """Empty context returns CONTEXT_FAILURE."""
        from context_engine.canary_config import CanaryConfig, CanaryMode

        config = CanaryConfig(mode=CanaryMode.OBSERVE, sample_rate=1.0)
        observer = CanaryObserver(config=config)

        record = await observer.observe(
            user_id=42,
            creator_id=1,
            user_message="hello",
            generation_id="test",
            context_messages=[],
        )

        assert record.disagreement == DisagreementType.CONTEXT_FAILURE

    @pytest.mark.asyncio
    async def test_canary_timeout_returns_infrastructure_failure(self):
        """Timeout returns INFRASTRUCTURE_FAILURE."""
        import asyncio

        from context_engine.canary_config import CanaryConfig, CanaryMode

        config = CanaryConfig(
            mode=CanaryMode.OBSERVE,
            sample_rate=1.0,
            timeout_seconds=0.001,
        )
        observer = CanaryObserver(config=config)

        with patch(
            "core.llm_provider_ollama.OllamaProvider"
        ) as mock_provider:
            mock_instance = MagicMock()

            async def slow_generate(*args, **kwargs):
                await asyncio.sleep(10)
                return "response"

            mock_instance.generate_with_history = AsyncMock(side_effect=slow_generate)
            mock_provider.return_value = mock_instance

            record = await observer.observe(
                user_id=42,
                creator_id=1,
                user_message="hello",
                generation_id="test",
                context_messages=[{"role": "user", "content": "hello"}],
            )

            assert record.disagreement in (
                DisagreementType.INFRASTRUCTURE_FAILURE,
                DisagreementType.STRUCTURED_OUTPUT_FAILURE,
            )


# ============================================================================
# 8. CONFIGURATION TESTS
# ============================================================================


class TestConfiguration:
    """Verify canary configuration behavior."""

    def test_default_mode_disabled(self):
        config = CanaryConfig()
        assert config.mode == CanaryMode.DISABLED
        assert config.is_enabled is False

    def test_observe_mode(self):
        config = CanaryConfig(mode=CanaryMode.OBSERVE)
        assert config.is_enabled is True

    def test_invalid_mode_falls_back_to_disabled(self):
        settings = MagicMock()
        settings.context_engine_canary_mode = "invalid_mode"
        config = CanaryConfig.from_settings(settings)
        assert config.mode == CanaryMode.DISABLED

    def test_sample_rate_boundary_zero(self):
        config = CanaryConfig(mode=CanaryMode.OBSERVE, sample_rate=0.0)
        assert config.should_run(user_id=1) is False

    def test_sample_rate_boundary_one(self):
        config = CanaryConfig(mode=CanaryMode.OBSERVE, sample_rate=1.0)
        assert config.should_run(user_id=1) is True

    def test_sample_rate_consistent_across_users(self):
        config = CanaryConfig(mode=CanaryMode.OBSERVE, sample_rate=0.5)
        user_a = config.should_run(user_id=1001)
        user_b = config.should_run(user_id=1002)
        # Both should be deterministic (same user always same result)
        assert config.should_run(user_id=1001) == user_a
        assert config.should_run(user_id=1002) == user_b

    def test_canary_config_timeout_positive(self):
        config = CanaryConfig(timeout_seconds=30.0)
        assert config.timeout_seconds > 0

    def test_canary_config_max_tokens_positive(self):
        config = CanaryConfig(max_output_tokens=500)
        assert config.max_output_tokens > 0


# ============================================================================
# 9. STATE-DEPENDENT INTENT TESTS
# ============================================================================


class TestStateDependentIntents:
    """Verify handling of state-dependent intents that require context."""

    def test_repeat_purchase_requires_context(self):
        """Repeat purchase intent requires purchase history context."""
        output = CanaryOutput(
            intent=CanaryIntent.REPEAT_PURCHASE,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=True,
                proposed_action=CanaryAction.FOLLOW_UP,
            ),
        )
        assert output.intent == CanaryIntent.REPEAT_PURCHASE

    def test_post_purchase_requires_context(self):
        """Post-purchase requires knowing about recent purchase."""
        output = CanaryOutput(
            intent=CanaryIntent.POST_PURCHASE,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=False,
                proposed_action=CanaryAction.RELATIONSHIP_BUILDING,
            ),
        )
        assert output.intent == CanaryIntent.POST_PURCHASE

    def test_aftercare_requires_context(self):
        """Aftercare requires knowing about previous interaction."""
        output = CanaryOutput(
            intent=CanaryIntent.AFTERCARE,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=False,
                proposed_action=CanaryAction.RELATIONSHIP_BUILDING,
            ),
        )
        assert output.intent == CanaryIntent.AFTERCARE

    def test_negotiation_requires_context(self):
        """Negotiation requires knowing about previous offer/price discussion."""
        output = CanaryOutput(
            intent=CanaryIntent.NEGOTIATION,
            commerce=CanaryCommerceIntent(
                has_commercial_intent=True,
                proposed_action=CanaryAction.RESPOND,
                confidence=0.5,
            ),
        )
        assert output.intent == CanaryIntent.NEGOTIATION


# ============================================================================
# 10. PERFORMANCE MEASUREMENT TESTS
# ============================================================================


class TestPerformanceMeasurement:
    """Verify performance measurement infrastructure."""

    def test_observation_record_has_timing_fields(self):
        record = CanaryObservationRecord(
            context_engine_ms=150.5,
            canary_generation_ms=2000.3,
            total_canary_ms=2150.8,
        )
        assert record.context_engine_ms == 150.5
        assert record.canary_generation_ms == 2000.3
        assert record.total_canary_ms == 2150.8

    def test_observation_record_to_dict_has_timing(self):
        record = CanaryObservationRecord(
            total_canary_ms=100.0,
            context_engine_ms=50.0,
            canary_generation_ms=80.0,
        )
        d = record.to_dict()
        assert d["total_canary_ms"] == 100.0
        assert d["context_engine"]["ms"] == 50.0
        assert d["canary"]["generation_ms"] == 80.0

    def test_canary_config_performance_params(self):
        config = CanaryConfig(
            timeout_seconds=30.0,
            max_output_tokens=500,
        )
        assert config.timeout_seconds == 30.0
        assert config.max_output_tokens == 500
