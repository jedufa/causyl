from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence

STRATEGY_ORDER = ["local_fix", "plan_revision", "context_expansion", "task_reinterpretation"]
STRATEGY_MUTATION_LEVEL = {
    "local_fix": 1,
    "plan_revision": 2,
    "context_expansion": 3,
    "task_reinterpretation": 4,
}
STRATEGY_REENTRY_STAGE = {
    "local_fix": "script_8_patch_generate",
    "plan_revision": "script_7_patch_plan",
    "context_expansion": "script_4_repo_retriever",
    "task_reinterpretation": "script_3_search_plan",
}


def _dedupe_strings(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _reason(label: str, *, allowed: bool, details: str) -> Dict[str, Any]:
    return {
        "strategy": str(label),
        "allowed": bool(allowed),
        "details": str(details or "").strip(),
        "mutation_level": int(STRATEGY_MUTATION_LEVEL[label]),
        "reentry_stage": STRATEGY_REENTRY_STAGE[label],
    }


def _base_strategy(
    *,
    primary_hypothesis: str,
    blocking_reason_codes: Sequence[Any],
    plan_execution_status: str,
    contract_status: str,
    low_confidence: bool,
) -> str:
    blocking = {str(code or "").strip() for code in list(blocking_reason_codes or []) if str(code or "").strip()}
    if low_confidence:
        return "context_expansion"
    if plan_execution_status in {"NEEDS_MORE_CONTEXT", "REVIEW_ONLY"}:
        return "context_expansion"
    if any(code in blocking for code in {"UPSTREAM_EVIDENCE_INCOMPLETE", "UPSTREAM_OUTPUT_MALFORMED"}):
        return "context_expansion"
    if contract_status in {"violated", "inconclusive"} and any(code in blocking for code in {"CONTRACT_SCOPE_VIOLATION", "validation_claim_missing_tests", "required_test_edit_missing"}):
        return "plan_revision"
    if primary_hypothesis in {"PATCH_APPLY_DEGRADATION", "PATH_NORMALIZATION_MISMATCH", "VERIFICATION_ENVIRONMENT_FAILURE", "REAL_TEST_FAILURE"}:
        return "local_fix"
    if primary_hypothesis in {"CONTRACT_SCOPE_VIOLATION"}:
        return "plan_revision"
    return "plan_revision" if contract_status != "satisfied" else "local_fix"


def select_retry_strategy(
    *,
    config: Mapping[str, Any],
    branch_state: Mapping[str, Any],
    failure_cluster_id: str,
    primary_hypothesis: str,
    blocking_reason_codes: Sequence[Any],
    plan_execution_status: str,
    contract_status: str,
    confidence_score: float,
    equivalent_attempt_detected: bool,
    progress_snapshot: Mapping[str, Any],
) -> Dict[str, Any]:
    strategy_config = dict(config.get("strategy_selection") or {})
    configured_order = [str(item) for item in list(strategy_config.get("order") or STRATEGY_ORDER) if str(item) in STRATEGY_MUTATION_LEVEL]
    order = configured_order or list(STRATEGY_ORDER)
    low_confidence_threshold = float(dict(config.get("orchestrator") or {}).get("low_confidence_threshold") or 0.6)
    low_confidence = float(confidence_score) < low_confidence_threshold
    base = _base_strategy(
        primary_hypothesis=primary_hypothesis,
        blocking_reason_codes=blocking_reason_codes,
        plan_execution_status=plan_execution_status,
        contract_status=contract_status,
        low_confidence=low_confidence,
    )
    current_mutation_level = max(1, int(branch_state.get("current_mutation_level") or 1))
    min_attempts_per_level = max(1, int(dict(config.get("mutation") or {}).get("min_attempts_per_level") or 2))
    same_level_attempts = int(branch_state.get("same_level_attempts") or 0)
    identical_attempt_repeats = int(branch_state.get("identical_attempt_repeats") or 0)
    cooldown_remaining = max(0, int(branch_state.get("cooldown_remaining") or 0))
    material_improvement = bool(progress_snapshot.get("materially_improved"))
    stagnation_detected = bool(progress_snapshot.get("stagnation_detected"))
    regression_detected = bool(progress_snapshot.get("regression_detected"))
    plan_review_blocked = bool(progress_snapshot.get("plan_review_blocked"))
    base_index = order.index(base) if base in order else 0
    required_index = max(base_index, current_mutation_level - 1)
    if equivalent_attempt_detected or regression_detected:
        required_index = min(len(order) - 1, max(required_index, current_mutation_level))
    elif stagnation_detected and same_level_attempts >= min_attempts_per_level:
        required_index = min(len(order) - 1, max(required_index, current_mutation_level))
    elif material_improvement and bool(dict(config.get("mutation") or {}).get("allow_backoff_on_improvement", True)):
        required_index = max(base_index, current_mutation_level - 2)
    if plan_review_blocked and identical_attempt_repeats >= 1:
        required_index = max(required_index, order.index("context_expansion"))
    if plan_review_blocked and identical_attempt_repeats >= 2:
        required_index = max(required_index, order.index("task_reinterpretation"))
    cluster_state = dict(dict(branch_state.get("failure_clusters") or {}).get(str(failure_cluster_id)) or {})
    exhausted = set(str(item) for item in list(cluster_state.get("exhausted_strategies") or []) if str(item).strip())
    tried = set(str(item) for item in list(cluster_state.get("strategies_tried") or []) if str(item).strip())
    candidate_reasons: List[Dict[str, Any]] = []
    for index, strategy in enumerate(order):
        if index < required_index:
            candidate_reasons.append(_reason(strategy, allowed=False, details="lower than the minimum viable mutation level for the current failure"))
            continue
        if strategy in exhausted:
            candidate_reasons.append(_reason(strategy, allowed=False, details="strategy already exhausted for this failure cluster"))
            continue
        if low_confidence and strategy == "local_fix":
            candidate_reasons.append(_reason(strategy, allowed=False, details="same-branch local retries are forbidden below the configured confidence threshold"))
            continue
        if cooldown_remaining > 0 and strategy != order[min(len(order) - 1, current_mutation_level - 1)]:
            candidate_reasons.append(_reason(strategy, allowed=False, details="mutation cooldown keeps the search on the current escalation level for one more attempt"))
            continue
        if equivalent_attempt_detected and strategy in tried and strategy != order[-1]:
            candidate_reasons.append(_reason(strategy, allowed=False, details="strategy already produced an equivalent attempt for a similar failure"))
            continue
        candidate_reasons.append(_reason(strategy, allowed=True, details="selected as the first deterministic strategy that satisfies confidence, mutation, and failure-cluster rules"))
        branch_action = "same_branch_reentry" if strategy == "local_fix" and not low_confidence else "fork_new_branch"
        return {
            "selected_strategy": strategy,
            "mutation_level": int(STRATEGY_MUTATION_LEVEL[strategy]),
            "reentry_stage": STRATEGY_REENTRY_STAGE[strategy],
            "branch_action": branch_action,
            "next_branch_purpose": strategy,
            "candidate_strategies": candidate_reasons,
            "strategy_exhausted": False,
            "low_confidence": bool(low_confidence),
        }
    return {
        "selected_strategy": None,
        "mutation_level": int(current_mutation_level),
        "reentry_stage": None,
        "branch_action": "terminal_abort",
        "next_branch_purpose": str(branch_state.get("branch_purpose") or "primary"),
        "candidate_strategies": candidate_reasons,
        "strategy_exhausted": True,
        "low_confidence": bool(low_confidence),
    }


def build_retry_contract(
    *,
    selected_strategy: str,
    mutation_level: int,
    failure_summary: str,
    feedback_bundle: Mapping[str, Any],
    failure_signature: Mapping[str, Any],
    equivalent_attempt_detected: bool,
    similar_failures: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    negative_constraints = _dedupe_strings(
        list(feedback_bundle.get("excerpts") or [])
        + list(failure_signature.get("key_error_tokens") or [])
    )[:10]
    strategy_note = {
        "local_fix": "Preserve the existing file scope and correct the local patch or verification defect without expanding the plan.",
        "plan_revision": "Revise the patch plan and execution contract so the next patch addresses the failure without repeating the previous plan.",
        "context_expansion": "Expand upstream evidence and retrieve additional supporting files before replanning.",
        "task_reinterpretation": "Reinterpret the task and generate alternative search queries or hypotheses before retrieval.",
    }.get(selected_strategy, "Apply a deterministic upstream mutation before retrying.")
    upstream_mutations = []
    if selected_strategy == "local_fix":
        upstream_mutations.append("inject negative constraints and failure excerpts into stage 8 prompt")
    elif selected_strategy == "plan_revision":
        upstream_mutations.append("reenter stage 7 with retry constraints and previous failure patterns")
    elif selected_strategy == "context_expansion":
        upstream_mutations.append("reenter stage 4 with additional retrieval hints derived from the failure cluster")
    elif selected_strategy == "task_reinterpretation":
        upstream_mutations.append("reenter stage 3 with alternative hypotheses and broader search cues")
    if equivalent_attempt_detected:
        upstream_mutations.append("forbid equivalent retry signatures in the next attempt")
    return {
        "selected_strategy": str(selected_strategy or ""),
        "mutation_level": int(mutation_level or 1),
        "failure_summary": str(failure_summary or "").strip(),
        "strategy_note": strategy_note,
        "negative_constraints": negative_constraints,
        "upstream_mutations": upstream_mutations,
        "similar_failure_run_ids": [str(item.get("run_id") or "") for item in list(similar_failures or []) if str(item.get("run_id") or "").strip()],
        "feedback_bundle": {
            "affected_files": list(feedback_bundle.get("affected_files") or []),
            "failing_checks": list(feedback_bundle.get("failing_checks") or []),
            "failing_tests": list(feedback_bundle.get("failing_tests") or []),
            "excerpts": list(feedback_bundle.get("excerpts") or []),
        },
        "failure_signature": dict(failure_signature or {}),
    }
