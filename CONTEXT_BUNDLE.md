# Context Bundle

<!-- BEGIN FILE: 00_START_HERE.md -->
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


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 01_PRODUCT_CONTEXT.md -->
# Product and Business Context

## Objective

Classify governed data assets, turn confirmed classifications into Apache Ranger policies, and verify effective access through Trino without building a second metadata-governance platform.

## Business problem

Metadata classification and access enforcement span multiple systems:

1. OpenMetadata knows the asset, columns, owners, glossary, lineage, and confirmed tags.
2. Classification logic decides whether metadata indicates sensitive data.
3. Human reviewers must approve uncertain or AI-generated classifications.
4. Ranger must enforce the desired policy.
5. Trino must prove the policy is effective for controlled identities.

A failure in any step can create a false sense of governance. The application therefore records compact cross-system evidence and retries side effects durably.

## Product boundary

The application is not a replacement for OpenMetadata. It adds only:

- deterministic exact/regex classification;
- optional MCP-grounded LangGraph classification fallback;
- native Suggestion creation;
- explicitly trusted direct tag application;
- Ranger desired-state reconciliation;
- Trino positive/negative verification;
- durable retry, idempotency, and audit evidence.

## Success criteria

- A repeated OpenMetadata event does not duplicate work or policies.
- Agent output cannot mutate governance state directly.
- Human review remains visible in OpenMetadata.
- Ranger changes are deterministic, owned, and reconcilable.
- Trino verification records pass/fail without persisting business rows.
- Runtime identities are machine Bots/service accounts, not employee accounts.
- The codebase remains understandable as an MVC application.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 02_DOMAIN_GLOSSARY.md -->
# Domain Glossary

| Term | Meaning in this project |
|---|---|
| Asset | OpenMetadata entity such as a table or dashboard data model. |
| Field | A column or other child field identified by a field path such as `columns.email`. |
| Classification | A governed OpenMetadata tag taxonomy. |
| Confirmed tag | A tag whose OpenMetadata `state` is `Confirmed`. |
| Suggested tag | A tag proposed through OpenMetadata Suggestions and not yet confirmed. |
| Deterministic rule | Exact or regex rule whose result is reproducible from normalized metadata. |
| Trusted auto-apply | Explicit deterministic exact rule allowed to write a confirmed tag under a global feature flag. |
| Agent fallback | Phase 2–3 LangGraph reasoning used for `NO_MATCH` or `AMBIGUOUS` deterministic outcomes. |
| Native Suggestion | OpenMetadata `SuggestTagLabel` object reviewed in OpenMetadata. |
| Desired policy | Canonical Ranger policy document produced from confirmed tags and YAML mappings. |
| Reconciliation | Compare desired and observed Ranger state, then choose dry-run/create/update/no-change/drift repair. |
| Verification case | Controlled Trino query, identity, and expected allow/deny result. |
| Bot | OpenMetadata machine identity represented as a special user for automation. |
| Service identity | Non-human Ranger or Trino credential used by a runtime component. |
| Control API | The single FastAPI HTTP application that accepts events and exposes job/run status. |
| Execution Worker | Worker allowed to call OpenMetadata REST mutations, Ranger, and Trino. |
| Agent Worker | Optional worker allowed to call read-only OpenMetadata MCP and an LLM provider. |
| Job | Durable unit in PostgreSQL `governance_jobs`. |
| Correlation ID | Identifier joining events, jobs, classification runs, reconciliations, and verification records. |
| Idempotency key | Deterministic key preventing duplicate logical work. |


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 03_ACTORS_AND_OWNERSHIP.md -->
# Actors, Identities, and Ownership

## Human actors

### Data steward

- Uses a personal OpenMetadata account.
- Accepts or rejects native Suggestions.
- Does not provide credentials to workers.

### Security administrator

- Approves policy mappings and production Ranger enablement.
- Uses a personal administrative account only for human administration.

### Platform operator

- Deploys the API and workers.
- Provisions Bots and service identities.
- Rotates secrets and investigates dead jobs.

### Developer

- Changes rules, application code, LangGraph, and tests.
- Must follow source boundaries and ADRs.

## Machine identities

### `governance-agent-bot`

Purpose: read-only OpenMetadata MCP access for Agent Worker.

Allowed:

- metadata search;
- semantic search;
- entity details;
- lineage;
- test-definition context when required.

Forbidden:

- `patch_entity` and all mutation tools;
- accepting Suggestions;
- writing confirmed tags;
- Ranger access;
- Trino privileged access.

### `governance-execution-bot`

Purpose: controlled OpenMetadata REST operations for Execution Worker.

Allowed:

- read asset/column metadata;
- create native tag Suggestions;
- perform explicitly trusted deterministic tag writes;
- read back postconditions.

It is not a human reviewer and must not accept its own Suggestions.

### Ranger service identity

Purpose: Ranger policy lookup and mutation by Execution Worker only.

### Trino verification service identities

Purpose: controlled allow/deny/masking/row-filter verification. These are test/service identities, not personal users.

## Ownership matrix

| Capability | OpenMetadata | Control API | Execution Worker | Agent Worker | Human |
|---|---:|---:|---:|---:|---:|
| Metadata truth | Owner | Read event | Read/write controlled | Read via MCP | View/edit by permission |
| Suggestion review | Owner | No | Create only | No | Accept/reject |
| Deterministic rules | No | Trigger only | Owner | No | Approve config change |
| LLM reasoning | Native optional | No | No | Owner | No |
| Ranger mutation | Metadata record only | No | Owner | Forbidden | Admin oversight |
| Trino verification | No | No | Owner | Forbidden | Review evidence |
| Durable jobs | No | Enqueue/query | Claim execution jobs | Claim agent jobs | Retry with role |


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 04_BUSINESS_RULES.md -->
# Business Rules

## Classification

BR-001. Deterministic rules always run before Agent fallback.

BR-002. Exact/regex results are reproducible from the normalized event and versioned YAML configuration.

BR-003. A result is ambiguous when the same target receives more than one distinct tag.

BR-004. Trusted auto-apply requires all conditions:

- deterministic `EXACT` outcome;
- every contributing rule has `auto_apply: true`;
- no ambiguity;
- `TRUSTED_AUTO_APPLY_ENABLED=true`.

BR-005. Agent fallback may run only when `AGENT_ENABLED=true` and the deterministic outcome is `NO_MATCH` or `AMBIGUOUS`.

BR-006. Agent output is constrained to the configured governed tag allow-list.

BR-007. Agent output never auto-applies. Non-empty Agent output always becomes native OpenMetadata Suggestions.

BR-008. Empty Agent output creates a completed classification run with no mutation job.

## OpenMetadata

BR-010. OpenMetadata owns Suggestion acceptance/rejection and reviewer experience.

BR-011. The application must not maintain a parallel proposal or approval state machine.

BR-012. Tag writes target the smallest entity possible, preferably column-by-FQN for column tags.

BR-013. Existing unrelated tags must be preserved.

BR-014. Every controlled direct write requires read-back verification.

## Ranger

BR-020. Ranger policies are generated only from confirmed OpenMetadata tags.

BR-021. One tag must resolve to zero or exactly one active mapping. More than one mapping is configuration error.

BR-022. A mapping may generate one policy per tagged column.

BR-023. The application mutates only policies it owns, identified by its management marker.

BR-024. Ranger dry-run is the default and must not make mutation requests.

BR-025. No matching enforcement mapping is a valid classified-but-unenforced outcome and must be audited.

## Trino

BR-030. Verification occurs only after a non-dry-run Ranger reconciliation that can affect policy state.

BR-031. Verification stores query fingerprints and outcomes, never business result rows.

BR-032. A verification group completes when all expected cases are recorded.

## Identity

BR-040. Runtime components must use Bot/service identities, never personal accounts.

BR-041. OpenMetadata Agent Bot and Execution Bot must be distinct.

BR-042. Agent Worker must not receive Ranger, Trino, or OpenMetadata mutation credentials.

BR-043. Execution Worker must not require an LLM provider key.

## Jobs

BR-050. Every logical work item has a deterministic idempotency key.

BR-051. Agent Worker claims only `AGENT_CLASSIFY`.

BR-052. Execution Worker excludes `AGENT_CLASSIFY`.

BR-053. Retryable failures use bounded exponential backoff; exhausted/non-retryable failures become `DEAD`.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 05_DECISION_TABLES.md -->
# Decision Tables

## Deterministic classification action

| Outcome | Agent enabled | Trusted exact + global flag | Action |
|---|---:|---:|---|
| `NO_MATCH` | No | N/A | `NONE` |
| `NO_MATCH` | Yes | N/A | `AGENT_FALLBACK` → `AGENT_CLASSIFY` |
| `AMBIGUOUS` | No | No | Native OpenMetadata Suggestion from deterministic evidence |
| `AMBIGUOUS` | Yes | No | `AGENT_FALLBACK` → `AGENT_CLASSIFY` |
| `EXACT` | Any | Yes | `AUTO_APPLY` |
| `EXACT` | Any | No | Native OpenMetadata Suggestion |

## Agent result

| Suggestions | Tags valid | Action |
|---:|---:|---|
| 0 | N/A | Record Agent run, no mutation |
| >0 | Yes | Enqueue `CREATE_OM_SUGGESTIONS` |
| >0 | No | Configuration failure; do not create Suggestion |

## Ranger reconciliation

| Existing owned policy | Desired equals observed | Dry-run | Action |
|---:|---:|---:|---|
| No | N/A | Yes | `DRY_RUN` |
| No | N/A | No | `CREATE` |
| Yes | Yes | Any | `NO_CHANGE` or dry-run report |
| Yes | No | Yes | `DRY_RUN` |
| Yes | No | No | `UPDATE` / `DRIFT_REPAIR` |
| Existing policy not owned | Any | No | Fail safely; do not overwrite |

## Worker claim ownership

| Job type | API | Execution Worker | Agent Worker |
|---|---:|---:|---:|
| `CLASSIFY_ASSET` | Enqueue | Claim | Never |
| `AGENT_CLASSIFY` | Indirect enqueue | Never | Claim |
| `CREATE_OM_SUGGESTIONS` | No | Claim | Never |
| `APPLY_CONFIRMED_TAGS` | No | Claim | Never |
| `RECONCILE_RANGER` | Confirmed-tag intake/enqueue | Claim | Never |
| `VERIFY_TRINO` | No | Claim | Never |


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 06_INVARIANTS.md -->
# Non-Negotiable Invariants

INV-001. OpenMetadata is the metadata and review system of record.

INV-002. The backend (`governance_app`) is the FastAPI application for Control API and Execution Worker. The AI Agent (`governance_agent`) is a separate standalone project connected directly to OpenMetadata.

INV-003. PostgreSQL `governance_jobs` is the only production work queue until an ADR changes this.

INV-004. No runtime component uses personal human credentials.

INV-005. Agent and Execution OpenMetadata Bot identities are different.

INV-006. Agent Worker has read-only MCP access and no Ranger/Trino/mutation credentials.

INV-007. Agent output cannot enter `APPLY_CONFIRMED_TAGS`, `RECONCILE_RANGER`, or `VERIFY_TRINO` directly.

INV-008. Agent output always requires native OpenMetadata review.

INV-009. Controllers contain no business decisions or SQLAlchemy queries.

INV-010. Repositories contain persistence logic only; they do not call external systems.

INV-011. External API payloads are normalized by clients/services before persistence.

INV-012. No raw business query result rows are stored.

INV-013. No custom proposal/approval tables or reviewer UI.

INV-014. Ranger dry-run is enabled by default.

INV-015. Trusted auto-apply is disabled by default.

INV-016. The legacy Ranger ownership marker `managed-by=dg-backend` is retained for compatibility unless a migration plan is accepted.

INV-017. All Python imports across all projects must be placed at the top header of code files; inline or deferred imports inside functions, methods, or code blocks are strictly forbidden.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 07_PHASES_AND_NON_GOALS.md -->
# Delivery Phases and Non-Goals

## Phase 1 — OpenMetadata-native deterministic governance

- Keep OpenMetadata close to upstream.
- Use native OpenMetadata AI features when they are already sufficient.
- Run deterministic exact/regex classification.
- Use native Suggestions except explicitly trusted exact rules.
- Reconcile Ranger and verify through Trino.
- Agent Worker is disabled.

## Phase 2 — Standalone Agent Service (`governance_agent/`)

- Enable AI Agent fallback for no-match/ambiguity.
- Run LangGraph classification flow inside standalone `governance_agent/` project.
- Retrieve context through read-only OpenMetadata MCP Bot (`governance-agent-bot`).
- Directly submit native OpenMetadata Suggestions using Agent Bot token.
- Support parallel development across multiple AI Coding Agents (multi-LLM adapters, state persistence).

## Phase 3 — Specialist Reasoning Nodes

Specialist graph nodes developed in parallel within `governance_agent/app/reasoning/`:

- `lineage_risk.py`: Lineage-aware sensitivity risk scoring;
- `conflict_detector.py`: Conflict detection with existing tags/glossary;
- `impact_analyzer.py`: Policy impact analysis on downstream assets;
- Specialist graph nodes/subgraphs;
- OpenMetadata Workflow Definitions integration when simple Suggestion review is insufficient.

The Phase 3 Agent still has no direct enforcement authority (human review via OpenMetadata Suggestions remains mandatory).

## Current non-goals

- Forking OpenMetadata for custom LLM orchestration;
- a second FastAPI Agent application;
- microservices;
- full Hexagonal/Clean Architecture;
- CQRS or event sourcing;
- generic repository framework;
- Kafka/RabbitMQ/Redis/Celery/n8n;
- custom approval UI/state machine;
- database-backed policy authoring platform;
- automatic Agent tag confirmation.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 08_ARCHITECTURE.md -->
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


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 09_RUNTIME_TOPOLOGY.md -->
# Runtime Topology

## Processes

### Control API

Command:

```bash
uvicorn app.main:app
```

Responsibilities:

- accept OpenMetadata and manual events;
- accept confirmed-tag events;
- expose jobs, classification runs, capabilities, health, and manual retry;
- enqueue durable jobs only.

Credentials:

- database;
- API authentication configuration.

It does not need Ranger, Trino, MCP, or LLM credentials.

### Execution Worker

Command:

```bash
python -m app.workers.execution_worker
```

Claims every supported job except `AGENT_CLASSIFY`.

Credentials:

- OpenMetadata Execution Bot token;
- Ranger service identity;
- Trino verification service identity;
- database.

### Governance Agent Service

Directory: `governance_agent/`

Command:

```bash
python -m app.main
```

Runs as a standalone AI Agent service connecting directly to OpenMetadata.

Credentials:

- OpenMetadata Agent MCP / REST Bot token (`governance-agent-bot`);
- LLM provider machine credential (`LLM_API_KEY`).

Forbidden environment variables/network permissions:

- OpenMetadata mutation Bot token;
- Ranger secret;
- Trino privileged credential.

## Scaling

- API scales by HTTP request volume.
- Execution Workers scale by durable queue depth and external side-effect limits.
- Governance Agent scales independently by MCP/LLM latency and provider quota.
- PostgreSQL locking prevents duplicate claim on supported databases.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 10_SOURCE_CODE_MAP.md -->
Repository root contains two main project directories: `governance_app/` (Backend) and `governance_agent/` (AI Agent).

## 1. Backend Project (`governance_app/`)

```text
governance_app/
├── main.py                     FastAPI composition and exception mapping
├── app/
│   ├── api/
│   │   ├── router.py           Controller registration
│   │   ├── dependencies.py     DB/settings/actor dependencies
│   │   └── routes/             MVC controllers
│   ├── core/                   Config, errors, logging, security
│   ├── db/                     Database declarative base & sessions
│   ├── models/                 SQLAlchemy models and enums
│   ├── schemas/                Request/response/domain DTOs
│   ├── repositories/           Persistence-only operations
│   ├── rules/                  Deterministic classification & policy mapping
│   ├── services/               Intake, classification, policy sync, verification
│   ├── clients/                OpenMetadata REST, Ranger REST, Trino clients
│   ├── jobs/                   Handlers, dispatcher, processor
│   └── workers/
│       ├── base.py             Worker claim loop
│       └── execution_worker.py Execution Worker entrypoint
└── tests/                      Unit and contract tests
```

## 2. Standalone Agent Project (`governance_agent/`)

```text
governance_agent/
├── pyproject.toml              Agent package configuration & dependencies
├── README.md                   Project overview
├── app/
│   ├── main.py                 Service entrypoint
│   ├── runner.py               GovernanceAgentRunner
│   ├── classifier.py           Structured LLM classifier
│   ├── graph.py                LangGraph classification flow
│   ├── schemas.py              Agent DTOs & decisions
│   └── clients/
│       ├── mcp.py              Read-only OpenMetadata MCP JSON-RPC client
│       └── openmetadata.py     REST client for submitting Suggestions
└── tests/                      Unit tests for Agent graph and MCP client
```

## Change locations

- Add HTTP endpoint: `governance_app/app/api/routes`, `schemas`, relevant `service`, tests.
- Change business decision: relevant `service`, decision tables, tests.
- Change database query/state: `repository` and model/migration if required.
- Change external payload: relevant `client`, contract tests, capability verification.
- Change LangGraph / Agent logic: `governance_agent/app/graph.py`, `governance_agent/app/classifier.py`, Agent tests.
- Change worker ownership: `models/enums.py`, `repositories/jobs.py`, workers, decision table, tests.

## Forbidden shortcuts

- Route directly calling `Session.query`/`select`.
- Repository calling HTTP/LLM/Trino.
- Client deciding approval or auto-apply.
- Agent code importing Ranger/Trino clients.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 11_DESIGN_PATTERNS.md -->
# Design Patterns

Use only patterns that solve a current problem.

## MVC + Service Layer

Controllers validate transport input and delegate. Services own business rules and orchestration. Pydantic response schemas are the JSON view.

## Repository Pattern

Concrete repositories hide SQLAlchemy details:

- `JobRepository`;
- `ClassificationRunRepository`;
- `AuditRepository`.

Do not create a generic `Repository[T]` framework.

## Strategy Pattern

Appropriate for classification algorithms/providers:

- deterministic exact/regex engine;
- structured LLM classifier;
- future specialist strategies.

The strategy returns data. It does not mutate OpenMetadata or Ranger.

## Adapter/Client Pattern

External systems are wrapped in narrow clients:

- OpenMetadata REST;
- OpenMetadata MCP;
- Ranger REST;
- Trino DB-API;
- LLM provider.

This is local boundary abstraction, not full Hexagonal Architecture.

## Chain/Fallback

Classification sequence:

```text
deterministic rules
  -> exact action / reviewed suggestion
  -> no-match or ambiguity + Agent enabled
  -> Agent fallback
```

## Durable Job / Transactional Queue

`governance_jobs` provides idempotency, claim locking, retry, stale recovery, and dead-job evidence.

## Idempotent Consumer

Event ID, entity FQN, configuration/graph version, and action form deterministic idempotency material.

## Reconciliation Pattern

Ranger is treated as observed state. The service computes desired state, hashes both, and selects create/update/no-change/drift repair.

## Patterns deliberately rejected

- full Hexagonal/Clean Architecture;
- CQRS;
- event sourcing;
- command bus framework;
- generic repositories;
- internal HTTP microservices;
- custom state-machine library.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 12_DATA_MODEL.md -->
# Data Model

The application persists five business record types.

## `governance_jobs`

Durable queue fields include job type, status, priority, idempotency key, payload, attempts, availability, lock owner/time, errors, and correlation ID.

Statuses:

```text
QUEUED -> RUNNING -> SUCCEEDED
                   -> RETRY_WAIT -> RUNNING
                   -> DEAD
CANCELLED -> QUEUED only through authorized retry
```

Job types:

- `CLASSIFY_ASSET`;
- `AGENT_CLASSIFY`;
- `CREATE_OM_SUGGESTIONS`;
- `APPLY_CONFIRMED_TAGS`;
- `RECONCILE_RANGER`;
- `VERIFY_TRINO`.

## `classification_runs`

Stores deterministic or Agent source, version, outcome, action, suggestions, compact evidence, confidence, OpenMetadata Suggestion IDs, and correlation ID.

It does not store full prompts, conversations, or raw MCP responses.

## `policy_reconciliations`

Stores policy key, mapping version, desired/observed hashes, Ranger policy ID, selected action, compact result, and correlation ID.

## `access_verifications`

Stores controlled identity, expected/observed allow result, pass flag, query fingerprint, error class/message, and duration. No query rows.

## `audit_events`

Stores actor identity, action, object, correlation ID, and compact details.

## Explicitly absent

- proposal/approval tables;
- human account credentials;
- Agent checkpoint tables in baseline;
- policy authoring/versioning tables;
- raw business data.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 13_WORKFLOWS.md -->
# End-to-End Workflows

## A. Trusted deterministic exact match

```text
OpenMetadata event
  -> POST /events/metadata
  -> CLASSIFY_ASSET
  -> exact trusted rule + global flag
  -> APPLY_CONFIRMED_TAGS
  -> OpenMetadata targeted write using Execution Bot
  -> read-back assertion
  -> RECONCILE_RANGER
  -> VERIFY_TRINO
```

## B. Deterministic reviewed match

```text
OpenMetadata event
  -> CLASSIFY_ASSET
  -> exact non-trusted result
  -> CREATE_OM_SUGGESTIONS
  -> native OpenMetadata review by human
  -> confirmed-tag event
  -> RECONCILE_RANGER
  -> VERIFY_TRINO
```

## C. Agent fallback

```text
OpenMetadata event
  -> CLASSIFY_ASSET
  -> NO_MATCH or AMBIGUOUS
  -> AGENT_CLASSIFY
  -> Agent Worker claims job
  -> read-only MCP context using Agent Bot
  -> LangGraph + structured LLM output
  -> validate governed tag allow-list
  -> persist Agent classification run
  -> CREATE_OM_SUGGESTIONS
  -> Execution Worker creates Suggestions using Execution Bot
  -> human review
  -> confirmed-tag event
  -> Ranger + Trino
```

There is no Agent HTTP service and no internal HTTP submission token.

## D. Confirmed manual/native tag

```text
OpenMetadata confirmed-tag event
  -> POST /events/confirmed-tags
  -> RECONCILE_RANGER
  -> optional VERIFY_TRINO
```

## E. Drift repair

```text
confirmed OM state + YAML mapping
  -> desired Ranger policy
  -> observed Ranger policy
  -> hash comparison
  -> DRY_RUN / CREATE / UPDATE / NO_CHANGE / DRIFT_REPAIR
  -> grouped Trino verification when applicable
```


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 14_API_EVENT_CONTRACTS.md -->
# API and Event Contracts

Base prefix: `/api/v1`.

## `POST /events/metadata`

Accepts normalized metadata event:

- `event_id`;
- `event_type`;
- entity type/FQN/name;
- description;
- fields with bounded sample values;
- existing tags;
- correlation ID.

Returns HTTP 202 with durable `job_id`.

## `POST /events/confirmed-tags`

Accepts a normalized event after tags are confirmed in OpenMetadata. It queues Ranger reconciliation.

## Read/admin endpoints

- `GET /classification-runs/{run_id}`;
- `GET /jobs/{job_id}`;
- `POST /jobs/{job_id}/retry` — governance operator/admin only;
- `GET /capabilities`;
- `GET /health/live`;
- `GET /health/ready`.

## Removed v0.3 contract

`POST /events/agent-classifications` and `X-Agent-Token` are removed. Agent results are persisted internally by the Agent Worker through the shared service/repository layer.

## Event normalization

OpenMetadata raw webhook payloads should be converted to the normalized request contract at the edge. Do not let raw vendor payload shape propagate through services.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 15_INTEGRATION_CONTRACTS.md -->
# Integration Contracts

## OpenMetadata REST

Execution Worker uses the Execution Bot token for:

- native `SuggestTagLabel` creation;
- table/entity reads;
- column-by-FQN GET/PUT;
- confirmed tag read-back.

Production enablement requires verification against the deployed 1.13 OpenAPI and permissions.

## OpenMetadata MCP

Agent Worker uses the Agent Bot token. Allowed tool names are hard allow-listed:

- `search_metadata`;
- `semantic_search`;
- `get_entity_details`;
- `get_entity_lineage`;
- `get_test_definitions`.

Mutation tools are rejected before network invocation.

The official OpenMetadata AI SDK may replace the direct JSON-RPC transport, but tool filtering and worker boundaries remain unchanged.

## Apache Ranger

Execution Worker uses Ranger service credentials. Reconciliation uses lookup, canonical normalization/hash, ownership marker, and create/update endpoints. Dry-run performs reads only.

## Trino

Execution Worker runs controlled verification cases. Identity propagation and deny/masking/row-filter behavior must be verified in the target deployment.

## LLM provider

Only Agent Worker receives the provider machine credential. Structured output must conform to `AgentDecision` and allowed tags. Prompts instruct the model not to claim mutations.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 16_SECURITY_IDENTITY.md -->
# Security and Identity Model

## Core rule

Automation uses non-human identities. Human accounts are reserved for human review and administration.

## Secret placement

| Process | Allowed secrets |
|---|---|
| Control API | Database and API auth only |
| Execution Worker | DB, OpenMetadata Execution Bot, Ranger service secret, Trino verification credentials |
| Agent Worker | DB, OpenMetadata Agent MCP Bot, LLM provider key |

## Bot separation

Configuration validation rejects identical Agent and Execution Bot names. Deployments should also use distinct tokens, roles, and rotation schedules.

## Least privilege

- Agent Bot: read-only MCP tools.
- Execution Bot: create Suggestions and controlled direct metadata writes.
- Ranger identity: policy scope required by managed service only.
- Trino identities: minimal verification access.

## Human review

Agent and ordinary deterministic recommendations are accepted/rejected by a human personal OpenMetadata account. The Execution Bot creates the Suggestion but does not accept it.

## Data minimization

- Do not send raw table rows to Agent Worker.
- Bound sample values and disable them in sensitive environments unless approved.
- Store hashes and compact evidence instead of full prompts/MCP payloads.
- Never log secrets or Authorization headers.

## Network policy recommendation

- Agent Worker egress: OpenMetadata MCP and LLM endpoint only.
- Execution Worker egress: OpenMetadata REST, Ranger, and Trino only.
- API egress: normally none beyond database.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 17_ERROR_RETRY.md -->
# Error and Retry Model

## Error categories

- Validation/configuration: non-retryable until configuration changes.
- Authorization/permission: non-retryable by default; investigate Bot/service role.
- External transient: retryable, such as timeout or temporary 5xx.
- External contract mismatch: non-retryable until adapter/schema is corrected.
- Conflict/idempotency: return existing logical work or fail safely.
- Verification mismatch: business failure evidence, not necessarily transport retry.

## Retry ownership

- Execution Worker retries OpenMetadata REST, Ranger, and Trino jobs.
- Agent Worker retries the `AGENT_CLASSIFY` job according to job attempts.
- Internal Agent-to-Execution hand-off does not use HTTP; enqueue occurs in the shared database transaction.

## Backoff

Retryable jobs transition to `RETRY_WAIT` with bounded exponential backoff and jitter. Exhausted attempts become `DEAD`.

## Stale recovery

Workers periodically return stale `RUNNING` jobs to `QUEUED` based on heartbeat timestamp.

## Manual retry

Only governance operator/admin API actors can retry `DEAD` or `CANCELLED` jobs and must provide a reason.

## Fail-safe examples

- Unknown Agent tag: reject; no Suggestion.
- Ranger policy not owned: do not overwrite.
- OpenMetadata read-back missing confirmed tag: fail the job.
- MCP mutation tool request: reject locally.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 18_TESTING.md -->
# Testing Strategy

## Unit tests

Cover pure rules and decisions:

- exact/regex matching;
- ambiguity;
- trusted auto-apply;
- Agent fallback selection;
- policy mapping/rendering;
- Ranger normalization/hash;
- Bot identity validation.

## Repository tests

Cover:

- idempotent enqueue;
- claim once;
- worker-role job filtering;
- retry transitions;
- stale recovery where practical.

## Client contract tests

Use mocked HTTP/DB adapters to verify:

- OpenMetadata entity links and tag payloads;
- targeted column API usage;
- MCP read-only allow-list;
- Ranger dry-run makes no mutation request;
- Trino observation normalization.

## Service tests

Cover:

- deterministic action to next job;
- Agent output allow-list validation;
- native Suggestion grouping/idempotency;
- Ranger-to-verification job creation;
- verification-group completion;
- audit actor uses Bot/service identity.

## Migration validation

Run clean upgrade, downgrade where supported, and upgrade again. Historical migrations may enforce empty prototype tables.

## Smoke tests

- health/readiness;
- metadata event returns 202 and job is retrievable;
- Execution Worker processes deterministic job;
- Agent Worker job claim is isolated, using test doubles when provider packages are unavailable.

## Production contract tests

Before enabling writes, verify deployed OpenMetadata, Ranger, Trino, MCP, and identity permissions using `CAPABILITY_VERIFICATION.md`.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 19_OPERATIONS.md -->
# Operations

## Deployables

One source package/image may run three commands:

```bash
uvicorn app.main:app
python -m app.workers.execution_worker
python -m app.workers.agent_worker
```

Agent Worker is omitted in Phase 1.

## Environment profiles

Create separate secret sets and deployment service accounts for API, Execution Worker, and Agent Worker. Do not inject a superset of all secrets into one pod/container.

## Safety defaults

- `AGENT_ENABLED=false`;
- `RANGER_DRY_RUN=true`;
- `TRUSTED_AUTO_APPLY_ENABLED=false`;
- MCP read-only tool allow-list;
- distinct Bot names.

## Metrics

Track:

- queue depth by job type/status;
- job latency/retry/dead counts;
- deterministic outcome/action distribution;
- Agent duration, model/graph version, no-suggestion rate;
- MCP errors and tool calls;
- native Suggestion acceptance/rejection rate;
- Ranger action/drift counts;
- Trino verification pass/fail rate.

## Secret rotation

Rotate Agent Bot, Execution Bot, Ranger, Trino, and LLM credentials independently. Bot identity names should remain stable for audit while tokens rotate.

## Recovery

- Stop only the affected worker role.
- Correct credentials/configuration.
- Retry dead jobs through authorized API.
- Run reconciliation to restore desired state.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 20_DECISIONS.md -->
# Architecture Decision Records

## ADR-001 — OpenMetadata is the governance system of record

Accepted. Reuse assets, tags, Suggestions, review UI, history, Events, Apps, MCP, and Workflows.

## ADR-002 — Keep Phase 1 close to upstream OpenMetadata

Accepted. Native AI may be used when sufficient. No deep LLM modification of OpenMetadata source.

## ADR-003 — MVC + Service + Repository

Accepted. Full Hexagonal/Clean Architecture is rejected as unnecessary complexity for the current scope. Narrow interfaces remain for strategies and external clients.

## ADR-004 — Separate Backend and Agent Projects

Superseded by ADR-010.

## ADR-005 — PostgreSQL durable queue

Accepted. `governance_jobs` coordinates API and Execution Worker. No broker until measured need.

## ADR-006 — Separate machine identities

Accepted. Agent Bot and Execution Bot are distinct; runtime automation never uses personal accounts.

## ADR-007 — Agent Worker is read-only toward OpenMetadata

Accepted. MCP mutation tools are excluded; Agent output always requires native review.

## ADR-008 — Ranger and Trino remain Execution Worker-only

Accepted. Agent has no credentials or direct path.

## ADR-009 — Preserve Ranger ownership marker compatibility

Accepted. Existing `managed-by=dg-backend` marker remains until a safe live-policy migration is designed.

## ADR-010 — Separate project directory for AI Agent (`governance_agent/`)

Accepted. The AI Agent is decoupled from `governance_app` backend into a standalone project `governance_agent/`. The agent connects directly to OpenMetadata via MCP for discovery and REST for native Suggestions.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 21_EXAMPLES.md -->
# Examples

## Metadata event

```json
{
  "event_id": "evt-123",
  "event_type": "ENTITY_UPDATED",
  "entity_type": "table",
  "entity_fqn": "hive.sales.customers",
  "entity_name": "customers",
  "fields": [
    {"name": "email", "data_type": "varchar", "description": "Customer email"}
  ],
  "existing_tags": [],
  "correlation_id": "corr-123"
}
```

## Deterministic exact result

```json
{
  "outcome": "EXACT",
  "action": "AUTO_APPLY",
  "suggestions": [
    {
      "tag": "PII.Email",
      "field_path": "columns.email",
      "confidence": 0.99,
      "rule_id": "email-exact"
    }
  ]
}
```

## Agent structured decision

```json
{
  "suggestions": [
    {
      "tag": "PII.Email",
      "field_path": "columns.contact_address",
      "confidence": 0.87,
      "rationale": "Column description and lineage indicate customer email data."
    }
  ],
  "summary": "One governed classification is supported by MCP metadata context."
}
```

The Agent decision is persisted, then the Execution Worker creates a native Suggestion. It is not a confirmed tag.

## Bot audit actors

```text
bot:governance-agent-bot      -> AGENT_CLASSIFICATION_COMPLETED
bot:governance-execution-bot  -> OPENMETADATA_SUGGESTIONS_CREATED
alice@example.com             -> native OpenMetadata acceptance/rejection
```


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 22_ACTIVE_TASK.md -->
# Active Task

## Status

Phase 1 Completed for v0.4 on 2026-07-29.

## Change

Complete Phase 1 as a deterministic, OpenMetadata-native governance flow per `PHASE1_COMPLETION_BRIEF_EN.md`.

## Implemented

- decoupled Agent code into standalone project `governance_agent/`;
- created `governance_agent` project structure (`pyproject.toml`, `README.md`, `app/`, `tests/`);
- connected `governance_agent` directly to OpenMetadata via MCP client & REST Suggestion API;
- removed in-tree `app/agent` module and `agent_worker.py` from `governance_app/`;
- updated `governance_app` pyproject.toml and Makefile;
- recorded ADR-010 in `20_DECISIONS.md`;
- updated documentation (`AGENTS.md`, `.context/00_START_HERE.md`, `.context/08_ARCHITECTURE.md`, `.context/20_DECISIONS.md`);
- verified both test suites (32 tests in `governance_app`, 3 tests in `governance_agent`) pass cleanly.

## Validation pending in live environment

- actual OpenMetadata Bot permissions;
- live OpenMetadata webhook payload event subscription;
- live Ranger policy API endpoint behavior;
- live Trino query execution & policy propagation.



<!-- END FILE: {fname} -->

<!-- BEGIN FILE: 23_TASK_PLAYBOOK.md -->
# Engineering Task Playbook

## Task header template

```text
Goal:
Business rule(s):
Invariant(s):
Affected controller/service/repository/client/worker:
External contract:
Migration impact:
Tests required:
Rollback/safety note:
```

## Adding a deterministic rule

1. Update YAML.
2. Confirm target, exact/regex condition, tag, confidence, rationale, and `auto_apply`.
3. Add exact, no-match, and conflict tests.
4. Update business examples if semantics change.

## Changing Agent behavior

1. Confirm it remains fallback-only.
2. Keep MCP tool list read-only.
3. Update structured schema/prompt/graph version.
4. Ensure tags are validated again after model output.
5. Add tests using fake MCP/classifier; do not require a live provider for unit tests.
6. Never import Ranger/Trino/OpenMetadata REST mutation client into Agent code.

## Adding a job type

1. Add enum.
2. Define which worker owns it.
3. Add handler and deterministic idempotency key.
4. Add claim-filter and lifecycle tests.
5. Update decision table and data model docs.

## Changing OpenMetadata mutation

1. Verify deployed OpenAPI.
2. Update narrow client method.
3. Preserve unrelated metadata.
4. Add read-back assertion.
5. Use Execution Bot only.
6. Add mocked contract test.

## Changing credentials

1. Keep identities machine-only.
2. Preserve separate Agent/Execution Bot roles.
3. Update `.env.example`, capability output, security docs, and tests.
4. Do not log values.

## Definition of done

- Code compiles.
- Unit/contract tests pass.
- Migration round-trip passes when applicable.
- API/worker smoke passes.
- Context and active task are updated.
- Live-contract limitations are stated honestly.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: CAPABILITY_VERIFICATION.md -->
# Live Capability Verification

Complete before enabling production writes.

## OpenMetadata identities

- Create `governance-agent-bot` and assign read-only MCP permissions.
- Create `governance-execution-bot` and assign only required REST permissions.
- Confirm Bot tokens are distinct and no worker uses a human token.
- Confirm Execution Bot cannot accept Suggestions as reviewer.

## OpenMetadata REST

- Verify Suggestion payload/entity-link format.
- Verify column-by-FQN GET/PUT and tag merge behavior.
- Verify confirmed-tag event shape after acceptance.
- Verify read-back state and change-source behavior.

## MCP / AI SDK

- Verify endpoint, auth, transport, and actual tool names.
- Confirm mutation tools are not exposed to Agent Bot or are filtered.
- Verify `get_entity_details`/`get_entity_lineage` arguments.
- Verify installed `data-ai-sdk` version against deployed OpenMetadata.

## Agent runtime

- Install LangGraph/provider extras.
- Validate structured output and empty-result behavior.
- Confirm container has no Ranger, Trino, or Execution Bot secret.

## Ranger

- Verify service account, policy lookup/create/update, resources, access types, masking/row filters, ownership marker, and propagation delay.

## Trino

- Verify controlled service identities, impersonation, deny semantics, masking, row filters, timeout, and Ranger propagation.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: INDEX.md -->
# Context Index

Start with `00_START_HERE.md`. The numbered documents are intentionally ordered from business context through implementation and operations.


<!-- END FILE: {fname} -->

<!-- BEGIN FILE: RESEARCH_REFERENCES.md -->
# Research References

- Uploaded OpenMetadata 1.13 OpenAPI specification (`openapi-spec.json`).
- OpenMetadata AI SDK documentation.
- OpenMetadata MCP documentation and API connection guide.
- Apache Ranger Public API v2 documentation.
- Trino client, impersonation, and access-control documentation.

Source-derived decisions:

- OpenMetadata has native Suggestions and tag/column APIs.
- OpenMetadata Bot is a special automation user identity.
- OpenMetadata Apps can reference a Bot and have scheduling/run records.
- MCP exposes tools/resources/prompts for external AI applications.

Live deployment contracts remain subject to `CAPABILITY_VERIFICATION.md`.


<!-- END FILE: {fname} -->

