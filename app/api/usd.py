"""USD history API endpoint."""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_db
from app.schemas.usd import UsdHistoryResponse
from app.services import usd_service

router = APIRouter(prefix="/usd", tags=["USD"])


@router.get(
    "/history",
    response_model=UsdHistoryResponse,
    summary="Get USD conversion history (up to previous day)",
    description=(
        "Returns all USD amounts credited to the user from token conversions, "
        "up to but not including the current calendar day in their timezone. "
        "Amounts are shown as positive values (stored internally as negative credits)."
    ),
)
async def usd_history(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> UsdHistoryResponse:
    data = await usd_service.get_usd_history(db, user_id)
    return UsdHistoryResponse(**data)