from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Set

from .core import PipelineError


@dataclasses.dataclass(frozen=True)
class WorkspaceInfo:
    source_repo_path: str
    workspace_root: str
    workspace_path: str
    strategy: str


ALWAYS_EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".pipeline_workspaces",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
ALWAYS_EXCLUDED_TOP_LEVEL_PREFIXES = {
    "jobs",
    "runtime",
    "reports",
    "archives",
    ".pipeline_workspaces",
}
ALWAYS_EXCLUDED_GLOBS = {
    "*.zip",
    "*.tar",
    "*.tar.gz",
    "*.tgz",
    "*.tar.bz2",
    "*.tbz2",
    "*.tar.xz",
    "*.txz",
}


def _is_subpath(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except Exception:
        return False


def resolve_safe_workspace_root(repo_root: str | Path, requested_workspace_root: str | Path) -> Path:
    repo = Path(repo_root).resolve()
    requested = Path(requested_workspace_root).resolve()
    if requested == repo or _is_subpath(requested, repo):
        suffix = hashlib.sha256(str(repo).encode("utf-8")).hexdigest()[:8]
        return (repo.parent / f".{repo.name}_pipeline_workspaces_{suffix}").resolve()
    return requested


def _build_copy_ignore(repo_root: Path, excluded_prefixes: Iterable[str]) -> Callable[[str, list[str]], Set[str]]:
    excluded = [str(prefix).strip("/") for prefix in excluded_prefixes if str(prefix).strip("/")]

    def _ignore(src: str, names: list[str]) -> Set[str]:
        ignored: Set[str] = set()
        src_path = Path(src).resolve()
        for name in names:
            candidate = (src_path / name).resolve()
            try:
                rel = candidate.relative_to(repo_root).as_posix()
            except Exception:
                continue
            for prefix in excluded:
                if rel == prefix or rel.startswith(prefix + "/"):
                    ignored.add(name)
                    break
        return ignored

    return _ignore


def _normalize_rel_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").strip("/")


def _should_exclude_path(rel_path: str, *, forced_includes: Set[str]) -> bool:
    normalized = _normalize_rel_path(rel_path)
    if not normalized:
        return False
    if normalized in forced_includes:
        return False
    parts = Path(normalized).parts
    top_level = parts[0] if parts else ""
    if top_level in ALWAYS_EXCLUDED_TOP_LEVEL_PREFIXES:
        return True
    if any(part in ALWAYS_EXCLUDED_DIR_NAMES for part in parts):
        return True
    if any(part.startswith(".causyl_pipeline_workspaces_") for part in parts):
        return True
    basename = Path(normalized).name
    if any(fnmatch.fnmatch(basename, pattern) or fnmatch.fnmatch(normalized, pattern) for pattern in ALWAYS_EXCLUDED_GLOBS):
        return True
    return False


def _git_copy_candidates(repo_root: Path, *, forced_includes: Set[str]) -> Optional[List[str]]:
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return None
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        check=False,
    )
    if proc.returncode != 0:
        return None
    candidates: List[str] = []
    seen: Set[str] = set()
    for raw in proc.stdout.split(b"\x00"):
        if not raw:
            continue
        rel_path = _normalize_rel_path(raw.decode("utf-8", errors="surrogateescape"))
        if not rel_path or rel_path in seen or _should_exclude_path(rel_path, forced_includes=forced_includes):
            continue
        candidates.append(rel_path)
        seen.add(rel_path)
    for rel_path in sorted(forced_includes):
        if rel_path and rel_path not in seen:
            candidates.append(rel_path)
            seen.add(rel_path)
    return candidates


def _walk_copy_candidates(repo_root: Path, *, forced_includes: Set[str]) -> List[str]:
    candidates: List[str] = []
    seen: Set[str] = set()
    for dirpath, dirnames, filenames in os.walk(repo_root):
        src_dir = Path(dirpath)
        try:
            rel_dir = src_dir.relative_to(repo_root).as_posix()
        except Exception:
            rel_dir = ""
        filtered_dirs: List[str] = []
        for dirname in dirnames:
            rel_path = _normalize_rel_path(f"{rel_dir}/{dirname}" if rel_dir else dirname)
            if _should_exclude_path(rel_path, forced_includes=forced_includes):
                continue
            filtered_dirs.append(dirname)
        dirnames[:] = filtered_dirs
        for filename in filenames:
            rel_path = _normalize_rel_path(f"{rel_dir}/{filename}" if rel_dir else filename)
            if not rel_path or rel_path in seen or _should_exclude_path(rel_path, forced_includes=forced_includes):
                continue
            candidates.append(rel_path)
            seen.add(rel_path)
    for rel_path in sorted(forced_includes):
        if rel_path and rel_path not in seen:
            candidates.append(rel_path)
            seen.add(rel_path)
    return candidates


def _copy_rel_paths(*, repo_root: Path, workspace_path: Path, rel_paths: Sequence[str]) -> None:
    for rel_path in rel_paths:
        normalized = _normalize_rel_path(rel_path)
        if not normalized:
            continue
        src = (repo_root / normalized).resolve()
        dst = workspace_path / normalized
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not src.exists() and not src.is_symlink():
            continue
        if src.is_symlink():
            target = os.readlink(src)
            dst.unlink(missing_ok=True)
            os.symlink(target, dst)
            continue
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        shutil.copy2(src, dst)


def create_isolated_workspace(
    *,
    repo_root: str | Path,
    workspace_root: str | Path,
    job_id: str,
    run_id: str,
    stage_key: str,
    include_paths: Optional[Iterable[str]] = None,
) -> WorkspaceInfo:
    source_repo = Path(repo_root).resolve()
    if not source_repo.exists() or not source_repo.is_dir():
        raise PipelineError("repo_access_error", "Source repository root does not exist", details={"repo_root": str(source_repo)}, exit_code=4)

    safe_workspace_root = resolve_safe_workspace_root(source_repo, workspace_root)
    safe_workspace_root.mkdir(parents=True, exist_ok=True)
    workspace_path = (safe_workspace_root / str(job_id) / str(run_id) / str(stage_key) / "repo").resolve()
    if workspace_path.exists():
        raise PipelineError(
            "workspace_exists",
            "Isolated workspace already exists for this job/run/stage",
            details={"workspace_path": str(workspace_path)},
            exit_code=6,
        )

    excluded_prefixes = []
    if _is_subpath(safe_workspace_root, source_repo):
        excluded_prefixes.append(safe_workspace_root.relative_to(source_repo).as_posix())
    workspace_path.parent.mkdir(parents=True, exist_ok=True)
    forced_includes = {
        rel_path
        for rel_path in (_normalize_rel_path(value) for value in list(include_paths or []))
        if rel_path
    }
    if forced_includes:
        workspace_path.mkdir(parents=True, exist_ok=False)
        _copy_rel_paths(repo_root=source_repo, workspace_path=workspace_path, rel_paths=sorted(forced_includes))
        strategy = "targeted_copy"
    else:
        git_candidates = _git_copy_candidates(source_repo, forced_includes=forced_includes)
        rel_paths = git_candidates if git_candidates is not None else _walk_copy_candidates(source_repo, forced_includes=forced_includes)
        workspace_path.mkdir(parents=True, exist_ok=False)
        _copy_rel_paths(repo_root=source_repo, workspace_path=workspace_path, rel_paths=rel_paths)
        strategy = "git_filtered_copy" if git_candidates is not None else "filtered_copy"

    return WorkspaceInfo(
        source_repo_path=str(source_repo),
        workspace_root=str(safe_workspace_root),
        workspace_path=str(workspace_path),
        strategy=strategy,
    )
