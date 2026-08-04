# Active Task

## Status

DB-backed Ranger policy control plane is active. Cleanup/hardening remains active as of 2026-08-03. OpenMetadata native Suggestion reliability hardening was completed on 2026-08-05.

## Completed OpenMetadata Suggestion reliability hardening

- Validate every candidate OpenMetadata tag FQN before a native Suggestion batch can write, so incomplete taxonomy cannot create a partial batch.
- Read the target entity once and skip tags already present on the same entity or column as `Suggested` or `Confirmed`.
- Preserve OpenMetadata 404 response details in backend errors.
- Regression coverage: focused client/service tests passed (`9 passed`).

## Validated before cleanup

- Alembic upgraded to `0006_governance_policy_catalog` on PostgreSQL.
- Test suite passed: `48 passed`.
- Local FastAPI startup succeeded.
- DB-backed policy sync reached local Apache Ranger with HTTP 200 updates for the migrated `dev_tag` policies.
- Ranger mutation was observed with `RANGER_DRY_RUN=false`.

## Cleanup scope

- remove policy YAML runtime/bootstrap code;
- remove startup policy auto-sync;
- remove legacy Trino verification runtime code/dependency/job;
- remove sample-value scanner/Trino sampling fallback from classification;
- require explicit actor role headers for policy read/write/sync;
- close OpenMetadata clients created by long-running worker handlers;
- reserve space for the Ranger ownership marker when truncating descriptions;
- align version/docs/capability reporting with v0.6.1.

## Intentionally retained

- existing historical Alembic migrations and DB audit tables;
- Trino as the Ranger-protected query engine;
- `RECONCILE_RANGER` compatibility job temporarily, for already-queued pre-v0.6 jobs;
- optional Agent/LLM code outside the core policy flow, pending a separate decision/cleanup.

## Validation after applying cleanup

```bash
alembic upgrade head
pytest -q
python -m compileall -q app tests
```

Then verify:

1. backend restart does not enqueue policy sync automatically;
2. `GET /api/v1/policies` without role headers returns 403;
3. explicit `POST /api/v1/policies/sync` queues a job;
4. repeated sync of converged state results in `NO_CHANGE`;
5. unmanaged same-name Ranger policies remain protected;
6. OM -> Ranger tag resource hierarchy is validated against the live `dev_trino` service definition.
