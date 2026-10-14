#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]

CRITICAL_PATH_PREFIXES = (
    "infra/bin/scheduler_live_watch.py",
    "infra/bin/scheduler_watchdog.py",
    "infra/bin/scheduler_batch_from_parent.py",
    "tools/bin/scheduler_pipeline_healer.py",
    "infra/controlplane/continuation_packet_bootstrap.py",
    "infra/controlplane/scheduled_jobs.py",
    "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py",
    "infra/controlplane/scheduled_jobs_parts/part03__compiled_execution_shadow_bundle_for_job.py",
    "infra/controlplane/scheduled_jobs_parts/part05__extract_markdown_section.py",
    "infra/webui/server_lab_assistant_task_register.py",
    "tools/bin/implement_recursive.py",
    "tools/bin/intent_sheet_next.py",
    "experiments/shared/action_plane/operator_interface.py",
)

TEST_GROUPS: Dict[str, List[List[str]]] = {
    "option_a": [
        [
            "pytest",
            "-q",
            "tests/control_plane/test_scheduler_live_watch.py",
            "tests/control_plane/test_plane_cli.py::test_plane_cli_request_plans_implement_uid_prompt_to_recursive_implement",
            "tests/control_plane/test_plane_cli.py::test_plane_cli_implement_the_next_intent_sheet_routes_to_recursive_implement",
            "tests/tools/test_intent_sheet_next.py::test_next_sheets_set_safe_only_excludes_resume_existing_family_candidates",
        ],
    ],
    "option_b": [
        [
            "pytest",
            "-q",
            "tests/control_plane/test_scheduler_batch_from_parent.py",
            "tests/control_plane/test_continuation_packet_bootstrap.py",
            "tests/control_plane/test_scheduled_jobs.py::test_build_verify_loop_does_not_recreate_family_closeout_when_family_already_verified",
            "tests/control_plane/test_scheduled_jobs.py::test_queue_execution_family_closeout_retires_existing_job_when_family_already_verified",
            "tests/control_plane/test_scheduled_jobs.py::test_queue_execution_family_closeout_retires_existing_job_when_child_dependencies_missing",
        ],
    ],
}


def _is_critical_path(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/").lstrip("./")
    return any(
        normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/")
        for prefix in CRITICAL_PATH_PREFIXES
    )


def _recent_removals_report(*, touched_paths: List[str], since: str, max_commits: int) -> Dict[str, object]:
    report: Dict[str, object] = {"critical_paths": [], "findings": []}
    critical_paths = [path for path in touched_paths if _is_critical_path(path)]
    report["critical_paths"] = critical_paths
    for path in critical_paths:
        log = subprocess.run(
            ["git", "log", f"--since={since}", "--format=%H", "--", path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        commits = [line.strip() for line in log.stdout.splitlines() if line.strip()][:max_commits]
        if not commits:
            continue
        for sha in commits:
            diff = subprocess.run(
                ["git", "diff", f"{sha}^", sha, "--", path],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            removed = [
                line[1:].strip()
                for line in diff.stdout.splitlines()
                if line.startswith("-") and not line.startswith("---") and line[1:].strip()
            ]
            if removed:
                report["findings"].append(
                    {
                        "path": path,
                        "commit": sha,
                        "removed_lines_sample": removed[:12],
                        "removed_line_count": len(removed),
                    }
                )
    return report


def _run_group(name: str) -> List[Dict[str, object]]:
    results: List[Dict[str, object]] = []
    for command in TEST_GROUPS[name]:
        cp = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
        results.append(
            {
                "group": name,
                "command": command,
                "returncode": cp.returncode,
                "stdout": cp.stdout,
                "stderr": cp.stderr,
            }
        )
        if cp.returncode != 0:
            break
    return results


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the plane critical regression pack.")
    ap.add_argument("--group", choices=("option_a", "option_b", "all"), default="all")
    ap.add_argument("--list", action="store_true", help="List the configured regression pack commands.")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    ap.add_argument("--check-recent-removals", action="store_true", help="Inspect recent removals for touched critical paths.")
    ap.add_argument("--touched-path", action="append", default=[], help="Path touched by the change under review.")
    ap.add_argument("--since", default="7 days ago", help="Window for recent-removal inspection.")
    ap.add_argument("--max-commits", type=int, default=12, help="Maximum commits per path to inspect for removals.")
    ap.add_argument("--ack-recent-removals", action="store_true", help="Allow recent-removal findings without failing.")
    args = ap.parse_args(argv)

    groups = ["option_a", "option_b"] if args.group == "all" else [args.group]
    payload: Dict[str, object] = {"groups": groups}
    if args.list:
        payload["commands"] = {name: TEST_GROUPS[name] for name in groups}
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            for name in groups:
                print(f"[{name}]")
                for command in TEST_GROUPS[name]:
                    print("  " + " ".join(command))
        return 0

    if args.check_recent_removals:
        removals = _recent_removals_report(
            touched_paths=[str(path) for path in list(args.touched_path or [])],
            since=str(args.since or "7 days ago"),
            max_commits=max(1, int(args.max_commits or 12)),
        )
        payload["recent_removals"] = removals
        if removals.get("findings") and not bool(args.ack_recent_removals):
            if args.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print("Recent removals found in critical paths; review before finalizing:")
                for row in removals.get("findings", []):
                    print(f"- {row['path']} @ {row['commit']} ({row['removed_line_count']} removed lines)")
            return 2

    results: List[Dict[str, object]] = []
    for name in groups:
        results.extend(_run_group(name))
        if results and int(results[-1].get("returncode", 1)) != 0:
            break
    payload["results"] = results
    ok = all(int(row.get("returncode", 1)) == 0 for row in results)
    payload["ok"] = ok
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for row in results:
            print(f"[{row['group']}] {' '.join(row['command'])}")
            sys.stdout.write(str(row.get("stdout") or ""))
            sys.stderr.write(str(row.get("stderr") or ""))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
