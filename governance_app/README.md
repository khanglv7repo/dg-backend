Backend FastAPI application using **MVC + Service + Repository** with an Execution Worker role:

- **FastAPI Control API** — webhook event intake, API endpoints, classification triggers.
- **Execution Worker** — deterministic classification actions, OpenMetadata REST mutations, Ranger reconciliation, and Trino verification.

The AI Agent is in a separate project directory: `../governance_agent/`.

## Runtime model

```text
OpenMetadata REST / Events ---------> FastAPI Control API
                                          |
                                          v
                               PostgreSQL governance_jobs
                                          |
                                          v
                                 Execution Worker
                                 OM REST Bot / Ranger / Trino
```

## Identity rule

No runtime component uses a personal human account.

- `OM_INGESTION_BOT_TOKEN`: OpenMetadata Bot token for catalog ingest/discovery reads.
- `OM_AUTOCLASSIFICATION_BOT_TOKEN`: OpenMetadata Bot token for classification metadata reads.
- `OM_AUTO_TAG_BOT_TOKEN`: OpenMetadata Bot token for native Suggestions, trusted classification tag writes, and read-back.
- Ranger service account: Execution Worker only.
- Trino verification service identity: Execution Worker only.
- Human personal accounts: native OpenMetadata Suggestion review only.

## Start

### Using Conda (`dg_backend`)

```bash
# 1. Activate conda environment and navigate to project
conda activate dg_backend
cd governance_app

# 2. Upgrade database schema
alembic upgrade head

# 3. Run FastAPI Backend Control API (Port 8000)
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Running Execution Worker (Separate Terminal)

```bash
conda activate dg_backend
cd governance_app
python -m app.workers.execution_worker
```

Set all three Bot tokens in `governance_app/.env` before enabling OpenMetadata. They
must belong to distinct machine identities; no personal OpenMetadata token is accepted.

### Docker metadata ingestion for the existing lab

The old running lab used an admin JWT and an admin-login fallback for the
`metadata-ingestion` container. Replace only that container with the current
Bot-only sidecar; do not copy the old `.env` or its token:

```bash
cd governance_app
docker compose --env-file .env -f docker-compose.ingestion.yml up -d --force-recreate
```

This sidecar joins `OM_DOCKER_NETWORK` (default
`data-governance-lab_governance_net`), connects to `openmetadata` and `postgres`
by their existing Docker network names, and uses only `OM_INGESTION_BOT_TOKEN`.
It needs `FINANCIAL_DB`, `POSTGRES_SUPERUSER`, and `POSTGRES_SUPERUSER_PASSWORD`
from the local `.env`; `OPENMETADATA_ADMIN_TOKEN` is intentionally not injected.

## Safety defaults

- Agent Worker disabled.
- Ranger dry-run enabled.
- Trusted direct tag application disabled.
- MCP tool allow-list is read-only.
- Agent results always become native OpenMetadata Suggestions.
- Agent Worker receives no Ranger, Trino, or OpenMetadata mutation credential.

## Tests

```bash
pytest -q
```

Read `../.context/00_START_HERE.md` before making significant changes.
