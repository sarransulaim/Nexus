from fastapi import WebSocket
from typing import List

class ConnectionManager:
    def __init__(self):
        # This keeps a list of every user currently looking at your React app
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        # Send a message to everyone instantly
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

# Create a global intercom system
notifier = ConnectionManager()