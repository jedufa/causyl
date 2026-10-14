"""Bounded verification workflow launch and scheduler-facing execution slice."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from infra.controlplane import scheduled_jobs as _scheduled_jobs
from infra.controlplane import verification_workflow_launch_contract as _contract
from infra.controlplane.static_verification.static_verification_phase_b_anchor import run_phase_b_anchor
from infra.controlplane.verification_workflow_planner_bridge import (
    VerificationWorkflowPlannerBridgeError,
    build_broad_verification_plan,
)


class VerificationWorkflowLaunchError(ValueError):
    """Raised when a verification workflow launch request is invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _request_instance_token() -> str:
    return hashlib.sha256(datetime.now(timezone.utc).isoformat().encode("utf-8")).hexdigest()[:16]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalized_list(values: Iterable[Any]) -> list[str]:
    items: list[str] = []
    for raw in values or []:
        text = _clean_text(raw)
        if text and text not in items:
            items.append(text)
    return items


def _parse_utc_text(value: Any) -> datetime | None:
    raw = _clean_text(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _normalize_phase_b_anchor_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    scenarios = list(payload.get("scenarios") or [])
    raw_findings = list(payload.get("raw_findings") or [])
    if scenarios and raw_findings:
        return payload

    scenario_id = _clean_text(payload.get("scenario_id"))
    proof_target = _clean_text(payload.get("proof_target"))
    disqualifying_incident = _clean_text(payload.get("disqualifying_incident"))
    repair_surface = _clean_text(payload.get("repair_surface"))
    scenario_family = _clean_text(payload.get("scenario_family"))
    required_evidence = _normalized_list(payload.get("required_evidence") or [])
    runtime_class = _clean_text(payload.get("runtime_class")) or "bounded_anchor"

    if not scenarios and scenario_id:
        execution_class = "runnable_now"
        if runtime_class in {"support_work", "runnable_after_support_work"}:
            execution_class = "runnable_after_support_work"
        elif runtime_class in {"bounded_setup", "runnable_after_bounded_setup"}:
            execution_class = "runnable_after_bounded_setup"
        scenarios = [
            {
                "scenario_id": scenario_id,
                "execution_class": execution_class,
                "scenario_summary": proof_target or disqualifying_incident or scenario_id,
                "required_evidence": required_evidence or ["job_receipt", "finding_receipt"],
                "repair_triggerable": True,
            }
        ]

    if not raw_findings and (disqualifying_incident or repair_surface):
        raw_findings = [
            {
                "finding_id": f"finding::{scenario_id or 'phase_b_anchor'}",
                "truth_boundary": repair_surface or scenario_family or "verification_truth",
                "symptom_class": "unsafe_action",
                "operator_claim_under_attack": disqualifying_incident or proof_target or "unsafe action accepted",
                "severity_hint": "critical",
                "production_reachability_hint": "reachable_in_normal_operation",
                "affected_truths": [repair_surface or scenario_family or "verification_truth"],
                "repair_signal": "repair_worthy",
            }
        ]

    if scenarios:
        payload["scenarios"] = scenarios
    if raw_findings:
        payload["raw_findings"] = raw_findings
    if "budget" not in payload:
        payload["budget"] = {"max_budget_units": 4}
    if "actual" not in payload:
        payload["actual"] = {
            "spent_budget_units": 2,
            "repeated_cluster_ratio": 0.0,
            "new_signal_ratio": 0.8,
            "dut_improvement_delta": 0.2,
            "repair_confidence": 0.7,
            "risk_score": 0.2,
            "completed_iterations": 1,
        }
    if "baseline_health_snapshot" not in payload:
        payload["baseline_health_snapshot"] = {"open_bug_classes": 3, "critical_open": 1}
    if "post_campaign_health_snapshot" not in payload:
        payload["post_campaign_health_snapshot"] = {"open_bug_classes": 2, "critical_open": 0}
    if "reduced_bug_classes" not in payload:
        payload["reduced_bug_classes"] = [repair_surface or scenario_family or "verification_truth"]
    if "unresolved_bug_classes" not in payload:
        payload["unresolved_bug_classes"] = ["long_run_unsupervised_unproven"]
    if "residual_risk" not in payload:
        payload["residual_risk"] = "still_not_safe_unattended"
    if "blocked_claims" not in payload:
        payload["blocked_claims"] = ["broad_robustness_unproven"]
    return payload


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_fingerprint(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _contract_paths(repo_root: Path) -> dict[str, Path]:
    return {
        "launch_request": repo_root / _contract.R5S8_LAUNCH_REQUEST_CONTRACT_ARTIFACT_REL,
        "selected_block_summary": repo_root / _contract.S6T9_SELECTED_BLOCK_CONTRACT_ARTIFACT_REL,
        "launch_contract_summary": repo_root / _contract.ARTIFACT_REL,
    }


def _runtime_root(repo_root: Path) -> Path:
    return repo_root / "runtime" / "state" / "verification_workflow"


def _ensure_launch_dirs(repo_root: Path) -> dict[str, Path]:
    root = _runtime_root(repo_root)
    requests_dir = root / "requests"
    review_dir = root / "review_packets"
    scheduler_dir = root / "scheduler_packets"
    campaigns_dir = root / "campaign_runs"
    plans_dir = root / "plans"
    native_families_dir = root / "native_family_packets"
    scenario_packets_dir = root / "scenario_packets"
    scenario_runs_dir = root / "scenario_runs"
    closeout_packets_dir = root / "closeout_packets"
    for path in (
        requests_dir,
        review_dir,
        scheduler_dir,
        campaigns_dir,
        plans_dir,
        native_families_dir,
        scenario_packets_dir,
        scenario_runs_dir,
        closeout_packets_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "requests": requests_dir,
        "review": review_dir,
        "scheduler": scheduler_dir,
        "campaigns": campaigns_dir,
        "plans": plans_dir,
        "native_families": native_families_dir,
        "scenario_packets": scenario_packets_dir,
        "scenario_runs": scenario_runs_dir,
        "closeout_packets": closeout_packets_dir,
    }


def _translate_contract_error(exc: _contract.VerificationWorkflowLaunchError) -> VerificationWorkflowLaunchError:
    mapping = {
        "launch_request_block_not_launchable": ("selected_block_rejected", "selected block verdict reject blocks launch"),
        "selected_block_summary_overclaims_runtime_proof": (
            "overclaiming_selected_block_summary",
            "selected block summary over-claims runtime proof or unattended safety",
        ),
        "launch_request_invalid_output_mode": ("invalid_requested_output_mode", str(exc)),
        "launch_request_invalid_max_iterations": ("max_iterations_out_of_range", str(exc)),
        "launch_request_missing_selected_block_summary": ("missing_selected_block_summary", str(exc)),
        "launch_request_missing_request_id": ("missing_required_launch_fields", str(exc)),
        "launch_request_missing_verification_goal": ("missing_required_launch_fields", str(exc)),
    }
    code, message = mapping.get(exc.code, (exc.code, str(exc)))
    return VerificationWorkflowLaunchError(code, message)


def _translate_planner_error(exc: VerificationWorkflowPlannerBridgeError) -> VerificationWorkflowLaunchError:
    mapping = {
        "missing_verification_goal": ("missing_required_launch_fields", str(exc)),
        "unsupported_broad_verification_goal": ("unsupported_broad_verification_goal", str(exc)),
    }
    code, message = mapping.get(exc.code, (exc.code, str(exc)))
    return VerificationWorkflowLaunchError(code, message)


def _materialize_planned_payload(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    payload = deepcopy(dict(raw or {}))
    if payload.get("selected_block_summary"):
        return payload, None
    try:
        broad_plan = build_broad_verification_plan(payload)
    except VerificationWorkflowPlannerBridgeError as exc:
        raise _translate_planner_error(exc) from exc
    payload.setdefault("selected_block_summary", deepcopy(broad_plan["selected_block_summary"]))
    payload.setdefault("phase_b_anchor_payload", deepcopy(broad_plan["phase_b_anchor_payload"]))
    payload.setdefault("requested_output_mode", _clean_text((broad_plan["planner_output"] or {}).get("output_mode")) or "child_pis_family")
    return payload, broad_plan


def create_verification_launch(
    raw: Mapping[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    repo = (repo_root or Path(__file__).resolve().parent).resolve()
    contracts = _contract_paths(repo)
    launch_contract = _load_json(contracts["launch_contract_summary"])
    _load_json(contracts["launch_request"])
    _load_json(contracts["selected_block_summary"])

    created_utc = _utc_now()
    seeded = deepcopy(dict(raw or {}))
    if not seeded.get("selected_block_summary") and not _clean_text(seeded.get("request_instance_token")):
        seeded["request_instance_token"] = _request_instance_token()
    payload, broad_plan = _materialize_planned_payload(seeded)
    try:
        durable_request = _contract.normalize_launch_request(payload, created_utc=created_utc)
    except _contract.VerificationWorkflowLaunchError as exc:
        raise _translate_contract_error(exc) from exc

    selected_summary = _contract.build_selected_block_summary(payload.get("selected_block_summary") or {})
    raw_operator_intent = durable_request["raw_operator_intent"]
    normalized_request = durable_request["normalized_request"]
    request_fingerprint = durable_request["request_fingerprint"]
    dirs = _ensure_launch_dirs(repo)
    request_path = dirs["requests"] / f"{request_fingerprint}.json"
    review_path = dirs["review"] / f"{request_fingerprint}.json"
    scheduler_path = dirs["scheduler"] / f"{request_fingerprint}.json"
    plan_path = dirs["plans"] / f"{request_fingerprint}.json"

    durable_request = {
        **durable_request,
        "schema_version": "verification_workflow_launch_request_record_v1",
        "selected_block_summary": selected_summary,
    }
    if request_path.exists():
        durable_request = _load_json(request_path)
    else:
        request_path.write_text(json.dumps(durable_request, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    review_packet = {
        "schema_version": "verification_workflow_review_packet_v1",
        "request_fingerprint": request_fingerprint,
        "normalized_request": normalized_request,
        "selected_block_summary": selected_summary,
        "planner_mode": _clean_text(((broad_plan or {}).get("planner_output") or {}).get("output_mode")),
        "planner_maximum_supported_claim": _clean_text((broad_plan or {}).get("planner_maximum_supported_claim")),
        "review_state": "ready_for_confirm",
        "operator_text": (
            "The plane will submit one bounded verification workflow request using the normalized launch "
            "record and the selected-block summary shown here. This launch does not prove execution success, "
            "completion, or DUT improvement."
        ),
    }
    review_path.write_text(json.dumps(review_packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    planner_packet: dict[str, Any] | None = None
    native_family_packet: dict[str, Any] | None = None
    native_family_path = Path()
    if broad_plan is not None:
        planner_packet = {
            "schema_version": "verification_workflow_planner_packet_v1",
            "request_fingerprint": request_fingerprint,
            "planner_family": _clean_text(broad_plan.get("planner_family")),
            "target_family": _clean_text(broad_plan.get("target_family")),
            "planner_goal": _clean_text(broad_plan.get("goal")),
            "planner_maximum_supported_claim": _clean_text(broad_plan.get("planner_maximum_supported_claim")),
            "planner_rerun_fingerprint": _clean_text(broad_plan.get("planner_rerun_fingerprint")),
            "native_execution_family": deepcopy(broad_plan.get("native_execution_family") or {}),
            "extraction_output": deepcopy(broad_plan.get("extraction_output") or {}),
            "threat_matrix": deepcopy(broad_plan.get("threat_matrix") or {}),
            "planner_output": deepcopy(broad_plan.get("planner_output") or {}),
        }
        plan_path.write_text(json.dumps(planner_packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        native_family_packet = _build_native_family_packet(
            request_fingerprint=request_fingerprint,
            request_id=_clean_text(normalized_request.get("request_id")),
            target_family=_clean_text(planner_packet.get("target_family")),
            native_execution_family=planner_packet.get("native_execution_family") or {},
            scenarios=list((payload.get("phase_b_anchor_payload") or {}).get("scenarios") or []),
        )
        native_family_path = _native_family_packet_path(dirs, request_fingerprint)
        native_family_path.write_text(json.dumps(native_family_packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    scheduler_packet = {
        "schema_version": "verification_workflow_scheduler_packet_v1",
        "job_id": f"verify_workflow::{normalized_request['request_id']}",
        "request_fingerprint": request_fingerprint,
        "request_record_path": str(request_path.relative_to(repo)),
        "review_packet_path": str(review_path.relative_to(repo)),
        "planner_packet_path": str(plan_path.relative_to(repo)) if planner_packet is not None else "",
        "job_kind": normalized_request["scheduler_job_kind"],
        "target_family": _clean_text((planner_packet or {}).get("target_family")),
        "max_iterations": normalized_request["max_iterations"],
        "native_execution_family": deepcopy((planner_packet or {}).get("native_execution_family") or {}),
        "native_family_packet_path": str(native_family_path.relative_to(repo)) if native_family_packet is not None else "",
        "phase_b_anchor_payload": dict(payload.get("phase_b_anchor_payload") or {}),
    }
    scheduler_path.write_text(json.dumps(scheduler_packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "schema_version": "verification_workflow_launch_result_v1",
        "durable_request": durable_request,
        "review_packet": review_packet,
        "scheduler_packet": scheduler_packet,
        "durable_request_path": str(request_path.relative_to(repo)),
        "review_packet_path": str(review_path.relative_to(repo)),
        "scheduler_packet_path": str(scheduler_path.relative_to(repo)),
        "planner_packet": planner_packet,
        "planner_packet_path": str(plan_path.relative_to(repo)) if planner_packet is not None else "",
        "native_family_packet": native_family_packet,
        "native_family_packet_path": str(native_family_path.relative_to(repo)) if native_family_packet is not None else "",
    }


def build_scheduler_job_spec_for_verification_workflow(
    launch_result: Mapping[str, Any],
    *,
    schedule_mode: str = "once",
    run_at: str = "2099-01-01T00:00:00Z",
) -> dict[str, Any]:
    result = deepcopy(dict(launch_result or {}))
    durable_request = dict(result.get("durable_request") or {})
    scheduler_packet = dict(result.get("scheduler_packet") or {})
    request_fingerprint = _clean_text(durable_request.get("request_fingerprint") or scheduler_packet.get("request_fingerprint"))
    packet_job_id = _clean_text(scheduler_packet.get("job_id"))
    request_id = _clean_text(packet_job_id.split("::", 1)[1] if "::" in packet_job_id else durable_request.get("request_id") or "verification_workflow")
    if not request_fingerprint:
        raise VerificationWorkflowLaunchError("missing_request_fingerprint", "launch result missing request fingerprint")
    if not _clean_text(result.get("scheduler_packet_path")):
        raise VerificationWorkflowLaunchError("missing_scheduler_packet_path", "launch result missing scheduler packet path")
    job_id = f"verification_workflow_{request_id}"
    return {
        "job_id": job_id,
        "enabled": True,
        "binding": {"type": "none"},
        "job_kind": "project_runner",
        "display_title": f"Verification workflow {request_id}",
        "schedule": {"mode": schedule_mode, "run_at": run_at, "timezone": "UTC"},
        "policy": {"max_runtime_seconds": 600},
        "expected_output": {"kind": "report", "format": "markdown"},
        "required_context_bundle": "lab_core_v1",
        "project_run": {
            "project_id": "verification_workflow",
            "runner_id": "anchor",
            "args": {
                "request_fingerprint": request_fingerprint,
                "scheduler_packet_path": _clean_text(result.get("scheduler_packet_path")),
                "dry_run": False,
            },
        },
    }


def _safe_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() else "_" for ch in str(value or "").strip())
    token = "_".join(part for part in token.split("_") if part)
    return token[:48] or "token"


def _probe_kind_token(native_probe_kind: str) -> str:
    probe_kind = _clean_text(native_probe_kind)
    if not probe_kind:
        return "probe"
    return _safe_token(probe_kind.replace("_probe", "")).lower()


def _target_label(target_family: str) -> str:
    if target_family == "android_agent_tabs":
        return "Android tabs"
    if target_family == "scheduler":
        return "Scheduler"
    return "Verification"


def _native_family_packet_path(dirs: Mapping[str, Path], request_fingerprint: str) -> Path:
    return dirs["native_families"] / f"{request_fingerprint}.json"


def _load_request_record_by_fingerprint(*, repo: Path, request_fingerprint: str) -> dict[str, Any]:
    fingerprint = _clean_text(request_fingerprint)
    if not fingerprint:
        return {}
    path = _ensure_launch_dirs(repo)["requests"] / f"{fingerprint}.json"
    if not path.exists():
        return {}
    return _load_json(path)


def _is_top_level_verification_workflow_launcher(spec: Mapping[str, Any]) -> bool:
    if _clean_text(spec.get("job_kind")).lower() != "project_runner":
        return False
    project_run = spec.get("project_run") if isinstance(spec.get("project_run"), Mapping) else {}
    if _clean_text(project_run.get("project_id")) != "verification_workflow":
        return False
    args = project_run.get("args") if isinstance(project_run.get("args"), Mapping) else {}
    if _clean_text(args.get("scenario_packet_path")) or _clean_text(args.get("campaign_closeout_path")):
        return False
    return bool(_clean_text(args.get("request_fingerprint")))


def _retire_superseded_verification_workflow_launchers(
    current_spec: Mapping[str, Any],
    current_request_record: Mapping[str, Any],
    *,
    repo: Path,
) -> list[str]:
    current_job_id = _clean_text(current_spec.get("job_id"))
    current_normalized = current_request_record.get("normalized_request") if isinstance(current_request_record.get("normalized_request"), Mapping) else {}
    current_goal = _clean_text(current_normalized.get("verification_goal"))
    current_family = _clean_text(current_normalized.get("workflow_family"))
    if not current_goal or not current_family:
        return []

    retired: list[str] = []
    for row in list(_scheduled_jobs.list_jobs(repo_root=repo)):
        job_id = _clean_text(row.get("job_id"))
        if not job_id or job_id == current_job_id:
            continue
        try:
            candidate = _scheduled_jobs.load_job(job_id, repo_root=repo)
        except Exception:
            continue
        if not bool(candidate.get("enabled", True)):
            continue
        if not _is_top_level_verification_workflow_launcher(candidate):
            continue
        project_run = candidate.get("project_run") if isinstance(candidate.get("project_run"), Mapping) else {}
        args = project_run.get("args") if isinstance(project_run.get("args"), Mapping) else {}
        candidate_request = _load_request_record_by_fingerprint(repo=repo, request_fingerprint=_clean_text(args.get("request_fingerprint")))
        candidate_normalized = candidate_request.get("normalized_request") if isinstance(candidate_request.get("normalized_request"), Mapping) else {}
        if _clean_text(candidate_normalized.get("verification_goal")) != current_goal:
            continue
        if _clean_text(candidate_normalized.get("workflow_family")) != current_family:
            continue
        candidate["enabled"] = False
        _scheduled_jobs.save_job(candidate, repo_root=repo)
        retired.append(job_id)
    return retired


def _build_native_family_packet(
    *,
    request_fingerprint: str,
    request_id: str,
    target_family: str,
    native_execution_family: Mapping[str, Any],
    scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    root_node_id = f"{target_family or 'verification'}::campaign_root"
    closeout_probe_kind = _clean_text((native_execution_family.get("swarm_shape") or {}).get("closeout_role")) or "closeout"
    adaptive_probe_kind = _clean_text((native_execution_family.get("swarm_shape") or {}).get("adaptive_followup_kind"))
    graph_nodes: list[dict[str, Any]] = [
        {
            "node_id": root_node_id,
            "node_kind": "campaign_root",
            "stage": "campaign_root",
            "target_family": target_family,
            "display_label": _target_label(target_family),
        }
    ]
    graph_edges: list[dict[str, Any]] = []
    for scenario in scenarios:
        scenario_id = _clean_text(scenario.get("scenario_id"))
        node_id = f"scenario::{scenario_id}"
        graph_nodes.append(
            {
                "node_id": node_id,
                "node_kind": "seed_probe",
                "stage": "probe",
                "scenario_id": scenario_id,
                "native_probe_kind": _clean_text(scenario.get("native_probe_kind")),
                "recommended_agent_role": _clean_text(scenario.get("recommended_agent_role")),
                "execution_class": _clean_text(scenario.get("execution_class")),
            }
        )
        graph_edges.append({"from_node_id": root_node_id, "to_node_id": node_id, "edge_kind": "decomposes_into"})
    if adaptive_probe_kind:
        graph_nodes.append(
            {
                "node_id": "adaptive_followup_template",
                "node_kind": "adaptive_followup_template",
                "stage": "adaptive_followup",
                "native_probe_kind": adaptive_probe_kind,
                "recommended_agent_role": "builder_verifier",
            }
        )
        graph_edges.append(
            {"from_node_id": root_node_id, "to_node_id": "adaptive_followup_template", "edge_kind": "may_expand_into"}
        )
    graph_nodes.append(
        {
            "node_id": "campaign_closeout",
            "node_kind": "closeout",
            "stage": "closeout",
            "native_probe_kind": closeout_probe_kind,
            "recommended_agent_role": "closeout",
        }
    )
    for node in [row for row in graph_nodes if row.get("stage") in {"probe", "adaptive_followup"}]:
        graph_edges.append(
            {"from_node_id": _clean_text(node.get("node_id")), "to_node_id": "campaign_closeout", "edge_kind": "aggregates_into"}
        )
    return {
        "schema_version": "verification_workflow_native_family_packet_v1",
        "request_fingerprint": request_fingerprint,
        "request_id": request_id,
        "target_family": target_family,
        "native_execution_family": deepcopy(dict(native_execution_family or {})),
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
    }


def _build_child_project_runner_spec(
    *,
    request_fingerprint: str,
    request_id: str,
    scheduler_packet_path: str,
    scenario_packet_path: str,
    scenario_id: str,
    scenario_unique_key: str = "",
    target_family: str,
    native_probe_kind: str,
    recommended_agent_role: str,
    native_family_packet_path: str,
    native_family_node_id: str,
    wave_index: int,
    run_at: str,
) -> dict[str, Any]:
    child_token = hashlib.sha256(f"{scenario_id}|{scenario_unique_key or scenario_packet_path}".encode("utf-8")).hexdigest()[:12]
    request_token = hashlib.sha256(request_fingerprint.encode("utf-8")).hexdigest()[:12]
    probe_token = _probe_kind_token(native_probe_kind)
    return {
        "job_id": f"vw_{probe_token}_{request_token}_{child_token}",
        "enabled": True,
        "binding": {"type": "none"},
        "job_kind": "project_runner",
        "display_title": f"{_target_label(target_family)} {native_probe_kind or 'probe'} {scenario_id}",
        "schedule": {"mode": "once", "run_at": run_at, "timezone": "UTC"},
        "policy": {"max_runtime_seconds": 600},
        "expected_output": {"kind": "report", "format": "markdown"},
        "required_context_bundle": "lab_core_v1",
        "prompt_meta": {
            "execution_family_contract": {
                "contract_kind": "verification_native_probe_family_v1",
                "request_fingerprint": request_fingerprint,
                "request_id": request_id,
                "target_family": target_family,
                "native_probe_kind": native_probe_kind,
                "recommended_agent_role": recommended_agent_role,
                "native_family_packet_path": native_family_packet_path,
                "native_family_node_id": native_family_node_id,
                "wave_index": int(wave_index),
            }
        },
        "project_run": {
            "project_id": "verification_workflow",
            "runner_id": "anchor",
            "args": {
                "request_fingerprint": request_fingerprint,
                "scheduler_packet_path": scheduler_packet_path,
                "scenario_packet_path": scenario_packet_path,
                "dry_run": False,
            },
        },
    }


def _build_closeout_project_runner_spec(
    *,
    request_fingerprint: str,
    request_id: str,
    scheduler_packet_path: str,
    closeout_packet_path: str,
    child_job_ids: list[str],
    target_family: str,
    native_family_packet_path: str,
    run_at: str,
) -> dict[str, Any]:
    request_token = hashlib.sha256(request_fingerprint.encode("utf-8")).hexdigest()[:12]
    return {
        "job_id": f"vw_{_safe_token(target_family or 'verification')}_closeout_{request_token}",
        "enabled": True,
        "binding": {"type": "none"},
        "job_kind": "project_runner",
        "display_title": f"{_target_label(target_family)} closeout {request_id}",
        "depends_on": list(child_job_ids),
        "schedule": {"mode": "once", "run_at": run_at, "timezone": "UTC"},
        "policy": {"max_runtime_seconds": 600},
        "expected_output": {"kind": "report", "format": "markdown"},
        "required_context_bundle": "lab_core_v1",
        "prompt_meta": {
            "execution_family_contract": {
                "contract_kind": "verification_native_probe_family_v1",
                "request_fingerprint": request_fingerprint,
                "request_id": request_id,
                "target_family": target_family,
                "native_probe_kind": "campaign_closeout",
                "recommended_agent_role": "closeout",
                "native_family_packet_path": native_family_packet_path,
                "native_family_node_id": "campaign_closeout",
            }
        },
        "project_run": {
            "project_id": "verification_workflow",
            "runner_id": "anchor",
            "args": {
                "request_fingerprint": request_fingerprint,
                "scheduler_packet_path": scheduler_packet_path,
                "campaign_closeout_path": closeout_packet_path,
                "dry_run": False,
            },
        },
    }


def _scenario_family_id(scenario_id: str) -> str:
    marker = "::scenario_"
    return scenario_id.split(marker, 1)[0] if marker in scenario_id else scenario_id


def _write_campaign_record(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _scenario_packet_path(dirs: Mapping[str, Path], request_fingerprint: str, scenario_index: int) -> Path:
    return dirs["scenario_packets"] / f"{request_fingerprint}__{scenario_index:02d}.json"


def _scenario_run_path(dirs: Mapping[str, Path], request_fingerprint: str, scenario_index: int) -> Path:
    return dirs["scenario_runs"] / f"{request_fingerprint}__{scenario_index:02d}.json"


def _find_scenario_packet_by_scenario_id(*, repo: Path, request_fingerprint: str, scenario_id: str) -> dict[str, Any] | None:
    target = _clean_text(scenario_id)
    if not target:
        return None
    for path in sorted((_ensure_launch_dirs(repo)["scenario_packets"]).glob(f"{request_fingerprint}__*.json")):
        payload = _load_json(path)
        scenario = dict(payload.get("scenario") or {})
        if _clean_text(scenario.get("scenario_id")) == target:
            return payload
    return None


def _find_scenario_run_by_scenario_id(
    *,
    repo: Path,
    request_fingerprint: str,
    scenario_id: str,
) -> tuple[Path, dict[str, Any]] | None:
    target = _clean_text(scenario_id)
    if not target:
        return None
    matches: list[tuple[int, Path, dict[str, Any]]] = []
    for path in sorted((_ensure_launch_dirs(repo)["scenario_runs"]).glob(f"{request_fingerprint}__*.json")):
        payload = _load_json(path)
        if _clean_text(payload.get("scenario_id")) != target:
            continue
        try:
            scenario_index = int(_clean_text(path.stem.split("__")[-1]) or "0")
        except Exception:
            scenario_index = 0
        matches.append((scenario_index, path, payload))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    _, path, payload = matches[0]
    return path, payload


def _hydrate_scenario_packet_defaults(packet: Mapping[str, Any], *, repo: Path) -> dict[str, Any]:
    hydrated = deepcopy(dict(packet or {}))
    baseline = dict(hydrated.get("baseline_health_snapshot") or {})
    post = dict(hydrated.get("post_campaign_health_snapshot") or {})
    if baseline and post:
        return hydrated
    request_fingerprint = _clean_text(hydrated.get("request_fingerprint"))
    derived_from_scenario_id = _clean_text(hydrated.get("derived_from_scenario_id"))
    source_packet = _find_scenario_packet_by_scenario_id(
        repo=repo,
        request_fingerprint=request_fingerprint,
        scenario_id=derived_from_scenario_id,
    )
    source_run: dict[str, Any] = {}
    if source_packet is not None:
        scenario_index = int(source_packet.get("scenario_index") or 0)
        if scenario_index > 0:
            source_run_path = _scenario_run_path(_ensure_launch_dirs(repo), request_fingerprint, scenario_index)
            if source_run_path.exists():
                source_run = _load_json(source_run_path)
    dut_delta = dict((source_run.get("anchor_report") or {}).get("dut_health_delta") or {})
    if not hydrated.get("budget") and source_packet is not None:
        hydrated["budget"] = dict(source_packet.get("budget") or {})
    if not hydrated.get("actual") and source_packet is not None:
        hydrated["actual"] = dict(source_packet.get("actual") or {})
    if not baseline:
        hydrated["baseline_health_snapshot"] = dict(
            hydrated.get("baseline_health_snapshot")
            or (source_packet.get("baseline_health_snapshot") if source_packet is not None else {})
            or dut_delta.get("baseline_health_snapshot")
            or {}
        )
    if not post:
        hydrated["post_campaign_health_snapshot"] = dict(
            hydrated.get("post_campaign_health_snapshot")
            or (source_packet.get("post_campaign_health_snapshot") if source_packet is not None else {})
            or dut_delta.get("post_campaign_health_snapshot")
            or {}
        )
    if not hydrated.get("reduced_bug_classes"):
        hydrated["reduced_bug_classes"] = list(
            hydrated.get("reduced_bug_classes")
            or (source_packet.get("reduced_bug_classes") if source_packet is not None else [])
            or dut_delta.get("reduced_bug_classes")
            or []
        )
    if not hydrated.get("unresolved_bug_classes"):
        hydrated["unresolved_bug_classes"] = list(
            hydrated.get("unresolved_bug_classes")
            or (source_packet.get("unresolved_bug_classes") if source_packet is not None else [])
            or dut_delta.get("unresolved_bug_classes")
            or []
        )
    if not _clean_text(hydrated.get("residual_risk")):
        hydrated["residual_risk"] = _clean_text(
            hydrated.get("residual_risk")
            or (source_packet.get("residual_risk") if source_packet is not None else "")
            or dut_delta.get("residual_risk")
            or ""
        )
    if not hydrated.get("blocked_claims"):
        hydrated["blocked_claims"] = list(
            hydrated.get("blocked_claims")
            or (source_packet.get("blocked_claims") if source_packet is not None else [])
            or dut_delta.get("blocked_claims")
            or []
        )
    if not _clean_text(hydrated.get("target_family")) and source_packet is not None:
        hydrated["target_family"] = _clean_text(source_packet.get("target_family"))
    if not _clean_text(hydrated.get("native_probe_kind")) and source_packet is not None:
        hydrated["native_probe_kind"] = _clean_text(source_packet.get("native_probe_kind"))
    return hydrated


def _next_wave_followup_rows(
    *,
    request_fingerprint: str,
    wave_index: int,
    child_jobs: list[dict[str, Any]],
    repo: Path,
) -> list[dict[str, Any]]:
    followups: list[dict[str, Any]] = []
    request_token = hashlib.sha256(request_fingerprint.encode("utf-8")).hexdigest()[:10]
    for row in child_jobs:
        scenario_packet_path = repo / _clean_text(row.get("scenario_packet_path"))
        if not scenario_packet_path.exists():
            continue
        scenario_packet = _load_json(scenario_packet_path)
        if _clean_text(scenario_packet.get("adaptive_root_cluster_id")):
            continue
        scenario_index = int(scenario_packet.get("scenario_index") or 0)
        scenario_run_path = _scenario_run_path(_ensure_launch_dirs(repo), request_fingerprint, scenario_index)
        if not scenario_run_path.exists():
            continue
        scenario_run = _load_json(scenario_run_path)
        report = dict(scenario_run.get("anchor_report") or {})
        dut_delta = dict(report.get("dut_health_delta") or {})
        normalized = list(report.get("normalized_defect_candidates") or [])
        repairs = list(report.get("routed_repairs") or [])
        if not normalized:
            continue
        defect = dict(normalized[0])
        cluster_id = _clean_text(defect.get("cluster_id"))
        if not cluster_id:
            continue
        source_scenario = dict(scenario_packet.get("scenario") or {})
        target_family = _clean_text(scenario_packet.get("target_family") or source_scenario.get("target_family"))
        native_probe_kind = (
            "mobile_adaptive_followup_probe" if target_family == "android_agent_tabs" else "scheduler_adaptive_followup_probe"
        )
        inherited_budget = dict(scenario_packet.get("budget") or {})
        inherited_actual = dict(scenario_packet.get("actual") or {})
        baseline_health_snapshot = dict(scenario_packet.get("baseline_health_snapshot") or {})
        post_campaign_health_snapshot = dict(scenario_packet.get("post_campaign_health_snapshot") or {})
        reduced_bug_classes = list(scenario_packet.get("reduced_bug_classes") or [])
        unresolved_bug_classes = list(scenario_packet.get("unresolved_bug_classes") or [])
        residual_risk = _clean_text(scenario_packet.get("residual_risk"))
        blocked_claims = list(scenario_packet.get("blocked_claims") or [])
        if not baseline_health_snapshot:
            baseline_health_snapshot = dict(dut_delta.get("baseline_health_snapshot") or {})
        if not post_campaign_health_snapshot:
            post_campaign_health_snapshot = dict(dut_delta.get("post_campaign_health_snapshot") or {})
        if not reduced_bug_classes:
            reduced_bug_classes = list(dut_delta.get("reduced_bug_classes") or [])
        if not unresolved_bug_classes:
            unresolved_bug_classes = list(dut_delta.get("unresolved_bug_classes") or [])
        if not residual_risk:
            residual_risk = _clean_text(dut_delta.get("residual_risk"))
        if not blocked_claims:
            blocked_claims = list(dut_delta.get("blocked_claims") or [])
        required_evidence = _normalized_list(
            list(source_scenario.get("required_evidence") or [])
            + ["targeted_revalidation", "operator_projection_snapshot", "repair_receipt"]
        )
        family_id = f"adaptive-followup-{request_token}-{hashlib.sha256(cluster_id.encode('utf-8')).hexdigest()[:10]}-family"
        followups.append(
            {
                "scenario": {
                    "scenario_id": f"{family_id}::scenario_{wave_index + 1:02d}",
                    "target_family": target_family,
                    "native_probe_kind": native_probe_kind,
                    "recommended_agent_role": "builder_verifier",
                    "execution_class": _clean_text(source_scenario.get("execution_class")) or "runnable_now",
                    "scenario_summary": (
                        f"Adaptive follow-up for {cluster_id}: replay the repair path under receipt, claim, and operator-projection disagreement."
                    ),
                    "required_evidence": required_evidence,
                    "setup_requirements": _normalized_list(
                        list(source_scenario.get("setup_requirements") or []) + ["reseed_cluster_contradiction", "operator_projection_refresh_lag"]
                    ),
                    "reset_requirements": _normalized_list(list(source_scenario.get("reset_requirements") or []) + ["clear_scheduler_residue"]),
                    "repair_triggerable": True,
                },
                "raw_finding": {
                    "finding_id": f"finding::{family_id}",
                    "target_family": target_family,
                    "native_probe_kind": native_probe_kind,
                    "truth_boundary": _clean_text(defect.get("truth_boundary")) or "adaptive_scheduler_truth",
                    "symptom_class": _clean_text(defect.get("symptom_class")) or "false_calm",
                    "operator_claim_under_attack": (
                        f"Adaptive replay must keep {cluster_id} visible across repair and projection refresh."
                    ),
                    "severity_hint": _clean_text(defect.get("severity")) or "high",
                    "production_reachability_hint": "reachable_in_normal_operation",
                    "affected_truths": _normalized_list(defect.get("affected_truths") or defect.get("evidence_refs") or [])[:2],
                    "repair_signal": "repair_worthy" if repairs else "monitor_only",
                },
                "adaptive_root_cluster_id": cluster_id,
                "derived_from_scenario_id": _clean_text(scenario_packet.get("scenario_id") or source_scenario.get("scenario_id")),
                "target_family": target_family,
                "native_probe_kind": native_probe_kind,
                "recommended_agent_role": "builder_verifier",
                "native_family_packet_path": _clean_text(scenario_packet.get("native_family_packet_path")),
                "native_family_node_id": "adaptive_followup_template",
                "budget": inherited_budget,
                "actual": inherited_actual,
                "baseline_health_snapshot": baseline_health_snapshot,
                "post_campaign_health_snapshot": post_campaign_health_snapshot,
                "reduced_bug_classes": reduced_bug_classes,
                "unresolved_bug_classes": unresolved_bug_classes,
                "residual_risk": residual_risk,
                "blocked_claims": blocked_claims,
            }
        )
    return followups


def _queue_wave(
    *,
    request_fingerprint: str,
    request_id: str,
    scheduler_packet_path: str,
    repo: Path,
    dirs: Mapping[str, Path],
    scenarios: list[dict[str, Any]],
    wave_index: int,
    next_scenario_index: int,
) -> tuple[list[dict[str, Any]], int]:
    child_rows: list[dict[str, Any]] = []
    run_at = _utc_now()
    for scenario_row in scenarios:
        scenario = dict(scenario_row.get("scenario") or {})
        raw_finding = dict(scenario_row.get("raw_finding") or {})
        scenario_id = _clean_text(scenario.get("scenario_id"))
        scenario_packet = {
            "schema_version": "verification_workflow_scenario_packet_v1",
            "request_fingerprint": request_fingerprint,
            "request_id": request_id,
            "scenario_index": next_scenario_index,
            "wave_index": wave_index,
            "target_family": _clean_text(scenario.get("target_family") or scenario_row.get("target_family")),
            "native_probe_kind": _clean_text(scenario.get("native_probe_kind") or scenario_row.get("native_probe_kind")),
            "recommended_agent_role": _clean_text(
                scenario.get("recommended_agent_role") or scenario_row.get("recommended_agent_role")
            ),
            "native_family_packet_path": _clean_text(scenario_row.get("native_family_packet_path")),
            "native_family_node_id": _clean_text(scenario_row.get("native_family_node_id")),
            "scenario": scenario,
            "raw_finding": raw_finding,
            "budget": dict(scenario_row.get("budget") or {}),
            "actual": dict(scenario_row.get("actual") or {}),
            "baseline_health_snapshot": dict(scenario_row.get("baseline_health_snapshot") or {}),
            "post_campaign_health_snapshot": dict(scenario_row.get("post_campaign_health_snapshot") or {}),
            "reduced_bug_classes": list(scenario_row.get("reduced_bug_classes") or []),
            "unresolved_bug_classes": list(scenario_row.get("unresolved_bug_classes") or []),
            "residual_risk": _clean_text(scenario_row.get("residual_risk")),
            "blocked_claims": list(scenario_row.get("blocked_claims") or []),
            "adaptive_root_cluster_id": _clean_text(scenario_row.get("adaptive_root_cluster_id")),
            "derived_from_scenario_id": _clean_text(scenario_row.get("derived_from_scenario_id")),
        }
        scenario_path = _scenario_packet_path(dirs, request_fingerprint, next_scenario_index)
        _write_campaign_record(scenario_path, scenario_packet)
        spec = _build_child_project_runner_spec(
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            scheduler_packet_path=scheduler_packet_path,
            scenario_packet_path=str(scenario_path.relative_to(repo)),
            scenario_id=scenario_id,
            scenario_unique_key=str(next_scenario_index),
            target_family=_clean_text(scenario_packet.get("target_family")),
            native_probe_kind=_clean_text(scenario_packet.get("native_probe_kind")),
            recommended_agent_role=_clean_text(scenario_packet.get("recommended_agent_role")),
            native_family_packet_path=_clean_text(scenario_packet.get("native_family_packet_path")),
            native_family_node_id=_clean_text(scenario_packet.get("native_family_node_id")),
            wave_index=wave_index,
            run_at=run_at,
        )
        saved = _scheduled_jobs.save_job(spec, repo_root=repo)
        child_rows.append(
            {
                "job_id": str(saved.get("job_id") or ""),
                "scenario_id": scenario_id,
                "scenario_packet_path": str(scenario_path.relative_to(repo)),
                "wave_index": wave_index,
                "target_family": _clean_text(scenario_packet.get("target_family")),
                "native_probe_kind": _clean_text(scenario_packet.get("native_probe_kind")),
                "native_family_node_id": _clean_text(scenario_packet.get("native_family_node_id")),
                "adaptive_root_cluster_id": _clean_text(scenario_packet.get("adaptive_root_cluster_id")),
            }
        )
        next_scenario_index += 1
    return child_rows, next_scenario_index


def _job_exists(job_id: str, *, repo: Path) -> bool:
    try:
        _scheduled_jobs.load_job(job_id, repo_root=repo)
        return True
    except Exception:
        return False


def _rebuild_child_job(
    *,
    repo: Path,
    request_fingerprint: str,
    request_id: str,
    scheduler_packet_path: str,
    child_row: Mapping[str, Any],
) -> dict[str, Any] | None:
    scenario_packet_path = repo / _clean_text(child_row.get("scenario_packet_path"))
    if not scenario_packet_path.exists():
        return None
    packet = _load_json(scenario_packet_path)
    scenario = dict(packet.get("scenario") or {})
    scenario_id = _clean_text(scenario.get("scenario_id") or child_row.get("scenario_id"))
    if not scenario_id:
        return None
    spec = _build_child_project_runner_spec(
        request_fingerprint=request_fingerprint,
        request_id=request_id,
        scheduler_packet_path=scheduler_packet_path,
        scenario_packet_path=str(scenario_packet_path.relative_to(repo)),
        scenario_id=scenario_id,
        scenario_unique_key=str(packet.get("scenario_index") or ""),
        target_family=_clean_text(packet.get("target_family")),
        native_probe_kind=_clean_text(packet.get("native_probe_kind")),
        recommended_agent_role=_clean_text(packet.get("recommended_agent_role")),
        native_family_packet_path=_clean_text(packet.get("native_family_packet_path")),
        native_family_node_id=_clean_text(packet.get("native_family_node_id")),
        wave_index=int(packet.get("wave_index") or 1),
        run_at=_utc_now(),
    )
    if _clean_text(child_row.get("job_id")):
        spec["job_id"] = _clean_text(child_row.get("job_id"))
    return _scheduled_jobs.save_job(spec, repo_root=repo)


def _rebuild_closeout_job(
    *,
    repo: Path,
    request_fingerprint: str,
    request_id: str,
    scheduler_packet_path: str,
    closeout_packet_path: str,
    closeout_job_id: str,
) -> dict[str, Any] | None:
    packet_path = repo / _clean_text(closeout_packet_path)
    if not packet_path.exists():
        return None
    packet = _load_json(packet_path)
    child_jobs = [str(row.get("job_id") or "") for row in list(packet.get("child_jobs") or []) if str(row.get("job_id") or "").strip()]
    spec = _build_closeout_project_runner_spec(
        request_fingerprint=request_fingerprint,
        request_id=request_id,
        scheduler_packet_path=scheduler_packet_path,
        closeout_packet_path=str(packet_path.relative_to(repo)),
        child_job_ids=child_jobs,
        target_family=_clean_text(packet.get("target_family")),
        native_family_packet_path=_clean_text(packet.get("native_family_packet_path")),
        run_at=_utc_now(),
    )
    if closeout_job_id:
        spec["job_id"] = closeout_job_id
    return _scheduled_jobs.save_job(spec, repo_root=repo)


def reconcile_verification_workflow_campaigns(*, repo_root: Optional[Path] = None) -> dict[str, Any]:
    repo = (repo_root or Path(__file__).resolve().parent).resolve()
    dirs = _ensure_launch_dirs(repo)
    reconciled_campaigns = 0
    recreated_child_jobs = 0
    recreated_closeout_jobs = 0
    errors: list[dict[str, Any]] = []
    for campaign_path in sorted(dirs["campaigns"].glob("*.json")):
        try:
            campaign = _load_json(campaign_path)
            if _clean_text(campaign.get("execution_status")) != "running_children":
                continue
            request_fingerprint = _clean_text(campaign.get("request_fingerprint"))
            request_id = _clean_text(campaign.get("request_id"))
            scheduler_packet_path = _clean_text(campaign.get("scheduler_packet_path"))
            if not request_fingerprint or not request_id or not scheduler_packet_path:
                continue
            for child_row in list(campaign.get("queued_child_jobs") or []):
                job_id = _clean_text(child_row.get("job_id"))
                if not job_id or _job_exists(job_id, repo=repo):
                    continue
                rebuilt = _rebuild_child_job(
                    repo=repo,
                    request_fingerprint=request_fingerprint,
                    request_id=request_id,
                    scheduler_packet_path=scheduler_packet_path,
                    child_row=child_row,
                )
                if rebuilt is not None:
                    recreated_child_jobs += 1
            closeout_job_id = _clean_text(campaign.get("closeout_job_id"))
            closeout_packet_path = _clean_text(campaign.get("closeout_packet_path"))
            if closeout_job_id and closeout_packet_path and not _job_exists(closeout_job_id, repo=repo):
                rebuilt = _rebuild_closeout_job(
                    repo=repo,
                    request_fingerprint=request_fingerprint,
                    request_id=request_id,
                    scheduler_packet_path=scheduler_packet_path,
                    closeout_packet_path=closeout_packet_path,
                    closeout_job_id=closeout_job_id,
                )
                if rebuilt is not None:
                    recreated_closeout_jobs += 1
            reconciled_campaigns += 1
        except Exception as exc:
            errors.append({"campaign_path": str(campaign_path), "error": str(exc)})
    return {
        "ok": not errors,
        "reconciled_campaigns": reconciled_campaigns,
        "recreated_child_jobs": recreated_child_jobs,
        "recreated_closeout_jobs": recreated_closeout_jobs,
        "errors": errors,
    }


def _orchestrate_child_campaign(
    *,
    packet: Mapping[str, Any],
    request_record: Mapping[str, Any],
    anchor_payload: Mapping[str, Any],
    repo: Path,
    dirs: Mapping[str, Path],
) -> dict[str, Any]:
    request_fingerprint = _clean_text(packet.get("request_fingerprint"))
    request_id = _clean_text(request_record.get("request_id"))
    scheduler_packet_path = _clean_text(packet.get("scheduler_packet_path"))
    native_family_packet_path = _clean_text(packet.get("native_family_packet_path"))
    target_family = _clean_text(packet.get("target_family"))
    if not scheduler_packet_path:
        scheduler_packet_path = str((dirs["scheduler"] / f"{request_fingerprint}.json").relative_to(repo))
    scenarios = list(anchor_payload.get("scenarios") or [])
    findings_by_family = {
        _scenario_family_id(_clean_text(row.get("finding_id")).replace("finding::", "")): dict(row)
        for row in list(anchor_payload.get("raw_findings") or [])
        if _clean_text(row.get("finding_id"))
    }
    wave_rows: list[dict[str, Any]] = []
    for index, scenario in enumerate(scenarios, start=1):
        scenario_id = _clean_text(scenario.get("scenario_id"))
        family_id = _scenario_family_id(scenario_id)
        raw_finding = findings_by_family.get(family_id)
        if raw_finding is None:
            raise VerificationWorkflowLaunchError(
                "missing_raw_finding_for_scenario_family",
                f"scenario family {family_id} is missing a matching raw finding",
            )
        wave_rows.append(
            {
                "scenario": dict(scenario),
                "raw_finding": dict(raw_finding),
                "target_family": target_family,
                "native_probe_kind": _clean_text(scenario.get("native_probe_kind")),
                "recommended_agent_role": _clean_text(scenario.get("recommended_agent_role")),
                "native_family_packet_path": native_family_packet_path,
                "native_family_node_id": f"scenario::{scenario_id}",
                "budget": dict(anchor_payload.get("budget") or {}),
                "actual": dict(anchor_payload.get("actual") or {}),
                "baseline_health_snapshot": dict(anchor_payload.get("baseline_health_snapshot") or {}),
                "post_campaign_health_snapshot": dict(anchor_payload.get("post_campaign_health_snapshot") or {}),
                "reduced_bug_classes": [family_id],
                "unresolved_bug_classes": [f"{family_id}::needs_follow_up"],
                "residual_risk": str(anchor_payload.get("residual_risk") or ""),
                "blocked_claims": list(anchor_payload.get("blocked_claims") or []),
            }
        )
    child_rows, next_scenario_index = _queue_wave(
        request_fingerprint=request_fingerprint,
        request_id=request_id,
        scheduler_packet_path=scheduler_packet_path,
        repo=repo,
        dirs=dirs,
        scenarios=wave_rows,
        wave_index=1,
        next_scenario_index=1,
    )
    run_at = _utc_now()

    closeout_packet = {
        "schema_version": "verification_workflow_campaign_closeout_packet_v1",
        "request_fingerprint": request_fingerprint,
        "request_id": request_id,
        "planner_packet_path": _clean_text(packet.get("planner_packet_path")),
        "request_record_path": _clean_text(packet.get("request_record_path")),
        "target_family": target_family,
        "native_family_packet_path": native_family_packet_path,
        "wave_index": 1,
        "next_scenario_index": next_scenario_index,
        "child_jobs": child_rows,
    }
    closeout_path = dirs["closeout_packets"] / f"{request_fingerprint}.json"
    _write_campaign_record(closeout_path, closeout_packet)
    closeout_spec = _build_closeout_project_runner_spec(
        request_fingerprint=request_fingerprint,
        request_id=request_id,
        scheduler_packet_path=scheduler_packet_path,
        closeout_packet_path=str(closeout_path.relative_to(repo)),
        child_job_ids=[row["job_id"] for row in child_rows],
        target_family=target_family,
        native_family_packet_path=native_family_packet_path,
        run_at=run_at,
    )
    closeout_job = _scheduled_jobs.save_job(closeout_spec, repo_root=repo)
    campaign_record = {
        "schema_version": "verification_workflow_campaign_record_v1",
        "request_fingerprint": request_fingerprint,
        "request_id": request_id,
        "job_id": _clean_text(packet.get("job_id")),
        "execution_status": "running_children",
        "planner_packet_path": _clean_text(packet.get("planner_packet_path")),
        "request_record_path": _clean_text(packet.get("request_record_path")),
        "scheduler_packet_path": scheduler_packet_path,
        "target_family": target_family,
        "native_execution_family": deepcopy(packet.get("native_execution_family") or {}),
        "native_family_packet_path": native_family_packet_path,
        "current_wave_index": 1,
        "next_scenario_index": next_scenario_index,
        "queued_child_jobs": child_rows,
        "all_child_jobs": child_rows,
        "completed_wave_indices": [],
        "closeout_job_id": str(closeout_job.get("job_id") or ""),
        "closeout_packet_path": str(closeout_path.relative_to(repo)),
        "stop_reason": "child_campaign_running",
    }
    campaign_path = dirs["campaigns"] / f"{request_fingerprint}.json"
    _write_campaign_record(campaign_path, campaign_record)
    return {
        "schema_version": "verification_workflow_scheduler_run_result_v1",
        "campaign_record": campaign_record,
        "campaign_record_path": str(campaign_path.relative_to(repo)),
    }


def run_verification_scenario_packet(
    scenario_packet: Mapping[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    repo = (repo_root or Path(__file__).resolve().parent).resolve()
    dirs = _ensure_launch_dirs(repo)
    packet = _hydrate_scenario_packet_defaults(scenario_packet, repo=repo)
    request_fingerprint = _clean_text(packet.get("request_fingerprint"))
    scenario = dict(packet.get("scenario") or {})
    raw_finding = dict(packet.get("raw_finding") or {})
    if not request_fingerprint or not scenario:
        raise VerificationWorkflowLaunchError("invalid_scenario_packet", "scenario packet must include request_fingerprint and scenario")
    anchor_report = run_phase_b_anchor(
        {
            "scenarios": [scenario],
            "raw_findings": [raw_finding],
            "budget": dict(packet.get("budget") or {}),
            "actual": dict(packet.get("actual") or {}),
            "baseline_health_snapshot": dict(packet.get("baseline_health_snapshot") or {}),
            "post_campaign_health_snapshot": dict(packet.get("post_campaign_health_snapshot") or {}),
            "reduced_bug_classes": list(packet.get("reduced_bug_classes") or []),
            "unresolved_bug_classes": list(packet.get("unresolved_bug_classes") or []),
            "residual_risk": _clean_text(packet.get("residual_risk")),
            "blocked_claims": list(packet.get("blocked_claims") or []),
        }
    )
    scenario_id = _clean_text(scenario.get("scenario_id"))
    scenario_result = {
        "schema_version": "verification_workflow_scenario_run_v1",
        "request_fingerprint": request_fingerprint,
        "request_id": _clean_text(packet.get("request_id")),
        "scenario_id": scenario_id,
        "wave_index": int(packet.get("wave_index") or 1),
        "target_family": _clean_text(packet.get("target_family")),
        "native_probe_kind": _clean_text(packet.get("native_probe_kind")),
        "native_family_packet_path": _clean_text(packet.get("native_family_packet_path")),
        "native_family_node_id": _clean_text(packet.get("native_family_node_id")),
        "adaptive_root_cluster_id": _clean_text(packet.get("adaptive_root_cluster_id")),
        "anchor_report": anchor_report,
        "execution_status": "completed",
    }
    scenario_index = int(packet.get("scenario_index") or 0)
    scenario_path = _scenario_run_path(dirs, request_fingerprint, scenario_index)
    _write_campaign_record(scenario_path, scenario_result)
    return {
        "schema_version": "verification_workflow_scenario_result_v1",
        "scenario_result": scenario_result,
        "scenario_result_path": str(scenario_path.relative_to(repo)),
    }


def run_verification_campaign_closeout(
    closeout_packet: Mapping[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    repo = (repo_root or Path(__file__).resolve().parent).resolve()
    dirs = _ensure_launch_dirs(repo)
    packet = deepcopy(dict(closeout_packet or {}))
    request_fingerprint = _clean_text(packet.get("request_fingerprint"))
    child_jobs = list(packet.get("child_jobs") or [])
    if not request_fingerprint or not child_jobs:
        raise VerificationWorkflowLaunchError("invalid_closeout_packet", "closeout packet must include request_fingerprint and child_jobs")
    request_record_path = repo / _clean_text(packet.get("request_record_path"))
    if not request_record_path.exists():
        raise VerificationWorkflowLaunchError("missing_request_record", "closeout packet points to a missing request record")
    request_record = _load_json(request_record_path)
    campaign_path = dirs["campaigns"] / f"{request_fingerprint}.json"
    existing_campaign = _load_json(campaign_path) if campaign_path.exists() else {}
    aggregated_jobs: list[dict[str, Any]] = []
    aggregated_findings: list[dict[str, Any]] = []
    scenario_statuses: list[dict[str, Any]] = []
    for row in child_jobs:
        scenario_packet_path = repo / _clean_text(row.get("scenario_packet_path"))
        scenario_name = _clean_text(row.get("scenario_id"))
        scenario_index = int(_clean_text(scenario_packet_path.stem.split("__")[-1]) or "0")
        scenario_run_path = _scenario_run_path(dirs, request_fingerprint, scenario_index)
        if scenario_run_path.exists():
            payload = _load_json(scenario_run_path)
        else:
            fallback = _find_scenario_run_by_scenario_id(
                repo=repo,
                request_fingerprint=request_fingerprint,
                scenario_id=scenario_name,
            )
            if fallback is None:
                if not scenario_packet_path.exists():
                    raise VerificationWorkflowLaunchError(
                        "missing_scenario_run_artifact",
                        f"scenario result missing for {scenario_name}",
                    )
                rebuilt = run_verification_scenario_packet(
                    _load_json(scenario_packet_path),
                    repo_root=repo,
                )
                scenario_run_path = repo / _clean_text(rebuilt.get("scenario_result_path"))
                payload = _load_json(scenario_run_path)
            else:
                scenario_run_path, payload = fallback
        report = dict(payload.get("anchor_report") or {})
        aggregated_jobs.extend(list(report.get("compiled_jobs") or []))
        aggregated_findings.extend(list(report.get("normalized_defect_candidates") or []))
        scenario_statuses.append(
            {
                "scenario_id": scenario_name,
                "job_id": _clean_text(row.get("job_id")),
                "scenario_result_path": str(scenario_run_path.relative_to(repo)),
                "status": _clean_text(payload.get("execution_status")) or "completed",
                "wave_index": int(packet.get("wave_index") or 1),
                "native_probe_kind": _clean_text(payload.get("native_probe_kind")),
                "native_family_node_id": _clean_text(payload.get("native_family_node_id")),
            }
        )
    max_iterations = int(((request_record.get("normalized_request") or {}).get("max_iterations")) or 1)
    current_wave_index = int(packet.get("wave_index") or 1)
    followup_rows: list[dict[str, Any]] = []
    if current_wave_index < max_iterations:
        followup_rows = _next_wave_followup_rows(
            request_fingerprint=request_fingerprint,
            wave_index=current_wave_index,
            child_jobs=child_jobs,
            repo=repo,
        )
    if followup_rows:
        scheduler_packet_path = _clean_text(existing_campaign.get("scheduler_packet_path"))
        next_scenario_index = int(packet.get("next_scenario_index") or (len(list(existing_campaign.get("all_child_jobs") or [])) + 1))
        next_child_jobs, next_scenario_index = _queue_wave(
            request_fingerprint=request_fingerprint,
            request_id=_clean_text(packet.get("request_id")),
            scheduler_packet_path=scheduler_packet_path,
            repo=repo,
            dirs=dirs,
            scenarios=followup_rows,
            wave_index=current_wave_index + 1,
            next_scenario_index=next_scenario_index,
        )
        next_closeout_packet = {
            "schema_version": "verification_workflow_campaign_closeout_packet_v1",
            "request_fingerprint": request_fingerprint,
            "request_id": _clean_text(packet.get("request_id")),
            "planner_packet_path": _clean_text(packet.get("planner_packet_path")),
            "request_record_path": _clean_text(packet.get("request_record_path")),
            "target_family": _clean_text(existing_campaign.get("target_family")),
            "native_family_packet_path": _clean_text(existing_campaign.get("native_family_packet_path")),
            "wave_index": current_wave_index + 1,
            "next_scenario_index": next_scenario_index,
            "child_jobs": next_child_jobs,
        }
        closeout_path = dirs["closeout_packets"] / f"{request_fingerprint}__wave_{current_wave_index + 1:02d}.json"
        _write_campaign_record(closeout_path, next_closeout_packet)
        next_closeout_spec = _build_closeout_project_runner_spec(
            request_fingerprint=request_fingerprint,
            request_id=_clean_text(packet.get("request_id")),
            scheduler_packet_path=scheduler_packet_path,
            closeout_packet_path=str(closeout_path.relative_to(repo)),
            child_job_ids=[row["job_id"] for row in next_child_jobs],
            target_family=_clean_text(existing_campaign.get("target_family")),
            native_family_packet_path=_clean_text(existing_campaign.get("native_family_packet_path")),
            run_at=_utc_now(),
        )
        next_closeout_job = _scheduled_jobs.save_job(next_closeout_spec, repo_root=repo)
        campaign_record = {
            "schema_version": "verification_workflow_campaign_record_v1",
            "request_fingerprint": request_fingerprint,
            "request_id": _clean_text(packet.get("request_id")),
            "job_id": _clean_text(existing_campaign.get("job_id")),
            "execution_status": "running_children",
            "planner_packet_path": _clean_text(packet.get("planner_packet_path")),
            "request_record_path": _clean_text(packet.get("request_record_path")),
            "scheduler_packet_path": scheduler_packet_path,
            "target_family": _clean_text(existing_campaign.get("target_family")),
            "native_execution_family": deepcopy(existing_campaign.get("native_execution_family") or {}),
            "native_family_packet_path": _clean_text(existing_campaign.get("native_family_packet_path")),
            "current_wave_index": current_wave_index + 1,
            "next_scenario_index": next_scenario_index,
            "completed_wave_indices": list(existing_campaign.get("completed_wave_indices") or []) + [current_wave_index],
            "child_scenarios": list(existing_campaign.get("child_scenarios") or []) + scenario_statuses,
            "queued_child_jobs": next_child_jobs,
            "all_child_jobs": list(existing_campaign.get("all_child_jobs") or []) + next_child_jobs,
            "closeout_job_id": str(next_closeout_job.get("job_id") or ""),
            "closeout_packet_path": str(closeout_path.relative_to(repo)),
            "adaptive_followup_count": len(followup_rows),
            "stop_reason": "adaptive_followup_wave_running",
        }
        _write_campaign_record(campaign_path, campaign_record)
        return {
            "schema_version": "verification_workflow_campaign_closeout_result_v1",
            "campaign_record": campaign_record,
            "campaign_record_path": str(campaign_path.relative_to(repo)),
        }
    all_child_jobs = list(existing_campaign.get("all_child_jobs") or []) or child_jobs
    aggregated_jobs = []
    aggregated_findings = []
    final_statuses: list[dict[str, Any]] = []
    for row in all_child_jobs:
        scenario_packet_path = repo / _clean_text(row.get("scenario_packet_path"))
        scenario_index = int(_clean_text(scenario_packet_path.stem.split("__")[-1]) or "0")
        scenario_run_path = _scenario_run_path(dirs, request_fingerprint, scenario_index)
        if scenario_run_path.exists():
            payload = _load_json(scenario_run_path)
        else:
            fallback = _find_scenario_run_by_scenario_id(
                repo=repo,
                request_fingerprint=request_fingerprint,
                scenario_id=_clean_text(row.get("scenario_id")),
            )
            if fallback is None:
                continue
            scenario_run_path, payload = fallback
        report = dict(payload.get("anchor_report") or {})
        aggregated_jobs.extend(list(report.get("compiled_jobs") or []))
        aggregated_findings.extend(list(report.get("normalized_defect_candidates") or []))
        final_statuses.append(
            {
                "scenario_id": _clean_text(payload.get("scenario_id")),
                "job_id": _clean_text(row.get("job_id")),
                "scenario_result_path": str(scenario_run_path.relative_to(repo)),
                "status": _clean_text(payload.get("execution_status")) or "completed",
                "wave_index": int(payload.get("wave_index") or row.get("wave_index") or 1),
                "native_probe_kind": _clean_text(payload.get("native_probe_kind")),
                "native_family_node_id": _clean_text(payload.get("native_family_node_id")),
                "adaptive_root_cluster_id": _clean_text(payload.get("adaptive_root_cluster_id")),
            }
        )
    campaign_record = {
        "schema_version": "verification_workflow_campaign_record_v1",
        "request_fingerprint": request_fingerprint,
        "request_id": _clean_text(packet.get("request_id")),
        "job_id": _clean_text(existing_campaign.get("job_id")),
        "execution_status": "completed",
        "planner_packet_path": _clean_text(packet.get("planner_packet_path")),
        "request_record_path": _clean_text(packet.get("request_record_path")),
        "scheduler_packet_path": _clean_text(existing_campaign.get("scheduler_packet_path")),
        "target_family": _clean_text(existing_campaign.get("target_family")),
        "native_execution_family": deepcopy(existing_campaign.get("native_execution_family") or {}),
        "native_family_packet_path": _clean_text(existing_campaign.get("native_family_packet_path")),
        "current_wave_index": current_wave_index,
        "completed_wave_indices": list(existing_campaign.get("completed_wave_indices") or []) + [current_wave_index],
        "child_scenarios": final_statuses,
        "queued_child_jobs": [],
        "all_child_jobs": all_child_jobs,
        "anchor_report": {
            "schema_version": "verification_workflow_campaign_aggregate_v1",
            "compiled_jobs": aggregated_jobs,
            "normalized_defect_candidates": aggregated_findings,
            "maximum_supported_claim": "multi_wave_campaign_aggregated_only",
            "stop_reason": "campaign_complete_after_adaptive_followups" if current_wave_index > 1 else "child_campaign_complete",
        },
        "stop_reason": "campaign_complete_after_adaptive_followups" if current_wave_index > 1 else "child_campaign_complete",
    }
    _write_campaign_record(campaign_path, campaign_record)
    return {
        "schema_version": "verification_workflow_campaign_closeout_result_v1",
        "campaign_record": campaign_record,
        "campaign_record_path": str(campaign_path.relative_to(repo)),
    }


def create_scheduler_backed_verification_launch(
    raw: Mapping[str, Any],
    *,
    repo_root: Optional[Path] = None,
    run_now: bool = False,
) -> dict[str, Any]:
    repo = (repo_root or Path(__file__).resolve().parent).resolve()
    launch_result = create_verification_launch(raw, repo_root=repo)
    spec = build_scheduler_job_spec_for_verification_workflow(launch_result)
    saved_job = _scheduled_jobs.save_job(spec, repo_root=repo)
    retired_job_ids = _retire_superseded_verification_workflow_launchers(
        saved_job,
        launch_result.get("durable_request") if isinstance(launch_result.get("durable_request"), Mapping) else {},
        repo=repo,
    )
    run_payload: dict[str, Any] = {}
    if run_now:
        run_payload = _scheduled_jobs.request_run_now(str(saved_job.get("job_id") or ""), repo_root=repo, request_source="verification_workflow_launch")
    return {
        **launch_result,
        "scheduled_job": dict(saved_job),
        "retired_job_ids": retired_job_ids,
        "run_now": run_payload,
    }


def run_verification_scheduler_packet(
    scheduler_packet: Mapping[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    repo = (repo_root or Path(__file__).resolve().parent).resolve()
    dirs = _ensure_launch_dirs(repo)
    packet = deepcopy(dict(scheduler_packet or {}))
    request_fingerprint = _clean_text(packet.get("request_fingerprint"))
    if not request_fingerprint:
        raise VerificationWorkflowLaunchError("missing_request_fingerprint", "scheduler packet missing request fingerprint")
    request_record_path = repo / _clean_text(packet.get("request_record_path"))
    if not request_record_path.exists():
        raise VerificationWorkflowLaunchError("missing_request_record", "scheduler packet points to a missing request record")

    request_record = _load_json(request_record_path)
    raw_anchor_payload = packet.get("phase_b_anchor_payload") or {}
    if not isinstance(raw_anchor_payload, Mapping) or not dict(raw_anchor_payload):
        raise VerificationWorkflowLaunchError("missing_phase_b_anchor_payload", "scheduler packet missing anchor payload")
    anchor_payload = _normalize_phase_b_anchor_payload(raw_anchor_payload)
    if _clean_text(packet.get("planner_packet_path")) and len(list(anchor_payload.get("scenarios") or [])) > 1:
        return _orchestrate_child_campaign(
            packet=packet,
            request_record=request_record,
            anchor_payload=anchor_payload,
            repo=repo,
            dirs=dirs,
        )
    anchor_report = run_phase_b_anchor(anchor_payload)

    campaign_record = {
        "schema_version": "verification_workflow_campaign_record_v1",
        "request_fingerprint": request_fingerprint,
        "request_id": request_record["request_id"],
        "job_id": _clean_text(packet.get("job_id")),
        "execution_status": "completed",
        "planner_packet_path": _clean_text(packet.get("planner_packet_path")),
        "target_family": _clean_text(packet.get("target_family")),
        "native_execution_family": deepcopy(packet.get("native_execution_family") or {}),
        "native_family_packet_path": _clean_text(packet.get("native_family_packet_path")),
        "anchor_report": anchor_report,
        "stop_reason": anchor_report["stop_reason"],
    }
    campaign_path = dirs["campaigns"] / f"{request_fingerprint}.json"
    campaign_path.write_text(json.dumps(campaign_record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "schema_version": "verification_workflow_scheduler_run_result_v1",
        "campaign_record": campaign_record,
        "campaign_record_path": str(campaign_path.relative_to(repo)),
    }
