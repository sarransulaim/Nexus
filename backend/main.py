import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Allow HTTP for local OAuth dev (remove in production)
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
from api.ws_manager import notifier
from api.routers import tasks, employees, meetings, ai_commands, peer_requests, auth
from api.routers import notifications
from api.google_router import router as google_router
from database.core import engine, Base
import asyncio
from contextlib import asynccontextmanager
from database.core import SessionLocal
from database.models import Task, Employee
from api.claude_orchestrator import glass_brain_queue

# NOTE: Tables are managed by Alembic migrations — do NOT use create_all here

load_dotenv()

async def proactive_agent_loop():
    """This function loops silently in the background forever."""
    await asyncio.sleep(10) 
    
    while True:
        # THE FIX 1: Slow the heartbeat down to 5 minutes (300 seconds)
        await asyncio.sleep(3600) 
        
        db = SessionLocal()
        try:
            active_tasks = db.query(Task).filter(Task.is_completed == False).count()
            
            if active_tasks > 0:
                # THE FIX 2: We add "Manager|" to the very front of the message
                alert = f"Manager|Sir, I have audited the registry. We currently have {active_tasks} unresolved directives pending execution. I am standing by for routing orders."
                
                await notifier.broadcast(f"THOUGHT:{alert}")
                print("⚡ Proactive Agent Fired!")
        except Exception as e:
            print(f"Proactive loop error: {e}")
        finally:
            db.close()

async def glass_brain_loop():
    """Reads thoughts from the AI tools and broadcasts them instantly to React."""
    while True:
        await asyncio.sleep(0.1)  # Check the AI's brain 10 times a second!
        while not glass_brain_queue.empty():
            thought = glass_brain_queue.get()
            await notifier.broadcast(f"THOUGHT:{thought}")

# The Lifespan manager boots the agent when Uvicorn starts
@asynccontextmanager
async def lifespan(app: FastAPI):
    task1 = asyncio.create_task(proactive_agent_loop())
    task2 = asyncio.create_task(glass_brain_loop())
    yield
    task1.cancel()
    task2.cancel()

app = FastAPI(
    title="Nexus Core Enterprise API",
    description="Backend gateway for the AI Swarm and Second Brain architecture.",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "Nexus Core Gateway Online", "version": "2.0.0"}

# --- ROUTER INJECTIONS ---
app.include_router(auth.router,          prefix="/api/v1/auth",          tags=["Auth"])
app.include_router(tasks.router,         prefix="/api/v1/tasks",         tags=["Tasks"])
app.include_router(employees.router,     prefix="/api/v1/employees",     tags=["Employees"])
app.include_router(meetings.router,      prefix="/api/v1/meetings",      tags=["Meetings"])
app.include_router(peer_requests.router, prefix="/api/v1/peer-requests", tags=["Peer Requests"])
app.include_router(notifications.router, prefix="/api/v1/notifications", tags=["Notifications"])
app.include_router(google_router,        prefix="/api/v1/google",        tags=["Google Workspace"])
app.include_router(ai_commands.router,   prefix="/api/v1/manager",       tags=["AI Swarm"])

@app.post("/api/v1/internal/sync")
async def internal_sync():
    """Called by Slack bot to broadcast SYNC_REQUIRED after DB changes."""
    await notifier.broadcast("SYNC_REQUIRED")
    return {"status": "ok"}


@app.websocket("/api/v1/ws")
async def websocket_endpoint(websocket: WebSocket):
    await notifier.connect(websocket)
    try:
        while True:
            # Keep the bridge open forever
            await websocket.receive_text() 
    except WebSocketDisconnect:
        notifier.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    print("🚀 Booting Nexus Core Enterprise Gateway...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)