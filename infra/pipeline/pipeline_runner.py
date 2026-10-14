from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .artifact_registry import ArtifactRegistry
from .core import (
    PipelineError,
    artifact_metadata,
    emit_pipeline_error,
    ensure_directory,
    load_json,
    load_pipeline_config,
    make_run_id,
    nested_config_to_env,
    resolve_stage_io,
    run_subprocess,
    save_json_atomic,
)
from .execution_trace import persist_execution_trace
from .job_state import JobStateStore
from .retry_scaffolding import persist_execution_attempt_scaffold
from .run_summary import build_run_summary
from .schemas import validate_stage_output
from .semantic import semantic_cli_overrides


@dataclasses.dataclass(frozen=True)
class PipelineStage:
    key: str
    script_path: str

    def command(self, *, python_exe: str, io_input: Path, io_output: Path, log_level: str, repo_root: Path, script_root: Optional[Path] = None) -> List[str]:
        resolved_script_root = Path(script_root or repo_root).resolve()
        return [python_exe, str((resolved_script_root / self.script_path).resolve()), "--input", str(io_input), "--output", str(io_output), "--log-level", log_level, "--repo-root", str(repo_root)]


DEFAULT_PIPELINE: List[PipelineStage] = [
    PipelineStage("script_1_job_intake", "infra/pipeline/stages/job_intake.py"),
    PipelineStage("script_2_task_classifier", "infra/pipeline/stages/task_classifier.py"),
    PipelineStage("script_3_search_plan", "infra/pipeline/stages/search_plan.py"),
    PipelineStage("script_4_repo_retriever", "infra/pipeline/stages/repo_retriever.py"),
    PipelineStage("script_5_context_ranker", "infra/pipeline/stages/context_ranker.py"),
    PipelineStage("script_6_context_bundle", "infra/pipeline/stages/context_bundle.py"),
    PipelineStage("script_7_patch_plan", "infra/pipeline/stages/patch_plan.py"),
    PipelineStage("script_8_patch_generate", "infra/pipeline/stages/patch_generate.py"),
    PipelineStage("script_9_artifact_validate", "infra/pipeline/stages/artifact_validate.py"),
    PipelineStage("script_10_patch_apply", "infra/pipeline/stages/patch_apply.py"),
    PipelineStage("script_11_verify_static", "infra/pipeline/stages/static_verify.py"),
    PipelineStage("script_12_verify_runtime", "infra/pipeline/stages/runtime_verify.py"),
    PipelineStage("script_13_contract_verify", "infra/pipeline/stages/contract_verify.py"),
    PipelineStage("script_14_repair_loop", "infra/pipeline/stages/repair_loop.py"),
    PipelineStage("script_15_finalize_job", "infra/pipeline/stages/finalize_job.py"),
    PipelineStage("script_16_job_report", "infra/pipeline/stages/job_report.py"),
]


class PipelineRunner:
    def __init__(
        self,
        *,
        repo_root: str | os.PathLike[str],
        stages: Optional[Iterable[PipelineStage]] = None,
        config_overrides: Optional[Mapping[str, Any]] = None,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.script_root = Path(__file__).resolve().parents[2]
        self.config_overrides = dict(config_overrides or {})
        self.config = load_pipeline_config(repo_root=self.repo_root, cli_overrides=self.config_overrides)
        self.stages = list(stages or DEFAULT_PIPELINE)
        self.python_exe = str(self.config.get("tools", {}).get("python_path", sys.executable))
        self.stage_keys = [stage.key for stage in self.stages]
        self.env_overrides = nested_config_to_env(self.config_overrides) if self.config_overrides else {}
        self.progress_callback = progress_callback
        self.cancel_requested: Optional[Callable[[], bool]] = None
        self.process_handle_callback: Optional[Callable[[Any], None]] = None

    def _emit_progress(self, event: str, **payload: Any) -> None:
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(event, dict(payload))
        except Exception:
            pass

    def _job_dir(self, job_id: str) -> Path:
        jobs_root = Path(self.config["_derived"]["jobs_root"])
        job_dir = jobs_root / str(job_id)
        ensure_directory(job_dir / "input")
        ensure_directory(job_dir / "runs")
        return job_dir

    def _stage_index(self, stage_key: str) -> int:
        for idx, stage in enumerate(self.stages):
            if stage.key == stage_key:
                return idx
        raise PipelineError("unknown_stage", f"Unknown stage: {stage_key}", details={"stage_key": stage_key}, exit_code=2)

    def _prepare_state(self, *, job_dir: Path, job_id: str, branch_id: str, run_id: Optional[str], resume: bool, start_stage: str) -> Dict[str, str]:
        store = JobStateStore(job_dir)
        if resume:
            state = store.load()
            return {"run_id": str(state["active_head_run_id"]), "branch_id": str(state["active_branch_id"])}
        state = store.initialize_job(job_id=job_id, branch_id=branch_id, run_id=str(run_id or make_run_id()), schema_version=str(self.config.get("pipeline", {}).get("schema_version")), contract_version=str(self.config.get("pipeline", {}).get("contract_version")), stage_sequence=self.stage_keys)
        if start_stage and start_stage != self.stage_keys[0]:
            state = store.create_child_run(branch_id=branch_id, run_id=make_run_id(), parent_run_id=str(state["active_head_run_id"]), reentry_stage=start_stage, retry_mode="manual_reentry", retry_reason=f"operator selected start stage {start_stage}")
        return {"run_id": str(state["active_head_run_id"]), "branch_id": str(state["active_branch_id"])}

    def _persist_execution_log(self, io_log: Path, *, stage: str, cmd: List[str], result: Dict[str, Any]) -> None:
        save_json_atomic(io_log, {"stage": stage, "command": cmd, "result": result})

    def _stage15_authoritative_status(self, *, job_dir: Path, run_id: str) -> Optional[Dict[str, str]]:
        finalization_path = job_dir / "runs" / str(run_id) / "15_finalization.json"
        if not finalization_path.exists():
            return None
        try:
            payload = load_json(finalization_path)
        except Exception:
            return None
        final_status = str(payload.get("result", {}).get("finalization", {}).get("final_status") or "").strip().lower()
        status_map = {
            "accepted": "FINALIZED",
            "needs_review": "WAITING_REVIEW",
            "aborted": "ABORTED",
        }
        mapped_status = status_map.get(final_status)
        if not mapped_status:
            return None
        return {
            "final_status": final_status,
            "runner_status": mapped_status,
            "current_stage": "script_15_finalize_job",
        }

    def _preserve_stage15_truth(
        self,
        *,
        stage: PipelineStage,
        reason: str,
        job_dir: Path,
        job_id: str,
        run_id: str,
        branch_id: str,
        state_store: JobStateStore,
        registry: ArtifactRegistry,
        stage_results: List[Dict[str, Any]],
        stage_log_artifact_id: Optional[str] = None,
        stage_log_path: Optional[Path] = None,
    ) -> Optional[Dict[str, Any]]:
        if stage.key != "script_16_job_report":
            return None
        authoritative = self._stage15_authoritative_status(job_dir=job_dir, run_id=run_id)
        if authoritative is None:
            return None
        state_store.set_run_status(
            run_id,
            authoritative["runner_status"],
            current_stage=authoritative["current_stage"],
        )
        preserved_results = list(stage_results)
        if stage_log_artifact_id and stage_log_path is not None:
            preserved_results.append(
                {
                    "stage": stage.key,
                    "execution_log_artifact_id": stage_log_artifact_id,
                    "file_path": str(stage_log_path),
                    "preserved_stage15_truth": True,
                    "stage16_failure_reason": reason,
                }
            )
        trace_info = persist_execution_trace(
            job_dir=job_dir,
            run_id=run_id,
            config=self.config,
            status=authoritative["runner_status"],
        )
        trace_record = registry.register(
            stage="pipeline_execution",
            run_id=run_id,
            branch_id=branch_id,
            file_path=trace_info["file_path"],
            metadata=artifact_metadata(self.config, stage_key="pipeline_execution"),
            role="execution_trace",
        )
        preserved_results.append(
            {
                "stage": "pipeline_execution",
                "artifact_id": trace_record["artifact_id"],
                "file_path": trace_record["file_path"],
                "preserved_stage15_truth": True,
                "stage16_failure_reason": reason,
            }
        )
        self._emit_progress(
            "pipeline_complete",
            job_id=job_id,
            run_id=run_id,
            branch_id=branch_id,
            status=authoritative["runner_status"],
            current_stage=authoritative["current_stage"],
            next_stage=None,
            preserved_stage15_truth=True,
            stage16_failure_reason=reason,
        )
        return self._finalize_summary(
            job_dir=job_dir,
            job_id=job_id,
            run_id=run_id,
            branch_id=branch_id,
            status=authoritative["runner_status"],
            current_stage=authoritative["current_stage"],
            next_stage=None,
            stage_results=preserved_results,
            registry=registry,
        )

    def _finalize_summary(self, *, job_dir: Path, job_id: str, run_id: str, branch_id: str, status: str, current_stage: str, next_stage: Optional[str], stage_results: List[Dict[str, Any]], registry: ArtifactRegistry) -> Dict[str, Any]:
        summary = {"job_id": job_id, "run_id": run_id, "branch_id": branch_id, "status": status, "current_stage": current_stage, "next_stage": next_stage, "job_dir": str(job_dir), "artifacts": stage_results, "effective_config": {"semantic": dict(self.config.get("semantic") or {})}}
        try:
            run_summary = build_run_summary(job_dir=job_dir, run_id=run_id, config=self.config, status=status)
            summary["selected_files"] = list(run_summary.get("selected_files") or [])
            summary["elapsed_time_ms"] = int(run_summary.get("elapsed_time_ms") or 0)
            summary["stage_timings"] = list(run_summary.get("stage_timings") or [])
            summary["retrieval_provenance"] = dict(run_summary.get("retrieval_provenance") or {})
        except Exception:
            pass
        summary["artifact_registry_entries"] = registry.lookup_by_run_id(run_id)
        save_json_atomic(job_dir / "pipeline_run_summary.json", summary)
        return summary

    def run(self, *, job_id: str, run_id: Optional[str] = None, branch_id: str = "BRANCH-main", resume: bool = False, start_stage: str = "", stop_after_stage: str = "") -> Dict[str, Any]:
        if start_stage:
            self._stage_index(start_stage)
        if stop_after_stage:
            self._stage_index(stop_after_stage)
        job_dir = self._job_dir(job_id)
        state_store = JobStateStore(job_dir)
        registry = ArtifactRegistry(job_dir, schema_version=str(self.config.get("pipeline", {}).get("schema_version")), contract_version=str(self.config.get("pipeline", {}).get("contract_version")))
        with state_store.lock():
            prepared = self._prepare_state(job_dir=job_dir, job_id=job_id, branch_id=branch_id, run_id=run_id, resume=resume, start_stage=start_stage)
            run_id = prepared["run_id"]
            branch_id = prepared["branch_id"]
            state_store.validate_registry_alignment(registry.registry_by_artifact_id())
            next_stage_key = start_stage or state_store.next_stage()
            if next_stage_key is None:
                active_run = state_store.get_active_run()
                trace_info = persist_execution_trace(job_dir=job_dir, run_id=run_id, config=self.config, status=str(active_run.get("status") or "UNKNOWN"))
                registry.register(stage="pipeline_execution", run_id=run_id, branch_id=branch_id, file_path=trace_info["file_path"], metadata=artifact_metadata(self.config, stage_key="pipeline_execution"), role="execution_trace")
                return self._finalize_summary(job_dir=job_dir, job_id=job_id, run_id=run_id, branch_id=branch_id, status=str(active_run.get("status") or "UNKNOWN"), current_stage=str(active_run.get("current_stage") or ""), next_stage=None, stage_results=[], registry=registry)
            start_idx = self._stage_index(next_stage_key)
            stop_idx = self._stage_index(stop_after_stage) if stop_after_stage else len(self.stages) - 1
            if stop_idx < start_idx:
                raise PipelineError("invalid_stage_range", "stop_after_stage cannot be before start stage", details={"start_stage": next_stage_key, "stop_after_stage": stop_after_stage}, exit_code=2)
            log_level = str(self.config.get("pipeline", {}).get("default_log_level", "INFO"))
            timeout_seconds = int(self.config.get("timeouts", {}).get("subprocess_default_seconds", 180))
            stage_results: List[Dict[str, Any]] = []
            stage_slice = self.stages[start_idx : stop_idx + 1]
            total_stages = len(stage_slice)
            self._emit_progress(
                "pipeline_started",
                job_id=job_id,
                run_id=run_id,
                branch_id=branch_id,
                total_stages=total_stages,
                start_stage=next_stage_key,
                stop_after_stage=stop_after_stage or stage_slice[-1].key,
            )
            for stage_index, stage in enumerate(stage_slice, start=1):
                active_run = state_store.get_active_run()
                if stage.key in active_run.get("completed_stages", []):
                    continue
                io = resolve_stage_io(job_dir, run_id, stage.key)
                io.output_path.parent.mkdir(parents=True, exist_ok=True)
                io.log_path.parent.mkdir(parents=True, exist_ok=True)
                if not io.input_path.exists():
                    self._emit_progress(
                        "stage_failed",
                        job_id=job_id,
                        run_id=run_id,
                        stage=stage.key,
                        stage_index=stage_index,
                        total_stages=total_stages,
                        reason="stage_input_missing",
                        input_path=str(io.input_path),
                    )
                    preserved = self._preserve_stage15_truth(
                        stage=stage,
                        reason="stage_input_missing",
                        job_dir=job_dir,
                        job_id=job_id,
                        run_id=run_id,
                        branch_id=branch_id,
                        state_store=state_store,
                        registry=registry,
                        stage_results=stage_results,
                    )
                    if preserved is not None:
                        return preserved
                    state_store.set_run_status(run_id, "ABORTED", current_stage=stage.key, failed_stage=stage.key)
                    raise PipelineError("stage_input_missing", f"Missing input artifact for {stage.key}", details={"stage": stage.key, "input": str(io.input_path)}, exit_code=3)
                state_store.set_run_status(run_id, "RUNNING", current_stage=stage.key)
                cmd = stage.command(
                    python_exe=self.python_exe,
                    io_input=io.input_path,
                    io_output=io.output_path,
                    log_level=log_level,
                    repo_root=self.repo_root,
                    script_root=self.script_root,
                )
                env = os.environ.copy()
                env.update(self.env_overrides)
                env["PIPELINE_JOB_ID"] = str(job_id)
                env["PIPELINE_RUN_ID"] = str(run_id)
                env["PIPELINE_BRANCH_ID"] = str(branch_id)
                progress_path = io.log_path.parent / f"{stage.key}.progress.json"
                env["PIPELINE_PROGRESS_PATH"] = str(progress_path)
                self._emit_progress(
                    "stage_start",
                    job_id=job_id,
                    run_id=run_id,
                    stage=stage.key,
                    stage_index=stage_index,
                    total_stages=total_stages,
                    input_path=str(io.input_path),
                    output_path=str(io.output_path),
                )
                try:
                    last_heartbeat_at = 0.0
                    heartbeat_enabled = stage.key in {"script_4_repo_retriever", "script_10_patch_apply", "script_11_verify_static", "script_12_verify_runtime"}

                    def _heartbeat(progress_payload: Dict[str, Any]) -> None:
                        nonlocal last_heartbeat_at
                        if not heartbeat_enabled:
                            return
                        now = time.monotonic()
                        if now - last_heartbeat_at < 1.0:
                            return
                        last_heartbeat_at = now
                        detail = ""
                        if progress_path.exists():
                            try:
                                detail_payload = json.loads(progress_path.read_text(encoding="utf-8"))
                                detail = str(detail_payload.get("status_line") or "").strip()
                            except Exception:
                                detail = ""
                        self._emit_progress(
                            "stage_heartbeat",
                            job_id=job_id,
                            run_id=run_id,
                            stage=stage.key,
                            stage_index=stage_index,
                            total_stages=total_stages,
                            elapsed_ms=int(progress_payload.get("elapsed_ms") or 0),
                            detail=detail,
                        )

                    result = run_subprocess(
                        cmd,
                        cwd=self.repo_root,
                        env=env,
                        timeout_seconds=timeout_seconds,
                        progress_callback=_heartbeat,
                        process_handle_callback=self.process_handle_callback,
                    )
                except PipelineError as exc:
                    self._persist_execution_log(io.log_path, stage=stage.key, cmd=cmd, result=exc.details)
                    log_record = registry.register(stage=stage.key, run_id=run_id, branch_id=branch_id, file_path=io.log_path, metadata=artifact_metadata(self.config, stage_key=stage.key), role="execution_log")
                    self._emit_progress(
                        "stage_failed",
                        job_id=job_id,
                        run_id=run_id,
                        stage=stage.key,
                        stage_index=stage_index,
                        total_stages=total_stages,
                        reason=str(exc.code),
                        exit_code=int(exc.exit_code),
                        log_path=str(io.log_path),
                    )
                    preserved = self._preserve_stage15_truth(
                        stage=stage,
                        reason=str(exc.code),
                        job_dir=job_dir,
                        job_id=job_id,
                        run_id=run_id,
                        branch_id=branch_id,
                        state_store=state_store,
                        registry=registry,
                        stage_results=stage_results,
                        stage_log_artifact_id=log_record["artifact_id"],
                        stage_log_path=io.log_path,
                    )
                    if preserved is not None:
                        return preserved
                    state_store.set_run_status(run_id, "ABORTED", current_stage=stage.key, failed_stage=stage.key)
                    state_store.record_failure_classification(run_id=run_id, primary_hypothesis="stage_execution_error", competing_hypotheses=["timeout", "invalid_input_artifact", "tool_failure"], confidence=0.75, rationale_trace=[f"stage={stage.key}", f"error_code={exc.code}"])
                    raise PipelineError(exc.code, exc.message, details={**dict(exc.details), "execution_log_artifact_id": log_record["artifact_id"]}, exit_code=exc.exit_code) from exc
                self._persist_execution_log(io.log_path, stage=stage.key, cmd=cmd, result=result.to_dict())
                if progress_path.exists():
                    try:
                        progress_path.unlink()
                    except OSError:
                        pass
                log_record = registry.register(stage=stage.key, run_id=run_id, branch_id=branch_id, file_path=io.log_path, metadata=artifact_metadata(self.config, stage_key=stage.key), role="execution_log")
                if result.returncode != 0:
                    self._emit_progress(
                        "stage_failed",
                        job_id=job_id,
                        run_id=run_id,
                        stage=stage.key,
                        stage_index=stage_index,
                        total_stages=total_stages,
                        reason="stage_failed",
                        returncode=int(result.returncode),
                        log_path=str(io.log_path),
                    )
                    preserved = self._preserve_stage15_truth(
                        stage=stage,
                        reason="stage_failed",
                        job_dir=job_dir,
                        job_id=job_id,
                        run_id=run_id,
                        branch_id=branch_id,
                        state_store=state_store,
                        registry=registry,
                        stage_results=stage_results,
                        stage_log_artifact_id=log_record["artifact_id"],
                        stage_log_path=io.log_path,
                    )
                    if preserved is not None:
                        return preserved
                    state_store.set_run_status(run_id, "ABORTED", current_stage=stage.key, failed_stage=stage.key)
                    state_store.record_failure_classification(run_id=run_id, primary_hypothesis="stage_nonzero_exit", competing_hypotheses=["tool_failure", "validation_failure"], confidence=0.8, rationale_trace=[f"stage={stage.key}", f"returncode={result.returncode}"])
                    raise PipelineError("stage_failed", f"Stage failed: {stage.key}", details={"stage": stage.key, "run_id": run_id, "branch_id": branch_id, "returncode": result.returncode, "execution_log_artifact_id": log_record["artifact_id"]}, exit_code=max(1, int(result.returncode)))
                if not io.output_path.exists():
                    self._emit_progress(
                        "stage_failed",
                        job_id=job_id,
                        run_id=run_id,
                        stage=stage.key,
                        stage_index=stage_index,
                        total_stages=total_stages,
                        reason="stage_output_missing",
                        output_path=str(io.output_path),
                    )
                    preserved = self._preserve_stage15_truth(
                        stage=stage,
                        reason="stage_output_missing",
                        job_dir=job_dir,
                        job_id=job_id,
                        run_id=run_id,
                        branch_id=branch_id,
                        state_store=state_store,
                        registry=registry,
                        stage_results=stage_results,
                        stage_log_artifact_id=log_record["artifact_id"],
                        stage_log_path=io.log_path,
                    )
                    if preserved is not None:
                        return preserved
                    state_store.set_run_status(run_id, "ABORTED", current_stage=stage.key, failed_stage=stage.key)
                    state_store.record_failure_classification(run_id=run_id, primary_hypothesis="missing_stage_output", competing_hypotheses=["silent_stage_failure", "output_path_misconfiguration"], confidence=0.9, rationale_trace=[f"stage={stage.key}"])
                    raise PipelineError("stage_output_missing", f"Stage did not produce expected artifact: {stage.key}", details={"stage": stage.key, "output": str(io.output_path), "execution_log_artifact_id": log_record["artifact_id"]}, exit_code=3)
                try:
                    payload = load_json(io.output_path)
                    validate_stage_output(payload, expected_stage=stage.key, expected_job_id=str(job_id), expected_run_id=str(run_id))
                except Exception as exc:
                    self._emit_progress(
                        "stage_failed",
                        job_id=job_id,
                        run_id=run_id,
                        stage=stage.key,
                        stage_index=stage_index,
                        total_stages=total_stages,
                        reason="stage_output_invalid",
                        output_path=str(io.output_path),
                    )
                    preserved = self._preserve_stage15_truth(
                        stage=stage,
                        reason="stage_output_invalid",
                        job_dir=job_dir,
                        job_id=job_id,
                        run_id=run_id,
                        branch_id=branch_id,
                        state_store=state_store,
                        registry=registry,
                        stage_results=stage_results,
                        stage_log_artifact_id=log_record["artifact_id"],
                        stage_log_path=io.log_path,
                    )
                    if preserved is not None:
                        return preserved
                    state_store.set_run_status(run_id, "ABORTED", current_stage=stage.key, failed_stage=stage.key)
                    state_store.record_failure_classification(run_id=run_id, primary_hypothesis="invalid_stage_output", competing_hypotheses=["schema_validation_failed", "malformed_output_json"], confidence=0.85, rationale_trace=[f"stage={stage.key}"])
                    raise PipelineError(
                        "stage_output_invalid",
                        f"Stage produced invalid output: {stage.key}",
                        details={
                            "stage": stage.key,
                            "output": str(io.output_path),
                            "execution_log_artifact_id": log_record["artifact_id"],
                            "error": str(exc),
                        },
                        exit_code=3,
                    ) from exc
                record = registry.register(stage=stage.key, run_id=run_id, branch_id=branch_id, file_path=io.output_path, metadata=artifact_metadata(self.config, stage_key=stage.key), role="stage_output")
                extra_artifacts: Dict[str, Any] = {}
                if stage.key == "script_7_patch_plan":
                    execution_contract_path = io.output_path.parent / "07_execution_contract.json"
                    if execution_contract_path.exists():
                        extra_record = registry.register(stage=stage.key, run_id=run_id, branch_id=branch_id, file_path=execution_contract_path, metadata=artifact_metadata(self.config, stage_key=stage.key), role="execution_contract")
                        extra_artifacts = {
                            "execution_contract_artifact_id": extra_record["artifact_id"],
                            "execution_contract_path": extra_record["file_path"],
                        }
                        scaffold_info = persist_execution_attempt_scaffold(job_dir=job_dir, run_id=run_id, config=self.config)
                        scaffold_record = registry.register(stage=stage.key, run_id=run_id, branch_id=branch_id, file_path=scaffold_info["file_path"], metadata=artifact_metadata(self.config, stage_key=stage.key), role="execution_attempt_scaffold")
                        extra_artifacts.update({
                            "execution_attempt_scaffold_artifact_id": scaffold_record["artifact_id"],
                            "execution_attempt_scaffold_path": scaffold_record["file_path"],
                        })
                state_store.mark_stage_complete(run_id=run_id, stage_key=stage.key, artifact_id=record["artifact_id"], execution_log_id=log_record["artifact_id"])
                stage_results.append({"stage": stage.key, "artifact_id": record["artifact_id"], "execution_log_artifact_id": log_record["artifact_id"], "file_path": record["file_path"], "duration_ms": int(payload.get("duration_ms") or 0), "execution_duration_ms": int(result.duration_ms), **extra_artifacts})
                self._emit_progress(
                    "stage_complete",
                    job_id=job_id,
                    run_id=run_id,
                    stage=stage.key,
                    stage_index=stage_index,
                    total_stages=total_stages,
                    execution_duration_ms=int(result.duration_ms),
                    stage_duration_ms=int(payload.get("duration_ms") or 0),
                    output_path=str(io.output_path),
                )
            state_store.validate_registry_alignment(registry.registry_by_artifact_id())
            next_remaining = state_store.next_stage()
            active_run = state_store.get_active_run()
            if next_remaining is None:
                final_stage = stage_results[-1]["stage"] if stage_results else str(active_run.get("current_stage") or self.stage_keys[-1])
                completed_stages = set(active_run.get("completed_stages", []))
                if "script_15_finalize_job" in completed_stages:
                    finalization_path = job_dir / "runs" / str(run_id) / "15_finalization.json"
                    finalization_payload = load_json(finalization_path) if finalization_path.exists() else {}
                    final_status = str(finalization_payload.get("result", {}).get("finalization", {}).get("final_status") or "").strip().lower()
                    if final_status == "accepted":
                        state_store.set_run_status(run_id, "FINALIZED", current_stage=str(final_stage))
                        status = "FINALIZED"
                    elif final_status == "needs_review":
                        state_store.set_run_status(run_id, "WAITING_REVIEW", current_stage=str(final_stage))
                        status = "WAITING_REVIEW"
                    else:
                        state_store.set_run_status(run_id, "ABORTED", current_stage=str(final_stage))
                        status = "ABORTED"
                elif "script_13_contract_verify" in completed_stages:
                    contract_path = job_dir / "runs" / str(run_id) / "13_contract_verification.json"
                    contract_payload = load_json(contract_path) if contract_path.exists() else {}
                    contract_status = str(contract_payload.get("result", {}).get("contract_verification", {}).get("contract_verification_status") or "").strip().lower()
                    if contract_status == "satisfied":
                        state_store.set_run_status(run_id, "FINALIZED", current_stage=str(final_stage))
                        status = "FINALIZED"
                    elif contract_status == "skipped":
                        state_store.set_run_status(run_id, "WAITING_REVIEW", current_stage=str(final_stage))
                        status = "WAITING_REVIEW"
                    else:
                        state_store.set_run_status(run_id, "WAITING_RETRY_DECISION", current_stage=str(final_stage))
                        status = "WAITING_RETRY_DECISION"
                elif "script_10_patch_apply" in completed_stages:
                    state_store.set_run_status(run_id, "FINALIZED", current_stage=str(final_stage))
                    status = "FINALIZED"
                elif "script_7_patch_plan" in completed_stages:
                    state_store.set_run_status(run_id, "WAITING_REVIEW", current_stage=str(final_stage))
                    status = "WAITING_REVIEW"
                else:
                    state_store.set_run_status(run_id, "FINALIZED", current_stage=str(final_stage))
                    status = "FINALIZED"
            else:
                final_stage = stage_results[-1]["stage"] if stage_results else next_stage_key
                status = "RUNNING"
            trace_info = persist_execution_trace(job_dir=job_dir, run_id=run_id, config=self.config, status=status)
            trace_record = registry.register(stage="pipeline_execution", run_id=run_id, branch_id=branch_id, file_path=trace_info["file_path"], metadata=artifact_metadata(self.config, stage_key="pipeline_execution"), role="execution_trace")
            stage_results.append({"stage": "pipeline_execution", "artifact_id": trace_record["artifact_id"], "file_path": trace_record["file_path"]})
            self._emit_progress(
                "pipeline_complete",
                job_id=job_id,
                run_id=run_id,
                branch_id=branch_id,
                status=status,
                current_stage=str(final_stage),
                next_stage=next_remaining,
            )
            return self._finalize_summary(job_dir=job_dir, job_id=job_id, run_id=run_id, branch_id=branch_id, status=status, current_stage=str(final_stage), next_stage=next_remaining, stage_results=stage_results, registry=registry)


def run_pipeline(repo_root: str, job_id: str, run_id: Optional[str] = None, *, branch_id: str = "BRANCH-main", resume: bool = False, start_stage: str = "", stop_after_stage: str = "", config_overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    return PipelineRunner(repo_root=repo_root, config_overrides=config_overrides).run(job_id=job_id, run_id=run_id, branch_id=branch_id, resume=resume, start_stage=start_stage, stop_after_stage=stop_after_stage)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic MVP-2 pipeline runner")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--branch-id", default="BRANCH-main")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--start-stage", default="")
    parser.add_argument("--stop-after-stage", default="")
    parser.add_argument("--semantic-provider", default="")
    parser.add_argument("--semantic-model", default="")
    parser.add_argument("--semantic-mode", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    overrides = semantic_cli_overrides(provider=args.semantic_provider or None, model=args.semantic_model or None, mode=args.semantic_mode or None)
    try:
        result = run_pipeline(args.repo_root, args.job_id, args.run_id or None, branch_id=args.branch_id, resume=args.resume, start_stage=args.start_stage, stop_after_stage=args.stop_after_stage, config_overrides=overrides)
    except Exception as exc:
        return emit_pipeline_error(exc)
    if args.json:
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(result["status"] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
