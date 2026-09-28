"""Typed representations of Fangate API resources.

Field sets are intentionally limited to what the official Fangate
documentation establishes. Unknown fields are preserved via `raw` snapshots
but never interpreted.
"""

import enum
from dataclasses import dataclass, field
from typing import Any

TRANSACTION_STATUS_MAP = {
    "payment.successful": "successful",
    "payment.failed": "failed",
    "payment.pending": "pending",
}


def normalize_transaction_status(value: str | None) -> str | None:
    """Normalize a raw Fangate status string to {successful, failed, pending}.

    Unknown/raw values pass through unchanged so no upstream status is ever
    misinterpreted or lost.
    """
    if not value:
        return value
    return TRANSACTION_STATUS_MAP.get(value, value)


class ReconcileEventOutcome(str, enum.Enum):
    """Deterministic outcome of reconciling one webhook-event record against
    upstream (per-record audit), distinct from webhook-registration
    reconciliation (see ReconcileOutcome)."""

    MATCHED = "matched"
    STATUS_UPDATED = "status_updated"
    MISSING_LOCALLY = "missing_locally"
    MISSING_UPSTREAM = "missing_upstream"
    API_ERROR = "api_error"
    ALREADY_PROCESSED = "already_processed"
    VALIDATION_ERROR = "validation_error"
    AUTH_ERROR = "auth_error"
    UNHANDLED = "unhandled"


@dataclass
class ReconcileEventResult:
    """Per-record reconciliation verdict. One per audited webhook event."""

    creator_id: int
    transaction_id: str | None
    state: ReconcileEventOutcome
    details: str = ""

    @property
    def requires_manual(self) -> bool:
        """States where no safe automatic mutation was (or could be) applied."""
        return self.state in {
            ReconcileEventOutcome.MISSING_UPSTREAM,
            ReconcileEventOutcome.API_ERROR,
            ReconcileEventOutcome.VALIDATION_ERROR,
            ReconcileEventOutcome.AUTH_ERROR,
            ReconcileEventOutcome.UNHANDLED,
        }


class ReconcileOutcome(str, enum.Enum):
    """Deterministic outcome of reconciling ONE creator's webhook registration
    against the live Fangate webhook catalog.

    Exactly seven states (Phase 5.1 contract):
    - NO_ACTION_REQUIRED: local + remote already match (or nothing to do)
    - ORPHAN_FOUND: an orphan is identified but identity is too weak to act
    - ORPHAN_REPLACED: a verified orphan was deleted and safely recreated
    - AMBIGUOUS: multiple remote webhooks match; zero mutations
    - REMOTE_WEBHOOK_NOT_FOUND: known local webhook vanished remotely;
      recreated and persisted when configuration is known
    - PERSISTENCE_FAILED: remote succeeded but local persistence failed
    - RECONCILIATION_FAILED: an unexpected failure aborted the reconciliation
    """

    NO_ACTION_REQUIRED = "no_action_required"
    ORPHAN_FOUND = "orphan_found"
    ORPHAN_REPLACED = "orphan_replaced"
    AMBIGUOUS = "ambiguous"
    REMOTE_WEBHOOK_NOT_FOUND = "remote_webhook_not_found"
    PERSISTENCE_FAILED = "persistence_failed"
    RECONCILIATION_FAILED = "reconciliation_failed"


@dataclass
class ReconcileResult:
    """Verdict of one webhook-registration reconciliation run."""

    creator_id: int
    state: ReconcileOutcome
    details: str = ""

    @property
    def requires_manual(self) -> bool:
        """Outcomes where the operator must intervene before convergence."""
        return self.state in {
            ReconcileOutcome.ORPHAN_FOUND,
            ReconcileOutcome.AMBIGUOUS,
            ReconcileOutcome.PERSISTENCE_FAILED,
            ReconcileOutcome.RECONCILIATION_FAILED,
        }


@dataclass
class FangateContentFolder:
    """Standalone content folder resource from GET/POST/PATCH /content-folders."""

    id: str
    name: str
    items_count: int = 0
    created_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "FangateContentFolder":
        return cls(
            id=str(data["id"]),
            name=data.get("name", ""),
            items_count=int(data.get("items_count") or 0),
            created_at=data.get("created_at"),
            raw=data,
        )


@dataclass
class FangateFolderDeleteResult:
    """Response from DELETE /content-folders/{folder_id}."""

    id: str
    deleted: bool
    items_unassigned: bool
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "FangateFolderDeleteResult":
        return cls(
            id=str(data["id"]),
            deleted=bool(data.get("deleted")),
            items_unassigned=bool(data.get("items_unassigned")),
            raw=data,
        )


@dataclass
class FangateFolder:
    id: str | None
    name: str | None


@dataclass
class FangateMedia:
    id: int
    type: str | None = None
    preview: str | None = None
    preview_blurred: str | None = None
    veriff_status: str | None = None
    removal_description: str | None = None


@dataclass
class FangateProduct:
    id: int
    product_type: str | None = None
    title: str | None = None
    preview: str | None = None
    preview_blurred: str | None = None
    price_minor: int | None = None
    in_collection: bool = False
    link: str | None = None
    link_clicks: int | None = None
    unlocks: int | None = None
    total_earnings: int | None = None
    folder_id: str | None = None
    folder: FangateFolder | None = None
    media: list[FangateMedia] = field(default_factory=list)
    is_adult_content: bool = False
    is_verif_age: bool = False
    is_epoch_enabled: bool = False
    is_should_consent: bool = False
    is_downloadable: bool = False
    is_accessible: bool = False
    private_description: str | None = None
    public_description: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "FangateProduct":
        folder_data = data.get("folder") or None
        folder = (
            FangateFolder(id=str(folder_data["id"]), name=folder_data["name"])
            if folder_data
            else None
        )
        media = [
            FangateMedia(
                id=m["id"],
                type=m.get("type"),
                preview=m.get("preview"),
                preview_blurred=m.get("preview_blurred"),
                veriff_status=m.get("veriff_status"),
                removal_description=m.get("removal_description"),
            )
            for m in data.get("media") or []
            if isinstance(m, dict) and "id" in m
        ]
        return cls(
            id=int(data["id"]),
            product_type=data.get("type"),
            title=data.get("title"),
            preview=data.get("preview"),
            preview_blurred=data.get("preview_blurred"),
            price_minor=data.get("price"),
            in_collection=bool(data.get("in_collection")),
            link=data.get("link"),
            link_clicks=data.get("link_clicks"),
            unlocks=data.get("unlocks"),
            total_earnings=data.get("total_earnings"),
            folder_id=str(data["folder_id"]) if data.get("folder_id") is not None else None,
            folder=folder,
            media=media,
            is_adult_content=bool(data.get("is_adult_content")),
            is_verif_age=bool(data.get("is_verif_age")),
            is_epoch_enabled=bool(data.get("is_epoch_enabled")),
            is_should_consent=bool(data.get("is_should_consent")),
            is_downloadable=bool(data.get("is_downloadable")),
            is_accessible=bool(data.get("is_accessible")),
            private_description=data.get("private_description"),
            public_description=data.get("public_description"),
            raw=data,
        )


@dataclass
class FangateProductPage:
    pages_total: int
    collection_link: str | None
    products: list[FangateProduct]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "FangateProductPage":
        return cls(
            pages_total=int(data.get("pages_total") or 1),
            collection_link=data.get("collection_link"),
            products=[
                FangateProduct.from_api(p)
                for p in data.get("data") or []
                if isinstance(p, dict) and "id" in p
            ],
        )


@dataclass
class FangateWalletTransaction:
    id: int
    amount_minor: int | None = None
    txn_type: str | None = None
    created_at: str | None = None
    title: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class FangateWallet:
    available_minor: int | None = None
    hold_minor: int | None = None
    pending_minor: int | None = None
    total_minor: int | None = None
    referral_revenue_minor: int | None = None
    cashout_available: bool = False
    transactions: list[FangateWalletTransaction] = field(default_factory=list)
    has_more: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "FangateWallet":
        tx_data = data.get("transactions") or {}
        tx_items = tx_data.get("data") or []
        transactions = [
            FangateWalletTransaction(
                id=int(t["id"]),
                amount_minor=t.get("amount"),
                txn_type=t.get("type"),
                created_at=t.get("created_at"),
                title=t.get("title"),
                raw=t,
            )
            for t in tx_items
            if isinstance(t, dict) and "id" in t
        ]
        return cls(
            available_minor=data.get("available"),
            hold_minor=data.get("hold"),
            pending_minor=data.get("pending"),
            total_minor=data.get("total"),
            referral_revenue_minor=data.get("referral_revenue"),
            cashout_available=bool(data.get("cashout_available")),
            transactions=transactions,
            raw=data,
        )


@dataclass
class FangateWebhookEventPayload:
    event: str
    timestamp: str | None = None
    transaction_id: str | None = None
    buyer_email: str | None = None
    seller_earning: str | float | None = None
    currency: str | None = None
    product_id: int | None = None
    media_ids: list[str] = field(default_factory=list)
    set_price: str | float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "FangateWebhookEventPayload":
        d = data.get("data") or {}
        return cls(
            event=data.get("event", ""),
            timestamp=data.get("timestamp"),
            transaction_id=d.get("transaction_id"),
            buyer_email=d.get("buyer_email"),
            seller_earning=d.get("seller_earning"),
            currency=d.get("currency"),
            product_id=d.get("product_id"),
            media_ids=list(d.get("media_ids") or []),
            set_price=d.get("set_price"),
            raw=data,
        )


@dataclass
class FangateWebhook:
    id: int
    url: str | None = None
    events: list[str] = field(default_factory=list)
    include_set_price: bool = False
    is_active: bool = True
    secret: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "FangateWebhook":
        return cls(
            id=int(data["id"]),
            url=data.get("url"),
            events=list(data.get("events") or []),
            include_set_price=bool(data.get("include_set_price")),
            is_active=data.get("is_active", True),
            secret=data.get("secret"),
            raw=data,
        )


# ── Dashboard summary ────────────────────────────────────────────────────


@dataclass
class DashboardWalletBalance:
    """Aggregated wallet balance from GET /api/dashboard/summary."""

    available: int = 0
    hold: int = 0
    pending: int = 0
    total: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "available": self.available,
            "hold": self.hold,
            "pending": self.pending,
            "total": self.total,
        }


@dataclass
class DashboardAccount:
    """Per-creator account entry within the dashboard summary response."""

    user_id: int = 0
    email: str = ""
    display_name: str = ""
    currency_id: int = 0
    currency_code: str | None = None
    wallet_balance: DashboardWalletBalance | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "currency_id": self.currency_id,
            "currency_code": self.currency_code,
            "wallet_balance": self.wallet_balance.to_dict() if self.wallet_balance else None,
        }


@dataclass
class DashboardTopPerformingItem:
    """Single top-performing product within the dashboard summary response."""

    id: str = ""
    title: str = ""
    thumbnail_url: str | None = None
    price: int = 0
    unlocks: int = 0
    clicks: int = 0
    revenue: int = 0
    conversion_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "thumbnail_url": self.thumbnail_url,
            "price": self.price,
            "unlocks": self.unlocks,
            "clicks": self.clicks,
            "revenue": self.revenue,
            "conversion_rate": self.conversion_rate,
        }


@dataclass
class DashboardSummaryData:
    """Full dashboard summary payload from GET /api/dashboard/summary."""

    wallet_balance: DashboardWalletBalance | None = None
    currency_unified: bool = False
    accounts: list[DashboardAccount] = field(default_factory=list)
    total_content_items: int = 0
    total_link_clicks: int = 0
    total_unlocks: int = 0
    total_product_revenue: int = 0
    overall_conversion_rate: float = 0.0
    top_performing: list[DashboardTopPerformingItem] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "DashboardSummaryData":
        wb_data = data.get("wallet_balance") or {}
        wallet_balance = (
            DashboardWalletBalance(
                available=int(wb_data.get("available") or 0),
                hold=int(wb_data.get("hold") or 0),
                pending=int(wb_data.get("pending") or 0),
                total=int(wb_data.get("total") or 0),
            )
            if wb_data
            else None
        )
        accounts = []
        for a in data.get("accounts") or []:
            if isinstance(a, dict) and "user_id" in a:
                ac_wb = a.get("wallet_balance") or {}
                accounts.append(
                    DashboardAccount(
                        user_id=int(a.get("user_id") or 0),
                        email=a.get("email", ""),
                        display_name=a.get("display_name", ""),
                        currency_id=int(a.get("currency_id") or 0),
                        currency_code=a.get("currency_code"),
                        wallet_balance=(
                            DashboardWalletBalance(
                                available=int(ac_wb.get("available") or 0),
                                hold=int(ac_wb.get("hold") or 0),
                                pending=int(ac_wb.get("pending") or 0),
                                total=int(ac_wb.get("total") or 0),
                            )
                            if ac_wb
                            else None
                        ),
                    )
                )
        top_performing = []
        for t in data.get("top_performing") or []:
            if isinstance(t, dict) and "id" in t:
                top_performing.append(
                    DashboardTopPerformingItem(
                        id=str(t.get("id", "")),
                        title=t.get("title", ""),
                        thumbnail_url=t.get("thumbnail_url"),
                        price=int(t.get("price") or 0),
                        unlocks=int(t.get("unlocks") or 0),
                        clicks=int(t.get("clicks") or 0),
                        revenue=int(t.get("revenue") or 0),
                        conversion_rate=float(t.get("conversion_rate") or 0.0),
                    )
                )
        return cls(
            wallet_balance=wallet_balance,
            currency_unified=bool(data.get("currency_unified")),
            accounts=accounts,
            total_content_items=int(data.get("total_content_items") or 0),
            total_link_clicks=int(data.get("total_link_clicks") or 0),
            total_unlocks=int(data.get("total_unlocks") or 0),
            total_product_revenue=int(data.get("total_product_revenue") or 0),
            overall_conversion_rate=float(data.get("overall_conversion_rate") or 0.0),
            top_performing=top_performing,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet_balance": self.wallet_balance.to_dict() if self.wallet_balance else None,
            "currency_unified": self.currency_unified,
            "accounts": [a.to_dict() for a in self.accounts],
            "total_content_items": self.total_content_items,
            "total_link_clicks": self.total_link_clicks,
            "total_unlocks": self.total_unlocks,
            "total_product_revenue": self.total_product_revenue,
            "overall_conversion_rate": self.overall_conversion_rate,
            "top_performing": [t.to_dict() for t in self.top_performing],
        }
