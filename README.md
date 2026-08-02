# OpenMetadata-Native Governance Platform v0.4

This package implements the agreed simplified architecture:

- **`governance_app/`**: FastAPI backend Control API & Execution Worker (MVC + Service + Repository);
- **`governance_agent/`**: Standalone AI Agent project (LangGraph + MCP), connected directly to OpenMetadata;
- **PostgreSQL durable queue**: `governance_jobs` table for backend execution tasks;
- OpenMetadata native Suggestions and review;
- Separate machine Bot/service credentials for every runtime role.

```text
OpenMetadata REST/Events ----> Governance Backend (governance_app/)
                                  |
                           PostgreSQL jobs
                                  |
                                  v
                          Execution Worker
                          OM REST Bot / Ranger / Trino

OpenMetadata MCP / REST <----> Governance Agent (governance_agent/)
                               LLM + LangGraph Agent
```

## Package layout

- `.context/` — task-routed context engineering bundle: business, rules, invariants, decisions, source map, patterns, contracts, tests, and playbook.
- `governance_app/` — runnable FastAPI application, migrations, workers, configuration, and tests.
- `CONTEXT_BUNDLE.md` — concatenated English context for environments that prefer a single file.
- `VALIDATION.md` — build and test evidence plus live limitations.

## Runtime identities

- `governance-agent-bot`: read-only MCP Bot.
- `governance-execution-bot`: controlled REST mutation Bot.
- Ranger service identity: Execution Worker only.
- Trino verification service identity: Execution Worker only.
- Human personal accounts: native OpenMetadata review/administration only.

Start with `.context/00_START_HERE.md`.
