# Start Here — Context Routing

This repository uses task-scoped context engineering. Do not load every document for every task. Read the minimum set that contains the business rules, invariants, source map, contracts, and tests relevant to the next change.

## Stable system summary

- OpenMetadata is the metadata and governance system of record.
- The backend system is a FastAPI application under `governance_app/` (MVC + Service + Repository).
- The AI Agent system is a separate project under `governance_agent/` (LangGraph + MCP).
- Backend runs Control API & Execution Worker role; Agent runs standalone connected directly to OpenMetadata.
- PostgreSQL `governance_jobs` is the durable hand-off for execution tasks.
- OpenMetadata native Suggestions own human review.
- Apache Ranger owns enforcement.
- Trino is the effective-access verification surface.
- Runtime components use Bot/service identities, never personal human accounts.

## Read-by-task map

| Task | Required context |
|---|---|
| Change deterministic classification | `01`, `02`, `04`, `05`, `06`, `10`, `11`, `13`, `18` |
| Change Agent Worker / LangGraph | `01`, `02`, `04`, `05`, `06`, `08`, `09`, `10`, `11`, `15`, `16`, `17`, `18` |
| Change OpenMetadata Suggestions or tag writes | `03`, `04`, `06`, `10`, `13`, `14`, `15`, `16`, `18` |
| Change Ranger reconciliation | `04`, `05`, `06`, `10`, `11`, `12`, `13`, `15`, `17`, `18` |
| Change Trino verification | `04`, `06`, `10`, `12`, `13`, `15`, `16`, `17`, `18` |
| Change jobs/retry/worker behavior | `04`, `06`, `09`, `10`, `11`, `12`, `17`, `18`, `19` |
| Change authentication or credentials | `03`, `06`, `09`, `15`, `16`, `19`, `20` |
| Add API endpoint | `04`, `06`, `10`, `14`, `16`, `17`, `18` |
| Plan a phase or major feature | Read all documents, then create/update an ADR and `22_ACTIVE_TASK.md` |

## Required workflow for significant changes

1. Read the routed context.
2. State the affected business rule and invariant.
3. Identify controller, service, repository, integration client, worker, and tests affected.
4. Update contracts before or with code.
5. Run unit, contract, migration, and smoke validation as applicable.
6. Update `22_ACTIVE_TASK.md` and an ADR when a boundary changes.

## Stop conditions

Do not implement a change when:

- it duplicates a native OpenMetadata capability without an accepted ADR;
- it introduces a second FastAPI application without measured need;
- it gives the Agent Worker Ranger, Trino, or OpenMetadata mutation credentials;
- it uses a personal user account for runtime automation;
- it bypasses native OpenMetadata review for Agent output;
- a live external contract is unknown and the change would perform production mutation.
