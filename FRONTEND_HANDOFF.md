# CoachAI Frontend Handoff

Verified locally: 2026-10-14 (Africa/Cairo)

## Current integration boundary

The local PostgreSQL runtime, FastAPI readiness, Firebase bearer adapter,
request-scoped database lifetime, task listing, and task update route are
implemented and tested. The available API is intentionally small:

- `GET /health`
- `GET /ready`
- `GET /v1/tasks`
- `PATCH /v1/tasks/{task_id}`

Task creation, plans, sync, Calendar, notifications, AI, analytics, and account
routes are not implemented. Do not infer routes from application-service
methods or `MOBILE_BACKEND_CONTRACTS.md`.

## Local startup

Requirements:

- Python 3.12 with `requirements-lock.txt` installed.
- PostgreSQL 16 and an explicit `DATABASE_URL`; there is no SQLite fallback.
- Alembic revision `20261009_0002`.
- `FIREBASE_PROJECT_ID` for authenticated routes.
- Default Firebase Admin credentials or another supported Firebase Admin
  credential mechanism when verifying real tokens.
- An untracked `.env` based on `.env.example`.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m uvicorn fastapi_app:app --env-file .env --host 127.0.0.1 --port 8000
```

Base URL: `http://127.0.0.1:8000`

OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

Interactive documentation: `http://127.0.0.1:8000/docs`

Loopback is intentional. A physical device cannot reach the server through
its own `127.0.0.1`; LAN/device exposure requires a separately reviewed bind,
firewall rule, and trusted-network configuration.

## Authentication

Business requests require a Firebase ID token:

```http
Authorization: Bearer <FIREBASE_ID_TOKEN>
```

The backend uses Firebase Admin verification with revocation checking enabled,
then validates issuer, audience, expiry, and subject before constructing an
immutable `AuthenticatedPrincipal`. Tenant identity comes only from verified
claims. Never send or accept `user_id` as the acting identity.

Missing, malformed, expired, revoked, wrong-project, or disabled identities
return HTTP 401 with `AUTHENTICATION_REQUIRED`. A disabled local CoachAI user is
also rejected before an application service is created. Tokens and sensitive
claims are never returned or logged.

Unit tests use injected verifiers. Live verification against a real Firebase
project remains unverified until deployment credentials and a real token are
available.

## Operational endpoints

### `GET /health`

No authentication or database access. Success: HTTP 200.

```json
{"status":"healthy"}
```

### `GET /ready`

No authentication. It checks configuration, PostgreSQL connectivity, and the
required Alembic revision. Success: HTTP 200.

```json
{
  "status": "healthy",
  "checks": {
    "configuration": {
      "status": "healthy",
      "summary": "Runtime configuration is valid."
    },
    "database": {
      "status": "healthy",
      "summary": "Database connection succeeded."
    }
  }
}
```

Failure is HTTP 503 with the same sanitized shape.

## Task endpoints

### `GET /v1/tasks`

Returns only tasks owned by the authenticated principal.

Query parameters:

- `limit`: integer `1..100`, default `50`.
- `offset`: integer from `0` through `100000`, default `0`.

Example:

```http
GET /v1/tasks?limit=50&offset=0
Authorization: Bearer <FIREBASE_ID_TOKEN>
```

```json
{
  "items": [],
  "limit": 50,
  "offset": 0,
  "next_offset": null
}
```

When `next_offset` is non-null, use it as the next request's `offset`. Ordering
is stable by plan date, task order, and task ID.

### `PATCH /v1/tasks/{task_id}`

Updates one owned, non-terminal task. Foreign and absent task IDs both return
the same HTTP 404 response to prevent tenant enumeration.

Required header:

```http
Idempotency-Key: <opaque-key-up-to-200-characters>
```

Example request:

```http
PATCH /v1/tasks/42
Authorization: Bearer <FIREBASE_ID_TOKEN>
Idempotency-Key: 9ca9287f-fd88-44fd-911f-204d31275a91
Content-Type: application/json

{
  "expected_version": 3,
  "title": "Updated task title",
  "priority": 4
}
```

Allowed update fields are `title`, `description`, `priority`,
`estimated_minutes`, `order_index`, `category_id`, `is_fixed_time`, `is_break`,
`earliest_start`, and `deadline`. Unknown fields are rejected. At least one
update field is required.

Database-backed integer identifiers and version/order fields must fit a signed
32-bit integer (`0` through `2147483647` where zero is allowed by the field).

The response is the mobile-safe task representation and includes the new
`version`, `sync_revision`, and `idempotent_replay` flag. Provider credentials,
ownership joins, outbox state, and other internal rows are never serialized.

Idempotency and versions:

- Persist the exact request and idempotency key before sending.
- Retrying the same canonical request/key returns the original response with
  `idempotent_replay=true`.
- Reusing a key with a changed request returns HTTP 409
  `IDEMPOTENCY_CONFLICT`.
- A stale `expected_version` returns HTTP 409 `VERSION_CONFLICT`; fetch current
  state and submit a new command with a new key.

## Error envelope

Errors use a stable safe shape:

```json
{
  "code": "VERSION_CONFLICT",
  "message": "The resource changed before the operation was applied.",
  "details": {},
  "correlation_id": "opaque-safe-id"
}
```

Relevant statuses are:

- 401: authentication required or rejected.
- 403: authorization/tenant policy rejection.
- 404: absent or foreign resource.
- 409: version, idempotency, scheduling, or state conflict.
- 422: request validation/capacity error.
- 429: rate limited; inspect `Retry-After` when present.
- 500: sanitized unexpected failure.
- 503: invalid configuration or unavailable readiness dependency.

Every HTTP response includes `X-Correlation-ID`. Branch on `code`, not English
message text.

## CORS

Browser CORS is disabled when `COACHAI_CORS_ALLOWED_ORIGINS` is empty. Configure
an exact comma-separated allowlist when needed:

```dotenv
COACHAI_CORS_ALLOWED_ORIGINS=https://frontend.example,https://preview.example
```

Only exact HTTP(S) origins are accepted. Wildcards, embedded credentials,
paths, queries, and fragments are rejected. Allowed methods are `GET`, `PATCH`,
and `OPTIONS`; allowed request headers are `Authorization`, `Content-Type`, and
`Idempotency-Key`. Cookie credentials are disabled.

Native Android clients are not subject to browser CORS.

## Sync and external integration limits

Application and notification sync revisions, cursors, pagination checkpoints,
tombstones, and full-resync rules exist in the service layer but have no HTTP
routes yet. Do not implement frontend sync calls until a separate reviewed
transport patch exposes them.

Also not verified with real external credentials/devices:

- Firebase Admin against a real project.
- Gemini provider calls.
- Google Calendar OAuth/provider operations.
- FCM/APNs delivery to physical devices.

Notification, Calendar, and maintenance workers remain separate processes and
are never started by FastAPI.

## Reproducible frontend smoke check

After starting the API:

```powershell
curl.exe -i http://127.0.0.1:8000/health
curl.exe -i http://127.0.0.1:8000/ready
curl.exe -s http://127.0.0.1:8000/openapi.json
curl.exe -i http://127.0.0.1:8000/v1/tasks
```

Expected results:

- `/health`: 200.
- `/ready`: 200 with configuration and database healthy.
- OpenAPI: paths for `/v1/tasks` and `/v1/tasks/{task_id}`.
- Unauthenticated `/v1/tasks`: 401 safe error envelope.

With a real Firebase ID token stored only in the local shell environment:

```powershell
curl.exe -i -H "Authorization: Bearer $env:COACHAI_TEST_ID_TOKEN" http://127.0.0.1:8000/v1/tasks
```

Never paste or commit that token. Stop only the Uvicorn process started for the
check.
