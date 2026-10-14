#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from infra.webui import server_lab_assistant_web_operations as _web_ops


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _print_json(obj: Dict[str, Any]) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _print_header(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def _print_kv(label: str, value: Any) -> None:
    text = _clean(value)
    print(f"{label}: {text if text else '-'}")


def _joined(values: Iterable[Any]) -> str:
    items = [_clean(value) for value in values if _clean(value)]
    return ", ".join(items) if items else "-"


def _format_health(row: Dict[str, Any]) -> str:
    health = dict(row.get("health") or {})
    state = _clean(health.get("health"))
    reason = _clean(health.get("reason"))
    if state and reason:
        return f"{state} ({reason})"
    return state or reason or "-"


def _format_timer_state(unit: Dict[str, Any]) -> str:
    active = _clean(unit.get("active_state"))
    sub = _clean(unit.get("sub_state"))
    file_state = _clean(unit.get("unit_file_state"))
    parts = [part for part in (active, sub, file_state) if part]
    return " / ".join(parts) if parts else "missing"


def _print_mode_status(name: str, row: Dict[str, Any]) -> None:
    print(f"{name}:")
    if not row.get("exists"):
        print("  no report yet")
        return
    print(f"  health: {_format_health(row)}")
    print(f"  truth: {_clean(dict(row.get('truth_reconciliation') or {}).get('overall_state')) or '-'}")
    print(f"  updated: {_clean(row.get('utc')) or '-'}")
    root_causes = list(row.get("root_causes") or [])
    if root_causes:
        print("  root causes:")
        for item in root_causes[:4]:
            condition_id = _clean(item.get("condition_id"))
            risk_level = _clean(item.get("risk_level"))
            summary = _clean(item.get("summary"))
            details = f"{condition_id} [{risk_level}]" if risk_level else condition_id
            print(f"    - {details or 'unknown'}")
            if summary:
                print(f"      {summary}")
    else:
        print("  root causes: none")


def _print_status() -> None:
    payload = _web_ops.watchdog_snapshot(_repo_root())
    timer_status = dict(payload.get("timer_status") or {})
    latest = dict(payload.get("latest") or {})
    _print_header("Watchdog Status")
    _print_kv("Installed", "yes" if timer_status.get("installed") else "no")
    units = dict(timer_status.get("units") or {})
    if units:
        print("Timers:")
        for key in ("audit_timer", "selfheal_timer", "audit_service", "selfheal_service"):
            unit = dict(units.get(key) or {})
            if not unit:
                continue
            print(f"  - {key}: {_format_timer_state(unit)}")
    _print_header("Latest Reports")
    _print_mode_status("Audit", dict(latest.get("audit") or {}))
    _print_mode_status("Self-Heal", dict(latest.get("selfheal") or {}))


def _print_recent_actions() -> None:
    payload = _web_ops.watchdog_activity_snapshot(_repo_root(), action_limit=20, event_limit=12, control_limit=12, log_lines=40)
    _print_header("Recent Actions")
    _print_kv("Generated", payload.get("utc"))
    recent_actions = list(payload.get("recent_actions") or [])
    if recent_actions:
        print("Actions:")
        for row in recent_actions:
            source = _clean(row.get("source"))
            mode = _clean(row.get("mode"))
            detail = _clean(row.get("action") or row.get("decision_kind") or row.get("scheduler_disposition"))
            condition_id = _clean(row.get("condition_id"))
            requested_actions = _joined(list(row.get("requested_actions") or []))
            parts = [part for part in (source, mode, detail, condition_id) if part]
            line = " | ".join(parts) if parts else "unknown action"
            print(f"  - {line}")
            if requested_actions != "-":
                print(f"    requested: {requested_actions}")
    else:
        print("Actions: none")
    recent_controls = list(payload.get("recent_controls") or [])
    if recent_controls:
        print("Controls:")
        for row in recent_controls:
            confirmation = dict(row.get("confirmation") or {})
            state = _clean(confirmation.get("state"))
            summary = _clean(confirmation.get("summary"))
            action_id = _clean(row.get("action_id"))
            requested_utc = _clean(row.get("requested_utc"))
            print(f"  - {action_id or 'unknown'} at {requested_utc or '-'} [{state or 'unknown'}]")
            if summary:
                print(f"    {summary}")
    else:
        print("Controls: none")
    maintenance_events = list(payload.get("recent_maintenance_events") or [])
    if maintenance_events:
        print("Maintenance Runs:")
        for row in maintenance_events[:8]:
            mode = _clean(row.get("mode"))
            run_id = _clean(row.get("run_id"))
            truth = _clean(row.get("truth_overall_state"))
            health = _clean(dict(row.get("health") or {}).get("reason"))
            print(f"  - {mode or 'unknown'} | {run_id or '-'} | truth={truth or '-'} | reason={health or '-'}")
    else:
        print("Maintenance Runs: none")


def _print_recent_log() -> None:
    payload = _web_ops.watchdog_activity_snapshot(_repo_root(), log_lines=120)
    recent_log = dict(payload.get("recent_log") or {})
    _print_header("Recent Log")
    _print_kv("Generated", payload.get("utc"))
    _print_kv("Mode", recent_log.get("mode"))
    _print_kv("Path", recent_log.get("path"))
    lines = list(recent_log.get("lines") or [])
    print("Log:")
    if not lines:
        print("  no recent log available")
        return
    for line in lines:
        print(f"  {line}")


def _print_activity() -> None:
    payload = _web_ops.watchdog_activity_snapshot(_repo_root())
    _print_header("Watchdog Activity")
    _print_kv("Generated", payload.get("utc"))
    recent_actions = list(payload.get("recent_actions") or [])
    recent_controls = list(payload.get("recent_controls") or [])
    recent_maintenance_events = list(payload.get("recent_maintenance_events") or [])
    recent_log = dict(payload.get("recent_log") or {})
    _print_kv("Recent action count", len(recent_actions))
    _print_kv("Recent control count", len(recent_controls))
    _print_kv("Recent maintenance count", len(recent_maintenance_events))
    _print_kv("Latest log mode", recent_log.get("mode"))
    if recent_actions:
        print("Top actions:")
        for row in recent_actions[:5]:
            source = _clean(row.get("source"))
            detail = _clean(row.get("action") or row.get("decision_kind") or row.get("scheduler_disposition"))
            condition_id = _clean(row.get("condition_id"))
            print(f"  - {' | '.join(part for part in (source, detail, condition_id) if part)}")
    if recent_controls:
        print("Top controls:")
        for row in recent_controls[:5]:
            confirmation = dict(row.get("confirmation") or {})
            print(
                f"  - {_clean(row.get('action_id')) or 'unknown'} | "
                f"{_clean(confirmation.get('state')) or 'unknown'} | "
                f"{_clean(row.get('requested_utc')) or '-'}"
            )
    print("\nUse `infra/user/server-watchdog actions` for the action history and `infra/user/server-watchdog log` for the readable report tail.")


def _run_action(action_id: str) -> None:
    payload = _web_ops.invoke_runtime_control(_repo_root(), action_id=action_id, writer="tools.user.server_watchdog")
    confirmation = dict(payload.get("confirmation") or {})
    invocation = dict(payload.get("invocation") or {})
    response = dict(invocation.get("response") or {})
    _print_header("Watchdog Action")
    _print_kv("Action", payload.get("action_id"))
    _print_kv("Result", confirmation.get("state"))
    _print_kv("Summary", confirmation.get("summary"))
    _print_kv("Requested", payload.get("requested_utc"))
    if response:
        _print_kv("Executor action", response.get("action"))
        _print_kv("Executor status", "ok" if invocation.get("ok") else "failed")
        _print_kv("Message", response.get("message") or response.get("status"))


def _menu() -> int:
    choices = [
        ("Show Status", "status"),
        ("Activate Watchdog", "activate"),
        ("Deactivate Watchdog", "deactivate"),
        ("Restart Watchdog", "restart"),
        ("Run Audit Now", "audit"),
        ("Run Self-Heal Now", "selfheal"),
        ("Read Recent Actions", "actions"),
        ("Read Recent Log", "log"),
        ("Read Full Activity", "activity"),
        ("Quit", "quit"),
    ]
    while True:
        print("\nWatchdog")
        for idx, (label, _) in enumerate(choices, start=1):
            print(f"  {idx}. {label}")
        selected = input("Select option> ").strip()
        if not selected.isdigit():
            print("Select a number.")
            continue
        idx = int(selected)
        if idx < 1 or idx > len(choices):
            print(f"Select a number between 1 and {len(choices)}.")
            continue
        action = choices[idx - 1][1]
        if action == "quit":
            return 0
        if action == "status":
            _print_status()
        elif action == "activate":
            _run_action("watchdog_enable_timers")
        elif action == "deactivate":
            _run_action("watchdog_disable_timers")
        elif action == "restart":
            _run_action("watchdog_restart_timers")
        elif action == "audit":
            _run_action("watchdog_step_audit")
        elif action == "selfheal":
            _run_action("watchdog_step_selfheal")
        elif action == "actions":
            _print_recent_actions()
        elif action == "log":
            _print_recent_log()
        elif action == "activity":
            _print_activity()


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Menu-driven watchdog operator helper.")
    ap.add_argument(
        "command",
        nargs="?",
        choices=["status", "activate", "deactivate", "restart", "audit", "selfheal", "actions", "log", "activity"],
        help="Optional direct command. Omit to open the numbered menu.",
    )
    return ap.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.command:
        return _menu()
    command = str(args.command)
    if command == "status":
        _print_status()
    elif command == "activate":
        _run_action("watchdog_enable_timers")
    elif command == "deactivate":
        _run_action("watchdog_disable_timers")
    elif command == "restart":
        _run_action("watchdog_restart_timers")
    elif command == "audit":
        _run_action("watchdog_step_audit")
    elif command == "selfheal":
        _run_action("watchdog_step_selfheal")
    elif command == "actions":
        _print_recent_actions()
    elif command == "log":
        _print_recent_log()
    else:
        _print_activity()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
