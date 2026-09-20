"""Official-source intelligence, immutable lineage and fresh approval checks."""

from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("0002_intelligence.sql", "0002_guards.sql"):
        with op.get_bind().connection.driver_connection.cursor() as cursor:
            cursor.execute((Path(__file__).parent / name).read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError(
        "Restore a backup or recreate a disposable database; destructive downgrade disabled"
    )
