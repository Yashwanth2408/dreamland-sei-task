"""
Account provisioning service.

Ensures every user has the required rows in the accounts table
(chart of accounts) before any ledger writes happen.
Called lazily — accounts are created on first use, not at signup.

Per-user accounts:
  USER_TOKEN_WALLET  — one per user
  USER_USD_WALLET    — one per user

System (global) accounts:
  TOKEN_ISSUANCE     — one global row (user_id = NULL)
  CONVERSION_POOL    — one global row
  FEE_PAYABLE        — one global row
  DREAMLAND_FEE_EXP  — one global row
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounts import Account, AccountCode, AccountType
from app.core.logging import logger


# Metadata for per-user accounts
USER_ACCOUNT_META = {
    AccountCode.USER_TOKEN_WALLET: (AccountType.ASSET,   "User's DREAM token balance"),
    AccountCode.USER_USD_WALLET:   (AccountType.ASSET,   "User's converted USD balance"),
}

# Metadata for system-level accounts
SYSTEM_ACCOUNT_META = {
    AccountCode.TOKEN_ISSUANCE:    (AccountType.LIABILITY, "Tokens issued by Dreamland"),
    AccountCode.CONVERSION_POOL:   (AccountType.ASSET,     "USD pool used for conversions"),
    AccountCode.FEE_PAYABLE:       (AccountType.LIABILITY, "Fees owed by Dreamland"),
    AccountCode.DREAMLAND_FEE_EXP: (AccountType.EXPENSE,   "Dreamland's fee expense"),
}


async def get_or_create_user_account(
    db: AsyncSession,
    user_id: uuid.UUID,
    code: AccountCode,
) -> Account:
    """
    Fetch a user's account by code, or create it if it doesn't exist yet.
    Uses flush() so the new row is visible within the current transaction
    without requiring a separate commit.
    """
    result = await db.execute(
        select(Account).where(
            Account.user_id == user_id,
            Account.code == code,
        )
    )
    account = result.scalar_one_or_none()

    if account is None:
        acct_type, description = USER_ACCOUNT_META[code]
        now = datetime.now(timezone.utc)
        account = Account(
            id=uuid.uuid4(),
            user_id=user_id,
            code=code,
            account_type=acct_type,
            name=f"{code.value}:{user_id}",
            description=description,
            created_at=now,
        )
        db.add(account)
        await db.flush()   # makes account.id available immediately
        logger.info("account.created", user_id=str(user_id), code=code.value)

    return account


async def get_or_create_system_account(
    db: AsyncSession,
    code: AccountCode,
) -> Account:
    """
    Fetch a global system account, or create it if it doesn't exist.
    System accounts have user_id = NULL.
    """
    result = await db.execute(
        select(Account).where(
            Account.user_id == None,  # noqa: E711
            Account.code == code,
        )
    )
    account = result.scalar_one_or_none()

    if account is None:
        acct_type, description = SYSTEM_ACCOUNT_META[code]
        now = datetime.now(timezone.utc)
        account = Account(
            id=uuid.uuid4(),
            user_id=None,
            code=code,
            account_type=acct_type,
            name=code.value,
            description=description,
            created_at=now,
        )
        db.add(account)
        await db.flush()
        logger.info("system_account.created", code=code.value)

    return account