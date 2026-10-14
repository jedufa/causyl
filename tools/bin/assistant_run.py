#!/usr/bin/env python3
"""Apply a unified diff locally and run focused validation commands."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _forbidden_shell_tokens(command: str) -> bool:
    return any(token in str(command or "") for token in ("&&", "||", ";", "|"))


def _run(argv: List[str], *, cwd: Path) -> Dict[str, Any]:
    started = time.monotonic()
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    duration_ms = max(0, int(round((time.monotonic() - started) * 1000.0)))
    return {
        "argv": list(argv),
        "exit_code": int(proc.returncode),
        "stdout": str(proc.stdout or ""),
        "stderr": str(proc.stderr or ""),
        "duration_ms": duration_ms,
        "ok": int(proc.returncode) == 0,
    }


def _parse_validate_commands(raw_commands: List[str]) -> List[List[str]]:
    out: List[List[str]] = []
    for raw in list(raw_commands or []):
        text = str(raw or "").strip()
        if not text:
            continue
        if _forbidden_shell_tokens(text):
            raise ValueError(f"validation command uses blocked shell chaining tokens: {text}")
        argv = shlex.split(text)
        if not argv:
            continue
        out.append(argv)
    return out


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply a ChatGPT-generated diff locally and run validation commands.")
    parser.add_argument("--diff", required=True, help="Path to a git-compatible unified diff file.")
    parser.add_argument("--repo-root", default=".", help="Repo root to apply the diff in.")
    parser.add_argument("--validate", action="append", default=[], help="Validation command to run locally. Repeat for multiple commands.")
    parser.add_argument("--check-only", action="store_true", help="Only run git apply --check; do not apply the patch.")
    parser.add_argument("--json-out", default="", help="Optional path for structured JSON output.")
    args = parser.parse_args(argv)

    repo_root = (Path(args.repo_root).expanduser() if Path(args.repo_root).is_absolute() else (_repo_root() / args.repo_root)).resolve()
    diff_path = (Path(args.diff).expanduser() if Path(args.diff).is_absolute() else Path.cwd() / args.diff).resolve()
    if not repo_root.exists():
        raise SystemExit(f"repo root does not exist: {repo_root}")
    if not diff_path.exists() or not diff_path.is_file():
        raise SystemExit(f"diff file does not exist: {diff_path}")

    summary: Dict[str, Any] = {
        "ok": False,
        "repo_root": str(repo_root),
        "diff": str(diff_path),
        "check_only": bool(args.check_only),
        "apply_check": {},
        "apply": {},
        "validations": [],
    }

    apply_check = _run(["git", "apply", "--check", str(diff_path)], cwd=repo_root)
    summary["apply_check"] = apply_check
    if not apply_check["ok"]:
        _emit_summary(summary, args.json_out)
        return 1

    if not bool(args.check_only):
        apply_result = _run(["git", "apply", str(diff_path)], cwd=repo_root)
        summary["apply"] = apply_result
        if not apply_result["ok"]:
            _emit_summary(summary, args.json_out)
            return 1

    validations: List[Dict[str, Any]] = []
    for command_argv in _parse_validate_commands(list(args.validate or [])):
        validations.append(_run(command_argv, cwd=repo_root))
    summary["validations"] = validations
    summary["ok"] = bool(apply_check["ok"]) and (bool(args.check_only) or bool(summary["apply"].get("ok", True))) and all(
        bool(row.get("ok")) for row in validations
    )
    _emit_summary(summary, args.json_out)
    return 0 if summary["ok"] else 1


def _emit_summary(summary: Dict[str, Any], json_out: str) -> None:
    rendered = json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    if str(json_out or "").strip():
        target = Path(str(json_out)).expanduser()
        if not target.is_absolute():
            target = (Path.cwd() / target).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)


if __name__ == "__main__":
    raise SystemExit(main())
