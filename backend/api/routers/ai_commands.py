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

router = APIRouter()


# ---------------------------------------------------------------------------
# SCHEMAS
# ---------------------------------------------------------------------------
class CommandRequest(BaseModel):
    manager_id: str
    command_text: str
    input_method: str = "manual"


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
def process_command(
    request: CommandRequest,
    background_tasks: BackgroundTasks,
    current_user: Employee = Depends(get_current_user),
):
    """
    Routes a command through the Claude Orchestrator.

    SECURITY: the agent identity is DERIVED from the authenticated token, not
    the request body — otherwise any caller could impersonate any agent
    (the manager or any employee) and drive the full toolset.
    """
    agent_id = "Manager_1" if current_user.system_role == "manager" else f"Employee_{current_user.id}"
    print(f"\n--- [INCOMING COMMAND] ---")
    print(f"Agent: {agent_id} | Input: {request.command_text[:80]}")

    try:
        final_response = run_orchestrator(
            agent_id=agent_id,
            command=request.command_text.strip()
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