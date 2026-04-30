"""Central settings — loaded once at startup via pydantic-settings."""
from decimal import Decimal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8"
    )

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://dreamland:secret@localhost:5432/dreamland"
    DATABASE_URL_SYNC: str = "postgresql://dreamland:secret@localhost:5432/dreamland"

    # App
    ENVIRONMENT: str = "development"
    SECRET_KEY: str = "change-me"

    # Business rules
    DREAM_TOKEN_RATE_USD: Decimal = Decimal("0.15")
    MAX_TOKENS_PER_DAY: int = 5
    CONVERSION_FEE_RATE: Decimal = Decimal("0.02")

    # Rate provider (optional)
    RATE_PROVIDER_URL: str = ""
    RATE_PROVIDER_TIMEOUT_SECONDS: int = 5

    # Multi-region (optional)
    MULTI_REGION_ENABLED: bool = False
    DATABASE_URLS_BY_REGION: str = ""

    AWS_REGION: str = "ap-south-1"
    SENTRY_DSN: str = ""


settings = Settings()