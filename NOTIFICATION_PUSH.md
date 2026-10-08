# CoachAI Push Delivery

## Status

- Provider architecture and delivery reconciliation: **IMPLEMENTED**
- FCM and APNs contract tests with deterministic fakes: **VERIFIED**
- Real FCM device delivery: **NOT VERIFIED** until test credentials and a disposable device token are supplied
- Real APNs device delivery: **NOT VERIFIED** until test credentials and a disposable device token are supplied
- PostgreSQL Patch 2/3 runtime: **VERIFIED** against local PostgreSQL 16.15, including outbox atomicity, worker claim races, lease recovery, local schedules, sync, and tenant isolation

## Architecture

The notification outbox and policy create one logical notification and one device-level delivery intent. The push worker claims each queued intent with a database lease, builds a versioned payload, and dispatches it through either `FCMProvider` or `APNsProvider`. Provider-specific imports, authentication, requests, and error mapping remain under `notifications/providers/`.

`sent` means FCM or APNs accepted the request. It does not mean the device displayed it. The `delivered` state is reserved for future provider/device feedback that actually supports that assertion.

## Configuration

Copy the variable names from `.env.example`; do not copy actual secrets into that file.

FCM uses the Firebase Admin SDK and application-default or service-account credentials:

- `COACHAI_FCM_ENABLED`
- `COACHAI_FCM_CREDENTIALS`
- `COACHAI_FCM_PROJECT_ID`
- `COACHAI_FCM_DRY_RUN`

APNs uses HTTP/2 token authentication with an ES256 `.p8` key:

- `COACHAI_APNS_ENABLED`
- `COACHAI_APNS_ENVIRONMENT` (`sandbox` or `production`)
- `COACHAI_APNS_KEY_ID`
- `COACHAI_APNS_TEAM_ID`
- `COACHAI_APNS_BUNDLE_ID`
- `COACHAI_APNS_PRIVATE_KEY`

`COACHAI_PUSH_TIMEOUT_SECONDS` bounds both APNs and FCM network requests. Invalid, non-finite, zero, or negative values safely fall back to 10 seconds instead of preventing the notification worker from starting.

## Delivery and retry lifecycle

Queued deliveries are claimed using `locked_at` and `locked_by`. A stale lease is recoverable by another worker. Transient, rate-limited, and unknown failures retry using the existing 30/60/120/240/480-second policy, capped at 15 minutes and five attempts. Provider `Retry-After` guidance is honored within that cap.

Accepted requests become `sent`. Permanent request, authentication, and configuration failures become `failed`. Permanent-token failures additionally disable the owning device, clear its token, and preserve the historical delivery row. One failed device never prevents other devices or channels from progressing.

## Payload and security

Payload version 1 contains only notification type and stable identifiers: notification, task, segment, segment index, schedule revision, operation, and an action identifier (`START`, `COMPLETE`, `SNOOZE`, `REPLAN`, or `OPEN`). It contains no credentials, tokens, or full database objects.

All device and delivery operations are scoped by `user_id`. Logs include stable delivery metadata and latency but never tokens, credentials, private keys, authorization headers, or notification body content. Provider credentials and `.p8` files must remain outside the repository.

Before every send, the worker rechecks notification expiration, task lifecycle state, and schedule revision. Foreground suppression, quiet hours, cooldowns, preferences, and channel selection remain the responsibility of the existing Patch 1/2 policy and runtime.
