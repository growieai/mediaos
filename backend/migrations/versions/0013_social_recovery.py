"""Account-wide social rate limits and guarded uncertain operation recovery."""

from pathlib import Path

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(
            (Path(__file__).parent / "0013_social_recovery.sql").read_text(encoding="utf-8")
        )


def downgrade():
    raise RuntimeError("Restore a backup; destructive downgrade disabled")
