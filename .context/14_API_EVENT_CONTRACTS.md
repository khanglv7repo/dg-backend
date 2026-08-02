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
