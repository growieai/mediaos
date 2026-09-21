"""Guarded speaking media preparation, spend reservation and exact human approval."""

from pathlib import Path

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute((Path(__file__).parent / "0007_media.sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError(
        "Restore a backup or recreate a disposable database; destructive downgrade disabled"
    )
