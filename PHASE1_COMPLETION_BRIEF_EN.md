# Phase 1 Completion Brief

## Objective

Complete Phase 1 as a deterministic, OpenMetadata-native governance flow:

```text
OpenMetadata ingestion
→ automatic classification
→ metadata pattern rules
→ optional bounded sample scan
→ OpenMetadata Suggestion or trusted auto-apply
→ confirmed tag change
→ Ranger policy reconciliation
→ Trino verification
```

Phase 1 must not use LangGraph or LLM classification.

---

## Current Status

| Capability | Status |
|---|---|
| Email pattern by column name | Done |
| Phone pattern by column name | Done |
| YAML rule customization | Done |
| OpenMetadata Suggestion creation | Done |
| Trusted auto-apply | Done |
| Tag-to-Ranger mapping | Done |
| Ranger dry-run/create/update/no-change | Done |
| Trino verification | Done |
| Raw OpenMetadata ChangeEvent intake | Incomplete |
| Tag-removal reconciliation | Incomplete |
| Real sample-value pattern scan | Missing |
| Automatic classification after ingestion | Incomplete |

---

## Work to Complete

### 1. Raw OpenMetadata ChangeEvent Adapter

Add:

```text
app/api/routes/openmetadata_events.py
app/schemas/openmetadata_events.py
app/services/openmetadata_event_adapter.py
```

Endpoint:

```http
POST /api/v1/integrations/openmetadata/events
```

Required behavior:

- authenticate webhook requests;
- deduplicate events;
- parse entity/schema/tag changes;
- read back current entity state from OpenMetadata;
- enqueue `CLASSIFY_ASSET` for new or changed assets;
- enqueue `RECONCILE_RANGER` for confirmed tag changes;
- ignore suggestion creation/rejection events unless confirmed tag state changed.

### 2. Automatic Post-Ingestion Classification

Primary path:

```text
OpenMetadata asset/schema ChangeEvent
→ CLASSIFY_ASSET
```

Add fallback job:

```text
DISCOVER_UNCLASSIFIED_ASSETS
```

The fallback must:

- query only assets changed since a stored watermark;
- enqueue only new, changed, or unclassified assets;
- avoid full catalog scans;
- use the same idempotency key as the event path.

Suggested key:

```text
classify:<entity_fqn>:<entity_version>:<ruleset_hash>
```

### 3. Bounded Sample-Value Scanner

Add job:

```text
SAMPLE_COLUMN_VALUES
```

Run only when metadata rules return `NO_MATCH` or `AMBIGUOUS`.

Add:

```text
app/services/data_value_scanner.py
app/rules/value_detectors.py
app/clients/sample_query.py
app/models/data_value_scan.py
app/repositories/data_value_scan.py
config/data_value_scan.yaml
```

Rules:

- maximum 500 rows per column by default;
- short query timeout;
- low concurrency;
- only unclassified string columns;
- do not store or log raw values;
- store aggregate metrics only;
- reuse OpenMetadata profiles before querying;
- prefer bounded Trino queries;
- never full-scan large tables in the backend.

Initial detectors:

```text
EmailValueDetector
PhoneValueDetector
NationalIdValueDetector
PaymentCardValueDetector
```

Sample-based results always create OpenMetadata Suggestions. They never auto-apply.

### 4. Tag-Removal Reconciliation

When a confirmed governed tag is removed:

```text
OpenMetadata ChangeEvent
→ read current tag state
→ recalculate desired Ranger state
→ UPDATE or DISABLE owned policy
→ Trino verification
```

Requirements:

- add ownership markers to backend-managed Ranger policies;
- never modify manually managed policies;
- default removal behavior is `DISABLE`;
- allow `DELETE` only through an explicit feature flag;
- do not leave stale access after tag removal.

Example ownership marker:

```text
managed-by=data-governance-platform
policy-key=<canonical-policy-key>
source=openmetadata
```

---

## Required Job Types

```text
DISCOVER_UNCLASSIFIED_ASSETS
CLASSIFY_ASSET
SAMPLE_COLUMN_VALUES
CREATE_OM_SUGGESTIONS
APPLY_CONFIRMED_TAGS
RECONCILE_RANGER
VERIFY_TRINO
```

`AGENT_CLASSIFY` must remain disabled in Phase 1.

---

## Required Data Model Additions

Add:

```text
data_value_scan_runs
integration_watermarks
```

`data_value_scan_runs` stores only:

- entity and field identifiers;
- scanner/ruleset version;
- input fingerprint;
- sample counts;
- aggregate match metrics;
- status and failure evidence.

It must not store raw sampled values.

---

## Business Rules

1. Metadata rules run before value sampling.
2. Exact trusted rules may auto-apply only when `TRUSTED_AUTO_APPLY_ENABLED=true`.
3. Regex and sample-based detections require native OpenMetadata review.
4. Rejected Suggestions never affect Ranger.
5. Only confirmed current tag state affects Ranger.
6. External writes must be idempotent.
7. Ranger reconciliation is desired-state based:
   `CREATE | UPDATE | NO_CHANGE | DISABLE | DELETE | DRY_RUN`.
8. Trino verification runs only after successful Ranger mutation and propagation delay.
9. Runtime automation uses bot/service identities, never personal accounts.
10. The backend must not perform distributed full-data scans.

---

## Acceptance Tests

Phase 1 is complete when all of these pass:

- ingestion of a new table automatically starts classification;
- `email_address` produces `PII.Email`;
- `mobile_phone` produces `PII.Phone`;
- YAML rules can be added, disabled, and versioned;
- ambiguous `contact_value` can be classified from bounded sample metrics;
- raw sample values are never persisted;
- duplicate OpenMetadata events create one logical run;
- accepting a Suggestion triggers Ranger reconciliation;
- rejecting a Suggestion does not change Ranger;
- Ranger supports dry-run, create, update, and no-change;
- removing a tag updates or disables only owned Ranger policies;
- repeated reconciliation does not create duplicate policies;
- Trino positive and negative verification both pass;
- missed webhook events are recovered by watermark-based discovery;
- Agent Worker remains disabled.

---

## Non-Goals

Do not add:

- a second FastAPI application;
- LangGraph or LLM execution in Phase 1;
- a custom approval workflow;
- Kafka, RabbitMQ, Redis, Celery, or n8n;
- full-table scanning in Python;
- generic repositories or full Hexagonal Architecture;
- mutation of manually managed Ranger policies.

---

## Recommended Implementation Order

```text
1. Verify live OpenMetadata, Ranger, and Trino contracts
2. Implement raw OpenMetadata ChangeEvent adapter
3. Add automatic ingestion trigger and scheduled fallback
4. Add tag-removal desired-state reconciliation
5. Add bounded sample-value scanner
6. Run end-to-end acceptance tests
```
