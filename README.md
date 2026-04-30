# Dreamland Backend System

## Overview

Dreamland is a backend service for an AI + metaverse gaming platform where users play games against AI agents and earn **DREAM tokens** multiple times per day. The system enforces a **5-token daily cap per user**, converts tokens to **USD hourly at a fixed rate of $0.15/token**, and tracks all financial events using **double-entry accounting ledgers**.

Users are charged **zero fees**. Instead, **Dreamland bears all conversion fees** (~2% per transaction) and tracks them separately in the ledger for accounting purposes.

This implementation demonstrates production-grade financial backend design: idempotent APIs, timezone-aware business logic, ACID compliance, distributed job scheduling, and comprehensive test coverage.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Architecture Overview](#2-architecture-overview)
3. [Tech Stack](#3-tech-stack)
4. [Database Design](#4-database-design)
5. [API Implementation](#5-api-implementation)
6. [Hourly Conversion Job](#6-hourly-conversion-job)
7. [Edge Cases & Handling](#7-edge-cases--handling)
8. [Testing](#8-testing)
9. [Setup & Running](#9-setup--running)
10. [Infrastructure & Deployment](#10-infrastructure--deployment)
11. [Key Design Decisions](#11-key-design-decisions)

---

## 1. Problem Statement

**Business Requirements:**
- Users play games throughout the day and earn DREAM tokens multiple times
- Hard daily cap: **5 tokens per user per calendar day** (enforced in user's local timezone, not UTC)
- Tokens carry real monetary value: fixed conversion rate of **$0.15 USD per token**
- Automatic hourly conversion: all unconverted tokens → USD at the end of each clock hour
- Fee tracking: Dreamland pays ~2% conversion fee (not charged to users)

**Technical Constraints:**
- Must track all financial events using **double-entry accounting** (tokens and USD separately)
- API must be **idempotent** — clients can retry without creating duplicate ledger entries
- Daily cap must respect **user timezone** — a user in Tokyo at 23:30 JST is on a different day than UTC
- Ledger must be **immutable** — no updates or deletes, only appends (audit trail)
- Monetary calculations must use **exact arithmetic** (no floating-point errors)

**Requirements by API:**
1. Accept token win events from the game layer
2. Return token history for the current calendar day
3. Return USD conversion history (prior days only)
4. Return combined stats (tokens today + total USD balance + remaining cap)

---

## 2. Architecture Overview

```
Game Client
    |
    v
FastAPI Application (async request handlers)
    |
    +-- /api/v1/tokens/win       (POST)   → Award tokens, enforce daily cap
    +-- /api/v1/tokens/history   (GET)    → Today's token wins
    +-- /api/v1/usd/history      (GET)    → Prior USD conversions
    +-- /api/v1/stats            (GET)    → Aggregated summary
    +-- /api/v1/admin/overview   (GET)    → Owner overview metrics
    +-- /api/v1/admin/users      (GET)    → Owner user list + search
    +-- /api/v1/dev/seed-user    (POST)   → Dev-only user creation
    |
    v
PostgreSQL Database
    |
    +-- users                (user profiles, timezone, region)
    +-- accounts             (chart of accounts for double-entry ledger)
    +-- token_ledger         (all token wins and conversions)
    +-- usd_ledger           (all USD conversions and fees)
    +-- conversion_jobs      (hourly job tracking)
    +-- idempotency_keys     (request deduplication)
    |
    v
APScheduler (in-process)
    |
    +-- Runs hourly at HH:00:00 UTC
    +-- Processes all unconverted tokens from the previous hour
    +-- Converts to USD (writes 6 ledger rows: 2 for token burn, 2 for conversion, 2 for fees)
    +-- Marks token entries as converted
```

**Design Philosophy:**
- **Idempotency**: All APIs are safe to retry. Clients can submit the same request multiple times and receive the same response without creating duplicate ledger entries.
- **Ledger Immutability**: Ledger rows are never updated or deleted. All corrections are new reversing entries, creating an append-only audit trail.
- **Timezone Awareness**: Daily cap is enforced in the user's local timezone, not UTC. All timestamps are stored as UTC internally; timezone conversion happens only at query boundaries.
- **Exact Arithmetic**: All monetary amounts use PostgreSQL's `NUMERIC(18,8)` type — never floats. This prevents IEEE 754 rounding errors from compounding across millions of transactions.

---

## 3. Tech Stack

| Component        | Technology              | Reason                                                                   |
|------------------|-------------------------|--------------------------------------------------------------------------|
| Framework        | FastAPI                 | Native async, Pydantic v2 validation, automatic OpenAPI docs            |
| Async ORM        | SQLAlchemy 2.0 (async)  | Async session, no N+1 queries, bulk operations                          |
| Database         | PostgreSQL 16           | ACID guarantees, NUMERIC type for exact arithmetic, CHECK constraints   |
| Task Scheduler   | APScheduler             | In-process hourly job, CronTrigger, coalesce for missed runs            |
| Retries          | Tenacity                | Exponential back-off, per-user isolation (one failure ≠ block others)   |
| Validation       | Pydantic v2             | Request/response validation, custom field validators                    |
| Logging          | structlog               | Structured JSON logging, compatible with log aggregators                |
| Metrics          | prometheus-fastapi      | /metrics endpoint for monitoring                                         |
| Migrations       | Alembic                 | Version-controlled schema migrations                                     |
| Testing          | pytest + pytest-asyncio | Full async test support, real database (not mocked)                     |

---

## 4. Database Design

### Double-Entry Accounting Ledger

The system uses the **Square Books** model of double-entry accounting:

**Core Rule:** Every financial transaction produces exactly **two ledger rows** with the same `transaction_id`. The amounts always sum to zero.

- **DEBIT** entries: positive amount (+)  
- **CREDIT** entries: negative amount (-)  
- **Net**: DEBIT + CREDIT = 0 (invariant)

**Why This Matters:** The ledger is self-auditing. Any transaction that violates the invariant is immediately detectable as a data integrity issue.

### Ledger Transactions

**Token Win (User wins 3 tokens):**
```
transaction_id: txn-abc
DEBIT   USER_TOKEN_WALLET   +3.00000000   (user receives tokens)
CREDIT  TOKEN_ISSUANCE      -3.00000000   (tokens issued from pool)
SUM = 0.00000000 ✓
```

**Hourly Conversion (3 tokens → $0.45 USD):**

*Pair 1: Token Burn*
```
transaction_id: txn-def
CREDIT  USER_TOKEN_WALLET   -3.00000000   (user loses tokens)
DEBIT   TOKEN_ISSUANCE      +3.00000000   (system liability reduced)
SUM = 0.00000000 ✓
```

*Pair 2: USD Conversion*
```
transaction_id: txn-ghi
CREDIT  CONVERSION_POOL     -0.45000000   (USD leaves pool)
DEBIT   USER_USD_WALLET     +0.45000000   (user receives USD)
SUM = 0.00000000 ✓
```

**Fee Tracking (Dreamland pays 2% = $0.009):**
```
transaction_id: txn-fee
DEBIT   DREAMLAND_FEE_EXP   +0.00900000   (Dreamland's expense)
CREDIT  FEE_PAYABLE         -0.00900000   (Dreamland's liability)
SUM = 0.00000000 ✓
```

### Why NUMERIC(18,8) Instead of FLOAT

IEEE 754 floating-point cannot represent most decimal fractions exactly:

```python
# BAD (in Python, JavaScript, most languages)
0.1 + 0.2  # → 0.30000000000000004 ❌

# GOOD (PostgreSQL NUMERIC)
0.1 + 0.2  # → 0.30000000 ✓
```

Over millions of ledger rows, float rounding errors compound into real dollar discrepancies. PostgreSQL's `NUMERIC(18,8)` is exact fixed-point arithmetic with 18 digits of precision and 8 decimal places (max: $9,999,999,999.99999999).

### Tables

#### `users` — User profiles
```sql
CREATE TABLE users (
    id          UUID PRIMARY KEY,
    external_id VARCHAR(128) NOT NULL UNIQUE,  -- External ID provider
    username    VARCHAR(80) NOT NULL UNIQUE,
    email       VARCHAR(200) NOT NULL UNIQUE,
    timezone    VARCHAR(60) NOT NULL DEFAULT 'UTC',  -- IANA timezone string
    region      VARCHAR(30) NOT NULL DEFAULT 'global',  -- Data residency
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `accounts` — Chart of Accounts
```sql
CREATE TABLE accounts (
    id           UUID PRIMARY KEY,
    user_id      UUID REFERENCES users(id),  -- NULL for system accounts
    code         account_code_enum NOT NULL,  -- e.g., USER_TOKEN_WALLET
    account_type account_type_enum NOT NULL,  -- ASSET, LIABILITY, etc.
    name         VARCHAR(120) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Account Codes:**
- `USER_TOKEN_WALLET` — User's DREAM token balance (per-user)
- `USER_USD_WALLET` — User's USD balance (per-user)
- `TOKEN_ISSUANCE` — Issued tokens pool (system-wide)
- `CONVERSION_POOL` — USD pool for conversions (system-wide)
- `FEE_PAYABLE` — Fees owed externally (system-wide)
- `DREAMLAND_FEE_EXP` — Fee expense (system-wide)

#### `token_ledger` — Token transactions (immutable)
```sql
CREATE TABLE token_ledger (
    id                UUID PRIMARY KEY,
    transaction_id    UUID NOT NULL,  -- Groups DEBIT + CREDIT pair
    account_id        UUID NOT NULL REFERENCES accounts(id),
    entry_type        entry_type_enum NOT NULL,  -- DEBIT | CREDIT
    amount            NUMERIC(18,8) NOT NULL,  -- Positive or negative
    idempotency_key   VARCHAR(128) UNIQUE,  -- Prevent duplicate wins
    won_at            TIMESTAMPTZ NOT NULL,  -- When win occurred (UTC)
    is_converted      BOOLEAN NOT NULL DEFAULT FALSE,  -- Converted to USD?
    conversion_job_id UUID REFERENCES conversion_jobs(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Enforce sign correctness at DB level
    CONSTRAINT chk_token_amount_sign CHECK (
        (entry_type = 'DEBIT'  AND amount > 0) OR
        (entry_type = 'CREDIT' AND amount < 0)
    )
);

-- Index for conversion job (only unconverted rows)
CREATE INDEX idx_token_ledger_unconverted ON token_ledger(is_converted, won_at)
WHERE is_converted = FALSE;
```

#### `usd_ledger` — USD conversion transactions (immutable)
```sql
CREATE TABLE usd_ledger (
    id                          UUID PRIMARY KEY,
    transaction_id              UUID NOT NULL,
    account_id                  UUID NOT NULL REFERENCES accounts(id),
    entry_type                  entry_type_enum NOT NULL,
    amount                      NUMERIC(18,8) NOT NULL,
    source_token_transaction_id UUID,  -- Cross-reference to token ledger
    conversion_job_id           UUID REFERENCES conversion_jobs(id),
    converted_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT chk_usd_amount_sign CHECK (
        (entry_type = 'DEBIT'  AND amount > 0) OR
        (entry_type = 'CREDIT' AND amount < 0)
    )
);
```

#### `conversion_jobs` — Hourly job tracking
```sql
CREATE TABLE conversion_jobs (
    id                UUID PRIMARY KEY,
    hour_bucket       TIMESTAMPTZ NOT NULL UNIQUE,  -- e.g., 2024-11-01T14:00:00Z
    status            job_status_enum NOT NULL,  -- PENDING | RUNNING | COMPLETED | FAILED | RETRYING
    token_rate_usd    NUMERIC(18,8) NOT NULL,  -- Fixed: $0.15
    entries_processed INT NOT NULL DEFAULT 0,
    usd_total         NUMERIC(18,8),
    fee_total         NUMERIC(18,8),
    retry_count       INT NOT NULL DEFAULT 0,
    error_message     VARCHAR(500),
    started_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Why `hour_bucket` is UNIQUE:** Acts as a distributed mutex. Only one job can exist per hour. Re-running for the same hour is safe — already-converted entries are skipped.

#### `idempotency_keys` — Request deduplication
```sql
CREATE TABLE idempotency_keys (
    id              UUID PRIMARY KEY,
    key             VARCHAR(128) NOT NULL,  -- Client-generated key
    user_id         UUID NOT NULL REFERENCES users(id),
    request_path    VARCHAR(200) NOT NULL,
    request_params  TEXT NOT NULL,
    response_code   INT,
    response_body   TEXT,  -- Cached response
    locked_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    
    UNIQUE(user_id, key)  -- One key per user
);
```

**Stripe/Brandur Pattern:** When a client retries with the same `idempotency_key`, the server returns the cached response from `response_body` without re-processing.

---

## 5. API Implementation

All APIs are async FastAPI endpoints with full Pydantic v2 validation. Monetary fields are returned as strings (not floats) to prevent JSON serialization precision loss.

### POST /api/v1/tokens/win

Awards DREAM tokens to a user with full double-entry accounting and idempotency support.

**Request:**
```json
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "amount": "2",
  "won_at": "2024-11-01T14:30:00+05:30",
  "idempotency_key": "client-unique-key-abc123"
}
```

**Response (201 Created):**
```json
{
  "transaction_id": "660e8400-e29b-41d4-a716-446655440111",
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "tokens_awarded": "2",
  "tokens_won_today": "2",
  "tokens_remaining_today": "3",
  "won_at": "2024-11-01T14:30:00+05:30",
  "message": "Tokens awarded successfully"
}
```

**Execution Flow:**
1. Check idempotency key → return cached response if already processed
2. Validate user exists and is active
3. Get/create user's token wallet account
4. Compute today's tokens (in user's local timezone)
5. Enforce daily cap (max 5 tokens/day)
6. Write DEBIT (user wallet) + CREDIT (token issuance) ledger pair
7. Persist idempotency key with response for replay safety

**Validation Rules:**
- `amount` must be a positive integer (1-5). Fractions rejected at Pydantic layer with 422.
- `won_at` must be timezone-aware. Naive datetimes rejected with 422.
- `amount` must not exceed remaining daily cap
- `idempotency_key` must be 8-128 characters
- `user_id` must exist and be active

**Error Responses:**
- 404: User not found
- 403: User is inactive
- 409: Idempotency key conflict (concurrent duplicate submission)
- 422: Validation failed (fractional amount, naive datetime, exceeds daily cap, etc.)

**Idempotency Guarantee:**
If a client retries with the same `idempotency_key`:
- The server returns the exact same response
- No additional ledger rows are created
- The operation is replayed from cache, not re-executed

---

### GET /api/v1/tokens/history

Returns all token wins for the current calendar day in the user's local timezone.

**Query Parameters:**
- `user_id` (UUID, required)

**Response (200 OK):**
```json
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "date": "2024-11-01",
  "entries": [
    {
      "transaction_id": "660e8400-e29b-41d4-a716-446655440111",
      "amount": "2",
      "won_at": "2024-11-01T14:30:00+05:30",
      "is_converted": false,
      "created_at": "2024-11-01T09:00:00Z"
    }
  ],
  "total_tokens_today": "2"
}
```

**Details:**
- Returns only DEBIT entries from the token ledger (user receives tokens)
- "Date" is in the user's local timezone (YYYY-MM-DD format)
- Includes only entries where `won_at` falls within today's local date boundaries
- `is_converted` flag indicates if this entry was processed by the hourly job

---

### GET /api/v1/usd/history

Returns USD conversion history for all prior days (excluding today).

**Query Parameters:**
- `user_id` (UUID, required)

**Response (200 OK):**
```json
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "entries": [
    {
      "transaction_id": "770e8400-e29b-41d4-a716-446655440222",
      "amount_usd": "0.30",
      "source_tokens": "2.00000000",
      "converted_at": "2024-10-31T15:00:00Z",
      "hour_bucket": "2024-10-31T14:00:00Z"
    }
  ],
  "total_usd_balance": "0.30"
}
```

**Details:**
- Returns only USD ledger DEBIT entries (user receives USD)
- Excludes entries from the current calendar day
- Amounts shown as positive (stored internally as negative credits)
- `total_usd_balance` is derived from SUM(amount) — never from a stored column

---

### GET /api/v1/stats

Returns aggregated summary: tokens won today, remaining tokens, and total USD balance.

**Query Parameters:**
- `user_id` (UUID, required)

**Response (200 OK):**
```json
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "tokens_won_today": "3",
  "total_usd_balance": "0.45",
  "tokens_remaining_today": "2"
}
```

**Calculation:**
- `tokens_won_today`: SUM of all DEBIT entries in token_ledger where date = today (user's timezone)
- `tokens_remaining_today`: 5 - tokens_won_today
- `total_usd_balance`: SUM of all DEBIT entries in usd_ledger (never from stored column)

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

   c. Write Token Burn pair:
      CREDIT  USER_TOKEN_WALLET  -total_tokens
      DEBIT   TOKEN_ISSUANCE     +total_tokens

   d. Write USD pair:
      CREDIT  CONVERSION_POOL    -gross_usd
      DEBIT   USER_USD_WALLET    +gross_usd

   e. Write fee pair:
      DEBIT   DREAMLAND_FEE_EXP  +fee_usd
      CREDIT  FEE_PAYABLE        -fee_usd

   f. UPDATE token_ledger SET is_converted = TRUE
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

## 7. Edge Cases & Handling

### API Input Validation

- **Zero/negative amounts**: Rejected at Pydantic validation layer with 422
- **Fractional tokens** (e.g., 1.5): Rejected by custom `field_validator` — tokens must be whole numbers
- **Amount > remaining cap**: Rejected with 422. Error includes exact remaining allowance.
- **Naive datetime** (no timezone): Rejected with 422. Requires ISO-8601 with timezone offset.
- **Missing idempotency key**: Required field. Request rejected with 422.
- **Short idempotency key** (<8 chars): Rejected. Minimum 8 characters enforced.

### User Validation

- **User not found**: Returns 404 before any database writes
- **Inactive user**: Returns 403. Tokens never awarded to suspended accounts.

### Concurrency Protection

- **Two requests racing for the last token**: Both read `tokens_today = 4`, both pass cap check, both succeed → **6 tokens earned (BUG)**
  - Solution: Use `SELECT ... FOR UPDATE` on user's token wallet account row, or PostgreSQL advisory locks
  - Current implementation: Idempotency key UNIQUE constraint prevents duplicate ledger entries, but does NOT prevent race condition on cap check

### Timezone Handling

- **User in UTC+14 (Line Islands) at 23:30 local**: Still on today's date in their timezone, but UTC is already tomorrow
- **Daily cap must use user's IANA timezone**, not UTC
- Example: User in Asia/Kolkata (UTC+5:30) at 23:30 IST is on tomorrow in UTC but still on today in their timezone

### Monetary Arithmetic

- **NUMERIC(18,8) prevents rounding errors**: All operations use exact fixed-point arithmetic
- **Sign constraint enforced at DB level**: CHECK CONSTRAINT prevents wrong-sign inserts even if application has bugs
- **Ledger immutability**: No UPDATE or DELETE on ledger tables. All corrections are new reversing entries.

### Idempotency

- **Same key, same response**: Client can retry indefinitely. Only the first submission creates ledger entries.
- **Full replay safety**: Request payload + response cached in `idempotency_keys` table

### Conversion Job Edge Cases

- **Server down for 3 hours**: APScheduler `coalesce=True` means the job runs once on restart, not three times
- **Job crashes mid-batch**: On restart, job queries only `is_converted = FALSE` entries. Already-completed entries are not reprocessed.
- **Per-user batch failure**: One user's DB timeout does NOT block other users' conversions
- **Hour_bucket UNIQUE constraint**: Only one job row per hour. Re-running same hour is safe — already-completed job is never restarted.

---

## 8. Testing

The test suite contains **24 async integration tests** running against a real PostgreSQL test database (`dreamland_test`). There are **no mocks** for the database layer — every test exercises the full stack from HTTP request to database write and read.

### Test Architecture

**Why not mocks?**
- Mocks would not catch real database constraint violations
- Double-entry invariant (SUM = 0) must be verified at the actual database
- Timezone calculations must be tested against real PostgreSQL TIMESTAMPTZ behavior
- Idempotency key logic must be tested against actual unique constraints

### Test Execution

All tests use:
- `pytest-asyncio` for async test support
- `httpx` with ASGI transport (no mock server needed)
- Real PostgreSQL (in-memory SQLite for CI, but structure defined for Postgres)

### Test Coverage

| Test Class | Count | What's Tested |
|-----------|-------|---------------|
| `TestWinTokens` | 14 | Token awards, daily cap, idempotency, validation, edge cases |
| `TestTokenHistory` | 4 | Token history retrieval, timezone handling |
| `TestStats` | 4 | Stats aggregation, balance calculations |
| `TestUsdHistory` | 2 | USD history retrieval |

**Key Tests:**
- Happy path: successful token win with correct ledger entries
- Double-entry invariant: verify SUM(debit) + SUM(credit) = 0
- Daily cap enforcement: 5th token succeeds, 6th token fails with 422
- Idempotency: same key returns same response, ledger has exactly 2 rows (not 4)
- Fractional tokens: rejected with 422
- Naive datetime: rejected with 422
- User not found: 404 before any DB writes
- Inactive user: 403 blocked

### Run Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test class
pytest tests/ -v -k TestWinTokens

# Run with coverage
pytest tests/ -v --cov=app --cov-report=term-missing
```

---

## 9. Setup & Running

### Prerequisites

- Python 3.10+
- PostgreSQL 14+ (or Docker)
- Git

### Step 1: Clone and Set Up Virtual Environment

```bash
git clone <repo>
cd dreamland

python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:
```
DATABASE_URL=postgresql+asyncpg://dreamland:secret@localhost:5432/dreamland
DATABASE_URL_SYNC=postgresql+psycopg2://dreamland:secret@localhost:5432/dreamland
DREAM_TOKEN_RATE_USD=0.15
DREAM_TOKEN_DAILY_CAP=5
ENVIRONMENT=development
AWS_REGION=us-east-1
```

### Step 4: Start PostgreSQL

With Docker:
```bash
docker-compose up db -d
```

Or manually create the database:
```sql
CREATE DATABASE dreamland;
CREATE DATABASE dreamland_test;
CREATE USER dreamland WITH PASSWORD 'secret';
GRANT ALL PRIVILEGES ON DATABASE dreamland TO dreamland;
GRANT ALL PRIVILEGES ON DATABASE dreamland_test TO dreamland;
```

### Step 5: Run Migrations

```bash
alembic upgrade head
```

### Step 6: Start the Server

```bash
uvicorn app.main:app --reload --port 8000
```

### APIs Available

- **Swagger UI**: http://localhost:8000/docs
- **OpenAPI JSON**: http://localhost:8000/openapi.json
- **Prometheus metrics**: http://localhost:8000/metrics
- **Health check**: http://localhost:8000/health

### Frontend (Operator / Owner Console)

The frontend lives in the `frontend/` folder and provides:
- Operator console (token wins, stats, history)
- Owner console (overview metrics, user search)

```bash
cd frontend
npm install
npm run dev
```

Default frontend URL: http://localhost:5173

### CORS (Local Dev)

The API allows the Vite dev server origins:
- http://localhost:5173
- http://127.0.0.1:5173

---

## 10. Infrastructure & Deployment

### Current Implementation

This is a **single-region, in-process implementation**:
- **Framework**: FastAPI on localhost (easily deployable to AWS ECS/Fargate)
- **Database**: PostgreSQL on localhost (easily upgradeable to Aurora)
- **Scheduler**: APScheduler in-process (easily replaceable with Celery)
- **Logging**: structlog JSON (compatible with Datadog/CloudWatch)
- **Metrics**: Prometheus (compatible with Grafana)

### Production Considerations

**Multi-Region Deployment:**
```
Game Client
    ↓
Route 53 (Latency Routing)
    ↓
AWS ALB (Regional)
    ↓
ECS Fargate (FastAPI)
    ↓
Aurora PostgreSQL (Multi-AZ + Read Replicas)
    ↓
APScheduler (or Celery Beat)
```

**GDPR Data Residency:**
- EU user data confined to `eu-west-1` region
- `users.region` field is immutable after creation
- Route 53 Geolocation Routing ensures EU users never route to non-EU endpoints
- Analytics pipeline uses Debezium CDC to replicate only anonymized ledger data (no PII)

**Scaling Path:**
1. **Current**: In-process APScheduler
2. **Next**: Replace with Celery Beat for distributed scheduling
3. **Analytics**: Debezium → Kafka → Snowflake for centralized financial reporting

---

## 11. Key Design Decisions

1. **Double-entry accounting**: Every token win = 2 ledger rows with same `transaction_id`, sum = 0
2. **NUMERIC(18,8)**: Exact arithmetic, never floats. Prevents IEEE 754 rounding errors.
3. **Idempotent APIs**: Full request + response cached in `idempotency_keys`. Safe to retry.
4. **Timezone-aware daily cap**: Enforced in user's IANA timezone, not UTC
5. **Ledger immutability**: No updates/deletes. All corrections are new reversing entries.
6. **Sign constraint at DB level**: CHECK CONSTRAINT prevents application bugs from corrupting ledger
7. **Partial index on `is_converted = FALSE`**: Conversion job query stays O(unconverted rows)
8. **Distributed job mutex**: UNIQUE `hour_bucket` prevents duplicate hourly jobs in multi-instance deployments
9. **Per-user batch isolation**: One user's failure doesn't block other users' conversions
10. **APScheduler `coalesce=True`**: Missed hours don't cause job explosion on restart

---

## Project Structure

```
dreamland/
├── app/
│   ├── main.py                    FastAPI app, lifespan, middleware
│   ├── core/
│   │   ├── config.py              Environment configuration
│   │   └── logging.py             structlog JSON logging
│   ├── db/
│   │   └── engine.py              Async SQLAlchemy engine
│   ├── models/
│   │   ├── base.py                SQLAlchemy base
│   │   ├── users.py               User table
│   │   ├── accounts.py            Chart of Accounts
│   │   ├── ledger.py              Token & USD ledgers
│   │   └── conversion_jobs.py     Hourly job tracking
│   ├── schemas/
│   │   ├── tokens.py              Request/response models
│   │   ├── usd.py                 USD history response
│   │   ├── stats.py               Stats response
│   │   ├── admin.py               Owner schemas
│   │   └── dev.py                 Dev schemas
│   ├── services/
│   │   ├── token_service.py       Token win logic
│   │   ├── usd_service.py         USD history logic
│   │   ├── stats_service.py       Stats aggregation
│   │   ├── account_service.py     Account management
│   │   └── admin_service.py       Owner overview + users
│   ├── api/
│   │   ├── tokens.py              Token endpoints
│   │   ├── usd.py                 USD endpoints
│   │   ├── stats.py               Stats endpoint
│   │   ├── admin.py               Owner endpoints
│   │   └── dev.py                 Dev-only endpoints
│   ├── jobs/
│   │   └── conversion_job.py      Hourly conversion job
│   └── utils/
│       └── time_utils.py          Timezone utilities
├── frontend/                      Operator + Owner UI
│   ├── src/
│   │   ├── App.jsx                UI layout and views
│   │   ├── styles.css             UI theme and layout
│   │   └── lib/api.js             API helper
├── alembic/                       Database migrations
├── tests/
│   ├── conftest.py                Test fixtures
│   └── test_tokens.py             24 async tests
├── requirements.txt
├── alembic.ini
├── pytest.ini
├── docker-compose.yml
└── Dockerfile
```

---

## Running Migrations

```bash
# Apply all migrations
alembic upgrade head

# Create a new migration after model changes
alembic revision --autogenerate -m "describe change"

# Downgrade one step
alembic downgrade -1
```

---

## Running the Server

### Development

```bash
uvicorn app.main:app --reload --port 8000
```

### API Documentation

Once the server is running:

- **Swagger UI**: http://localhost:8000/docs
- **OpenAPI JSON**: http://localhost:8000/openapi.json
- **Prometheus metrics**: http://localhost:8000/metrics

---

## License

Built as a technical interview exercise for Sei .
