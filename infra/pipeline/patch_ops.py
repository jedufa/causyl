from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .core import PipelineError, run_subprocess
from .diff_utils import count_changed_lines, extract_target_files, parse_unified_diff
from .path_policy import validate_candidate_paths


def file_digest(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_expected_target_files(target_files: Sequence[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for value in target_files:
        path = str(value or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        normalized.append(path)
    return normalized


def preflight_validated_patch(
    *,
    repo_root: str,
    patch_text: str,
    expected_target_files: Sequence[str],
) -> List[str]:
    parse_unified_diff(patch_text)
    parsed_targets = extract_target_files(patch_text)
    normalized_expected = normalize_expected_target_files(expected_target_files)
    normalized_targets = validate_candidate_paths(
        repo_root,
        parsed_targets,
        allowed_paths=normalized_expected,
    )
    if normalized_expected and sorted(normalized_targets) != sorted(normalized_expected):
        raise PipelineError(
            "validated_artifact_target_mismatch",
            "validated_artifact.target_files do not match the patch target set",
            details={"expected_target_files": normalized_expected, "patch_target_files": normalized_targets},
            exit_code=11,
        )
    return normalized_targets


def workspace_changed_files(
    *,
    workspace_path: str,
    git_path: str,
    timeout_seconds: int,
    fallback_target_files: Sequence[str],
    before_digests: Mapping[str, Optional[str]],
    run_subprocess_fn: Callable[..., Any] = run_subprocess,
) -> List[str]:
    diff_result = run_subprocess_fn(
        [git_path, "diff", "--name-only", "--relative"],
        cwd=workspace_path,
        timeout_seconds=timeout_seconds,
    )
    if diff_result.returncode == 0:
        changed = [line.strip() for line in diff_result.stdout.splitlines() if line.strip()]
        if changed:
            return changed
    changed_files: List[str] = []
    for rel_path in fallback_target_files:
        after_digest = file_digest(Path(workspace_path) / rel_path)
        if before_digests.get(rel_path) != after_digest:
            changed_files.append(rel_path)
    return changed_files


def classify_apply_failure(
    *,
    logs: str,
    patch_text: str,
    changed_files: Sequence[str],
    repo_root: str,
    expected_target_files: Sequence[str],
) -> Dict[str, Any]:
    lowered = str(logs or "").lower()
    rejected_markers = [
        "corrupt patch",
        "unrecognized input",
        "malformed patch",
        "patch fragment without header",
        "bad git-diff",
        "invalid path",
        "unsafe path",
        "outside repository",
        "beyond a symbolic link",
        "cannot apply binary patch",
    ]
    conflict_markers = [
        "patch does not apply",
        "patch failed",
        "failed to apply",
        "while searching for",
        "already exists in working directory",
        "does not match",
        "does not exist in index",
        "removal patch leaves file contents",
        "no such file or directory",
    ]

    try:
        preflight_validated_patch(
            repo_root=repo_root,
            patch_text=patch_text,
            expected_target_files=expected_target_files,
        )
    except PipelineError as exc:
        return {
            "apply_status": "rejected",
            "failure_reason_code": str(exc.code),
            "failure_detail": str(exc.message),
        }

    if any(marker in lowered for marker in rejected_markers):
        return {
            "apply_status": "rejected",
            "failure_reason_code": "git_apply_rejected",
            "failure_detail": "git apply rejected the patch as malformed or unsafe",
        }
    if changed_files:
        return {
            "apply_status": "conflict",
            "failure_reason_code": "partial_apply_detected",
            "failure_detail": "git apply returned non-zero after mutating workspace files",
        }
    if any(marker in lowered for marker in conflict_markers):
        return {
            "apply_status": "conflict",
            "failure_reason_code": "git_apply_conflict",
            "failure_detail": "git apply failed because the patch did not match workspace state",
        }
    return {
        "apply_status": "conflict",
        "failure_reason_code": "git_apply_failed_unknown",
        "failure_detail": "git apply failed without a recognized malformed-artifact marker",
    }


def inspect_workspace_patch_targets(
    *,
    workspace_path: str,
    patch_text: str,
) -> Dict[str, Any]:
    sections = parse_unified_diff(patch_text)
    missing_existing_targets: List[str] = []
    for section in sections:
        target = str(section.target_path)
        if section.is_new_file:
            continue
        if not (Path(workspace_path) / target).exists():
            missing_existing_targets.append(target)
    return {
        "missing_existing_targets": missing_existing_targets,
        "declared_targets": [str(section.target_path) for section in sections],
    }


def apply_unified_diff_to_workspace(
    *,
    repo_root: str,
    workspace_path: str,
    patch_text: str,
    target_files: Sequence[str],
    git_path: str,
    timeout_seconds: int,
    git_apply_args: Optional[Sequence[str]] = None,
    run_subprocess_fn: Callable[..., Any] = run_subprocess,
) -> Dict[str, Any]:
    workspace_check = inspect_workspace_patch_targets(
        workspace_path=workspace_path,
        patch_text=patch_text,
    )
    missing_existing_targets = list(workspace_check.get("missing_existing_targets") or [])
    if missing_existing_targets:
        return {
            "apply_status": "rejected",
            "changed_files": [],
            "workspace_path": str(workspace_path),
            "logs": "",
            "patch_stats": {"files_changed": 0, "insertions": 0, "deletions": 0},
            "diagnostics": {
                "failure_reason_code": "workspace_missing_patch_targets",
                "failure_stage": "script_10_patch_apply",
                "failure_detail": "isolated workspace is missing one or more existing patch target files",
                "missing_existing_targets": missing_existing_targets,
                "declared_targets": list(workspace_check.get("declared_targets") or []),
                "log_excerpt": ", ".join(missing_existing_targets)[:240],
            },
        }
    before = {rel_path: file_digest(Path(workspace_path) / rel_path) for rel_path in target_files}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".diff", delete=False) as tmp:
        tmp.write(patch_text)
        tmp.flush()
        patch_path = Path(tmp.name)
    try:
        apply_result = run_subprocess_fn(
            [git_path, "apply", *(list(git_apply_args or [])), str(patch_path)],
            cwd=workspace_path,
            timeout_seconds=timeout_seconds,
        )
    finally:
        patch_path.unlink(missing_ok=True)

    logs = (apply_result.stdout or "") + (("\n" if apply_result.stdout and apply_result.stderr else "") + (apply_result.stderr or "") if apply_result.stderr else "")
    changed_files = workspace_changed_files(
        workspace_path=workspace_path,
        git_path=git_path,
        timeout_seconds=timeout_seconds,
        fallback_target_files=list(target_files),
        before_digests=before,
        run_subprocess_fn=run_subprocess_fn,
    )
    if apply_result.returncode == 0:
        line_stats = count_changed_lines(patch_text)
        return {
            "apply_status": "applied_cleanly",
            "changed_files": changed_files,
            "workspace_path": str(workspace_path),
            "logs": logs.strip(),
            "patch_stats": {
                "files_changed": len(changed_files),
                "insertions": int(line_stats.get("insertions") or 0),
                "deletions": int(line_stats.get("deletions") or 0),
            },
        }

    failure = classify_apply_failure(
        logs=logs,
        patch_text=patch_text,
        changed_files=changed_files,
        repo_root=repo_root,
        expected_target_files=target_files,
    )
    return {
        "apply_status": str(failure.get("apply_status") or "conflict"),
        "changed_files": changed_files,
        "workspace_path": str(workspace_path),
        "logs": logs.strip(),
        "patch_stats": {"files_changed": len(changed_files), "insertions": 0, "deletions": 0},
        "diagnostics": {
            "failure_reason_code": str(failure.get("failure_reason_code") or "git_apply_failed"),
            "failure_stage": "script_10_patch_apply",
            "failure_detail": str(failure.get("failure_detail") or "git apply failed"),
            "subprocess_returncode": int(apply_result.returncode),
            "log_excerpt": logs.strip()[:240],
        },
    }
