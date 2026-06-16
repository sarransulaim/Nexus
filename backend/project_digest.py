"""
project_digest.py — Daily Project Digest
========================================
For each project, the AI reads the day's REAL activity already in the DB —
tasks that moved, dependencies between tasks, help requests, open work — and
posts a plain-language digest into the project's chat channel: what moved,
what's at risk, and what's now blocked on what (the first, primitive form of
integration-drift surfacing).

Deterministic today (no LLM dependency → always works, free). The SAME shape
will later be fed by MCP/GitHub diffs and can be narrated by an LLM — so MCP
becomes a data-source swap behind a surface people already read.
"""

import logging
from datetime import date, timedelta

from database.core import SessionLocal
from database.models import (
    Project, Task, TaskDependency, PeerRequest, Channel, ChannelMember,
    Employee, Notification, Contract, project_members,
)

log = logging.getLogger("nexus.digest")

DIGEST_HOUR   = 17   # 17:00 UTC, end-of-day
DIGEST_MINUTE = 0
DUE_SOON_DAYS = 3


def _name(db, emp_id, cache):
    if emp_id is None:
        return "Unassigned"
    if emp_id not in cache:
        e = db.query(Employee).filter(Employee.id == emp_id).first()
        cache[emp_id] = e.name if e else f"#{emp_id}"
    return cache[emp_id]


def build_project_digest(project, db) -> str:
    """Compose the digest text for one project, or None if there's nothing
    worth saying (so we never post empty noise)."""
    today = date.today()
    names = {}
    tasks = db.query(Task).filter(Task.project_id == project.id).all()
    if not tasks:
        return None
    task_by_id = {t.id: t for t in tasks}

    completed_today, overdue, due_soon = [], [], []
    for t in tasks:
        owner = _name(db, t.owner_id, names)
        if t.is_completed:
            if t.completed_at and t.completed_at.date() == today:
                completed_today.append(f"✅ {owner} finished \"{t.title}\"")
        elif t.due_date and t.due_date < today:
            overdue.append(f"⚠️ \"{t.title}\" ({owner}) is overdue — was due {t.due_date}")
        elif t.due_date and t.due_date <= today + timedelta(days=DUE_SOON_DAYS):
            days = (t.due_date - today).days
            when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
            due_soon.append(f"⏳ \"{t.title}\" ({owner}) is due {when}")

    # Dependencies in play — where the pieces connect (integration-risk seed)
    dep_lines = []
    deps = db.query(TaskDependency).filter(
        TaskDependency.task_id.in_(list(task_by_id.keys()))
    ).all()
    for d in deps:
        blocked = task_by_id.get(d.task_id)
        blocker = task_by_id.get(d.depends_on_id) or \
            db.query(Task).filter(Task.id == d.depends_on_id).first()
        if not blocked or not blocker:
            continue
        b_owner = _name(db, blocked.owner_id, names)
        status = "done ✓" if blocker.is_completed else "NOT done yet"
        dep_lines.append(
            f"🔗 \"{blocked.title}\" ({b_owner}) depends on \"{blocker.title}\" — {status}"
        )

    # Help in flight — peer requests on this project's tasks
    help_lines = []
    prs = db.query(PeerRequest).filter(
        PeerRequest.task_id.in_(list(task_by_id.keys())),
        PeerRequest.status.in_(["Pending", "Accepted"]),
    ).all()
    for pr in prs:
        s = _name(db, pr.sender_id, names)
        r = _name(db, pr.recipient_id, names)
        help_lines.append(f"🤝 {s} → {r}: {(pr.topic or '')[:80]} ({pr.status})")

    sections = []
    if completed_today:
        sections.append("*What moved today*\n" + "\n".join(completed_today))
    if overdue or due_soon:
        sections.append("*Heads-up / at risk*\n" + "\n".join(overdue + due_soon))
    if dep_lines:
        sections.append("*Dependencies in play* — watch these, it's where the pieces have to fit:\n"
                        + "\n".join(dep_lines))
    if help_lines:
        sections.append("*Help in flight*\n" + "\n".join(help_lines))

    contract_lines = []
    for c in db.query(Contract).filter(
        Contract.project_id == project.id,
        Contract.status.in_(["active", "at_risk", "broken"]),
    ).all():
        p  = c.producer.title if c.producer else "?"
        co = c.consumer.title if c.consumer else "?"
        flag = " ⚠️ AT RISK" if c.status == "at_risk" else (" ❌ BROKEN" if c.status == "broken" else "")
        contract_lines.append(f"📄 {c.name}: \"{p}\" → \"{co}\"{flag}")
    if contract_lines:
        sections.append("*Contracts* — the agreed interfaces between pieces:\n" + "\n".join(contract_lines))

    if not sections:
        return None

    open_count = sum(1 for t in tasks if not t.is_completed)
    header = (f"📋 Daily digest — {project.name} ({today.isoformat()}) · "
              f"{open_count} open task{'s' if open_count != 1 else ''}")
    return header + "\n\n" + "\n\n".join(sections)


def _narrate(facts: str) -> str:
    """Turn the deterministic fact list into a short natural-language briefing via
    the local LLM. Falls back to the structured facts if Ollama is unavailable —
    so the digest is smart when it can be, and always works."""
    try:
        try:
            from api.ollama_client import OllamaClient
        except ImportError:
            from ollama_client import OllamaClient
        client = OllamaClient()
        client.timeout = max(getattr(client, "timeout", 60) or 60, 120)
        system = (
            "You write a short daily project briefing for a software team. Turn the facts into "
            "3-6 sentences of plain, concrete prose: what moved, what's at risk, what's blocked on "
            "what, and call out any interface contracts that drifted. KEEP the specific task and "
            "person names. Do NOT invent anything not in the facts. No preamble, no markdown headers."
        )
        out = client.generate(model="qwen2.5:7b", prompt=f"Facts:\n{facts}",
                              system=system, temperature=0.3)
        return out.strip() if out and out.strip() else facts
    except Exception as e:
        log.info(f"digest narration unavailable ({type(e).__name__}); posting structured facts")
        return facts


def _get_or_create_project_channel(project, db):
    """The digest needs a channel to post into. Reuse the project's channel, or
    create it and sync members so they receive the digest."""
    channel = db.query(Channel).filter(
        Channel.project_id == project.id, Channel.type == "project"
    ).first()
    if channel:
        return channel
    channel = Channel(
        company_id=project.company_id,
        name=project.name,
        description=f"Team channel for {project.name}",
        type="project",
        project_id=project.id,
        created_by=project.created_by,
    )
    db.add(channel)
    db.flush()
    rows = db.execute(
        project_members.select().where(project_members.c.project_id == project.id)
    ).fetchall()
    member_ids = {r.employee_id for r in rows}
    if project.created_by:
        member_ids.add(project.created_by)
    for emp_id in member_ids:
        db.add(ChannelMember(channel_id=channel.id, employee_id=emp_id))
    db.commit()
    db.refresh(channel)
    return channel


def _push_notif_async(employee_id: int, n: Notification):
    """Fire-and-forget real-time push of a notification (so the bell updates live)."""
    payload = {
        "id": n.id, "type": n.type, "title": n.title, "message": n.message,
        "is_read": False, "entity_type": n.entity_type, "entity_id": n.entity_id,
        "created_at": str(n.created_at),
    }
    import threading

    def _run():
        try:
            import asyncio
            from api.ws_manager import notifier
            asyncio.run(notifier.send_notification(employee_id, payload))
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def run_drift_alerts() -> dict:
    """
    The directed half of the coordination story: for every cross-person task
    dependency, tap the DOWNSTREAM owner when the thing they depend on is at
    risk (upstream overdue) or just cleared (upstream done). De-duped so the
    same alert doesn't re-fire every run — a new alert only fires when the
    upstream state actually changes (the message text changes with it).
    """
    results = {"alerts": 0, "details": []}
    db = SessionLocal()
    try:
        today = date.today()
        for d in db.query(TaskDependency).all():
            blocked = db.query(Task).filter(Task.id == d.task_id).first()
            blocker = db.query(Task).filter(Task.id == d.depends_on_id).first()
            if not blocked or not blocker or blocked.owner_id is None:
                continue
            # Only alert on cross-person dependencies — you depend on someone ELSE's work.
            if blocker.owner_id == blocked.owner_id:
                continue
            if blocked.is_completed:
                continue  # the downstream piece is already done; nothing to warn about

            msg = None
            if blocker.is_completed:
                msg = (f"You're unblocked: \"{blocker.title}\" — which your "
                       f"\"{blocked.title}\" depends on — is now done. You can proceed.")
            elif blocker.due_date and blocker.due_date < today:
                msg = (f"Heads-up: \"{blocker.title}\" — which your \"{blocked.title}\" "
                       f"depends on — is overdue. Your piece is at risk until it lands.")
            if not msg:
                continue

            # De-dup: skip if this exact alert already exists for this task/owner.
            dup = db.query(Notification).filter(
                Notification.recipient_id == blocked.owner_id,
                Notification.type         == "dependency_drift",
                Notification.entity_type  == "task",
                Notification.entity_id    == blocked.id,
                Notification.message      == msg,
            ).first()
            if dup:
                continue

            n = Notification(
                company_id=blocked.company_id,
                recipient_id=blocked.owner_id,
                type="dependency_drift",
                title="Dependency update",
                message=msg,
                entity_type="task",
                entity_id=blocked.id,
            )
            db.add(n)
            db.commit()
            db.refresh(n)
            _push_notif_async(blocked.owner_id, n)
            results["alerts"] += 1
            results["details"].append({"to_owner": blocked.owner_id, "msg": msg[:70]})

        # Contract drift: the producer changed AFTER the interface was agreed.
        for c in db.query(Contract).filter(Contract.status.in_(["active", "at_risk"])).all():
            producer, consumer = c.producer, c.consumer
            if not producer or not consumer or consumer.owner_id is None or consumer.is_completed:
                continue
            if not (producer.updated_at and c.baseline_at and producer.updated_at > c.baseline_at):
                continue
            if c.status != "at_risk":
                c.status = "at_risk"
                db.commit()
            msg = (f"Contract drift: \"{producer.title}\" changed after the interface \"{c.name}\" "
                   f"was agreed. Re-check it before integrating your \"{consumer.title}\".")
            dup = db.query(Notification).filter(
                Notification.recipient_id == consumer.owner_id,
                Notification.type         == "contract_drift",
                Notification.entity_type  == "contract",
                Notification.entity_id    == c.id,
                Notification.message      == msg,
            ).first()
            if dup:
                continue
            n = Notification(
                company_id=c.company_id, recipient_id=consumer.owner_id,
                type="contract_drift", title="Contract may have drifted",
                message=msg, entity_type="contract", entity_id=c.id,
            )
            db.add(n); db.commit(); db.refresh(n)
            _push_notif_async(consumer.owner_id, n)
            results["alerts"] += 1
            results["details"].append({"to_owner": consumer.owner_id, "msg": msg[:70]})

        log.info(f"🔔 Drift alerts: {results['alerts']} sent")
        return results
    finally:
        db.close()


def run_all_digests(force: bool = False) -> dict:
    """Post a digest into every project channel. `force` is accepted for the
    manual demo trigger (there's no daily dedup guard yet — digests are
    idempotent-ish since they reflect current state)."""
    results = {"posted": 0, "skipped": 0, "details": []}
    db = SessionLocal()
    try:
        from chat_router import post_ai_message
        projects = db.query(Project).all()
        log.info(f"📋 Running project digests for {len(projects)} projects...")
        for p in projects:
            try:
                digest = build_project_digest(p, db)
                if not digest:
                    results["skipped"] += 1
                    results["details"].append({"project": p.name, "status": "nothing_to_report"})
                    continue
                channel = _get_or_create_project_channel(p, db)
                post_ai_message(db, channel, _narrate(digest), ai_agent_id="Nexus Daily", message_type="ai")
                results["posted"] += 1
                results["details"].append({"project": p.name, "status": "posted",
                                           "channel_id": channel.id})
            except Exception as e:
                log.error(f"digest failed for project {p.id}: {e}")
                results["skipped"] += 1
                results["details"].append({"project": p.name, "status": f"error: {e}"})
        log.info(f"📋 Digests done: {results['posted']} posted, {results['skipped']} skipped")
    finally:
        db.close()
    # Pair the shared digest with directed drift alerts to downstream owners.
    drift = run_drift_alerts()
    results["drift_alerts"] = drift["alerts"]
    return results


# ── Scheduler ─────────────────────────────────────────────────
_scheduler = None


def start_digest_scheduler():
    """Start a daily (Mon-Fri, end-of-day) project-digest job. Idempotent."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.warning("APScheduler not installed — daily project digest disabled.")
        return None
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_all_digests,
        CronTrigger(day_of_week="mon-fri", hour=DIGEST_HOUR, minute=DIGEST_MINUTE),
        id="project_digests", replace_existing=True, misfire_grace_time=3600,
    )
    _scheduler.start()
    log.info(f"⏰ Project-digest scheduler started — Mon-Fri at {DIGEST_HOUR:02d}:{DIGEST_MINUTE:02d} UTC")
    return _scheduler


def stop_digest_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
