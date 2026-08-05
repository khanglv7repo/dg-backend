from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml

ENVIRONMENT_FILE_PATH_VARIABLE = "ENVIRONMENT_FILE_PATH"
TAXONOMY_CONFIGURATION_PATH_VARIABLE = "OM_TAXONOMY_FILE"
DEFAULT_ENVIRONMENT_FILE_PATH = Path(".env")


def load_env_file(path: Path) -> None:
    """Load a simple KEY=VALUE .env file without requiring python-dotenv."""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def configured_environment_file_path() -> Path:
    return (
        Path(
            os.getenv(
                ENVIRONMENT_FILE_PATH_VARIABLE,
                str(DEFAULT_ENVIRONMENT_FILE_PATH),
            )
        )
        .expanduser()
        .resolve()
    )


def configured_taxonomy_path(environment_file_path: Path) -> Path:
    configured_path = Path(required_env(TAXONOMY_CONFIGURATION_PATH_VARIABLE)).expanduser()
    if not configured_path.is_absolute():
        configured_path = environment_file_path.parent / configured_path
    return configured_path.resolve()


class OpenMetadataBootstrap:
    def __init__(self, base_url: str, token: str, timeout: float = 20.0) -> None:
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = self.client.request(method, path, **kwargs)
        if response.is_error and response.status_code != 404:
            raise RuntimeError(
                f"OpenMetadata {method} {path} failed: "
                f"HTTP {response.status_code}: {response.text[:1000]}"
            )
        return response

    def ensure_classification(self, item: dict) -> None:
        name = str(item["name"])
        encoded = quote(name, safe="")
        response = self.request("GET", f"/v1/classifications/name/{encoded}")

        if response.status_code == 200:
            print(f"[skip] classification exists: {name}")
            return

        payload = {
            "name": name,
            "displayName": item.get("displayName", name),
            "description": item["description"],
            "provider": item.get("provider", "automation"),
            "mutuallyExclusive": bool(item.get("mutuallyExclusive", False)),
        }
        response = self.request("POST", "/v1/classifications", json=payload)
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Could not create classification {name}: "
                f"HTTP {response.status_code}: {response.text[:1000]}"
            )
        print(f"[create] classification: {name}")

    def ensure_tag(self, classification_name: str, item: dict) -> None:
        name = str(item["name"])
        fqn = f"{classification_name}.{name}"
        encoded = quote(fqn, safe="")
        response = self.request("GET", f"/v1/tags/name/{encoded}")

        if response.status_code == 200:
            print(f"[skip] tag exists: {fqn}")
            return

        payload = {
            "name": name,
            "displayName": item.get("displayName", name),
            "description": item["description"],
            "classification": classification_name,
        }

        parent = item.get("parent")
        if parent:
            payload["parent"] = parent

        response = self.request("POST", "/v1/tags", json=payload)
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Could not create tag {fqn}: HTTP {response.status_code}: {response.text[:1000]}"
            )
        print(f"[create] tag: {fqn}")

    def bootstrap(self, config: dict) -> None:
        classifications = config.get("classifications", [])
        if not classifications:
            raise RuntimeError("No classifications found in taxonomy config")

        for classification in classifications:
            self.ensure_classification(classification)
            for tag in classification.get("tags", []):
                self.ensure_tag(str(classification["name"]), tag)


def main() -> int:
    env_path = configured_environment_file_path()
    if not env_path.exists():
        raise RuntimeError(f"Environment file not found: {env_path}")

    load_env_file(env_path)
    print(f"[env] loaded: {env_path}")

    config_path = configured_taxonomy_path(env_path)

    base_url = os.getenv(
        "OPENMETADATA_BASE_URL",
        "http://localhost:8585/api",
    )

    # Prefer the backend autoclassification bot token. Fall back to the
    # ingestion token only if that is how the local dev environment is wired.
    token = os.getenv("OM_AUTOCLASSIFICATION_BOT_TOKEN") or os.getenv("OM_INGESTION_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing OM_AUTOCLASSIFICATION_BOT_TOKEN (or OM_INGESTION_BOT_TOKEN fallback)"
        )

    if not config_path.exists():
        raise RuntimeError(f"Taxonomy file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    bootstrap = OpenMetadataBootstrap(base_url=base_url, token=token)
    try:
        bootstrap.bootstrap(config)
    finally:
        bootstrap.close()

    print("[ok] Phase 1 OpenMetadata taxonomy bootstrap complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise
