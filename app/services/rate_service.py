"""Rate provider integration for token conversion."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import logger


async def fetch_token_rate_usd() -> tuple[Decimal, str, Optional[str], datetime]:
    """
    Fetch the token->USD rate from an external provider.

    Returns:
        (rate, source, error, fetched_at)
    """
    fetched_at = datetime.now(timezone.utc)

    if not settings.RATE_PROVIDER_URL:
        return settings.DREAM_TOKEN_RATE_USD, "static", None, fetched_at

    try:
        timeout = httpx.Timeout(settings.RATE_PROVIDER_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(settings.RATE_PROVIDER_URL)
            resp.raise_for_status()
            data = resp.json()

        raw_rate = (
            data.get("rate_usd")
            or data.get("token_rate_usd")
            or data.get("rate")
        )
        if raw_rate is None:
            raise ValueError("Rate field missing from provider response")

        rate = Decimal(str(raw_rate))
        if rate <= 0:
            raise ValueError("Rate must be positive")

        return rate, "provider", None, fetched_at
    except Exception as exc:
        logger.error("rate_provider.error", error=str(exc))
        return settings.DREAM_TOKEN_RATE_USD, "fallback", str(exc), fetched_at
