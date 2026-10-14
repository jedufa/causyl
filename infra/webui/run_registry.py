"""infra.webui.run_registry

Persistent registry for WebUI-managed runs.

This module is *operational metadata* only. It must not influence
simulation determinism or checkpoint contents.

Registry format (v1)
--------------------
Stored at: <repo_root>/runtime/state/webui/registry/webui_registry.json

{
  "schema_version": "webui_registry_v1",
  "updated_at_utc": "...",
  "runs": {
     "<run_key>": {
        "run_key": "...",
        "run_dir": "...",
        "origin": "webui",
        "state": "PLANNED|STARTING|RUNNING|HUNG|STOPPED|FAILED",
        "pid": 1234,
        "created_at_utc": "...",
        "started_at_utc": "...",
        "argv_hash": "sha256:...",
        "message": "..."
     }
  }
}

All writes are atomic (temp file + os.replace).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
import json
import os
import tempfile
import time


def _now_utc_iso() -> str:
    """Return current UTC time in ISO8601 format with 'Z'."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _atomic_write_json(path: Path, obj: Dict[str, Any]) -> None:
    """Write JSON atomically to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass


def load_registry(registry_path: Path) -> Dict[str, Any]:
    """Load registry JSON; return empty v1 doc if missing/corrupt."""
    if not registry_path.exists():
        return {"schema_version": "webui_registry_v1", "updated_at_utc": _now_utc_iso(), "runs": {}}
    try:
        obj = json.loads(registry_path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("registry must be dict")
        if obj.get("schema_version") != "webui_registry_v1":
            # tolerate older/unknown by resetting
            return {"schema_version": "webui_registry_v1", "updated_at_utc": _now_utc_iso(), "runs": {}}
        if not isinstance(obj.get("runs"), dict):
            obj["runs"] = {}
        return obj
    except Exception:
        return {"schema_version": "webui_registry_v1", "updated_at_utc": _now_utc_iso(), "runs": {}}


def save_registry(registry_path: Path, obj: Dict[str, Any]) -> None:
    """Save registry JSON atomically."""
    if not isinstance(obj, dict):
        raise ValueError("registry obj must be dict")
    obj["schema_version"] = "webui_registry_v1"
    obj["updated_at_utc"] = _now_utc_iso()
    if not isinstance(obj.get("runs"), dict):
        obj["runs"] = {}
    _atomic_write_json(registry_path, obj)


def upsert_run(
    registry_path: Path,
    *,
    run_key: str,
    run_dir: str,
    origin: str = "webui",
    state: str = "PLANNED",
    pid: Optional[int] = None,
    argv_hash: Optional[str] = None,
    message: Optional[str] = None,
    created_at_utc: Optional[str] = None,
    started_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Create or update a run record."""
    reg = load_registry(registry_path)
    runs = reg.get("runs")
    if not isinstance(runs, dict):
        runs = {}
        reg["runs"] = runs

    rec = runs.get(run_key)
    if not isinstance(rec, dict):
        rec = {}

    rec.update(
        {
            "run_key": str(run_key),
            "run_dir": str(run_dir),
            "origin": str(origin),
            "state": str(state),
        }
    )
    if created_at_utc:
        rec["created_at_utc"] = str(created_at_utc)
    if "created_at_utc" not in rec:
        rec["created_at_utc"] = _now_utc_iso()

    if started_at_utc is not None:
        rec["started_at_utc"] = started_at_utc

    if pid is not None:
        rec["pid"] = int(pid)

    if argv_hash is not None:
        rec["argv_hash"] = str(argv_hash)

    if message is not None:
        rec["message"] = str(message)

    runs[run_key] = rec
    save_registry(registry_path, reg)
    return rec


def set_state(
    registry_path: Path,
    *,
    run_key: str,
    state: str,
    pid: Optional[int] = None,
    message: Optional[str] = None,
    started_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Set state of an existing run (creates record if missing)."""
    reg = load_registry(registry_path)
    runs = reg.get("runs")
    if not isinstance(runs, dict):
        runs = {}
        reg["runs"] = runs
    rec = runs.get(run_key)
    if not isinstance(rec, dict):
        rec = {"run_key": str(run_key), "run_dir": "", "origin": "webui"}

    rec["state"] = str(state)
    if pid is not None:
        rec["pid"] = int(pid)
    if message is not None:
        rec["message"] = str(message)
    if started_at_utc is not None:
        rec["started_at_utc"] = str(started_at_utc)

    runs[run_key] = rec
    save_registry(registry_path, reg)
    return rec
