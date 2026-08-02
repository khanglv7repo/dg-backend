# Migration Notes: v0.3 to v0.4

## Deployment topology

Removed:

- sibling `agent_service/` FastAPI application;
- `/events/agent-classifications` endpoint;
- `X-Agent-Token` internal HTTP hand-off;
- Agent service URL/token configuration.

Added:

- `AGENT_CLASSIFY` durable job;
- `app.workers.agent_worker`;
- `app.workers.execution_worker`;
- shared Agent result service/repository hand-off.

## Credential names

Replace:

- `OPENMETADATA_TOKEN` with `OM_AUTOCLASSIFICATION_BOT_TOKEN`, `OM_AUTO_TAG_BOT_TOKEN`, and `OM_INGESTION_BOT_TOKEN`;
- `RANGER_USERNAME`/`RANGER_PASSWORD` with `RANGER_SERVICE_ACCOUNT`/`RANGER_SERVICE_SECRET`;
- `TRINO_USER` with `TRINO_VERIFICATION_SERVICE_USER`;
- Agent service MCP token with `OPENMETADATA_AGENT_BOT_TOKEN`.

Set distinct names:

- `OPENMETADATA_EXECUTION_BOT_NAME=governance-execution-bot`;
- `OPENMETADATA_AGENT_BOT_NAME=governance-agent-bot`.

The application rejects identical Bot names.

`OPENMETADATA_EXECUTION_BOT_TOKEN` is accepted as a temporary alias for
`OM_AUTOCLASSIFICATION_BOT_TOKEN`. New deployments must set the three explicit Bot
tokens; configuration rejects equal values.

## Database

Migration `0004_single_app` has no schema change. The existing `governance_jobs.job_type` string column supports `AGENT_CLASSIFY`.

## Commands

Old:

```bash
uvicorn agent_service.app.main:app --port 8010
python -m app.workers.runner
```

New:

```bash
uvicorn app.main:app
python -m app.workers.execution_worker
python -m app.workers.agent_worker  # Phase 2–3 only
```
