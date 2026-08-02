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

### Auto-classification Bot

Purpose: read OpenMetadata metadata needed by classification/sample scanning in the
Execution Worker. It is configured with `OM_AUTOCLASSIFICATION_BOT_TOKEN`.

### Auto-tag Bot

Purpose: controlled OpenMetadata REST mutations for the Execution Worker. It is
configured with `OM_AUTO_TAG_BOT_TOKEN`; its audit name remains
`governance-execution-bot` unless deployment configuration overrides it.

Allowed:

- read asset/column metadata;
- create native tag Suggestions;
- perform explicitly trusted deterministic tag writes;
- read back postconditions.

It is not a human reviewer and must not accept its own Suggestions.

### Ingestion Bot

Purpose: run the upstream metadata-ingestion workflow and read catalog assets for
the Execution Worker's watermark-based discovery. It is configured with
`OM_INGESTION_BOT_TOKEN` and has no classification/tag mutation role.

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
