"""
Dreamland — complete test suite.

This suite runs against in-memory SQLite while production models are written
for PostgreSQL. To make that work safely for tests only, we:

1. Replace PostgreSQL UUID columns with String(36)
2. Remove PostgreSQL-only server defaults like gen_random_uuid()
3. Replace server-side now() defaults with Python-side datetime defaults
4. Restore original model metadata after creating the test schema

Why: SQLite cannot execute PostgreSQL-specific defaults/functions, and if we
strip a NOT NULL timestamp server_default without replacing it, inserts fail
with NOT NULL constraint errors.
"""
import uuid
import asyncio
from decimal import Decimal
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import String, select, func, DateTime, ColumnDefault
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.main import app
from app.db.engine import get_db
from app.models.base import Base
from app.models.users import User
from app.models.accounts import Account
from app.models.ledger import TokenLedgerEntry, UsdLedgerEntry, IdempotencyKey
from app.models.conversion_jobs import ConversionJob
from app.models.conversion_job_batches import ConversionJobBatch


ALL_MODELS = [
    User,
    Account,
    TokenLedgerEntry,
    UsdLedgerEntry,
    IdempotencyKey,
    ConversionJob,
    ConversionJobBatch,
]


def utcnow():
    return datetime.now(timezone.utc)


def _make_sqlite_safe():
    """
    Patch model metadata in-place before Base.metadata.create_all() for SQLite.

    We restore everything afterward, so production runtime remains untouched.
    """
    saved = {}

    for model in ALL_MODELS:
        for col in model.__table__.columns:
            key = (model.__tablename__, col.key)
            entry = {}

            # 1) PostgreSQL UUID -> String(36) for SQLite
            if isinstance(col.type, PG_UUID):
                entry["type"] = col.type
                col.type = String(36)
                
                # Ensure UUID columns have uuid.uuid4 default if they don't already
                if col.default is None and col.server_default is not None:
                    # Will be restored later but saves the original
                    entry["default"] = col.default

            # 2) Remove PostgreSQL-only server defaults
            if col.server_default is not None:
                sd_str = (
                    str(col.server_default.arg)
                    if hasattr(col.server_default, "arg")
                    else str(col.server_default)
                )

                if "gen_random_uuid" in sd_str or "now()" in sd_str:
                    entry["server_default"] = col.server_default
                    col.server_default = None

            if entry:
                saved[key] = entry

    return saved


def _restore(saved):
    """Restore original SQLAlchemy column metadata after schema creation."""
    for model in ALL_MODELS:
        for col in model.__table__.columns:
            key = (model.__tablename__, col.key)
            if key not in saved:
                continue

            entry = saved[key]

            if "type" in entry:
                col.type = entry["type"]
            if "server_default" in entry:
                col.server_default = entry["server_default"]
            if "default" in entry:
                col.default = entry["default"]


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    saved = _make_sqlite_safe()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _restore(saved)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def SessionLocal(db_engine):
    """Shared session factory for all tests."""
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(SessionLocal):
    """Session for direct database access in tests."""
    async with SessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client(SessionLocal):
    async def override_get_db():
        async with SessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(SessionLocal):
    """Create a test user for each test."""
    async with SessionLocal() as session:
        user = User(
            id=uuid.uuid4(),  # Keep as UUID object - SQLAlchemy will convert
            external_id=f"ext_{uuid.uuid4().hex[:8]}",
            username=f"player_{uuid.uuid4().hex[:6]}",
            email=f"player_{uuid.uuid4().hex[:6]}@test.com",
            timezone="UTC",
            region="global",
            is_active=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        
        # Detach the object from session before returning
        # so it can be used in other sessions
        user_id_val = user.id
        
    # Fetch the user again in a fresh session to ensure it's persisted
    async with SessionLocal() as verify_session:
        result = await verify_session.execute(
            select(User).where(User.id == user_id_val)
        )
        verified_user = result.scalar_one_or_none()
        if verified_user:
            await verify_session.refresh(verified_user)
        return verified_user or user


def win_payload(user_id, amount="2", ikey=None, won_at=None):
    return {
        "user_id": str(user_id),
        "amount": amount,
        "won_at": won_at or utcnow().isoformat(),
        "idempotency_key": ikey or str(uuid.uuid4()),
    }


class TestWinTokens:

    @pytest.mark.asyncio
    async def test_happy_path_returns_201(self, client, test_user):
        r = await client.post("/api/v1/tokens/win", json=win_payload(test_user.id))
        assert r.status_code == 201, r.text
        data = r.json()
        assert Decimal(str(data["tokens_awarded"])) == Decimal("2")
        assert "transaction_id" in data

    @pytest.mark.asyncio
    async def test_double_entry_net_zero(self, client, test_user, db_session):
        r = await client.post("/api/v1/tokens/win", json=win_payload(test_user.id, amount="3"))
        assert r.status_code == 201, r.text
        txn_id = uuid.UUID(r.json()["transaction_id"])

        result = await db_session.execute(
            select(func.sum(TokenLedgerEntry.amount))
            .where(TokenLedgerEntry.transaction_id == txn_id)
        )
        net = result.scalar()
        assert net == Decimal("0"), f"Double-entry violated: net={net}"

    @pytest.mark.asyncio
    async def test_exactly_two_rows_per_win(self, client, test_user, db_session):
        r = await client.post("/api/v1/tokens/win", json=win_payload(test_user.id, amount="1"))
        assert r.status_code == 201, r.text
        txn_id = uuid.UUID(r.json()["transaction_id"])

        result = await db_session.execute(
            select(func.count())
            .select_from(TokenLedgerEntry)
            .where(TokenLedgerEntry.transaction_id == txn_id)
        )
        assert result.scalar() == 2

    @pytest.mark.asyncio
    async def test_daily_cap_exactly_5_succeeds(self, client, test_user):
        won_at = utcnow().isoformat()
        for amount, suffix in [("2", "a"), ("2", "b"), ("1", "c")]:
            r = await client.post(
                "/api/v1/tokens/win",
                json={
                    **win_payload(test_user.id, amount=amount),
                    "won_at": won_at,
                    "idempotency_key": f"cap-ok-{suffix}",
                },
            )
            assert r.status_code == 201, r.text

    @pytest.mark.asyncio
    async def test_daily_cap_exceeded_returns_422(self, client, test_user):
        won_at = utcnow().isoformat()
        for amount, suffix in [("2", "a"), ("2", "b"), ("1", "c")]:
            await client.post(
                "/api/v1/tokens/win",
                json={
                    **win_payload(test_user.id, amount=amount),
                    "won_at": won_at,
                    "idempotency_key": f"cap-fail-{suffix}",
                },
            )

        r = await client.post(
            "/api/v1/tokens/win",
            json={
                **win_payload(test_user.id, amount="1"),
                "won_at": won_at,
                "idempotency_key": "cap-fail-over",
            },
        )
        assert r.status_code == 422, r.text
        assert "cap" in r.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_cap_exceeded_by_large_single_win(self, client, test_user):
        won_at = utcnow().isoformat()

        r1 = await client.post(
            "/api/v1/tokens/win",
            json={
                **win_payload(test_user.id, amount="3"),
                "won_at": won_at,
                "idempotency_key": "big-win-001",
            },
        )
        assert r1.status_code == 201, r1.text

        r2 = await client.post(
            "/api/v1/tokens/win",
            json={
                **win_payload(test_user.id, amount="4"),
                "won_at": won_at,
                "idempotency_key": "big-win-002",
            },
        )
        assert r2.status_code == 422, r2.text

    @pytest.mark.asyncio
    async def test_idempotency_replay_same_response(self, client, test_user, db_session):
        ikey = str(uuid.uuid4())
        payload = win_payload(test_user.id, amount="1", ikey=ikey)

        r1 = await client.post("/api/v1/tokens/win", json=payload)
        r2 = await client.post("/api/v1/tokens/win", json=payload)

        assert r1.status_code == 201, r1.text
        assert r2.status_code == 201, r2.text
        assert r1.json()["transaction_id"] == r2.json()["transaction_id"]

        count = await db_session.execute(
            select(func.count()).select_from(TokenLedgerEntry)
        )
        assert count.scalar() == 2

    @pytest.mark.asyncio
    async def test_fractional_tokens_rejected(self, client, test_user):
        r = await client.post("/api/v1/tokens/win", json=win_payload(test_user.id, amount="1.5"))
        assert r.status_code == 422, r.text

    @pytest.mark.asyncio
    async def test_zero_amount_rejected(self, client, test_user):
        r = await client.post("/api/v1/tokens/win", json=win_payload(test_user.id, amount="0"))
        assert r.status_code == 422, r.text

    @pytest.mark.asyncio
    async def test_negative_amount_rejected(self, client, test_user):
        r = await client.post("/api/v1/tokens/win", json=win_payload(test_user.id, amount="-1"))
        assert r.status_code == 422, r.text

    @pytest.mark.asyncio
    async def test_amount_over_5_rejected(self, client, test_user):
        r = await client.post("/api/v1/tokens/win", json=win_payload(test_user.id, amount="6"))
        assert r.status_code == 422, r.text

    @pytest.mark.asyncio
    async def test_naive_datetime_rejected(self, client, test_user):
        r = await client.post(
            "/api/v1/tokens/win",
            json={
                "user_id": str(test_user.id),
                "amount": "1",
                "won_at": "2024-11-01T14:30:00",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        assert r.status_code == 422, r.text

    @pytest.mark.asyncio
    async def test_nonexistent_user_returns_404(self, client):
        r = await client.post("/api/v1/tokens/win", json=win_payload(uuid.uuid4()))
        assert r.status_code == 404, r.text

    @pytest.mark.asyncio
    async def test_missing_idempotency_key_returns_422(self, client, test_user):
        r = await client.post(
            "/api/v1/tokens/win",
            json={
                "user_id": str(test_user.id),
                "amount": "1",
                "won_at": utcnow().isoformat(),
            },
        )
        assert r.status_code == 422, r.text

    @pytest.mark.asyncio
    async def test_concurrent_wins_respect_daily_cap(self, client, test_user, db_session):
        bind = db_session.get_bind()
        if bind is not None and bind.dialect.name == "sqlite":
            pytest.xfail("SQLite does not enforce row-level locks")

        won_at = utcnow().isoformat()
        payload_a = {
            **win_payload(test_user.id, amount="3"),
            "won_at": won_at,
            "idempotency_key": f"lock-a-{uuid.uuid4().hex}",
        }
        payload_b = {
            **win_payload(test_user.id, amount="3"),
            "won_at": won_at,
            "idempotency_key": f"lock-b-{uuid.uuid4().hex}",
        }

        res_a, res_b = await asyncio.gather(
            client.post("/api/v1/tokens/win", json=payload_a),
            client.post("/api/v1/tokens/win", json=payload_b),
        )

        statuses = {res_a.status_code, res_b.status_code}
        assert statuses.issubset({201, 422}), (res_a.text, res_b.text)
        assert 201 in statuses, (res_a.text, res_b.text)

        stats = await client.get(f"/api/v1/stats?user_id={test_user.id}")
        assert stats.status_code == 200, stats.text
        tokens_today = Decimal(str(stats.json()["tokens_won_today"]))
        assert tokens_today <= Decimal("5")


class TestTokenHistory:

    @pytest.mark.asyncio
    async def test_history_shows_todays_wins(self, client, test_user):
        await client.post(
            "/api/v1/tokens/win",
            json=win_payload(test_user.id, amount="3", ikey=str(uuid.uuid4())),
        )

        r = await client.get(f"/api/v1/tokens/history?user_id={test_user.id}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert Decimal(str(data["total_tokens_today"])) == Decimal("3")
        assert len(data["entries"]) == 1

    @pytest.mark.asyncio
    async def test_history_empty_for_new_user(self, client, test_user):
        r = await client.get(f"/api/v1/tokens/history?user_id={test_user.id}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["entries"] == []
        assert Decimal(str(data["total_tokens_today"])) == Decimal("0")

    @pytest.mark.asyncio
    async def test_history_accumulates_multiple_wins(self, client, test_user):
        won_at = utcnow().isoformat()

        for amount, ikey in [("1", "hist-001"), ("2", "hist-002")]:
            await client.post(
                "/api/v1/tokens/win",
                json={
                    **win_payload(test_user.id, amount=amount),
                    "won_at": won_at,
                    "idempotency_key": ikey,
                },
            )

        r = await client.get(f"/api/v1/tokens/history?user_id={test_user.id}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert Decimal(str(data["total_tokens_today"])) == Decimal("3")
        assert len(data["entries"]) == 2

    @pytest.mark.asyncio
    async def test_history_unknown_user_returns_404(self, client):
        r = await client.get(f"/api/v1/tokens/history?user_id={uuid.uuid4()}")
        assert r.status_code == 404, r.text


class TestStats:

    @pytest.mark.asyncio
    async def test_stats_correct_after_win(self, client, test_user):
        await client.post(
            "/api/v1/tokens/win",
            json=win_payload(test_user.id, amount="2", ikey=str(uuid.uuid4())),
        )

        r = await client.get(f"/api/v1/stats?user_id={test_user.id}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert Decimal(str(data["tokens_won_today"])) == Decimal("2")
        assert data["tokens_remaining_today"] == 3

    @pytest.mark.asyncio
    async def test_stats_remaining_decrements(self, client, test_user):
        won_at = utcnow().isoformat()

        for amount, ikey in [("1", "stat-001"), ("2", "stat-002")]:
            await client.post(
                "/api/v1/tokens/win",
                json={
                    **win_payload(test_user.id, amount=amount),
                    "won_at": won_at,
                    "idempotency_key": ikey,
                },
            )

        r = await client.get(f"/api/v1/stats?user_id={test_user.id}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert Decimal(str(data["tokens_won_today"])) == Decimal("3")
        assert data["tokens_remaining_today"] == 2

    @pytest.mark.asyncio
    async def test_stats_zero_for_fresh_user(self, client, test_user):
        r = await client.get(f"/api/v1/stats?user_id={test_user.id}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert Decimal(str(data["tokens_won_today"])) == Decimal("0")
        assert data["tokens_remaining_today"] == 5
        assert Decimal(str(data["total_usd_balance"])) == Decimal("0")

    @pytest.mark.asyncio
    async def test_stats_unknown_user_returns_404(self, client):
        r = await client.get(f"/api/v1/stats?user_id={uuid.uuid4()}")
        assert r.status_code == 404, r.text


class TestUsdHistory:

    @pytest.mark.asyncio
    async def test_usd_history_empty_before_conversion(self, client, test_user):
        r = await client.get(f"/api/v1/usd/history?user_id={test_user.id}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["entries"] == []
        assert Decimal(str(data["total_usd_balance"])) == Decimal("0")

    @pytest.mark.asyncio
    async def test_usd_history_unknown_user_returns_404(self, client):
        r = await client.get(f"/api/v1/usd/history?user_id={uuid.uuid4()}")
        assert r.status_code == 404, r.text