# CoachAI

CoachAI is a Streamlit productivity coach with deterministic scheduling,
future planning, analytics, Google Calendar integration, and a durable
notification runtime.

## Current architecture

- Streamlit multipage application
- PostgreSQL through SQLAlchemy and `psycopg2`
- Smart Scheduler with task segments, schedule revisions, atomic apply/undo,
  ownership enforcement, and optimistic concurrency
- Future Planner with date ranges, constraints, drafts, and deterministic
  allocation
- Analytics and recommendation services
- Notification foundation with an outbox, worker leases, deduplication,
  cooldowns, local schedules, sync revisions, and tenant isolation
- FCM and APNs provider adapters

PostgreSQL is the normal development/runtime database. SQLite compatibility is
retained only for explicit isolated tests and legacy-schema utilities; there is
no automatic runtime fallback to `coach_ai.db`.

## Local setup

1. Install PostgreSQL 16 and create a local database and restricted application
   role.
2. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
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

The application initializes a fresh PostgreSQL schema from the SQLAlchemy
metadata. A missing or invalid `DATABASE_URL` fails clearly and never falls
back to the legacy SQLite file.

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

Both tools deliberately require explicit localhost-only test database URLs and
refuse unexpected database names.

## Notifications

The provider-independent notification runtime is implemented and covered by
unit and PostgreSQL integration tests. FCM and APNs adapters are implemented,
but real device delivery is **not verified** without external provider
credentials and test devices. Providers remain disabled unless explicitly
configured in `.env`.

See [NOTIFICATION_PUSH.md](NOTIFICATION_PUSH.md),
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
