"""
Chart of Accounts.

Every ledger entry points to exactly one account.
System accounts (user_id=NULL) are shared global nodes.
User accounts (user_id=<uuid>) are per-user wallet nodes.

AccountCode reference:
  USER_TOKEN_WALLET  — user's DREAM token balance         (ASSET)
  TOKEN_ISSUANCE     — tokens Dreamland has issued        (LIABILITY)
  USER_USD_WALLET    — user's converted USD balance       (ASSET)
  CONVERSION_POOL    — USD pool used for conversions      (ASSET)
  FEE_PAYABLE        — fees Dreamland owes externally     (LIABILITY)
  DREAMLAND_FEE_EXP  — fee expense borne by Dreamland     (EXPENSE)
"""
import uuid
from enum import Enum as PyEnum
from sqlalchemy import Column, String, Enum, DateTime, func, text
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class AccountType(str, PyEnum):
    ASSET     = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY    = "EQUITY"
    REVENUE   = "REVENUE"
    EXPENSE   = "EXPENSE"


class AccountCode(str, PyEnum):
    USER_TOKEN_WALLET  = "USER_TOKEN_WALLET"
    TOKEN_ISSUANCE     = "TOKEN_ISSUANCE"
    USER_USD_WALLET    = "USER_USD_WALLET"
    CONVERSION_POOL    = "CONVERSION_POOL"
    FEE_PAYABLE        = "FEE_PAYABLE"
    DREAMLAND_FEE_EXP  = "DREAMLAND_FEE_EXP"


class Account(Base):
    __tablename__ = "accounts"

    id = Column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id      = Column(UUID(as_uuid=True), nullable=True, index=True)
    code         = Column(Enum(AccountCode), nullable=False, index=True)
    account_type = Column(Enum(AccountType), nullable=False)
    name         = Column(String(120), nullable=False)
    description  = Column(String(255), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)