"""Tests for LLM Provider Abstraction (llama.cpp sole provider).

Covers:
- Provider interface contract (preserved abstraction)
- Factory returns LlamaCppProvider for 'llamacpp' only
- Invalid provider names raise ValueError (no fake alternatives)
- Error hierarchy (LLMProviderError / LLMRateLimitError)
- Security: no new HTTP clients beyond httpx, no hardcoded secrets
"""

from unittest.mock import MagicMock, patch

import pytest

from core.llm_provider import (
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    get_llm_provider,
)


class TestLLMProviderInterface:
    """Test the abstract LLMProvider interface contract."""

    def test_provider_is_abstract(self):
        with pytest.raises(TypeError):
            LLMProvider()  # type: ignore

    def test_provider_has_required_methods(self):
        assert hasattr(LLMProvider, "generate")
        assert hasattr(LLMProvider, "generate_with_history")
        assert hasattr(LLMProvider, "health_check")
        assert hasattr(LLMProvider, "provider_name")

    def test_concrete_provider_must_implement_all(self):
        class IncompleteProvider(LLMProvider):
            async def generate(self, *args, **kwargs) -> str:
                return "test"

        with pytest.raises(TypeError):
            IncompleteProvider()  # type: ignore

    def test_concrete_provider_works(self):
        class CompleteProvider(LLMProvider):
            async def generate(self, *args, **kwargs) -> str:
                return "test"

            async def generate_with_history(self, *args, **kwargs) -> str:
                return "test"

            async def health_check(self) -> bool:
                return True

            @property
            def provider_name(self) -> str:
                return "test"

        provider = CompleteProvider()
        assert provider.provider_name == "test"

    def test_onecall_flag_in_signature(self):
        import inspect

        sig = inspect.signature(LLMProvider.generate)
        assert "onecall_json_schema" in sig.parameters
        sig2 = inspect.signature(LLMProvider.generate_with_history)
        assert "onecall_json_schema" in sig2.parameters


class TestProviderSelection:
    """Factory exposes only llama.cpp."""

    def test_default_provider_is_llamacpp(self):
        with patch("core.llm_provider.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(llm_provider="llamacpp")
            provider = get_llm_provider()
            assert provider.provider_name == "llamacpp"

    def test_missing_provider_defaults_to_llamacpp(self):
        with patch("core.llm_provider.get_settings") as mock_settings:
            settings = MagicMock(spec=[])
            mock_settings.return_value = settings
            provider = get_llm_provider()
            assert provider.provider_name == "llamacpp"

    def test_ollama_rejected(self):
        with patch("core.llm_provider.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(llm_provider="ollama")
            with pytest.raises(ValueError, match="Unknown LLM provider"):
                get_llm_provider()

    def test_gemini_rejected(self):
        with patch("core.llm_provider.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(llm_provider="gemini")
            with pytest.raises(ValueError, match="Unknown LLM provider"):
                get_llm_provider()

    def test_invalid_provider_raises_error(self):
        with patch("core.llm_provider.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(llm_provider="invalid")
            with pytest.raises(ValueError, match="Unknown LLM provider"):
                get_llm_provider()

    def test_factory_returns_llmprovider_instance(self):
        with patch("core.llm_provider.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(llm_provider="llamacpp")
            provider = get_llm_provider()
            assert isinstance(provider, LLMProvider)


class TestErrorHandling:
    def test_provider_error_is_exception(self):
        assert issubclass(LLMProviderError, Exception)

    def test_rate_limit_error_is_provider_error(self):
        assert issubclass(LLMRateLimitError, LLMProviderError)

    def test_rate_limit_error_can_be_caught_as_provider_error(self):
        with pytest.raises(LLMProviderError):
            raise LLMRateLimitError("rate limited")


class TestSecurityConstraints:
    def test_no_new_http_clients_imported(self):
        import core.llm_provider
        import core.llm_provider_llamacpp

        forbidden = ["requests", "aiohttp", "urllib3"]
        for module in [core.llm_provider, core.llm_provider_llamacpp]:
            source = open(module.__file__).read()
            for client in forbidden:
                assert f"import {client}" not in source

    def test_llamacpp_uses_httpx(self):
        import core.llm_provider_llamacpp

        source = open(core.llm_provider_llamacpp.__file__).read()
        assert "import httpx" in source

    def test_no_ollama_gemini_imports_in_factory(self):
        import core.llm_provider

        source = open(core.llm_provider.__file__).read()
        assert "llm_provider_ollama" not in source
        assert "llm_provider_gemini" not in source
        assert "OllamaProvider" not in source
        assert "GeminiProvider" not in source


class TestIntegration:
    def test_provider_has_required_attributes(self):
        with patch("core.llm_provider.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(llm_provider="llamacpp")
            provider = get_llm_provider()
            assert hasattr(provider, "generate")
            assert hasattr(provider, "generate_with_history")
            assert hasattr(provider, "health_check")
            assert hasattr(provider, "provider_name")

    def test_provider_generate_signature(self):
        import inspect

        with patch("core.llm_provider.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(llm_provider="llamacpp")
            provider = get_llm_provider()
            sig = inspect.signature(provider.generate)
            params = list(sig.parameters.keys())
            assert "system_instruction" in params
            assert "user_content" in params
            assert "model" in params
            assert "onecall_json_schema" in params
