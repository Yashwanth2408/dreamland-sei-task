"""
Dreamland FastAPI application — entry point.

Startup sequence:
  1. Configure structured logging
  2. Register all API routers under /api/v1
  3. Start APScheduler for hourly conversion job
  4. Expose Prometheus metrics at /metrics
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import api_router
from app.core.config import settings
from app.core.logging import configure_logging, logger
from app.jobs.conversion_job import scheduler, start_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    configure_logging(settings.ENVIRONMENT)
    logger.info(
        "dreamland.startup",
        env=settings.ENVIRONMENT,
        region=settings.AWS_REGION,
    )
    start_scheduler()

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    if scheduler.running:
        scheduler.shutdown(wait=False)
    logger.info("dreamland.shutdown")


app = FastAPI(
    title="Dreamland Token API",
    description=(
        "Double-entry accounting backend for DREAM token issuance "
        "and USD conversion. Ledger model: Square Books."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Prometheus metrics (/metrics) ────────────────────────────────────────────
Instrumentator().instrument(app).expose(app)

# ── All API routes (/api/v1/...) ─────────────────────────────────────────────
app.include_router(api_router, prefix="/api/v1")


# ── Global exception handler ─────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", path=str(request.url), error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["Ops"])
async def health():
    return {
        "status": "ok",
        "env":    settings.ENVIRONMENT,
        "region": settings.AWS_REGION,
    }