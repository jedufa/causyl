"""Operator-facing wrapper for typed host verification requests."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from infra.host import verification_actions as _actions
from infra.host import verification_contract as _contract


def _build_request(args: argparse.Namespace) -> Dict[str, Any]:
    """Build one typed verification request from CLI args."""
    action_id = str(args.action_id)
    params: Dict[str, Any] = {}
    if action_id == "android_unit_test":
        params["gradle_task"] = str(args.gradle_task)
        if args.test_selector:
            params["test_selector"] = str(args.test_selector)
    elif action_id == "playwright_spec":
        params["spec"] = str(args.spec)
        if args.project:
            params["project"] = str(args.project)
    return {
        "request_id": str(args.request_id or "").strip(),
        "workflow_id": str(args.workflow_id or "wf-manual").strip(),
        "turn": str(args.turn or "manual").strip(),
        "requested_by_role": str(args.requested_by_role or "operator").strip(),
        "binding": {
            "type": str(args.binding_type or "task_id").strip(),
            "id": str(args.binding_id or "manual").strip(),
        },
        "action": {
            "action_id": action_id,
            "params": params,
        },
        "artifacts": {
            "capture_stdout": True,
            "capture_stderr": True,
        },
        "policy": {
            "timeout_s": int(args.timeout_s),
            "allow_network": False,
            "require_clean_exit": True,
        },
        "reason": str(args.reason or "manual host verification").strip(),
    }


def _print_result_summary(result: Dict[str, Any]) -> None:
    """Print a compact human-readable result summary."""
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    lines = [
        f"request_id: {result.get('request_id', '')}",
        f"action_id: {result.get('action_id', '')}",
        f"ok: {bool(result.get('ok', False))}",
        f"failure_kind: {result.get('failure_kind', '') or '(none)'}",
        f"exit_code: {result.get('exit_code', '')}",
        f"summary: {str(evidence.get('summary') or '').strip()}",
        f"stdout_path: {evidence.get('stdout_path', '')}",
        f"stderr_path: {evidence.get('stderr_path', '')}",
        f"artifact_paths: {', '.join([str(x) for x in (evidence.get('artifact_paths') or [])]) or '(none)'}",
    ]
    sys.stdout.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Run one typed host verification request.")
    parser.add_argument(
        "action_id",
        choices=_actions.allowed_action_ids(),
        help="Allowlisted verification action to execute.",
    )
    parser.add_argument("--workflow-id", default="wf-manual", help="Workflow id to stamp onto the request.")
    parser.add_argument("--turn", default="manual", help="Turn label to stamp onto the request.")
    parser.add_argument("--requested-by-role", default="operator", help="Role label for the request.")
    parser.add_argument("--binding-type", default="task_id", help="Binding type to stamp onto the request.")
    parser.add_argument("--binding-id", default="manual", help="Binding id to stamp onto the request.")
    parser.add_argument("--request-id", default="", help="Optional explicit request id.")
    parser.add_argument("--reason", default="manual host verification", help="Reason string for the request.")
    parser.add_argument("--timeout-s", type=int, default=900, help="Runner timeout in seconds.")
    parser.add_argument("--gradle-task", default=":app:testDebugUnitTest", help="Gradle task for android_unit_test.")
    parser.add_argument("--test-selector", default="", help="Optional JUnit selector for android_unit_test.")
    parser.add_argument("--spec", default="", help="Relative Playwright spec path for playwright_spec.")
    parser.add_argument("--project", default="", help="Optional Playwright project.")
    args = parser.parse_args(argv)

    request = _build_request(args)
    normalized = _contract.normalize_request(request, repo=_contract.repo_root())
    job_dir = Path(str((normalized.get("artifacts") or {}).get("root_dir") or "")).resolve()
    request_path = (job_dir / "request.json").resolve()
    _contract.write_json_atomic(request_path, normalized)

    cmd = [sys.executable, "-m", "infra.host.verification_runner", "--request", str(request_path)]
    cp = subprocess.run(
        cmd,
        cwd=str(_contract.repo_root()),
        capture_output=True,
        text=True,
        check=False,
    )

    result_path = (job_dir / "result.json").resolve()
    if result_path.exists():
        result = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        sys.stderr.write(cp.stderr or "runner did not write result.json\n")
        return int(cp.returncode or 1)

    sys.stdout.write(f"request_path: {request_path}\n")
    sys.stdout.write(f"result_path: {result_path}\n")
    _print_result_summary(result)
    return 0 if bool(result.get("ok")) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
