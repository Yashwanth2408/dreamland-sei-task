"""Admin/owner aggregation queries."""
from decimal import Decimal
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.users import User
from app.models.accounts import Account, AccountCode
from app.models.ledger import TokenLedgerEntry, UsdLedgerEntry, EntryType


async def get_admin_overview(db: AsyncSession) -> dict:
    total_users = await db.scalar(select(func.count(User.id)))

    tokens_issued = await db.scalar(
        select(func.coalesce(func.sum(TokenLedgerEntry.amount), 0))
        .join(Account, TokenLedgerEntry.account_id == Account.id)
        .where(
            TokenLedgerEntry.entry_type == EntryType.DEBIT,
            Account.code == AccountCode.USER_TOKEN_WALLET,
        )
    )

    tokens_converted = await db.scalar(
        select(func.coalesce(func.sum(-TokenLedgerEntry.amount), 0))
        .join(Account, TokenLedgerEntry.account_id == Account.id)
        .where(
            TokenLedgerEntry.entry_type == EntryType.CREDIT,
            TokenLedgerEntry.is_converted == True,  # noqa: E712
            Account.code == AccountCode.USER_TOKEN_WALLET,
        )
    )

    total_token_wins = await db.scalar(
        select(func.count(TokenLedgerEntry.id))
        .join(Account, TokenLedgerEntry.account_id == Account.id)
        .where(
            TokenLedgerEntry.entry_type == EntryType.DEBIT,
            Account.code == AccountCode.USER_TOKEN_WALLET,
        )
    )

    usd_paid_out = await db.scalar(
        select(func.coalesce(func.sum(UsdLedgerEntry.amount), 0))
        .join(Account, UsdLedgerEntry.account_id == Account.id)
        .where(
            UsdLedgerEntry.entry_type == EntryType.DEBIT,
            Account.code == AccountCode.USER_USD_WALLET,
        )
    )

    fees_paid = await db.scalar(
        select(func.coalesce(func.sum(UsdLedgerEntry.amount), 0))
        .join(Account, UsdLedgerEntry.account_id == Account.id)
        .where(
            UsdLedgerEntry.entry_type == EntryType.DEBIT,
            Account.code == AccountCode.DREAMLAND_FEE_EXP,
        )
    )

    last_conversion_at = await db.scalar(
        select(func.max(UsdLedgerEntry.converted_at))
        .join(Account, UsdLedgerEntry.account_id == Account.id)
        .where(
            UsdLedgerEntry.entry_type == EntryType.DEBIT,
            Account.code == AccountCode.USER_USD_WALLET,
        )
    )

    return {
        "total_users": int(total_users or 0),
        "total_token_wins": int(total_token_wins or 0),
        "tokens_issued": Decimal(tokens_issued or 0),
        "tokens_converted": Decimal(tokens_converted or 0),
        "usd_paid_out": Decimal(usd_paid_out or 0),
        "fees_paid": Decimal(fees_paid or 0),
        "last_conversion_at": last_conversion_at,
    }


async def get_admin_users(db: AsyncSession, query: str | None, limit: int) -> dict:
    safe_limit = max(1, min(limit, 100))
    token_sum_subq = (
        select(
            Account.user_id.label("user_id"),
            func.coalesce(func.sum(TokenLedgerEntry.amount), 0).label("tokens_won"),
        )
        .join(TokenLedgerEntry, TokenLedgerEntry.account_id == Account.id)
        .where(
            TokenLedgerEntry.entry_type == EntryType.DEBIT,
            Account.code == AccountCode.USER_TOKEN_WALLET,
        )
        .group_by(Account.user_id)
        .subquery()
    )

    base_query = (
        select(User, token_sum_subq.c.tokens_won)
        .outerjoin(token_sum_subq, token_sum_subq.c.user_id == User.id)
    )

    if query:
        like = f"%{query}%"
        base_query = base_query.where(
            or_(
                User.username.ilike(like),
                User.email.ilike(like),
                User.external_id.ilike(like),
            )
        )

    total = await db.scalar(
        select(func.count(User.id)).select_from(base_query.subquery())
    )

    result = await db.execute(
        base_query.order_by(User.created_at.desc()).limit(safe_limit)
    )
    rows = result.all()

    return {
        "total": int(total or 0),
        "items": [
            {
                "id": str(user.id),
                "username": user.username,
                "email": user.email,
                "tokens_won_lifetime": Decimal(tokens_won or 0),
                "timezone": user.timezone,
                "region": user.region,
                "is_active": user.is_active,
                "created_at": user.created_at,
            }
            for user, tokens_won in rows
        ],
    }
