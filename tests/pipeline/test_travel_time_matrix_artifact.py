from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path


ARTIFACT_DIR = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/05/"
    "PIS-20260502-141927Z-7A71__compute-travel-balance-evidence-from-montreal-halifax-and-dolbeau-mistassini/"
    "artifacts/travel"
)


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_builder_output_validates() -> None:
    builder = _load_module(ARTIFACT_DIR / "build_travel_time_matrix.py", "pis_7a71_builder")
    validator = _load_module(ARTIFACT_DIR / "validate_travel_time_matrix.py", "pis_7a71_validator")

    artifact = builder.build_artifact()

    assert artifact["record_stats"]["eligible_property_count"] == 1
    assert artifact["record_stats"]["over_eight_hour_property_count"] == 1
    assert validator.validate_artifact(artifact) == []


def test_validator_rejects_missing_over_eight_hour_penalty_flag() -> None:
    validator = _load_module(ARTIFACT_DIR / "validate_travel_time_matrix.py", "pis_7a71_validator_neg")
    artifact = json.loads((ARTIFACT_DIR / "travel_time_matrix.json").read_text(encoding="utf-8"))

    artifact["rows"][0]["penalty_flags"] = ["TRAVEL_ASYMMETRY_OVER_2_5_HOURS"]
    artifact["rows"][0]["over_eight_hour_origins"] = []
    artifact["rows"][0]["penalty_notes"] = []

    issues = validator.validate_artifact(artifact)

    assert any("exceeds 8 hours without ORIGIN_OVER_EIGHT_HOURS flag" in issue for issue in issues)


def test_builder_documents_gap_when_property_route_snapshot_is_missing() -> None:
    builder = _load_module(ARTIFACT_DIR / "build_travel_time_matrix.py", "pis_7a71_builder_gap")
    canonical_records = json.loads(
        (
            Path(
                "governance/ledger/patch_instruction_sheets/causyl/active/2026/05/"
                "PIS-20260502-141924Z-76B3__canonicalize-deduplicate-and-filter-reunion-property-records/"
                "artifacts/research/canonical_property_records.json"
            )
        ).read_text(encoding="utf-8")
    )
    synthetic = deepcopy(canonical_records["surviving_properties"][0])
    synthetic["canonical_property_id"] = "reunion_property::missing_route_snapshot"
    canonical_records["surviving_properties"].append(synthetic)

    builder._load_json = lambda _path: canonical_records
    artifact = builder.build_artifact()

    assert artifact["record_stats"]["eligible_property_count"] == 2
    assert artifact["record_stats"]["matrix_row_count"] == 1
    assert artifact["record_stats"]["documented_gap_count"] == 1
    assert artifact["record_stats"]["over_eight_hour_property_count"] == 1
    assert artifact["rows"][0]["canonical_property_id"] != synthetic["canonical_property_id"]
    assert artifact["documented_gaps"] == [
        {
            "canonical_property_id": "reunion_property::missing_route_snapshot",
            "property_name": synthetic["property_name"],
            "municipality": synthetic["municipality"],
            "region": synthetic["region"],
            "gap_reason": "No checked-in route observation exists yet for this canonical property.",
            "recovery_expectation": (
                "Add one consistent route snapshot or a defended approximation for all three origins, "
                "or keep the property downstream only with this uncertainty carried forward explicitly."
            ),
            "location_granularity_note": (
                "No travel row was emitted because the checked-in observation set does not yet provide a "
                "complete three-origin route snapshot for this canonical property."
            ),
        }
    ]


def test_validator_rejects_incorrect_record_stats_summary_counts() -> None:
    validator = _load_module(ARTIFACT_DIR / "validate_travel_time_matrix.py", "pis_7a71_validator_stats")
    artifact = json.loads((ARTIFACT_DIR / "travel_time_matrix.json").read_text(encoding="utf-8"))

    artifact["record_stats"]["eligible_property_count"] = 9
    artifact["record_stats"]["matrix_row_count"] = 0
    artifact["record_stats"]["documented_gap_count"] = 2
    artifact["record_stats"]["over_eight_hour_property_count"] = 0

    issues = validator.validate_artifact(artifact)

    assert any(
        "record_stats.eligible_property_count=9 does not match computed value 1" in issue
        for issue in issues
    )
    assert any(
        "record_stats.matrix_row_count=0 does not match computed value 1" in issue
        for issue in issues
    )
    assert any(
        "record_stats.documented_gap_count=2 does not match computed value 0" in issue
        for issue in issues
    )
    assert any(
        "record_stats.over_eight_hour_property_count=0 does not match computed value 1" in issue
        for issue in issues
    )


def test_validator_rejects_gap_without_recovery_expectation() -> None:
    validator = _load_module(ARTIFACT_DIR / "validate_travel_time_matrix.py", "pis_7a71_validator_gap")
    artifact = json.loads((ARTIFACT_DIR / "travel_time_matrix.json").read_text(encoding="utf-8"))
    artifact["documented_gaps"] = [
        {
            "canonical_property_id": "reunion_property::missing_route_snapshot",
            "gap_reason": "No checked-in route observation exists yet for this canonical property.",
            "recovery_expectation": "",
        }
    ]

    issues = validator.validate_artifact(artifact)

    assert any("documented_gaps[0] missing recovery_expectation" in issue for issue in issues)
