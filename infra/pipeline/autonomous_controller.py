from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .core import (
    PipelineError,
    STAGE_FILE_SPECS,
    load_json,
    make_run_id,
    save_json_atomic,
    sha256_payload,
    utc_now_iso,
)
from .job_state import JobStateStore

CONTROLLER_SCHEMA_VERSION = "1"
RETRY_CONTRACT_FILENAME = "retry_contract.json"
REENTRY_SEED_FILES = {
    "script_8_patch_generate": ["07_patch_plan.json", "07_execution_contract.json"],
    "script_7_patch_plan": ["06_context_bundle.json"],
    "script_4_repo_retriever": ["03_search_plan.json"],
    "script_3_search_plan": ["02_task_profile.json"],
    "script_2_task_classifier": ["01_job_contract.json"],
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


def _user_budgets(config: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(config.get("user_budgets") or {})
    return {
        "max_wall_clock_minutes": int(raw.get("max_wall_clock_minutes") or 60),
        "max_model_tokens_total": int(raw.get("max_model_tokens_total") or 100000),
        "max_attempts_total": int(raw.get("max_attempts_total") or 30),
        "max_stagnant_attempts": int(raw.get("max_stagnant_attempts") or 5),
        "min_progress_delta": float(raw.get("min_progress_delta") or 0.01),
    }


def ensure_controller_state(state: Dict[str, Any], config: Mapping[str, Any]) -> Dict[str, Any]:
    now = utc_now_iso()
    controller = dict(state.get("controller") or {})
    controller.setdefault("schema_version", CONTROLLER_SCHEMA_VERSION)
    controller.setdefault("started_at", now)
    controller.setdefault("updated_at", now)
    controller.setdefault("best_run_id", None)
    controller.setdefault("best_progress_score", None)
    controller.setdefault("stop_reason", None)
    controller.setdefault("recommended_next_step", None)
    controller.setdefault("budgets", _user_budgets(config))
    totals = dict(controller.get("totals") or {})
    totals.setdefault("attempts_total", 0)
    totals.setdefault("model_tokens_input", 0)
    totals.setdefault("model_tokens_output", 0)
    totals.setdefault("model_tokens_total", 0)
    totals.setdefault("model_calls_total", 0)
    totals.setdefault("expensive_stage_calls_total", 0)
    totals.setdefault("wall_clock_started_at", controller["started_at"])
    controller["totals"] = totals
    controller["negative_memory"] = dict(controller.get("negative_memory") or {"attempt_signatures": [], "failed_branch_purposes": [], "failed_patterns": []})
    controller["recent_attempts"] = list(controller.get("recent_attempts") or [])
    controller["milestones"] = dict(controller.get("milestones") or {})
    branches = dict(controller.get("branches") or {})
    for branch_id, branch in dict(state.get("branches") or {}).items():
        item = dict(branches.get(branch_id) or {})
        item.setdefault("branch_id", str(branch_id))
        item.setdefault("branch_purpose", str(branch.get("branch_purpose") or "primary"))
        item.setdefault("attempt_run_ids", [str(x) for x in list(branch.get("run_ids") or [])])
        item.setdefault("current_mutation_level", 1)
        item.setdefault("same_level_attempts", 0)
        item.setdefault("cooldown_remaining", 0)
        item.setdefault("best_progress_score", None)
        item.setdefault("last_progress_score", None)
        item.setdefault("stagnant_attempts", 0)
        item.setdefault("progress_baseline", {"mode": str(dict(config.get("progress") or {}).get("baseline_mode") or "first_attempt"), "baseline_tests": None})
        item.setdefault("failure_clusters", {})
        item.setdefault("milestones", {})
        item.setdefault("identical_attempt_repeats", 0)
        item.setdefault("last_attempt_signature_hash", None)
        item.setdefault("last_structural_hash", None)
        item.setdefault("last_selected_strategy", None)
        item.setdefault("last_failure_cluster_id", None)
        item.setdefault("last_plan_review_blocked", False)
        item.setdefault("last_generation_blocked_reason", None)
        branches[branch_id] = item
    controller["branches"] = branches
    state["controller"] = controller
    return controller


def branch_controller_state(controller: Mapping[str, Any], branch_id: str, *, branch_purpose: str = "primary") -> Dict[str, Any]:
    branches = dict(controller.get("branches") or {})
    item = dict(branches.get(branch_id) or {})
    item.setdefault("branch_id", str(branch_id))
    item.setdefault("branch_purpose", str(branch_purpose or "primary"))
    item.setdefault("attempt_run_ids", [])
    item.setdefault("current_mutation_level", 1)
    item.setdefault("same_level_attempts", 0)
    item.setdefault("cooldown_remaining", 0)
    item.setdefault("best_progress_score", None)
    item.setdefault("last_progress_score", None)
    item.setdefault("stagnant_attempts", 0)
    item.setdefault("progress_baseline", {"mode": "first_attempt", "baseline_tests": None})
    item.setdefault("failure_clusters", {})
    item.setdefault("milestones", {})
    item.setdefault("identical_attempt_repeats", 0)
    item.setdefault("last_attempt_signature_hash", None)
    item.setdefault("last_structural_hash", None)
    item.setdefault("last_selected_strategy", None)
    item.setdefault("last_failure_cluster_id", None)
    item.setdefault("last_plan_review_blocked", False)
    item.setdefault("last_generation_blocked_reason", None)
    branches[str(branch_id)] = item
    return item


def _stage8_usage(job_dir: Path, run_id: str) -> Dict[str, int]:
    path = job_dir / "runs" / str(run_id) / "08_patch_artifact.json"
    if not path.exists():
        return {"input": 0, "output": 0, "total": 0, "calls": 0, "expensive_calls": 0}
    try:
        payload = load_json(path)
    except Exception:
        return {"input": 0, "output": 0, "total": 0, "calls": 0, "expensive_calls": 0}
    result = dict(payload.get("result") or {})
    metadata = dict(result.get("generation_metadata") or {})
    total = int(metadata.get("usage_total_tokens") or metadata.get("estimated_total_tokens") or 0)
    return {
        "input": int(metadata.get("usage_input_tokens") or metadata.get("estimated_input_tokens") or 0),
        "output": int(metadata.get("usage_output_tokens") or metadata.get("estimated_output_tokens") or 0),
        "total": total,
        "calls": 1 if metadata or result else 0,
        "expensive_calls": 1 if metadata or result else 0,
    }


def record_attempt_result(
    *,
    job_dir: str | Path,
    run_id: str,
    config: Mapping[str, Any],
    repair_decision: Mapping[str, Any],
    final_status: str,
) -> Dict[str, Any]:
    job_path = Path(job_dir).resolve()
    store = JobStateStore(job_path)
    state = store.load()
    controller = ensure_controller_state(state, config)
    run = dict(state.get("runs", {}).get(run_id) or {})
    branch_id = str(run.get("branch_id") or state.get("active_branch_id") or "BRANCH-main")
    branch_purpose = str(dict(state.get("branches", {}).get(branch_id) or {}).get("branch_purpose") or "primary")
    branch_state = branch_controller_state(controller, branch_id, branch_purpose=branch_purpose)
    existing = {str(item.get("run_id") or "") for item in list(controller.get("recent_attempts") or [])}
    if str(run_id) in existing:
        return controller
    analysis = dict(repair_decision.get("controller_analysis") or {})
    progress_snapshot = dict(analysis.get("progress_snapshot") or {})
    mutation_level = int(repair_decision.get("mutation_level") or analysis.get("mutation_level") or branch_state.get("current_mutation_level") or 1)
    selected_strategy = str(repair_decision.get("selected_strategy") or analysis.get("selected_strategy") or "")
    failure_cluster_id = str(analysis.get("failure_cluster_id") or "")
    attempt_signature = dict(analysis.get("normalized_attempt_signature") or {})
    attempt_signature_hash = str(attempt_signature.get("signature_hash") or "").strip() or None
    structural_state = dict(progress_snapshot.get("structural_state") or {})
    structural_hash = sha256_payload(structural_state) if structural_state else None
    plan_review_blocked = bool(progress_snapshot.get("plan_review_blocked"))
    generation_blocked_reason = str(progress_snapshot.get("generation_blocked_reason") or "").strip() or None
    attempt_record = {
        "run_id": str(run_id),
        "branch_id": branch_id,
        "attempt_index": int(run.get("attempt_index") or 0),
        "branch_purpose": branch_purpose,
        "strategy": selected_strategy,
        "mutation_level": mutation_level,
        "progress_score": progress_snapshot.get("progress_score"),
        "failure_cluster_id": failure_cluster_id,
        "attempt_signature": attempt_signature,
        "failure_signature": dict(analysis.get("failure_signature") or {}),
        "final_status": str(final_status or ""),
    }
    recent_attempts = list(controller.get("recent_attempts") or [])
    recent_attempts.append(attempt_record)
    max_recent = max(1, int(dict(config.get("retention") or {}).get("max_recent_attempts") or 10))
    controller["recent_attempts"] = recent_attempts[-max_recent:]
    totals = dict(controller.get("totals") or {})
    usage = _stage8_usage(job_path, run_id)
    totals["attempts_total"] = int(totals.get("attempts_total") or 0) + 1
    totals["model_tokens_input"] = int(totals.get("model_tokens_input") or 0) + int(usage["input"])
    totals["model_tokens_output"] = int(totals.get("model_tokens_output") or 0) + int(usage["output"])
    totals["model_tokens_total"] = int(totals.get("model_tokens_total") or 0) + int(usage["total"])
    totals["model_calls_total"] = int(totals.get("model_calls_total") or 0) + int(usage["calls"])
    totals["expensive_stage_calls_total"] = int(totals.get("expensive_stage_calls_total") or 0) + int(usage["expensive_calls"])
    controller["totals"] = totals
    progress_score = progress_snapshot.get("progress_score")
    if progress_score is not None and (
        branch_state.get("best_progress_score") is None or float(progress_score) > float(branch_state.get("best_progress_score") or 0.0)
    ):
        branch_state["best_progress_score"] = float(progress_score)
        branch_state.setdefault("milestones", {})["best_run_id"] = str(run_id)
    if progress_score is not None and (
        controller.get("best_progress_score") is None or float(progress_score) > float(controller.get("best_progress_score") or 0.0)
    ):
        controller["best_progress_score"] = float(progress_score)
        controller["best_run_id"] = str(run_id)
    if progress_snapshot.get("baseline_tests") is not None:
        branch_state["progress_baseline"] = {
            "mode": str(progress_snapshot.get("baseline_mode") or "first_attempt"),
            "baseline_tests": int(progress_snapshot.get("baseline_tests") or 0),
        }
    min_progress_delta = float(dict(controller.get("budgets") or {}).get("min_progress_delta") or 0.01)
    delta_from_previous = progress_snapshot.get("delta_from_previous")
    previous_attempt_hash = str(branch_state.get("last_attempt_signature_hash") or "").strip() or None
    previous_structural_hash = str(branch_state.get("last_structural_hash") or "").strip() or None
    previous_strategy = str(branch_state.get("last_selected_strategy") or "").strip() or None
    previous_cluster_id = str(branch_state.get("last_failure_cluster_id") or "").strip() or None
    previous_generation_blocked_reason = str(branch_state.get("last_generation_blocked_reason") or "").strip() or None
    same_failure_cluster = bool(failure_cluster_id and failure_cluster_id == previous_cluster_id)
    same_strategy = bool(selected_strategy and selected_strategy == previous_strategy)
    same_signature_retry = bool(same_failure_cluster and same_strategy and attempt_signature_hash and attempt_signature_hash == previous_attempt_hash)
    same_structural_retry = bool(same_failure_cluster and same_strategy and structural_hash and structural_hash == previous_structural_hash)
    same_plan_review_retry = bool(
        same_failure_cluster
        and same_strategy
        and plan_review_blocked
        and bool(branch_state.get("last_plan_review_blocked"))
        and generation_blocked_reason
        and generation_blocked_reason == previous_generation_blocked_reason
    )
    if same_signature_retry or same_structural_retry or same_plan_review_retry:
        branch_state["stagnant_attempts"] = int(branch_state.get("stagnant_attempts") or 0) + 1
        branch_state["identical_attempt_repeats"] = int(branch_state.get("identical_attempt_repeats") or 0) + 1
    elif delta_from_previous is not None and abs(float(delta_from_previous)) <= min_progress_delta:
        branch_state["stagnant_attempts"] = int(branch_state.get("stagnant_attempts") or 0) + 1
        branch_state["identical_attempt_repeats"] = 0
    elif progress_snapshot.get("materially_improved") or (
        delta_from_previous is not None and abs(float(delta_from_previous)) > min_progress_delta
    ):
        branch_state["stagnant_attempts"] = 0
        branch_state["identical_attempt_repeats"] = 0
    branch_state["last_progress_score"] = progress_score
    if mutation_level == int(branch_state.get("current_mutation_level") or 1):
        branch_state["same_level_attempts"] = int(branch_state.get("same_level_attempts") or 0) + 1
    else:
        branch_state["same_level_attempts"] = 1
    branch_state["current_mutation_level"] = int(mutation_level or 1)
    if int(branch_state.get("cooldown_remaining") or 0) > 0:
        branch_state["cooldown_remaining"] = max(0, int(branch_state.get("cooldown_remaining") or 0) - 1)
    branch_state["last_attempt_signature_hash"] = attempt_signature_hash
    branch_state["last_structural_hash"] = structural_hash
    branch_state["last_selected_strategy"] = selected_strategy or None
    branch_state["last_failure_cluster_id"] = failure_cluster_id or None
    branch_state["last_plan_review_blocked"] = bool(plan_review_blocked)
    branch_state["last_generation_blocked_reason"] = generation_blocked_reason
    cluster_state = dict(dict(branch_state.get("failure_clusters") or {}).get(failure_cluster_id) or {}) if failure_cluster_id else {}
    if failure_cluster_id:
        cluster_state.setdefault("cluster_id", failure_cluster_id)
        cluster_state.setdefault("run_ids", [])
        cluster_state.setdefault("strategies_tried", [])
        cluster_state.setdefault("exhausted_strategies", [])
        cluster_state["run_ids"] = _dedupe_strings([*list(cluster_state.get("run_ids") or []), run_id])
        if selected_strategy:
            cluster_state["strategies_tried"] = _dedupe_strings([*list(cluster_state.get("strategies_tried") or []), selected_strategy])
        failure_clusters = dict(branch_state.get("failure_clusters") or {})
        failure_clusters[failure_cluster_id] = cluster_state
        branch_state["failure_clusters"] = failure_clusters
    negative_memory = dict(controller.get("negative_memory") or {})
    if str(final_status or "").strip().lower() != "accepted":
        signatures = list(negative_memory.get("attempt_signatures") or [])
        signatures.append(
            {
                "run_id": str(run_id),
                "branch_id": branch_id,
                "branch_purpose": branch_purpose,
                "strategy": selected_strategy,
                "failure_cluster_id": failure_cluster_id,
                "attempt_signature": dict(analysis.get("normalized_attempt_signature") or {}),
                "failure_signature": dict(analysis.get("failure_signature") or {}),
            }
        )
        negative_memory["attempt_signatures"] = signatures[-max_recent:]
        if selected_strategy and failure_cluster_id:
            negative_memory["failed_branch_purposes"] = _dedupe_strings(
                [*list(negative_memory.get("failed_branch_purposes") or []), f"{failure_cluster_id}:{selected_strategy}"]
            )[-max_recent:]
        key_tokens = list(dict(analysis.get("failure_signature") or {}).get("key_error_tokens") or [])
        if key_tokens:
            negative_memory["failed_patterns"] = _dedupe_strings(
                [*list(negative_memory.get("failed_patterns") or []), f"{failure_cluster_id}:{' '.join(key_tokens[:6])}"]
            )[-max_recent:]
        controller["negative_memory"] = negative_memory
    controller["branches"][branch_id] = branch_state
    controller["updated_at"] = utc_now_iso()
    state["controller"] = controller
    store.save(state)
    return controller


def evaluate_stop_condition(
    *,
    controller: Mapping[str, Any],
    branch_id: str,
    repair_decision: Mapping[str, Any],
) -> Dict[str, Any]:
    budgets = dict(controller.get("budgets") or {})
    totals = dict(controller.get("totals") or {})
    branch = dict(dict(controller.get("branches") or {}).get(branch_id) or {})
    should_retry = bool(repair_decision.get("should_retry"))
    authorization = str(repair_decision.get("retry_authorization_state") or "not_allowed")
    selected_strategy = str(repair_decision.get("selected_strategy") or "")
    if not should_retry:
        return {"stop": True, "reason_code": "terminal_outcome", "message": str(repair_decision.get("failure_summary") or ""), "recommended_next_step": None}
    if authorization != "authorized":
        return {
            "stop": True,
            "reason_code": "retry_not_authorized",
            "message": str(repair_decision.get("failure_summary") or "automatic retry is not authorized"),
            "recommended_next_step": str(repair_decision.get("recommended_reentry_stage") or "") or None,
        }
    if int(totals.get("attempts_total") or 0) >= int(budgets.get("max_attempts_total") or 30):
        return {
            "stop": True,
            "reason_code": "attempt_budget_exhausted",
            "message": "attempt budget exhausted before convergence",
            "recommended_next_step": selected_strategy or None,
        }
    if int(totals.get("model_tokens_total") or 0) >= int(budgets.get("max_model_tokens_total") or 100000):
        return {
            "stop": True,
            "reason_code": "token_budget_exhausted",
            "message": "model token budget exhausted before convergence",
            "recommended_next_step": selected_strategy or None,
        }
    started_at = str(dict(controller.get("totals") or {}).get("wall_clock_started_at") or controller.get("started_at") or "").strip()
    if started_at:
        try:
            started = dt.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            elapsed_minutes = max(0.0, (dt.datetime.now(dt.timezone.utc) - started).total_seconds() / 60.0)
            if elapsed_minutes >= float(budgets.get("max_wall_clock_minutes") or 60):
                return {
                    "stop": True,
                    "reason_code": "time_budget_exhausted",
                    "message": "wall-clock budget exhausted before convergence",
                    "recommended_next_step": selected_strategy or None,
                }
        except Exception:
            pass
    if int(branch.get("stagnant_attempts") or 0) >= int(budgets.get("max_stagnant_attempts") or 5):
        return {
            "stop": True,
            "reason_code": "no_progress",
            "message": "progress has stagnated across repeated attempts in the active branch",
            "recommended_next_step": selected_strategy or None,
        }
    if int(branch.get("identical_attempt_repeats") or 0) >= 2 and int(branch.get("current_mutation_level") or 1) >= 4:
        return {
            "stop": True,
            "reason_code": "no_progress",
            "message": "repeated structurally identical retries exhausted the escalation ladder without changing the search state",
            "recommended_next_step": selected_strategy or None,
        }
    return {"stop": False, "reason_code": None, "message": "", "recommended_next_step": selected_strategy or None}


def persist_controller_stop(
    *,
    job_dir: str | Path,
    config: Mapping[str, Any],
    reason_code: str,
    message: str,
    recommended_next_step: Optional[str],
) -> Dict[str, Any]:
    job_path = Path(job_dir).resolve()
    store = JobStateStore(job_path)
    state = store.load()
    controller = ensure_controller_state(state, config)
    controller["stop_reason"] = {"reason_code": str(reason_code), "message": str(message or "").strip()}
    controller["recommended_next_step"] = str(recommended_next_step or "").strip() or None
    controller["updated_at"] = utc_now_iso()
    state["controller"] = controller
    store.save(state)
    return controller


def _next_branch_id(state: Mapping[str, Any], purpose: str) -> str:
    existing = {str(key) for key in dict(state.get("branches") or {}).keys()}
    prefix = f"BRANCH-{str(purpose or 'retry').replace('_', '-')}"
    counter = 1
    while True:
        candidate = f"{prefix}-{counter:02d}"
        if candidate not in existing:
            return candidate
        counter += 1


def _inject_retry_contract(payload: Dict[str, Any], retry_contract: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(payload or {})
    result = dict(out.get("result") or {})
    result["retry_contract"] = dict(retry_contract or {})
    if "search_plan" in result and isinstance(result.get("search_plan"), Mapping):
        search_plan = dict(result.get("search_plan") or {})
        search_plan["retry_contract"] = dict(retry_contract or {})
        result["search_plan"] = search_plan
    if "context_bundle" in result and isinstance(result.get("context_bundle"), Mapping):
        bundle = dict(result.get("context_bundle") or {})
        bundle["retry_contract"] = dict(retry_contract or {})
        result["context_bundle"] = bundle
    if "patch_plan" in result and isinstance(result.get("patch_plan"), Mapping):
        patch_plan = dict(result.get("patch_plan") or {})
        patch_plan["retry_contract"] = dict(retry_contract or {})
        result["patch_plan"] = patch_plan
    out["result"] = result
    return out


def seed_reentry_inputs(
    *,
    job_dir: str | Path,
    source_run_id: str,
    target_run_id: str,
    start_stage: str,
    retry_contract: Mapping[str, Any],
) -> None:
    job_path = Path(job_dir).resolve()
    source_run_dir = job_path / "runs" / str(source_run_id)
    target_run_dir = job_path / "runs" / str(target_run_id)
    target_run_dir.mkdir(parents=True, exist_ok=True)
    files_to_copy = list(REENTRY_SEED_FILES.get(start_stage) or [])
    if not files_to_copy:
        raise PipelineError("unsupported_reentry_stage", f"Unsupported automatic reentry stage: {start_stage}", details={"stage": start_stage}, exit_code=3)
    for filename in files_to_copy:
        source_path = source_run_dir / filename
        if not source_path.exists():
            raise PipelineError("reentry_seed_missing", f"Cannot seed retry input because {filename} is missing from source run", details={"source_run_id": source_run_id, "filename": filename}, exit_code=3)
        payload = load_json(source_path)
        payload["run_id"] = str(target_run_id)
        if filename == "07_execution_contract.json":
            execution_contract = dict(payload.get("execution_contract") or {})
            execution_contract["retry_contract"] = dict(retry_contract or {})
            payload["execution_contract"] = execution_contract
        else:
            payload = _inject_retry_contract(payload, retry_contract)
        save_json_atomic(target_run_dir / filename, payload)
    save_json_atomic(target_run_dir / RETRY_CONTRACT_FILENAME, dict(retry_contract or {}))


def prepare_retry_run(
    *,
    job_dir: str | Path,
    config: Mapping[str, Any],
    source_run_id: str,
    repair_decision: Mapping[str, Any],
) -> Dict[str, str]:
    job_path = Path(job_dir).resolve()
    store = JobStateStore(job_path)
    state = store.load()
    source_run = dict(state.get("runs", {}).get(source_run_id) or {})
    if not source_run:
        raise PipelineError("missing_source_run", "source run is missing from job state", details={"run_id": source_run_id}, exit_code=3)
    branch_action = str(repair_decision.get("branch_action") or "")
    reentry_stage = str(repair_decision.get("recommended_reentry_stage") or "")
    retry_contract = dict(repair_decision.get("retry_contract") or {})
    mutation_level = int(repair_decision.get("mutation_level") or 1)
    retry_mode = str(repair_decision.get("retry_mode") or "escalate")
    retry_reason = str(repair_decision.get("retry_reason") or repair_decision.get("failure_summary") or "autonomous retry")
    if not reentry_stage:
        raise PipelineError("missing_reentry_stage", "repair decision did not provide a reentry stage", exit_code=3)
    new_run_id = make_run_id()
    source_branch_id = str(source_run.get("branch_id") or "BRANCH-main")
    source_branch = dict(state.get("branches", {}).get(source_branch_id) or {})
    source_branch_purpose = str(source_branch.get("branch_purpose") or "primary")
    next_branch_purpose = str(repair_decision.get("next_branch_purpose") or "repair")
    allow_same_branch_reentry = bool(dict(config.get("orchestrator") or {}).get("allow_same_branch_reentry", True))
    if branch_action == "same_branch_reentry":
        branch_id = source_branch_id
        store.create_child_run(
            branch_id=branch_id,
            run_id=new_run_id,
            parent_run_id=source_run_id,
            reentry_stage=reentry_stage,
            retry_mode=retry_mode,
            retry_reason=retry_reason,
            recommended_branch_action=branch_action,
        )
    elif branch_action == "fork_new_branch":
        if allow_same_branch_reentry and source_branch_purpose == next_branch_purpose and source_branch_purpose != "primary":
            branch_id = source_branch_id
            store.create_child_run(
                branch_id=branch_id,
                run_id=new_run_id,
                parent_run_id=source_run_id,
                reentry_stage=reentry_stage,
                retry_mode=retry_mode,
                retry_reason=retry_reason,
                recommended_branch_action="same_branch_reentry",
            )
            branch_action = "same_branch_reentry"
        else:
            total_branches_limit = int(dict(config.get("branching") or {}).get("max_total_branches") or dict(config.get("orchestrator") or {}).get("max_retry_branches_per_job") or 10)
            if len(dict(state.get("branches") or {})) >= total_branches_limit:
                raise PipelineError("branch_budget_exhausted", "maximum retry branches exceeded", details={"max_total_branches": total_branches_limit}, exit_code=3)
            branch_purpose = next_branch_purpose
            branch_id = _next_branch_id(state, branch_purpose)
            store.fork_branch(
                parent_run_id=source_run_id,
                new_branch_id=branch_id,
                new_run_id=new_run_id,
                branch_purpose=branch_purpose,
                reentry_stage=reentry_stage,
                retry_mode=retry_mode,
                retry_reason=retry_reason,
                recommended_branch_action=branch_action,
            )
    else:
        raise PipelineError("unsupported_branch_action", f"Unsupported automatic branch action: {branch_action}", details={"branch_action": branch_action}, exit_code=3)
    state = store.load()
    controller = ensure_controller_state(state, config)
    branch_state = branch_controller_state(controller, branch_id, branch_purpose=str(dict(state.get("branches", {}).get(branch_id) or {}).get("branch_purpose") or "primary"))
    branch_state["current_mutation_level"] = int(mutation_level or 1)
    branch_state["same_level_attempts"] = 0
    branch_state["cooldown_remaining"] = max(0, int(dict(config.get("mutation") or {}).get("escalation_cooldown") or 1) - 1)
    controller["branches"][branch_id] = branch_state
    state["controller"] = controller
    store.save(state)
    seed_reentry_inputs(
        job_dir=job_path,
        source_run_id=source_run_id,
        target_run_id=new_run_id,
        start_stage=reentry_stage,
        retry_contract=retry_contract,
    )
    return {"run_id": new_run_id, "branch_id": branch_id, "reentry_stage": reentry_stage}
