# CoachAI Database Migrations

Alembic is the only production schema-versioning mechanism for CoachAI.
`database.metadata.create_all()` is useful for isolated tests and legacy
SQLite compatibility, but it is **not** a migration system and must not be used
to upgrade a PostgreSQL environment.

## Canonical history

- Alembic configuration: `alembic.ini`
- Migration environment: `migrations/env.py`
- First revision: `20261008_0001`
- Current head: `20261009_0002`
- Frozen first-revision schema: `migrations/schema_20261008_0001.py`
- Live application metadata: `database.metadata`

The frozen schema is immutable migration history. When live metadata changes,
add a new revision; never edit an already released snapshot. The migration
tests verify that post-baseline metadata is owned by an explicit follow-up
revision.

Revision `20261008_0001` serves both supported starting states:

1. On an empty database it creates the complete canonical schema in foreign-key
   dependency order.
2. On the pre-Alembic CoachAI schema it preserves existing rows, adds the known
   authentication/mobile columns, creates missing mobile-core tables and
   indexes, and then validates that every canonical table and column exists.

It intentionally does not use `MetaData.create_all()`. A structurally incomplete
or unexpected legacy database fails closed rather than being stamped current.

## Required configuration

Migrations require an explicit `DATABASE_URL`. `alembic.ini` contains an
unusable sentinel URL so a missing setting cannot migrate `coach_ai.db` or an
accidental SQLite file.

Example for a PostgreSQL 16 database:

```powershell
$env:DATABASE_URL = 'postgresql+psycopg2://coachai_user:<PASSWORD>@127.0.0.1:5432/coachai'
python -m alembic current
python -m alembic history
python -m alembic upgrade head
python -m alembic current
```

Do not run `alembic stamp head` on an existing CoachAI database. Stamping skips
the adoption checks and does not perform the required column/table changes.
Run `upgrade head` so the baseline can inspect and safely adopt the schema.

## Release procedure

1. Stop writers and the notification/Calendar workers, or put the deployment
   into maintenance mode.
2. Take a PostgreSQL backup and record the database name, server version,
   current revision, application release, and backup checksum.
3. Restore that backup into a disposable database and verify it before changing
   production.
4. Run `python -m alembic upgrade head` against the disposable restore.
5. Run schema, ownership, transaction, and application smoke tests there.
6. Run the same upgrade against production exactly once.
7. Confirm `alembic current` reports the expected head before starting writers.

Alembic's version table makes repeated `upgrade head` commands idempotent. The
revision itself also conditionally adopts the known pre-Alembic columns and
tables, which makes recovery from an interrupted non-transactional SQLite test
safe. PostgreSQL DDL runs transactionally where supported.

## Rollback and recovery

The adoption baseline is deliberately forward-only. It cannot know whether a
table was present before Alembic or was created during a fresh install, so a
generic downgrade that drops tables would risk deleting user data. Its
`downgrade()` therefore stops with an explicit error.

For a failed baseline deployment:

1. keep the application stopped;
2. retain logs and the failed database for diagnosis;
3. restore the verified pre-migration PostgreSQL backup to a new database;
4. validate row counts and critical user/task/calendar data;
5. switch the application only after the restored database passes checks.

Future revisions should be reversible only when the reverse operation is
provably non-destructive. Data-destructive changes require an explicit backup,
copy/backfill/verify/cutover plan and a separately reviewed recovery procedure.

Revision `20261009_0002` adds only short-lived shared rate-limit counters. Its
downgrade drops that table because the rows are disposable operational state,
not user content; requests remain safe but temporarily lose prior window usage.

## Authentication schema decision

`users.password_hash` is intentionally nullable. It exists only for isolated
legacy Streamlit password compatibility; Firebase identity remains separate
from domain logic and mobile authentication does not require a local password.
`users.is_disabled` provides a backend account gate, and `users.updated_at`
supports deterministic account synchronization. This resolves the former
PostgreSQL/SQLite `password_hash` divergence without deleting legacy hashes.

## Revision `20261008_0001` additions

- `users.password_hash` (nullable legacy compatibility)
- `users.is_disabled` with a `0/1` check and default `0`
- `users.updated_at` with a database timestamp default
- `tasks.version` with a non-negative check and default `0`
- `idempotency_records`
- `application_sync_state`
- `application_changes`
- `oauth_states`
- `calendar_sync_state`
- `calendar_operations`
- their foreign keys, uniqueness rules, checks, and query indexes

## Revision `20261009_0002` additions

- `rate_limit_buckets`, an atomic database-coordinated counter keyed by user,
  operation scope, and fixed window
- an expiry index for bounded cleanup

## Verification

The no-server unit suite exercises:

- one linear revision head;
- explicit revision ownership for every live metadata addition;
- fresh migration creation;
- repeated `upgrade head` idempotency;
- pre-Alembic column/table adoption with row preservation;
- explicit database selection; and
- absence of `metadata.create_all()` from revision execution.

Run it with:

```powershell
python -m unittest tests.test_migrations -v
```

Before production release, also run fresh and upgrade scenarios against a
disposable PostgreSQL 16 database and exercise backup restoration. SQLite tests
are a fast portability check; they are not proof of PostgreSQL correctness.
