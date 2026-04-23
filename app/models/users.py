import uuid
from sqlalchemy import Column, String, DateTime, Boolean, func, text
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    external_id = Column(
        String(128), nullable=False, unique=True, index=True,
        comment="ID from upstream identity provider (Auth0, Cognito, etc.)"
    )
    username   = Column(String(80),  nullable=False, unique=True, index=True)
    email      = Column(String(200), nullable=False, unique=True)
    timezone   = Column(
        String(60), nullable=False, default="UTC",
        comment="IANA timezone string — used for daily-cap boundary"
    )
    region     = Column(
        String(30), nullable=False, default="global",
        comment="Data residency region: eu | us | apac | global"
    )
    is_active  = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )