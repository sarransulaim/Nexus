"""
The direct MCP client — verified against a mock server that speaks the real
protocol, so the transport details are pinned without depending on a live
third-party connector or a valid token.

Every one of these covers something that actually costs an afternoon when you
get it wrong: session ids, SSE-vs-JSON responses, and the initialized
notification that gets no reply.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from api.mcp_client import MCPClient, MCPError


class _MockMCP(BaseHTTPRequestHandler):
    """Minimal MCP server. Class attributes configure per-test behaviour."""

    mode = "json"            # "json" | "sse"
    require_session = True
    seen_headers: list = []
    notifications: list = []

    def log_message(self, *args):
        pass                  # keep pytest output clean

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        type(self).seen_headers.append(dict(self.headers))
        method = body.get("method")

        # Notifications have no id and MUST NOT get a JSON-RPC reply.
        if "id" not in body:
            type(self).notifications.append(method)
            self.send_response(202)
            self.end_headers()
            return

        if method == "initialize":
            result = {"protocolVersion": "2025-06-18",
                      "serverInfo": {"name": "mock", "version": "0.1"}}
        elif method == "tools/list":
            # Exercise pagination: first page returns a cursor.
            if not body.get("params", {}).get("cursor"):
                result = {"tools": [{"name": "first"}], "nextCursor": "page2"}
            else:
                result = {"tools": [{"name": "second"}]}
        elif method == "tools/call":
            name = body["params"]["name"]
            if name == "explodes":
                result = {"isError": True, "content": [{"type": "text", "text": "boom"}]}
            elif name == "structured":
                result = {"structuredContent": {"issues": [{"key": "ABC-1"}]}}
            else:
                result = {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}
        else:
            payload = json.dumps({"jsonrpc": "2.0", "id": body["id"],
                                  "error": {"code": -32601, "message": "no such method"}})
            self._respond(payload)
            return

        self._respond(json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}),
                      set_session=(method == "initialize"))

    def _respond(self, payload: str, set_session: bool = False):
        if type(self).mode == "sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if set_session and type(self).require_session:
                self.send_header("Mcp-Session-Id", "sess-123")
            self.end_headers()
            self.wfile.write(f"event: message\ndata: {payload}\n\n".encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if set_session and type(self).require_session:
                self.send_header("Mcp-Session-Id", "sess-123")
            self.end_headers()
            self.wfile.write(payload.encode())


@pytest.fixture()
def server():
    _MockMCP.seen_headers = []
    _MockMCP.notifications = []
    _MockMCP.mode = "json"
    httpd = HTTPServer(("127.0.0.1", 0), _MockMCP)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/"
    httpd.shutdown()


def test_handshake_and_tool_listing(server):
    with MCPClient(server) as m:
        assert m.server_info.get("name") == "mock"
        names = [t["name"] for t in m.list_tools()]
    # Both pages — a client that ignores nextCursor silently sees half the tools.
    assert names == ["first", "second"]


def test_session_id_is_captured_and_echoed(server):
    """Servers issue a session on initialize and expect it back on every later
    call; without it each request looks like a brand-new session."""
    with MCPClient(server) as m:
        assert m.session_id == "sess-123"
        m.list_tools()
    later = [h for h in _MockMCP.seen_headers[1:] if "Mcp-Session-Id" in h]
    assert later, "session id was never echoed on subsequent requests"
    assert all(h["Mcp-Session-Id"] == "sess-123" for h in later)


def test_initialized_is_sent_as_a_notification(server):
    """It has no id and gets no reply — treating it as a request hangs."""
    with MCPClient(server):
        pass
    assert "notifications/initialized" in _MockMCP.notifications


def test_sse_responses_are_parsed(server):
    """The server chooses JSON or an event stream; both must work."""
    _MockMCP.mode = "sse"
    with MCPClient(server) as m:
        assert m.server_info.get("name") == "mock"
        assert [t["name"] for t in m.list_tools()] == ["first", "second"]


def test_tool_results_expose_text_and_structured_data(server):
    with MCPClient(server) as m:
        plain = m.call_tool("anything")
        assert MCPClient.data_of(plain) == {"ok": True}, "JSON-in-text not parsed"
        structured = m.call_tool("structured")
        assert MCPClient.data_of(structured) == {"issues": [{"key": "ABC-1"}]}


def test_a_failing_tool_raises_rather_than_returning_junk(server):
    with MCPClient(server) as m:
        with pytest.raises(MCPError, match="reported failure"):
            m.call_tool("explodes")


def test_unknown_method_surfaces_the_server_error(server):
    with MCPClient(server) as m:
        with pytest.raises(MCPError, match="no such method"):
            m._request("resources/list")


def test_auth_header_is_sent_when_a_token_is_present(server):
    with MCPClient(server, token="secret-value"):
        pass
    assert _MockMCP.seen_headers[0].get("Authorization") == "Bearer secret-value"


def test_no_auth_header_when_there_is_no_token(server):
    with MCPClient(server):
        pass
    assert "Authorization" not in _MockMCP.seen_headers[0]


def test_accepts_both_content_types(server):
    """A streaming-only server answers 406 if we advertise JSON alone."""
    with MCPClient(server):
        pass
    accept = _MockMCP.seen_headers[0].get("Accept", "")
    assert "application/json" in accept and "text/event-stream" in accept
