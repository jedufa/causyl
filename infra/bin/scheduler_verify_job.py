#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend


def _git_is_clean(repo_root: Path) -> Tuple[bool, str]:
    cp = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if cp.returncode != 0:
        return False, f"git_status_error:{(cp.stderr or cp.stdout).strip()}"
    return not bool((cp.stdout or "").strip()), (cp.stdout or "").strip()


def _runtime_label(runtime: Dict[str, Any]) -> str:
    tech = str(runtime.get("agent_tech") or "").strip() or "?"
    model = str(runtime.get("model") or "").strip() or "?"
    return f"{tech}/{model}"


def _clear_stale_dirty_flag(job_id: str, state: Dict[str, Any], *, repo_root: Path) -> bool:
    if str(state.get("last_nonlaunch_reason") or "") != "repo_guard_dirty_worktree":
        return False
    clean, _detail = _git_is_clean(repo_root)
    if not clean:
        return False
    changed = False
    for key in (
        "last_nonlaunch_reason",
        "last_nonlaunch_status",
        "last_nonlaunch_utc",
        "last_nonlaunch_run_id",
        "last_nonlaunch_receipt_path",
        "last_nonlaunch_report_path",
        "last_nonlaunch_signature",
    ):
        if state.get(key):
            state[key] = ""
            changed = True
    if changed:
        path = (repo_root / "governance/agents/runtime/scheduled_jobs/state" / f"{job_id}.json").resolve()
        sched._write_json_atomic(path, state)
    return changed


def verify_job(job_id: str, *, repo_root: Path, clean_dirty_flag: bool = False) -> Dict[str, Any]:
    spec = sched.load_job(job_id, repo_root=repo_root)
    state = sched.load_state(job_id, repo_root=repo_root)
    summary = sched.job_summary(spec, state=state, repo_root=repo_root)
    state = dict(summary.get("state") or {})
    due = dict(summary.get("due") or {})
    runtime = dict(spec.get("runtime") or {})
    loop = dict(spec.get("loop") or {})

    warnings: List[str] = []
    failures: List[str] = []
    actions: List[str] = []

    if not str(runtime.get("agent_tech") or "").strip():
        failures.append("missing runtime.agent_tech")
    if not str(runtime.get("model") or "").strip():
        failures.append("missing runtime.model")

    if str(spec.get("job_kind") or "") == "build_verify_loop":
        builder_runtime = dict(loop.get("builder_runtime") or {})
        verifier_runtime = dict(loop.get("verifier_runtime") or {})
        for name, stage_runtime in (("builder", builder_runtime), ("verifier", verifier_runtime)):
            if not str(stage_runtime.get("agent_tech") or "").strip():
                failures.append(f"missing loop.{name}_runtime.agent_tech")
            if not str(stage_runtime.get("model") or "").strip():
                failures.append(f"missing loop.{name}_runtime.model")
        top_label = _runtime_label(runtime)
        builder_label = _runtime_label(builder_runtime)
        verifier_label = _runtime_label(verifier_runtime)
        if top_label != builder_label or top_label != verifier_label:
            warnings.append(
                f"top-level runtime diverges from loop runtimes (top={top_label}, builder={builder_label}, verifier={verifier_label})"
            )

    active_run_id = str(state.get("active_run_id") or "").strip()
    if active_run_id:
        if not bool(state.get("active_claim_present")):
            failures.append(
                f"stranded active run: active_run_id={active_run_id} but claim is missing; "
                "runner likely exited before writing a receipt"
            )
        elif bool(state.get("active_claim_stale")):
            failures.append(
                f"stranded active run: active_run_id={active_run_id} has stale claim "
                f"({str(state.get('active_claim_reason') or '')})"
            )

    if str(state.get("last_nonlaunch_reason") or "") == "repo_guard_dirty_worktree":
        clean, detail = _git_is_clean(repo_root)
        if clean:
            warnings.append("stale repo_guard_dirty_worktree flag; repo is currently clean")
            if clean_dirty_flag and _clear_stale_dirty_flag(job_id, dict(state), repo_root=repo_root):
                actions.append("cleared stale dirty-worktree nonlaunch state")
        else:
            warnings.append("repo guard is blocked by dirty worktree")
            if detail:
                warnings.append(f"dirty preview: {detail.splitlines()[0][:120]}")

    verdict = "FAIL" if failures else ("WARN" if warnings else "PASS")
    return {
        "job_id": job_id,
        "verdict": verdict,
        "failures": failures,
        "warnings": warnings,
        "actions": actions,
        "job_kind": str(spec.get("job_kind") or ""),
        "display_title": str(summary.get("display_title") or ""),
        "due_reason": str(due.get("reason") or ""),
        "runtime": _runtime_label(runtime),
        "builder_runtime": _runtime_label(dict(loop.get("builder_runtime") or {})) if loop else "",
        "verifier_runtime": _runtime_label(dict(loop.get("verifier_runtime") or {})) if loop else "",
        "active_run_id": active_run_id,
        "active_claim_reason": str(state.get("active_claim_reason") or ""),
        "last_status": str(state.get("last_status") or ""),
        "last_nonlaunch_reason": str(state.get("last_nonlaunch_reason") or ""),
    }


def _print_result(result: Dict[str, Any]) -> None:
    print(f"[{result['verdict']}] {result['job_id']}")
    print(f"- title: {result['display_title']}")
    print(f"- kind: {result['job_kind']}")
    print(f"- due_reason: {result['due_reason']}")
    print(f"- runtime: {result['runtime']}")
    if result.get("builder_runtime") or result.get("verifier_runtime"):
        print(f"- builder_runtime: {result.get('builder_runtime')}")
        print(f"- verifier_runtime: {result.get('verifier_runtime')}")
    if result.get("active_run_id"):
        print(f"- active_run_id: {result['active_run_id']}")
        print(f"- active_claim_reason: {result['active_claim_reason']}")
    for item in result.get("failures") or []:
        print(f"  FAIL: {item}")
    for item in result.get("warnings") or []:
        print(f"  WARN: {item}")
    for item in result.get("actions") or []:
        print(f"  ACTION: {item}")


def _summary_table(results: List[Dict[str, Any]]) -> None:
    print("\nSummary")
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for row in results:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    print(f"- PASS={counts['PASS']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    for row in results:
        issue_count = len(row.get("failures") or []) + len(row.get("warnings") or [])
        print(f"- {row['verdict']:4} | {row['job_id']} | issues={issue_count} | runtime={row['runtime']}")


def main(argv: List[str] | None = None) -> int:
    try:
        parser = argparse.ArgumentParser(description="Verify scheduled job config/state truth.")
        parser.add_argument("job_ids", nargs="*", help="Optional job IDs. Default: verify all jobs.")
        parser.add_argument("--clean-dirty-flag", action="store_true", help="Clear stale repo_guard_dirty_worktree state when the repo is actually clean.")
        parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable output.")
        parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
        args = parser.parse_args(argv)
        if sched_backend.selected_backend(args.backend or None) == "v2":
            if args.clean_dirty_flag:
                sched_backend.require_legacy_for_mutation("clean scheduled job dirty flag", backend=args.backend or None)
            bundle = sched_backend.v2_surface_bundle()
            payload = {
                "backend": "v2",
                "surface_status": str(bundle.get("status") or ""),
                "parent_uid": str(bundle.get("parent_uid") or ""),
                "projection_equivalent": bool(bundle.get("projection_equivalent")),
                "jobs": list(((bundle.get("surfaces") or {}).get("watch") or [])),
            }
            if args.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print(f"[V2] surface_status={payload['surface_status']} parent={payload['parent_uid']} projection_equivalent={str(payload['projection_equivalent']).lower()}")
                for row in payload["jobs"]:
                    print(f"- {row.get('child_uid') or '-'} {row.get('decision') or '-'} {row.get('node_kind') or '-'} {row.get('title') or ''}")
            return 0 if payload["surface_status"] in {"ready", "pending_decomposition"} else 1

        job_ids = args.job_ids or [row["job_id"] for row in sched.list_jobs(repo_root=REPO_ROOT)]
        results = [verify_job(job_id, repo_root=REPO_ROOT, clean_dirty_flag=args.clean_dirty_flag) for job_id in job_ids]
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            for idx, result in enumerate(results):
                if idx:
                    print()
                _print_result(result)
            _summary_table(results)
        return 1 if any(row["verdict"] == "FAIL" for row in results) else 0
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
