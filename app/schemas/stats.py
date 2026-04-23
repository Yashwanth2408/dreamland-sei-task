"""Pydantic v2 response model for stats endpoint."""
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel


class StatsResponse(BaseModel):
    user_id: UUID
    tokens_won_today: Decimal
    total_usd_balance: Decimal
    tokens_remaining_today: int