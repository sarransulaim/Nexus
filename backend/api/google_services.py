"""
google_services.py — Gemini-powered Gmail + Calendar
======================================================
Gemini handles all Google Workspace operations:
  - Read & summarize emails
  - Draft email replies
  - Send emails
  - Read calendar events
  - Check availability
  - Create calendar events
  - Suggest focus time blocks

FIX FROM PREVIOUS VERSION:
- Removed all EmailSummary table references (removed in v3 schema)
- Summaries are returned directly to user, not cached in DB
- This means each "check emails" call hits Gemini fresh, which is fine
  because the AI router will eventually use Gemini Flash (cheaper)
"""

import os
import json
import base64
import re
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import google.genai as genai
from google.genai import types
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from api.google_auth import get_credentials, is_google_connected
from database.core import SessionLocal
from database.models import Employee, OAuthToken

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Gemini setup ──────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client  = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
GEMINI_MODEL   = "gemini-2.5-pro"


# ═══════════════════════════════════════════════════════════════
# GMAIL FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def get_gmail_service(employee_id: int, db: Session):
    """Returns an authenticated Gmail API service object."""
    creds = get_credentials(employee_id, db)
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds)


def read_recent_emails(employee_id: int, max_results: int = 10, db: Session = None) -> str:
    """
    Fetches and summarizes the most recent unread emails for an employee.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        if not is_google_connected(employee_id, db):
            return "Google account not connected. Employee needs to authorize Google access first."

        service = get_gmail_service(employee_id, db)
        if not service:
            return "Failed to connect to Gmail."

        # Fetch recent unread message IDs
        results = service.users().messages().list(
            userId="me",
            maxResults=max_results,
            labelIds=["INBOX"],
            q="is:unread"
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return "No unread emails found."

        email_data = []
        for msg in messages[:5]:  # Top 5 to avoid token overload
            full_msg = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="full"
            ).execute()

            headers   = {h["name"]: h["value"] for h in full_msg["payload"].get("headers", [])}
            subject   = headers.get("Subject", "No subject")
            sender    = headers.get("From", "Unknown")
            date      = headers.get("Date", "")
            thread_id = full_msg.get("threadId", "")

            body = _extract_email_body(full_msg["payload"])

            email_data.append({
                "id":        msg["id"],
                "thread_id": thread_id,
                "subject":   subject,
                "sender":    sender,
                "date":      date,
                "body":      body[:1000],
            })

        if not gemini_client:
            return "Gemini API key not configured. Cannot summarize emails."

        # Summarize with Gemini
        prompt = f"""You are an AI assistant summarizing emails for a busy professional.

Here are their recent unread emails:

{json.dumps(email_data, indent=2)}

For each email provide:
1. A one-line summary
2. Whether it requires action (yes/no)
3. The action needed (if yes)
4. Priority (high/medium/low)

Be concise. Format as a clean readable list."""

        response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        summary  = response.text

        return f"📧 Email Summary ({len(email_data)} unread):\n\n{summary}"

    except Exception as e:
        return f"Gmail error: {str(e)}"
    finally:
        if close_db:
            db.close()


def draft_email_reply(employee_id: int, thread_id: str, instruction: str, db: Session = None) -> str:
    """Uses Gemini to draft an email reply in the employee's voice."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        if not is_google_connected(employee_id, db):
            return "Google account not connected."

        service = get_gmail_service(employee_id, db)
        if not service:
            return "Failed to connect to Gmail."

        emp = db.query(Employee).filter(Employee.id == employee_id).first()
        emp_name = emp.name if emp else "Employee"

        thread = service.users().threads().get(
            userId="me",
            id=thread_id,
            format="full"
        ).execute()

        thread_text = ""
        for msg in thread.get("messages", [])[-3:]:
            headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            sender  = headers.get("From", "Unknown")
            body    = _extract_email_body(msg["payload"])
            thread_text += f"\nFrom: {sender}\n{body[:500]}\n---"

        if not gemini_client:
            return "Gemini API key not configured."

        prompt = f"""You are drafting an email reply for {emp_name}.

Email thread context:
{thread_text}

Instructions for the reply: {instruction}

Draft a professional, natural email reply. Match the tone of the conversation.
Do not add subject line. Just write the email body.
Sign it as: {emp_name}"""

        response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        draft    = response.text

        return f"📝 Draft Reply:\n\n{draft}\n\n---\nReply to thread ID: {thread_id}\nSay 'send this' to send it or 'edit it to...' to modify."

    except Exception as e:
        return f"Draft error: {str(e)}"
    finally:
        if close_db:
            db.close()


def send_email(employee_id: int, to: str, subject: str, body: str, db: Session = None) -> str:
    """Sends an email from the employee's Gmail account."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # ── Recipient validation (polish: catch malformed / likely-typo addresses) ──
        to_clean = (to or "").strip()
        if "@" not in to_clean or "." not in to_clean.split("@")[-1]:
            return (f"That email address looks invalid: '{to}'. "
                    f"Please give me a valid recipient address (like name@company.com) and I'll send it.")

        local_part = to_clean.split("@")[0]
        domain = to_clean.split("@")[-1].lower()

        # Detect domains that are CLOSE to a known provider but not exact (catches
        # gmil, gmal, gmaill, gmail.cm, yahooo, hotmial, etc. without a fixed list).
        import difflib
        KNOWN_PROVIDERS = [
            "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
            "icloud.com", "aol.com", "protonmail.com", "live.com",
        ]
        if domain not in KNOWN_PROVIDERS:
            close = difflib.get_close_matches(domain, KNOWN_PROVIDERS, n=1, cutoff=0.8)
            if close:
                return (f"The address '{to}' looks like a typo — did you mean "
                        f"'{local_part}@{close[0]}'? "
                        f"I did NOT send it. Confirm the correct address and I'll send.")

        if not is_google_connected(employee_id, db):
            return "Google account not connected."

        service = get_gmail_service(employee_id, db)
        if not service:
            return "Failed to connect to Gmail."

        profile  = service.users().getProfile(userId="me").execute()
        from_email = profile.get("emailAddress", "me")

        emp      = db.query(Employee).filter(Employee.id == employee_id).first()
        emp_name = emp.name if emp else "Nexus User"

        message = MIMEMultipart("alternative")
        message["to"]      = to
        message["from"]    = f"{emp_name} <{from_email}>"
        message["subject"] = subject
        message.attach(MIMEText(body, "plain", "utf-8"))

        raw_bytes = message.as_bytes()
        raw_b64   = base64.urlsafe_b64encode(raw_bytes).decode("ascii")

        result = service.users().messages().send(
            userId="me",
            body={"raw": raw_b64}
        ).execute()

        msg_id = result.get("id", "unknown")
        return (f"✅ Email sent successfully!\n"
                f"To: {to}\n"
                f"Subject: {subject}\n"
                f"Message ID: {msg_id}")

    except Exception as e:
        return f"Send error: {str(e)}"
    finally:
        if close_db:
            db.close()


# ═══════════════════════════════════════════════════════════════
# CALENDAR FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def get_calendar_service(employee_id: int, db: Session):
    creds = get_credentials(employee_id, db)
    if not creds:
        return None
    return build("calendar", "v3", credentials=creds)


def get_upcoming_events(employee_id: int, days: int = 7, db: Session = None) -> str:
    """Fetches and formats upcoming calendar events."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        if not is_google_connected(employee_id, db):
            return "Google Calendar not connected."

        service = get_calendar_service(employee_id, db)
        if not service:
            return "Failed to connect to Google Calendar."

        now      = datetime.now(timezone.utc)
        end_time = now + timedelta(days=days)

        events_result = service.events().list(
            calendarId    = "primary",
            timeMin       = now.isoformat(),
            timeMax       = end_time.isoformat(),
            maxResults    = 20,
            singleEvents  = True,
            orderBy       = "startTime",
        ).execute()

        events = events_result.get("items", [])
        if not events:
            return f"No events in the next {days} days. Calendar is clear."

        event_list = []
        for e in events:
            start    = e["start"].get("dateTime", e["start"].get("date", ""))
            end      = e["end"].get("dateTime", e["end"].get("date", ""))
            title    = e.get("summary", "Untitled")
            location = e.get("location", "")
            attendees = [a.get("email", "") for a in e.get("attendees", [])]

            event_list.append({
                "title":     title,
                "start":     start,
                "end":       end,
                "location":  location,
                "attendees": attendees,
            })

        if not gemini_client:
            # Fallback to plain formatting if Gemini unavailable
            lines = [f"📅 Your next {days} days:\n"]
            for e in event_list:
                lines.append(f"  • {e['title']} — {e['start']}")
            return "\n".join(lines)

        prompt = f"""Format these calendar events into a clean, readable schedule summary:

{json.dumps(event_list, indent=2)}

Group by day. Use natural language like "Monday 9am - Team standup (45 min)".
Highlight any conflicts or back-to-back meetings.
Note if there's a particularly busy day."""

        response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return f"📅 Your next {days} days:\n\n{response.text}"

    except Exception as e:
        return f"Calendar error: {str(e)}"
    finally:
        if close_db:
            db.close()


def check_availability(employee_id: int, date_str: str, duration_minutes: int = 60, db: Session = None) -> str:
    """Checks availability on a specific date and returns free slots."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        if not is_google_connected(employee_id, db):
            return f"Employee {employee_id} hasn't connected Google Calendar."

        service = get_calendar_service(employee_id, db)
        if not service:
            return "Failed to connect to Calendar."

        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            target_date = datetime.now(timezone.utc) + timedelta(days=1)

        day_start = target_date.replace(hour=0,  minute=0,  second=0)
        day_end   = target_date.replace(hour=23, minute=59, second=59)

        events_result = service.events().list(
            calendarId   = "primary",
            timeMin      = day_start.isoformat(),
            timeMax      = day_end.isoformat(),
            singleEvents = True,
            orderBy      = "startTime",
        ).execute()

        busy_slots = []
        for e in events_result.get("items", []):
            start = e["start"].get("dateTime", "")
            end   = e["end"].get("dateTime",   "")
            if start and end:
                busy_slots.append({"start": start, "end": end, "title": e.get("summary", "Busy")})

        work_start = target_date.replace(hour=9,  minute=0, second=0)
        work_end   = target_date.replace(hour=18, minute=0, second=0)

        free_slots = _find_free_slots(busy_slots, work_start, work_end, duration_minutes)

        if not free_slots:
            return f"No free {duration_minutes}-minute slots on {date_str}. Fully booked."

        slot_strs = [f"{s['start'].strftime('%I:%M %p')} - {s['end'].strftime('%I:%M %p')}" for s in free_slots]
        return f"✅ Free {duration_minutes}-min slots on {date_str}:\n" + "\n".join(slot_strs)

    except Exception as e:
        return f"Availability check error: {str(e)}"
    finally:
        if close_db:
            db.close()


def create_calendar_event(
    employee_id: int,
    title: str,
    start_time: str,
    end_time: str,
    attendee_emails: list = None,
    description: str = "",
    db: Session = None,
) -> str:
    """Creates a Google Calendar event."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        if not is_google_connected(employee_id, db):
            return "Google Calendar not connected."

        service = get_calendar_service(employee_id, db)
        if not service:
            return "Failed to connect to Calendar."

        event = {
            "summary":     title,
            "description": description,
            "start":       {"dateTime": start_time, "timeZone": "UTC"},
            "end":         {"dateTime": end_time,   "timeZone": "UTC"},
        }

        if attendee_emails:
            event["attendees"] = [{"email": e} for e in attendee_emails]

        created = service.events().insert(
            calendarId  = "primary",
            body        = event,
            sendUpdates = "all" if attendee_emails else "none",
        ).execute()

        return f"✅ Calendar event '{title}' created for {start_time}. Event ID: {created.get('id')}"

    except Exception as e:
        return f"Calendar event error: {str(e)}"
    finally:
        if close_db:
            db.close()


def get_focus_time_suggestions(employee_id: int, db: Session = None) -> str:
    """Analyzes calendar patterns to suggest focus time blocks."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        events_summary = get_upcoming_events(employee_id, days=14, db=db)

        if not gemini_client:
            return "Gemini not configured. Here are your upcoming events:\n\n" + events_summary

        prompt = f"""Based on this employee's calendar for the next 2 weeks:

{events_summary}

Analyze their schedule and suggest:
1. Their best focus time windows (when they have no meetings)
2. Days that are overloaded with meetings
3. Recommended time blocks for deep work
4. Any concerning patterns (too many back-to-backs, no breaks)

Be specific with times and actionable in your recommendations."""

        response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return response.text

    except Exception as e:
        return f"Focus time analysis error: {str(e)}"
    finally:
        if close_db:
            db.close()


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _extract_email_body(payload: dict) -> str:
    """Recursively extracts plain text from email payload."""
    body = ""
    if payload.get("body", {}).get("data"):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
    elif payload.get("parts"):
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                break
            elif part.get("parts"):
                body = _extract_email_body(part)
                if body:
                    break
    body = re.sub(r"<[^>]+>", " ", body)
    return body.strip()


def _find_free_slots(busy_slots: list, work_start: datetime, work_end: datetime, duration_min: int) -> list:
    """Finds free time slots given a list of busy periods."""
    free = []
    cursor = work_start

    busy_sorted = sorted(busy_slots, key=lambda x: x["start"])

    for slot in busy_sorted:
        try:
            slot_start = datetime.fromisoformat(slot["start"].replace("Z", "+00:00"))
            slot_end   = datetime.fromisoformat(slot["end"].replace("Z",   "+00:00"))
        except Exception:
            continue

        gap_minutes = (slot_start - cursor).total_seconds() / 60
        if gap_minutes >= duration_min:
            free.append({
                "start": cursor,
                "end":   cursor + timedelta(minutes=duration_min),
            })
        cursor = max(cursor, slot_end)

    remaining = (work_end - cursor).total_seconds() / 60
    if remaining >= duration_min:
        free.append({
            "start": cursor,
            "end":   cursor + timedelta(minutes=duration_min),
        })

    return free