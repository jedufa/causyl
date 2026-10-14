from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping
from uuid import uuid4

from experiments_shared import (
    build_action_plane_all_phases_report_v1,
    build_request_decomposition_v1,
    execute_build_verify_loop_v1,
    execute_patch_apply_chain_v1,
    execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1,
    execute_phase3_blocked_chain_explain_and_recommend_v1,
    execute_status_reconcile_v1,
)
from infra.webui.ledger.ledger_manager import ChangeLedger


TERMINAL_STATUSES = {
    "ok",
    "proof_gap",
    "economic_stop_no_new_signal",
    "failed_verification",
    "scope_rejected",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _evidence_dir(repo_root: Path, scenario_name: str, run_id: str) -> Path:
    return repo_root / "runtime" / "state" / "e2e" / "action_plane" / scenario_name / run_id


def _proof_gaps_from_report(report: Mapping[str, Any]) -> list[str]:
    gaps: list[str] = []
    for row in list(report.get("step_records") or []):
        for gap in list(dict(row).get("proof_gaps") or []):
            text = str(gap or "").strip()
            if text and text not in gaps:
                gaps.append(text)
    return gaps


def _classify_phase2_receipt(output: Mapping[str, Any]) -> Dict[str, Any]:
    report = dict(output.get("report") or {})
    if not report:
        reports = list(output.get("iteration_reports") or [])
        report = dict(reports[-1] or {}) if reports else {}
    final_outcome = str(report.get("final_chain_outcome") or output.get("final_chain_outcome") or "").strip()
    terminal_reason = str(report.get("terminal_reason") or output.get("terminal_reason") or "").strip()
    proof_gaps = _proof_gaps_from_report(report)
    if terminal_reason == "economic_stop":
        return {"status": "economic_stop_no_new_signal", "proof_gap": True, "proof_gap_reason": ",".join(proof_gaps[:3])}
    if "unauthorized_repo_mutation" in proof_gaps:
        return {"status": "scope_rejected", "proof_gap": True, "proof_gap_reason": "unauthorized_repo_mutation"}
    if terminal_reason == "verifier_failed" or str(report.get("final_verifier_verdict") or "").strip().upper() == "FAIL":
        return {"status": "failed_verification", "proof_gap": False, "proof_gap_reason": ""}
    if final_outcome == "completed_with_pass":
        return {"status": "ok", "proof_gap": False, "proof_gap_reason": ""}
    return {"status": "proof_gap", "proof_gap": True, "proof_gap_reason": ",".join(proof_gaps[:3])}


def _classify_terminal_status(entrypoint: str, output: Mapping[str, Any]) -> Dict[str, Any]:
    if entrypoint == "lab_cli":
        mode = str(output.get("mode") or "").strip()
        if mode == "plan":
            return {
                "status": "ok" if bool(output.get("supported")) else "failed_verification",
                "proof_gap": False,
                "proof_gap_reason": "",
            }
        action_id = str(dict(output.get("selection") or {}).get("action_id") or "").strip()
        execution = dict(output.get("execution") or {})
        if action_id in {"patch_apply_chain_v1", "build_verify_loop_v1", "status_reconcile_v1"}:
            return _classify_terminal_status(
                {
                    "patch_apply_chain_v1": "execute_patch_apply_chain_v1",
                    "build_verify_loop_v1": "execute_build_verify_loop_v1",
                    "status_reconcile_v1": "execute_status_reconcile_v1",
                }[action_id],
                execution,
            )
        if action_id == "blocked_chain_explain_and_recommend_v1":
            return _classify_terminal_status("execute_phase3_blocked_chain_explain_and_recommend_v1", execution)
        if action_id == "repeated_patch_fallback_to_approved_upgrade_v1":
            return _classify_terminal_status("execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1", execution)
        outcome = dict(execution.get("outcome") or {})
        terminal = str(outcome.get("outcome") or output.get("terminal_class") or "").strip()
        proof_gaps = [str(x).strip() for x in list(outcome.get("proof_gaps") or []) if str(x).strip()]
        if terminal.startswith("success_"):
            return {"status": "ok", "proof_gap": False, "proof_gap_reason": ""}
        if terminal in {"blocked_requires_review", "proof_gap_present", "partial_success"}:
            return {"status": "proof_gap", "proof_gap": True, "proof_gap_reason": ",".join(proof_gaps[:3])}
        if terminal == "failed_with_known_gap":
            return {"status": "failed_verification", "proof_gap": bool(proof_gaps), "proof_gap_reason": ",".join(proof_gaps[:3])}
        return {"status": "failed_verification", "proof_gap": False, "proof_gap_reason": ""}
    if entrypoint in {"execute_patch_apply_chain_v1", "execute_build_verify_loop_v1", "execute_status_reconcile_v1"}:
        return _classify_phase2_receipt(output)
    if entrypoint == "execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1":
        economic = dict(output.get("economic_decision_output") or {})
        decision = str(economic.get("decision") or "").strip()
        return {
            "status": "ok" if bool(output.get("ok")) else "failed_verification",
            "proof_gap": decision == "rejected",
            "proof_gap_reason": str(economic.get("reason_class") or ""),
        }
    if entrypoint == "build_action_plane_all_phases_report_v1":
        phase2_result = dict(output.get("phase2_runtime_result") or {})
        if phase2_result:
            status = _classify_phase2_receipt(phase2_result)
        else:
            status = {"status": "failed_verification", "proof_gap": False, "proof_gap_reason": ""}
        phase4 = dict(output.get("phase4") or {})
        phase3 = dict(output.get("phase3") or {})
        economic = dict(phase4.get("economic_decision_output") or {})
        if status["status"] == "economic_stop_no_new_signal":
            return status
        if bool(dict(phase3.get("uncertainty_surface") or {}).get("proof_gap_present")):
            return {"status": "proof_gap", "proof_gap": True, "proof_gap_reason": ",".join(list(dict(phase3.get("uncertainty_surface") or {}).get("unresolved_items") or [])[:3])}
        if bool(output.get("ok")) and str(economic.get("decision") or "").strip() in {"approved", "rejected"}:
            return {"status": "ok", "proof_gap": False, "proof_gap_reason": ""}
        return status
    if entrypoint == "execute_phase3_blocked_chain_explain_and_recommend_v1":
        uncertainty = dict(output.get("uncertainty_surface") or {})
        if bool(uncertainty.get("proof_gap_present")):
            return {"status": "proof_gap", "proof_gap": True, "proof_gap_reason": ",".join(list(uncertainty.get("unresolved_items") or [])[:3])}
        return {"status": "ok" if bool(output.get("ok")) else "failed_verification", "proof_gap": False, "proof_gap_reason": ""}
    raise ValueError(f"unsupported E2E entrypoint: {entrypoint}")


def _build_snapshot(scenario: Mapping[str, Any], entrypoint: str, output: Mapping[str, Any], receipt: Mapping[str, Any]) -> Dict[str, Any]:
    if entrypoint == "lab_cli":
        surface = dict(output.get("operator_surface") or {})
        executive = dict(surface.get("executive_surface") or {})
        recommendations = dict(surface.get("recommendations") or {})
        primary = dict(recommendations.get("primary") or executive.get("primary_next_action") or {})
        codex = dict(surface.get("codex_transparency") or output.get("codex_fallback_request") or {})
        classification = dict(output.get("classification") or {})
        decomposition = dict(dict(classification.get("decomposition") or {}).get("decomposition") or {})
        execution = dict(output.get("execution") or {})
        iteration_reports = list(execution.get("iteration_reports") or [])
        return {
            "schema_version": "action_plane_e2e_snapshot_v1",
            "scenario_name": scenario["name"],
            "run_id": receipt["run_id"],
            "terminal_status": receipt["status"],
            "operator_state": str(executive.get("state") or surface.get("status_class") or output.get("terminal_class") or "unknown"),
            "proof_gap": bool(receipt["proof_gap"]),
            "mode": str(output.get("mode") or ""),
            "action_id": str(dict(output.get("selection") or {}).get("action_id") or ""),
            "authority_class": str(dict(decomposition.get("risk_authority_layer") or {}).get("authority_class") or ""),
            "execution_shape": str(dict(decomposition.get("work_shape_layer") or {}).get("execution_shape") or ""),
            "primary_action_id": str(primary.get("action_id") or ""),
            "primary_action_label": str(primary.get("label") or ""),
            "confidence": str(executive.get("confidence") or ""),
            "codex_used": bool(codex.get("codex_used")),
            "human_output_present": bool(str(output.get("_human_output") or "").strip()),
            "recovery_used": len(iteration_reports) > 1,
        }
    if entrypoint in {"execute_patch_apply_chain_v1", "execute_build_verify_loop_v1", "execute_status_reconcile_v1"}:
        report = dict(output.get("report") or {})
        if not report:
            reports = list(output.get("iteration_reports") or [])
            report = dict(reports[-1] or {}) if reports else {}
        return {
            "schema_version": "action_plane_e2e_snapshot_v1",
            "scenario_name": scenario["name"],
            "run_id": receipt["run_id"],
            "terminal_status": receipt["status"],
            "operator_state": str(report.get("final_chain_outcome") or "unknown"),
            "proof_gap": bool(receipt["proof_gap"]),
            "final_chain_outcome": str(report.get("final_chain_outcome") or ""),
            "terminal_reason": str(report.get("terminal_reason") or ""),
            "iteration_count": len(list(output.get("iteration_reports") or [])) or 1,
            "recovery_used": len(list(output.get("iteration_reports") or [])) > 1,
        }
    if entrypoint == "execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1":
        economic = dict(output.get("economic_decision_output") or {})
        upgrade = dict(output.get("upgrade_pipeline_output") or {})
        return {
            "schema_version": "action_plane_e2e_snapshot_v1",
            "scenario_name": scenario["name"],
            "run_id": receipt["run_id"],
            "terminal_status": receipt["status"],
            "operator_state": str(economic.get("decision") or "unknown"),
            "proof_gap": bool(receipt["proof_gap"]),
            "phase4_decision": str(economic.get("decision") or ""),
            "proposal_created": bool(upgrade.get("proposal_created")),
        }
    if entrypoint == "build_action_plane_all_phases_report_v1":
        phase3 = dict(output.get("phase3") or {})
        phase4 = dict(output.get("phase4") or {})
        return {
            "schema_version": "action_plane_e2e_snapshot_v1",
            "scenario_name": scenario["name"],
            "run_id": receipt["run_id"],
            "terminal_status": receipt["status"],
            "operator_state": str(dict(phase3.get("operator_surface") or {}).get("status_class") or "unknown"),
            "proof_gap": bool(dict(phase3.get("uncertainty_surface") or {}).get("proof_gap_present")),
            "phase4_decision": str(dict(phase4.get("economic_decision_output") or {}).get("decision") or ""),
            "proposal_created": bool(dict(phase4.get("upgrade_pipeline_output") or {}).get("proposal_created")),
            "phase_results": {
                "phase1": str(dict(output.get("phase1") or {}).get("decision") or ""),
                "phase2": bool(dict(output.get("phase2") or {}).get("ok")),
                "phase3": bool(phase3.get("ok")),
                "phase4": bool(phase4.get("ok")),
            },
        }

    return {
        "schema_version": "action_plane_e2e_snapshot_v1",
        "scenario_name": scenario["name"],
        "run_id": receipt["run_id"],
        "terminal_status": receipt["status"],
        "operator_state": str(dict(output.get("operator_surface") or {}).get("status_class") or "unknown"),
        "proof_gap": bool(dict(output.get("uncertainty_surface") or {}).get("proof_gap_present")),
        "phase_results": {
            "phase3": bool(output.get("ok")),
        },
    }


def _build_report(scenario: Mapping[str, Any], entrypoint: str, payload: Mapping[str, Any], output: Mapping[str, Any], receipt: Mapping[str, Any], snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    prompt_text = str(scenario.get("prompt_text") or "")
    report = {
        "schema_version": "action_plane_e2e_report_v1",
        "scenario_name": scenario["name"],
        "intent_sheet_uid": str(scenario.get("intent_sheet_uid") or ""),
        "entrypoint": entrypoint,
        "prompt_text": prompt_text,
        "status": receipt["status"],
        "proof_gap": receipt["proof_gap"],
        "operator_state": snapshot["operator_state"],
        "payload": dict(payload),
        "output": dict(output),
        "checks": {
            "receipt_snapshot_agree": receipt["status"] == snapshot["terminal_status"],
            "proof_gap_snapshot_agree": receipt["proof_gap"] == snapshot["proof_gap"],
        },
    }
    if entrypoint == "lab_cli":
        report["cli_args"] = list(output.get("_cli_args") or [])
        report["human_output"] = str(output.get("_human_output") or "")
        report["stderr"] = str(output.get("_stderr") or "")
        report["returncode"] = int(output.get("_returncode") or 0)
        report["checks"]["json_human_both_present"] = bool(str(output.get("_human_output") or "").strip()) and "operator_surface" in output
    return report


def _write_json(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)


def _resolve_scenario_path(scenario: Mapping[str, Any], relative_path: str) -> Path:
    return Path(str(scenario["scenario_root"])) / relative_path


def _materialize_payload(scenario: Mapping[str, Any], *, repo_root: Path) -> Dict[str, Any]:
    payload = dict(scenario.get("payload") or {})
    request = dict(payload.get("request") or payload)
    artifact_inputs = dict(scenario.get("artifact_inputs") or {})
    if "patch_file" in artifact_inputs:
        request["patch_text"] = _resolve_scenario_path(scenario, str(artifact_inputs["patch_file"])).read_text(encoding="utf-8")
    payload["request"] = request
    if scenario.get("entrypoint") == "build_action_plane_all_phases_report_v1":
        phase2_request = dict(payload.get("phase2_request") or {})
        if "patch_file" in artifact_inputs:
            phase2_request["patch_text"] = _resolve_scenario_path(scenario, str(artifact_inputs["patch_file"])).read_text(encoding="utf-8")
        payload["phase2_request"] = phase2_request
    return payload


def _source_repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _resolve_cli_args(scenario: Mapping[str, Any], *, repo_root: Path, seeded_target_pis_uid: str) -> list[str]:
    resolved: list[str] = []
    for raw in list(dict(scenario.get("payload") or {}).get("cli_args") or []):
        text = str(raw)
        if text == "{seed_target_pis_uid}":
            resolved.append(seeded_target_pis_uid)
            continue
        if text.startswith("{") and text.endswith("}"):
            key = text[1:-1]
            artifact_inputs = dict(scenario.get("artifact_inputs") or {})
            if key in artifact_inputs:
                resolved.append(str(_resolve_scenario_path(scenario, str(artifact_inputs[key]))))
                continue
        resolved.append(text)
    return resolved


def _run_lab_cli_entrypoint(scenario: Mapping[str, Any], *, repo_root: Path) -> Dict[str, Any]:
    seeded_target_pis_uid = ""
    if scenario.get("seed_target_pis"):
        ledger = ChangeLedger(repo_root=repo_root)
        seed = dict(scenario.get("seed_target_pis") or {})
        created = ledger.create_item(
            title=str(seed.get("title") or "e2e target").strip() or "e2e target",
            slug=str(seed.get("slug") or "e2e-target").strip() or "e2e-target",
            pis_markdown=str(seed.get("pis_markdown") or "# acceptance\n\n- prove item\n").strip() + "\n",
            project_id=str(seed.get("project_id") or "causyl"),
        )
        seeded_target_pis_uid = str(created["uid"])

    cli_args = ["--repo-root", str(repo_root), "--json"]
    cli_args.extend(_resolve_cli_args(scenario, repo_root=repo_root, seeded_target_pis_uid=seeded_target_pis_uid))
    prompt_text = str(scenario.get("prompt_text") or "").strip()
    if prompt_text:
        cli_args.append(prompt_text)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(_source_repo_root())
    command = [sys.executable, "-m", "infra.bin.lab", *cli_args]
    proc = subprocess.run(command, cwd=str(repo_root), env=env, capture_output=True, text=True)
    if proc.returncode not in {0, 1}:
        raise AssertionError(f"lab CLI failed unexpectedly for {scenario['name']}: rc={proc.returncode} stderr={proc.stderr}")
    parsed = json.loads(proc.stdout)

    human_args = [sys.executable, "-m", "infra.bin.lab", "--repo-root", str(repo_root)]
    if bool(scenario.get("verbose_human_output")):
        human_args.append("--verbose")
    human_args.extend(_resolve_cli_args(scenario, repo_root=repo_root, seeded_target_pis_uid=seeded_target_pis_uid))
    if prompt_text:
        human_args.append(prompt_text)
    human_proc = subprocess.run(human_args, cwd=str(repo_root), env=env, capture_output=True, text=True)
    if human_proc.returncode not in {0, 1}:
        raise AssertionError(
            f"lab CLI human render failed unexpectedly for {scenario['name']}: rc={human_proc.returncode} stderr={human_proc.stderr}"
        )

    parsed["_cli_args"] = cli_args
    parsed["_returncode"] = proc.returncode
    parsed["_stderr"] = proc.stderr
    parsed["_human_output"] = human_proc.stdout
    return parsed


def _seed_target_pis_if_requested(scenario: Mapping[str, Any], request: Dict[str, Any], *, repo_root: Path) -> None:
    seed = dict(scenario.get("seed_target_pis") or {})
    if not seed:
        return
    ledger = ChangeLedger(repo_root=repo_root)
    created = ledger.create_item(
        title=str(seed.get("title") or "e2e target").strip() or "e2e target",
        slug=str(seed.get("slug") or "e2e-target").strip() or "e2e-target",
        pis_markdown=str(seed.get("pis_markdown") or "# acceptance\n\n- prove item\n").strip() + "\n",
        project_id=str(seed.get("project_id") or "causyl"),
    )
    request["target_pis_uid"] = str(created["uid"])
    request.pop("pis_markdown", None)


def _run_phase2_entrypoint(entrypoint: str, request: Mapping[str, Any], *, repo_root: Path) -> Dict[str, Any]:
    if entrypoint == "execute_patch_apply_chain_v1":
        return execute_patch_apply_chain_v1(request, repo_root=repo_root)
    if entrypoint == "execute_build_verify_loop_v1":
        return execute_build_verify_loop_v1(request, repo_root=repo_root)
    if entrypoint == "execute_status_reconcile_v1":
        return execute_status_reconcile_v1(request, repo_root=repo_root)
    raise ValueError(f"unsupported phase2 E2E entrypoint: {entrypoint}")


def launch_scenario(scenario: Mapping[str, Any], *, repo_root: Path) -> Dict[str, Any]:
    entrypoint = str(scenario["entrypoint"])
    payload = _materialize_payload(scenario, repo_root=repo_root)
    run_id = f"ap_e2e_{uuid4().hex[:12]}"
    started_utc = _utc_now()

    if entrypoint == "build_action_plane_all_phases_report_v1":
        phase2_entrypoint = str(payload.get("phase2_entrypoint") or "execute_patch_apply_chain_v1").strip()
        phase2_request = dict(payload.get("phase2_request") or {})
        _seed_target_pis_if_requested(scenario, phase2_request, repo_root=repo_root)
        phase2_result = _run_phase2_entrypoint(phase2_entrypoint, phase2_request, repo_root=repo_root)
        full_payload = {
            "report": dict(phase2_result.get("report") or (list(phase2_result.get("iteration_reports") or [])[-1] if list(phase2_result.get("iteration_reports") or []) else {})),
            "fallback_events": list(payload.get("fallback_events") or []),
        }
        output = build_action_plane_all_phases_report_v1(full_payload, repo_root=repo_root)
        output["phase2_runtime_result"] = phase2_result
    elif entrypoint == "execute_phase3_blocked_chain_explain_and_recommend_v1":
        output = execute_phase3_blocked_chain_explain_and_recommend_v1(payload, repo_root=repo_root)
    elif entrypoint == "execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1":
        output = execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1(payload, repo_root=repo_root)
    elif entrypoint in {"execute_patch_apply_chain_v1", "execute_build_verify_loop_v1", "execute_status_reconcile_v1"}:
        request = dict(payload.get("request") or payload)
        _seed_target_pis_if_requested(scenario, request, repo_root=repo_root)
        output = _run_phase2_entrypoint(entrypoint, request, repo_root=repo_root)
    elif entrypoint == "lab_cli":
        output = _run_lab_cli_entrypoint(scenario, repo_root=repo_root)
    else:
        raise ValueError(f"unsupported E2E entrypoint: {entrypoint}")

    status_info = _classify_terminal_status(entrypoint, output)
    status = status_info["status"]
    proof_gap = bool(status_info["proof_gap"])
    proof_gap_reason = str(status_info["proof_gap_reason"] or "")

    evidence_dir = _evidence_dir(repo_root, scenario["name"], run_id)
    request = dict(payload.get("request") or payload)
    if entrypoint == "lab_cli":
        decomposition = dict(output.get("classification", {}).get("decomposition") or {})
    else:
        decomposition = build_request_decomposition_v1(
            {
                "request_text": str(scenario.get("prompt_text") or ""),
                "action_id": str(request.get("action_family") or ""),
                "authority_class": str(request.get("authority_scope") or "read_only"),
                "fallback_policy": "bounded",
                "artifact_presence_flags": {
                    "patch_supplied": bool(request.get("patch_text")),
                },
            },
        )
    stimulus = {
        "schema_version": "action_plane_e2e_stimulus_v1",
        "scenario_name": scenario["name"],
        "intent_sheet_uid": str(scenario.get("intent_sheet_uid") or ""),
        "prompt_text": str(scenario.get("prompt_text") or ""),
        "artifact_inputs": dict(scenario.get("artifact_inputs") or {}),
        "decomposition": decomposition,
    }
    if entrypoint == "lab_cli":
        stimulus["cli_args"] = list(output.get("_cli_args") or [])
    receipt = {
        "schema_version": "action_plane_e2e_receipt_v1",
        "scenario_name": scenario["name"],
        "run_id": run_id,
        "intent_sheet_uid": str(scenario.get("intent_sheet_uid") or ""),
        "entrypoint": entrypoint,
        "started_utc": started_utc,
        "completed_utc": _utc_now(),
        "status": status,
        "proof_gap": proof_gap,
        "proof_gap_reason": proof_gap_reason,
        "recovery_used": bool(_build_snapshot(scenario, entrypoint, output, {"run_id": run_id, "status": status, "proof_gap": proof_gap}).get("recovery_used")),
    }
    snapshot = _build_snapshot(scenario, entrypoint, output, receipt)
    report = _build_report(scenario, entrypoint, payload, output, receipt, snapshot)

    receipt_path = _write_json(evidence_dir / "receipt.json", receipt)
    snapshot_path = _write_json(evidence_dir / "snapshot.json", snapshot)
    report_path = _write_json(evidence_dir / "report.json", report)
    stimulus_path = _write_json(evidence_dir / "stimulus.json", stimulus)

    receipt["receipt_path"] = receipt_path
    receipt["snapshot_path"] = snapshot_path
    receipt["report_path"] = report_path
    receipt["stimulus_path"] = stimulus_path
    _write_json(Path(receipt_path), receipt)

    return {
        "scenario": dict(scenario),
        "run_id": run_id,
        "entrypoint": entrypoint,
        "receipt_path": receipt_path,
        "snapshot_path": snapshot_path,
        "report_path": report_path,
        "output": output,
    }
