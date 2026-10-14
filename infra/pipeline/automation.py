from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from .autonomous_controller import evaluate_stop_condition, persist_controller_stop, prepare_retry_run, record_attempt_result
from .core import PipelineError, ensure_directory, load_json, load_pipeline_config, save_json_atomic, utc_now_iso
from .operator_decision import persist_operator_decision
from .pipeline_runner import PipelineRunner
from .review_render import render_review
from .run_summary import build_run_summary, persist_run_summary
from .user_progress import update_user_progress


def _slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    return (slug or "job")[:max_len].strip("-") or "job"


def make_job_id(prompt_text: str) -> str:
    timestamp = utc_now_iso().replace("-", "").replace(":", "").replace("T", "-").replace("Z", "")
    return f"JOB-{timestamp}-{_slugify(prompt_text, max_len=32)}"


def make_raw_request(*, prompt_text: str, repo_root: str | Path, job_id: str) -> Dict[str, Any]:
    prompt = str(prompt_text or "").strip()
    short_title = prompt[:220]
    return {
        "job_id": str(job_id),
        "repo_root": str(Path(repo_root).resolve()),
        "title": short_title,
        "description": prompt,
        "objective": prompt,
        "user_notes": prompt,
        "task_type_hint": "unknown",
        "constraints": [],
        "acceptance_criteria": [],
        "metadata": {"invocation_mode": "full_auto_prompt", "defaults_auto_accepted": True, "auto_review_default": True},
    }


def prepare_job_input(*, repo_root: str | Path, job_id: str, raw_request: Mapping[str, Any], config: Optional[Mapping[str, Any]] = None) -> Path:
    cfg = dict(config or load_pipeline_config(repo_root=repo_root))
    jobs_root = Path(cfg["_derived"]["jobs_root"])
    job_dir = jobs_root / str(job_id)
    ensure_directory(job_dir / "input")
    ensure_directory(job_dir / "runs")
    input_path = job_dir / "input" / "00_raw_request.json"
    save_json_atomic(input_path, dict(raw_request))
    return input_path


def _load_stage_result(job_dir: Path, run_id: str, filename: str, key: str) -> Dict[str, Any]:
    path = job_dir / "runs" / str(run_id) / filename
    if not path.exists():
        return {}
    try:
        payload = load_json(path)
    except Exception:
        return {}
    return dict(dict(payload.get("result") or {}).get(key) or {})


def run_full_auto(
    *,
    repo_root: str | Path,
    prompt_text: str,
    raw_request: Optional[Mapping[str, Any]] = None,
    job_id: Optional[str] = None,
    config_overrides: Optional[Mapping[str, Any]] = None,
    auto_approve: bool = True,    progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    stop_after_stage: str = "",
    stop_requested: Optional[Callable[[], bool]] = None,
    process_handle_callback: Optional[Callable[[Any], None]] = None,
) -> Dict[str, Any]:
    job_progress_dir: Optional[Path] = None

    def _emit(event: str, **payload: Any) -> None:
        if job_progress_dir is not None:
            try:
                update_user_progress(job_dir=job_progress_dir, event=event, payload=dict(payload), prompt_text=prompt_text_resolved)
            except Exception:
                pass
        if progress_callback is None:            return
        try:
            progress_callback(event, dict(payload))
        except Exception:
            pass

    def _runner_progress(event: str, payload: Dict[str, Any]) -> None:
        _emit(event, **dict(payload))

    repo = Path(repo_root).resolve()
    config = load_pipeline_config(repo_root=repo, cli_overrides=config_overrides)
    request_payload = dict(raw_request or {})
    prompt_text_resolved = str(prompt_text or request_payload.get("user_notes") or request_payload.get("description") or request_payload.get("objective") or "").strip()
    if not prompt_text_resolved:
        prompt_text_resolved = str(prompt_text or "").strip()
    resolved_job_id = str(job_id or request_payload.get("job_id") or make_job_id(prompt_text_resolved))
    request_payload = dict(request_payload or make_raw_request(prompt_text=prompt_text_resolved, repo_root=repo, job_id=resolved_job_id))
    request_payload.setdefault("job_id", resolved_job_id)
    request_payload.setdefault("repo_root", str(repo))
    input_path = prepare_job_input(repo_root=repo, job_id=resolved_job_id, raw_request=request_payload, config=config)
    job_progress_dir = input_path.parent.parent
    _emit("job_prepared", job_id=resolved_job_id, repo_root=str(repo), input_path=str(input_path))
    runner = PipelineRunner(repo_root=repo, config_overrides=config_overrides, progress_callback=_runner_progress)
    setattr(runner, "process_handle_callback", process_handle_callback)
    final_run_result: Dict[str, Any] = {}
    final_run_id = ""
    final_job_dir = Path()
    review_result: Dict[str, Any] = {}
    decision_result: Dict[str, Any] = {}
    controller_stop: Dict[str, Any] = {}
    resume = False
    while True:
        run_kwargs: Dict[str, Any] = {"job_id": resolved_job_id}
        if stop_after_stage:
            run_kwargs["stop_after_stage"] = stop_after_stage
        if resume:
            run_kwargs["resume"] = True
        setattr(runner, "cancel_requested", stop_requested)
        run_result = runner.run(**run_kwargs)
        run_id = str(run_result["run_id"])
        job_dir = Path(run_result["job_dir"])
        final_run_result = run_result
        final_run_id = run_id
        final_job_dir = job_dir
        repair_decision = _load_stage_result(job_dir, run_id, "14_repair_decision.json", "repair_decision")
        finalization = _load_stage_result(job_dir, run_id, "15_finalization.json", "finalization")
        if repair_decision:
            controller = record_attempt_result(
                job_dir=job_dir,
                run_id=run_id,
                config=config,
                repair_decision=repair_decision,
                final_status=str(finalization.get("final_status") or run_result.get("status") or ""),
            )
            if stop_requested is not None and stop_requested():
                if not controller_stop:
                    controller_stop = {
                        "stop": True,
                        "reason_code": "user_cancelled",
                        "message": "user requested stop; returning the best available result so far",
                        "recommended_next_step": str(repair_decision.get("recommended_reentry_stage") or repair_decision.get("selected_strategy") or "").strip() or None,
                    }
                    persist_controller_stop(
                        job_dir=job_dir,
                        config=config,
                        reason_code="user_cancelled",
                        message=str(controller_stop.get("message") or ""),
                        recommended_next_step=controller_stop.get("recommended_next_step"),
                    )
                    _emit(
                        "controller_stop",
                        job_id=resolved_job_id,
                        run_id=run_id,
                        reason_code="user_cancelled",
                        message=str(controller_stop.get("message") or ""),
                        recommended_next_step=str(controller_stop.get("recommended_next_step") or ""),
                    )
                break
            if stop_after_stage:
                break
            stop_eval = evaluate_stop_condition(
                controller=controller,
                branch_id=str(run_result.get("branch_id") or ""),
                repair_decision=repair_decision,
            )
            if stop_eval["stop"]:
                if str(stop_eval.get("reason_code") or "") not in {"terminal_outcome"}:
                    controller_stop = stop_eval
                    persist_controller_stop(
                        job_dir=job_dir,
                        config=config,
                        reason_code=str(stop_eval.get("reason_code") or "controller_stop"),
                        message=str(stop_eval.get("message") or ""),
                        recommended_next_step=stop_eval.get("recommended_next_step"),
                    )
                    _emit(
                        "controller_stop",
                        job_id=resolved_job_id,
                        run_id=run_id,
                        reason_code=str(stop_eval.get("reason_code") or ""),
                        message=str(stop_eval.get("message") or ""),
                        recommended_next_step=str(stop_eval.get("recommended_next_step") or ""),
                    )
                break
            try:
                next_run = prepare_retry_run(
                    job_dir=job_dir,
                    config=config,
                    source_run_id=run_id,
                    repair_decision=repair_decision,
                )
            except PipelineError as exc:
                reason_code = exc.code or "retry_preparation_failed"
                message = exc.message or str(exc)
                controller_stop = {
                    "stop": True,
                    "reason_code": str(reason_code),
                    "message": str(message or "").strip() or "automatic retry preparation failed",
                    "recommended_next_step": str(repair_decision.get("selected_strategy") or repair_decision.get("recommended_reentry_stage") or "").strip() or None,
                }
                persist_controller_stop(
                    job_dir=job_dir,
                    config=config,
                    reason_code=str(controller_stop.get("reason_code") or ""),
                    message=str(controller_stop.get("message") or ""),
                    recommended_next_step=controller_stop.get("recommended_next_step"),
                )
                _emit(
                    "controller_stop",
                    job_id=resolved_job_id,
                    run_id=run_id,
                    reason_code=str(controller_stop.get("reason_code") or ""),
                    message=str(controller_stop.get("message") or ""),
                    recommended_next_step=str(controller_stop.get("recommended_next_step") or ""),
                )
                break
            controller_state = dict(controller or {})
            controller_budgets = dict(controller_state.get("budgets") or {})
            controller_totals = dict(controller_state.get("totals") or {})
            current_branch_id = str(run_result.get("branch_id") or "")
            branch_state = dict(dict(controller_state.get("branches") or {}).get(current_branch_id) or {})
            controller_analysis = dict(repair_decision.get("controller_analysis") or {})
            progress_snapshot = dict(controller_analysis.get("progress_snapshot") or {})
            retry_contract = dict(repair_decision.get("retry_contract") or {})
            failure_cluster_id = str(controller_analysis.get("failure_cluster_id") or "")
            cluster_state = dict(dict(branch_state.get("failure_clusters") or {}).get(failure_cluster_id) or {}) if failure_cluster_id else {}
            attempts_total = int(controller_totals.get("attempts_total") or 0)
            max_attempts_total = int(controller_budgets.get("max_attempts_total") or 0)
            token_budget_total = int(controller_budgets.get("max_model_tokens_total") or 0)
            token_budget_used = int(controller_totals.get("model_tokens_total") or 0)
            stagnant_attempts = int(branch_state.get("stagnant_attempts") or 0)
            max_stagnant_attempts = int(controller_budgets.get("max_stagnant_attempts") or 0)
            _emit(
                "retry_prepared",
                job_id=resolved_job_id,
                run_id=run_id,
                next_run_id=str(next_run.get("run_id") or ""),
                next_branch_id=str(next_run.get("branch_id") or ""),
                reentry_stage=str(next_run.get("reentry_stage") or ""),
                strategy=str(repair_decision.get("selected_strategy") or ""),
                retry_reason=str(repair_decision.get("retry_reason") or ""),
                failure_summary=str(repair_decision.get("failure_summary") or ""),
                branch_action=str(repair_decision.get("branch_action") or ""),
                mutation_level=int(repair_decision.get("mutation_level") or 1),
                strategy_note=str(retry_contract.get("strategy_note") or ""),
                upstream_mutations=list(retry_contract.get("upstream_mutations") or []),
                failure_cluster_id=failure_cluster_id,
                branch_id=current_branch_id,
                branch_purpose=str(branch_state.get("branch_purpose") or ""),
                equivalent_attempt_detected=bool(controller_analysis.get("equivalent_attempt_detected")),
                progress_score=progress_snapshot.get("progress_score"),
                delta_from_previous=progress_snapshot.get("delta_from_previous"),
                delta_from_best=progress_snapshot.get("delta_from_best"),
                stagnation_detected=bool(progress_snapshot.get("stagnation_detected")),
                materially_improved=bool(progress_snapshot.get("materially_improved")),
                regression_detected=bool(progress_snapshot.get("regression_detected")),
                failing_tests=progress_snapshot.get("failing_tests"),
                attempts_total=attempts_total,
                next_attempt_index=attempts_total + 1,
                max_attempts_total=max_attempts_total,
                attempts_remaining=max(0, max_attempts_total - attempts_total),
                same_level_attempts=int(branch_state.get("same_level_attempts") or 0),
                identical_attempt_repeats=int(branch_state.get("identical_attempt_repeats") or 0),
                failure_cluster_repeat_count=len(list(cluster_state.get("run_ids") or [])),
                stagnant_attempts=stagnant_attempts,
                max_stagnant_attempts=max_stagnant_attempts,
                stagnant_retries_remaining=max(0, max_stagnant_attempts - stagnant_attempts),
                token_budget_remaining=max(0, token_budget_total - token_budget_used),
                best_progress_score=controller_state.get("best_progress_score"),
            )
            resume = True
            continue
        break
    run_result = final_run_result
    run_id = final_run_id
    job_dir = final_job_dir
    review_required = str(run_result.get("status") or "") == "WAITING_REVIEW" and not controller_stop
    if review_required and (job_dir / "runs" / run_id / "07_patch_plan.json").exists():
        _emit("review_render_start", job_id=resolved_job_id, run_id=run_id)
        review_result = render_review(job_dir=job_dir, run_id=run_id, config=config)
        _emit("review_render_complete", job_id=resolved_job_id, run_id=run_id, review_rendered=bool(review_result))
        if auto_approve:
            decision_result = persist_operator_decision(job_dir=job_dir, run_id=run_id, config=config, decision="APPROVE", decision_reason="Auto-approved by projflow full-auto mode", requested_revisions=[])
            _emit("operator_decision_saved", job_id=resolved_job_id, run_id=run_id, decision=decision_result.get("decision") or "")
    summary = build_run_summary(job_dir=job_dir, run_id=run_id, config=config, prompt_text=prompt_text_resolved, status=run_result.get("status"))
    summary["auto_review"] = {"review_rendered": bool(review_result), "auto_approved": bool(decision_result), "decision": decision_result.get("decision") if decision_result else None}
    summary["defaults"] = {"contract_auto_accepted": True, "review_auto_accepted": auto_approve}
    if controller_stop:        summary["controller_stop"] = dict(controller_stop)
    summary["summary_paths"] = persist_run_summary(job_dir=job_dir, summary=summary)
    _emit(
        "summary_persisted",
        job_id=resolved_job_id,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        summary_json=str(summary.get("summary_paths", {}).get("json_path") or ""),
        summary_md=str(summary.get("summary_paths", {}).get("markdown_path") or ""),
    )
    return summary
