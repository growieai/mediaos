"""Safe stale-plan cancellation and current credentials for historical reads."""

from pathlib import Path

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(
            (Path(__file__).parent / "0015_social_reconnect.sql").read_text(encoding="utf-8")
        )


def downgrade():
    raise RuntimeError("Restore a backup; destructive downgrade disabled")
