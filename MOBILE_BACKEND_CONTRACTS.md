# CoachAI Mobile Backend Contracts

This document defines the transport-neutral contracts that a future Android
client and FastAPI adapter must preserve. It deliberately defines no routes,
HTTP methods, status codes, or FastAPI models. The supported boundary is the
principal-bound application service layer.

## Contract status and boundaries

- Android authenticates with Firebase Authentication and presents a Firebase
  ID token to the future transport.
- The transport verifies the token and provisions the CoachAI account before
  constructing `CoachAIApplication`.
- The application services derive ownership from an immutable
  `AuthenticatedPrincipal`; request bodies and path values never select a
  `user_id`.
- Domain scheduling remains deterministic. The LLM may interpret input and
  produce a draft, but cannot place work authoritatively or write directly.
- PostgreSQL is authoritative for tasks, timers, schedules, idempotency,
  application sync, notification sync, Calendar state, and conflict decisions.
- Notification sync and general application-data sync are separate revision
  streams and must have separate client cursors.
- Provider credentials, password hashes, push tokens, worker leases, raw
  provider errors, and internal database rows are never mobile payloads.

The future transport is a thin adapter: authenticate, validate/deserialize,
open a unit of work, call one application-service method, serialize the safe
result/error, and close the unit of work. It must not duplicate the state
machine, scheduler, idempotency, ownership, or reconciliation logic.

## Authentication and account provisioning

The target authentication sequence is:

1. Android obtains a Firebase ID token for the signed-in account.
2. The transport verifies signature, issuer, audience, expiration, and
   revocation where configured.
3. `FirebaseTokenVerifier` returns an immutable principal containing the
   Firebase UID, verified email/display name, and read-only claims.
4. `get_or_create_user_from_principal` resolves or provisions the local user.
5. A disabled Firebase identity, invalid/expired token, or disabled local user
   fails before an application service is constructed.

The Firebase UID is the CoachAI tenant identifier. If a payload contains a
`user_id`, it must be rejected as an unsupported field or ignored for routing;
it can never override the principal. Cross-tenant identifiers are returned as
`RESOURCE_NOT_FOUND` when distinguishing foreign from absent data would permit
enumeration.

Legacy Streamlit password login is a separate compatibility adapter and is not
an Android authentication mechanism.

Real Firebase verification is environment-dependent and requires configured
Firebase credentials/project state. Deterministic fake-verifier tests do not
constitute real-project verification.

## Common value and serialization rules

- Resource identifiers are opaque. Android must not infer ownership or order
  from them.
- All instants sent to mobile are RFC 3339 timestamps with an explicit offset;
  canonical server instants use UTC and `Z` (for example,
  `2026-10-09T01:15:30.123Z`). A naive timestamp is never a mobile value.
- Local calendar dates use `YYYY-MM-DD` and wall-clock times use `HH:MM:SS`.
  They are interpreted only with the account's IANA timezone.
- Timezone names are IANA identifiers such as `Africa/Cairo`, never only
  `UTC+03:00` and never an Android display abbreviation.
- Durations are integer seconds for live timer accumulation and integer minutes
  for estimated/actual task duration.
- Versions and sync revisions are non-negative integers.
- Unknown fields in strict AI/planning structures are rejected. Clients should
  also ignore unknown response fields for forward compatibility unless a
  declared payload version changes semantics.
- Push payload version `1` contains stable identifiers and action metadata,
  not full task/database objects.

## Command and idempotency contract

Every state-changing mobile command supplies:

- a fresh, opaque idempotency key of at most 200 characters, normally a UUID;
- the expected task version or schedule revision when the resource is
  versioned; and
- only resource identifiers and fields relevant to that command.

Durable idempotency is scoped by authenticated principal, operation name, and
idempotency key:

- same scope + same key + same canonical request returns the original result
  with `idempotent_replay=true`;
- same scope + same key + different request fails with
  `IDEMPOTENCY_CONFLICT`;
- a matching request that is still being completed fails as a retryable
  `VERSION_CONFLICT`; and
- different principals or operation names do not share a key namespace.

Android persists a pending command and its exact request/key before sending.
If the network fails after send, it repeats that exact request and key. It must
not edit a pending request while retaining its key. After a version conflict it
syncs, asks the user to reconcile if necessary, and creates a new command with
a new idempotency key.

Idempotency does not mean every command is valid forever. Expected-version,
ownership, state-machine, schedule-revision, notification-staleness, and
Calendar-provenance checks still apply on first execution.

## Stable error contract

Application errors are independent of HTTP. A future transport serializes only
the stable code, safe message, optional safe details, and correlation ID:

```json
{
  "code": "VERSION_CONFLICT",
  "message": "The resource changed before the operation was applied.",
  "details": {},
  "correlation_id": "opaque-safe-id"
}
```

Supported codes are:

- `AUTHENTICATION_REQUIRED`
- `AUTHORIZATION_DENIED`
- `RESOURCE_NOT_FOUND`
- `TENANT_MISMATCH`
- `INVALID_STATE_TRANSITION`
- `VERSION_CONFLICT`
- `IDEMPOTENCY_CONFLICT`
- `SCHEDULING_CONFLICT`
- `CAPACITY_EXCEEDED`
- `EXTERNAL_PROVIDER_FAILURE`
- `VALIDATION_ERROR`
- `CONFIGURATION_ERROR`

`INVALID_STATE_TRANSITION` may include safe `current` and `requested` values.
No error payload includes SQL, a traceback, exception text, credential data,
provider response bodies, file paths, or model/provider internals. Android must
branch on `code`, not English message text.

`BackendError` paths already carry this stable model. The shared rate limiter
still raises `RateLimitExceeded`, and a few read-only notification validators
can raise `ValueError` before application-level translation. A future transport
must not serialize either exception directly; those remaining paths need a
stable error adapter (and a generic safe fallback) before exposure.

## Task aggregate and ownership

The mobile-safe task representation includes task/plan/category identifiers,
title and description, priority, estimated/actual minutes, schedule fields,
state/timer fields, earliest start/deadline, `version`, `timer_version`, and
created/updated timestamps. It excludes internal ownership joins, outbox rows,
credentials, and notification/provider internals.

Creating a task requires an owned plan and, when present, an owned category.
Updating, deleting, deferring, scheduling, moving, or commanding a task always
checks the task through the principal. Foreign and absent identifiers have the
same externally safe result.

Limits currently enforced by the core include:

- task title: `1..200` characters;
- task description: at most `4000` characters;
- estimated duration: `1..960` minutes;
- priority: integer `1..5`; and
- category name: `1..80` characters.

## Task state machine

Persisted task states are `pending`, `in_progress`, `completed`, and `failed`.
Pause is a timer substate of `in_progress`, not a fifth persisted task status.

Legal lifecycle commands are:

- `start`: `pending` to running `in_progress`;
- `pause`: running `in_progress` to paused `in_progress`;
- `resume`: paused `in_progress` to running `in_progress`;
- `complete`: `pending`, running, or paused to `completed`;
- `fail`: `pending`, running, or paused to `failed`, with a supported failure
  reason; and
- `defer_to_tomorrow`: `pending` only, moving the task unscheduled into the
  account's next local-day plan.

Terminal tasks cannot restart, defer, or be edited. Duplicate completion/fail,
starting an already running task, pausing an already paused task, and resuming a
non-paused task fail with `INVALID_STATE_TRANSITION` unless the request is an
exact idempotent replay of the already completed command.

Each mutation validates `expected_version`, changes task and timer state in one
transaction, increments `version` and the timer version where applicable,
emits any command-required notification/outbox effects, and records an
application-sync change.

Task deletion requires the expected version. A currently `in_progress` task
must be finished first. Deletion writes a tombstone and durably queues deletion
of CoachAI-owned Calendar events; it never deletes a foreign Calendar event.

## Timer and offline execution

The server is authoritative for elapsed time. Canonical elapsed seconds are:

- `timer_accumulated_seconds` while stopped/paused; or
- accumulated seconds plus server `now - timer_segment_started_at` while
  running.

Start, pause, resume, complete, and fail each use one transaction and a server
timestamp. Android may animate a local timer between syncs, but that display is
only a projection of the last server state:

- app killed/restarted — fetch current task state and reconstruct the display;
- network lost — continue a visual estimate, label it pending/offline, and do
  not claim the command was accepted;
- device clock changed — discard wall-clock arithmetic and reconcile from the
  server; use Android monotonic time only for a temporary on-screen delta;
- response lost — repeat the exact request with the same idempotency key; and
- version conflict — sync and reconcile instead of blindly applying the local
  sequence.

An offline command queue preserves command order, expected version, request
body, and idempotency key. A later command that depends on an unconfirmed prior
version cannot be sent until the prior command is replayed or reconciled.

## Scheduling, Future Planning, and Undo

Schedule mutations use the plan's `schedule_revision`, separate from a task's
`version`:

- schedule/apply and add-and-schedule require `expected_revision`;
- manual move requires `expected_schedule_revision`;
- Future Planning is preview first, explicit idempotent Apply second; and
- Undo references an owned schedule operation and is itself idempotent.

Successful mutations publish task/plan changes and a `plan_segments` replacement
bundle. They also durably queue Calendar reconciliation and return an operation
ID plus `calendar_reconciliation_pending`; Calendar success is not implied by
the database commit.

Android must show scheduling conflicts/unplaced tasks from the canonical result
and must not attempt to reproduce the deterministic scheduler locally.

## Application-data synchronization

The application sync cursor is per account, not per device. Each accepted
business change increments a durable revision and records:

- `sync_revision`
- `entity_type`
- `entity_id`
- `change_type` (`upsert` or `delete`)
- optional `entity_version`
- a mobile-safe payload
- creation timestamp

`sync_since(cursor, limit)` has a maximum page size of `500` and returns:

- `from_revision` — the requested cursor;
- `revision` — the checkpoint represented by this page;
- `current_revision` — newest server revision captured for the response;
- `has_more` — whether another page is required;
- `full_resync_required` — whether retained deltas no longer cover the cursor;
  and
- ordered `changes`.

The client applies a page transactionally, persists the page's `revision`, then
requests the next page while `has_more=true`. It must not jump directly to
`current_revision` before applying all pages.

If the cursor is ahead of the server, the backend returns `VERSION_CONFLICT`.
If it is older than `minimum_available_revision`, the response contains no
deltas and sets `full_resync_required=true`. Android then replaces its account
cache from `full_resync()`, which returns one revision-consistent, secret-free
snapshot containing the user, plans, tasks, segments, categories, account-wide
and type-specific notification preferences, selected-calendar metadata, a
Calendar connection summary, and Calendar sync-range state.

The Calendar connection summary is limited to provider, connected state, token
expiry, scopes, and record timestamps. OAuth access and refresh tokens are never
selected for snapshot construction. If the application revision changes
repeatedly while the snapshot is being built, the backend returns
`VERSION_CONFLICT` and the client retries.

Deletion semantics are explicit:

- task deletion is a `delete` tombstone;
- a `plan_segments` change has `replace_all=true`; every cached segment for
  that plan absent from the supplied list is deleted locally; and
- clients never infer deletion merely from a partial delta page.

Multiple devices share this stream. Task `version` resolves task-write races;
`schedule_revision` resolves scheduling races. An exact idempotent replay
returns the original result even if a newer revision now exists. Preferences
without an expected version use atomic last-committed-write semantics and are
then distributed by sync.

## Timezone contract and reconciliation

The account timezone is an IANA name and is authoritative for scheduling,
Future Planning date ranges, notification quiet hours/local alarms, Calendar
conversion, stale-task maintenance, analytics day boundaries, and the meaning
of "today"/"tomorrow". Persisted instants remain UTC.

Changing timezone is an idempotent account command. In one transaction it:

1. validates the IANA name;
2. updates the account and registered-device timezone;
3. cancels outstanding local-notification schedules with reason
   `timezone_changed`;
4. cancels queued/failed reminder outbox rows created under the old timezone;
5. reconciles pending scheduled tasks and queues replacement reminders;
6. queues durable Calendar `timezone_reconcile` work;
7. publishes an `account_settings` sync change; and
8. returns the cancelled-schedule count, replacement-reminder count, Calendar
   operation ID, and new sync revision.

Android handles the resulting sync change by cancelling every cached OS alarm
for the old timezone immediately. It fetches the authoritative local schedule
set again and arms only newly reconciled rows. It must not shift an old alarm by
the raw UTC-offset difference: IANA rules and DST transitions can make that
wrong. Running timer elapsed time does not change because it is based on UTC
server instants, while future local dates/times are interpreted in the new
timezone.

Calendar reconciliation is asynchronous. The UI may show a pending Calendar
state until the Calendar worker completes the queued operation. Replacement
notification intents are committed with the timezone change, but their local
schedule rows are created asynchronously when the standalone notification
worker processes them. Android keeps old alarms cancelled until that new
authoritative set appears.

## AI planning and voice input

The safe AI sequence is:

1. untrusted text/audio enters a bounded provider call outside a business
   transaction;
2. the model returns strict structured output;
3. Pydantic rejects unknown, malformed, or oversized output;
4. the deterministic planner produces a preview/draft;
5. Android displays the draft for explicit review; and
6. `apply_draft` revalidates and persists it atomically with an idempotency key.

Draft generation does not persist plans/tasks. The quarantined
`generate_and_save_plan` convenience path is not a mobile contract. Model text
cannot select a tenant, bypass ownership, or decide authoritative schedule
placement.

Current boundaries include:

- planning input at most `12000` characters;
- at most `50` proposed tasks;
- output at most `8192` model tokens;
- Calendar context at most `500` events;
- transcript at most `12000` characters;
- audio at most `15 MiB`; and
- audio MIME type one of FLAC, MP3/MPEG, MP4 audio, OGG, WAV, or WebM audio.

AI provider timeouts and retries are bounded. The principal-bound day-plan
draft, recommendations, voice transcription, and chatbot use principal-bound,
database-coordinated rate limits through `CoachAIApplication`. Chatbot tools use
the explicitly supplied request database rather than Streamlit service state.
Voice transcription uses the supported `google-genai` 2.x client against API
`v1`; the deployment default is `gemini-3.8-flash` and only deployment
configuration may select another validated voice model. Android treats malformed
drafts and provider failures as recoverable errors; it never retries Apply with
a modified draft under the same key.

## Google Calendar contract

OAuth authorization uses cryptographically random, short-lived, single-use,
principal-bound state and PKCE. The callback must present the same principal,
state, and redirect URI. Code exchange is outside a database transaction;
tokens are stored only as versioned encrypted envelopes.

Android-visible Calendar state is limited to connection state, selected
calendar metadata, sync range/freshness/status, safe segment reconciliation
state, and durable operation IDs/status. Access tokens, refresh tokens, OAuth
codes/verifiers/state values after consumption, client secrets, and raw
provider errors are never returned.

The supported sync range is `1..84` days (the 12-week Future Planning horizon).
A sync request requires at least one selected owned account calendar and queues
one durable `sync.range` operation per calendar. Event changes are also queued:

- `event.upsert` requires an owned task; and
- `event.delete` requires a segment marked `coachai_created` with the matching
  owned Calendar/event identity.

Queued, processing, completed, failed, and cancelled are durable states.
Workers use leases and bounded attempts; transient failures retry with backoff.
Principal-bound application methods get one operation, list owned operations
with bounded status filters, and idempotently cancel an operation. Only
queued/failed operations may be cancelled. Provider success is recorded in a
new database transaction. Unknown outcomes remain reconcilable rather than
being assumed successful.

Schedule apply/move/undo and task deletion queue their own reconciliation. A
revoked Google authorization is permanent: encrypted tokens are removed, work
fails safely, and Android prompts the user to reconnect. User-facing failure
text is generic and contains no provider response.

## Android notification and device contract

### Device identity and registration

Android creates a stable, opaque `device_id` for one installation/account
binding and registers it idempotently with:

- `platform="android"`
- `provider="fcm"` (or `none` when push is intentionally disabled)
- current FCM token, when available
- app identifier/version
- locale (`ar` or `en`)
- current IANA timezone
- primary/enabled flags

The backend never returns the push token from the application-service layer.
A device ID already owned by another principal cannot be claimed. On account
switch, invalidate the old account binding and use a new account-scoped device
ID; do not attempt to transfer a globally owned ID.

Both the account-timezone command and device registration validate the supplied
value as an IANA timezone using `ZoneInfo`. Invalid names, offsets, and display
abbreviations are rejected before device persistence. Android should send its
canonical IANA zone, such as `Africa/Cairo`.

FCM token refresh is the same registration command with the same device ID, the
new token, and a new idempotency key. Logging out, deleting the local binding,
or losing permission invokes idempotent device invalidation, which clears the
token and disables delivery. A permanent provider-token error performs the
same server-side invalidation; the next valid token refresh re-registers it.

After applying notification changes and replacing local alarms, Android uses
the idempotent, principal-bound acknowledgement command to acknowledge the
applied notification-sync revision for its owned device. Acknowledgement is
monotonic, cannot exceed the server's current notification revision, and
updates device liveness; it does not advance another device's local cursor.

### Preferences

Account preferences support tone, reminder lead (`off`, `5`, `10`, `15`, or
`30` minutes), quiet-hours start/end, daily summary and coach-check-in flags,
privacy mode, sound, vibration, multi-device policy, locale, and 12/24-hour
format. Type-specific policy additionally supports enabled/channel/lead,
cooldown, and interruption level through principal-bound read and idempotent
update operations.

Preference updates are principal-bound, idempotent, and published to
application sync. Lock-screen privacy is a rendering rule: Android must obey
the returned privacy mode even if a stale local cache has richer text.

### Notification sync, read, and acted

Notification sync has its own per-account revision and returns ordered
notification rows, a page checkpoint, `current_revision`, and `has_more`. Its
cursor is never exchanged with the application-data cursor. The application
service and repository enforce a page size of `1..500` rows. If the device loses
this cursor, it starts again from revision `0` and pages retained notification
history; it does not use an application full-resync revision as a shortcut.

Mark-read and mark-acted are idempotent principal-bound commands. Acting also
marks read. The backend accepts an action only while the notification is
`active`; suppressed, cancelled, expired, or otherwise stale notifications are
rejected. Android must not optimistically treat an action as committed.

Push payload version `1` includes notification type plus stable notification,
task, segment, segment-index, schedule-revision, schedule-operation, and action
identifiers. Supported action identifiers are `START`, `COMPLETE`, `SNOOZE`,
`REPLAN`, and `OPEN`. Any task-changing action still invokes the normal task or
scheduling command with expected version and its own durable idempotency key.

Before displaying or acting, reconcile the notification's task/segment/
schedule identity when online. The backend worker also checks expiration, task
state, and schedule revision immediately before provider delivery.

### Local notification schedules and cancellation

The local-schedule read returns only currently `scheduled` rows, optionally
filtered to an owned device. Each row includes:

- local schedule, notification, device, task, and segment identifiers;
- segment index and schedule revision;
- schedule operation ID;
- trigger type and authoritative fire/expiry instants;
- locale, timezone, status, payload, and update timestamp.

For a device, this list is an authoritative replace-all set. Android persists
the `local_schedule_id` and schedule identity beside the OS alarm, schedules or
updates every returned row, and cancels any previously cached CoachAI alarm not
present in the new set. This handles task completion/deletion/failure,
rescheduling, Undo, expiration, and timezone changes. When preferences change,
Android first applies any stricter local privacy/sound/vibration/quiet-hour
rule, then replaces alarms from the next authoritative schedule set; it never
keeps an alarm merely because an older local preference allowed it.

Reminder-affecting account preference changes and `task_reminder` type-policy
changes cancel existing task-reminder local schedules, cancel queued or failed
reminder intents created under the old preference generation, and enqueue
replacement intents for owned pending tasks that have scheduled segments. The
command returns `local_schedules_cancelled` and
`notification_reminders_queued`.

A non-task type-policy change cancels matching local schedules but does not
enqueue task reminders. New local-schedule rows are materialized asynchronously
by the notification worker, so Android still applies stricter presentation
rules immediately and then performs authoritative replace-all reconciliation.

At fire time, Android suppresses an alarm whose expiry passed or whose cached
identity was superseded. If offline, it may display non-destructive content but
queues any action for server validation; it cannot assume an offline task
mutation succeeded. After reconnecting it performs notification sync,
application sync, and local-schedule replace-all reconciliation.

### Multi-device and foreground behavior

The server chooses delivery targets from enabled, non-invalidated devices and
the `all`, `primary`, or selected-device policy. Each device maintains its own
OS alarms but shares account notification/application revision streams.

The current standalone worker defaults to no authoritative foreground-presence
lease. Android therefore decides whether to show a system notification while
its process is foregrounded and then reconciles through notification sync. A
future presence signal may be used only as a short-lived delivery hint; it must
not be durable account state or suppress notification creation/sync. Until such
a transport-bound lease exists, the backend must not claim server-side
foreground suppression is verified.

FCM `sent` means Firebase accepted the request; it does not prove the Android
device displayed or received it. Real physical Android FCM delivery is
**NOT VERIFIED** without external Firebase credentials and a disposable test
device. Fake-provider and queue tests verify backend behavior only.

## Analytics contract

Analytics is a principal-bound read with a window of `1..365` days and language
`ar` or `en`. Day/hour bucketing uses the account IANA timezone. The result is a
formatted safe projection, not arbitrary query access. Android cannot submit
SQL, choose a tenant, or request unbounded history.

## Recurrence decision

Recurrence is intentionally **excluded from Android v1**. The canonical model
contains tasks, plan dates, segments, and explicit Future Planning ranges but no
recurrence rule, occurrence exception, series version, or series-level conflict
model. Implementing a boolean repeat flag would create unsafe ambiguity across
timezone changes, edits, completion, notifications, Calendar export, sync, and
Undo.

Therefore Android v1 contracts must not send or advertise RRULEs, repeat
intervals, series IDs, occurrence edits, or "all/future instance" actions.
Repeated work is represented as separate explicit tasks/plans. Adding
recurrence later requires its own versioned domain/migration/sync design and is
not a backward-compatible inference from current tasks.

## Android recovery sequence

On login, app restart, cursor loss, or reconnect:

1. obtain a fresh Firebase ID token;
2. register/refresh the account-scoped device and FCM token;
3. reconcile the account timezone if Android's IANA zone changed;
4. replay pending commands in dependency order with their original requests
   and idempotency keys;
5. page application-data sync to the current revision, or perform the required
   full resync;
6. page notification sync using its separate cursor;
7. replace the device's local CoachAI alarms from the authoritative schedule
   set; and
8. render timers/schedules from canonical returned versions and instants.

If authentication fails, stop replay and require sign-in. If ownership or
state validation fails, discard the invalid local intent after showing a safe
conflict. If a version conflict occurs, sync before deciding whether to create
a new command. If Calendar or push providers fail, retain the local CoachAI
mutation result and show the separately reported asynchronous provider state.

These contracts define the backend behavior a future FastAPI layer must expose;
they do not authorize implementing endpoints until the complete backend,
PostgreSQL, security, and Streamlit readiness gates pass.
