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
from infra.pipeline.attempt_signatures import attempt_signatures_equivalent, build_normalized_attempt_signature
from infra.pipeline.autonomous_controller import ensure_controller_state
from infra.pipeline.failure_similarity import build_failure_signature, failure_cluster_id, find_similar_failures
from infra.pipeline.progress_model import compute_progress_snapshot
from infra.pipeline.schemas import validate_repair_decision_result
from infra.pipeline.strategy_policy import build_retry_contract, select_retry_strategy

STAGE_KEY = "script_14_repair_loop"

REQUIRED_ARTIFACT = "13_contract_verification.json"
STRONGLY_EXPECTED_ARTIFACTS = ("10_apply_result.json", "11_static_verification.json")
OPTIONAL_ARTIFACT = "12_runtime_verification.json"
INFORMATIVE_ARTIFACTS = ("07_patch_plan.json", "07_execution_contract.json")

MAX_FAILING_CHECKS = 5
MAX_FAILING_TESTS = 5
MAX_AFFECTED_FILES = 8
MAX_EXCERPTS = 4
MAX_EXCERPT_CHARS = 240
MAX_RATIONALE_ITEMS = 10
MAX_APPROVAL_ITEMS = 5
MAX_REMEDIATION_ITEMS = 5

REMEDIATION_ALLOWLIST = {
    "normalize_repo_paths",
    "retry_with_repo_local_tool",
    "rerun_with_corrected_cwd",
    "regenerate_patch_due_to_scope_drift",
    "scope_extension_requires_operator",
}

FAILURE_GROUPS: Dict[str, Tuple[str, ...]] = {
    "upstream_evidence": (
        "REQUIRED_ARTIFACT_MISSING",
        "UPSTREAM_EVIDENCE_INCOMPLETE",
        "UPSTREAM_STAGE_SKIPPED_BY_POLICY",
        "UPSTREAM_OUTPUT_MALFORMED",
        "RETRY_LINEAGE_DISAGREEMENT",
        "RETRY_LIMIT_EXHAUSTED",
    ),
    "execution_apply": (
        "PATCH_APPLY_DEGRADATION",
        "PATH_NORMALIZATION_MISMATCH",
    ),
    "verification_tooling": (
        "VERIFICATION_ENVIRONMENT_FAILURE",
        "OPTIONAL_TOOL_MISSING",
        "MANDATORY_TOOL_MISSING",
        "REAL_TEST_FAILURE",
    ),
    "contract_policy": (
        "CONTRACT_SCOPE_VIOLATION",
    ),
}

ALLOWED_BLOCKING_REASON_CODES = {code for values in FAILURE_GROUPS.values() for code in values}


def _truncate_text(value: Any, *, limit: int = MAX_EXCERPT_CHARS) -> str:
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
    except Exception as exc:  # pragma: no cover - exercised through stage behavior
        state["malformed"] = True
        state["error"] = _truncate_text(exc)
        return state
    state["payload"] = payload
    state["result"] = dict(payload.get("result") or {})
    return state


def _run_dir_for(args_input: str, args_output: str) -> Path:
    input_path = Path(args_input)
    if input_path.exists() or input_path.parent.exists():
        return input_path.resolve().parent
    return Path(args_output).resolve().parent


def _load_context(run_dir: Path) -> Dict[str, Dict[str, Any]]:
    return {
        "patch_plan": _load_artifact(run_dir / "07_patch_plan.json"),
        "execution_contract": _load_artifact(run_dir / "07_execution_contract.json"),
        "patch": _load_artifact(run_dir / "08_patch_artifact.json"),
        "validated": _load_artifact(run_dir / "09_validated_artifact.json"),
        "apply": _load_artifact(run_dir / "10_apply_result.json"),
        "static": _load_artifact(run_dir / "11_static_verification.json"),
        "runtime": _load_artifact(run_dir / "12_runtime_verification.json"),
        "contract": _load_artifact(run_dir / "13_contract_verification.json"),
    }


def _payload_lineage(payload: Mapping[str, Any]) -> Tuple[str, str]:
    job_id = str(payload.get("job_id") or os.environ.get("PIPELINE_JOB_ID") or "")
    run_id = str(payload.get("run_id") or os.environ.get("PIPELINE_RUN_ID") or "")
    return job_id, run_id


def _load_input_payload(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return dict(load_json(path) or {})
    except Exception:
        return {}


def _artifact_root_value(artifact_state: Mapping[str, Any], result_key: str) -> Dict[str, Any]:
    return dict(dict(artifact_state.get("result") or {}).get(result_key) or {})


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


def _is_runtime_legitimately_skipped(context: Mapping[str, Any], runtime_verification: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
    runtime_status = _artifact_status(runtime_verification, "runtime_verification_status")
    if runtime_status == "skipped":
        return True, "runtime verification artifact explicitly recorded a skipped status"
    execution_contract = _execution_contract(context)
    runtime_required = _verification_required(execution_contract, "stage12_runtime_verification")
    if runtime_required is False:
        return True, "execution contract marks runtime verification as not required"
    return False, None


def _load_job_state(job_dir: Path) -> Dict[str, Any]:
    path = job_dir / "job_state.json"
    if not path.exists():
        return {}
    try:
        return dict(load_json(path) or {})
    except Exception:
        return {}


def _branch_context(job_state: Mapping[str, Any], run_id: str, *, config: Mapping[str, Any]) -> Dict[str, Any]:
    runs = dict(job_state.get("runs") or {})
    branches = dict(job_state.get("branches") or {})
    run = dict(runs.get(run_id) or {})
    branch_id = str(run.get("branch_id") or job_state.get("active_branch_id") or "BRANCH-main")
    branch = dict(branches.get(branch_id) or {})
    controller = ensure_controller_state(dict(job_state), config).get("controller", {}) if job_state else {}
    branch_controller = dict(dict(controller.get("branches") or {}).get(branch_id) or {})
    return {
        "branch_id": branch_id,
        "branch_purpose": str(branch.get("branch_purpose") or branch_controller.get("branch_purpose") or "primary"),
        "branch_state": branch_controller,
        "controller": controller,
        "prior_attempts": list(controller.get("recent_attempts") or []),
    }


def _job_state_retry_count(job_state: Mapping[str, Any], run_id: str) -> Optional[int]:
    runs = dict(job_state.get("runs") or {})
    run = dict(runs.get(run_id) or {})
    retry_context = dict(run.get("retry_context") or {})
    for key in ("retry_count_total", "retry_count_in_branch"):
        value = retry_context.get(key)
        if isinstance(value, int) and value >= 0:
            return int(value)
    return None


def _derived_retry_count_from_prior_repairs(job_dir: Path, job_state: Mapping[str, Any], run_id: str) -> Tuple[Optional[int], List[str]]:
    runs = dict(job_state.get("runs") or {})
    current_run = dict(runs.get(run_id) or {})
    if not current_run:
        return None, []
    parent_run_id = str(current_run.get("parent_run_id") or "").strip()
    if not parent_run_id:
        return None, []
    seen = set()
    artifact_chain: List[str] = []
    retry_count = 0
    while parent_run_id and parent_run_id not in seen:
        seen.add(parent_run_id)
        artifact_path = job_dir / "runs" / parent_run_id / "14_repair_decision.json"
        if artifact_path.exists():
            artifact = _load_artifact(artifact_path)
            decision = _artifact_root_value(artifact, "repair_decision") if not artifact.get("malformed") else {}
            if decision:
                retry_count += 1
                artifact_chain.append(str(artifact_path.relative_to(job_dir)))
        parent_run = dict(runs.get(parent_run_id) or {})
        parent_run_id = str(parent_run.get("parent_run_id") or "").strip()
    return retry_count, artifact_chain


def _resolve_retry_lineage(*, job_dir: Path, run_id: str, retry_limit: int) -> Tuple[int, List[str], List[str]]:
    rationale: List[str] = []
    blocking_reason_codes: List[str] = []
    job_state = _load_job_state(job_dir)
    state_count = _job_state_retry_count(job_state, run_id) if run_id else None
    if state_count is not None:
        rationale.append(f"retry lineage from job_state={state_count}")
    derived_count, artifact_chain = _derived_retry_count_from_prior_repairs(job_dir, job_state, run_id) if run_id and job_state else (None, [])
    if derived_count is not None:
        rationale.append(f"retry lineage from prior repair artifacts={derived_count}")
    if state_count is not None and derived_count is not None and state_count != derived_count:
        blocking_reason_codes.append("RETRY_LINEAGE_DISAGREEMENT")
        retry_count = max(state_count, derived_count)
        rationale.append("retry lineage disagreement detected; using higher retry count to fail closed")
    elif state_count is not None:
        retry_count = state_count
    elif derived_count is not None:
        retry_count = derived_count
    else:
        retry_count = 0
        rationale.append("retry lineage defaulted to 0 because no explicit source was available")
    if retry_count >= retry_limit:
        blocking_reason_codes.append("RETRY_LIMIT_EXHAUSTED")
        rationale.append(f"retry limit exhausted ({retry_count} >= {retry_limit})")
    if artifact_chain:
        rationale.append("prior repair lineage: " + ", ".join(artifact_chain))
    return retry_count, _dedupe_strings(rationale), _dedupe_strings(blocking_reason_codes)


def _check_summary_entries(verification: Mapping[str, Any], *, prefer_keys: Sequence[str], limit: int) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    source_entries: List[Mapping[str, Any]] = []
    for key in prefer_keys:
        value = verification.get(key)
        if isinstance(value, list):
            source_entries = [dict(item) for item in value if isinstance(item, Mapping)]
            if source_entries:
                break
    for entry in source_entries:
        status = str(entry.get("status") or "").strip().lower()
        if status != "failed":
            continue
        item: Dict[str, Any] = {}
        for field in ("name", "status", "failure_reason", "exit_code", "command", "log_path"):
            if field in entry and entry.get(field) is not None:
                item[field] = entry.get(field)
        if item:
            entries.append(item)
        if len(entries) >= limit:
            break
    return entries


def _log_excerpt(log_path: str) -> Optional[str]:
    candidate = Path(str(log_path or "").strip())
    if not candidate.exists() or not candidate.is_file():
        return None
    try:
        lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None
    useful: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("name:") or stripped.lower().startswith("command:") or stripped.lower().startswith("cwd:"):
            continue
        useful.append(_truncate_text(stripped))
        if len(useful) >= 2:
            break
    if not useful:
        return None
    return " | ".join(useful)


def _structured_excerpts(*, apply_result: Mapping[str, Any], static: Mapping[str, Any], runtime: Mapping[str, Any], contract: Mapping[str, Any]) -> List[str]:
    excerpts: List[str] = []
    for block_name, block in (("apply", apply_result), ("static", static), ("runtime", runtime), ("contract", contract)):
        diagnostics = list(block.get("diagnostics") or [])
        for item in diagnostics:
            if not isinstance(item, Mapping):
                continue
            message = str(item.get("message") or item.get("summary") or "").strip()
            if not message:
                continue
            evidence = str(item.get("evidence") or item.get("detail") or item.get("failure_detail") or "").strip()
            text = f"{block_name}: {message}"
            if evidence:
                text += f" ({evidence})"
            excerpts.append(_truncate_text(text))
            if len(excerpts) >= MAX_EXCERPTS:
                return _dedupe_strings(excerpts)
    return _dedupe_strings(excerpts)


def _feedback_bundle(*, apply_result: Mapping[str, Any], static: Mapping[str, Any], runtime: Mapping[str, Any], contract: Mapping[str, Any]) -> Dict[str, Any]:
    affected_files = _dedupe_strings(
        list(apply_result.get("changed_files") or [])
        + list(dict(contract.get("scope_assessment") or {}).get("unexpected_files_touched") or [])
        + list(dict(contract.get("scope_assessment") or {}).get("validated_target_mismatches") or [])
    )[:MAX_AFFECTED_FILES]
    failing_checks = _check_summary_entries(static, prefer_keys=("executed_checks", "checks"), limit=MAX_FAILING_CHECKS)
    failing_tests = _check_summary_entries(runtime, prefer_keys=("test_runs", "executed_checks"), limit=MAX_FAILING_TESTS)
    excerpts = _structured_excerpts(apply_result=apply_result, static=static, runtime=runtime, contract=contract)
    if not excerpts:
        for item in failing_checks + failing_tests:
            excerpt = _log_excerpt(str(item.get("log_path") or ""))
            if excerpt:
                excerpts.append(excerpt)
            if len(excerpts) >= MAX_EXCERPTS:
                break
    return {
        "failing_checks": failing_checks[:MAX_FAILING_CHECKS],
        "failing_tests": failing_tests[:MAX_FAILING_TESTS],
        "affected_files": affected_files,
        "excerpts": _dedupe_strings(excerpts)[:MAX_EXCERPTS],
    }


def _classify_upstream_evidence(
    *,
    context: Mapping[str, Any],
    static_required_equivalent: bool,
    runtime_legitimately_skipped: bool,
) -> Tuple[List[str], List[str]]:
    codes: List[str] = []
    rationale: List[str] = []
    contract_artifact = dict(context.get("contract") or {})
    if not contract_artifact.get("exists") or contract_artifact.get("malformed"):
        codes.append("REQUIRED_ARTIFACT_MISSING")
        if contract_artifact.get("malformed"):
            codes.append("UPSTREAM_OUTPUT_MALFORMED")
            rationale.append("required contract verification artifact is malformed")
        else:
            rationale.append("required contract verification artifact is missing")
        return _dedupe_strings(codes), rationale

    apply_artifact = dict(context.get("apply") or {})
    if not apply_artifact.get("exists"):
        codes.append("UPSTREAM_EVIDENCE_INCOMPLETE")
        rationale.append("apply result artifact is missing")
    elif apply_artifact.get("malformed"):
        codes.extend(["UPSTREAM_EVIDENCE_INCOMPLETE", "UPSTREAM_OUTPUT_MALFORMED"])
        rationale.append("apply result artifact is malformed")

    static_artifact = dict(context.get("static") or {})
    if not static_artifact.get("exists"):
        if static_required_equivalent:
            codes.append("UPSTREAM_EVIDENCE_INCOMPLETE")
            rationale.append("static verification artifact is missing without equivalent truth source")
    elif static_artifact.get("malformed"):
        codes.extend(["UPSTREAM_EVIDENCE_INCOMPLETE", "UPSTREAM_OUTPUT_MALFORMED"])
        rationale.append("static verification artifact is malformed")

    runtime_artifact = dict(context.get("runtime") or {})
    if not runtime_artifact.get("exists"):
        if not runtime_legitimately_skipped:
            codes.append("UPSTREAM_EVIDENCE_INCOMPLETE")
            rationale.append("runtime verification artifact is silently absent")
    elif runtime_artifact.get("malformed"):
        codes.extend(["UPSTREAM_EVIDENCE_INCOMPLETE", "UPSTREAM_OUTPUT_MALFORMED"])
        rationale.append("runtime verification artifact is malformed")

    return _dedupe_strings(codes), _dedupe_strings(rationale)


def _classify_execution_apply(apply_result: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    codes: List[str] = []
    rationale: List[str] = []
    apply_status = _artifact_status(apply_result, "apply_status")
    diagnostics = dict(apply_result.get("diagnostics") or {})
    reason_code = str(diagnostics.get("failure_reason_code") or "").strip().lower()
    if apply_status in {"applied_with_offsets", "conflict", "rejected"}:
        codes.append("PATCH_APPLY_DEGRADATION")
        rationale.append(f"apply status indicates degradation: {apply_status}")
    if "path" in reason_code and "normal" in reason_code:
        codes.append("PATH_NORMALIZATION_MISMATCH")
        rationale.append("apply diagnostics indicate a path normalization mismatch")
    return _dedupe_strings(codes), _dedupe_strings(rationale)


def _classify_verification_tooling(static: Mapping[str, Any], runtime: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    codes: List[str] = []
    rationale: List[str] = []
    for label, block in (("static", static), ("runtime", runtime)):
        status = _artifact_status(block, f"{label}_verification_status")
        if status == "failed":
            checks = list(block.get("executed_checks") or block.get("test_runs") or [])
            missing_exec = any(str(dict(item).get("failure_reason") or "") == "missing_executable" for item in checks if isinstance(item, Mapping))
            if missing_exec:
                codes.append("MANDATORY_TOOL_MISSING")
                rationale.append(f"{label} verification failed because a required executable was missing")
            elif label == "runtime":
                codes.append("REAL_TEST_FAILURE")
                rationale.append("runtime verification reported a real test or build failure")
            else:
                codes.append("VERIFICATION_ENVIRONMENT_FAILURE")
                rationale.append("static verification failed without a narrower deterministic tool classification")
        for diagnostic in list(block.get("diagnostics") or []):
            if not isinstance(diagnostic, Mapping):
                continue
            reason_code = str(diagnostic.get("reason_code") or "")
            if reason_code in {"missing_repo_root", "invalid_static_profile", "invalid_runtime_profile"}:
                codes.append("VERIFICATION_ENVIRONMENT_FAILURE")
                rationale.append(f"{label} verification diagnostics indicate an environment or profile failure")
    return _dedupe_strings(codes), _dedupe_strings(rationale)


def _classify_contract_policy(contract: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    codes: List[str] = []
    rationale: List[str] = []
    status = _artifact_status(contract, "contract_verification_status")
    scope = dict(contract.get("scope_assessment") or {})
    decision = dict(contract.get("decision") or {})
    if status == "violated" and (
        not bool(scope.get("allowed_files_only", True))
        or bool(scope.get("forbidden_files_touched"))
        or "scope_violation" in [str(item) for item in list(decision.get("reason_codes") or [])]
    ):
        codes.append("CONTRACT_SCOPE_VIOLATION")
        rationale.append("contract verification reported a deterministic scope violation")
    return _dedupe_strings(codes), _dedupe_strings(rationale)


def _candidate(candidate: str, *, reason_code: str, details: str) -> Dict[str, Any]:
    if candidate not in REMEDIATION_ALLOWLIST:
        raise ValueError(f"unsupported remediation candidate: {candidate}")
    return {
        "candidate": candidate,
        "reason_code": str(reason_code),
        "details": _truncate_text(details),
    }


def _build_remediation_candidates(*, apply_codes: Sequence[str], verification_codes: Sequence[str], contract_codes: Sequence[str]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    if "PATH_NORMALIZATION_MISMATCH" in apply_codes:
        candidates.append(_candidate("normalize_repo_paths", reason_code="PATH_NORMALIZATION_MISMATCH", details="normalize repo-relative paths before regenerating the patch artifact"))
    if "MANDATORY_TOOL_MISSING" in verification_codes:
        candidates.append(_candidate("retry_with_repo_local_tool", reason_code="MANDATORY_TOOL_MISSING", details="retry verification with a repo-local or configured tool path instead of assuming the executable exists globally"))
    if "VERIFICATION_ENVIRONMENT_FAILURE" in verification_codes:
        candidates.append(_candidate("rerun_with_corrected_cwd", reason_code="VERIFICATION_ENVIRONMENT_FAILURE", details="rerun verification with the corrected workspace root and cwd semantics"))
    if "CONTRACT_SCOPE_VIOLATION" in contract_codes:
        candidates.append(_candidate("regenerate_patch_due_to_scope_drift", reason_code="CONTRACT_SCOPE_VIOLATION", details="regenerate the patch with stricter scope controls because the observed changes drifted outside contract scope"))
        candidates.append(_candidate("scope_extension_requires_operator", reason_code="CONTRACT_SCOPE_VIOLATION", details="scope expansion or policy change requires explicit operator approval"))
    return candidates[:MAX_REMEDIATION_ITEMS]


def _blocking_primary_hypothesis(codes: Sequence[str]) -> Optional[str]:
    for group_name in ("upstream_evidence", "contract_policy", "execution_apply", "verification_tooling"):
        for code in FAILURE_GROUPS[group_name]:
            if code in codes:
                return code
    return None


def _exact_terminal_abort(
    *,
    retry_count: int,
    retry_limit: int,
    blocking_reason_codes: Sequence[str],
    rationale_trace: Sequence[str],
    failure_summary: str,
    primary_hypothesis: str,
) -> Dict[str, Any]:
    codes = _dedupe_strings(["REQUIRED_ARTIFACT_MISSING", *blocking_reason_codes])
    return {
        "should_retry": False,
        "retry_reason": failure_summary,
        "retry_mode": "abort",
        "retry_authorization_state": "not_allowed",
        "branch_action": "terminal_abort",
        "recommended_reentry_stage": None,
        "failure_summary": failure_summary,
        "failure_classification": {
            "primary_hypothesis": primary_hypothesis,
            "competing_hypotheses": [code for code in codes if code != primary_hypothesis][:3],
            "confidence_score": 0.98,
        },
        "rationale_trace": list(rationale_trace)[:MAX_RATIONALE_ITEMS],
        "minimal_feedback_bundle": {
            "failing_checks": [],
            "failing_tests": [],
            "affected_files": [],
            "excerpts": [],
        },
        "retry_count": int(retry_count),
        "retry_limit": int(retry_limit),
        "operator_decision_required": False,
        "operator_decision_reason": None,
        "approval_items": [],
        "auto_remediation_candidates": [],
        "blocking_reason_codes": codes,
    }


def _review_or_abort_decision(
    *,
    retry_count: int,
    retry_limit: int,
    blocking_reason_codes: Sequence[str],
    rationale_trace: Sequence[str],
    failure_summary: str,
    feedback_bundle: Mapping[str, Any],
    remediation_candidates: Sequence[Mapping[str, Any]],
    recommended_first_artifact: str,
    operator_reason: str,
) -> Dict[str, Any]:
    blocking = _dedupe_strings(blocking_reason_codes)
    primary = _blocking_primary_hypothesis(blocking) or "UPSTREAM_EVIDENCE_INCOMPLETE"
    approval_items = [
        {
            "reason_code": primary,
            "artifact": recommended_first_artifact,
            "required_action": "operator_review",
        }
    ]
    return {
        "should_retry": False,
        "retry_reason": operator_reason,
        "retry_mode": "abort",
        "retry_authorization_state": "requires_operator",
        "branch_action": "no_action",
        "recommended_reentry_stage": None,
        "failure_summary": failure_summary,
        "failure_classification": {
            "primary_hypothesis": primary,
            "competing_hypotheses": [code for code in blocking if code != primary][:3],
            "confidence_score": 0.83,
        },
        "rationale_trace": list(rationale_trace)[:MAX_RATIONALE_ITEMS],
        "minimal_feedback_bundle": dict(feedback_bundle),
        "retry_count": int(retry_count),
        "retry_limit": int(retry_limit),
        "operator_decision_required": True,
        "operator_decision_reason": operator_reason,
        "approval_items": approval_items[:MAX_APPROVAL_ITEMS],
        "auto_remediation_candidates": [dict(item) for item in list(remediation_candidates or [])][:MAX_REMEDIATION_ITEMS],
        "blocking_reason_codes": blocking,
    }


def _clean_pass_decision(*, retry_count: int, retry_limit: int, affected_files: Sequence[str], rationale_trace: Sequence[str]) -> Dict[str, Any]:
    return {
        "should_retry": False,
        "retry_reason": "no retry required",
        "retry_mode": "none",
        "retry_authorization_state": "not_allowed",
        "branch_action": "no_action",
        "recommended_reentry_stage": None,
        "failure_summary": "All required evidence is present and the contract verification passed without blocking conditions.",
        "failure_classification": {
            "primary_hypothesis": None,
            "competing_hypotheses": [],
            "confidence_score": 1.0,
        },
        "rationale_trace": list(rationale_trace)[:MAX_RATIONALE_ITEMS],
        "minimal_feedback_bundle": {
            "failing_checks": [],
            "failing_tests": [],
            "affected_files": list(affected_files)[:MAX_AFFECTED_FILES],
            "excerpts": [],
        },
        "retry_count": int(retry_count),
        "retry_limit": int(retry_limit),
        "operator_decision_required": False,
        "operator_decision_reason": None,
        "approval_items": [],
        "auto_remediation_candidates": [],
        "blocking_reason_codes": [],
    }


def _scope_retry_decision(
    *,
    retry_count: int,
    retry_limit: int,
    rationale_trace: Sequence[str],
    feedback_bundle: Mapping[str, Any],
    remediation_candidates: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    blocking = ["CONTRACT_SCOPE_VIOLATION"]
    return {
        "should_retry": True,
        "retry_reason": "scope or forbidden-path violation requires operator-approved regeneration",
        "retry_mode": "patch_regenerate",
        "retry_authorization_state": "requires_operator",
        "branch_action": "fork_new_branch",
        "recommended_reentry_stage": "script_8_patch_generate",
        "failure_summary": "Contract verification found scope drift or forbidden file changes.",
        "failure_classification": {
            "primary_hypothesis": "CONTRACT_SCOPE_VIOLATION",
            "competing_hypotheses": [],
            "confidence_score": 0.92,
        },
        "rationale_trace": list(rationale_trace)[:MAX_RATIONALE_ITEMS],
        "minimal_feedback_bundle": dict(feedback_bundle),
        "retry_count": int(retry_count),
        "retry_limit": int(retry_limit),
        "operator_decision_required": True,
        "operator_decision_reason": "scope expansion or policy change would be required",
        "approval_items": [
            {
                "reason_code": "CONTRACT_SCOPE_VIOLATION",
                "artifact": REQUIRED_ARTIFACT,
                "required_action": "approve_scope_change_or_abort",
            }
        ],
        "auto_remediation_candidates": [dict(item) for item in list(remediation_candidates or [])][:MAX_REMEDIATION_ITEMS],
        "blocking_reason_codes": blocking,
    }


def _mechanical_retry_decision(
    *,
    retry_count: int,
    retry_limit: int,
    blocking_reason_codes: Sequence[str],
    rationale_trace: Sequence[str],
    feedback_bundle: Mapping[str, Any],
    remediation_candidates: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    blocking = _dedupe_strings(blocking_reason_codes)
    primary = _blocking_primary_hypothesis(blocking) or "VERIFICATION_ENVIRONMENT_FAILURE"
    return {
        "should_retry": True,
        "retry_reason": "deterministic verification or patch-apply evidence suggests a bounded repair attempt",
        "retry_mode": "patch_repair",
        "retry_authorization_state": "authorized",
        "branch_action": "same_branch_reentry",
        "recommended_reentry_stage": "script_8_patch_generate",
        "failure_summary": "A mechanical execution, verification, or environment issue was detected and can be retried within policy.",
        "failure_classification": {
            "primary_hypothesis": primary,
            "competing_hypotheses": [code for code in blocking if code != primary][:3],
            "confidence_score": 0.76,
        },
        "rationale_trace": list(rationale_trace)[:MAX_RATIONALE_ITEMS],
        "minimal_feedback_bundle": dict(feedback_bundle),
        "retry_count": int(retry_count),
        "retry_limit": int(retry_limit),
        "operator_decision_required": False,
        "operator_decision_reason": None,
        "approval_items": [],
        "auto_remediation_candidates": [dict(item) for item in list(remediation_candidates or [])][:MAX_REMEDIATION_ITEMS],
        "blocking_reason_codes": blocking,
    }


def _contract_review_decision(
    *,
    retry_count: int,
    retry_limit: int,
    rationale_trace: Sequence[str],
    feedback_bundle: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "should_retry": False,
        "retry_reason": "contract verification did not produce a deterministic auto-retry route",
        "retry_mode": "abort",
        "retry_authorization_state": "requires_operator",
        "branch_action": "no_action",
        "recommended_reentry_stage": None,
        "failure_summary": "Contract verification did not pass and the remaining evidence requires operator review rather than an automatic retry.",
        "failure_classification": {
            "primary_hypothesis": "UPSTREAM_EVIDENCE_INCOMPLETE",
            "competing_hypotheses": [],
            "confidence_score": 0.7,
        },
        "rationale_trace": list(rationale_trace)[:MAX_RATIONALE_ITEMS],
        "minimal_feedback_bundle": dict(feedback_bundle),
        "retry_count": int(retry_count),
        "retry_limit": int(retry_limit),
        "operator_decision_required": True,
        "operator_decision_reason": "review contract verification evidence and choose whether to retry manually",
        "approval_items": [
            {
                "reason_code": "UPSTREAM_EVIDENCE_INCOMPLETE",
                "artifact": REQUIRED_ARTIFACT,
                "required_action": "review_contract_verification",
            }
        ],
        "auto_remediation_candidates": [],
        "blocking_reason_codes": [],
    }


def _build_decision(*, context: Mapping[str, Any], retry_limit: int, run_dir: Path, job_id: str, run_id: str, config: Mapping[str, Any]) -> Dict[str, Any]:
    apply_result = _artifact_root_value(context.get("apply") or {}, "apply_result")
    static = _artifact_root_value(context.get("static") or {}, "static_verification")
    runtime = _artifact_root_value(context.get("runtime") or {}, "runtime_verification")
    contract = _artifact_root_value(context.get("contract") or {}, "contract_verification")
    patch_plan = _artifact_root_value(context.get("patch_plan") or {}, "patch_plan")
    patch_plan_result = dict(dict(context.get("patch_plan") or {}).get("result") or {})
    patch_payload = dict(dict(context.get("patch") or {}).get("result") or {})
    patch_artifact = dict(patch_payload.get("patch_artifact") or {})
    execution_contract = _execution_contract(context)
    repo_root = str(dict(patch_plan_result.get("job_contract") or {}).get("repo_root") or "").strip() or str(run_dir.parent.parent)

    retry_count, lineage_rationale, lineage_blocking = _resolve_retry_lineage(job_dir=run_dir.parent.parent, run_id=run_id, retry_limit=retry_limit)

    contract_artifact = dict(context.get("contract") or {})
    if not contract_artifact.get("exists") or contract_artifact.get("malformed"):
        failure_summary = "Required upstream evidence is missing or malformed."
        primary = "UPSTREAM_OUTPUT_MALFORMED" if contract_artifact.get("malformed") else "REQUIRED_ARTIFACT_MISSING"
        return _exact_terminal_abort(
            retry_count=retry_count,
            retry_limit=retry_limit,
            blocking_reason_codes=[*lineage_blocking, "REQUIRED_ARTIFACT_MISSING", *( ["UPSTREAM_OUTPUT_MALFORMED"] if contract_artifact.get("malformed") else [] )],
            rationale_trace=[*lineage_rationale, failure_summary],
            failure_summary=failure_summary,
            primary_hypothesis=primary,
        )

    runtime_legitimately_skipped, runtime_skip_reason = _is_runtime_legitimately_skipped(context, runtime)
    static_required_equivalent = _verification_required(execution_contract, "stage11_static_verification") is not False or bool(static)

    upstream_codes, upstream_rationale = _classify_upstream_evidence(
        context=context,
        static_required_equivalent=static_required_equivalent,
        runtime_legitimately_skipped=runtime_legitimately_skipped,
    )
    apply_codes, apply_rationale = _classify_execution_apply(apply_result)
    verification_codes, verification_rationale = _classify_verification_tooling(static, runtime)
    contract_codes, contract_rationale = _classify_contract_policy(contract)

    feedback_bundle = _feedback_bundle(apply_result=apply_result, static=static, runtime=runtime, contract=contract)
    remediation_candidates = _build_remediation_candidates(
        apply_codes=apply_codes,
        verification_codes=verification_codes,
        contract_codes=contract_codes,
    )
    rationale_trace = _dedupe_strings(
        [
            *lineage_rationale,
            *upstream_rationale,
            *apply_rationale,
            *verification_rationale,
            *contract_rationale,
            *( [runtime_skip_reason] if runtime_skip_reason else [] ),
        ]
    )
    blocking_codes = _dedupe_strings([*lineage_blocking, *upstream_codes, *apply_codes, *verification_codes, *contract_codes])
    primary_hypothesis = _blocking_primary_hypothesis(blocking_codes) or (
        "contract_satisfied" if _artifact_status(contract, "contract_verification_status") == "satisfied" else "contract_not_satisfied"
    )
    confidence_score = 0.96 if _artifact_status(contract, "contract_verification_status") == "satisfied" and not blocking_codes else (
        0.88 if "CONTRACT_SCOPE_VIOLATION" in contract_codes else (
            0.8 if verification_codes or apply_codes else (
                0.55 if upstream_codes else 0.62
            )
        )
    )
    aggregated_diagnostics: List[Any] = []
    for block in (apply_result, static, runtime, contract):
        aggregated_diagnostics.extend(list(dict(block).get("diagnostics") or []))
    job_state = _load_job_state(run_dir.parent.parent)
    controller = ensure_controller_state(dict(job_state or {}), config).get("controller", {}) if job_state else {}
    branch_context = _branch_context(job_state, run_id, config=config) if job_state else {
        "branch_id": os.environ.get("PIPELINE_BRANCH_ID") or "BRANCH-main",
        "branch_purpose": "primary",
        "branch_state": {},
        "controller": controller,
        "prior_attempts": [],
    }
    branch_state = dict(branch_context.get("branch_state") or {})
    current_mutation_level = max(1, int(branch_state.get("current_mutation_level") or 1))
    failure_signature = build_failure_signature(
        repo_root=repo_root,
        primary_hypothesis=primary_hypothesis,
        failure_summary="; ".join(rationale_trace) or str(contract.get("decision", {}).get("summary") or ""),
        blocking_reason_codes=blocking_codes,
        feedback_bundle=feedback_bundle,
        apply_result=apply_result,
        diagnostics=aggregated_diagnostics,
    )
    attempt_signature = build_normalized_attempt_signature(
        repo_root=repo_root,
        branch_purpose=str(branch_context.get("branch_purpose") or "primary"),
        mutation_level=current_mutation_level,
        failure_signature=failure_signature,
        patch_artifact=patch_artifact,
        apply_result=apply_result,
        execution_contract=execution_contract,
    )
    weights = dict(dict(config.get("failure_signature") or {}).get("weights") or {})
    similarity_threshold = float(dict(config.get("failure_signature") or {}).get("similarity_threshold") or 0.7)
    prior_attempts = [dict(item) for item in list(branch_context.get("prior_attempts") or []) if str(dict(item).get("run_id") or "") != str(run_id)]
    similar_failures = find_similar_failures(
        current_signature=failure_signature,
        prior_attempts=prior_attempts,
        weights=weights,
        similarity_threshold=similarity_threshold,
    )
    cluster_id = str(similar_failures[0].get("cluster_id") or "") if similar_failures else failure_cluster_id(failure_signature)
    equivalent_run_id = None
    for prior in prior_attempts:
        if attempt_signatures_equivalent(attempt_signature, dict(prior.get("attempt_signature") or {})):
            equivalent_run_id = str(prior.get("run_id") or "") or None
            break
    progress_cfg = dict(config.get("progress") or {})
    progress_snapshot = compute_progress_snapshot(
        branch_state=branch_state,
        feedback_bundle=feedback_bundle,
        contract_status=_artifact_status(contract, "contract_verification_status"),
        static_status=_artifact_status(static, "static_verification_status"),
        runtime_status=_artifact_status(runtime, "runtime_verification_status"),
        apply_status=_artifact_status(apply_result, "apply_status"),
        blocking_reason_codes=blocking_codes,
        baseline_mode=str(progress_cfg.get("baseline_mode") or "first_attempt"),
        epsilon=float(progress_cfg.get("float_tolerance") or 0.001),
        plan_execution_status=str(patch_plan.get("execution_status") or ""),
        generation_applicable=bool(execution_contract.get("generation_applicable")),
        generation_blocked_reason=str(execution_contract.get("generation_blocked_reason") or ""),
        allowed_edit_files=list(execution_contract.get("allowed_edit_files") or []),
        implementation_target_files=list(execution_contract.get("implementation_target_files") or []),
        validation_target_files=list(execution_contract.get("validation_target_files") or []),
        changed_files=list(apply_result.get("changed_files") or []),
    )
    controller_analysis = {
        "normalized_attempt_signature": attempt_signature,
        "equivalent_attempt_detected": bool(equivalent_run_id),
        "equivalent_run_id": equivalent_run_id,
        "failure_signature": failure_signature,
        "failure_cluster_id": cluster_id,
        "similar_failures": similar_failures,
        "progress_snapshot": progress_snapshot,
        "mutation_level": current_mutation_level,
    }

    if retry_count >= retry_limit:
        latest_issue = _blocking_primary_hypothesis([*contract_codes, *verification_codes, *apply_codes, *upstream_codes, *lineage_blocking]) or "RETRY_LIMIT_EXHAUSTED"
        decision = {
            "should_retry": False,
            "retry_reason": "retry limit reached",
            "retry_mode": "abort",
            "retry_authorization_state": "not_allowed",
            "branch_action": "terminal_abort",
            "recommended_reentry_stage": None,
            "failure_summary": "Retry limit exhausted. Automatic retry is no longer allowed for this lineage.",
            "failure_classification": {
                "primary_hypothesis": latest_issue,
                "competing_hypotheses": [code for code in _dedupe_strings([*contract_codes, *verification_codes, *apply_codes, *upstream_codes, *lineage_blocking, "RETRY_LIMIT_EXHAUSTED"]) if code != latest_issue][:3],
                "confidence_score": 0.96,
            },
            "rationale_trace": list(_dedupe_strings([*rationale_trace, f"retry_count={retry_count}", f"retry_limit={retry_limit}"]))[:MAX_RATIONALE_ITEMS],
            "minimal_feedback_bundle": dict(feedback_bundle),
            "retry_count": int(retry_count),
            "retry_limit": int(retry_limit),
            "operator_decision_required": False,
            "operator_decision_reason": None,
            "approval_items": [],
            "auto_remediation_candidates": [dict(item) for item in remediation_candidates[:MAX_REMEDIATION_ITEMS]],
            "blocking_reason_codes": _dedupe_strings([*lineage_blocking, *upstream_codes, *apply_codes, *verification_codes, *contract_codes, "RETRY_LIMIT_EXHAUSTED"]),
            "selected_strategy": None,
            "mutation_level": current_mutation_level,
            "next_branch_purpose": str(branch_context.get("branch_purpose") or "primary"),
            "retry_contract": {},
            "controller_analysis": controller_analysis,
        }
        return decision

    if "RETRY_LINEAGE_DISAGREEMENT" in lineage_blocking or "UPSTREAM_EVIDENCE_INCOMPLETE" in upstream_codes or "UPSTREAM_OUTPUT_MALFORMED" in upstream_codes:
        return {
            "should_retry": False,
            "retry_reason": "terminal upstream evidence failure",
            "retry_mode": "abort",
            "retry_authorization_state": "not_allowed",
            "branch_action": "terminal_abort",
            "recommended_reentry_stage": None,
            "failure_summary": "Required upstream evidence is incomplete or malformed, so the controller cannot continue without risking false progress.",
            "failure_classification": {
                "primary_hypothesis": primary_hypothesis,
                "competing_hypotheses": [code for code in blocking_codes if code != primary_hypothesis][:3],
                "confidence_score": confidence_score,
            },
            "rationale_trace": list(rationale_trace)[:MAX_RATIONALE_ITEMS],
            "minimal_feedback_bundle": dict(feedback_bundle),
            "retry_count": int(retry_count),
            "retry_limit": int(retry_limit),
            "operator_decision_required": False,
            "operator_decision_reason": None,
            "approval_items": [],
            "auto_remediation_candidates": [dict(item) for item in remediation_candidates[:MAX_REMEDIATION_ITEMS]],
            "blocking_reason_codes": blocking_codes,
            "selected_strategy": None,
            "mutation_level": current_mutation_level,
            "next_branch_purpose": str(branch_context.get("branch_purpose") or "primary"),
            "retry_contract": {},
            "controller_analysis": controller_analysis,
        }

    contract_status = _artifact_status(contract, "contract_verification_status")
    if contract_status == "satisfied" and not contract_codes and not apply_codes and not verification_codes:
        decision = _clean_pass_decision(
            retry_count=retry_count,
            retry_limit=retry_limit,
            affected_files=list(apply_result.get("changed_files") or []),
            rationale_trace=[*rationale_trace, "contract verification satisfied and no blocking reason codes remain"],
        )
        decision["selected_strategy"] = None
        decision["mutation_level"] = current_mutation_level
        decision["next_branch_purpose"] = str(branch_context.get("branch_purpose") or "primary")
        decision["retry_contract"] = {}
        decision["controller_analysis"] = controller_analysis
        return decision

    strategy = select_retry_strategy(
        config=config,
        branch_state=branch_state,
        failure_cluster_id=cluster_id,
        primary_hypothesis=primary_hypothesis,
        blocking_reason_codes=blocking_codes,
        plan_execution_status=str(patch_plan.get("execution_status") or ""),
        contract_status=contract_status,
        confidence_score=confidence_score,
        equivalent_attempt_detected=bool(equivalent_run_id),
        progress_snapshot=progress_snapshot,
    )
    retry_contract = build_retry_contract(
        selected_strategy=str(strategy.get("selected_strategy") or ""),
        mutation_level=int(strategy.get("mutation_level") or current_mutation_level),
        failure_summary=str(contract.get("decision", {}).get("summary") or "retry required after failed execution attempt"),
        feedback_bundle=feedback_bundle,
        failure_signature=failure_signature,
        equivalent_attempt_detected=bool(equivalent_run_id),
        similar_failures=similar_failures,
    ) if strategy.get("selected_strategy") else {}
    controller_analysis.update(
        {
            "selected_strategy": strategy.get("selected_strategy"),
            "candidate_strategies": list(strategy.get("candidate_strategies") or []),
            "strategy_exhausted": bool(strategy.get("strategy_exhausted")),
            "low_confidence": bool(strategy.get("low_confidence")),
        }
    )
    if not strategy.get("selected_strategy"):
        return {
            "should_retry": False,
            "retry_reason": "all deterministic strategies exhausted",
            "retry_mode": "abort",
            "retry_authorization_state": "not_allowed",
            "branch_action": "terminal_abort",
            "recommended_reentry_stage": None,
            "failure_summary": "The controller exhausted all deterministic retry strategies for this failure cluster without a justified next move.",
            "failure_classification": {
                "primary_hypothesis": primary_hypothesis,
                "competing_hypotheses": [code for code in blocking_codes if code != primary_hypothesis][:3],
                "confidence_score": confidence_score,
            },
            "rationale_trace": list(_dedupe_strings([*rationale_trace, "all configured retry strategies were exhausted for this failure cluster"]))[:MAX_RATIONALE_ITEMS],
            "minimal_feedback_bundle": dict(feedback_bundle),
            "retry_count": int(retry_count),
            "retry_limit": int(retry_limit),
            "operator_decision_required": False,
            "operator_decision_reason": None,
            "approval_items": [],
            "auto_remediation_candidates": [dict(item) for item in remediation_candidates[:MAX_REMEDIATION_ITEMS]],
            "blocking_reason_codes": blocking_codes,
            "selected_strategy": None,
            "mutation_level": current_mutation_level,
            "next_branch_purpose": str(branch_context.get("branch_purpose") or "primary"),
            "retry_contract": {},
            "controller_analysis": controller_analysis,
        }
    retry_mode = "patch_repair" if str(strategy.get("selected_strategy")) == "local_fix" else "escalate"
    return {
        "should_retry": True,
        "retry_reason": f"deterministic {strategy.get('selected_strategy')} retry selected",
        "retry_mode": retry_mode,
        "retry_authorization_state": "authorized",
        "branch_action": str(strategy.get("branch_action") or "fork_new_branch"),
        "recommended_reentry_stage": str(strategy.get("reentry_stage") or ""),
        "failure_summary": str(contract.get("decision", {}).get("summary") or "contract verification did not pass, so the controller selected a new upstream mutation"),
        "failure_classification": {
            "primary_hypothesis": primary_hypothesis,
            "competing_hypotheses": [code for code in blocking_codes if code != primary_hypothesis][:3],
            "confidence_score": confidence_score,
        },
        "rationale_trace": list(_dedupe_strings([
            *rationale_trace,
            f"selected_strategy={strategy.get('selected_strategy')}",
            f"equivalent_attempt_detected={bool(equivalent_run_id)}",
            f"failure_cluster_id={cluster_id}",
        ]))[:MAX_RATIONALE_ITEMS],
        "minimal_feedback_bundle": dict(feedback_bundle),
        "retry_count": int(retry_count),
        "retry_limit": int(retry_limit),
        "operator_decision_required": False,
        "operator_decision_reason": None,
        "approval_items": [],
        "auto_remediation_candidates": [dict(item) for item in remediation_candidates[:MAX_REMEDIATION_ITEMS]],
        "blocking_reason_codes": blocking_codes,
        "selected_strategy": str(strategy.get("selected_strategy") or ""),
        "mutation_level": int(strategy.get("mutation_level") or current_mutation_level),
        "next_branch_purpose": str(strategy.get("next_branch_purpose") or branch_context.get("branch_purpose") or "primary"),
        "retry_contract": retry_contract,
        "controller_analysis": controller_analysis,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Execution MVP stage 14 repair loop")
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
        context = _load_context(run_dir)
        retry_limit = int(dict(config.get("user_budgets") or {}).get("max_attempts_total") or config.get("limits", {}).get("retry_limit") or 30)
        result = {
            "repair_decision": _build_decision(
                context=context,
                retry_limit=retry_limit,
                run_dir=run_dir,
                job_id=job_id,
                run_id=run_id,
                config=config,
            )
        }
        validate_repair_decision_result(result)
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
