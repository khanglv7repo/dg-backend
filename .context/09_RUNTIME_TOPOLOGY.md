# Runtime Topology

## Processes

### Control API

Command:

```bash
uvicorn app.main:app
```

Responsibilities:

- accept OpenMetadata and manual events;
- accept confirmed-tag events;
- expose jobs, classification runs, capabilities, health, and manual retry;
- enqueue durable jobs only.

Credentials:

- database;
- API authentication configuration.

It does not need Ranger, Trino, MCP, or LLM credentials.

### Execution Worker

Command:

```bash
python -m app.workers.execution_worker
```

Claims every supported job except `AGENT_CLASSIFY`.

Credentials:

- `OM_INGESTION_BOT_TOKEN` for catalog discovery/ingest reads;
- `OM_AUTOCLASSIFICATION_BOT_TOKEN` for classification metadata reads;
- `OM_AUTO_TAG_BOT_TOKEN` for native Suggestions, trusted tag writes, and read-back;
- Ranger service identity;
- Trino verification service identity;
- database.

### Upstream metadata-ingestion runtime

Command from `governance_app/`:

```bash
docker compose --env-file .env -f docker-compose.ingestion.yml up -d --force-recreate
```

This is the official OpenMetadata ingestion image connected to the existing lab
Docker network. It receives only `OM_INGESTION_BOT_TOKEN`, never an OpenMetadata
admin token or admin login credentials.

### Governance Agent Service

Directory: `governance_agent/`

Command:

```bash
python -m app.main
```

Runs as a standalone AI Agent service connecting directly to OpenMetadata.

Credentials:

- OpenMetadata Agent MCP / REST Bot token (`governance-agent-bot`);
- LLM provider machine credential (`LLM_API_KEY`).

Forbidden environment variables/network permissions:

- OpenMetadata mutation Bot token;
- Ranger secret;
- Trino privileged credential.

## Scaling

- API scales by HTTP request volume.
- Execution Workers scale by durable queue depth and external side-effect limits.
- Governance Agent scales independently by MCP/LLM latency and provider quota.
- PostgreSQL locking prevents duplicate claim on supported databases.
