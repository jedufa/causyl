#!/usr/bin/env python3
"""
tools.cli_regression.verify_cli_args

Regression test for CLI argument availability across the repo.

What this *is*
--------------
A contract test that verifies: "the CLIs we snapshot still expose at least the
same flags/options as the snapshot."

It is intentionally conservative:
- It FAILS if an expected option disappears (deleted/renamed/forgotten).
- It ALLOWS new options to be added (additions are not regressions here).

Why this exists
---------------
Emergence has multiple execution layers (manifest/wrapper layer, engine/nox
layer). Humans inevitably "clean up" CLIs and accidentally remove args that
automation depends on. This test is the tripwire.

How it works (simple on purpose)
--------------------------------
1) Load a committed snapshot JSON:
     tools/cli_regression/cli_args_snapshot_v1.json
2) For each module entry in the snapshot:
     python -m <module> --help
3) Parse option strings from the help text.
4) Assert current_options is a SUPerset of expected_options.

Usage
-----
  # Run the regression check
  python -m tools.cli_regression.verify_cli_args

  # Re-generate the snapshot (intentional update)
  python -m tools.cli_regression.verify_cli_args --update-snapshot

Exit codes
----------
0 = OK
1 = regression detected (missing options) or execution failure

Notes
-----
- This test does not validate "forwarding correctness" across layers.
  It only validates that the options remain available on the relevant CLIs.
  Forwarding is a separate contract and should be tested with an argv-dump or
  wrapper artifact check when available.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple


# Extract both long and short options from typical help/usage text.
#
# NOTE: Be careful with escaping here. We want regex tokens like \w (word char),
# not the literal characters backslash+w.
_OPT_RE = re.compile(r"(?<!\w)(--[A-Za-z0-9][\w-]*)(?!\w)|(?<!\w)(-[A-Za-z0-9])(?!\w)")


@dataclass(frozen=True)
class ModuleSnapshot:
    """Expected CLI options for a module entrypoint."""
    module: str
    options: Set[str]


def _repo_root_from_this_file() -> Path:
    """Return repo root based on this file location."""
    # tools/cli_regression/verify_cli_args.py -> repo root is 3 levels up.
    return Path(__file__).resolve().parents[2]


def _snapshot_path(repo_root: Path) -> Path:
    """Return path to the snapshot JSON file."""
    return repo_root / "tools" / "cli_regression" / "cli_args_snapshot_v1.json"


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    """Parse CLI args for this verifier."""
    p = argparse.ArgumentParser(description="Verify Emergence CLI args regression snapshot (superset check).")
    p.add_argument(
        "--update-snapshot",
        action="store_true",
        help="Rewrite the snapshot file using the current repo state (intentional updates only).",
    )
    p.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to use (default: current interpreter).",
    )
    p.add_argument(
        "--cwd",
        default="",
        help="Working directory to run modules from (default: repo root).",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-module option counts and extra diagnostics.",
    )
    return p.parse_args(argv)


def _run_help(python_exe: str, module: str, cwd: Path) -> Tuple[int, str]:
    """Run `python -m <module> --help` and return (rc, combined_output)."""
    proc = subprocess.run(
        [python_exe, "-m", module, "--help"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _extract_options(help_text: str) -> Set[str]:
    """Extract CLI option strings from help text."""
    return {m.group(0) for m in _OPT_RE.finditer(help_text) if m.group(0)}


def _load_snapshot(path: Path) -> List[ModuleSnapshot]:
    """Load snapshot file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    modules = []
    for entry in data.get("modules", []):
        modules.append(ModuleSnapshot(module=str(entry["module"]), options=set(entry.get("options", []))))
    return modules


def _write_snapshot(path: Path, modules: List[ModuleSnapshot]) -> None:
    """Write snapshot file."""
    # utcnow() is deprecated in newer Python; use an explicit UTC-aware timestamp.
    now_utc = _dt.datetime.now(_dt.UTC).isoformat().replace("+00:00", "Z")
    payload = {
        "schema_version": "v1",
        "generated_at_utc": now_utc,
        "modules": [{"module": m.module, "options": sorted(m.options)} for m in modules],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _discover_modules(repo_root: Path) -> List[str]:
    """Discover runnable modules that declare an argparse parser.

    This is only used for snapshot generation. The *verification* step uses
    the committed snapshot as the authoritative list.
    """
    modules: Set[str] = set()

    def to_module(p: Path) -> str:
        rel = p.relative_to(repo_root)
        parts = list(rel.parts)
        parts[-1] = parts[-1][:-3]  # strip .py
        return ".".join(parts)

    for base in [repo_root / "tools", repo_root / "scripts"]:
        for py in base.rglob("*.py"):
            if py.name == "__init__.py":
                continue
            txt = py.read_text(encoding="utf-8", errors="ignore")
            if "if __name__" in txt and "ArgumentParser" in txt:
                modules.add(to_module(py))

    # Include thin shims that are the canonical UX even if they contain no argparse.
    modules.add("tools.run_manifest")
    modules.add("tools.run_job")

    # Ignore legacy copies.
    modules = {m for m in modules if "tools.__old" not in m and "scripts.__old" not in m}
    return sorted(modules)


def main(argv: List[str] | None = None) -> int:
    """CLI entrypoint."""
    args = _parse_args(argv)
    repo_root = _repo_root_from_this_file()
    cwd = Path(args.cwd).resolve() if args.cwd else repo_root

    snap_path = _snapshot_path(repo_root)

    if args.update_snapshot:
        modules = _discover_modules(repo_root)
        new_entries: List[ModuleSnapshot] = []
        for mod in modules:
            rc, out = _run_help(args.python, mod, cwd=cwd)
            if rc != 0:
                sys.stderr.write(f"[cli_args] SNAPSHOT_FAIL module={mod} rc={rc}\n{out[:2000]}\n")
                return 1
            new_entries.append(ModuleSnapshot(module=mod, options=_extract_options(out)))
            if args.verbose:
                sys.stderr.write(f"[cli_args] snapshot {mod}: {len(new_entries[-1].options)} opts\n")
        _write_snapshot(snap_path, new_entries)
        sys.stdout.write(f"OK: wrote snapshot {snap_path}\n")
        return 0

    if not snap_path.exists():
        sys.stderr.write(f"ERROR: snapshot not found: {snap_path}\n")
        sys.stderr.write("Run with --update-snapshot to generate it.\n")
        return 1

    expected = _load_snapshot(snap_path)

    missing_total: Dict[str, List[str]] = {}
    for entry in expected:
        rc, out = _run_help(args.python, entry.module, cwd=cwd)
        if rc != 0:
            missing_total[entry.module] = [f"<module failed rc={rc}>"]
            if args.verbose:
                sys.stderr.write(f"[cli_args] module failed {entry.module} rc={rc}\n{out[:2000]}\n")
            continue
        current = _extract_options(out)
        missing = sorted(entry.options - current)
        if missing:
            missing_total[entry.module] = missing
        if args.verbose:
            sys.stderr.write(f"[cli_args] {entry.module}: expected={len(entry.options)} current={len(current)} missing={len(missing)}\n")

    if missing_total:
        sys.stderr.write("\nCLI ARG REGRESSION DETECTED (missing expected options)\n")
        sys.stderr.write("-----------------------------------------------------\n")
        for mod, miss in missing_total.items():
            sys.stderr.write(f"\n{mod}\n")
            for opt in miss:
                sys.stderr.write(f"  - {opt}\n")
        sys.stderr.write("\nIf this was an intentional CLI change, regenerate the snapshot:\n")
        sys.stderr.write("  python -m tools.cli_regression.verify_cli_args --update-snapshot\n")
        return 1

    sys.stdout.write("OK: CLI args superset check passed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
