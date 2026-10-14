from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ARTIFACT_DIR = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/05/"
    "PIS-20260502-141921Z-8DCF__discover-reunion-scale-quebec-rental-listings-across-target-and-proxy-dates/"
    "artifacts/research"
)


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_raw_listing_candidates_validate() -> None:
    validator = _load_module(
        ARTIFACT_DIR / "validate_raw_listing_candidates.py",
        "pis_8dcf_validator",
    )
    artifact = json.loads((ARTIFACT_DIR / "raw_listing_candidates.json").read_text(encoding="utf-8"))

    assert {row["region"] for row in artifact["candidates"]} == {
        "Bas-Saint-Laurent",
        "Charlevoix",
        "Saguenay-Lac-Saint-Jean",
    }
    assert any(row["discovery_strategy"] == "PROXY_2026_DATES" for row in artifact["candidates"])
    assert sum(1 for row in artifact["candidates"] if row["region"] == "Charlevoix") >= 3
    assert any(
        row["region"] == "Charlevoix" and row["discovery_strategy"] == "PROXY_2026_DATES"
        for row in artifact["candidates"]
    )
    assert validator.validate_artifact(artifact) == []


def test_validator_rejects_non_quebec_candidate() -> None:
    validator = _load_module(
        ARTIFACT_DIR / "validate_raw_listing_candidates.py",
        "pis_8dcf_validator_non_quebec",
    )
    artifact = json.loads((ARTIFACT_DIR / "raw_listing_candidates.json").read_text(encoding="utf-8"))
    artifact["candidates"][0]["province"] = "Ontario"

    issues = validator.validate_artifact(artifact)

    assert any("outside Quebec" in issue for issue in issues)


def test_validator_rejects_missing_lake_reference() -> None:
    validator = _load_module(
        ARTIFACT_DIR / "validate_raw_listing_candidates.py",
        "pis_8dcf_validator_missing_lake",
    )
    artifact = json.loads((ARTIFACT_DIR / "raw_listing_candidates.json").read_text(encoding="utf-8"))
    artifact["candidates"][0]["lake_reference"] = "   "

    issues = validator.validate_artifact(artifact)

    assert any("missing lake_reference" in issue for issue in issues)


def test_validator_rejects_unknown_animals_without_uncertainty_flag() -> None:
    validator = _load_module(
        ARTIFACT_DIR / "validate_raw_listing_candidates.py",
        "pis_8dcf_validator_missing_animals_flag",
    )
    artifact = json.loads((ARTIFACT_DIR / "raw_listing_candidates.json").read_text(encoding="utf-8"))
    artifact["candidates"][6]["uncertainty_flags"] = ["NO_2027_CALENDAR_EVIDENCE"]

    issues = validator.validate_artifact(artifact)

    assert any("ANIMALS_POLICY_UNCLEAR" in issue for issue in issues)


def test_validator_rejects_proxy_candidate_without_proxy_provenance_signal() -> None:
    validator = _load_module(
        ARTIFACT_DIR / "validate_raw_listing_candidates.py",
        "pis_8dcf_validator_proxy_provenance",
    )
    artifact = json.loads((ARTIFACT_DIR / "raw_listing_candidates.json").read_text(encoding="utf-8"))
    artifact["candidates"][3]["availability_signal"] = "Calendar appears open."

    issues = validator.validate_artifact(artifact)

    assert any("proxy-date provenance" in issue for issue in issues)


def test_validator_rejects_indirect_lake_candidate_without_indirect_flag() -> None:
    validator = _load_module(
        ARTIFACT_DIR / "validate_raw_listing_candidates.py",
        "pis_8dcf_validator_indirect_lake",
    )
    artifact = json.loads((ARTIFACT_DIR / "raw_listing_candidates.json").read_text(encoding="utf-8"))
    artifact["candidates"][7]["uncertainty_flags"] = ["NO_2027_CALENDAR_EVIDENCE"]

    issues = validator.validate_artifact(artifact)

    assert any("LAKE_ACCESS_INDIRECT" in issue for issue in issues)


def test_validator_rejects_missing_observed_timestamp() -> None:
    validator = _load_module(
        ARTIFACT_DIR / "validate_raw_listing_candidates.py",
        "pis_8dcf_validator_missing_observed_utc",
    )
    artifact = json.loads((ARTIFACT_DIR / "raw_listing_candidates.json").read_text(encoding="utf-8"))
    artifact["candidates"][8]["observed_utc"] = "2026-05-02 21:55:00"

    issues = validator.validate_artifact(artifact)

    assert any("missing observed_utc" in issue for issue in issues)
