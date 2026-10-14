"""infra.webui.run_launcher

Detached launcher for WebUI-managed runs.

Why this exists
---------------
The web server should not keep subprocess handles alive to manage runs.
Instead it writes a deterministic *argv array* in ui/start_cmd.json,
then this launcher reads it and spawns the simulation in a detached
session.

This module is operational plane runtime only. It must not influence
simulation determinism.

Files written under the run directory (operational only)
-------------------------------------------------------
- ui/pid.txt
- ui/status.json
- logs/runner.log

"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time


def _now_utc_iso() -> str:
    """Return current UTC time in ISO8601 format with 'Z'."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path: Path) -> Any:
    """Read JSON file, returning None on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    """Write JSON with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _select_sim_root(run_root: Path) -> Path:
    """Select sim_root for operational status writes.

    Some runs are payload-wrapped (artifacts live under run_root/payload).
    This selection is for *operational UI status* only.
    """
    pr = run_root / "payload"
    try:
        if pr.exists() and pr.is_dir():
            # If payload contains any sim markers, prefer it.
            for m in (pr / "viz", pr / "checkpoint_bundles_v2", pr / "run_status.json", pr / "metrics.csv"):
                if m.exists():
                    return pr
    except Exception:
        pass
    return run_root


def _write_run_status(sim_root: Path, *, state: str, message: str, pid: int | None = None) -> None:
    """Write a minimal run_status.json for UI consumption.

    This is operational-only and must not affect determinism.
    """
    viz = sim_root / "viz"
    viz.mkdir(parents=True, exist_ok=True)
    obj: Dict[str, Any] = {
        "schema_version": "run_status_v1",
        "utc": _now_utc_iso(),
        "status": state,
        "branch_state": state,
        "message": message,
        "last_update_timestamp": _now_utc_iso(),
    }
    if pid is not None:
        obj["pid"] = int(pid)
    _write_json(viz / "run_status.json", obj)


def _argv_hash(argv: List[str]) -> str:
    """Return sha256 hash of argv tokens."""
    b = "\0".join([str(x) for x in argv]).encode("utf-8", errors="replace")
    return "sha256:" + hashlib.sha256(b).hexdigest()


def main(argv: List[str] | None = None) -> int:
    """Main entrypoint."""
    p = argparse.ArgumentParser(description="WebUI detached run launcher")
    p.add_argument("--run_dir", required=True, help="Absolute path to run directory")
    p.add_argument("--repo_root", default="", help="Repo root to set as cwd (optional)")
    args = p.parse_args(argv)

    run_dir = Path(args.run_dir).expanduser().resolve()
    sim_root = _select_sim_root(run_dir)
    ui_dir = run_dir / "ui"
    logs_dir = run_dir / "logs"
    start_cmd_path = ui_dir / "start_cmd.json"

    if not start_cmd_path.exists():
        raise SystemExit(f"missing start_cmd.json: {start_cmd_path}")

    obj = _read_json(start_cmd_path)
    if not isinstance(obj, dict):
        raise SystemExit("start_cmd.json must be a JSON object")

    argv_tokens = obj.get("argv")
    if not isinstance(argv_tokens, list) or not argv_tokens:
        raise SystemExit("start_cmd.json missing argv")

    # No shell execution: argv list only.
    argv_tokens = [str(x) for x in argv_tokens]

    # Force current interpreter for reliability.
    if argv_tokens[0] == "python":
        argv_tokens[0] = sys.executable

    env_add = obj.get("env_add") if isinstance(obj.get("env_add"), dict) else {}
    cwd = str(obj.get("cwd") or "")

    # Log file.
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "runner.log"

    # Prepare env.
    env = os.environ.copy()
    for k, v in env_add.items():
        env[str(k)] = str(v)

    # Status file before spawn.
    _write_json(
        ui_dir / "status.json",
        {
            "schema_version": "webui_run_status_v1",
            "utc": _now_utc_iso(),
            "state": "STARTING",
            "argv": argv_tokens,
            "argv_hash": _argv_hash(argv_tokens),
            "message": "launcher starting",
        },
    )

    # Operational status for overview cards.
    _write_run_status(sim_root, state="STARTING", message="launcher starting")

    f = open(log_path, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            argv_tokens,
            cwd=(cwd or (args.repo_root or str(run_dir))),
            env=env,
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        try:
            f.close()
        except Exception:
            pass

    ui_dir.mkdir(parents=True, exist_ok=True)
    (ui_dir / "pid.txt").write_text(str(int(proc.pid)) + "\n", encoding="utf-8")
    _write_json(
        ui_dir / "status.json",
        {
            "schema_version": "webui_run_status_v1",
            "utc": _now_utc_iso(),
            "state": "RUNNING",
            "pid": int(proc.pid),
            "argv_hash": _argv_hash(argv_tokens),
            "log_path": str(log_path),
        },
    )

    _write_run_status(sim_root, state="RUNNING", message="process started", pid=int(proc.pid))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
