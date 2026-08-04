# DG Backend – Classification / OpenMetadata Context

## 1. Project structure

### Repositories
- Backend: `https://github.com/khanglv7repo/dg-backend`
- Infrastructure: `https://github.com/khanglv7repo/dg-infrastructure`

### Local backend
- Root: `/home/minh_chau/Documents/goto_ssi/dg_lab/backend`
- App: `/home/minh_chau/Documents/goto_ssi/dg_lab/backend/governance_app`
- Conda env: `dg_backend`

### Important backend files
```text
governance_app/
├── app/
│   ├── api/routes/
│   │   ├── classifications.py
│   │   ├── jobs.py
│   │   └── policies.py
│   ├── clients/openmetadata.py
│   ├── services/
│   │   ├── classification.py
│   │   ├── classification_commands.py
│   │   └── openmetadata_governance.py
│   ├── rules/classification.py
│   ├── repositories/
│   │   ├── classification.py
│   │   └── jobs.py
│   └── core/config.py
└── tests/
```

### Runtime sources of truth
- OpenMetadata = metadata + taxonomy + review/confirmed tag source of truth
- PostgreSQL `classification_rule_sets` = ACTIVE classification rules
- PostgreSQL `classification_runs` = classification results/audit
- PostgreSQL `governance_jobs` = durable jobs
- Ranger = enforcement target

## 2. Current classification flow

```text
POST /api/v1/classifications/run
        ↓
CLASSIFY_ASSET_FROM_OM
        ↓
BE GET metadata from OpenMetadata
        ↓
Classification Engine
  - name_exact
  - name_regex
  - description_regex
  - data_types
  - contains_any
        ↓
classification_runs
        ↓
TRUSTED_AUTO_APPLY_ENABLED=false
        ↓
action = OPENMETADATA_SUGGESTION
        ↓
CREATE_OM_SUGGESTIONS
        ↓
GET /api/v1/suggestions   (idempotency check)
POST /api/v1/suggestions  (create native OM suggestion)
        ↓
Human accepts/rejects in OM
        ↓
Confirmed tags in OM
        ↓
SYNC_RANGER_TAGS
        ↓
Ranger
```

Current target:
```text
financial_postgres.financial_db.crm.customers
```

`CLASSIFY_ASSET_FROM_OM` is working and returns `SUCCEEDED`.

## 3. OpenMetadata details

Version:
```text
1.13.1
```

Verified APIs:
```text
GET  /api/v1/tables/name/{fqn}?fields=tags,columns
GET  /api/v1/tags/name/{tagFQN}
GET  /api/v1/suggestions
POST /api/v1/suggestions
```

`/api/v1/tasks` is NOT the correct API for this deployment.

Example native suggestion payload:
```json
{
  "type": "SuggestTagLabel",
  "entityLink": "<#E::table::financial_postgres.financial_db.crm.customers::columns::email>",
  "description": "...",
  "tagLabels": [
    {
      "tagFQN": "PII.Email",
      "source": "Classification",
      "labelType": "Automated",
      "state": "Suggested"
    }
  ]
}
```

## 4. Current confirmed bug

Current job:
```text
b4261911-9778-4c97-906e-1d0aacc9e3df
CREATE_OM_SUGGESTIONS
status = DEAD
last_error = OpenMetadata resource not found: /v1/suggestions
```

The endpoint itself is valid. The real failure is inside OM payload validation.

Existing taxonomy:
```text
200 PII.Email
200 PII.Phone
200 Sensitivity.Confidential
```

Missing taxonomy:
```text
404 PII.Name
404 PII.DateOfBirth
404 PII.Address
404 PII.CustomerIdentifier
404 PII.EmployeeName
404 PII.Preference
404 PII.Employment
404 Sensitivity.Internal
```

OM auto-tag bot cannot create these:
```text
403 Create not allowed
```

This is expected. Runtime classification bot should NOT own taxonomy creation.

Current `customers` state:
```text
customers.email -> PII.Email, state=Suggested
customers.phone -> PII.Phone, state=Suggested
```

Other target columns currently have no tags.

## 5. Why the current batch fails

`OpenMetadataSuggestionService.create()` groups suggestions by `field_path` and processes them sequentially.

Current logic:
```text
for each field_path:
    find existing native OM suggestion by DG marker
    if not found:
        POST /v1/suggestions
```

If one field references a missing `tagFQN`:
```text
columns.address_line_1 -> PII.Address
                         ↓
PII.Address does not exist
                         ↓
OM returns 404
                         ↓
exception
                         ↓
whole CREATE_OM_SUGGESTIONS job stops
```

Therefore valid proposals later in the batch are never processed.

Also, duplicate detection only checks a native suggestion with the same DG marker. It does NOT reliably skip a tag already present on the column as `Suggested` or `Confirmed`.

## 6. Required fixes

### FIX 1 — Pre-validate all tag FQNs
Before POSTing any suggestion:
1. Collect unique candidate `tagFQN`s.
2. Check existence in OM.
3. If any are missing, fail BEFORE partial creation.
4. Return a clear error:
```text
Missing OpenMetadata tags:
- PII.Address
- PII.Name
- PII.DateOfBirth
...
```

Suggested client methods:
```python
get_tag(tag_fqn)
tag_exists(tag_fqn)
validate_tag_fqns(tag_fqns)
```

### FIX 2 — Skip already Suggested / Confirmed tags
Before creating a proposal:
1. Read live OM entity with `fields=tags,columns`.
2. Build current tag state per entity/column.
3. If same `tagFQN` already exists with `state=Suggested` or `state=Confirmed`, skip it.

Example:
```text
customers.email already has PII.Email Suggested
→ do not POST another PII.Email suggestion
```

### FIX 3 — Preserve useful OM 404 messages
Current `_request()` converts every 404 into:
```text
OpenMetadata resource not found: {path}
```

Change it to preserve the OM response body/message when available.

Preferred:
```text
OpenMetadata returned 404 for /v1/suggestions:
tag instance for PII.Address not found
```

## 7. Recommended implementation location

### `app/clients/openmetadata.py`
Add:
```text
get_tag(...)
tag_exists(...)
current tag-state extraction
better 404 error propagation
```

### `app/services/openmetadata_governance.py`
Update `OpenMetadataSuggestionService.create()`:
```text
1. collect unique candidate tags
2. validate all tags exist
3. load current OM entity/tag state once
4. skip already Suggested/Confirmed tags
5. keep existing marker-based native suggestion idempotency
6. POST only genuinely new suggestions
7. save resulting suggestion IDs
```

Avoid N GETs per column if possible; fetch the entity once.

## 8. Desired behavior after fix

For:
```text
financial_postgres.financial_db.crm.customers
```

Flow:
```text
BE classify
→ 25 candidate matches

OM current state:
- email = PII.Email Suggested
- phone = PII.Phone Suggested

BE:
- skip email duplicate
- skip phone duplicate
- validate remaining tagFQNs
```

If taxonomy incomplete:
```text
CREATE_OM_SUGGESTIONS
→ fail cleanly before partial creation
→ clear missing-tag list
```

After admin bootstraps all required taxonomy:
```text
CREATE_OM_SUGGESTIONS
→ create only missing proposals
→ SUCCEEDED
→ proposals visible in OM UI
```

## 9. Constraints

- Do NOT auto-create taxonomy from the runtime classification bot.
- Do NOT re-enable trusted auto-apply.
- Keep `TRUSTED_AUTO_APPLY_ENABLED=false`.
- Do NOT move to `/v1/tasks`; this deployment uses `/v1/suggestions`.
- Do NOT reintroduce sample-value / Trino sampling classification.
- Keep OpenMetadata as tag/review source of truth.
- Keep Ranger sync only after OM Confirmed tags.
- Preserve existing durable job/idempotency model.

## 10. Acceptance tests

```text
1. all candidate tagFQNs exist
   → creates missing OM suggestions

2. one candidate tagFQN missing
   → no partial creation
   → clear missing tag list

3. field already has same tag state=Suggested
   → skip

4. field already has same tag state=Confirmed
   → skip

5. same DG marker suggestion already exists
   → reuse/skip existing native OM suggestion

6. OM 404 body contains useful message
   → backend error preserves that message

7. existing classification tests still pass
```

Current test baseline:
```text
46 tests passed
```
