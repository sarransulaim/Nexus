"""
ws_manager.py — Room-Based WebSocket Manager
==============================================
Enterprise-grade real-time messaging.

Message routing:
  broadcast()               → all connected clients (SYNC_REQUIRED)
  send_to_employee(id, msg) → one employee (THOUGHT, NOTIF)
  broadcast_to_channel()    → chat channel members only
  broadcast_to_meeting()    → meeting attendees only
  send_thought(agent_id)    → routes Glass Brain to correct employee

Dead connections are cleaned up on every failed send.
Thread-safe via asyncio.Lock.
"""

import json
import asyncio
import logging
from typing import Dict, Set, List
from fastapi import WebSocket

log = logging.getLogger("nexus.ws")


class ConnectionManager:

    def __init__(self):
        self._connections: Dict[int, WebSocket] = {}    # employee_id → ws
        self._channel_rooms: Dict[int, Set[int]] = {}   # channel_id → {employee_ids}
        self._meeting_rooms: Dict[int, Set[int]] = {}   # meeting_id → {employee_ids}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, employee_id: int):
        await websocket.accept()
        async with self._lock:
            old = self._connections.get(employee_id)
            if old:
                try:
                    await old.close()
                except Exception:
                    pass
            self._connections[employee_id] = websocket
        log.info(f"WS connected: Employee {employee_id} | total={len(self._connections)}")

    async def disconnect(self, employee_id: int):
        async with self._lock:
            self._connections.pop(employee_id, None)
            for members in self._channel_rooms.values():
                members.discard(employee_id)
            for members in self._meeting_rooms.values():
                members.discard(employee_id)

    # ── Room management ────────────────────────────

    async def join_channel(self, employee_id: int, channel_id: int):
        async with self._lock:
            self._channel_rooms.setdefault(channel_id, set()).add(employee_id)

    async def leave_channel(self, employee_id: int, channel_id: int):
        async with self._lock:
            if channel_id in self._channel_rooms:
                self._channel_rooms[channel_id].discard(employee_id)

    async def join_meeting(self, employee_id: int, meeting_id: int):
        async with self._lock:
            self._meeting_rooms.setdefault(meeting_id, set()).add(employee_id)

    async def leave_meeting(self, employee_id: int, meeting_id: int):
        async with self._lock:
            if meeting_id in self._meeting_rooms:
                self._meeting_rooms[meeting_id].discard(employee_id)

    # ── Internal send ──────────────────────────────

    async def _send_one(self, employee_id: int, message: str) -> bool:
        ws = self._connections.get(employee_id)
        if not ws:
            return True
        try:
            await ws.send_text(message)
            return True
        except Exception:
            self._connections.pop(employee_id, None)
            return False

    # ── Public methods ─────────────────────────────

    async def broadcast(self, message: str):
        """Send to ALL employees. Used for SYNC_REQUIRED."""
        async with self._lock:
            ids = list(self._connections.keys())
        dead = []
        for emp_id in ids:
            async with self._lock:
                ok = await self._send_one(emp_id, message)
            if not ok:
                dead.append(emp_id)

    async def send_to_employee(self, employee_id: int, message: str):
        """Send to one specific employee."""
        async with self._lock:
            await self._send_one(employee_id, message)

    async def broadcast_to_channel(self, channel_id: int, message: str, exclude_id: int = None):
        """Send to all members of a chat channel."""
        async with self._lock:
            members = self._channel_rooms.get(channel_id, set()).copy()
        for emp_id in members:
            if emp_id == exclude_id:
                continue
            async with self._lock:
                await self._send_one(emp_id, message)

    async def broadcast_to_meeting(self, meeting_id: int, message: str):
        """Send to all participants of a meeting."""
        async with self._lock:
            members = self._meeting_rooms.get(meeting_id, set()).copy()
        for emp_id in members:
            async with self._lock:
                await self._send_one(emp_id, message)

    async def send_thought(self, agent_id: str, thought: str):
        """Route Glass Brain thought to the correct employee."""
        message = f"THOUGHT:{agent_id}|{thought}"
        if agent_id.startswith("Employee_"):
            try:
                emp_id = int(agent_id.split("_")[1])
                await self.send_to_employee(emp_id, message)
            except (ValueError, IndexError):
                pass
        else:
            # Manager or unknown — broadcast (only manager is connected anyway)
            await self.broadcast(message)

    async def send_notification(self, employee_id: int, notif: dict):
        """Real-time notification push to one employee."""
        await self.send_to_employee(employee_id, f"NOTIF:{json.dumps(notif)}")

    async def send_chat_message(self, channel_id: int, data: dict, sender_id: int = None):
        """Push new chat message to channel members (excluding sender)."""
        await self.broadcast_to_channel(channel_id, f"CHAT:{channel_id}|{json.dumps(data)}", exclude_id=sender_id)

    async def send_meeting_event(self, meeting_id: int, event: dict):
        """Push meeting event to all attendees."""
        await self.broadcast_to_meeting(meeting_id, f"MEETING:{meeting_id}|{json.dumps(event)}")

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @property
    def connected_employee_ids(self) -> List[int]:
        return list(self._connections.keys())


notifier = ConnectionManager()
