"""
Token ledger service — core business logic.

FLOW for POST /tokens/win:
  1. Check idempotency key → replay if already processed
  2. Load user → 404 if not found, 403 if inactive
  3. Get/create user's token wallet account
  4. Compute today's tokens (in user's local timezone)
  5. Enforce daily cap (max 5 tokens/day)
  6. Write DEBIT + CREDIT ledger pair (same transaction_id)
  7. Persist idempotency key with response snapshot
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.models.accounts import Account, AccountCode
from app.models.ledger import TokenLedgerEntry, EntryType, IdempotencyKey
from app.models.users import User
from app.schemas.tokens import WinTokenRequest, WinTokenResponse
from app.services.account_service import (
    get_or_create_user_account,
    get_or_create_system_account,
)
from app.utils.time_utils import get_user_day_bounds


async def win_tokens(
    db: AsyncSession,
    payload: WinTokenRequest,
) -> WinTokenResponse:
    """
    Award tokens to a user with full double-entry accounting.
    Fully idempotent via persisted idempotency key.
    """

    # ── Step 1: Idempotency check ────────────────────────────────────────────
    existing_key = await db.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.user_id == payload.user_id,
            IdempotencyKey.key == payload.idempotency_key,
        )
    )
    ikey_row = existing_key.scalar_one_or_none()

    if ikey_row and ikey_row.completed_at is not None:
        # Already processed — return the exact same response (no re-processing)
        logger.info("idempotency.replay", key=payload.idempotency_key)
        import json
        cached = json.loads(ikey_row.response_body)
        return WinTokenResponse(**cached)

    # ── Step 2: Load and validate user ───────────────────────────────────────
    user_result = await db.execute(
        select(User).where(User.id == payload.user_id)
    )
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")

    # ── Step 3: Get/create accounts ──────────────────────────────────────────
    token_wallet = await get_or_create_user_account(
        db, payload.user_id, AccountCode.USER_TOKEN_WALLET
    )
    # Lock the user's token wallet row to serialize daily cap enforcement
    await db.execute(
        select(Account)
        .where(Account.id == token_wallet.id)
        .with_for_update()
    )
    token_issuance = await get_or_create_system_account(
        db, AccountCode.TOKEN_ISSUANCE
    )

    # ── Step 4: Compute tokens won today (in user's local timezone) ──────────
    day_start, day_end = get_user_day_bounds(user.timezone, payload.won_at)

    daily_sum_result = await db.execute(
        select(func.coalesce(func.sum(TokenLedgerEntry.amount), Decimal("0")))
        .where(
            TokenLedgerEntry.account_id == token_wallet.id,
            TokenLedgerEntry.entry_type == EntryType.DEBIT,
            TokenLedgerEntry.won_at >= day_start,
            TokenLedgerEntry.won_at <  day_end,
        )
    )
    tokens_today = daily_sum_result.scalar() or Decimal("0")

    # ── Step 5: Enforce daily cap ────────────────────────────────────────────
    remaining = Decimal(settings.MAX_TOKENS_PER_DAY) - tokens_today

    if remaining <= 0:
        raise HTTPException(
            status_code=422,
            detail=f"Daily token cap of {settings.MAX_TOKENS_PER_DAY} reached for today.",
        )
    if payload.amount > remaining:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot award {payload.amount} tokens. "
                f"Only {remaining} token(s) remaining today."
            ),
        )

    # ── Step 6: Write double-entry ledger pair ───────────────────────────────
    txn_id     = uuid.uuid4()
    won_at_utc = payload.won_at.astimezone(timezone.utc)
    now_utc = datetime.now(timezone.utc)

    # DEBIT: user's wallet grows (+)
    debit_entry = TokenLedgerEntry(
        id              = uuid.uuid4(),
        transaction_id  = txn_id,
        account_id      = token_wallet.id,
        entry_type      = EntryType.DEBIT,
        amount          = payload.amount,           # positive
        description     = f"Token win: {payload.amount} DREAM",
        idempotency_key = payload.idempotency_key,  # stored on debit side only
        won_at          = won_at_utc,
        created_at      = now_utc,
        is_converted    = False,
    )

    # CREDIT: system issuance grows (-)
    credit_entry = TokenLedgerEntry(
        id              = uuid.uuid4(),
        transaction_id  = txn_id,
        account_id      = token_issuance.id,
        entry_type      = EntryType.CREDIT,
        amount          = -payload.amount,          # negative (mirror of debit)
        description     = f"Token issuance: {payload.amount} DREAM to user {payload.user_id}",
        idempotency_key = None,
        won_at          = won_at_utc,
        created_at      = now_utc,
        is_converted    = False,
    )

    db.add(debit_entry)
    db.add(credit_entry)

    # ── Step 7: Persist idempotency key with response snapshot ───────────────
    new_tokens_today = tokens_today + payload.amount

    response = WinTokenResponse(
        transaction_id         = txn_id,
        user_id                = payload.user_id,
        tokens_awarded         = payload.amount,
        tokens_won_today       = new_tokens_today,
        tokens_remaining_today = Decimal(settings.MAX_TOKENS_PER_DAY) - new_tokens_today,
        won_at                 = won_at_utc,
        message                = "Tokens awarded successfully",
    )

    if ikey_row is None:
        ikey_row = IdempotencyKey(
            id             = uuid.uuid4(),
            key            = payload.idempotency_key,
            user_id        = payload.user_id,
            request_path   = "/tokens/win",
            request_params = payload.model_dump_json(),
            response_code  = "201",
            response_body  = response.model_dump_json(),
            created_at     = now_utc,
        )
        db.add(ikey_row)
    else:
        ikey_row.response_code = "201"
        ikey_row.response_body = response.model_dump_json()

    ikey_row.completed_at = now_utc

    await db.flush()

    logger.info(
        "tokens.won",
        user_id=str(payload.user_id),
        amount=str(payload.amount),
        txn_id=str(txn_id),
        tokens_today=str(new_tokens_today),
    )

    return response


async def get_token_history(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> dict:
    """Return today's token win history for a user (DEBIT entries only)."""
    now = datetime.now(timezone.utc)

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    day_start, day_end = get_user_day_bounds(user.timezone, now)

    token_wallet = await get_or_create_user_account(
        db, user_id, AccountCode.USER_TOKEN_WALLET
    )

    result = await db.execute(
        select(TokenLedgerEntry)
        .where(
            TokenLedgerEntry.account_id == token_wallet.id,
            TokenLedgerEntry.entry_type == EntryType.DEBIT,
            TokenLedgerEntry.won_at >= day_start,
            TokenLedgerEntry.won_at <  day_end,
        )
        .order_by(TokenLedgerEntry.won_at)
    )
    entries = result.scalars().all()
    total   = sum(e.amount for e in entries) if entries else Decimal("0")

    return {
        "user_id": user_id,
        "date":    day_start.date().isoformat(),
        "entries": [
            {
                "transaction_id": e.transaction_id,
                "amount":         e.amount,
                "won_at":         e.won_at,
                "is_converted":   e.is_converted,
                "created_at":     e.created_at,
            }
            for e in entries
        ],
        "total_tokens_today": total,
    }