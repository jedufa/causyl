#!/usr/bin/env python3
"""
tools.create_manifest

One-shot registry -> manifest workflow.

All paths are resolved relative to the Git repository root,
not the current working directory.

USAGE: infra/bin/create_manifest --testname identity_gate_plasticity_decay_smoke

Default values:
  --registry_repo = experiments/registry/v1
  --out_jobs_repo = runtime/artifacts/manifests/jobs
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_REGISTRY_REPO = "experiments/registry/v1"
DEFAULT_OUT_JOBS_REPO = "runtime/artifacts/manifests/jobs"


def _run(cmd: list[str]) -> None:
    """
    Execute a subprocess command and exit on failure.
    """
    p = subprocess.run(cmd)
    if p.returncode != 0:
        raise SystemExit(p.returncode)


def _get_repo_root() -> Path:
    """
    Return absolute path to git repository root.

    Fails if not inside a git repository.
    """
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True
        ).strip()
        return Path(out).resolve()
    except Exception as e:
        raise SystemExit(
            f"Not inside a git repository or git not available: {e}"
        )


def _git_rev_parse_head() -> str:
    """
    Return current git HEAD commit hash.
    """
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True
        ).strip()
    except Exception as e:
        raise SystemExit(f"Failed to get git ref via `git rev-parse HEAD`: {e}")

    if len(out) < 7:
        raise SystemExit(f"Got suspicious git ref: {out!r}")

    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    Parse CLI arguments.
    """
    p = argparse.ArgumentParser(
        description="Create a manifest from a registry in one shot"
    )
    p.add_argument(
        "--registry_repo",
        default=DEFAULT_REGISTRY_REPO,
        help=(
            "Path to registry directory "
            f"(default: {DEFAULT_REGISTRY_REPO})"
        ),
    )
    p.add_argument(
        "--testname",
        required=True,
        help="Registry filename without .yaml",
    )
    p.add_argument(
        "--out_jobs_repo",
        default=DEFAULT_OUT_JOBS_REPO,
        help=(
            "Output directory "
            f"(default: {DEFAULT_OUT_JOBS_REPO})"
        ),
    )
    return p.parse_args(argv)


def _resolve_path(base: Path, user_path: str) -> Path:
    """
    Resolve user-provided path.

    - If absolute → use as-is
    - If relative → resolve against repo root
    """
    p = Path(user_path)
    if p.is_absolute():
        return p.resolve()
    return (base / p).resolve()


def main(argv: list[str] | None = None) -> int:
    """
    Main entrypoint.
    """

    args = _parse_args(argv)

    repo_root = _get_repo_root()

    registry_repo = _resolve_path(repo_root, args.registry_repo)
    out_repo = _resolve_path(repo_root, args.out_jobs_repo)
    testname = str(args.testname)

    if not registry_repo.exists():
        raise SystemExit(
            f"Registry directory not found: {registry_repo}"
        )

    registry_path = registry_repo / f"{testname}.yaml"

    if not registry_path.exists():
        raise SystemExit(
            f"Registry file not found: {registry_path}"
        )

    out_repo.mkdir(parents=True, exist_ok=True)

    out_path = out_repo / f"jobs_{testname}.yaml"
    tmp_path = out_repo / f".jobs_{testname}.tmp.yaml"

    print(f"[INFO] Repo root: {repo_root}")
    print("[1/4] Expanding registry…")

    try:
        from tools import expand_registry  # type: ignore
        expand_registry.main(
            ["--registry", str(registry_path),
             "--out-jobs", str(tmp_path)]
        )
    except Exception:
        _run([
            sys.executable, "-m", "tools.expand_registry",
            "--registry", str(registry_path),
            "--out-jobs", str(tmp_path),
        ])

    jobs_fragment = tmp_path.read_text(encoding="utf-8").rstrip() + "\n"

    print("[2/4] Writing manifest header…")

    git_ref = _git_rev_parse_head()

    header = (
        "manifest_api: 1\n"
        f"git_ref: {git_ref}\n"
        f"output_root: runs/{testname}\n"
        "category: manual\n\n"
    )

    out_path.write_text(header + jobs_fragment, encoding="utf-8")
    tmp_path.unlink(missing_ok=True)

    print("[3/4] Verifying manifest…")

    try:
        from tools import verify_manifest  # type: ignore
        verify_manifest.main(["--manifest", str(out_path)])  # type: ignore
    except Exception:
        _run([
            sys.executable, "-m", "tools.verify_manifest",
            "--manifest", str(out_path)
        ])

    print("[4/4] DONE")
    print(f"Manifest created at: {out_path}")
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
