# Start Here — Context Routing

## Stable system summary

- OpenMetadata is the metadata and confirmed-tag source of truth.
- `governance_app/` is the FastAPI Control API plus Execution Worker.
- PostgreSQL `governance_jobs` is the durable work queue.
- PostgreSQL `governance_policies` is the runtime desired-state store for Ranger policies.
- Native Ranger JSON is the policy import contract.
- Apache Ranger is the enforcement target.
- Trino is governed by Ranger, but backend-side Trino verification is not part of the production control plane.
- Policy sync is explicit; application startup does not seed YAML or auto-sync Ranger.
- Future AI/MCP integrations are thin adapters over application services and do not receive Ranger credentials.
- Missing identity headers grant no roles.

## Read-by-task map

| Task | Required context |
|---|---|
| Classification | `01`, `04`, `06`, `08`, `20`, `22` |
| OpenMetadata tag/Suggestion writes | `04`, `06`, `08`, `20` |
| Ranger policy catalog/reconciliation | `04`, `06`, `08`, `20`, `22` |
| Ranger tag assignments | `04`, `06`, `08`, `20` |
| Jobs/retry/worker | `06`, `08`, `20`, `22` |
| Authentication/identity | `06`, `20` |
| Future MCP | `06`, `08`, `20` |
| Major architecture change | read all relevant docs and update ADR + `22_ACTIVE_TASK.md` |

## Stop conditions

Do not introduce a change that:

- makes YAML/files the runtime policy source of truth;
- lets HTTP/MCP/AI mutate Ranger directly;
- overwrites unmanaged Ranger policies;
- treats absence of a DB policy row as authorization to delete Ranger state;
- grants admin/operator roles when identity headers are missing;
- adds production Trino verification back into the core control plane without a new ADR;
- invents a second policy DSL when native Ranger JSON is sufficient.
