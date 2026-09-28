"""LLM Provider Abstraction Layer (llama.cpp sole provider).

Provider-agnostic interface for LLM text generation. Designed around the
actual CRM workloads: system instruction + user content → text or JSON.

Architecture constraints:
- No ORM, no second scheduler, no second Telegram sender
- Preserve Redis Streams, asyncpg, Telethon
- LLM interprets → application decides → AutomationService executes
- llama.cpp is the sole production provider (local OpenAI-compatible server)

Usage::

    from core.llm_provider import get_llm_provider
    provider = get_llm_provider()
    response = await provider.generate(
        system_instruction="You are a helpful assistant.",
        user_content="Hello!",
    )
"""

import abc
import logging
from dataclasses import dataclass, field
from typing import Any

from core.config import get_settings

logger = logging.getLogger("llm_provider")


@dataclass
class ToolCall:
    """Represents a tool/function call from the LLM."""
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Result of a tool execution to send back to the LLM."""
    name: str
    response: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""
    text: str | None = None
    tool_calls: list[ToolCall] | None = None
    candidates: list[Any] | None = None


class LLMProvider(abc.ABC):
    """Abstract base class for LLM providers.

    Designed around the actual CRM workloads: system instruction + user
    content → text or JSON response. Minimal interface matching the 8
    call sites identified in the forensic audit.
    """

    @abc.abstractmethod
    async def generate(
        self,
        system_instruction: str,
        user_content: str,
        model: str | None = None,
        *,
        response_mime_type: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        timeout_seconds: float | None = None,
        onecall_json_schema: bool = False,
    ) -> str:
        """Generate a text response from the LLM.

        Args:
            system_instruction: System prompt defining behavior.
            user_content: User message content.
            model: Model identifier (provider-specific). If None, uses provider default.
            response_mime_type: Response format (e.g., "application/json").
            max_output_tokens: Maximum tokens in response.
            temperature: Sampling temperature (0.0-1.0).
            timeout_seconds: Request timeout override.
            onecall_json_schema: Request the canonical OneCall structured-output
                contract. Only meaningful for providers that support it
                (llama.cpp); other providers ignore it. Non-OneCall callers
                must leave this False so generic JSON behavior is preserved.

        Returns:
            Generated text response.

        Raises:
            LLMProviderError: On transport, timeout, or generation failures.
            LLMRateLimitError: On rate limit exhaustion.
        """
        ...

    @abc.abstractmethod
    async def generate_with_history(
        self,
        system_instruction: str,
        messages: list[dict[str, str]],
        model: str | None = None,
        *,
        response_mime_type: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        timeout_seconds: float | None = None,
        top_p: float | None = None,
        onecall_json_schema: bool = False,
    ) -> str:
        """Generate a text response with conversation history.

        Args:
            system_instruction: System prompt defining behavior.
            messages: List of message dicts with 'role' and 'content' keys.
                     Roles: 'user', 'model'/'assistant', 'system'
            model: Model identifier (provider-specific). If None, uses provider default.
            response_mime_type: Response format (e.g., "application/json").
            max_output_tokens: Maximum tokens in response.
            temperature: Sampling temperature (0.0-1.0).
            timeout_seconds: Request timeout override.
            top_p: Top-p sampling parameter.
            onecall_json_schema: Request the canonical OneCall structured-output
                contract. Only meaningful for providers that support it
                (llama.cpp); other providers ignore it. Non-OneCall callers
                must leave this False so generic JSON behavior is preserved.

        Returns:
            Generated text response.

        Raises:
            LLMProviderError: On transport, timeout, or generation failures.
            LLMRateLimitError: On rate limit exhaustion.
        """
        ...

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is healthy and reachable.

        Returns:
            True if healthy, False otherwise.
        """
        ...

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'gemini', 'ollama')."""
        ...

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        """Generate an embedding vector for the given text.

        Default implementation raises NotImplementedError. Providers that
        support embeddings should override this method.

        Args:
            text: Text to embed.
            model: Embedding model name (provider-specific).

        Returns:
            List of floats representing the embedding vector.

        Raises:
            NotImplementedError: If provider doesn't support embeddings.
        """
        raise NotImplementedError(
            f"Embeddings not supported by {self.provider_name} provider"
        )

    def supports_tool_calling(self) -> bool:
        """Check if the provider supports tool/function calling.

        Returns:
            True if tool calling is supported, False otherwise.
        """
        return False


class LLMProviderError(Exception):
    """Base exception for LLM provider errors."""
    pass


class LLMRateLimitError(LLMProviderError):
    """Raised when rate limit is exhausted."""
    pass


def get_llm_provider() -> LLMProvider:
    """Factory function to get the configured LLM provider.

    llama.cpp is the sole supported provider. ``LLM_PROVIDER`` is retained
    for deployment/testing clarity but only ``llamacpp`` is accepted.

    Returns:
        LlamaCppProvider instance.

    Raises:
        ValueError: If LLM_PROVIDER is anything other than 'llamacpp'.
    """
    settings = get_settings()
    provider_name = getattr(settings, "llm_provider", "llamacpp")

    if provider_name == "llamacpp":
        from core.llm_provider_llamacpp import LlamaCppProvider
        return LlamaCppProvider()
    else:
        raise ValueError(
            f"Unknown LLM provider: {provider_name!r}. "
            f"Valid options: 'llamacpp'"
        )
