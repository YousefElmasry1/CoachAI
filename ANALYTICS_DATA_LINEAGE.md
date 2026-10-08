# CoachAI Analytics Data Lineage (Patch 4)

## Authoritative input and filtering

All analytics are loaded by `AnalyticsLoader`. The task query joins `tasks` to
`plans` and optionally `categories`, filters by the requested `plans.user_id`,
and includes exactly the user's local current day plus the preceding
`window_days - 1` dates. A second tenant-scoped query loads `task_segments` for
those task IDs. Each task becomes one `TaskSnapshot`; segments are attached as
an ordered tuple and never create duplicate logical task rows.

Break rows are excluded from every logical-task aggregation. They remain in
`schedule_snapshots` only so physical occupancy, recovery, and chronology can
include them. Segment rows are authoritative for scheduled intervals; the
legacy `tasks.scheduled_start`/`scheduled_end` cache is not used when building
the timeline.

## Metric lineage and semantics

Each line below follows: **metric -> source -> transformation/calculation ->
expected displayed semantics**.

### Summary and productivity

- Total Tasks, Completed, Failed, Pending, In Progress -> logical non-break
  task rows and `status` -> count once per `task_id` -> logical task counts.
- Completion Rate / Failure Rate -> logical counts -> completed or failed
  divided by total logical tasks -> rate over tasks, not segments.
- Estimated/Actual totals and averages -> logical `estimated_minutes` and
  non-null `actual_minutes` -> sums and arithmetic means -> one duration value
  per task.
- Average/Maximum Delay -> measured logical tasks ->
  `max(0, actual_minutes - estimated_minutes)` -> delay in minutes.
- Planning Error / Accuracy -> measured logical tasks with a positive estimate
  -> mean `(actual-estimated)/estimated`; accuracy is
  `clamp(1-abs(mean_error), 0, 1)` -> estimate reliability.
- Productivity Score and components -> completion, planning accuracy, focus,
  consistency, delay and failure components produced from the same logical
  aggregates -> documented weighted composite -> 0-100 task-performance score.

### Priority, category, failure, and planning views

- Priority metrics -> logical tasks grouped by `priority` -> counts,
  completion/failure rates, delay, planning accuracy and focus per group -> one
  contribution per task.
- Category metrics -> logical tasks grouped by resolved category name -> the
  same group aggregates plus category habit score -> no segment duplication.
- Failure reason distribution / main reason -> logical failed tasks and
  `failure_reason` -> count by reason and select the largest group -> task-level
  failure analysis.
- Planning chart -> logical measured tasks -> under/accurate/over estimate
  buckets from relative actual-vs-estimated error -> estimate behavior, not
  schedule occupancy.

### Time trends, patterns, insights, and confidence

- Daily trend -> logical tasks grouped by `plan_date` -> per-day completion
  rate, comparing the first and second halves where enough days exist ->
  task-completion trend.
- Current/longest streak and active days -> local calendar dates containing at
  least one completed logical task -> consecutive-day scan -> user-local task
  completion streak.
- Best Hour -> logical tasks grouped by the first authoritative segment start
  hour -> highest completion rate subject to the minimum sample -> task
  outcome by start hour.
- Patterns and Insights -> the preceding logical aggregates -> rule-based
  thresholds with confidence metadata -> explanatory observations only; they
  do not mutate data or fabricate samples.
- Correlations -> paired logical task attributes/outcomes with sufficient
  samples -> correlation calculation and confidence -> association, not
  causation.
- Heatmaps -> logical task groupings by weekday/hour/category as named by each
  heatmap -> counts/rates per cell -> one task per applicable cell.

### Habit, duration, weekday/weekend, and capacity

- Overall/category/activity/time-of-day habit scores -> matching logical tasks
  -> `100 * frequency * recency * completion_rate` -> task habit consistency.
- Duration buckets -> logical `estimated_minutes` -> bucket counts,
  completion/failure, planning accuracy, delay, and focus -> logical outcome by
  estimated duration.
- Weekday vs Weekend -> logical tasks classified from `plans.plan_date` ->
  counts, outcome rates, delay, focus, and completion adjusted for delay ->
  task performance by calendar group.
- Realistic Daily Capacity -> logical tasks grouped by date -> sum estimated
  minutes and calculate completion rate per day; median load of successful days
  when sufficiently sampled, otherwise documented fallback -> planned logical
  workload, never occupied segment minutes.

### Physical schedule and burnout metrics

- Schedule Density -> all authoritative segment intervals, including breaks ->
  merge overlapping or touching intervals for each day, sum occupied minutes,
  then average across scheduled dates -> physical occupancy without overlap or
  split-task double counting.
- Recovery Time -> the same per-day merged occupied intervals -> measure only
  gaps between consecutive occupied intervals; exclude time before the first
  and after the last interval -> mean measurable recovery gap.
- Context Switching -> chronological authoritative intervals -> ignore breaks
  as work categories and compare the next non-break logical task/category;
  repeated segments of the same `task_id` do not switch -> `A/A=0`,
  `A/Break/A=0`, `A/Break/B=1` when work categories differ.
- Time Fragmentation -> logical non-break tasks and their segment tuples ->
  extra segments divided by physical non-break segment count -> split-schedule
  fragmentation, not additional tasks.
- Deep Work Score -> logical scheduled tasks whose uninterrupted segment
  duration meets the deep-work threshold -> share of qualifying work -> break
  rows excluded.
- Habit Stability -> daily logical task counts ->
  `1 - min(1, standard_deviation/mean)` -> consistency of daily task load.
- Planning Reliability -> task-level planning accuracy -> logical estimate
  reliability.
- Burnout Risk -> normalized density, failure rate, inverse planning
  reliability, context switching, fragmentation, and inverse habit stability ->
  arithmetic composite scaled to 0-100. Physical inputs use segments; outcome
  inputs use logical tasks.

## Empty and partial data

No rows produce an empty profile. Unscheduled tasks contribute to logical task
metrics but not physical schedule metrics. A break-only day has zero logical
tasks but may have physical occupancy. Missing actual duration excludes that
task from estimate-error and delay denominators. Missing segments never cause
the compatibility cache to be treated as an authoritative interval.

## Controlled verification

`tests/test_patch4_analytics.py` independently verifies known answers for:

- five logical tasks, six physical segments, and a break;
- one split task counted once but scheduled for the sum of both windows;
- overlapping and touching intervals merged exactly once;
- recovery gaps and context switches with breaks;
- empty, unscheduled, and break-only data;
- multi-user tenant isolation;
- manual move followed by exact operation Undo.

External integrations are not analytics inputs. No Google Calendar or
PostgreSQL runtime claim follows from this audit.
