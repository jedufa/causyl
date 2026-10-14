"""WebUI server module (extracted from server.py to keep things maintainable)."""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_run_discovery import _read_json, _read_text  # noqa: F401
# -----------------------------------------------------------------------------
# UI-owned metadata (ui/)
# -----------------------------------------------------------------------------


_UI_META_SCHEMA = "ui_meta_v1"


def ui_dir(run_root: Path) -> Path:
    """Return UI directory path (<run_root>/ui)."""
    return Path(run_root) / "ui"


def read_ui_meta(run_root: Path) -> Dict[str, Any]:
    """Read <run_root>/ui/ui_meta.json (best-effort)."""
    p = ui_dir(run_root) / "ui_meta.json"
    obj = _read_json(p)
    if not isinstance(obj, dict):
        return {"schema_version": _UI_META_SCHEMA, "archived": False, "label": None, "tags": []}

    archived = bool(obj.get("archived", False))
    label = obj.get("label")
    tags = obj.get("tags") if isinstance(obj.get("tags"), list) else []

    return {
        "schema_version": _UI_META_SCHEMA,
        "archived": archived,
        "label": label if isinstance(label, str) or label is None else str(label),
        "tags": [str(t) for t in tags if t is not None],
        "updated_at_utc": obj.get("updated_at_utc"),
    }


def write_ui_meta(run_root: Path, *, archived: Optional[bool] = None, label: Optional[str] = None, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """Write <run_root>/ui/ui_meta.json (atomic)."""
    cur = read_ui_meta(run_root)
    if archived is not None:
        cur["archived"] = bool(archived)
    if label is not None:
        cur["label"] = str(label) if label != "" else None
    if tags is not None:
        cur["tags"] = [str(t) for t in tags]

    cur["schema_version"] = _UI_META_SCHEMA
    cur["updated_at_utc"] = _now_utc_iso()

    out = ui_dir(run_root) / "ui_meta.json"
    atomic_write_canonical_json(out, cur)
    return cur


def notes_path(run_root: Path) -> Path:
    """Return <run_root>/ui/notes.md path."""
    return ui_dir(run_root) / "notes.md"


def read_notes(run_root: Path, *, max_bytes: int = 256 * 1024) -> str:
    """Read notes.md (best-effort)."""
    p = notes_path(run_root)
    txt = _read_text(p, max_bytes=max_bytes)
    return txt if txt is not None else ""


def write_notes(run_root: Path, text: str) -> None:
    """Write notes.md (atomic)."""
    atomic_write_text(notes_path(run_root), text or "")


def notes_excerpt(run_root: Path, *, max_chars: int = 200) -> str:
    """Return a one-line excerpt suitable for tables."""
    txt = read_notes(run_root, max_bytes=64 * 1024).strip()
    if not txt:
        return ""
    # Squash whitespace.
    one = " ".join(txt.split())
    if len(one) > int(max_chars):
        return one[: int(max_chars) - 1] + "…"
    return one


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
