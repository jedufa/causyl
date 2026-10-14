from __future__ import annotations

from typing import Iterable, List, Optional

from .core import enforce_path_policy as _core_enforce_path_policy
from .core import is_path_allowed as _core_is_path_allowed
from .core import normalize_repo_relative_path as _core_normalize_repo_relative_path


def normalize_repo_relative_path(repo_root: str, value: str) -> str:
    """Normalize a candidate path into repo-relative POSIX form.

    The implementation is intentionally delegated to the existing core helper so
    path policy behavior stays consistent across the pipeline.
    """

    return _core_normalize_repo_relative_path(repo_root, value)


def enforce_path_policy(
    repo_root: str,
    candidate_paths: Iterable[str],
    *,
    allowed_paths: Optional[Iterable[str]] = None,
    forbidden_paths: Optional[Iterable[str]] = None,
) -> None:
    _core_enforce_path_policy(
        repo_root,
        candidate_paths,
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
    )


def is_path_allowed(
    repo_root: str,
    candidate_path: str,
    *,
    allowed_paths: Optional[Iterable[str]] = None,
    forbidden_paths: Optional[Iterable[str]] = None,
) -> bool:
    return _core_is_path_allowed(
        repo_root,
        candidate_path,
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
    )


def validate_candidate_paths(
    repo_root: str,
    candidate_paths: Iterable[str],
    *,
    allowed_paths: Optional[Iterable[str]] = None,
    forbidden_paths: Optional[Iterable[str]] = None,
) -> List[str]:
    normalized = [normalize_repo_relative_path(repo_root, path) for path in candidate_paths]
    enforce_path_policy(
        repo_root,
        normalized,
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
    )
    return normalized
