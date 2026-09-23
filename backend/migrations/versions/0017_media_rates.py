"""Enforce current media rates and retain safe failed-provider correlation IDs."""

from pathlib import Path

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute((Path(__file__).parent / "0017_media_rates.sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a backup; destructive downgrade disabled")
