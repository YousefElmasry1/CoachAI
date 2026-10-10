from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
import logging
from zoneinfo import ZoneInfo
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class GoogleCalendarError(Exception):
    pass


class GoogleCalendarEventIdGone(GoogleCalendarError):
    """A requested custom event ID is a permanent deleted-event tombstone."""

    pass


class GoogleCalendarAuthorizationRevoked(GoogleCalendarError):
    """The stored grant cannot be refreshed and requires reconnection."""

    pass

class GoogleCalendarClient:
    _SCOPES = [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events"
    ]

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        token_expiry: datetime,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout_seconds: float = 20.0,
    ):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_expiry = token_expiry
        self.client_id = client_id or os.environ.get("GOOGLE_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("GOOGLE_CLIENT_SECRET")
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self._service = None

    @staticmethod
    def build_auth_url(
        client_id: str,
        redirect_uri: str,
        *,
        state: str | None = None,
        code_challenge: str | None = None,
    ) -> str:
        """Build a Google authorization URL, optionally with state + PKCE.

        Secure application entry points should obtain both values from
        :class:`calendar_security.OAuthStateStore`.  Both are accepted as
        optional here only to preserve the existing desktop integration while
        it is migrated; supplying just one is rejected.
        """
        try:
            from urllib.parse import urlencode

            if (state is None) != (code_challenge is None):
                raise GoogleCalendarError(
                    "OAuth state and PKCE challenge must be supplied together."
                )

            params = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(GoogleCalendarClient._SCOPES),
                "access_type": "offline",
                "prompt": "consent",
            }
            if state is not None and code_challenge is not None:
                params.update({
                    "state": state,
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                })
            return f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}"
        except GoogleCalendarError:
            raise
        except Exception as e:
            raise GoogleCalendarError("Failed to build Google authorization URL.") from e

    @staticmethod
    def exchange_code(
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        *,
        code_verifier: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> dict:
        try:
            import requests as _requests

            request_data = {
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
            if code_verifier is not None:
                request_data["code_verifier"] = code_verifier
            bounded_timeout = max(1.0, min(float(timeout_seconds), 120.0))
            token_response = _requests.post(
                "https://oauth2.googleapis.com/token",
                data=request_data,
                timeout=bounded_timeout,
            )

            if token_response.status_code != 200:
                logger.warning(
                    "Google token exchange failed with HTTP status %s.",
                    token_response.status_code,
                )
                raise GoogleCalendarError(
                    "Google Calendar authorization could not be completed."
                )

            token_data = token_response.json()

            if not isinstance(token_data, dict) or not token_data.get("access_token"):
                raise GoogleCalendarError(
                    "Google Calendar authorization returned an invalid response."
                )

            expiry = None
            if "expires_in" in token_data:
                from datetime import timedelta
                expiry = datetime.now(dt_timezone.utc).replace(tzinfo=None) + timedelta(
                    seconds=int(token_data["expires_in"])
                )

            return {
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token"),
                "token_expiry": expiry,
            }
        except GoogleCalendarError:
            raise
        except Exception as e:
            raise GoogleCalendarError(
                "Google Calendar authorization could not be completed."
            ) from e

    def refresh_if_expired(self) -> dict | None:
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from google.auth.exceptions import RefreshError
            import requests
            
            creds = Credentials(
                token=self.access_token,
                refresh_token=self.refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=self._SCOPES,
                # Without this, google-auth's Credentials.expired property
                # always returns False (it only compares against
                # self.expiry, and treats a missing expiry as "not
                # expired") -- so refresh_if_expired() below would never
                # actually refresh anything, no matter how old the token
                # really is.
                expiry=self.token_expiry,
            )
            
            if creds.expired and creds.refresh_token:
                session = requests.Session()
                transport = Request(session=session)

                def bounded_request(**kwargs):
                    kwargs["timeout"] = self.timeout_seconds
                    return transport(**kwargs)

                try:
                    creds.refresh(bounded_request)
                except RefreshError as exc:
                    raise GoogleCalendarAuthorizationRevoked(
                        "Google Calendar authorization must be renewed."
                    ) from exc
                self.access_token = creds.token
                self.token_expiry = creds.expiry
                return {
                    "access_token": self.access_token,
                    "token_expiry": self.token_expiry
                }
            return None
        except GoogleCalendarAuthorizationRevoked:
            raise
        except Exception as e:
            raise GoogleCalendarError("Google Calendar token refresh failed.") from e

    def _build_service(self):
        if not self._service:
            try:
                from google.oauth2.credentials import Credentials
                from googleapiclient.discovery import build
                from google_auth_httplib2 import AuthorizedHttp
                import httplib2
                
                creds = Credentials(
                    token=self.access_token,
                    refresh_token=self.refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    scopes=self._SCOPES
                )
                transport = AuthorizedHttp(
                    creds, http=httplib2.Http(timeout=self.timeout_seconds)
                )
                self._service = build(
                    "calendar", "v3", http=transport, cache_discovery=False,
                )
            except Exception as e:
                raise GoogleCalendarError("Google Calendar client is unavailable.") from e
        return self._service

    def list_calendars(self) -> list[dict]:
        try:
            service = self._build_service()
            page_token = None
            calendars = []
            while True:
                calendar_list = service.calendarList().list(
                    pageToken=page_token
                ).execute(num_retries=3)
                for calendar_list_entry in calendar_list['items']:
                    calendars.append({
                        "calendar_id": calendar_list_entry.get("id"),
                        "name": calendar_list_entry.get("summary"),
                        "is_primary": calendar_list_entry.get("primary", False),
                        "color": calendar_list_entry.get("backgroundColor")
                    })
                page_token = calendar_list.get('nextPageToken')
                if not page_token:
                    break
            return calendars
        except Exception as e:
            raise GoogleCalendarError("Google Calendar list could not be loaded.") from e

    @staticmethod
    def _resolve_tz(timezone: str | None) -> ZoneInfo:
        """Resolve an IANA timezone name, falling back safely to UTC for
        an unrecognised/empty name rather than raising."""
        try:
            return ZoneInfo(timezone) if timezone else ZoneInfo("UTC")
        except Exception:
            return ZoneInfo("UTC")

    def fetch_events(self, calendar_id: str, event_date: str, timezone: str = "UTC") -> list[dict]:
        day = date.fromisoformat(event_date)
        return [
            {key: value for key, value in event.items() if key != "event_date"}
            for event in self.fetch_events_range(
                calendar_id, day, day, timezone=timezone,
            )
        ]

    def fetch_events_range(
        self,
        calendar_id: str,
        range_start: date,
        range_end: date,
        *,
        timezone: str = "UTC",
    ) -> list[dict]:
        """Fetch and split timed events for an inclusive range up to 12 weeks."""
        if range_end < range_start or (range_end - range_start).days + 1 > 84:
            raise GoogleCalendarError("Google Calendar range must be between 1 and 84 days.")
        try:
            service = self._build_service()
            tz = self._resolve_tz(timezone)
            start_of_range = datetime.combine(range_start, time.min, tzinfo=tz)
            end_of_range = datetime.combine(
                range_end + timedelta(days=1), time.min, tzinfo=tz
            )

            events: list[dict] = []
            page_token = None
            while True:
                page = service.events().list(
                    calendarId=calendar_id,
                    timeMin=start_of_range.isoformat(),
                    timeMax=end_of_range.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                ).execute(num_retries=3)
                events.extend(page.get("items", []))
                page_token = page.get("nextPageToken")
                if not page_token:
                    break

            result: list[dict] = []
            for event in events:
                if (
                    event.get("status") == "cancelled"
                    or "dateTime" not in event.get("start", {})
                    or "dateTime" not in event.get("end", {})
                ):
                    continue
                start_dt = datetime.fromisoformat(
                    event["start"]["dateTime"].replace("Z", "+00:00")
                ).astimezone(tz)
                end_dt = datetime.fromisoformat(
                    event["end"]["dateTime"].replace("Z", "+00:00")
                ).astimezone(tz)
                if end_dt <= start_dt:
                    continue
                first_day = max(range_start, start_dt.date())
                last_day = min(
                    range_end, (end_dt - timedelta(microseconds=1)).date()
                )
                current_day = first_day
                while current_day <= last_day:
                    day_start = datetime.combine(current_day, time.min, tzinfo=tz)
                    next_day = datetime.combine(
                        current_day + timedelta(days=1), time.min, tzinfo=tz
                    )
                    segment_start = max(start_dt, day_start)
                    segment_end = min(end_dt, next_day)
                    if segment_end > segment_start:
                        result.append({
                            "google_event_id": event["id"],
                            "title": event.get("summary", ""),
                            "start_time": segment_start.strftime("%H:%M:%S"),
                            "end_time": (
                                "23:59:59" if segment_end == next_day
                                else segment_end.strftime("%H:%M:%S")
                            ),
                            "event_date": current_day.isoformat(),
                            "calendar_id": calendar_id,
                        })
                    current_day += timedelta(days=1)
            return result
        except GoogleCalendarError:
            raise
        except Exception as e:
            raise GoogleCalendarError(
                "Google Calendar events could not be loaded."
            ) from e

    def create_event(
        self, calendar_id: str, title: str, start_time: str, end_time: str,
        event_date: str, timezone: str = "UTC", event_id: Optional[str] = None,
    ) -> str:
        try:
            service = self._build_service()

            tz = self._resolve_tz(timezone)
            date_obj = datetime.fromisoformat(event_date).date()
            start_t = time.fromisoformat(start_time)
            end_t = time.fromisoformat(end_time)

            # Attach the USER'S OWN timezone (never the server's local
            # timezone via a bare .astimezone()) -- otherwise Google
            # re-interprets these times as the server's zone and shifts
            # them when displaying in the user's real timezone.
            start_dt = self._validated_local_datetime(date_obj, start_t, tz)
            end_dt = self._validated_local_datetime(date_obj, end_t, tz)
            
            event = {
                'summary': title,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': timezone or "UTC",
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': timezone or "UTC",
                }
            }
            if event_id:
                event['id'] = event_id
            
            created_event = service.events().insert(
                calendarId=calendar_id, body=event
            ).execute(num_retries=3)
            return created_event['id']
        except Exception as e:
            if event_id and getattr(getattr(e, "resp", None), "status", None) == 409:
                try:
                    existing = service.events().get(
                        calendarId=calendar_id, eventId=event_id
                    ).execute(num_retries=3)
                    if existing.get("status") == "cancelled":
                        raise GoogleCalendarEventIdGone(
                            f"Event ID {event_id} is a deleted Google Calendar tombstone."
                        )
                    return event_id
                except GoogleCalendarEventIdGone:
                    raise
                except Exception:
                    pass
            raise GoogleCalendarError("Google Calendar event could not be created.") from e

    def update_event(
        self, calendar_id: str, google_event_id: str, title: str, start_time: str,
        end_time: str, event_date: str, timezone: str = "UTC",
    ):
        try:
            service = self._build_service()

            tz = self._resolve_tz(timezone)
            date_obj = datetime.fromisoformat(event_date).date()
            start_t = time.fromisoformat(start_time)
            end_t = time.fromisoformat(end_time)

            start_dt = self._validated_local_datetime(date_obj, start_t, tz)
            end_dt = self._validated_local_datetime(date_obj, end_t, tz)
            
            event = service.events().get(
                calendarId=calendar_id, eventId=google_event_id
            ).execute(num_retries=3)
            event['summary'] = title
            event['start'] = {
                'dateTime': start_dt.isoformat(),
                'timeZone': timezone or "UTC",
            }
            event['end'] = {
                'dateTime': end_dt.isoformat(),
                'timeZone': timezone or "UTC",
            }
            
            service.events().update(
                calendarId=calendar_id, eventId=google_event_id, body=event
            ).execute(num_retries=3)
        except Exception as e:
            raise GoogleCalendarError("Google Calendar event could not be updated.") from e

    def delete_event(self, calendar_id: str, google_event_id: str):
        try:
            service = self._build_service()
            service.events().delete(
                calendarId=calendar_id, eventId=google_event_id
            ).execute(num_retries=3)
        except Exception as e:
            # Google may retain a cancelled tombstone and answer 410 Gone
            # after a successful deletion.  Both responses mean the active
            # event is absent, so cleanup is idempotently complete.
            if getattr(getattr(e, "resp", None), "status", None) in {404, 410}:
                return
            raise GoogleCalendarError("Google Calendar event could not be deleted.") from e

    @staticmethod
    def _validated_local_datetime(date_obj, time_obj: time, tz: ZoneInfo) -> datetime:
        """Attach an IANA zone and reject nonexistent DST wall times."""
        naive = datetime.combine(date_obj, time_obj)
        aware = naive.replace(tzinfo=tz, fold=0)
        round_trip = aware.astimezone(dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
        if round_trip != naive:
            raise GoogleCalendarError(
                f"The local time {naive.isoformat()} does not exist in timezone {tz.key}."
            )
        return aware
