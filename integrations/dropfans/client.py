"""Dropfans async HTTP client.

Implements the Dropfans external API contract:
- Bearer token authentication
- Configurable base URL and timeout
- Structured error handling (maps HTTP status to typed exceptions)
- Rate-limit handling with Retry-After support
- Structured logging (never logs credentials)
- No retry loops for 401/403
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .errors import (
    DropfansAuthenticationError,
    DropfansAuthorizationError,
    DropfansError,
    DropfansNotFoundError,
    DropfansRateLimitError,
    DropfansResponseError,
    DropfansServerError,
    DropfansTimeoutError,
    DropfansTransportError,
    DropfansValidationError,
)
from .models import (
    DropfansAccount,
    DropfansBalance,
    DropfansDrop,
    DropfansDropResult,
    DropfansEarnings,
    DropfansLinks,
    DropfansNotifications,
    DropfansPost,
    DropfansPostCreateResult,
    DropfansPostListResult,
    DropfansSaleStatus,
    DropfansVaultItem,
    DropfansVaultListResult,
    DropfansVaultFolder,
    DropfansVaultUploadResult,
    DropfansVideoStatus,
    DropfansVideoUploadComplete,
    DropfansVideoUploadStart,
)

logger = logging.getLogger("dropfans.client")


def _map_status_error(
    operation: str,
    status_code: int,
    body: Any,
    retry_after: float | None = None,
) -> DropfansError:
    """Map HTTP status to typed exception."""
    msg = ""
    if isinstance(body, dict):
        msg = body.get("message") or body.get("error") or body.get("detail") or ""
        if not msg and "errors" in body:
            errors = body["errors"]
            if isinstance(errors, list) and errors:
                msg = str(errors[0])
            elif isinstance(errors, dict):
                msg = str(list(errors.values())[0])

    if status_code == 401:
        return DropfansAuthenticationError(operation, msg or "Invalid API key", status_code)
    if status_code == 403:
        return DropfansAuthorizationError(operation, msg or "Access denied", status_code)
    if status_code == 404:
        return DropfansNotFoundError(operation, msg or "Not found", status_code)
    if status_code == 429:
        return DropfansRateLimitError(operation, msg or "Rate limit exceeded", status_code, retry_after=retry_after)
    if status_code in (400, 422):
        return DropfansValidationError(operation, msg or "Validation error", status_code)
    if 500 <= status_code < 600:
        return DropfansServerError(operation, msg or f"Server error {status_code}", status_code)
    return DropfansError(operation, msg or f"HTTP {status_code}", status_code)


def _parse_body(response: httpx.Response) -> Any:
    """Parse JSON response body. Returns None on empty/undecodable bodies."""
    if not response.content:
        return None
    try:
        return response.json()
    except Exception:
        return None


def _parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After header (seconds or HTTP date)."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


class DropfansClient:
    """Async HTTP client for the Dropfans external API."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://www.dropfans.io",
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        self._base_url = base_url.rstrip("/")
        self._api_prefix = "/api/external"
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
        )
        self._api_key = api_key  # kept for close-only; never logged

    async def __aenter__(self) -> DropfansClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal request helpers
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Perform an HTTP request with structured error handling."""
        url = f"{self._api_prefix}{path}"
        t0 = time.monotonic()
        try:
            response = await self._client.request(method, url, params=params, json=json)
        except httpx.TimeoutException:
            raise DropfansTimeoutError(
                f"{method} {path}", f"Request timed out after {self._client.timeout.connect}s"
            ) from None
        except httpx.RequestError as exc:
            raise DropfansTransportError(
                f"{method} {path}", f"Network error: {exc}"
            ) from None

        duration_ms = int((time.monotonic() - t0) * 1000)
        status = response.status_code
        body = _parse_body(response)

        # P3-A: low-cost rate-limit observability. Headers may be absent;
        # never used for business decisions, never carries secrets.
        try:
            _rl_remaining = response.headers.get("X-RateLimit-Remaining")
            _rl_reset = response.headers.get("X-RateLimit-Reset")
        except Exception:
            _rl_remaining = None
            _rl_reset = None
        logger.debug(
            "dropfans.response",
            extra={
                "operation": f"{method} {path}",
                "status": status,
                "duration_ms": duration_ms,
                "rate_remaining": _rl_remaining,
                "rate_reset": _rl_reset,
            },
        )

        if status >= 400:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            error = _map_status_error(f"{method} {path}", status, body, retry_after)
            logger.warning(
                "dropfans.error",
                extra={
                    "operation": f"{method} {path}",
                    "status": status,
                    "duration_ms": duration_ms,
                    "error_type": type(error).__name__,
                },
            )
            raise error

        if body is None:
            return None

        # Dropfans may wrap responses in {"data": ...}
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    async def _request_multipart(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any:
        """Perform a multipart/form-data request."""
        url = f"{self._api_prefix}{path}"
        t0 = time.monotonic()
        try:
            response = await self._client.request(method, url, data=data, files=files)
        except httpx.TimeoutException:
            raise DropfansTimeoutError(
                f"{method} {path}", "Request timed out"
            ) from None
        except httpx.RequestError as exc:
            raise DropfansTransportError(
                f"{method} {path}", f"Network error: {exc}"
            ) from None

        duration_ms = int((time.monotonic() - t0) * 1000)
        status = response.status_code
        body = _parse_body(response)

        if status >= 400:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            error = _map_status_error(f"{method} {path}", status, body, retry_after)
            raise error

        return body

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def get_me(self) -> DropfansAccount:
        """Validate API key and return creator identity."""
        data = await self._request("GET", "/me")
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("get_me", "Unexpected response format")
        return DropfansAccount.from_api(data)

    async def get_timezone(self) -> str | None:
        data = await self._request("GET", "/timezone")
        if isinstance(data, dict):
            return data.get("timezone")
        return None

    async def set_timezone(self, timezone: str) -> str | None:
        data = await self._request("PUT", "/timezone", json={"timezone": timezone})
        if isinstance(data, dict):
            return data.get("timezone")
        return timezone

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    async def get_balance(self) -> DropfansBalance:
        data = await self._request("GET", "/balance")
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("get_balance", "Unexpected response format")
        return DropfansBalance.from_api(data)

    # ------------------------------------------------------------------
    # Vault
    # ------------------------------------------------------------------

    async def list_vault(
        self,
        *,
        page: int = 1,
        limit: int = 50,
        folder_id: str | None = None,
        include_pending: bool = False,
    ) -> DropfansVaultListResult:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if folder_id:
            params["folderId"] = folder_id
        if include_pending:
            params["includePending"] = "true"
        data = await self._request("GET", "/vault", params=params)
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("list_vault", "Unexpected response format")
        return DropfansVaultListResult.from_api(data)

    async def upload_vault_item(
        self,
        *,
        file_type: str,
        original_name: str,
        display_file: Any = None,
        thumbnail_file: Any = None,
        file: Any = None,
        folder_id: str | None = None,
        duration_seconds: int | None = None,
    ) -> DropfansVaultUploadResult:
        """Upload an image, audio, or small video via multipart."""
        form_data: dict[str, Any] = {
            "fileType": file_type,
            "originalName": original_name,
        }
        if folder_id:
            form_data["folderId"] = folder_id
        if duration_seconds is not None:
            form_data["durationSeconds"] = str(duration_seconds)

        files_dict: dict[str, Any] = {}
        if file_type == "image":
            if display_file:
                files_dict["displayFile"] = display_file
            if thumbnail_file:
                files_dict["thumbnailFile"] = thumbnail_file
        else:
            if file:
                files_dict["file"] = file

        data = await self._request_multipart("POST", "/vault", data=form_data, files=files_dict)
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("upload_vault_item", "Unexpected response")
        return DropfansVaultUploadResult.from_api(data)

    async def delete_vault_item(self, item_id: str) -> bool:
        try:
            await self._request("DELETE", f"/vault/{item_id}")
            return True
        except DropfansNotFoundError:
            return False

    async def move_vault_item(self, item_id: str, folder_id: str | None) -> bool:
        body: dict[str, Any] = {}
        if folder_id:
            body["folderId"] = folder_id
        data = await self._request("PATCH", f"/vault/{item_id}/folder", json=body)
        if isinstance(data, dict):
            return bool(data.get("success", True))
        return True

    async def update_vault_tags(self, item_id: str, tags: list[str]) -> bool:
        data = await self._request("PATCH", f"/vault/{item_id}/tags", json={"tags": tags})
        if isinstance(data, dict):
            return bool(data.get("success", True))
        return True

    # ------------------------------------------------------------------
    # Vault folders
    # ------------------------------------------------------------------

    async def list_vault_folders(self) -> list[DropfansVaultFolder]:
        data = await self._request("GET", "/vault/folders")
        items = data if isinstance(data, list) else (data.get("folders", []) if isinstance(data, dict) else [])
        return [DropfansVaultFolder.from_api(f) for f in items]

    async def create_vault_folder(self, name: str) -> DropfansVaultFolder:
        data = await self._request("POST", "/vault/folders", json={"name": name})
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("create_vault_folder", "Unexpected response")
        return DropfansVaultFolder.from_api(data)

    async def delete_vault_folder(self, folder_id: str) -> bool:
        try:
            await self._request("DELETE", f"/vault/folders/{folder_id}")
            return True
        except DropfansNotFoundError:
            return False

    # ------------------------------------------------------------------
    # Video upload (3-step TUS flow)
    # ------------------------------------------------------------------

    async def start_video_upload(
        self,
        *,
        original_name: str,
        file_size: int | None = None,
    ) -> DropfansVideoUploadStart:
        """Step 1: Start a video upload, get TUS credentials."""
        body: dict[str, Any] = {"originalName": original_name}
        if file_size is not None:
            body["fileSize"] = file_size
        data = await self._request("POST", "/vault/video-upload", json=body)
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("start_video_upload", "Unexpected response")
        return DropfansVideoUploadStart.from_api(data)

    async def complete_video_upload(
        self,
        *,
        video_id: str,
        original_name: str,
        completion_token: str,
        folder_id: str | None = None,
    ) -> DropfansVideoUploadComplete:
        """Step 3: Complete a video upload after TUS transfer."""
        body: dict[str, Any] = {
            "videoId": video_id,
            "originalName": original_name,
            "completionToken": completion_token,
        }
        if folder_id:
            body["folderId"] = folder_id
        data = await self._request("POST", "/vault/video-upload/complete", json=body)
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("complete_video_upload", "Unexpected response")
        return DropfansVideoUploadComplete.from_api(data)

    async def get_video_status(self, video_ids: list[str]) -> list[DropfansVideoStatus]:
        """Check transcoding status for video GUIDs (max 50)."""
        if not video_ids:
            return []
        chunked = [video_ids[i:i+50] for i in range(0, len(video_ids), 50)]
        results: list[DropfansVideoStatus] = []
        for chunk in chunked:
            data = await self._request("POST", "/vault/video-status", json={"videoIds": chunk})
            if isinstance(data, dict):
                statuses = data.get("statuses", {})
                for vid, status_data in statuses.items():
                    if isinstance(status_data, dict):
                        results.append(DropfansVideoStatus.from_api(vid, status_data))
        return results

    # ------------------------------------------------------------------
    # Drops (product creation)
    # ------------------------------------------------------------------

    async def create_drop(
        self,
        *,
        name: str | None = None,
        price: float,
        vault_item_ids: list[str],
        allow_download: bool = True,
        description: str | None = None,
    ) -> DropfansDropResult:
        """Create a new drop. Price in USD dollars."""
        payload: dict[str, Any] = {
            "price": price,
            "vaultItemIds": vault_item_ids,
            "allowDownload": allow_download,
        }
        if name:
            payload["name"] = name
        if description:
            payload["description"] = description
        data = await self._request("POST", "/drops", json=payload)
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("create_drop", "Unexpected response")
        return DropfansDropResult.from_api(data)

    async def get_drop(self, drop_id: str) -> DropfansDrop:
        data = await self._request("GET", f"/drops/{drop_id}")
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("get_drop", "Unexpected response")
        return DropfansDrop.from_api(data)

    async def attach_drop_previews(
        self,
        drop_id: str,
        previews: dict[str, Any],
    ) -> int:
        """Attach baked blur previews to a drop. Returns count of updated previews."""
        files_dict: dict[str, Any] = {}
        form_data: dict[str, Any] = {}
        for key, value in previews.items():
            if key.startswith("previewBlob_"):
                files_dict[key] = value
            elif key.startswith("blurMeta_"):
                form_data[key] = value

        data = await self._request_multipart(
            "POST", f"/drops/{drop_id}/previews",
            data=form_data, files=files_dict,
        )
        if isinstance(data, dict):
            return int(data.get("updated", 0))
        return 0

    async def check_drop_status(
        self,
        product_ids: list[str],
    ) -> dict[str, DropfansSaleStatus]:
        """Poll for sales. Returns dict keyed by product_id. Max 200 IDs per call."""
        if not product_ids:
            return {}
        results: dict[str, DropfansSaleStatus] = {}
        chunked = [product_ids[i:i+200] for i in range(0, len(product_ids), 200)]
        for chunk in chunked:
            data = await self._request("POST", "/drops/check-status", json={"productIds": chunk})
            if isinstance(data, dict):
                sales_map = data.get("sales", {})
                for pid, sale_data in sales_map.items():
                    if isinstance(sale_data, dict) and sale_data.get("paid"):
                        results[pid] = DropfansSaleStatus.from_api(pid, sale_data)
        return results

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    async def create_post(
        self,
        *,
        caption: str | None = None,
        kind: str = "TEXT",
        product_id: str | None = None,
        media: list[dict[str, Any]] | None = None,
        scheduled_at: str | None = None,
    ) -> DropfansPostCreateResult:
        """Publish a post to the For You feed."""
        payload: dict[str, Any] = {}
        if caption:
            payload["caption"] = caption
        if kind:
            payload["kind"] = kind
        if product_id:
            payload["productId"] = product_id
        if media:
            payload["media"] = media
        if scheduled_at:
            payload["scheduledAt"] = scheduled_at
        data = await self._request("POST", "/posts", json=payload)
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("create_post", "Unexpected response")
        return DropfansPostCreateResult.from_api(data)

    async def list_posts(
        self,
        *,
        page: int = 1,
        limit: int = 20,
        status: str | None = None,
    ) -> DropfansPostListResult:
        """List creator posts with pagination and optional status filter."""
        params: dict[str, Any] = {"page": page, "limit": limit}
        if status:
            params["status"] = status
        data = await self._request("GET", "/posts", params=params)
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("list_posts", "Unexpected response")
        return DropfansPostListResult.from_api(data)

    async def get_post(self, post_id: str) -> DropfansPost:
        """Get a single post by ID."""
        data = await self._request("GET", f"/posts/{post_id}")
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("get_post", "Unexpected response")
        return DropfansPost.from_api(data)

    async def delete_post(self, post_id: str) -> bool:
        """Delete a post or cancel a scheduled one."""
        data = await self._request("DELETE", f"/posts/{post_id}")
        if isinstance(data, dict):
            return bool(data.get("ok", True))
        return True

    # ------------------------------------------------------------------
    # Earnings
    # ------------------------------------------------------------------

    async def get_earnings(
        self,
        *,
        start_date: str,
        end_date: str,
        tz: str = "UTC",
    ) -> DropfansEarnings:
        """Get earnings stats, chart and recent transactions."""
        params: dict[str, Any] = {
            "startDate": start_date,
            "endDate": end_date,
            "tz": tz,
        }
        data = await self._request("GET", "/earnings", params=params)
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("get_earnings", "Unexpected response")
        return DropfansEarnings.from_api(data)

    # ------------------------------------------------------------------
    # Links
    # ------------------------------------------------------------------

    async def get_links(self) -> DropfansLinks:
        data = await self._request("GET", "/links")
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("get_links", "Unexpected response")
        return DropfansLinks.from_api(data)

    # ------------------------------------------------------------------
    # Telegram notifications
    # ------------------------------------------------------------------

    async def get_notifications(self) -> DropfansNotifications:
        """Read Telegram notification status."""
        data = await self._request("GET", "/notifications")
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("get_notifications", "Unexpected response")
        return DropfansNotifications.from_api(data)

    async def update_notifications(
        self,
        *,
        action: str,
        telegram_handle: str | None = None,
        type: str | None = None,
        group_chat_id: str | None = None,
    ) -> dict[str, Any]:
        """Update Telegram notification settings."""
        payload: dict[str, Any] = {"action": action}
        if telegram_handle:
            payload["telegramHandle"] = telegram_handle
        if type:
            payload["type"] = type
        if group_chat_id:
            payload["groupChatId"] = group_chat_id
        data = await self._request("PUT", "/notifications", json=payload)
        if not data or not isinstance(data, dict):
            raise DropfansResponseError("update_notifications", "Unexpected response")
        return data

    async def register_telegram_chat(self, telegram_chat_id: str) -> bool:
        """Register the creator's personal notification chat."""
        data = await self._request(
            "POST", "/register-telegram-chat",
            json={"telegramChatId": telegram_chat_id},
        )
        if isinstance(data, dict):
            return bool(data.get("success", True))
        return True
