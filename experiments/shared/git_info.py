"""Shared git resolution helpers for experiment execution contracts."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class GitResolution:
    """Resolved git information for a repo."""

    resolved_commit: str
    describe: str
    dirty: bool


def _run_git(args: list[str], repo_root: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def is_immutable_git_ref(git_ref: str, repo_root: str) -> tuple[bool, str]:
    """Validate that git_ref is an immutable commit hash or annotated tag."""
    ref = (git_ref or "").strip()
    if not ref:
        return False, "git_ref is empty"
    if ref.upper() == "HEAD":
        return False, "SPEC_UNSAFE_GIT_REF: 'HEAD' is not immutable"

    if _COMMIT_RE.match(ref):
        cp = _run_git(["cat-file", "-t", ref], repo_root)
        if cp.returncode != 0:
            return False, f"SPEC_GIT_REF_NOT_FOUND: {cp.stderr.strip()}"
        if cp.stdout.strip() != "commit":
            return False, "SPEC_INVALID_JOB_SPEC: commit hash did not resolve to a commit object"
        return True, ""

    typ = _run_git(["cat-file", "-t", ref], repo_root)
    if typ.returncode != 0:
        return False, f"SPEC_GIT_REF_NOT_FOUND: {typ.stderr.strip()}"
    if typ.stdout.strip() != "tag":
        return False, "SPEC_UNSAFE_GIT_REF: git_ref must be an annotated tag or full commit hash"

    cp = _run_git(["rev-parse", "--verify", f"{ref}^{{commit}}"], repo_root)
    if cp.returncode != 0:
        return False, f"SPEC_GIT_REF_NOT_FOUND: {cp.stderr.strip()}"
    resolved = cp.stdout.strip()
    if not _COMMIT_RE.match(resolved):
        return False, "SPEC_INVALID_JOB_SPEC: tag did not resolve to a full commit hash"
    return True, ""


def resolve_git(git_ref: str, repo_root: str) -> GitResolution:
    """Resolve git_ref to immutable details."""
    ok, reason = is_immutable_git_ref(git_ref, repo_root)
    if not ok:
        raise RuntimeError(reason)

    cp = _run_git(["rev-parse", "--verify", f"{git_ref}^{{commit}}"], repo_root)
    if cp.returncode != 0:
        raise RuntimeError(f"SPEC_GIT_REF_NOT_FOUND: {cp.stderr.strip()}")
    resolved = cp.stdout.strip()

    desc = _run_git(["describe", "--tags", "--always"], repo_root)
    describe = desc.stdout.strip() if desc.returncode == 0 else resolved[:7]

    dirty_cp = _run_git(["status", "--porcelain"], repo_root)
    dirty = bool(dirty_cp.stdout.strip()) if dirty_cp.returncode == 0 else False

    return GitResolution(resolved_commit=resolved, describe=describe, dirty=dirty)
