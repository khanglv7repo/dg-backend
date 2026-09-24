# Coding-Agent Instructions for `backend/`

Read `../docs/00_README.md`, `../docs/DG_FINAL_SPEC.md`, `../docs/01_REQUIREMENTS.md`, `../docs/03_BACKEND_MVC_DESIGN.md`, and the relevant `../planning/tasks/` file first.

`../context/` was removed because it was stale. Do not rely on it or recreate it.

## Binding architecture (Simple MVC)

- Backend FastAPI application under `governance_app/`.
- Simple MVC structure:
  - `controllers/`: HTTP, Webhook, and FastMCP entry points (thin request parsing, DTO mapping).
  - `services/`: Deterministic business logic (Policy, Compiler, Reconcile, DQ, Classification, Audit).
  - `models/`: Domain entities and SQLAlchemy persistence models.
  - `repositories/`: PostgreSQL data access (only for Backend-owned state).
  - `integrations/`: Isolated client adapters for OpenMetadata, Ranger, and Trino.
  - `jobs/`: Celery tasks and background reconciliation/retry workers.
- PostgreSQL owns governance intent: Logical Policies, Policy versions, Approval state, Desired State, Service Mappings, DQ Registry, Transactional Outbox + Inbox, and Audit.
- OpenMetadata 2.0.2 is the authoritative system of record for Metadata, Tags, Taxonomy, Lineage, and DQ TestDefinitions/TestCases/TestResults.
- Apache Ranger is the runtime enforcement target (never a business SoT).
- Trino provides behavioral runtime verification for Ranger access policies.
- Backend FastMCP exposes only the Agent-facing bounded contract in `docs/DG_FINAL_SPEC.md` section 7.2: `get_policy_context(asset_fqn?)`, `get_dq_requirement_spec(control_id)`, and `submit_policy_draft(draft)`.
- AI Agent communicates via FastMCP/REST and only proposes drafts; it does not directly activate policies or bypass human approval.

## Dependency rules

- Controllers delegate to services.
- Services own deterministic business decisions (no LLM in compiler or reconciliation).
- Repositories perform persistence only (no business logic in repositories).
- Integration adapters wrap external systems only.
- Agent code must not import Ranger or write to Backend DB.
- All Python imports MUST be placed at the top header of code files; inline or deferred imports inside functions or code blocks are strictly forbidden.

## Invariants & Consistency

- Desired State Reconciliation is level-triggered: `Desired -> Apply -> Read-Back -> Compare -> Verify`.
- Webhook events trigger fast reaction through the Inbox Pattern; periodic reconciliation guarantees eventual consistency.
- Policy versions are immutable; updates create new versions. Rollbacks create an explicit target version rather than a silent walkback.
- High/Critical policies must undergo Trino behavioral verification.
