"""Persistent assistant identity and source-of-truth contract (2848).

This module owns:
- stable assistant identity
- versioned assistant runtime state schema
- append-only assistant event log
- explicit write-arbitration for assistant-runtime mutations
- source precedence across ledger, runtime state, and chat transcript

It does not own task-register contents, bootstrap context assembly, delegation
policy, or full restart reconciliation.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.webui import server_lab_assistant_host as _lab_host

try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover
    _fcntl = None  # type: ignore[assignment]


ASSISTANT_STATE_SCHEMA_VERSION = "lab_assistant_state_v1"
ASSISTANT_EVENT_SCHEMA_VERSION = "lab_assistant_event_v1"
ASSISTANT_ALLOWED_STATES = {
    "idle",
    "thinking",
    "delegating",
    "waiting_on_job",
    "waiting_on_verification",
    "waiting_on_operator",
    "blocked",
    "interrupted_recovering",
    "completed",
    "failed",
}
_LOCK = threading.RLock()
ASSISTANT_DEFAULT_LABEL = "Jeff"


class AssistantStateError(RuntimeError):
    """Raised when the assistant-state contract cannot be satisfied."""


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _assistant_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "assistant_state").resolve()


def _state_path(repo_root: Path) -> Path:
    return (_assistant_root(repo_root) / "assistant_state.json").resolve()


def _events_path(repo_root: Path) -> Path:
    return (_assistant_root(repo_root) / "assistant_events.jsonl").resolve()


def _lock_path(repo_root: Path) -> Path:
    return (_assistant_root(repo_root) / ".assistant_state.lock").resolve()


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _read_json_strict(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise AssistantStateError(f"{path.name} is missing")
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        raise AssistantStateError(f"{path.name} is malformed") from exc
    if not isinstance(obj, dict):
        raise AssistantStateError(f"{path.name} is malformed")
    return obj


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tmp = path.parent / f".{path.name}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(raw)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def default_assistant_label() -> str:
    return str(os.environ.get("ASSISTANT_LABEL", ASSISTANT_DEFAULT_LABEL)).strip() or ASSISTANT_DEFAULT_LABEL


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(raw)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.is_symlink():
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


def _read_jsonl_strict(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    out: List[Dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except Exception as exc:
            raise AssistantStateError(f"{path.name} is malformed at line {lineno}") from exc
        if not isinstance(row, dict):
            raise AssistantStateError(f"{path.name} has non-object row at line {lineno}")
        out.append(row)
    return out


def _default_recovery_marker(*, host_health: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(host_health.get("mode") or "")
    status = str(host_health.get("status") or "")
    if status in {"blocked", "degraded"}:
        return {
            "kind": "host_baseline_attention_required",
            "host_mode": mode,
            "host_status": status,
            "set_utc": _utc_now_iso(),
        }
    return {}


def _candidate_state_files(repo_root: Path) -> List[Path]:
    root = _assistant_root(repo_root)
    if not root.exists() or not root.is_dir():
        return []
    out: List[Path] = []
    for path in sorted(root.glob("assistant_state*.json")):
        if path.name.startswith("."):
            continue
        if path.is_file():
            out.append(path.resolve())
    return out


def _validate_state_obj(obj: Dict[str, Any]) -> None:
    schema = str(obj.get("schema_version") or "").strip()
    if schema != ASSISTANT_STATE_SCHEMA_VERSION:
        raise AssistantStateError(f"incompatible schema_version: {schema or '<missing>'}")
    assistant_id = str(obj.get("assistant_id") or "").strip()
    if not assistant_id:
        raise AssistantStateError("assistant_id is required")
    state = str(obj.get("current_state") or "").strip()
    if state not in ASSISTANT_ALLOWED_STATES:
        raise AssistantStateError(f"invalid current_state: {state or '<missing>'}")
    generation = obj.get("write_generation")
    if not isinstance(generation, int) or generation < 0:
        raise AssistantStateError("write_generation must be a non-negative integer")


def _validate_event_rows(rows: List[Dict[str, Any]], *, path: Path) -> None:
    for index, row in enumerate(rows, start=1):
        schema = str(row.get("schema_version") or "").strip()
        if schema != ASSISTANT_EVENT_SCHEMA_VERSION:
            raise AssistantStateError(f"{path.name} has incompatible schema_version at line {index}")
        if not str(row.get("event_id") or "").strip():
            raise AssistantStateError(f"{path.name} is missing event_id at line {index}")
        if not str(row.get("event") or "").strip():
            raise AssistantStateError(f"{path.name} is missing event at line {index}")
        if not str(row.get("utc") or "").strip():
            raise AssistantStateError(f"{path.name} is missing utc at line {index}")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            raise AssistantStateError(f"{path.name} has invalid payload at line {index}")


def _append_event(repo_root: Path, *, event_type: str, payload: Dict[str, Any]) -> None:
    _append_jsonl(
        _events_path(repo_root),
        {
            "schema_version": ASSISTANT_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event": str(event_type),
            "utc": _utc_now_iso(),
            "payload": dict(payload),
        },
    )


@contextmanager
def _state_file_lock(repo_root: Path):
    """Protect assistant-state read/modify/write across processes."""
    lock_path = _lock_path(repo_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as handle:
        if _fcntl is not None:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
        try:
            yield
        finally:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


def _initial_state(*, host_health: Dict[str, Any], assistant_label: str) -> Dict[str, Any]:
    recovery_marker = _default_recovery_marker(host_health=host_health)
    blocked = bool(recovery_marker)
    now = _utc_now_iso()
    return {
        "schema_version": ASSISTANT_STATE_SCHEMA_VERSION,
        "assistant_id": f"assistant_{uuid.uuid4().hex}",
        "assistant_label": str(assistant_label or default_assistant_label()),
        "current_state": "blocked" if blocked else "idle",
        "state_reason": (
            f"host baseline requires attention ({host_health.get('mode')})"
            if blocked
            else "assistant initialized"
        ),
        "state_since_utc": now,
        "last_turn_utc": "",
        "recovery_marker": recovery_marker,
        "write_generation": 0,
        "last_writer": "assistant_state.initialize",
        "last_written_utc": now,
        "task_register_generation_hint": None,
        "delegation_index_cache": {
            "schema_version": "assistant_delegation_index_cache_v1",
            "derived_only": True,
            "entries": [],
        },
    }


def _host_health_for_state(repo_root: Path) -> Dict[str, Any]:
    """Read ADD9 health without re-running boot classification."""
    host_health = _lab_host.host_health_snapshot(repo_root, initialize_if_missing=False)
    if str(host_health.get("mode") or "") == "host_baseline_uninitialized":
        raise AssistantStateError("ADD9 host baseline is not initialized")
    return host_health


def resolve_source_precedence(
    *,
    topic: str,
    ledger_value: Any = None,
    runtime_value: Any = None,
    chat_value: Any = None,
) -> Dict[str, Any]:
    """Return an explicit winner for one truth topic."""
    tt = str(topic or "").strip().lower()
    ledger_topics = {"task_truth", "task_priority", "project_artifact"}
    runtime_topics = {"assistant_identity", "assistant_state", "assistant_recovery_marker", "write_arbitration"}
    if tt in ledger_topics:
        winner = "ledger"
        value = ledger_value if ledger_value is not None else runtime_value if runtime_value is not None else chat_value
    elif tt in runtime_topics:
        winner = "runtime_state"
        value = runtime_value if runtime_value is not None else ledger_value if ledger_value is not None else chat_value
    else:
        raise AssistantStateError(f"unknown precedence topic: {tt}")
    conflict = False
    conflict_sources: List[str] = []
    if winner == "ledger":
        if chat_value is not None and ledger_value is not None and chat_value != ledger_value:
            conflict = True
            conflict_sources.append("chat")
        if runtime_value is not None and ledger_value is not None and runtime_value != ledger_value:
            conflict = True
            conflict_sources.append("runtime_state")
    else:
        if chat_value is not None and runtime_value is not None and chat_value != runtime_value:
            conflict = True
            conflict_sources.append("chat")
        if ledger_value is not None and runtime_value is not None and ledger_value != runtime_value:
            conflict = True
            conflict_sources.append("ledger")
    return {
        "topic": tt,
        "winner_source": winner,
        "value": value,
        "conflict": conflict,
        "conflict_sources": sorted(set(conflict_sources)),
        "chat_is_canonical": False,
    }


def _load_state_internal(repo_root: Path) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    state = _read_json_strict(_state_path(repo_root))
    _validate_state_obj(state)
    _validate_event_rows(_read_jsonl_strict(_events_path(repo_root)), path=_events_path(repo_root))
    return state


def _load_or_init_assistant_state_locked(
    repo_root: Path,
    *,
    assistant_label: str,
) -> Dict[str, Any]:
    """Locked implementation for load_or_init_assistant_state."""
    _lab_host.ensure_host_storage(repo_root)
    root = _assistant_root(repo_root)
    root.mkdir(parents=True, exist_ok=True)
    candidates = _candidate_state_files(repo_root)
    canonical_path = _state_path(repo_root)
    if not canonical_path.exists():
        if candidates:
            _append_event(
                repo_root,
                event_type="assistant_state_ambiguous",
                payload={"candidate_files": [p.name for p in candidates]},
            )
            raise AssistantStateError("assistant state candidates exist without canonical winner")
        host_health = _host_health_for_state(repo_root)
        state = _initial_state(host_health=host_health, assistant_label=assistant_label)
        _write_json_atomic(canonical_path, state)
        _append_event(repo_root, event_type="assistant_initialized", payload={"assistant_id": state["assistant_id"]})
        return dict(state)

    state = _load_state_internal(repo_root)
    winning_id = str(state.get("assistant_id") or "")
    extra_ids: List[str] = []
    extra_files: List[str] = []
    for candidate in candidates:
        if candidate == canonical_path:
            continue
        obj = _read_json_strict(candidate)
        _validate_state_obj(obj)
        cid = str(obj.get("assistant_id") or "").strip()
        if cid and cid != winning_id:
            extra_ids.append(cid)
            extra_files.append(candidate.name)
    if extra_files:
        marker = {
            "kind": "identity_conflict_detected",
            "winning_assistant_id": winning_id,
            "other_assistant_ids": sorted(set(extra_ids)),
            "other_files": sorted(extra_files),
            "set_utc": _utc_now_iso(),
        }
        state["recovery_marker"] = marker
        if state.get("current_state") != "blocked":
            state["current_state"] = "blocked"
            state["state_since_utc"] = _utc_now_iso()
        state["state_reason"] = "assistant identity conflict detected"
        state["write_generation"] = int(state.get("write_generation") or 0) + 1
        state["last_writer"] = "assistant_state.identity_conflict"
        state["last_written_utc"] = _utc_now_iso()
        _write_json_atomic(canonical_path, state)
        _append_event(
            repo_root,
            event_type="identity_conflict_detected",
            payload={"assistant_id": winning_id, "other_files": extra_files, "other_assistant_ids": extra_ids},
        )
    return dict(state)


def load_or_init_assistant_state(
    repo_root: Path,
    *,
    assistant_label: str | None = None,
) -> Dict[str, Any]:
    """Load the canonical assistant state or initialize it once."""
    repo_root = Path(repo_root).resolve()
    with _LOCK:
        with _state_file_lock(repo_root):
            assistant_label = str(assistant_label or default_assistant_label())
            return _load_or_init_assistant_state_locked(repo_root, assistant_label=assistant_label)


def read_assistant_state_snapshot(repo_root: Path, *, initialize_if_missing: bool = True) -> Dict[str, Any]:
    """Return current assistant state plus live host health and precedence samples."""
    repo_root = Path(repo_root).resolve()
    with _LOCK:
        if initialize_if_missing:
            state = load_or_init_assistant_state(repo_root)
        else:
            state = _load_state_internal(repo_root) if _state_path(repo_root).exists() else None
        host_health = _lab_host.host_health_snapshot(repo_root, initialize_if_missing=False)
        return {
            "schema_version": "lab_assistant_state_snapshot_v1",
            "utc": _utc_now_iso(),
            "assistant_state": state,
            "host_health": host_health,
            "precedence_samples": {
                "assistant_state": resolve_source_precedence(
                    topic="assistant_state",
                    runtime_value=(state or {}).get("current_state"),
                    chat_value="chat_never_wins",
                ),
                "task_truth": resolve_source_precedence(
                    topic="task_truth",
                    ledger_value="ledger_authoritative",
                    chat_value="chat_never_wins",
                ),
            },
        }


def update_assistant_state(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    current_state: Optional[str] = None,
    state_reason: Optional[str] = None,
    recovery_marker: Optional[Dict[str, Any]] = None,
    last_turn_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply one assistant-state mutation using compare-and-swap semantics."""
    repo_root = Path(repo_root).resolve()
    with _LOCK:
        with _state_file_lock(repo_root):
            state = _load_or_init_assistant_state_locked(
                repo_root, assistant_label=default_assistant_label()
            )
            current_generation = int(state.get("write_generation") or 0)
            if int(expected_generation) != current_generation:
                _append_event(
                    repo_root,
                    event_type="assistant_write_rejected",
                    payload={
                        "writer": str(writer or ""),
                        "expected_generation": int(expected_generation),
                        "current_generation": current_generation,
                    },
                )
                raise AssistantStateError(
                    f"stale write rejected: expected_generation={int(expected_generation)} current_generation={current_generation}"
                )
            changed: Dict[str, Any] = {}
            if current_state is not None:
                st = str(current_state).strip()
                if st not in ASSISTANT_ALLOWED_STATES:
                    raise AssistantStateError(f"invalid current_state: {st}")
                if st != state.get("current_state"):
                    state["current_state"] = st
                    state["state_since_utc"] = _utc_now_iso()
                    changed["current_state"] = st
            if state_reason is not None and str(state_reason) != str(state.get("state_reason") or ""):
                state["state_reason"] = str(state_reason)
                changed["state_reason"] = str(state_reason)
            if recovery_marker is not None and dict(recovery_marker) != dict(state.get("recovery_marker") or {}):
                state["recovery_marker"] = dict(recovery_marker)
                changed["recovery_marker"] = dict(recovery_marker)
            if last_turn_utc is not None and str(last_turn_utc) != str(state.get("last_turn_utc") or ""):
                state["last_turn_utc"] = str(last_turn_utc)
                changed["last_turn_utc"] = str(last_turn_utc)
            if not changed:
                return {"ok": True, "changed": False, "assistant_state": dict(state)}
            state["write_generation"] = current_generation + 1
            state["last_writer"] = str(writer or "")
            state["last_written_utc"] = _utc_now_iso()
            _write_json_atomic(_state_path(repo_root), state)
            _append_event(
                repo_root,
                event_type="assistant_state_changed",
                payload={"writer": str(writer or ""), "new_generation": state["write_generation"], "changed": changed},
            )
            return {"ok": True, "changed": True, "assistant_state": dict(state)}
