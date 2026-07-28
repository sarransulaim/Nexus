"""
proactive_engine.py — The Proactive Watcher (Phase 6, Step 3)
==============================================================
Where briefings are a once-a-day summary, this is the agent WATCHING —
periodically scanning real state and surfacing things worth your attention
the moment they matter, then proposing what to do. This is what turns Nexus
from "answers when asked" into "reaches out like a chief of staff."

Design principles (same DNA as autonomous_briefings.py):
  - DETERMINISTIC detection: triggers are plain rules over real DB data,
    NO LLM. Zero hallucination, zero cost, instant, runs as often as we want.
  - FRESH: queried at scan time, never cached.
  - RESPECTS ATTENTION: everything lands in the in-app feed; only urgent
    items optionally push to a channel (behind a flag, default OFF).
  - DEDUP: never nags about the same thing twice (in-memory guard).
  - SAFE: every scan is best-effort; a failure never crashes the app.

Triggers (first set):
  - OVERDUE: task past its due date, not done  → urgency
  - DEADLINE APPROACHING: due within the next 48h, not done → foresight
  - OVERLOADED: someone carrying far more open tasks than the team norm → team awareness

Scheduling: APScheduler interval job (default every 15 min). A manual
trigger (run_proactive_scan_now) fires it instantly for testing/demo.
"""

import os
import logging
from datetime import datetime, date, timezone, timedelta

from database.core import SessionLocal
from database.models import Employee, Task, Notification

log = logging.getLogger("nexus.proactive")

# ── Config (env-overridable) ───────────────────────────────────────────────
SCAN_INTERVAL_MINUTES = int(os.getenv("PROACTIVE_SCAN_MINUTES", "15"))
DEADLINE_WINDOW_HOURS = int(os.getenv("PROACTIVE_DEADLINE_HOURS", "48"))
# Someone is "overloaded" if they hold >= this many open tasks AND at least
# this many more than the team average (both conditions, to avoid false alarms).
OVERLOAD_MIN_TASKS    = int(os.getenv("PROACTIVE_OVERLOAD_MIN", "4"))
OVERLOAD_MARGIN       = int(os.getenv("PROACTIVE_OVERLOAD_MARGIN", "3"))
DEFAULT_COMPANY_ID    = 1

# Push urgent alerts to the manager's Slack DM?
# Default OFF so we don't spam during development. Flip to "1" when ready.
PUSH_TO_CHANNEL = os.getenv("PROACTIVE_PUSH_CHANNEL", "0") == "1"

# In-memory dedup: set of alert keys already surfaced, like
# "overdue:task:5" or "overload:emp:2:2026-05-30". Resets on restart —
# acceptable for now; a DB column is the later upgrade.
_alerted = set()


# ═══════════════════════════════════════════════════════════════
# THE SCAN — deterministic detection over fresh data
# ═══════════════════════════════════════════════════════════════

def scan_company(company_id: int = DEFAULT_COMPANY_ID, force: bool = False) -> dict:
    """
    Run one proactive scan for a company. Detects trigger conditions,
    creates in-app notifications for anything new, optionally pushes urgent
    items to the manager's channel. Returns a summary dict.

    `force=True` ignores the dedup guard (used by the manual demo trigger so
    you can see alerts fire repeatedly while testing).
    """
    db = SessionLocal()
    created = []
    try:
        now   = datetime.now(timezone.utc)
        today = date.today()
        window_end = today + timedelta(hours=DEADLINE_WINDOW_HOURS)

        # The manager receives team-level alerts.
        manager = (
            db.query(Employee)
              .filter(Employee.company_id == company_id,
                      Employee.system_role == "manager")
              .first()
        )

        # All open tasks for the company (same query style as briefings).
        open_tasks = (
            db.query(Task)
              .filter(Task.is_completed == False,
                      Task.company_id == company_id)
              .all()
        )

        # Name lookup for friendly messages.
        emps = db.query(Employee).filter(Employee.company_id == company_id).all()
        name_by_id = {e.id: e.name for e in emps}

        # ── TRIGGER 1: OVERDUE ────────────────────────────────────────────
        for t in open_tasks:
            if t.due_date and t.due_date < today:
                key = f"overdue:task:{t.id}"
                if force or key not in _alerted:
                    owner = name_by_id.get(t.owner_id, "unassigned")
                    msg = (f"'{t.title}' is overdue (was due {t.due_date:%b %d}), "
                           f"owned by {owner}. Want me to reassign, extend the deadline, "
                           f"or follow up with them?")
                    if _notify(db, manager, "proactive_overdue", "Task overdue", msg,
                               entity_id=t.id, urgent=True):
                        created.append(key); _alerted.add(key)

        # ── TRIGGER 2: DEADLINE APPROACHING (next 48h) ────────────────────
        for t in open_tasks:
            if t.due_date and today <= t.due_date <= window_end:
                key = f"approaching:task:{t.id}"
                if force or key not in _alerted:
                    owner = name_by_id.get(t.owner_id, "unassigned")
                    msg = (f"'{t.title}' is due {t.due_date:%b %d} ({owner}). "
                           f"It's coming up — want me to check progress or give them a nudge?")
                    if _notify(db, manager, "proactive_deadline", "Deadline approaching", msg,
                               entity_id=t.id, urgent=False):
                        created.append(key); _alerted.add(key)

        # ── TRIGGER 3: OVERLOADED PERSON ──────────────────────────────────
        load = {}
        for t in open_tasks:
            if t.owner_id:
                load[t.owner_id] = load.get(t.owner_id, 0) + 1
        if load:
            avg = sum(load.values()) / len(load)
            for emp_id, count in load.items():
                if count >= OVERLOAD_MIN_TASKS and count >= avg + OVERLOAD_MARGIN:
                    # dedup per person per day (load shifts slowly)
                    key = f"overload:emp:{emp_id}:{today.isoformat()}"
                    if force or key not in _alerted:
                        who = name_by_id.get(emp_id, f"Employee {emp_id}")
                        msg = (f"{who} is carrying {count} open tasks — well above the team "
                               f"average of {avg:.0f}. Want me to rebalance some of their work "
                               f"to someone with capacity?")
                        if _notify(db, manager, "proactive_overload", "Workload imbalance", msg,
                                   entity_id=emp_id, urgent=False):
                            created.append(key); _alerted.add(key)

        db.commit()
        if created:
            log.info(f"🔔 Proactive scan surfaced {len(created)} item(s): {created}")
        return {"company_id": company_id, "alerts_created": len(created), "keys": created}

    except Exception as e:
        db.rollback()
        log.warning(f"proactive scan failed for company {company_id}: {e}")
        return {"company_id": company_id, "error": str(e)}
    finally:
        db.close()


def _notify(db, manager, ntype, title, message, entity_id=None, urgent=False) -> bool:
    """Create an in-app notification for the manager; optionally push to channel."""
    if not manager:
        return False
    try:
        db.add(Notification(
            company_id   = manager.company_id,
            recipient_id = manager.id,
            type         = ntype,
            title        = title,
            message      = message,
            entity_type  = "proactive",
            entity_id    = entity_id,
        ))
        # Tell the admin board something autonomous happened
        try:
            from event_bus import event_bus
            event_bus.emit("message_sent", actor="Manager_1",
                           to="ws_broadcast", kind="proactive_alert")
        except Exception:
            pass

        # Optional channel push for urgent items (behind the flag)
        if urgent and PUSH_TO_CHANNEL:
            _push_to_channel(db, manager, f"{title}: {message}")
        return True
    except Exception as e:
        log.warning(f"proactive notify failed: {e}")
        return False


def _push_to_channel(db, employee, text):
    """Send to the employee's best linked channel (best-effort).

    Delegates to api.channel_delivery so Slack, WhatsApp and Telegram are all
    reachable from one place — this previously routed through the briefing
    module's Slack-only path and skipped Slack users entirely.
    """
    try:
        from api.channel_delivery import deliver
        return deliver(employee, text, db)
    except Exception as e:
        log.warning(f"channel push failed for employee {getattr(employee, 'id', '?')}: {e}")
        return "error"

def run_proactive_scan(force: bool = False) -> dict:
    """The job the scheduler calls. Scans the default company."""
    return scan_company(DEFAULT_COMPANY_ID, force=force)


def run_proactive_scan_now() -> dict:
    """Manual trigger for testing/demo — fires immediately, ignores dedup."""
    log.info("▶️  Manual proactive scan triggered")
    return scan_company(DEFAULT_COMPANY_ID, force=True)


_scheduler = None


def start_proactive_scheduler():
    """Start the interval scanner. Idempotent. Returns the scheduler or None."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        log.warning("⚠️  APScheduler not installed — proactive engine disabled.")
        return None

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_proactive_scan,
        IntervalTrigger(minutes=SCAN_INTERVAL_MINUTES),
        id="proactive_scan",
        replace_existing=True,
        misfire_grace_time=300,
    )
    _scheduler.start()
    log.info(f"⏰ Proactive engine started — scanning every {SCAN_INTERVAL_MINUTES} min "
             f"(channel push: {'ON' if PUSH_TO_CHANNEL else 'OFF'})")
    return _scheduler


def stop_proactive_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None