# Zen Maestro Android handoff

The Android app is implemented with Kotlin and Jetpack Compose. It includes
Firebase authentication, Home, Schedule, Future Planning, Focus timer,
Analytics, Coach chat/report, profile, notification preferences, Calendar, and
app settings. Home and Analytics use bundled, hardened HTML presentation layers
fed only by native repositories; authentication and business mutations remain
native.

## Schedule source

`app/src/main/java/com/coachai/android/ui/schedule/ScheduleScreen.kt`

The screen contains the no-plan state, manual/AI/voice task creation, capacity
checks, protected breaks, summary counters, timeline controls, Focus Mode,
pause/resume/complete, Undo snackbars, task management, native Android time
pickers, scheduler settings, exact-reminder preference, and the Google Calendar
sync states used by the approved mockup.

Task, scheduling, Future Planning, profile, analytics, coach, Calendar, and
notification-preference flows use the FastAPI backend. Firebase supplies the ID
token. Provider-backed Calendar, Gemini, and FCM delivery additionally require
the server-side credentials documented in the root `.env.example`.

## Build

The project targets API 37 and uses Gradle 9.6 / Android Gradle Plugin 9.4.
Run unit tests with `gradlew.bat testDebugUnitTest` and build a debug APK with
`gradlew.bat assembleDebug` after the wrapper and Maven dependencies are
available.

Create `app/google-services.json` from the Firebase console. It is ignored by
Git and must be shared through a secure channel, never committed. For a physical
USB device, run `adb reverse tcp:8000 tcp:8000` while the local API is running.
