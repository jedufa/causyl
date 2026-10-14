"""Hosted durability baseline for Lab Assistant v1 (ADD9).

This module intentionally owns only the hosted durability envelope:
- persistent storage surface inventory
- backup/export and restore/import contract
- boot-time host classification
- minimal host/storage health reporting

It does not define assistant identity, task-register semantics, fresh-turn
bootstrap, delegation policy, or assistant-level recovery reconciliation.
Those belong to later child PIS items.
"""

from __future__ import annotations

import atexit
import datetime as _dt
import hashlib
import json
import os
import shutil
import socket
import stat
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover
    _fcntl = None  # type: ignore[assignment]


_LOCK = threading.RLock()
_REGISTERED_SHUTDOWN_ROOTS: set[str] = set()

_SURFACES: tuple[Dict[str, Any], ...] = (
    {
        "key": "ledger_state",
        "relpath": "governance/ledger",
        "required": True,
        "reserved": False,
        "exclude_prefixes": (),
    },
    {
        "key": "agent_runtime",
        "relpath": "governance/agents/runtime",
        "required": True,
        "reserved": False,
        "exclude_prefixes": (),
    },
    {
        "key": "host_bundle",
        "relpath": "runtime/state/lab_assistant/host",
        "required": True,
        "reserved": False,
        "exclude_prefixes": ("runtime/state/lab_assistant/host/exports",),
    },
    {
        "key": "assistant_state",
        "relpath": "runtime/state/lab_assistant/assistant_state",
        "required": False,
        "reserved": True,
        "exclude_prefixes": (),
    },
    {
        "key": "task_register",
        "relpath": "runtime/state/lab_assistant/task_register",
        "required": False,
        "reserved": True,
        "exclude_prefixes": (),
    },
)


def _utc_now_iso() -> str:
    """Return the current UTC timestamp in stable Zulu format."""
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_compact_stamp() -> str:
    """Return a filesystem-safe UTC timestamp."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _host_root(repo_root: Path) -> Path:
    """Return the canonical ADD9 host state root."""
    return (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "host").resolve()


def _exports_root(repo_root: Path) -> Path:
    """Return the ADD9 export root."""
    return (_host_root(repo_root) / "exports").resolve()


def _host_state_path(repo_root: Path) -> Path:
    """Return the persisted host state snapshot path."""
    return (_host_root(repo_root) / "host_state.json").resolve()


def _boot_receipt_path(repo_root: Path) -> Path:
    """Return the latest boot classification receipt path."""
    return (_host_root(repo_root) / "boot_receipt.json").resolve()


def _restore_receipt_path(repo_root: Path) -> Path:
    """Return the latest restore receipt path."""
    return (_host_root(repo_root) / "restore_receipt.json").resolve()


def _boot_events_path(repo_root: Path) -> Path:
    """Return the append-only boot event log path."""
    return (_host_root(repo_root) / "boot_events.jsonl").resolve()


def _local_host_marker_path(repo_root: Path) -> Path:
    """Return the non-exported local-host marker path."""
    return (Path(repo_root).resolve() / "runtime" / "state" / "local" / "lab_assistant_host_id.txt").resolve()


def _read_json(path: Path) -> Dict[str, Any]:
    """Read a JSON object from disk or return an empty dict."""
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    """Atomically write canonical JSON plus trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tmp = (path.parent / f".{path.name}.tmp").resolve()
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(raw)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    """Append one canonical JSON row to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def _sha256_file(path: Path) -> str:
    """Compute a streaming SHA256 digest for one file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_json(obj: Dict[str, Any]) -> str:
    """Compute SHA256 of canonical JSON bytes."""
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _manifest_sha256(manifest: Dict[str, Any]) -> str:
    """Compute the canonical manifest hash excluding the hash field itself."""
    obj = dict(manifest or {})
    obj.pop("manifest_sha256", None)
    return _sha256_json(obj)


def _parse_iso_utc(value: Any) -> Optional[_dt.datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _age_seconds(*, now: _dt.datetime, value: Any) -> Optional[int]:
    parsed = _parse_iso_utc(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds()))


def _assistant_state_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "assistant_state").resolve()


def _assistant_state_json_path(repo_root: Path) -> Path:
    return (_assistant_state_root(repo_root) / "assistant_state.json").resolve()


def _assistant_identity_json_path(repo_root: Path) -> Path:
    return (_assistant_state_root(repo_root) / "identity.json").resolve()


def _task_register_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "task_register").resolve()


def _task_register_json_path(repo_root: Path) -> Path:
    return (_task_register_root(repo_root) / "task_register.json").resolve()


def _file_sha256_or_empty(path: Path) -> str:
    return _sha256_file(path) if path.exists() and path.is_file() else ""


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _durable_truth_snapshot(repo_root: Path) -> Dict[str, Any]:
    assistant_state = _read_json(_assistant_state_json_path(repo_root))
    identity_state = _read_json(_assistant_identity_json_path(repo_root))
    task_register = _read_json(_task_register_json_path(repo_root))
    tasks = list(task_register.get("tasks") or []) if isinstance(task_register, dict) else []
    return {
        "schema_version": "lab_assistant_host_durable_truth_snapshot_v1",
        "captured_utc": _utc_now_iso(),
        "assistant_id": str(identity_state.get("assistant_id") or assistant_state.get("assistant_id") or "").strip(),
        "assistant_state_relpath": _assistant_state_json_path(repo_root).resolve().relative_to(Path(repo_root).resolve()).as_posix(),
        "assistant_state_sha256": _file_sha256_or_empty(_assistant_state_json_path(repo_root)),
        "assistant_identity_relpath": _assistant_identity_json_path(repo_root).resolve().relative_to(Path(repo_root).resolve()).as_posix(),
        "assistant_identity_sha256": _file_sha256_or_empty(_assistant_identity_json_path(repo_root)),
        "task_register_relpath": _task_register_json_path(repo_root).resolve().relative_to(Path(repo_root).resolve()).as_posix(),
        "task_register_sha256": _file_sha256_or_empty(_task_register_json_path(repo_root)),
        "task_register_write_generation": _coerce_int(task_register.get("write_generation") if isinstance(task_register, dict) else 0, 0),
        "task_count": len(tasks),
    }


def _restore_context_snapshot(restore_receipt: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(restore_receipt, dict) or not restore_receipt:
        return {}
    now = _dt.datetime.now(_dt.timezone.utc)
    age_s = _age_seconds(now=now, value=restore_receipt.get("source_export_created_utc"))
    partial_restore = bool(restore_receipt.get("partial_restore"))
    summary = (
        "Restore receipt reports a partial restore; durable truth may be incomplete."
        if partial_restore
        else "Restore receipt is present; durable truth came from a backup/export bundle."
    )
    if age_s is not None:
        summary += f" Source backup age: {age_s}s."
    return {
        "source_export_id": str(restore_receipt.get("source_export_id") or "").strip(),
        "source_export_created_utc": str(restore_receipt.get("source_export_created_utc") or "").strip(),
        "source_backup_age_s": age_s,
        "source_manifest_sha256": str(restore_receipt.get("source_manifest_sha256") or "").strip(),
        "partial_restore": partial_restore,
        "source_durable_truth_snapshot": dict(restore_receipt.get("source_durable_truth_snapshot") or {}),
        "summary": summary.strip(),
    }


def _path_writable(path: Path) -> bool:
    """Return whether the path or its nearest existing parent looks writable.

    Use permission bits as a fail-closed gate before consulting ``os.access`` so
    test sandboxes and root-owned local runs still classify chmod'ed read-only
    state as blocked storage instead of silently treating it as writable.
    """
    current = Path(path).resolve()
    while True:
        if current.exists():
            try:
                mode = current.stat().st_mode
            except OSError:
                return False
            if (mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)) == 0:
                return False
            return os.access(current, os.W_OK)
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _safe_target_path(base: Path, relpath: str) -> Path:
    """Resolve one manifest relpath safely under a target root."""
    target = (Path(base).resolve() / Path(relpath)).resolve()
    target.relative_to(Path(base).resolve())
    return target


def _ensure_local_host_id(repo_root: Path) -> str:
    """Return the local host id, creating it if needed."""
    path = _local_host_marker_path(repo_root)
    if path.exists() and path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return text
    value = f"{socket.gethostname().strip() or 'host'}:{uuid.uuid4().hex}"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(value)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return value


def _ensure_host_storage(repo_root: Path) -> Dict[str, str]:
    """Create only the local ADD9 storage scaffolding without classifying a boot."""
    host_root = _host_root(repo_root)
    host_root.mkdir(parents=True, exist_ok=True)
    return {
        "host_root": str(host_root),
        "local_host_id": _ensure_local_host_id(repo_root),
    }


def ensure_host_storage(repo_root: Path) -> Dict[str, str]:
    """Public wrapper that creates only the local ADD9 storage scaffolding."""
    with _LOCK:
        return _ensure_host_storage(Path(repo_root).resolve())


def _iter_surface_files(repo_root: Path, surface: Dict[str, Any]) -> List[Path]:
    """Return the exportable files that belong to one surface."""
    base = (Path(repo_root).resolve() / str(surface.get("relpath") or "")).resolve()
    if not base.exists() or not base.is_dir():
        return []
    exclude_prefixes = tuple(str(v) for v in surface.get("exclude_prefixes") or ())
    out: List[Path] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.resolve().relative_to(Path(repo_root).resolve()).as_posix()
        blocked = False
        for prefix in exclude_prefixes:
            px = str(prefix or "").strip().strip("/")
            if px and (rel == px or rel.startswith(px + "/")):
                blocked = True
                break
        if not blocked:
            out.append(path.resolve())
    return out


def _collect_surface_statuses(repo_root: Path) -> List[Dict[str, Any]]:
    """Collect presence and writeability for tracked ADD9 surfaces."""
    repo_root = Path(repo_root).resolve()
    rows: List[Dict[str, Any]] = []
    for surface in _SURFACES:
        relpath = str(surface.get("relpath") or "")
        path = (repo_root / relpath).resolve()
        present = bool(path.exists())
        rows.append(
            {
                "key": str(surface.get("key") or ""),
                "relpath": relpath,
                "required": bool(surface.get("required")),
                "reserved": bool(surface.get("reserved")),
                "present": present,
                "writable": _path_writable(path if present else path.parent),
                "is_dir": bool(path.is_dir()) if present else False,
            }
        )
    return rows


def _compute_boot_mode(
    *,
    state: Dict[str, Any],
    restore_receipt: Dict[str, Any],
    surfaces: List[Dict[str, Any]],
    local_host_id: str,
) -> tuple[str, str, List[str]]:
    """Classify the current host/storage state for ADD9."""
    missing_required = [str(row["key"]) for row in surfaces if bool(row.get("required")) and (not bool(row.get("present")))]
    readonly_required = [str(row["key"]) for row in surfaces if bool(row.get("required")) and (not bool(row.get("writable")))]
    reasons: List[str] = []

    if readonly_required:
        reasons.append("read_only:" + ",".join(sorted(readonly_required)))
        return "blocked_storage_readonly", "Persistent storage is not writable.", reasons

    if missing_required and restore_receipt:
        reasons.append("partial_restore_missing:" + ",".join(sorted(missing_required)))
        return "partial_restore_degraded", "Restore is missing required surfaces.", reasons

    if missing_required:
        reasons.append("missing_required:" + ",".join(sorted(missing_required)))
        return "blocked_storage_missing", "Required persistent surfaces are missing.", reasons

    previous_local_host_id = str(state.get("last_seen_local_host_id") or "").strip()
    restore_hash = str(restore_receipt.get("source_manifest_sha256") or "").strip()
    applied_restore_hash = str(state.get("last_applied_restore_sha256") or "").strip()

    if bool(restore_receipt.get("partial_restore")):
        reasons.append("restore_receipt_partial")
        return "partial_restore_degraded", "Restore receipt reports a partial restore.", reasons

    if restore_hash and restore_hash != applied_restore_hash:
        reasons.append("restore_receipt_unapplied")
        return "restored_host", "Restored state was detected on this host.", reasons

    if previous_local_host_id and previous_local_host_id != local_host_id:
        reasons.append("local_host_changed")
        return "restored_host", "Persistent state moved to a different host.", reasons

    if bool(state.get("boot_in_progress")) or (
        str(state.get("last_boot_id") or "").strip() and (not bool(state.get("last_shutdown_clean", True)))
    ):
        reasons.append("previous_boot_unclean")
        return "interrupted_restart", "Previous boot did not end cleanly.", reasons

    return "clean_boot", "Persistent state is present and boot continuity is clean.", reasons


def _status_from_mode(mode: str) -> str:
    """Map boot mode to a compact health status."""
    value = str(mode or "").strip().lower()
    if value.startswith("blocked"):
        return "blocked"
    if value == "partial_restore_degraded":
        return "degraded"
    return "ok"


def _live_surface_mode(
    *,
    cached_mode: str,
    restore_receipt: Dict[str, Any],
    surfaces: List[Dict[str, Any]],
) -> tuple[str, str, List[str]]:
    """Derive live health from current surfaces without rewriting boot history."""
    missing_required = [str(row["key"]) for row in surfaces if bool(row.get("required")) and (not bool(row.get("present")))]
    readonly_required = [str(row["key"]) for row in surfaces if bool(row.get("required")) and (not bool(row.get("writable")))]
    reasons: List[str] = []
    if readonly_required:
        reasons.append("read_only:" + ",".join(sorted(readonly_required)))
        return "blocked_storage_readonly", "Persistent storage is not writable.", reasons
    if missing_required and restore_receipt:
        reasons.append("partial_restore_missing:" + ",".join(sorted(missing_required)))
        return "partial_restore_degraded", "Restore is missing required surfaces.", reasons
    if missing_required:
        reasons.append("missing_required:" + ",".join(sorted(missing_required)))
        return "blocked_storage_missing", "Required persistent surfaces are missing.", reasons
    if bool(restore_receipt.get("partial_restore")):
        reasons.append("restore_receipt_partial")
        return "partial_restore_degraded", "Restore receipt reports a partial restore.", reasons
    return cached_mode, "", reasons


def bootstrap_host_baseline(repo_root: Path) -> Dict[str, Any]:
    """Classify and persist the ADD9 host baseline for the current boot."""
    repo_root = Path(repo_root).resolve()
    with _LOCK:
        local_host_id = _ensure_host_storage(repo_root)["local_host_id"]
        state = _read_json(_host_state_path(repo_root))
        restore_receipt = _read_json(_restore_receipt_path(repo_root))
        surfaces = _collect_surface_statuses(repo_root)
        mode, summary, reasons = _compute_boot_mode(
            state=state,
            restore_receipt=restore_receipt,
            surfaces=surfaces,
            local_host_id=local_host_id,
        )
        boot_id = uuid.uuid4().hex
        now = _utc_now_iso()
        install_id = str(state.get("install_id") or "").strip() or uuid.uuid4().hex
        restore_hash = str(restore_receipt.get("source_manifest_sha256") or "").strip()
        receipt = {
            "schema_version": "lab_assistant_host_boot_receipt_v1",
            "boot_id": boot_id,
            "booted_utc": now,
            "install_id": install_id,
            "local_host_id": local_host_id,
            "mode": mode,
            "status": _status_from_mode(mode),
            "blocked": bool(_status_from_mode(mode) == "blocked"),
            "summary": summary,
            "reasons": reasons,
            "surfaces": surfaces,
            "restore_receipt_present": bool(restore_receipt),
            "restore_receipt_path": str(_restore_receipt_path(repo_root)) if restore_receipt else "",
        }
        next_state = {
            "schema_version": "lab_assistant_host_state_v1",
            "install_id": install_id,
            "last_seen_local_host_id": local_host_id,
            "boot_in_progress": True,
            "last_boot_id": boot_id,
            "last_boot_mode": mode,
            "last_boot_utc": now,
            "last_shutdown_clean": False,
            "last_shutdown_utc": str(state.get("last_shutdown_utc") or ""),
            "last_applied_restore_sha256": restore_hash or str(state.get("last_applied_restore_sha256") or ""),
        }
        _write_json_atomic(_host_state_path(repo_root), next_state)
        _write_json_atomic(_boot_receipt_path(repo_root), receipt)
        _append_jsonl(
            _boot_events_path(repo_root),
            {
                "schema_version": "lab_assistant_host_boot_event_v1",
                "event": "boot_classified",
                "utc": now,
                "boot_id": boot_id,
                "mode": mode,
                "status": receipt["status"],
                "reasons": reasons,
            },
        )
        return dict(receipt)


def mark_clean_shutdown(repo_root: Path, *, reason: str = "normal_exit") -> Dict[str, Any]:
    """Mark the current ADD9 host state as a clean shutdown."""
    repo_root = Path(repo_root).resolve()
    with _LOCK:
        state = _read_json(_host_state_path(repo_root))
        if not state:
            return {"ok": False, "reason": "host_state_missing"}
        now = _utc_now_iso()
        state["boot_in_progress"] = False
        state["last_shutdown_clean"] = True
        state["last_shutdown_utc"] = now
        _write_json_atomic(_host_state_path(repo_root), state)
        _append_jsonl(
            _boot_events_path(repo_root),
            {
                "schema_version": "lab_assistant_host_boot_event_v1",
                "event": "shutdown_clean",
                "utc": now,
                "boot_id": str(state.get("last_boot_id") or ""),
                "reason": str(reason or ""),
            },
        )
        return {"ok": True, "shutdown_utc": now}


def register_clean_shutdown(repo_root: Path) -> None:
    """Register one atexit clean-shutdown marker for this repo root."""
    key = str(Path(repo_root).resolve())
    with _LOCK:
        if key in _REGISTERED_SHUTDOWN_ROOTS:
            return
        _REGISTERED_SHUTDOWN_ROOTS.add(key)
    atexit.register(lambda rr=Path(repo_root).resolve(): mark_clean_shutdown(rr, reason="atexit"))


def host_health_snapshot(repo_root: Path, *, initialize_if_missing: bool = True) -> Dict[str, Any]:
    """Return the current ADD9 health snapshot without mutating boot history."""
    repo_root = Path(repo_root).resolve()
    receipt = _read_json(_boot_receipt_path(repo_root))
    if not receipt:
        if initialize_if_missing:
            receipt = bootstrap_host_baseline(repo_root)
        else:
            restore_receipt = _read_json(_restore_receipt_path(repo_root))
            surfaces = _collect_surface_statuses(repo_root)
            mode, summary, live_reasons = _live_surface_mode(
                cached_mode="blocked_storage_missing",
                restore_receipt=restore_receipt,
                surfaces=surfaces,
            )
            if mode == "blocked_storage_missing" and not live_reasons:
                mode = "host_baseline_uninitialized"
                summary = "ADD9 host baseline has not been bootstrapped for this repo."
                live_reasons = ["host_baseline_uninitialized"]
            status = _status_from_mode(mode) if mode != "host_baseline_uninitialized" else "blocked"
            return {
                "schema_version": "lab_assistant_host_health_v1",
                "utc": _utc_now_iso(),
                "status": status,
                "mode": mode,
                "blocked": bool(status == "blocked"),
                "summary": summary,
                "reasons": live_reasons,
                "install_id": "",
                "last_boot_id": "",
                "last_boot_utc": "",
                "last_shutdown_clean": False,
                "restore_receipt_present": bool(restore_receipt),
                "restore_context": _restore_context_snapshot(restore_receipt),
                "surfaces": surfaces,
            }
    state = _read_json(_host_state_path(repo_root))
    restore_receipt = _read_json(_restore_receipt_path(repo_root))
    surfaces = _collect_surface_statuses(repo_root)
    cached_mode = str(receipt.get("mode") or "blocked_storage_missing")
    mode, summary, live_reasons = _live_surface_mode(
        cached_mode=cached_mode,
        restore_receipt=restore_receipt,
        surfaces=surfaces,
    )
    status = _status_from_mode(mode)
    reasons = live_reasons or list(receipt.get("reasons") or [])
    return {
        "schema_version": "lab_assistant_host_health_v1",
        "utc": _utc_now_iso(),
        "status": status,
        "mode": mode,
        "blocked": bool(status == "blocked"),
        "summary": summary or str(receipt.get("summary") or ""),
        "reasons": reasons,
        "install_id": str(state.get("install_id") or receipt.get("install_id") or ""),
        "last_boot_id": str(state.get("last_boot_id") or receipt.get("boot_id") or ""),
        "last_boot_utc": str(state.get("last_boot_utc") or receipt.get("booted_utc") or ""),
        "last_shutdown_clean": bool(state.get("last_shutdown_clean", False)),
        "restore_receipt_present": bool(restore_receipt),
        "restore_context": _restore_context_snapshot(restore_receipt),
        "surfaces": surfaces,
    }


def export_host_baseline(repo_root: Path, *, note: str = "") -> Dict[str, Any]:
    """Export the ADD9 surfaces plus reserved future assistant roots."""
    repo_root = Path(repo_root).resolve()
    with _LOCK:
        _ensure_host_storage(repo_root)
        export_id = f"{_utc_compact_stamp()}__{uuid.uuid4().hex[:8]}"
        export_root = (_exports_root(repo_root) / export_id).resolve()
        payload_root = (export_root / "payload").resolve()
        payload_root.mkdir(parents=True, exist_ok=True)

        surfaces_payload: List[Dict[str, Any]] = []
        files_payload: List[Dict[str, Any]] = []
        errors: List[str] = []
        partial_export = False

        for surface in _SURFACES:
            relpath = str(surface.get("relpath") or "")
            base = (repo_root / relpath).resolve()
            present = bool(base.exists() and base.is_dir())
            files = _iter_surface_files(repo_root, surface) if present else []
            for src in files:
                rel = src.relative_to(repo_root).as_posix()
                dest = (payload_root / rel).resolve()
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                files_payload.append(
                    {
                        "relpath": rel,
                        "surface_key": str(surface.get("key") or ""),
                        "bytes": int(src.stat().st_size),
                        "sha256": _sha256_file(src),
                    }
                )
            if bool(surface.get("required")) and not present:
                partial_export = True
                errors.append(f"missing_required_surface:{surface.get('key')}")
            surfaces_payload.append(
                {
                    "key": str(surface.get("key") or ""),
                    "relpath": relpath,
                    "required": bool(surface.get("required")),
                    "reserved": bool(surface.get("reserved")),
                    "present": present,
                    "file_count": len(files),
                }
            )

        manifest = {
            "schema_version": "lab_assistant_host_export_v1",
            "export_id": export_id,
            "created_utc": _utc_now_iso(),
            "note": str(note or ""),
            "partial_export": partial_export,
            "errors": errors,
            "surfaces": surfaces_payload,
            "files": files_payload,
            "durable_truth_snapshot": _durable_truth_snapshot(repo_root),
        }
        manifest["manifest_sha256"] = _manifest_sha256(manifest)
        manifest_path = (export_root / "manifest.json").resolve()
        _write_json_atomic(manifest_path, manifest)
        return {
            "ok": True,
            "schema_version": "lab_assistant_host_export_v1",
            "utc": manifest["created_utc"],
            "export_id": export_id,
            "manifest_path": str(manifest_path),
            "payload_root": str(payload_root),
            "partial_export": partial_export,
            "errors": errors,
            "file_count": len(files_payload),
            "surface_count": len(surfaces_payload),
        }


def restore_host_baseline(
    *,
    manifest_path: Path,
    target_repo_root: Path,
    note: str = "",
    allow_nonempty_target: bool = False,
) -> Dict[str, Any]:
    """Restore one exported ADD9 bundle into a target repo root."""
    manifest_path = Path(manifest_path).resolve()
    target_repo_root = Path(target_repo_root).resolve()
    manifest = _read_json(manifest_path)
    if str(manifest.get("schema_version") or "") != "lab_assistant_host_export_v1":
        raise ValueError("manifest schema_version must be lab_assistant_host_export_v1")
    claimed_manifest_sha = str(manifest.get("manifest_sha256") or "").strip()
    if not claimed_manifest_sha:
        raise ValueError("manifest_sha256 is required")
    if claimed_manifest_sha != _manifest_sha256(manifest):
        raise ValueError("manifest_sha256 mismatch")
    payload_root = (manifest_path.parent / "payload").resolve()
    if not payload_root.exists() or not payload_root.is_dir():
        raise FileNotFoundError(f"payload root missing: {payload_root}")

    partial_restore = bool(manifest.get("partial_export"))
    errors: List[str] = list(manifest.get("errors") or [])
    missing_files: List[str] = []
    fatal_errors: List[str] = []
    if target_repo_root.exists():
        has_entries = any(target_repo_root.iterdir())
        if has_entries and (not allow_nonempty_target):
            raise ValueError("target_repo_root must be absent or empty")
    stage_parent = target_repo_root.parent.resolve()
    stage_parent.mkdir(parents=True, exist_ok=True)
    stage_root = (stage_parent / f".lab_assistant_restore_stage_{uuid.uuid4().hex}").resolve()
    stage_repo_root = (stage_root / "repo").resolve()
    stage_repo_root.mkdir(parents=True, exist_ok=True)

    for surface in list(manifest.get("surfaces") or []):
        if not isinstance(surface, dict) or not bool(surface.get("present")):
            continue
        relpath = str(surface.get("relpath") or "").strip()
        if not relpath:
            continue
        target_dir = _safe_target_path(stage_repo_root, relpath)
        target_dir.mkdir(parents=True, exist_ok=True)

    restored_count = 0
    for row in list(manifest.get("files") or []):
        if not isinstance(row, dict):
            continue
        rel = str(row.get("relpath") or "").strip()
        if not rel:
            continue
        src = (payload_root / rel).resolve()
        if not src.exists() or not src.is_file():
            partial_restore = True
            missing_files.append(rel)
            errors.append(f"missing_payload_file:{rel}")
            fatal_errors.append(f"missing_payload_file:{rel}")
            continue
        source_sha = str(row.get("sha256") or "").strip()
        if source_sha and _sha256_file(src) != source_sha:
            partial_restore = True
            errors.append(f"payload_sha256_mismatch:{rel}")
            fatal_errors.append(f"payload_sha256_mismatch:{rel}")
            continue
        dest = _safe_target_path(stage_repo_root, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        if source_sha and _sha256_file(dest) != source_sha:
            partial_restore = True
            errors.append(f"sha256_mismatch:{rel}")
            fatal_errors.append(f"sha256_mismatch:{rel}")
            continue
        restored_count += 1

    if fatal_errors:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise ValueError("restore validation failed before target mutation")

    if target_repo_root.exists() and (not any(target_repo_root.iterdir())):
        target_repo_root.rmdir()
    os.replace(stage_repo_root, target_repo_root)
    shutil.rmtree(stage_root, ignore_errors=True)

    restored_utc = _utc_now_iso()
    receipt = {
        "schema_version": "lab_assistant_host_restore_receipt_v1",
        "restored_utc": restored_utc,
        "source_manifest_path": str(manifest_path),
        "source_export_id": str(manifest.get("export_id") or ""),
        "source_export_created_utc": str(manifest.get("created_utc") or ""),
        "source_backup_age_s": _age_seconds(now=_parse_iso_utc(restored_utc) or _dt.datetime.now(_dt.timezone.utc), value=manifest.get("created_utc")),
        "source_manifest_sha256": str(manifest.get("manifest_sha256") or _sha256_file(manifest_path)),
        "source_durable_truth_snapshot": dict(manifest.get("durable_truth_snapshot") or {}),
        "partial_restore": partial_restore,
        "missing_files": missing_files,
        "errors": errors,
        "restored_file_count": restored_count,
        "note": str(note or ""),
    }
    _write_json_atomic(_restore_receipt_path(target_repo_root), receipt)
    return {
        "ok": True,
        "schema_version": "lab_assistant_host_restore_v1",
        "utc": receipt["restored_utc"],
        "target_repo_root": str(target_repo_root),
        "restore_receipt_path": str(_restore_receipt_path(target_repo_root)),
        "source_export_id": receipt["source_export_id"],
        "source_export_created_utc": receipt["source_export_created_utc"],
        "source_backup_age_s": receipt["source_backup_age_s"],
        "source_durable_truth_snapshot": receipt["source_durable_truth_snapshot"],
        "partial_restore": partial_restore,
        "restored_file_count": restored_count,
        "errors": errors,
        "missing_files": missing_files,
    }


def run_backup_restore_host_replacement_drill(
    source_repo_root: Path,
    *,
    target_repo_root: Optional[Path] = None,
    note: str = "",
) -> Dict[str, Any]:
    """Run a focused export -> restore -> replacement-host drill.

    The drill is intentionally bounded to current hosted durability surfaces.
    It does not pretend to prove HA or distributed failover semantics.
    """
    source_repo_root = Path(source_repo_root).resolve()
    target_root = (
        Path(target_repo_root).resolve()
        if target_repo_root is not None
        else (source_repo_root.parent / f"{source_repo_root.name}__restored_{uuid.uuid4().hex[:8]}").resolve()
    )
    source_truth = _durable_truth_snapshot(source_repo_root)
    source_health = host_health_snapshot(source_repo_root, initialize_if_missing=False)
    export = export_host_baseline(source_repo_root, note=note or "host replacement drill")
    restore = restore_host_baseline(
        manifest_path=Path(str(export["manifest_path"])),
        target_repo_root=target_root,
        note=note or "host replacement drill restore",
    )
    target_boot = bootstrap_host_baseline(target_root)
    target_health = host_health_snapshot(target_root, initialize_if_missing=False)
    target_truth = _durable_truth_snapshot(target_root)
    pre_reconcile_scheduler_truth = _scheduler_truth_snapshot(target_root)
    reconciliation_started = _dt.datetime.now(_dt.timezone.utc)
    scheduler_reconciliation = _reconcile_scheduler_truth_after_restore(target_root)
    reconciliation_finished = _dt.datetime.now(_dt.timezone.utc)
    post_reconcile_scheduler_truth = _scheduler_truth_snapshot(target_root)

    mismatches: List[str] = []
    if str(source_truth.get("assistant_id") or "") != str(target_truth.get("assistant_id") or ""):
        mismatches.append("assistant_id_mismatch")
    if str(source_truth.get("task_register_sha256") or "") != str(target_truth.get("task_register_sha256") or ""):
        mismatches.append("task_register_sha256_mismatch")
    if int(source_truth.get("task_register_write_generation") or 0) != int(target_truth.get("task_register_write_generation") or 0):
        mismatches.append("task_register_write_generation_mismatch")
    if str(target_boot.get("mode") or "") != "restored_host":
        mismatches.append(f"unexpected_target_boot_mode:{str(target_boot.get('mode') or '')}")
    if not bool(post_reconcile_scheduler_truth.get("coherent")):
        mismatches.append("scheduler_truth_not_coherent_after_restore")

    return {
        "schema_version": "lab_assistant_host_recovery_drill_v1",
        "drill_utc": _utc_now_iso(),
        "status": "pass" if not mismatches and not bool(restore.get("partial_restore")) else "fail",
        "source_repo_root": str(source_repo_root),
        "target_repo_root": str(target_root),
        "export": export,
        "restore": restore,
        "source_health": source_health,
        "target_boot": target_boot,
        "target_health": target_health,
        "source_durable_truth_snapshot": source_truth,
        "target_durable_truth_snapshot": target_truth,
        "pre_reconcile_scheduler_truth": pre_reconcile_scheduler_truth,
        "scheduler_reconciliation": scheduler_reconciliation,
        "post_reconcile_scheduler_truth": post_reconcile_scheduler_truth,
        "reconciliation_required": not bool(pre_reconcile_scheduler_truth.get("coherent")),
        "time_to_coherent_truth_ms": max(
            0,
            int((reconciliation_finished - reconciliation_started).total_seconds() * 1000),
        ),
        "mismatches": mismatches,
    }


def _scheduler_truth_snapshot(repo_root: Path) -> Dict[str, Any]:
    """Capture whether restored scheduler truth is coherent on this host."""
    from infra.controlplane import scheduled_jobs as _sched
    from tools.bin import scheduler_watchdog as _watchdog
    from infra.webui import server_lab_assistant_task_register as _task_register

    now_utc = _utc_now_iso()
    rows = _sched.list_jobs(repo_root=repo_root, now_utc=now_utc)
    active_run_job_ids: List[str] = []
    active_claim_job_ids: List[str] = []
    blocked_by_active_run_job_ids: List[str] = []
    ambiguous_projection_job_ids: List[str] = []
    known_job_ids = {
        str(row.get("job_id") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("job_id") or "").strip()
    }
    for row in rows:
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        due = row.get("due") if isinstance(row.get("due"), dict) else {}
        projection = row.get("scheduler_projection_v1") if isinstance(row.get("scheduler_projection_v1"), dict) else {}
        job_id = str(row.get("job_id") or "").strip()
        if str(state.get("active_run_id") or "").strip():
            active_run_job_ids.append(job_id)
        if bool(state.get("active_claim_present")):
            active_claim_job_ids.append(job_id)
        if str(due.get("reason") or "").strip() == "active_run":
            blocked_by_active_run_job_ids.append(job_id)
        if str(projection.get("projected_status") or "").strip() == "ambiguous" or bool(row.get("scheduler_truth_degraded")):
            ambiguous_projection_job_ids.append(job_id)
    scheduler_binding_conflict_task_ids: List[str] = []
    orphan_scheduler_binding_task_ids: List[str] = []
    try:
        register_snapshot = _task_register.read_task_register_snapshot(repo_root, initialize_if_missing=False)
    except Exception:
        register_snapshot = {}
    register = register_snapshot.get("task_register") if isinstance(register_snapshot.get("task_register"), dict) else {}
    for task in list(register.get("active_tasks") or []):
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            continue
        summary = _task_register.scheduler_binding_summary(task)
        ownership = task.get("execution_surface_ownership") if isinstance(task.get("execution_surface_ownership"), dict) else {}
        reference_status = str(ownership.get("reference_status") or "").strip().lower()
        job_ids = [
            str(job_id).strip()
            for job_id in list(summary.get("job_ids") or [])
            if str(job_id).strip()
        ]
        if reference_status in {"execution_ref_stale", "execution_ref_conflict"}:
            scheduler_binding_conflict_task_ids.append(task_id)
            continue
        if bool(summary.get("has_live_scheduler_binding")) and job_ids and not any(job_id in known_job_ids for job_id in job_ids):
            orphan_scheduler_binding_task_ids.append(task_id)
    watchdog = _watchdog.run_watchdog(repo_root=repo_root, mode="observe")
    findings = [str(item.get("kind") or "").strip() for item in list(watchdog.get("findings") or []) if str(item.get("kind") or "").strip()]
    truth_breakers = {
        "stale_lock",
        "orphan_running_state",
        "task_register_snapshot_unreadable",
        "terminal_receipt_state_conflict",
        "runtime_storage_low",
        "runtime_storage_critical",
        "truth_ambiguous",
    }
    coherent = (
        not active_run_job_ids
        and not active_claim_job_ids
        and not blocked_by_active_run_job_ids
        and not ambiguous_projection_job_ids
        and not scheduler_binding_conflict_task_ids
        and not orphan_scheduler_binding_task_ids
        and not any(kind in truth_breakers for kind in findings)
    )
    return {
        "captured_utc": now_utc,
        "job_count": len(rows),
        "active_run_job_ids": active_run_job_ids,
        "active_claim_job_ids": active_claim_job_ids,
        "blocked_by_active_run_job_ids": blocked_by_active_run_job_ids,
        "ambiguous_projection_job_ids": ambiguous_projection_job_ids,
        "scheduler_binding_conflict_task_ids": scheduler_binding_conflict_task_ids,
        "orphan_scheduler_binding_task_ids": orphan_scheduler_binding_task_ids,
        "watchdog_health": watchdog.get("health"),
        "watchdog_finding_kinds": findings,
        "coherent": coherent,
    }


def _reconcile_scheduler_truth_after_restore(repo_root: Path) -> Dict[str, Any]:
    """Run the scheduler-owned startup reconciliation needed after host restore."""
    from infra.controlplane import scheduled_jobs as _sched

    claim_audit = _sched.startup_claim_audit(repo_root=repo_root)
    stranded_running_reconciliation = _sched._reconcile_stranded_running_without_live_claim(repo_root=repo_root)
    stale_running_transition = _sched._transition_stale_running_to_blocked(repo_root=repo_root)
    return {
        "startup": True,
        "claim_audit": claim_audit,
        "stranded_running_reconciliation": stranded_running_reconciliation,
        "stale_running_transition": stale_running_transition,
        "ok": bool(claim_audit.get("ok"))
        and bool(stranded_running_reconciliation.get("ok"))
        and bool(stale_running_transition.get("ok")),
    }
