# Smart Scheduling architecture

`task_segments` is the scheduling source of truth. A logical task remains one row in
`tasks`; a task may have zero to three ordered segment rows. The legacy
`tasks.scheduled_start` and `tasks.scheduled_end` fields are a compatibility cache of
the first segment start and last segment end. Application reads that need schedule
detail must use the grouped task-and-segment APIs, not infer a continuous block from
the cache.

## Allocation and persistence

- Scheduling is same-day only. `config.WORK_DAY_END` is the inclusive upper clock
  boundary; a segment may end at that boundary but cannot wrap past midnight.
- Flexible work uses at most `config.MAX_SEGMENTS_PER_TASK` segments. Each flexible
  segment is at least `config.MIN_SEGMENT_MINUTES` long.
- Continuity is preferred only when the resulting session is human-realistic.
  A reasonable whole window beats needless fragmentation, but an excessively long
  uninterrupted block may be represented as two or three sessions even when it fits.
- An unplaced task has no partial segments. The structured reasons are
  `capacity_shortfall` and `fragmentation_segment_cap`.
- Fixed tasks and breaks are immutable scheduling blocks. Imported Calendar events
  also block time.
- A full scheduling run keeps its deterministic input/order-index behavior and uses
  priority when multiple pending tasks can fill the same gap. During incremental
  flexible-task creation, the new task and existing pending, never-started Flexible
  tasks in the affected future region participate in the priority decision
  (`1` highest through `5` lowest). The service first tests the new task against
  the intact schedule. It replans only lower-priority eligible tasks that block
  the new task's proper earlier position; equal/higher-priority tasks and
  already-correct later placements remain stable. Eligible unscheduled tasks
  always join the competition because they have no valid window to preserve.
- The incremental replanning boundary is the latest segment end belonging to a
  started or terminal task, or the work-day start when no such protected work exists.
  Pending tasks with a segment before that boundary remain outside the affected
  region. Fixed tasks, breaks, Calendar blocks, and all other excluded segments are
  immutable anchors around which the eligible pool is allocated. Priority never
  overrides those hard constraints.
- Every plan has a monotonic `schedule_revision`. A scheduling write supplies its
  expected revision; stale writes fail without changing segments, cache, revision,
  or operation history.
- One successful scheduling run creates one `schedule_operations` record containing
  the before/after snapshots. Undo is operation-level and restores the prior snapshot
  atomically, subject to the same revision protection.

## Intelligent session suitability

Smart Scheduling, Manual Move repair, and Future Planning share the pure deterministic
rules in `session_suitability.py`. This is session-shaping policy, not another placement
engine: `SmartScheduler` and `FuturePlanningService` still own legal windows, times,
ordering, protected occupancy, and persistence. The LLM never chooses a session time or
boundary.

- There is no universal two-hour maximum and no `duration > N` split rule. A neutral
  preferred session grows as `sqrt(task duration * 90 minutes)`, so preference grows
  with the task but more slowly than its total duration.
- Existing title, description, and category metadata adjust the preference. Multilingual
  high-focus cues shorten it, leisure cues lengthen it, practical/admin cues modestly
  favor continuity, and unknown tasks use the neutral duration fallback. CoachAI has no
  reliable persisted per-user session preference today, so no historical behavior is
  fabricated.
- For an explicit deadline, pressure is the fraction of feasible minutes required by
  the remaining work. Pressure can scale the relaxed preference up to twice its value;
  this allows completion urgency to justify longer sessions without bypassing any hard
  constraint.
- Candidate session quality is squared deviation from the inferred preference plus an
  additional-session cost derived from that preference and
  `MIN_SEGMENT_MINUTES`. This penalizes excessive uninterrupted blocks and also prevents
  tiny or gratuitous fragments. Legal candidates are compared by session quality, then
  continuity, daily workload, and chronological deterministic tie-breakers.
- Feasibility always wins. Exact duration, protected intervals, earliest start,
  deadline, same-day bounds, the 15-minute minimum, and the three-segment maximum remain
  hard requirements. A constrained candidate may therefore use longer or less balanced
  sessions than the relaxed preference.
- Short tasks normally remain contiguous. Long cognitive tasks may be segmented;
  low-focus work may remain continuous for longer. Real breaks and Calendar/fixed
  commitments can become natural session boundaries. No fake breaks are created. When
  exact capacity has no real gap, adjacent physical segments preserve completion and
  expose the session boundary without inventing unavailable time.
- When a contiguous window has spare capacity, another eligible Flexible task may be
  placed between a long task's sessions. Priority, deadline, earliest start, and exact
  completion remain protected.

## Current-day manual move

- Manual Move is an explicit current-day operation for a pending, never-started
  Flexible task. It moves the complete logical duration into the requested continuous
  interval. If that task was split, its old segment set is replaced atomically.
- Fixed tasks, breaks, started/paused work, completed/failed work, imported or
  legacy-unknown Calendar events, and external Calendar blocks are protected anchors.
  A requested interval that overlaps one is rejected with no database write.
- The requested interval becomes a protected anchor for that operation. Every other
  scheduled pending Flexible task is passed through the same `SmartScheduler`; Manual
  Move has no independent placement engine.
- Breaks and other protected items remain occupied, but they do not partition Flexible
  work. A legal Flexible placement may move from one side of a Break to the other.
  Deadline, earliest start, priority, duration, feasibility, the 15-minute minimum,
  and the three-segment maximum continue to constrain Smart Scheduler placement.
- The requested move and every scheduling consequence are validated and persisted as one
  revision-checked `schedule_operations` record. A race in which an affected task
  starts before commit aborts the complete operation. Undo restores the exact prior
  segment and compatibility-cache snapshot.

## Write and voice planning intent

- The LLM extracts task identity, explicit duration, urgency/priority language, and
  explicit date constraints. For a new draft with no stated duration it supplies a
  realistic task-specific estimate and marks whether duration was user-explicit or
  estimated. Missing duration alone is not a clarification reason.
- An estimated duration never overwrites an existing task's persisted remaining work;
  only a duration explicitly provided by the user may do so.
- The LLM never selects clock times, task order, gaps, segments, or final placement.
  Deterministic scheduling services remain the sole placement authority.

## Calendar ownership and recovery

Calendar provenance is explicit:

- `imported`: external event; read-only and never deleted by CoachAI.
- `coachai_created`: CoachAI owns the per-segment event and may update/delete it.
- `legacy_unknown`: ownership cannot be proven; protected from destructive cleanup.

Calendar reconciliation is durable rather than fire-and-forget. Segment sync states
record pending/error work, deterministic event identities prevent duplicate creates,
and retry/reconciliation completes partial failures. Self-owned events are excluded
from blocking their originating task. A schedule database transaction can succeed
while Calendar is unavailable; the UI must then show the pending reconciliation state.

## Consumers and analytics

Task-oriented UI, history, chatbot, recommendations, timers, completion, and failure
remain at the logical-task level. Timelines and Calendar synchronization use individual
segments. Recovery-time analytics merge actual occupied segment intervals before
measuring free gaps. Context switching follows actual segment order while preserving
logical task identity; breaks do not count as work categories or create false switches.

## Database support boundary

SQLite migration code is SQLite-only. A fresh PostgreSQL database is defined from the
SQLAlchemy metadata and requires the PostgreSQL driver in `requirements.txt`. Migration
of an existing PostgreSQL schema is not currently supported. The current scheduler,
future-planner, analytics, transaction, constraint, and ownership contracts are verified
against local PostgreSQL 16.15 by `tests/postgres_patch5_verification.py`.
# Patch 5: Future Planning

Future Planning is a deterministic multi-day orchestration layer over the same
logical tasks, `task_segments`, constraints, revisions, operations, Calendar
ownership, and Undo mechanism used by current-day scheduling. It is not a
second scheduler. Every physical segment now has an effective local
`scheduled_date`; legacy/current-day segments inherit the parent plan date.

## Horizon and task constraints

The service accepts explicit inclusive `planning_start` and `planning_end`
local dates plus a work-day start. It never extends the horizon. Automated
Future Planning has a hard maximum of 12 weeks (`planning_end` may be at most
`planning_start + 12 weeks`). Longer requests are rejected in both preview and
apply; they are never truncated or partially persisted. `deadline` is
the latest permitted local completion timestamp, not a target date.
`earliest_start` is the first permitted local start timestamp. Both are stored
as user-local wall times; offset-aware values are rejected to prevent silent
UTC/local shifts. Remaining work is
`max(0, estimated_minutes - actual_minutes)`.

Fixed tasks, breaks, started tasks, terminal tasks, imported/legacy Calendar
segments, and external Calendar events are hard occupancy. CoachAI-owned
events are excluded from external blocking so a task never blocks itself.

## Deterministic ordering and placement

The exact policy is:

1. Build hard occupancy and legal free windows per local date.
2. Calculate each flexible task's feasible capacity before its effective
   completion boundary and subtract remaining work to obtain slack.
3. Rank tasks by lower slack, then priority (`1` first), completion boundary,
   and stable task ID. This schedules constrained work before flexible work and
   prevents low-urgency work from consuming capacity needed by tighter tasks.
4. For each task, retain its existing exact placement when it is legal and its session
   shape is already optimal under the shared suitability model.
5. Otherwise enumerate legal one-, two-, and three-window representations.
   Reject candidates that violate the 15-minute minimum, exact remaining
   duration, same-day rule, work-day end, earliest start, deadline, or anchors.
6. Score feasible candidates by shared session suitability, then fewer segments,
   lowest maximum daily load, lowest squared daily load, and the chronological tuple.
   Thus workload balance breaks ties after session quality rather than overriding it.
   There is no randomness and no LLM placement decision.

The maximum remains three physical segments per logical task, including when
those segments occur on different dates. A task that cannot be represented in
full is left unscheduled; no partial segments are persisted. Structured reasons
distinguish impossible deadlines, earliest-start outside the horizon,
insufficient feasible capacity, and fragmentation/segment-cap limits.

## Preview, commit, concurrency, and Undo

`FuturePlanningService.preview()` is read-only and returns named outcomes,
old/new dated windows, explanations, expected plan revisions, and task-state
fingerprints. `apply()` rechecks every revision and task fingerprint inside one
transaction, validates the complete candidate against tenant-owned occupancy,
updates segment/cache state, increments each affected daily revision, and
writes one versioned `schedule_operations` record. Any failure rolls back the
entire horizon.

Undo recognizes the versioned multi-plan snapshot, verifies every resulting
revision, and restores the exact previous segments and compatibility cache
without rerunning planning. CoachAI-owned Calendar identities are preserved
only for unchanged physical segments; changed/removed identities remain in the
operation snapshots for ownership-aware reconciliation and retry.

## Analytics and UI

Analytics still counts each logical task once. Physical occupancy, recovery,
density, and switching use each segment's effective date. The Future Planning
page provides range selection, dry-run preview, readable old-to-new changes,
structured unplaced reasons, atomic Apply, and one-operation Undo. History labels
plans as logical planning batches anchored to one date, reports logical task
estimates separately from per-date physical segment occupancy, and includes each
segment's actual date in its window label.

## Explicit non-goals

Patch 5 does not add FastAPI, recurrence, weekly templates, task dependencies,
resource optimization, LLM scheduling, a parallel segment table, a parallel
operation system, or a destructive whole-database rescheduler.
