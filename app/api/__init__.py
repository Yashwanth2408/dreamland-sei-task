from fastapi import APIRouter

from app.api.tokens import router as tokens_router
from app.api.usd import router as usd_router
from app.api.stats import router as stats_router
from app.api.dev import router as dev_router
from app.api.admin import router as admin_router

api_router = APIRouter()
api_router.include_router(tokens_router)
api_router.include_router(usd_router)
api_router.include_router(stats_router)
api_router.include_router(dev_router)
api_router.include_router(admin_router)