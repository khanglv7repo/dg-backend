from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_OPERATIONS = {
    "/v1/suggestions": {"get", "post"},
    "/v1/suggestions/{id}/accept": {"put"},
    "/v1/suggestions/{id}/reject": {"put"},
    "/v1/columns/name/{fqn}": {"get", "put"},
    "/v1/events/subscriptions": {"get"},
    "/v1/apps/configure/{name}": {"post"},
    "/v1/apps/schedule/{name}": {"post"},
    "/v1/apps/trigger/{name}": {"post"},
    "/v1/apps/stop/{name}": {"post"},
}


def verify(document: dict) -> list[str]:
    errors: list[str] = []
    paths = document.get("paths", {})
    for path, methods in REQUIRED_OPERATIONS.items():
        available = set(paths.get(path, {}))
        missing = methods - available
        if missing:
            errors.append(f"{path}: missing methods {sorted(missing)}")

    schemas = document.get("components", {}).get("schemas", {})
    create_suggestion = schemas.get("CreateSuggestion", {}).get("properties", {})
    for field in ("description", "tagLabels", "type", "entityLink"):
        if field not in create_suggestion:
            errors.append(f"CreateSuggestion: missing property {field}")

    required_tag_fields = set(schemas.get("TagLabel", {}).get("required", []))
    expected_tag_fields = {"tagFQN", "source", "labelType", "state"}
    if not expected_tag_fields.issubset(required_tag_fields):
        errors.append(
            "TagLabel required fields do not include "
            f"{sorted(expected_tag_fields - required_tag_fields)}"
        )
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the OpenMetadata OpenAPI capabilities used by this backend."
    )
    parser.add_argument("spec", type=Path)
    args = parser.parse_args()
    document = json.loads(args.spec.read_text(encoding="utf-8"))
    errors = verify(document)
    if errors:
        raise SystemExit("OpenAPI capability verification failed:\n- " + "\n- ".join(errors))
    version = document.get("info", {}).get("version", "unknown")
    print(f"OpenMetadata OpenAPI capability verification passed (version={version})")


if __name__ == "__main__":
    main()
