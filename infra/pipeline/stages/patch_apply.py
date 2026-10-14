from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import signal
import tempfile
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.artifact_lineage import build_artifact_lineage, resolve_lineage_artifact
from infra.pipeline.core import PipelineError, build_envelope, emit_pipeline_error, load_json, load_pipeline_config, run_subprocess, save_json_atomic, utc_now_iso
from infra.pipeline.patch_ops import apply_unified_diff_to_workspace, preflight_validated_patch
from infra.pipeline.schemas import validate_patch_apply_result, validate_patch_plan_result, validate_stage_input, validate_validated_artifact
from infra.pipeline.workspace import create_isolated_workspace

STAGE_KEY = "script_10_patch_apply"


class _StageProgressReporter:
    def __init__(self) -> None:
        raw_path = str(os.environ.get("PIPELINE_PROGRESS_PATH") or "").strip()
        self.path = Path(raw_path) if raw_path else None
        self._last_text = ""

    def update(self, text: str, **payload: Any) -> None:
        normalized = " ".join(str(text or "").split()).strip()
        if not normalized or self.path is None or normalized == self._last_text:
            return
        self._last_text = normalized
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"status_line": normalized, **payload}, ensure_ascii=False) + "\n", encoding="utf-8")


class _WorkspaceCleanup:
    def __init__(self) -> None:
        self._stage_root: Optional[Path] = None
        self._armed = False
        self._previous_handlers: Dict[int, Any] = {}

    def arm(self, workspace_path: str | Path) -> None:
        repo_path = Path(workspace_path).resolve()
        self._stage_root = repo_path.parent
        self._armed = True
        atexit.register(self.cleanup)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._previous_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._handle_signal)
            except Exception:
                continue

    def disarm(self) -> None:
        if not self._armed:
            return
        self._armed = False
        for sig, handler in list(self._previous_handlers.items()):
            try:
                signal.signal(sig, handler)
            except Exception:
                pass
        self._previous_handlers.clear()

    def cleanup(self) -> None:
        if not self._armed or self._stage_root is None:
            return
        stage_root = self._stage_root
        self._armed = False
        for sig, handler in list(self._previous_handlers.items()):
            try:
                signal.signal(sig, handler)
            except Exception:
                pass
        self._previous_handlers.clear()
        shutil.rmtree(stage_root, ignore_errors=True)

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        self.cleanup()
        raise SystemExit(128 + int(signum))


def _bootstrap_git_metadata_for_workspace(
    *,
    workspace_path: str | Path,
    git_path: str,
    timeout_seconds: int,
    target_files: List[str],
) -> None:
    workspace_root = Path(workspace_path).resolve()
    if (workspace_root / ".git").exists():
        return
    git_meta_dir = workspace_root.parent / ".gitmeta"
    init_result = run_subprocess(
        [git_path, "init", "-q", "--separate-git-dir", str(git_meta_dir)],
        cwd=str(workspace_root),
        timeout_seconds=timeout_seconds,
    )
    if init_result.returncode != 0:
        raise PipelineError(
            "workspace_git_bootstrap_failed",
            "Failed to initialize detached git metadata for patch-apply workspace",
            details=init_result.to_dict(),
            exit_code=max(1, int(init_result.returncode)),
        )
    existing_targets = [path for path in target_files if (workspace_root / path).exists()]
    if not existing_targets:
        return
    add_result = run_subprocess(
        [git_path, "add", "--", *existing_targets],
        cwd=str(workspace_root),
        timeout_seconds=timeout_seconds,
    )
    if add_result.returncode != 0:
        raise PipelineError(
            "workspace_git_stage_failed",
            "Failed to stage target files in detached patch-apply workspace",
            details=add_result.to_dict(),
            exit_code=max(1, int(add_result.returncode)),
        )


def _load_repo_root(run_dir: Path, lineage: Optional[Mapping[str, Any]] = None) -> tuple[str, str]:
    patch_plan_path = resolve_lineage_artifact(
        run_dir=run_dir,
        lineage=lineage,
        key="patch_plan",
        fallback_filename="07_patch_plan.json",
    )
    patch_plan_payload = load_json(patch_plan_path)
    validate_stage_input(patch_plan_payload, ["job_contract", "patch_plan"])
    validate_patch_plan_result(dict(patch_plan_payload.get("result") or {}))
    repo_root = str(patch_plan_payload.get("result", {}).get("job_contract", {}).get("repo_root") or "").strip()
    if not repo_root:
        raise PipelineError("missing_repo_root", "job_contract.repo_root is required for patch apply", exit_code=3)
    return (repo_root, patch_plan_path.name)

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Execution MVP stage 10 patch apply")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    cleanup = _WorkspaceCleanup()
    try:
        started = utc_now_iso()
        progress = _StageProgressReporter()
        progress.update("stage 10 preparing patch apply")
        config = load_pipeline_config(repo_root=args.repo_root, stage_key=STAGE_KEY)
        payload = load_json(args.input)
        raw_result = dict(payload.get("result") or {})
        artifact_lineage = build_artifact_lineage(
            raw_result.get("artifact_lineage"),
            additions={
                "validated_artifact": Path(args.input).name,
                "apply_result": Path(args.output).name,
            },
        )
        if raw_result.get("validation_skipped") is True:
            skip_reason = str(raw_result.get("generation_blocked_reason") or "patch apply skipped because earlier execution stages were review-only")
            result = {
                "apply_result": {
                    "apply_status": "skipped",
                    "changed_files": [],
                    "workspace_path": str(Path(args.repo_root).resolve()),
                    "logs": skip_reason,
                    "patch_stats": {"files_changed": 0, "insertions": 0, "deletions": 0},
                    "diagnostics": {
                        "failure_reason_code": "upstream_generation_failed" if raw_result.get("upstream_generation_failed") is True else "validation_skipped",
                        "failure_stage": "script_9_artifact_validate",
                        "failure_detail": skip_reason,
                        "log_excerpt": skip_reason[:240],
                    },
                },
                "artifact_lineage": artifact_lineage,
            }
            validate_patch_apply_result(result)
            envelope = build_envelope(
                stage=STAGE_KEY,
                job_id=str(payload.get("job_id") or ""),
                run_id=str(payload.get("run_id") or ""),
                input_payload=payload,
                result=result,
                tool_version=str(config.get("pipeline", {}).get("tool_version")),
                started_at_iso=started,
            )
            save_json_atomic(args.output, envelope)
            return 0
        if raw_result.get("validation_failed") is True:
            skip_reason = str(raw_result.get("validation_failed_reason") or "patch apply skipped because artifact validation failed")
            result = {
                "apply_result": {
                    "apply_status": "skipped",
                    "changed_files": [],
                    "workspace_path": str(Path(args.repo_root).resolve()),
                    "logs": skip_reason,
                    "patch_stats": {"files_changed": 0, "insertions": 0, "deletions": 0},
                    "diagnostics": {
                        "failure_reason_code": str(raw_result.get("failure_reason_code") or "validation_failed"),
                        "failure_stage": "script_9_artifact_validate",
                        "failure_detail": skip_reason,
                        "log_excerpt": skip_reason[:240],
                    },
                },
                "artifact_lineage": artifact_lineage,
            }
            validate_patch_apply_result(result)
            envelope = build_envelope(
                stage=STAGE_KEY,
                job_id=str(payload.get("job_id") or ""),
                run_id=str(payload.get("run_id") or ""),
                input_payload=payload,
                result=result,
                tool_version=str(config.get("pipeline", {}).get("tool_version")),
                started_at_iso=started,
            )
            save_json_atomic(args.output, envelope)
            return 0
        env = validate_stage_input(payload, ["validated_artifact"])
        validate_validated_artifact(env.result)
        validated_artifact = dict(env.result.get("validated_artifact") or {})
        run_dir = Path(args.input).resolve().parent
        progress.update("stage 10 loading repo context")
        repo_root, patch_plan_artifact_ref = _load_repo_root(run_dir, lineage=artifact_lineage)
        artifact_lineage = build_artifact_lineage(artifact_lineage, additions={"patch_plan": patch_plan_artifact_ref})
        target_files = [str(path) for path in list(validated_artifact.get("target_files") or [])]
        patch_text = str(validated_artifact.get("normalized_content") or "")
        try:
            preflight_validated_patch(
                repo_root=repo_root,
                patch_text=patch_text,
                expected_target_files=target_files,
            )
        except PipelineError as exc:
            message = f"{exc.code}: {exc.message}"
            result = {
                "apply_result": {
                    "apply_status": "rejected",
                    "changed_files": [],
                    "workspace_path": str(Path(repo_root).resolve()),
                    "logs": message,
                    "patch_stats": {"files_changed": 0, "insertions": 0, "deletions": 0},
                    "diagnostics": {
                        "failure_reason_code": str(exc.code),
                        "failure_stage": "script_10_patch_apply_preflight",
                        "failure_detail": str(exc.message),
                        "log_excerpt": message[:240],
                    },
                },
                "artifact_lineage": artifact_lineage,
            }
            validate_patch_apply_result(result)
            envelope = build_envelope(
                stage=STAGE_KEY,
                job_id=str(env.job_id),
                run_id=str(env.run_id),
                input_payload=payload,
                result=result,
                tool_version=str(config.get("pipeline", {}).get("tool_version")),
                started_at_iso=started,
            )
            save_json_atomic(args.output, envelope)
            return 0
        progress.update("stage 10 creating isolated workspace")
        workspace = create_isolated_workspace(
            repo_root=repo_root,
            workspace_root=str(config.get("_derived", {}).get("workspace_root") or ""),
            job_id=str(env.job_id),
            run_id=str(env.run_id),
            stage_key=STAGE_KEY,
            include_paths=list(validated_artifact.get("target_files") or []),
        )
        cleanup.arm(workspace.workspace_path)
        git_path = str(config.get("tools", {}).get("git_path") or "git")
        timeout_seconds = int(config.get("timeouts", {}).get("subprocess_default_seconds", 180))
        _bootstrap_git_metadata_for_workspace(
            workspace_path=workspace.workspace_path,
            git_path=git_path,
            timeout_seconds=timeout_seconds,
            target_files=target_files,
        )
        progress.update("stage 10 applying patch in workspace")
        result_payload = apply_unified_diff_to_workspace(
            repo_root=repo_root,
            workspace_path=workspace.workspace_path,
            patch_text=patch_text,
            target_files=target_files,
            git_path=git_path,
            timeout_seconds=timeout_seconds,
            git_apply_args=["--verbose"],
            run_subprocess_fn=run_subprocess,
        )

        progress.update(f"stage 10 finalizing apply result ({result_payload.get('apply_status')})")
        result = {"apply_result": result_payload, "artifact_lineage": artifact_lineage}
        validate_patch_apply_result(result)
        envelope = build_envelope(
            stage=STAGE_KEY,
            job_id=str(env.job_id),
            run_id=str(env.run_id),
            input_payload=payload,
            result=result,
            tool_version=str(config.get("pipeline", {}).get("tool_version")),
            started_at_iso=started,
        )
        save_json_atomic(args.output, envelope)
        cleanup.disarm()
        return 0
    except Exception as exc:
        cleanup.cleanup()
        return emit_pipeline_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
