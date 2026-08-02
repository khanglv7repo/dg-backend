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
