"""Token API endpoints."""
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_db
from app.schemas.tokens import WinTokenRequest, WinTokenResponse, TokenHistoryResponse
from app.services import token_service

router = APIRouter(prefix="/tokens", tags=["Tokens"])


@router.post(
    "/win",
    response_model=WinTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a token win event",
    description=(
        "Awards DREAM tokens to a user after a game win. "
        "Enforces the 5-token daily cap (in user's local timezone). "
        "Idempotent: same idempotency_key returns the same response "
        "without re-processing or writing duplicate ledger rows."
    ),
)
async def win_tokens(
    payload: WinTokenRequest,
    db: AsyncSession = Depends(get_db),
) -> WinTokenResponse:
    return await token_service.win_tokens(db, payload)


@router.get(
    "/history",
    response_model=TokenHistoryResponse,
    summary="Get today's token win history for a user",
    description=(
        "Returns all DREAM token wins for the current calendar day "
        "in the user's local timezone."
    ),
)
async def token_history(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> TokenHistoryResponse:
    data = await token_service.get_token_history(db, user_id)
    return TokenHistoryResponse(**data)