# Product and Business Context

## Objective

Provide a small governance control plane that classifies assets using OpenMetadata metadata and manages desired Apache Ranger policies without replacing either OpenMetadata or Ranger.

## System responsibilities

1. OpenMetadata owns asset metadata, confirmed tags, Suggestions and reviewer workflows.
2. Deterministic classification evaluates current OpenMetadata metadata using versioned rules.
3. PostgreSQL owns desired Ranger policy state.
4. Ranger owns enforcement.
5. Trino is the governed query engine behind Ranger; backend-side query verification is not a production capability.

## Product boundary

The backend adds:

- deterministic exact/regex classification;
- native OpenMetadata Suggestion creation;
- explicitly trusted direct tag application;
- confirmed-tag synchronization into Ranger's tag store;
- native Ranger JSON policy import and desired-state storage;
- explicit DB -> Ranger reconciliation;
- durable jobs, idempotency and audit evidence.

The backend does not add:

- a second metadata catalog;
- a custom human review UI;
- a custom policy language;
- automatic policy generation from OpenMetadata tags;
- startup policy mutation;
- production Trino verification jobs.

## Success criteria

- OpenMetadata and Ranger remain authoritative in their respective domains.
- Repeated policy import is idempotent and revisioned.
- Explicit policy sync converges DB desired state to owned Ranger policies.
- Unmanaged Ranger policies are never overwritten implicitly.
- Missing identity headers grant no governance roles.
- The core remains understandable as MVC + Service + Repository.
