from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.controlplane import scheduled_jobs as sched


def _runtime_dir(repo_root: Path) -> Path:
    return (repo_root / "runtime" / "state" / "webui").resolve()


def pause_manifest_path(*, repo_root: Optional[Path] = None) -> Path:
    root = Path(repo_root or sched._repo_root()).resolve()
    return (_runtime_dir(root) / "scheduler_pause_state.json").resolve()


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _state_for(job_id: str, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(sched._state_path(job_id, repo_root=repo_root))


def _lock_max_runtime_seconds(job_id: str, *, repo_root: Path) -> int:
    spec = sched.load_job(job_id, repo_root=repo_root) if job_id else {}
    policy = spec.get("policy") if isinstance(spec.get("policy"), dict) else {}
    try:
        return max(60, int(policy.get("max_runtime_seconds") or 1800))
    except Exception:
        return 1800


def _lock_health(lock_path: Path, row: Dict[str, Any], *, repo_root: Path) -> Dict[str, Any]:
    job_id = str(row.get("job_id") or lock_path.stem.replace(".lock", ""))
    pid = int(row.get("pid") or 0)
    stale_reason = ""
    if pid > 0:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            stale_reason = "dead_pid"
    if not stale_reason:
        max_runtime_seconds = _lock_max_runtime_seconds(job_id, repo_root=repo_root)
        try:
            if bool(sched._claim_is_stale(lock_path, max_runtime_seconds=max_runtime_seconds)):
                stale_reason = "stale_heartbeat"
        except Exception:
            stale_reason = "invalid_claim_state"
    return {
        "path": str(lock_path),
        "job_id": job_id,
        "claimed_utc": str(row.get("claimed_utc") or ""),
        "heartbeat_utc": str(row.get("heartbeat_utc") or ""),
        "stale": bool(stale_reason),
        "stale_reason": stale_reason,
    }


def job_lock_snapshot(*, repo_root: Optional[Path] = None) -> Dict[str, List[Dict[str, Any]]]:
    root = Path(repo_root or sched._repo_root()).resolve()
    active: List[Dict[str, Any]] = []
    stale: List[Dict[str, Any]] = []
    for lock_path in sorted(sched.locks_dir(root).glob("*.lock.json")):
        row = _load_json(lock_path)
        if not row:
            continue
        lock = _lock_health(lock_path, row, repo_root=root)
        if lock["stale"]:
            stale.append(lock)
        else:
            active.append(lock)
    return {
        "active_locks": active,
        "stale_locks": stale,
    }


def active_job_locks(*, repo_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in list(job_lock_snapshot(repo_root=repo_root).get("active_locks") or []):
        out.append(
            {
                "path": str(row.get("path") or ""),
                "job_id": str(row.get("job_id") or ""),
                "claimed_utc": str(row.get("claimed_utc") or ""),
                "heartbeat_utc": str(row.get("heartbeat_utc") or ""),
            }
        )
    return out


def unfinished_enabled_jobs(*, repo_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = Path(repo_root or sched._repo_root()).resolve()
    rows: List[Dict[str, Any]] = []
    for spec_path in sorted(sched.jobs_dir(root).glob("*.json")):
        spec = _load_json(spec_path)
        if not spec:
            continue
        job_id = str(spec.get("job_id") or spec_path.stem)
        enabled = bool(spec.get("enabled", True))
        state = _state_for(job_id, repo_root=root)
        last_status = str(state.get("last_status") or "")
        if enabled and last_status != "ok":
            rows.append(
                {
                    "job_id": job_id,
                    "path": str(spec_path),
                    "last_status": last_status,
                }
            )
    return rows


def pause_unfinished_jobs(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo_root or sched._repo_root()).resolve()
    existing_manifest = load_pause_manifest(repo_root=root)
    paused: List[str] = []
    for row in unfinished_enabled_jobs(repo_root=root):
        sched.set_job_enabled(str(row["job_id"]), False, repo_root=root)
        paused.append(str(row["job_id"]))
    combined: List[str] = []
    for job_id in list(existing_manifest.get("paused_job_ids") or []) + paused:
        txt = str(job_id or "").strip()
        if txt and txt not in combined:
            combined.append(txt)
    manifest = {
        "schema_version": "scheduler_pause_state_v1",
        "paused_job_ids": combined,
    }
    _write_json(pause_manifest_path(repo_root=root), manifest)
    return {
        "paused_job_ids": paused,
        "paused_count": len(paused),
        "manifest_paused_job_ids": combined,
        "manifest_path": str(pause_manifest_path(repo_root=root)),
    }


def load_pause_manifest(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo_root or sched._repo_root()).resolve()
    data = _load_json(pause_manifest_path(repo_root=root))
    paused_job_ids = data.get("paused_job_ids")
    if not isinstance(paused_job_ids, list):
        paused_job_ids = []
    out: List[str] = []
    for item in paused_job_ids:
        txt = str(item or "").strip()
        if txt and txt not in out:
            out.append(txt)
    return {
        "schema_version": "scheduler_pause_state_v1",
        "paused_job_ids": out,
        "manifest_path": str(pause_manifest_path(repo_root=root)),
        "exists": pause_manifest_path(repo_root=root).exists(),
    }


def resume_paused_jobs(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo_root or sched._repo_root()).resolve()
    manifest = load_pause_manifest(repo_root=root)
    resumed: List[str] = []
    skipped_ok: List[str] = []
    skipped_missing: List[str] = []
    for job_id in manifest["paused_job_ids"]:
        spec_path = sched._job_path(job_id, repo_root=root)
        if not spec_path.exists():
            skipped_missing.append(job_id)
            continue
        state = _state_for(job_id, repo_root=root)
        if str(state.get("last_status") or "") == "ok":
            skipped_ok.append(job_id)
            continue
        sched.set_job_enabled(job_id, True, repo_root=root)
        resumed.append(job_id)
    manifest_path = pause_manifest_path(repo_root=root)
    if manifest_path.exists():
        manifest_path.unlink()
    return {
        "resumed_job_ids": resumed,
        "resumed_count": len(resumed),
        "skipped_ok_job_ids": skipped_ok,
        "skipped_missing_job_ids": skipped_missing,
    }


def status_snapshot(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo_root or sched._repo_root()).resolve()
    lock_snapshot = job_lock_snapshot(repo_root=root)
    return {
        "active_locks": lock_snapshot.get("active_locks") or [],
        "stale_locks": lock_snapshot.get("stale_locks") or [],
        "unfinished_enabled_jobs": unfinished_enabled_jobs(repo_root=root),
        "pause_manifest": load_pause_manifest(repo_root=root),
    }
