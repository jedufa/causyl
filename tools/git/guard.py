"""Git change control guard.

Last modified: 2026-03-31 00:00 Europe/Zurich
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any, Sequence

from tools.deterministic_tooling.common import build_envelope, git_head, normalized_list, repo_rel, write_output
from tools.git.support import commit_journal

SCHEMA_VERSION = "git_guard_report_v1"
TOOL_NAME = "git_change_control_guard"


def _git(repo_root: Path, *args: str) -> tuple[int, str, str]:
    try:
        cp = subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True, timeout=5.0, check=False)
    except Exception as exc:
        return (1, "", f"{exc.__class__.__name__}: {exc}")
    return (int(cp.returncode), str(cp.stdout or ""), str(cp.stderr or ""))


def build_git_guard_report(*, repo_root: Path, require_clean: bool = False, modified_paths: Sequence[str] = (), require_commit_journal: bool = False) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    rc, status_out, status_err = _git(repo_root, "status", "--porcelain=v1", "--branch")
    if rc != 0:
        errors.append({"code": "git_status_failed", "severity": "error", "message": status_err or "git status failed"})
        dirty_lines: list[str] = []
        branch_name = "unknown"
    else:
        dirty_lines = [line for line in status_out.splitlines() if line and not line.startswith("##")]
        first = next((line for line in status_out.splitlines() if line.startswith("##")), "")
        branch_name = first.replace("##", "").strip() or "unknown"
    dirty_paths = normalized_list(line[3:] for line in dirty_lines if len(line) > 3)
    if require_clean and dirty_paths:
        errors.append({"code": "dirty_tree", "severity": "error", "message": "Repository is dirty and --require-clean was requested."})
    journal_scan = commit_journal.scan_pending_entries(repo=repo_root)
    pending_paths = [str(row.get("path") or "") for row in journal_scan.get("valid", [])]
    if require_commit_journal and not pending_paths:
        errors.append({"code": "missing_commit_journal", "severity": "error", "message": "No pending commit-journal fragments were found."})
    rc, tag_out, _ = _git(repo_root, "tag", "--list")
    if rc == 0 and not str(tag_out).strip():
        warnings.append({"code": "no_tags", "severity": "warning", "message": "Repository has no tags."})
    cmds = sorted(
        normalized_list(
            [
                "git diff --stat",
                "git status --short",
                *("python -m tools.git.support.commit_journal scan" if pending_paths else "" for _ in [0]),
            ]
        )
    )
    outputs = {
        "safety_report": {
            "git_head": git_head(repo_root),
            "branch_name": branch_name,
            "dirty_paths": dirty_paths,
            "pending_commit_journal_entries": pending_paths,
            "checks_passed": [] if errors else ["git_status", "commit_journal_scan"],
        },
        "cmds_to_run": cmds,
        "warnings": [row["code"] for row in warnings],
    }
    return build_envelope(
        schema_version=SCHEMA_VERSION,
        tool_name=TOOL_NAME,
        inputs={"repo_root": str(repo_root), "require_clean": bool(require_clean), "modified_paths": normalized_list(modified_paths), "require_commit_journal": bool(require_commit_journal)},
        outputs=outputs,
        warnings=warnings,
        errors=errors,
        provenance=[{"ref_type": "git", "ref": str(repo_root), "content_hash": git_head(repo_root)}],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Git change-control guardrails")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--require-commit-journal", action="store_true")
    parser.add_argument("--modified-path", action="append", default=[])
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    payload = build_git_guard_report(
        repo_root=Path(args.repo_root),
        require_clean=bool(args.require_clean),
        require_commit_journal=bool(args.require_commit_journal),
        modified_paths=args.modified_path,
    )
    write_output(Path(args.output).resolve() if args.output else None, payload)
    print(payload["output_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
