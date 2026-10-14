from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from infra.webui.ledger.ledger_manager import ChangeLedger

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = (
    ChangeLedger(repo_root=REPO_ROOT).get_item_dir("PIS-20260502-141937Z-FA35")
    / "artifacts"
    / "intent_validation"
)


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_builder_output_validates_and_tracks_soft_uncertainty() -> None:
    builder = _load_module(ARTIFACT_DIR / "build_intent_validation_artifact.py", "pis_fa35_builder")
    validator = _load_module(ARTIFACT_DIR / "validate_intent_validation_artifact.py", "pis_fa35_validator")

    artifact = builder.build_artifact()

    assert artifact["validation_outcome"] == "passed"
    assert artifact["hard_failures"] == []
    assert any("August 2027 availability remains CONTACT_REQUIRED" in row for row in artifact["soft_uncertainties"])
    assert any(row["scenario_id"] == "negative_animals_prohibited_shortlist_rejected" for row in artifact["scenario_results"])
    assert validator.validate_artifact(artifact) == []


def test_negative_control_rejects_animals_prohibited_shortlist_swap() -> None:
    builder = _load_module(ARTIFACT_DIR / "build_intent_validation_artifact.py", "pis_fa35_builder_negative")

    scenario = builder.run_negative_prohibited_shortlist_check()

    assert scenario["outcome"] == "passed"
    assert "Chalet Laroche" in "\n".join(scenario["observations"])


def test_validator_rejects_artifact_without_negative_path_proof() -> None:
    validator = _load_module(ARTIFACT_DIR / "validate_intent_validation_artifact.py", "pis_fa35_validator_negative")
    artifact = json.loads((ARTIFACT_DIR / "quebec_family_lake_house_intent_validation.json").read_text(encoding="utf-8"))
    artifact["scenario_results"] = [row for row in artifact["scenario_results"] if row["scenario_kind"] != "negative_path"]

    issues = validator.validate_artifact(artifact)

    assert "scenario_results is missing a negative_path scenario" in issues
