"""Immutable visual configurations, rendered manifests and guarded export approval."""

from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute((Path(__file__).parent / "0003_visuals.sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError(
        "Restore a backup or recreate a disposable database; destructive downgrade disabled"
    )
