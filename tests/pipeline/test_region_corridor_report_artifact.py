from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ARTIFACT_DIR = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/05/"
    "PIS-20260502-141914Z-BBF4__define-the-quebec-search-corridor-and-candidate-lake-regions/"
    "artifacts/research"
)


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_region_corridor_report_validates() -> None:
    validator = _load_module(
        ARTIFACT_DIR / "validate_region_corridor_report.py", "pis_bbf4_validator"
    )
    artifact = json.loads((ARTIFACT_DIR / "region_corridor_report.json").read_text(encoding="utf-8"))

    assert [row["region"] for row in artifact["included_regions"]] == [
        "Bas-Saint-Laurent",
        "Charlevoix",
        "Saguenay-Lac-Saint-Jean",
    ]
    assert validator.validate_artifact(artifact) == []


def test_validator_rejects_non_quebec_region() -> None:
    validator = _load_module(
        ARTIFACT_DIR / "validate_region_corridor_report.py", "pis_bbf4_validator_non_quebec"
    )
    artifact = json.loads((ARTIFACT_DIR / "region_corridor_report.json").read_text(encoding="utf-8"))
    artifact["included_regions"][0]["province"] = "New Brunswick"

    issues = validator.validate_artifact(artifact)

    assert any("non-Quebec province" in issue for issue in issues)


def test_validator_rejects_raw_discovery_region_drift() -> None:
    validator = _load_module(
        ARTIFACT_DIR / "validate_region_corridor_report.py", "pis_bbf4_validator_drift"
    )
    artifact = json.loads((ARTIFACT_DIR / "region_corridor_report.json").read_text(encoding="utf-8"))
    artifact["included_regions"] = artifact["included_regions"][:-1]

    issues = validator.validate_artifact(artifact)

    assert any("included_regions do not match raw discovery allowed_regions" in issue for issue in issues)
