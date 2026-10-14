"""Common deterministic tooling helpers.

Last modified: 2026-03-30 23:55 Europe/Zurich
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

TOOL_VERSION = "2026-03-30"
_TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".sh",
    ".cfg",
    ".ini",
    ".properties",
    ".csv",
    ".tsv",
}


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_json_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def hash_text(text: str) -> str:
    return hash_bytes(str(text).encode("utf-8"))


def hash_object(obj: Any) -> str:
    return hash_text(stable_json_dumps(obj))


def repo_rel(repo_root: Path, path: Path | str | None) -> str:
    if path is None:
        return ""
    value = Path(path)
    try:
        return value.resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except Exception:
        return value.as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_json(path: Path) -> Any:
    return json.loads(read_text(path))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def git_head(repo_root: Path) -> str:
    try:
        cp = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except Exception:
        return "unknown"
    text = str(cp.stdout or "").strip()
    return text or "unknown"


def ensure_runtime_dir(repo_root: Path, *parts: str) -> Path:
    path = Path(repo_root).resolve() / "runtime" / "state"
    for part in parts:
        path = path / part
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_cache(path: Path, *, ttl_s: float | None = None) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = read_json(path)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if ttl_s is not None:
        created = str(payload.get("created_utc") or "").strip()
        if created:
            try:
                created_dt = _dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
                age = (_dt.datetime.now(_dt.timezone.utc) - created_dt).total_seconds()
                if age > float(ttl_s):
                    return None
            except Exception:
                return None
    return payload


def store_cache(path: Path, payload: Mapping[str, Any]) -> None:
    envelope = dict(payload)
    envelope.setdefault("created_utc", utc_now_iso())
    write_json(path, envelope)


def safe_uuid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def find_item_dir_for_uid(repo_root: Path, uid: str) -> Path | None:
    prefix = f"{uid}__"
    ledger_root = Path(repo_root).resolve() / "governance" / "ledger"
    candidate_roots = [
        ledger_root / "patch_instruction_sheets",
        ledger_root / "items",  # Legacy fallback for older tooling/layouts.
    ]
    for root in candidate_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob(f"{uid}*")):
            if path.is_dir() and path.name.startswith(prefix):
                return path
    return None


def iter_existing_paths(repo_root: Path, values: Sequence[str | Path]) -> list[Path]:
    seen: list[Path] = []
    for raw in list(values or []):
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        path = Path(repo_root).resolve() / text if not Path(text).is_absolute() else Path(text).resolve()
        if path.exists() and path not in seen:
            seen.append(path)
    return sorted(seen, key=lambda item: repo_rel(repo_root, item))


def is_text_file(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_EXTENSIONS


def estimate_tokens(text: str) -> int:
    return max(1, (len(str(text or "")) + 3) // 4)


def excerpt_text(text: str, *, max_lines: int = 80, max_chars: int = 2400) -> tuple[str, tuple[int, int]]:
    lines = str(text).splitlines()
    if not lines:
        return "", (0, 0)
    start = 1
    # Prefer the first non-empty line, but keep deterministic behavior.
    while start <= len(lines) and not lines[start - 1].strip():
        start += 1
    if start > len(lines):
        start = 1
    collected: list[str] = []
    used = 0
    end = start - 1
    for idx in range(start - 1, min(len(lines), start - 1 + max_lines)):
        line = lines[idx]
        extra = len(line) + 1
        if collected and (used + extra) > max_chars:
            break
        collected.append(line)
        used += extra
        end = idx + 1
    excerpt = "\n".join(collected).strip("\n")
    return excerpt, (start, end)


def file_excerpt(path: Path, *, max_lines: int = 80, max_chars: int = 2400) -> dict[str, Any]:
    text = read_text(path)
    excerpt, (start, end) = excerpt_text(text, max_lines=max_lines, max_chars=max_chars)
    return {
        "path": str(path),
        "start_line": start,
        "end_line": end,
        "content": excerpt,
        "content_hash": hash_text(excerpt),
    }


def canonical_source(repo_root: Path, path: Path, *, ref_type: str = "file", line_span: tuple[int, int] | None = None) -> dict[str, Any]:
    ref: dict[str, Any] = {
        "ref_type": ref_type,
        "ref": repo_rel(repo_root, path),
        "content_hash": hash_text(read_text(path)) if path.exists() and is_text_file(path) else "sha256:missing",
    }
    if line_span is not None:
        ref["line_span"] = [int(line_span[0]), int(line_span[1])]
    return ref


def normalized_list(values: Iterable[str | None]) -> list[str]:
    out: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def parse_run_timestamp(value: str) -> str:
    match = re.search(r"(20\d{6}T\d{6}Z)", str(value or ""))
    if not match:
        return ""
    raw = match.group(1)
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}T{raw[9:11]}:{raw[11:13]}:{raw[13:15]}Z"


def build_envelope(
    *,
    schema_version: str,
    tool_name: str,
    inputs: Mapping[str, Any],
    outputs: Mapping[str, Any],
    warnings: Sequence[Mapping[str, Any] | str] = (),
    errors: Sequence[Mapping[str, Any] | str] = (),
    provenance: Sequence[Mapping[str, Any]] = (),
    artifacts: Sequence[Mapping[str, Any]] = (),
    tool_version: str = TOOL_VERSION,
    run_id: str | None = None,
) -> dict[str, Any]:
    warning_rows = [
        row if isinstance(row, Mapping) else {"code": "warning", "severity": "warning", "message": str(row)}
        for row in list(warnings or [])
    ]
    error_rows = [
        row if isinstance(row, Mapping) else {"code": "error", "severity": "error", "message": str(row)}
        for row in list(errors or [])
    ]
    envelope = {
        "envelope_schema_version": schema_version,
        "tool_name": tool_name,
        "tool_version": tool_version,
        "run_id": run_id or safe_uuid(tool_name),
        "timestamp_utc": utc_now_iso(),
        "inputs": dict(inputs or {}),
        "input_hash": hash_object(inputs or {}),
        "outputs": dict(outputs or {}),
        "output_hash": hash_object(outputs or {}),
        "artifacts": list(artifacts or []),
        "warnings": warning_rows,
        "errors": error_rows,
        "provenance": {"canonical_sources": list(provenance or [])},
    }
    return envelope


def write_output(path: Path | None, payload: Mapping[str, Any]) -> None:
    if path is None:
        return
    write_json(path, payload)


def parse_properties_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        name = key.strip()
        if name:
            out[name] = value.strip()
    return out
