.PHONY: install test api execution-worker migrate lint verify-openapi

install:
	python -m pip install -e '.[dev,postgres,trino]'

test:
	pytest -q

api:
	uvicorn app.main:app --reload

execution-worker:
	python -m app.workers.execution_worker

migrate:
	alembic upgrade head

lint:
	ruff check app tests scripts

verify-openapi:
	python scripts/verify_openmetadata_openapi.py $(SPEC)
