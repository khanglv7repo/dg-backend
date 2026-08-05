from pathlib import Path

APPLICATION_ROOT_PATH = Path(__file__).resolve().parents[1]
APPLICATION_SOURCE_PATH = APPLICATION_ROOT_PATH / "app"
TARGET_SERVICE_PATHS = [
    APPLICATION_SOURCE_PATH / "services/classification.py",
    APPLICATION_SOURCE_PATH / "services/asset_discovery.py",
    APPLICATION_SOURCE_PATH / "services/intake.py",
]


def test_classification_runtime_services_do_not_reference_rule_file_path():
    legacy_constructor = "ClassificationRuleEngine." + "from_path"
    legacy_setting = "classification_" + "rules_path"
    legacy_yaml = "classification_" + "rules.yaml"

    for path in TARGET_SERVICE_PATHS:
        text = path.read_text(encoding="utf-8")
        assert legacy_setting not in text
        assert legacy_constructor not in text
        assert legacy_yaml not in text
