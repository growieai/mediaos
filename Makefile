.PHONY: up down demo test api console

up:
	docker compose up -d postgres redis minio temporal temporal-ui

down:
	docker compose down

api:
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

demo:
	cd backend && python -m app.workflows.sofia_demo

test:
	cd backend && python -m pytest -q

console:
	cd apps/console && npm run dev
