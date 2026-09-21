"""Verified platform comments and separately authorized exact reply dispatch."""

from pathlib import Path

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(
            (Path(__file__).parent / "0011_social_community.sql").read_text(encoding="utf-8")
        )


def downgrade():
    raise RuntimeError("Restore a backup; destructive downgrade disabled")
