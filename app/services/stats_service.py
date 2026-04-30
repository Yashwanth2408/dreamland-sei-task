"""Stats aggregation service — tokens today + full USD balance."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.core.config import settings
from app.models.accounts import AccountCode
from app.models.ledger import TokenLedgerEntry, UsdLedgerEntry, EntryType
from app.models.users import User
from app.services.account_service import get_or_create_user_account
from app.utils.time_utils import get_user_day_bounds


async def get_stats(db: AsyncSession, user_id: uuid.UUID) -> dict:
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)
    day_start, day_end = get_user_day_bounds(user.timezone, now)

    # Tokens won today
    token_wallet = await get_or_create_user_account(db, user_id, AccountCode.USER_TOKEN_WALLET)
    token_result = await db.execute(
        select(func.coalesce(func.sum(TokenLedgerEntry.amount), Decimal("0")))
        .where(
            TokenLedgerEntry.account_id == token_wallet.id,
            TokenLedgerEntry.entry_type == EntryType.DEBIT,
            TokenLedgerEntry.won_at >= day_start,
            TokenLedgerEntry.won_at <  day_end,
        )
    )
    tokens_today = token_result.scalar() or Decimal("0")

    # Total USD balance all-time
    usd_wallet = await get_or_create_user_account(db, user_id, AccountCode.USER_USD_WALLET)
    usd_result = await db.execute(
        select(func.coalesce(func.sum(UsdLedgerEntry.amount), Decimal("0")))
        .where(
            UsdLedgerEntry.account_id == usd_wallet.id,
        )
    )
    usd_balance = usd_result.scalar() or Decimal("0")

    return {
        "user_id":                user_id,
        "tokens_won_today":       tokens_today,
        "tokens_remaining_today": max(Decimal("0"), Decimal(settings.MAX_TOKENS_PER_DAY) - tokens_today),
        "total_usd_balance":      usd_balance,
        "token_rate_usd":         settings.DREAM_TOKEN_RATE_USD,
    }