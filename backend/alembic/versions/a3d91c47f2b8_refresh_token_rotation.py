"""refresh token rotation columns

Adds the two columns /auth/refresh needs to rotate refresh tokens and to spot
a stolen one being replayed:

  refresh_token_prev       — bcrypt hash of the token we just replaced
  refresh_token_rotated_at — when that replacement happened

Both nullable, so existing sessions keep working: a row with NULLs simply has
no previous token to compare against and rotates normally on its next refresh.

Revision ID: a3d91c47f2b8
Revises: 97c0ea18f9e2
Create Date: 2026-08-01
"""
from alembic import op
import sqlalchemy as sa

revision = "a3d91c47f2b8"
down_revision = "97c0ea18f9e2"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    # Guarded because create_tables.py may already have emitted these via
    # create_all on a fresh database before alembic stamps it.
    if not _has_column("employees", "refresh_token_prev"):
        op.add_column("employees", sa.Column("refresh_token_prev", sa.String(512), nullable=True))
    if not _has_column("employees", "refresh_token_rotated_at"):
        op.add_column("employees", sa.Column("refresh_token_rotated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if _has_column("employees", "refresh_token_rotated_at"):
        op.drop_column("employees", "refresh_token_rotated_at")
    if _has_column("employees", "refresh_token_prev"):
        op.drop_column("employees", "refresh_token_prev")
