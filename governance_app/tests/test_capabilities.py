from app.core.config import Settings
from app.services.capabilities import CapabilityService


def test_capabilities_describe_current_architecture() -> None:
    settings = Settings(
        _env_file=None,
        mcp_enabled=True,
        trino_readonly_enabled=True,
        trino_readonly_user="alice",
        trino_verification_control_user="control",
    )

    report = CapabilityService(settings).report()

    assert report["application"]["legacy_governance_job_queue"] is False
    assert report["application"]["legacy_native_ranger_policy_catalog"] is False
    assert report["application"]["policy_source_of_truth"] == (
        "PostgreSQL data_access_policy_version"
    )
    assert report["openmetadata"]["backend_direct_tag_mutation"] is False
    assert report["policy"]["activation_via_mcp"] is False
    assert report["data_quality"]["human_approval_required"] is True
    assert report["data_quality"]["execution_semantics"] == "AT_LEAST_ONCE"
    assert report["trino_verification"]["access"] is True
    assert report["trino_verification"]["mask_hash_character_columns"] is True
    assert report["trino_verification"]["row_filter"] is True
    assert report["trino_verification"]["subject_scope"] == "DIRECT_USER_ONLY"
    assert (
        report["trino_verification"]["group_only_policy_behavior"]
        == "VERIFICATION_UNAVAILABLE"
    )
    assert (
        report["trino_verification"]["insufficient_evidence_behavior"]
        == "VERIFICATION_UNAVAILABLE"
    )
