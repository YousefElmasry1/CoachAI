# Notification worker runtime

Run notification processing as a dedicated supervised process:

```text
python -m notifications
```

Do not run this loop in FastAPI startup/lifespan hooks. API processes and this
worker are separate deployment roles. Multiple notification-worker processes
may run when extra throughput is required; the existing database claims and
leases prevent concurrent claims, while abandoned leases become recoverable.

Required configuration:

- `DATABASE_URL`

Optional operational configuration:

- `COACHAI_NOTIFICATION_WORKER_ID` (otherwise host/PID/random suffix)
- `COACHAI_NOTIFICATION_POLL_SECONDS` (default `1`)
- `COACHAI_NOTIFICATION_ERROR_BACKOFF_SECONDS` (default `5`)
- `COACHAI_NOTIFICATION_BATCH_SIZE` (default `20`)
- `COACHAI_NOTIFICATION_LEASE_SECONDS` (default `300`)
- `COACHAI_NOTIFICATION_MAX_ATTEMPTS` (default `5`)
- `COACHAI_NOTIFICATION_LEASE_SAFETY_MARGIN_SECONDS` (default `30`)
- `COACHAI_NOTIFICATION_HEALTH_STALE_SECONDS` (default `300`)
- `COACHAI_NOTIFICATION_LOG_LEVEL` (default `INFO`)
- `COACHAI_PUSH_TIMEOUT_SECONDS` (default `10`)

Startup rejects a lease shorter than the worst-case configured serial push
batch (`batch size * push timeout + safety margin`). This prevents a second
worker from reclaiming a still-running batch under normal timeout behavior.
The health staleness window must cover that same worst-case batch so a healthy
worker is not restarted while it is still completing valid provider calls.

`NotificationWorkerRuntime.health_snapshot()` and `metrics_snapshot()` return
secret-free dictionaries for a process supervisor or metrics sidecar.
`add_snapshot_hook()` can publish both snapshots after each cycle; hook errors
are isolated from delivery. SIGINT and SIGTERM stop polling, allow the active
bounded batch to finish, restore prior signal handlers, and close the database.

Provider environment is validated before the database connection is opened.
Physical-device FCM/APNs delivery still requires external credentials and a
real device and is not established by the deterministic runtime tests.
