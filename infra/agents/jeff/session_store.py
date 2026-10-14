from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


SESSIONS_ROOT = Path(__file__).resolve().parent / "sessions"


def utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sessions_root() -> Path:
    SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
    return SESSIONS_ROOT


def new_session_id() -> str:
    return f"jeff_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"


def session_dir(session_id: str) -> Path:
    return sessions_root() / str(session_id or "").strip()


def session_path(session_id: str) -> Path:
    sid = str(session_id or "").strip()
    return sessions_root() / f"{sid}.json"


def state_path(session_id: str) -> Path:
    return session_dir(session_id) / "state.json"


def transcript_path(session_id: str) -> Path:
    return session_dir(session_id) / "transcript.jsonl"


def checkpoints_dir(session_id: str) -> Path:
    return session_dir(session_id) / "checkpoints"


def undone_checkpoints_dir(session_id: str) -> Path:
    return checkpoints_dir(session_id) / "undone"


def artifacts_dir(session_id: str) -> Path:
    return session_dir(session_id) / "artifacts"


def _session_paths(session_id: str) -> List[Path]:
    return [
        state_path(session_id),
        transcript_path(session_id),
        checkpoints_dir(session_id),
        undone_checkpoints_dir(session_id),
        artifacts_dir(session_id),
    ]


def ensure_session_layout(session_id: str) -> Path:
    root = session_dir(session_id)
    state_path(session_id).parent.mkdir(parents=True, exist_ok=True)
    checkpoints_dir(session_id).mkdir(parents=True, exist_ok=True)
    undone_checkpoints_dir(session_id).mkdir(parents=True, exist_ok=True)
    artifacts_dir(session_id).mkdir(parents=True, exist_ok=True)
    transcript_path(session_id).touch(exist_ok=True)
    return root


def new_session(*, repo_path: str) -> Dict[str, Any]:
    now = utc_iso()
    session = {
        "schema_version": "jeff_session_v2",
        "session_id": new_session_id(),
        "repo_path": str(repo_path),
        "created_utc": now,
        "updated_utc": now,
        "turns": [],
        "checkpoints": [],
        "stage_logs": [],
        "last_checkpoint_id": "",
    }
    ensure_session_layout(str(session["session_id"]))
    return session


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _migrate_legacy_flat_session(session_id: str) -> Dict[str, Any]:
    legacy = session_path(session_id)
    state = _read_json(legacy)
    save_session(state)
    try:
        legacy.unlink()
    except Exception:
        pass
    return _read_json(state_path(session_id))


def load_session(session_id: str) -> Dict[str, Any]:
    sid = str(session_id or "").strip()
    current = state_path(sid)
    if current.exists():
        ensure_session_layout(sid)
        return _read_json(current)
    legacy = session_path(sid)
    if legacy.exists():
        return _migrate_legacy_flat_session(sid)
    raise FileNotFoundError(f"session not found: {sid}")


def save_session(session: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(session)
    sid = str(out.get("session_id") or "").strip()
    if not sid:
        raise ValueError("session_id is required")
    out["updated_utc"] = utc_iso()
    ensure_session_layout(sid)
    _write_json(state_path(sid), out)
    return out


def append_transcript_row(session_id: str, row: Dict[str, Any]) -> None:
    ensure_session_layout(session_id)
    path = transcript_path(session_id)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def checkpoint_file(session_id: str, checkpoint_id: str) -> Path:
    return checkpoints_dir(session_id) / f"{str(checkpoint_id or '').strip()}.json"


def checkpoint_undone_file(session_id: str, checkpoint_id: str) -> Path:
    return undone_checkpoints_dir(session_id) / f"{str(checkpoint_id or '').strip()}.json"


def save_checkpoint(session_id: str, checkpoint: Dict[str, Any]) -> Path:
    ensure_session_layout(session_id)
    path = checkpoint_file(session_id, str(checkpoint.get("checkpoint_id") or ""))
    _write_json(path, checkpoint)
    return path


def load_checkpoint(session_id: str, checkpoint_id: str) -> Dict[str, Any]:
    return _read_json(checkpoint_file(session_id, checkpoint_id))


def archive_checkpoint(session_id: str, checkpoint_id: str) -> Path:
    src = checkpoint_file(session_id, checkpoint_id)
    dst = checkpoint_undone_file(session_id, checkpoint_id)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return dst
