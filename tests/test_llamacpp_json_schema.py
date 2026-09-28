"""llama.cpp JSON Schema constrained decoding for OneCall (regression).

Covers:
- schema generation from the executable OneCallReply contract
- request construction (json_schema envelope, existing fields preserved)
- response parsing through the normal provider/application path
- failure behavior (malformed/unusable responses keep existing error path)
- OneCall pipeline wire-model propagation per active provider

All transport is mocked; no live server required.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from commerce.signals import _NEGATIVE_INTENTS, INTENT_CATEGORIES
from core.one_call import OneCallReply, build_onecall_json_schema, validate_one_call_response


def _walk(node: Any):
    """Yield every dict/list node in a JSON-like structure."""
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


# ============================================================================
# Schema generation
# ============================================================================


class TestOneCallJsonSchemaGeneration:
    """Schema comes from OneCallReply.model_json_schema(), normalized."""

    def test_derived_from_executable_model(self):
        """Top-level shape tracks the Pydantic contract (no static duplicate)."""
        schema = build_onecall_json_schema()
        derived = OneCallReply.model_json_schema()
        assert schema["type"] == derived["type"] == "object"
        assert set(schema["properties"]) == set(OneCallReply.model_fields)
        assert set(schema["properties"]["commerce_signals"]["properties"]) >= {
            "purchase_intent",
            "primary_intent",
            "requested_price",
            "evidence",
            "fan_asks_question",
        }

    def test_local_refs_inlined(self):
        """$defs are inlined; no $defs key and no local $ref remains."""
        schema = build_onecall_json_schema()
        assert "$defs" not in schema
        for node in _walk(schema):
            if isinstance(node, dict) and "$ref" in node:
                ref = node["$ref"]
                assert not str(ref).startswith("#/"), f"unresolved local ref: {ref}"
        signals = schema["properties"]["commerce_signals"]
        assert signals.get("type") == "object"
        assert isinstance(signals.get("properties"), dict)

    def test_top_level_additional_properties_false(self):
        """Top-level extra=forbid is preserved in the constraint."""
        assert build_onecall_json_schema().get("additionalProperties") is False

    def test_nested_signals_not_forbidden(self):
        """Nested CommerceSignals keeps extra=ignore (no additionalProperties:false)."""
        signals = build_onecall_json_schema()["properties"]["commerce_signals"]
        assert "additionalProperties" not in signals

    def test_primary_intent_enum_matches_executable_source(self):
        """The 22 INTENT_CATEGORIES values are enforced (not a second list)."""
        enum = build_onecall_json_schema()["properties"]["commerce_signals"][
            "properties"
        ]["primary_intent"]["enum"]
        assert sorted(enum) == sorted(INTENT_CATEGORIES)
        assert len(enum) == 22

    def test_negative_intent_tags_enum_matches_executable_source(self):
        """negative_intent_tags items enforce the executable negative set."""
        items = build_onecall_json_schema()["properties"]["commerce_signals"][
            "properties"
        ]["negative_intent_tags"]["items"]
        assert items["type"] == "string"
        assert sorted(items["enum"]) == sorted(_NEGATIVE_INTENTS)

    def test_numeric_bounds_are_valid_json_schema(self):
        """No Pydantic-internal ge/le/gt/lt keys; standard bounds present."""
        text = json.dumps(build_onecall_json_schema())
        for internal in ('"ge"', '"le"', '"gt"', '"lt"'):
            assert internal not in text
        confidence = build_onecall_json_schema()["properties"]["confidence"]
        assert confidence["minimum"] == 0.0
        assert confidence["maximum"] == 1.0
        price = build_onecall_json_schema()["properties"]["commerce_signals"][
            "properties"
        ]["requested_price"]
        number_branch = next(
            b for b in price["anyOf"] if b.get("type") == "number"
        )
        assert number_branch["exclusiveMinimum"] == 0
        assert any(b.get("type") == "null" for b in price["anyOf"])

    def test_required_matches_pydantic_contract(self):
        """Only truly required model fields are required (defaults optional)."""
        expected = [n for n, f in OneCallReply.model_fields.items() if f.is_required()]
        assert build_onecall_json_schema()["required"] == expected
        assert expected == ["reply"]

    def test_deterministic(self):
        """Repeated builds are identical (no randomness/clock)."""
        assert build_onecall_json_schema() == build_onecall_json_schema()


# ============================================================================
# Request construction (mocked HTTP)
# ============================================================================


def _mock_client(response):
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    return client


def _ok_response(content: str):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    mock_response.raise_for_status = MagicMock()
    return mock_response


@pytest.fixture
def llamacpp_settings():
    """Patch provider settings without touching the repo .env."""
    with patch("core.llm_provider_llamacpp._settings") as mock:
        mock.llama_base_url = "http://localhost:8081"
        mock.llama_model = "test-model"
        mock.llama_timeout = 60.0
        mock.llama_api_key = ""
        yield mock


class TestConstrainedRequestConstruction:
    """application/json requests carry the OneCall json_schema envelope."""

    @pytest.mark.asyncio
    async def test_json_mime_sends_json_schema_envelope(self, llamacpp_settings):
        """response_format.type/name/strict match the tested live payload."""
        from core.llm_provider_llamacpp import LlamaCppProvider

        provider = LlamaCppProvider()
        real_post_client = _mock_client(_ok_response('{"reply": "hi"}'))

        async def _fake_get_client():
            return real_post_client

        provider._get_client = _fake_get_client  # type: ignore[method-assign]
        await provider.generate(
            system_instruction="sys",
            user_content="user",
            response_mime_type="application/json",
            onecall_json_schema=True,
            max_output_tokens=400,
            temperature=0.7,
        )
        sent = real_post_client.post.call_args[1]["json"]
        rf = sent["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"] == "OneCallReply"
        assert rf["json_schema"]["strict"] is True
        assert rf["json_schema"]["schema"] == build_onecall_json_schema()

    @pytest.mark.asyncio
    async def test_generic_json_keeps_json_object(self, llamacpp_settings):
        """Without the OneCall opt-in, generic JSON keeps pre-constraint behavior."""
        from core.llm_provider_llamacpp import LlamaCppProvider

        provider = LlamaCppProvider()
        real_post_client = _mock_client(_ok_response('{"facts": {}}'))

        async def _fake_get_client():
            return real_post_client

        provider._get_client = _fake_get_client  # type: ignore[method-assign]
        await provider.generate(
            system_instruction="sys",
            user_content="user",
            response_mime_type="application/json",
            max_output_tokens=400,
            temperature=0.7,
        )
        sent = real_post_client.post.call_args[1]["json"]
        assert sent["response_format"] == {"type": "json_object"}
        assert "OneCallReply" not in json.dumps(sent)

    def test_generic_payload_has_no_onecall_schema(self, llamacpp_settings):
        """Unit-level: generic payload carries no OneCall schema information."""
        from core.llm_provider_llamacpp import LlamaCppProvider

        provider = LlamaCppProvider()
        payload = provider._build_payload(
            [{"role": "user", "content": "hi"}],
            "m",
            response_mime_type="application/json",
            max_output_tokens=400,
        )
        assert payload["response_format"] == {"type": "json_object"}
        assert "OneCallReply" not in json.dumps(payload)

    def test_explicit_opt_out_even_with_json_mime(self, llamacpp_settings):
        """onecall_json_schema=False is identical to the default generic path."""
        from core.llm_provider_llamacpp import LlamaCppProvider

        provider = LlamaCppProvider()
        payload = provider._build_payload(
            [{"role": "user", "content": "hi"}],
            "m",
            response_mime_type="application/json",
            onecall_json_schema=False,
        )
        assert payload["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_existing_request_properties_preserved(self, llamacpp_settings):
        """Endpoint fields, non-streaming, timeout/auth behavior unchanged."""
        from core.llm_provider_llamacpp import LlamaCppProvider

        provider = LlamaCppProvider()
        client = _mock_client(_ok_response('{"reply": "hi"}'))

        async def _fake_get_client():
            return client

        provider._get_client = _fake_get_client  # type: ignore[method-assign]
        await provider.generate(
            system_instruction="sys",
            user_content="user",
            model="custom-model",
            response_mime_type="application/json",
            max_output_tokens=400,
            temperature=0.7,
        )
        args, kwargs = client.post.call_args
        assert args[0] == "/v1/chat/completions"
        sent = kwargs["json"]
        assert sent["model"] == "custom-model"
        assert sent["stream"] is False
        assert sent["max_tokens"] == 400
        assert sent["temperature"] == 0.7
        assert kwargs["timeout"] == 60.0
        assert kwargs["headers"] == {"Content-Type": "application/json"}

    def test_no_response_format_without_json_mime(self, llamacpp_settings):
        """Non-JSON requests do not carry any response_format."""
        from core.llm_provider_llamacpp import LlamaCppProvider

        provider = LlamaCppProvider()
        payload = provider._build_payload(
            [{"role": "user", "content": "hi"}], "m", max_output_tokens=10
        )
        assert "response_format" not in payload

    def test_history_path_also_constrained(self, llamacpp_settings):
        """generate_with_history maps application/json + opt-in the same way."""
        from core.llm_provider_llamacpp import LlamaCppProvider

        provider = LlamaCppProvider()
        payload = provider._build_payload(
            [{"role": "user", "content": "hi"}],
            "m",
            response_mime_type="application/json",
            onecall_json_schema=True,
            top_p=0.8,
        )
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["top_p"] == 0.8

    def test_history_generic_path_keeps_json_object(self, llamacpp_settings):
        """generate_with_history without opt-in keeps generic JSON behavior."""
        from core.llm_provider_llamacpp import LlamaCppProvider

        provider = LlamaCppProvider()
        payload = provider._build_payload(
            [{"role": "user", "content": "hi"}],
            "m",
            response_mime_type="application/json",
            top_p=0.8,
        )
        assert payload["response_format"] == {"type": "json_object"}
        assert "OneCallReply" not in json.dumps(payload)


# ============================================================================
# Response parsing + application validation still executes
# ============================================================================


class TestConstrainedResponseValidation:
    """Constrained output flows through unchanged application validation."""

    @pytest.mark.asyncio
    async def test_valid_constrained_response_end_to_end(self, llamacpp_settings):
        """Provider returns content; OneCall validation accepts it."""
        from core.llm_provider_llamacpp import LlamaCppProvider

        body = json.dumps({
            "reply": "Hey Alex! Great to see you here!",
            "commerce_signals": {
                "purchase_intent": 0.1,
                "content_interest": 0.4,
                "relationship_engagement": 0.7,
                "price_interest": 0.0,
                "explicit_purchase_request": False,
                "explicit_content_request": False,
                "requested_price": None,
                "declined_recent_offer": False,
                "asks_for_free_content": False,
                "negative_sentiment": 0.0,
                "confidence": 0.8,
                "evidence": ["Great to see you here"],
                "model_uncertainty": 0.2,
                "primary_intent": "greeting",
                "intent_tags": ["greeting"],
                "negative_intent_tags": [],
                "fan_asks_question": False,
            },
            "confidence": 0.85,
            "needs_handoff": False,
        })
        provider = LlamaCppProvider()
        client = _mock_client(_ok_response(body))

        async def _fake_get_client():
            return client

        provider._get_client = _fake_get_client  # type: ignore[method-assign]
        content = await provider.generate(
            system_instruction="sys",
            user_content="user",
            response_mime_type="application/json",
        )
        result = validate_one_call_response(content)
        assert result.is_valid is True
        assert result.reply == "Hey Alex! Great to see you here!"
        assert result.confidence == 0.85
        assert provider.last_prompt_tokens == 10
        assert provider.last_generation_tokens == 5

    def test_app_validation_still_rejects_bad_envelope(self):
        """Constrained decoding does not weaken validation (bad values fail)."""
        bad = json.dumps({
            "reply": "hi",
            "commerce_signals": {"primary_intent": "buy_now_xxx"},
            "confidence": "High",
            "needs_handoff": False,
        })
        result = validate_one_call_response(bad)
        assert result.is_valid is False
        assert result.needs_handoff is True

    def test_app_validation_still_rejects_non_envelope(self):
        """Prose output still follows the invalid path even if ever produced."""
        result = validate_one_call_response("Hey Alex! How is it going?")
        assert result.is_valid is False
        assert result.needs_handoff is True


# ============================================================================
# Failure behavior (existing error/fallback path preserved)
# ============================================================================


class TestConstrainedFailureBehavior:
    """Malformed/unusable responses keep the existing error path."""

    @pytest.mark.asyncio
    async def test_malformed_body_raises(self, llamacpp_settings):
        """Non-JSON HTTP body raises LLMProviderError (no silent fallback)."""
        from core.llm_provider import LLMProviderError
        from core.llm_provider_llamacpp import LlamaCppProvider

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("bad json")
        mock_response.raise_for_status = MagicMock()
        provider = LlamaCppProvider()
        client = _mock_client(mock_response)

        async def _fake_get_client():
            return client

        provider._get_client = _fake_get_client  # type: ignore[method-assign]
        with pytest.raises(LLMProviderError, match="malformed JSON response"):
            await provider.generate(system_instruction="s", user_content="u")

    @pytest.mark.asyncio
    async def test_empty_content_raises(self, llamacpp_settings):
        """Empty constrained content raises (existing guard preserved)."""
        from core.llm_provider import LLMProviderError
        from core.llm_provider_llamacpp import LlamaCppProvider

        provider = LlamaCppProvider()
        client = _mock_client(_ok_response("   "))

        async def _fake_get_client():
            return client

        provider._get_client = _fake_get_client  # type: ignore[method-assign]
        with pytest.raises(LLMProviderError, match="empty response"):
            await provider.generate(
                system_instruction="s",
                user_content="u",
                response_mime_type="application/json",
            )


# ============================================================================
# Pipeline wire-model propagation
# ============================================================================


class TestPipelineWireModel:
    """OneCall wire model reflects the active provider's configured model."""

    @pytest.mark.asyncio
    async def test_llamacpp_provider_model_propagated(self):
        """llamacpp active: wire model is LLAMA_MODEL, not qwen2.5:3b."""
        from core.one_call_pipeline import one_call_generation

        mock_provider = AsyncMock()
        mock_provider.provider_name = "llamacpp"
        mock_provider._model = "llama-test-model"
        mock_provider.generate = AsyncMock(return_value=json.dumps({
            "reply": "Hey there!",
            "confidence": 0.8,
        }))
        with patch(
            "core.one_call_pipeline.get_llm_provider", return_value=mock_provider
        ):
            await one_call_generation(
                user_id=1,
                creator_id=1,
                user_message="Hello!",
                persona="You are Sunny.",
                profile={},
                user={"first_name": "Alex", "funnel_stage": "new"},
            )
        assert mock_provider.generate.call_args[1]["model"] == "llama-test-model"

    @pytest.mark.asyncio
    async def test_non_string_provider_model_falls_back(self):
        """Providers without a string _model fall back to LLAMA_MODEL."""
        from core.one_call_pipeline import one_call_generation

        mock_provider = AsyncMock()
        mock_provider.provider_name = "llamacpp"
        del mock_provider._model  # no _model attr: getattr -> default None
        mock_provider.generate = AsyncMock(return_value=json.dumps({
            "reply": "Hey there!",
            "confidence": 0.8,
        }))
        with patch(
            "core.one_call_pipeline.get_llm_provider", return_value=mock_provider
        ), patch("core.one_call_pipeline._settings") as mock_settings:
            mock_settings.llama_model = "default"
            mock_settings.llm_provider = "llamacpp"
            await one_call_generation(
                user_id=1,
                creator_id=1,
                user_message="Hello!",
                persona="You are Sunny.",
                profile={},
                user={"first_name": "Alex", "funnel_stage": "new"},
            )
        assert mock_provider.generate.call_args[1]["model"] == "default"

    @pytest.mark.asyncio
    async def test_pipeline_requests_onecall_schema(self):
        """Canonical OneCall path explicitly opts into the OneCall constraint."""
        from core.one_call_pipeline import one_call_generation

        mock_provider = AsyncMock()
        mock_provider.provider_name = "llamacpp"
        mock_provider._model = "llama-test-model"
        mock_provider.generate = AsyncMock(return_value=json.dumps({
            "reply": "Hey there!",
            "confidence": 0.8,
        }))
        with patch(
            "core.one_call_pipeline.get_llm_provider", return_value=mock_provider
        ):
            await one_call_generation(
                user_id=1,
                creator_id=1,
                user_message="Hello!",
                persona="You are Sunny.",
                profile={},
                user={"first_name": "Alex", "funnel_stage": "new"},
            )
        assert mock_provider.generate.call_args[1]["onecall_json_schema"] is True


# ============================================================================
# Non-OneCall JSON consumers keep generic behavior
# ============================================================================


def _assert_generic_request(mock_generate):
    """The caller must not opt into the OneCall schema constraint."""
    assert mock_generate.call_count == 1
    assert mock_generate.call_args[1].get("onecall_json_schema", False) is False


class TestNonOneCallConsumers:
    """Audited consumers (profile/deepseek/scoring) stay on generic JSON."""

    @pytest.mark.asyncio
    async def test_profile_extraction_uses_generic_json(self):
        """memory/profile.py must not request the OneCall schema."""
        from memory.profile import extract_profile_facts

        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value=json.dumps({
            "facts": {"interests": ["horror movies"]},
            "confidence": {"interests": "explicit"},
        }))
        with patch("memory.profile.get_llm_provider", return_value=mock_provider):
            facts, confidence = await extract_profile_facts("Fan: I love horror movies")
        assert facts == {"interests": ["horror movies"]}
        assert confidence == {"interests": "explicit"}
        _assert_generic_request(mock_provider.generate)

    @pytest.mark.asyncio
    async def test_commerce_signals_use_generic_json(self):
        """commerce/deepseek.py must not request the OneCall schema."""
        from commerce.deepseek import extract_commerce_signals

        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value=json.dumps({
            "purchase_intent": 0.1,
            "content_interest": 0.4,
            "relationship_engagement": 0.6,
            "price_interest": 0.0,
            "explicit_purchase_request": False,
            "explicit_content_request": False,
            "requested_price": None,
            "declined_recent_offer": False,
            "asks_for_free_content": False,
            "negative_sentiment": 0.0,
            "confidence": 0.7,
            "evidence": [],
            "model_uncertainty": 0.3,
            "primary_intent": "casual_chat",
            "intent_tags": [],
            "negative_intent_tags": [],
            "fan_asks_question": False,
        }))
        with patch("commerce.deepseek.get_llm_provider", return_value=mock_provider):
            signals = await extract_commerce_signals(
                [{"role": "user", "content": "hey beautiful"}]
            )
        assert signals.purchase_intent == 0.1
        assert signals.primary_intent == "casual_chat"
        _assert_generic_request(mock_provider.generate)

    @pytest.mark.asyncio
    async def test_scoring_uses_generic_json(self):
        """core/scoring.py legacy path must not request the OneCall schema."""
        from core.scoring import score_draft

        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value=json.dumps({
            "contextually_aware": 8,
            "natural_tone": 8,
            "appropriate_length": 8,
            "not_repetitive": 8,
            "flags": [],
        }))
        with patch("core.scoring.get_llm_provider", return_value=mock_provider):
            score, flags = await score_draft("Hey there!", "Hello!", [])
        assert score == pytest.approx(0.8)
        assert flags == []
        _assert_generic_request(mock_provider.generate)


# ============================================================================
# Sole-provider flag ownership
# ============================================================================


class TestFlagCompatibility:
    """Only llamacpp implements the flag; no alternate providers remain."""

    def test_factory_has_no_alternate_providers(self):
        import core.llm_provider

        source = open(core.llm_provider.__file__).read()
        assert "OllamaProvider" not in source
        assert "GeminiProvider" not in source
        assert "llamacpp" in source
