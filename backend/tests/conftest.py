"""
Shared pytest fixtures. These are INTEGRATION tests — they run against the real
local Postgres (seeded via seed_demo.py) and local Ollama. Run from backend/:

    venv\\Scripts\\python.exe -m pytest tests/ -q
"""
import os
import sys

# make the backend package importable when pytest runs from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import pytest
from fastapi.testclient import TestClient
from database.core import SessionLocal
from database.models import Employee
from api.security import create_access_token
import main


@pytest.fixture(scope="session")
def client():
    # No `with` → app lifespan/background schedulers don't start during tests.
    return TestClient(main.app)


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(scope="session")
def people():
    """A real manager + two distinct employees from the seeded company, with tokens."""
    s = SessionLocal()
    try:
        mgr  = s.query(Employee).filter(Employee.system_role == "manager").first()
        emp  = s.query(Employee).filter(Employee.system_role == "employee").first()
        emp2 = s.query(Employee).filter(Employee.system_role == "employee",
                                        Employee.id != emp.id).first()
        assert mgr and emp and emp2, "seed the demo data first (python seed_demo.py)"
        return {
            "mgr": mgr.id, "emp": emp.id, "emp2": emp2.id,
            "mgr_tok":  create_access_token(mgr.id,  mgr.system_role,  mgr.name),
            "emp_tok":  create_access_token(emp.id,  emp.system_role,  emp.name),
            "emp2_tok": create_access_token(emp2.id, emp2.system_role, emp2.name),
        }
    finally:
        s.close()


@pytest.fixture()
def mgr_headers(people):
    return {"Authorization": f"Bearer {people['mgr_tok']}"}


@pytest.fixture()
def emp_headers(people):
    return {"Authorization": f"Bearer {people['emp_tok']}"}
