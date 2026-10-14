#!/usr/bin/env python3
"""Preview or apply textual rewrite plan for engine -> engine."""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
from pathlib import Path

TOKEN_OLD = "engine"
TOKEN_NEW = "engine"


def _run_rg_files() -> list[Path]:
    cmd = [
        "rg",
        "-l",
        TOKEN_OLD,
        ".",
        "--glob",
        "!.git/**",
        "--glob",
        "!**/__pycache__/**",
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "rg failed")
    files = [Path(line.strip()) for line in proc.stdout.splitlines() if line.strip()]
    files.sort()
    return files


def _in_scope(path: Path, scope: str) -> bool:
    p = str(path).lstrip("./")
    if scope == "docs":
        return p.startswith("docs/")
    if scope == "runtime":
        return not p.startswith("docs/")
    return True


def _is_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:4096]
    except Exception:
        return True
    return b"\x00" in chunk


def _is_safe_text_target(path: Path) -> bool:
    text_exts = {
        ".py",
        ".sh",
        ".md",
        ".txt",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".ini",
        ".cfg",
        ".js",
        ".ts",
        ".html",
        ".css",
    }
    return path.suffix.lower() in text_exts


def main() -> int:
    ap = argparse.ArgumentParser(description="Preview/apply rename token rewrite plan")
    ap.add_argument("--apply", action="store_true", help="Apply replacements in safe text files")
    ap.add_argument(
        "--scope",
        choices=["runtime", "docs", "all"],
        default="runtime",
        help="Target subset of files (default: runtime, excludes docs/)",
    )
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Glob pattern(s) to exclude, can be repeated",
    )
    args = ap.parse_args()

    files = _run_rg_files()
    changed = 0
    skipped_binary = 0
    skipped_unsafe = 0

    for p in files:
        if not p.exists():
            continue
        if not _in_scope(p, args.scope):
            continue
        if any(fnmatch.fnmatch(str(p), pat) for pat in args.exclude):
            continue
        if _is_binary(p):
            skipped_binary += 1
            continue
        if not _is_safe_text_target(p):
            skipped_unsafe += 1
            continue

        text = p.read_text(encoding="utf-8", errors="replace")
        count = text.count(TOKEN_OLD)
        if count == 0:
            continue

        print(f"{p}: replacements={count}")
        if args.apply:
            p.write_text(text.replace(TOKEN_OLD, TOKEN_NEW), encoding="utf-8")
            changed += 1

    mode = "applied" if args.apply else "preview"
    print(
        f"mode={mode} scope={args.scope} files_indexed={len(files)} changed={changed}"
        f" skipped_binary={skipped_binary} skipped_unsafe={skipped_unsafe}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
