"""Privileged, explicitly invoked maintenance. Never imported by the API."""

import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.config import REPO_ROOT


def main():
    load_dotenv(REPO_ROOT / ".env")
    action = sys.argv[1]
    if action == "migrate":
        command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
        password = os.environ["POSTGRES_RUNTIME_PASSWORD"]
        with create_engine(os.environ["MIGRATION_DATABASE_URL"], hide_parameters=True).begin() as c:
            driver = c.connection.driver_connection
            assert driver is not None
            with driver.cursor() as cursor:
                cursor.execute(
                    sql.SQL("ALTER ROLE mediaos_runtime PASSWORD {}").format(sql.Literal(password))
                )
        print("Migrations and runtime credential configured.")
    elif action == "create-test-db":
        target = make_url(os.environ["TEST_MIGRATION_DATABASE_URL"])
        if target.database != "mediaos_test":
            raise ValueError("Only the disposable mediaos_test database can be created")
        with create_engine(
            target.set(database="postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
        ).connect() as c:
            driver = c.connection.driver_connection
            assert driver is not None
            with driver.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", ("mediaos_test",))
                if not cursor.fetchone():
                    cursor.execute("CREATE DATABASE mediaos_test")
        print("Disposable test database ready.")
    else:
        raise ValueError("Unknown maintenance command")


if __name__ == "__main__":
    main()
