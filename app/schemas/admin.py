"""Admin/owner API schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel


class AdminOverviewResponse(BaseModel):
    total_users: int
    total_token_wins: int
    tokens_issued: Decimal
    tokens_converted: Decimal
    usd_paid_out: Decimal
    fees_paid: Decimal
    last_conversion_at: Optional[datetime]


class AdminUser(BaseModel):
    id: str
    username: str
    email: str
    tokens_won_lifetime: Decimal
    timezone: str
    region: str
    is_active: bool
    created_at: datetime


class AdminUsersResponse(BaseModel):
    total: int
    items: list[AdminUser]
