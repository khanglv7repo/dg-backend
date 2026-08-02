# Coding-Agent Instructions

Read `.context/00_START_HERE.md` first and load only the documents routed for the task.

## Binding architecture

- Backend FastAPI application under `governance_app/`.
- AI Agent project in a separate directory under `governance_agent/`.
- MVC + Service + Repository for backend; LangGraph + MCP for standalone agent.
- One Backend Control API, one Execution Worker role, one standalone Agent service/runner connected directly to OpenMetadata.
- PostgreSQL `governance_jobs` is the durable queue for execution tasks.
- OpenMetadata is the metadata/review system of record.
- Native OpenMetadata Suggestions own human approval.
- Agent project uses read-only OpenMetadata MCP and an LLM provider, connecting directly to OpenMetadata.
- Execution Worker owns OpenMetadata REST mutations, Ranger, and Trino.
- All runtime identities are Bots/service accounts; personal human credentials are forbidden.

## Dependency rules

- Controllers delegate to services.
- Services own business decisions.
- Repositories perform persistence only.
- Clients wrap external systems only.
- Agent code must not import Ranger, Trino, or OpenMetadata REST mutation clients.
- Execution code must not require an LLM key.
- All Python imports MUST be placed at the top header of code files; inline or deferred imports inside functions or code blocks are strictly forbidden.

## Change discipline

For significant work, update:

1. relevant numbered context documents;
2. business rules/invariants/decision tables when semantics change;
3. tests;
4. `22_ACTIVE_TASK.md`;
5. ADRs when a boundary changes.

Never claim a live integration is validated without credentials and an executed capability check.
