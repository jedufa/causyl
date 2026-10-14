"""
tools.verify_manifest

Manifest consistency verifier (fast preflight).

Goals
-----
- Catch common "instant failure" causes before running run_manifest.
- Validate git_ref immutability rules (full 40 SHA or annotated tag).
- Validate basic manifest schema invariants.
- Validate that referenced modules and files exist in expected locations.

Usage
-----
infra/bin/verify_manifest --manifest tools/_examples/manifest_x.yaml

Exit codes
----------
0 = OK
2 = Validation failed (action required)
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from tools.manifest_io import _load_manifest, _repo_root_from_this_file


_GIT_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_GIT_HEX_SHORT_RE = re.compile(r"^[0-9a-f]{7,39}$")


@dataclass
class Issue:
    """A single validation issue."""

    level: str  # "ERROR" | "WARN"
    code: str
    message: str


def _run_git(repo_root: Path, args: List[str]) -> Tuple[int, str]:
    """Run a git command and return (rc, stdout_stripped)."""
    try:
        out = subprocess.check_output(["git"] + args, cwd=str(repo_root), text=True, stderr=subprocess.STDOUT)
        return 0, out.strip()
    except subprocess.CalledProcessError as e:
        return int(e.returncode or 1), (e.output or "").strip()
    except Exception as e:
        return 1, repr(e)


def _validate_git_ref(repo_root: Path, git_ref: str) -> List[Issue]:
    """Validate git_ref immutability contract."""
    issues: List[Issue] = []
    ref = (git_ref or "").strip()

    if not ref:
        issues.append(Issue("ERROR", "GIT_REF_MISSING", "manifest.git_ref is missing"))
        return issues

    if "dirty" in ref.lower():
        issues.append(Issue("ERROR", "GIT_REF_DIRTY", f"git_ref contains 'dirty' which is not allowed for manifests: {ref}"))
        return issues

    if _GIT_HEX_SHORT_RE.match(ref) and not _GIT_HEX_40_RE.match(ref):
        issues.append(Issue("ERROR", "GIT_REF_SHORT_SHA", f"Short SHA not allowed: {ref}. Use: git rev-parse HEAD"))
        return issues

    if _GIT_HEX_40_RE.match(ref):
        # Optional: verify commit exists
        rc, out = _run_git(repo_root, ["cat-file", "-t", ref])
        if rc != 0 or out.strip() != "commit":
            issues.append(Issue("ERROR", "GIT_REF_BAD_COMMIT", f"git_ref looks like SHA but is not a commit: {ref} ({out})"))
        return issues

    # Otherwise, require annotated tag
    rc, t = _run_git(repo_root, ["cat-file", "-t", ref])
    if rc != 0:
        issues.append(Issue("ERROR", "GIT_REF_UNRESOLVED", f"git_ref cannot be resolved by git: {ref} ({t})"))
        return issues

    if t.strip() != "tag":
        issues.append(Issue("ERROR", "GIT_REF_NOT_ANNOTATED_TAG", f"git_ref must be full SHA or annotated tag. '{ref}' is type '{t}'."))
        return issues

    rc, out = _run_git(repo_root, ["rev-parse", f"{ref}^{{commit}}"])
    if rc != 0 or not _GIT_HEX_40_RE.match(out.strip()):
        issues.append(Issue("ERROR", "GIT_REF_TAG_NOT_COMMIT", f"Annotated tag does not resolve to commit: {ref} ({out})"))

    return issues


def _validate_manifest_schema(manifest: Dict[str, Any]) -> List[Issue]:
    """Validate core manifest schema fields."""
    issues: List[Issue] = []

    api = int(manifest.get("manifest_api", 0) or 0)
    if api != 1:
        issues.append(Issue("ERROR", "MANIFEST_API", f"manifest_api must be 1 (got {manifest.get('manifest_api')})"))

    jobs = list(manifest.get("jobs", []) or [])
    if not jobs:
        issues.append(Issue("ERROR", "JOBS_EMPTY", "manifest.jobs is empty"))

    # Unique job_id
    seen = set()
    for j in jobs:
        jid = str(j.get("job_id", "") or "")
        if not jid:
            issues.append(Issue("ERROR", "JOB_ID_MISSING", "A job is missing job_id"))
            continue
        if jid in seen:
            issues.append(Issue("ERROR", "JOB_ID_DUP", f"Duplicate job_id: {jid}"))
        seen.add(jid)

        mod = str(j.get("module", "") or "")
        if not mod:
            issues.append(Issue("ERROR", "JOB_MODULE_MISSING", f"Job '{jid}' is missing module"))

    return issues


def _validate_modules(jobs: List[Dict[str, Any]]) -> List[Issue]:
    """Validate that job modules import."""
    issues: List[Issue] = []
    for j in jobs:
        jid = str(j.get("job_id", "") or "")
        mod = str(j.get("module", "") or "")
        if not mod:
            continue
        try:
            importlib.import_module(mod)
        except Exception as e:
            issues.append(Issue("ERROR", "MODULE_IMPORT_FAIL", f"Job '{jid}': cannot import module '{mod}': {e!r}"))
    return issues


def _validate_file_refs(repo_root: Path, manifest: Dict[str, Any]) -> List[Issue]:
    """Best-effort scan for referenced file paths inside job args."""
    issues: List[Issue] = []
    jobs = list(manifest.get("jobs", []) or [])

    # Heuristic keys that usually hold paths
    path_keys = {"path", "file", "filepath", "registry", "registry_path", "spec", "mask", "masks", "asset", "assets", "drought_spec", "drought_spec_path"}

    def check_path(p: str, context: str) -> None:
        if not p:
            return
        pp = Path(p)
        # Treat relative paths as repo-root relative for verification.
        if not pp.is_absolute():
            pp = (repo_root / pp).resolve()
        if not pp.exists():
            issues.append(Issue("ERROR", "MISSING_FILE", f"{context}: missing file/path: {p} (resolved: {pp})"))

    for j in jobs:
        jid = str(j.get("job_id", "") or "")
        args_dict = dict(j.get("args", {}) or {})
        for k, v in args_dict.items():
            lk = str(k).lower()
            if lk in path_keys:
                if isinstance(v, str):
                    check_path(v, f"job '{jid}' args.{k}")
                elif isinstance(v, list):
                    for idx, item in enumerate(v):
                        if isinstance(item, str):
                            check_path(item, f"job '{jid}' args.{k}[{idx}]")

    # output_root sanity
    out_root = str(manifest.get("output_root", "") or "")
    if out_root:
        pp = Path(out_root)
        if not pp.is_absolute():
            pp = (repo_root / pp).resolve()
        try:
            pp.mkdir(parents=True, exist_ok=True)
            test = pp / "_verify_manifest_write_test.txt"
            test.write_text("ok\n", encoding="utf-8")
            test.unlink(missing_ok=True)
        except Exception as e:
            issues.append(Issue("ERROR", "OUTPUT_ROOT_WRITE_FAIL", f"output_root not writable: {out_root} ({e!r})"))

    return issues


def _write_report(path: Path, issues: List[Issue]) -> None:
    """Write JSON report."""
    obj = {"ok": all(i.level != "ERROR" for i in issues), "issues": [i.__dict__ for i in issues]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _print_summary_json(*, manifest_path: str, git_ref: str, ok: bool, error_count: int, warn_count: int, job_count: int) -> None:
    """Print a single-line JSON summary to stdout.

    This is useful for scripting/CI where you want machine-readable output without parsing logs.
    """
    obj = {
        "ok": bool(ok),
        "manifest": str(manifest_path),
        "git_ref": str(git_ref),
        "errors": int(error_count),
        "warnings": int(warn_count),
        "jobs": int(job_count),
    }
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main(argv: List[str] | None = None) -> int:
    """CLI entrypoint."""
    p = argparse.ArgumentParser(description="Verify a manifest (git_ref + modules + file refs)")
    p.add_argument("--manifest", required=True, help="Path to manifest YAML/JSON")
    p.add_argument("--report", default="", help="Optional path to write report JSON")
    p.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    p.add_argument("--verbose", action="store_true", help="Print extra details on success (and context on failure)")
    p.add_argument(
        "--summary-json",
        action="store_true",
        help="Print a one-line machine-readable JSON summary to stdout at the end",
    )
    args = p.parse_args(argv)

    repo_root = Path(_repo_root_from_this_file()).resolve()
    manifest_path = str(args.manifest)
    manifest = _load_manifest(args.manifest)

    issues: List[Issue] = []
    issues += _validate_manifest_schema(manifest)
    issues += _validate_git_ref(repo_root, str(manifest.get("git_ref", "") or ""))

    jobs = list(manifest.get("jobs", []) or [])
    issues += _validate_modules(jobs)
    issues += _validate_file_refs(repo_root, manifest)

    if args.strict:
        for i in issues:
            if i.level == "WARN":
                i.level = "ERROR"

    # Print issues
    error_count = 0
    warn_count = 0

    for i in issues:
        stream = sys.stderr if i.level == "ERROR" else sys.stdout
        stream.write(f"{i.level} {i.code}: {i.message}\n")
        if i.level == "ERROR":
            error_count += 1
        elif i.level == "WARN":
            warn_count += 1

    ok = error_count == 0

    # Summary line (always)
    if ok:
        sys.stdout.write(f"OK: manifest verified successfully (0 errors, {warn_count} warnings)\n")
        if args.verbose:
            git_ref = str(manifest.get("git_ref", "") or "").strip()
            out_root = str(manifest.get("output_root", "") or "").strip()
            if out_root:
                pp = Path(out_root)
                if not pp.is_absolute():
                    pp = (repo_root / pp).resolve()
                out_root_res = str(pp)
            else:
                out_root_res = ""
            sys.stdout.write(f"  manifest: {manifest_path}\n")
            sys.stdout.write(f"  repo_root: {repo_root}\n")
            sys.stdout.write(f"  git_ref: {git_ref}\n")
            sys.stdout.write(f"  jobs: {len(jobs)}\n")
            if out_root:
                sys.stdout.write(f"  output_root: {out_root} (resolved: {out_root_res})\n")
    else:
        sys.stderr.write(f"FAILED: manifest verification failed ({error_count} errors, {warn_count} warnings)\n")
        if args.verbose:
            sys.stderr.write(f"  manifest: {manifest_path}\n")
            sys.stderr.write(f"  repo_root: {repo_root}\n")
            sys.stderr.write(f"  jobs: {len(jobs)}\n")

    if args.report:
        _write_report(Path(args.report), issues)

    if args.summary_json:
        _print_summary_json(
            manifest_path=manifest_path,
            git_ref=str(manifest.get("git_ref", "") or "").strip(),
            ok=ok,
            error_count=error_count,
            warn_count=warn_count,
            job_count=len(jobs),
        )

    return 0 if ok else 2

if __name__ == "__main__":
    raise SystemExit(main())
