# CoachAI Android — Firebase Auth handoff

This project contains the Android frontend. Firebase SDK setup and backend token injection are implemented; the sign-in UI and provider-specific sign-in flow are still pending.

## Project details

- Application ID: `com.zenmaestro.app`
- Minimum Android SDK: 26
- UI: Jetpack Compose with Light and Dark themes
- API base URL is read from `COACHAI_API_BASE_URL` in `gradle.properties`
- The local development URL currently uses `http://127.0.0.1:8000` with `adb reverse tcp:8000 tcp:8000`

## Firebase integration point

The API clients already request a token through the `AuthTokenProvider` interface in:

`app/src/main/java/com/coachai/android/data/remote/TaskApi.kt`

The application injects `FirebaseAuthTokenProvider` in:

`app/src/main/java/com/coachai/android/CoachAiApplication.kt`

`FirebaseAuthTokenProvider` reads the current Firebase user's ID token. All existing repositories send that token without changes to individual screens.

## Backend contract

Every private API request must include:

`Authorization: Bearer <Firebase ID token>`

The backend verifies the Firebase project ID and derives the user identity from the verified token. Do not send a user ID from the UI as a substitute for authentication.

## Required Firebase work

1. Build the sign-in and account-creation flow.
2. Persist the Firebase session using the Firebase SDK.
3. Handle signed-out, loading, expired-token, and disabled-account states.
4. Configure the backend `FIREBASE_PROJECT_ID` to match the Android Firebase project.

## Acceptance checks

- Signed-out users cannot open private account data.
- A signed-in user can load and edit Profile.
- Analytics and Notification Preferences send the same Firebase bearer token.
- Expired tokens are refreshed by Firebase and requests retry safely.
- Release builds contain no fixed test token or Firebase secret.
- Production builds use an HTTPS API URL instead of the local ADB URL.

## Important current limitations

- Home is connected to `GET /v1/home`; Schedule still needs its scheduling API contract.
- Ask CoachAI, Coach Report, and Calendar still need their mobile HTTP routes.
- Firebase configuration files and credentials are intentionally not included in this archive.
