#!/usr/bin/env python3
"""
tools.debug_review

Purpose
-------
Consolidate debug evidence from the most recent Emergence runs into:

1) Terminal output (stdout)
2) A timestamped text file: <YYYYMMDD_HHMMSS_+ZZZZ>_debug_all.txt

This is meant to make "send me the debug info" a single command.

Timezone Behavior
-----------------
Filenames and header timestamps now use LOCAL time with timezone offset.
Example: 20260214_111945_+0100_debug_all.txt

What it collects (best-effort)
------------------------------
- Latest wrapper jobs: runtime/live/wrapper_jobs/<job_id>__...__<ts>
  - run_meta.json, error.json
  - stdout_stderr.log (tail)
  - logs/*.jsonl (tail)
  - payload/logs/*.jsonl (tail)
  - report.json if present

- Latest manifest node reports: runs/manifests/<ts>__<name>
  - node_report.json, phase3_verify.json, any *.json/*.txt (tail)

Notes
-----
- This script never raises on missing files; it writes what it can find.
- Default behavior is "recent evidence first".
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import List


# ============================================================
# Time handling (LOCAL TIME WITH TZ OFFSET)
# ============================================================


def _find_repo_root(start: Path) -> Path:
    """Find repo root by walking upward from `start` until a repo marker is found.

    This is robust against moving scripts into subpackages (e.g. tools/_scripts).
    Repo markers: .git, pyproject.toml, setup.cfg, requirements.txt.
    Falls back to current working directory if no marker is found.
    """
    start = start.resolve()
    if start.is_file():
        cur = start.parent
    else:
        cur = start
    markers = ('.git', 'pyproject.toml', 'setup.cfg', 'requirements.txt')
    for p in (cur, *cur.parents):
        for m in markers:
            if (p / m).exists():
                return p
    # Last resort: allow running from repo root via cwd
    cwd = Path.cwd().resolve()
    for p in (cwd, *cwd.parents):
        for m in markers:
            if (p / m).exists():
                return p
    return cwd

def _local_compact() -> str:
    """
    Local timestamp for filenames.
    Format: YYYYMMDD_HHMMSS_+ZZZZ
    """
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%z")


def _local_iso() -> str:
    """
    Local timestamp ISO-like string for report header.
    """
    return datetime.now().astimezone().isoformat()


# ============================================================
# Utilities
# ============================================================

def _tail_text(path: Path, max_lines: int = 200) -> str:
    """Read the tail of a text file safely."""
    try:
        data = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(data) <= max_lines:
            return "\n".join(data)
        return "\n".join(data[-max_lines:])
    except Exception as e:
        return f"<failed to read {path}: {e}>"


def _header(title: str) -> str:
    bar = "=" * 78
    return f"\n{bar}\n{title}\n{bar}\n"


def _list_recent_dirs(parent: Path, max_items: int) -> List[Path]:
    """List most recent subdirectories by mtime."""
    try:
        dirs = [p for p in parent.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return dirs[:max_items]
    except Exception:
        return []


def _select_dirs(
    parent: Path,
    *,
    max_items: int,
    since_minutes: int | None = None,
    run_id_contains: str | None = None,
    exact_run_id: str | None = None,
) -> List[Path]:
    """Select run directories under *parent* with optional filters."""
    dirs = _list_recent_dirs(parent, max_items=10_000)
    if not dirs:
        return []

    if since_minutes is not None and since_minutes > 0:
        cutoff = datetime.now().astimezone().timestamp() - (since_minutes * 60)
        dirs = [d for d in dirs if d.stat().st_mtime >= cutoff]

    if exact_run_id:
        dirs = [d for d in dirs if d.name == exact_run_id]
    elif run_id_contains:
        dirs = [d for d in dirs if run_id_contains in d.name]

    return dirs[:max_items]


# ============================================================
# Collection logic
# ============================================================

def _collect_wrapper(wrapper_dir: Path) -> str:
    out = []
    out.append(_header(f"WRAPPER RUN: {wrapper_dir}"))

    for fname in ["run_meta.json", "error.json", "job_spec.json", "artifact_manifest.json"]:
        fp = wrapper_dir / fname
        if fp.exists():
            out.append(f"--- {fname} ---\n{_tail_text(fp, 400)}\n")

    logp = wrapper_dir / "stdout_stderr.log"
    if logp.exists():
        out.append(f"--- stdout_stderr.log (tail) ---\n{_tail_text(logp, 300)}\n")
    else:
        out.append('--- stdout_stderr.log ---\n<MISSING: wrapper did not create stdout_stderr.log>\n')

    logs_dir = wrapper_dir / "logs"
    if logs_dir.exists():
        for fp in sorted(logs_dir.glob("*.jsonl")):
            out.append(f"--- logs/{fp.name} (tail) ---\n{_tail_text(fp, 300)}\n")

    payload_logs = wrapper_dir / "payload" / "logs"
    if payload_logs.exists():
        for fp in sorted(payload_logs.glob("*.jsonl")):
            out.append(f"--- payload/logs/{fp.name} (tail) ---\n{_tail_text(fp, 300)}\n")

    report = wrapper_dir / "payload" / "report.json"
    if report.exists():
        out.append(f"--- payload/report.json ---\n{_tail_text(report, 400)}\n")

    return "\n".join(out)


def _collect_manifest_report(report_dir: Path) -> str:
    out = []
    out.append(_header(f"MANIFEST REPORT: {report_dir}"))

    for fname in ["node_report.json", "phase3_verify.json", "manifest.json"]:
        fp = report_dir / fname
        if fp.exists():
            out.append(f"--- {fname} ---\n{_tail_text(fp, 400)}\n")

    for fp in sorted(report_dir.glob("*.json")):
        if fp.name in {"node_report.json", "phase3_verify.json", "manifest.json"}:
            continue
        out.append(f"--- {fp.name} ---\n{_tail_text(fp, 400)}\n")

    logs_dir = report_dir / "logs"
    if logs_dir.exists():
        for fp in sorted(logs_dir.glob("*.jsonl")):
            out.append(f"--- logs/{fp.name} (tail) ---\n{_tail_text(fp, 300)}\n")

    return "\n".join(out)


# ============================================================
# Report builder
# ============================================================

def build_report(
    *,
    repo_root: Path,
    max_wrapper: int,
    max_manifest: int,
    since_minutes: int | None = None,
    run_id_contains: str | None = None,
    exact_run_id: str | None = None,
) -> str:
    out = []
    out.append(_header("EXPERIMENT DEBUG REVIEW (CONSOLIDATED)"))
    out.append(f"repo_root: {repo_root}")
    out.append(f"ts_local: {_local_iso()}\n")

    wrapper_root = repo_root / "runtime" / "live" / "wrapper_jobs"
    if not wrapper_root.exists():
        legacy = repo_root / "runtime" / "state" / "wrapper_jobs"
        if legacy.exists():
            wrapper_root = legacy
    runs_dir = repo_root / "runs"
    manifests_root = runs_dir / "manifests"

    out.append(_header("RECENT WRAPPER RUNS"))
    if wrapper_root.exists():
        wrappers = _select_dirs(
            wrapper_root,
            max_items=max_wrapper,
            since_minutes=since_minutes,
            run_id_contains=run_id_contains,
            exact_run_id=exact_run_id,
        )
        if not wrappers:
            out.append(f"No wrapper runs found under: {wrapper_root}\n")
        for w in wrappers:
            out.append(_collect_wrapper(w))
    else:
        out.append(f"Wrapper root not found: {wrapper_root}\n")

    out.append(_header("RECENT MANIFEST REPORTS"))
    if manifests_root.exists():
        reps = _select_dirs(
            manifests_root,
            max_items=max_manifest,
            since_minutes=since_minutes,
            run_id_contains=run_id_contains,
            exact_run_id=exact_run_id,
        )
        if not reps:
            out.append(f"No manifest reports found under: {manifests_root}\n")
        for r in reps:
            out.append(_collect_manifest_report(r))
    else:
        out.append(f"Manifests root not found: {manifests_root}\n")

    return "\n".join(out) + "\n"


# ============================================================
# CLI
# ============================================================

def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Convenience:
    - You may pass SINCE_MINUTES as an optional positional argument:
        infra/bin/debug_review 60

    - Or use the explicit flag:
        infra/bin/debug_review --since_minutes 60
    """
    p = argparse.ArgumentParser(description="Consolidate debug info from recent Emergence runs.")
    p.add_argument("--repo_root", default="", help="Repo root (default: auto-detect from this file).")
    p.add_argument("--max_wrapper", type=int, default=3)
    p.add_argument("--max_manifest", type=int, default=3)
    p.add_argument("--out_dir", default="")

    # Optional positional convenience (default: 60 minutes)
    p.add_argument(
        "since_minutes_pos",
        nargs="?",
        type=int,
        default=60,
        help="Look back window in minutes (positional convenience; default: 60).",
    )

    # Explicit flag overrides positional if provided
    p.add_argument(
        "--since_minutes",
        dest="since_minutes",
        type=int,
        default=None,
        help="Look back window in minutes (overrides positional if provided).",
    )

    p.add_argument("--run_id_contains", default="")
    p.add_argument("--exact_run_id", default="")

    # Default TRUE (silent); allow explicit opt-out with --no-silent
    p.add_argument(
        "--silent",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Silent mode (default: True). Use --no-silent to print full report to stdout.",
    )
    return p.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(argv)
    here = Path(__file__).resolve()
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else _find_repo_root(here)

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else (repo_root / "runs")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Explicit flag overrides positional convenience
    since_minutes_effective = args.since_minutes if args.since_minutes is not None else args.since_minutes_pos

    report_text = build_report(
        repo_root=repo_root,
        max_wrapper=args.max_wrapper,
        max_manifest=args.max_manifest,
        since_minutes=since_minutes_effective or None,
        run_id_contains=args.run_id_contains or None,
        exact_run_id=args.exact_run_id or None,
    )

    out_file = out_dir / f"{_local_compact()}_debug_all.txt"
    out_file.write_text(report_text, encoding="utf-8")

    # silent=True => only print the output path
    # silent=False => print full report then output path
    if args.silent:
        print(f"Wrote consolidated debug report to:{out_file}")
    else:
        print(report_text)
        print(f"Wrote consolidated debug report to:{out_file}")
    return 0


# ============================================================
# Safe CLI wrapper (no Python traceback by default)
# ============================================================

def _main_safe() -> int:
    """Run main() with friendly error reporting.

    Set environment variable CAUSYL_TRACEBACK=1 to print full tracebacks.
    """
    import os
    import traceback
    try:
        return int(main())
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        # SystemExit with message/string
        print(f"[ERROR] {code}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[ERROR] Interrupted (KeyboardInterrupt).", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        if os.environ.get("CAUSYL_TRACEBACK", "") == "1":
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(_main_safe())
