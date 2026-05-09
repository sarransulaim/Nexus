"""
slack_bot.py — Nexus Slack Integration
========================================
Employees DM the Nexus bot in Slack.
The bot routes their message to their personal AI agent
in the Claude orchestrator, then replies in Slack.

Architecture:
  Employee DMs @Nexus in Slack
       ↓
  slack_bot.py receives via Socket Mode (no public URL needed)
       ↓
  Looks up which employee this Slack user maps to
       ↓
  Sends command to Claude orchestrator (same 70+ tools)
       ↓
  Replies in Slack with the result
       ↓
  Any DB changes broadcast via WebSocket to dashboard

Run alongside the FastAPI server:
  python slack_bot.py
"""

import os
import json
import asyncio
import threading
import logging
from dotenv import load_dotenv

load_dotenv()

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# ── Imports from our existing system ──────────────────────────
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.core import SessionLocal
from database.models import Employee
from api.claude_orchestrator import run_orchestrator

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nexus.slack")

# ── Simple JSON file for Slack→Employee mapping ───────────────
# Avoids needing ChannelConnection DB table
LINKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slack_links.json")

def load_links() -> dict:
    if os.path.exists(LINKS_FILE):
        with open(LINKS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_links(links: dict):
    with open(LINKS_FILE, "w") as f:
        json.dump(links, f, indent=2)

# ── Slack App ─────────────────────────────────────────────────
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")

if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
    raise ValueError("Missing SLACK_BOT_TOKEN or SLACK_APP_TOKEN in .env")

app = App(token=SLACK_BOT_TOKEN)


# ═══════════════════════════════════════════════════════════════
# EMPLOYEE LOOKUP
# ═══════════════════════════════════════════════════════════════

def get_employee_by_slack_id(slack_user_id: str) -> Employee | None:
    """Finds the Nexus employee linked to this Slack user ID."""
    links = load_links()
    employee_id = links.get(slack_user_id)
    if not employee_id:
        return None
    db = SessionLocal()
    try:
        return db.query(Employee).filter(Employee.id == employee_id).first()
    finally:
        db.close()


def link_employee_to_slack(employee_id: int, slack_user_id: str, slack_username: str):
    """Creates a permanent link between a Nexus employee and their Slack user ID."""
    links = load_links()
    links[slack_user_id] = employee_id
    save_links(links)
    log.info(f"Linked Employee {employee_id} → Slack {slack_user_id} ({slack_username})")


def log_message(employee_id: int, direction: str, content: str, channel: str = "slack"):
    """Simple console log — DB logging optional."""
    log.info(f"[{direction.upper()}] Employee {employee_id}: {content[:80]}")


# ═══════════════════════════════════════════════════════════════
# COMMAND ROUTING
# ═══════════════════════════════════════════════════════════════

def route_to_agent(employee: Employee, message: str) -> str:
    """
    Sends the employee's message to their personal AI agent
    and returns the response.
    """
    agent_id = f"Employee_{employee.id}"
    try:
        response = run_orchestrator(agent_id=agent_id, command=message)
        # Trigger WebSocket broadcast so dashboard updates instantly
        _trigger_sync()
        return response
    except Exception as e:
        log.error(f"Agent error for {agent_id}: {e}")
        return "Sorry, I ran into an issue processing that. Please try again."


def _trigger_sync():
    """
    Hits the FastAPI backend to broadcast SYNC_REQUIRED to all
    connected dashboard clients. Runs in background so it doesn't
    block the Slack response.
    """
    import threading
    def _call():
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://localhost:8000/api/v1/internal/sync",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=b"{}",
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass  # Non-critical — dashboard will sync on next poll
    threading.Thread(target=_call, daemon=True).start()


def format_for_slack(text: str) -> str:
    """
    Converts markdown-ish text to Slack mrkdwn format.
    Claude responds in markdown, Slack uses its own format.
    """
    # Bold: **text** → *text*
    import re
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    # Code blocks
    text = re.sub(r'`(.+?)`', r'`\1`', text)
    # Limit length for Slack
    if len(text) > 3000:
        text = text[:2950] + "\n\n_...response truncated. Open Nexus for full details._"
    return text


# ═══════════════════════════════════════════════════════════════
# SLACK EVENT HANDLERS
# ═══════════════════════════════════════════════════════════════

@app.event("message")
def handle_dm(event, say, client):
    """
    Handles direct messages to the Nexus bot.
    This is the main entry point for employee interactions.
    """
    # Ignore bot messages to prevent infinite loops
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return

    channel_type = event.get("channel_type", "")
    if channel_type != "im":
        return  # Only handle DMs, not channel mentions here

    slack_user_id = event.get("user")
    text          = event.get("text", "").strip()

    if not text or not slack_user_id:
        return

    log.info(f"DM from Slack user {slack_user_id}: {text[:60]}")

    # Look up the employee
    employee = get_employee_by_slack_id(slack_user_id)

    if not employee:
        # Not linked yet — ask them to identify themselves
        say(
            text=(
                "👋 *Welcome to Nexus!*\n\n"
                "I don't recognize your Slack account yet. "
                "Please tell me your name so I can link you to your Nexus account:\n\n"
                "_Type: `I am [Your Name]`_"
            )
        )

        # Handle name identification in the next message
        if text.lower().startswith("i am "):
            name = text[5:].strip()
            db = SessionLocal()
            try:
                match = db.query(Employee).filter(
                    Employee.name.ilike(f"%{name}%"),
                    Employee.system_role == "employee"
                ).first()

                if match:
                    # Get Slack profile for username
                    try:
                        profile = client.users_info(user=slack_user_id)
                        slack_username = profile["user"]["name"]
                    except Exception:
                        slack_username = slack_user_id

                    link_employee_to_slack(match.id, slack_user_id, slack_username)
                    say(
                        text=(
                            f"✅ *Linked!* You're now connected as *{match.name}* ({match.role}).\n\n"
                            f"You can now talk to your personal AI agent directly from Slack. "
                            f"Try: _\"What are my tasks?\"_ or _\"Check my emails\"_"
                        )
                    )
                else:
                    say(f"❌ Couldn't find an employee named *{name}* in Nexus. Please check the spelling and try again.")
            finally:
                db.close()
        return

    # Employee is linked — send typing indicator
    say(text=f"_Processing..._")

    # Log incoming message
    log_message(employee.id, "inbound", text)

    # Route to their AI agent
    response = route_to_agent(employee, text)

    # Format and send response
    formatted = format_for_slack(response)
    say(text=formatted)

    # Log outbound response
    log_message(employee.id, "outbound", response)


@app.event("app_mention")
def handle_mention(event, say):
    """
    Handles @Nexus mentions in channels.
    Extracts the message and routes to the mentioning employee's agent.
    """
    slack_user_id = event.get("user")
    text          = event.get("text", "")

    # Strip the bot mention from the text
    import re
    clean_text = re.sub(r'<@[A-Z0-9]+>', '', text).strip()

    if not clean_text:
        say("Hey! DM me directly to talk to your personal Nexus agent. 🤖")
        return

    employee = get_employee_by_slack_id(slack_user_id)

    if not employee:
        say(f"<@{slack_user_id}> You need to link your Slack account first. DM me to get started!")
        return

    say(f"<@{slack_user_id}> _Processing..._")

    response = route_to_agent(employee, clean_text)
    formatted = format_for_slack(response)
    say(f"<@{slack_user_id}> {formatted}")


# ═══════════════════════════════════════════════════════════════
# SLASH COMMANDS (optional — register in Slack dashboard)
# ═══════════════════════════════════════════════════════════════

@app.command("/nexus")
def handle_slash_nexus(ack, respond, command):
    """
    /nexus [command] — shortcut for employees to interact with their agent.
    Register this in Slack: Features → Slash Commands → /nexus
    """
    ack()

    slack_user_id = command["user_id"]
    text          = command.get("text", "").strip()

    if not text:
        respond("Usage: `/nexus [your command]`\nExample: `/nexus what are my tasks?`")
        return

    employee = get_employee_by_slack_id(slack_user_id)

    if not employee:
        respond("You're not linked to Nexus yet. DM the Nexus bot to get started.")
        return

    response  = route_to_agent(employee, text)
    formatted = format_for_slack(response)
    respond(formatted)


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    log.info("🚀 Starting Nexus Slack Bot (Socket Mode)...")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()