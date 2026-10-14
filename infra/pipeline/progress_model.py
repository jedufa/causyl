from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence


def _count_failed_tests(feedback_bundle: Mapping[str, Any]) -> int:
    failing_tests = list(feedback_bundle.get("failing_tests") or [])
    if not failing_tests:
        return 0
    total = 0
    for item in failing_tests:
        if isinstance(item, Mapping):
            named = list(item.get("failing_tests") or [])
            if named:
                total += len(named)
                continue
        total += 1
    return max(0, total)


def failure_severity(
    *,
    contract_status: str,
    blocking_reason_codes: Sequence[Any],
    static_status: str,
    runtime_status: str,
    apply_status: str,
) -> int:
    blocking = {str(code or "").strip() for code in list(blocking_reason_codes or []) if str(code or "").strip()}
    if contract_status == "satisfied" and not blocking and static_status != "failed" and runtime_status != "failed":
        return 0
    if any(code in blocking for code in {"PATCH_APPLY_DEGRADATION", "PATH_NORMALIZATION_MISMATCH"}) or apply_status in {"rejected", "conflict"}:
        return 5
    if static_status == "failed":
        return 4
    if runtime_status == "failed":
        return 2
    if contract_status in {"violated", "inconclusive", "failed"} or blocking:
        return 1
    return 3


def contract_score(contract_status: str) -> float:
    normalized = str(contract_status or "").strip().lower()
    if normalized == "satisfied":
        return 1.0
    if normalized in {"skipped", "inconclusive"}:
        return 0.5
    return 0.0


def compute_progress_snapshot(
    *,
    branch_state: Mapping[str, Any],
    feedback_bundle: Mapping[str, Any],
    contract_status: str,
    static_status: str,
    runtime_status: str,
    apply_status: str,
    blocking_reason_codes: Sequence[Any],
    baseline_mode: str,
    epsilon: float,
    plan_execution_status: str = "",
    generation_applicable: bool | None = None,
    generation_blocked_reason: str = "",
    allowed_edit_files: Sequence[Any] = (),
    implementation_target_files: Sequence[Any] = (),
    validation_target_files: Sequence[Any] = (),
    changed_files: Sequence[Any] = (),
) -> Dict[str, Any]:
    failing_tests = _count_failed_tests(feedback_bundle)
    prior_progress = branch_state.get("last_progress_score")
    best_progress = branch_state.get("best_progress_score")
    baseline = dict(branch_state.get("progress_baseline") or {})
    baseline_tests = baseline.get("baseline_tests")
    if baseline_mode == "max_seen":
        observed = max(int(baseline_tests or 0), int(failing_tests))
        baseline_tests = observed or baseline_tests
    elif baseline_tests is None:
        baseline_tests = int(failing_tests)

    tests_component = 1.0 if not int(baseline_tests or 0) else max(0.0, 1.0 - (float(failing_tests) / float(int(baseline_tests))))
    severity = failure_severity(
        contract_status=contract_status,
        blocking_reason_codes=blocking_reason_codes,
        static_status=static_status,
        runtime_status=runtime_status,
        apply_status=apply_status,
    )
    severity_component = max(0.0, 1.0 - (float(severity) / 5.0))
    contract_component = contract_score(contract_status)
    alpha = 0.45
    beta = 0.35
    gamma = 0.20
    progress_score = alpha * tests_component + beta * severity_component + gamma * contract_component
    delta_from_previous = None if prior_progress is None else float(progress_score) - float(prior_progress)
    delta_from_best = None if best_progress is None else float(progress_score) - float(best_progress)
    stagnation_detected = delta_from_previous is not None and abs(float(delta_from_previous)) <= float(epsilon)
    regression_detected = delta_from_previous is not None and float(delta_from_previous) < -float(epsilon)
    materially_improved = delta_from_previous is not None and float(delta_from_previous) > float(epsilon)
    blocked_reason_text = str(generation_blocked_reason or "").strip()
    structural_state = {
        "plan_execution_status": str(plan_execution_status or "").strip(),
        "generation_applicable": bool(generation_applicable),
        "generation_blocked_reason": blocked_reason_text,
        "allowed_edit_files": [str(item) for item in list(allowed_edit_files or []) if str(item or "").strip()],
        "implementation_target_files": [str(item) for item in list(implementation_target_files or []) if str(item or "").strip()],
        "validation_target_files": [str(item) for item in list(validation_target_files or []) if str(item or "").strip()],
        "changed_files": [str(item) for item in list(changed_files or []) if str(item or "").strip()],
        "apply_status": str(apply_status or "").strip(),
        "static_status": str(static_status or "").strip(),
        "runtime_status": str(runtime_status or "").strip(),
        "contract_status": str(contract_status or "").strip(),
    }
    plan_review_blocked = bool(
        blocked_reason_text and "review before generation" in blocked_reason_text.lower()
    )
    return {
        "progress_score": float(progress_score),
        "delta_from_previous": delta_from_previous,
        "delta_from_best": delta_from_best,
        "failing_tests": int(failing_tests),
        "baseline_tests": int(baseline_tests or 0),
        "baseline_mode": str(baseline_mode or "first_attempt"),
        "severity": int(severity),
        "contract_score": float(contract_component),
        "stagnation_detected": bool(stagnation_detected),
        "regression_detected": bool(regression_detected),
        "materially_improved": bool(materially_improved),
        "plan_execution_status": str(plan_execution_status or "").strip(),
        "generation_applicable": bool(generation_applicable),
        "generation_blocked_reason": blocked_reason_text,
        "plan_review_blocked": bool(plan_review_blocked),
        "structural_state": structural_state,
    }
