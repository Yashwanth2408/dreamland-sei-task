"""
Double-entry ledger tables.

SQUARE BOOKS PATTERN:
  Every financial event → exactly 2 rows sharing the same transaction_id.
  SUM(amount) WHERE transaction_id = X must ALWAYS equal 0.

  DEBIT  entry → positive amount  (+)
  CREDIT entry → negative amount  (-)
  ──────────────────────────────────
  DEBIT + CREDIT = 0

WHY NUMERIC(18,8) NOT FLOAT:
  IEEE 754 float: 0.1 + 0.2 = 0.30000000000000004  ← WRONG
  PostgreSQL NUMERIC(18,8): 0.1 + 0.2 = 0.30000000 ← CORRECT
  Financial rounding errors compound across millions of rows.
"""
import uuid
from enum import Enum as PyEnum
from sqlalchemy import (
    Column, String, Enum, DateTime, Numeric, Boolean,
    ForeignKey, Index, CheckConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class EntryType(str, PyEnum):
    DEBIT  = "DEBIT"
    CREDIT = "CREDIT"


class TokenLedgerEntry(Base):
    """
    Immutable double-entry token ledger.

    WIN of N tokens creates exactly this pair:
      DEBIT   USER_TOKEN_WALLET   +N    (user gains tokens)
      CREDIT  TOKEN_ISSUANCE      -N    (system issued N tokens)
    """
    __tablename__ = "token_ledger"

    id = Column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    transaction_id = Column(
        UUID(as_uuid=True), nullable=False, index=True,
        comment="Groups the debit+credit pair. SUM(amount) per txn_id must = 0",
    )
    account_id = Column(
        UUID(as_uuid=True), ForeignKey("accounts.id"),
        nullable=False, index=True,
    )
    entry_type      = Column(Enum(EntryType), nullable=False)
    amount          = Column(
        Numeric(18, 8), nullable=False,
        comment="Positive for DEBIT, negative for CREDIT. NEVER FLOAT.",
    )
    description     = Column(String(255), nullable=True)
    idempotency_key = Column(
        String(128), nullable=True, index=True,
        comment="Client-supplied key to prevent duplicate win events",
    )
    won_at       = Column(
        DateTime(timezone=True), nullable=False,
        comment="When the user actually won the tokens (stored as UTC)",
    )
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_converted = Column(
        Boolean, nullable=False, default=False, index=True,
        comment="True once the hourly job has converted this entry to USD",
    )
    conversion_job_id = Column(
        UUID(as_uuid=True), ForeignKey("conversion_jobs.id"),
        nullable=True, index=True,
    )

    __table_args__ = (
        # One idempotency_key per account — partial (only when key is set)
        Index(
            "uq_token_ledger_idempotency",
            "account_id", "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        # DB-level sign enforcement — last line of defence
        CheckConstraint(
            "(entry_type = 'DEBIT' AND amount > 0) OR "
            "(entry_type = 'CREDIT' AND amount < 0)",
            name="chk_token_amount_sign",
        ),
    )


class UsdLedgerEntry(Base):
    """
    Immutable double-entry USD ledger.

    CONVERSION of N tokens → gross_usd = N × 0.15:
      DEBIT   CONVERSION_POOL   +gross_usd   (USD leaves pool)
      CREDIT  USER_USD_WALLET   -gross_usd   (user receives USD)

    FEE (Dreamland pays, user does NOT):
      DEBIT   DREAMLAND_FEE_EXP  +fee_usd
      CREDIT  FEE_PAYABLE        -fee_usd
    """
    __tablename__ = "usd_ledger"

    id = Column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    transaction_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    account_id     = Column(
        UUID(as_uuid=True), ForeignKey("accounts.id"),
        nullable=False, index=True,
    )
    entry_type  = Column(Enum(EntryType), nullable=False)
    amount      = Column(Numeric(18, 8), nullable=False)
    description = Column(String(255), nullable=True)
    source_token_transaction_id = Column(
        UUID(as_uuid=True), nullable=True, index=True,
        comment="Cross-ref: which token_ledger transaction triggered this",
    )
    conversion_job_id = Column(
        UUID(as_uuid=True), ForeignKey("conversion_jobs.id"),
        nullable=True, index=True,
    )
    converted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "(entry_type = 'DEBIT' AND amount > 0) OR "
            "(entry_type = 'CREDIT' AND amount < 0)",
            name="chk_usd_amount_sign",
        ),
    )


class IdempotencyKey(Base):
    """
    Persisted idempotency store (Stripe / Brandur pattern).

    Stores full request params + response JSON.
    On replay: return cached response immediately, skip all processing.
    On lock: request is in-flight — return 409 to caller.
    """
    __tablename__ = "idempotency_keys"

    id = Column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    key          = Column(String(128), nullable=False)
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    request_path = Column(String(200), nullable=False)
    request_params  = Column(String(2000), nullable=False)  # JSON snapshot
    response_code   = Column(String(10),   nullable=True)
    response_body   = Column(String(4000), nullable=True)   # JSON snapshot
    locked_at       = Column(DateTime(timezone=True), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at    = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("uq_idempotency_user_key", "user_id", "key", unique=True),
    )