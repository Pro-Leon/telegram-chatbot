"""Phase 5.3B tests — DeepSeek V4 Flash signal adapter (on shared transport).

Chk 2: adapter transport behavior (reuse Gemini call-site pattern).
Chk 3: structured output contract (JSON mime, parsing, validation).
Chk 4: prompt design (fields match CommerceSignals, no action words).
Chk 7: failure handling — every failure yields low-information signals.
Chk 9: determinism (temperature 0.0, no randomness/clock).
Chk 10: privacy — no raw conversation/output in logs.
Chk 11: no execution surfaces, no forbidden imports.
"""

import ast
import inspect
import json

import pytest

from commerce.deepseek import (
    COMMERCE_SIGNAL_EXTRACTION_SYSTEM,
    SIGNAL_MAX_MESSAGE_CHARS,
    SIGNAL_MAX_TRANSCRIPT_MESSAGES,
    SIGNAL_TEMPERATURE,
    _build_signals,
    _parse_signals_json,
    compose_signal_extraction_input,
    extract_commerce_signals,
)
from commerce.signals import CommerceSignals

pytestmark = [pytest.mark.unit]

SIGNAL_FIELDS = {
    "purchase_intent",
    "content_interest",
    "relationship_engagement",
    "price_interest",
    "explicit_purchase_request",
    "explicit_content_request",
    "requested_price",
    "declined_recent_offer",
    "accepted_recent_offer",
    "asks_for_free_content",
    "negative_sentiment",
    "conversation_relevance",
    "confidence",
    "evidence",
    "model_uncertainty",
    "primary_intent",
    "intent_tags",
    "negative_intent_tags",
    "fan_asks_question",
    "topic_continuity",
}


def valid_payload(**overrides):
    data = {
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
        "primary_intent": "uncertain",
        "intent_tags": [],
        "negative_intent_tags": [],
        "fan_asks_question": False,
        "topic_continuity": None,
    }
    data.update(overrides)
    return data


def transcript():
    return [
        {"role": "system", "content": "persona boilerplate"},
        {"role": "user", "content": "hi there"},
        {"role": "assistant", "content": "hey! how are you?"},
        {"role": "user", "content": "I want to buy your video"},
    ]


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


# ═══════════════════════════════════════════════════════════════════════════
# GROUP A — prompt contract (Chk 4)
# ═══════════════════════════════════════════════════════════════════════════


class TestPromptContract:
    def test_prompt_field_names_match_model(self):
        """Prompt's JSON schema must cover exactly CommerceSignals fields."""
        model_fields = set(CommerceSignals.model_fields)
        assert model_fields == SIGNAL_FIELDS
        for field in model_fields:
            assert field in COMMERCE_SIGNAL_EXTRACTION_SYSTEM

    def test_prompt_never_instructs_actions(self):
        """The prompt must not teach the model to decide or execute actions."""
        lowered = COMMERCE_SIGNAL_EXTRACTION_SYSTEM.lower()
        for word in ("send_ppv", "offer_ppv", "create an offer", "make an offer"):
            assert word not in lowered

    def test_prompt_bans_payment_data_in_evidence(self):
        assert "card number" in COMMERCE_SIGNAL_EXTRACTION_SYSTEM.lower()
        assert "cvv" in COMMERCE_SIGNAL_EXTRACTION_SYSTEM.lower()

    def test_prompt_demands_json_only(self):
        assert "json" in COMMERCE_SIGNAL_EXTRACTION_SYSTEM.lower()

    def test_deterministic_temperature_constant(self):
        assert SIGNAL_TEMPERATURE == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# GROUP B — transcript composition (bounded input)
# ═══════════════════════════════════════════════════════════════════════════


class TestComposeInput:
    def test_skips_system_messages(self):
        text = compose_signal_extraction_input(transcript())
        assert "persona boilerplate" not in text

    def test_maps_roles(self):
        text = compose_signal_extraction_input(transcript())
        assert "Fan: hi there" in text
        assert "Creator: hey! how are you?" in text
        assert "Fan: I want to buy your video" in text

    def test_empty_input(self):
        assert compose_signal_extraction_input([]) == ""
        assert compose_signal_extraction_input(None) == ""

    def test_non_string_content_skipped(self):
        ctx = [{"role": "user", "content": None}, {"role": "user", "content": 42}]
        assert compose_signal_extraction_input(ctx) == ""

    def test_message_count_capped(self):
        many = [
            {"role": "user", "content": f"m{i}"} for i in range(SIGNAL_MAX_TRANSCRIPT_MESSAGES + 50)
        ]
        text = compose_signal_extraction_input(many)
        assert text.count("\n") == SIGNAL_MAX_TRANSCRIPT_MESSAGES - 1

    def test_message_length_capped(self):
        ctx = [{"role": "user", "content": "x" * 5000}]
        text = compose_signal_extraction_input(ctx)
        assert text == "Fan: " + "x" * SIGNAL_MAX_MESSAGE_CHARS


# ═══════════════════════════════════════════════════════════════════════════
# GROUP C — structured output parsing (Chk 3)
# ═══════════════════════════════════════════════════════════════════════════


class TestParseSignalsJson:
    def test_plain_json(self):
        raw = _parse_signals_json(json.dumps(valid_payload()))
        assert raw is not None
        assert raw["purchase_intent"] == 0.0

    def test_json_inside_code_fence(self):
        text = "```json\n" + json.dumps(valid_payload()) + "\n```"
        assert _parse_signals_json(text) is not None

    def test_json_inside_plain_fence(self):
        text = "```\n" + json.dumps(valid_payload()) + "\n```"
        assert _parse_signals_json(text) is not None

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            "",
            "   ",
            "not json at all",
            "Here is the JSON: " + json.dumps(valid_payload()),
            json.dumps([valid_payload()]),
            json.dumps("a string"),
            json.dumps(42),
            json.dumps(valid_payload()) + " trailing garbage",
        ],
    )
    def test_non_object_or_garbage_rejected(self, bad):
        assert _parse_signals_json(bad) is None


class TestBuildSignals:
    def test_valid_payload_builds(self):
        s = _build_signals(valid_payload(purchase_intent=0.9, evidence=["fan is eager"]))
        assert s is not None
        assert s.purchase_intent == 0.9

    def test_out_of_range_rejected(self):
        assert _build_signals(valid_payload(purchase_intent=1.1)) is None

    def test_negative_rejected(self):
        assert _build_signals(valid_payload(confidence=-0.2)) is None

    def test_nan_rejected(self):
        assert _build_signals(valid_payload(purchase_intent=float("nan"))) is None

    def test_extra_fields_rejected(self):
        assert _build_signals(valid_payload(action="offer_ppv")) is None

    def test_missing_field_rejected(self):
        data = valid_payload()
        del data["evidence"]
        assert _build_signals(data) is None

    def test_wrong_type_rejected(self):
        assert _build_signals(valid_payload(explicit_purchase_request="yes")) is None

    def test_evidence_payment_data_rejected(self):
        assert _build_signals(valid_payload(evidence=["card number 4111111111111111"])) is None

    def test_requested_price_zero_rejected(self):
        assert _build_signals(valid_payload(requested_price=0)) is None

    def test_negative_price_rejected(self):
        assert _build_signals(valid_payload(requested_price=-5)) is None

    def test_string_price_rejected(self):
        assert _build_signals(valid_payload(requested_price="50")) is None


# ═══════════════════════════════════════════════════════════════════════════
# GROUP D — extract_commerce_signals happy path + transport (Chk 2)
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractHappyPath:
    @pytest.mark.asyncio
    async def test_valid_output_returns_signals(self):
        provider = fake_provider(json.dumps(valid_payload(purchase_intent=0.9)))
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("commerce.deepseek.get_llm_provider", lambda: provider)
            signals = await extract_commerce_signals(transcript())
        assert isinstance(signals, CommerceSignals)
        assert signals.purchase_intent == 0.9

    @pytest.mark.asyncio
    async def test_calls_generate_with_json_contract(self):
        provider = fake_provider(json.dumps(valid_payload()))
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("commerce.deepseek.get_llm_provider", lambda: provider)
            await extract_commerce_signals(transcript())
        provider.generate.assert_called_once()
        call_kwargs = provider.generate.call_args.kwargs
        assert call_kwargs["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_deterministic_for_same_input(self):
        with pytest.MonkeyPatch.context() as mp:
            provider = fake_provider(json.dumps(valid_payload(purchase_intent=0.75)))
            mp.setattr("commerce.deepseek.get_llm_provider", lambda: provider)
            s1 = await extract_commerce_signals(transcript())
            provider2 = fake_provider(json.dumps(valid_payload(purchase_intent=0.75)))
            mp.setattr("commerce.deepseek.get_llm_provider", lambda: provider2)
            s2 = await extract_commerce_signals(transcript())
        assert s1 == s2

    @pytest.mark.asyncio
    async def test_no_llm_call_on_empty_context(self):
        provider = fake_provider()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("commerce.deepseek.get_llm_provider", lambda: provider)
            s = await extract_commerce_signals([])
        assert s == CommerceSignals.low_information()
        provider.generate.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# GROUP E — failure handling: safe-low fallback (Chk 7)
# ═══════════════════════════════════════════════════════════════════════════


class TestFailureHandling:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure",
        [
            None,
            "",
            "not json",
            json.dumps([1, 2]),
            "```{",
        ],
    )
    async def test_malformed_output_yields_low_information(self, failure):
        provider = fake_provider(failure)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("commerce.deepseek.get_llm_provider", lambda: provider)
            s = await extract_commerce_signals(transcript())
        assert s == CommerceSignals.low_information()

    @pytest.mark.asyncio
    async def test_invalid_values_yield_low_information(self):
        provider = fake_provider(json.dumps(valid_payload(purchase_intent=7)))
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("commerce.deepseek.get_llm_provider", lambda: provider)
            s = await extract_commerce_signals(transcript())
        assert s == CommerceSignals.low_information()

    @pytest.mark.asyncio
    async def test_transport_exception_yields_low_information(self):
        provider = fake_provider(exc=RuntimeError("upstream down"))
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("commerce.deepseek.get_llm_provider", lambda: provider)
            s = await extract_commerce_signals(transcript())
        assert s == CommerceSignals.low_information()

    @pytest.mark.asyncio
    async def test_provider_failure_yields_low_information(self):
        def boom():
            raise RuntimeError("no provider")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("commerce.deepseek.get_llm_provider", boom)
            s = await extract_commerce_signals(transcript())
        assert s == CommerceSignals.low_information()

    @pytest.mark.asyncio
    async def test_failure_never_implies_interest(self):
        provider = fake_provider(json.dumps(valid_payload(purchase_intent=0.99)))
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("commerce.deepseek.get_llm_provider", lambda: provider)
            s = await extract_commerce_signals([])
        assert s.purchase_intent == 0.0
        assert s.explicit_purchase_request is False

    @pytest.mark.asyncio
    async def test_never_raises_on_any_failure(self):
        for failure in (None, "", "bad", "```{", 42):
            provider = fake_provider(failure)
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr("commerce.deepseek.get_llm_provider", lambda: provider)
                s = await extract_commerce_signals(transcript())
            assert isinstance(s, CommerceSignals)


# ═══════════════════════════════════════════════════════════════════════════
# GROUP F — privacy: bounded logging only (Chk 10)
# ═══════════════════════════════════════════════════════════════════════════


class TestLogPrivacy:
    @pytest.mark.asyncio
    async def test_failure_log_contains_no_conversation_or_raw_output(self, caplog):
        import logging

        secret_text = "fan said card number 4111111111111111 then buy now"
        provider = fake_provider(json.dumps(valid_payload(evidence=[secret_text])))
        with (
            caplog.at_level(logging.WARNING, logger="commerce_deepseek"),
            pytest.MonkeyPatch.context() as mp,
        ):
            mp.setattr("commerce.deepseek.get_llm_provider", lambda: provider)
            await extract_commerce_signals(transcript())
        logs = caplog.text
        assert "low-information" in logs
        assert secret_text not in logs
        assert "4111111111111111" not in logs

    @pytest.mark.asyncio
    async def test_malformed_output_log_has_no_raw_text(self, caplog):
        import logging

        raw = "here is my raw secret garbage: S3CR3T-RAW-OUTPUT"
        provider = fake_provider(raw)
        with (
            caplog.at_level(logging.WARNING, logger="commerce_deepseek"),
            pytest.MonkeyPatch.context() as mp,
        ):
            mp.setattr("commerce.deepseek.get_llm_provider", lambda: provider)
            await extract_commerce_signals(transcript())
        logs = caplog.text
        assert "low-information" in logs
        assert "S3CR3T-RAW-OUTPUT" not in logs
        assert "raw secret garbage" not in logs

    @pytest.mark.asyncio
    async def test_transport_failure_log_has_no_payload(self, caplog):
        import logging

        provider = fake_provider(exc=RuntimeError("boom"))
        with (
            caplog.at_level(logging.WARNING, logger="commerce_deepseek"),
            pytest.MonkeyPatch.context() as mp,
        ):
            mp.setattr("commerce.deepseek.get_llm_provider", lambda: provider)
            await extract_commerce_signals(transcript())
        assert "boom" in caplog.text or "failure" in caplog.text


# ═══════════════════════════════════════════════════════════════════════════
# GROUP G — no forbidden surfaces (Chk 11)
# ═══════════════════════════════════════════════════════════════════════════


class TestNoForbiddenSurfaces:
    def test_imports_are_limited(self):
        source = inspect.getsource(__import__("commerce.deepseek", fromlist=["x"]))
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
            "typing",
            "math",
            "pydantic",
            "commerce.signals",
            "core.config",
            "core.llm_provider",
            "core.provider_fallback",
        )
        for mod in imported:
            assert mod.startswith(allowed_prefixes), f"forbidden import: {mod}"

    def test_no_execution_surfaces_in_source(self):
        source = inspect.getsource(__import__("commerce.deepseek", fromlist=["x"]))
        for forbidden in (
            "enqueue_send",
            "publish_event",
            "send_ppv",
            "create_offer",
            "move_to_dlq",
            "asyncpg",
            "redis",
            "httpx",
            "requests",
            "add_to_operator_queue",
        ):
            assert forbidden not in source, f"forbidden token: {forbidden}"

    def test_no_randomness_or_clock(self):
        source = inspect.getsource(__import__("commerce.deepseek", fromlist=["x"]))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "random" not in alias.name and alias.name != "time"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "random" not in node.module and node.module != "time"

    def test_temperature_is_hardcoded_to_zero(self):
        source = inspect.getsource(__import__("commerce.deepseek", fromlist=["x"]))
        assert "temperature=SIGNAL_TEMPERATURE" in source
        assert "SIGNAL_TEMPERATURE = 0.0" in source
