from __future__ import annotations

from app.rules.policy_mapping import PolicyMappingResolver

from app.core.config import get_settings
from app.rules.classification import ClassificationRuleEngine


def main() -> None:
    settings = get_settings()
    rules = ClassificationRuleEngine.from_path(
        settings.resolve_path(settings.classification_rules_path)
    )
    policies = PolicyMappingResolver.from_path(settings.resolve_path(settings.policy_mappings_path))
    print(f"classification_version={rules.configuration_version} rules={len(rules.rules)}")
    print(f"policy_version={policies.configuration_version} mappings={len(policies.mappings)}")


if __name__ == "__main__":
    main()
