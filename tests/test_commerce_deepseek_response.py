"""Phase 5.4 Chk 4 tests — DeepSeek V4 Flash commerce response adapter.

Chk 1: sealed input contract (strict, instance-only sealed types).
Chk 2: transport reuse (shared gemini_client pattern, cheap_model).
Chk 3: output contract (GENERATED/FAILED, validation, no fabrication).
Chk 4: strategy compliance (goals, tone, constraints, no escalation).
Chk 5: product authority (verified price/URL/title only, no overrides).
Chk 6: execution semantics (offer language only for EXECUTED/ALREADY_EXECUTED).
Chk 7: security (secrets, internal leakage, prompt leakage).
Chk 8: determinism (temperature 0.0, no randomness/clock).
Chk 9: privacy (bounded logs, no conversation/raw output).
Chk 10: no forbidden surfaces (no workers/queues/events/HTTP imports).
"""

import ast
import inspect
import json
import logging

import pytest

from commerce.context import (
    CommerceConversationMessage,
    ProductCommerceState,
    ProductIdentity,
)
from commerce.decision import CommerceDecision, CommerceReason
from commerce.deepseek_response import (
    COMMERCE_RESPONSE_SYSTEM,
    CONSTRAINT_PHRASES,
    RESPONSE_FAILURE_CODES,
    RESPONSE_MAX_CHARS,
    RESPONSE_MAX_OUTPUT_TOKENS,
    RESPONSE_MAX_TRANSCRIPT_MESSAGES,
    RESPONSE_TEMPERATURE,
    CommerceResponse,
    CommerceResponseInput,
    CommerceResponseStatus,
    _system_prompt,
    generate_commerce_response,
)
from commerce.execution import ExecutionResult, ExecutionStatus
from commerce.models import CommerceAction
from commerce.strategy import (
    CommerceStrategy,
    CommunicationConstraints,
    SalesPressure,
    StrategyKind,
)
from core.config import get_settings

pytestmark = [pytest.mark.unit]

SETTINGS = get_settings()


# ═══════════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════════


def _decision(action=CommerceAction.OFFER_PPV, **overrides):
    data = {
        "action": action,
        "reason_code": CommerceReason.STRONG_BUYING_SIGNAL,
        "allowed": True,
        "confidence": 0.95,
    }
    data.update(overrides)
    return CommerceDecision(**data)


def _strategy(action=CommerceAction.OFFER_PPV, **overrides):
    data = {
        "action": action,
        "kind": StrategyKind.OFFER_PPV,
        "pressure": SalesPressure.LOW,
        "allow_cta": True,
        "allow_price_reference": True,
        "allow_product_reference": True,
        "relationship_first": False,
        "follow_up_allowed": False,
        "reason": CommerceReason.STRONG_BUYING_SIGNAL,
        "communication_constraints": CommunicationConstraints(),
    }
    data.update(overrides)
    return CommerceStrategy(**data)


def _messages():
    return [
        CommerceConversationMessage(role="user", content="hi there"),
        CommerceConversationMessage(role="assistant", content="hey! how are you?"),
    ]


def _identity(**overrides):
    data = {"product_id": 10, "title": "VIP Video Bundle", "available": True}
    data.update(overrides)
    return ProductIdentity(**data)


def _state(**overrides):
    data = {
        "price_minor": 4400,
        "sales_url": "https://shop.example/vip",
        "is_accessible": True,
    }
    data.update(overrides)
    return ProductCommerceState(**data)


def _execution(status=ExecutionStatus.EXECUTED, **overrides):
    data = {"status": status, "offer_id": 77}
    data.update(overrides)
    return ExecutionResult(**data)


def _input(**overrides):
    data = {
        "user_id": 1,
        "creator_id": 2,
        "conversation": _messages(),
        "decision": _decision(),
        "strategy": _strategy(),
    }
    data.update(overrides)
    return CommerceResponseInput(**data)


def fake_provider(response_text=None, exc=None):
    from unittest.mock import AsyncMock, MagicMock

    provider = MagicMock()
    provider.__class__.__name__ = "GeminiProvider"

    if exc is not None:
        async def raise_exc(*a, **k):
            raise exc
        provider.generate = AsyncMock(side_effect=raise_exc)
    else:
        provider.generate = AsyncMock(return_value=response_text)

    return provider


def _patch_transport(mp, provider):
    mp.setattr("commerce.deepseek_response.get_llm_provider", lambda: provider)


# ═══════════════════════════════════════════════════════════════════════════
# GROUP A — sealed input contract (Chk 1)
# ═══════════════════════════════════════════════════════════════════════════


class TestInputContract:
    def test_valid_input_builds(self):
        inp = _input()
        assert inp.user_id == 1
        assert inp.creator_id == 2

    def test_extra_fields_rejected(self):
        with pytest.raises(ValueError):
            CommerceResponseInput(**_input().model_dump(), invented=True)

    def test_user_id_must_be_positive_int(self):
        for bad in (0, -1, 1.5, "1", True):
            with pytest.raises(ValueError):
                _input(user_id=bad)

    def test_creator_id_must_be_positive_int(self):
        with pytest.raises(ValueError):
            _input(creator_id=0)

    def test_conversation_message_role_and_content_strict(self):
        with pytest.raises(ValueError):
            _input(conversation=[CommerceConversationMessage(role="admin", content="hi")])
        with pytest.raises(ValueError):
            _input(conversation=[CommerceConversationMessage(role="user", content=42)])

    def test_conversation_is_capped(self):
        many = [
            CommerceConversationMessage(role="user", content="m")
            for _ in range(RESPONSE_MAX_TRANSCRIPT_MESSAGES + 5)
        ]
        with pytest.raises(ValueError):
            _input(conversation=many)

    def test_empty_conversation_allowed_by_contract(self):
        inp = _input(conversation=[])
        assert inp.conversation == []

    def test_decision_must_be_instance(self):
        with pytest.raises(ValueError):
            _input(decision="offer_ppv")

    def test_decision_dict_rejected(self):
        with pytest.raises(ValueError):
            CommerceResponseInput(
                user_id=1,
                creator_id=2,
                conversation=_messages(),
                decision={"action": "offer_ppv"},
                strategy=_strategy(),
            )

    def test_execution_result_must_be_instance_or_none(self):
        assert _input(execution_result=None).execution_result is None
        with pytest.raises(ValueError):
            _input(execution_result={"status": "executed"})

    def test_persona_bounded(self):
        _input(persona="x" * 2000)
        with pytest.raises(ValueError):
            _input(persona="x" * 2001)

    def test_currency_bounded(self):
        _input(currency="USD")
        _input(currency=None)
        with pytest.raises(ValueError):
            _input(currency="U")
        with pytest.raises(ValueError):
            _input(currency="X" * 13)

    def test_strategy_is_validated_nested(self):
        with pytest.raises(ValueError):
            _input(strategy={"action": "offer_ppv"})


# ═══════════════════════════════════════════════════════════════════════════
# GROUP B — prompt contract (Chk 4 / Chk 7)
# ═══════════════════════════════════════════════════════════════════════════


class TestPromptContract:
    def test_ten_principle_markers_present(self):
        lowered = COMMERCE_RESPONSE_SYSTEM.lower()
        for marker in (
            "natural, conversational prose",
            "verified facts",
            "never invent a price",
            "unless verified facts say an active offer exists",
            "never mention eligibility",
            "never fabricate context",
            "no urgency, no guilt, no scarcity",
            "accept it gracefully",
            "never escalate above the goal",
            "output only the reply text",
        ):
            assert marker in lowered

    def test_prompt_forbids_chain_of_thought(self):
        lowered = COMMERCE_RESPONSE_SYSTEM.lower()
        assert "no prefixes" in lowered
        assert "chain of thought" in lowered or "no explanations" in lowered

    def test_prompt_never_instructs_actions(self):
        lowered = (
            COMMERCE_RESPONSE_SYSTEM
            + _system_prompt(_input(strategy=_strategy(CommerceAction.OFFER_PPV)))
        ).lower()
        for word in ("send_ppv", "execute_ppv", "create_an_offer", "enqueue"):
            assert word not in lowered
        assert "output only the reply text" in lowered

    def test_prompt_bans_payment_surfaces(self):
        lowered = COMMERCE_RESPONSE_SYSTEM.lower()
        assert "card number" in lowered or "payment" in lowered

    def test_deterministic_temperature_constant(self):
        assert RESPONSE_TEMPERATURE == 0.0

    def test_failure_codes_are_bounded_and_closed(self):
        assert len(RESPONSE_FAILURE_CODES) == 8
        assert "empty_conversation" in RESPONSE_FAILURE_CODES
        assert "invalid_output" in RESPONSE_FAILURE_CODES


# ═══════════════════════════════════════════════════════════════════════════
# GROUP C — verified facts (Chk 5 / Chk 6)
# ═══════════════════════════════════════════════════════════════════════════


class TestVerifiedFacts:
    def test_persona_included(self):
        facts = _system_prompt(_input(persona="warm and playful"))
        assert "Creator persona: warm and playful" in facts

    def test_persona_omitted_when_none(self):
        facts = _system_prompt(_input(persona=None))
        assert "Creator persona" not in facts

    def test_product_title_when_reference_allowed(self):
        facts = _system_prompt(_input(product_identity=_identity(), product_state=_state()))
        assert "Product title: VIP Video Bundle" in facts

    def test_product_title_omitted_when_reference_forbidden(self):
        inp = _input(strategy=_strategy(allow_product_reference=False))
        facts = _system_prompt(inp)
        assert "Product title" not in facts

    def test_product_title_omitted_when_unavailable(self):
        inp = _input(
            product_identity=_identity(available=False),
            product_state=_state(),
        )
        assert "Product title" not in _system_prompt(inp)

    def test_price_appears_when_authoritative(self):
        inp = _input(
            currency="USD",
            product_state=_state(price_minor=4400),
        )
        assert "Verified price: 44.00 USD" in _system_prompt(inp)

    def test_price_requires_price_minor(self):
        inp = _input(
            currency="USD",
            product_state=_state(price_minor=None),
        )
        assert "Verified price" not in _system_prompt(inp)

    def test_price_requires_currency(self):
        inp = _input(
            currency=None,
            product_state=_state(price_minor=4400),
        )
        assert "Verified price" not in _system_prompt(inp)

    def test_price_omitted_when_reference_forbidden(self):
        inp = _input(
            currency="USD",
            strategy=_strategy(allow_price_reference=False),
            product_state=_state(price_minor=4400),
        )
        assert "Verified price" not in _system_prompt(inp)

    def test_sales_url_when_reference_allowed(self):
        inp = _input(product_state=_state())
        assert "Sales URL: https://shop.example/vip" in _system_prompt(inp)

    def test_sales_url_omitted_when_reference_forbidden(self):
        inp = _input(
            strategy=_strategy(allow_product_reference=False),
            product_state=_state(),
        )
        assert "Sales URL" not in _system_prompt(inp)

    def test_sales_url_omitted_when_missing(self):
        inp = _input(product_state=_state(sales_url=None))
        assert "Sales URL" not in _system_prompt(inp)

    def test_active_offer_fact_for_executed(self):
        inp = _input(execution_result=_execution(ExecutionStatus.EXECUTED))
        assert "An active offer exists for this fan." in _system_prompt(inp)

    def test_active_offer_fact_for_already_executed(self):
        inp = _input(execution_result=_execution(ExecutionStatus.ALREADY_EXECUTED))
        assert "An active offer exists for this fan." in _system_prompt(inp)

    def test_no_offer_fact_for_every_failure_status(self):
        for status in (
            ExecutionStatus.PERSISTENCE_FAILED,
            ExecutionStatus.PROVIDER_ERROR,
            ExecutionStatus.REQUIRES_MANUAL_REVIEW,
            ExecutionStatus.DENIED,
            ExecutionStatus.ELIGIBILITY_DENIED,
            ExecutionStatus.PRODUCT_UNAVAILABLE,
            ExecutionStatus.CREATOR_NOT_READY,
            ExecutionStatus.EXECUTION_CONFLICT,
        ):
            inp = _input(execution_result=_execution(status))
            facts = _system_prompt(inp)
            assert "An active offer exists" not in facts
            assert (
                "No active offer: do not claim an offer was created, confirmed, or set up" in facts
            )

    def test_no_offer_fact_without_execution_record(self):
        facts = _system_prompt(_input(execution_result=None))
        assert "An active offer exists" not in facts
        assert "No active offer: do not claim an offer was created, confirmed, or set up" in facts


# ═══════════════════════════════════════════════════════════════════════════
# GROUP D — strategy instructions (Chk 4)
# ═══════════════════════════════════════════════════════════════════════════


class TestStrategyInstructions:
    @pytest.mark.parametrize(
        "action,marker",
        [
            (CommerceAction.NO_OFFER, "Do NOT mention any product, price, or offer"),
            (CommerceAction.DONT_OFFER, "Do NOT mention any product, price, or offer"),
            (CommerceAction.RELATIONSHIP_BUILDING, "Do NOT pitch or sell"),
            (CommerceAction.SOFT_OFFER, "casually mention that paid exclusive content exists"),
            (CommerceAction.FOLLOW_UP, "follow up on the existing context"),
            (CommerceAction.OFFER_PPV, "VERIFIED FACTS"),
        ],
    )
    def test_action_goal_encoded(self, action, marker):
        assert marker in _system_prompt(_input(strategy=_strategy(action)))

    def test_never_escalates_no_offer(self):
        prompt = _system_prompt(_input(strategy=_strategy(CommerceAction.NO_OFFER)))
        assert "Communication goal: build a normal, friendly conversation" in prompt
        assert "present the offer" not in prompt

    @pytest.mark.parametrize(
        "pressure,tone",
        [
            (SalesPressure.NONE, "neutral, zero pressure"),
            (SalesPressure.LOW, "gentle and warm"),
            (SalesPressure.MODERATE, "warm but direct"),
        ],
    )
    def test_pressure_tone(self, pressure, tone):
        assert tone in _system_prompt(_input(strategy=_strategy(pressure=pressure)))

    def test_cta_allowed_vs_forbidden(self):
        assert "A clear call to action is allowed." in _system_prompt(
            _input(strategy=_strategy(allow_cta=True))
        )
        assert "Do not push for an action." in _system_prompt(
            _input(strategy=_strategy(allow_cta=False))
        )

    def test_relationship_first(self):
        assert "Lead with warmth and the relationship" in _system_prompt(
            _input(strategy=_strategy(relationship_first=True))
        )

    def test_follow_up_allowed_vs_forbidden(self):
        assert "You may offer a future follow-up." in _system_prompt(
            _input(strategy=_strategy(follow_up_allowed=True))
        )
        assert "Do not propose another follow-up right now." in _system_prompt(
            _input(strategy=_strategy(follow_up_allowed=False))
        )

    def test_default_constraints_forbid_all_eleven(self):
        prompt = _system_prompt(_input())
        for phrase in CONSTRAINT_PHRASES.values():
            assert phrase in prompt
        assert "You must NEVER use:" in prompt

    def test_enabled_constraint_moves_to_allowed_list(self):
        constraints = CommunicationConstraints(allow_urgency=True)
        prompt = _system_prompt(_input(strategy=_strategy(communication_constraints=constraints)))
        assert "urgency" in prompt.split("You ARE allowed to use:")[1]
        never_block = prompt.split("You must NEVER use:")[1].split(".")[0]
        assert "urgency" not in never_block

    def test_execution_guardrail_always_present(self):
        assert (
            "NEVER claim that an offer, payment, or link was created, sent, or confirmed"
            in _system_prompt(_input())
        )


# ═══════════════════════════════════════════════════════════════════════════
# GROUP E — happy path + transport (Chk 2)
# ═══════════════════════════════════════════════════════════════════════════


class TestGenerateHappyPath:
    @pytest.mark.asyncio
    async def test_generates_valid_reply(self):
        provider = fake_provider("  sure thing! happy to share more.  ")
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            result = await generate_commerce_response(_input())
        assert result.status is CommerceResponseStatus.GENERATED
        assert result.text == "sure thing! happy to share more."
        assert result.failure_code is None

    @pytest.mark.asyncio
    async def test_uses_sole_provider_default_model(self):
        # Sole llama.cpp provider: no stale cheap_model is passed; the
        # provider default (LLAMA_MODEL) applies.
        provider = fake_provider("ok")
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            await generate_commerce_response(_input())
        provider.generate.assert_called_once()
        call_kwargs = provider.generate.call_args.kwargs
        assert "model" not in call_kwargs or call_kwargs.get("model") is None
        assert "cheap_model" not in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_transport_contract_and_temperature(self):
        provider = fake_provider("ok")
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            await generate_commerce_response(_input())
        provider.generate.assert_called_once()
        call_kwargs = provider.generate.call_args.kwargs
        assert call_kwargs["temperature"] == RESPONSE_TEMPERATURE
        assert call_kwargs["max_output_tokens"] == RESPONSE_MAX_OUTPUT_TOKENS

    @pytest.mark.asyncio
    async def test_system_prompt_injects_facts_and_strategy(self):
        inp = _input(
            currency="USD",
            product_identity=_identity(),
            product_state=_state(),
            execution_result=_execution(ExecutionStatus.EXECUTED),
            persona="warm and playful",
        )
        provider = fake_provider("Your VIP link: 44.00")
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            await generate_commerce_response(inp)
        provider.generate.assert_called_once()
        call_kwargs = provider.generate.call_args.kwargs
        system = call_kwargs["system_instruction"]
        assert "Verified price: 44.00 USD" in system
        assert "Sales URL: https://shop.example/vip" in system
        assert "Product title: VIP Video Bundle" in system
        assert "An active offer exists for this fan." in system

    @pytest.mark.asyncio
    async def test_deterministic_for_same_input(self):
        r1 = None
        r2 = None
        with pytest.MonkeyPatch.context() as mp:
            provider = fake_provider("same text")
            _patch_transport(mp, provider)
            r1 = await generate_commerce_response(_input())
        with pytest.MonkeyPatch.context() as mp:
            provider = fake_provider("same text")
            _patch_transport(mp, provider)
            r2 = await generate_commerce_response(_input())
        assert r1 == r2

    @pytest.mark.asyncio
    async def test_transcript_is_bounded_and_roles_mapped(self):
        provider = fake_provider("ok")
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            await generate_commerce_response(_input())
        provider.generate.assert_called_once()
        call_kwargs = provider.generate.call_args.kwargs
        transcript = call_kwargs["user_content"]
        assert "Fan: hi there" in transcript
        assert "Creator: hey! how are you?" in transcript


# ═══════════════════════════════════════════════════════════════════════════
# GROUP F — failure paths (Chk 3)
# ═══════════════════════════════════════════════════════════════════════════


class TestFailurePaths:
    @pytest.mark.asyncio
    async def test_empty_conversation_short_circuits(self):
        provider = fake_provider()
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            result = await generate_commerce_response(_input(conversation=[]))
        assert result.status is CommerceResponseStatus.FAILED
        assert result.failure_code == "empty_conversation"
        provider.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_provider_failure(self):
        def boom():
            raise RuntimeError("no provider")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("commerce.deepseek_response.get_llm_provider", boom)
            result = await generate_commerce_response(_input())
        assert result.status is CommerceResponseStatus.FAILED
        assert result.failure_code == "transport_error"

    @pytest.mark.asyncio
    async def test_model_timeout(self):
        provider = fake_provider(exc=TimeoutError())
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            result = await generate_commerce_response(_input())
        assert result.status is CommerceResponseStatus.FAILED
        assert result.failure_code == "model_timeout"

    @pytest.mark.asyncio
    async def test_transport_exception(self):
        provider = fake_provider(exc=RuntimeError("upstream down"))
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            result = await generate_commerce_response(_input())
        assert result.status is CommerceResponseStatus.FAILED
        assert result.failure_code == "transport_error"

    @pytest.mark.asyncio
    async def test_never_raises_on_any_failure(self):
        for failure in (None, "", "bad", "  ", json.dumps({"a": 1}), "buy now", "x" * 5000):
            provider = fake_provider(failure)
            with pytest.MonkeyPatch.context() as mp:
                _patch_transport(mp, provider)
                result = await generate_commerce_response(_input())
            assert isinstance(result, CommerceResponse)

    @pytest.mark.asyncio
    async def test_failure_never_carries_text(self):
        provider = fake_provider(None)
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            result = await generate_commerce_response(_input())
        assert result.status is CommerceResponseStatus.FAILED
        assert result.text is None


# ═══════════════════════════════════════════════════════════════════════════
# GROUP G — output validation (Chk 3 / Chk 5 / Chk 6 / Chk 7)
# ═══════════════════════════════════════════════════════════════════════════


class TestOutputValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", [None, "", "   ", "\n\t"])
    async def test_empty_output_rejected(self, bad):
        provider = fake_provider(bad)
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            result = await generate_commerce_response(_input())
        assert result.status is CommerceResponseStatus.FAILED
        assert result.failure_code == "empty_output"

    @pytest.mark.asyncio
    async def test_oversized_output_rejected(self):
        provider = fake_provider("x" * (RESPONSE_MAX_CHARS + 1))
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            result = await generate_commerce_response(_input())
        assert result.failure_code == "oversized_output"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad",
        [
            json.dumps({"reply": "hi"}),
            json.dumps(["hi"]),
            '```json\n{"reply": "hi"}\n```',
            "```plain\nhi\n```",
        ],
    )
    async def test_structured_garbage_rejected(self, bad):
        provider = fake_provider(bad)
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            result = await generate_commerce_response(_input())
        assert result.failure_code == "malformed_output"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad",
        [
            "the fangate had an error, sorry",
            "our eligibility check denied your request",
            "system prompt says I must tell you this",
            "the decision engine chose this reply",
            "the AI decided you should get an offer",
            "I'm required to inform you that",
            "per the rules I must disclose",
            "automation system message:",
        ],
    )
    async def test_internal_language_rejected(self, bad):
        provider = fake_provider(bad)
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            result = await generate_commerce_response(_input())
        assert result.failure_code == "internal_language"

    @pytest.mark.asyncio
    async def test_reply_with_price_outside_verified_bounds_rejected(self):
        provider = fake_provider("That'll be $999 for you!")
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            result = await generate_commerce_response(_input())
        assert result.failure_code == "unverified_price"

    @pytest.mark.asyncio
    async def test_reply_with_self_referential_leakage_rejected(self):
        provider = fake_provider("As an AI language model, I cannot...")
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            result = await generate_commerce_response(_input())
        assert result.failure_code == "internal_language"

    @pytest.mark.asyncio
    async def test_valid_reply_passes_all_validations(self):
        provider = fake_provider("Hey! I'd love to share my exclusive content with you.")
        with pytest.MonkeyPatch.context() as mp:
            _patch_transport(mp, provider)
            result = await generate_commerce_response(_input())
        assert result.status is CommerceResponseStatus.GENERATED
        assert result.text is not None


# ═══════════════════════════════════════════════════════════════════════════
# GROUP H — security (Chk 7)
# ═══════════════════════════════════════════════════════════════════════════


class TestSecurity:
    @pytest.mark.asyncio
    async def test_no_prompt_leakage_in_logs(self, caplog):
        with (
            caplog.at_level(logging.WARNING, logger="commerce_deepseek_response"),
            pytest.MonkeyPatch.context() as mp,
        ):
            provider = fake_provider("some reply")
            _patch_transport(mp, provider)
            await generate_commerce_response(_input())
        for record in caplog.records:
            assert "system_instruction" not in record.message.lower()

    @pytest.mark.asyncio
    async def test_no_raw_conversation_in_logs(self, caplog):
        with (
            caplog.at_level(logging.WARNING, logger="commerce_deepseek_response"),
            pytest.MonkeyPatch.context() as mp,
        ):
            provider = fake_provider("some reply")
            _patch_transport(mp, provider)
            await generate_commerce_response(_input())
        for record in caplog.records:
            assert "hi there" not in record.message.lower()

    @pytest.mark.asyncio
    async def test_no_api_key_in_logs(self, caplog):
        with (
            caplog.at_level(logging.WARNING, logger="commerce_deepseek_response"),
            pytest.MonkeyPatch.context() as mp,
        ):
            provider = fake_provider("some reply")
            _patch_transport(mp, provider)
            await generate_commerce_response(_input())
        for record in caplog.records:
            assert "api_key" not in record.message.lower()
            assert "token" not in record.message.lower()


# ═══════════════════════════════════════════════════════════════════════════
# GROUP I — determinism (Chk 8)
# ═══════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def test_temperature_is_zero(self):
        assert RESPONSE_TEMPERATURE == 0.0

    def test_no_random_imports(self):
        source = inspect.getsource(__import__("commerce.deepseek_response", fromlist=["x"]))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "random" not in alias.name
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "random" not in node.module

    def test_no_time_imports(self):
        source = inspect.getsource(__import__("commerce.deepseek_response", fromlist=["x"]))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "time"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "time"


# ═══════════════════════════════════════════════════════════════════════════
# GROUP J — no forbidden surfaces (Chk 10)
# ═══════════════════════════════════════════════════════════════════════════


class TestNoForbiddenSurfaces:
    def test_imports_are_limited(self):
        source = inspect.getsource(__import__("commerce.deepseek_response", fromlist=["x"]))
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(a.name for a in node.names)
        allowed_prefixes = (
            "json",
            "re",
            "logging",
            "asyncio",
            "enum",
            "decimal",
            "typing",
            "pydantic",
            "commerce.context",
            "commerce.decision",
            "commerce.deepseek",
            "commerce.execution",
            "commerce.models",
            "commerce.strategy",
            "core.config",
            "core.llm_provider",
            "core.provider_fallback",
        )
        for mod in imported:
            assert mod.startswith(allowed_prefixes), f"forbidden import: {mod}"

    def test_no_execution_surfaces_in_source(self):
        source = inspect.getsource(__import__("commerce.deepseek_response", fromlist=["x"]))
        for forbidden in (
            "enqueue_send",
            "publish_event",
            "send_ppv",
            "execute_ppv",
            "create_offer",
            "move_to_dlq",
            "add_to_operator_queue",
            "asyncpg",
            "redis",
            "httpx",
            "requests",
            "ws_manager",
            "event_subscriber",
        ):
            assert forbidden not in source, f"forbidden token: {forbidden}"

    def test_no_response_mime_type_contract(self):
        source = inspect.getsource(__import__("commerce.deepseek_response", fromlist=["x"]))
        assert "response_mime_type" not in source
