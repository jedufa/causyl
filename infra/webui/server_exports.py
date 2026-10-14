"""WebUI server module (extracted from server.py to keep things maintainable)."""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_run_meta import *  # noqa: F403,F401
from infra.webui.server_ui_metadata import *  # noqa: F403,F401
from infra.webui.server_changesets import _sanitize_snapshot_name_for_filename  # noqa: F401
# -----------------------------------------------------------------------------
# Export snapshot (ui/exports)
# -----------------------------------------------------------------------------


# Export guardrails (analysis-oriented): skip huge blobs by default.
_EXPORT_MAX_FILE_BYTES = 200 * 1024 * 1024
_EXPORT_NPZ_MAX_BYTES = 50 * 1024 * 1024




def _should_skip_export_relpath(rel: str) -> bool:
    """Return True if a relative path should be excluded from analysis exports."""

    r = (rel or "").replace('\\', '/')
    if not r:
        return True
    # Never re-export exports (avoids recursion + huge history).
    if r.startswith('ui/exports'):
        return True
    # Exclude checkpoint bank blobs to keep exports manageable.
    base = r.rsplit('/', 1)[-1]
    if base == 'banks.pt':
        return True
    if re.match(r'^[^/]*banks[^/]*\.pt$', base):
        return True

    # Common heavy tensor artifacts (analysis export is not replay).
    ext = os.path.splitext(base.lower())[1]
    if ext in {'.pt', '.pth', '.ckpt', '.safetensors'}:
        return True

    # Optional: raw media dumps.
    if ext in {'.mp4', '.avi', '.mov'}:
        return True
    return False
def _iter_files_recursive(root: Path) -> Iterable[Path]:
    """Yield files under root, skipping symlinks and *.tmp."""
    for dirpath, dirnames, filenames in os.walk(str(root)):
        # Skip symlinked dirs.
        dp = Path(dirpath)
        # Prune hidden/system.
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and not d.endswith(".tmp") and d not in {"__pycache__"}]
        for fn in filenames:
            if fn.endswith(".tmp"):
                continue
            if fn.startswith("."):
                continue
            p = dp / fn
            try:
                if p.is_symlink() or (not p.is_file()):
                    continue
            except Exception:
                continue
            yield p


# ---------------------------------------------------------------------
# Export Snapshot filename safety
# ---------------------------------------------------------------------

# ext4 path segment limit is typically 255 bytes; be conservative.
_MAX_EXPORT_FILENAME_CHARS = 160


def _safe_export_run_tag(run_key: str) -> str:
    """Return a short, filesystem-safe tag for a run key.

    Motivation:
        Deep manifest/payload run keys can exceed filesystem filename
        component limits (ENAMETOOLONG). We keep the filename bounded
        while preserving traceability via a hash suffix.

    Format:
        <tail>__<sha12>

    Where:
        tail  = last 1-2 path segments of the run key, sanitized
        sha12 = sha256(run_key)[:12]
    """
    rk = normalize_run_key(run_key)
    parts = [p for p in rk.split("/") if p]
    tail_parts = parts[-2:] if len(parts) >= 2 else parts
    tail_raw = "__".join(tail_parts) if tail_parts else "run"

    # Sanitize tail: allow only alnum + underscore.
    tail = re.sub(r"[^A-Za-z0-9_]+", "_", tail_raw).strip("_")
    if not tail:
        tail = "run"

    h = hashlib.sha256(rk.encode("utf-8")).hexdigest()[:12]
    return f"{tail}__{h}"


def _bound_export_filename(fname: str, max_len: int = _MAX_EXPORT_FILENAME_CHARS) -> str:
    """Bound an export zip filename to a safe max length.

    Strategy:
        Preserve the right-side suffix (hash/user/timestamp/.zip) and
        truncate only the leading human-readable segment.
    """
    if len(fname) <= int(max_len):
        return fname

    # Ensure we keep extension.
    ext = ".zip" if fname.endswith(".zip") else ""
    base = fname[: -len(ext)] if ext else fname

    # Keep the last ~64 chars of base as a suffix anchor.
    # This should include "__<hash>__<user>__<ts>" in our construction.
    keep_tail = 64
    tail = base[-keep_tail:] if len(base) > keep_tail else base

    # Prefix should remain recognizable.
    prefix = "export__"
    avail = int(max_len) - len(prefix) - len(tail) - len(ext)
    if avail < 1:
        # Worst-case fallback: just keep the tail.
        return (prefix + tail)[-int(max_len) :]

    head = base[:avail]
    return f"{prefix}{head}{tail}{ext}"


def create_export_zip(run_key: str, run_root: Path, sim_root: Path, *, include_checkpoints: bool = False, name: Optional[str] = None) -> Dict[str, Any]:
    """Create a snapshot zip under <run_root>/ui/exports/.

    The export is read-only: it only reads engine outputs and writes the zip
    into UI-owned storage.

    Returns:
        {"status":"ok","filename":...,"relpath":...,"bytes":...}
    """
    rk = normalize_run_key(run_key)
    exports_dir = ui_dir(run_root) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    # Avoid ENAMETOOLONG for deep manifest/payload run keys.
    short_run_tag = _safe_export_run_tag(rk)
    ts = _now_utc_iso().replace(":", "").replace("-", "").replace("Z", "Z")
    user_part = _sanitize_snapshot_name_for_filename(name)
    if user_part:
        fname = f"export__{short_run_tag}__{user_part}__{ts}.zip"
    else:
        fname = f"export__{short_run_tag}__{ts}.zip"

    fname = _bound_export_filename(fname, _MAX_EXPORT_FILENAME_CHARS)
    out_path = exports_dir / fname

    # File selection (analysis-oriented allowlist-ish).
    roots_to_include: List[Path] = []

    # UI-owned metadata (excluding exports to avoid recursion).
    u = ui_dir(run_root)
    if u.exists() and u.is_dir():
        roots_to_include.append(u)

    # Common analysis artifacts under sim_root.
    # Directories (if present).
    for d in [
        sim_root / "viz",
        sim_root / "events",
        sim_root / "acks",
        sim_root / "logs",
        # ControlPlane / analysis-critical state (best-effort; varies by run).
        sim_root / "wal",
        sim_root / "plans",
        sim_root / "intervention_plans",
        sim_root / "pressure",
        sim_root / "pressures",
        sim_root / "schedules",
        sim_root / "checkpoint_meta",
    ]:
        if d.exists() and d.is_dir() and (not d.is_symlink()):
            roots_to_include.append(d)

    # Root-level metadata (if present).
    for fp in [
        sim_root / "run_manifest.json",
        sim_root / "run_status.json",
        sim_root / "run_determinism_status.json",
        sim_root / "branch_meta.json",
        sim_root / "config.json",
        sim_root / "effective_config.json",
        sim_root / "effective_config.yaml",
        sim_root / "effective_config.yml",
        sim_root / "registry_case.yaml",
        sim_root / "registry_case.yml",
        sim_root / "registry_case.json",
        sim_root / "campaign.json",
        sim_root / "campaign_meta.json",
        sim_root / "pressure_schedule.json",
        sim_root / "shock_schedule.json",
        sim_root / "hazard_schedule.json",
        sim_root / "baseline_config_hash.json",
        sim_root / "baseline_config.json",
        sim_root / "git.json",
        sim_root / "git_meta.json",
        sim_root / "resume_from.json",
        sim_root / "manifest.json",
        sim_root / "ui_meta.json",
        sim_root / "ui_meta_summary.json",
        sim_root / "metrics.csv",
        sim_root / "behaviors.csv",
        sim_root / "metrics.json",
        sim_root / "behaviors.json",
        # Command evidence fallbacks (in case directories are not present).
        sim_root / "command_wal.jsonl",
        sim_root / "event_log.jsonl",
    ]:
        if fp.exists() and fp.is_file() and (not fp.is_symlink()):
            roots_to_include.append(fp)

    # Fallback: include top-level *.log / stdout/stderr style files if present.
    for fp in sorted(sim_root.glob("*.log"), key=lambda p: p.name):
        if fp.exists() and fp.is_file() and (not fp.is_symlink()):
            roots_to_include.append(fp)
    for nm in ["stdout.txt", "stderr.txt", "stdout_stderr.log"]:
        fp = sim_root / nm
        if fp.exists() and fp.is_file() and (not fp.is_symlink()):
            roots_to_include.append(fp)

    if include_checkpoints:
        ck = sim_root / "checkpoint_bundles_v2"
        if ck.exists() and ck.is_dir() and (not ck.is_symlink()):
            roots_to_include.append(ck)

    # Write zip (+ analysis manifest).
    total_bytes = 0
    included_files: List[Dict[str, Any]] = []
    excluded_files_count = 0
    seen_rel: set[str] = set()

    # Size caps (env override for operators who like living dangerously).
    try:
        max_file_bytes = int(os.environ.get("WEBUI_EXPORT_MAX_FILE_BYTES", str(_EXPORT_MAX_FILE_BYTES)) or str(_EXPORT_MAX_FILE_BYTES))
    except Exception:
        max_file_bytes = int(_EXPORT_MAX_FILE_BYTES)
    try:
        npz_max_bytes = int(os.environ.get("WEBUI_EXPORT_NPZ_MAX_BYTES", str(_EXPORT_NPZ_MAX_BYTES)) or str(_EXPORT_NPZ_MAX_BYTES))
    except Exception:
        npz_max_bytes = int(_EXPORT_NPZ_MAX_BYTES)

    def _dir_present(p: Path) -> bool:
        try:
            return p.exists() and p.is_dir() and (not p.is_symlink())
        except Exception:
            return False

    def _file_present(p: Path) -> bool:
        try:
            return p.exists() and p.is_file() and (not p.is_symlink())
        except Exception:
            return False

    sources = {
        "viz_dir": _dir_present(sim_root / "viz"),
        "events_dir": _dir_present(sim_root / "events"),
        "acks_dir": _dir_present(sim_root / "acks"),
        "wal_dir": _dir_present(sim_root / "wal"),
        "wal_file": _file_present(sim_root / "command_wal.jsonl"),
        "plans_dir": _dir_present(sim_root / "plans") or _dir_present(sim_root / "intervention_plans"),
        "pressure_dir": _dir_present(sim_root / "pressure") or _dir_present(sim_root / "pressures"),
        "schedules_dir": _dir_present(sim_root / "schedules"),
        "checkpoint_meta_dir": _dir_present(sim_root / "checkpoint_meta"),
    }

    excluded_rules = [
        "ui/exports recursion",
        "banks*.pt",
        "*.pt/*.pth/*.ckpt/*.safetensors",
        "*.mp4/*.avi/*.mov",
        f"*.npz>{int(npz_max_bytes)} bytes",
        f"file_size_cap>{int(max_file_bytes)} bytes",
    ]

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:

        def add_file(src: Path) -> None:
            """Add a file to zip with sha256 + size tracking (best-effort)."""
            nonlocal total_bytes, excluded_files_count

            rel = _posix_relpath(src, run_root).replace("\\", "/")
            if not rel or rel in seen_rel:
                return

            if _should_skip_export_relpath(rel):
                excluded_files_count += 1
                return

            try:
                if src.is_symlink() or (not src.is_file()):
                    return
            except Exception:
                return

            try:
                size = int(src.stat().st_size)
            except Exception:
                size = 0

            # Size-based filters.
            if size > int(max_file_bytes):
                excluded_files_count += 1
                return

            if src.suffix.lower() == ".npz" and size > int(npz_max_bytes):
                excluded_files_count += 1
                return

            h = hashlib.sha256()
            try:
                with open(src, "rb") as f, z.open(rel, "w") as out_f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(chunk)
                        out_f.write(chunk)
            except Exception:
                # Best-effort: if a file fails mid-export, just skip it.
                excluded_files_count += 1
                return

            included_files.append({"relpath": rel, "bytes": int(size), "sha256": h.hexdigest()})
            total_bytes += int(size)
            seen_rel.add(rel)

        for item in roots_to_include:
            try:
                if item.is_file():
                    add_file(item)
                    continue
            except Exception:
                continue

            # Deterministic-ish ordering for analysis tooling.
            for fp in sorted(_iter_files_recursive(item), key=lambda p: _posix_relpath(p, run_root)):
                add_file(fp)

        # Export-local manifest (self-describing analysis bundle).
        included_files_sorted = sorted(included_files, key=lambda x: str(x.get("relpath", "")))
        manifest_obj = {
            "schema_version": "analysis_snapshot_manifest_v1",
            "run_key": rk,
            "created_at_utc": _now_utc_iso(),
            "include_checkpoints": bool(include_checkpoints),
            "zip_filename": fname,
            "included_files": included_files_sorted,
            "excluded_rules": excluded_rules,
            "excluded_files_count": int(excluded_files_count),
            "sources": sources,
        }
        manifest_bytes = (json.dumps(manifest_obj, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        z.writestr("analysis/snapshot_manifest.json", manifest_bytes)
        total_bytes += int(len(manifest_bytes))

        # Lightweight top-level metadata for traceability even when the
        # zip filename is bounded.
        git_ref = None
        try:
            for gp in [sim_root / "git_meta.json", sim_root / "git.json"]:
                if gp.exists() and gp.is_file() and (not gp.is_symlink()):
                    gj = json.loads(gp.read_text(encoding="utf-8"))
                    git_ref = gj.get("git_ref") or gj.get("commit") or gj.get("head")
                    if git_ref:
                        break
        except Exception:
            git_ref = None

        export_meta = {
            "schema_version": "export_meta_v1",
            "run_key": rk,
            "created_at_utc": _now_utc_iso(),
            "export_filename": fname,
            "git_ref": git_ref,
        }
        z.writestr("export_meta.json", (json.dumps(export_meta, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    return {
        "ok": True,
        "status": "ok",
        "filename": fname,
        "relpath": f"ui/exports/{fname}",
        "bytes": int(total_bytes),
    }


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
