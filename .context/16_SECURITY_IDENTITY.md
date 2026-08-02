# Security and Identity Model

## Core rule

Automation uses non-human identities. Human accounts are reserved for human review and administration.

## Secret placement

| Process | Allowed secrets |
|---|---|
| Control API | Database and API auth only |
| Execution Worker | DB, OpenMetadata Ingestion Bot, Auto-classification Bot, Auto-tag Bot, Ranger service secret, Trino verification credentials |
| Agent Worker | DB, OpenMetadata Agent MCP Bot, LLM provider key |
| Metadata-ingestion sidecar | `OM_INGESTION_BOT_TOKEN`, source database credential |

## Bot separation

Configuration validation rejects identical Agent and Auto-classification Bot names, and rejects equal Ingestion, Auto-classification, and Auto-tag token values. Deployments must provision distinct tokens, roles, and rotation schedules.

## Least privilege

- Agent Bot: read-only MCP tools.
- Ingestion Bot: upstream catalog ingestion and discovery only.
- Ingestion Bot is the only OpenMetadata credential passed to the upstream ingestion sidecar; do not inject `OPENMETADATA_ADMIN_TOKEN`, an admin PAT, or admin login credentials.
- Auto-classification Bot: classification metadata reads only.
- Auto-tag Bot: create Suggestions and controlled direct metadata writes.
- Ranger identity: policy scope required by managed service only.
- Trino identities: minimal verification access.

## Human review

Agent and ordinary deterministic recommendations are accepted/rejected by a human personal OpenMetadata account. The Auto-tag Bot creates the Suggestion but does not accept it.

## Data minimization

- Do not send raw table rows to Agent Worker.
- Bound sample values and disable them in sensitive environments unless approved.
- Store hashes and compact evidence instead of full prompts/MCP payloads.
- Never log secrets or Authorization headers.

## Network policy recommendation

- Agent Worker egress: OpenMetadata MCP and LLM endpoint only.
- Execution Worker egress: OpenMetadata REST, Ranger, and Trino only.
- API egress: normally none beyond database.
