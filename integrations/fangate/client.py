"""Async HTTP client for the Fangate API.

Transport/API abstraction only — NO business logic. Uses httpx (already a
project dependency) for async support, connection reuse, and timeouts.

Security rules:
- Never log API keys, Authorization headers, or request/response bodies.
- Errors retain operation + status code only.
"""

import logging
import time
from typing import Any, Self

import httpx

from core.config import get_settings

from .errors import (
    FangateAuthenticationError,
    FangateAuthorizationError,
    FangateError,
    FangateNotFoundError,
    FangateRateLimitError,
    FangateResponseError,
    FangateServerError,
    FangateTimeoutError,
    FangateTransportError,
    FangateValidationError,
)

SUPPORTED_WEBHOOK_EVENTS = ("payment.successful", "payment.failed", "payment.pending")

from .models import (
    FangateContentFolder,
    FangateFolderDeleteResult,
    FangateProduct,
    FangateProductPage,
    FangateWallet,
    FangateWebhook,
)

logger = logging.getLogger("integrations.fangate.client")

_BASE_URL = get_settings().fangate_api_base_url
_TIMEOUT = get_settings().fangate_api_timeout


def _map_status_error(
    operation: str,
    status_code: int,
    body: Any,
    retry_after: float | None = None,
) -> FangateError:
    """Map an HTTP status code to the matching Fangate exception."""
    message = None
    if isinstance(body, dict):
        message = body.get("errors_message") or body.get("message")
    if status_code == 401:
        return FangateAuthenticationError(operation, message=message, status_code=status_code)
    if status_code == 403:
        return FangateAuthorizationError(operation, message=message, status_code=status_code)
    if status_code == 404:
        return FangateNotFoundError(operation, message=message, status_code=status_code)
    if status_code == 429:
        return FangateRateLimitError(
            operation,
            message=message,
            status_code=status_code,
            retry_after=retry_after,
        )
    if status_code in (400, 422):
        return FangateValidationError(operation, message=message, status_code=status_code)
    if 500 <= status_code < 600:
        return FangateServerError(operation, message=message, status_code=status_code)
    return FangateError(operation, message=message, status_code=status_code)


class FangateClient:
    """Minimal async wrapper around httpx for Fangate calls."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        timeout: float | None = None,
        api_prefix: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("A Fangate API key is required")
        self._api_key = api_key  # stored in memory only; never logged
        self._timeout = timeout if timeout is not None else _TIMEOUT
        self._client = httpx.AsyncClient(
            base_url=(base_url or _BASE_URL) + api_prefix,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(self._timeout),
            transport=transport,
            # Never follow redirects: an attacker-influenced Location header
            # must not receive the Authorization header on a second hop.
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _request(
        self, method: str, path: str, *, params: dict | None = None, json: dict | None = None
    ) -> Any:
        """Perform a request and return the parsed JSON envelope data.

        Raises Fangate* exceptions on transport or API failures.
        """
        operation = f"{method} {path}"
        start = time.monotonic()
        try:
            response = await self._client.request(method, path, params=params, json=json)
        except httpx.TimeoutException as exc:
            logger.warning("Fangate timeout on %s after %.1fs", operation, time.monotonic() - start)
            raise FangateTimeoutError(operation) from exc
        except httpx.RequestError as exc:
            logger.warning("Fangate transport failure on %s: %s", operation, exc.__class__.__name__)
            raise FangateTransportError(operation) from exc

        elapsed = round((time.monotonic() - start) * 1000, 1)

        if response.is_error:
            body = _parse_body(response)
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            logger.warning(
                "Fangate HTTP %d on %s (%.0fms)",
                response.status_code,
                operation,
                elapsed,
            )
            raise _map_status_error(operation, response.status_code, body, retry_after)

        body = _parse_body(response)
        if isinstance(body, dict):
            if body.get("success") is False:
                message = body.get("errors_message") or body.get("message")
                logger.warning("Fangate business failure on %s (%.0fms)", operation, elapsed)
                raise FangateError(operation, message=message, status_code=response.status_code)
            return body.get("data")
        if isinstance(body, list):
            return body
        logger.error("Fangate malformed response on %s (%.0fms)", operation, elapsed)
        raise FangateResponseError(operation, status_code=response.status_code)

    async def _request_multipart(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any:
        """Perform a multipart/form-data request and return parsed JSON envelope data.

        Raises Fangate* exceptions on transport or API failures.
        """
        operation = f"{method} {path}"
        start = time.monotonic()
        try:
            response = await self._client.request(method, path, data=data, files=files)
        except httpx.TimeoutException as exc:
            logger.warning("Fangate timeout on %s after %.1fs", operation, time.monotonic() - start)
            raise FangateTimeoutError(operation) from exc
        except httpx.RequestError as exc:
            logger.warning("Fangate transport failure on %s: %s", operation, exc.__class__.__name__)
            raise FangateTransportError(operation) from exc

        elapsed = round((time.monotonic() - start) * 1000, 1)

        if response.is_error:
            body = _parse_body(response)
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            logger.warning(
                "Fangate HTTP %d on %s (%.0fms)",
                response.status_code,
                operation,
                elapsed,
            )
            raise _map_status_error(operation, response.status_code, body, retry_after)

        body = _parse_body(response)
        if isinstance(body, dict):
            if body.get("success") is False:
                message = body.get("errors_message") or body.get("message")
                logger.warning("Fangate business failure on %s (%.0fms)", operation, elapsed)
                raise FangateError(operation, message=message, status_code=response.status_code)
            return body.get("data")
        if isinstance(body, list):
            return body
        logger.error("Fangate malformed response on %s (%.0fms)", operation, elapsed)
        raise FangateResponseError(operation, status_code=response.status_code)

    # ── Products ───────────────────────────────────────────────────────────

    async def list_products(
        self,
        page: int = 1,
        limit: int = 50,
        sort_by: str | None = None,
        folder_id: int | None = None,
        folder_state: str | None = None,
        filter_folder: str | None = None,
    ) -> FangateProductPage:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if sort_by:
            params["sort_by"] = sort_by
        if folder_id is not None:
            params["folder_id"] = folder_id
        if folder_state is not None:
            params["folder_state"] = folder_state
        if filter_folder is not None:
            params["filterFolder"] = filter_folder
        data = await self._request("GET", "/products", params=params)
        if not isinstance(data, dict):
            raise FangateResponseError("GET /products")
        return FangateProductPage.from_api(data)

    async def get_product(self, product_id: int) -> FangateProduct:
        data = await self._request("GET", f"/products/{product_id}")
        if not isinstance(data, dict):
            raise FangateResponseError(f"GET /products/{product_id}")
        return FangateProduct.from_api(data)

    async def update_product(
        self,
        product_id: int,
        *,
        title: str | None = None,
        private_description: str | None = None,
        public_description: str | None = None,
        is_adult_content: bool | None = None,
        is_verif_age: bool | None = None,
    ) -> FangateProduct:
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if private_description is not None:
            payload["private_description"] = private_description
        if public_description is not None:
            payload["public_description"] = public_description
        if is_adult_content is not None:
            payload["is_adult_content"] = is_adult_content
        if is_verif_age is not None:
            payload["is_verif_age"] = is_verif_age
        data = await self._request("PATCH", f"/products/{product_id}", json=payload)
        if not isinstance(data, dict):
            raise FangateResponseError(f"PATCH /products/{product_id}")
        return FangateProduct.from_api(data)

    async def update_product_price(self, product_id: int, price_minor: int) -> FangateProduct:
        payload = {"price": price_minor}
        data = await self._request("PATCH", f"/products/{product_id}/price", json=payload)
        if not isinstance(data, dict):
            raise FangateResponseError(f"PATCH /products/{product_id}/price")
        return FangateProduct.from_api(data)

    async def toggle_product_collection(self, product_id: int) -> FangateProduct:
        data = await self._request("POST", f"/products/{product_id}/collection")
        if not isinstance(data, dict):
            raise FangateResponseError(f"POST /products/{product_id}/collection")
        return FangateProduct.from_api(data)

    async def update_product_folder(self, product_id: int, folder_id: int | None) -> FangateProduct:
        payload: dict[str, Any] = {"folder_id": folder_id}
        data = await self._request("PATCH", f"/products/{product_id}/folder", json=payload)
        if not isinstance(data, dict):
            raise FangateResponseError(f"PATCH /products/{product_id}/folder")
        return FangateProduct.from_api(data)

    async def create_price_link(
        self,
        product_id: int,
        price: int | None = None,
        title: str | None = None,
        private_description: str | None = None,
        public_description: str | None = None,
    ) -> FangateProduct:
        payload: dict[str, Any] = {}
        if price is not None:
            payload["price"] = price
        if title is not None:
            payload["title"] = title
        if private_description is not None:
            payload["private_description"] = private_description
        if public_description is not None:
            payload["public_description"] = public_description
        data = await self._request("POST", f"/products/{product_id}/price-links", json=payload)
        if not isinstance(data, dict):
            raise FangateResponseError(f"POST /products/{product_id}/price-links")
        product_data = data.get("product", data)
        if isinstance(product_data, dict) and "resource" in product_data:
            product_data = product_data["resource"]
        if not isinstance(product_data, dict):
            raise FangateResponseError(f"POST /products/{product_id}/price-links")
        return FangateProduct.from_api(product_data)

    # ── Content folders ───────────────────────────────────────────────────

    async def list_content_folders(self) -> list[FangateContentFolder]:
        data = await self._request("GET", "/content-folders")
        if not isinstance(data, list):
            raise FangateResponseError("GET /content-folders")
        return [FangateContentFolder.from_api(f) for f in data if isinstance(f, dict) and "id" in f]

    async def create_content_folder(self, name: str) -> FangateContentFolder:
        data = await self._request("POST", "/content-folders", json={"name": name})
        if not isinstance(data, dict):
            raise FangateResponseError("POST /content-folders")
        return FangateContentFolder.from_api(data)

    async def update_content_folder(self, folder_id: str, name: str) -> FangateContentFolder:
        data = await self._request("PATCH", f"/content-folders/{folder_id}", json={"name": name})
        if not isinstance(data, dict):
            raise FangateResponseError(f"PATCH /content-folders/{folder_id}")
        return FangateContentFolder.from_api(data)

    async def delete_content_folder(self, folder_id: str) -> FangateFolderDeleteResult:
        data = await self._request("DELETE", f"/content-folders/{folder_id}")
        if not isinstance(data, dict):
            raise FangateResponseError(f"DELETE /content-folders/{folder_id}")
        return FangateFolderDeleteResult.from_api(data)

    # ── Dashboard summary ────────────────────────────────────────────────

    async def get_dashboard_summary(self) -> dict[str, Any]:
        data = await self._request("GET", "/dashboard/summary")
        if not isinstance(data, dict):
            raise FangateResponseError("GET /dashboard/summary")
        return data

    # ── Wallet / transactions ──────────────────────────────────────────────

    async def get_wallet(self, page: int = 1, limit: int = 50) -> FangateWallet:
        data = await self._request("GET", "/wallet", params={"page": page, "limit": limit})
        if not isinstance(data, dict):
            raise FangateResponseError("GET /wallet")
        return FangateWallet.from_api(data)

    # ── Webhooks ───────────────────────────────────────────────────────────

    async def create_webhook(
        self,
        url: str,
        events: list[str],
        include_set_price: bool = False,
    ) -> FangateWebhook:
        if not url.startswith("https://"):
            raise FangateValidationError("POST /webhooks", message="Webhook URL must be HTTPS")
        unknown = [e for e in events if e not in SUPPORTED_WEBHOOK_EVENTS]
        if not events or unknown:
            raise FangateValidationError(
                "POST /webhooks",
                message=f"Unsupported webhook events: {', '.join(unknown)}",
            )
        data = await self._request(
            "POST",
            "/webhooks",
            json={"url": url, "events": list(events), "include_set_price": include_set_price},
        )
        if not isinstance(data, dict):
            raise FangateResponseError("POST /webhooks")
        webhook = FangateWebhook.from_api(data)
        logger.info(
            "Registered Fangate webhook id=%s (events=%s)", webhook.id, ",".join(webhook.events)
        )
        return webhook

    async def list_webhooks(self) -> list[FangateWebhook]:
        data = await self._request("GET", "/webhooks")
        if not isinstance(data, list):
            raise FangateResponseError("GET /webhooks")
        return [FangateWebhook.from_api(w) for w in data if isinstance(w, dict) and "id" in w]

    async def update_webhook(
        self,
        webhook_id: int,
        *,
        url: str | None = None,
        events: list[str] | None = None,
        include_set_price: bool | None = None,
        is_active: bool | None = None,
    ) -> FangateWebhook:
        payload: dict[str, Any] = {}
        if url is not None:
            if not url.startswith("https://"):
                raise FangateValidationError(
                    "PATCH /webhooks/{id}", message="Webhook URL must be HTTPS"
                )
            payload["url"] = url
        if events is not None:
            unknown = [e for e in events if e not in SUPPORTED_WEBHOOK_EVENTS]
            if not events or unknown:
                raise FangateValidationError(
                    "PATCH /webhooks/{id}",
                    message=f"Unsupported webhook events: {', '.join(unknown)}",
                )
            payload["events"] = list(events)
        if include_set_price is not None:
            payload["include_set_price"] = include_set_price
        if is_active is not None:
            payload["is_active"] = is_active
        data = await self._request("PATCH", f"/webhooks/{webhook_id}", json=payload)
        if not isinstance(data, dict):
            raise FangateResponseError(f"PATCH /webhooks/{webhook_id}")
        return FangateWebhook.from_api(data)

    async def delete_webhook(self, webhook_id: int) -> None:
        await self._request("DELETE", f"/webhooks/{webhook_id}")

    async def delete_product(self, product_id: int) -> Any:
        return await self._request("DELETE", f"/products/{product_id}")

    async def create_product(
        self,
        *,
        product_id: int | None = None,
        title: str | None = None,
        price: int | None = None,
        media_bytes: bytes | None = None,
        media_filename: str | None = None,
        upload_session_id: str | None = None,
        extension: str | None = None,
        is_adult_content: bool | None = None,
        is_verif_age: bool | None = None,
        is_should_consent: bool | None = None,
        private_description: str | None = None,
        public_description: str | None = None,
    ) -> FangateProduct:
        """Create a new product via POST /products (multipart/form-data)."""
        form_data: dict[str, Any] = {}
        if product_id is not None:
            form_data["product_id"] = str(product_id)
        if title is not None:
            form_data["title"] = title
        if price is not None:
            form_data["price"] = str(price)
        if upload_session_id is not None:
            form_data["upload_session_id"] = upload_session_id
        if extension is not None:
            form_data["extension"] = extension
        if is_adult_content is not None:
            form_data["is_adult_content"] = str(is_adult_content).lower()
        if is_verif_age is not None:
            form_data["is_verif_age"] = str(is_verif_age).lower()
        if is_should_consent is not None:
            form_data["is_should_consent"] = str(is_should_consent).lower()
        if private_description is not None:
            form_data["private_description"] = private_description
        if public_description is not None:
            form_data["public_description"] = public_description

        files_spec: dict[str, Any] | None = None
        if media_bytes is not None:
            fname = media_filename or "upload"
            files_spec = {"media": (fname, media_bytes, "application/octet-stream")}

        data = await self._request_multipart("POST", "/products", data=form_data, files=files_spec)
        if not isinstance(data, dict):
            raise FangateResponseError("POST /products")
        return FangateProduct.from_api(data)

    async def upload_product_media(
        self,
        product_id: int,
        *,
        media_bytes: bytes,
        media_filename: str = "upload",
        upload_session_id: str | None = None,
    ) -> FangateProduct:
        """Upload media to an existing product via POST /products/{id}/media.

        .. deprecated::
            This uses the legacy multipart upload path. Prefer
            :meth:`attach_product_media` which uses the documented JSON
            ``{"media_ids": [...]}`` approach for n:m library linking.
        """
        form_data: dict[str, Any] = {}
        if upload_session_id is not None:
            form_data["upload_session_id"] = upload_session_id
        files_spec = {"media": (media_filename, media_bytes, "application/octet-stream")}
        data = await self._request_multipart(
            "POST", f"/products/{product_id}/media", data=form_data, files=files_spec
        )
        if not isinstance(data, dict):
            raise FangateResponseError(f"POST /products/{product_id}/media")
        return FangateProduct.from_api(data)

    async def attach_product_media(
        self,
        product_id: int,
        media_ids: list[int],
    ) -> FangateProduct:
        """Attach existing library media to a product via POST /products/{id}/media.

        Uses the documented JSON ``{"media_ids": [...]}`` approach for n:m
        product-media linking.  The media must already exist in the creator's
        media library (uploaded via upload sessions or POST /api/media).
        """
        payload: dict[str, Any] = {"media_ids": media_ids}
        data = await self._request(
            "POST", f"/products/{product_id}/media", json=payload
        )
        if not isinstance(data, dict):
            raise FangateResponseError(f"POST /products/{product_id}/media")
        return FangateProduct.from_api(data)

    async def delete_product_media(self, media_id: int) -> Any:
        """Delete product media via DELETE /products/media/{media_id}."""
        return await self._request("DELETE", f"/products/media/{media_id}")

    # ── Product collection ────────────────────────────────────────────────

    async def list_product_collection(
        self,
        page: int = 1,
        limit: int = 50,
    ) -> FangateProductPage:
        """List products in the collection via GET /products/collection."""
        params: dict[str, Any] = {"page": page, "limit": limit}
        data = await self._request("GET", "/products/collection", params=params)
        if not isinstance(data, dict):
            raise FangateResponseError("GET /products/collection")
        return FangateProductPage.from_api(data)

    # ── Wallet (vault) ─────────────────────────────────────────────────────

    async def get_wallet_vault(self, page: int = 1, limit: int = 50) -> FangateWallet:
        """Get wallet balance and transactions via GET /wallet."""
        data = await self._request("GET", "/wallet", params={"page": page, "limit": limit})
        if not isinstance(data, dict):
            raise FangateResponseError("GET /wallet")
        return FangateWallet.from_api(data)


def _parse_body(response: httpx.Response) -> Any:
    """Parse response JSON; None payload on empty/undecodable bodies."""
    try:
        return response.json()
    except ValueError:
        return None


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header value (seconds or HTTP date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
