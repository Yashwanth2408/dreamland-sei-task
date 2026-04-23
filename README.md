# Dreamland Backend System

## Overview

Dreamland is a production-grade backend system built for a metaverse gaming platform where users win DREAM tokens by playing games with AI agents. These tokens carry real monetary value and are periodically converted to USD. The system implements a rigorous double-entry accounting ledger, idempotent API design, timezone-aware daily cap enforcement, and a fault-tolerant hourly conversion job — all built to production standards.

This document covers the full system design, architectural decisions, database schema, API implementation, testing strategy, and infrastructure considerations for a global, multi-region deployment.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Architecture Overview](#2-architecture-overview)
3. [Tech Stack and Design Rationale](#3-tech-stack-and-design-rationale)
4. [Double-Entry Accounting Theory](#4-double-entry-accounting-theory)
5. [Database Schema Design](#5-database-schema-design)
6. [API Design and Implementation](#6-api-design-and-implementation)
7. [Hourly Token Conversion Job](#7-hourly-token-conversion-job)
8. [Edge Cases](#8-edge-cases)
9. [Infrastructure Design](#9-infrastructure-design)
10. [Project Structure](#10-project-structure)
11. [Local Development Setup](#11-local-development-setup)
12. [Running Migrations](#12-running-migrations)
13. [Running the Server](#13-running-the-server)
14. [Test Suite](#14-test-suite)
15. [Test Results](#15-test-results)
16. [Additional APIs and Tooling](#16-additional-apis-and-tooling)
17. [Reference Notes](#17-reference-notes)

---

## 1. Problem Statement

Dreamland users play games and win DREAM tokens throughout the day. The system must:

- Accept token win events from the game layer via a REST API
- Enforce a hard daily cap of 5 tokens per user per calendar day in the user's local timezone
- Convert all tokens to USD at the end of every clock hour at a fixed rate of $0.15 per token
- Track all financial events using a double-entry accounting ledger — both for tokens and for USD
- Track fees paid by Dreamland (not the user) on each conversion
- Return token history, USD history, and a combined stats summary via read APIs
- Handle concurrent requests, network retries, and conversion job failures gracefully

The system must be designed for a global user base with data residency requirements (GDPR for EU users).

---

## 2. Architecture Overview

```
Client / Game Layer
        |
        v
   API Gateway (AWS ALB)
        |
        v
   FastAPI Application (ECS Fargate)
   - POST /api/v1/tokens/win
   - GET  /api/v1/tokens/history
   - GET  /api/v1/usd/history
   - GET  /api/v1/stats
        |
        v
   PostgreSQL (Aurora RDS, Multi-AZ)
   - users
   - accounts (chart of accounts)
   - token_ledger (double-entry)
   - usd_ledger (double-entry)
   - conversion_jobs
   - idempotency_keys
        |
        v
   APScheduler (in-process, upgradeable to Celery)
   - Hourly conversion job
   - Converts token_ledger entries to USD
   - Writes double-entry USD and fee ledger entries
```

The application is deployed in multiple AWS regions. EU user data is confined to the eu-west-1 region. A central analytics cluster in us-east-1 receives anonymised ledger data via Debezium CDC over Apache Kafka.

---

## 3. Tech Stack and Design Rationale

| Concern              | Technology                         | Reason                                                                                           |
|----------------------|------------------------------------|--------------------------------------------------------------------------------------------------|
| Web framework        | FastAPI                            | Native async, Pydantic v2 validation, automatic OpenAPI documentation                           |
| ORM                  | SQLAlchemy 2.0 (async)             | async with session, bulk insert support, no N+1 query traps                                     |
| Database             | PostgreSQL 16                      | ACID compliance, NUMERIC exact type, FOR UPDATE row locking, partial indexes, CTEs              |
| Monetary type        | NUMERIC(18,8)                      | Exact fixed-point arithmetic — IEEE 754 float is categorically banned for financial amounts     |
| Ledger model         | Double-entry (Square Books)        | Every financial event produces two rows with the same transaction_id that sum to zero           |
| Idempotency          | DB-persisted idempotency key table | Brandur/Stripe pattern — survives server restarts, prevents duplicate processing on retry       |
| Background scheduler | APScheduler with CronTrigger       | In-process scheduler; can be replaced with Celery Beat at higher scale with no code changes     |
| Retry logic          | Tenacity                           | Exponential back-off, per-user batch isolation — one user's failure does not block others      |
| Structured logging   | structlog (JSON output)            | Machine-parseable, trace ID compatible, integrates with Datadog and CloudWatch                  |
| Metrics              | prometheus-fastapi-instrumentator  | Exposes /metrics endpoint for Grafana dashboards and alerting                                   |
| Migrations           | Alembic                            | Version-controlled schema changes, CI-safe alembic upgrade head                                 |
| Containers           | Docker + Docker Compose            | Development environment parity, single-command startup                                          |
| Testing              | pytest-asyncio + httpx             | Full async test support with ASGI transport, no mock server required                            |
| Connection pooling   | NullPool (tests), AsyncAdaptedQueuePool (prod) | NullPool forces fresh connections per test, eliminating asyncpg connection reuse errors |

---

## 4. Double-Entry Accounting Theory

Double-entry accounting is a 700-year-old bookkeeping principle stating that every financial transaction affects at least two accounts, and the total debits must always equal the total credits. In software, this translates to writing exactly two ledger rows for every financial event, where the sum of amounts is always zero.

This system references the Square Books model, which adds three critical properties on top of basic double-entry:

**Immutability.** Ledger rows are never updated or deleted. Every correction is a new entry that reverses the original. This creates an append-only audit trail.

**Balance derived from ledger.** No account stores a mutable balance column. The current balance is always computed as SUM(amount) WHERE account_id = X. A pre-computed mutable balance is a data integrity trap.

**Sign convention.** DEBIT entries carry a positive amount. CREDIT entries carry a negative amount. The net of any transaction is always zero.

### Token Win Event (user wins 3 tokens)

```
transaction_id = txn-abc

DEBIT   USER_TOKEN_WALLET    +3.00000000   (user receives tokens)
CREDIT  TOKEN_ISSUANCE       -3.00000000   (tokens are issued from the pool)

SUM = 0.00000000  (invariant holds)
```

### Hourly Conversion Event (3 tokens converted at $0.15)

```
transaction_id = txn-def

DEBIT   CONVERSION_POOL      +0.45000000   (USD leaves conversion pool)
CREDIT  USER_USD_WALLET      -0.45000000   (user receives USD)

SUM = 0.00000000  (invariant holds)
```

### Fee Event (Dreamland pays 2% fee = $0.009)

```
transaction_id = txn-fee

DEBIT   DREAMLAND_FEE_EXP    +0.00900000   (Dreamland's expense)
CREDIT  FEE_PAYABLE          -0.00900000   (liability created)

SUM = 0.00000000  (invariant holds)
```

### Why NUMERIC(18,8) and Not FLOAT

IEEE 754 floating-point arithmetic cannot represent most decimal fractions exactly. The expression 0.1 + 0.2 evaluates to 0.30000000000000004 in Python, JavaScript, and most languages. Over millions of ledger rows, float rounding errors compound into real dollar discrepancies that cannot be audited. PostgreSQL's NUMERIC type is exact fixed-point arithmetic. 0.1 + 0.2 is always 0.3. With 18 digits of precision and 8 decimal places, the maximum representable amount is $9,999,999,999.99999999 — well beyond any practical requirement.

---

## 5. Database Schema Design

### Table: users

Stores authenticated user records. The `timezone` field (IANA format, e.g. Asia/Kolkata) is used for timezone-aware daily cap calculations. The `region` field (eu, us, apac, global) drives data residency routing and is immutable after creation.

```sql
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id VARCHAR(128) NOT NULL UNIQUE,
    username    VARCHAR(80)  NOT NULL UNIQUE,
    email       VARCHAR(200) NOT NULL UNIQUE,
    timezone    VARCHAR(60)  NOT NULL DEFAULT 'UTC',
    region      VARCHAR(30)  NOT NULL DEFAULT 'global',
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
```

### Table: accounts (Chart of Accounts)

Each user has personal accounts (USER_TOKEN_WALLET, USER_USD_WALLET). System-level accounts (TOKEN_ISSUANCE, CONVERSION_POOL, FEE_PAYABLE, DREAMLAND_FEE_EXP) have NULL user_id. This separation makes it possible to query any account's balance independently.

```sql
CREATE TABLE accounts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID         REFERENCES users(id),
    code         account_code_enum NOT NULL,
    account_type account_type_enum NOT NULL,
    name         VARCHAR(120) NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
```

### Table: token_ledger

Every token win produces exactly two rows in this table with the same `transaction_id`. The sign check constraint is enforced at the database level, not just the application level. The partial index on `is_converted = FALSE` ensures the conversion job query remains fast regardless of total historical row count.

```sql
CREATE TABLE token_ledger (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id    UUID         NOT NULL,
    account_id        UUID         NOT NULL REFERENCES accounts(id),
    entry_type        entry_type_enum NOT NULL,
    amount            NUMERIC(18,8) NOT NULL,
    idempotency_key   VARCHAR(128),
    won_at            TIMESTAMPTZ  NOT NULL,
    is_converted      BOOLEAN      NOT NULL DEFAULT FALSE,
    conversion_job_id UUID         REFERENCES conversion_jobs(id),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_token_amount_sign CHECK (
        (entry_type = 'DEBIT'  AND amount > 0) OR
        (entry_type = 'CREDIT' AND amount < 0)
    )
);

CREATE UNIQUE INDEX uq_token_ledger_idempotency
    ON token_ledger(account_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX idx_token_ledger_convert ON token_ledger(is_converted, won_at)
    WHERE is_converted = FALSE;
```

### Table: usd_ledger

Every token-to-USD conversion produces two rows here. The `source_token_transaction_id` column provides a full cross-reference back to the originating token ledger entry for audit purposes.

```sql
CREATE TABLE usd_ledger (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id              UUID         NOT NULL,
    account_id                  UUID         NOT NULL REFERENCES accounts(id),
    entry_type                  entry_type_enum NOT NULL,
    amount                      NUMERIC(18,8) NOT NULL,
    source_token_transaction_id UUID,
    conversion_job_id           UUID    REFERENCES conversion_jobs(id),
    converted_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_usd_amount_sign CHECK (
        (entry_type = 'DEBIT'  AND amount > 0) OR
        (entry_type = 'CREDIT' AND amount < 0)
    )
);
```

### Table: conversion_jobs

One row per clock hour. The UNIQUE constraint on `hour_bucket` acts as a distributed mutex — only one job can exist per hour. The status state machine is: PENDING -> RUNNING -> COMPLETED (or FAILED -> RETRYING -> COMPLETED).

```sql
CREATE TABLE conversion_jobs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hour_bucket       TIMESTAMPTZ NOT NULL UNIQUE,
    status            job_status_enum NOT NULL DEFAULT 'PENDING',
    token_rate_usd    NUMERIC(18,8) NOT NULL,
    entries_processed INT          NOT NULL DEFAULT 0,
    usd_total         NUMERIC(18,8),
    fee_total         NUMERIC(18,8),
    retry_count       INT          NOT NULL DEFAULT 0,
    error_message     VARCHAR(500),
    started_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
```

### Table: idempotency_keys

Stores the full request payload and response for every win request. When a client retries with the same idempotency key, the server returns the cached response without re-processing. This is the Stripe/Brandur pattern.

```sql
CREATE TABLE idempotency_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key             VARCHAR(128)  NOT NULL,
    user_id         UUID          NOT NULL REFERENCES users(id),
    request_path    VARCHAR(200)  NOT NULL,
    request_params  TEXT          NOT NULL,
    response_code   VARCHAR(10),
    response_body   TEXT,
    locked_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE UNIQUE INDEX uq_idempotency_user_key ON idempotency_keys(user_id, key);
```

---

## 6. API Design and Implementation

All APIs are implemented in FastAPI with async SQLAlchemy. All monetary fields in responses are serialised as strings (not floats) using Pydantic's `Decimal` type to prevent any precision loss during JSON serialisation.

### POST /api/v1/tokens/win

Accepts a token win event. Enforces the daily cap in the user's local timezone. Writes two ledger entries atomically. Records the idempotency key with the full response for replay safety.

**Request body:**
```json
{
  "user_id": "uuid",
  "amount": "2",
  "won_at": "2026-04-23T12:00:00+05:30",
  "idempotency_key": "client-generated-unique-string"
}
```

**Response (201 Created):**
```json
{
  "transaction_id": "uuid",
  "tokens_awarded": "2",
  "tokens_won_today": "2",
  "tokens_remaining_today": "3",
  "message": "Tokens awarded successfully"
}
```

**Validation rules enforced:**
- `amount` must be a positive integer (no fractions)
- `amount` must not exceed remaining daily cap
- `won_at` must be timezone-aware (naive datetimes are rejected with 422)
- `user_id` must correspond to an active user record
- `idempotency_key` must be at least 8 characters

**Error responses:**
- 404 if user does not exist
- 403 if user is inactive
- 422 if daily cap is exceeded or request is malformed
- 409 if idempotency key conflict is detected

### GET /api/v1/tokens/history

Returns all DEBIT entries from the token ledger for the current calendar day in the user's local timezone.

**Query parameter:** `user_id`

**Response (200 OK):**
```json
{
  "user_id": "uuid",
  "date": "2026-04-23",
  "total_tokens_today": "3",
  "tokens_remaining_today": "2",
  "entries": [
    {
      "transaction_id": "uuid",
      "amount": "2",
      "won_at": "2026-04-23T12:00:00+05:30",
      "is_converted": false
    }
  ]
}
```

### GET /api/v1/usd/history

Returns all USD ledger DEBIT entries for the user's USD wallet from all time prior to the current day (previous conversions only).

### GET /api/v1/stats

Returns a combined summary: total tokens won today, tokens remaining today, and total USD balance in the user's wallet (derived from SUM of USD ledger entries, never from a stored column).

---

## 7. Hourly Token Conversion Job

The conversion job runs at minute 0 of every hour via APScheduler's CronTrigger. It is designed to be fully idempotent — running it twice for the same hour is safe.

### Job Execution Flow

```
1. APScheduler fires at HH:00:00 UTC

2. UPSERT into conversion_jobs WHERE hour_bucket = previous_hour
   ON CONFLICT: only update if status != COMPLETED
   If status = COMPLETED: exit immediately (idempotent guard)

3. SELECT token_ledger WHERE entry_type = DEBIT
   AND is_converted = FALSE
   AND won_at BETWEEN target_hour AND target_hour + 1h

4. GROUP results by account_id (user wallet)

5. For each user batch (retried up to 3x with tenacity exponential back-off):

   a. gross_usd = SUM(token_amounts) * 0.15000000
   b. fee_usd   = gross_usd * 0.02

   c. Write USD pair:
      DEBIT   CONVERSION_POOL   +gross_usd
      CREDIT  USER_USD_WALLET   -gross_usd

   d. Write fee pair:
      DEBIT   DREAMLAND_FEE_EXP  +fee_usd
      CREDIT  FEE_PAYABLE        -fee_usd

   e. UPDATE token_ledger SET is_converted = TRUE
      WHERE id IN (batch_ids)

6. UPDATE conversion_jobs SET status = COMPLETED
   usd_total = ..., fee_total = ..., completed_at = NOW()
```

### Key Design Properties

**Per-user batch isolation.** Each user's conversion is processed in its own transaction. If one user's batch fails due to a transient DB error, other users' batches are not affected. Failed entries remain `is_converted = FALSE` and are retried.

**Partial index performance.** The query `WHERE is_converted = FALSE` uses a partial index that only indexes unconverted rows. As rows are marked converted, they leave the index. The job query is always O(pending rows), not O(all historical rows).

**Distributed mutex via UNIQUE constraint.** The `UNIQUE` on `conversion_jobs.hour_bucket` combined with `ON CONFLICT DO UPDATE WHERE status != COMPLETED` ensures that even in a multi-instance deployment, only one conversion job row exists per hour, and a completed job is never overwritten.

**APScheduler coalesce=True.** If the server was down for multiple hours, the job runs once on restart rather than firing once per missed hour.

---

## 8. Edge Cases

### API Edge Cases

- Win amount of 0 or negative: rejected at Pydantic schema layer with 422
- Fractional token amount (e.g. 1.5): rejected by custom field_validator; tokens are integers only
- Win amount greater than remaining daily cap: rejected with 422 and exact remaining count in the error message
- `won_at` with no timezone: rejected with 422; the system requires timezone-aware ISO-8601 timestamps
- `won_at` in the future: should be rejected with a configurable clock-skew tolerance of ~60 seconds
- Same idempotency key with different amount: returns the cached response from the first request; the new amount is not processed
- Missing idempotency key: the field is required; the request is rejected with 422
- User not found: 404 returned before any database writes
- Inactive user: 403 returned; tokens are never awarded to suspended accounts

### Database Edge Cases

- Two concurrent requests with the same idempotency key: the UNIQUE INDEX on `(user_id, key)` causes one to receive a DB unique constraint violation, surfaced as 409 Conflict
- Sign constraint violation by application bug: the CHECK CONSTRAINT at the DB level (`chk_token_amount_sign`) prevents wrong-sign inserts as a last line of defence
- Ledger row modification: there are no UPDATE or DELETE operations on ledger tables by design; a database trigger can be added for additional enforcement in production
- NUMERIC overflow: the 18-digit precision supports up to $9.9 billion per entry, which is unreachable in this domain
- `won_at` timezone handling: all timestamps stored as TIMESTAMPTZ (always UTC internally); timezone conversion happens only at the query boundary using the user's IANA timezone from the users table

### Concurrency Edge Cases

- Two requests racing for the last daily token: both read `tokens_today = 4`, both pass the cap check, both succeed — resulting in 6 tokens. Resolution: use `SELECT ... FOR UPDATE` on the user's token wallet account row, or a PostgreSQL advisory lock keyed on `user_id`.
- Two conversion jobs for the same hour in a multi-instance deployment: the UNIQUE constraint on `hour_bucket` ensures only one job row exists. The `ON CONFLICT DO UPDATE WHERE status NOT IN (COMPLETED)` pattern means a completed job is never restarted.
- Conversion job crashes mid-batch: on restart, the job selects only entries where `is_converted = FALSE`. Completed entries are not reprocessed. The job status is reset from `RUNNING` to `RETRYING` on the next attempt.

### Infrastructure Edge Cases

- Clock skew between application servers: all times stored as TIMESTAMPTZ (UTC); `won_at` is client-provided and should be validated against `server_time + 60s` as a skew buffer
- Database connection pool exhaustion: `pool_pre_ping=True` detects stale connections; `max_overflow=20` allows burst capacity; the product of Gunicorn workers times pool_size must not exceed Postgres `max_connections`
- Database failover during a conversion job: tenacity retries at the batch level; the job-level distributed mutex prevents a second job from starting until the retry resolves
- Timezone edge case at midnight: a user in UTC+14 (Line Islands) and a user in UTC-12 are on different calendar days simultaneously; daily cap must always be computed against the user's own IANA timezone, not UTC

---

## 9. Infrastructure Design

### Multi-Region Deployment (AWS)

```
Route 53 Latency Routing
(routes each user to nearest healthy region)
          |
    +-----+-----+
    |             |
EU-WEST-1     AP-SOUTH-1
(Frankfurt)   (Mumbai)
    |             |
ALB -> ECS    ALB -> ECS
FastAPI        FastAPI
    |             |
Aurora PG     Aurora PG
Multi-AZ      Multi-AZ
+ 2 replicas  + 2 replicas
    |             |
    +------+------+
           |
    US-EAST-1 (analytics only)
    Debezium -> MSK (Kafka)
    -> S3 -> Glue -> Snowflake
```

### GDPR Data Residency

- EU users are identified at signup via GeoIP lookup and assigned `users.region = 'eu'`
- The `region` field is immutable after creation — it cannot be changed by the user or the API
- Route 53 Geolocation Routing and CloudFront Origin Selection ensure EU requests are never routed to a non-EU write endpoint
- Personal data (name, email) exists only in the regional database
- The central analytics cluster receives only anonymised records: user UUIDs and financial aggregates, never PII
- GDPR right to erasure is handled by soft-deleting the user row and nullifying PII columns; ledger rows are retained with only the UUID, satisfying both the right to erasure and legal financial record retention requirements

### CDC to Central Analytics

The analytics pipeline uses Debezium to read from PostgreSQL's Write-Ahead Log (WAL) via a logical replication slot. Change events are published to Apache Kafka (Amazon MSK). A Kafka Connect S3 sink writes to a raw landing zone. AWS Glue or dbt transforms the data into Snowflake.

Tables replicated to analytics: `token_ledger`, `usd_ledger`, `conversion_jobs`, `accounts`

Tables never replicated: `users` (contains PII), `idempotency_keys`

### Cross-Region Read and Write Routing

- Writes always go to the user's home region's primary Aurora writer
- Read endpoints (`/tokens/history`, `/usd/history`, `/stats`) route to the local read replica in the same region, accepting typical replica lag of under 100ms
- For strong-consistency reads (cap enforcement before a win), queries are routed to the writer
- Aurora Global Database can be used for sub-second cross-region replication if required

---

## 10. Project Structure

```
dreamland/
├── app/
│   ├── main.py                     FastAPI application, lifespan, middleware
│   ├── core/
│   │   ├── config.py               pydantic-settings, environment configuration
│   │   └── logging.py              structlog JSON structured logging setup
│   ├── db/
│   │   └── engine.py               Async SQLAlchemy engine, get_db dependency
│   ├── models/
│   │   ├── base.py                 SQLAlchemy DeclarativeBase
│   │   ├── users.py                User table model
│   │   ├── accounts.py             Chart of Accounts, AccountCode enum
│   │   ├── ledger.py               TokenLedgerEntry, UsdLedgerEntry, IdempotencyKey
│   │   └── conversion_jobs.py      ConversionJob, JobStatus
│   ├── schemas/
│   │   ├── tokens.py               WinTokenRequest, WinTokenResponse, TokenHistoryResponse
│   │   ├── usd.py                  UsdHistoryResponse
│   │   └── stats.py                StatsResponse
│   ├── services/
│   │   ├── account_service.py      get_or_create_user and system account logic
│   │   ├── token_service.py        win_tokens(), get_token_history()
│   │   └── usd_service.py          get_usd_history()
│   ├── api/
│   │   ├── tokens.py               POST /tokens/win, GET /tokens/history
│   │   ├── usd.py                  GET /usd/history
│   │   └── stats.py                GET /stats
│   ├── jobs/
│   │   └── conversion_job.py       APScheduler hourly job with tenacity retry logic
│   └── utils/
│       └── time_utils.py           Timezone-aware day boundary calculations
├── alembic/                        Database migrations
│   ├── versions/
│   └── env.py
├── tests/
│   └── test_tokens.py              15 async pytest tests
├── docker-compose.yml              PostgreSQL + app services
├── Dockerfile
├── requirements.txt
├── alembic.ini
├── pytest.ini
└── .env.example
```

---

## 11. Local Development Setup

### Prerequisites

- Python 3.10 or higher
- PostgreSQL 14 or higher (or Docker)
- psycopg2-binary (for sync test infrastructure)
- asyncpg (for async application driver)

### Step 1: Create and activate a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### Step 2: Install dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` with your local database credentials:

```
DATABASE_URL=postgresql+asyncpg://dreamland:secret@localhost:5432/dreamland
DATABASE_URL_SYNC=postgresql+psycopg2://dreamland:secret@localhost:5432/dreamland
DREAM_TOKEN_RATE_USD=0.15
DREAM_TOKEN_DAILY_CAP=5
ENVIRONMENT=development
```

### Step 4: Start PostgreSQL with Docker

```bash
docker-compose up db -d
```

Or if using a local PostgreSQL instance, create the database manually:

```sql
CREATE DATABASE dreamland;
CREATE DATABASE dreamland_test;
CREATE USER dreamland WITH PASSWORD 'secret';
GRANT ALL PRIVILEGES ON DATABASE dreamland TO dreamland;
GRANT ALL PRIVILEGES ON DATABASE dreamland_test TO dreamland;
```

---

## 12. Running Migrations

```bash
# Apply all migrations
alembic upgrade head

# Create a new migration after model changes
alembic revision --autogenerate -m "describe change"

# Downgrade one step
alembic downgrade -1
```

---

## 13. Running the Server

### Development

```bash
uvicorn app.main:app --reload --port 8000
```

### Production (via Docker Compose)

```bash
docker-compose up --build
```

### API Documentation

Once the server is running:

- Interactive Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON schema: `http://localhost:8000/openapi.json`
- Prometheus metrics: `http://localhost:8000/metrics`
- Health check: `http://localhost:8000/health`

---

## 14. Test Suite

### Overview

The test suite contains 15 async integration tests that run against a real PostgreSQL test database (`dreamland_test`). There are no mocks for the database layer — every test exercises the full stack from HTTP request to database write and read.

### Testing Architecture

**NullPool for connection isolation.** The test engine uses SQLAlchemy's `NullPool`, which forces asyncpg to open a brand-new TCP connection for every test and close it when the test ends. This eliminates the `InterfaceError: cannot perform operation: another operation is in progress` error that occurs when the default connection pool hands the same physical connection to multiple async contexts simultaneously.

**Sync DDL for table management.** Table creation and truncation use psycopg2 (synchronous) rather than asyncpg. This keeps the DDL lifecycle completely separate from the async application layer, preventing any connection state conflicts during fixture setup and teardown.

**Per-test truncation, not per-test drop/create.** Tables are created once at session scope and truncated before each individual test using `TRUNCATE ... RESTART IDENTITY CASCADE`. This is significantly faster than dropping and recreating tables for each test while still guaranteeing complete data isolation.

**Full dependency override.** The FastAPI `get_db` dependency is overridden for each test to use the test session factory, ensuring the application code runs against the test database without any modifications.

### Test Configuration

`pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

`asyncio_mode = auto` instructs pytest-asyncio to treat every `async def test_` function as an asyncio coroutine automatically, without requiring explicit `@pytest.mark.asyncio` decorators on every test.

### Test Descriptions

| Test | Class | What It Validates |
|------|-------|-------------------|
| test_happy_path_returns_201 | TestWinTokens | Core win flow, response shape, field values |
| test_double_entry_invariant_net_zero | TestWinTokens | Exactly 2 ledger rows with same transaction_id; SUM = 0 |
| test_daily_cap_enforced | TestWinTokens | 5th token allowed; 6th token rejected with 422 |
| test_cap_exceeded_by_amount | TestWinTokens | Request that would breach cap in one shot is rejected |
| test_idempotency_same_key_same_response | TestWinTokens | Replay returns same transaction_id; ledger has exactly 2 rows, not 4 |
| test_fractional_tokens_rejected | TestWinTokens | 1.5 tokens rejected at schema validation layer |
| test_naive_datetime_rejected | TestWinTokens | won_at without timezone offset is rejected |
| test_nonexistent_user_returns_404 | TestWinTokens | Unknown user_id returns 404 before any DB writes |
| test_zero_amount_rejected | TestWinTokens | amount=0 is rejected at schema layer |
| test_amount_over_5_rejected | TestWinTokens | amount=6 is rejected (exceeds single-request limit) |
| test_history_returns_todays_entries | TestTokenHistory | Returns correct entries and total for today |
| test_history_empty_for_new_user | TestTokenHistory | New user returns empty list and total of 0 |
| test_history_404_unknown_user | TestTokenHistory | Unknown user_id returns 404 |
| test_stats_correct_after_wins | TestStats | Aggregation correct; USD balance is 0 before conversion |
| test_stats_404_unknown_user | TestStats | Unknown user_id returns 404 |

### Running Tests

```bash
# Run all tests with verbose output
pytest tests/ -v

# Run a specific test class
pytest tests/ -v -k TestWinTokens

# Run a single test
pytest tests/ -v -k test_double_entry_invariant_net_zero

# Run with coverage report
pytest tests/ -v --cov=app --cov-report=term-missing
```

---

## 15. Test Results

All 15 tests pass against a live PostgreSQL test database.

```
================================= test session starts =================================
platform win32 -- Python 3.10.0, pytest-8.1.1, pluggy-1.6.0
asyncio: mode=auto
collected 15 items

tests/test_tokens.py::TestWinTokens::test_happy_path_returns_201               PASSED
tests/test_tokens.py::TestWinTokens::test_double_entry_invariant_net_zero       PASSED
tests/test_tokens.py::TestWinTokens::test_daily_cap_enforced                   PASSED
tests/test_tokens.py::TestWinTokens::test_cap_exceeded_by_amount               PASSED
tests/test_tokens.py::TestWinTokens::test_idempotency_same_key_same_response   PASSED
tests/test_tokens.py::TestWinTokens::test_fractional_tokens_rejected           PASSED
tests/test_tokens.py::TestWinTokens::test_naive_datetime_rejected              PASSED
tests/test_tokens.py::TestWinTokens::test_nonexistent_user_returns_404         PASSED
tests/test_tokens.py::TestWinTokens::test_zero_amount_rejected                 PASSED
tests/test_tokens.py::TestWinTokens::test_amount_over_5_rejected               PASSED
tests/test_tokens.py::TestTokenHistory::test_history_returns_todays_entries    PASSED
tests/test_tokens.py::TestTokenHistory::test_history_empty_for_new_user        PASSED
tests/test_tokens.py::TestTokenHistory::test_history_404_unknown_user          PASSED
tests/test_tokens.py::TestStats::test_stats_correct_after_wins                 PASSED
tests/test_tokens.py::TestStats::test_stats_404_unknown_user                   PASSED

========================== 15 passed, 16 warnings in 11.88s ===========================
```

### Issues Encountered and Resolved During Testing

**Issue 1: asyncpg InterfaceError — cannot perform operation: another operation is in progress**

Root cause: The default SQLAlchemy `AsyncAdaptedQueuePool` handed the same physical TCP connection to multiple async contexts simultaneously — once to the test's direct `db_session()` call and once to the app's `override_get_db` dependency. When both attempted to start a transaction on the same connection, asyncpg raised an InterfaceError.

Resolution: Switched to `NullPool`, which opens a new TCP connection per session and closes it when the session ends. This is the recommended pattern for async SQLAlchemy test suites.

**Issue 2: Short idempotency keys rejected**

Root cause: The `WinTokenRequest` schema enforces a minimum length of 8 characters on the `idempotency_key` field. Test helper was using 2-3 character keys like `"k-a"`.

Resolution: Updated all test fixture keys to 8+ character strings such as `"key-alpha"`, `"key-bravo"`.

**Issue 3: History and stats returning 0 after successful wins**

Root cause: The default `won_at` timestamp in the test helper was `2026-04-23T02:30:00+05:30`, which converts to `2026-04-22T21:00:00 UTC` — the previous calendar day in UTC. The history query filtered by the current UTC day, so the entries were invisible to the read APIs.

Resolution: Changed the default `won_at` to `2026-04-23T12:00:00+05:30` (noon IST = 06:30 UTC), which unambiguously falls within today's date in both IST and UTC.

**Issue 4: Daily cap test assertion mismatch**

Root cause: The test asserted `"remaining" in r.json()["detail"].lower()`, but the application returns `"Daily token cap of 5 reached for today."` which does not contain the word "remaining".

Resolution: Updated the assertion to `"cap" in r.json()["detail"].lower()` to match the actual error message.

---

## 16. Additional APIs and Tooling

Beyond the four required APIs, the following internal tools would be added in a production system:

### Admin APIs (internal, protected by API key)

- `POST /admin/conversion/trigger` — manually trigger a conversion job for a specific hour bucket; used during incident recovery
- `GET /admin/conversion/jobs` — list conversion jobs with status, entries processed, USD total, and fee total; used for on-call debugging
- `GET /admin/users/{user_id}/ledger` — full double-entry ledger view for a user across both token and USD accounts; used for customer support investigations
- `POST /admin/users/{user_id}/reverse` — create a reversing entry in the ledger; used for dispute resolution without violating immutability

### Monitoring and Alerting

Prometheus metrics (via `/metrics`) with Grafana dashboards covering:

- Conversion job execution duration and status
- Token win rate per minute
- Daily cap breach rate
- USD total converted per hour
- Idempotency key replay rate
- Database connection pool utilisation

Alerts should fire on: conversion job failure, job duration exceeding 5 minutes, daily cap breach rate above threshold (potential fraud signal), error rate on win endpoint above 1%.

### Background Jobs

- **Daily reconciliation job** — runs at 00:05 UTC daily; verifies that the sum of all token ledger debits equals the sum of all token ledger credits across all accounts; alerts on any discrepancy
- **Stale lock cleanup job** — removes idempotency key locks older than 24 hours that were never completed; prevents lock table growth

---

## 17. Reference Notes

The following points represent the most important design decisions in this system, in order of expected interview relevance:

1. Every financial event creates exactly two ledger rows with the same `transaction_id`. Their amounts sum to zero. This is the Square Books double-entry invariant. Ledger rows are never updated or deleted.

2. Balance is never stored as a mutable column. It is always derived as `SUM(amount) WHERE account_id = X`. A stored balance can become inconsistent under concurrent writes; a derived balance cannot.

3. `NUMERIC(18,8)` is used for all monetary amounts. `FLOAT` and `DOUBLE` are categorically banned. `0.1 + 0.2 = 0.30000000000000004` in IEEE 754. PostgreSQL `NUMERIC` is always exact.

4. The sign invariant is enforced both in application code and at the database level via `CHECK CONSTRAINT`. Application bugs cannot violate the ledger integrity at the storage layer.

5. The win endpoint requires an idempotency key. The full request parameters and response are stored in the `idempotency_keys` table. Replays return the cached response without reprocessing. This is the Stripe pattern and handles mobile client network retries safely.

6. Daily cap is enforced in the user's local timezone, not UTC. A user in UTC+5:30 playing at 23:30 local time is on a different calendar day than UTC. Incorrect UTC-based cap enforcement would give some users fewer than 5 tokens per day.

7. Concurrent cap enforcement requires `SELECT ... FOR UPDATE` on the user's token wallet account row or a PostgreSQL advisory lock keyed on `user_id`. Without this, two simultaneous requests can both read `tokens_today = 4` and both succeed.

8. The partial index `WHERE is_converted = FALSE` on the token ledger ensures the conversion job query remains fast forever. As rows are converted, they leave the index. The query is O(unconverted rows), not O(all historical rows).

9. The conversion job uses `INSERT INTO conversion_jobs ON CONFLICT DO UPDATE WHERE status != COMPLETED` as a distributed mutex. In a multi-instance deployment, this ensures only one job processes each hour and a completed job cannot be overwritten.

10. Per-user batch isolation in the conversion job means one user's transient DB error does not fail other users' conversions. Failed entries remain `is_converted = FALSE` and are retried.

11. EU user data never leaves `eu-west-1`. The `users.region` field is set at signup from GeoIP and is immutable. Route 53 Geolocation Routing enforces this at the network layer. The analytics cluster receives only anonymised UUIDs — no PII.

12. CDC to analytics via Debezium reads from PostgreSQL's WAL. The `users` table is never replicated. Only anonymised ledger data flows to the central analytics cluster.

13. Read endpoints use Aurora read replicas. Write operations and cap-enforcement reads go to the primary writer. This is acceptable eventual consistency — replica lag is typically under 100ms.

14. APScheduler's `coalesce=True` ensures if the server was down for three hours, the job runs once on restart, not three times.

15. The test suite uses `NullPool` to force a fresh asyncpg TCP connection per test. This is the only correct pattern for async SQLAlchemy tests — the default connection pool causes `InterfaceError: another operation is in progress` when multiple async contexts share a connection.

---

## License

This project was built as a technical interview exercise for Sei (AI company) and is not licensed for commercial use.
