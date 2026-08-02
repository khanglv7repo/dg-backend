# System Architecture

## Context diagram

```text
+-----------------------------------------------------------------------+
| OpenMetadata                                                          |
| assets, tags, Suggestions, reviewers, history, Apps, MCP              |
+-------------------+-------------------------------+-------------------+
                    | REST / events                 | MCP / REST
                    v                               v
+------------------------------------+   +------------------------------+
| Governance Backend                 |   | Governance Agent             |
| (governance_app/)                  |   | (governance_agent/)          |
|                                    |   |                              |
| FastAPI Control API                |   | LangGraph Agent + LLM        |
| PostgreSQL governance_jobs         |   | OpenMetadata MCP read Bot    |
| Execution Worker (Ranger / Trino)  |   | Directly creates Suggestions |
+-------------------+----------------+   +--------------+---------------+
                    |                                   |
                    v                                   v
              Apache Ranger                        LLM Provider
                    |
                    v
                  Trino
```

## Application architecture

The repository is structured into two separate project directories:

1. **`governance_app/` (Backend Control & Execution)**
   - **Controller**: FastAPI route functions under `app/api/routes`.
   - **Model**: SQLAlchemy models and Pydantic schemas.
   - **Service**: Business rules, deterministic classification, policy sync, verification.
   - **Repository**: SQLAlchemy persistence for jobs & audit runs.
   - **Execution Worker**: ranger reconciliation & Trino verification.

2. **`governance_agent/` (Standalone AI Agent Project)**
   - Standalone project directory with its own `pyproject.toml`.
   - **MCP Client**: Read-only OpenMetadata MCP connection.
   - **LangGraph**: Classification node graph for metadata sensitivity.
   - **OpenMetadata Client**: Creates native Suggestions directly in OpenMetadata using Agent Bot token.

## Project Separation Rationale

Agent and Execution are decoupled into separate project directories to ensure:
- Clear separation of concerns and dependency isolation (LLM & LangGraph dependencies stay in `governance_agent`);
- Independent deployment, scaling, and execution life-cycles;
- Direct connection from Agent to OpenMetadata without cluttering the core backend;
- Strict security credential isolation (Agent Bot token vs Execution Bot token).
