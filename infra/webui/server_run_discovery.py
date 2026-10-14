"""WebUI server module (extracted from server.py to keep things maintainable)."""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
# -----------------------------------------------------------------------------
# Run discovery
# -----------------------------------------------------------------------------




def _is_valid_run_dir(run_dir: Path) -> bool:
    """Return True if run_dir is a valid run directory (UI-owned markers).

    Single source of truth for planned/managed run qualification.
    This is intentionally conservative and does not depend on telemetry.
    """
    try:
        ui = Path(run_dir) / "ui"
        if not ui.is_dir():
            return False
        return (
            (ui / "run_spec.json").exists()
            or (ui / "start_cmd.json").exists()
            or (ui / "status.json").exists()
            or (ui / "webui_managed.json").exists()
        )
    except Exception:
        return False
def iter_candidate_run_roots(runs_root: Path, *, scan_depth: int = 2) -> Iterable[Tuple[str, Path]]:
    """Yield (run_key, path) for candidate run directories.

    We scan up to scan_depth levels beneath runs_root.

    Heuristic: a directory is a run root if it contains *any* of:
      - viz/run_status.json
      - run_status.json
      - run_manifest.json
      - metrics.csv / behaviors.csv (legacy)
      - checkpoint_bundles_v2/
      - events/ or wal/
      - ui/ (planned runs)

    The goal is to be tolerant of different launchers while avoiding
    descending into obviously unrelated folders.
    """
    root = Path(runs_root)
    if not root.exists() or not root.is_dir():
        return

    def looks_like_run_dir(p: Path) -> bool:
        if not p.is_dir():
            return False
        name = p.name
        if name.startswith("."):
            return False
        if name in {"logs", "tmp", "__pycache__", "changesets"}:
            return False
        if name.endswith(".tmp"):
            return False

        # Wrapper/harness container roots may carry a manifest.json and nest
        # real runs under runs/ or r/. Never classify these containers as runs.
        if (p / "manifest.json").exists():
            if ((p / "runs").exists() and (p / "runs").is_dir()) or ((p / "r").exists() and (p / "r").is_dir()):
                return False

        # Planned runs: tolerate directories that only have UI-owned metadata.
        # This is critical for WebUI-started runs which may not yet have produced telemetry.
        if _is_valid_run_dir(p):
            return True

        if (p / "viz" / "run_status.json").exists() or (p / "run_status.json").exists():
            return True
        if (p / "run_manifest.json").exists():
            return True
        if (p / "metrics.csv").exists() or (p / "behaviors.csv").exists():
            return True

        # Some launcher wrappers include a manifest.json at the wrapper root and
        # store the real run(s) under a nested runs/ directory. Treat those
        # wrapper roots as containers, not runs.
        if (p / "manifest.json").exists():
            runs_dir = p / "runs"
            if runs_dir.exists() and runs_dir.is_dir():
                return False
            r_dir = p / "r"
            if r_dir.exists() and r_dir.is_dir():
                return False
            return True
        if (p / "checkpoint_bundles_v2").exists() or (p / "payload" / "checkpoint_bundles_v2").exists():
            return True
        if (p / "events").exists() or (p / "wal").exists():
            return True
        if (p / "payload" / "viz" / "run_status.json").exists():
            return True

        return False

    def nested_wrapper_runs(container: Path) -> List[Path]:
        """Return real simulation run dirs nested under a wrapper payload.

        Wrapper jobs commonly use:
          <wrapper>/payload/e/<experiment_hash>/<batch_id>/r/<run_id>/

        The normal breadth scan can miss those runs unless scan_depth is set
        very high. This bounded layout scan keeps discovery useful with the
        default scan depth while still requiring normal run markers.
        """

        payload = container / "payload"
        if not (payload.exists() and payload.is_dir()) and container.name == "payload":
            payload = container
        if not (payload.exists() and payload.is_dir()) or payload.is_symlink():
            return []

        out: List[Path] = []
        try:
            for r_dir in sorted(payload.glob("**/r"), key=lambda p: str(p)):
                if (not r_dir.is_dir()) or r_dir.is_symlink():
                    continue
                try:
                    children = sorted([c for c in r_dir.iterdir() if c.is_dir()], key=lambda p: p.name)
                except Exception:
                    continue
                for cand in children:
                    if cand.name.startswith(".") or cand.is_symlink():
                        continue
                    if looks_like_run_dir(cand):
                        out.append(cand)
        except Exception:
            return []
        return out

    queue: List[Tuple[Path, int]] = [(root, 0)]
    seen: set[str] = set()

    while queue:
        cur, depth = queue.pop(0)
        try:
            children = sorted([c for c in cur.iterdir() if c.is_dir()], key=lambda x: x.name)
        except Exception:
            continue

        for child in children:
            if child.name.startswith("."):
                continue
            if child.name in {"__pycache__", ".ui_cache"}:
                continue

            rk = _posix_relpath(child, root)
            if rk in seen:
                continue
            seen.add(rk)

            if looks_like_run_dir(child):
                yield rk, child
                continue

            nested = nested_wrapper_runs(child)
            if nested:
                for run_dir in nested:
                    nrk = _posix_relpath(run_dir, root)
                    if nrk in seen:
                        continue
                    seen.add(nrk)
                    yield nrk, run_dir
                continue

            if depth + 1 < int(scan_depth):
                queue.append((child, depth + 1))


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    """Best-effort JSON reader (dict only)."""
    try:
        if not path.exists() or not path.is_file():
            return None
        # Refuse symlink escapes.
        if path.is_symlink():
            return None
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _read_text(path: Path, *, max_bytes: int = 1024 * 1024) -> Optional[str]:
    """Best-effort text reader with size cap."""
    try:
        if not path.exists() or not path.is_file():
            return None
        if path.is_symlink():
            return None
        if path.stat().st_size > int(max_bytes):
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _read_first_line(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readline().strip("\r\n")
    except Exception:
        return ""


def _read_last_generation_from_csv(path: Path) -> Optional[int]:
    """Parse last generation int from a CSV with a 'generation' column."""
    try:
        if not path.exists() or not path.is_file():
            return None
        header = _read_first_line(path)
        if not header:
            return None
        cols = [c.strip() for c in header.split(",")]
        if "generation" not in cols:
            return None
        gen_idx = cols.index("generation")
        lines = tail_lines(path, max_lines=5)
        for raw in reversed(lines):
            if not raw.strip():
                continue
            if raw.strip().startswith("generation,"):
                continue
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) <= gen_idx:
                continue
            try:
                return int(float(parts[gen_idx]))
            except Exception:
                continue
        return None
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
