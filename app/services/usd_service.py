"""USD ledger query service."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounts import AccountCode
from app.models.ledger import UsdLedgerEntry, EntryType
from app.models.users import User
from app.services.account_service import get_or_create_user_account
from app.utils.time_utils import get_user_day_bounds


async def get_usd_history(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> dict:
    """
    Returns USD conversion history UP TO (not including) today.
    'Till the previous day' = converted_at < start of user's current local day.

    We show debits to the user's USD wallet (stored as positive amounts)
    which represent the USD they received.
    """
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)
    day_start, _ = get_user_day_bounds(user.timezone, now)

    usd_wallet = await get_or_create_user_account(
        db, user_id, AccountCode.USER_USD_WALLET
    )

    # Debits to user's USD wallet — these represent money the user received
    # Stored as positive amounts
    result = await db.execute(
        select(UsdLedgerEntry)
        .where(
            UsdLedgerEntry.account_id == usd_wallet.id,
            UsdLedgerEntry.entry_type == EntryType.DEBIT,
            UsdLedgerEntry.converted_at < day_start,
        )
        .order_by(UsdLedgerEntry.converted_at)
    )
    entries = result.scalars().all()
    total   = sum(e.amount for e in entries) if entries else Decimal("0")

    return {
        "user_id": user_id,
        "entries": [
            {
                "transaction_id": e.transaction_id,
                "amount_usd":     e.amount,
                "converted_at":   e.converted_at,
                "source_tokens":  None,
            }
            for e in entries
        ],
        "total_usd_balance": total,
    }