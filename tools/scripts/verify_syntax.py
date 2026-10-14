#!/usr/bin/env python3
"""
tools.verify_syntax

Repo sanity checker (syntax + line endings), with optional interactive fixes.

Adds:
- Colored terminal output (TTY-aware, CI-safe)
- --no-color option
"""

from __future__ import annotations

import argparse
import compileall
import os
import re
import sys
import tempfile
import zipfile
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple


# ============================================================
# Terminal Colors (TTY safe)
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

class TermColor:
    ENABLE = sys.stdout.isatty()

    RED = "\033[31m" if ENABLE else ""
    GREEN = "\033[32m" if ENABLE else ""
    YELLOW = "\033[33m" if ENABLE else ""
    CYAN = "\033[36m" if ENABLE else ""
    RESET = "\033[0m" if ENABLE else ""


def red(s: str) -> str:
    return f"{TermColor.RED}{s}{TermColor.RESET}"


def green(s: str) -> str:
    return f"{TermColor.GREEN}{s}{TermColor.RESET}"


def yellow(s: str) -> str:
    return f"{TermColor.YELLOW}{s}{TermColor.RESET}"


def cyan(s: str) -> str:
    return f"{TermColor.CYAN}{s}{TermColor.RESET}"


# ============================================================
# Helpers
# ============================================================

def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _which(cmd: str) -> Optional[str]:
    from shutil import which
    return which(cmd)


def _prompt_yes_no(question: str, default_no: bool = True) -> bool:
    if not _is_interactive():
        return False

    suffix = " [y/N]: " if default_no else " [Y/n]: "
    while True:
        try:
            ans = input(yellow(question + suffix)).strip().lower()
        except EOFError:
            return False

        if ans == "":
            return not default_no
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False

        print("Please answer y or n.")


def _detect_crlf_bytes(data: bytes) -> bool:
    return (b"\r\n" in data) or (b"\r" in data)


def _normalize_to_lf_bytes(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _check_and_maybe_fix_line_endings(path: Path, *, auto_fix: bool) -> Tuple[bool, bool]:
    try:
        raw = path.read_bytes()
    except Exception as e:
        sys.stderr.write(red(f"[verify_syntax] ERROR reading {path}: {e}\n"))
        return (False, False)

    if not _detect_crlf_bytes(raw):
        return (True, False)

    sys.stderr.write(yellow(f"[verify_syntax] CRLF/CR detected: {path}\n"))

    if auto_fix:
        new_raw = _normalize_to_lf_bytes(raw)
        if new_raw != raw:
            path.write_bytes(new_raw)
            sys.stderr.write(green(f"[verify_syntax] Fixed line endings (LF) for: {path}\n"))
            return (True, True)
        return (True, False)

    if _prompt_yes_no(f"Fix line endings (convert to LF) for {path}?"):
        new_raw = _normalize_to_lf_bytes(raw)
        if new_raw != raw:
            path.write_bytes(new_raw)
            sys.stderr.write(green(f"[verify_syntax] Fixed line endings (LF) for: {path}\n"))
            return (True, True)
        return (True, False)

    sys.stderr.write(red(f"[verify_syntax] Not fixed: {path}\n"))
    return (False, False)


# ============================================================
# Python checks
# ============================================================

_INLINE_SUITE_START_RE = re.compile(
    r"^(?P<indent>\s*)(?P<head>(?:async\s+def|def|class|if|elif|else|for|while|try|except|finally|with)\b.*:)(?P<gap> {4,})(?P<tail>\S.*)$"
)
_JOIN_SPLIT_GAP_RE = re.compile(r" {4,}")
_JOIN_SPLIT_RIGHT_RE = re.compile(
    r"^(?:"
    r"(?:if|elif|else|for|while|try|except|finally|with|return|raise|yield|assert|pass|break|continue)\b"
    r"|[A-Za-z_][A-Za-z0-9_]*\s*(?:\(|\[|\.|=|:)"
    r")"
)


def _compile_python_source(source: str, filename: str) -> Tuple[bool, str]:
    try:
        compile(source, filename, "exec")
        return True, ""
    except (SyntaxError, IndentationError, TabError) as e:
        lineno = getattr(e, "lineno", None)
        line_part = f", line {lineno}" if lineno else ""
        return False, f"Sorry: {e.__class__.__name__}: {e.msg} ({filename}{line_part})"


def _extract_failed_line_number(error_text: str) -> Optional[int]:
    match = re.search(r", line (\d+)\)", str(error_text or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _repair_inline_suite_line(line: str) -> Optional[List[str]]:
    match = _INLINE_SUITE_START_RE.match(line)
    if not match:
        return None
    indent = match.group("indent")
    head = match.group("head").rstrip()
    tail = match.group("tail").lstrip()
    if not tail:
        return None
    return [head, f"{indent}    {tail}"]


def _repair_joined_statement_line(line: str) -> Optional[List[str]]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    indent_len = len(line) - len(line.lstrip(" \t"))
    for match in _JOIN_SPLIT_GAP_RE.finditer(line):
        if match.start() <= indent_len:
            continue
        left = line[:match.start()].rstrip()
        right = line[match.end():].lstrip()
        if not left or not right:
            continue
        if not _JOIN_SPLIT_RIGHT_RE.match(right):
            continue
        indent = line[:indent_len]
        return [left, f"{indent}{right}"]
    return None


def _candidate_repair_indexes(line_no: int, total_lines: int, *, lookback: int = 12) -> List[int]:
    if line_no < 1 or total_lines <= 0:
        return []
    upper = min(line_no - 1, total_lines - 1)
    lower = max(0, upper - (lookback - 1))
    return list(range(upper, lower - 1, -1))


def _repair_python_source(source: str, filename: str) -> Tuple[str, bool, List[str]]:
    changed = False
    notes: List[str] = []
    current = source

    for _ in range(12):
        ok, error_text = _compile_python_source(current, filename)
        if ok:
            return current, changed, notes
        line_no = _extract_failed_line_number(error_text)
        if line_no is None:
            break
        lines = current.splitlines(keepends=True)
        if line_no < 1 or line_no > len(lines):
            break
        candidate_indexes = _candidate_repair_indexes(line_no, len(lines))
        replacement = None
        repair_kind = ""
        repair_index = None
        for idx in candidate_indexes:
            original = lines[idx]
            newline = "\n" if original.endswith("\n") else ""
            base = original[:-1] if newline else original
            candidate = _repair_inline_suite_line(base)
            candidate_kind = "inline_suite_split"
            if candidate is None:
                candidate = _repair_joined_statement_line(base)
                candidate_kind = "joined_statement_split"
            if candidate is None:
                continue
            new_lines = [item + newline for item in candidate[:-1]] + [candidate[-1] + newline]
            if "".join(new_lines) == original:
                continue
            replacement = new_lines
            repair_kind = candidate_kind
            repair_index = idx
            break
        if replacement is None or repair_index is None:
            break
        lines[repair_index:repair_index + 1] = replacement
        current = "".join(lines)
        changed = True
        notes.append(f"{repair_kind}:{repair_index + 1}")

    return current, changed, notes


def _compile_python_file(path: Path, *, repair: bool) -> bool:
    try:
        original = path.read_text(encoding="utf-8")
    except Exception as e:
        sys.stderr.write(red(f"[verify_syntax] ERROR reading {path}: {e}\n"))
        return False

    source = original
    changed = False
    repair_notes: List[str] = []
    if repair:
        source, changed, repair_notes = _repair_python_source(original, str(path))
        if changed and source != original:
            path.write_text(source, encoding="utf-8")
            sys.stderr.write(green(f"[verify_syntax] Repaired Python syntax patterns in: {path}\n"))
            for note in repair_notes:
                sys.stderr.write(cyan(f"[verify_syntax]   repair: {note}\n"))

    ok, error_text = _compile_python_source(source, str(path))
    if not ok:
        if error_text:
            sys.stderr.write(f"{error_text}\n")
        sys.stderr.write(red(f"[verify_syntax] Python syntax FAILED: {path}\n"))
    return ok


def _compile_path(path: Path, *, repair_python: bool) -> bool:
    if path.is_dir():
        ok = True
        for py_path in path.rglob("*.py"):
            if not _compile_python_file(py_path, repair=repair_python):
                ok = False
        return ok
    if path.is_file() and path.suffix == ".py":
        return _compile_python_file(path, repair=repair_python)
    return True


# ============================================================
# Shell checks
# ============================================================

def _check_shell_file(path: Path) -> bool:
    ok = True

    result = subprocess.run(
        ["bash", "-n", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(red(f"[verify_syntax] bash -n FAILED: {path}\n"))
        sys.stderr.write(result.stderr)
        ok = False

    if _which("shellcheck"):
        sc = subprocess.run(
            ["shellcheck", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if sc.returncode != 0:
            sys.stderr.write(yellow(f"[verify_syntax] shellcheck warnings/errors: {path}\n"))
            sys.stderr.write(sc.stdout)
            ok = False

    return ok


def _gather_targets(t: Path) -> List[Path]:
    if t.is_file():
        return [t]
    if t.is_dir():
        out: List[Path] = []
        out.extend(t.rglob("*.py"))
        out.extend(t.rglob("*.sh"))
        seen = set()
        uniq: List[Path] = []
        for p in out:
            rp = str(p.resolve())
            if rp in seen:
                continue
            seen.add(rp)
            uniq.append(p)
        return uniq
    return []


def _check_line_endings_in_targets(target: Path, *, auto_fix: bool) -> bool:
    ok = True
    for p in _gather_targets(target):
        if p.suffix not in (".py", ".sh"):
            continue
        o, _changed = _check_and_maybe_fix_line_endings(p, auto_fix=auto_fix)
        if not o:
            ok = False
    return ok


def _check_shell_in_dir(path: Path) -> bool:
    ok = True
    for p in path.rglob("*.sh"):
        if not _check_shell_file(p):
            ok = False
    return ok


# ============================================================
# ZIP handling
# ============================================================

def _check_zip(zip_path: Path) -> bool:
    with tempfile.TemporaryDirectory(prefix="verify_syntax_") as td:
        root = Path(td)
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            zf.extractall(str(root))

        ok_le = _check_line_endings_in_targets(root, auto_fix=False)
        ok_py = compileall.compile_dir(str(root), quiet=1)
        ok_sh = _check_shell_in_dir(root)

        return bool(ok_le) and bool(ok_py) and bool(ok_sh)


# ============================================================
# CLI
# ============================================================

def _default_targets(repo_root: Path) -> List[Path]:
    return [
        repo_root / "tools",
        repo_root / "scripts",
        repo_root / "engine",
        repo_root / "experiments",
        repo_root / "experiments" / "shared",
    ]


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--file", action="append", default=[], help="File/dir/zip to check (repeatable).")
    p.add_argument("--fix", action="store_true", help="Auto-fix line endings and attempt known Python syntax repairs.")
    p.add_argument("--repair-python", action="store_true", help="Attempt known Python syntax repairs on failing files.")
    p.add_argument("--no-color", action="store_true", help="Disable colored output.")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    if args.no_color:
        TermColor.ENABLE = False
        TermColor.RED = TermColor.GREEN = TermColor.YELLOW = TermColor.CYAN = TermColor.RESET = ""

    overall_ok = True
    repair_python = bool(args.fix or args.repair_python)

    if args.file:
        targets = [Path(x).expanduser() for x in args.file]
    else:
        repo_root = _find_repo_root(Path(__file__).resolve())
        targets = _default_targets(repo_root)

    for t in targets:
        if not t.exists():
            continue

        if t.suffix == ".zip":
            ok = _check_zip(t)
        else:
            ok_le = _check_line_endings_in_targets(t, auto_fix=bool(args.fix))
            ok_py = _compile_path(t, repair_python=repair_python)
            ok_sh = _check_shell_in_dir(t) if t.is_dir() else (_check_shell_file(t) if t.suffix == ".sh" else True)
            ok = bool(ok_le) and bool(ok_py) and bool(ok_sh)

        print(f"[verify_syntax] {cyan(str(t))} {green('OK') if ok else red('FAILED')}")

        if not ok:
            overall_ok = False

    if not overall_ok:
        if _is_interactive() and not args.fix:
            sys.stderr.write(yellow("[verify_syntax] Tip: re-run with --fix to auto-normalize line endings and attempt known Python syntax repairs.\n"))
        return 2

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        if os.environ.get("CAUSYL_TRACEBACK") == "1":
            raise
        sys.stderr.write(red(f"[verify_syntax] ERROR: {e}\n"))
        raise SystemExit(2)
