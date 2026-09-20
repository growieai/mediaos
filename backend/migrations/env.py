import os

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine

from app.config import REPO_ROOT

load_dotenv(REPO_ROOT / ".env")
url = os.environ["MIGRATION_DATABASE_URL"]
engine = create_engine(url, hide_parameters=True)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
