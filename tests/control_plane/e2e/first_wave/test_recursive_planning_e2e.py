from __future__ import annotations

from infra.controlplane import recursive_planning_family_proof as proof

from .helpers.contracts import ScenarioContract, assert_scenario_contract
from .helpers.plane import configure_plane_routes, get_json


CONTRACT = ScenarioContract(
    scenario_id="recursive_planning_e2e",
    operator_job="Inspect recursive planning output in the plane without letting dry-run planning silently turn into live descendant execution.",
    capability="Recursive planning records durable dry-run receipts, blocks unsafe recursion cases fail-closed, and exposes the resulting plan truth through the plane inspection surface.",
    durable_truth=(
        "recursive planning receipts",
        "recursive planning latest projections",
        "non-live admission receipt",
        "plane recursive planning inspection payloads",
    ),
    manual_repair_eliminated="Manually reconstructing whether recursive planning stayed non-live, which boundary blocked unsafe plans, and whether the plane agrees with durable receipts.",
    primary_intent_sheets=("IS-11",),
    allowed_shortcuts=(
        "fixture seeding before scenario start",
        "deterministic recursive planning proof fixtures",
        "temp-repo plane inspection execution",
    ),
    forbidden_shortcuts=(
        "direct state mutation after scenario start to fake success",
        "bypassing non-live admission for the claimed recursive planning path",
        "writing projection records manually",
    ),
)


def test_recursive_planning_e2e(first_wave_root, monkeypatch) -> None:
    assert_scenario_contract(CONTRACT)
    report = proof.collect_recursive_planning_family_proof(repo_root=first_wave_root)
    assert report["all_cases_met"] is True
    assert report["listing_total_plans"] == 4

    happy = report["cases"]["happy_path_non_live_admitted"]
    cycle = report["cases"]["dependency_cycle_rejected"]
    depth = report["cases"]["depth_cap_rejected"]
    budget = report["cases"]["budget_stop_rejected"]
    live_launch = report["cases"]["live_launch_forbidden"]

    assert happy["admission_ok"] is True
    assert happy["projection_status"] == "non_live_admitted"
    assert happy["phase_id"] == "phase_recursive_plan"
    assert happy["blocking_reason_codes"] == ["recursive_planning_dry_run_only"]
    assert cycle["projection_status"] == "blocked"
    assert "dependency_cycle_detected" in cycle["blocking_reason_codes"]
    assert depth["projection_status"] == "blocked"
    assert "depth_cap_exceeded" in depth["blocking_reason_codes"]
    assert budget["projection_status"] == "blocked"
    assert "child_budget_exceeds_spendable" in budget["blocking_reason_codes"]
    assert live_launch["admission_ok"] is False
    assert "live_recursive_launch_forbidden" in live_launch["error_codes"]

    cfg = configure_plane_routes(repo_root=first_wave_root, monkeypatch=monkeypatch)
    list_status, list_payload = get_json(
        cfg,
        "/api/admin/recursive_planning/inspection?binding_type=ledger_uid&binding_id=PIS-20260409-153903Z-7C65",
    )
    assert list_status == 200
    assert list_payload["schema_version"] == "recursive_planning_inspection_list_v1"
    assert list_payload["total_plans"] == 4
    projected = {row["plan_id"]: row["status"] for row in list_payload["projections"]}
    assert projected == {
        "plan_recursive_budget_001": "blocked",
        "plan_recursive_cycle_001": "blocked",
        "plan_recursive_depth_001": "blocked",
        "plan_recursive_happy_001": "non_live_admitted",
    }

    detail_status, detail_payload = get_json(
        cfg,
        "/api/admin/recursive_planning/inspection?plan_id=plan_recursive_happy_001",
    )
    assert detail_status == 200
    assert detail_payload["schema_version"] == "recursive_planning_inspection_view_v1"
    projection = detail_payload["projection"]
    assert projection["status"] == "non_live_admitted"
    assert projection["planned_graph"]["node_count"] == 3
    assert projection["planned_graph"]["dependency_edge_count"] == 3
    assert projection["non_live_admission"]["campaign_context"]["phase_id"] == "phase_recursive_plan"
    assert projection["non_live_admission"]["scheduler_admission"]["launch_allowed"] is False
    assert projection["blocking_reason_codes"] == ["recursive_planning_dry_run_only"]
    assert len(projection["receipt_paths"]) >= 3
    assert any(path.endswith("compiled_work_order_envelope.json") for path in projection["receipt_paths"])
