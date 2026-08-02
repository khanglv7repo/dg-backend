# Operations

## Deployables

One source package/image may run three commands:

```bash
uvicorn app.main:app
python -m app.workers.execution_worker
python -m app.workers.agent_worker
```

Agent Worker is omitted in Phase 1.

## Environment profiles

Create separate secret sets and deployment service accounts for API, Execution Worker, and Agent Worker. The Execution Worker receives `OM_INGESTION_BOT_TOKEN`, `OM_AUTOCLASSIFICATION_BOT_TOKEN`, and `OM_AUTO_TAG_BOT_TOKEN`; do not inject a superset of all secrets into one pod/container.

The Docker metadata-ingestion sidecar is deployed from `governance_app/docker-compose.ingestion.yml` onto the existing lab network. It receives `OM_INGESTION_BOT_TOKEN` and source-database credentials only; `OPENMETADATA_ADMIN_TOKEN` must not be injected.

## Safety defaults

- `AGENT_ENABLED=false`;
- `RANGER_DRY_RUN=true`;
- `TRUSTED_AUTO_APPLY_ENABLED=false`;
- MCP read-only tool allow-list;
- distinct Bot names.

## Metrics

Track:

- queue depth by job type/status;
- job latency/retry/dead counts;
- deterministic outcome/action distribution;
- Agent duration, model/graph version, no-suggestion rate;
- MCP errors and tool calls;
- native Suggestion acceptance/rejection rate;
- Ranger action/drift counts;
- Trino verification pass/fail rate.

## Secret rotation

Rotate Agent Bot, Ingestion Bot, Auto-classification Bot, Auto-tag Bot, Ranger, Trino, and LLM credentials independently. Bot identity names should remain stable for audit while tokens rotate.

## Recovery

- Stop only the affected worker role.
- Correct credentials/configuration.
- Retry dead jobs through authorized API.
- Run reconciliation to restore desired state.
