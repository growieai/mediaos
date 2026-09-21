"""Preserve historical post/comment identity across account reconnections."""

from pathlib import Path

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(
            (Path(__file__).parent / "0014_social_comments.sql").read_text(encoding="utf-8")
        )


def downgrade():
    raise RuntimeError("Restore a backup; destructive downgrade disabled")
