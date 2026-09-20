"""Standalone Media OS foundation and guarded runtime permissions."""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    root = Path(__file__).parent
    for name in ("0001_schema.sql", "0001_guards.sql"):
        with op.get_bind().connection.driver_connection.cursor() as cursor:
            cursor.execute((root / name).read_text(encoding="utf-8-sig"))


def downgrade():
    raise RuntimeError(
        "Destructive downgrade disabled. Restore a backup or recreate a disposable database."
    )
