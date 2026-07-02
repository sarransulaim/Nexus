"""
create_tables.py — schema management via Alembic migrations (2026-07-02).

- Fresh database  → builds the whole schema at the newest migration.
- Existing database → applies only the pending migrations.
- Legacy database (created by the old create_all flow, no alembic_version
  table) → stamped at the baseline first, then upgraded. This assumes the
  legacy schema matches the baseline — true for every deployment to date
  (the old COLUMN_UPGRADES kept them current).

To change the schema from now on:
  1. Edit database/models.py
  2. venv\\Scripts\\python.exe -m alembic revision --autogenerate -m "what changed"
  3. Review the generated file in alembic/versions/  (autogenerate is a draft!)
  4. Run this script (or `alembic upgrade head`) everywhere the app runs.
Never hand-ALTER the database anymore.
"""
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import inspect
from alembic.config import Config
from alembic import command
from database.core import engine

BASELINE_REV = "1f73cc0a0608"

cfg = Config("alembic.ini")

insp = inspect(engine)
tables = insp.get_table_names()
if tables and "alembic_version" not in tables:
    print(f"Legacy database detected (no alembic_version) — stamping baseline {BASELINE_REV}...")
    command.stamp(cfg, BASELINE_REV)

print("Applying migrations (alembic upgrade head)...")
command.upgrade(cfg, "head")
print("✅ Schema is at the latest migration.")
