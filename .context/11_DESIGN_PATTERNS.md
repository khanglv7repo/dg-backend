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
