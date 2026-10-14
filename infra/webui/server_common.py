

"""infra.webui.server

EIEL Phase 5d Tier-1 Web UI (monitor + compare + branch planning + safe interventions).

Last modified: 2026-02-26 Europe/Vienna

This server is intentionally boring:
- Python stdlib only (http.server)
- Static HTML + vanilla JS
- No process launching (Tier-1 is plan-only)

Authoritative contracts
-----------------------
- Phase 5d Technical Spec v0.4
- Phase 5d Web UI Spec v0.4
- Phase 5d viz artifact validation report
- Phase 5c deterministic branching report (branch_meta lineage fields)

Non-negotiable constraints (DSIP v1)
------------------------------------
- READ-ONLY on determinism-critical artifacts:
    metrics.csv, behaviors.csv, wal/, event logs, checkpoint contents.
- UI may write ONLY:
    - <run_dir>/inbox/   (command submission files; tmp + os.replace)
    - <run_dir>/ui/      (notes.md, ui_meta.json, branch_meta.json, exports/)
    - <repo_root>/runtime/state/webui/ (optional runtime caches/logs)
    - New run folders (planned branch/continuation skeletons)
- Never rewrite/normalize engine output files (CSV/JSONL). Read them as-is.
- Restart-safe: caches are disposable; truth comes from filesystem artifacts.

API surface (Tier-1)
--------------------
GET  /api/health
GET  /api/runs
GET  /api/run/{run_id}/meta
GET  /api/run/{run_id}/status
GET  /api/run/{run_id}/checkpoints
GET  /api/run/{run_id}/inbox?limit=<int>
GET  /api/run/{run_id}/epiav?after_gen=<int>&limit=<int>
GET  /api/run/{run_id}/applied_commands?after_gen=<int>&limit=<int>
GET  /api/run/{run_id}/autopilot_actions?after_gen=<int>&limit=<int>
GET  /api/changesets

GET  /api/builder/config
GET  /api/builder/registries
GET  /api/builder/templates
GET  /api/builder/download/{filename}

POST /api/builder/registry/load
POST /api/builder/registry/parse

POST /api/builder/generate_harness
POST /api/builder/generate_manifest
POST /api/builder/templates/save
POST /api/builder/templates/load

POST /api/run/{run_id}/notes
GET  /api/run/{run_id}/notes
POST /api/run/{run_id}/archive
POST /api/run/{run_id}/unarchive
POST /api/run/{run_id}/export
GET  /api/run/{run_id}/export/{filename}
POST /api/run/{run_id}/submit_command
POST /api/branch/plan

Notes for humans (unfortunate requirement)
------------------------------------------
- run_id in the API is a "run_key": a posix-style relative path under RUNS_ROOT.
  It may include slashes for nested folders.
- Wrapper runs: if <run_root>/payload exists, that is treated as sim_root.
  UI-owned files live under run_root/ui.

"""

from __future__ import annotations

import argparse
import csv
import bisect
import hashlib
import importlib
import io
import json
import math
import os
import py_compile
import re
import sys
import shlex
import shutil
import selectors
import threading
import time
import uuid
import zipfile
import subprocess
import signal
import tempfile
from infra.controlplane import job_registry as _jobreg
from experiments_shared.runner_utils import (
    builder_harness_module_prefix,
    builder_harness_out_dir_rel,
    builder_manifest_runner_module,
    builder_project_runner_module,
    builder_registry_out_dir_rel,
    builder_run_job_module,
    default_project_runs_root,
)
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

# Change Ledger (governance-backed patch/task records)
from infra.webui.ledger.ledger_manager import ChangeLedger


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------


UI_SCHEMA_VERSION = "phase5d_webui_v0.4"

# FDP v3 (forensic debug) support: thread-safe debug logging for selected endpoints.
_METRICS_SERIES_DEBUG_LOCK = threading.Lock()


@dataclass(frozen=True)
class WebUIConfig:
    """Configuration for the Web UI server."""

    runs_root: Path
    host: str = "127.0.0.1"
    port: int = 8787
    scan_depth: int = 2
    static_dir: Path = Path(__file__).resolve().parent / "static"

    # Approved roots for the read-only Scientific Chronicle Explorer. When
    # empty, runs_root is the only approved root.

    # Optional MkDocs build output directory.
    # Served under /docs/ (see PIS-K4T9).
    docs_root: Path = Path("./site")

    # Operator routes require a token even on loopback. Browser login exchanges
    # it for an expiring HttpOnly session; CLI clients use X-Admin-Token.
    admin_enabled: bool = True
    admin_token: str = ""


# -----------------------------------------------------------------------------
# Small utilities (safe pathing, atomic writes, file tail)
# -----------------------------------------------------------------------------


_RUN_KEY_RE = re.compile(r"^[A-Za-z0-9._\-/]+$")
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _now_utc_iso() -> str:
    """Return current UTC time as ISO-8601 (seconds resolution)."""
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc_iso(ts: str) -> Optional[float]:
    """Parse an ISO-8601 UTC-ish timestamp into epoch seconds.

    Accepts:
    - 2026-02-25T12:00:00Z
    - 2026-02-25T12:00:00+00:00

    Returns None on failure.
    """
    if not ts:
        return None
    try:
        import datetime as _dt

        s = str(ts).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = _dt.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            # Assume UTC.
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _posix_relpath(child: Path, base: Path) -> str:
    """Return child relative to base, using posix separators."""
    try:
        rel = child.resolve().relative_to(base.resolve())
        return rel.as_posix()
    except Exception:
        return child.name


def normalize_run_key(run_key: str) -> str:
    """Normalize and validate a run_key.

    A run_key is an OS-neutral identifier for a run folder, expressed as a
    posix-style relative path under RUNS_ROOT.

    Safety rules:
    - no drive letters
    - no absolute paths
    - no '.', '..' path segments
    - only characters [A-Za-z0-9._-/]

    Windows-style separators are accepted but normalized to '/'.

    Returns:
        Normalized run_key (posix separators).

    Raises:
        ValueError if run_key is unsafe.
    """
    if not isinstance(run_key, str):
        raise ValueError("run_key must be a string")

    rk = run_key.strip()
    if not rk:
        raise ValueError("run_key must not be empty")

    # Normalize slashes.
    rk = rk.replace("\\", "/")

    # Reject absolute paths / drive letters.
    if rk.startswith("/") or re.match(r"^[A-Za-z]:/", rk):
        raise ValueError("run_key must be a relative path")

    if rk.startswith("//"):
        raise ValueError("run_key must not be a UNC path")

    if not _RUN_KEY_RE.match(rk):
        raise ValueError("run_key contains invalid characters")

    parts = [p for p in rk.split("/") if p != ""]
    if not parts:
        raise ValueError("run_key must contain at least one path component")

    for p in parts:
        if p in (".", ".."):
            raise ValueError("run_key must not contain '.' or '..'")
        if not _COMPONENT_RE.match(p):
            raise ValueError("run_key component contains invalid characters")

    return "/".join(parts)


_DISPLAY_NOISE_TOKENS = {
    "experiments",
    "harnesses",
    "harness",
    "generated",
    "scripts",
    "runs",
    "runtime",
    "live",
    "wrapper",
    "wrapperjobs",
    "wrapperjob",
    "payload",
    "python",
    "module",
    "main",
    "default",
    "baseline",
    "seed",
    "gen",
    "run",
    "job",
    "planned",
}
_DISPLAY_HASHLIKE_RE = re.compile(r"(?i)^[0-9a-f]{6,12}$")
_DISPLAY_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


def _display_compact_suffix(*candidates: Any) -> str:
    """Return a stable short suffix for a technical identifier."""

    for raw in candidates:
        txt = str(raw or "").strip()
        if not txt:
            continue
        toks = [t for t in _DISPLAY_ALNUM_RE.split(txt) if t]
        for tok in reversed(toks):
            if _DISPLAY_HASHLIKE_RE.match(tok):
                return tok[-4:].lower()
    joined = "||".join(str(v or "").strip() for v in candidates if str(v or "").strip())
    if not joined:
        return "0000"
    return hashlib.sha1(joined.encode("utf-8", errors="replace")).hexdigest()[-4:]


def _display_slug(*candidates: Any, fallback: str = "item") -> str:
    """Derive a compact readable slug from labels/modules/ids."""

    for raw in candidates:
        txt = str(raw or "").strip()
        if not txt:
            continue
        txt = txt.replace("\\", "/")
        if "/" in txt:
            txt = txt.split("/")[-1]
        if "." in txt and not txt.startswith("."):
            txt = txt.split(".")[-1]
        txt = re.sub(r"(__+)?harness$", "", txt, flags=re.IGNORECASE)
        txt = re.sub(r"[_\-]*(py|json|yaml|yml|log)$", "", txt, flags=re.IGNORECASE)
        toks = [t.lower() for t in _DISPLAY_ALNUM_RE.split(txt) if t]
        if not toks:
            continue
        words: List[str] = []
        tail_hint = ""
        for tok in toks:
            if tok in _DISPLAY_NOISE_TOKENS:
                continue
            if re.fullmatch(r"\d{6,14}", tok):
                continue
            if re.fullmatch(r"seed\d+", tok) or re.fullmatch(r"gen\d+", tok):
                continue
            if _DISPLAY_HASHLIKE_RE.match(tok):
                tail_hint = tok
                continue
            if tok.isdigit():
                if len(tok) <= 3:
                    words.append(tok)
                continue
            if any(ch.isalpha() for ch in tok):
                words.append(tok)
        if not words and tail_hint:
            words.append(fallback)
        if not words:
            continue
        slug = "-".join(words[:3]).strip("-")
        if tail_hint and tail_hint[-6:].lower() not in slug:
            slug = f"{slug}-{tail_hint[-6:].lower()}"
        slug = re.sub(r"-{2,}", "-", slug).strip("-")
        if slug:
            return slug[:48]
    return fallback


def _display_identity(*, canonical_id: str, label: str = "", module: str = "", stable_hint: str = "", fallback: str = "item") -> Dict[str, str]:
    """Return canonical + short user-facing identifiers for UI payloads."""

    cid = str(canonical_id or "").strip()
    slug = _display_slug(label, module, cid, fallback=fallback)
    suffix = _display_compact_suffix(stable_hint, module, cid, label)
    return {
        "canonical_id": cid,
        "display_slug": slug,
        "display_suffix": suffix,
        "display_id": f"{slug} · {suffix}",
    }




def _looks_like_sim_root_dir(p: Path) -> bool:
    """Return True if directory contains key simulation artifacts.

    This is a *small, stable* marker set used for deterministic sim_root selection.
    """
    try:
        if not p.exists() or not p.is_dir():
            return False
        markers = [
            p / 'viz' / 'checkpoints_index.json',
            p / 'viz',
            p / 'checkpoint_bundles_v2',
            p / 'checkpoints',
            p / 'events',
            p / 'acks',
            p / 'run_status.json',
            p / 'metrics.csv',
        ]
        return any(m.exists() for m in markers)
    except Exception:
        return False
def resolve_run_dirs(runs_root: Path, run_key: str) -> Tuple[Path, Path]:
    """Resolve (run_root, sim_root) for a given run_key.

    - run_root is the directory that contains UI-owned metadata (ui/).
    - sim_root is the directory that contains simulation artifacts
      (viz/, acks/, events/, checkpoints, ...).

    Wrapper runs use:
      run_root/<payload>/ as sim_root.

    Raises:
        FileNotFoundError: if the run folder does not exist.
        ValueError: if the run_key is unsafe.
    """
    rk = normalize_run_key(run_key)
    root = Path(runs_root).resolve()
    run_root = (root / Path(rk)).resolve()

    # Enforce containment under runs_root.
    try:
        run_root.relative_to(root)
    except Exception as e:
        raise ValueError("run_key resolves outside runs_root") from e

    if not run_root.exists() or not run_root.is_dir():
        raise FileNotFoundError(f"run not found: {rk}")

    payload = run_root / "payload"

    # Deterministic sim_root selection rule (CRITICAL):
    #
    # Wrapper runs (tools.core.run_job) ALWAYS create a payload/ directory and
    # then set EXPERIMENT_RUNS_DIR / EXPERIMENT_OUTPUTS_DIR to payload/. That means:
    #   - engine writes *and reads* simulation-side artifacts (including inbox/) under payload/
    #
    # The older rule "only use payload if it already contains markers" is unsafe because
    # the first operator action (submit_command) often happens *before* the engine has
    # created any marker files. In that window we would mis-detect sim_root as run_root,
    # write inbox commands into the wrong directory, and then later the UI would switch
    # sim_root to payload once markers appear. Result: "pending ack" popup but empty tables.
    #
    # New rule:
    # 1) If run_root already looks like a sim_root (direct-run layout), use run_root.
    # 2) Else if payload/ exists, use payload/ even if currently empty.
    # 3) Else fallback to run_root.
    sim_root = run_root
    if not _looks_like_sim_root_dir(run_root):
        if payload.exists() and payload.is_dir():
            sim_root = payload
    # Extra defense: sim_root must also be under run_root (no symlink escape).
    try:
        sim_root.resolve().relative_to(run_root.resolve())
    except Exception as e:
        raise ValueError("sim_root resolves outside run_root") from e

    return run_root, sim_root




def _scan_archived_run_keys_by_zip(archives_dir: Path) -> set[str]:
    """Return set of run_keys that have an archive in archives_dir.

    Supported formats:
    - New (bounded filename + sidecar):
        <zip>.runkey.json with {"schema_version":"run_archive_index_v1","run_key":...}
    - Legacy:
        run_archive__<safe(run_key)>__<tag>.zip  (best-effort inference)
    """
    out: set[str] = set()
    ad = Path(archives_dir)
    if not ad.exists() or not ad.is_dir():
        return out

    # Prefer sidecar index files (new bounded filename scheme).
    try:
        for p in sorted(ad.glob("*.runkey.json")):
            try:
                if p.is_symlink() or (not p.is_file()):
                    continue
                obj = _read_json(p)
                if not isinstance(obj, dict):
                    continue
                if str(obj.get("schema_version") or "") != "run_archive_index_v1":
                    continue
                rk = obj.get("run_key")
                if not isinstance(rk, str) or not rk.strip():
                    continue
                out.add(normalize_run_key(rk))
            except Exception:
                continue
    except Exception:
        pass

    # Backward-compatible fallback: parse legacy filenames.
    # NOTE: This is inherently lossy if the run_key itself contained '__'.
    try:
        prefix = "run_archive__"
        suffix = ".zip"
        for name in sorted(os.listdir(ad)):
            if not name.startswith(prefix) or not name.endswith(suffix):
                continue
            mid = name[len(prefix):]
            # Legacy shape: <safe_run_key>__<ts>(__N)?.zip
            # We take everything up to the last '__' group that looks like a timestamp.
            stem = mid[:-len(suffix)]
            parts = stem.split("__")
            if len(parts) < 2:
                continue
            # Best-effort: assume last part is ts (YYYYMMDD...Z). Everything before that is safe_rk.
            safe_rk = "__".join(parts[:-1])
            if not safe_rk:
                continue
            try:
                out.add(normalize_run_key(safe_rk.replace("__", "/")))
            except Exception:
                continue
    except Exception:
        pass


def _safe_resolve_under(base: Path, child: Path) -> Optional[Path]:
    """Resolve child and ensure it stays under base.

    Returns resolved path if safe, else None.
    """
    try:
        b = Path(base).resolve()
        c = Path(child).resolve()
        c.relative_to(b)
        return c
    except Exception:
        return None


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically write text to a file (tmp + os.replace).

    This is used only for UI-owned files under ui/.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding=encoding, newline="\n") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically write bytes to a file (tmp + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_canonical_json(path: Path, obj: Any) -> None:
    """Atomically write canonical JSON (sorted keys, minified) + trailing newline."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    atomic_write_text(path, text)


def _sha256_file(path: Path) -> str:
    """Streaming SHA256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tail_bytes(path: Path, *, max_bytes: int = 16384) -> bytes:
    """Read up to max_bytes from the end of a file (best-effort)."""
    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return b""
        size = p.stat().st_size
        start = max(0, size - int(max_bytes))
        with open(p, "rb") as f:
            f.seek(start)
            data = f.read()
        # If we started mid-line, drop first partial line for readability.
        if start > 0:
            nl = data.find(b"\n")
            if nl != -1:
                data = data[nl + 1 :]
        return data
    except Exception:
        return b""


def tail_lines(path: Path, *, max_lines: int = 200, max_bytes: int = 1024 * 1024) -> List[str]:
    """Read up to max_lines from the end of a text file.

    This is legacy/diagnostic only. Phase 5d UI should prefer viz summaries
    and incremental parsers.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return []

    # Fast path: small file.
    try:
        if p.stat().st_size <= max_bytes:
            txt = p.read_text(encoding="utf-8", errors="replace")
            lines = txt.splitlines()
            return lines[-int(max_lines) :]
    except Exception:
        pass

    # Chunked tail.
    chunk_size = 64 * 1024
    data = b""
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            pos = end
            while pos > 0 and data.count(b"\n") <= max_lines and len(data) < max_bytes:
                step = min(chunk_size, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
    except Exception:
        return []

    txt = data.decode("utf-8", errors="replace")
    lines = txt.splitlines()
    return lines[-int(max_lines) :]


# -----------------------------------------------------------------------------
# Run Builder helpers (Phase 5e Tier-1)
# -----------------------------------------------------------------------------


_BUILDER_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_BUILDER_SAFE_TEMPLATE_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _repo_root_from_webui() -> Path:
    """Return repo root as a Path.

    Assumes this file lives at: <repo_root>/infra/webui/server.py
    """

    return Path(__file__).resolve().parents[2]


def _webui_runtime_root() -> Path:
    """Return canonical runtime/cache root for WebUI internals.

    This keeps transient server artifacts out of runs*/ so users can freely
    archive/delete run trees without losing operational logs or cache state.
    """
    return (Path(_repo_root_from_webui()) / "runtime" / "state" / "webui").resolve()


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
