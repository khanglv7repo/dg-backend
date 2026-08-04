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

## Native OpenMetadata Suggestion creation

| Candidate tag taxonomy | Live entity/column tag state | Existing DG marker | Action |
|---|---|---|---|
| Any candidate missing | Any | Any | Fail the complete batch before any write, listing missing tag FQNs |
| All exist | Same tag is `Suggested` or `Confirmed` | None | Skip that tag |
| All exist | Tag not currently present | Matching open marker | Reuse existing native Suggestion |
| All exist | Tag not currently present | None | Create native OpenMetadata Suggestion |

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
