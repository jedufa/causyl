from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane.static_verification.static_verification_planner_artifacts import PlannerArtifactError, build_artifact_bundle, build_operator_entrypoint


REPO_ROOT = Path(__file__).resolve().parents[2]
R5U6_UID = "PIS-20260413-160258Z-R5U6"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_dir(uid: str) -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{uid}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing ledger directory for {uid}")


def _sample_bundle() -> dict:
    return {
        "run_identity": {
            "repo_commit_sha": "abc123",
            "selected_block_selector": "infra/controlplane/sched/runtime.py::scheduler_v2",
            "selected_block_fingerprint": "block-fp-1",
            "planning_mode": "dense_test_pack",
            "planner_generator_version": "planner-v1",
            "rerun_fingerprint": "rerun-fp-1",
        },
        "artifact_schema_versions": {
            "qualification": "qualified_block_boundary_v1",
            "extraction": "complex_block_extraction_output_v1",
            "threat_matrix": "static_verification_threat_matrix_v1",
            "generation": "dense_test_pack_output_v1",
        },
        "traceability_rows": [
            {
                "extracted_fact_id": "fact-1",
                "threat_family_id": "restart-family",
                "generated_output_id": "restart-family::scenario_01",
                "claim_blocker": "planning_output_only_until_executed",
            }
        ],
        "review_gate": {
            "state": "draft_generated",
            "review_decision": "awaiting_operator_review",
            "planning_only_warning": "Planning output is not proof of runtime robustness.",
            "maximum_supported_claim": "planning_quality_improved_only",
        },
        "plan_freshness": {
            "status": "current",
            "status_message": "Current for the selected selector and rerun fingerprint.",
            "invalidation_triggers": [
                "repo commit changed",
                "block selector changed",
                "planner generator version changed",
            ],
        },
        "artifact_locations": {
            "bundle_root": "artifacts/planning/runs/rerun-fp-1/",
            "current_manifest": "artifacts/planning/runs/rerun-fp-1/current_manifest.json",
        },
    }


def test_r5u6_meta_points_to_required_artifact_contracts() -> None:
    meta = _load_json(_find_dir(R5U6_UID) / "meta.json")
    assert meta["required_evidence"] == [
        "artifacts/planning/planner_artifact_contract_v1.json",
        "artifacts/planning/planner_entrypoint_contract_v1.json",
        "artifacts/verification/status_reconcile_verification.json",
    ]


def test_r5u6_contracts_require_traceability_and_entrypoint_warnings() -> None:
    base = _find_dir(R5U6_UID) / "artifacts" / "planning"
    artifact_contract = _load_json(base / "planner_artifact_contract_v1.json")
    entrypoint_contract = _load_json(base / "planner_entrypoint_contract_v1.json")
    assert artifact_contract["quality_rules"]["traceability_rows_required"] is True
    assert "claim_ceiling_warning" in entrypoint_contract["required_fields"]
    assert "maximum_supported_claim" in entrypoint_contract["required_fields"]
    assert "plan_freshness_status" in entrypoint_contract["required_fields"]


def test_r5u6_bundle_builder_preserves_provenance_and_traceability() -> None:
    bundle = build_artifact_bundle(_sample_bundle())
    assert bundle["run_identity"]["selected_block_selector"].endswith("scheduler_v2")
    assert bundle["traceability_rows"][0]["claim_blocker"] == "planning_output_only_until_executed"
    entrypoint = build_operator_entrypoint({"artifact_bundle": _sample_bundle()})
    assert entrypoint["claim_ceiling_warning"] == "Planning output is not proof of runtime robustness."
    assert entrypoint["maximum_supported_claim"] == "planning_quality_improved_only"
    assert entrypoint["plan_freshness_status"] == "current"


def test_r5u6_bundle_builder_fails_closed_without_traceability() -> None:
    bad = _sample_bundle()
    bad["traceability_rows"] = []
    with pytest.raises(PlannerArtifactError) as excinfo:
        build_artifact_bundle(bad)
    assert excinfo.value.code == "traceability_rows_missing"


def test_r5u6_bundle_builder_requires_stale_reason_for_superseded_plan() -> None:
    bad = _sample_bundle()
    bad["plan_freshness"] = {
        "status": "superseded",
        "status_message": "Superseded by a rerun against a newer selector fingerprint.",
        "invalidation_triggers": ["selected block fingerprint changed"],
    }
    with pytest.raises(PlannerArtifactError) as excinfo:
        build_artifact_bundle(bad)
    assert excinfo.value.code == "plan_freshness_stale_reason_missing"
