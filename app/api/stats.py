"""GET /stats endpoint."""
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.engine import get_db
from app.services.stats_service import get_stats

router = APIRouter()


@router.get("/stats")
async def get_user_stats(
    user_id: uuid.UUID = Query(..., description="User UUID"),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns:
    - Sum of tokens won today (current local day for the user's timezone)
    - Total USD balance accumulated across all time
    - Remaining token allowance for today
    """
    return await get_stats(db, user_id)