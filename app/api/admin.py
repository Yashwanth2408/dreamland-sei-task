"""Owner/admin API endpoints (no auth in demo)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_db
from app.schemas.admin import AdminOverviewResponse, AdminUsersResponse
from app.services.admin_service import get_admin_overview, get_admin_users

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/overview", response_model=AdminOverviewResponse)
async def admin_overview(db: AsyncSession = Depends(get_db)) -> AdminOverviewResponse:
    data = await get_admin_overview(db)
    return AdminOverviewResponse(**data)


@router.get("/users", response_model=AdminUsersResponse)
async def admin_users(
    q: str | None = Query(default=None, description="Search by username/email/external_id"),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> AdminUsersResponse:
    data = await get_admin_users(db, q, limit)
    return AdminUsersResponse(**data)
