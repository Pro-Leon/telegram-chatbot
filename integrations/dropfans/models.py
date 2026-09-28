"""Dropfans data models.

Dropfans uses opaque string IDs (CUIDs), not integers.
Drop prices are in USD dollars; earnings are in cents; balance is in dollars.
Signed media URLs expire (~12 hours).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------

@dataclass
class DropfansVaultItem:
    """A single vault media item from Dropfans."""

    id: str
    file_name: str | None = None
    file_type: str | None = None  # image/video/audio
    file_path: str | None = None
    thumbnail_path: str | None = None
    download_url: str | None = None
    file_size: int | None = None
    duration_seconds: int | None = None
    bunny_stream_id: str | None = None
    content_tags: list[str] = field(default_factory=list)
    folder_id: str | None = None
    folder_name: str | None = None
    moderation_status: str | None = None  # APPROVED/PENDING/REJECTED/FLAGGED
    created_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansVaultItem:
        return cls(
            id=str(data.get("id", "")),
            file_name=data.get("fileName"),
            file_type=data.get("fileType"),
            file_path=data.get("filePath"),
            thumbnail_path=data.get("thumbnailPath"),
            download_url=data.get("downloadUrl"),
            file_size=data.get("fileSize"),
            duration_seconds=data.get("durationSeconds"),
            bunny_stream_id=data.get("bunnyStreamId"),
            content_tags=list(data.get("contentTags") or data.get("tags") or []),
            folder_id=data.get("folderId"),
            folder_name=data.get("folderName"),
            moderation_status=data.get("moderationStatus"),
            created_at=data.get("createdAt"),
            raw=data,
        )


@dataclass
class DropfansVaultFolder:
    """A vault folder from Dropfans."""

    id: str
    name: str
    item_count: int = 0
    created_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansVaultFolder:
        return cls(
            id=str(data.get("id", "")),
            name=data.get("name", ""),
            item_count=int(data.get("itemCount", 0)),
            created_at=data.get("createdAt"),
            raw=data,
        )


@dataclass
class DropfansVaultListResult:
    """Paginated vault list result."""

    items: list[DropfansVaultItem] = field(default_factory=list)
    folders: list[DropfansVaultFolder] = field(default_factory=list)
    has_more: bool = False
    total: int = 0
    page: int = 1
    limit: int = 50
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansVaultListResult:
        items_data = data.get("items", []) if isinstance(data, dict) else []
        folders_data = data.get("folders", []) if isinstance(data, dict) else []
        return cls(
            items=[DropfansVaultItem.from_api(i) for i in items_data],
            folders=[DropfansVaultFolder.from_api(f) for f in folders_data],
            has_more=bool(data.get("hasMore", False)),
            total=int(data.get("total", 0)),
            page=int(data.get("page", 1)),
            limit=int(data.get("limit", 50)),
            raw=data,
        )


@dataclass
class DropfansVaultUploadResult:
    """Result of a vault upload (image/audio)."""

    success: bool = False
    item: DropfansVaultItem | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansVaultUploadResult:
        item_data = data.get("item")
        return cls(
            success=bool(data.get("success", False)),
            item=DropfansVaultItem.from_api(item_data) if item_data else None,
            raw=data,
        )


# ---------------------------------------------------------------------------
# Video upload
# ---------------------------------------------------------------------------

@dataclass
class DropfansVideoUploadStart:
    """Result of starting a video upload (step 1 of 3)."""

    video_id: str = ""
    tus_endpoint: str = ""
    library_id: str = ""
    signature: str = ""
    expires: int = 0
    completion_token: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansVideoUploadStart:
        return cls(
            video_id=data.get("videoId", ""),
            tus_endpoint=data.get("tusEndpoint", ""),
            library_id=data.get("libraryId", ""),
            signature=data.get("signature", ""),
            expires=int(data.get("expires", 0)),
            completion_token=data.get("completionToken", ""),
            raw=data,
        )


@dataclass
class DropfansVideoUploadComplete:
    """Result of completing a video upload (step 3 of 3)."""

    success: bool = False
    item: DropfansVaultItem | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansVideoUploadComplete:
        item_data = data.get("item")
        return cls(
            success=bool(data.get("success", False)),
            item=DropfansVaultItem.from_api(item_data) if item_data else None,
            raw=data,
        )


@dataclass
class DropfansVideoStatus:
    """Transcoding status for a single video."""

    video_id: str = ""
    is_ready: bool = False
    is_processing: bool = False
    is_failed: bool = False
    length: int | None = None

    @classmethod
    def from_api(cls, video_id: str, data: dict[str, Any]) -> DropfansVideoStatus:
        return cls(
            video_id=video_id,
            is_ready=bool(data.get("isReady", False)),
            is_processing=bool(data.get("isProcessing", False)),
            is_failed=bool(data.get("isFailed", False)),
            length=data.get("length"),
        )


# ---------------------------------------------------------------------------
# Drops / Products
# ---------------------------------------------------------------------------

@dataclass
class DropfansDropResult:
    """Result of creating a drop."""

    product_id: str
    buy_url: str
    media_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansDropResult:
        return cls(
            product_id=str(data.get("productId", "")),
            buy_url=data.get("buyUrl", ""),
            media_count=int(data.get("mediaCount", 0)),
            raw=data,
        )


@dataclass
class DropfansDropMediaItem:
    """Media item within a drop detail."""

    vault_item_id: str = ""
    order: int = 0
    file_type: str | None = None
    moderation_status: str | None = None
    has_preview: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansDropMediaItem:
        return cls(
            vault_item_id=data.get("vaultItemId", ""),
            order=int(data.get("order", 0)),
            file_type=data.get("fileType"),
            moderation_status=data.get("moderationStatus"),
            has_preview=bool(data.get("hasPreview", False)),
            raw=data,
        )


@dataclass
class DropfansDrop:
    """A Dropfans drop with full details."""

    product_id: str = ""
    name: str | None = None
    price: float = 0.0  # USD dollars
    currency: str = "USD"
    status: str | None = None  # PENDING/APPROVED/REJECTED/FLAGGED
    moderation_reason: str | None = None
    buy_url: str | None = None
    allow_download: bool = False
    media_count: int = 0
    media: list[DropfansDropMediaItem] = field(default_factory=list)
    sales_count: int = 0
    last_sale_at: str | None = None
    created_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansDrop:
        media_data = data.get("media", [])
        return cls(
            product_id=str(data.get("id", data.get("productId", ""))),
            name=data.get("name"),
            price=float(data.get("price", 0)),
            currency=data.get("currency", "USD"),
            status=data.get("status"),
            moderation_reason=data.get("moderationReason"),
            buy_url=data.get("buyUrl"),
            allow_download=bool(data.get("allowDownload", False)),
            media_count=int(data.get("mediaCount", 0)),
            media=[DropfansDropMediaItem.from_api(m) for m in media_data],
            sales_count=int(data.get("salesCount", 0)),
            last_sale_at=data.get("lastSaleAt"),
            created_at=data.get("createdAt"),
            raw=data,
        )


# ---------------------------------------------------------------------------
# Sale status
# ---------------------------------------------------------------------------

@dataclass
class DropfansSaleStatus:
    """Status of a single drop from check-status polling."""

    product_id: str
    paid: bool = False
    sale_amount_cents: int | None = None
    buyer_email: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, product_id: str, data: dict[str, Any]) -> DropfansSaleStatus:
        return cls(
            product_id=product_id,
            paid=bool(data.get("paid")),
            sale_amount_cents=data.get("saleAmountCents"),
            buyer_email=data.get("buyerEmail"),
            raw=data,
        )


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------

@dataclass
class DropfansPostMedia:
    """Media entry in a post."""

    id: str = ""
    vault_item_id: str = ""
    is_paid: bool = False
    order: int = 0
    type: str | None = None  # image/video
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansPostMedia:
        return cls(
            id=data.get("id", ""),
            vault_item_id=data.get("vaultItemId", ""),
            is_paid=bool(data.get("isPaid", False)),
            order=int(data.get("order", 0)),
            type=data.get("type"),
            raw=data,
        )


@dataclass
class DropfansPost:
    """A post on the For You feed."""

    id: str = ""
    kind: str | None = None  # TEXT/MEDIA/DROP/SUBSCRIPTION/COMMUNITY
    caption: str | None = None
    status: str | None = None  # PENDING/APPROVED/REJECTED/FLAGGED
    live: bool = False
    scheduled_at: str | None = None
    published_at: str | None = None
    created_at: str | None = None
    product_id: str | None = None
    likes: int = 0
    comments: int = 0
    media: list[DropfansPostMedia] = field(default_factory=list)
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansPost:
        media_data = data.get("media", [])
        return cls(
            id=data.get("id", ""),
            kind=data.get("kind"),
            caption=data.get("caption"),
            status=data.get("status"),
            live=bool(data.get("live", False)),
            scheduled_at=data.get("scheduledAt"),
            published_at=data.get("publishedAt"),
            created_at=data.get("createdAt"),
            product_id=data.get("productId"),
            likes=int(data.get("likes", 0)),
            comments=int(data.get("comments", 0)),
            media=[DropfansPostMedia.from_api(m) for m in media_data],
            url=data.get("url"),
            raw=data,
        )


@dataclass
class DropfansPostLimits:
    """Posting limits from the list-posts response."""

    posts_per_day: int = 5
    max_caption_chars: int = 2000
    max_media_per_post: int = 10
    max_schedule_days: int = 30
    min_paid_price: int = 5

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansPostLimits:
        return cls(
            posts_per_day=int(data.get("postsPerDay", 5)),
            max_caption_chars=int(data.get("maxCaptionChars", 2000)),
            max_media_per_post=int(data.get("maxMediaPerPost", 10)),
            max_schedule_days=int(data.get("maxScheduleDays", 30)),
            min_paid_price=int(data.get("minPaidPrice", 5)),
        )


@dataclass
class DropfansPostListResult:
    """Paginated post list result."""

    posts: list[DropfansPost] = field(default_factory=list)
    page: int = 1
    limit: int = 20
    total: int = 0
    has_more: bool = False
    limits: DropfansPostLimits = field(default_factory=DropfansPostLimits)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansPostListResult:
        posts_data = data.get("posts", [])
        pagination = data.get("pagination", {})
        limits_data = data.get("limits", {})
        return cls(
            posts=[DropfansPost.from_api(p) for p in posts_data],
            page=int(pagination.get("page", 1)),
            limit=int(pagination.get("limit", 20)),
            total=int(pagination.get("total", 0)),
            has_more=bool(pagination.get("hasMore", False)),
            limits=DropfansPostLimits.from_api(limits_data),
            raw=data,
        )


@dataclass
class DropfansPostCreateResult:
    """Result of creating a post."""

    id: str = ""
    status: str | None = None
    pending: bool = False
    scheduled_at: str | None = None
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansPostCreateResult:
        return cls(
            id=data.get("id", ""),
            status=data.get("status"),
            pending=bool(data.get("pending", False)),
            scheduled_at=data.get("scheduledAt"),
            url=data.get("url"),
            raw=data,
        )


# ---------------------------------------------------------------------------
# Earnings
# ---------------------------------------------------------------------------

@dataclass
class DropfansEarningsTransaction:
    """A single earnings transaction."""

    id: str = ""
    product_id: str | None = None
    product_name: str | None = None
    amount_cents: int = 0
    gross_amount_cents: int = 0
    buyer_email: str | None = None
    buyer_name: str | None = None
    paid_at: str | None = None
    type: str | None = None  # drop/tip/subscription
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansEarningsTransaction:
        return cls(
            id=str(data.get("id", "")),
            product_id=str(data["productId"]) if data.get("productId") else None,
            product_name=data.get("productName"),
            amount_cents=int(data.get("amountCents", 0)),
            gross_amount_cents=int(data.get("grossAmountCents", 0)),
            buyer_email=data.get("buyerEmail"),
            buyer_name=data.get("buyerName"),
            paid_at=data.get("paidAt"),
            type=data.get("type"),
            raw=data,
        )


@dataclass
class DropfansEarningsTypeTotal:
    """Per-type earnings totals."""

    gross_cents: int = 0
    net_cents: int = 0
    count: int = 0

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansEarningsTypeTotal:
        return cls(
            gross_cents=int(data.get("grossCents", 0)),
            net_cents=int(data.get("netCents", 0)),
            count=int(data.get("count", 0)),
        )


@dataclass
class DropfansEarningsStats:
    """Earnings statistics window."""

    total_earnings_cents: int = 0
    gross_earnings_cents: int = 0
    previous_period_earnings_cents: int = 0
    previous_period_gross_earnings_cents: int = 0
    transaction_count: int = 0
    avg_transaction_cents: int = 0
    unique_customers: int = 0
    type_totals: dict[str, DropfansEarningsTypeTotal] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansEarningsStats:
        type_totals_data = data.get("typeTotals", {})
        return cls(
            total_earnings_cents=int(data.get("totalEarningsCents", 0)),
            gross_earnings_cents=int(data.get("grossEarningsCents", 0)),
            previous_period_earnings_cents=int(data.get("previousPeriodEarningsCents", 0)),
            previous_period_gross_earnings_cents=int(data.get("previousPeriodGrossEarningsCents", 0)),
            transaction_count=int(data.get("transactionCount", 0)),
            avg_transaction_cents=int(data.get("avgTransactionCents", 0)),
            unique_customers=int(data.get("uniqueCustomers", 0)),
            type_totals={
                k: DropfansEarningsTypeTotal.from_api(v)
                for k, v in type_totals_data.items()
                if isinstance(v, dict)
            },
        )


@dataclass
class DropfansEarningsChart:
    """Earnings chart data."""

    labels: list[str] = field(default_factory=list)
    values: list[int] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    group_by: str = "day"  # day/week/month
    typed_values: dict[str, list[int]] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansEarningsChart:
        return cls(
            labels=list(data.get("labels", [])),
            values=list(data.get("values", [])),
            dates=list(data.get("dates", [])),
            group_by=data.get("groupBy", "day"),
            typed_values={
                k: list(v) for k, v in data.get("typedValues", {}).items()
                if isinstance(v, list)
            },
        )


@dataclass
class DropfansEarnings:
    """Earnings summary from Dropfans."""

    stats: DropfansEarningsStats = field(default_factory=DropfansEarningsStats)
    chart: DropfansEarningsChart = field(default_factory=DropfansEarningsChart)
    transactions: list[DropfansEarningsTransaction] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansEarnings:
        stats_data = data.get("stats", {})
        chart_data = data.get("chart", {})
        txns_data = data.get("transactions", [])
        return cls(
            stats=DropfansEarningsStats.from_api(stats_data),
            chart=DropfansEarningsChart.from_api(chart_data),
            transactions=[DropfansEarningsTransaction.from_api(t) for t in txns_data],
            raw=data,
        )


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------

@dataclass
class DropfansBalance:
    """Wallet balance from Dropfans. Values are in USD DOLLARS (not cents)."""

    currency: str = "USD"
    pending: float = 0.0
    available: float = 0.0
    processing: float = 0.0
    paid_out: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansBalance:
        return cls(
            currency=data.get("currency", "USD"),
            pending=float(data.get("pending", 0)),
            available=float(data.get("available", 0)),
            processing=float(data.get("processing", 0)),
            paid_out=float(data.get("paidOut", 0)),
            raw=data,
        )


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

@dataclass
class DropfansWebLinks:
    """Canonical web links."""

    profile: str | None = None
    tip: str | None = None
    tip_template: str | None = None
    subscribe: str | None = None
    buy_template: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansWebLinks:
        if not data:
            return cls()
        return cls(
            profile=data.get("profile"),
            tip=data.get("tip"),
            tip_template=data.get("tipTemplate"),
            subscribe=data.get("subscribe"),
            buy_template=data.get("buyTemplate"),
        )


@dataclass
class DropfansTelegramLinks:
    """Telegram Mini App deep links."""

    bot: str | None = None
    profile: str | None = None
    tip: str | None = None
    tip_template: str | None = None
    subscribe: str | None = None
    spin: str | None = None
    buy_template: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansTelegramLinks | None:
        if not data:
            return None
        return cls(
            bot=data.get("bot"),
            profile=data.get("profile"),
            tip=data.get("tip"),
            tip_template=data.get("tipTemplate"),
            subscribe=data.get("subscribe"),
            spin=data.get("spin"),
            buy_template=data.get("buyTemplate"),
        )


@dataclass
class DropfansLinks:
    """Canonical creator links from Dropfans."""

    username: str | None = None
    web: DropfansWebLinks = field(default_factory=DropfansWebLinks)
    telegram: DropfansTelegramLinks | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansLinks:
        web_data = data.get("web", {})
        telegram_data = data.get("telegram")
        return cls(
            username=data.get("username"),
            web=DropfansWebLinks.from_api(web_data) if isinstance(web_data, dict) else DropfansWebLinks(),
            telegram=DropfansTelegramLinks.from_api(telegram_data) if isinstance(telegram_data, dict) else None,
            raw=data,
        )


# ---------------------------------------------------------------------------
# Account / Me
# ---------------------------------------------------------------------------

@dataclass
class DropfansAccount:
    """Dropfans account identity."""

    creator_id: str = ""
    username: str | None = None
    display_name: str | None = None
    image: str | None = None
    email: str | None = None
    account_type: str | None = None  # CONSUMER/CREATOR/AGENCY
    key_name: str | None = None
    app_slug: str | None = None
    app_name: str | None = None
    tier: str | None = None  # personal/app/first_party
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansAccount:
        key_data = data.get("key", {})
        app_data = key_data.get("app", {}) if isinstance(key_data, dict) else {}
        return cls(
            creator_id=str(data.get("id", data.get("userId", ""))),
            username=data.get("username"),
            display_name=data.get("displayName") or data.get("name"),
            image=data.get("image"),
            email=data.get("email"),
            account_type=data.get("accountType"),
            key_name=key_data.get("name") if isinstance(key_data, dict) else None,
            app_slug=app_data.get("slug") if isinstance(app_data, dict) else None,
            app_name=app_data.get("name") if isinstance(app_data, dict) else None,
            tier=key_data.get("tier") if isinstance(key_data, dict) else None,
            raw=data,
        )


# ---------------------------------------------------------------------------
# Telegram notifications
# ---------------------------------------------------------------------------

@dataclass
class DropfansNotifications:
    """Telegram notification status."""

    telegram_handle: str | None = None
    personal_connected: bool = False
    group_connected: bool = False
    group_chat_id: str | None = None
    group_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DropfansNotifications:
        return cls(
            telegram_handle=data.get("telegramHandle"),
            personal_connected=bool(data.get("personalConnected", False)),
            group_connected=bool(data.get("groupConnected", False)),
            group_chat_id=data.get("groupChatId"),
            group_name=data.get("groupName"),
            raw=data,
        )
