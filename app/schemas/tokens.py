"""Pydantic v2 request/response models for token endpoints."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator


class WinTokenRequest(BaseModel):
    user_id: UUID = Field(..., description="UUID of the user")
    amount: Decimal = Field(
        ..., gt=0, le=5,
        description="Tokens won — must be a whole number between 1 and 5"
    )
    won_at: datetime = Field(
        ...,
        description="ISO-8601 timestamp WITH timezone when win occurred"
    )
    idempotency_key: str = Field(
        ..., min_length=8, max_length=128,
        description="Client-generated UUID — prevents duplicate submissions"
    )

    @field_validator("amount")
    @classmethod
    def amount_must_be_whole_number(cls, v: Decimal) -> Decimal:
        """Tokens are whole numbers only — no fractional tokens allowed."""
        if v != int(v):
            raise ValueError("Token amount must be a whole number (e.g. 1, 2, 3)")
        return v

    @field_validator("won_at")
    @classmethod
    def won_at_must_be_timezone_aware(cls, v: datetime) -> datetime:
        """Reject naive datetimes — we cannot know what timezone they represent."""
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError(
                "won_at must be timezone-aware (e.g. 2024-11-01T14:30:00+00:00). "
                "Naive datetimes are rejected — we cannot infer timezone."
            )
        return v


class WinTokenResponse(BaseModel):
    transaction_id: UUID
    user_id: UUID
    tokens_awarded: Decimal
    tokens_won_today: Decimal
    tokens_remaining_today: Decimal
    won_at: datetime
    message: str


class TokenHistoryEntry(BaseModel):
    transaction_id: UUID
    amount: Decimal
    won_at: datetime
    is_converted: bool
    created_at: datetime


class TokenHistoryResponse(BaseModel):
    user_id: UUID
    date: str                        # YYYY-MM-DD in user's local timezone
    entries: list[TokenHistoryEntry]
    total_tokens_today: Decimal