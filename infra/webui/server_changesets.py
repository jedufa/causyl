"""WebUI server module (extracted from server.py to keep things maintainable)."""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_run_meta import *  # noqa: F403,F401
# -----------------------------------------------------------------------------
# Changesets
# -----------------------------------------------------------------------------


def _changesets_root(_runs_root: Path) -> Path:
    """Return canonical ChangeSets directory under runtime/state.

    Keep the runs_root argument for API compatibility, but store ChangeSets in
    WebUI runtime state so run trees remain disposable.
    """
    return (_webui_runtime_root() / "changesets").resolve()


def list_changesets(runs_root: Path) -> List[Dict[str, Any]]:
    """List ChangeSets from runtime/state/webui/changesets/*.json."""
    out: List[Dict[str, Any]] = []
    root = _changesets_root(runs_root)
    if not root.exists() or not root.is_dir():
        return out

    for p in sorted(root.glob("*.json"), key=lambda x: x.name):
        if p.name.endswith(".tmp"):
            continue
        if p.is_symlink():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(obj, dict):
                continue
            out.append(
                {
                    "id": p.stem,
                    "filename": p.name,
                    "label": obj.get("label") or obj.get("name") or p.stem,
                    "description": obj.get("description") or "",
                    "expectation_note": obj.get("expectation_note") or "",
                    "commands": obj.get("commands") if isinstance(obj.get("commands"), list) else [],
                }
            )
        except Exception:
            continue
    return out


def _load_changeset_by_id(runs_root: Path, cs_id: str) -> Optional[Dict[str, Any]]:
    """Load a single ChangeSet dict by id (stem), enforcing directory boundary."""
    if not cs_id or not re.match(r"^[A-Za-z0-9._-]+$", cs_id):
        return None
    root = _changesets_root(runs_root)
    p = (root / f"{cs_id}.json").resolve()
    try:
        p.relative_to(root)
    except Exception:
        return None
    if not p.exists() or not p.is_file() or p.is_symlink():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None




def _sanitize_snapshot_name_for_filename(name: Optional[str]) -> str:
    """Sanitize an optional user-provided name for safe filenames.

    Rules:
      - Allowed: letters, numbers, dash, underscore, dot
      - Spaces become '_'
      - Trim to 80 chars
      - Empty result -> ''
    """

    if not name:
        return ""
    out = str(name).strip().replace(" ", "_")
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", out)
    out = out.strip("._-")
    if len(out) > 80:
        out = out[:80]
    return out


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
