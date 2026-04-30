"""Development-only endpoints."""
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.engine import get_db
from app.models.users import User
from app.schemas.dev import SeedUserRequest, SeedUserResponse

router = APIRouter(prefix="/dev", tags=["Dev"])


@router.post(
    "/seed-user",
    response_model=SeedUserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a demo user (development only)",
)
async def seed_user(
    payload: SeedUserRequest = SeedUserRequest(),
    db: AsyncSession = Depends(get_db),
) -> SeedUserResponse:
    if settings.ENVIRONMENT.lower() != "development":
        raise HTTPException(status_code=404, detail="Not found")

    suffix = uuid4().hex[:8]
    username = payload.username or f"demo_{suffix}"
    email = payload.email or f"demo_{suffix}@dreamland.local"
    external_id = payload.external_id or f"demo-ext-{suffix}"

    user = User(
        external_id=external_id,
        username=username,
        email=email,
        timezone=payload.timezone or "UTC",
        region=payload.region or "global",
        is_active=True,
    )

    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User seed conflict; try again",
        )

    await db.refresh(user)
    return SeedUserResponse(
        id=str(user.id),
        external_id=user.external_id,
        username=user.username,
        email=user.email,
        timezone=user.timezone,
        region=user.region,
        is_active=user.is_active,
    )
