"""
dependency_inference.py — can an agent infer a project's dependency graph + contracts?
======================================================================================
SPIKE for the autonomous-coordination vision. Reads a project's tasks and asks the AI
to identify which task PRODUCES something another CONSUMES (the dependency graph), plus
the interface CONTRACT for each edge. Returns a proposal; writes NOTHING to the DB.

If this infers dependencies accurately from task text, the rest of the loop
(auto-create contracts → watch → resolve drift) is worth building. The handler can
later wrap this and surface proposals for one-click human confirmation.
"""
import os
import json
import logging

import anthropic
from database.core import SessionLocal
from database.models import Project, Task, Employee

log = logging.getLogger("nexus.depinfer")

_client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))
MODEL = "claude-sonnet-4-6"   # use the strongest model to measure the achievable ceiling


def _gather(project_id: int):
    db = SessionLocal()
    try:
        proj = db.query(Project).filter(Project.id == project_id).first()
        if not proj:
            return None, []
        rows = []
        for t in proj.tasks:
            owner = db.query(Employee).filter(Employee.id == t.owner_id).first() if t.owner_id else None
            rows.append({
                "id": t.id,
                "title": t.title,
                "description": (t.description or "")[:400],
                "owner": owner.name if owner else "Unassigned",
            })
        return proj.name, rows
    finally:
        db.close()


_DEPENDENCIES_TOOL = {
    "name": "record_dependencies",
    "description": "Record the real inter-task dependencies found in the project.",
    "input_schema": {
        "type": "object",
        "properties": {
            "dependencies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "producer_task_id": {"type": "integer"},
                        "consumer_task_id": {"type": "integer"},
                        "reason":           {"type": "string"},
                        "contract": {
                            "type": "object",
                            "properties": {
                                "name":        {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["name", "description"],
                        },
                    },
                    "required": ["producer_task_id", "consumer_task_id", "reason", "contract"],
                },
            },
        },
        "required": ["dependencies"],
    },
}


def infer_dependencies(project_name: str, tasks: list) -> dict:
    """Core inference over (name, tasks) — no DB. `tasks` is a list of
    {id, title, description, owner}. Structured via FORCED tool use, so the
    result always parses (replaces the old text.index('{') hand-parse).
    Also the entry point the eval harness measures (evals/run_evals.py)."""
    task_list = "\n".join(
        f"- Task {t['id']}: \"{t['title']}\" (owner: {t.get('owner', 'Unassigned')}). {t.get('description', '')}"
        for t in tasks
    )
    prompt = (
        f"Project: {project_name}\n\nTasks:\n{task_list}\n\n"
        "Identify the DEPENDENCIES between these tasks — where one task PRODUCES something "
        "another task CONSUMES (e.g. an API the UI calls, a dataset a model uses, a design the "
        "build follows, a backend the integration wires up). For each REAL dependency, also "
        "state the INTERFACE CONTRACT: what the producer must hand the consumer.\n\n"
        "Only include dependencies you are confident are real — an empty list is a valid "
        "answer for independent tasks. producer = the task that makes the thing; "
        "consumer = the task that needs it."
    )
    resp = _client.messages.create(
        model=MODEL,
        max_tokens=2000,
        tools=[_DEPENDENCIES_TOOL],
        tool_choice={"type": "tool", "name": "record_dependencies"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "record_dependencies":
            deps = block.input.get("dependencies") or []
            # keep only well-formed int pairs (defense against schema edge cases)
            clean = []
            for d in deps:
                try:
                    clean.append({
                        "producer_task_id": int(d["producer_task_id"]),
                        "consumer_task_id": int(d["consumer_task_id"]),
                        "reason": str(d.get("reason", "")),
                        "contract": {
                            "name": str((d.get("contract") or {}).get("name", "interface")),
                            "description": str((d.get("contract") or {}).get("description", "")),
                        },
                    })
                except (KeyError, TypeError, ValueError):
                    continue
            return {"dependencies": clean}
    return {"dependencies": []}


def propose_dependencies(project_id: int) -> dict:
    """Returns {dependencies: [{producer_task_id, consumer_task_id, reason, contract:{name,description}}]}.
    Read-only — never writes to the DB."""
    name, tasks = _gather(project_id)
    if not tasks:
        return {"error": "project not found or has no tasks", "dependencies": []}
    try:
        return infer_dependencies(name, tasks)
    except Exception as e:
        return {"error": f"inference failed: {e}", "dependencies": []}


# ═══════════════════════════════════════════════════════════════
# AUTOMATIC MAPPING
# The agents map dependencies WITHOUT being asked: a background pass finds a
# project whose tasks are distributed but whose dependencies aren't mapped yet,
# infers them, and stores them as PROVISIONAL contracts (status="proposed").
# Provisional = the agents did the work automatically, but the contracts don't
# start watching for drift until a human confirms (advise-before-dictate). The
# manager confirms with one sentence to the AI (confirm_dependency_map tool).
# ═══════════════════════════════════════════════════════════════

def map_project(project_id: int) -> dict:
    """Infer + store a project's dependency map as PROPOSED contracts, post it to
    the project channel, and notify the manager. Idempotent: replaces prior
    proposals, keeps already-confirmed (active) contracts."""
    proposal = propose_dependencies(project_id)
    deps = proposal.get("dependencies", [])
    if not deps:
        return {"project_id": project_id, "mapped": 0, "note": proposal.get("error", "no dependencies found")}

    from database.models import Project, Contract
    db = SessionLocal()
    try:
        proj = db.query(Project).filter(Project.id == project_id).first()
        if not proj:
            return {"project_id": project_id, "mapped": 0, "note": "project not found"}
        # replace any prior PROPOSED map; never touch confirmed (active) contracts
        db.query(Contract).filter(
            Contract.project_id == project_id, Contract.status == "proposed"
        ).delete()
        titles = {t.id: t.title for t in proj.tasks}
        created = []
        for d in deps:
            p, c = d.get("producer_task_id"), d.get("consumer_task_id")
            if p not in titles or c not in titles or p == c:
                continue
            co = d.get("contract", {}) or {}
            db.add(Contract(
                company_id=proj.company_id, project_id=project_id,
                producer_task_id=p, consumer_task_id=c,
                name=(co.get("name") or "interface")[:300],
                description=co.get("description") or "",
                status="proposed",
            ))
            created.append((titles[p], titles[c], co.get("name") or "interface"))
        db.commit()
        company_id, proj_name = proj.company_id, proj.name
    finally:
        db.close()

    if not created:
        return {"project_id": project_id, "mapped": 0}

    # Post the proposed map into the project channel (shared record)
    lines = "\n".join(f"🔗 {p} → {c}  ({name})" for p, c, name in created)
    body = (
        f"I analyzed \"{proj_name}\" and mapped these dependencies + interface contracts "
        f"(provisional — they begin watching for drift once you confirm):\n\n{lines}\n\n"
        f"Manager: say \"confirm the dependency map for {proj_name}\" to activate them, "
        f"or tell me which to drop."
    )
    try:
        from project_digest import _get_or_create_project_channel, _push_notif_async
        from chat_router import post_ai_message
        from database.models import Project, Employee, Notification
        db = SessionLocal()
        try:
            proj = db.query(Project).filter(Project.id == project_id).first()
            channel = _get_or_create_project_channel(proj, db)
            post_ai_message(db, channel, body, ai_agent_id="Nexus Coordinator", message_type="ai")
            # tap the manager(s) on the shoulder
            for m in db.query(Employee).filter(
                Employee.company_id == company_id, Employee.system_role == "manager"
            ).all():
                n = Notification(
                    company_id=company_id, recipient_id=m.id, type="dependency_map",
                    title="Dependency map ready",
                    message=f"I mapped {len(created)} dependencies for \"{proj_name}\" — review & confirm.",
                    entity_type="project", entity_id=project_id,
                )
                db.add(n); db.commit(); db.refresh(n)
                _push_notif_async(m.id, n)
        finally:
            db.close()
    except Exception as e:
        log.warning(f"map_project surface failed for {project_id}: {e}")

    return {"project_id": project_id, "project": proj_name, "mapped": len(created)}


# Cap how many projects one background pass will map. Each map_project() makes a
# multi-thousand-token Sonnet call, so a large backlog of unmapped projects would
# otherwise fire an unbounded burst of expensive inference in a single 30-min
# tick. The remainder is picked up on subsequent passes. (#13)
_MAX_MAPS_PER_RUN = int(os.getenv("DEP_MAP_MAX_PER_RUN", "5"))


def auto_map_unmapped_projects() -> dict:
    """Background pass: map projects that have ≥2 tasks but no contracts yet,
    at most _MAX_MAPS_PER_RUN per run to bound Claude cost/latency."""
    db = SessionLocal()
    try:
        from database.models import Project, Contract
        targets = [
            p.id for p in db.query(Project).all()
            if len(p.tasks) >= 2 and not db.query(Contract).filter(Contract.project_id == p.id).first()
        ]
    finally:
        db.close()

    total = len(targets)
    deferred = max(0, total - _MAX_MAPS_PER_RUN)
    if deferred:
        # Never silently truncate — log what got held back for the next pass.
        log.info(f"auto-map: {total} unmapped projects; mapping {_MAX_MAPS_PER_RUN} "
                 f"this run, deferring {deferred} to the next pass.")
        targets = targets[:_MAX_MAPS_PER_RUN]

    results = []
    for pid in targets:
        try:
            results.append(map_project(pid))
        except Exception as e:
            log.error(f"auto-map failed for project {pid}: {e}")
    return {"projects_mapped": len(results), "deferred": deferred, "details": results}


# ── Scheduler (runs the auto-mapper periodically) ─────────────
_scheduler = None


def start_mapping_scheduler():
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        log.warning("APScheduler not installed — automatic dependency mapping disabled.")
        return None
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        auto_map_unmapped_projects,
        IntervalTrigger(minutes=30),
        id="auto_dependency_mapping", replace_existing=True, misfire_grace_time=600,
    )
    _scheduler.start()
    log.info("⏰ Automatic dependency-mapping scheduler started — every 30 min")
    return _scheduler


def stop_mapping_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
