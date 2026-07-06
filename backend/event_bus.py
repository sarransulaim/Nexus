"""
event_bus.py — In-Memory Pub/Sub for Admin Telemetry
======================================================
Every interesting thing that happens in Nexus emits an event here.
The admin WebSocket forwards events to connected admin dashboards.

Non-blocking, thread-safe, drops events if subscribers fall behind
(so a stuck admin tab can never slow down the main system).
"""

import time
import queue
import threading
import logging
from collections import deque
from typing import Optional

log = logging.getLogger("nexus.event_bus")


class EventBus:
    RING_BUFFER_SIZE      = 50
    SUBSCRIBER_QUEUE_SIZE = 500

    def __init__(self):
        self._lock        = threading.Lock()
        self._ring        = deque(maxlen=self.RING_BUFFER_SIZE)
        self._subscribers = []
        self._counters = {
            "total_events":       0,
            "total_tool_calls":   0,
            "total_db_queries":   0,
            "total_ai_calls":     0,
            "total_errors":       0,
            "total_negotiations": 0,
            "total_cost_usd":     0.0,
        }

    def emit(self, event_type: str, actor: Optional[str] = None, **data):
        event = {
            "ts":    time.time(),
            "type":  event_type,
            "actor": actor or "system",
            "data":  data or {},
        }
        with self._lock:
            self._ring.append(event)
            self._counters["total_events"] += 1
            if event_type == "tool_called":         self._counters["total_tool_calls"]   += 1
            elif event_type == "db_query":          self._counters["total_db_queries"]   += 1
            elif event_type == "agent_thinking":    self._counters["total_ai_calls"]     += 1
            elif event_type == "error":             self._counters["total_errors"]       += 1
            elif event_type == "negotiation_start": self._counters["total_negotiations"] += 1
            elif event_type == "cost_recorded":     self._counters["total_cost_usd"]     += float(data.get("cost_usd", 0))

            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=self.SUBSCRIBER_QUEUE_SIZE)
        with self._lock:
            for event in list(self._ring):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    break
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def get_snapshot(self) -> dict:
        with self._lock:
            return {
                "counters":      dict(self._counters),
                "recent_events": list(self._ring),
                "subscribers":   len(self._subscribers),
            }


event_bus = EventBus()


# ── Cost estimation ───────────────────────────────────────────
PRICING = {
    "claude-sonnet-4-6":  {"in": 3.00,  "out": 15.00},
    "claude-haiku-4-5":   {"in": 1.00,  "out": 5.00},
    "gemini-2.5-pro":     {"in": 1.25,  "out": 5.00},
    "qwen2.5:3b":         {"in": 0.00,  "out": 0.00},
    "qwen2.5:7b":         {"in": 0.00,  "out": 0.00},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """
    input_tokens is the UNcached remainder (the API reports cached tokens
    separately). Cache reads bill at 0.1x the input rate, cache writes at
    1.25x (5-minute TTL).
    """
    p = PRICING.get(model)
    if not p:
        return 0.0
    return (
        input_tokens * p["in"]
        + output_tokens * p["out"]
        + cache_read_tokens * p["in"] * 0.10
        + cache_write_tokens * p["in"] * 1.25
    ) / 1_000_000


# ── Convenience emitters ──────────────────────────────────────

def emit_agent_thinking(agent_id: str, model: str):
    event_bus.emit("agent_thinking", actor=agent_id, model=model)


def emit_agent_idle(agent_id: str, duration_ms: int = 0):
    event_bus.emit("agent_idle", actor=agent_id, duration_ms=duration_ms)


def emit_tool_called(agent_id: str, tool_name: str, tool_input: dict = None):
    safe_input = {}
    if tool_input:
        for k, v in tool_input.items():
            s = str(v)
            safe_input[k] = s[:100] if len(s) > 100 else s
    event_bus.emit("tool_called", actor=agent_id, tool=tool_name, input=safe_input)


def emit_tool_completed(agent_id: str, tool_name: str, success: bool = True):
    event_bus.emit("tool_completed", actor=agent_id, tool=tool_name, success=success)


def emit_db_query(table: str, operation: str = "write", actor: str = "system"):
    event_bus.emit("db_query", actor=actor, table=table, operation=operation)


def emit_negotiation_start(employees: list = None):
    event_bus.emit("negotiation_start", actor="negotiation_engine",
                   employees=employees or [])


def emit_negotiation_step(from_agent: str, to_agent: str, accepted: bool = None):
    event_bus.emit("negotiation_step", actor="negotiation_engine",
                   from_agent=from_agent, to_agent=to_agent, accepted=accepted)


def emit_negotiation_done(resolved: int = 0):
    event_bus.emit("negotiation_done", actor="negotiation_engine", resolved=resolved)


def emit_error(location: str, message: str, actor: str = "system"):
    event_bus.emit("error", actor=actor, location=location, message=message[:300])


def emit_cost(model: str, input_tokens: int, output_tokens: int, actor: str = "system",
              cache_read_tokens: int = 0, cache_write_tokens: int = 0):
    cost = estimate_cost(model, input_tokens, output_tokens,
                         cache_read_tokens, cache_write_tokens)
    event_bus.emit("cost_recorded", actor=actor,
                   model=model,
                   input_tokens=input_tokens,
                   output_tokens=output_tokens,
                   cache_read_tokens=cache_read_tokens,
                   cache_write_tokens=cache_write_tokens,
                   cost_usd=cost)


def emit_message(from_agent: str, to_agent: str, kind: str = "thought"):
    event_bus.emit("message_sent", actor=from_agent, to=to_agent, kind=kind)