# governance_app

FastAPI control API plus PostgreSQL-backed Execution Worker.

## Sources of truth

```text
metadata + confirmed tags -> OpenMetadata
desired Ranger policies   -> PostgreSQL governance_policies
durable work              -> PostgreSQL governance_jobs
enforcement               -> Apache Ranger
```

`config/policies.yaml` is no longer a runtime policy source and is removed by the v0.6.1 cleanup.

## Policy workflow

```text
native Ranger JSON
       |
       v
POST /api/v1/policies/import
       |
       v
governance_policies
       |
       v
POST /api/v1/policies/sync
       |
       v
SYNC_RANGER_POLICIES
       |
       v
Execution Worker -> Ranger
```

Import and sync are intentionally separate. HTTP routes do not mutate Ranger directly.

## Classification workflow

```text
POST /api/v1/classifications/run
       |
       v
CLASSIFY_ASSET_FROM_OM
       |
       v
read current OpenMetadata entity
       |
       v
ACTIVE PostgreSQL classification rule set
       |
       +--> native OpenMetadata Suggestion
       |
       +--> trusted direct tag write (only when explicitly enabled)
                    |
                    v
             SYNC_RANGER_TAGS
```

Tag assignment synchronization never generates access policies.

## Identity headers

Local development can use trusted identity headers, but omitted headers grant **no roles**.

Example admin call:

```bash
curl http://127.0.0.1:8000/api/v1/policies \
  -H 'X-Actor-Id: local-admin' \
  -H 'X-Actor-Name: Local Admin' \
  -H 'X-Actor-Roles: governance-admin'
```

If the API is exposed beyond a trusted local/proxy boundary, configure real authentication and set `TRUSTED_IDENTITY_HEADERS=false` until that boundary is in place.

## Start

```bash
conda activate dg_backend
alembic upgrade head
pytest -q

python -m uvicorn app.main:app \
  --reload \
  --host 127.0.0.1 \
  --port 8000
```

The API may auto-start the execution worker according to `AUTO_START_EXECUTION_WORKER`. Startup does not auto-sync Ranger policies.

## Trino

Trino remains the resource service/query engine protected by Ranger. The old backend-side Trino verification executor/job is removed; policy effectiveness testing belongs in explicit integration/E2E tests rather than the production control plane.
