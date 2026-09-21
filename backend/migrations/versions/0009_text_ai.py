"""Opt-in bounded structured text selection with immutable paid attempts."""

from pathlib import Path

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute((Path(__file__).parent / "0009_text_ai.sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError(
        "Restore a backup or recreate a disposable database; destructive downgrade disabled"
    )
