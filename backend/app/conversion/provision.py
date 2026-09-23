"""Privileged one-time provisioning; the web process never receives the migration identity."""

import argparse
import os
from uuid import UUID

from dotenv import load_dotenv
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import REPO_ROOT
from app.conversion.transport import signing_key, validate_endpoint


def main():
    parser = argparse.ArgumentParser(description="Provision an exact standalone handoff endpoint")
    parser.add_argument("--tenant", required=True, type=UUID)
    parser.add_argument("--destination", required=True, type=UUID)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--retention-days", type=int, default=7)
    parser.add_argument("--secret-env", default="CONVERSION_SHARED_SECRET")
    args = parser.parse_args()
    load_dotenv(REPO_ROOT / ".env")
    validate_endpoint(args.endpoint)
    secret = os.environ.get(args.secret_env, "")
    signing_key(SecretStr(secret))
    url = os.environ["MIGRATION_DATABASE_URL"]
    if make_url(url).username == "mediaos_runtime":
        raise ValueError("Separate migration identity is required")
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text(
                    "SELECT private.provision_conversion_transport(:tenant,:destination,:endpoint,:days,:secret)"
                ),
                {
                    "tenant": args.tenant,
                    "destination": args.destination,
                    "endpoint": args.endpoint,
                    "days": args.retention_days,
                    "secret": secret,
                },
            ).scalar_one()
        print(f"Provisioned transport: {result}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
