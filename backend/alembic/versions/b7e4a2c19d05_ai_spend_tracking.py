"""ai_spend table — persisted model cost, so it can be queried and capped

Revision ID: b7e4a2c19d05
Revises: a3d91c47f2b8
Create Date: 2026-08-02
"""
from alembic import op
import sqlalchemy as sa

revision = "b7e4a2c19d05"
down_revision = "a3d91c47f2b8"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # Guarded: create_tables.py may already have emitted this via create_all
    # on a fresh database before alembic stamps it.
    if _has_table("ai_spend"):
        return
    op.create_table(
        "ai_spend",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(),
                  sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.Integer(),
                  sa.ForeignKey("employees.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent_id", sa.String(100), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default="0"),
        sa.Column("output_tokens", sa.Integer(), server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer(), server_default="0"),
        sa.Column("cost_usd", sa.Float(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_spend_company_id", "ai_spend", ["company_id"])
    op.create_index("ix_ai_spend_created_at", "ai_spend", ["created_at"])
    op.create_index("ix_spend_company_created", "ai_spend", ["company_id", "created_at"])
    op.create_index("ix_spend_employee_created", "ai_spend", ["employee_id", "created_at"])


def downgrade() -> None:
    if _has_table("ai_spend"):
        op.drop_table("ai_spend")
