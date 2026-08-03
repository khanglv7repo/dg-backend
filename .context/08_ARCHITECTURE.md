# System Architecture

```text
                    Human / CLI / future MCP
                               |
                               v
+------------------------------------------------------------------+
| Governance Backend                                               |
|                                                                  |
| FastAPI Control API                                              |
|   /classifications/run                                           |
|   /policies/import                                               |
|   /policies/sync                                                 |
|                                                                  |
| PostgreSQL                                                       |
|   governance_jobs       durable execution queue                  |
|   governance_policies   Ranger desired-state catalog             |
|                                                                  |
| Execution Worker                                                 |
|   OpenMetadata classification/tagging                            |
|   Ranger policy reconciliation                                   |
|   Ranger tag-assignment synchronization                          |
+----------------------+-------------------------+-----------------+
                       |                         |
                       v                         v
                 OpenMetadata               Apache Ranger
               metadata/tag truth          enforcement target
                                                 |
                                                 v
                                               Trino
```

## Core capability 1 — Classification

- API accepts target identity.
- Worker reads current metadata from OpenMetadata.
- `classification_rules.yaml` performs deterministic matching.
- Uncertain results use OpenMetadata Suggestions.
- Trusted deterministic rules may directly apply tags only under the explicit feature flag.
- Confirmed tags may be mirrored to Ranger's tag store.

## Core capability 2 — Policy management

- Native Ranger JSON enters through API/CLI/future MCP.
- `governance_policies` stores desired state.
- Import does not mutate Ranger.
- `/policies/sync` enqueues `SYNC_RANGER_POLICIES`.
- Worker compares DB desired state with Ranger observed state.
- Only backend-owned Ranger policies can be changed.

## Startup behavior

Startup starts the execution worker when configured. It does not seed policy YAML and does not automatically reconcile Ranger policies.

## Future MCP

Governance MCP is a thin adapter over the same application services used by REST. It may expose classification and policy-management commands. It does not implement Ranger logic and does not receive Ranger credentials.

OpenMetadata MCP remains appropriate for metadata discovery/lineage.
