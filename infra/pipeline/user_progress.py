from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .core import load_json, save_json_atomic, utc_now_iso
from .job_state import JobStateStore

USER_PROGRESS_SCHEMA_VERSION = "1"
USER_PROGRESS_FILENAME = "job_progress.json"

_STAGE_MESSAGES: Dict[str, Dict[str, str]] = {
    "script_1_job_intake": {"phase": "understanding_request", "headline": "Understanding the request", "detail": "Reading the request and preparing the job."},
    "script_2_task_classifier": {"phase": "classifying_task", "headline": "Classifying the task", "detail": "Determining the task type and risk profile."},
    "script_3_search_plan": {"phase": "planning_search", "headline": "Planning the search", "detail": "Choosing where to look in the repo."},
    "script_4_repo_retriever": {"phase": "searching_repo", "headline": "Searching the repo", "detail": "Looking for likely implementation and test files."},
    "script_5_context_ranker": {"phase": "ranking_context", "headline": "Narrowing the file set", "detail": "Ranking owner, support, and test files."},
    "script_6_context_bundle": {"phase": "bundling_context", "headline": "Preparing context", "detail": "Bundling edit targets and validation context."},
    "script_7_patch_plan": {"phase": "planning_change", "headline": "Planning the change", "detail": "Choosing edits, scope, and checks."},
    "script_8_patch_generate": {"phase": "generating_patch", "headline": "Generating a candidate patch", "detail": "Asking the model for a patch and verification claim."},
    "script_9_artifact_validate": {"phase": "validating_patch", "headline": "Validating the patch", "detail": "Checking diff format, scope, and applicability."},
    "script_10_patch_apply": {"phase": "applying_patch", "headline": "Applying the patch", "detail": "Trying the patch in an isolated workspace."},
    "script_11_verify_static": {"phase": "running_static_checks", "headline": "Running static checks", "detail": "Checking syntax and non-runtime validation."},
    "script_12_verify_runtime": {"phase": "running_tests", "headline": "Running tests", "detail": "Executing the selected test commands."},
    "script_13_contract_verify": {"phase": "verifying_contract", "headline": "Verifying the contract", "detail": "Checking whether the change matches the requested behavior."},
    "script_14_repair_loop": {"phase": "assessing_attempt", "headline": "Assessing the attempt", "detail": "Deciding whether to stop or retry."},
    "script_15_finalize_job": {"phase": "finalizing_attempt", "headline": "Finalizing the result", "detail": "Committing the outcome for this attempt."},
    "script_16_job_report": {"phase": "building_report", "headline": "Preparing the report", "detail": "Building the operator-facing report."},
}

_STRATEGY_LABELS = {
    "local_fix": "local patch fix",
    "plan_revision": "plan revision",
    "context_expansion": "context expansion",
    "task_reinterpretation": "task reinterpretation",
}


def _compact_text(text: Any, *, limit: int = 180) -> str:
    raw = " ".join(str(text or "").strip().split())
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 3)].rstrip() + "..."


def _human_stage_name(stage: str) -> str:
    stage_info = dict(_STAGE_MESSAGES.get(str(stage or "").strip()) or {})
    headline = str(stage_info.get("headline") or "").strip()
    return headline.lower() if headline else str(stage or "").strip().replace("_", " ")


def _retry_strategy_label(strategy: Any) -> str:
    key = str(strategy or "").strip()
    return _STRATEGY_LABELS.get(key, key.replace("_", " "))


def _retry_goal(payload: Mapping[str, Any]) -> str:
    strategy = str(payload.get("strategy") or "").strip()
    failure_summary = _compact_text(payload.get("failure_summary") or payload.get("retry_reason") or "", limit=120)
    if strategy == "local_fix":
        base = "repair the current patch without widening scope"
    elif strategy == "plan_revision":
        base = "revise the plan so the next patch changes the right files and checks"
    elif strategy == "context_expansion":
        base = "pull in more supporting code and tests before replanning"
    elif strategy == "task_reinterpretation":
        base = "reinterpret the task before searching and planning again"
    else:
        base = "change the upstream inputs before retrying"
    if failure_summary:
        return f"{base}; fixing: {failure_summary}"
    return base


def _expected_change(payload: Mapping[str, Any]) -> str:
    strategy_note = _compact_text(payload.get("strategy_note") or "", limit=120)
    if strategy_note:
        return strategy_note
    mutations = [str(item or "").strip() for item in list(payload.get("upstream_mutations") or []) if str(item or "").strip()]
    if mutations:
        return _compact_text("; ".join(mutations[:2]), limit=120)
    reentry_stage = str(payload.get("reentry_stage") or "").strip()
    if reentry_stage:
        return f"restart from {_human_stage_name(reentry_stage)} with different inputs"
    return "change the next attempt inputs in a measurable way"


def _signed_delta(value: Any) -> str:
    try:
        return f"{float(value):+.3f}"
    except Exception:
        return ""


def _trend_label(payload: Mapping[str, Any]) -> str:
    if bool(payload.get("materially_improved")):
        return "improving"
    if bool(payload.get("regression_detected")):
        return "regressing"
    if bool(payload.get("stagnation_detected")):
        return "stalled"
    if int(payload.get("attempts_total") or 0) > 1:
        return "unclear"
    return "initial"


def _stop_forecast(payload: Mapping[str, Any]) -> str:
    stagnant_left = payload.get("stagnant_retries_remaining")
    attempts_left = payload.get("attempts_remaining")
    token_remaining = payload.get("token_budget_remaining")
    if stagnant_left is not None:
        try:
            count = int(stagnant_left)
            if count <= 1:
                return "one more non-improving retry before stop"
            return f"{count} non-improving retries left before stop"
        except Exception:
            pass
    if attempts_left is not None:
        try:
            count = int(attempts_left)
            return f"{count} attempts left before budget stop"
        except Exception:
            pass
    if token_remaining is not None:
        try:
            count = int(token_remaining)
            return f"{count:,} tokens left before budget stop"
        except Exception:
            pass
    return ""


def _convergence_summary(payload: Mapping[str, Any]) -> str:
    trend = _trend_label(payload)
    delta = _signed_delta(payload.get("delta_from_previous"))
    repeats = int(payload.get("failure_cluster_repeat_count") or 0)
    same_level_attempts = int(payload.get("same_level_attempts") or 0)
    identical_attempt_repeats = int(payload.get("identical_attempt_repeats") or 0)
    parts = [trend]
    if delta:
        parts.append(f"delta {delta}")
    if identical_attempt_repeats > 0:
        parts.append(f"same structural retry x{identical_attempt_repeats + 1}")
    elif repeats > 1:
        parts.append(f"same failure x{repeats}")
    elif same_level_attempts > 1:
        parts.append(f"{same_level_attempts} tries on this strategy")
    forecast = _stop_forecast(payload)
    if forecast:
        parts.append(forecast)
    return ", ".join(part for part in parts if part)


def _convergence_note(payload: Mapping[str, Any]) -> str:
    summary = _convergence_summary(payload)
    if summary:
        return summary
    if bool(payload.get("equivalent_attempt_detected")):
        return "the last attempt looked equivalent, so the controller forced an upstream change"
    if bool(payload.get("materially_improved")):
        return "this branch is improving materially"
    if bool(payload.get("regression_detected")):
        return "the latest attempt regressed, so the controller is escalating"
    if bool(payload.get("stagnation_detected")):
        return "there is no material improvement yet"
    attempts_total = int(payload.get("attempts_total") or 0)
    if attempts_total > 1:
        return "the controller is testing a new retry path"
    return "this is the first attempt"


def _default_goal_for_phase(phase: str, *, retry_goal: str = "") -> str:
    if retry_goal and phase in {"retrying", "planning_change", "planning_search", "generating_patch"}:
        return retry_goal
    return {
        "understanding_request": "understand the request well enough to avoid the wrong fix",
        "classifying_task": "identify the task shape and risk profile",
        "planning_search": "decide which code and tests need inspection",
        "searching_repo": "find the likely implementation and test files",
        "ranking_context": "narrow the file set to the most actionable owner and test files",
        "bundling_context": "prepare the smallest useful edit and validation bundle",
        "planning_change": "produce an actionable change plan and edit scope",
        "generating_patch": "produce the next viable patch and verification claim",
        "validating_patch": "prove the patch is structurally valid before apply",
        "applying_patch": "apply the patch cleanly in isolation",
        "running_static_checks": "catch syntax and static issues quickly",
        "running_tests": "prove the requested behavior through tests",
        "verifying_contract": "verify the patch actually satisfies the request",
        "assessing_attempt": "decide whether the system is done or needs a smarter retry",
        "retrying": retry_goal or "prepare the next attempt with different upstream inputs",
    }.get(str(phase or "").strip(), "keep making measurable progress toward a valid result")


def user_progress_path(job_dir: str | Path) -> Path:
    return Path(job_dir).resolve() / USER_PROGRESS_FILENAME


def _safe_load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return load_json(path)
    except Exception:
        return {}


def _parse_iso_timestamp(text: str) -> Optional[dt.datetime]:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _dedupe_strings(values: List[Any], *, limit: int = 12) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _path_list(entries: Any) -> List[str]:
    if not isinstance(entries, list):
        return []
    out: List[str] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            out.append(str(entry.get("path") or "").strip())
        else:
            out.append(str(entry or "").strip())
    return _dedupe_strings(out)


def describe_progress_event(event: str, payload: Mapping[str, Any]) -> Dict[str, str]:
    stage = str(payload.get("stage") or payload.get("start_stage") or "").strip()
    stage_info = dict(_STAGE_MESSAGES.get(stage) or {})
    if event == "job_prepared":
        return {"phase": "preparing_job", "headline": "Preparing the job", "detail": f"Created job {str(payload.get('job_id') or '').strip()}.".strip()}
    if event == "pipeline_started":
        if stage_info:
            return {"phase": stage_info["phase"], "headline": stage_info["headline"], "detail": stage_info["detail"]}
        return {"phase": "starting_pipeline", "headline": "Starting the pipeline", "detail": "Launching the first attempt."}
    if event == "stage_start":
        if stage_info:
            return {"phase": stage_info["phase"], "headline": stage_info["headline"], "detail": stage_info["detail"]}
        return {"phase": "running_stage", "headline": "Working", "detail": stage or "Running a pipeline stage."}
    if event == "stage_heartbeat":
        detail = str(payload.get("detail") or "").strip()
        if stage_info:
            return {"phase": stage_info["phase"], "headline": stage_info["headline"], "detail": detail or stage_info["detail"]}
        return {"phase": "running_stage", "headline": "Working", "detail": detail or "Still working."}
    if event == "stage_complete":
        if stage_info:
            return {"phase": stage_info["phase"], "headline": f"Completed {stage_info['headline'].lower()}", "detail": "Moving to the next step."}
        return {"phase": "stage_complete", "headline": "Completed a stage", "detail": "Moving to the next step."}
    if event == "stage_failed":
        reason = str(payload.get("reason") or "stage_failed").strip().replace("_", " ")
        if stage_info:
            return {"phase": stage_info["phase"], "headline": f"Stopped in {stage_info['headline'].lower()}", "detail": reason}
        return {"phase": "stage_failed", "headline": "Stopped in a stage", "detail": reason}
    if event == "retry_prepared":
        strategy = _retry_strategy_label(payload.get("strategy") or "")
        goal = _compact_text(_retry_goal(payload), limit=84)
        expected_change = _compact_text(_expected_change(payload), limit=84)
        convergence = _convergence_note(payload)
        return {
            "phase": "retrying",
            "headline": f"Retrying via {strategy}" if strategy else "Retrying after a failed attempt",
            "detail": f"Goal: {goal}. Expected change: {expected_change}. Progress: {convergence}.",
        }
    if event == "review_render_start":
        return {"phase": "rendering_review", "headline": "Preparing the review", "detail": "Rendering the review package for the completed attempt."}
    if event == "review_render_complete":
        return {"phase": "rendering_review", "headline": "Review ready", "detail": "The review package is ready."}
    if event == "operator_decision_saved":
        decision = str(payload.get("decision") or "").strip().lower()
        return {"phase": "review_decision", "headline": "Review decision saved", "detail": f"Decision: {decision or 'unknown'}."}
    if event == "controller_stop":
        message = str(payload.get("message") or "").strip() or "Returning the best available result so far."
        return {"phase": "stopping", "headline": "Stopping and returning the best available result", "detail": message}
    if event == "summary_persisted":
        status = str(payload.get("status") or "").strip()
        return {"phase": "completed", "headline": "Finished", "detail": f"Final status: {status or 'unknown'}."}
    if event == "pipeline_complete":
        status = str(payload.get("status") or "").strip()
        return {
            "phase": "attempt_complete",
            "headline": "Attempt complete",
            "detail": _compact_text(f"Current status: {status or 'unknown'}. The controller is deciding whether this is good enough or needs a different retry.", limit=220),
        }
    if event == "user_stop_requested":
        message = str(payload.get("message") or "").strip() or "Finishing the current attempt and returning the best available result."
        return {"phase": "stop_requested", "headline": "Stop requested", "detail": message}
    return {"phase": "working", "headline": "Working", "detail": str(event or "working").replace("_", " ")}


def _load_attempt_metadata(job_dir: Path, run_id: str) -> Dict[str, Any]:
    if not run_id:
        return {}
    try:
        state = JobStateStore(job_dir).load()
    except Exception:
        return {}
    run = dict(state.get("runs", {}).get(run_id) or {})
    controller = dict(state.get("controller") or {})
    budgets = dict(controller.get("budgets") or {})
    totals = dict(controller.get("totals") or {})
    best_run_id = str(controller.get("best_run_id") or "").strip()
    return {
        "attempt_index": int(run.get("attempt_index") or 0) + 1,
        "max_attempts": int(budgets.get("max_attempts_total") or 0),
        "branch_id": str(run.get("branch_id") or state.get("active_branch_id") or "").strip(),
        "best_run_id": best_run_id or None,
        "best_progress_score": controller.get("best_progress_score"),
        "model_tokens_total": int(totals.get("model_tokens_total") or 0),
        "max_model_tokens_total": int(budgets.get("max_model_tokens_total") or 0),
        "attempts_total": int(totals.get("attempts_total") or 0),
    }


def _extract_files_under_consideration(job_dir: Path, run_id: str) -> List[str]:
    if not run_id:
        return []
    run_dir = job_dir / "runs" / str(run_id)
    execution_contract = dict(_safe_load_json(run_dir / "07_execution_contract.json").get("execution_contract") or {})
    files = _dedupe_strings(
        list(execution_contract.get("allowed_edit_files") or [])
        + list(execution_contract.get("validation_target_files") or [])
        + list(execution_contract.get("implementation_target_files") or [])
    )
    if files:
        return files
    bundle = dict(dict(_safe_load_json(run_dir / "06_context_bundle.json").get("result") or {}).get("context_bundle") or {})
    files = _dedupe_strings(
        _path_list(bundle.get("editable_targets"))
        + _path_list(bundle.get("validation_targets"))
        + _path_list(bundle.get("read_only_context"))
    )
    if files:
        return files
    ranking = dict(dict(_safe_load_json(run_dir / "05_context_ranked.json").get("result") or {}).get("ranked_context") or {})
    return _dedupe_strings(
        _path_list(ranking.get("implementation_targets"))
        + _path_list(ranking.get("validation_targets"))
        + _path_list(ranking.get("selected_files"))
    )


def update_user_progress(
    *,
    job_dir: str | Path,
    event: str,
    payload: Mapping[str, Any],
    prompt_text: str = "",
) -> Dict[str, Any]:
    job_path = Path(job_dir).resolve()
    path = user_progress_path(job_path)
    current = _safe_load_json(path)
    now_iso = utc_now_iso()
    job_id = str(payload.get("job_id") or current.get("job_id") or job_path.name).strip()
    run_id = str(payload.get("run_id") or current.get("current_run_id") or "").strip()
    status_info = describe_progress_event(str(event), payload)
    started_at = str(current.get("started_at") or now_iso)
    started_dt = _parse_iso_timestamp(started_at) or _parse_iso_timestamp(now_iso) or dt.datetime.now(dt.timezone.utc)
    elapsed_ms = max(0, int((dt.datetime.now(dt.timezone.utc) - started_dt).total_seconds() * 1000))
    attempt_meta = _load_attempt_metadata(job_path, run_id)
    files_under_consideration = current.get("files_under_consideration") or []
    fresh_files = _extract_files_under_consideration(job_path, run_id)
    if fresh_files:
        files_under_consideration = fresh_files
    progress: Dict[str, Any] = dict(current)
    progress.update(
        {
            "schema_version": USER_PROGRESS_SCHEMA_VERSION,
            "job_id": job_id,
            "started_at": started_at,
            "last_updated": now_iso,
            "current_run_id": run_id or None,
            "current_branch_id": str(payload.get("branch_id") or attempt_meta.get("branch_id") or current.get("current_branch_id") or "").strip() or None,
            "current_phase": status_info["phase"],
            "headline": status_info["headline"],
            "detail": status_info["detail"],
            "last_event": str(event),
            "elapsed_ms": elapsed_ms,
            "files_under_consideration": files_under_consideration,
            "attempt_index": attempt_meta.get("attempt_index") or current.get("attempt_index"),
            "max_attempts": attempt_meta.get("max_attempts") or current.get("max_attempts"),
            "best_result_so_far": {
                "best_run_id": attempt_meta.get("best_run_id") or dict(current.get("best_result_so_far") or {}).get("best_run_id"),
                "best_progress_score": attempt_meta.get("best_progress_score"),
            },
            "model_tokens_total": attempt_meta.get("model_tokens_total") or current.get("model_tokens_total"),
            "max_model_tokens_total": attempt_meta.get("max_model_tokens_total") or current.get("max_model_tokens_total"),
            "attempts_total": attempt_meta.get("attempts_total") or current.get("attempts_total"),
            "prompt_excerpt": (str(prompt_text or current.get("prompt_excerpt") or "").strip()[:220] or None),
        }
    )
    if "stage" in payload:
        progress["stage"] = str(payload.get("stage") or "").strip() or None
    if "stage_index" in payload:
        progress["stage_index"] = int(payload.get("stage_index") or 0) or None
    if "total_stages" in payload:
        progress["total_stages"] = int(payload.get("total_stages") or 0) or None
    if event == "retry_prepared":
        retry_goal = _retry_goal(payload)
        expected_change = _expected_change(payload)
        convergence_note = _convergence_note(payload)
        convergence_summary = _convergence_summary(payload)
        stop_forecast = _stop_forecast(payload)
        progress["retry_reason"] = str(payload.get("failure_summary") or payload.get("retry_reason") or "").strip() or None
        progress["recommended_next_step"] = str(payload.get("strategy") or payload.get("reentry_stage") or "").strip() or None
        progress["retry_strategy"] = str(payload.get("strategy") or "").strip() or None
        progress["retry_reentry_stage"] = str(payload.get("reentry_stage") or "").strip() or None
        progress["retry_goal"] = retry_goal or None
        progress["expected_change"] = expected_change or None
        progress["convergence_note"] = convergence_note or None
        progress["convergence_summary"] = convergence_summary or None
        progress["stop_forecast"] = stop_forecast or None
        progress["attempts_total"] = int(payload.get("attempts_total") or progress.get("attempts_total") or 0) or None
        progress["next_attempt_index"] = int(payload.get("next_attempt_index") or 0) or None
        progress["progress_score"] = payload.get("progress_score")
        progress["delta_from_previous"] = payload.get("delta_from_previous")
        progress["delta_from_best"] = payload.get("delta_from_best")
        progress["same_level_attempts"] = int(payload.get("same_level_attempts") or 0) or None
        progress["identical_attempt_repeats"] = int(payload.get("identical_attempt_repeats") or 0) or None
        progress["failure_cluster_repeat_count"] = int(payload.get("failure_cluster_repeat_count") or 0) or None
        progress["attempts_remaining"] = int(payload.get("attempts_remaining") or 0) if payload.get("attempts_remaining") is not None else None
        progress["stagnant_retries_remaining"] = int(payload.get("stagnant_retries_remaining") or 0) if payload.get("stagnant_retries_remaining") is not None else None
        progress["token_budget_remaining"] = int(payload.get("token_budget_remaining") or 0) if payload.get("token_budget_remaining") is not None else None
        progress["stagnation_detected"] = bool(payload.get("stagnation_detected"))
        progress["materially_improved"] = bool(payload.get("materially_improved"))
        progress["regression_detected"] = bool(payload.get("regression_detected"))
        progress["status"] = "retrying"
    elif event == "controller_stop":
        progress["status"] = "stopped"
        progress["recommended_next_step"] = str(payload.get("recommended_next_step") or "").strip() or None
    elif event == "summary_persisted":
        progress["status"] = str(payload.get("status") or "completed").strip().lower() or "completed"
        progress["summary_json"] = str(payload.get("summary_json") or "").strip() or None
        progress["summary_md"] = str(payload.get("summary_md") or "").strip() or None
    elif event == "user_stop_requested":
        progress["status"] = "stop_requested"
        progress["stop_requested"] = True
    elif event == "stage_failed":
        progress["status"] = "failed"
    elif event == "pipeline_complete":
        progress["attempt_status"] = str(payload.get("status") or "").strip() or None
    else:
        progress["status"] = str(current.get("status") or "running")
    phase = str(progress.get("current_phase") or "").strip()
    retry_goal = str(progress.get("retry_goal") or "").strip()
    progress["current_goal"] = _default_goal_for_phase(phase, retry_goal=retry_goal) or None
    save_json_atomic(path, progress)
    return progress


def mark_user_stop_requested(*, job_dir: str | Path, message: str) -> Dict[str, Any]:
    return update_user_progress(job_dir=job_dir, event="user_stop_requested", payload={"message": str(message or "").strip()})
