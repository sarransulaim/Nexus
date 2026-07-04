"""Coordination layer: project digest, dependency-drift alerts, and the contract
primitive (define + drift detection)."""
import project_digest as pd
import api.claude_orchestrator as co
from database.core import SessionLocal
from database.models import Project, Task, Contract, Notification


def _wipe_drift():
    s = SessionLocal()
    try:
        s.query(Notification).filter(
            Notification.type.in_(["dependency_drift", "contract_drift"])
        ).delete()
        s.query(Contract).filter(Contract.name == "_pytest_contract").delete()
        s.commit()
    finally:
        s.close()


def test_digest_builds_for_a_seeded_project():
    s = SessionLocal()
    try:
        produced = False
        for p in s.query(Project).all():
            d = pd.build_project_digest(p, s)
            if d:
                produced = True
                assert "Daily digest" in d
        assert produced, "no seeded project produced a digest"
    finally:
        s.close()


def test_dependency_drift_alerts_fire_and_dedup():
    _wipe_drift()
    try:
        res = pd.run_drift_alerts()
        assert res["alerts"] >= 1, "expected drift from the overdue dependency chain"
        # second run must not re-spam the same alerts
        res2 = pd.run_drift_alerts()
        assert res2["alerts"] == 0
    finally:
        _wipe_drift()


def test_contract_define_view_and_drift():
    _wipe_drift()
    # find two tasks in one project with DIFFERENT owners and a non-completed consumer
    s = SessionLocal()
    try:
        proj = s.query(Project).first()
        tasks = s.query(Task).filter(Task.project_id == proj.id, Task.owner_id.isnot(None)).all()
        pid = cid = None
        for a in tasks:
            for b in tasks:
                if a.id != b.id and a.owner_id != b.owner_id and not b.is_completed:
                    pid, cid = a.id, b.id
                    break
            if pid:
                break
        assert pid and cid, "need two tasks with different owners"
    finally:
        s.close()

    # Start the producer from a KNOWN clean description and restore it after —
    # otherwise each run's appended edits pollute the next run's baseline.
    s = SessionLocal()
    try:
        t = s.query(Task).filter(Task.id == pid).first()
        original_desc = t.description
        t.description = "Deliver the payments endpoint returning a JSON payload."
        s.commit()
    finally:
        s.close()

    try:
        # define via the real orchestrator tool handler
        out = co.execute_tool("define_contract", {
            "producer_task_id": pid, "consumer_task_id": cid,
            "name": "_pytest_contract",
            "description": "producer hands the consumer a JSON payload over the agreed endpoint",
        }, "Manager_1")
        assert "recorded" in out.lower()

        # view_contracts returns it
        view = co.execute_tool("view_contracts", {"task_id": pid}, "Manager_1")
        assert "_pytest_contract" in view

        # no drift until the producer changes
        pd.run_drift_alerts()
        s = SessionLocal()
        try:
            assert s.query(Notification).filter(Notification.type == "contract_drift").count() == 0
        finally:
            s.close()

        # DRIFT V2: a BENIGN edit (rewording, no interface change) must NOT alert —
        # the semantic assessor re-baselines it silently
        s = SessionLocal()
        try:
            t = s.query(Task).filter(Task.id == pid).first()
            t.description = (t.description or "") + " (clarified the wording of this task; deliverable unchanged)"
            s.commit()
        finally:
            s.close()
        pd.run_drift_alerts()
        s = SessionLocal()
        try:
            assert s.query(Notification).filter(Notification.type == "contract_drift").count() == 0, \
                "benign edit wrongly flagged as drift"
            con = s.query(Contract).filter(Contract.name == "_pytest_contract").first()
            assert con.status == "active"
            assert con.baseline_snapshot and "clarified the wording" in con.baseline_snapshot  # rebaselined
        finally:
            s.close()

        # a BREAKING change → consumer alerted, contract at_risk
        s = SessionLocal()
        try:
            t = s.query(Task).filter(Task.id == pid).first()
            t.description = (t.description or "") + \
                " BREAKING: the deliverable now returns XML instead of JSON and requires a new auth header."
            s.commit()
        finally:
            s.close()
        pd.run_drift_alerts()
        s = SessionLocal()
        try:
            alerts = s.query(Notification).filter(Notification.type == "contract_drift").count()
            con = s.query(Contract).filter(Contract.name == "_pytest_contract").first()
            assert alerts >= 1
            assert con.status == "at_risk"
        finally:
            s.close()
    finally:
        _wipe_drift()
        s = SessionLocal()
        try:
            t = s.query(Task).filter(Task.id == pid).first()
            t.description = original_desc
            s.commit()
        finally:
            s.close()
