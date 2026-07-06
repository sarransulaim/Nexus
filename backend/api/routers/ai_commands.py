"""
ai_commands.py — AI Command Router
=====================================
Routes all commands through the Claude Orchestrator.
Keeps the TTS (text-to-speech) and manual audit endpoints.
Gemini will be re-added here for Google Workspace tasks in the next phase.
"""

import edge_tts
import tempfile
from fastapi.responses import FileResponse, JSONResponse
from fastapi import APIRouter, BackgroundTasks, Request, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.core import SessionLocal, get_db
from database.models import Task, Employee
from api.ws_manager import notifier
from api.security import get_current_user, require_manager
from api.claude_orchestrator import run_orchestrator, glass_brain_queue, load_agent_memory
from api.rate_limit import limiter
from fastapi.concurrency import run_in_threadpool
import asyncio

router = APIRouter()

# Cap concurrent orchestrator runs so a burst (or the 30-min schedulers firing)
# can't exhaust the threadpool and starve fast endpoints like /health. Each run
# is offloaded off the event loop via run_in_threadpool; the semaphore bounds
# how many run at once.
_orchestrator_sem = asyncio.Semaphore(8)


# ---------------------------------------------------------------------------
# SCHEMAS
# ---------------------------------------------------------------------------
class CommandRequest(BaseModel):
    manager_id: str
    command_text: str
    input_method: str = "manual"
    stream_id: str = ""     # client-chosen id for live-typing WS frames (optional)


# ---------------------------------------------------------------------------
# BACKGROUND HELPERS
# ---------------------------------------------------------------------------
async def broadcast_db_update():
    """Ping the React frontend to refresh its data after any DB change."""
    await notifier.broadcast("SYNC_REQUIRED")


# ---------------------------------------------------------------------------
# GLASS BRAIN LOOP
# This runs in main.py's lifespan and streams Claude's thinking
# to the frontend in real time via WebSocket.
# Imported here so main.py can access it.
# ---------------------------------------------------------------------------
# (glass_brain_queue is imported from claude_orchestrator)


# ---------------------------------------------------------------------------
# POST /manager/command — MAIN AI ENDPOINT
# ---------------------------------------------------------------------------
@router.post("/command")
@limiter.limit("30/minute")
async def process_command(
    request: Request,
    payload: CommandRequest,
    background_tasks: BackgroundTasks,
    current_user: Employee = Depends(get_current_user),
):
    """
    Routes a command through the Claude Orchestrator.

    SECURITY: the agent identity is DERIVED from the authenticated token, not the
    request body — otherwise any caller could impersonate any agent.
    AVAILABILITY: the orchestrator is a blocking, multi-call loop, so we run it in
    a threadpool behind a concurrency semaphore and rate-limit the endpoint — one
    client (or a burst) can't hang the whole API, including /health.
    """
    agent_id = "Manager_1" if current_user.system_role == "manager" else f"Employee_{current_user.id}"
    print(f"\n--- [INCOMING COMMAND] ---")
    print(f"Agent: {agent_id} | Input: {payload.command_text[:80]}")

    # Live-typing stream id: client-chosen, echoed into WS frames — so only a
    # safe charset is accepted (it is interpolated into the frame text).
    import re as _re
    stream_id = (payload.stream_id or "").strip()[:64]
    if stream_id and not _re.fullmatch(r"[A-Za-z0-9_-]+", stream_id):
        stream_id = ""

    try:
        async with _orchestrator_sem:
            final_response = await run_in_threadpool(
                run_orchestrator, agent_id, payload.command_text.strip(),
                None, stream_id or None,
            )
        # After any command, ping the frontend to refresh dashboard data
        background_tasks.add_task(broadcast_db_update)
        return {"status": "success", "ai_response": final_response}

    except Exception as e:
        print(f"\n❌ ORCHESTRATOR ERROR: {e}\n")
        # Don't leak exception internals to the client
        return {"status": "error", "ai_response": "The AI core hit an error processing that. Please try again."}


# ---------------------------------------------------------------------------
# POST /manager/trigger-audit — MANUAL AUDIT
# ---------------------------------------------------------------------------
@router.post("/trigger-audit")
async def trigger_manual_audit(current_user: Employee = Depends(require_manager)):
    """Forces an immediate system audit broadcast to the manager dashboard."""
    db = SessionLocal()
    try:
        active_tasks = db.query(Task).filter(Task.is_completed == False).count()
        if active_tasks > 0:
            alert = (
                f"Manager|Manual Audit: {active_tasks} unresolved directives pending. "
                f"Standing by for routing orders."
            )
        else:
            alert = "Manager|Manual Audit: All directives resolved. Registry is clear."

        await notifier.broadcast(f"THOUGHT:{alert}")
        return {"status": "success"}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# POST /manager/speak — TEXT TO SPEECH
# ---------------------------------------------------------------------------
@router.get("/command-history/{agent_id}")
def get_command_history(
    agent_id: str,
    current_user: Employee = Depends(get_current_user),
):
    """
    Returns the recent command thread for an agent (persisted conversation
    from AgentMemory). Text-only turns, last ~20 exchanges.

    SECURITY: an employee may only read their OWN Employee_{id} thread; a
    manager may read any (including Manager_1).
    """
    if current_user.system_role != "manager" and agent_id != f"Employee_{current_user.id}":
        raise HTTPException(status_code=403, detail="You can only read your own command history.")
    try:
        turns = load_agent_memory(agent_id)
    except Exception:
        turns = []

    thread = []
    for t in (turns or []):
        role = t.get("role")
        content = t.get("content", "")
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            content = " ".join(p for p in parts if p)
        if isinstance(content, str) and content.strip() and role in ("user", "assistant"):
            thread.append({"role": role, "content": content.strip()})

    return {"thread": thread[-40:]}


@router.post("/speak")
async def generate_speech(request: Request, current_user: Employee = Depends(get_current_user)):
    """
    Converts a text response to audio via Microsoft Edge TTS.
    Robust: retries the flaky free endpoint, and fails cleanly (503 + JSON)
    so the frontend simply skips audio instead of choking on a fake-200.
    """
    import asyncio
    try:
        data = await request.json()
        text = data.get("text", "Nexus standing by.")
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid request"})

    clean_text = (
        text.replace("**", "").replace("*", "").replace("#", "")
            .replace("_", "").replace("`", "")
    ).strip()
    if not clean_text:
        clean_text = "Done."

    voice = "en-US-AriaNeural"

    # Retry the flaky free Edge TTS endpoint a few times before giving up.
    last_err = None
    for attempt in range(3):
        try:
            temp_audio = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            temp_audio.close()
            communicate = edge_tts.Communicate(clean_text, voice, rate="+10%")
            await communicate.save(temp_audio.name)
            # Sanity check: did we actually get audio bytes?
            import os
            if os.path.getsize(temp_audio.name) > 0:
                return FileResponse(temp_audio.name, media_type="audio/mpeg")
            last_err = "empty audio file"
        except Exception as e:
            last_err = str(e)
            await asyncio.sleep(0.6 * (attempt + 1))  # brief backoff

    # All retries failed — fail cleanly so the frontend just skips audio.
    print(f"Voice generation unavailable after retries: {last_err}")
    return JSONResponse(status_code=503, content={"error": "voice unavailable"})