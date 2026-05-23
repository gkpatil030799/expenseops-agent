from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
    date: date | None
    authorized_date: date | None
    pending: bool
    status: str
    agent_question: str | None
    splitwise_expense_id: str | None
    last_error: str | None
    classification_suggestion: Literal["likely_personal", "likely_shared", "unsure"] | None = None
    classification_reason: str | None = None
    can_undo_transaction: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MarkPersonalResponse(BaseModel):
    transaction: TransactionOut
    message: str


class FriendOut(BaseModel):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    display_name: str


class GroupOut(BaseModel):
    id: int
    name: str


class SplitwiseUserOut(BaseModel):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None


class SplitwiseOAuthAuthorizeResponse(BaseModel):
    authorize_url: str
    oauth_token: str
    oauth_token_secret: str


class SplitwiseOAuthAccessTokenResponse(BaseModel):
    oauth_token: str
    oauth_token_secret: str
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


class CustomShare(BaseModel):
    user_id: int
    owed_share: Decimal = Field(..., gt=Decimal("0"))


class CustomSplitRequest(BaseModel):
    shares: list[CustomShare]
    group_id: int | None = None
    description: str | None = None
    details: str | None = None
    currency_code: str | None = None
    confirm: bool = True
    post_pending: bool = False


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
