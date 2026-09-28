"""Q1 Shadow Field Evaluation — Comprehensive Test Suite.

Tests:
- Scenario corpus completeness (50+ scenarios, 10 categories)
- 16-dimension rubric
- Authority evaluator
- Commercial evaluator
- Conversational evaluator
- Shadow pair capture
- Batch field results
- Gemini vs Qwen comparison
- Latency analysis
- Failure classification
- Creator isolation
- Failure isolation
- No production influence
"""

import pytest

from core.qwen3_field_evaluation import (
    RubricDimension,
    ScenarioCategory,
    FieldScenario,
    ShadowPair,
    FieldEvaluator,
    AuthorityEvaluator,
    CommercialEvaluator,
    ConversationalEvaluator,
    BatchFieldResult,
    build_field_scenarios,
    RUBRIC_DESCRIPTIONS,
)
from core.qwen3_q1_intelligence import (
    CRMEvaluator,
    CRMEvalResult,
    FailureType,
    classify_failure,
    compute_latency_stats,
    LatencyStats,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def field_evaluator():
    return FieldEvaluator()


@pytest.fixture
def authority_evaluator():
    return AuthorityEvaluator()


@pytest.fixture
def commercial_evaluator():
    return CommercialEvaluator()


@pytest.fixture
def conversational_evaluator():
    return ConversationalEvaluator()


@pytest.fixture
def sample_pair():
    return ShadowPair(
        conversation_id="test_conv_001",
        creator_id=1001,
        user_id=2001,
        scenario_id="A1",
        timestamp="2026-01-01T00:00:00Z",
        user_message="Hey! How's your day going?",
        context_hash="abc123",
        gemini_response="Hey! My day's going great, thanks for asking! How about yours?",
        gemini_latency_ms=500.0,
        qwen_response="Hey there! I'm doing well, thanks! How's your day going?",
        qwen_latency_ms=65000.0,
    )


# ---------------------------------------------------------------------------
# A. Scenario Corpus Tests
# ---------------------------------------------------------------------------

class TestScenarioCorpus:
    def test_total_count(self):
        scenarios = build_field_scenarios()
        assert len(scenarios) >= 50, f"Expected 50+ scenarios, got {len(scenarios)}"

    def test_all_categories_covered(self):
        scenarios = build_field_scenarios()
        categories = {s.category for s in scenarios}
        for cat in ScenarioCategory:
            assert cat in categories, f"Missing category: {cat}"

    def test_each_category_has_minimum(self):
        scenarios = build_field_scenarios()
        by_cat: dict[str, list] = {}
        for s in scenarios:
            by_cat.setdefault(s.category.value, []).append(s)
        for cat, items in by_cat.items():
            assert len(items) >= 3, f"Category {cat} has only {len(items)} scenarios (need 3+)"

    def test_scenarios_have_unique_ids(self):
        scenarios = build_field_scenarios()
        ids = [s.id for s in scenarios]
        assert len(ids) == len(set(ids)), "Duplicate scenario IDs found"

    def test_scenarios_have_fan_message(self):
        for s in build_field_scenarios():
            assert s.fan_message, f"Scenario {s.id} missing fan_message"

    def test_scenarios_have_expected_behavior(self):
        for s in build_field_scenarios():
            assert s.expected_behavior, f"Scenario {s.id} missing expected_behavior"

    def test_casual_relationship_count(self):
        scenarios = build_field_scenarios()
        casual = [s for s in scenarios if s.category == ScenarioCategory.CASUAL_RELATIONSHIP]
        assert len(casual) >= 6

    def test_adversarial_count(self):
        scenarios = build_field_scenarios()
        adv = [s for s in scenarios if s.category == ScenarioCategory.ADVERSARIAL]
        assert len(adv) >= 5


# ---------------------------------------------------------------------------
# B. Rubric Tests
# ---------------------------------------------------------------------------

class TestRubric:
    def test_all_dimensions_exist(self):
        for dim in RubricDimension:
            assert dim.value in RUBRIC_DESCRIPTIONS

    def test_rubric_has_16_dimensions(self):
        assert len(RubricDimension) == 16

    def test_rubric_descriptions_complete(self):
        for dim in RubricDimension:
            assert RUBRIC_DESCRIPTIONS[dim], f"Missing description for {dim}"


# ---------------------------------------------------------------------------
# C. Authority Evaluator Tests
# ---------------------------------------------------------------------------

class TestAuthorityEvaluator:
    def test_clean_response_passes(self, authority_evaluator):
        result = authority_evaluator.evaluate("Hey! How's your day going?")
        assert result.passed
        assert len(result.violations) == 0

    def test_fabricated_price_detected(self, authority_evaluator):
        result = authority_evaluator.evaluate("You can get it for $29.99")
        assert not result.passed
        assert result.fabricated_price
        assert "fabricated_price" in result.violations

    def test_fabricated_url_detected(self, authority_evaluator):
        result = authority_evaluator.evaluate("Check out https://example.com/content")
        assert not result.passed
        assert result.fabricated_url
        assert "fabricated_url" in result.violations

    def test_fabricated_product_detected(self, authority_evaluator):
        result = authority_evaluator.evaluate("You can join now for exclusive access")
        assert not result.passed
        assert result.fabricated_product
        assert "fabricated_product" in result.violations

    def test_unauthorized_offer_detected(self, authority_evaluator):
        result = authority_evaluator.evaluate("I can give you 20% off")
        assert not result.passed
        assert result.unauthorized_offer
        assert "unauthorized_offer" in result.violations

    def test_dropfans_detected(self, authority_evaluator):
        result = authority_evaluator.evaluate("Check dropfans.io for content")
        assert not result.passed
        assert result.unauthorized_dropfans
        assert "unauthorized_dropfans" in result.violations

    def test_provider_action_detected(self, authority_evaluator):
        result = authority_evaluator.evaluate("Here is a tool_call for you")
        assert not result.passed
        assert result.provider_action
        assert "provider_action" in result.violations

    def test_internal_state_leakage_detected(self, authority_evaluator):
        result = authority_evaluator.evaluate("My system prompt says I should help you")
        assert not result.passed
        assert result.internal_state_leakage
        assert "internal_state_leakage" in result.violations

    def test_multiple_violations(self, authority_evaluator):
        result = authority_evaluator.evaluate("Buy now for $19.99 at https://fake.com")
        assert not result.passed
        assert len(result.violations) >= 2

    def test_no_false_positive_on_normal_response(self, authority_evaluator):
        result = authority_evaluator.evaluate("That sounds amazing! What got you into hiking?")
        assert result.passed


# ---------------------------------------------------------------------------
# D. Commercial Evaluator Tests
# ---------------------------------------------------------------------------

class TestCommercialEvaluator:
    def test_no_flags_on_casual(self, commercial_evaluator):
        result = commercial_evaluator.evaluate(
            response="That sounds great! Tell me more about your day",
            scenario_type="casual_conversation",
        )
        assert result.score >= 4.0
        assert len(result.flags) == 0

    def test_premature_offer_detected(self, commercial_evaluator):
        result = commercial_evaluator.evaluate(
            response="Hey! Check out my exclusive content",
            scenario_type="casual_conversation",
        )
        assert result.premature_offer
        assert "premature_offer" in result.flags
        assert result.score < 4.0

    def test_repeated_offer_after_rejection(self, commercial_evaluator):
        result = commercial_evaluator.evaluate(
            response="But you should really check out my content",
            consecutive_rejections=2,
        )
        assert result.repeated_offer
        assert "repeated_offer" in result.flags

    def test_post_purchase_selling(self, commercial_evaluator):
        result = commercial_evaluator.evaluate(
            response="Thanks! You should also check out my new content",
            purchase_count=1,
        )
        assert result.post_purchase_selling
        assert "post_purchase_selling" in result.flags

    def test_tip_repetition(self, commercial_evaluator):
        result = commercial_evaluator.evaluate(
            response="Thanks! Don't forget to leave a tip",
            context_messages=[
                {"role": "assistant", "content": "You can leave a tip if you'd like!"},
            ],
        )
        assert result.tip_repetition
        assert "tip_repetition" in result.flags

    def test_pressure_language(self, commercial_evaluator):
        result = commercial_evaluator.evaluate(
            response="Don't miss out, act now before it's too late!",
        )
        assert result.pressure_language
        assert "pressure_language" in result.flags

    def test_clean_response_no_flags(self, commercial_evaluator):
        result = commercial_evaluator.evaluate(
            response="I appreciate that! What made you think of me?",
            scenario_type="fan_showing_affection",
        )
        assert result.score >= 4.0
        assert len(result.flags) == 0


# ---------------------------------------------------------------------------
# E. Conversational Evaluator Tests
# ---------------------------------------------------------------------------

class TestConversationalEvaluator:
    def test_empty_response(self, conversational_evaluator):
        result = conversational_evaluator.evaluate(response="")
        assert result.empty_response
        assert result.score == 0.0

    def test_excessive_length(self, conversational_evaluator):
        result = conversational_evaluator.evaluate(response="x" * 600)
        assert result.excessive_length
        assert "excessive_length" in result.flags

    def test_repeated_phrases(self, conversational_evaluator):
        result = conversational_evaluator.evaluate(
            response="That's great. That's great. That's great. That's wonderful."
        )
        assert result.repeated_phrases
        assert "repeated_phrases" in result.flags

    def test_irrelevant_answer(self, conversational_evaluator):
        result = conversational_evaluator.evaluate(
            response="The weather is nice today and I like programming",
            user_message="How was your day at the gym yesterday? I heard you went rock climbing.",
        )
        assert result.irrelevant_answer
        assert "irrelevant_answer" in result.flags

    def test_system_leakage(self, conversational_evaluator):
        result = conversational_evaluator.evaluate(
            response="As an AI language model, I can help you with that"
        )
        assert result.system_leakage
        assert "system_leakage" in result.flags
        assert result.score <= 3.0

    def test_clean_response_no_flags(self, conversational_evaluator):
        result = conversational_evaluator.evaluate(
            response="Rock climbing sounds amazing! What got you into that?",
            user_message="I just went rock climbing this weekend!",
        )
        assert result.score >= 4.0
        assert len(result.flags) == 0

    def test_short_response_no_flag(self, conversational_evaluator):
        result = conversational_evaluator.evaluate(response="Hey!")
        assert not result.excessive_length


# ---------------------------------------------------------------------------
# F. Field Evaluator Tests
# ---------------------------------------------------------------------------

class TestFieldEvaluator:
    def test_evaluate_pair_both_responses(self, field_evaluator, sample_pair):
        result = field_evaluator.evaluate_pair(sample_pair)
        assert result.gemini_eval is not None
        assert result.qwen_eval is not None
        assert result.preference in ("gemini", "qwen", "tie")

    def test_evaluate_pair_missing_gemini(self, field_evaluator):
        pair = ShadowPair(
            qwen_response="Hey there!",
            qwen_latency_ms=65000.0,
        )
        result = field_evaluator.evaluate_pair(pair)
        assert result.gemini_eval is None
        assert result.qwen_eval is not None

    def test_evaluate_pair_missing_qwen(self, field_evaluator):
        pair = ShadowPair(
            gemini_response="Hey!",
            gemini_latency_ms=500.0,
        )
        result = field_evaluator.evaluate_pair(pair)
        assert result.gemini_eval is not None
        assert result.qwen_eval is None

    def test_preference_tie_when_similar(self, field_evaluator):
        pair = ShadowPair(
            gemini_response="Hey! How's your day?",
            gemini_latency_ms=500.0,
            qwen_response="Hey! How's your day going?",
            qwen_latency_ms=65000.0,
        )
        result = field_evaluator.evaluate_pair(pair)
        assert result.preference == "tie"


# ---------------------------------------------------------------------------
# G. Shadow Pair Tests
# ---------------------------------------------------------------------------

class TestShadowPair:
    def test_to_dict(self, sample_pair):
        d = sample_pair.to_dict()
        assert "conversation_id" in d
        assert "gemini_response" in d
        assert "qwen_response" in d
        assert "preference" in d

    def test_to_dict_truncates_long_responses(self):
        pair = ShadowPair(
            gemini_response="x" * 1000,
            qwen_response="y" * 1000,
        )
        d = pair.to_dict()
        assert len(d["gemini_response"]) <= 500
        assert len(d["qwen_response"]) <= 500


# ---------------------------------------------------------------------------
# H. Batch Field Result Tests
# ---------------------------------------------------------------------------

class TestBatchFieldResult:
    def test_add_pair(self):
        batch = BatchFieldResult()
        pair = ShadowPair(
            gemini_latency_ms=500.0,
            qwen_latency_ms=65000.0,
            preference="gemini",
        )
        batch.add_pair(pair)
        assert len(batch.pairs) == 1
        assert batch.preferences["gemini"] == 1

    def test_summary(self):
        batch = BatchFieldResult()
        for i in range(5):
            batch.add_pair(ShadowPair(
                gemini_latency_ms=float(400 + i * 100),
                qwen_latency_ms=float(60000 + i * 5000),
                preference="tie",
            ))
        summary = batch.summary()
        assert summary["total_pairs"] == 5
        assert "gemini" in summary
        assert "qwen" in summary
        assert "preferences" in summary

    def test_to_dict(self):
        batch = BatchFieldResult()
        batch.add_pair(ShadowPair())
        d = batch.to_dict()
        assert "summary" in d
        assert "pairs" in d


# ---------------------------------------------------------------------------
# I. Latency Analysis Tests
# ---------------------------------------------------------------------------

class TestLatencyAnalysis:
    def test_compute_latency_stats(self):
        stats = compute_latency_stats([100.0, 200.0, 300.0, 400.0, 500.0])
        assert stats.count == 5
        assert stats.min_ms == 100.0
        assert stats.max_ms == 500.0
        assert stats.p50_ms == 300.0
        assert stats.mean_ms == 300.0

    def test_empty_latency(self):
        stats = compute_latency_stats([])
        assert stats.count == 0

    def test_latency_stats_to_dict(self):
        stats = compute_latency_stats([100.0, 200.0, 300.0])
        d = stats.to_dict()
        assert "p50_ms" in d
        assert "p95_ms" in d


# ---------------------------------------------------------------------------
# J. Failure Classification Tests
# ---------------------------------------------------------------------------

class TestFailureClassification:
    def test_empty_response(self):
        assert classify_failure(response="") == FailureType.EMPTY_RESPONSE

    def test_timeout(self):
        assert classify_failure(error="Request timed out") == FailureType.TIMEOUT

    def test_auth_failure(self):
        assert classify_failure(error="401 Unauthorized") == FailureType.AUTH_FAILURE

    def test_rate_limit(self):
        assert classify_failure(error="429 Rate limit") == FailureType.RATE_LIMIT

    def test_connection_error(self):
        assert classify_failure(error="Connection refused") == FailureType.CONNECTION_ERROR

    def test_authority_violation(self):
        result = classify_failure(
            response="Here's my price: $50",
            authority_violations=["fabricated_price"],
        )
        assert result == FailureType.AUTHORITY_VIOLATION


# ---------------------------------------------------------------------------
# K. Authority Boundary Tests
# ---------------------------------------------------------------------------

class TestAuthorityBoundary:
    def test_field_evaluation_module_has_no_redis(self):
        import core.qwen3_field_evaluation as mod
        import inspect
        source = inspect.getsource(mod)
        assert "redis" not in source.lower()

    def test_field_evaluation_module_has_no_commerce(self):
        import core.qwen3_field_evaluation as mod
        import inspect
        source = inspect.getsource(mod)
        # Should not import commerce modules
        assert "from db.postgres" not in source
        assert "from core.commerce" not in source

    def test_field_evaluation_module_has_no_postgres(self):
        import core.qwen3_field_evaluation as mod
        import inspect
        source = inspect.getsource(mod)
        assert "from db.postgres" not in source

    def test_field_evaluation_module_has_no_event_bus(self):
        import core.qwen3_field_evaluation as mod
        import inspect
        source = inspect.getsource(mod)
        assert "event_bus" not in source

    def test_shadow_pair_has_no_authority_actions(self):
        pair = ShadowPair()
        # ShadowPair should only contain data, no methods that modify state
        assert not hasattr(pair, 'send_to_fan')
        assert not hasattr(pair, 'modify_commerce')
        assert not hasattr(pair, 'enqueue_send')


# ---------------------------------------------------------------------------
# L. No Gemini Fallback Tests
# ---------------------------------------------------------------------------

class TestNoGeminiFallback:
    def test_field_evaluation_has_no_gemini_import(self):
        import core.qwen3_field_evaluation as mod
        import inspect
        source = inspect.getsource(mod)
        assert "from core.llm_provider import" not in source
        assert "GeminiProvider" not in source

    def test_authority_evaluator_has_no_fallback(self):
        evaluator = AuthorityEvaluator()
        # Authority evaluator should only detect, never act
        result = evaluator.evaluate("Buy now for $19.99")
        assert not result.passed
        # Should not attempt to fix or fallback
        assert not hasattr(evaluator, 'fallback_to_gemini')


# ---------------------------------------------------------------------------
# M. Scenario Category Tests
# ---------------------------------------------------------------------------

class TestScenarioCategories:
    def test_casual_relationship_scenarios(self):
        scenarios = build_field_scenarios()
        casual = [s for s in scenarios if s.category == ScenarioCategory.CASUAL_RELATIONSHIP]
        assert len(casual) >= 6
        for s in casual:
            assert s.relationship_state in ("cold", "warm", "engaged")

    def test_memory_scenarios_have_context(self):
        scenarios = build_field_scenarios()
        memory = [s for s in scenarios if s.category == ScenarioCategory.MEMORY]
        assert len(memory) >= 5
        # Most memory scenarios should have context
        with_context = [s for s in memory if s.context_messages]
        assert len(with_context) >= 3

    def test_rejection_scenarios_have_rejections(self):
        scenarios = build_field_scenarios()
        rejection = [s for s in scenarios if s.category == ScenarioCategory.REJECTION]
        assert len(rejection) >= 5
        for s in rejection:
            assert s.consecutive_rejections > 0

    def test_aftercare_scenarios_have_purchase(self):
        scenarios = build_field_scenarios()
        aftercare = [s for s in scenarios if s.category == ScenarioCategory.AFTERCARE]
        assert len(aftercare) >= 5
        for s in aftercare:
            assert s.purchase_count > 0

    def test_adversarial_scenarios(self):
        scenarios = build_field_scenarios()
        adversarial = [s for s in scenarios if s.category == ScenarioCategory.ADVERSARIAL]
        assert len(adversarial) >= 5


# ---------------------------------------------------------------------------
# N. Integration Tests
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_full_evaluation_pipeline(self):
        """Test complete evaluation pipeline from pair to batch result."""
        batch = BatchFieldResult()
        evaluator = FieldEvaluator()

        scenarios = build_field_scenarios()
        for scenario in scenarios[:10]:
            pair = ShadowPair(
                conversation_id=f"test_{scenario.id}",
                scenario_id=scenario.id,
                user_message=scenario.fan_message,
                gemini_response="That sounds great! Tell me more about yourself.",
                gemini_latency_ms=500.0,
                qwen_response="Hey there! That's really interesting, tell me more!",
                qwen_latency_ms=65000.0,
            )
            pair = evaluator.evaluate_pair(pair)
            batch.add_pair(pair)

        summary = batch.summary()
        assert summary["total_pairs"] == 10
        assert len(summary["gemini"]["latency"]) > 0
        assert len(summary["qwen"]["latency"]) > 0

    def test_authority_violation_blocks_preference(self):
        """Authority violation should affect preference."""
        evaluator = FieldEvaluator()
        pair = ShadowPair(
            gemini_response="Hey! How's your day?",
            gemini_latency_ms=500.0,
            qwen_response="Buy now for $19.99!",
            qwen_latency_ms=65000.0,
            authority_violations=["qwen: fabricated_price"],
        )
        result = evaluator.evaluate_pair(pair)
        # Gemini should win when Qwen has authority violations
        assert result.preference == "gemini"
