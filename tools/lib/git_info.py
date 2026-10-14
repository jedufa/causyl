"""tools.joblib.git_info

Git resolution helpers for Phase 0 contracts.

Hard rule (v1): git_ref must be immutable:
- full commit hash (preferred), OR
- annotated tag (must be an actual tag object, not a lightweight tag),
  that resolves to a commit.

We explicitly reject branch names and 'HEAD'.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Optional


_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class GitResolution:
    """Resolved git information for a repo."""

    resolved_commit: str
    describe: str
    dirty: bool


def _run_git(args: list[str], repo_root: str) -> subprocess.CompletedProcess:
    """Run a git command in the given repo root."""
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def is_immutable_git_ref(git_ref: str, repo_root: str) -> tuple[bool, str]:
    """Validate that git_ref is an immutable commit hash or annotated tag.

    Returns
    -------
    (ok, reason)
        ok=True if valid. If ok=False, reason explains why.
    """
    ref = (git_ref or "").strip()
    if not ref:
        return False, "git_ref is empty"
    if ref.upper() == "HEAD":
        return False, "SPEC_UNSAFE_GIT_REF: 'HEAD' is not immutable"

    # Full 40-hex commit hashes are acceptable.
    if _COMMIT_RE.match(ref):
        cp = _run_git(["cat-file", "-t", ref], repo_root)
        if cp.returncode != 0:
            return False, f"SPEC_GIT_REF_NOT_FOUND: {cp.stderr.strip()}"
        if cp.stdout.strip() != "commit":
            return False, "SPEC_INVALID_JOB_SPEC: commit hash did not resolve to a commit object"
        return True, ""

    # Non-commit refs must be annotated tags (object type 'tag').
    # (Lightweight tags resolve to 'commit' and are rejected.)
    typ = _run_git(["cat-file", "-t", ref], repo_root)
    if typ.returncode != 0:
        return False, f"SPEC_GIT_REF_NOT_FOUND: {typ.stderr.strip()}"
    if typ.stdout.strip() != "tag":
        return False, "SPEC_UNSAFE_GIT_REF: git_ref must be an annotated tag or full commit hash"

    # Ensure it resolves to a commit.
    cp = _run_git(["rev-parse", "--verify", f"{ref}^{{commit}}"], repo_root)
    if cp.returncode != 0:
        return False, f"SPEC_GIT_REF_NOT_FOUND: {cp.stderr.strip()}"
    resolved = cp.stdout.strip()
    if not _COMMIT_RE.match(resolved):
        return False, "SPEC_INVALID_JOB_SPEC: tag did not resolve to a full commit hash"

    return True, ""


def resolve_git(git_ref: str, repo_root: str) -> GitResolution:
    """Resolve git_ref to immutable details.

    Parameters
    ----------
    git_ref:
        Full commit hash or annotated tag.
    repo_root:
        Git working tree root.

    Returns
    -------
    GitResolution
        Resolved commit, describe string, dirty flag.

    Raises
    ------
    RuntimeError
        If git_ref cannot be resolved.
    """
    ok, reason = is_immutable_git_ref(git_ref, repo_root)
    if not ok:
        raise RuntimeError(reason)

    cp = _run_git(["rev-parse", "--verify", f"{git_ref}^{{commit}}"], repo_root)
    if cp.returncode != 0:
        raise RuntimeError(f"SPEC_GIT_REF_NOT_FOUND: {cp.stderr.strip()}")
    resolved = cp.stdout.strip()

    # describe is best-effort; fall back to short sha.
    desc = _run_git(["describe", "--tags", "--always"], repo_root)
    describe = desc.stdout.strip() if desc.returncode == 0 else resolved[:7]

    # dirty means the working tree has changes.
    dirty_cp = _run_git(["status", "--porcelain"], repo_root)
    dirty = bool(dirty_cp.stdout.strip()) if dirty_cp.returncode == 0 else False

    return GitResolution(resolved_commit=resolved, describe=describe, dirty=dirty)
