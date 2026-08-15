from __future__ import annotations

import os
from datetime import datetime, time

from dotenv import load_dotenv

load_dotenv()

class GoogleCalendarError(Exception):
    pass

class GoogleCalendarClient:
    _SCOPES = [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events"
    ]

    def __init__(self, access_token: str, refresh_token: str, token_expiry: datetime, client_id: str | None = None, client_secret: str | None = None):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_expiry = token_expiry
        self.client_id = client_id or os.environ.get("GOOGLE_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("GOOGLE_CLIENT_SECRET")
        self._service = None

    @staticmethod
    def build_auth_url(client_id: str, redirect_uri: str) -> str:
        try:
            from urllib.parse import urlencode

            params = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(GoogleCalendarClient._SCOPES),
                "access_type": "offline",
                "prompt": "consent",
            }
            return f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}"
        except Exception as e:
            raise GoogleCalendarError(f"Failed to build auth url: {e}")

    @staticmethod
    def exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
        try:
            import requests as _requests

            token_response = _requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=30,
            )

            if token_response.status_code != 200:
                error_detail = token_response.text
                print(f"[CoachAI] Google token exchange failed ({token_response.status_code}): {error_detail}")
                raise GoogleCalendarError(
                    f"Token exchange failed ({token_response.status_code}): {error_detail}"
                )

            token_data = token_response.json()

            expiry = None
            if "expires_in" in token_data:
                from datetime import timedelta
                expiry = datetime.utcnow() + timedelta(seconds=int(token_data["expires_in"]))

            return {
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token"),
                "token_expiry": expiry,
            }
        except GoogleCalendarError:
            raise
        except Exception as e:
            raise GoogleCalendarError(f"Failed to exchange code: {e}")

    def refresh_if_expired(self) -> dict | None:
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            
            creds = Credentials(
                token=self.access_token,
                refresh_token=self.refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=self._SCOPES
            )
            
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self.access_token = creds.token
                self.token_expiry = creds.expiry
                return {
                    "access_token": self.access_token,
                    "token_expiry": self.token_expiry
                }
            return None
        except Exception as e:
            raise GoogleCalendarError(f"Failed to refresh token: {e}")

    def _build_service(self):
        if not self._service:
            try:
                from google.oauth2.credentials import Credentials
                from googleapiclient.discovery import build
                
                creds = Credentials(
                    token=self.access_token,
                    refresh_token=self.refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    scopes=self._SCOPES
                )
                self._service = build("calendar", "v3", credentials=creds)
            except Exception as e:
                raise GoogleCalendarError(f"Failed to build service: {e}")
        return self._service

    def list_calendars(self) -> list[dict]:
        try:
            service = self._build_service()
            page_token = None
            calendars = []
            while True:
                calendar_list = service.calendarList().list(pageToken=page_token).execute()
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
            raise GoogleCalendarError(f"Failed to list calendars: {e}")

    def fetch_events(self, calendar_id: str, event_date: str) -> list[dict]:
        try:
            service = self._build_service()
            
            date_obj = datetime.fromisoformat(event_date)
            start_of_day = date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)
            
            time_min = start_of_day.isoformat() + 'Z'
            time_max = end_of_day.isoformat() + 'Z'
            
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            result = []
            
            for event in events:
                if 'dateTime' not in event.get('start', {}):
                    continue
                    
                start_dt = datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00'))
                end_dt = datetime.fromisoformat(event['end']['dateTime'].replace('Z', '+00:00'))
                
                result.append({
                    "google_event_id": event['id'],
                    "title": event.get('summary', ''),
                    "start_time": start_dt.strftime("%H:%M"),
                    "end_time": end_dt.strftime("%H:%M"),
                    "calendar_id": calendar_id
                })
                
            return result
        except Exception as e:
            raise GoogleCalendarError(f"Failed to fetch events: {e}")

    def create_event(self, calendar_id: str, title: str, start_time: str, end_time: str, event_date: str) -> str:
        try:
            service = self._build_service()
            
            date_obj = datetime.fromisoformat(event_date).date()
            start_t = time.fromisoformat(start_time)
            end_t = time.fromisoformat(end_time)
            
            start_dt = datetime.combine(date_obj, start_t)
            end_dt = datetime.combine(date_obj, end_t)
            
            event = {
                'summary': title,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': 'UTC',
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': 'UTC',
                }
            }
            
            created_event = service.events().insert(calendarId=calendar_id, body=event).execute()
            return created_event['id']
        except Exception as e:
            raise GoogleCalendarError(f"Failed to create event: {e}")

    def update_event(self, calendar_id: str, google_event_id: str, title: str, start_time: str, end_time: str, event_date: str):
        try:
            service = self._build_service()
            
            date_obj = datetime.fromisoformat(event_date).date()
            start_t = time.fromisoformat(start_time)
            end_t = time.fromisoformat(end_time)
            
            start_dt = datetime.combine(date_obj, start_t)
            end_dt = datetime.combine(date_obj, end_t)
            
            event = service.events().get(calendarId=calendar_id, eventId=google_event_id).execute()
            event['summary'] = title
            event['start'] = {
                'dateTime': start_dt.isoformat(),
                'timeZone': 'UTC',
            }
            event['end'] = {
                'dateTime': end_dt.isoformat(),
                'timeZone': 'UTC',
            }
            
            service.events().update(calendarId=calendar_id, eventId=google_event_id, body=event).execute()
        except Exception as e:
            raise GoogleCalendarError(f"Failed to update event: {e}")

    def delete_event(self, calendar_id: str, google_event_id: str):
        try:
            service = self._build_service()
            service.events().delete(calendarId=calendar_id, eventId=google_event_id).execute()
        except Exception as e:
            raise GoogleCalendarError(f"Failed to delete event: {e}")
