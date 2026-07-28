"""
main.py — Nexus Command API Gateway v3.0
==========================================
Phase 1 — Hardened core.

Features:
  - Health endpoint at /health
  - Rate limiting via slowapi (extracted to api/rate_limit.py)
  - Room-based WebSocket: /api/v1/ws/{employee_id}
  - Glass Brain queue properly drained per agent
  - Company bootstrap on first run
  - CORS locked to localhost for local dev
  - Schema managed manually via create_tables.py (Alembic disabled)
"""

import os
import asyncio
import queue
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv

load_dotenv()

# Only relax OAuth transport security for LOCAL/dev (http redirect URI). In
# production the redirect URI is https, so OAuth stays strict.
if os.getenv("GOOGLE_REDIRECT_URI", "http://localhost").startswith("http://"):
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# ── Schema management ─────────────────────────────────────────
# When you change models.py, drop and recreate the DB:
#   psql -U postgres -d nexus_core -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
#   python create_tables.py
print("✅ Schema managed via create_tables.py — Alembic auto-run disabled")

from api.ws_manager import notifier
from api.routers import tasks, employees, meetings, ai_commands, peer_requests, auth, notifications, goals, approvals
from api.routers import analytics, files as files_router, integrations, mcp, team_lead, attention
from api.google_router import router as google_router
from database.core import SessionLocal
from database.models import Task, Company, Employee
from api.claude_orchestrator import glass_brain_queue
from negotiation_engine import negotiation_engine
from api.rate_limit import limiter
from api.security import require_manager


# ── Company bootstrap ────────────────────────────────────────
def _bootstrap_company():
    """Create company_id=1 on first run."""
    db = SessionLocal()
    try:
        existing = db.query(Company).first()
        if not existing:
            company = Company(
                name="Nexus Command",
                slug="nexus-command",
                plan="enterprise",
            )
            db.add(company)
            db.commit()
            print(f"✅ Bootstrap: Company 'Nexus Command' created (id=1)")
        else:
            print(f"✅ Company: '{existing.name}' (id={existing.id})")
        # B2 guard: the AI tools hardcode company_id=1, so a single-tenant pilot
        # instance must hold exactly one company with id=1. A stray second company
        # (or a non-1 id) would silently mis-scope data until per-company query
        # scoping (Tier-4) lands.
        companies = db.query(Company).all()
        if len(companies) != 1 or companies[0].id != 1:
            print(f"⚠️  SECURITY: expected exactly ONE company (id=1) for a "
                  f"single-tenant pilot, found {len(companies)} "
                  f"(ids={[c.id for c in companies]}). The AI tools hardcode "
                  f"company_id=1 and would mis-scope data across companies — do "
                  f"not run a pilot with more than one company in this database.")
    finally:
        db.close()


# ── Background loops ──────────────────────────────────────────

async def proactive_agent_loop():
    """
    Hourly system audit broadcast to manager Glass Brain.

    FIX (Bug 10): Looks up actual manager ID from DB instead of
    hardcoding Manager_1 (which only works if manager has dbId=1).
    The "Manager_1" agent_id format is still correct because
    send_thought broadcasts to all when target starts with "Manager_".
    """
    await asyncio.sleep(10)
    while True:
        await asyncio.sleep(3600)
        db = SessionLocal()
        try:
            active_tasks = db.query(Task).filter(Task.is_completed == False).count()
            if active_tasks > 0:
                # Manager agent_id format is "Manager_1" (singleton — broadcasts to managers)
                await notifier.send_thought(
                    "Manager_1",
                    f"Sir, audit complete. {active_tasks} unresolved directives in registry. Standing by."
                )
        except Exception as e:
            print(f"Proactive loop error: {e}")
        finally:
            db.close()


async def glass_brain_loop():
    """
    Drains glass_brain_queue and routes thoughts to the right employee.
    Thought format expected from orchestrator:
      "Agent_ID|[GLASS BRAIN] message"
    """
    while True:
        await asyncio.sleep(0.1)
        while True:
            try:
                thought = glass_brain_queue.get_nowait()
                if "|" in thought:
                    agent_id = thought.split("|")[0].strip()
                    text = thought.split("|", 1)[1]
                    if text.startswith("STREAM"):      # live-typing frames, raw
                        await notifier.send_stream(agent_id, text)
                    else:
                        await notifier.send_thought(agent_id, text.strip())
                else:
                    await notifier.broadcast(f"THOUGHT:{thought}")
            except queue.Empty:
                break
            except Exception:
                break


# ── Lifespan ──────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    _bootstrap_company()
    task1 = asyncio.create_task(proactive_agent_loop())
    task2 = asyncio.create_task(glass_brain_loop())
    # Negotiation runs synchronous multi-agent Claude calls (10-60s each) — run it
    # in its OWN daemon thread (own event loop + own DB session) so a cycle can
    # never freeze the FastAPI event loop and block every HTTP + WebSocket request.
    import threading as _threading
    _threading.Thread(target=lambda: asyncio.run(negotiation_engine.run_forever()),
                      daemon=True, name="negotiation").start()
    # Phase 5: autonomous morning briefings (APScheduler, Mon-Fri)
    try:
        from autonomous_briefings import start_scheduler, stop_scheduler
        start_scheduler()
    except Exception as e:
        print(f"⚠️  Briefing scheduler failed to start: {e}")
    # Phase 6 (Step 3): proactive engine — periodic watcher (every 15 min)
    try:
        from proactive_engine import start_proactive_scheduler
        start_proactive_scheduler()
    except Exception as e:
        print(f"⚠️  Proactive engine failed to start: {e}")
    # Daily Project Digest — AI posts "what moved / what's blocked on what" into each project channel
    try:
        from project_digest import start_digest_scheduler
        start_digest_scheduler()
    except Exception as e:
        print(f"⚠️  Project digest scheduler failed to start: {e}")
    # Automatic dependency mapping — agents map a project's dependencies (as provisional
    # contracts) without being asked; the manager confirms to activate drift-watching.
    try:
        from dependency_inference import start_mapping_scheduler
        start_mapping_scheduler()
    except Exception as e:
        print(f"⚠️  Dependency-mapping scheduler failed to start: {e}")
    # RAG health: the embedding model can silently disappear from Ollama, which
    # breaks semantic search/ingest with no error. Probe it in the BACKGROUND (off
    # the event loop) so a slow/absent Ollama can't block app boot for up to 60s.
    async def _rag_healthcheck():
        try:
            import rag
            ok = await asyncio.to_thread(lambda: len(rag.embed_text("healthcheck")) == rag.EMBED_DIM)
            if not ok:
                raise RuntimeError("dim mismatch")
        except Exception:
            print("⚠️  RAG: embedding model unavailable — semantic search/ingest will silently fail. "
                  "Fix with:  ollama pull nomic-embed-text")
    asyncio.create_task(_rag_healthcheck())
    # Slack bot — auto-start in background, GATED by SLACK_ENABLED. Its Socket-Mode
    # WebSocket can be force-closed by some networks / firewalls / antivirus, and the
    # resulting reconnect storm has crashed this process (segfault). Set
    # SLACK_ENABLED=false to keep Nexus stable when Slack can't hold a connection.
    if os.getenv("SLACK_ENABLED", "true").lower() not in ("false", "0", "no"):
        try:
            import slack_bot
            slack_bot.start_in_background()
        except Exception as e:
            print(f"⚠️  Slack bot failed to start: {e}")
    else:
        print("ℹ️  Slack bot disabled (SLACK_ENABLED=false) — re-enable when on a network where Slack stays connected.")
    yield
    task1.cancel()
    task2.cancel()
    # negotiation runs in a daemon thread (dies with the process) — nothing to cancel
    try:
        from autonomous_briefings import stop_scheduler
        stop_scheduler()
    except Exception:
        pass
    try:
        from proactive_engine import stop_proactive_scheduler
        stop_proactive_scheduler()
    except Exception:
        pass
    try:
        from project_digest import stop_digest_scheduler
        stop_digest_scheduler()
    except Exception:
        pass
    try:
        from dependency_inference import stop_mapping_scheduler
        stop_mapping_scheduler()
    except Exception:
        pass


# ── App ───────────────────────────────────────────────────────

app = FastAPI(
    title="Nexus Command Enterprise API",
    version="3.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health check ──────────────────────────────────────────────

@app.get("/health")
def health_check():
    """For cloud platforms and load balancers."""
    db_ok = False
    db = SessionLocal()
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
    finally:
        db.close()

    return {
        "status":         "healthy" if db_ok else "degraded",
        "db":             "ok" if db_ok else "error",
        "ws_connections": notifier.connection_count,
        "brain_queue":    glass_brain_queue.qsize(),
        "version":        "3.0.0",
    }


@app.get("/")
def read_root():
    return {"status": "Nexus Command Online", "version": "3.0.0"}


# ── Routers ───────────────────────────────────────────────────

app.include_router(auth.router,          prefix="/api/v1/auth",          tags=["Auth"])
app.include_router(tasks.router,         prefix="/api/v1/tasks",         tags=["Tasks"])
app.include_router(employees.router,     prefix="/api/v1/employees",     tags=["Employees"])
app.include_router(meetings.router,      prefix="/api/v1/meetings",      tags=["Meetings"])
app.include_router(peer_requests.router, prefix="/api/v1/peer-requests", tags=["Peer Requests"])
app.include_router(notifications.router, prefix="/api/v1/notifications", tags=["Notifications"])
app.include_router(goals.router,         prefix="/api/v1/goals",         tags=["Goals"])
app.include_router(approvals.router,     prefix="/api/v1/approvals",     tags=["Approvals"])
app.include_router(integrations.router,  prefix="/api/v1/integrations",  tags=["Integrations"])
app.include_router(mcp.router,           prefix="/api/v1/mcp",           tags=["MCP / Connectors"])
app.include_router(team_lead.router,     prefix="/api/v1/team-lead",     tags=["Team Lead"])
app.include_router(attention.router,     prefix="/api/v1/attention",     tags=["Attention"])
app.include_router(analytics.router,     prefix="/api/v1/analytics",     tags=["Analytics"])
app.include_router(google_router,        prefix="/api/v1/google",        tags=["Google Workspace"])
app.include_router(ai_commands.router,   prefix="/api/v1/manager",       tags=["AI Swarm"])
app.include_router(files_router.router,  prefix="/api/v1/files",         tags=["File Intelligence"])

# Admin router (Phase 3) — manager-only dashboard
from admin_router import router as admin_router
app.include_router(admin_router, prefix="/api/v1/admin", tags=["Admin"])

# Channels router (Phase 4) — WhatsApp/Telegram/Slack omnichannel
from channels_router import router as channels_router
app.include_router(channels_router, prefix="/api/v1/channels", tags=["Channels"])

from chat_router import router as chat_router
app.include_router(chat_router, prefix="/api/v1/chat", tags=["Team Chat"])


# ── Internal sync (Slack bot pings this) ─────────────────────

@app.post("/api/v1/internal/sync")
async def internal_sync():
    await notifier.broadcast("SYNC_REQUIRED")
    return {"status": "ok"}


# ── Manual briefing trigger (demo / testing) ─────────────────

@app.post("/api/v1/admin/run-briefings-now")
async def run_briefings_now(current_user: Employee = Depends(require_manager)):
    """Fire morning briefings immediately (demo/testing). Manager only."""
    from autonomous_briefings import run_all_briefings
    # force=True so repeated demo runs always send
    results = run_all_briefings(force=True)
    return {"status": "done", "results": results}


# ── Manual proactive scan trigger (demo / testing) ───────────

@app.post("/api/v1/admin/run-proactive-scan-now")
async def run_proactive_scan_now_endpoint(current_user: Employee = Depends(require_manager)):
    """
    Fire a proactive scan immediately instead of waiting for the 15-min
    interval. Ignores the dedup guard so repeated demo runs always surface.
    Manager only.
    """
    from proactive_engine import run_proactive_scan_now
    return run_proactive_scan_now()


# ── Manual project-digest trigger (demo / testing) ───────────

@app.post("/api/v1/admin/run-digests-now")
async def run_digests_now_endpoint(current_user: Employee = Depends(require_manager)):
    """
    Post the daily project digest into every project channel right now, instead
    of waiting for the scheduled end-of-day run. For demos. Manager only.
    """
    from project_digest import run_all_digests
    return run_all_digests(force=True)


@app.post("/api/v1/admin/run-dependency-mapping-now")
async def run_dependency_mapping_now_endpoint(current_user: Employee = Depends(require_manager)):
    """
    Map dependencies now for any project that has tasks but no contracts yet
    (instead of waiting for the 30-min auto pass). For demos. Manager only.
    """
    from dependency_inference import auto_map_unmapped_projects
    return auto_map_unmapped_projects()


# ── WebSocket endpoint (room-based) ──────────────────────────

@app.websocket("/api/v1/ws/{employee_id}")
async def websocket_endpoint(websocket: WebSocket, employee_id: int):
    """
    Room-based WebSocket. Requires a valid access token (?token=) whose subject
    matches employee_id, so a client can only connect as itself — otherwise it
    could eavesdrop on another user's notifications and channel broadcasts.
    """
    token = websocket.query_params.get("token")
    try:
        from api.security import decode_token
        payload = decode_token(token) if token else None
        if (not payload or payload.get("type") != "access"
                or int(payload.get("sub")) != employee_id):
            await websocket.close(code=1008)
            return
    except Exception:
        await websocket.close(code=1008)
        return

    # Look up role (for manager-scoped telemetry) + channel memberships in ONE
    # short session, before registering the socket.
    is_manager = False
    channel_ids = []
    db = SessionLocal()
    try:
        from database.models import ChannelMember, Employee
        emp = db.query(Employee).filter(Employee.id == employee_id).first()
        is_manager = bool(emp and emp.system_role == "manager")
        channel_ids = [m.channel_id for m in db.query(ChannelMember).filter(
            ChannelMember.employee_id == employee_id).all()]
    except Exception:
        pass
    finally:
        db.close()

    await notifier.connect(websocket, employee_id, is_manager=is_manager)
    for cid in channel_ids:
        await notifier.join_channel(employee_id, cid)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await notifier.disconnect(employee_id, websocket)
    except Exception:
        await notifier.disconnect(employee_id, websocket)


# ── Entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("🚀 Nexus Command v3.0 starting...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)