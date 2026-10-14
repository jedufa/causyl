#!/usr/bin/env python3
"""Create a contract-first scheduled project runner job."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from experiments_shared import project_runner_contract
from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend

REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_key_value(raw: str, *, field_name: str) -> tuple[str, str]:
    text = str(raw or "").strip()
    if "=" not in text:
        raise ValueError(f"{field_name} entries must use key=value")
    key, value = text.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        raise ValueError(f"{field_name} key is required")
    return key, value


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create a scheduled project runner job")
    p.add_argument("--job-id", required=True)
    p.add_argument("--display-title", default="")
    p.add_argument("--project-id", required=True)
    p.add_argument("--runner-id", required=True)
    p.add_argument(
        "--allow-manifest-compatibility",
        action="store_true",
        help="Use the legacy manifest compatibility path when no experiment contract is available.",
    )
    p.add_argument("--arg", action="append", default=[], help="Repeatable key=value runner arg")
    p.add_argument("--flag", action="append", default=[], help="Repeatable boolean runner flag name")
    p.add_argument("--binding-type", default="none", choices=("none", "task_id", "ledger_uid"))
    p.add_argument("--binding-id", default="")
    p.add_argument("--schedule-mode", default="once", choices=("once", "daily", "interval"))
    p.add_argument("--run-at", default="")
    p.add_argument("--time-of-day", default="03:30")
    p.add_argument("--timezone", default="UTC")
    p.add_argument("--every-minutes", type=int, default=60)
    p.add_argument("--max-runtime-seconds", type=int, default=1800)
    p.add_argument("--expected-output-kind", default="report")
    p.add_argument("--expected-output-format", default="markdown")
    p.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
    return p.parse_args(argv)


def _build_schedule(args: argparse.Namespace) -> Dict[str, object]:
    if args.schedule_mode == "once":
        if not str(args.run_at or "").strip():
            raise ValueError("--run-at is required for --schedule-mode once")
        return {"mode": "once", "run_at": str(args.run_at).strip(), "timezone": str(args.timezone).strip() or "UTC"}
    if args.schedule_mode == "daily":
        return {"mode": "daily", "time_of_day": str(args.time_of_day).strip() or "03:30", "timezone": str(args.timezone).strip() or "UTC"}
    return {"mode": "interval", "every_minutes": int(args.every_minutes), "timezone": "UTC"}


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(argv)
    sched_backend.require_legacy_for_mutation("create project runner scheduled job", backend=args.backend or None)
    resolved = project_runner_contract.resolve_project_runner(
        str(args.project_id).strip(),
        str(args.runner_id).strip(),
        repo_root=REPO_ROOT,
        allow_manifest_compatibility=bool(args.allow_manifest_compatibility),
    )
    arg_contract = resolved.get("args") if isinstance(resolved.get("args"), dict) else {}
    runner_args: Dict[str, object] = {}
    for raw in list(args.arg or []):
        key, value = _parse_key_value(raw, field_name="--arg")
        arg_spec = arg_contract.get(key) if isinstance(arg_contract.get(key), dict) else {}
        arg_type = str(arg_spec.get("type") or "str").strip().lower() or "str"
        if arg_type == "string_list":
            current = runner_args.get(key)
            items = list(current or []) if isinstance(current, list) else []
            if value and value not in items:
                items.append(value)
            runner_args[key] = items
        elif arg_type == "bool":
            runner_args[key] = str(value).strip().lower() in {"1", "true", "yes", "on"}
        else:
            runner_args[key] = value
    for raw in list(args.flag or []):
        key = str(raw or "").strip()
        if not key:
            raise ValueError("--flag entries must be non-empty")
        arg_spec = arg_contract.get(key) if isinstance(arg_contract.get(key), dict) else {}
        arg_type = str(arg_spec.get("type") or "bool").strip().lower() or "bool"
        if arg_type != "bool":
            raise ValueError(f"--flag may only be used with bool args, got {key}:{arg_type}")
        runner_args[key] = True
    binding = {"type": str(args.binding_type).strip()}
    if str(args.binding_type).strip() != "none":
        binding["id"] = str(args.binding_id or "").strip()
    spec = {
        "job_id": str(args.job_id).strip(),
        "enabled": True,
        "binding": binding,
        "job_kind": "project_runner",
        "display_title": str(args.display_title or "").strip(),
        "schedule": _build_schedule(args),
        "policy": {"max_runtime_seconds": int(args.max_runtime_seconds)},
        "expected_output": {
            "kind": str(args.expected_output_kind).strip() or "report",
            "format": str(args.expected_output_format).strip() or "markdown",
        },
        "project_run": {
            "project_id": str(args.project_id).strip(),
            "runner_id": str(args.runner_id).strip(),
            "args": runner_args,
            "allow_manifest_compatibility": bool(args.allow_manifest_compatibility),
        },
    }
    saved = sched.save_job(spec, repo_root=REPO_ROOT)
    sys.stdout.write(json.dumps(saved, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
