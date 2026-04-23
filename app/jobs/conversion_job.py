"""
Hourly Token → USD Conversion Job.

WHAT IT DOES (runs at top of every hour):
  1. Targets the PREVIOUS completed hour (e.g. at 15:00, processes 14:00–15:00)
  2. Claims the job using INSERT ... ON CONFLICT (distributed mutex via DB)
  3. Finds all token_ledger DEBIT entries with is_converted=False in that hour
  4. Groups them by user wallet
  5. For each user, writes 4 ledger rows (2 pairs):

     Pair A — USD conversion:
       DEBIT   CONVERSION_POOL   +gross_usd   (USD leaves pool)
       CREDIT  USER_USD_WALLET   -gross_usd   (user receives USD)

     Pair B — Fee (Dreamland pays, NOT the user):
       DEBIT   DREAMLAND_FEE_EXP  +fee_usd
       CREDIT  FEE_PAYABLE        -fee_usd

  6. Marks token entries as is_converted=True
  7. Updates job row to COMPLETED

IDEMPOTENCY:
  hour_bucket is UNIQUE on conversion_jobs.
  Re-running the job for the same hour is safe — already-converted
  token entries are skipped; completed job rows are not overwritten.

RETRY:
  Tenacity retries each user's batch up to 3 times with exponential back-off.
  One user's failure does NOT block other users.
"""
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.logging import logger
from app.db.engine import AsyncSessionLocal
from app.models.accounts import Account, AccountCode
from app.models.conversion_jobs import ConversionJob, JobStatus
from app.models.ledger import TokenLedgerEntry, UsdLedgerEntry, EntryType
from app.services.account_service import (
    get_or_create_system_account,
    get_or_create_user_account,
)
from app.utils.time_utils import floor_to_hour

# APScheduler instance — started in app lifespan
scheduler = AsyncIOScheduler(timezone="UTC")


def start_scheduler() -> None:
    """Register the hourly job and start the scheduler."""
    scheduler.add_job(
        run_conversion_job,
        trigger=CronTrigger(minute=0, second=0),  # fires at HH:00:00 UTC
        id="hourly_token_conversion",
        replace_existing=True,
        misfire_grace_time=300,   # allow up to 5 min late firing
        coalesce=True,            # if missed multiple hours, run only once
    )
    scheduler.start()
    logger.info("scheduler.started", job="hourly_token_conversion")


async def run_conversion_job() -> None:
    """
    Main entry point — called by APScheduler at the top of every hour.
    Processes the PREVIOUS completed hour.
    """
    now         = datetime.now(timezone.utc)
    current_hour = floor_to_hour(now)
    target_hour  = current_hour - timedelta(hours=1)

    logger.info("conversion_job.begin", target_hour=target_hour.isoformat())

    async with AsyncSessionLocal() as db:
        # ── Claim the job (idempotent upsert) ────────────────────────────────
        # Only claim if status is not COMPLETED
        stmt = (
            pg_insert(ConversionJob)
            .values(
                id             = uuid.uuid4(),
                hour_bucket    = target_hour,
                status         = JobStatus.RUNNING,
                token_rate_usd = settings.DREAM_TOKEN_RATE_USD,
                started_at     = now,
            )
            .on_conflict_do_update(
                index_elements=["hour_bucket"],
                set_={
                    "status":     JobStatus.RUNNING,
                    "started_at": now,
                },
                where=(ConversionJob.status.in_([
                    JobStatus.PENDING,
                    JobStatus.RETRYING,
                    JobStatus.FAILED,
                ])),
            )
            .returning(ConversionJob.id, ConversionJob.status)
        )
        result = await db.execute(stmt)
        row    = result.fetchone()
        await db.commit()

        if row is None:
            logger.info("conversion_job.skipped_already_done",
                        hour=target_hour.isoformat())
            return

        job_id = row[0]

        try:
            # ── Fetch unconverted DEBIT entries for this hour ─────────────────
            entries_result = await db.execute(
                select(TokenLedgerEntry)
                .where(
                    TokenLedgerEntry.entry_type  == EntryType.DEBIT,
                    TokenLedgerEntry.is_converted == False,           # noqa: E712
                    TokenLedgerEntry.won_at       >= target_hour,
                    TokenLedgerEntry.won_at       <  target_hour + timedelta(hours=1),
                )
                .order_by(TokenLedgerEntry.account_id, TokenLedgerEntry.won_at)
            )
            token_entries = entries_result.scalars().all()

            if not token_entries:
                logger.info("conversion_job.no_entries",
                            hour=target_hour.isoformat())
                await _mark_completed(db, job_id, 0, Decimal("0"), Decimal("0"))
                return

            # ── Group entries by user wallet account ──────────────────────────
            by_account: dict[uuid.UUID, list[TokenLedgerEntry]] = defaultdict(list)
            for entry in token_entries:
                by_account[entry.account_id].append(entry)

            # ── Pre-fetch system accounts (once per job) ──────────────────────
            conversion_pool = await get_or_create_system_account(db, AccountCode.CONVERSION_POOL)
            fee_payable     = await get_or_create_system_account(db, AccountCode.FEE_PAYABLE)
            fee_expense     = await get_or_create_system_account(db, AccountCode.DREAMLAND_FEE_EXP)

            total_usd        = Decimal("0")
            total_fee        = Decimal("0")
            total_processed  = 0

            # ── Process each user's batch independently ───────────────────────
            for token_account_id, acct_entries in by_account.items():
                try:
                    gross, fee = await _convert_user_batch(
                        db, job_id, token_account_id, acct_entries,
                        conversion_pool, fee_payable, fee_expense,
                    )
                    total_usd       += gross
                    total_fee       += fee
                    total_processed += len(acct_entries)

                except Exception as exc:
                    # One user's failure is isolated — other users are unaffected
                    logger.error(
                        "conversion_job.user_batch_failed",
                        account_id=str(token_account_id),
                        error=str(exc),
                    )

            await _mark_completed(db, job_id, total_processed, total_usd, total_fee)
            logger.info(
                "conversion_job.complete",
                hour=target_hour.isoformat(),
                entries=total_processed,
                usd=str(total_usd),
                fee=str(total_fee),
            )

        except Exception as exc:
            await db.rollback()
            await _mark_failed(db, job_id, str(exc))
            logger.error("conversion_job.fatal_error", error=str(exc))
            raise


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
async def _convert_user_batch(
    db,
    job_id: uuid.UUID,
    token_account_id: uuid.UUID,
    entries: list[TokenLedgerEntry],
    conversion_pool,
    fee_payable,
    fee_expense,
) -> tuple[Decimal, Decimal]:
    """
    Convert one user's token batch to USD.
    Retried up to 3× with exponential back-off on failure.
    Returns (gross_usd, fee_usd).
    """
    total_tokens = sum(e.amount for e in entries)
    gross_usd = (total_tokens * settings.DREAM_TOKEN_RATE_USD).quantize(
        Decimal("0.00000001")
    )
    fee_usd = (gross_usd * settings.CONVERSION_FEE_RATE).quantize(
        Decimal("0.00000001")
    )

    # Find the user_id from the token account row
    acct_result = await db.execute(
        select(Account).where(Account.id == token_account_id)
    )
    token_acct = acct_result.scalar_one()

    # Get / create user's USD wallet
    usd_wallet = await get_or_create_user_account(
        db, token_acct.user_id, AccountCode.USER_USD_WALLET
    )

    usd_txn_id = uuid.uuid4()
    fee_txn_id = uuid.uuid4()

    # ── Pair A: USD conversion ────────────────────────────────────────────────
    db.add(UsdLedgerEntry(
        transaction_id              = usd_txn_id,
        account_id                  = conversion_pool.id,
        entry_type                  = EntryType.DEBIT,
        amount                      = gross_usd,          # positive
        description                 = (
            f"Token→USD: {total_tokens} DREAM "
            f"@ ${settings.DREAM_TOKEN_RATE_USD}/token"
        ),
        source_token_transaction_id = entries[0].transaction_id,
        conversion_job_id           = job_id,
    ))
    db.add(UsdLedgerEntry(
        transaction_id              = usd_txn_id,
        account_id                  = usd_wallet.id,
        entry_type                  = EntryType.CREDIT,
        amount                      = -gross_usd,         # negative (mirror)
        description                 = f"USD credited: {total_tokens} tokens converted",
        source_token_transaction_id = entries[0].transaction_id,
        conversion_job_id           = job_id,
    ))

    # ── Pair B: Fee (Dreamland pays, not the user) ────────────────────────────
    db.add(UsdLedgerEntry(
        transaction_id    = fee_txn_id,
        account_id        = fee_expense.id,
        entry_type        = EntryType.DEBIT,
        amount            = fee_usd,                      # positive expense
        description       = f"Fee expense for job {job_id}",
        conversion_job_id = job_id,
    ))
    db.add(UsdLedgerEntry(
        transaction_id    = fee_txn_id,
        account_id        = fee_payable.id,
        entry_type        = EntryType.CREDIT,
        amount            = -fee_usd,                     # negative liability
        description       = f"Fee payable for job {job_id}",
        conversion_job_id = job_id,
    ))

    # ── Mark token entries as converted ──────────────────────────────────────
    entry_ids = [e.id for e in entries]
    await db.execute(
        update(TokenLedgerEntry)
        .where(TokenLedgerEntry.id.in_(entry_ids))
        .values(is_converted=True, conversion_job_id=job_id)
    )

    await db.flush()
    return gross_usd, fee_usd


async def _mark_completed(db, job_id, count, usd, fee):
    await db.execute(
        update(ConversionJob)
        .where(ConversionJob.id == job_id)
        .values(
            status            = JobStatus.COMPLETED,
            entries_processed = count,
            usd_total         = usd,
            fee_total         = fee,
            completed_at      = datetime.now(timezone.utc),
        )
    )
    await db.commit()


async def _mark_failed(db, job_id, error_msg: str):
    try:
        await db.execute(
            update(ConversionJob)
            .where(ConversionJob.id == job_id)
            .values(
                status        = JobStatus.FAILED,
                error_message = error_msg[:500],
                retry_count   = ConversionJob.retry_count + 1,
            )
        )
        await db.commit()
    except Exception:
        pass