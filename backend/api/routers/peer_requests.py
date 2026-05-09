from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database.core import get_db
from database.models import PeerRequest
from api.ws_manager import notifier

router = APIRouter()

# The data React sends when the button is clicked
class RespondPayload(BaseModel):
    action: str  # Will be "Accepted" or "Declined"

async def broadcast_db_update():
    """Instantly pings the React frontend to refresh."""
    await notifier.broadcast("SYNC_REQUIRED")

@router.post("/{req_id}/respond")
def respond_to_request(req_id: int, payload: RespondPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    # 1. Find the request in the database
    req = db.query(PeerRequest).filter(PeerRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Peer Request not found")
    
    # 2. Update it to Accepted or Declined
    req.status = payload.action
    db.commit()
    
    # 3. Pull the WebSocket Fire Alarm
    background_tasks.add_task(broadcast_db_update)
    
    return {"status": "success", "message": f"Request {payload.action}"}