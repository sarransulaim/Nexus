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

Concurrency: guarded by a plain **threading.Lock** (loop-agnostic). The lock
protects ONLY the in-memory registries and is NEVER held across an ``await`` —
so broadcasts fired from background threads (each on its own ``asyncio.run``
loop) can't deadlock or raise on a cross-event-loop ``asyncio.Lock`` (which
would silently drop SYNC_REQUIRED / chat / notification pushes). Targets are
snapshotted under the lock, then the sends happen outside it.
"""

import json
import logging
import threading
from typing import Dict, Set, List
from fastapi import WebSocket

log = logging.getLogger("nexus.ws")


class ConnectionManager:

    def __init__(self):
        self._connections: Dict[int, WebSocket] = {}    # employee_id → ws
        self._channel_rooms: Dict[int, Set[int]] = {}   # channel_id → {employee_ids}
        self._meeting_rooms: Dict[int, Set[int]] = {}   # meeting_id → {employee_ids}
        self._managers: Set[int] = set()                # connected employee_ids who are managers
        self._lock = threading.Lock()   # guards registries only; never held across an await

    async def connect(self, websocket: WebSocket, employee_id: int, is_manager: bool = False):
        await websocket.accept()
        with self._lock:
            old = self._connections.get(employee_id)
            self._connections[employee_id] = websocket
            if is_manager:
                self._managers.add(employee_id)
        if old is not None and old is not websocket:
            try:
                await old.close()   # outside the lock — never await while holding it
            except Exception:
                pass
        log.info(f"WS connected: Employee {employee_id} | total={len(self._connections)}")

    async def disconnect(self, employee_id: int, websocket: WebSocket = None):
        # Identity-aware: only forget the employee if the socket being torn down is
        # the one CURRENTLY registered. A stale socket's teardown (after the client
        # reconnected) must not evict the fresh socket — otherwise the server
        # silently stops delivering notifications/chat to a "connected" client.
        with self._lock:
            if websocket is None or self._connections.get(employee_id) is websocket:
                self._connections.pop(employee_id, None)
                self._managers.discard(employee_id)
                for members in self._channel_rooms.values():
                    members.discard(employee_id)
                for members in self._meeting_rooms.values():
                    members.discard(employee_id)

    # ── Room management ────────────────────────────

    async def join_channel(self, employee_id: int, channel_id: int):
        with self._lock:
            self._channel_rooms.setdefault(channel_id, set()).add(employee_id)

    async def leave_channel(self, employee_id: int, channel_id: int):
        with self._lock:
            if channel_id in self._channel_rooms:
                self._channel_rooms[channel_id].discard(employee_id)

    async def join_meeting(self, employee_id: int, meeting_id: int):
        with self._lock:
            self._meeting_rooms.setdefault(meeting_id, set()).add(employee_id)

    async def leave_meeting(self, employee_id: int, meeting_id: int):
        with self._lock:
            if meeting_id in self._meeting_rooms:
                self._meeting_rooms[meeting_id].discard(employee_id)

    # ── Internal send: snapshot targets under the lock, await sends OUTSIDE it ──

    async def _send_targets(self, targets: List, message: str):
        """targets = list of (employee_id, websocket). Sends happen with NO lock
        held; dead sockets are pruned afterward (only if still the registered one)."""
        dead = []
        for eid, ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append((eid, ws))
        if dead:
            with self._lock:
                for eid, ws in dead:
                    if self._connections.get(eid) is ws:   # don't evict a reconnected socket
                        self._connections.pop(eid, None)

    # ── Public methods ─────────────────────────────

    async def broadcast(self, message: str):
        """Send to ALL employees. Used for SYNC_REQUIRED."""
        with self._lock:
            targets = list(self._connections.items())
        await self._send_targets(targets, message)

    async def send_to_employee(self, employee_id: int, message: str):
        """Send to one specific employee."""
        with self._lock:
            ws = self._connections.get(employee_id)
            targets = [(employee_id, ws)] if ws is not None else []
        await self._send_targets(targets, message)

    async def broadcast_to_channel(self, channel_id: int, message: str, exclude_id: int = None):
        """Send to all members of a chat channel."""
        with self._lock:
            targets = [(m, self._connections[m])
                       for m in self._channel_rooms.get(channel_id, set())
                       if m != exclude_id and m in self._connections]
        await self._send_targets(targets, message)

    async def broadcast_to_meeting(self, meeting_id: int, message: str):
        """Send to all participants of a meeting."""
        with self._lock:
            targets = [(m, self._connections[m])
                       for m in self._meeting_rooms.get(meeting_id, set())
                       if m in self._connections]
        await self._send_targets(targets, message)

    async def broadcast_to_managers(self, message: str):
        """Send only to connected MANAGER employees (manager-scoped telemetry —
        never leak manager glass-brain / negotiation reports to employees)."""
        with self._lock:
            targets = [(eid, ws) for eid, ws in self._connections.items()
                       if eid in self._managers]
        await self._send_targets(targets, message)

    async def send_stream(self, agent_id: str, frame: str):
        """Raw live-typing frames (STREAM/STREAM_RESET/STREAM_END:...) routed to
        the caller like thoughts, but WITHOUT the THOUGHT: prefix."""
        if agent_id.startswith("Employee_"):
            try:
                await self.send_to_employee(int(agent_id.split("_")[1]), frame)
            except (ValueError, IndexError):
                pass
        else:
            await self.broadcast_to_managers(frame)

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
            # Manager or unknown — managers only (never leak manager telemetry to all).
            await self.broadcast_to_managers(message)

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
