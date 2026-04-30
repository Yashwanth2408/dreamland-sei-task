"""Async SQLAlchemy engine + session factory."""
import json
from typing import Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.ENVIRONMENT == "development",
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
)

_engines: dict[str, AsyncEngine] = {"default": engine}
_region_map: Optional[dict[str, str]] = None


def _load_region_map() -> dict[str, str]:
    global _region_map
    if _region_map is not None:
        return _region_map
    if not settings.DATABASE_URLS_BY_REGION:
        _region_map = {}
        return _region_map

    try:
        raw = json.loads(settings.DATABASE_URLS_BY_REGION)
        _region_map = {str(k).lower(): str(v) for k, v in raw.items()}
    except Exception:
        _region_map = {}
    return _region_map


def _get_engine_for_region(region: Optional[str]) -> AsyncEngine:
    if not settings.MULTI_REGION_ENABLED:
        return _engines["default"]

    region_key = (region or "").lower().strip()
    region_map = _load_region_map()
    target_url = region_map.get(region_key)
    if not target_url:
        return _engines["default"]

    if region_key not in _engines:
        _engines[region_key] = create_async_engine(
            target_url,
            echo=settings.ENVIRONMENT == "development",
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _engines[region_key]


async def get_db(request: Optional[Request] = None) -> AsyncSession:
    """FastAPI dependency that yields a DB session."""
    region = request.headers.get("x-region") if request else None
    engine_for_region = _get_engine_for_region(region)
    SessionLocal = async_sessionmaker(
        bind=engine_for_region,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise