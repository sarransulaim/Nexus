"""
mcp_client.py — talk to an MCP server directly, without going through a model
============================================================================
Until now MCP was only reachable via the model: connections were handed to
Anthropic as `mcp_servers=[…]` and the model decided what to call. That is the
right design for "answer this question" and the wrong one for a scheduled
morning brief, where we want the same three fields every day. Going through a
model to fetch a list of tickets costs tokens per person per morning, takes
seconds, and can come back differently each time.

This is a small client for MCP's Streamable HTTP transport — plain JSON-RPC
2.0 over POST — so the briefing and drift engines can read from a connector
deterministically and for free.

Deliberately minimal: initialize, tools/list, tools/call. No prompts, no
resources, no notifications beyond the one the handshake requires. It exists
to answer "what work is assigned to this person", not to be a full SDK.

Transport notes that cost time if you don't know them:

* A server may answer a POST with either `application/json` or an SSE stream
  (`text/event-stream`), and it chooses — so both have to be parsed.
* `Mcp-Session-Id` comes back on initialize and must be echoed on every later
  request, or the server treats each call as a new unauthenticated session.
* The `notifications/initialized` message is a notification, not a request:
  it has no `id` and the server sends nothing back. Waiting for a reply to it
  hangs until the timeout.
"""

from __future__ import annotations

import json
import logging

import requests

log = logging.getLogger("nexus.mcp_client")

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_TIMEOUT = 20


class MCPError(RuntimeError):
    """The server was reached but refused, or answered something unusable."""


class MCPClient:
    """One short-lived conversation with one MCP server."""

    def __init__(self, url: str, token: str | None = None, timeout: int = DEFAULT_TIMEOUT):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.session_id: str | None = None
        self.server_info: dict = {}
        self._next_id = 0
        self._http = requests.Session()

    # ── plumbing ──────────────────────────────────────────────────
    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            # The server picks the response format; advertise both or a
            # streaming server will 406.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    @staticmethod
    def _parse(response) -> dict:
        """Read a JSON-RPC result from either a JSON body or an SSE stream."""
        content_type = (response.headers.get("content-type") or "").lower()

        if "text/event-stream" in content_type:
            # Frames look like `event: message\ndata: {...}\n\n`. We want the
            # first data payload carrying a JSON-RPC envelope.
            for raw_line in response.text.splitlines():
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                try:
                    message = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict) and ("result" in message or "error" in message):
                    return message
            raise MCPError("event stream contained no JSON-RPC response")

        try:
            return response.json()
        except ValueError:
            raise MCPError(f"non-JSON response ({response.status_code}): {response.text[:180]}")

    def _request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        body = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            body["params"] = params

        response = self._http.post(self.url, json=body, headers=self._headers(),
                                   timeout=self.timeout)

        # Captured on initialize; required on everything after it.
        session = response.headers.get("mcp-session-id") or response.headers.get("Mcp-Session-Id")
        if session:
            self.session_id = session

        if response.status_code == 401:
            raise MCPError("unauthorised — the stored token was rejected")
        if response.status_code >= 400:
            raise MCPError(f"HTTP {response.status_code}: {response.text[:180]}")

        message = self._parse(response)
        if "error" in message:
            err = message["error"] or {}
            raise MCPError(f"{err.get('code')}: {err.get('message')}")
        return message.get("result") or {}

    def _notify(self, method: str, params: dict | None = None) -> None:
        """Fire-and-forget: a notification has no id and gets no reply."""
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        try:
            self._http.post(self.url, json=body, headers=self._headers(), timeout=self.timeout)
        except requests.RequestException:
            pass    # the handshake notification is advisory; losing it is survivable

    # ── the three calls we actually need ──────────────────────────
    def connect(self) -> dict:
        result = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "nexus-command", "version": "1.0"},
        })
        self.server_info = result.get("serverInfo") or {}
        self._notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params)
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise MCPError(f"tool {name} reported failure: {str(result.get('content'))[:180]}")
        return result

    @staticmethod
    def text_of(result: dict) -> str:
        """Flatten a tool result's content blocks into plain text."""
        return "\n".join(
            block.get("text", "")
            for block in (result.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "text"
        )

    @staticmethod
    def data_of(result: dict):
        """A tool's structured payload, if it returned one.

        Servers may answer with `structuredContent`, or with JSON encoded
        inside a text block. Prefer the former; fall back to parsing the text,
        because most servers still do it the second way.
        """
        if isinstance(result.get("structuredContent"), (dict, list)):
            return result["structuredContent"]
        text = MCPClient.text_of(result)
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:
            pass

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def client_for(connection, decrypt) -> MCPClient:
    """Build a client for a stored MCPConnection, refreshing the token first
    if it has expired."""
    token = None
    if connection.auth_token_enc:
        try:
            token = decrypt(connection.auth_token_enc)
        except Exception:
            token = None
    return MCPClient(connection.url, token)
