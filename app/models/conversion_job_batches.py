"""Per-user batch tracking for conversion jobs."""
import uuid
from enum import Enum as PyEnum
from sqlalchemy import Column, String, Enum, DateTime, Numeric, ForeignKey, func, text
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class BatchStatus(str, PyEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ConversionJobBatch(Base):
    __tablename__ = "conversion_job_batches"

    id = Column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    job_id = Column(UUID(as_uuid=True), ForeignKey("conversion_jobs.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    token_account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    status = Column(Enum(BatchStatus), nullable=False)
    tokens_total = Column(Numeric(18, 8), nullable=False)
    usd_total = Column(Numeric(18, 8), nullable=True)
    fee_total = Column(Numeric(18, 8), nullable=True)
    error_message = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
