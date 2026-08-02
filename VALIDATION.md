# Validation Report — v0.4

Validation date: 2026-07-29.

## Scope validated

- one FastAPI application imports and starts;
- Execution Worker and Agent Worker source compiles;
- MVC/service/repository code paths compile;
- deterministic classification and Agent fallback decisions;
- distinct Bot identity configuration;
- worker-role job claim isolation;
- OpenMetadata Suggestion and targeted column adapter contracts with mocks;
- read-only MCP tool filtering and Bot bearer token with mocks;
- Ranger dry-run behavior with mocks;
- Trino grouped verification with a fake executor;
- Alembic clean upgrade/downgrade/upgrade;
- metadata event API and deterministic worker smoke;
- uploaded OpenMetadata 1.13 OpenAPI capabilities used by this project.

## Results

### Python compilation

```text
python -m compileall -q app tests alembic scripts
PASS
```

### Tests

```text
pytest -q
32 passed
```

The suite includes:

- classification rule exact/ambiguity tests;
- trusted/reviewed/Agent-fallback action tests;
- raw OpenMetadata ChangeEvent adapter and authentication tests;
- watermark asset discovery tests;
- bounded sample-value scanner metrics and suggestion creation tests;
- zero raw sample value persistence/logging assertions;
- Ranger policy disable and delete tag-removal reconciliation tests;
- Agent output allow-list and Bot audit identity tests;
- OpenMetadata REST contract tests;
- MCP read-only and authentication tests;
- Ranger dry-run and policy normalization tests;
- policy mapping tests;
- job idempotency and worker-role claim tests;
- grouped Trino verification tests;
- distinct machine Bot configuration tests.

### Migration round-trip

```text
alembic upgrade head
alembic downgrade base
alembic upgrade head
PASS on a clean SQLite database
```

Migration head: `0005_phase1_completion`.

### API and worker smoke

- `GET /api/v1/health/live` returned 200.
- `GET /api/v1/health/ready` returned 200.
- `POST /api/v1/events/metadata` returned 202.
- Accepted job was retrievable.
- Execution Worker processing changed `CLASSIFY_ASSET` to `SUCCEEDED`.
- The classification step queued `CREATE_OM_SUGGESTIONS`.

### OpenMetadata OpenAPI capability check

```text
python scripts/verify_openmetadata_openapi.py /mnt/data/openapi-spec.json
OpenMetadata OpenAPI capability verification passed (version=1.13)
```

Verified presence of Suggestions, accept/reject, column-by-FQN, Events subscriptions, and App configure/schedule/trigger/stop contracts used by the architecture.

### Editable package install

```text
python -m pip install -e . --no-deps --no-build-isolation
PASS
```

### Config parsing

```text
classification_version=505dfa1d09e44e68 rules=6
policy_version=c2ac38a87e9fcb0f mappings=4
```

## Not validated live

No live credentials/endpoints were supplied. The following are intentionally not claimed as validated:

- actual OpenMetadata Bot permissions and token behavior;
- live MCP transport/tool argument contract;
- official AI SDK package compatibility;
- LangGraph/provider execution with a real LLM;
- Ranger service resource hierarchy, access types, masking, row filters, or mutation permission;
- Ranger-to-Trino propagation delay;
- Trino impersonation/identity propagation and production deny behavior.

Agent runtime extras were not installed in this build environment. Agent modules compile and their clients/contracts are unit-tested, but a real LangGraph/LLM run remains a deployment capability check.

## Tooling limitation

`ruff` was not installed in the build container, so lint execution was not performed. Python compilation and tests passed.
