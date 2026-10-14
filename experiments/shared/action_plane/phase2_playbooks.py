"""Concrete phase-2 playbooks and composed execution reporting."""

from __future__ import annotations

import json

from .contract_source import load_contract
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from infra.pipeline.diff_utils import extract_target_files
from infra.pipeline.patch_ops import classify_apply_failure, preflight_validated_patch

from .outcomes import SUCCESS_OUTCOMES, classify_action_outcome
from .phase1_runtime import assess_pis_proof_gap_v1, normalize_patch_for_phase1
from .phase2_boundary import PHASE2_ACTION_FAMILY_IDS, PHASE2_PLAYBOOK_IDS, PHASE2_VERIFICATION_OWNER
from .phase2_graph import COMPOSED_EXECUTION_GRAPH_PATH, execute_composed_graph, load_composed_execution_graph
from .phase2_runtime import dispatch_phase2_action_request

COMPOSED_PLAYBOOK_REGISTRY_PATH = Path("governance/contracts/composed_playbook_registry_v1.json")
COMPOSED_ACTION_REPORT_SCHEMA_PATH = Path("governance/contracts/composed_action_report_schema_v1.json")


def _contract_root(repo_root: Optional[Path] = None) -> Path:
    default_root = Path(__file__).resolve().parents[3]
    if repo_root is None:
        return default_root
    candidate = Path(repo_root).resolve()
    if (candidate / COMPOSED_PLAYBOOK_REGISTRY_PATH).exists() and (candidate / COMPOSED_EXECUTION_GRAPH_PATH).exists():
        return candidate
    return default_root


def _execution_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_composed_playbook_registry(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _contract_root(repo_root)
    return _load_json((root / COMPOSED_PLAYBOOK_REGISTRY_PATH).resolve())


def load_composed_action_report_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _contract_root(repo_root)
    return _load_json((root / COMPOSED_ACTION_REPORT_SCHEMA_PATH).resolve())


def _playbook_rows(repo_root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    rows = list(load_composed_playbook_registry(repo_root=repo_root).get("playbooks") or [])
    return {str(row.get("playbook_id") or "").strip(): dict(row) for row in rows if str(row.get("playbook_id") or "").strip()}


def _graph_rows(repo_root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    rows = list(load_composed_execution_graph(repo_root=repo_root).get("graphs") or [])
    return {str(row.get("graph_id") or "").strip(): dict(row) for row in rows if str(row.get("graph_id") or "").strip()}


def validate_phase2_playbook_contracts(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    registry = load_composed_playbook_registry(repo_root=repo_root)
    graphs = _graph_rows(repo_root=repo_root)
    report_schema = load_composed_action_report_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    if str(registry.get("schema_version") or "").strip() != "composed_playbook_registry_v1":
        errors.append({"code": "unexpected_playbook_schema_version", "message": "playbook registry schema mismatch"})
    if str(report_schema.get("schema_version") or "").strip() != "composed_action_report_schema_v1":
        errors.append({"code": "unexpected_report_schema_version", "message": "composed action report schema mismatch"})
    rows = list(registry.get("playbooks") or [])
    playbook_ids = tuple(str(row.get("playbook_id") or "").strip() for row in rows if str(row.get("playbook_id") or "").strip())
    if playbook_ids != PHASE2_PLAYBOOK_IDS:
        errors.append({"code": "unexpected_playbook_ids", "message": "phase-2 playbook ids must match canonical order"})
    for row in rows:
        playbook_id = str(row.get("playbook_id") or "").strip()
        action_family = str(row.get("action_family") or "").strip()
        graph_id = str(row.get("graph_id") or "").strip()
        if action_family not in PHASE2_ACTION_FAMILY_IDS:
            errors.append({"code": "unknown_action_family", "message": f"{playbook_id} references unknown action family {action_family}"})
        if str(row.get("verification_owner") or "").strip() != PHASE2_VERIFICATION_OWNER:
            errors.append({"code": "wrong_verification_owner", "message": f"{playbook_id} must use {PHASE2_VERIFICATION_OWNER}"})
        if graph_id not in graphs:
            errors.append({"code": "missing_graph", "message": f"{playbook_id} references missing graph {graph_id}"})
    return {"ok": not errors, "errors": errors, "registry": registry, "report_schema": report_schema}


def _run_command(command: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=str(cwd), shell=True, text=True, capture_output=True, check=False)


def _run_validation_subset(commands: Sequence[str], *, cwd: Path) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    logs: List[str] = []
    for command in commands:
        text = str(command or "").strip()
        if not text:
            continue
        result = _run_command(text, cwd=cwd)
        rows.append({"command": text, "returncode": int(result.returncode), "passed": result.returncode == 0})
        if result.stdout:
            logs.append(result.stdout.strip())
        if result.stderr:
            logs.append(result.stderr.strip())
        if result.returncode != 0:
            break
    return {"rows": rows, "log": "\n".join(part for part in logs if part).strip()}


def _step_patch_ingest_repair_v1(*, request: Mapping[str, Any], artifacts: Mapping[str, Any], repo_root: Optional[Path], **_: Any) -> Dict[str, Any]:
    root = _execution_root(repo_root)
    patch_text = str(request.get("patch_text") or artifacts.get("patch_text") or "")
    patch_path = str(request.get("patch_path") or artifacts.get("patch_path") or "").strip()
    if patch_path and not patch_text:
        patch_text = (root / patch_path).read_text(encoding="utf-8")
    if not patch_text.strip():
        return {
            "step_outcome": "blocked",
            "proof_gaps": ["missing_patch_input"],
            "produced_artifacts": {},
            "pre_escalation_checklist": {},
            "fallback_allowed": False,
            "deterministic_recovery_remaining": 0,
        }
    normalized = normalize_patch_for_phase1(patch_text)
    normalized_patch = str(normalized.get("patch_text") or "")
    repair_classes = list(normalized.get("repairs_applied") or [])
    checklist = {
        "evidence_gathered": False,
        "failure_class_recorded": False,
        "deterministic_recovery_exhausted_or_inapplicable": False,
        "downstream_validation_contract_prepared": bool(list(request.get("validation_commands") or [])),
    }
    try:
        target_files = extract_target_files(normalized_patch)
        preflight_validated_patch(repo_root=str(root), patch_text=normalized_patch, expected_target_files=target_files)
        checklist["evidence_gathered"] = True
    except Exception as exc:  # pragma: no cover - narrow failure classification path
        failure_reason = str(getattr(exc, "code", "patch_contract_invalid") or "patch_contract_invalid")
        checklist["failure_class_recorded"] = True
        checklist["deterministic_recovery_exhausted_or_inapplicable"] = True
        return {
            "step_outcome": "failed_known",
            "proof_gaps": [failure_reason],
            "produced_artifacts": {
                "patch_failure_reason": failure_reason,
                "repair_classes_applied": repair_classes,
            },
            "pre_escalation_checklist": checklist,
            "fallback_allowed": bool(request.get("allow_fallback")),
            "deterministic_recovery_remaining": 0,
        }

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".patch", delete=False) as tmp:
        tmp.write(normalized_patch)
        tmp.flush()
        patch_file = Path(tmp.name)
    try:
        check = subprocess.run(["git", "apply", "--check", str(patch_file)], cwd=str(root), text=True, capture_output=True, check=False)
        check_log = ((check.stdout or "") + "\n" + (check.stderr or "")).strip()
        if check.returncode != 0:
            failure = classify_apply_failure(
                logs=check_log,
                patch_text=normalized_patch,
                changed_files=[],
                repo_root=str(root),
                expected_target_files=target_files,
            )
            failure_reason = str(failure.get("failure_reason_code") or "git_apply_failed")
            checklist["failure_class_recorded"] = True
            checklist["deterministic_recovery_exhausted_or_inapplicable"] = True
            return {
                "step_outcome": "failed_known",
                "proof_gaps": [failure_reason],
                "produced_artifacts": {
                    "patch_failure_reason": failure_reason,
                    "git_apply_check_log": check_log,
                    "repair_classes_applied": repair_classes,
                },
                "pre_escalation_checklist": checklist,
                "fallback_allowed": bool(request.get("allow_fallback")),
                "deterministic_recovery_remaining": 0,
            }
        apply_result = subprocess.run(["git", "apply", str(patch_file)], cwd=str(root), text=True, capture_output=True, check=False)
        apply_log = ((apply_result.stdout or "") + "\n" + (apply_result.stderr or "")).strip()
        if apply_result.returncode != 0:
            failure = classify_apply_failure(
                logs=apply_log,
                patch_text=normalized_patch,
                changed_files=[],
                repo_root=str(root),
                expected_target_files=target_files,
            )
            failure_reason = str(failure.get("failure_reason_code") or "git_apply_failed")
            checklist["failure_class_recorded"] = True
            checklist["deterministic_recovery_exhausted_or_inapplicable"] = True
            return {
                "step_outcome": "failed_known",
                "proof_gaps": [failure_reason],
                "produced_artifacts": {
                    "patch_failure_reason": failure_reason,
                    "git_apply_check_log": check_log,
                    "git_apply_log": apply_log,
                    "repair_classes_applied": repair_classes,
                },
                "pre_escalation_checklist": checklist,
                "fallback_allowed": bool(request.get("allow_fallback")),
                "deterministic_recovery_remaining": 0,
            }
        produced_artifacts = {
            "patch_step_result": {
                "target_files": target_files,
                "repair_classes_applied": repair_classes,
                "git_apply_check_log": check_log,
                "git_apply_log": apply_log,
            },
            "target_files": target_files,
            "evidence_artifacts": ["git_apply_check", "git_apply"],
            "validation_commands": list(request.get("validation_commands") or []),
            "patch_normalized": normalized_patch,
        }
        return {
            "step_outcome": "success",
            "proof_gaps": [],
            "produced_artifacts": produced_artifacts,
            "pre_escalation_checklist": checklist,
            "fallback_allowed": False,
            "deterministic_recovery_remaining": 0,
        }
    finally:
        patch_file.unlink(missing_ok=True)


def _step_build_prepare_v1(*, request: Mapping[str, Any], artifacts: Mapping[str, Any], **_: Any) -> Dict[str, Any]:
    evidence_artifacts = list(request.get("evidence_artifacts") or []) or ["build_prepare_summary"]
    validation_commands = list(request.get("validation_commands") or [])
    proof_gaps = [str(x).strip() for x in list(request.get("proof_gaps") or []) if str(x).strip()]
    produced = {
        "evidence_artifacts": evidence_artifacts,
        "validation_commands": validation_commands,
        "build_prepare": {
            "builder_summary": str(request.get("builder_summary") or "build preparation complete").strip(),
            "iteration_label": str(request.get("iteration_label") or "iteration").strip(),
        },
    }
    if proof_gaps:
        produced["proof_gaps"] = proof_gaps
    return {
        "step_outcome": "success_with_proof_gap" if proof_gaps else "success",
        "proof_gaps": proof_gaps,
        "produced_artifacts": produced,
        "pre_escalation_checklist": {},
        "fallback_allowed": False,
        "deterministic_recovery_remaining": 0,
        "confidence_delta": float(request.get("confidence_delta") or 1.0),
        "expected_gain": float(request.get("expected_gain") or 1.0),
        "cost_per_iteration": float(request.get("cost_per_iteration") or 0.0),
        "retry_count": int(request.get("retry_count") or 0),
        "stagnant_iterations": int(request.get("stagnant_iterations") or 0),
    }


def _step_status_reconcile_prepare_v1(*, request: Mapping[str, Any], artifacts: Mapping[str, Any], **_: Any) -> Dict[str, Any]:
    target_uid = str(request.get("target_uid") or request.get("target_pis_uid") or "").strip()
    drift_class = str(request.get("drift_class") or "status_drift").strip() or "status_drift"
    current_status = str(request.get("current_status") or "unknown").strip() or "unknown"
    expected_status = str(request.get("expected_status") or "reconciled").strip() or "reconciled"
    repo_mutation_requested = bool(request.get("repo_mutation_requested"))
    authority_scope = str(request.get("authority_scope") or "").strip()
    proof_gaps: List[str] = []
    if not target_uid:
        proof_gaps.append("missing_target_uid")
    if repo_mutation_requested and authority_scope != "repo_mutation":
        proof_gaps.append("unauthorized_repo_mutation")
    produced = {
        "status_reconcile_result": {
            "target_uid": target_uid,
            "drift_class": drift_class,
            "current_status": current_status,
            "expected_status": expected_status,
            "proposed_status": expected_status,
            "repo_mutation": False,
        },
        "evidence_artifacts": ["status_reconcile_proposal"],
        "validation_commands": list(request.get("validation_commands") or []),
    }
    return {
        "step_outcome": "success_with_proof_gap" if proof_gaps else "success",
        "proof_gaps": proof_gaps,
        "produced_artifacts": produced,
        "pre_escalation_checklist": {},
        "fallback_allowed": False,
        "deterministic_recovery_remaining": 0,
    }


def _step_validation_subset_v1(*, request: Mapping[str, Any], artifacts: Mapping[str, Any], repo_root: Optional[Path], **_: Any) -> Dict[str, Any]:
    root = _execution_root(repo_root)
    commands = [str(x).strip() for x in list(artifacts.get("validation_commands") or request.get("validation_commands") or []) if str(x).strip()]
    if not commands:
        return {
            "step_outcome": "success_with_proof_gap",
            "proof_gaps": ["missing_validation_commands"],
            "produced_artifacts": {"validation_commands_executed": [], "validation_results": []},
            "pre_escalation_checklist": {},
            "fallback_allowed": False,
            "deterministic_recovery_remaining": 0,
        }
    run = _run_validation_subset(commands, cwd=root)
    rows = list(run.get("rows") or [])
    passed = bool(rows) and all(bool(row.get("passed")) for row in rows)
    proof_gaps = [] if passed else ["validation_failed"]
    produced = {
        "validation_commands_executed": [str(row.get("command") or "").strip() for row in rows if str(row.get("command") or "").strip()],
        "validation_results": rows,
        "evidence_artifacts": ["validation_results"],
    }
    if run.get("log"):
        produced["validation_log"] = str(run.get("log") or "")
    return {
        "step_outcome": "success" if passed else "failed_known",
        "proof_gaps": proof_gaps,
        "produced_artifacts": produced,
        "validation_commands_executed": produced["validation_commands_executed"],
        "pre_escalation_checklist": {
            "evidence_gathered": True,
            "failure_class_recorded": not passed,
            "deterministic_recovery_exhausted_or_inapplicable": not passed,
            "downstream_validation_contract_prepared": True,
        },
        "fallback_allowed": bool(request.get("allow_fallback")) and not passed,
        "deterministic_recovery_remaining": 0,
    }


def _step_pis_assess_v1(*, request: Mapping[str, Any], artifacts: Mapping[str, Any], repo_root: Optional[Path], **_: Any) -> Dict[str, Any]:
    payload = {
        "target_pis_uid": str(request.get("target_pis_uid") or request.get("target_uid") or "").strip(),
        "pis_markdown": str(request.get("pis_markdown") or ""),
        "evidence_artifacts": list(artifacts.get("evidence_artifacts") or []) + ["pis_assess"],
        "validation_commands_executed": list(artifacts.get("validation_commands_executed") or []),
        "verifier_verdict": str(request.get("verifier_verdict") or ""),
        "allow_fallback": bool(request.get("allow_fallback")),
        "bounded_ambiguity_reason": str(request.get("bounded_ambiguity_reason") or "phase2_chain_assess").strip(),
    }
    result = assess_pis_proof_gap_v1(payload, repo_root=repo_root)
    return {
        "step_outcome": "success" if bool(result.get("closeable")) else "success_with_proof_gap",
        "proof_gaps": list(result.get("proof_gaps") or []),
        "produced_artifacts": {
            "pis_assess_result": result,
            "evidence_artifacts": list(payload["evidence_artifacts"]),
            "validation_commands_executed": list(payload["validation_commands_executed"]),
        },
        "pre_escalation_checklist": dict(result.get("pre_escalation_checklist") or {}),
        "fallback_allowed": bool(result.get("fallback_allowed")),
        "deterministic_recovery_remaining": 0,
    }


def _step_scheduler_verifier_gate_v1(*, request: Mapping[str, Any], artifacts: Mapping[str, Any], **_: Any) -> Dict[str, Any]:
    proof_gaps = [str(x).strip() for x in list(artifacts.get("proof_gaps") or []) if str(x).strip()]
    for key in ("pis_assess_result",):
        nested = artifacts.get(key)
        if isinstance(nested, dict):
            for gap in list(nested.get("proof_gaps") or []):
                text = str(gap or "").strip()
                if text and text not in proof_gaps:
                    proof_gaps.append(text)
    validation_commands = [str(x).strip() for x in list(artifacts.get("validation_commands_executed") or []) if str(x).strip()]
    evidence_artifacts = [str(x).strip() for x in list(artifacts.get("evidence_artifacts") or []) if str(x).strip()]
    verifier_verdict = str(request.get("verifier_verdict") or artifacts.get("verifier_verdict") or "").strip().upper()
    classified = classify_action_outcome(
        {
            "requested_outcome": "success_deterministic",
            "validation_commands_executed": validation_commands,
            "evidence_artifacts": evidence_artifacts,
            "verifier_verdict": verifier_verdict,
            "proof_gaps": proof_gaps,
        }
    )
    outcome = str(classified.get("outcome") or "failed_unknown_class").strip()
    if outcome in SUCCESS_OUTCOMES:
        chain_outcome = "completed_with_pass"
        terminal_reason = "verifier_pass"
    elif outcome == "proof_gap_present":
        chain_outcome = "completed_with_proof_gap"
        terminal_reason = "proof_gap_present"
    elif outcome == "blocked_requires_review":
        chain_outcome = "blocked"
        terminal_reason = "missing_pass_or_evidence"
    else:
        chain_outcome = "failed"
        terminal_reason = "verifier_failed"
    return {
        "step_outcome": "success" if chain_outcome == "completed_with_pass" else "failed_known",
        "proof_gaps": proof_gaps,
        "produced_artifacts": {
            "final_verifier_result": classified,
        },
        "validation_commands_executed": validation_commands,
        "verifier_verdict": verifier_verdict,
        "chain_outcome": chain_outcome,
        "terminal_reason": terminal_reason,
        "pre_escalation_checklist": {},
        "fallback_allowed": False,
        "deterministic_recovery_remaining": 0,
    }


_PHASE2_HANDLERS = {
    "patch_ingest_repair_v1": _step_patch_ingest_repair_v1,
    "build_prepare_v1": _step_build_prepare_v1,
    "status_reconcile_prepare_v1": _step_status_reconcile_prepare_v1,
    "validation_subset_v1": _step_validation_subset_v1,
    "pis_assess_v1": _step_pis_assess_v1,
    "scheduler_verifier_gate_v1": _step_scheduler_verifier_gate_v1,
}


def _build_chain_report(
    *,
    playbook_id: str,
    action_family: str,
    dispatch: Mapping[str, Any],
    graph_result: Mapping[str, Any],
    iteration_index: Optional[int] = None,
) -> Dict[str, Any]:
    schema = load_composed_action_report_schema()
    required = [str(x).strip() for x in list(schema.get("required_fields") or []) if str(x).strip()]
    artifacts = dict(graph_result.get("artifacts") or {})
    final_verifier = dict(artifacts.get("final_verifier_result") or {})
    report = {
        "schema_version": str(schema.get("schema_version") or "composed_action_report_schema_v1"),
        "chain_id": f"{playbook_id}{'' if iteration_index is None else f'::iteration::{iteration_index}'}",
        "playbook_id": playbook_id,
        "action_family": action_family,
        "request_classification": {
            "task_id": str(dispatch.get("request", {}).get("task_id") or ""),
            "entrypoint_id": str(dispatch.get("request", {}).get("entrypoint_id") or ""),
            "execution_mode": str(dispatch.get("request", {}).get("execution_mode") or ""),
            "context_bundle_id": str(dispatch.get("request", {}).get("context_bundle_id") or ""),
        },
        "executor": str(dispatch.get("routing", {}).get("executor") or ""),
        "verification_owner": str(dispatch.get("routing", {}).get("verification_owner") or PHASE2_VERIFICATION_OWNER),
        "step_records": list(graph_result.get("step_records") or []),
        "produced_artifacts": sorted(artifacts.keys()),
        "failure_escalation_log": [
            {
                "node_id": record.get("node_id"),
                "decision": record.get("decision"),
                "proof_gaps": list(record.get("proof_gaps") or []),
                "checklist_complete": record.get("checklist_complete"),
                "economic_stop_reasons": list(record.get("economic_stop_reasons") or []),
            }
            for record in list(graph_result.get("step_records") or [])
            if str(record.get("decision") or "") not in {"", "continue", "terminal", "continue_with_proof_gap_resolver"}
        ],
        "final_verifier_verdict": str(final_verifier.get("verifier_verdict") or "").strip().upper(),
        "final_chain_outcome": str(graph_result.get("final_outcome") or "blocked").strip() or "blocked",
        "terminal_reason": str(graph_result.get("terminal_reason") or "").strip(),
    }
    missing = [field for field in required if field not in report]
    if missing:
        raise ValueError(f"composed report missing required fields: {missing}")
    return report


def execute_patch_apply_chain_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    dispatch = dispatch_phase2_action_request(request, repo_root=repo_root)
    if not dispatch.get("ok"):
        return {"ok": False, "errors": list(dispatch.get("errors") or []), "phase": "dispatch"}
    playbook = _playbook_rows(repo_root=repo_root).get("patch_apply_chain_v1", {})
    graph = _graph_rows(repo_root=repo_root).get(str(playbook.get("graph_id") or "patch_apply_chain_v1"), {})
    graph_result = execute_composed_graph(graph, request=request, handlers=_PHASE2_HANDLERS, repo_root=repo_root)
    report = _build_chain_report(playbook_id="patch_apply_chain_v1", action_family="patch_apply", dispatch=dispatch, graph_result=graph_result)
    return {
        "ok": report["final_chain_outcome"] == "completed_with_pass",
        "playbook_id": "patch_apply_chain_v1",
        "dispatch": dispatch,
        "graph_result": graph_result,
        "report": report,
    }


def _build_iteration_request(base_request: Mapping[str, Any], iteration: Mapping[str, Any], index: int) -> Dict[str, Any]:
    merged = dict(base_request)
    merged.update(dict(iteration))
    merged["iteration_label"] = str(iteration.get("iteration_label") or f"iteration_{index + 1}")
    merged["retry_count"] = index
    return merged


def execute_build_verify_loop_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    dispatch = dispatch_phase2_action_request(request, repo_root=repo_root)
    if not dispatch.get("ok"):
        return {"ok": False, "errors": list(dispatch.get("errors") or []), "phase": "dispatch"}
    playbook = _playbook_rows(repo_root=repo_root).get("build_verify_loop_v1", {})
    graph = _graph_rows(repo_root=repo_root).get(str(playbook.get("graph_id") or "build_verify_iteration_v1"), {})
    iterations = list(request.get("iterations") or [])
    if not iterations:
        iterations = [dict(request)]
    loop_reports: List[Dict[str, Any]] = []
    final_outcome = "blocked"
    terminal_reason = "iterations_exhausted"
    for index, iteration in enumerate(iterations):
        iteration_request = _build_iteration_request(request, iteration, index)
        graph_result = execute_composed_graph(graph, request=iteration_request, handlers=_PHASE2_HANDLERS, repo_root=repo_root)
        report = _build_chain_report(playbook_id="build_verify_loop_v1", action_family="build_verify", dispatch=dispatch, graph_result=graph_result, iteration_index=index)
        loop_reports.append(report)
        final_outcome = report["final_chain_outcome"]
        terminal_reason = report["terminal_reason"]
        if final_outcome == "completed_with_pass":
            break
        if final_outcome in {"blocked", "failed", "escalated_and_blocked"}:
            break
        if final_outcome == "completed_with_proof_gap" and index == len(iterations) - 1:
            break
    return {
        "ok": final_outcome == "completed_with_pass",
        "playbook_id": "build_verify_loop_v1",
        "dispatch": dispatch,
        "iteration_reports": loop_reports,
        "final_chain_outcome": final_outcome,
        "terminal_reason": terminal_reason,
    }


def execute_status_reconcile_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    dispatch = dispatch_phase2_action_request(request, repo_root=repo_root)
    if not dispatch.get("ok"):
        return {"ok": False, "errors": list(dispatch.get("errors") or []), "phase": "dispatch"}
    playbook = _playbook_rows(repo_root=repo_root).get("status_reconcile_v1", {})
    graph = _graph_rows(repo_root=repo_root).get(str(playbook.get("graph_id") or "status_reconcile_v1"), {})
    graph_result = execute_composed_graph(graph, request=request, handlers=_PHASE2_HANDLERS, repo_root=repo_root)
    report = _build_chain_report(playbook_id="status_reconcile_v1", action_family="status_reconcile", dispatch=dispatch, graph_result=graph_result)
    return {
        "ok": report["final_chain_outcome"] == "completed_with_pass",
        "playbook_id": "status_reconcile_v1",
        "dispatch": dispatch,
        "graph_result": graph_result,
        "report": report,
    }


def execute_phase2_action_request(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    dispatch = dispatch_phase2_action_request(request, repo_root=repo_root)
    if not dispatch.get("ok"):
        return {"ok": False, "errors": list(dispatch.get("errors") or []), "phase": "dispatch"}
    playbook_id = str(dispatch.get("routing", {}).get("default_playbook_id") or "").strip()
    if playbook_id == "patch_apply_chain_v1":
        return execute_patch_apply_chain_v1(request, repo_root=repo_root)
    if playbook_id == "build_verify_loop_v1":
        return execute_build_verify_loop_v1(request, repo_root=repo_root)
    if playbook_id == "status_reconcile_v1":
        return execute_status_reconcile_v1(request, repo_root=repo_root)
    return {"ok": False, "errors": [{"code": "unknown_playbook", "message": f"unknown playbook {playbook_id}"}], "dispatch": dispatch}


def build_phase2_regression_report(results: Mapping[str, Any]) -> Dict[str, Any]:
    build_result = dict(results.get("build_verify") or {})
    reconcile_result = dict(results.get("status_reconcile") or {})
    patch_result = dict(results.get("patch_apply") or {})

    def _effective_report(report_like: Mapping[str, Any]) -> Dict[str, Any]:
        if isinstance(report_like.get("report"), dict):
            return dict(report_like.get("report") or {})
        iteration_reports = list(report_like.get("iteration_reports") or [])
        if iteration_reports:
            return dict(iteration_reports[-1] or {})
        return dict(report_like)

    def _pass_bound(report_like: Mapping[str, Any]) -> bool:
        report = _effective_report(report_like)
        final_outcome = str(report.get("final_chain_outcome") or report_like.get("final_chain_outcome") or "")
        final_verdict = str(report.get("final_verifier_verdict") or "").upper()
        if final_outcome == "completed_with_pass":
            return final_verdict == "PASS"
        return True

    def _verification_owner(report_like: Mapping[str, Any]) -> str:
        report = _effective_report(report_like)
        return str(report.get("verification_owner") or PHASE2_VERIFICATION_OWNER)

    def _escalations_have_checklist(report_like: Mapping[str, Any]) -> bool:
        records = []
        report = _effective_report(report_like)
        records.extend(list(report.get("step_records") or []))
        for prior in list(report_like.get("iteration_reports") or [])[:-1]:
            records.extend(list(dict(prior or {}).get("step_records") or []))
        for record in records:
            if str(record.get("decision") or "") == "escalate" and not bool(record.get("checklist_complete")):
                return False
        return True

    checks = {
        "build_verify_pass_bound": _pass_bound(build_result),
        "status_reconcile_pass_bound": _pass_bound(reconcile_result),
        "patch_apply_pass_bound": _pass_bound(patch_result),
        "verification_owner_singular": {
            _verification_owner(build_result),
            _verification_owner(reconcile_result),
            _verification_owner(patch_result),
        } == {PHASE2_VERIFICATION_OWNER},
        "escalation_requires_checklist": _escalations_have_checklist(build_result)
        and _escalations_have_checklist(reconcile_result)
        and _escalations_have_checklist(patch_result),
    }
    return {
        "schema_version": "action_plane_phase2_regression_report_v1",
        "checks": checks,
        "ok": all(bool(value) for value in checks.values()),
        "results": {
            "build_verify": build_result,
            "status_reconcile": reconcile_result,
            "patch_apply": patch_result,
        },
    }
