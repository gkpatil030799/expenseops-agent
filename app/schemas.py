from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LinkTokenResponse(BaseModel):
    link_token: str
    expiration: datetime | None = None
    request_id: str | None = None
    hosted_link_url: str | None = None


class PublicTokenExchangeRequest(BaseModel):
    public_token: str
    institution_name: str | None = None


class PublicTokenExchangeResponse(BaseModel):
    item_id: str
    plaid_item_db_id: int


class TransactionOut(BaseModel):
    id: int
    plaid_transaction_id: str
    merchant_name: str | None
    name: str
    amount_cents: int
    amount: str = ""
    iso_currency_code: str
    institution_name: str | None = None
    category: str | None = None
    payment_channel: str | None = None
    date: date | None
    authorized_date: date | None
    pending: bool
    status: str
    agent_question: str | None
    review_notification_queued_at: datetime | None = None
    splitwise_expense_id: str | None
    splitwise_payload_json: str | None
    splitwise_amount_cents: int | None = None
    replaces_transaction_id: int | None = None
    replaced_by_transaction_id: int | None = None
    last_error: str | None
    classification_suggestion: Literal["likely_personal", "likely_shared", "unsure"] | None = None
    classification_reason: str | None = None
    classification_preference_id: int | None = None
    can_undo_transaction: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FinancialActivityEventOut(BaseModel):
    id: int
    event_type: str
    action: str
    outcome: Literal["succeeded", "failed", "ambiguous"]
    actor_user_id: int | None = None
    actor_display_name: str
    channel: str
    attempt: int | None = None
    transaction_id: int | None = None
    merchant_name: str | None = None
    amount_cents: int | None = None
    currency_code: str | None = None
    provider_object_id: str | None = None
    correlation_id: str | None = None
    created_at: datetime


class FinancialActivityPage(BaseModel):
    events: list[FinancialActivityEventOut]
    total: int
    limit: int
    offset: int
    has_more: bool


class MarkPersonalResponse(BaseModel):
    transaction: TransactionOut
    message: str


class FriendOut(BaseModel):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    display_name: str
    registration_status: str | None = None


class GroupOut(BaseModel):
    id: int
    name: str
    invite_link: str | None = None


class CreateGroupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    group_type: Literal["home", "trip", "couple", "other"] = "home"
    simplify_by_default: bool = False
    user_ids: list[int] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def strip_group_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("group name cannot be blank")
        return value

    @field_validator("user_ids")
    @classmethod
    def unique_group_user_ids(cls, value: list[int]) -> list[int]:
        if any(user_id <= 0 for user_id in value):
            raise ValueError("user IDs must be positive")
        return list(dict.fromkeys(value))


class GroupMemberRequest(BaseModel):
    user_id: int = Field(..., gt=0)


class InviteGroupMemberRequest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(default="", max_length=100)
    email: str = Field(..., min_length=3, max_length=320)

    @field_validator("first_name", "last_name", "email")
    @classmethod
    def strip_invite_fields(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def validate_invite_email(cls, value: str) -> str:
        if "@" not in value or "." not in value.rsplit("@", 1)[-1]:
            raise ValueError("enter a valid email address")
        return value.lower()


class SplitwiseUserOut(BaseModel):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None


class SplitwiseOAuthAuthorizeResponse(BaseModel):
    authorize_url: str
    oauth_token: str
    oauth_token_secret: str = ""


class SplitwiseOAuthAccessTokenResponse(BaseModel):
    oauth_token: str = ""
    oauth_token_secret: str = ""
    message: str


class EqualSplitRequest(BaseModel):
    friend_user_ids: list[int] = Field(default_factory=list)
    group_id: int | None = None
    description: str | None = None
    details: str | None = None
    currency_code: str | None = None
    confirm: bool = True
    post_pending: bool = False

    @field_validator("friend_user_ids")
    @classmethod
    def unique_friend_ids(cls, value: list[int]) -> list[int]:
        seen: set[int] = set()
        output: list[int] = []
        for user_id in value:
            if user_id not in seen:
                seen.add(user_id)
                output.append(user_id)
        return output


class LegacyCustomShare(BaseModel):
    user_id: int
    owed_share: Decimal = Field(..., gt=Decimal("0"))


class CustomParticipantSplit(BaseModel):
    user_id: int
    display_name: str | None = None
    amount: Decimal | None = Field(default=None, ge=Decimal("0"))
    percentage: Decimal | None = Field(default=None, ge=Decimal("0"))
    shares: Decimal | None = Field(default=None, ge=Decimal("0"))


class CustomSplitRequest(BaseModel):
    group_id: int | None = None
    payer_user_id: int | None = None
    payer_included: bool = True
    split_mode: Literal["equal", "exact_amounts", "percentages", "shares"] = "equal"
    participant_splits: list[CustomParticipantSplit] = Field(default_factory=list)
    shares: list[LegacyCustomShare] = Field(default_factory=list)
    description: str | None = None
    details: str | None = None
    metadata: dict[str, Any] | None = None
    currency_code: str | None = None
    confirm: bool = True
    post_pending: bool = False

    @field_validator("participant_splits")
    @classmethod
    def unique_participant_ids(
        cls, value: list[CustomParticipantSplit]
    ) -> list[CustomParticipantSplit]:
        seen: set[int] = set()
        for split in value:
            if split.user_id in seen:
                raise ValueError("participant user IDs must be unique")
            seen.add(split.user_id)
        return value


class SplitwisePostResponse(BaseModel):
    transaction: TransactionOut
    splitwise_expense_id: str | None
    splitwise_response: dict


class InterpretRequest(BaseModel):
    transaction_id: int
    text: str


class InterpretResponse(BaseModel):
    intent: Literal["personal", "shared", "unknown"]
    split_mode: Literal["equal", "custom", "unknown"] = "unknown"
    friend_matches: list[FriendOut] = Field(default_factory=list)
    explanation: str


class WebhookAck(BaseModel):
    ok: bool
    message: str


class AIMemoryOut(BaseModel):
    id: int
    original_message: str
    label: str
    rationale: str
    source: Literal["explicit_preference", "confirmed_action", "correction"]
    failure_reason: str
    final_action: str
    final_group_name: str | None
    final_participants: list[str]
    final_split_mode: str | None
    payer_included: bool
    custom_values: list[dict[str, Any]] | None
    correction_type: str
    merchant: str | None
    amount_cents: int | None
    currency: str | None
    usage_count: int
    last_used_at: datetime | None
    created_at: datetime


class StructuredMemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant: str = Field(min_length=1, max_length=255)
    preference: Literal["personal", "shared"]
    participant_names: list[str] = Field(default_factory=list, max_length=8)
    group_name: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("participant_names")
    @classmethod
    def validate_memory_participants(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 120 for value in normalized):
            raise ValueError("participant names must contain 1 to 120 characters")
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("participant names must be unique")
        return normalized


class StructuredMemoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preference: Literal["personal", "shared"]
    participant_names: list[str] = Field(default_factory=list, max_length=8)
    group_name: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("participant_names")
    @classmethod
    def validate_memory_participants(cls, values: list[str]) -> list[str]:
        return StructuredMemoryCreate.validate_memory_participants(values)


class StructuredMemorySettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_learning_enabled: bool


class StructuredMemoryFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["accepted", "edited", "rejected"]


class StructuredMemorySettingsOut(BaseModel):
    transaction_learning_enabled: bool


class StructuredMemoryMetricsOut(BaseModel):
    shown: int
    accepted: int
    edited: int
    rejected: int
    agreement_rate: float | None
    correction_rate: float | None
