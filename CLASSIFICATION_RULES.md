# Classification Rule Management

Classification rules are now JSON-only and DB-backed.

## Runtime source of truth

`classification_rule_sets` in PostgreSQL is the runtime source of truth.

`config/classification_rules.yaml` is removed and is not read by the runtime.

## Import

Admin-only multipart upload:

```bash
curl -X POST \
  'http://127.0.0.1:8000/api/v1/classification-rules/import?activate=true' \
  -H 'X-Actor-Id: local-admin' \
  -H 'X-Actor-Name: Local Admin' \
  -H 'X-Actor-Roles: governance-admin' \
  -F 'file=@config/classification_rules.example.json;type=application/json'
```

The default `activate=true` means a successfully validated upload immediately
becomes the active rule set.

## Inspect

```bash
curl \
  http://127.0.0.1:8000/api/v1/classification-rules/active \
  -H 'X-Actor-Id: local-admin' \
  -H 'X-Actor-Name: Local Admin' \
  -H 'X-Actor-Roles: governance-admin'
```

List versions:

```bash
curl \
  http://127.0.0.1:8000/api/v1/classification-rules \
  -H 'X-Actor-Id: local-admin' \
  -H 'X-Actor-Name: Local Admin' \
  -H 'X-Actor-Roles: governance-admin'
```

## Roll back / switch version

```bash
curl -X POST \
  http://127.0.0.1:8000/api/v1/classification-rules/<RULE_SET_ID>/activate \
  -H 'X-Actor-Id: local-admin' \
  -H 'X-Actor-Name: Local Admin' \
  -H 'X-Actor-Roles: governance-admin'
```

## Classification

Existing command remains:

```bash
curl -X POST \
  http://127.0.0.1:8000/api/v1/classifications/run \
  -H 'Content-Type: application/json' \
  -H 'X-Actor-Id: local-admin' \
  -H 'X-Actor-Name: Local Admin' \
  -H 'X-Actor-Roles: governance-admin' \
  -d '{
    "entity_type": "table",
    "entity_fqn": "<OPENMETADATA_TABLE_FQN>"
  }'
```

The execution worker hydrates current metadata from OpenMetadata and evaluates
the active PostgreSQL rule set.

## Safety

- JSON only.
- Maximum upload size: 2 MiB.
- Rules are validated before persistence.
- Duplicate rule IDs are rejected.
- Invalid regular expressions are rejected.
- Confidence must be between 0 and 1.
- Only one rule set named `default` can be ACTIVE at a time.
- Rule-set activation does not itself classify assets or mutate OpenMetadata.
