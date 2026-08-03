# System Architecture

## Context diagram

```text
                    Human / CLI / future AI + MCP
                               |
                               v
+------------------------------------------------------------------+
| Governance Backend                                               |
|                                                                  |
| FastAPI Control API                                              |
|   - POST /classifications/run                                    |
|   - POST /policies/import                                        |
|   - POST /policies/sync                                          |
|                                                                  |
| PostgreSQL                                                       |
|   - governance_jobs       durable execution queue                |
|   - governance_policies   Ranger desired-state catalog           |
|                                                                  |
| Execution Worker                                                 |
|   - hydrate metadata from OpenMetadata                           |
|   - deterministic classification / controlled tag write          |
|   - Ranger policy reconciliation                                 |
|   - confirmed-tag assignment synchronization                     |
+---------------------------+----------------------+---------------+
                            |                      |
                            v                      v
                     OpenMetadata             Apache Ranger
                  metadata + tag truth       enforcement target
```

## Application boundaries

The backend exposes two primary business capabilities:

1. **Classification**
   - target identity enters through REST/automation;
   - Execution Worker reads the current asset from OpenMetadata;
   - `classification_rules.yaml` drives deterministic exact/regex matching;
   - trusted matches may apply tags to OpenMetadata under the existing feature flag;
   - confirmed tags may be synchronized to Ranger's tag store as a technical integration concern.

2. **Policy management and reconciliation**
   - native Ranger JSON is imported into PostgreSQL;
   - `governance_policies` is desired state;
   - API/MCP/CLI can create or update desired state without Ranger credentials;
   - `SYNC_RANGER_POLICIES` performs DB -> Ranger reconciliation in the Execution Worker;
   - TAG and RESOURCE policies share the same native document persistence and reconciler, but target different configured Ranger services.

## Future MCP boundary

A Governance MCP server should remain a thin adapter over the same application services used by REST. It may expose tools such as policy import/list/disable/sync and classification run. It must not reimplement Ranger/OpenMetadata clients and must not receive Ranger credentials.

OpenMetadata's own MCP remains the preferred metadata discovery interface for AI agents. The Governance MCP exists for backend-owned commands and desired-state management.
