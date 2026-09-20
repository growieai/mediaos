#!/usr/bin/env bash
set -euo pipefail

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

echo "Next:"
echo "  1) docker compose up -d postgres redis minio temporal temporal-ui"
echo "  2) cd backend && python -m venv .venv && source .venv/bin/activate"
echo "  3) pip install -e '.[dev]'"
echo "  4) python -m app.workflows.sofia_demo"
echo "  5) uvicorn app.main:app --reload"
echo "  6) in another terminal: cd apps/console && npm install && npm run dev"
