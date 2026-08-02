"""login_attempts — durable, account-aware brute-force throttling

Revision ID: c1f8b3d7e920
Revises: b7e4a2c19d05
Create Date: 2026-08-02
"""
from alembic import op
import sqlalchemy as sa

revision = "c1f8b3d7e920"
down_revision = "b7e4a2c19d05"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("login_attempts"):
        return
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("identifier", sa.String(200), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_login_attempts_identifier", "login_attempts", ["identifier"])
    op.create_index("ix_login_attempts_ip_address", "login_attempts", ["ip_address"])
    op.create_index("ix_login_attempts_created_at", "login_attempts", ["created_at"])
    op.create_index("ix_login_attempt_identifier_created", "login_attempts",
                    ["identifier", "created_at"])
    op.create_index("ix_login_attempt_ip_created", "login_attempts",
                    ["ip_address", "created_at"])


def downgrade() -> None:
    if _has_table("login_attempts"):
        op.drop_table("login_attempts")
