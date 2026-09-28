"""Commerce boundary structures. V2 is a client; commerce confirms."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class CommerceActionResultCode(str, Enum):
    CONFIRMED = "confirmed"
    DENIED = "denied"
    INELIGIBLE = "ineligible"
    UNAVAILABLE = "unavailable"
    CONFLICT = "conflict"
    PROVIDER_ERROR = "provider_error"
    PERSISTENCE_FAILED = "persistence_failed"
    VERIFICATION_FAILED = "verification_failed"


class CommerceContextRef(BaseModel):
    """Cached commerce CONFIRMATION. Never authored by V2."""

    id: UUID
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    relationship_id: UUID
    commerce_request_id: str = Field(min_length=1)
    payload_hash: str = Field(min_length=1)
    confirmed_at: datetime
    expires_at: datetime | None = None

    model_config = {"frozen": True}


class CommerceActionRequest(BaseModel):
    commerce_request_id: str = Field(min_length=1)
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    action: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    owner: str = Field(min_length=1)

    model_config = {"frozen": True}


class CommerceActionResult(BaseModel):
    commerce_request_id: str = Field(min_length=1)
    code: CommerceActionResultCode
    confirmed_at: datetime | None = None

    model_config = {"frozen": True}
