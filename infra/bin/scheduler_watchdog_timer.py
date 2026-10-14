#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
UNIT_PREFIX = "causyl-scheduler-watchdog"


def _unit_paths() -> Dict[str, Path]:
    return {
        "audit_service": SYSTEMD_USER_DIR / f"{UNIT_PREFIX}-audit.service",
        "audit_timer": SYSTEMD_USER_DIR / f"{UNIT_PREFIX}-audit.timer",
        "selfheal_service": SYSTEMD_USER_DIR / f"{UNIT_PREFIX}-selfheal.service",
        "selfheal_timer": SYSTEMD_USER_DIR / f"{UNIT_PREFIX}-selfheal.timer",
    }


def _script(name: str) -> str:
    return str((REPO_ROOT / "tools" / "bin" / name).resolve())


def _service_unit(*, description: str, script_name: str) -> str:
    return f"""[Unit]
Description={description}
After=default.target

[Service]
Type=oneshot
WorkingDirectory={REPO_ROOT}
ExecStart={_script(script_name)}
"""


def _timer_unit(*, description: str, service_name: str, on_calendar: str) -> str:
    return f"""[Unit]
Description={description}

[Timer]
OnCalendar={on_calendar}
Persistent=true
Unit={service_name}

[Install]
WantedBy=timers.target
"""


def _write_units() -> None:
    paths = _unit_paths()
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    paths["audit_service"].write_text(
        _service_unit(
            description="Causyl scheduler watchdog audit",
            script_name="scheduler_watchdog_audit.sh",
        ),
        encoding="utf-8",
    )
    paths["selfheal_service"].write_text(
        _service_unit(
            description="Causyl scheduler watchdog self-heal",
            script_name="scheduler_watchdog_selfheal.sh",
        ),
        encoding="utf-8",
    )
    paths["audit_timer"].write_text(
        _timer_unit(
            description="Run Causyl scheduler watchdog audit every 15 minutes",
            service_name=paths["audit_service"].name,
            on_calendar="*:0/15",
        ),
        encoding="utf-8",
    )
    paths["selfheal_timer"].write_text(
        _timer_unit(
            description="Run Causyl scheduler watchdog self-heal every 15 minutes offset by 7",
            service_name=paths["selfheal_service"].name,
            on_calendar="*:7/15",
        ),
        encoding="utf-8",
    )


def _systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=check,
    )


def _systemctl_show(unit: str, *, properties: Iterable[str]) -> Dict[str, str]:
    result = _systemctl("show", unit, "--property", ",".join(properties), check=False)
    out: Dict[str, str] = {}
    for raw in str(result.stdout or "").splitlines():
        if "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        out[str(k).strip()] = str(v).strip()
    return out


def _systemd_usec_to_utc(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or raw == "0":
        return ""
    if re.fullmatch(r"\d+", raw):
        try:
            ts = dt.datetime.fromtimestamp(int(raw) / 1_000_000, tz=dt.timezone.utc)
        except Exception:
            return ""
        return ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    human = re.match(r"^[A-Za-z]{3}\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+([A-Za-z/_+-]+)$", raw)
    if not human:
        return ""
    naive_text = str(human.group(1) or "").strip()
    tz_label = str(human.group(2) or "").strip()
    try:
        naive = dt.datetime.strptime(naive_text, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""
    if ZoneInfo is not None and tz_label in {"CET", "CEST"}:
        aware = naive.replace(tzinfo=ZoneInfo("Europe/Vienna"))
        return aware.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return ""


def timer_status_snapshot() -> Dict[str, object]:
    paths = _unit_paths()
    units = {
        "audit_timer": f"{UNIT_PREFIX}-audit.timer",
        "audit_service": f"{UNIT_PREFIX}-audit.service",
        "selfheal_timer": f"{UNIT_PREFIX}-selfheal.timer",
        "selfheal_service": f"{UNIT_PREFIX}-selfheal.service",
    }
    installed = all(path.exists() for path in paths.values())
    props = ("Id", "ActiveState", "SubState", "UnitFileState", "Result", "NextElapseUSecRealtime", "LastTriggerUSec")
    snapshot: Dict[str, object] = {
        "schema_version": "scheduler_watchdog_timer_status_v1",
        "installed": bool(installed),
        "units": {},
    }
    units_out: Dict[str, object] = {}
    for key, unit in units.items():
        show = _systemctl_show(unit, properties=props)
        units_out[key] = {
            "unit": unit,
            "path": str(paths.get(key) or ""),
            "present": bool((paths.get(key) or Path()).exists()),
            "active_state": str(show.get("ActiveState") or ""),
            "sub_state": str(show.get("SubState") or ""),
            "unit_file_state": str(show.get("UnitFileState") or ""),
            "result": str(show.get("Result") or ""),
            "next_elapse_utc": _systemd_usec_to_utc(str(show.get("NextElapseUSecRealtime") or "")),
            "last_trigger_utc": _systemd_usec_to_utc(str(show.get("LastTriggerUSec") or "")),
        }
    snapshot["units"] = units_out
    return snapshot


def invoke_action(action: str) -> Dict[str, object]:
    safe = str(action or "").strip().lower()
    if safe not in {"install", "enable", "disable", "start", "stop", "restart", "uninstall", "step_audit", "step_selfheal"}:
        raise ValueError("unsupported watchdog timer action")
    if safe == "install":
        install_units()
        return {"ok": True, "action": safe, "status": timer_status_snapshot()}
    if safe == "enable":
        enable_units()
        return {"ok": True, "action": safe, "status": timer_status_snapshot()}
    if safe == "disable":
        disable_units()
        return {"ok": True, "action": safe, "status": timer_status_snapshot()}
    if safe == "start":
        start_units()
        return {"ok": True, "action": safe, "status": timer_status_snapshot()}
    if safe == "stop":
        stop_units()
        return {"ok": True, "action": safe, "status": timer_status_snapshot()}
    if safe == "restart":
        restart_units()
        return {"ok": True, "action": safe, "status": timer_status_snapshot()}
    if safe == "uninstall":
        uninstall_units()
        return {"ok": True, "action": safe, "status": timer_status_snapshot()}
    which = "audit" if safe == "step_audit" else "selfheal"
    rc = step(which)
    return {
        "ok": rc == 0,
        "action": safe,
        "returncode": int(rc),
        "status": timer_status_snapshot(),
    }


def install_units() -> None:
    _write_units()
    _systemctl("daemon-reload")


def enable_units() -> None:
    _systemctl("enable", "--now", f"{UNIT_PREFIX}-audit.timer", f"{UNIT_PREFIX}-selfheal.timer")


def disable_units() -> None:
    _systemctl("disable", "--now", f"{UNIT_PREFIX}-audit.timer", f"{UNIT_PREFIX}-selfheal.timer")


def start_units() -> None:
    _systemctl("start", f"{UNIT_PREFIX}-audit.timer", f"{UNIT_PREFIX}-selfheal.timer")


def stop_units() -> None:
    _systemctl("stop", f"{UNIT_PREFIX}-audit.timer", f"{UNIT_PREFIX}-selfheal.timer")


def restart_units() -> None:
    _systemctl("restart", f"{UNIT_PREFIX}-audit.timer", f"{UNIT_PREFIX}-selfheal.timer")


def status_units() -> int:
    result = _systemctl(
        "status",
        f"{UNIT_PREFIX}-audit.timer",
        f"{UNIT_PREFIX}-selfheal.timer",
        check=False,
    )
    sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return int(result.returncode)


def step(which: str) -> int:
    script = {
        "audit": _script("scheduler_watchdog_audit.sh"),
        "selfheal": _script("scheduler_watchdog_selfheal.sh"),
    }[which]
    return subprocess.run([script], cwd=str(REPO_ROOT), check=False).returncode


def uninstall_units() -> None:
    disable_units()
    for path in _unit_paths().values():
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    _systemctl("daemon-reload")


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Manage plain host timers for scheduler watchdog scripts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install")
    sub.add_parser("enable")
    sub.add_parser("disable")
    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("restart")
    sub.add_parser("status")
    sub.add_parser("uninstall")
    sub.add_parser("step-audit")
    sub.add_parser("step-selfheal")
    return ap.parse_args()


def main() -> int:
    args = _parse_args()
    cmd = str(args.cmd)
    if cmd == "install":
        install_units()
        return 0
    if cmd == "enable":
        enable_units()
        return 0
    if cmd == "disable":
        disable_units()
        return 0
    if cmd == "start":
        start_units()
        return 0
    if cmd == "stop":
        stop_units()
        return 0
    if cmd == "restart":
        restart_units()
        return 0
    if cmd == "status":
        return status_units()
    if cmd == "uninstall":
        uninstall_units()
        return 0
    if cmd == "step-audit":
        return step("audit")
    if cmd == "step-selfheal":
        return step("selfheal")
    raise SystemExit(f"unknown cmd: {cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
