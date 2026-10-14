from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ARTIFACT_DIR = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/05/"
    "PIS-20260502-141924Z-76B3__canonicalize-deduplicate-and-filter-reunion-property-records/"
    "artifacts/research"
)


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_builder_output_validates() -> None:
    builder = _load_module(ARTIFACT_DIR / "build_canonical_property_records.py", "pis_76b3_builder")
    validator = _load_module(ARTIFACT_DIR / "validate_canonical_property_records.py", "pis_76b3_validator")

    artifact = builder.build_artifact()

    assert artifact["record_stats"]["raw_candidate_count"] == 9
    assert artifact["record_stats"]["canonical_candidate_count"] == 8
    assert artifact["record_stats"]["surviving_property_count"] == 1
    assert artifact["record_stats"]["rejected_property_count"] == 7
    merged = next(
        record
        for record in artifact["rejected_properties"]
        if "charlevoix_lac_nairne_chalet_des_aviateurs_caq" in record["merged_source_candidate_ids"]
    )
    assert sorted(merged["merged_source_candidate_ids"]) == [
        "charlevoix_aviators_cottage_airbnb",
        "charlevoix_lac_nairne_chalet_des_aviateurs_caq",
    ]
    assert len(merged["duplicate_sources_found"]) == 2
    assert validator.validate_artifact(artifact) == []


def test_validator_rejects_duplicate_source_overlap_without_ambiguity_note() -> None:
    validator = _load_module(ARTIFACT_DIR / "validate_canonical_property_records.py", "pis_76b3_validator_dup")
    artifact = json.loads((ARTIFACT_DIR / "canonical_property_records.json").read_text(encoding="utf-8"))

    duplicate = json.loads(json.dumps(artifact["surviving_properties"][0]))
    duplicate["canonical_property_id"] = duplicate["canonical_property_id"] + "_dup"
    duplicate.pop("ambiguity_note", None)
    artifact["surviving_properties"][0].pop("ambiguity_note", None)
    artifact["surviving_properties"].append(duplicate)

    issues = validator.validate_artifact(artifact)

    assert any("duplicate source URL spans multiple surviving canonical records" in issue for issue in issues)


def test_validator_rejects_non_unique_canonical_name_place_without_ambiguity() -> None:
    validator = _load_module(ARTIFACT_DIR / "validate_canonical_property_records.py", "pis_76b3_validator_name")
    artifact = json.loads((ARTIFACT_DIR / "canonical_property_records.json").read_text(encoding="utf-8"))

    duplicate = json.loads(json.dumps(artifact["rejected_properties"][0]))
    duplicate["canonical_property_id"] = duplicate["canonical_property_id"] + "_dup"
    duplicate["canonical_url"] = "https://example.com/unique-but-same-name-place"
    duplicate.pop("ambiguity_note", None)
    artifact["rejected_properties"].append(duplicate)

    issues = validator.validate_artifact(artifact)

    assert any("canonical property name/place is not unique without ambiguity note" in issue for issue in issues)
