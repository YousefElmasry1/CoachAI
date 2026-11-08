# CoachAI Schedule — Android frontend handoff

## What this package contains

- A native Kotlin + Jetpack Compose implementation of the approved Schedule screen.
- The app launcher opens Schedule directly from `MainActivity`.
- No WebView and no embedded HTML are used by the Schedule implementation.
- Phone bottom navigation and tablet navigation rail layouts.

## Main implementation

`app/src/main/java/com/coachai/android/ui/schedule/ScheduleScreen.kt`

## Implemented UI flows

- Empty/no-plan state and live summary counters.
- Manual task and protected-break creation.
- AI text parsing preview and capacity warning.
- Voice recording/transcript review preview.
- Native Android time picker for task, break, move, and work-start times.
- Smart re-plan and whole-schedule undo.
- Timeline task cards with Start, Pause, Resume, Complete, Undo, and More.
- Full-screen Focus Mode with a running countdown and notification preview.
- Move, fail, defer-to-tomorrow, and delete actions with Undo snackbars.
- Priority distribution and focus-pattern cards.
- Optional precise-reminder preference.
- Google Calendar disconnected, pending, syncing, synced, and failed states;
  sync, retry, export, import, and disconnect interactions.

## Backend/platform integration boundary

The handoff intentionally keeps AI generation, speech transcription, Calendar
sync, persistence, and remote task mutations as local interactive state. Replace
those callbacks with the approved services and repositories while preserving the
Compose UI and state labels. The active Android foreground notification for a
production timer should be connected to the Focus Mode callbacks.

## Open and run

1. Open this folder in Android Studio.
2. Let Gradle sync.
3. Select an emulator or connected Android device.
4. Run the `app` configuration.

