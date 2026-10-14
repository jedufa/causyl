from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane.static_verification.static_verification_planner_workflow import PlannerWorkflowError, build_phase_a_workflow_view


REPO_ROOT = Path(__file__).resolve().parents[2]
Z4C5_UID = "PIS-20260413-161930Z-Z4C5"


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


def _sample_workflow() -> dict:
    return {
        "block_selector": "infra/controlplane/sched/runtime.py::scheduler_v2",
        "mode_selector": "auto",
        "optional_profile_controls": ["harshness=high"],
        "maximum_supported_claim": "planning_quality_improved_only",
        "claim_ceiling_warning": "Static planning cannot prove runtime robustness.",
        "planning_only_warning": "This Phase A run only produces planning artifacts.",
        "qualification_decision": "expanded",
        "run_id": "planner-run-1",
        "rerun_fingerprint": "rerun-fp-1",
        "artifact_locations": {"current_manifest": "artifacts/planning/runs/rerun-fp-1/current_manifest.json"},
        "plan_freshness": {
            "status": "current",
            "status_message": "Current for the selected selector and rerun fingerprint.",
            "invalidation_triggers": [
                "repo commit changed",
                "block selector changed",
                "planner generator version changed",
            ],
        },
        "stale_plan_invalidation": "invalidate when commit, selector, or generator version changes",
        "visible_phases": [
            "qualify_block",
            "extract_truth_seams",
            "build_threat_matrix",
            "generate_plan",
            "review_gate",
            "publish_artifacts",
        ],
    }


def test_z4c5_meta_points_to_required_workflow_contract() -> None:
    meta = _load_json(_find_dir(Z4C5_UID) / "meta.json")
    assert meta["required_evidence"] == [
        "artifacts/planning/planner_frontend_workflow_contract_v1.json",
        "artifacts/verification/status_reconcile_verification.json",
    ]


def test_z4c5_workflow_contract_requires_visible_phases_and_warnings() -> None:
    contract = _load_json(_find_dir(Z4C5_UID) / "artifacts" / "planning" / "planner_frontend_workflow_contract_v1.json")
    assert contract["required_visible_phases"] == [
        "qualify_block",
        "extract_truth_seams",
        "build_threat_matrix",
        "generate_plan",
        "review_gate",
        "publish_artifacts",
    ]
    assert "planning_only_warning" in contract["required_warnings"]
    assert "maximum_supported_claim" in contract["required_runtime_fields"]
    assert "plan_freshness" in contract["required_runtime_fields"]


def test_z4c5_workflow_builder_exposes_accepted_expanded_or_rejected_truth() -> None:
    view = build_phase_a_workflow_view(_sample_workflow())
    assert view["qualification_decision"] == "expanded"
    assert view["run_id"] == "planner-run-1"
    assert view["maximum_supported_claim"] == "planning_quality_improved_only"
    assert view["plan_freshness"]["status"] == "current"


def test_z4c5_workflow_builder_rejects_missing_phase_or_invalid_decision() -> None:
    bad = _sample_workflow()
    bad["visible_phases"] = ["qualify_block", "generate_plan"]
    with pytest.raises(PlannerWorkflowError) as excinfo:
        build_phase_a_workflow_view(bad)
    assert excinfo.value.code == "visible_phases_invalid"


def test_z4c5_workflow_builder_rejects_stronger_claim_ceiling() -> None:
    bad = _sample_workflow()
    bad["maximum_supported_claim"] = "scenario_pack_structurally_ready"
    with pytest.raises(PlannerWorkflowError) as excinfo:
        build_phase_a_workflow_view(bad)
    assert excinfo.value.code == "maximum_supported_claim_invalid"


def test_z4c5_workflow_builder_requires_stale_reason_for_invalidated_plan() -> None:
    bad = _sample_workflow()
    bad["plan_freshness"] = {
        "status": "stale",
        "status_message": "Stale because the selected block fingerprint moved.",
        "invalidation_triggers": ["selected block fingerprint changed"],
    }
    with pytest.raises(PlannerWorkflowError) as excinfo:
        build_phase_a_workflow_view(bad)
    assert excinfo.value.code == "plan_freshness_stale_reason_missing"
