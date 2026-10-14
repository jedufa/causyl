"""WebUI server module (extracted from server.py to keep things maintainable)."""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_run_discovery import *  # noqa: F403,F401
# -----------------------------------------------------------------------------
# Deprecated index-card builders (Phase 5c legacy)
# -----------------------------------------------------------------------------


def build_checkpoints_index(run_root: Path, sim_root: Path) -> Dict[str, Any]:
    """Build legacy checkpoints/index.json payload.

    Phase 5d UI should NOT rewrite engine indexes. This function is kept only
    because older tooling imported it.

    Returns a dict but does not write.
    """
    bundles = sim_root / "checkpoint_bundles_v2"
    items: List[Dict[str, Any]] = []
    if bundles.exists() and bundles.is_dir():
        for d in sorted(bundles.iterdir(), key=lambda p: p.name):
            if not d.is_dir():
                continue
            m = re.match(r"bundle_gen(\d{6})$", d.name)
            if not m:
                continue
            try:
                gen = int(m.group(1))
            except Exception:
                continue
            meta = _read_json(d / "metadata.json") or {}
            ckpt_id = str(meta.get("checkpoint_id", "") or "")
            items.append({"generation": int(gen), "checkpoint_id": ckpt_id, "relative_path": _posix_relpath(d, sim_root)})

    items.sort(key=lambda x: int(x.get("generation", 0)))
    return {"schema_version": "checkpoints_index_v1", "checkpoints": items}


def ensure_index_cards(
    *,
    runs_root: Path,
    run_key: str,
    run_root: Path,
    sim_root: Path,
    now_utc: str,
) -> None:
    """Deprecated (Phase 5c legacy): previously created index cards on scan.

    Phase 5d constraints forbid the server from auto-writing engine artifacts.
    This function is now a no-op to avoid accidental mutation.

    Kept for backward compatibility; do not call.
    """
    _ = (runs_root, run_key, run_root, sim_root, now_utc)
    return


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
