"""Immutable descriptive comparison of exact platform insight snapshots."""

from pathlib import Path

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(
            (Path(__file__).parent / "0012_social_learning.sql").read_text(encoding="utf-8")
        )


def downgrade():
    raise RuntimeError("Restore a backup; destructive downgrade disabled")
