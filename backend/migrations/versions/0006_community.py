"""Bounded internal community reply review; no outbound communication."""

from pathlib import Path

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute((Path(__file__).parent / "0006_community.sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError(
        "Restore a backup or recreate a disposable database; destructive downgrade disabled"
    )
