"""
Phase-1 security regression tests — data exposure.

Same style as Phase 0: each test ATTEMPTS the real exploit and asserts it
fails, with positive controls so nothing can pass vacuously.
"""
import pytest
from database.core import SessionLocal
from database.models import (
    Employee, UploadedFile, Notification, Task, TaskComment, Channel, ChannelMember,
)
from api.security import create_access_token, create_refresh_token, internal_token


def _pick(db, role="employee", exclude=None):
    q = db.query(Employee).filter(Employee.system_role == role, Employee.is_active == True)
    if exclude:
        q = q.filter(Employee.id != exclude)
    return q.first()


@pytest.fixture()
def actors():
    s = SessionLocal()
    try:
        mgr = _pick(s, "manager")
        emp = _pick(s, "employee")
        other = _pick(s, "employee", exclude=emp.id)
        assert mgr and emp and other
        return {
            "mgr_id": mgr.id, "emp_id": emp.id, "other_id": other.id,
            "mgr_tok": create_access_token(mgr.id, mgr.system_role, mgr.name),
            "emp_tok": create_access_token(emp.id, emp.system_role, emp.name),
        }
    finally:
        s.close()


# ── P1.1 Files: an employee must not read the manager's uploads ───
@pytest.fixture()
def mgr_file(actors):
    s = SessionLocal()
    try:
        f = UploadedFile(
            company_id=1, uploader_id=actors["mgr_id"],
            original_filename="P1TEST-confidential.pdf", stored_filename="p1test.pdf",
            file_path="/tmp/p1test.pdf", file_size=10, file_type="application/pdf",
            extracted_text="SALARY BANDS AND TERMINATION PLANS",
        )
        s.add(f); s.commit()
        fid = f.id
    finally:
        s.close()
    yield fid
    s = SessionLocal()
    s.query(UploadedFile).filter(UploadedFile.id == fid).delete()
    s.commit(); s.close()


def test_employee_cannot_open_managers_file(client, actors, mgr_file):
    r = client.get(f"/api/v1/files/{mgr_file}",
                   headers={"Authorization": f"Bearer {actors['emp_tok']}"})
    assert r.status_code == 404, "employee can still read the manager's uploaded document"


def test_manager_can_open_own_file(client, actors, mgr_file):
    r = client.get(f"/api/v1/files/{mgr_file}",
                   headers={"Authorization": f"Bearer {actors['mgr_tok']}"})
    assert r.status_code == 200


def test_employee_recent_files_excludes_managers_uploads(client, actors, mgr_file):
    r = client.get("/api/v1/files/recent",
                   headers={"Authorization": f"Bearer {actors['emp_tok']}"})
    assert r.status_code == 200
    assert not any(f["id"] == mgr_file for f in r.json()), \
        "manager's upload still listed to an employee"


def test_manager_recent_files_includes_own(client, actors, mgr_file):
    r = client.get("/api/v1/files/recent",
                   headers={"Authorization": f"Bearer {actors['mgr_tok']}"})
    assert any(f["id"] == mgr_file for f in r.json())


# ── P1.3 AI tools: ownership on comments + notifications ──────────
def test_employee_cannot_read_comments_on_foreign_task(actors):
    from api.claude_orchestrator import execute_tool
    s = SessionLocal()
    try:
        t = s.query(Task).filter(Task.owner_id == actors["other_id"]).first()
        if not t:
            pytest.skip("no task owned by the other employee")
        tid = t.id
    finally:
        s.close()
    out = execute_tool("view_task_comments", {"task_id": tid}, f"Employee_{actors['emp_id']}")
    assert "not authorized" in out.lower(), f"unexpected: {out[:120]}"


def test_employee_can_read_comments_on_own_task(actors):
    from api.claude_orchestrator import execute_tool
    s = SessionLocal()
    try:
        t = s.query(Task).filter(Task.owner_id == actors["emp_id"]).first()
        if not t:
            pytest.skip("no task owned by this employee")
        tid = t.id
    finally:
        s.close()
    out = execute_tool("view_task_comments", {"task_id": tid}, f"Employee_{actors['emp_id']}")
    assert "not authorized" not in out.lower()


def test_manager_can_read_any_task_comments(actors):
    from api.claude_orchestrator import execute_tool
    s = SessionLocal()
    try:
        t = s.query(Task).filter(Task.owner_id == actors["other_id"]).first()
        if not t:
            pytest.skip("no task to test with")
        tid = t.id
    finally:
        s.close()
    out = execute_tool("view_task_comments", {"task_id": tid}, "Manager_1")
    assert "not authorized" not in out.lower()


def test_employee_cannot_mark_someone_elses_notification_read(actors):
    from api.claude_orchestrator import execute_tool
    s = SessionLocal()
    try:
        n = Notification(company_id=1, recipient_id=actors["other_id"], type="test",
                         title="P1TEST", message="not yours", is_read=False)
        s.add(n); s.commit(); nid = n.id
    finally:
        s.close()
    try:
        out = execute_tool("mark_notification_read", {"notification_id": nid},
                           f"Employee_{actors['emp_id']}")
        assert "not found" in out.lower(), f"unexpected: {out[:120]}"
        s = SessionLocal()
        try:
            assert s.query(Notification).filter(Notification.id == nid).first().is_read is False, \
                "another user's notification was marked read"
        finally:
            s.close()
    finally:
        s = SessionLocal()
        s.query(Notification).filter(Notification.id == nid).delete()
        s.commit(); s.close()


# ── P1.5 /internal/sync must not be open ──────────────────────────
def test_internal_sync_requires_token(client):
    r = client.post("/api/v1/internal/sync")
    assert r.status_code == 401, "/internal/sync is still unauthenticated"


def test_internal_sync_accepts_the_internal_token(client):
    r = client.post("/api/v1/internal/sync",
                    headers={"X-Internal-Token": internal_token()})
    assert r.status_code == 200, "the Slack bot's own sync call broke"


# ── P1.7 admin stream: refresh token must not open it ─────────────
def test_admin_stream_rejects_refresh_token(client, actors):
    from starlette.websockets import WebSocketDisconnect
    refresh = create_refresh_token(actors["mgr_id"])
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/v1/admin/stream?token={refresh}") as ws:
            ws.receive_text()


# ── P1.2 search_knowledge ACL wiring ──────────────────────────────
def test_search_knowledge_filters_by_channel_membership():
    """The handler must scope message hits to channels the caller belongs to
    and files to ones they uploaded."""
    import inspect
    import api.claude_orchestrator as co
    src = inspect.getsource(co.execute_tool)
    assert "allowed_channels" in src, "no channel ACL in search_knowledge"
    assert "own_files_only" in src, "no file-ownership ACL in search_knowledge"
    assert "ChannelMember" in src, "membership never consulted"


def test_search_knowledge_excludes_foreign_channel_content(actors):
    """End-to-end: index a message in a channel the employee is NOT in, then
    confirm their search cannot retrieve it."""
    import rag
    if not rag.backend_available():
        pytest.skip("embedding backend unavailable")
    from api.claude_orchestrator import execute_tool
    s = SessionLocal()
    secret = "PHASE1SECRET the acquisition price is four hundred million dollars"
    try:
        ch = Channel(company_id=1, name="P1TEST-private", type="private", description="t")
        s.add(ch); s.commit()
        cid = ch.id
        # only the OTHER employee is a member
        s.add(ChannelMember(channel_id=cid, employee_id=actors["other_id"]))
        s.commit()
    finally:
        s.close()
    try:
        rag.index_content(1, "message", 999_999, secret, meta={"channel_id": cid})
        out = execute_tool("search_knowledge", {"query": "acquisition price"},
                           f"Employee_{actors['emp_id']}")
        assert "four hundred million" not in out.lower(), \
            "private-channel content leaked to a non-member via AI search"
    finally:
        s = SessionLocal()
        from database.models import KnowledgeEmbedding
        s.query(KnowledgeEmbedding).filter(
            KnowledgeEmbedding.source_type == "message",
            KnowledgeEmbedding.source_id == 999_999).delete()
        s.query(ChannelMember).filter(ChannelMember.channel_id == cid).delete()
        s.query(Channel).filter(Channel.id == cid).delete()
        s.commit(); s.close()
