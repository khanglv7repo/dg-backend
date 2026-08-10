.PHONY: install test api mcp-server execution-worker migrate lint verify-openapi celery-worker worker-tag-sync celery-beat

install:
	python -m pip install -e '.[dev,postgres,trino]'

test:
	pytest -q

api:
	uvicorn app.main:app --reload

mcp-server:
	MCP_ENABLED=true python -m app.mcp.backend_mcp_server

execution-worker:
	python -m app.workers.execution_worker

celery-worker:
	celery -A app.celery_app worker -Q default -c 4 --loglevel=info

worker-tag-sync:
	celery -A app.celery_app worker -Q ranger.tag-sync -c 1 --loglevel=info

celery-beat:
	celery -A app.celery_app beat --loglevel=info

migrate:
	alembic upgrade head

lint:
	ruff check app tests scripts

verify-openapi:
	python scripts/verify_openmetadata_openapi.py $(SPEC)
