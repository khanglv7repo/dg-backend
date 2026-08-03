# Data Governance Backend

Current backend architecture (v0.6.1):

- **OpenMetadata** is the source of truth for metadata and confirmed tags.
- **PostgreSQL `governance_policies`** is the source of truth for desired Ranger policies.
- **PostgreSQL `governance_jobs`** is the durable execution queue.
- **Apache Ranger** is the enforcement target.
- **Trino remains the governed query engine**, but the backend no longer performs Trino verification jobs.
- Policy input uses **native Ranger policy JSON**. There is no backend-specific policy DSL.
- A future Governance MCP server should be a thin adapter over the same backend application services and must not receive Ranger credentials.

```text
Human / CLI / future MCP
          |
          v
   FastAPI Control API
      /          \
     v            v
classification  governance_policies
     |            |
     v            v
OpenMetadata  governance_jobs
     |            |
     |            v
     |       Execution Worker
     |            |
     +--> Ranger Tag Store
                  |
                  +--> Ranger Policies

Ranger -> Trino enforcement
```

## Core capabilities

1. **Classification**
   - `POST /api/v1/classifications/run`
   - Worker reads the current OpenMetadata entity.
   - `classification_rules.yaml` drives deterministic matching.
   - OpenMetadata owns tags and native Suggestions.

2. **Policy management**
   - `POST /api/v1/policies/import`
   - `GET /api/v1/policies`
   - `GET /api/v1/policies/{id}`
   - `DELETE /api/v1/policies/{id}` performs a soft disable.
   - `POST /api/v1/policies/sync` explicitly enqueues DB -> Ranger reconciliation.

## Important runtime rule

Backend startup does **not** seed policy YAML and does **not** automatically mutate or sync Ranger policies. Policy desired state enters through the policy API (or a future MCP/CLI adapter) and is synchronized explicitly.

## Development

```bash
conda activate dg_backend
cd governance_app
alembic upgrade head
pytest -q
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

See `.context/00_START_HERE.md` and `VALIDATION.md`.
