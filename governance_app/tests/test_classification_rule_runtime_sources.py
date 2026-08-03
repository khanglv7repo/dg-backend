from pathlib import Path


def test_classification_runtime_services_do_not_reference_rule_file_path():
    app_root = Path(__file__).resolve().parents[1] / "app"
    targets = [
        app_root / "services/classification.py",
        app_root / "services/asset_discovery.py",
        app_root / "services/intake.py",
    ]

    legacy_constructor = "ClassificationRuleEngine." + "from_path"
    legacy_setting = "classification_" + "rules_path"
    legacy_yaml = "classification_" + "rules.yaml"

    for path in targets:
        text = path.read_text(encoding="utf-8")
        assert legacy_setting not in text
        assert legacy_constructor not in text
        assert legacy_yaml not in text
