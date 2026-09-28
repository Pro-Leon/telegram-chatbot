"""Commerce domain models (Phase 5.1A).

Deterministic CRM-side state for PPV offers and fan purchase attribution.

Security boundary: these types carry NO Fangate credentials. ``CommerceProposal``
is intentionally limited to an action plus a PPV reference — the AI layer can
never produce payment URLs, prices, or customer identifiers.
"""

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any

OFFER_STATES = frozenset({"pending", "clicked", "purchased", "declined", "expired", "revoked"})


class CommerceAction(str, enum.Enum):
    """Actions a proposal (AI layer) or a commerce decision (Phase 5.3A
    deterministic engine) may take. Values are stable machine-readable codes.

    Phase C adds CHAT, TIP_SUGGESTION, and OPERATOR_HANDOFF for
    relationship-aware autonomous operation.
    """

    NO_OFFER = "no_offer"
    RELATIONSHIP_BUILDING = "relationship_building"
    SOFT_OFFER = "soft_offer"
    OFFER_PPV = "offer_ppv"
    FOLLOW_UP = "follow_up"
    DONT_OFFER = "dont_offer"
    CHAT = "chat"
    TIP_SUGGESTION = "tip_suggestion"
    OPERATOR_HANDOFF = "operator_handoff"


class OfferState(str, enum.Enum):
    """Persisted lifecycle of a PPV offer (mirrors commerce_offers.state)."""

    PENDING = "pending"
    CLICKED = "clicked"
    PURCHASED = "purchased"
    DECLINED = "declined"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass
class CommerceProposal:
    """An AI-produced commerce proposal (or its JSON parse).

    Never carries payment URLs, prices, customer IDs, or credentials — the
    policy layer rehydrates those from trusted CRM/Fangate state.
    """

    action: CommerceAction | str
    ppv_id: int | None = None
    reason: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommerceProposal":
        raw = data.get("action")
        try:
            action = CommerceAction(raw)
        except ValueError:
            raise ValueError(f"Invalid commerce action: {raw!r}") from None
        return cls(action=action, ppv_id=data.get("ppv_id"), reason=data.get("reason") or "")


class CapabilityStatus(str, enum.Enum):
    """Status of a creator capability."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CreatorCapabilities:
    """Deterministic capability model for a creator.

    Tracks what a creator can actually do based on their configuration
    and provider status. The decision engine must not recommend actions
    that the creator cannot actually execute.
    """

    content_sales: CapabilityStatus = CapabilityStatus.UNKNOWN
    tips: CapabilityStatus = CapabilityStatus.UNKNOWN
    provider_health: CapabilityStatus = CapabilityStatus.UNKNOWN
    has_valid_product: bool = False
    has_valid_sales_url: bool = False
    has_dropfans_integration: bool = False
    dropfans_authenticated: bool = False

    def can_sell_content(self) -> bool:
        """Check if creator can actually sell content."""
        return (
            self.content_sales == CapabilityStatus.AVAILABLE
            and self.has_valid_product
            and self.has_valid_sales_url
            and self.has_dropfans_integration
            and self.dropfans_authenticated
            and self.is_provider_healthy()
        )

    def can_accept_tips(self) -> bool:
        """Check if creator can actually accept tips."""
        return (
            self.tips == CapabilityStatus.AVAILABLE
            and self.has_dropfans_integration
            and self.dropfans_authenticated
            and self.is_provider_healthy()
        )

    def is_provider_healthy(self) -> bool:
        """Check if provider is healthy enough for commerce."""
        return self.provider_health in (
            CapabilityStatus.AVAILABLE,
            CapabilityStatus.UNKNOWN,  # Unknown = assume healthy
        )


@dataclass
class PolicyDecision:
    """Verdict of the commerce policy layer (added in a later phase)."""

    allowed: bool
    denial_reason: str = ""


@dataclass
class PpvOffer:
    """A persisted PPV offer row (mirrors commerce_offers).

    P3.2: ``vault_item_ids`` + ``dropfans_product_id`` + ``media_count`` form
    the immutable offer-time content snapshot. Legacy rows may carry NULLs;
    new offers must populate the snapshot atomically at creation.
    """

    creator_id: int
    user_id: int
    product_id: int
    link: str
    state: str = OfferState.PENDING.value
    price_minor: int | None = None
    currency: str | None = None
    reason: str | None = None
    created_by: str | None = None
    id: int | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    clicked_at: datetime | None = None
    purchased_at: datetime | None = None
    transaction_id: str | None = None
    dropfans_product_id: str | None = None
    vault_item_ids: list[str] | None = None
    media_count: int | None = None
    drop_content_hash: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "PpvOffer":
        vault_ids: list[str] | None = None
        try:
            raw_ids = row["vault_item_ids"] if "vault_item_ids" in row.keys() else None
        except Exception:
            raw_ids = row.get("vault_item_ids") if isinstance(row, dict) else None
        if raw_ids is not None:
            vault_ids = [str(v) for v in list(raw_ids)]
        return cls(
            id=row["id"],
            creator_id=row["creator_id"],
            user_id=row["user_id"],
            product_id=row["product_id"],
            link=row["link"],
            price_minor=row["price_minor"],
            currency=row["currency"],
            state=row["state"],
            reason=row["reason"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            clicked_at=row["clicked_at"],
            purchased_at=row["purchased_at"],
            transaction_id=row["transaction_id"],
            dropfans_product_id=row.get("dropfans_product_id") if isinstance(row, dict) else getattr(row, "dropfans_product_id", None),
            vault_item_ids=vault_ids,
            media_count=row.get("media_count") if isinstance(row, dict) else getattr(row, "media_count", None),
            drop_content_hash=row.get("drop_content_hash") if isinstance(row, dict) else getattr(row, "drop_content_hash", None),
        )


@dataclass
class PurchaseRecord:
    """A completed purchase with fan attribution, for revenue reporting.

    Deliberately a reference (offer + transaction ids), not a duplicate of
    the full Fangate transaction object.
    """

    offer_id: int | None = None
    creator_id: int | None = None
    user_id: int | None = None
    transaction_id: str | None = None
    event_type: str | None = None
    product_id: int | None = None
    seller_earning: Any | None = None
    set_price: Any | None = None
    currency: str | None = None
    occurred_at: datetime | None = None


OFFER_DEFINITION_TYPES = frozenset({"SINGLE", "SMALL_BUNDLE", "CORE_BUNDLE", "PREMIUM"})

OFFER_DEFINITION_STATUSES = frozenset({"draft", "active", "retired"})


@dataclass
class OfferDefinition:
    """P3.3.5 — explicit commercial definition (mirrors commerce_offer_definitions).

    Describes exactly which Vault items constitute a sellable offer and which
    commercial terms apply. ``canonical_vault_item_ids`` is the authoritative
    composition (sorted unique Vault IDs); ``family_id`` is an optional
    descriptive reference that never determines composition. Versions are
    immutable; lifecycle is ``draft -> active -> retired``.
    """

    creator_id: int
    stable_key: str
    version: int
    offer_type: str
    canonical_vault_item_ids: list[str]
    price_minor: int
    currency: str
    allow_download: bool
    status: str = "draft"
    family_id: int | None = None
    config: dict[str, Any] | None = None
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> "OfferDefinition":
        get: Any
        if isinstance(row, dict):
            get = row.get
        else:
            def get(key: str, default: Any = None) -> Any:
                try:
                    return row[key]
                except Exception:
                    return getattr(row, key, default)
        raw_ids = get("canonical_vault_item_ids")
        vault_ids = [str(v) for v in list(raw_ids)] if raw_ids is not None else []
        return cls(
            id=get("id"),
            creator_id=get("creator_id"),
            stable_key=get("stable_key"),
            version=get("version"),
            offer_type=get("offer_type"),
            canonical_vault_item_ids=vault_ids,
            family_id=get("family_id"),
            price_minor=get("price_minor"),
            currency=get("currency"),
            allow_download=bool(get("allow_download")),
            status=get("status") or "draft",
            config=dict(get("config")) if get("config") is not None else None,
            created_at=get("created_at"),
            updated_at=get("updated_at"),
        )


@dataclass
class OfferDefinitionDropMapping:
    """P3.3.5 — mapping of a versioned definition to a concrete Dropfans Drop.

    Mapping only: records the relationship with the explicit definition
    version. It is not proof the live Drop matches the definition.
    """

    definition_id: int
    definition_version: int
    creator_id: int
    dropfans_product_id: str
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> "OfferDefinitionDropMapping":
        if isinstance(row, dict):
            return cls(
                definition_id=row["definition_id"],
                definition_version=row["definition_version"],
                creator_id=row["creator_id"],
                dropfans_product_id=row["dropfans_product_id"],
                created_at=row.get("created_at"),
            )
        return cls(
            definition_id=row["definition_id"],
            definition_version=row["definition_version"],
            creator_id=row["creator_id"],
            dropfans_product_id=row["dropfans_product_id"],
            created_at=getattr(row, "created_at", None),
        )
