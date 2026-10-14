#!/usr/bin/env python3
"""Launch Codex through a reusable slot-backed CODEX_HOME and project readable transcripts."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_SLOT_COUNT = 8
SHARED_HOME_FILES = (
    ".personality_migration",
    "auth.json",
    "config.toml",
    "models_cache.json",
    "version.json",
)
SHARED_HOME_DIRS = (
    "memories",
    "rules",
    "skills",
)


def _repo_root() -> Path:
    """Return the repository root from this launcher path."""
    return Path(__file__).resolve().parents[2]


def _terminal_slot_state_root(repo_root: Path) -> Path:
    """Return the runtime state root for terminal Codex slots."""
    return (repo_root / "runtime" / "state" / "codex_terminal_slots").resolve()


def _terminal_slot_sessions_root(repo_root: Path) -> Path:
    """Return the canonical transcript root for terminal Codex slots."""
    return (repo_root / "governance" / "agents" / "sessions" / "terminal_slots").resolve()


def _terminal_slot_bubbles_root(repo_root: Path) -> Path:
    """Return the readable Markdown transcript root for terminal Codex slots."""
    return (repo_root / "governance" / "agents" / "terminal_bubbles").resolve()


def _utc_now_iso() -> str:
    """Return the current UTC timestamp in stable Zulu format."""
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_timestamp(raw: Any) -> str:
    """Normalize epoch or ISO timestamps into UTC Zulu strings."""
    if isinstance(raw, (int, float)):
        return dt.datetime.fromtimestamp(float(raw), tz=dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except Exception:
        return text


def _read_json(path: Path, *, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Read a JSON object from disk or return the provided default object."""
    if not path.exists() or not path.is_file():
        return dict(default or {})
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return dict(default or {})
    return raw if isinstance(raw, dict) else dict(default or {})


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Read JSONL rows from disk, skipping malformed lines."""
    if not path.exists() or not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    """Write a JSON object atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_text_atomic(path: Path, text: str) -> None:
    """Write UTF-8 text atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(str(text or ""), encoding="utf-8")
    os.replace(tmp, path)


def _slot_name(index: int) -> str:
    """Return the stable slot identifier for the given 1-based index."""
    return f"slot_{index:02d}"


def _projection_mode() -> str:
    """Return whether live projection stays enabled during the slot lifetime."""
    raw = str(os.environ.get("EIEL_TERMINAL_SLOT_PROJECTION_MODE") or "live").strip().lower()
    return raw if raw in {"live", "exit_only"} else "live"


def _projection_format() -> str:
    """Return the enabled projection output format."""
    raw = str(os.environ.get("EIEL_TERMINAL_SLOT_PROJECTION_FORMAT") or "full").strip().lower()
    return raw if raw in {"full", "jsonl_only"} else "full"


def _projection_debug_enabled() -> bool:
    """Return whether detailed per-cycle debug logs should be emitted."""
    return str(os.environ.get("EIEL_TERMINAL_SLOT_PROJECTION_DEBUG") or "0").strip().lower() in {"1", "true", "yes", "on"}


def _projection_debug_log_path(repo_root: Path, slot_id: str) -> Path:
    """Return the optional projection debug JSONL log path."""
    return (_slot_dir(_terminal_slot_state_root(repo_root), slot_id) / "projection_debug.jsonl").resolve()


def _slot_dir(state_root: Path, slot_id: str) -> Path:
    """Return the state directory for a specific terminal slot."""
    return (state_root / slot_id).resolve()


def _slot_codex_home(state_root: Path, slot_id: str) -> Path:
    """Return the isolated CODEX_HOME directory for a slot."""
    return (_slot_dir(state_root, slot_id) / "codex_home").resolve()


def _slot_projection_jsonl(repo_root: Path, slot_id: str) -> Path:
    """Return the canonical projected transcript file for a slot."""
    return (_terminal_slot_sessions_root(repo_root) / slot_id / "session__codex.jsonl").resolve()


def _slot_projection_markdown(repo_root: Path, slot_id: str) -> Path:
    """Return the readable Markdown transcript file for a slot."""
    return (_terminal_slot_bubbles_root(repo_root) / f"{slot_id}.md").resolve()


def _slot_projection_metrics_path(repo_root: Path, slot_id: str) -> Path:
    """Return the persistent projection metrics file for a slot."""
    return (_slot_dir(_terminal_slot_state_root(repo_root), slot_id) / "projection_metrics.json").resolve()


def _registry_path(state_root: Path) -> Path:
    """Return the slot registry state file."""
    return (state_root / "registry.json").resolve()


def _registry_lock_path(state_root: Path) -> Path:
    """Return the cross-process lock file for slot registry updates."""
    return (state_root / "registry.lock").resolve()


def _pid_is_alive(pid: Any) -> bool:
    """Return whether a process id currently exists on this host."""
    try:
        value = int(pid)
    except Exception:
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except OSError:
        return False
    return True


def _shared_codex_home() -> Path:
    """Return the shared user-level Codex home used for auth and configuration."""
    env_home = str(os.environ.get("EIEL_CODEX_SHARED_HOME") or "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def _ensure_managed_link(target: Path, source: Path) -> None:
    """Ensure a managed slot path is a symlink to the shared Codex home source."""
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        try:
            if target.resolve() == source.resolve():
                return
        except Exception:
            pass
        target.unlink()
    elif target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    target.symlink_to(source, target_is_directory=source.is_dir())


def _ensure_slot_codex_home(slot_home: Path, shared_home: Path) -> None:
    """Initialize one slot-local CODEX_HOME while sharing auth/config resources."""
    slot_home.mkdir(parents=True, exist_ok=True)
    for name in ("log", "sessions", "shell_snapshots", "tmp"):
        (slot_home / name).mkdir(parents=True, exist_ok=True)
    for name in SHARED_HOME_FILES:
        _ensure_managed_link(slot_home / name, shared_home / name)
    for name in SHARED_HOME_DIRS:
        _ensure_managed_link(slot_home / name, shared_home / name)


def _default_registry(max_slots: int) -> Dict[str, Any]:
    """Build the default empty registry payload."""
    return {"version": 1, "max_slots": int(max_slots), "slots": {}}


def _load_registry(state_root: Path, *, max_slots: int) -> Dict[str, Any]:
    """Load the slot registry, falling back to a default empty layout."""
    reg = _read_json(_registry_path(state_root), default=_default_registry(max_slots))
    reg["version"] = 1
    reg["max_slots"] = int(max_slots)
    slots = reg.get("slots")
    reg["slots"] = slots if isinstance(slots, dict) else {}
    return reg


def _cleanup_registry_stale_entries(registry: Dict[str, Any]) -> Dict[str, Any]:
    """Mark slots inactive when their owning launcher process no longer exists."""
    slots = registry.get("slots")
    if not isinstance(slots, dict):
        registry["slots"] = {}
        return registry
    now = _utc_now_iso()
    for slot_id, raw_entry in list(slots.items()):
        entry = raw_entry if isinstance(raw_entry, dict) else {}
        if entry.get("active") and not _pid_is_alive(entry.get("pid")):
            entry["active"] = False
            entry["released_utc"] = now
            entry["released_reason"] = "stale_pid"
            entry.pop("pid", None)
            slots[slot_id] = entry
    return registry


class _RegistryLock:
    """Provide an exclusive cross-process registry lock."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._fh: Optional[Any] = None

    def __enter__(self) -> "_RegistryLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._lock_path, "a+", encoding="utf-8")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._fh is None:
            return
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()
        self._fh = None


def _allocate_slot(state_root: Path, *, max_slots: int) -> Tuple[str, Dict[str, Any]]:
    """Claim the first available slot and return its registry lease metadata."""
    launch_id = uuid.uuid4().hex
    with _RegistryLock(_registry_lock_path(state_root)):
        registry = _cleanup_registry_stale_entries(_load_registry(state_root, max_slots=max_slots))
        slots = registry["slots"]
        chosen: Optional[str] = None
        for index in range(1, int(max_slots) + 1):
            slot_id = _slot_name(index)
            entry = slots.get(slot_id)
            if not isinstance(entry, dict) or not entry.get("active"):
                chosen = slot_id
                break
        if not chosen:
            raise RuntimeError(f"all {int(max_slots)} Codex terminal slots are busy")
        slots[chosen] = {
            "active": True,
            "cwd": os.getcwd(),
            "launch_id": launch_id,
            "pid": os.getpid(),
            "argv": sys.argv[1:],
            "claimed_utc": _utc_now_iso(),
            "last_known_repo_root": str(_repo_root()),
        }
        _write_json_atomic(_registry_path(state_root), registry)
        return chosen, dict(slots[chosen])


def _release_slot(state_root: Path, *, slot_id: str, launch_id: str, reason: str) -> None:
    """Release a previously claimed slot if the current launcher still owns it."""
    with _RegistryLock(_registry_lock_path(state_root)):
        registry = _cleanup_registry_stale_entries(_load_registry(state_root, max_slots=DEFAULT_SLOT_COUNT))
        slots = registry.get("slots") or {}
        entry = slots.get(slot_id)
        if not isinstance(entry, dict):
            return
        if str(entry.get("launch_id") or "") != str(launch_id):
            return
        if int(entry.get("pid") or -1) != int(os.getpid()):
            return
        entry["active"] = False
        entry["released_reason"] = str(reason or "exit")
        entry["released_utc"] = _utc_now_iso()
        entry.pop("pid", None)
        slots[slot_id] = entry
        _write_json_atomic(_registry_path(state_root), registry)


def _extract_assistant_text(payload: Dict[str, Any]) -> str:
    """Extract visible text blocks from a Codex assistant message payload."""
    parts: List[str] = []
    for item in payload.get("content") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip().lower() not in {"output_text", "text"}:
            continue
        text = str(item.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _load_history_messages(codex_home: Path) -> Dict[str, List[Dict[str, str]]]:
    """Load user-entered prompts from the slot-local Codex history file."""
    out: Dict[str, List[Dict[str, str]]] = {}
    for row in _read_jsonl(codex_home / "history.jsonl"):
        session_id = str(row.get("session_id") or "").strip()
        text = str(row.get("text") or "").strip()
        if not session_id or not text:
            continue
        out.setdefault(session_id, []).append(
            {
                "content": text,
                "phase": "",
                "sender": "user",
                "utc": _normalize_timestamp(row.get("ts")),
            }
        )
    return out


def _rollout_file_signature(path: Path, *, codex_home: Path) -> Tuple[str, int, int]:
    """Return one stable signature tuple for a rollout file."""
    try:
        stat = path.stat()
    except Exception:
        return (str(path), 0, 0)
    try:
        rel = str(path.resolve().relative_to(codex_home.resolve()))
    except Exception:
        rel = str(path.resolve())
    return (rel, int(stat.st_size), int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))))


def _projection_input_snapshot(codex_home: Path) -> Dict[str, Any]:
    """Capture the current rollout/history input footprint for change detection."""
    sessions_root = codex_home / "sessions"
    rollout_paths = sorted(sessions_root.rglob("rollout-*.jsonl")) if sessions_root.exists() else []
    digest = hashlib.sha256()
    rollout_bytes = 0
    for path in rollout_paths:
        rel, size, mtime_ns = _rollout_file_signature(path, codex_home=codex_home)
        rollout_bytes += int(size)
        digest.update(rel.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii", errors="ignore"))
        digest.update(b"\0")
        digest.update(str(mtime_ns).encode("ascii", errors="ignore"))
        digest.update(b"\n")
    history_path = codex_home / "history.jsonl"
    history_size = 0
    history_mtime_ns = 0
    if history_path.exists() and history_path.is_file():
        try:
            stat = history_path.stat()
            history_size = int(stat.st_size)
            history_mtime_ns = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))
        except Exception:
            history_size = 0
            history_mtime_ns = 0
    digest.update(b"history\0")
    digest.update(str(history_size).encode("ascii", errors="ignore"))
    digest.update(b"\0")
    digest.update(str(history_mtime_ns).encode("ascii", errors="ignore"))
    digest.update(b"\n")
    return {
        "rollout_paths": rollout_paths,
        "rollout_file_count": len(rollout_paths),
        "rollout_bytes": int(rollout_bytes),
        "history_bytes": int(history_size),
        "history_mtime_ns": int(history_mtime_ns),
        "input_signature": digest.hexdigest(),
    }


def _default_projection_metrics() -> Dict[str, Any]:
    """Return the default metrics payload for one slot projection stream."""
    return {
        "schema_version": "terminal_slot_projection_metrics_v1",
        "updated_utc": "",
        "slot_id": "",
        "projection_mode": "",
        "projection_format": "",
        "last_input_signature": "",
        "last_inputs_changed": False,
        "last_projection_utc": "",
        "last_projection_duration_s": 0.0,
        "last_flush_iteration_s": 0.0,
        "last_write_slot_projection_s": 0.0,
        "last_load_rollout_sessions_s": 0.0,
        "last_project_slot_rows_s": 0.0,
        "last_rollout_file_count": 0,
        "last_rollout_bytes": 0,
        "last_history_bytes": 0,
        "last_rows_projected": 0,
        "last_output_bytes": 0,
        "last_executed": False,
        "executed_flush_cycles": 0,
        "skipped_flush_cycles": 0,
        "total_projection_duration_s": 0.0,
        "average_projection_duration_s": 0.0,
    }


def _load_projection_metrics(repo_root: Path, slot_id: str) -> Dict[str, Any]:
    """Load persisted projection metrics for one slot."""
    payload = _read_json(_slot_projection_metrics_path(repo_root, slot_id), default=_default_projection_metrics())
    merged = _default_projection_metrics()
    merged.update(payload)
    merged["slot_id"] = str(slot_id)
    return merged


def _write_projection_metrics(repo_root: Path, slot_id: str, metrics: Dict[str, Any]) -> None:
    """Persist projection metrics for one slot."""
    payload = _default_projection_metrics()
    payload.update(metrics)
    payload["slot_id"] = str(slot_id)
    payload["updated_utc"] = _utc_now_iso()
    _write_json_atomic(_slot_projection_metrics_path(repo_root, slot_id), payload)


def _append_projection_debug_log(repo_root: Path, slot_id: str, row: Dict[str, Any]) -> None:
    """Append one optional projection debug row."""
    if not _projection_debug_enabled():
        return
    path = _projection_debug_log_path(repo_root, slot_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        handle.write("\n")


def _load_rollout_sessions(
    codex_home: Path,
    *,
    rollout_paths: Optional[List[Path]] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Load assistant-visible messages and session metadata from slot-local rollout files."""
    started = time.perf_counter()
    sessions_root = codex_home / "sessions"
    out: Dict[str, Dict[str, Any]] = {}
    if not sessions_root.exists():
        if stats is not None:
            stats["load_rollout_sessions_s"] = round(time.perf_counter() - started, 6)
            stats["rollout_file_count"] = 0
            stats["rollout_bytes"] = 0
        return out
    chosen_paths = list(rollout_paths if rollout_paths is not None else sorted(sessions_root.rglob("rollout-*.jsonl")))
    rollout_bytes = 0
    for rollout_path in chosen_paths:
        try:
            rollout_bytes += int(rollout_path.stat().st_size)
        except Exception:
            continue
    for rollout_path in chosen_paths:
        session_id = ""
        for row in _read_jsonl(rollout_path):
            rtype = str(row.get("type") or "").strip()
            payload = row.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            row_utc = _normalize_timestamp(row.get("timestamp"))
            if rtype == "session_meta":
                session_id = str(payload.get("id") or "").strip()
                if not session_id:
                    continue
                entry = out.setdefault(
                    session_id,
                    {"assistant_messages": [], "cwd": "", "meta_utc": "", "rollout_path": str(rollout_path)},
                )
                entry["cwd"] = str(payload.get("cwd") or entry.get("cwd") or "").strip()
                entry["meta_utc"] = _normalize_timestamp(payload.get("timestamp") or row_utc or entry.get("meta_utc"))
                entry["rollout_path"] = str(rollout_path)
                continue
            if rtype != "response_item" or str(payload.get("type") or "").strip().lower() != "message":
                continue
            if str(payload.get("role") or "").strip().lower() != "assistant":
                continue
            if not session_id:
                continue
            text = _extract_assistant_text(payload)
            if not text:
                continue
            out.setdefault(
                session_id,
                {"assistant_messages": [], "cwd": "", "meta_utc": "", "rollout_path": str(rollout_path)},
            )["assistant_messages"].append(
                {
                    "content": text,
                    "phase": str(payload.get("phase") or "").strip().lower(),
                    "sender": "assistant",
                    "utc": row_utc,
                }
            )
    if stats is not None:
        stats["load_rollout_sessions_s"] = round(time.perf_counter() - started, 6)
        stats["rollout_file_count"] = len(chosen_paths)
        stats["rollout_bytes"] = int(rollout_bytes)
    return out


def _project_slot_rows_with_stats(
    slot_id: str,
    codex_home: Path,
    *,
    rollout_paths: Optional[List[Path]] = None,
    input_snapshot: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Project slot-local Codex artifacts into a clean sequential transcript row set."""
    started = time.perf_counter()
    stats: Dict[str, Any] = {}
    history_started = time.perf_counter()
    history_by_session = _load_history_messages(codex_home)
    stats["load_history_messages_s"] = round(time.perf_counter() - history_started, 6)
    rollout_by_session = _load_rollout_sessions(codex_home, rollout_paths=rollout_paths, stats=stats)
    if input_snapshot is not None:
        stats.setdefault("rollout_file_count", int(input_snapshot.get("rollout_file_count") or 0))
        stats.setdefault("rollout_bytes", int(input_snapshot.get("rollout_bytes") or 0))
        stats["history_bytes"] = int(input_snapshot.get("history_bytes") or 0)
    session_ids = sorted(
        set(history_by_session.keys()) | set(rollout_by_session.keys()),
        key=lambda sid: (
            min(
                [
                    row.get("utc") or "9999-99-99T99:99:99Z"
                    for row in history_by_session.get(sid, [])
                    + rollout_by_session.get(sid, {}).get("assistant_messages", [])
                    + ([{"utc": rollout_by_session.get(sid, {}).get("meta_utc", "")}] if rollout_by_session.get(sid, {}).get("meta_utc") else [])
                ]
                or ["9999-99-99T99:99:99Z"]
            ),
            sid,
        ),
    )
    rows: List[Dict[str, Any]] = []
    for session_id in session_ids:
        assistant_rows = list(rollout_by_session.get(session_id, {}).get("assistant_messages", []))
        user_rows = list(history_by_session.get(session_id, []))
        message_rows = user_rows + assistant_rows
        message_rows.sort(key=lambda row: ((row.get("utc") or "9999-99-99T99:99:99Z"), 0 if row.get("sender") == "user" else 1, row.get("content") or ""))
        meta_utc = str(rollout_by_session.get(session_id, {}).get("meta_utc") or "").strip()
        start_utc = meta_utc or (message_rows[0].get("utc") if message_rows else "")
        end_utc = message_rows[-1].get("utc") if message_rows else start_utc
        rows.append(
            {
                "kind": "session_start",
                "session_id": session_id,
                "slot_id": slot_id,
                "utc": start_utc,
                "cwd": str(rollout_by_session.get(session_id, {}).get("cwd") or "").strip(),
            }
        )
        for msg in message_rows:
            rows.append(
                {
                    "kind": "message",
                    "content": str(msg.get("content") or "").strip(),
                    "phase": str(msg.get("phase") or "").strip().lower(),
                    "sender": str(msg.get("sender") or "").strip().lower(),
                    "session_id": session_id,
                    "slot_id": slot_id,
                    "utc": str(msg.get("utc") or "").strip(),
                }
            )
        rows.append(
            {
                "kind": "session_end",
                "message_count": len(message_rows),
                "session_id": session_id,
                "slot_id": slot_id,
                "utc": end_utc,
            }
        )
    stats["rows_projected"] = len(rows)
    stats["project_slot_rows_s"] = round(time.perf_counter() - started, 6)
    return rows, stats


def _project_slot_rows(slot_id: str, codex_home: Path) -> List[Dict[str, Any]]:
    """Backward-compatible row projection helper."""
    rows, _stats = _project_slot_rows_with_stats(slot_id, codex_home)
    return rows


def _render_terminal_bubble_markdown(slot_id: str, rows: List[Dict[str, Any]]) -> str:
    """Render a human-friendly Markdown transcript for one slot."""
    lines: List[str] = [
        f"# Terminal Codex / {slot_id}",
        "",
        f"- updated_utc: {_utc_now_iso()}",
        "",
    ]
    session_seen = False
    for row in rows:
        kind = str(row.get("kind") or "").strip().lower()
        if kind == "session_start":
            session_seen = True
            session_id = str(row.get("session_id") or "").strip()
            utc = str(row.get("utc") or "-").strip() or "-"
            lines.append(f"## Session {session_id}")
            lines.append("")
            lines.append(f"- started_utc: {utc}")
            cwd = str(row.get("cwd") or "").strip()
            if cwd:
                lines.append(f"- cwd: `{cwd}`")
            lines.append("")
            continue
        if kind != "message":
            continue
        sender = str(row.get("sender") or "").strip().lower()
        if sender not in {"user", "assistant"}:
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        utc = str(row.get("utc") or "-").strip() or "-"
        phase = str(row.get("phase") or "").strip().lower()
        phase_suffix = f" [{phase}]" if sender == "assistant" and phase and phase != "final" else ""
        lines.append(f"[{utc}] {sender}{phase_suffix}")
        lines.append(content)
        lines.append("")
    if not session_seen:
        lines.append("(no terminal Codex transcript yet)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_slot_projection(repo_root: Path, slot_id: str, codex_home: Path) -> Dict[str, Any]:
    """Write canonical JSONL and readable Markdown projections for one slot."""
    started = time.perf_counter()
    input_snapshot = _projection_input_snapshot(codex_home)
    metrics = _load_projection_metrics(repo_root, slot_id)
    previous_signature = str(metrics.get("last_input_signature") or "")
    inputs_changed = str(input_snapshot.get("input_signature") or "") != previous_signature
    stats: Dict[str, Any] = {
        "utc": _utc_now_iso(),
        "slot_id": str(slot_id),
        "projection_mode": _projection_mode(),
        "projection_format": _projection_format(),
        "inputs_changed": bool(inputs_changed),
        "rollout_file_count": int(input_snapshot.get("rollout_file_count") or 0),
        "rollout_bytes": int(input_snapshot.get("rollout_bytes") or 0),
        "history_bytes": int(input_snapshot.get("history_bytes") or 0),
        "rows_projected": 0,
        "output_bytes": 0,
        "executed": False,
    }
    if not inputs_changed:
        metrics["projection_mode"] = _projection_mode()
        metrics["projection_format"] = _projection_format()
        metrics["last_inputs_changed"] = False
        metrics["last_rollout_file_count"] = int(input_snapshot.get("rollout_file_count") or 0)
        metrics["last_rollout_bytes"] = int(input_snapshot.get("rollout_bytes") or 0)
        metrics["last_history_bytes"] = int(input_snapshot.get("history_bytes") or 0)
        metrics["last_rows_projected"] = 0
        metrics["last_output_bytes"] = 0
        metrics["last_executed"] = False
        metrics["skipped_flush_cycles"] = int(metrics.get("skipped_flush_cycles") or 0) + 1
        metrics["last_write_slot_projection_s"] = round(time.perf_counter() - started, 6)
        _write_projection_metrics(repo_root, slot_id, metrics)
        stats["write_slot_projection_s"] = float(metrics["last_write_slot_projection_s"])
        _append_projection_debug_log(repo_root, slot_id, {"event": "write_slot_projection", **stats})
        return stats
    rows, project_stats = _project_slot_rows_with_stats(
        slot_id,
        codex_home,
        rollout_paths=list(input_snapshot.get("rollout_paths") or []),
        input_snapshot=input_snapshot,
    )
    jsonl_text = ""
    for row in rows:
        jsonl_text += json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    jsonl_bytes = len(jsonl_text.encode("utf-8", errors="replace"))
    output_bytes = int(jsonl_bytes)
    _write_text_atomic(_slot_projection_jsonl(repo_root, slot_id), jsonl_text)
    markdown_bytes = 0
    if _projection_format() == "full":
        markdown_text = _render_terminal_bubble_markdown(slot_id, rows)
        markdown_bytes = len(markdown_text.encode("utf-8", errors="replace"))
        output_bytes += int(markdown_bytes)
        _write_text_atomic(_slot_projection_markdown(repo_root, slot_id), markdown_text)
    metrics["projection_mode"] = _projection_mode()
    metrics["projection_format"] = _projection_format()
    metrics["last_input_signature"] = str(input_snapshot.get("input_signature") or "")
    metrics["last_inputs_changed"] = True
    metrics["last_projection_utc"] = _utc_now_iso()
    metrics["last_projection_duration_s"] = round(time.perf_counter() - started, 6)
    metrics["last_write_slot_projection_s"] = metrics["last_projection_duration_s"]
    metrics["last_load_rollout_sessions_s"] = float(project_stats.get("load_rollout_sessions_s") or 0.0)
    metrics["last_project_slot_rows_s"] = float(project_stats.get("project_slot_rows_s") or 0.0)
    metrics["last_rollout_file_count"] = int(project_stats.get("rollout_file_count") or input_snapshot.get("rollout_file_count") or 0)
    metrics["last_rollout_bytes"] = int(project_stats.get("rollout_bytes") or input_snapshot.get("rollout_bytes") or 0)
    metrics["last_history_bytes"] = int(project_stats.get("history_bytes") or input_snapshot.get("history_bytes") or 0)
    metrics["last_rows_projected"] = int(project_stats.get("rows_projected") or len(rows))
    metrics["last_output_bytes"] = int(output_bytes)
    metrics["last_executed"] = True
    metrics["executed_flush_cycles"] = int(metrics.get("executed_flush_cycles") or 0) + 1
    metrics["total_projection_duration_s"] = float(metrics.get("total_projection_duration_s") or 0.0) + float(metrics["last_projection_duration_s"])
    executed = max(1, int(metrics.get("executed_flush_cycles") or 0))
    metrics["average_projection_duration_s"] = round(float(metrics["total_projection_duration_s"]) / float(executed), 6)
    _write_projection_metrics(repo_root, slot_id, metrics)
    stats.update(project_stats)
    stats["output_bytes"] = int(output_bytes)
    stats["output_jsonl_bytes"] = int(jsonl_bytes)
    stats["output_markdown_bytes"] = int(markdown_bytes)
    stats["executed"] = True
    stats["write_slot_projection_s"] = float(metrics["last_projection_duration_s"])
    _append_projection_debug_log(repo_root, slot_id, {"event": "write_slot_projection", **stats})
    return stats


def _build_child_env(slot_home: Path) -> Dict[str, str]:
    """Build the child environment for the real Codex process."""
    env = dict(os.environ)
    env["CODEX_HOME"] = str(slot_home)
    env["EIEL_TERMINAL_CODEX_SLOT_HOME"] = str(slot_home)
    return env


def _launcher_script_path() -> Path:
    """Return the canonical launcher script path for recursion checks."""
    return Path(__file__).resolve()


def _real_codex_bin() -> str:
    """Resolve the underlying Codex executable without recursing into the wrapper."""
    candidate = str(os.environ.get("EIEL_CODEX_REAL_BIN") or "").strip()
    if not candidate:
        raise RuntimeError("EIEL_CODEX_REAL_BIN is required for persistent Codex slots")
    resolved = Path(candidate).expanduser().resolve()
    launcher = _launcher_script_path()
    if resolved == launcher:
        raise RuntimeError("EIEL_CODEX_REAL_BIN points to the persistent slot launcher itself")
    if not resolved.exists() or not resolved.is_file():
        raise RuntimeError(f"EIEL_CODEX_REAL_BIN does not exist: {resolved}")
    if not os.access(resolved, os.X_OK):
        raise RuntimeError(f"EIEL_CODEX_REAL_BIN is not executable: {resolved}")
    return str(resolved)


def _projection_flush_interval_s() -> float:
    """Return the periodic live projection flush interval."""
    raw = str(
        os.environ.get("EIEL_TERMINAL_SLOT_PROJECTION_INTERVAL_S")
        or os.environ.get("EIEL_TERMINAL_SLOT_PROJECTION_FLUSH_S")
        or "2.0"
    ).strip()
    try:
        value = float(raw)
    except Exception:
        value = 2.0
    return max(0.25, min(value, 30.0))


def _projection_flush_loop(*, repo_root: Path, slot_id: str, slot_home: Path, stop_event: threading.Event) -> None:
    """Refresh slot projections while the child Codex process is still alive."""
    interval_s = _projection_flush_interval_s()
    while not stop_event.wait(interval_s):
        iteration_started = time.perf_counter()
        iteration_stats: Dict[str, Any] = {}
        try:
            iteration_stats = _write_slot_projection(repo_root, slot_id, slot_home)
        except Exception:
            iteration_stats = {"slot_id": str(slot_id), "error": "write_slot_projection_failed"}
        finally:
            elapsed = round(time.perf_counter() - iteration_started, 6)
            metrics = _load_projection_metrics(repo_root, slot_id)
            metrics["last_flush_iteration_s"] = float(elapsed)
            _write_projection_metrics(repo_root, slot_id, metrics)
            _append_projection_debug_log(
                repo_root,
                slot_id,
                {
                    "event": "flush_iteration",
                    "utc": _utc_now_iso(),
                    "slot_id": str(slot_id),
                    "flush_iteration_s": float(elapsed),
                    **iteration_stats,
                },
            )
            continue


def _run_slot(repo_root: Path, slot_id: str, lease: Dict[str, Any], slot_home: Path, argv: List[str]) -> int:
    """Run the real Codex CLI in the claimed slot home and return its exit code."""
    stop_event = threading.Event()
    flush_thread: Optional[threading.Thread] = None
    if _projection_mode() == "live":
        flush_thread = threading.Thread(
            target=_projection_flush_loop,
            kwargs={"repo_root": repo_root, "slot_id": slot_id, "slot_home": slot_home, "stop_event": stop_event},
            daemon=True,
        )
        flush_thread.start()
    child = subprocess.Popen([_real_codex_bin(), *argv], env=_build_child_env(slot_home))
    try:
        return int(child.wait())
    except KeyboardInterrupt:
        while child.poll() is None:
            try:
                child.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                continue
        return int(child.returncode or 130)
    finally:
        stop_event.set()
        if flush_thread is not None:
            flush_thread.join(timeout=2.0)
        _write_slot_projection(repo_root, slot_id, slot_home)


def _parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse launcher arguments while passing unknown flags through to Codex."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--slot-count", type=int, default=int(os.environ.get("EIEL_CODEX_SLOT_COUNT") or DEFAULT_SLOT_COUNT))
    ns, remainder = parser.parse_known_args(argv)
    ns.codex_args = list(remainder or [])
    return ns


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for the slot-backed Codex launcher."""
    args = _parse_args(list(argv or sys.argv[1:]))
    repo_root = _repo_root()
    state_root = _terminal_slot_state_root(repo_root)
    slot_id, lease = _allocate_slot(state_root, max_slots=max(1, int(args.slot_count)))
    slot_home = _slot_codex_home(state_root, slot_id)
    _ensure_slot_codex_home(slot_home, _shared_codex_home())
    exit_code = 1
    release_reason = "exit"
    try:
        exit_code = _run_slot(repo_root, slot_id, lease, slot_home, args.codex_args)
        release_reason = f"exit_{exit_code}"
        return exit_code
    finally:
        _release_slot(state_root, slot_id=slot_id, launch_id=str(lease.get("launch_id") or ""), reason=release_reason)


if __name__ == "__main__":
    raise SystemExit(main())
