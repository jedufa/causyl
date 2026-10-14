from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane.verification_workflow_launch import (
    VerificationWorkflowLaunchError,
    _next_wave_followup_rows,
    _build_child_project_runner_spec,
    _build_closeout_project_runner_spec,
    create_verification_launch,
    run_verification_campaign_closeout,
    run_verification_scheduler_packet,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _selected_block_summary() -> dict:
    return {
        "schema_version": "verification_workflow_selected_block_summary_v1",
        "block_ref": "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py",
        "block_label": "scheduler launch handoff",
        "qualification_verdict": "expand",
        "operator_goal": "stress one real scheduler-driven verification workflow slice",
        "risk_unit_summary": "scheduler launch plus the adjacent queue-claim seam is the smallest honest unit",
        "qualification_basis": [
            "the launch handoff is the operator-facing claim under attack",
            "the queue-claim seam can change whether launch truth is honest",
        ],
        "included_paths": [
            "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py",
            "runtime/state/scheduler_watchdog/",
        ],
        "adjacent_truth_seams": [
            {
                "seam_id": "scheduler_claim_state",
                "relationship_to_block": "queue claim and active-run materialization",
                "disposition": "included",
                "note": "Needed so launch truth does not ignore claimed-but-unmaterialized runs.",
            }
        ],
        "expansion_notes": [
            "Include the queue-claim seam because it can falsify launch truth without changing the operator-selected file alone."
        ],
        "rejection_reasons": [],
        "bounded_operator_summary": (
            "The plane will launch verification planning for scheduler launch handoff with the named adjacent "
            "seams included before execution. This is a launch contract only; it does not prove runtime "
            "behavior, completion, or repair yield."
        ),
        "what_this_does_not_prove": (
            "The summary only bounds what the workflow intends to verify next. It does not prove runtime "
            "correctness, scheduler delivery, completion, or repair effectiveness."
        ),
        "stale_summary_policy": (
            "Treat the selected-block summary as stale and require recomputation if the request fingerprint, "
            "qualification inputs, or adjacent-seam review changes."
        ),
        "launch_readiness": True,
        "summary_fingerprint": "9cb9b473197a90e09fcee7a01023ca4b450b23862a78df4d21aa5aab59e3c932",
    }


def _anchor_payload() -> dict:
    return {
        "scenarios": [
            {
                "scenario_id": "scenario::active-busy-masks-storage-failure",
                "execution_class": "runnable_now",
                "scenario_summary": "storage failure must not be hidden by active_busy",
                "required_evidence": ["job_receipt", "finding_receipt"],
                "repo_commit_sha": "abc123",
                "repair_triggerable": True,
            }
        ],
        "raw_findings": [
            {
                "finding_id": "finding::1",
                "truth_boundary": "watchdog_truth",
                "symptom_class": "unsafe_action",
                "operator_claim_under_attack": "safe repair offered",
                "severity_hint": "critical",
                "production_reachability_hint": "reachable_in_normal_operation",
                "affected_truths": ["watchdog_truth"],
                "repair_signal": "repair_worthy",
            }
        ],
        "budget": {"max_budget_units": 4},
        "actual": {
            "spent_budget_units": 2,
            "repeated_cluster_ratio": 0.0,
            "new_signal_ratio": 0.8,
            "dut_improvement_delta": 0.2,
            "repair_confidence": 0.7,
            "risk_score": 0.2,
            "completed_iterations": 1,
        },
        "baseline_health_snapshot": {"open_bug_classes": 3, "critical_open": 1},
        "post_campaign_health_snapshot": {"open_bug_classes": 2, "critical_open": 0},
        "reduced_bug_classes": ["active_busy_masking"],
        "unresolved_bug_classes": ["long_run_unsupervised_unproven"],
        "residual_risk": "still_not_safe_unattended",
        "blocked_claims": ["broad_robustness_unproven"],
    }


def _live_style_anchor_payload() -> dict:
    return {
        "scenario_id": "scenario::scheduler-launch-claim-materialization-contradiction",
        "scenario_family": "scheduler_launch_truth",
        "proof_target": (
            "launch admission must fail closed when scheduler launch truth contradicts claim or active-run "
            "materialization state"
        ),
        "execution_substrate": "verifier",
        "negative_control": "seed a contradictory scheduler launch state and prove the workflow does not downgrade it into clean progress",
        "disqualifying_incident": "launch accepted despite contradictory scheduler truth",
        "required_evidence": [
            "compiled verifier job",
            "normalized defect candidate",
            "bounded repair candidate",
            "targeted revalidation plan",
            "dut delta closeout",
        ],
        "runtime_class": "bounded_anchor",
        "repair_surface": "scheduler_launch_truth",
    }


def test_verification_workflow_launch_persists_durable_request_and_runs_anchor(tmp_path: Path) -> None:
    result = create_verification_launch(
        {
            "request_id": "verification_workflow_launch_demo",
            "verification_goal": "Launch one bounded scheduler-facing verification workflow slice.",
            "requested_output_mode": "child_pis_family",
            "max_iterations": 4,
            "operator_note": "Use the bounded family path first.",
            "selected_block_summary": _selected_block_summary(),
            "phase_b_anchor_payload": _anchor_payload(),
        },
        repo_root=REPO_ROOT,
    )
    request_path = REPO_ROOT / result["durable_request_path"]
    review_path = REPO_ROOT / result["review_packet_path"]
    scheduler_path = REPO_ROOT / result["scheduler_packet_path"]

    assert request_path.exists()
    assert review_path.exists()
    assert scheduler_path.exists()

    run_result = run_verification_scheduler_packet(result["scheduler_packet"], repo_root=REPO_ROOT)
    campaign_path = REPO_ROOT / run_result["campaign_record_path"]
    campaign = _load_json(campaign_path)
    assert campaign["execution_status"] == "completed"
    assert campaign["anchor_report"]["dut_health_delta"]["net_dut_delta"] == "improved"
    assert campaign["anchor_report"]["compiled_jobs"][0]["job_id"].startswith("verify::")


def test_verification_workflow_launch_normalizes_live_single_scenario_anchor_payload() -> None:
    result = create_verification_launch(
        {
            "request_id": "verification_workflow_launch_live_shape",
            "verification_goal": "Launch one bounded scheduler verification workflow using the live single-scenario payload shape.",
            "requested_output_mode": "dense_test_pack",
            "max_iterations": 4,
            "selected_block_summary": _selected_block_summary(),
            "phase_b_anchor_payload": _live_style_anchor_payload(),
        },
        repo_root=REPO_ROOT,
    )

    run_result = run_verification_scheduler_packet(result["scheduler_packet"], repo_root=REPO_ROOT)
    campaign = _load_json(REPO_ROOT / run_result["campaign_record_path"])

    assert campaign["execution_status"] == "completed"
    compiled = campaign["anchor_report"]["compiled_jobs"]
    assert len(compiled) == 1
    assert compiled[0]["scenario_id"] == "scenario::scheduler-launch-claim-materialization-contradiction"
    normalized = campaign["anchor_report"]["normalized_defect_candidates"]
    assert normalized[0]["cluster_id"].startswith("scheduler_launch_truth::unsafe_action::")


def test_verification_workflow_launch_can_plan_broad_scheduler_goal_into_multi_scenario_campaign() -> None:
    result = create_verification_launch(
        {
            "request_id": "verification_workflow_broad_scheduler_goal",
            "verification_goal": "Verify the scheduler",
            "operator_note": "Broad scheduler verification request without a precomputed block summary.",
            "max_iterations": 4,
        },
        repo_root=REPO_ROOT,
    )

    planner_packet = result["planner_packet"]
    assert planner_packet is not None
    assert planner_packet["planner_family"] == "static_verification_planner_v1"
    assert planner_packet["planner_maximum_supported_claim"] == "planning_quality_improved_only"
    assert result["review_packet"]["planner_mode"] == "child_pis_family"
    assert result["durable_request"]["selected_block_summary"]["block_label"] == "scheduler verification family"
    assert result["native_family_packet"]["native_execution_family"]["family_kind"] == "scheduler_verification_family"
    assert (REPO_ROOT / result["native_family_packet_path"]).exists()

    scenarios = result["scheduler_packet"]["phase_b_anchor_payload"]["scenarios"]
    assert len(scenarios) >= 8
    scenario_ids = {row["scenario_id"] for row in scenarios}
    assert any(row.startswith("scheduler-launch-truth-family::scenario_") for row in scenario_ids)
    assert any(row.startswith("scheduler-recovery-truth-family::scenario_") for row in scenario_ids)
    assert any(row.startswith("scheduler-operator-false-calm-family::scenario_") for row in scenario_ids)
    assert any(row.startswith("adaptive-request-synthesis-") for row in scenario_ids)

    run_result = run_verification_scheduler_packet(result["scheduler_packet"], repo_root=REPO_ROOT)
    campaign = _load_json(REPO_ROOT / run_result["campaign_record_path"])
    assert campaign["planner_packet_path"] == result["planner_packet_path"]
    assert campaign["execution_status"] == "running_children"
    assert campaign["native_family_packet_path"] == result["native_family_packet_path"]
    assert len(campaign["queued_child_jobs"]) >= 8
    assert campaign["closeout_job_id"].startswith("vw_scheduler_closeout_")


def test_verification_workflow_launch_can_plan_android_agent_tabs_goal_into_harsh_multi_scenario_campaign() -> None:
    result = create_verification_launch(
        {
            "request_id": "verification_workflow_android_agent_tabs_goal",
            "verification_goal": "Very hardly verify and validate the Android agent tabs building block",
            "operator_note": "Broad Android/mobile tabs verification request.",
            "max_iterations": 4,
        },
        repo_root=REPO_ROOT,
    )

    planner_packet = result["planner_packet"]
    assert planner_packet is not None
    assert planner_packet["planner_family"] == "static_verification_planner_v1"
    assert result["durable_request"]["selected_block_summary"]["block_label"] == "Android agent tabs verification family"
    assert result["native_family_packet"]["native_execution_family"]["family_kind"] == "mobile_verification_family"
    family_packet_path = REPO_ROOT / result["native_family_packet_path"]
    family_packet = _load_json(family_packet_path)
    assert family_packet["target_family"] == "android_agent_tabs"
    assert any(node["node_id"] == "adaptive_followup_template" for node in family_packet["graph_nodes"])

    scenarios = result["scheduler_packet"]["phase_b_anchor_payload"]["scenarios"]
    assert len(scenarios) >= 20
    scenario_ids = {row["scenario_id"] for row in scenarios}
    assert any(row.startswith("android-agent-tabs-selection-family::scenario_") for row in scenario_ids)
    assert any(row.startswith("android-agent-tabs-topic-family::scenario_") for row in scenario_ids)
    assert any(row.startswith("android-agent-tabs-navigation-family::scenario_") for row in scenario_ids)
    assert any(row.startswith("android-agent-tabs-typed-mutation-family::scenario_") for row in scenario_ids)
    assert any(row.startswith("android-agent-tabs-upstream-truth-family::scenario_") for row in scenario_ids)
    assert any(row.startswith("android-agent-tabs-interruption-family::scenario_") for row in scenario_ids)
    assert any(row.startswith("adaptive-request-synthesis-") for row in scenario_ids)

    run_result = run_verification_scheduler_packet(result["scheduler_packet"], repo_root=REPO_ROOT)
    campaign = _load_json(REPO_ROOT / run_result["campaign_record_path"])
    assert campaign["execution_status"] == "running_children"
    assert campaign["native_family_packet_path"] == result["native_family_packet_path"]
    assert len(campaign["queued_child_jobs"]) >= 20
    assert campaign["closeout_job_id"].startswith("vw_android_agent_tabs_closeout_")
    assert all(row["job_id"].startswith("vw_mobile_") for row in campaign["queued_child_jobs"][:3])


def test_verification_workflow_launch_reuses_durable_request_when_fingerprint_matches() -> None:
    payload = {
        "request_id": "verification_workflow_launch_idempotent",
        "verification_goal": "Retry the same verification launch without duplicate durable records.",
        "requested_output_mode": "child_pis_family",
        "max_iterations": 2,
        "selected_block_summary": _selected_block_summary(),
        "phase_b_anchor_payload": _anchor_payload(),
    }
    first = create_verification_launch(payload, repo_root=REPO_ROOT)
    second = create_verification_launch(payload, repo_root=REPO_ROOT)
    assert first["durable_request_path"] == second["durable_request_path"]
    assert first["scheduler_packet"]["request_fingerprint"] == second["scheduler_packet"]["request_fingerprint"]


def test_verification_workflow_broad_launches_get_distinct_fingerprints_per_campaign_instance() -> None:
    first = create_verification_launch(
        {
            "request_id": "verification_workflow_broad_scheduler_goal_distinct",
            "verification_goal": "Verify the scheduler",
            "operator_note": "first broad campaign",
            "max_iterations": 4,
        },
        repo_root=REPO_ROOT,
    )
    second = create_verification_launch(
        {
            "request_id": "verification_workflow_broad_scheduler_goal_distinct",
            "verification_goal": "Verify the scheduler",
            "operator_note": "second broad campaign",
            "max_iterations": 4,
        },
        repo_root=REPO_ROOT,
    )
    assert first["durable_request"]["request_fingerprint"] != second["durable_request"]["request_fingerprint"]
    assert first["durable_request_path"] != second["durable_request_path"]


def test_verification_workflow_closeout_job_id_is_unique_per_campaign_fingerprint() -> None:
    first = _build_closeout_project_runner_spec(
        request_fingerprint="fp_one",
        request_id="verification_workflow_android_agent_tabs_goal",
        scheduler_packet_path="runtime/state/verification_workflow/scheduler_packets/fp_one.json",
        closeout_packet_path="runtime/state/verification_workflow/closeout_packets/fp_one.json",
        child_job_ids=["vw_child_a"],
        target_family="android_agent_tabs",
        native_family_packet_path="runtime/state/verification_workflow/native_family_packets/fp_one.json",
        run_at="2026-04-15T08:00:00Z",
    )
    second = _build_closeout_project_runner_spec(
        request_fingerprint="fp_two",
        request_id="verification_workflow_android_agent_tabs_goal",
        scheduler_packet_path="runtime/state/verification_workflow/scheduler_packets/fp_two.json",
        closeout_packet_path="runtime/state/verification_workflow/closeout_packets/fp_two.json",
        child_job_ids=["vw_child_b"],
        target_family="android_agent_tabs",
        native_family_packet_path="runtime/state/verification_workflow/native_family_packets/fp_two.json",
        run_at="2026-04-15T08:00:00Z",
    )

    assert first["job_id"] != second["job_id"]


def test_verification_workflow_launch_fails_closed_on_reject_verdict_or_overclaiming_summary() -> None:
    rejected = _selected_block_summary()
    rejected["qualification_verdict"] = "reject"
    rejected["rejection_reasons"] = ["boundary remains too ambiguous to launch honestly"]
    with pytest.raises(VerificationWorkflowLaunchError) as excinfo:
        create_verification_launch(
            {
                "request_id": "reject-demo",
                "verification_goal": "should fail",
                "selected_block_summary": rejected,
            },
            repo_root=REPO_ROOT,
        )
    assert excinfo.value.code == "selected_block_rejected"

    overclaiming = _selected_block_summary()
    overclaiming["bounded_operator_summary"] = "This proves runtime correctness and safe unattended repair."
    with pytest.raises(VerificationWorkflowLaunchError) as excinfo2:
        create_verification_launch(
            {
                "request_id": "overclaim-demo",
                "verification_goal": "should fail",
                "selected_block_summary": overclaiming,
            },
            repo_root=REPO_ROOT,
        )
    assert excinfo2.value.code == "overclaiming_selected_block_summary"


def test_verification_workflow_launch_rejects_invalid_output_mode_and_iteration_range() -> None:
    with pytest.raises(VerificationWorkflowLaunchError) as excinfo:
        create_verification_launch(
            {
                "request_id": "bad-mode",
                "verification_goal": "bad mode",
                "requested_output_mode": "runtime_magic",
                "selected_block_summary": _selected_block_summary(),
            },
            repo_root=REPO_ROOT,
        )
    assert excinfo.value.code == "invalid_requested_output_mode"

    with pytest.raises(VerificationWorkflowLaunchError) as excinfo2:
        create_verification_launch(
            {
                "request_id": "bad-iter",
                "verification_goal": "bad iterations",
                "max_iterations": 8,
                "selected_block_summary": _selected_block_summary(),
            },
            repo_root=REPO_ROOT,
        )
    assert excinfo2.value.code == "max_iterations_out_of_range"


def test_verification_workflow_launch_fails_closed_on_unsupported_broad_goal_without_block_summary() -> None:
    with pytest.raises(VerificationWorkflowLaunchError) as excinfo:
        create_verification_launch(
            {
                "request_id": "verify_everything_demo",
                "verification_goal": "Verify the whole plane",
            },
            repo_root=REPO_ROOT,
        )
    assert excinfo.value.code == "unsupported_broad_verification_goal"


def test_scheduler_packet_fails_closed_when_anchor_payload_or_request_record_is_missing() -> None:
    result = create_verification_launch(
        {
            "request_id": "verification_workflow_launch_missing_anchor",
            "verification_goal": "Create request only.",
            "selected_block_summary": _selected_block_summary(),
            "phase_b_anchor_payload": _anchor_payload(),
        },
        repo_root=REPO_ROOT,
    )
    packet = dict(result["scheduler_packet"])
    packet["phase_b_anchor_payload"] = {}
    with pytest.raises(VerificationWorkflowLaunchError) as excinfo:
        run_verification_scheduler_packet(packet, repo_root=REPO_ROOT)
    assert excinfo.value.code == "missing_phase_b_anchor_payload"

    packet2 = dict(result["scheduler_packet"])
    packet2["request_record_path"] = "runtime/state/verification_workflow/requests/missing.json"
    with pytest.raises(VerificationWorkflowLaunchError) as excinfo2:
        run_verification_scheduler_packet(packet2, repo_root=REPO_ROOT)
    assert excinfo2.value.code == "missing_request_record"


def test_adaptive_followups_inherit_health_snapshots_and_budget_from_completed_android_wave(tmp_path: Path) -> None:
    repo = tmp_path
    root = repo / "runtime" / "state" / "verification_workflow"
    (root / "scenario_packets").mkdir(parents=True, exist_ok=True)
    (root / "scenario_runs").mkdir(parents=True, exist_ok=True)
    fingerprint = "fp_android_wave"
    scenario_packet_path = root / "scenario_packets" / f"{fingerprint}__01.json"
    scenario_run_path = root / "scenario_runs" / f"{fingerprint}__01.json"

    scenario_packet = {
        "schema_version": "verification_workflow_scenario_packet_v1",
        "request_fingerprint": fingerprint,
        "request_id": "android_wave_demo",
        "scenario_index": 1,
        "wave_index": 1,
        "target_family": "android_agent_tabs",
        "native_probe_kind": "mobile_tab_topic_probe",
        "scenario": {
            "scenario_id": "android-agent-tabs-topic-family::scenario_03",
            "target_family": "android_agent_tabs",
            "native_probe_kind": "mobile_tab_topic_probe",
            "execution_class": "runnable_now",
            "scenario_summary": "topic truth probe",
            "required_evidence": ["session_rows_snapshot"],
        },
        "budget": {"max_budget_units": 20},
        "actual": {"spent_budget_units": 8},
        "baseline_health_snapshot": {"open_bug_classes": 8, "critical_open": 6},
        "post_campaign_health_snapshot": {"open_bug_classes": 6, "critical_open": 5},
        "reduced_bug_classes": ["android-agent-tabs-topic-family"],
        "unresolved_bug_classes": ["android-agent-tabs-topic-family::needs_follow_up"],
        "residual_risk": "android_followup_required",
        "blocked_claims": ["android_agent_tabs_health_unproven_until_live_campaign_execution"],
    }
    scenario_packet_path.write_text(json.dumps(scenario_packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    scenario_run = {
        "schema_version": "verification_workflow_scenario_run_v1",
        "request_fingerprint": fingerprint,
        "request_id": "android_wave_demo",
        "scenario_id": "android-agent-tabs-topic-family::scenario_03",
        "wave_index": 1,
        "anchor_report": {
            "normalized_defect_candidates": [
                {
                    "cluster_id": "topic_identity::false_calm::Each agent tab shows its own durable topic identity and not another tab's.",
                    "truth_boundary": "topic_identity",
                    "symptom_class": "false_calm",
                    "severity": "high",
                    "affected_truths": ["session rows", "tab topic payload"],
                }
            ],
            "routed_repairs": [{"repair_candidate_id": "repair::topic_identity"}],
            "dut_health_delta": {
                "baseline_health_snapshot": {"open_bug_classes": 8, "critical_open": 6},
                "post_campaign_health_snapshot": {"open_bug_classes": 6, "critical_open": 5},
                "reduced_bug_classes": ["android-agent-tabs-topic-family"],
                "unresolved_bug_classes": ["android-agent-tabs-topic-family::needs_follow_up"],
                "residual_risk": "android_followup_required",
                "blocked_claims": ["android_agent_tabs_health_unproven_until_live_campaign_execution"],
            },
        },
    }
    scenario_run_path.write_text(json.dumps(scenario_run, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    followups = _next_wave_followup_rows(
        request_fingerprint=fingerprint,
        wave_index=1,
        child_jobs=[{"scenario_packet_path": str(scenario_packet_path.relative_to(repo))}],
        repo=repo,
    )

    assert len(followups) == 1
    followup = followups[0]
    assert followup["budget"] == {"max_budget_units": 20}
    assert followup["actual"] == {"spent_budget_units": 8}
    assert followup["baseline_health_snapshot"] == {"open_bug_classes": 8, "critical_open": 6}
    assert followup["post_campaign_health_snapshot"] == {"open_bug_classes": 6, "critical_open": 5}
    assert followup["reduced_bug_classes"] == ["android-agent-tabs-topic-family"]
    assert followup["unresolved_bug_classes"] == ["android-agent-tabs-topic-family::needs_follow_up"]
    assert followup["residual_risk"] == "android_followup_required"
    assert followup["blocked_claims"] == ["android_agent_tabs_health_unproven_until_live_campaign_execution"]
    assert followup["scenario"]["native_probe_kind"] == "mobile_adaptive_followup_probe"


def test_adaptive_scenario_run_hydrates_missing_health_snapshots_from_source_scenario(tmp_path: Path) -> None:
    repo = tmp_path
    root = repo / "runtime" / "state" / "verification_workflow"
    (root / "scenario_packets").mkdir(parents=True, exist_ok=True)
    (root / "scenario_runs").mkdir(parents=True, exist_ok=True)
    fingerprint = "fp_hydrate_android_wave"

    source_packet_path = root / "scenario_packets" / f"{fingerprint}__01.json"
    source_run_path = root / "scenario_runs" / f"{fingerprint}__01.json"
    adaptive_packet_path = root / "scenario_packets" / f"{fingerprint}__02.json"

    source_packet = {
        "schema_version": "verification_workflow_scenario_packet_v1",
        "request_fingerprint": fingerprint,
        "request_id": "android_wave_demo",
        "scenario_index": 1,
        "wave_index": 1,
        "target_family": "android_agent_tabs",
        "native_probe_kind": "mobile_tab_navigation_probe",
        "scenario": {
            "scenario_id": "android-agent-tabs-navigation-family::scenario_03",
            "target_family": "android_agent_tabs",
            "native_probe_kind": "mobile_tab_navigation_probe",
            "execution_class": "runnable_now",
            "scenario_summary": "navigation truth probe",
            "required_evidence": ["navigation_payload_snapshot"],
            "repair_triggerable": True,
        },
        "raw_finding": {
            "finding_id": "finding::android-agent-tabs-navigation-family",
            "truth_boundary": "navigation_truth",
            "symptom_class": "unsafe_action",
            "operator_claim_under_attack": "Chat, backlog, execution, and audit surfaces preserve the selected tab context.",
            "severity_hint": "critical",
            "production_reachability_hint": "reachable_in_normal_operation",
            "affected_truths": ["selected binding", "navigation payload"],
            "repair_signal": "repair_worthy",
        },
        "budget": {"max_budget_units": 20},
        "actual": {"spent_budget_units": 8},
        "baseline_health_snapshot": {"open_bug_classes": 8, "critical_open": 6},
        "post_campaign_health_snapshot": {"open_bug_classes": 6, "critical_open": 5},
        "reduced_bug_classes": ["android-agent-tabs-navigation-family"],
        "unresolved_bug_classes": ["android-agent-tabs-navigation-family::needs_follow_up"],
        "residual_risk": "android_followup_required",
        "blocked_claims": ["android_agent_tabs_health_unproven_until_live_campaign_execution"],
    }
    source_packet_path.write_text(json.dumps(source_packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    source_run = {
        "schema_version": "verification_workflow_scenario_run_v1",
        "request_fingerprint": fingerprint,
        "request_id": "android_wave_demo",
        "scenario_id": "android-agent-tabs-navigation-family::scenario_03",
        "wave_index": 1,
        "anchor_report": {
            "normalized_defect_candidates": [
                {
                    "cluster_id": "navigation_truth::unsafe_action::Chat, backlog, execution, and audit surfaces preserve the selected tab context.",
                    "truth_boundary": "navigation_truth",
                    "symptom_class": "unsafe_action",
                    "severity": "critical",
                    "affected_truths": ["selected binding", "navigation payload"],
                }
            ],
            "routed_repairs": [{"repair_candidate_id": "repair::navigation_truth"}],
            "dut_health_delta": {
                "baseline_health_snapshot": {"open_bug_classes": 8, "critical_open": 6},
                "post_campaign_health_snapshot": {"open_bug_classes": 6, "critical_open": 5},
                "reduced_bug_classes": ["android-agent-tabs-navigation-family"],
                "unresolved_bug_classes": ["android-agent-tabs-navigation-family::needs_follow_up"],
                "residual_risk": "android_followup_required",
                "blocked_claims": ["android_agent_tabs_health_unproven_until_live_campaign_execution"],
            },
        },
    }
    source_run_path.write_text(json.dumps(source_run, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    adaptive_packet = {
        "schema_version": "verification_workflow_scenario_packet_v1",
        "request_fingerprint": fingerprint,
        "request_id": "android_wave_demo",
        "scenario_index": 2,
        "wave_index": 2,
        "adaptive_root_cluster_id": "navigation_truth::unsafe_action::Chat, backlog, execution, and audit surfaces preserve the selected tab context.",
        "derived_from_scenario_id": "android-agent-tabs-navigation-family::scenario_03",
        "scenario": {
            "scenario_id": "adaptive-followup-demo::scenario_02",
            "execution_class": "runnable_now",
            "scenario_summary": "Adaptive follow-up",
            "required_evidence": ["repair_receipt"],
            "repair_triggerable": True,
        },
        "raw_finding": {
            "finding_id": "finding::adaptive-followup-demo",
            "truth_boundary": "adaptive_scheduler_truth",
            "symptom_class": "false_calm",
            "operator_claim_under_attack": "Adaptive replay must keep the contradiction visible.",
            "severity_hint": "high",
            "production_reachability_hint": "reachable_in_normal_operation",
            "affected_truths": ["navigation_truth"],
            "repair_signal": "repair_worthy",
        },
        "budget": {},
        "actual": {},
        "baseline_health_snapshot": {},
        "post_campaign_health_snapshot": {},
        "reduced_bug_classes": [],
        "unresolved_bug_classes": [],
        "residual_risk": "",
        "blocked_claims": [],
    }
    adaptive_packet_path.write_text(json.dumps(adaptive_packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    from infra.controlplane.verification_workflow_launch import run_verification_scenario_packet

    result = run_verification_scenario_packet(json.loads(adaptive_packet_path.read_text(encoding="utf-8")), repo_root=repo)
    persisted = json.loads((repo / result["scenario_result_path"]).read_text(encoding="utf-8"))
    dut_delta = persisted["anchor_report"]["dut_health_delta"]
    assert dut_delta["baseline_health_snapshot"] == {"open_bug_classes": 8, "critical_open": 6}
    assert dut_delta["post_campaign_health_snapshot"] == {"open_bug_classes": 6, "critical_open": 5}
    assert result["scenario_result"]["native_probe_kind"] == "mobile_tab_navigation_probe"


def test_campaign_closeout_resolves_completed_retry_by_scenario_id_when_older_packet_path_is_stale(tmp_path: Path) -> None:
    repo = tmp_path
    root = repo / "runtime" / "state" / "verification_workflow"
    for name in ("requests", "campaign_runs", "closeout_packets", "scenario_packets", "scenario_runs"):
        (root / name).mkdir(parents=True, exist_ok=True)

    fingerprint = "fp_closeout_retry"
    scenario_id = "adaptive-followup-29a0f83bac-c82c549bd2-family::scenario_02"
    request_path = root / "requests" / f"{fingerprint}.json"
    campaign_path = root / "campaign_runs" / f"{fingerprint}.json"
    closeout_path = root / "closeout_packets" / f"{fingerprint}__wave_02.json"
    stale_packet_path = root / "scenario_packets" / f"{fingerprint}__21.json"
    completed_packet_path = root / "scenario_packets" / f"{fingerprint}__23.json"
    completed_run_path = root / "scenario_runs" / f"{fingerprint}__23.json"

    request_path.write_text(
        json.dumps(
            {
                "normalized_request": {
                    "request_fingerprint": fingerprint,
                    "request_id": "android_wave_demo",
                    "max_iterations": 2,
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    campaign_path.write_text(
        json.dumps(
            {
                "schema_version": "verification_workflow_campaign_record_v1",
                "request_fingerprint": fingerprint,
                "request_id": "android_wave_demo",
                "execution_status": "running_children",
                "current_wave_index": 2,
                "all_child_jobs": [
                    {
                        "job_id": "vw_child_demo",
                        "scenario_id": scenario_id,
                        "scenario_packet_path": str(stale_packet_path.relative_to(repo)),
                        "wave_index": 2,
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    closeout_path.write_text(
        json.dumps(
            {
                "schema_version": "verification_workflow_campaign_closeout_packet_v1",
                "request_fingerprint": fingerprint,
                "request_id": "android_wave_demo",
                "request_record_path": str(request_path.relative_to(repo)),
                "wave_index": 2,
                "child_jobs": [
                    {
                        "job_id": "vw_child_demo",
                        "scenario_id": scenario_id,
                        "scenario_packet_path": str(stale_packet_path.relative_to(repo)),
                        "wave_index": 2,
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    stale_packet_path.write_text(
        json.dumps(
            {
                "schema_version": "verification_workflow_scenario_packet_v1",
                "request_fingerprint": fingerprint,
                "scenario_index": 21,
                "scenario": {"scenario_id": scenario_id},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    completed_packet_path.write_text(
        json.dumps(
            {
                "schema_version": "verification_workflow_scenario_packet_v1",
                "request_fingerprint": fingerprint,
                "scenario_index": 23,
                "scenario": {"scenario_id": scenario_id},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    completed_run_path.write_text(
        json.dumps(
            {
                "schema_version": "verification_workflow_scenario_run_v1",
                "request_fingerprint": fingerprint,
                "request_id": "android_wave_demo",
                "scenario_id": scenario_id,
                "wave_index": 2,
                "execution_status": "completed",
                "anchor_report": {
                    "compiled_jobs": [{"job_id": "verify::demo"}],
                    "normalized_defect_candidates": [{"cluster_id": "demo::cluster"}],
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_verification_campaign_closeout(_load_json(closeout_path), repo_root=repo)
    campaign = _load_json(repo / result["campaign_record_path"])

    assert campaign["execution_status"] == "completed"
    assert len(campaign["anchor_report"]["compiled_jobs"]) == 1
    assert campaign["child_scenarios"][0]["scenario_result_path"].endswith("__23.json")


def test_campaign_closeout_rebuilds_missing_scenario_run_from_saved_packet(tmp_path: Path) -> None:
    repo = tmp_path
    root = repo / "runtime" / "state" / "verification_workflow"
    for name in ("requests", "campaign_runs", "closeout_packets", "scenario_packets", "scenario_runs"):
        (root / name).mkdir(parents=True, exist_ok=True)

    fingerprint = "fp_closeout_rebuild"
    scenario_id = "scheduler-launch-truth-family::scenario_01"
    request_path = root / "requests" / f"{fingerprint}.json"
    closeout_path = root / "closeout_packets" / f"{fingerprint}.json"
    scenario_packet_path = root / "scenario_packets" / f"{fingerprint}__01.json"

    request_path.write_text(
        json.dumps(
            {
                "normalized_request": {
                    "request_fingerprint": fingerprint,
                    "request_id": "scheduler_demo",
                    "max_iterations": 1,
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    closeout_path.write_text(
        json.dumps(
            {
                "schema_version": "verification_workflow_campaign_closeout_packet_v1",
                "request_fingerprint": fingerprint,
                "request_id": "scheduler_demo",
                "request_record_path": str(request_path.relative_to(repo)),
                "wave_index": 1,
                "child_jobs": [
                    {
                        "job_id": "vw_child_demo",
                        "scenario_id": scenario_id,
                        "scenario_packet_path": str(scenario_packet_path.relative_to(repo)),
                        "wave_index": 1,
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    scenario_packet_path.write_text(
        json.dumps(
            {
                "schema_version": "verification_workflow_scenario_packet_v1",
                "request_fingerprint": fingerprint,
                "request_id": "scheduler_demo",
                "scenario_index": 1,
                "scenario": {
                    "scenario_id": scenario_id,
                    "execution_class": "runnable_now",
                    "scenario_summary": "scheduler launch truth remains honest",
                    "required_evidence": ["job_receipt"],
                    "repo_commit_sha": "abc123",
                    "repair_triggerable": True,
                },
                "raw_finding": {
                    "finding_id": "finding::scheduler-demo",
                    "truth_boundary": "scheduler_truth",
                    "symptom_class": "false_calm",
                    "operator_claim_under_attack": "launch truth remains honest",
                    "severity_hint": "high",
                    "production_reachability_hint": "reachable_in_normal_operation",
                    "affected_truths": ["scheduler_truth"],
                    "repair_signal": "repair_worthy",
                },
                "budget": {},
                "actual": {
                    "spent_budget_units": 1,
                    "repeated_cluster_ratio": 0.0,
                    "new_signal_ratio": 1.0,
                    "dut_improvement_delta": 0.1,
                    "repair_confidence": 0.5,
                    "risk_score": 0.2,
                    "completed_iterations": 1,
                },
                "baseline_health_snapshot": {"open_bug_classes": 3, "critical_open": 1},
                "post_campaign_health_snapshot": {"open_bug_classes": 2, "critical_open": 0},
                "reduced_bug_classes": [],
                "unresolved_bug_classes": [],
                "residual_risk": "",
                "blocked_claims": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_verification_campaign_closeout(_load_json(closeout_path), repo_root=repo)
    scenario_run = _load_json(root / "scenario_runs" / f"{fingerprint}__01.json")
    campaign = _load_json(repo / result["campaign_record_path"])

    assert scenario_run["scenario_id"] == scenario_id
    assert campaign["execution_status"] == "completed"
    assert campaign["child_scenarios"][0]["scenario_result_path"].endswith("__01.json")


def test_child_project_runner_job_ids_include_request_fingerprint() -> None:
    base_kwargs = {
        "request_id": "verification_workflow_android_agent_tabs_goal",
        "scheduler_packet_path": "runtime/state/verification_workflow/scheduler_packets/demo.json",
        "scenario_packet_path": "runtime/state/verification_workflow/scenario_packets/demo__01.json",
        "scenario_id": "android-agent-tabs-navigation-family::scenario_01",
        "target_family": "android_agent_tabs",
        "native_probe_kind": "mobile_tab_navigation_probe",
        "recommended_agent_role": "",
        "native_family_packet_path": "runtime/state/verification_workflow/native_family_packets/demo.json",
        "native_family_node_id": "scenario::android-agent-tabs-navigation-family::scenario_01",
        "wave_index": 1,
        "run_at": "2026-04-25T18:30:00Z",
    }
    first = _build_child_project_runner_spec(request_fingerprint="fingerprint_a", **base_kwargs)
    second = _build_child_project_runner_spec(request_fingerprint="fingerprint_b", **base_kwargs)

    assert first["job_id"] != second["job_id"]


def test_child_project_runner_job_ids_include_scenario_unique_key() -> None:
    base_kwargs = {
        "request_fingerprint": "fingerprint_a",
        "request_id": "verification_workflow_scheduler_goal",
        "scheduler_packet_path": "runtime/state/verification_workflow/scheduler_packets/demo.json",
        "scenario_id": "adaptive-followup-demo-family::scenario_02",
        "target_family": "scheduler",
        "native_probe_kind": "scheduler_adaptive_followup_probe",
        "recommended_agent_role": "",
        "native_family_packet_path": "runtime/state/verification_workflow/native_family_packets/demo.json",
        "native_family_node_id": "adaptive_followup_template",
        "wave_index": 2,
        "run_at": "2026-04-26T08:30:00Z",
    }
    first = _build_child_project_runner_spec(
        scenario_packet_path="runtime/state/verification_workflow/scenario_packets/demo__12.json",
        scenario_unique_key="12",
        **base_kwargs,
    )
    second = _build_child_project_runner_spec(
        scenario_packet_path="runtime/state/verification_workflow/scenario_packets/demo__13.json",
        scenario_unique_key="13",
        **base_kwargs,
    )

    assert first["job_id"] != second["job_id"]
