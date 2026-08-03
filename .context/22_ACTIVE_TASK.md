# Active Task

## Status

Policy-control-plane refactor prepared on 2026-08-03.

## Change

Reduce the active backend to two primary capabilities while keeping future MCP/AI integration clean:

1. deterministic classification/tagging against current OpenMetadata metadata;
2. desired-state Ranger policy management and reconciliation.

## Prepared implementation

- add PostgreSQL `governance_policies` desired-state catalog;
- persist native Ranger policy JSON in JSON/JSONB instead of a custom policy DSL;
- infer/validate TAG vs RESOURCE policy from configured Ranger services/resources;
- accept native Ranger JSON through `POST /api/v1/policies/import`;
- list/get/soft-disable desired policies through the control API;
- enqueue `SYNC_RANGER_POLICIES` through `POST /api/v1/policies/sync`;
- reconcile DB policies to both `RANGER_TAG_SERVICE_NAME` and `RANGER_RESOURCE_SERVICE_NAME` in the Execution Worker;
- preserve `managed-by=dg-backend` ownership protection and dry-run default;
- seed the legacy YAML tag-policy catalog only when the DB catalog is empty;
- replace direct Ranger mutation in FastAPI startup with a durable policy-sync job;
- add `POST /api/v1/classifications/run`; caller supplies only target identity and the worker reads current OpenMetadata metadata before classification;
- keep confirmed OpenMetadata tag assignment sync separate from access-policy reconciliation;
- record ADR-014.

## Deliberately retained during incremental migration

- legacy YAML resolver/service class for compatibility tests and one-time seed;
- existing Trino verification modules/job type, but they are outside the new core policy/classification flow and can be removed in a follow-up cleanup after the new slice is validated;
- existing event ingestion routes for OpenMetadata webhook compatibility.

## Validation required after applying the patch

- run `alembic upgrade head`;
- run the full `governance_app` test suite in `conda activate dg_backend`;
- import the two Ranger-export examples (one `dev_tag`, one `dev_trino`) and confirm DB revisions/idempotent re-import;
- run policy sync first with `RANGER_DRY_RUN=true`;
- verify an unmanaged same-name Ranger policy is rejected rather than overwritten;
- then test `RANGER_DRY_RUN=false` against the local Ranger lab;
- trigger manual classification against a known OpenMetadata table and confirm the worker hydrates metadata from OM before applying classification rules.
