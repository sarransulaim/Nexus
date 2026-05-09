"""
ai_commands.py — AI Command Router
=====================================
Routes all commands through the Claude Orchestrator.
Keeps the TTS (text-to-speech) and manual audit endpoints.
Gemini will be re-added here for Google Workspace tasks in the next phase.
"""

import edge_tts
import tempfile
from fastapi.responses import FileResponse
from fastapi import APIRouter, BackgroundTasks, Request, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.core import SessionLocal, get_db
from database.models import Task
from api.ws_manager import notifier
from api.claude_orchestrator import run_orchestrator, glass_brain_queue

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
def process_command(request: CommandRequest, background_tasks: BackgroundTasks):
    """
    Receives a command from the frontend and routes it through
    the Claude Orchestrator. Claude decides what tools to use,
    executes them, and returns a natural language response.
    """
    print(f"\n--- [INCOMING COMMAND] ---")
    print(f"Agent: {request.manager_id} | Input: {request.command_text[:80]}")

    try:
        # Run the Claude orchestrator (this may call multiple tools)
        final_response = run_orchestrator(
            agent_id=request.manager_id,
            command=request.command_text.strip()
        )

        # After any command, ping the frontend to refresh dashboard data
        background_tasks.add_task(broadcast_db_update)

        return {"status": "success", "ai_response": final_response}

    except Exception as e:
        error_msg = str(e)
        print(f"\n❌ ORCHESTRATOR ERROR: {error_msg}\n")
        return {"status": "error", "ai_response": f"System error: {error_msg}"}


# ---------------------------------------------------------------------------
# POST /manager/trigger-audit — MANUAL AUDIT
# ---------------------------------------------------------------------------
@router.post("/trigger-audit")
async def trigger_manual_audit():
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
@router.post("/speak")
async def generate_speech(request: Request):
    """
    Converts Claude's text response to audio using Microsoft's Edge TTS.
    Returns an MP3 file that the frontend plays through the voice orb.
    """
    try:
        data = await request.json()
        text = data.get("text", "Nexus standing by.")

        # Strip markdown so it sounds natural when spoken
        clean_text = (
            text.replace("*", "").replace("#", "")
                .replace("_", "").replace("`", "")
                .replace("**", "")
        )

        voice = "en-US-AriaNeural"
        temp_audio = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        temp_audio.close()

        communicate = edge_tts.Communicate(clean_text, voice, rate="+10%")
        await communicate.save(temp_audio.name)

        return FileResponse(temp_audio.name, media_type="audio/mpeg")

    except Exception as e:
        print(f"Voice generation failed: {e}")
        return {"error": "Voice generation failed"}