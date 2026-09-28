"""llama.cpp LLM Provider Adapter (OpenAI-compatible).

HTTP client for a locally hosted llama.cpp OpenAI-compatible server::

    {LLAMA_BASE_URL}/v1/chat/completions

Contract:
- Non-streaming chat completions only (``stream: false``).
- ``max_output_tokens`` maps to ``max_tokens``.
- ``temperature`` maps to ``temperature``; ``top_p`` maps to ``top_p``.
- When ``response_mime_type == "application/json"`` without the OneCall
  opt-in, requests generic ``response_format: {"type": "json_object"}``
  (pre-constraint behavior, for non-OneCall JSON consumers).
- When ``response_mime_type == "application/json"`` WITH
  ``onecall_json_schema=True`` (canonical OneCall path only), requests
  constrained decoding via ``response_format: {"type": "json_schema", ...}``
  with the schema built from the executable ``OneCallReply`` contract
  (``core.one_call.build_onecall_json_schema``). If schema preparation ever
  fails, falls back to ``{"type": "json_object"}`` (fail-open, logged).
- Never sends Ollama-specific fields (``think``/``options``/``num_predict``/
  ``num_ctx``/``format``).
- Tool calling is NOT supported (``supports_tool_calling()`` is False).
- Embeddings are NOT supported (inherits ``LLMProvider.embed`` which raises
  ``NotImplementedError``).

Security model:
- Base URL, model, timeout, and optional API key come from Settings only.
- When ``LLAMA_API_KEY`` is empty/unset, no ``Authorization`` header is sent.
- When configured, sends ``Authorization: Bearer <key>`` per request.
- The API key is never logged, never included in exceptions, and never
  persisted.
"""

import logging
from typing import Any

import httpx

from core.config import get_settings
from core.llm_provider import LLMProvider, LLMProviderError, LLMRateLimitError

logger = logging.getLogger("llm_provider_llamacpp")

_settings = get_settings()

_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
_MODELS_PATH = "/v1/models"

# Truncation bound for server-supplied error text surfaced in exceptions.
_ERROR_MESSAGE_MAX_CHARS = 200

# Name used for the OneCall constrained-decoding schema envelope.
_ONECALL_SCHEMA_NAME = "OneCallReply"


def _onecall_response_format() -> dict[str, Any]:
    """Build the constrained-decoding ``response_format`` for OneCall requests.

    The schema is derived from the executable ``OneCallReply`` Pydantic model
    on every call (cheap, deterministic) so it can never drift from the
    application contract. Import is lazy to keep provider import light.

    Falls back to ``{"type": "json_object"}`` if schema preparation ever
    fails, preserving the pre-constraint request behavior (fail-open).
    """
    try:
        from core.one_call import build_onecall_json_schema

        return {
            "type": "json_schema",
            "json_schema": {
                "name": _ONECALL_SCHEMA_NAME,
                "schema": build_onecall_json_schema(),
                "strict": True,
            },
        }
    except Exception as exc:  # noqa: BLE001 — availability-preserving fallback
        logger.warning("llama.cpp OneCall schema unavailable, using json_object: %s", exc)
        return {"type": "json_object"}


class LlamaCppProvider(LLMProvider):
    """llama.cpp provider using the OpenAI-compatible chat completions API."""

    def __init__(self) -> None:
        """Initialize llama.cpp provider with configuration from Settings."""
        self._base_url = getattr(
            _settings, "llama_base_url", "http://localhost:8081"
        )
        self._model = getattr(_settings, "llama_model", "default")
        self._timeout = getattr(_settings, "llama_timeout", 120.0)
        self._api_key = getattr(_settings, "llama_api_key", "")
        self._client: httpx.AsyncClient | None = None
        # Token telemetry (mirrors OllamaProvider attribute names consumed
        # by one_call_pipeline/telemetry; fail-open None when unavailable).
        self.last_prompt_tokens: int | None = None
        self.last_generation_tokens: int | None = None
        self.last_total_tokens: int | None = None

    def _build_headers(self) -> dict[str, str]:
        """Build per-request headers without persisting secrets on the client."""
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the httpx async client (no auth stored on client)."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout),
                headers={"Content-Type": "application/json"},
            )
        return self._client

    def _sanitize(self, text: str) -> str:
        """Remove the configured API key from server-supplied text, if present."""
        if not text:
            return text
        if self._api_key and self._api_key in text:
            return text.replace(self._api_key, "[REDACTED]")
        return text

    def _server_message(self, exc: httpx.HTTPStatusError) -> str:
        """Extract a bounded, sanitized message from an HTTP error body."""
        try:
            response = exc.response
            if response is None:
                return ""
            try:
                body = response.json()
            except Exception:
                body = None
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict):
                    message = error.get("message", "")
                elif isinstance(error, str):
                    message = error
                else:
                    message = ""
                if isinstance(message, str) and message.strip():
                    return self._sanitize(message.strip())[:_ERROR_MESSAGE_MAX_CHARS]
            return ""
        except Exception:
            return ""

    def _classify_http_error(
        self, status_code: int, server_message: str = ""
    ) -> LLMProviderError:
        """Classify HTTP status into the existing LLMProviderError hierarchy."""
        suffix = f" {server_message}" if server_message else ""
        if status_code in (401, 403):
            return LLMProviderError(
                "llama.cpp authentication failed "
                f"(HTTP {status_code}).{suffix} "
                "Check LLAMA_API_KEY configuration."
            )
        if status_code == 429:
            return LLMRateLimitError(
                f"llama.cpp rate limit exceeded (HTTP 429).{suffix}"
            )
        if status_code == 404:
            return LLMProviderError(
                "llama.cpp endpoint or model not found "
                f"(HTTP 404).{suffix} "
                "Verify LLAMA_BASE_URL and LLAMA_MODEL."
            )
        if status_code >= 500:
            return LLMProviderError(f"llama.cpp server error (HTTP {status_code}).{suffix}")
        return LLMProviderError(f"llama.cpp HTTP error: {status_code}.{suffix}")

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        model: str,
        *,
        response_mime_type: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        onecall_json_schema: bool = False,
    ) -> dict[str, Any]:
        """Build an OpenAI-compatible chat completions payload.

        Only sends fields from the LLMProvider abstraction that llama.cpp
        supports. Never sends Ollama-specific fields.

        The OneCall ``json_schema`` constraint is applied ONLY when the
        caller explicitly opts in via ``onecall_json_schema=True``; generic
        ``application/json`` callers keep the pre-constraint ``json_object``
        behavior so non-OneCall response contracts are never coerced.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if response_mime_type == "application/json":
            if onecall_json_schema:
                payload["response_format"] = _onecall_response_format()
            else:
                payload["response_format"] = {"type": "json_object"}
        return payload

    def _parse_chat_response(self, data: Any) -> str:
        """Extract ``choices[0].message.content`` from a chat completions body.

        Records ``usage`` token counts when present (None otherwise).
        Raises LLMProviderError on missing/empty/malformed content.
        """
        if not isinstance(data, dict):
            raise LLMProviderError("llama.cpp returned malformed response (not an object)")
        choices = data.get("choices")
        if choices is None:
            raise LLMProviderError("llama.cpp response missing 'choices'")
        if not isinstance(choices, list) or len(choices) == 0:
            raise LLMProviderError("llama.cpp returned empty choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise LLMProviderError("llama.cpp returned malformed choice")
        message = first.get("message")
        if not isinstance(message, dict):
            raise LLMProviderError("llama.cpp response missing message")
        content = message.get("content")
        if content is None:
            raise LLMProviderError("llama.cpp response missing content")
        if not isinstance(content, str):
            raise LLMProviderError("llama.cpp returned non-text content")

        # Token telemetry (fail-open): OpenAI usage block, None when absent.
        try:
            usage = data.get("usage")
            if isinstance(usage, dict):
                prompt_tokens = usage.get("prompt_tokens")
                completion_tokens = usage.get("completion_tokens")
                total_tokens = usage.get("total_tokens")
                self.last_prompt_tokens = (
                    int(prompt_tokens) if isinstance(prompt_tokens, (int, float)) else None
                )
                self.last_generation_tokens = (
                    int(completion_tokens)
                    if isinstance(completion_tokens, (int, float))
                    else None
                )
                if isinstance(total_tokens, (int, float)):
                    self.last_total_tokens = int(total_tokens)
                elif (
                    self.last_prompt_tokens is not None
                    or self.last_generation_tokens is not None
                ):
                    self.last_total_tokens = int(self.last_prompt_tokens or 0) + int(
                        self.last_generation_tokens or 0
                    )
                else:
                    self.last_total_tokens = None
            else:
                self.last_prompt_tokens = None
                self.last_generation_tokens = None
                self.last_total_tokens = None
        except Exception:
            self.last_prompt_tokens = None
            self.last_generation_tokens = None
            self.last_total_tokens = None

        if not content.strip():
            raise LLMProviderError("llama.cpp returned empty response")
        return content

    async def _generate_chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        *,
        response_mime_type: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        timeout: float | None = None,
        onecall_json_schema: bool = False,
    ) -> str:
        """Execute one non-streaming chat completions request."""
        client = await self._get_client()
        effective_timeout = timeout if timeout is not None else self._timeout
        payload = self._build_payload(
            messages,
            model,
            response_mime_type=response_mime_type,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            top_p=top_p,
            onecall_json_schema=onecall_json_schema,
        )
        try:
            response = await client.post(
                _CHAT_COMPLETIONS_PATH,
                json=payload,
                headers=self._build_headers(),
                timeout=effective_timeout,
            )
            response.raise_for_status()
        except httpx.TimeoutException as e:
            raise LLMProviderError(f"llama.cpp request timed out: {e}") from e
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code if e.response is not None else 0
            raise self._classify_http_error(
                status_code, self._server_message(e)
            ) from e
        except httpx.RequestError as e:
            raise LLMProviderError(f"llama.cpp connection failed: {e}") from e

        try:
            data = response.json()
        except Exception as e:
            raise LLMProviderError("llama.cpp returned malformed JSON response") from e
        content = self._parse_chat_response(data)
        logger.info(
            "llama.cpp timing: prompt_tokens=%s gen_tokens=%s total_tokens=%s",
            self.last_prompt_tokens,
            self.last_generation_tokens,
            self.last_total_tokens,
        )
        return content

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
        """Generate a text response via ``POST /v1/chat/completions``."""
        effective_model = model or self._model
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_content},
        ]
        try:
            return await self._generate_chat(
                messages,
                effective_model,
                response_mime_type=response_mime_type,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                timeout=timeout_seconds,
                onecall_json_schema=onecall_json_schema,
            )
        except LLMProviderError:
            raise
        except Exception as e:
            raise LLMProviderError(f"llama.cpp generation failed: {e}") from e

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
        """Generate with conversation history, preserving system/user/assistant roles.

        The abstraction may supply ``model`` as a history role (Gemini
        convention); it is normalized to OpenAI ``assistant``. Assistant
        messages are never rewritten as user messages.
        """
        effective_model = model or self._model
        chat_messages = [{"role": "system", "content": system_instruction}]
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "model":
                role = "assistant"
            elif role not in ("system", "user", "assistant"):
                role = "user"
            chat_messages.append({"role": role, "content": content})
        try:
            return await self._generate_chat(
                chat_messages,
                effective_model,
                response_mime_type=response_mime_type,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                top_p=top_p,
                timeout=timeout_seconds,
                onecall_json_schema=onecall_json_schema,
            )
        except LLMProviderError:
            raise
        except Exception as e:
            raise LLMProviderError(f"llama.cpp generation failed: {e}") from e

    async def health_check(self) -> bool:
        """Check llama.cpp availability via lightweight ``GET /v1/models``.

        No generation is performed. Returns True on HTTP 200, False otherwise.
        """
        try:
            client = await self._get_client()
            response = await client.get(
                _MODELS_PATH,
                headers=self._build_headers(),
                timeout=10.0,
            )
            if response.status_code in (401, 403):
                logger.warning("llama.cpp health check authentication failed")
                return False
            if response.status_code == 200:
                return True
            logger.warning(
                "llama.cpp health check unexpected status=%s", response.status_code
            )
            return False
        except httpx.TimeoutException:
            logger.warning("llama.cpp health check timed out")
            return False
        except httpx.RequestError as e:
            logger.warning("llama.cpp health check connection failed: %s", e)
            return False
        except Exception as e:  # noqa: BLE001 — health check catch-all
            logger.warning("llama.cpp health check failed: %s", e)
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return "llamacpp"

    def supports_tool_calling(self) -> bool:
        """llama.cpp tool calling is out of scope for this change."""
        return False
