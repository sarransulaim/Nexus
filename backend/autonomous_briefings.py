"""
autonomous_briefings.py — Morning Briefings (Phase 5)
=======================================================
Each employee's agent proactively sends a "here's your day" briefing
to their preferred channel on a schedule.

Design principles:
  - DETERMINISTIC: the briefing is composed from real DB data, NO LLM
    call. Zero hallucination, zero cost, instant, reliable.
  - FRESH: data is queried at send time, never cached.
  - QUIET: skips people with nothing meaningful + no verified channel.
  - SAFE: in-memory guard prevents double-send within a run.

Scheduling uses APScheduler (cron, Mon-Fri, configurable hour).
A manual trigger endpoint exists for testing/demo so you don't wait
until 9am.
"""

import os
import logging
from datetime import datetime, date, timezone, timedelta

from database.core import SessionLocal
from database.models import Employee, Task, Meeting, Notification, ChannelConnection

log = logging.getLogger("nexus.briefings")

# Config (env-overridable)
BRIEFING_HOUR   = int(os.getenv("BRIEFING_HOUR", "9"))     # 9am server time
BRIEFING_MINUTE = int(os.getenv("BRIEFING_MINUTE", "0"))
DEFAULT_COMPANY_ID = 1

# In-memory guard: (employee_id, iso_date) already briefed.
# Prevents double-send if the job runs twice in one day (e.g. restart).
# Resets on restart — acceptable for now; a DB column is the later upgrade.
_briefed_today = set()


# ═══════════════════════════════════════════════════════════════
# COMPOSE — deterministic, no LLM
# ═══════════════════════════════════════════════════════════════

def compose_briefing(employee: Employee, db) -> str | None:
    """
    Build a briefing from fresh DB data.
    Managers get a team overview + their own items.
    Employees get their own tasks/meetings.
    Returns None only if there is genuinely nothing to say.
    """
    if employee.system_role == "manager":
        return _compose_manager_briefing(employee, db)
    return _compose_employee_briefing(employee, db)


def _compose_manager_briefing(employee: Employee, db) -> str | None:
    """Team overview first (what a manager cares about), then own items."""
    today = date.today()

    # Team-wide open tasks (all employees in this company)
    team_tasks = db.query(Task).filter(
        Task.is_completed == False,
        Task.company_id   == employee.company_id,
    ).all()

    overdue   = [t for t in team_tasks if t.due_date and t.due_date < today]
    due_today = [t for t in team_tasks if t.due_date and t.due_date == today]

    # Workload by owner — who's carrying the most
    load = {}
    for t in team_tasks:
        if t.owner_id:
            load[t.owner_id] = load.get(t.owner_id, 0) + 1

    # Resolve names for the heaviest-loaded people
    heaviest = sorted(load.items(), key=lambda kv: kv[1], reverse=True)[:3]
    name_by_id = {}
    if heaviest:
        ids = [eid for eid, _ in heaviest]
        for e in db.query(Employee).filter(Employee.id.in_(ids)).all():
            name_by_id[e.id] = e.name

    # Team meetings today
    team_meetings_today = db.query(Meeting).filter(
        Meeting.company_id     == employee.company_id,
        Meeting.scheduled_date == today,
    ).all()
    team_meetings_today = [m for m in team_meetings_today if (m.status or "scheduled") != "cancelled"]

    active_people = db.query(Employee).filter(
        Employee.company_id == employee.company_id,
        Employee.is_active  == True,
        Employee.system_role != "manager",
    ).count()

    # If the whole team is genuinely idle, skip
    if not team_tasks and not team_meetings_today:
        return None

    name_first = (employee.name or "there").split()[0]
    lines = [f"Good morning, {name_first}. Team status:"]

    lines.append("")
    lines.append(f"👥 {active_people} on the team · {len(team_tasks)} open task(s)")

    if overdue:
        lines.append("")
        lines.append(f"⚠️ Overdue across team ({len(overdue)}):")
        for t in overdue[:5]:
            owner = name_by_id.get(t.owner_id, "unassigned")
            lines.append(f"  • {t.title} — {owner} (was due {t.due_date:%b %d})")

    if due_today:
        lines.append("")
        lines.append(f"📌 Due today ({len(due_today)}):")
        for t in due_today[:5]:
            owner = name_by_id.get(t.owner_id) or _name_lookup(db, t.owner_id)
            lines.append(f"  • {t.title} — {owner}")

    if heaviest:
        lines.append("")
        lines.append("📊 Heaviest load:")
        for eid, cnt in heaviest:
            lines.append(f"  • {name_by_id.get(eid, f'Employee {eid}')}: {cnt} task(s)")

    if team_meetings_today:
        lines.append("")
        lines.append(f"📅 Team meetings today ({len(team_meetings_today)}):")
        for m in team_meetings_today[:5]:
            when = m.scheduled_time or "time TBD"
            lines.append(f"  • {m.topic} at {when}")

    # The manager's OWN items (tasks they personally own + meetings they attend)
    own_tasks = [t for t in team_tasks if t.owner_id == employee.id]
    own_meetings = []
    try:
        own_meetings = [m for m in employee.meetings
                        if m.scheduled_date == today and (m.status or "scheduled") != "cancelled"]
    except Exception:
        pass

    if own_tasks or own_meetings:
        lines.append("")
        lines.append("— Your own items —")
        for t in own_tasks[:5]:
            due_str = f" (due {t.due_date:%b %d})" if t.due_date else ""
            lines.append(f"  • {t.title} [{t.priority}]{due_str}")
        for m in own_meetings[:3]:
            lines.append(f"  • Meeting: {m.topic} at {m.scheduled_time or 'TBD'}")

    lines.append("")
    lines.append("Reply here to reassign, rebalance, or dig into anyone's load.")

    return "\n".join(lines)


def _name_lookup(db, emp_id) -> str:
    if not emp_id:
        return "unassigned"
    e = db.query(Employee).filter(Employee.id == emp_id).first()
    return e.name if e else f"Employee {emp_id}"


def _compose_employee_briefing(employee: Employee, db) -> str | None:
    """
    Build a briefing string from fresh DB data.
    Returns None only if the person has genuinely nothing (no open
    tasks AND no meetings today).
    """
    today = date.today()
    week_end = today + timedelta(days=7)

    # Fresh task pull — this employee's open tasks
    open_tasks = db.query(Task).filter(
        Task.owner_id     == employee.id,
        Task.is_completed == False,
        Task.company_id   == employee.company_id,
    ).all()

    overdue   = [t for t in open_tasks if t.due_date and t.due_date < today]
    due_today = [t for t in open_tasks if t.due_date and t.due_date == today]
    due_week  = [t for t in open_tasks if t.due_date and today < t.due_date <= week_end]
    # Tasks with a due date further out, or no due date at all
    later     = [t for t in open_tasks if t not in overdue and t not in due_today and t not in due_week]

    # Meetings today (via attendee relationship)
    meetings_today = []
    try:
        for m in employee.meetings:
            if m.scheduled_date == today and (m.status or "scheduled") != "cancelled":
                meetings_today.append(m)
    except Exception:
        pass

    # Skip ONLY if the person has truly nothing at all
    if not open_tasks and not meetings_today:
        return None

    # Build the message
    name_first = (employee.name or "there").split()[0]
    lines = [f"Good morning, {name_first}. Here's your day:"]

    if overdue:
        lines.append("")
        lines.append(f"⚠️ Overdue ({len(overdue)}):")
        for t in overdue[:5]:
            lines.append(f"  • {t.title} (was due {t.due_date:%b %d})")

    if due_today:
        lines.append("")
        lines.append(f"📌 Due today ({len(due_today)}):")
        for t in due_today[:5]:
            lines.append(f"  • {t.title} [{t.priority}]")

    if meetings_today:
        lines.append("")
        lines.append(f"📅 Meetings today ({len(meetings_today)}):")
        for m in meetings_today[:5]:
            when = m.scheduled_time or "time TBD"
            lines.append(f"  • {m.topic} at {when}")

    if due_week:
        lines.append("")
        lines.append(f"🗓️ Due this week ({len(due_week)}):")
        for t in due_week[:5]:
            lines.append(f"  • {t.title} (due {t.due_date:%b %d})")

    # Always give a sense of the overall workload, even if nothing's imminent.
    if later and not (overdue or due_today or due_week):
        lines.append("")
        lines.append(f"📋 On your plate ({len(open_tasks)} open):")
        for t in sorted(later, key=lambda x: (x.due_date or date.max))[:5]:
            due_str = f" (due {t.due_date:%b %d})" if t.due_date else ""
            lines.append(f"  • {t.title} [{t.priority}]{due_str}")
    elif later:
        lines.append("")
        lines.append(f"Plus {len(later)} more open task(s) further out.")

    lines.append("")
    lines.append("Reply here if you want to reprioritize anything.")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# DELIVER — resolve channel + send
# ═══════════════════════════════════════════════════════════════

def _primary_connection(employee: Employee, db) -> ChannelConnection | None:
    """Preferred verified channel: is_primary first, else any verified."""
    conns = db.query(ChannelConnection).filter(
        ChannelConnection.employee_id == employee.id,
        ChannelConnection.verified    == True,
    ).all()
    if not conns:
        return None
    for c in conns:
        if c.is_primary:
            return c
    return conns[0]


def send_briefing_to(employee: Employee, db) -> dict:
    """
    Compose + deliver a briefing to one employee.
    Always drops an in-app notification; also sends to their channel.
    """
    briefing = compose_briefing(employee, db)
    if not briefing:
        return {"employee_id": employee.id, "status": "skipped_empty"}

    # In-app notification (always)
    try:
        db.add(Notification(
            company_id   = employee.company_id,
            recipient_id = employee.id,
            type         = "briefing",
            title        = "Your morning briefing",
            message      = briefing,
            entity_type  = "briefing",
        ))
        db.commit()
    except Exception as e:
        log.warning(f"in-app notification failed for {employee.id}: {e}")
        db.rollback()

    # External channel (if linked). One shared dispatcher — this used to know
    # only WhatsApp/Telegram, so anyone linked via SLACK silently got nothing.
    from api.channel_delivery import deliver
    channel_status = deliver(employee, briefing, db)

    # Tell the admin board something autonomous just happened
    try:
        from event_bus import event_bus
        agent_id = "Manager_1" if employee.system_role == "manager" else f"Employee_{employee.id}"
        event_bus.emit("message_sent", actor=agent_id,
                       to="ws_broadcast", kind="autonomous_briefing")
    except Exception:
        pass

    return {"employee_id": employee.id, "status": "sent", "channel": channel_status}


# ═══════════════════════════════════════════════════════════════
# RUN ALL — the scheduled job
# ═══════════════════════════════════════════════════════════════

def run_all_briefings(force: bool = False) -> dict:
    """
    Send briefings to every active employee (and the manager).
    `force=True` ignores the daily guard (used by the manual demo trigger).
    """
    today_iso = date.today().isoformat()
    results = {"sent": 0, "skipped": 0, "details": []}

    db = SessionLocal()
    try:
        people = db.query(Employee).filter(Employee.is_active == True).all()
        log.info(f"🌅 Running morning briefings for {len(people)} people...")

        for emp in people:
            guard_key = (emp.id, today_iso)
            if not force and guard_key in _briefed_today:
                results["skipped"] += 1
                continue

            try:
                r = send_briefing_to(emp, db)
                if r["status"] == "sent":
                    results["sent"] += 1
                    _briefed_today.add(guard_key)
                else:
                    results["skipped"] += 1
                results["details"].append(r)
            except Exception as e:
                log.error(f"briefing failed for employee {emp.id}: {e}")
                results["skipped"] += 1

        log.info(f"🌅 Briefings done: {results['sent']} sent, {results['skipped']} skipped")
        return results
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════
# SCHEDULER
# ═══════════════════════════════════════════════════════════════

_scheduler = None


def start_scheduler():
    """Start APScheduler with a Mon-Fri morning cron job. Idempotent."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.warning("⚠️  APScheduler not installed — autonomous briefings disabled. "
                    "Run: pip install apscheduler")
        return None

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_all_briefings,
        CronTrigger(day_of_week="mon-fri", hour=BRIEFING_HOUR, minute=BRIEFING_MINUTE),
        id="morning_briefings",
        replace_existing=True,
        misfire_grace_time=3600,   # if server was down, still fire within the hour
    )
    _scheduler.start()
    log.info(f"⏰ Briefing scheduler started — Mon-Fri at {BRIEFING_HOUR:02d}:{BRIEFING_MINUTE:02d} UTC")
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None