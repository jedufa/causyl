#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.bin import plane_critical_regression_pack as pack
ZERO_SHA = "0" * 40
OVERRIDE_ENV = "PLANE_ALLOW_RECENT_REMOVAL_REUSE"
OVERRIDE_TRAILER = "ALLOW-RECENT-REMOVAL-REUSE"


def _run_git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _split_lines(text: str) -> List[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _parse_push_specs(stdin_text: str) -> List[Tuple[str, str, str, str]]:
    out: List[Tuple[str, str, str, str]] = []
    for line in _split_lines(stdin_text):
        parts = line.split()
        if len(parts) != 4:
            continue
        out.append((parts[0], parts[1], parts[2], parts[3]))
    return out


def _changed_paths_pre_commit() -> List[str]:
    cp = _run_git(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    return _split_lines(cp.stdout)


def _push_range(local_sha: str, remote_sha: str) -> str:
    local = str(local_sha or "").strip()
    remote = str(remote_sha or "").strip()
    if not local or local == ZERO_SHA:
        return ""
    if not remote or remote == ZERO_SHA:
        return local
    return f"{remote}..{local}"


def _changed_paths_pre_push(specs: Sequence[Tuple[str, str, str, str]]) -> List[str]:
    out: List[str] = []
    seen = set()
    for _local_ref, local_sha, _remote_ref, remote_sha in specs:
        rev = _push_range(local_sha, remote_sha)
        if not rev:
            continue
        cp = _run_git(["diff", "--name-only", "--diff-filter=ACMR", rev])
        for path in _split_lines(cp.stdout):
            if path not in seen:
                seen.add(path)
                out.append(path)
    return out


def _commit_messages_for_push(specs: Sequence[Tuple[str, str, str, str]]) -> List[str]:
    out: List[str] = []
    seen = set()
    for _local_ref, local_sha, _remote_ref, remote_sha in specs:
        rev = _push_range(local_sha, remote_sha)
        if not rev:
            continue
        if ".." in rev:
            cp = _run_git(["log", "--format=%B%x00", rev])
        else:
            cp = _run_git(["log", "--format=%B%x00", "-n", "1", rev])
        for message in str(cp.stdout or "").split("\x00"):
            normalized = message.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                out.append(normalized)
    return out


def _has_override(messages: Iterable[str]) -> bool:
    if str(os.environ.get(OVERRIDE_ENV) or "").strip():
        return True
    token = f"{OVERRIDE_TRAILER}:"
    return any(token in str(message or "") for message in messages)


def _critical_paths(paths: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for path in paths:
        normalized = str(path or "").strip().replace("\\", "/").lstrip("./")
        if not normalized or normalized in seen:
            continue
        if pack._is_critical_path(normalized):
            seen.add(normalized)
            out.append(normalized)
    return out


def _run_guard(*, touched_paths: Sequence[str], override_allowed: bool, enforce_recent_removals: bool = False) -> int:
    critical_paths = _critical_paths(touched_paths)
    if not critical_paths:
        return 0
    group_rc = pack.main(["--group", "all"])
    if group_rc != 0:
        return group_rc
    if not enforce_recent_removals:
        return 0
    removals = pack._recent_removals_report(
        touched_paths=list(critical_paths),
        since="7 days ago",
        max_commits=12,
    )
    findings = list(removals.get("findings") or [])
    if findings and not override_allowed:
        sys.stderr.write("Commit blocked by the recent-removal guard on critical paths.\n")
        sys.stderr.write(
            "This means the change touches code where removed behavior was seen recently and needs an explicit acknowledgment.\n"
        )
        sys.stderr.write("\n")
        sys.stderr.write("For a normal governed commit in the plane, rerun exactly:\n")
        sys.stderr.write(
            f'  {OVERRIDE_ENV}="brief reason" commit\n'
        )
        sys.stderr.write("Example:\n")
        sys.stderr.write(
            '  PLANE_ALLOW_RECENT_REMOVAL_REUSE="intentional closeout dependency repair" commit\n'
        )
        sys.stderr.write("\n")
        sys.stderr.write(
            f"If you are bypassing the normal commit wrapper, add `{OVERRIDE_TRAILER}: <reason>` to the final commit message.\n"
        )
        for row in findings[:8]:
            sys.stderr.write(f"- {row.get('path')} @ {row.get('commit')}\n")
        return 2
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Guard critical paths in the plane before commit/push.")
    ap.add_argument("--mode", choices=("pre-commit", "commit-msg", "pre-push"), required=True)
    ap.add_argument("message_file", nargs="?")
    args = ap.parse_args(argv)

    if args.mode == "pre-commit":
        touched_paths = _changed_paths_pre_commit()
        return _run_guard(
            touched_paths=touched_paths,
            override_allowed=False,
            enforce_recent_removals=False,
        )

    if args.mode == "commit-msg":
        touched_paths = _changed_paths_pre_commit()
        messages: List[str] = []
        if args.message_file:
            try:
                messages.append(Path(args.message_file).read_text(encoding="utf-8"))
            except OSError:
                pass
        return _run_guard(
            touched_paths=touched_paths,
            override_allowed=_has_override(messages),
            enforce_recent_removals=True,
        )

    specs = _parse_push_specs(sys.stdin.read())
    touched_paths = _changed_paths_pre_push(specs)
    override_allowed = _has_override(_commit_messages_for_push(specs))
    return _run_guard(
        touched_paths=touched_paths,
        override_allowed=override_allowed,
        enforce_recent_removals=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
