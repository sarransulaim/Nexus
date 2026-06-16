"""Auth hardening: protected endpoints reject no-token, work with a valid token,
and enforce identity (no impersonation)."""

UNAUTH = (401, 403)


def test_read_routers_reject_no_token(client):
    assert client.get("/api/v1/tasks/").status_code in UNAUTH
    assert client.get("/api/v1/employees/").status_code in UNAUTH
    assert client.get("/api/v1/meetings/").status_code in UNAUTH
    assert client.get("/api/v1/analytics/summary").status_code in UNAUTH


def test_read_routers_work_with_token(client, mgr_headers):
    assert client.get("/api/v1/tasks/", headers=mgr_headers).status_code == 200
    assert client.get("/api/v1/employees/", headers=mgr_headers).status_code == 200
    assert client.get("/api/v1/meetings/", headers=mgr_headers).status_code == 200


def test_command_requires_auth(client):
    r = client.post("/api/v1/manager/command",
                    json={"manager_id": "Manager_1", "command_text": "hi"})
    assert r.status_code in UNAUTH


def test_command_identity_derived_from_token(client, emp_headers):
    # Employee token + body claims Manager_1 → must run as the employee, not the manager.
    # "hi" short-circuits in the orchestrator (no Claude call).
    r = client.post("/api/v1/manager/command", headers=emp_headers,
                    json={"manager_id": "Manager_1", "command_text": "hi"})
    assert r.status_code == 200


def test_notifications_identity_enforced(client, emp_headers, people):
    assert client.get(f"/api/v1/notifications/{people['emp']}", headers=emp_headers).status_code == 200
    assert client.get(f"/api/v1/notifications/{people['mgr']}", headers=emp_headers).status_code == 403


def test_command_history_identity_enforced(client, emp_headers, mgr_headers, people):
    own = f"Employee_{people['emp']}"
    assert client.get(f"/api/v1/manager/command-history/{own}", headers=emp_headers).status_code == 200
    assert client.get("/api/v1/manager/command-history/Manager_1", headers=emp_headers).status_code == 403
    assert client.get("/api/v1/manager/command-history/Manager_1", headers=mgr_headers).status_code == 200


def test_admin_endpoints_manager_only(client, emp_headers):
    assert client.post("/api/v1/admin/run-proactive-scan-now").status_code in UNAUTH
    assert client.post("/api/v1/admin/run-proactive-scan-now", headers=emp_headers).status_code == 403


def test_websocket_token_gate(client, people):
    def ok(url):
        try:
            with client.websocket_connect(url):
                return True
        except Exception:
            return False
    emp, tok = people["emp"], people["emp_tok"]
    assert not ok(f"/api/v1/ws/{emp}")                       # no token → rejected
    assert ok(f"/api/v1/ws/{emp}?token={tok}")               # matching token → accepted
    assert not ok(f"/api/v1/ws/{people['mgr']}?token={tok}")  # wrong subject → rejected
