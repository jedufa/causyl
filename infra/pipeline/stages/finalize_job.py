from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.core import build_envelope, emit_pipeline_error, load_json, load_pipeline_config, save_json_atomic, utc_now_iso
from infra.pipeline.schemas import validate_finalization_result

STAGE_KEY = "script_15_finalize_job"
STATUS_PRECEDENCE_RULE = "aborted>needs_review>accepted"


def _truncate(value: Any, *, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"



def _dedupe_strings(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out



def _load_artifact(path: Path) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "name": path.name,
        "path": str(path.resolve()),
        "exists": path.exists(),
        "malformed": False,
        "payload": {},
        "result": {},
        "error": None,
    }
    if not path.exists():
        return state
    try:
        payload = load_json(path)
    except Exception as exc:  # pragma: no cover - stage behavior covers this
        state["malformed"] = True
        state["error"] = _truncate(exc)
        return state
    state["payload"] = payload
    state["result"] = dict(payload.get("result") or {})
    return state



def _run_dir_for(args_input: str, args_output: str) -> Path:
    input_path = Path(args_input)
    if input_path.exists() or input_path.parent.exists():
        return input_path.resolve().parent
    return Path(args_output).resolve().parent



def _load_input_payload(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return dict(load_json(path) or {})
    except Exception:
        return {}



def _payload_lineage(payload: Mapping[str, Any]) -> Tuple[str, str]:
    job_id = str(payload.get("job_id") or os.environ.get("PIPELINE_JOB_ID") or "")
    run_id = str(payload.get("run_id") or os.environ.get("PIPELINE_RUN_ID") or "")
    return job_id, run_id



def _artifact_root_value(artifact_state: Mapping[str, Any], result_key: str) -> Dict[str, Any]:
    return dict(dict(artifact_state.get("result") or {}).get(result_key) or {})



def _load_context(run_dir: Path) -> Dict[str, Dict[str, Any]]:
    return {
        "execution_contract": _load_artifact(run_dir / "07_execution_contract.json"),
        "apply": _load_artifact(run_dir / "10_apply_result.json"),
        "static": _load_artifact(run_dir / "11_static_verification.json"),
        "runtime": _load_artifact(run_dir / "12_runtime_verification.json"),
        "contract": _load_artifact(run_dir / "13_contract_verification.json"),
        "repair": _load_artifact(run_dir / "14_repair_decision.json"),
    }



def _execution_contract(context: Mapping[str, Any]) -> Dict[str, Any]:
    artifact = dict(context.get("execution_contract") or {})
    payload = dict(artifact.get("payload") or {})
    return dict(payload.get("execution_contract") or {})



def _verification_required(execution_contract: Mapping[str, Any], section_name: str) -> Optional[bool]:
    section = dict(execution_contract.get(section_name) or {})
    if "required" in section:
        return bool(section.get("required"))
    return None



def _artifact_status(block: Mapping[str, Any], *fields: str) -> str:
    for field in fields:
        value = block.get(field)
        if value is not None and str(value).strip():
            return str(value)
    return "missing"



def _artifact_explicitly_skipped(*, artifact_state: Mapping[str, Any], block: Mapping[str, Any], execution_contract: Mapping[str, Any], contract_key: str) -> bool:
    if artifact_state.get("exists") and not artifact_state.get("malformed"):
        if _artifact_status(block, f"{contract_key}_status") == "skipped":
            return True
        if contract_key == "static_verification" and _artifact_status(block, "static_verification_status") == "skipped":
            return True
        if contract_key == "runtime_verification" and _artifact_status(block, "runtime_verification_status") == "skipped":
            return True
    required = _verification_required(execution_contract, "stage11_static_verification" if contract_key == "static_verification" else "stage12_runtime_verification")
    return required is False



def _present_artifacts(run_dir: Path) -> List[str]:
    present: List[str] = []
    for name in [
        "10_apply_result.json",
        "11_static_verification.json",
        "12_runtime_verification.json",
        "13_contract_verification.json",
        "14_repair_decision.json",
        "15_finalization.json",
    ]:
        if (run_dir / name).exists():
            present.append(name)
    return present



def _job_state_branch(job_dir: Path, run_id: str) -> Optional[str]:
    job_state_path = job_dir / "job_state.json"
    if not job_state_path.exists() or not run_id:
        return None
    try:
        state = load_json(job_state_path)
    except Exception:
        return None
    run = dict(dict(state.get("runs") or {}).get(run_id) or {})
    branch_id = str(run.get("branch_id") or "").strip()
    return branch_id or None



def _approval_pending_summary(repair: Mapping[str, Any]) -> List[str]:
    items: List[str] = []
    for entry in list(repair.get("approval_items") or []):
        if isinstance(entry, Mapping):
            item = str(entry.get("required_action") or entry.get("approval") or entry.get("reason_code") or entry.get("issue_class") or "").strip()
        else:
            item = str(entry or "").strip()
        if item:
            items.append(item)
    if not items and repair.get("operator_decision_reason"):
        items.append(str(repair.get("operator_decision_reason")))
    return _dedupe_strings(items)



def _remediation_summary(repair: Mapping[str, Any]) -> List[str]:
    items: List[str] = []
    for entry in list(repair.get("auto_remediation_candidates") or []):
        if isinstance(entry, Mapping):
            item = str(entry.get("candidate") or entry.get("action") or entry.get("reason_code") or "").strip()
        else:
            item = str(entry or "").strip()
        if item:
            items.append(item)
    return _dedupe_strings(items)



def _review_action(
    *,
    repair: Mapping[str, Any],
    blocking_reason_codes: Sequence[str],
    strongly_expected_missing: Sequence[str],
    degraded_optional_missing: Sequence[str],
    contract_present: bool,
) -> Tuple[Optional[str], Optional[str]]:
    blocking = set(blocking_reason_codes)
    if not contract_present:
        return "inspect_contract_verification", "13_contract_verification.json"
    if strongly_expected_missing:
        return "inspect_missing_evidence", str(strongly_expected_missing[0])
    if degraded_optional_missing:
        return "inspect_missing_evidence", str(degraded_optional_missing[0])
    if "CONTRACT_SCOPE_VIOLATION" in blocking:
        return "review_repair_decision", "14_repair_decision.json"
    if repair.get("operator_decision_required") or repair.get("should_retry"):
        return "review_repair_decision", "14_repair_decision.json"
    if blocking:
        return "inspect_contract_verification", "13_contract_verification.json"
    return "review_finalization_evidence", "14_repair_decision.json"



def _finalize(context: Mapping[str, Any], run_dir: Path, run_id: str) -> Dict[str, Any]:
    execution_contract = _execution_contract(context)
    apply_artifact = dict(context.get("apply") or {})
    static_artifact = dict(context.get("static") or {})
    runtime_artifact = dict(context.get("runtime") or {})
    contract_artifact = dict(context.get("contract") or {})
    repair_artifact = dict(context.get("repair") or {})

    apply_result = _artifact_root_value(apply_artifact, "apply_result")
    static = _artifact_root_value(static_artifact, "static_verification")
    runtime = _artifact_root_value(runtime_artifact, "runtime_verification")
    contract = _artifact_root_value(contract_artifact, "contract_verification")
    repair = _artifact_root_value(repair_artifact, "repair_decision")

    required_artifacts_present = bool(contract_artifact.get("exists") and not contract_artifact.get("malformed") and repair_artifact.get("exists") and not repair_artifact.get("malformed"))
    strongly_expected_missing = []
    if not apply_artifact.get("exists"):
        strongly_expected_missing.append("10_apply_result.json")
    elif apply_artifact.get("malformed"):
        strongly_expected_missing.append("10_apply_result.json")

    optional_artifacts_missing: List[str] = []
    degraded_optional_missing: List[str] = []
    for artifact_name, artifact_state, block, contract_key in [
        ("11_static_verification.json", static_artifact, static, "static_verification"),
        ("12_runtime_verification.json", runtime_artifact, runtime, "runtime_verification"),
    ]:
        if artifact_state.get("exists") and not artifact_state.get("malformed"):
            continue
        optional_artifacts_missing.append(artifact_name)
        if not _artifact_explicitly_skipped(
            artifact_state=artifact_state,
            block=block,
            execution_contract=execution_contract,
            contract_key=contract_key,
        ):
            degraded_optional_missing.append(artifact_name)

    blocking_reason_codes = _dedupe_strings(list(repair.get("blocking_reason_codes") or []))
    approval_pending_summary = _approval_pending_summary(repair)
    remediation_summary = _remediation_summary(repair)

    contract_passed = bool(contract.get("passes")) and _artifact_status(contract, "contract_verification_status") == "satisfied"
    retry_pending = bool(repair.get("should_retry"))
    approval_pending = bool(repair.get("operator_decision_required")) or str(repair.get("retry_authorization_state") or "") == "requires_operator" or bool(approval_pending_summary)
    truth_degraded = bool(strongly_expected_missing or degraded_optional_missing or any(code in {"UPSTREAM_EVIDENCE_INCOMPLETE", "UPSTREAM_OUTPUT_MALFORMED", "RETRY_LINEAGE_DISAGREEMENT"} for code in blocking_reason_codes))

    aborted_reasons: List[str] = []
    review_reason_codes: List[str] = []
    unresolved_items: List[str] = []

    if not required_artifacts_present:
        aborted_reasons.append("required finalization evidence is missing or malformed")
    if str(repair.get("branch_action") or "") == "terminal_abort":
        aborted_reasons.append("repair loop fixed the terminal outcome as aborted")
    if "RETRY_LIMIT_EXHAUSTED" in blocking_reason_codes:
        aborted_reasons.append("retry budget exhausted")

    if not aborted_reasons:
        if blocking_reason_codes:
            review_reason_codes.extend(blocking_reason_codes)
            unresolved_items.append("blocking reason codes remain active")
        if strongly_expected_missing:
            review_reason_codes.append("UPSTREAM_EVIDENCE_INCOMPLETE")
            unresolved_items.append("strongly expected execution evidence is missing or malformed")
        if degraded_optional_missing:
            review_reason_codes.append("UPSTREAM_EVIDENCE_INCOMPLETE")
            unresolved_items.append("optional verification evidence is missing without explicit skip evidence")
        if retry_pending:
            review_reason_codes.append("RETRY_PENDING")
            unresolved_items.append("repair loop still recommends a retry path")
        if approval_pending:
            review_reason_codes.append("APPROVAL_PENDING")
            unresolved_items.append("operator approval or review is still required")
        if not contract_passed:
            review_reason_codes.append("CONTRACT_NOT_SATISFIED")
            unresolved_items.append("contract verification did not reach a satisfied state")
        if truth_degraded and not approval_pending and not retry_pending:
            review_reason_codes.append("TRUTH_DEGRADED")
            unresolved_items.append("material truth is degraded even though required artifacts are present")

    review_reason_codes = _dedupe_strings(review_reason_codes)
    unresolved_items = _dedupe_strings(unresolved_items + aborted_reasons)

    accepted = bool(
        required_artifacts_present
        and contract_passed
        and not blocking_reason_codes
        and not retry_pending
        and not approval_pending
        and not strongly_expected_missing
        and not degraded_optional_missing
    )

    if aborted_reasons:
        final_status = "aborted"
    elif review_reason_codes or not accepted:
        final_status = "needs_review" if not accepted else "accepted"
    else:
        final_status = "accepted"

    recommended_action: Optional[str] = None
    recommended_first_artifact: Optional[str] = None
    if final_status == "needs_review":
        recommended_action, recommended_first_artifact = _review_action(
            repair=repair,
            blocking_reason_codes=blocking_reason_codes,
            strongly_expected_missing=strongly_expected_missing,
            degraded_optional_missing=degraded_optional_missing,
            contract_present=bool(contract_artifact.get("exists") and not contract_artifact.get("malformed")),
        )

    return {
        "final_status": final_status,
        "final_workspace_path": apply_result.get("workspace_path"),
        "final_branch": _job_state_branch(run_dir.parent.parent, run_id),
        "persisted_artifacts": _present_artifacts(run_dir),
        "commit_created": False,
        "commit_sha": None,
        "decision_basis": {
            "contract_passed": contract_passed,
            "static_status": _artifact_status(static, "static_verification_status"),
            "runtime_status": _artifact_status(runtime, "runtime_verification_status"),
            "retry_mode": repair.get("retry_mode"),
            "retry_authorization_state": repair.get("retry_authorization_state"),
            "operator_decision_required": bool(repair.get("operator_decision_required")),
            "required_artifacts_present": required_artifacts_present,
            "optional_artifacts_missing": optional_artifacts_missing,
            "blocking_reason_codes": blocking_reason_codes,
            "status_precedence_rule_applied": STATUS_PRECEDENCE_RULE,
        },
        "unresolved_items": unresolved_items,
        "applied_remediation_summary": remediation_summary,
        "approval_pending_summary": approval_pending_summary,
        "review_reason_codes": review_reason_codes,
        "recommended_action": recommended_action,
        "recommended_first_artifact": recommended_first_artifact,
    }



def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Execution MVP stage 15 finalization")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    try:
        started = utc_now_iso()
        config = load_pipeline_config(repo_root=args.repo_root, stage_key=STAGE_KEY)
        input_path = Path(args.input)
        output_path = Path(args.output)
        run_dir = _run_dir_for(args.input, args.output)
        payload = _load_input_payload(input_path)
        job_id, run_id = _payload_lineage(payload)
        result = {"finalization": _finalize(_load_context(run_dir), run_dir, run_id)}
        validate_finalization_result(result)
        envelope = build_envelope(
            stage=STAGE_KEY,
            job_id=job_id,
            run_id=run_id,
            input_payload=payload,
            result=result,
            tool_version=str(config.get("pipeline", {}).get("tool_version")),
            started_at_iso=started,
        )
        save_json_atomic(output_path, envelope)
        return 0
    except Exception as exc:
        return emit_pipeline_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
