"""Pydantic request/response models for the dashboard API."""

from pydantic import BaseModel, Field, field_validator, model_validator


class SendMessageRequest(BaseModel):
    user_id: int
    content: str


class BulkAssignRequest(BaseModel):
    user_ids: list[int] = []
    segment_id: int | None = None
    assigned_operator_id: int | None = None


class BulkTagRequest(BaseModel):
    user_ids: list[int] = []
    segment_id: int | None = None
    tag_id: int


class BulkAttentionRequest(BaseModel):
    user_ids: list[int] = []
    segment_id: int | None = None
    status: str


class AttentionUpdateRequest(BaseModel):
    status: str | None = None


class AssignmentUpdateRequest(BaseModel):
    assigned_operator_id: int | None = None


class NoteCreateRequest(BaseModel):
    content: str


class NoteUpdateRequest(BaseModel):
    content: str


class TagCreateRequest(BaseModel):
    name: str
    description: str | None = None


class CreatorCreateRequest(BaseModel):
    name: str
    display_name: str | None = None


class IntegrationRequest(BaseModel):
    api_key: str
    api_key_name: str | None = None


class WebhookRegisterRequest(BaseModel):
    url: str
    events: list[str]
    include_set_price: bool = False


class WebhookReconcileRequest(BaseModel):
    events: list[dict] = []


class ProductUpdateRequest(BaseModel):
    title: str | None = None
    private_description: str | None = None
    public_description: str | None = None
    is_adult_content: bool | None = None
    is_verif_age: bool | None = None
    is_should_consent: bool | None = None
    is_downloadable: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self):
        if all(
            getattr(self, f) is None
            for f in [
                "title",
                "private_description",
                "public_description",
                "is_adult_content",
                "is_verif_age",
                "is_should_consent",
                "is_downloadable",
            ]
        ):
            raise ValueError("At least one field must be provided")
        return self


FANGATE_MIN_PRICE_MINOR = 500


class ProductPriceUpdateRequest(BaseModel):
    price_minor: int = Field(..., ge=FANGATE_MIN_PRICE_MINOR)

    @field_validator("price_minor", mode="after")
    @classmethod
    def _must_be_integer(cls, v: int) -> int:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("price_minor must be an integer")  # noqa: TRY004 — pydantic wraps into ValidationError
        return v


class ProductFolderRequest(BaseModel):
    folder_id: int | None


class PriceLinkRequest(BaseModel):
    price: int | None = Field(default=None, ge=FANGATE_MIN_PRICE_MINOR)
    title: str | None = None
    private_description: str | None = None
    public_description: str | None = None


class ContentFolderCreateRequest(BaseModel):
    name: str


class ContentFolderUpdateRequest(BaseModel):
    name: str


class ProductOfferRequest(BaseModel):
    user_id: int
    price: int | None = Field(default=None, ge=FANGATE_MIN_PRICE_MINOR)


class ProductOfferUpdateRequest(BaseModel):
    price: int | None = Field(default=None, ge=FANGATE_MIN_PRICE_MINOR)


class ProductMediaAttachRequest(BaseModel):
    media_ids: list[int]


class WebhookTestRequest(BaseModel):
    webhook_id: int


# ── Dropfans-specific request schemas ───────────────────────────────────────


class DropfansPostCreateRequest(BaseModel):
    caption: str | None = None
    kind: str = "TEXT"
    product_id: str | None = None
    media: list[dict] | None = None
    scheduled_at: str | None = None


class DropfansVaultMoveRequest(BaseModel):
    folder_id: str | None = None


class DropfansVaultTagsRequest(BaseModel):
    tags: list[str]


class DropfansVaultFolderCreateRequest(BaseModel):
    name: str


class DropfansVaultSyncRequest(BaseModel):
    """Operator-triggered bounded Vault index sync (P3.1 F-02)."""

    max_pages: int = 20


class DropfansSelectionConfigRequest(BaseModel):
    """Operator-configured deterministic selection allowlists (P3.1 F-02)."""

    allowed_folders: list[str] = []
    allowed_tags: list[str] = []
    hard_mode: bool = False


class DropfansVideoUploadStartRequest(BaseModel):
    original_name: str
    file_size: int | None = None


class DropfansVideoUploadCompleteRequest(BaseModel):
    video_id: str
    original_name: str
    completion_token: str
    folder_id: str | None = None


class DropfansDropCreateRequest(BaseModel):
    name: str | None = None
    price: float
    vault_item_ids: list[str]
    allow_download: bool = True
    description: str | None = None


class DropfansDropPreviewsRequest(BaseModel):
    previews: dict


class DropfansEarningsQuery(BaseModel):
    start_date: str
    end_date: str
    tz: str = "UTC"


class DropfansTelegramUpdateRequest(BaseModel):
    action: str
    telegram_handle: str | None = None
    type: str | None = None
    group_chat_id: str | None = None


class DropfansTelegramRegisterRequest(BaseModel):
    telegram_chat_id: str
