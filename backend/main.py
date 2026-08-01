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

import hmac
import os
import asyncio
import queue
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
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

# Interactive docs enumerate every route, its parameters, and its schemas —
# a complete map of the attack surface, served to anyone. Useful locally,
# not something to publish. Railway sets RAILWAY_ENVIRONMENT=production;
# NEXUS_PUBLIC_DOCS=1 forces them back on if they're ever wanted there.
def is_production(env=None) -> bool:
    """Whether this process is a production deployment."""
    env = os.environ if env is None else env
    return (env.get("RAILWAY_ENVIRONMENT", "").lower() == "production"
            or env.get("NEXUS_ENV", "").lower() == "production")


def docs_enabled(env=None) -> bool:
    """Whether to publish /docs, /redoc and /openapi.json.

    A plain function of the environment so it can be tested directly. The
    first version of this test re-imported `main` under a patched environment
    to check the result, which mutates sys.modules for every test that runs
    afterwards — a fragile trick to verify a boolean.
    """
    env = os.environ if env is None else env
    return (not is_production(env)) or env.get("NEXUS_PUBLIC_DOCS", "") == "1"


IS_PRODUCTION = is_production()
_EXPOSE_DOCS = docs_enabled()

app = FastAPI(
    title="Nexus Command Enterprise API",
    version="3.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _EXPOSE_DOCS else None,
    redoc_url="/redoc" if _EXPOSE_DOCS else None,
    openapi_url="/openapi.json" if _EXPOSE_DOCS else None,
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


# ── Request body size cap ─────────────────────────────────────
# Nothing bounded how much a client could POST. A single multi-hundred-MB
# JSON body is read into memory before any handler sees it, and /manager/command
# forwards its body into a paid model call — so an unbounded body is both a
# memory-exhaustion lever and a way to run up the API bill.
MAX_BODY_BYTES   = int(os.getenv("MAX_REQUEST_BODY_MB", "2")) * 1024 * 1024
MAX_UPLOAD_BYTES = 50 * 1024 * 1024   # matches file_intelligence.MAX_FILE_SIZE
_UPLOAD_PATHS    = ("/api/v1/files/upload",)


# ── Security response headers ─────────────────────────────────
# This service answers API calls for a separate frontend origin, so most of
# these matter less than they would for a site that renders its own HTML —
# but it DOES serve HTML on the OAuth callback, and a browser that is tricked
# into treating a JSON response as a document is exactly the case these
# headers close. Cheap, and they cost nothing to keep correct.
_SECURITY_HEADERS = {
    # No inline anything by default; the OAuth callback overrides this with its
    # own nonce policy (see routers/mcp.py). frame-ancestors 'none' is the
    # modern X-Frame-Options and stops the API being framed for clickjacking.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    # Stop browsers guessing a JSON/text response is HTML and executing it.
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",              # for browsers predating frame-ancestors
    "Referrer-Policy": "no-referrer",       # never leak our URLs to third parties
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}

# Deliberately NOT set, with reasons — both would break working features:
#   Cross-Origin-Opener-Policy: same-origin  severs window.opener, and the MCP
#     OAuth popup calls window.opener.postMessage to tell the app it finished.
#   Cross-Origin-Resource-Policy: same-site  blocks no-cors loads from another
#     site, and the frontend (vercel.app) is a different site from this API
#     (railway.app) — it would break <audio> playback of /manager/speak.
# Neither buys much for a JSON API that exists to be called cross-site.

# Swagger loads its JS/CSS from a CDN, so the blanket default-src 'none' policy
# would leave a blank page wherever docs are enabled (local dev).
_CSP_EXEMPT_PATHS = ("/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    exempt_csp = request.url.path.startswith(_CSP_EXEMPT_PATHS)
    for header, value in _SECURITY_HEADERS.items():
        if header == "Content-Security-Policy" and exempt_csp:
            continue
        # setdefault semantics: a route that set its own policy (the OAuth
        # callback's nonce CSP) must win over the blanket default.
        if header not in response.headers:
            response.headers[header] = value
    # HSTS only over TLS — sending it on plain http is meaningless, and
    # asserting it from a local dev server would pin localhost to https in the
    # developer's browser for a year.
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    cap = MAX_UPLOAD_BYTES if request.url.path.startswith(_UPLOAD_PATHS) else MAX_BODY_BYTES
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > cap:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Request body too large (limit {cap // (1024*1024)} MB)."},
                )
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length."})
    return await call_next(request)


# NOTE: a chunked request sends no Content-Length, so this check can't see its
# size up front. The upload route streams and counts bytes itself (files.py
# aborts past MAX_FILE_SIZE); browsers send Content-Length for ordinary JSON.
# A byte-counting ASGI wrapper would close the gap fully — worth doing if we
# ever accept chunked bodies on non-upload routes.


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

# Channels router (Phase 4) — Slack/Slack omnichannel
from channels_router import router as channels_router
app.include_router(channels_router, prefix="/api/v1/channels", tags=["Channels"])

from chat_router import router as chat_router
app.include_router(chat_router, prefix="/api/v1/chat", tags=["Team Chat"])


# ── Internal sync (Slack bot pings this) ─────────────────────

@app.post("/api/v1/internal/sync")
async def internal_sync(x_internal_token: str = Header(default="")):
    """In-process callers (the Slack bot) nudge every connected dashboard to
    refetch. It was unauthenticated, so anyone who could reach the port could
    force a fan-out to all clients. Gated on a secret derived from JWT_SECRET,
    so there's no new env var to manage."""
    from api.security import internal_token
    if not hmac.compare_digest(x_internal_token or "", internal_token()):
        raise HTTPException(status_code=401, detail="Invalid internal token.")
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
    from api.security import decode_token, ws_token_from, WS_AUTH_SUBPROTOCOL
    token, from_query = ws_token_from(websocket)
    accept_subprotocol = None if from_query else WS_AUTH_SUBPROTOCOL
    if from_query and token:
        print(f"⚠️  WS {employee_id} authenticated via ?token= — deprecated (proxies log "
              f"the URL). Update the client to offer the '{WS_AUTH_SUBPROTOCOL}' subprotocol.")
    try:
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
        emp = db.query(Employee).filter(
            Employee.id == employee_id,
            Employee.is_active == True,   # noqa: E712 — a deactivated account kept streaming
        ).first()
        if not emp:
            await websocket.close(code=1008)
            return
        is_manager = bool(emp.system_role == "manager")
        channel_ids = [m.channel_id for m in db.query(ChannelMember).filter(
            ChannelMember.employee_id == employee_id).all()]
    finally:
        db.close()

    await notifier.connect(websocket, employee_id, is_manager=is_manager,
                           subprotocol=accept_subprotocol)
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