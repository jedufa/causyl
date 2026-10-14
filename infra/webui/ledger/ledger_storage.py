"""infra.webui.ledger.ledger_storage

Filesystem utilities for Change Ledger.

Constraints
-----------
- Keep I/O boring and atomic.
- Enforce directory containment.
- Avoid symlinks.

This module does NOT depend on infra.webui.server to avoid circular
imports.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import datetime as _dt
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


_UID_RE = re.compile(r"^PIS-\d{8}-\d{6}Z-[A-Z0-9]{4}$")
_REGISTRY_SCHEMA_VERSION = "ledger_uid_registry_v1"
_REGISTRY_FILENAME = "uid_index.json"
_ATOMIC_WRITE_COUNTER_LOCK = threading.Lock()
_ATOMIC_WRITE_COUNTER = 0
_ACTIVE_STATUS_SET = {"draft", "active", "in_progress", "blocked"}
_DEFERRED_STATUS_SET = {"deferred"}
_VERIFIED_STATUS_SET = {"verified", "done", "completed"}
_DEPRECATED_STATUS_SET = {"deprecated", "superseded"}
_ARCHIVE_STATUS_SET = {"archived"}
_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def repo_root_from_here() -> Path:
    """Return repo root, assuming this file is under infra/webui/ledger/."""

    return Path(__file__).resolve().parents[3]


def ledger_patch_instruction_sheets_root(repo_root: Optional[Path] = None) -> Path:
    """Return the Patch Instruction Sheets root directory (created if missing)."""

    rr = Path(repo_root or repo_root_from_here()).resolve()
    p = (rr / "governance" / "ledger" / "patch_instruction_sheets").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def normalize_project_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "causyl"
    if not _PROJECT_ID_RE.fullmatch(text):
        raise ValueError(f"invalid project_id: {text}")
    return text


def validate_uid(uid: str) -> str:
    """Validate UID shape."""

    u = str(uid or "").strip()
    if not _UID_RE.match(u):
        raise ValueError(f"invalid uid: {u}")
    return u


def sanitize_slug(raw: str) -> str:
    """Sanitize slug to kebab-case, bounded."""

    s = str(raw or "").strip().lower()
    if not s:
        return "change"
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        s = "change"
    if len(s) > 80:
        s = s[:80].rstrip("-")
    return s


def safe_resolve_under(base: Path, p: Path) -> Optional[Path]:
    """Resolve p and ensure it stays under base."""

    try:
        b = Path(base).resolve()
        c = Path(p).resolve()
        c.relative_to(b)
        return c
    except Exception:
        return None


def _next_atomic_write_counter() -> int:
    global _ATOMIC_WRITE_COUNTER
    with _ATOMIC_WRITE_COUNTER_LOCK:
        _ATOMIC_WRITE_COUNTER += 1
        return int(_ATOMIC_WRITE_COUNTER)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomic text write (tmp + os.replace)."""

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / f".{p.name}.{os.getpid()}.{threading.get_ident()}.{_next_atomic_write_counter()}.tmp"
    try:
        with open(tmp, "w", encoding=encoding, newline="\n") as f:
            f.write(text or "")
            if text is not None and (not str(text).endswith("\n")):
                f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, obj: Any) -> None:
    """Atomic canonical-ish JSON write."""

    txt = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    atomic_write_text(path, txt)


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    """Best-effort JSON reader (dict only), refuses symlinks."""

    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return None
        if p.is_symlink():
            return None
        obj = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def list_item_dirs(items_root: Path) -> List[Path]:
    """List ledger item directories under items_root, including sharded layouts."""

    root = Path(items_root).resolve()
    out: List[Path] = []
    if not root.exists() or not root.is_dir():
        return out

    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            children = sorted(cur.iterdir(), key=lambda x: x.name, reverse=True)
        except Exception:
            continue
        for child in children:
            try:
                if child.is_symlink() or (not child.is_dir()):
                    continue
            except Exception:
                continue
            if _looks_like_item_dir(child):
                out.append(child.resolve())
                continue
            stack.append(child)
    return sorted(out, key=lambda p: str(p.relative_to(root)).replace("\\", "/"))


def _looks_like_item_dir(path: Path) -> bool:
    try:
        if not path.is_dir() or path.is_symlink():
            return False
        meta = path / "meta.json"
        pis = path / "pis.md"
        return meta.is_file() and (not meta.is_symlink()) and pis.is_file() and (not pis.is_symlink())
    except Exception:
        return False


def _candidate_uid_dir(path: Path, uid: str) -> bool:
    try:
        if not path.is_dir() or path.is_symlink():
            return False
        return path.name.startswith(uid + "__")
    except Exception:
        return False


def _raw_uid_dir_matches(items_root: Path, uid: str) -> List[Path]:
    """Compatibility fallback for callers that create uid directories before files exist."""

    root = Path(items_root)
    out: List[Path] = []
    if not root.exists() or not root.is_dir():
        return out
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            children = sorted(cur.iterdir(), key=lambda x: x.name, reverse=True)
        except Exception:
            continue
        for child in children:
            try:
                if child.is_symlink() or (not child.is_dir()):
                    continue
            except Exception:
                continue
            if _candidate_uid_dir(child, uid):
                out.append(child.resolve())
                continue
            stack.append(child)
    return sorted(out)


def ledger_registry_path(items_root: Path) -> Path:
    return Path(items_root).resolve() / _REGISTRY_FILENAME


def classify_storage_bucket(meta: Optional[Dict[str, Any]]) -> str:
    status = str((meta or {}).get("status") or "").strip().lower()
    if status in _DEFERRED_STATUS_SET:
        return "deferred"
    if status in _VERIFIED_STATUS_SET:
        return "verified"
    if status in _DEPRECATED_STATUS_SET:
        return "deprecated"
    if status in _ARCHIVE_STATUS_SET:
        return "archive"
    return "active"


def _year_month_for_item(uid: str, *, created_utc: str = "") -> tuple[str, str]:
    created_text = str(created_utc or "").strip()
    if created_text:
        try:
            dt = _dt.datetime.fromisoformat(created_text.replace("Z", "+00:00"))
            return dt.strftime("%Y"), dt.strftime("%m")
        except Exception:
            pass
    u = validate_uid(uid)
    return u[4:8], u[8:10]


def planned_item_dir(
    items_root: Path,
    *,
    uid: str,
    slug: str,
    project_id: str = "",
    status: str = "",
    created_utc: str = "",
    storage_layout: str = "flat",
) -> Path:
    root = Path(items_root).resolve()
    u = validate_uid(uid)
    s = sanitize_slug(slug)
    layout = str(storage_layout or "flat").strip().lower()
    if layout in {"", "flat"}:
        return (root / f"{u}__{s}").resolve()
    if layout in {"status_sharded", "lifecycle_sharded"}:
        project = normalize_project_id(project_id)
        bucket = classify_storage_bucket({"status": status})
        year, month = _year_month_for_item(u, created_utc=created_utc)
        return (root / project / bucket / year / month / f"{u}__{s}").resolve()
    raise ValueError(f"unsupported storage_layout: {storage_layout}")


def _registry_entry_is_valid(items_root: Path, uid: str, entry: Dict[str, Any]) -> Optional[Path]:
    relpath = str(entry.get("path") or "").strip()
    if not relpath:
        return None
    candidate = safe_resolve_under(Path(items_root).resolve(), Path(items_root).resolve() / relpath)
    if candidate is None or not _looks_like_item_dir(candidate):
        return None
    meta = read_json(candidate / "meta.json") or {}
    if str(meta.get("uid") or "").strip() != uid:
        return None
    return candidate


def _registry_duplicate_entry_is_valid(items_root: Path, relpath: Any) -> bool:
    normalized = str(relpath or "").strip()
    if not normalized:
        return False
    candidate = safe_resolve_under(Path(items_root).resolve(), Path(items_root).resolve() / normalized)
    return candidate is not None and _looks_like_item_dir(candidate)


def load_uid_registry(items_root: Path) -> Dict[str, Any]:
    root = Path(items_root).resolve()
    path = ledger_registry_path(root)
    obj = read_json(path)
    if not isinstance(obj, dict):
        return {}
    if str(obj.get("schema_version") or "") != _REGISTRY_SCHEMA_VERSION:
        return {}
    items = obj.get("items")
    duplicates = obj.get("duplicates")
    if not isinstance(items, dict) or not isinstance(duplicates, dict):
        return {}
    stale_items = any(
        not isinstance(entry, dict) or _registry_entry_is_valid(root, uid, entry) is None
        for uid, entry in items.items()
    )
    stale_duplicates = any(
        not isinstance(relpaths, list)
        or any(not _registry_duplicate_entry_is_valid(root, relpath) for relpath in relpaths)
        for relpaths in duplicates.values()
    )
    if stale_items or stale_duplicates:
        repaired = build_uid_registry(root)
        atomic_write_json(path, repaired)
        return repaired
    return obj


def build_uid_registry(items_root: Path) -> Dict[str, Any]:
    root = Path(items_root).resolve()
    items: Dict[str, Dict[str, Any]] = {}
    duplicates: Dict[str, List[str]] = {}
    for item_dir in list_item_dirs(root):
        meta = read_json(item_dir / "meta.json") or {}
        uid = str(meta.get("uid") or item_dir.name.split("__", 1)[0]).strip()
        if not uid:
            continue
        relpath = str(item_dir.relative_to(root)).replace("\\", "/")
        entry = {
            "path": relpath,
            "slug": str(meta.get("slug") or item_dir.name.split("__", 1)[-1]).strip(),
            "title": str(meta.get("title") or uid).strip(),
            "status": str(meta.get("status") or "draft").strip(),
            "project_id": normalize_project_id(meta.get("project_id")),
            "storage_bucket": classify_storage_bucket(meta),
            "created_utc": str(meta.get("created_utc") or "").strip(),
            "updated_utc": str(meta.get("updated_utc") or meta.get("created_utc") or "").strip(),
        }
        if uid in items:
            duplicates.setdefault(uid, [str(items[uid].get("path") or "")]).append(relpath)
            continue
        items[uid] = entry
    return {
        "schema_version": _REGISTRY_SCHEMA_VERSION,
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "items": items,
        "duplicates": duplicates,
    }


def write_uid_registry(items_root: Path) -> Dict[str, Any]:
    registry = build_uid_registry(items_root)
    atomic_write_json(ledger_registry_path(items_root), registry)
    return registry


def item_dir_for_uid(items_root: Path, uid: str) -> Optional[Path]:
    """Find item directory by uid using registry first, then recursive scan."""

    u = validate_uid(uid)
    root = Path(items_root)
    if not root.exists() or not root.is_dir():
        return None
    registry = load_uid_registry(root)
    duplicates = registry.get("duplicates") if isinstance(registry.get("duplicates"), dict) else {}
    if isinstance(duplicates, dict) and duplicates.get(u):
        return None
    items = registry.get("items") if isinstance(registry.get("items"), dict) else {}
    entry = items.get(u) if isinstance(items, dict) else None
    if isinstance(entry, dict):
        candidate = _registry_entry_is_valid(root, u, entry)
        if candidate is not None:
            return candidate.resolve()

    matches: List[Path] = []
    pref = u + "__"
    for item_dir in list_item_dirs(root):
        try:
            if item_dir.name.startswith(pref):
                matches.append(item_dir.resolve())
                continue
            meta = read_json(item_dir / "meta.json") or {}
            if str(meta.get("uid") or "").strip() == u:
                matches.append(item_dir.resolve())
        except Exception:
            continue
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return None
    if len(matches) == 0:
        raw_matches = _raw_uid_dir_matches(root, u)
        if len(raw_matches) == 1:
            return raw_matches[0]
        if len(raw_matches) > 1:
            return None
    return None


def duplicate_paths_for_uid(items_root: Path, uid: str) -> List[Path]:
    u = validate_uid(uid)
    root = Path(items_root).resolve()
    registry = load_uid_registry(root)
    duplicates = registry.get("duplicates") if isinstance(registry.get("duplicates"), dict) else {}
    relpaths = list(duplicates.get(u) or []) if isinstance(duplicates, dict) else []
    out: List[Path] = []
    for relpath in relpaths:
        candidate = safe_resolve_under(root, root / str(relpath))
        if candidate is not None:
            out.append(candidate.resolve())
    if out:
        return out
    pref = u + "__"
    matches: List[Path] = []
    for item_dir in list_item_dirs(root):
        try:
            meta = read_json(item_dir / "meta.json") or {}
            if item_dir.name.startswith(pref) or str(meta.get("uid") or "").strip() == u:
                matches.append(item_dir.resolve())
        except Exception:
            continue
    return matches if len(matches) > 1 else []


def ensure_item_dir(
    items_root: Path,
    *,
    uid: str,
    slug: str,
    project_id: str = "",
    status: str = "",
    created_utc: str = "",
    storage_layout: str = "flat",
) -> Path:
    """Create an item directory for uid/slug."""

    u = validate_uid(uid)
    s = sanitize_slug(slug)
    root = Path(items_root).resolve()
    if item_dir_for_uid(root, u) is not None or duplicate_paths_for_uid(root, u):
        raise FileExistsError(f"uid already exists: {u}")
    d = planned_item_dir(
        root,
        uid=u,
        slug=s,
        project_id=project_id,
        status=status,
        created_utc=created_utc,
        storage_layout=storage_layout,
    )
    safe = safe_resolve_under(root, d)
    if safe is None:
        raise ValueError("item path escapes items_root")
    safe.mkdir(parents=True, exist_ok=False)
    (safe / "artifacts").mkdir(parents=True, exist_ok=True)
    return safe


def relocate_item_dir(
    items_root: Path,
    *,
    item_dir: Path,
    uid: str,
    slug: str,
    project_id: str = "",
    status: str = "",
    created_utc: str = "",
    storage_layout: str = "flat",
) -> Path:
    """Move an existing item directory into its planned storage location."""

    root = Path(items_root).resolve()
    current = Path(item_dir).resolve()
    safe_current = safe_resolve_under(root, current)
    if safe_current is None or not safe_current.exists() or not safe_current.is_dir():
        raise ValueError("item_dir must exist under items_root")
    target = planned_item_dir(
        root,
        uid=uid,
        slug=slug,
        project_id=project_id,
        status=status,
        created_utc=created_utc,
        storage_layout=storage_layout,
    )
    safe_target = safe_resolve_under(root, target)
    if safe_target is None:
        raise ValueError("target item path escapes items_root")
    if safe_current == safe_target:
        return safe_current
    if safe_target.exists():
        raise FileExistsError(f"target already exists: {safe_target}")
    safe_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(safe_current), str(safe_target))

    prune = safe_current.parent
    while prune != root and prune.exists():
        try:
            prune.rmdir()
        except OSError:
            break
        prune = prune.parent
    return safe_target.resolve()


def read_text(path: Path, *, max_bytes: int = 512 * 1024) -> str:
    """Best-effort text read with size cap."""

    try:
        p = Path(path)
        if not p.exists() or not p.is_file() or p.is_symlink():
            return ""
        if p.stat().st_size > int(max_bytes):
            data = p.read_bytes()[:max_bytes]
            return data.decode("utf-8", errors="replace") + "\n... (truncated)\n"
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def list_artifacts(item_dir: Path) -> List[Dict[str, Any]]:
    """List artifacts under item_dir/artifacts."""

    ad = Path(item_dir) / "artifacts"
    out: List[Dict[str, Any]] = []
    if not ad.exists() or not ad.is_dir() or ad.is_symlink():
        return out
    for p in sorted(ad.rglob("*"), key=lambda x: str(x)):
        try:
            if p.is_symlink() or (not p.is_file()):
                continue
            rel = p.relative_to(item_dir).as_posix()
            out.append({"relpath": rel, "bytes": int(p.stat().st_size)})
        except Exception:
            continue
    return out


def safe_remove_dir(path: Path) -> None:
    """Remove a directory tree (used only for cleanup on failed create)."""

    try:
        shutil.rmtree(Path(path))
    except Exception:
        return
