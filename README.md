# CoachAI

CoachAI is a Streamlit productivity coach with deterministic scheduling,
future planning, analytics, Google Calendar integration, and a durable
notification runtime.

## Current architecture

- Streamlit multipage application with a separate thin FastAPI transport
- PostgreSQL through SQLAlchemy and `psycopg2`
- Smart Scheduler with task segments, schedule revisions, atomic apply/undo,
  ownership enforcement, and optimistic concurrency
- Future Planner with date ranges, constraints, drafts, and deterministic
  allocation
- Analytics and recommendation services
- Notification foundation with an outbox, worker leases, deduplication,
  cooldowns, local schedules, sync revisions, and tenant isolation
- FCM and APNs provider adapters
- Principal-bound application services, durable command idempotency, and
  application-data delta/full sync
- Versioned Alembic migrations and standalone notification, Calendar, and
  maintenance workers

The FastAPI transport exposes `GET /health`, `GET /ready`, authenticated task
listing, and one versioned/idempotent task-update route. It sits above the
existing application-service boundary, does not host background workers, and
does not duplicate domain rules. See `FRONTEND_HANDOFF.md` for the exact API.

PostgreSQL is the normal development/runtime database. SQLite compatibility is
retained only for explicit isolated tests and legacy-schema utilities; there is
no automatic runtime fallback to `coach_ai.db`.

## Local setup

1. Install PostgreSQL 16 and create a local database and restricted application
   role.
2. Install dependencies:

   ```powershell
   python -m pip install -r requirements-lock.txt
   python -m pip check
   ```

3. Copy `.env.example` to `.env` and replace placeholders. The required
   database setting has this form:

   ```dotenv
   DATABASE_URL=postgresql+psycopg2://coachai_user:<PASSWORD>@127.0.0.1:5432/coachai
   ```

4. Start the application:

   ```powershell
   python -m streamlit run app.py
   ```

5. Run each background role under a process supervisor, separately from the UI
   and API workers:

   ```powershell
   python -m notifications
   python calendar_worker.py
   python main.py
   ```

6. Run the API as its own process role:

   ```powershell
   python -m uvicorn fastapi_app:app --env-file .env --host 127.0.0.1 --port 8000
   ```

   `GET /health` is process-only and does not access PostgreSQL. `GET /ready`
   validates mandatory configuration, PostgreSQL connectivity, and the current
   Alembic schema revision without returning connection details. Authenticated
   routes require `FIREBASE_PROJECT_ID` and a valid Firebase ID token.

Install or upgrade the PostgreSQL schema with Alembic before starting the
application. A missing or invalid `DATABASE_URL` fails clearly and never falls
back to the legacy SQLite file. SQLAlchemy `metadata.create_all()` is not a
production upgrade mechanism; see [DATABASE_MIGRATIONS.md](DATABASE_MIGRATIONS.md)
for the migration, backup, and recovery procedure.

## Tests

Run the standard suite:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Permanent PostgreSQL integration tools are also included:

- `tests/postgres_patch5_verification.py` exercises scheduler, future-planner,
  analytics, schema, transaction, ownership, and persistence behavior against
  a disposable local PostgreSQL database.
- `tests/postgres_notification_verification.py` exercises notification schema,
  outbox atomicity, worker races and lease recovery, local schedules, sync, and
  tenant isolation against a disposable local PostgreSQL database.
- `tests/postgres_migration_verification.py` verifies `0001` to `0002`, the
  supported `0002` downgrade/re-upgrade path, idempotent head upgrades, and
  preservation of typed pre-Alembic rows.
- `tests/postgres_backend_core_verification.py` verifies tenant boundaries,
  task/timer state, durable and concurrent idempotency, application sync,
  Calendar queuing, AI Apply, and shared database rate limiting.

All four tools deliberately require explicit localhost-only test database URLs and
refuse unexpected database names.

## Notifications

The provider-independent notification runtime is implemented and has unit tests
plus dedicated PostgreSQL integration tooling. FCM and APNs adapters are
implemented, but real device delivery is **not verified** without external
provider credentials and test devices. Providers remain disabled unless
explicitly configured in `.env`.

See [NOTIFICATION_PUSH.md](NOTIFICATION_PUSH.md),
[BACKEND_OPERATIONS.md](BACKEND_OPERATIONS.md),
[MOBILE_BACKEND_CONTRACTS.md](MOBILE_BACKEND_CONTRACTS.md),
[SMART_SCHEDULING.md](SMART_SCHEDULING.md), and
[ANALYTICS_DATA_LINEAGE.md](ANALYTICS_DATA_LINEAGE.md) for focused design and
verification details.

## Security and local artifacts

- `.env`, database files, provider keys, service-account files, tokens, logs,
  caches, and generated output are ignored.
- Never commit the PostgreSQL password, Firebase service account, APNs private
  key, Google OAuth credentials, or device tokens.
- The legacy `coach_ai.db` may remain on a developer machine as a reference
  backup, but it is not tracked or used by the PostgreSQL runtime.
