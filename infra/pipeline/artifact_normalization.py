from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional

from .core import PipelineError
from .diff_utils import extract_target_files, normalize_diff_text, parse_unified_diff
from .path_policy import validate_candidate_paths


def _normalize_declared_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    if text.startswith("a/"):
        text = text[2:]
    elif text.startswith("b/"):
        text = text[2:]
    if not text:
        raise PipelineError("invalid_patch_artifact", "Declared target file path must be non-empty", exit_code=3)
    return text


def normalize_patch_artifact(
    artifact: Mapping[str, Any],
    *,
    repo_root: Optional[str] = None,
    allowed_paths: Optional[Iterable[str]] = None,
    forbidden_paths: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    if not isinstance(artifact, Mapping):
        raise PipelineError("invalid_patch_artifact", "Patch artifact must be a mapping", exit_code=3)
    artifact_type = str(artifact.get("artifact_type") or "").strip()
    if artifact_type != "unified_diff":
        raise PipelineError(
            "unsupported_patch_artifact",
            "Execution MVP only accepts unified_diff patch artifacts",
            details={"artifact_type": artifact_type},
            exit_code=11,
        )
    producer_raw = artifact.get("producer")
    if not isinstance(producer_raw, Mapping):
        raise PipelineError("invalid_patch_artifact", "patch_artifact.producer must be a mapping", exit_code=3)
    producer = {"provider": str(producer_raw.get("provider") or "").strip(), "model": str(producer_raw.get("model") or "").strip()}
    if not producer["provider"] or not producer["model"]:
        raise PipelineError("invalid_patch_artifact", "patch_artifact.producer.provider and model are required", exit_code=3)

    normalized_content = normalize_diff_text(str(artifact.get("content") or ""))
    sections = parse_unified_diff(normalized_content)
    extracted_targets = [section.target_path for section in sections]

    if repo_root is not None:
        normalized_targets = validate_candidate_paths(
            repo_root,
            extracted_targets,
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
        )
    else:
        normalized_targets = [_normalize_declared_path(path) for path in extracted_targets]

    declared_targets = list(artifact.get("target_files") or [])
    if declared_targets:
        if repo_root is not None:
            normalized_declared = validate_candidate_paths(
                repo_root,
                [_normalize_declared_path(path) for path in declared_targets],
                allowed_paths=allowed_paths,
                forbidden_paths=forbidden_paths,
            )
        else:
            normalized_declared = [_normalize_declared_path(path) for path in declared_targets]
        if normalized_declared != normalized_targets:
            raise PipelineError(
                "patch_artifact_target_mismatch",
                "Patch artifact target_files do not match the paths declared by the diff content",
                details={"declared": normalized_declared, "extracted": normalized_targets},
                exit_code=11,
            )

    return {
        "artifact_type": "unified_diff",
        "producer": producer,
        "target_files": normalized_targets,
        "content": normalized_content,
    }
