"""Hourly conversion job tracking table."""
import uuid
from enum import Enum as PyEnum
from sqlalchemy import Column, String, Enum, DateTime, Integer, Numeric, func, text
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class JobStatus(str, PyEnum):
    PENDING   = "PENDING"
    RUNNING   = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    RETRYING  = "RETRYING"


class ConversionJob(Base):
    """
    One row per hourly conversion run.
    hour_bucket is UNIQUE — acts as a distributed mutex for the job.
    """
    __tablename__ = "conversion_jobs"

    id = Column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    hour_bucket = Column(
        DateTime(timezone=True), nullable=False, unique=True,
        comment="Floor-to-hour UTC timestamp, e.g. 2024-11-01T14:00:00Z"
    )
    status            = Column(Enum(JobStatus), nullable=False, default=JobStatus.PENDING)
    token_rate_usd    = Column(Numeric(18, 8), nullable=False)
    entries_processed = Column(Integer, nullable=False, default=0)
    usd_total         = Column(Numeric(18, 8), nullable=True)
    fee_total         = Column(Numeric(18, 8), nullable=True)
    retry_count       = Column(Integer, nullable=False, default=0)
    error_message     = Column(String(500), nullable=True)
    started_at        = Column(DateTime(timezone=True), nullable=True)
    completed_at      = Column(DateTime(timezone=True), nullable=True)
    created_at        = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)