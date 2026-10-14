"""WebUI server module (extracted from server.py to keep things maintainable)."""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_run_meta import *  # noqa: F403,F401
from infra.webui.server_ui_metadata import *  # noqa: F403,F401
# -----------------------------------------------------------------------------
# Run archive (runtime/artifacts/archives)
# -----------------------------------------------------------------------------


_ARCHIVE_ZIP_SCHEMA_VERSION = "run_archive_zip_v1"


def runs_archives_dir(runs_root: Path) -> Path:
    """Return canonical run archives directory under runtime/artifacts.

    Policy:
    - Location: <repo_root>/runtime/artifacts/archives
    - This is intentionally outside any individual run folder and outside
      per-run roots so run roots remain disposable.

    Note:
    - This is distinct from the *admin* repo snapshot archives directory
      (which lives at <repo_root>/archives).
    """
    # Keep runs_root arg for API compatibility with existing call sites.
    _ = runs_root
    p = (Path(_repo_root_from_webui()) / "runtime" / "artifacts" / "archives").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


# Conservative filename cap so temp suffixes still fit (ext4 is ~255 bytes per component).
_MAX_ARCHIVE_FILENAME_CHARS = 160


def _archive_run_key_hash12(run_key: str) -> str:
    """Stable short hash of normalized run_key for uniqueness."""
    rk = normalize_run_key(run_key)
    return hashlib.sha256(rk.encode("utf-8")).hexdigest()[:12]


def _safe_archive_slug(run_key: str, *, max_segments: int = 3) -> str:
    """Build a short human-readable slug from the tail of the run_key.

    Strategy:
    - Take last N path segments.
    - Sanitize to [A-Za-z0-9_], collapse repeats.
    - Fall back to 'run' if empty.
    """
    rk = normalize_run_key(run_key).strip("/")
    segs = [s for s in rk.split("/") if s]
    tail = segs[-max_segments:] if segs else []
    base = "__".join(tail) if tail else rk
    base = base.replace("/", "__")
    base = re.sub(r"[^A-Za-z0-9]+", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    return base or "run"


def _bound_archive_filename(stem: str, *, max_chars: int) -> str:
    """Ensure the final archive filename stays under max_chars.

    Only truncates the human slug portion, preserving the tail:
        __{hash12}__{ts}[__n].zip
    """
    fname = str(stem)
    if not fname.endswith(".zip"):
        fname = fname + ".zip"

    if len(fname) <= int(max_chars):
        return fname

    # Expect stem: run_archive__{slug}__{h12}__{ts}[__n].zip
    # Truncate slug to fit while keeping prefix+tail.
    prefix = "run_archive__"
    if not fname.startswith(prefix):
        # Last resort: hard truncate (still better than failing)
        return fname[: int(max_chars) - 4] + ".zip"

    # Split into: run_archive__, slug, h12, ts(+maybe n)
    # We'll keep the last two groups plus any __n suffix.
    base = fname[:-4]  # strip .zip
    parts = base.split("__")
    if len(parts) < 4:
        return fname[: int(max_chars) - 4] + ".zip"

    # parts[0] = 'run_archive', parts[1]=slug, parts[2]=h12, parts[3]=ts or ts__n...
    slug = parts[1]
    tail = "__".join(parts[2:]) + ".zip"
    fixed = prefix + "__"  # 'run_archive__'
    # available for slug:
    avail = int(max_chars) - len(fixed) - len(tail)
    if avail <= 0:
        # Can't preserve slug at all.
        return fixed + tail[-(int(max_chars) - len(fixed)) :]

    slug2 = slug[:avail]
    slug2 = slug2.rstrip("_")
    if not slug2:
        slug2 = "run"
    return fixed + slug2 + "__" + tail


def _archive_index_sidecar_path(zip_path: Path) -> Path:
    """Sidecar mapping from archive zip -> full run_key."""
    zp = Path(zip_path)
    return zp.with_suffix(zp.suffix + ".runkey.json")


def _archive_safe_name(run_key: str) -> str:
    """Convert a run_key into a filesystem-safe archive stem."""
    rk = normalize_run_key(run_key)
    # Keep stable and readable.
    safe = rk.replace("/", "__")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", safe)
    safe = safe.strip("._-")
    return safe or "run"


def _iter_run_tree_for_archive(run_root: Path) -> Tuple[List[str], List[Tuple[str, Path, int, int]]]:
    """Enumerate directories + files under run_root for deterministic zipping.

    Returns:
        (dirs, files)

    Where:
        dirs: list of posix-relative directory paths (no leading '/', no trailing '/')
        files: list of (rel_posix, abs_path, size_bytes, mtime_ns)

    Filtering:
    - Skips symlinks (files and directories).
    - Skips '*.tmp' files.

    Determinism:
    - Traversal is sorted (dirnames + filenames), and returned lists are
      sorted lexicographically.
    """

    rr = Path(run_root).resolve()

    dirs: List[str] = []
    files: List[Tuple[str, Path, int, int]] = []

    for dirpath, dirnames, filenames in os.walk(str(rr)):
        dp = Path(dirpath)

        # Prune symlink dirs and keep stable order.
        kept_dirs: List[str] = []
        for d in sorted(dirnames):
            p = dp / d
            try:
                if p.is_symlink():
                    continue
                if not p.is_dir():
                    continue
            except Exception:
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        # Record this directory (relative to run_root), excluding root itself.
        try:
            rel_dir = dp.relative_to(rr).as_posix()
        except Exception:
            continue
        if rel_dir and rel_dir != ".":
            dirs.append(rel_dir)

        for fn in sorted(filenames):
            if fn.endswith(".tmp"):
                continue
            p = dp / fn
            try:
                if p.is_symlink() or (not p.is_file()):
                    continue
                st = p.stat()
            except Exception:
                continue

            rel = (dp / fn).relative_to(rr).as_posix()
            files.append((rel, p, int(getattr(st, "st_size", 0) or 0), int(getattr(st, "st_mtime_ns", 0) or 0)))

    dirs = sorted(set(dirs))
    files.sort(key=lambda t: t[0])
    return dirs, files


def _write_deterministic_zip(
    out_path: Path,
    *,
    arc_root_prefix: str,
    run_root: Path,
) -> Dict[str, Any]:
    """Write a deterministic zip file representing the entire run_root tree.

    Determinism contract (best-effort):
    - Zip entry order is lexicographically sorted by arcname.
    - Zip entry timestamps are forced to 1980-01-01 00:00:00.
    - Zip entry permissions are normalized (dirs 0755, files 0644).
    - Symlinks are skipped.
    - '*.tmp' files are skipped.

    Concurrency guard (best-effort):
    - Each file's (size, mtime_ns) is captured at enumeration time.
    - If a file changes during the archive, the archive fails rather than
      producing a non-repeatable zip.

    Returns:
        Metadata dict (no UI meta fields).
    """

    rr = Path(run_root).resolve()
    out_path = Path(out_path)

    fixed_dt = (1980, 1, 1, 0, 0, 0)  # zip minimum

    dirs, files = _iter_run_tree_for_archive(rr)

    # Build all arcnames.
    prefix = normalize_run_key(arc_root_prefix).rstrip("/")
    arc_entries: List[Tuple[str, str, Optional[Path], int, int]] = []

    # Root folder entry ensures extraction recreates the run folder even if empty.
    arc_entries.append((f"{prefix}/", "dir", None, 0, 0))

    for d in dirs:
        arc_entries.append((f"{prefix}/{d}/", "dir", None, 0, 0))
    for rel, p, size, mtime_ns in files:
        arc_entries.append((f"{prefix}/{rel}", "file", p, size, mtime_ns))

    arc_entries.sort(key=lambda t: t[0])

    input_bytes = 0
    file_count = 0

    # Use a unique tmp filename to avoid collisions under concurrent archive requests.
    tmp = out_path.parent / (".tmp_run_archive_" + uuid.uuid4().hex + ".zip")
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass

    # ZipFile writes directly to tmp, then we os.replace into place.
    with zipfile.ZipFile(
        tmp,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as z:
        # Ensure no archive comment.
        z.comment = b""

        for arcname, kind, p, size, mtime_ns in arc_entries:
            # zip uses forward slashes.
            arcname = str(arcname).replace("\\", "/")

            if kind == "dir":
                zi = zipfile.ZipInfo(arcname)
                zi.date_time = fixed_dt
                zi.compress_type = zipfile.ZIP_STORED
                zi.create_system = 3  # Unix
                zi.flag_bits |= 0x800  # UTF-8
                zi.external_attr = (0o755 & 0xFFFF) << 16
                zi.extra = b""
                zi.comment = b""
                z.writestr(zi, b"")
                continue

            # File entry.
            if p is None:
                continue

            # Best-effort detect concurrent mutation (size/mtime change).
            try:
                st0 = p.stat()
                sz0 = int(getattr(st0, "st_size", 0) or 0)
                mt0 = int(getattr(st0, "st_mtime_ns", 0) or 0)
            except Exception:
                continue

            if sz0 != int(size) or mt0 != int(mtime_ns):
                raise RuntimeError(f"run file changed during archive enumeration: {p}")

            zi = zipfile.ZipInfo(arcname)
            zi.date_time = fixed_dt
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3  # Unix
            zi.flag_bits |= 0x800  # UTF-8
            zi.external_attr = (0o644 & 0xFFFF) << 16
            zi.file_size = int(sz0)
            zi.extra = b""
            zi.comment = b""

            with z.open(zi, "w", force_zip64=True) as dst, open(p, "rb") as src:
                shutil.copyfileobj(src, dst, length=1024 * 1024)

            try:
                st1 = p.stat()
                sz1 = int(getattr(st1, "st_size", 0) or 0)
                mt1 = int(getattr(st1, "st_mtime_ns", 0) or 0)
            except Exception:
                raise RuntimeError(f"run file disappeared during archive: {p}")

            if sz1 != int(size) or mt1 != int(mtime_ns):
                raise RuntimeError(f"run file changed during archive write: {p}")

            file_count += 1
            input_bytes += int(sz0)

    os.replace(tmp, out_path)

    sha = _sha256_file(out_path)

    return {
        "schema_version": _ARCHIVE_ZIP_SCHEMA_VERSION,
        "status": "ok",
        "utc": _now_utc_iso(),
        "archive_path": str(out_path),
        "archive_bytes": int(out_path.stat().st_size) if out_path.exists() else 0,
        "archive_sha256": sha,
        "input_bytes": int(input_bytes),
        "file_count": int(file_count),
        "arc_prefix": str(prefix),
    }


def create_run_archive_zip(
    *,
    run_key: str,
    run_root: Path,
    runs_root: Path,
    delete_after: bool = False,
) -> Dict[str, Any]:
    """Create a deterministic zip of a run folder under runtime/artifacts/archives.

    User intent (per project instruction):
    - Include all run data (including huge checkpoints).
    - Produce a deterministic zip (stable ordering + stable metadata).
    - Store the zip in: <repo_root>/runtime/artifacts/archives

    Args:
        run_key: Posix-style relative run key under runs_root.
        run_root: Resolved run root directory.
        runs_root: RUNS_ROOT directory.
        delete_after: If True, attempts to delete the run folder after
            successfully creating the archive.
            Default False (safer).

    Returns:
        Dict with archive metadata + download URL.

    Raises:
        ValueError: on unsafe paths or unsupported operations.
        RuntimeError: if files mutate during archive creation.
    """

    rk = normalize_run_key(run_key)
    rr = Path(run_root).resolve()
    root = Path(runs_root).resolve()

    # Must stay under runs_root.
    try:
        rr.relative_to(root)
    except Exception as e:
        raise ValueError("run_root resolves outside runs_root") from e

    adir = runs_archives_dir(root)

    # Disallow archiving the archives directory itself (self-recursion hazard).
    if _safe_resolve_under(adir, rr) is not None:
        raise ValueError("refusing to archive a run inside runtime/artifacts/archives")

    slug = _safe_archive_slug(rk)
    h12 = _archive_run_key_hash12(rk)
    ts = _now_utc_iso().replace(":", "").replace("-", "").replace("Z", "Z")

    # Avoid overwriting existing archives (timestamp resolution is seconds).
    stem0 = f"run_archive__{slug}__{h12}__{ts}"
    fname = _bound_archive_filename(stem0 + ".zip", max_chars=_MAX_ARCHIVE_FILENAME_CHARS)
    out_path = (adir / fname).resolve()

    # Collision-avoid: append __n before .zip and re-bound.
    n = 1
    while out_path.exists():
        stem = f"{stem0}__{n}"
        fname = _bound_archive_filename(stem + ".zip", max_chars=_MAX_ARCHIVE_FILENAME_CHARS)
        out_path = (adir / fname).resolve()
        n += 1
# Final safety: ensure out_path is under adir.
    if _safe_resolve_under(adir, out_path) is None:
        raise ValueError("archive output path escapes archives dir")

    meta = _write_deterministic_zip(out_path, arc_root_prefix=rk, run_root=rr)

    # Sidecar sha file is convenient for humans.
    try:
        atomic_write_text(out_path.with_suffix(out_path.suffix + ".sha256"), str(meta.get("archive_sha256") or ""))
    except Exception:
        pass

    
    # Sidecar run_key index (required once archive filenames are bounded).
    try:
        idx = {
            "schema_version": "run_archive_index_v1",
            "created_utc": _now_utc_iso(),
            "filename": str(fname),
            "run_key": str(rk),
            "archive_sha256": str(meta.get("archive_sha256") or ""),
        }
        atomic_write_canonical_json(_archive_index_sidecar_path(out_path), idx)
    except Exception:
        pass

# Optional delete-after behavior (off by default).
    deleted = False
    if delete_after:
        # Danger zone: do the safest thing we can. We refuse to delete if the
        # run still looks like a live run.
        status_path = rr / "run_status.json"
        if status_path.exists():
            raise ValueError("refusing delete_after=True while run_status.json exists (run may be live)")
        try:
            shutil.rmtree(rr)
            deleted = True
        except Exception as e:
            raise RuntimeError(f"delete_after requested but delete failed: {e}") from e

    meta["filename"] = fname
    meta["relpath"] = f"runtime/artifacts/archives/{fname}"
    meta["download_url"] = f"/api/run/{rk}/archive/{fname}"
    meta["deleted_after"] = bool(deleted)

    return meta


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
