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
import re
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
            from api.security import internal_token as _internal_token
            req = urllib.request.Request(
                "http://localhost:8000/api/v1/internal/sync",
                method="POST",
                headers={"Content-Type": "application/json",
                         "X-Internal-Token": _internal_token()},
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

    # ── Slack linking: does this message contain a 6-digit verification code? ──
    # Slack often decorates the text — a pasted "*732305*" arrives bold — so strip
    # whitespace + Slack markup (*bold*, _italic_, `code`, ~strike~) before checking,
    # and fall back to a standalone 6-digit run anywhere in the message.
    code_candidate = re.sub(r"[\s*_`~]", "", text)
    if not (code_candidate.isdigit() and len(code_candidate) == 6):
        m = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
        code_candidate = m.group(1) if m else ""

    if code_candidate.isdigit() and len(code_candidate) == 6:
        try:
            from channels_router import complete_slack_link
            result = complete_slack_link(slack_user_id, code_candidate)
            if result.get("linked"):
                name = result.get("employee_name", "")
                say(text=f"✅ *Linked!* Your Slack is now connected to Nexus"
                         f"{f' as *{name}*' if name else ''}. "
                         f"Send me anything and your AI will respond.")
                return
            if result.get("reason") == "expired":
                say(text="⏰ That code has expired. Generate a fresh one in "
                         "Nexus → Connections → Slack and DM it to me within 10 minutes.")
                return
            # reason == no_pending_code: if they're not linked yet, the code was
            # wrong or stale — tell them, instead of silently asking for a name.
            if not get_employee_by_slack_id(slack_user_id):
                say(text="🔑 I couldn't find that code. Open Nexus → Connections → "
                         "Slack, click *Connect* for a new code, and DM me just the "
                         "6 digits.")
                return
            # else: an already-linked user happened to send 6 digits — fall through.
        except Exception as e:
            log.warning(f"slack link attempt failed: {e}")
        # fall through — maybe it's a real message from an already-linked user.

    # Look up the employee
    employee = get_employee_by_slack_id(slack_user_id)

    if not employee:
        # Not linked yet. We NEVER link by self-reported name — that would let
        # ANYONE in the workspace impersonate a colleague by typing their name and
        # hijack their private agent (tasks, email, calendar). Linking requires the
        # verified 6-digit code from Nexus → Connections → Slack → Connect, which
        # the DM handler above matches via complete_slack_link. Works for all roles.
        say(
            text=(
                "👋 *Welcome to Nexus!*\n\n"
                "I don't recognize your Slack account yet. To link it *securely*:\n"
                "1. Open *Nexus → Connections → Slack* and click *Connect*.\n"
                "2. DM me the *6-digit code* it shows you.\n\n"
                "_(I can't link by name — that would let anyone impersonate a teammate.)_"
            )
        )
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


def _slack_display_name(client, user_id: str) -> str:
    """Best-effort human name for a Slack user — prefer the linked Nexus
    employee, fall back to their Slack profile."""
    if not user_id:
        return "Someone"
    try:
        emp = get_employee_by_slack_id(user_id)
        if emp:
            return emp.name
    except Exception:
        pass
    try:
        u = client.users_info(user=user_id)["user"]
        return (u.get("profile", {}).get("display_name")
                or u.get("real_name") or u.get("name") or "Someone")
    except Exception:
        return "Someone"


def _slack_channel_name(client, channel_id: str) -> str:
    try:
        return client.conversations_info(channel=channel_id)["channel"].get("name") or ""
    except Exception:
        return ""


def _slack_recent_transcript(client, channel_id: str, limit: int = 14,
                             exclude_ts: str = None) -> str:
    """Readable transcript of the channel's recent messages. Every line here
    is ALREADY visible to all channel members, so using it as context cannot
    leak anything private. Degrades to '' if the bot lacks history scope."""
    try:
        resp = client.conversations_history(channel=channel_id, limit=limit)
    except Exception as e:
        log.warning(f"channel history unavailable ({e}); replying without context")
        return ""
    msgs = list(reversed(resp.get("messages", []) or []))  # oldest first
    name_cache, lines = {}, []
    for m in msgs:
        if exclude_ts and m.get("ts") == exclude_ts:
            continue  # skip the mention we're currently answering
        body = re.sub(r"<@[A-Z0-9]+>", "", (m.get("text") or "")).strip()
        if not body:
            continue
        if m.get("bot_id") or m.get("subtype") == "bot_message":
            who = "Nexus"
        else:
            uid = m.get("user")
            if uid not in name_cache:
                name_cache[uid] = _slack_display_name(client, uid)
            who = name_cache[uid]
        if len(body) > 400:
            body = body[:400] + "…"
        lines.append(f"{who}: {body}")
    return "\n".join(lines[-limit:])


def _employee_workload_line(emp) -> str:
    """One-line 'who's working on what' for a channel member — their open task
    titles. Read-only; these are team work items, not private data."""
    from database.models import Task
    db = SessionLocal()
    try:
        tasks = db.query(Task).filter(
            Task.owner_id == emp.id, Task.is_completed == False  # noqa: E712
        ).all()
        if not tasks:
            return f"{emp.name} ({emp.role}) — no open tasks"
        titles = ", ".join(t.title for t in tasks[:4])
        more   = f" +{len(tasks) - 4} more" if len(tasks) > 4 else ""
        return f"{emp.name} ({emp.role}) — {len(tasks)} open: {titles}{more}"
    finally:
        db.close()


def _slack_channel_project_context(client, channel_id: str) -> str:
    """Auto-detect the channel's project focus: map channel members → linked
    Nexus employees → their open work. Read-only, best-effort. Returns '' if the
    member list isn't available (e.g. missing scope)."""
    try:
        member_ids = client.conversations_members(
            channel=channel_id, limit=50).get("members", []) or []
    except Exception as e:
        log.warning(f"channel members unavailable ({e})")
        return ""
    seen, lines = set(), []
    for uid in member_ids:
        if uid in seen:
            continue
        seen.add(uid)
        emp = get_employee_by_slack_id(uid)
        if emp:
            lines.append(_employee_workload_line(emp))
    return "\n".join(lines)


@app.event("app_mention")
def handle_mention(event, say, client):
    """
    @Nexus in a CHANNEL → the TEAM / COORDINATOR assistant: run_orchestrator with
    a `Team_{channel}` identity. It reads project/task/dependency/team state and
    can escalate blockers, but has NO personal data and NO manager powers — safe
    for a shared room (enforced server-side in execute_tool). Personal help stays
    in DMs (handle_dm). On any failure it falls back to a light conversational
    reply so the channel never gets a hard error.
    """
    channel_id    = event.get("channel")
    slack_user_id = event.get("user")
    thread_ts     = event.get("thread_ts")  # stay in-thread if mentioned in one
    clean_text    = re.sub(r"<@[A-Z0-9]+>", "", event.get("text", "")).strip()

    if not clean_text:
        say(text=f"<@{slack_user_id}> 👋 I'm Nexus — ask me about the team's "
                 f"projects, tasks or dependencies here, or *DM me* for your "
                 f"private tasks, emails and calendar.",
            thread_ts=thread_ts)
        return

    # SECURITY: only a recognized company employee may drive the team agent —
    # otherwise an unlinked guest merely present in the channel could read
    # internal team status/tasks/dependencies. Linked employees pass instantly.
    if not get_employee_by_slack_id(slack_user_id):
        say(text=f"<@{slack_user_id}> 👋 I help this team coordinate, but I don't "
                 f"recognize your account yet. *DM me* to link it, then mention me "
                 f"here.",
            thread_ts=thread_ts)
        return

    speaker      = _slack_display_name(client, slack_user_id)
    channel_name = _slack_channel_name(client, channel_id)
    transcript   = _slack_recent_transcript(client, channel_id, exclude_ts=event.get("ts"))
    roster       = _slack_channel_project_context(client, channel_id)

    # Context the team agent should see — all of it already visible to every
    # member of this channel (roster of who's here + what they're working on,
    # plus the recent messages).
    ctx = [f"=== SHARED CHANNEL #{channel_name or 'team'} ==="]
    if roster:
        ctx.append("WHO'S IN THIS CHANNEL AND WHAT THEY'RE WORKING ON:\n" + roster)
    if transcript:
        ctx.append("RECENT MESSAGES (oldest first):\n" + transcript)
    ctx.append("=== END CHANNEL CONTEXT ===")

    try:
        reply = run_orchestrator(
            agent_id=f"Team_{channel_id}",
            command=f"{speaker} (in the channel) says: {clean_text}",
            extra_context="\n\n".join(ctx),
        )
        _trigger_sync()
        say(text=format_for_slack(reply), thread_ts=thread_ts)
    except Exception as e:
        log.error(f"team agent failed, falling back to light reply: {e}")
        try:
            from channel_assistant import build_channel_reply
            reply = build_channel_reply(channel_name, transcript, speaker, clean_text)
            say(text=format_for_slack(reply), thread_ts=thread_ts)
        except Exception:
            say(text=f"<@{slack_user_id}> Sorry, I ran into an issue. Please try again.",
                thread_ts=thread_ts)


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


def send_dm(slack_user_id: str, message: str) -> dict:
    """DM a person on Slack. Used by api/channel_delivery so briefings and
    proactive alerts reach people where they already are — Slack DMs are free
    and instant, unlike the WhatsApp sandbox.

    Posting to a user id opens (or reuses) the bot↔user DM automatically.
    Returns {"success": bool, "error": str} — never raises."""
    if not app:
        return {"success": False, "error": "slack not configured"}
    if not slack_user_id:
        return {"success": False, "error": "no slack user id"}
    try:
        resp = app.client.chat_postMessage(
            channel=slack_user_id, text=format_for_slack(message))
        if resp.get("ok"):
            return {"success": True, "ts": resp.get("ts")}
        return {"success": False, "error": str(resp.get("error") or resp)[:120]}
    except Exception as e:
        log.warning(f"Slack DM to {slack_user_id} failed: {e}")
        return {"success": False, "error": str(e)[:120]}


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
        import time as _time
        failures = 0
        MAX_FAILURES = 8
        # Resilient supervisor. slack_sdk's OWN auto-reconnect storms when a
        # firewall / antivirus / flaky network keeps killing the WebSocket — that
        # storm has segfaulted the whole process. So we DISABLE its auto-reconnect
        # and pace reconnects ourselves with backoff, then give up. The FastAPI
        # backend stays healthy regardless.
        while failures < MAX_FAILURES:
            try:
                log.info("🚀 Slack bot connecting (Socket Mode)...")
                _handler = SocketModeHandler(app, SLACK_APP_TOKEN)
                try:
                    _handler.client.auto_reconnect_enabled = False  # we pace reconnects, not slack_sdk
                except Exception:
                    pass
                _handler.connect()
                log.info("✅ Slack connected")
                # Stay alive while connected; break out to reconnect when it drops.
                probe_errs = 0
                while True:
                    _time.sleep(5)
                    try:
                        if not _handler.client.is_connected():
                            log.warning("Slack WebSocket dropped.")
                            break
                        probe_errs = 0
                    except Exception as pe:
                        # Don't spin forever if is_connected() itself keeps raising —
                        # after a few consecutive errors drop out to reconnect (the
                        # outer loop backs off + eventually gives up) instead of
                        # wedging as falsely "connected".
                        probe_errs += 1
                        if probe_errs >= 3:
                            log.warning(f"Slack connection probe failing ({pe}) — reconnecting.")
                            break
            except Exception as e:
                log.warning(f"Slack connect error: {e}")
            finally:
                try:
                    if _handler:
                        _handler.close()
                except Exception:
                    pass
            failures += 1
            delay = min(120, 10 * failures)
            log.warning(f"Slack reconnect {failures}/{MAX_FAILURES} — backing off {delay}s")
            _time.sleep(delay)
        log.error("🛑 Slack bot gave up after repeated connection failures — the backend stays "
                  "healthy. A firewall/AV/network is dropping the WebSocket; fix that, then "
                  "restart with SLACK_ENABLED=true.")

    threading.Thread(target=_run, daemon=True, name="slack-bot").start()
    _started = True
    log.info("✅ Slack bot thread launched")


if __name__ == "__main__":
    # Standalone mode still works: python slack_bot.py
    log.info("🚀 Starting Nexus Slack Bot (Socket Mode, standalone)...")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()