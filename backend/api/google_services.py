"""
google_services.py — Gemini-powered Gmail + Calendar
======================================================
This is where Gemini does its job.
Claude (orchestrator) calls these functions when an employee
needs Google Workspace actions. Gemini handles the heavy lifting
because it's natively optimized for Google's APIs.

Architecture:
  Claude decides WHAT to do
  → calls these functions
  → Gemini reads/writes Google data
  → returns structured result to Claude
  → Claude formats the final response
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
from database.models import Employee, EmailSummary, OAuthToken

load_dotenv_flag = False
try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv_flag = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# GEMINI SETUP — new google.genai SDK
# ---------------------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client  = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL   = "gemini-2.5-pro"


# ===========================================================================
# GMAIL FUNCTIONS
# ===========================================================================

def get_gmail_service(employee_id: int, db: Session):
    """Returns an authenticated Gmail API service object."""
    creds = get_credentials(employee_id, db)
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds)


def read_recent_emails(employee_id: int, max_results: int = 10, db: Session = None) -> str:
    """
    Fetches and AI-summarizes the most recent emails for an employee.
    Uses Gemini to extract key info and action items.
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

        # Fetch recent message IDs
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
        for msg in messages[:5]:  # Process top 5 to avoid token overload
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

            # Extract body text
            body = _extract_email_body(full_msg["payload"])

            email_data.append({
                "id":        msg["id"],
                "thread_id": thread_id,
                "subject":   subject,
                "sender":    sender,
                "date":      date,
                "body":      body[:1000],  # cap at 1000 chars per email
            })

        # Use Gemini to summarize and extract action items
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

        # Cache summaries in DB to avoid re-processing
        for email in email_data:
            existing = db.query(EmailSummary).filter(
                EmailSummary.employee_id     == employee_id,
                EmailSummary.gmail_thread_id == email["thread_id"]
            ).first()

            if not existing:
                db.add(EmailSummary(
                    employee_id     = employee_id,
                    gmail_thread_id = email["thread_id"],
                    subject         = email["subject"],
                    sender          = email["sender"],
                    received_at     = email["date"],
                    summary         = summary,
                ))

        db.commit()
        return f"📧 Email Summary ({len(email_data)} unread):\n\n{summary}"

    except Exception as e:
        return f"Gmail error: {str(e)}"
    finally:
        if close_db:
            db.close()


def draft_email_reply(
    employee_id: int,
    thread_id: str,
    instruction: str,
    db: Session = None
) -> str:
    """
    Uses Gemini to draft an email reply in the employee's communication style.
    Reads the thread context first, then drafts a contextually appropriate reply.
    """
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

        # Get employee info for style context
        emp = db.query(Employee).filter(Employee.id == employee_id).first()
        emp_name = emp.name if emp else "Employee"

        # Fetch the thread
        thread = service.users().threads().get(
            userId="me",
            id=thread_id,
            format="full"
        ).execute()

        # Extract thread messages
        thread_text = ""
        for msg in thread.get("messages", [])[-3:]:  # last 3 messages for context
            headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            sender  = headers.get("From", "Unknown")
            body    = _extract_email_body(msg["payload"])
            thread_text += f"\nFrom: {sender}\n{body[:500]}\n---"

        # Gemini drafts the reply
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


def send_email(
    employee_id: int,
    to: str,
    subject: str,
    body: str,
    db: Session = None
) -> str:
    """Sends an email from the employee's Gmail account."""
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

        # Get employee's Gmail address from their profile
        profile  = service.users().getProfile(userId="me").execute()
        from_email = profile.get("emailAddress", "me")

        emp      = db.query(Employee).filter(Employee.id == employee_id).first()
        emp_name = emp.name if emp else "Nexus User"

        # Build email with proper headers and charset
        message = MIMEMultipart("alternative")
        message["to"]      = to
        message["from"]    = f"{emp_name} <{from_email}>"
        message["subject"] = subject
        message.attach(MIMEText(body, "plain", "utf-8"))

        # Encode correctly — Gmail API needs urlsafe base64 without padding issues
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
                f"Message ID: {msg_id}\n"
                f"Check your Sent folder in Gmail to confirm.")

    except Exception as e:
        return f"Send error: {str(e)}"
    finally:
        if close_db:
            db.close()


def get_email_summary_from_cache(employee_id: int, db: Session) -> str:
    """Returns cached email summaries from DB — avoids hitting Gmail API repeatedly."""
    summaries = db.query(EmailSummary).filter(
        EmailSummary.employee_id == employee_id,
        EmailSummary.is_actioned == False
    ).order_by(EmailSummary.created_at.desc()).limit(10).all()

    if not summaries:
        return "No cached email summaries. Try 'check my emails' to fetch fresh ones."

    result = []
    for s in summaries:
        result.append(f"📧 {s.subject}\nFrom: {s.sender} | {s.received_at}\n{s.summary or 'No summary yet'}")

    return "\n\n".join(result)


# ===========================================================================
# GOOGLE CALENDAR FUNCTIONS
# ===========================================================================

def get_calendar_service(employee_id: int, db: Session):
    """Returns an authenticated Calendar API service object."""
    creds = get_credentials(employee_id, db)
    if not creds:
        return None
    return build("calendar", "v3", credentials=creds)


def get_upcoming_events(employee_id: int, days: int = 7, db: Session = None) -> str:
    """
    Fetches upcoming calendar events for the next N days.
    Returns a formatted summary using Gemini.
    """
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

        # Gemini formats it nicely
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


def check_availability(
    employee_id: int,
    date_str: str,
    duration_minutes: int = 60,
    db: Session = None
) -> str:
    """
    Checks an employee's availability on a specific date.
    Returns free slots of the requested duration.
    Used by Claude's scheduling logic.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        if not is_google_connected(employee_id, db):
            return f"Employee {employee_id} hasn't connected Google Calendar. Cannot check real availability."

        service = get_calendar_service(employee_id, db)
        if not service:
            return "Failed to connect to Calendar."

        # Parse the date
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            target_date = datetime.now(timezone.utc) + timedelta(days=1)

        # Fetch events for that day
        day_start = target_date.replace(hour=0, minute=0, second=0)
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

        # Find free slots (9am-6pm working hours)
        work_start = target_date.replace(hour=9,  minute=0,  second=0)
        work_end   = target_date.replace(hour=18, minute=0,  second=0)

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
    db: Session = None
) -> str:
    """Creates a real Google Calendar event for an employee."""
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
            calendarId=  "primary",
            body=        event,
            sendUpdates= "all" if attendee_emails else "none",
        ).execute()

        return f"✅ Calendar event '{title}' created for {start_time}. Event ID: {created.get('id')}"

    except Exception as e:
        return f"Calendar event error: {str(e)}"
    finally:
        if close_db:
            db.close()


def get_focus_time_suggestions(employee_id: int, db: Session = None) -> str:
    """
    Analyzes calendar patterns to suggest focus time blocks.
    This feeds the digital twin — learns when the employee does deep work.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        events_summary = get_upcoming_events(employee_id, days=14, db=db)

        prompt = f"""Based on this employee's calendar for the next 2 weeks:

{events_summary}

Analyze their schedule and suggest:
1. Their best focus time windows (when they have no meetings)
2. Days that are overloaded with meetings
3. Recommended time blocks for deep work
4. Any concerning patterns (too many back-to-backs, no breaks, etc.)

Be specific with times and actionable in your recommendations."""

        response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return response.text

    except Exception as e:
        return f"Focus time analysis error: {str(e)}"
    finally:
        if close_db:
            db.close()


# ===========================================================================
# HELPERS
# ===========================================================================

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
    # Strip HTML tags if any slipped through
    body = re.sub(r"<[^>]+>", " ", body)
    return body.strip()


def _find_free_slots(
    busy_slots: list,
    work_start: datetime,
    work_end: datetime,
    duration_min: int
) -> list:
    """Finds free time slots given a list of busy periods."""
    free = []
    cursor = work_start

    # Sort busy slots
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

    # Check after last event
    remaining = (work_end - cursor).total_seconds() / 60
    if remaining >= duration_min:
        free.append({
            "start": cursor,
            "end":   cursor + timedelta(minutes=duration_min),
        })

    return free