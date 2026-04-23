from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID
from pydantic import BaseModel
from pydantic.config import ConfigDict


class UsdHistoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)   # ← replaces class Config

    transaction_id: UUID
    amount_usd: Decimal
    source_tokens: Optional[Decimal] = None
    converted_at: datetime
    hour_bucket: Optional[str] = None


class UsdHistoryResponse(BaseModel):
    user_id: UUID
    entries: list[UsdHistoryEntry]
    total_usd_balance: Decimal