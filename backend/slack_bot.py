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
    """
    Finds the Nexus employee linked to this Slack user ID.
    DB-first (channel_connections), with JSON fallback for any
    legacy links created before the DB flow existed.
    """
    # 1. Try the database (the proper, UI-managed path)
    try:
        from channels_router import slack_employee_lookup
        emp = slack_employee_lookup(slack_user_id)
        if emp:
            return emp
    except Exception as e:
        log.warning(f"DB slack lookup failed, trying JSON: {e}")

    # 2. Fallback: legacy JSON file
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
    # Match the web path's agent_id convention so Slack and web share
    # the SAME agent identity (and therefore the same memory thread).
    # Manager → "Manager_1", everyone else → "Employee_{id}".
    if employee.system_role == "manager":
        agent_id = "Manager_1"
    else:
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

    # ── Slack linking: is this message a 6-digit verification code? ──
    # If so, try to complete a pending link instead of routing to AI.
    stripped = text.replace(" ", "")
    if stripped.isdigit() and len(stripped) == 6:
        try:
            from channels_router import complete_slack_link
            result = complete_slack_link(slack_user_id, stripped)
            if result.get("linked"):
                name = result.get("employee_name", "")
                say(text=f"✅ Linked! Your Slack is now connected to Nexus"
                         f"{f' as {name}' if name else ''}. "
                         f"Send me anything and your AI will respond.")
                return
            # If not linked and reason is expired/no_pending, fall through only
            # if they're not already a known user (so existing users can still
            # send numbers). We check below.
            if result.get("reason") == "expired":
                say(text="That code has expired. Generate a new one in Nexus → Connections.")
                return
        except Exception as e:
            log.warning(f"slack link attempt failed: {e}")
        # If no pending code matched, fall through — maybe it's a real message
        # from an already-linked user (e.g. "123456" as actual content).

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
# ENTRY POINTS
# ═══════════════════════════════════════════════════════════════

# ===========================================================================
# OUTBOUND: POST TO A CHANNEL (cross-tool action)
# ===========================================================================

def list_channels() -> str:
    """Return public channels the bot can see, as a readable string."""
    try:
        resp = app.client.conversations_list(
            types="public_channel", limit=200, exclude_archived=True
        )
        chans = resp.get("channels", [])
        if not chans:
            return "No channels found."
        return "\n".join(f"#{c['name']} (id: {c['id']})" for c in chans)
    except Exception as e:
        return f"Could not list channels: {e}"


def _resolve_channel_id(channel: str):
    """Accept '#name', 'name', or a channel ID and return a channel ID."""
    channel = (channel or "").strip().lstrip("#")
    if not channel:
        return None
    if channel.startswith("C") and channel.isupper() and len(channel) >= 9:
        return channel
    try:
        resp = app.client.conversations_list(
            types="public_channel", limit=200, exclude_archived=True
        )
        for c in resp.get("channels", []):
            if c.get("name", "").lower() == channel.lower():
                return c.get("id")
    except Exception:
        pass
    return None


def post_to_channel(channel: str, message: str) -> str:
    """
    Post a message to a Slack channel as the Nexus bot.
    `channel` may be '#name', 'name', or a channel ID.
    The bot must be a member of the channel (invite it first).
    """
    if not message or not message.strip():
        return "Nothing to post — message was empty."
    channel_id = _resolve_channel_id(channel)
    if not channel_id:
        return (f"Could not find channel '{channel}'. "
                f"Make sure it exists and the Nexus bot has been added to it.")

    # Try to join the channel first (no-op if already a member; needs channels:join).
    # This is what makes the post actually appear for public channels.
    try:
        app.client.conversations_join(channel=channel_id)
    except Exception as join_err:
        log.info(f"conversations_join skipped/failed for {channel_id}: {join_err}")

    try:
        resp = app.client.chat_postMessage(channel=channel_id, text=message)
        clean = channel if channel.startswith("#") else "#" + channel.lstrip("#")
        ok = resp.get("ok")
        ts = resp.get("ts")
        posted_channel = resp.get("channel")
        log.info(f"chat_postMessage ok={ok} channel={posted_channel} ts={ts}")
        if ok:
            return f"Posted to {clean} (message id {ts})."
        return f"Slack reported a problem posting to {clean}: {resp}"
    except Exception as e:
        err = str(e)
        if "not_in_channel" in err or "channel_not_found" in err:
            return ("Could not post — the Nexus bot is not a member of that channel. "
                    "In Slack, invite the bot with /invite @Nexus, then try again.")
        if "missing_scope" in err:
            return ("Could not post — the bot is missing a required scope "
                    "(need chat:write and channels:join). Add them in the Slack app "
                    "settings and reinstall.")
        return f"Failed to post: {err}"



def read_channel_messages(channel: str, limit: int = 15) -> str:
    """
    Read recent messages from a Slack channel and return them as readable text.
    `channel` may be '#name', 'name', or a channel ID.
    Needs the channels:history scope and the bot must be in the channel.
    """
    channel_id = _resolve_channel_id(channel)
    if not channel_id:
        return (f"Could not find channel '{channel}'. Make sure it exists and the "
                f"Nexus bot has been added to it.")
    try:
        resp = app.client.conversations_history(channel=channel_id, limit=limit)
        msgs = resp.get("messages", [])
        if not msgs:
            return f"No recent messages in {channel}."

        # Resolve user IDs to names where possible (cached per call)
        name_cache = {}
        def name_for(uid):
            if not uid:
                return "someone"
            if uid in name_cache:
                return name_cache[uid]
            try:
                info = app.client.users_info(user=uid)
                nm = info["user"]["profile"].get("real_name") or info["user"].get("name") or uid
            except Exception:
                nm = uid
            name_cache[uid] = nm
            return nm

        lines = []
        # Slack returns newest-first; reverse to read oldest-to-newest
        for m in reversed(msgs):
            if m.get("subtype"):  # skip joins/leaves/system messages
                continue
            who = name_for(m.get("user") or m.get("bot_id"))
            txt = (m.get("text") or "").strip()
            if txt:
                lines.append(f"{who}: {txt}")
        if not lines:
            return f"No readable messages in {channel} (only system events)."
        clean = channel if channel.startswith("#") else "#" + channel.lstrip("#")
        return f"Recent messages in {clean}:\n" + "\n".join(lines)
    except Exception as e:
        err = str(e)
        if "not_in_channel" in err or "channel_not_found" in err:
            return ("Could not read — the Nexus bot is not a member of that channel. "
                    "Invite it with /invite @Nexus, then try again.")
        if "missing_scope" in err:
            return ("Could not read — the bot is missing the channels:history scope. "
                    "Add it in the Slack app settings and reinstall.")
        return f"Failed to read channel: {err}"

_handler = None
_started = False


def start_in_background():
    """
    Start the Slack Socket Mode bot in a daemon thread so it runs
    inside the FastAPI process (no separate `python slack_bot.py`).

    Safe to call once at app startup. No-ops if tokens are missing
    or if already started.
    """
    global _handler, _started
    if _started:
        return
    if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
        log.warning("⚠️  Slack tokens not set — Slack bot not started.")
        return

    def _run():
        global _handler
        try:
            log.info("🚀 Starting Nexus Slack Bot (Socket Mode, background)...")
            _handler = SocketModeHandler(app, SLACK_APP_TOKEN)
            # Use connect() not start() in a thread — start() installs a
            # signal handler that only works in the main thread. connect()
            # opens the socket and listens without the signal setup.
            _handler.connect()
            # Keep this thread alive so the socket stays open.
            import time as _time
            while True:
                _time.sleep(3600)
        except Exception as e:
            log.error(f"Slack bot crashed: {e}")

    threading.Thread(target=_run, daemon=True, name="slack-bot").start()
    _started = True
    log.info("✅ Slack bot thread launched")


if __name__ == "__main__":
    # Standalone mode still works: python slack_bot.py
    log.info("🚀 Starting Nexus Slack Bot (Socket Mode, standalone)...")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()