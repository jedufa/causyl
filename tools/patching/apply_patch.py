#!/usr/bin/env python3
"""tools/patching/apply_patch.py

Governed patch applier for Emergence/EIEL repo.

This tool is designed to replace the fragile workflow of unzipping a patch
archive and overwriting repo files blindly.

It supports two common workflows:

1) Apply a specific patch zip:
   python -m tools.patching.apply_patch --zip /path/to/patch.zip

2) Apply the most recent patch source from a Windows Downloads folder (WSL):
   python -m tools.patching.apply_patch
   (auto-detects newest among .zip, .patch, .diff)

Key safety features:
- Validates we're inside a git repo.
- Optional baseline enforcement: refuse unless HEAD matches expected commit.
- Computes per-file and total change stats (added/deleted lines) before applying.
- Enforces thresholds (max files, max additions, max deletions, max deletion ratio).
- Optional allowlist of path prefixes.
- Blocks binary overwrites unless explicitly allowed.
- Verifies sha256(source)==sha256(dest) after copy.
- Clear, actionable error messages.

Note:
This is a governance helper. It cannot prove semantic correctness.
It prevents the common failure modes: wrong baseline, huge unintended rewrites,
applying outside intended directories, and silent partial extraction issues.
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


# -----------------------------
# Data models
# -----------------------------


@dataclasses.dataclass(frozen=True)
class ChangeStat:
    """Line-based change stats for a single file."""

    path: str
    added: int
    deleted: int

    @property
    def total(self) -> int:
        return self.added + self.deleted

    @property
    def del_ratio_pct(self) -> int:
        denom = self.added + self.deleted
        if denom <= 0:
            return 0
        return int((self.deleted * 100) / denom)


@dataclasses.dataclass(frozen=True)
class PlannedOp:
    """A planned file write from extracted content to repo destination."""

    src: Path
    dest: Path
    is_text: bool
    stat: ChangeStat
    sha256_src: str

    # Whether destination is modified/untracked in the working tree.
    # Used to decide whether to 3-way merge instead of clobbering local work.
    dest_is_dirty: bool = False


# -----------------------------
# Small utilities
# -----------------------------


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _run(cmd: Sequence[str], cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    """Run a subprocess command, capture stdout/stderr.

    Returns:
        (return_code, stdout, stderr)
    """

    p = subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return p.returncode, p.stdout, p.stderr


def _is_git_repo(root: Path) -> bool:
    rc, out, _ = _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root)
    return rc == 0 and out.strip() == "true"


def _git_head(root: Path) -> str:
    rc, out, err = _run(["git", "rev-parse", "HEAD"], cwd=root)
    if rc != 0:
        raise RuntimeError(f"git rev-parse HEAD failed: {err.strip()}")
    return out.strip()


def _git_branch(root: Path) -> str:
    rc, out, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    return out.strip() if rc == 0 else "UNKNOWN"


def _git_is_clean(root: Path) -> bool:
    rc, out, err = _run(["git", "status", "--porcelain"], cwd=root)
    if rc != 0:
        raise RuntimeError(f"git status failed: {err.strip()}")
    return out.strip() == ""


def _git_dirty_paths(root: Path) -> set[str]:
    """Return repo-relative paths that are modified/untracked in the working tree.

    Parses `git status --porcelain` output. Directories are returned as their file
    paths (git porcelain is file-oriented).
    """

    rc, out, err = _run(["git", "status", "--porcelain"], cwd=root)
    if rc != 0:
        raise RuntimeError(f"git status failed: {err.strip()}")
    dirty: set[str] = set()
    for raw in out.splitlines():
        line = raw.rstrip("\n")
        if not line:
            continue
        # Porcelain v1: XY <path> (or XY <old> -> <new>)
        path_part = line[3:].strip()
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1].strip()
        if path_part:
            dirty.add(path_part)
    return dirty


def _sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()


def _read_bytes(p: Path) -> bytes:
    return p.read_bytes()


def _looks_binary(b: bytes) -> bool:
    # Simple heuristic: NUL byte or very high non-text ratio.
    if b"\x00" in b:
        return True
    # If it decodes as UTF-8 with replacement, consider text.
    try:
        b.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def _read_text_lines(p: Path) -> List[str]:
    # Use utf-8 with replacement to avoid hard failure on odd files.
    return p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=False)


def _diff_stats(old_lines: List[str], new_lines: List[str], path: str) -> ChangeStat:
    added = 0
    deleted = 0
    for line in difflib.unified_diff(old_lines, new_lines, lineterm=""):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            deleted += 1
    return ChangeStat(path=path, added=added, deleted=deleted)


def _confirm(prompt: str, default_yes: bool = False) -> bool:
    """Ask a y/n confirmation on stdin."""

    suffix = "[Y/n]" if default_yes else "[y/N]"
    while True:
        resp = input(f"{prompt} {suffix} ").strip().lower()
        if resp == "" and default_yes:
            return True
        if resp == "" and not default_yes:
            return False
        if resp in {"y", "yes"}:
            return True
        if resp in {"n", "no"}:
            return False
        print("Please answer 'y' or 'n'.")


def _now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _find_downloads_dir(force: str = "") -> Path:
    """Resolve the Windows Downloads directory from WSL.

    Resolution order:
      1) Explicit override via --downloads-dir or FORCE_DOWNLOADS_DIR
      2) Hardcoded default path for this machine: /mnt/c/home/user/Downloads
    """

    if force:
        p = Path(force).expanduser().resolve()
        if p.is_dir():
            return p
        raise FileNotFoundError(f"FORCE_DOWNLOADS_DIR does not exist: {force}")

    default_path = Path("/mnt/c/home/user/Downloads")
    if default_path.is_dir():
        return default_path

    raise FileNotFoundError(
        "Could not locate Windows Downloads folder. Expected: /mnt/c/home/user/Downloads. "
        "Set FORCE_DOWNLOADS_DIR or pass --downloads-dir."
    )


def _latest_zip(downloads: Path) -> Path:
    zips = sorted(downloads.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        raise FileNotFoundError(f"No .zip files found in: {downloads}")
    return zips[0]


def _latest_any(downloads: Path) -> Path:
    """Return newest patch source from Downloads among .zip, .patch, .diff."""
    candidates = (
        list(downloads.glob("*.zip"))
        + list(downloads.glob("*.patch"))
        + list(downloads.glob("*.diff"))
    )
    if not candidates:
        raise FileNotFoundError(f"No .zip, .patch or .diff files found in: {downloads}")
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _zip_list(zip_path: Path) -> List[str]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        return zf.namelist()


def _is_patch_file(p: Path) -> bool:
    return p.suffix.lower() in {".patch", ".diff"}


def _read_patch_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _patch_has_binary(patch_text: str) -> bool:
    # Common markers for git binary patches
    if "GIT binary patch" in patch_text:
        return True
    if "Binary files " in patch_text:
        return True
    return False


def _patch_has_deletions(patch_text: str) -> bool:
    return "deleted file mode" in patch_text


def _patch_numstat(repo_root: Path, patch_path: Path) -> List[ChangeStat]:
    rc, out, err = _run(["git", "apply", "--numstat", str(patch_path)], cwd=repo_root)
    if rc != 0:
        raise RuntimeError(f"git apply --numstat failed: {err.strip() or out.strip()}")

    stats: List[ChangeStat] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a_s, d_s, path = parts[0], parts[1], parts[2]
        # '-' indicates binary patches; handled separately
        if a_s == "-" or d_s == "-":
            stats.append(ChangeStat(path=path, added=0, deleted=0))
            continue
        try:
            a = int(a_s)
            d = int(d_s)
        except Exception:
            a, d = 0, 0
        stats.append(ChangeStat(path=path, added=a, deleted=d))
    stats.sort(key=lambda s: s.path)
    return stats


def _split_patch_by_file(patch_text: str) -> List[Tuple[str, List[str]]]:
    """
    Split a unified diff into per-file chunks keyed by the 'b/<path>' in 'diff --git a/... b/...'.

    Returns: [(rel_path, lines), ...]
    """
    lines = patch_text.splitlines()
    chunks: List[Tuple[str, List[str]]] = []
    cur_lines: List[str] = []
    cur_path: Optional[str] = None

    def flush() -> None:
        nonlocal cur_lines, cur_path
        if cur_path is not None and cur_lines:
            chunks.append((cur_path, cur_lines))
        cur_lines = []
        cur_path = None

    for ln in lines:
        if ln.startswith("diff --git "):
            flush()
            # Example: diff --git a/tools/x.py b/tools/x.py
            parts = ln.split()
            if len(parts) >= 4:
                b = parts[3]
                # strip leading 'b/'
                cur_path = b[2:] if b.startswith("b/") else b
            else:
                cur_path = "UNKNOWN"
            cur_lines = [ln]
        else:
            if cur_lines is not None:
                cur_lines.append(ln)

    flush()
    return chunks


def _write_preimage_bundle_paths(repo_root: Path, rel_paths: Sequence[str], archive_dir: Path, stamp: str) -> Tuple[Path, List[str]]:
    """
    Create a preimage bundle for a list of paths. Used for unified-diff patch apply.
    """
    pre_dir = archive_dir / "preimages"
    pre_dir.mkdir(parents=True, exist_ok=True)
    out_zip = pre_dir / f"{stamp}__preimage.zip"

    created_files: List[str] = []
    saved_files: List[str] = []

    meta: dict = {"ts": stamp, "created_files": created_files, "saved_files": saved_files}

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in rel_paths:
            dest = repo_root / rel
            if dest.exists():
                zf.writestr(f"files/{rel}", dest.read_bytes())
                saved_files.append(rel)
            else:
                created_files.append(rel)
        zf.writestr("meta.json", json.dumps(meta, sort_keys=True, indent=2).encode("utf-8"))

    return out_zip, created_files


def _apply_unified_patch(repo_root: Path, patch_path: Path) -> None:
    rc, out, err = _run(["git", "apply", "--whitespace=nowarn", str(patch_path)], cwd=repo_root)
    if rc != 0:
        raise RuntimeError(f"git apply failed: {err.strip() or out.strip()}")


def _stage_input_to_local(p: Path, purpose: str) -> Path:
    """Copy an input file to a local temp path.

    Why this exists:
      - Accessing files directly under /mnt/c/... can intermittently fail under WSL
        with I/O errors (especially when git tries to read them).
      - Staging the file into the Linux filesystem makes operations reliable.

    Args:
        p: Source file path.
        purpose: Short label used in temp directory prefix.

    Returns:
        A new absolute path under /tmp containing the same bytes.
    """

    if not p.is_file():
        raise FileNotFoundError(f"Input file not found: {p}")

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"apply_patch_{purpose}_"))
    staged = tmp_dir / p.name
    shutil.copy2(p, staged)
    return staged


def _git_show_head_file(repo_root: Path, rel_path: str) -> Optional[bytes]:
    """Return file bytes as stored in HEAD for rel_path, or None if absent."""

    # 'git show' returns nonzero if the path is not in HEAD.
    rc, out, _err = _run(["git", "show", f"HEAD:{rel_path}"], cwd=repo_root)
    if rc != 0:
        return None
    return out.encode("utf-8", errors="replace")


def _three_way_merge_text(
    repo_root: Path,
    rel_path: str,
    current_bytes: bytes,
    base_bytes: bytes,
    incoming_bytes: bytes,
) -> Tuple[bytes, bool]:
    """Perform a 3-way merge using git merge-file.

    Returns:
        (merged_bytes, had_conflicts)
    """

    tmp_dir = Path(tempfile.mkdtemp(prefix="apply_patch_merge_"))
    cur_p = tmp_dir / "current"
    base_p = tmp_dir / "base"
    inc_p = tmp_dir / "incoming"
    cur_p.write_bytes(current_bytes)
    base_p.write_bytes(base_bytes)
    inc_p.write_bytes(incoming_bytes)

    # git merge-file exit codes: 0=clean merge, 1=conflicts, 2=error
    rc, out, err = _run(["git", "merge-file", "-p", str(cur_p), str(base_p), str(inc_p)], cwd=repo_root)
    if rc == 2:
        raise RuntimeError(f"git merge-file failed for {rel_path}: {err.strip() or out.strip()}")
    merged = out.encode("utf-8", errors="replace")
    had_conflicts = (rc == 1)
    return merged, had_conflicts


def _is_md(path: str) -> bool:
    return path.lower().endswith(".md")


def _is_dir_entry(name: str) -> bool:
    return name.endswith("/")


def _extract_filtered(zip_path: Path, out_dir: Path, skip_md: bool = True) -> List[Path]:
    """Extract zip, optionally skipping markdown files.

    Returns list of extracted file paths (not directories).
    """

    extracted: List[Path] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if _is_dir_entry(name):
                continue
            if skip_md and _is_md(name):
                continue
            # Protect against zip slip
            dest = out_dir / name
            dest_parent = dest.resolve().parent
            if out_dir.resolve() not in dest_parent.parents and dest_parent != out_dir.resolve():
                raise RuntimeError(f"Zip entry escapes extraction root: {name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name, "r") as src_f:
                data = src_f.read()
            dest.write_bytes(data)
            extracted.append(dest)
    return extracted


def _detect_flat_zip(entries: Iterable[str]) -> bool:
    # If no entry contains '/', consider it flat.
    for e in entries:
        if "/" in e.strip("/"):
            return False
    return True


def _resolve_flat_dest(repo_root: Path, filename: str) -> Path:
    """Resolve a flat-zip filename into a unique repo path.

    If ambiguous or not found, raise with a clear message.
    """

    matches = list(repo_root.rglob(filename))
    # Filter out .git and common junk
    matches = [m for m in matches if ".git" not in m.parts]

    if not matches:
        raise FileNotFoundError(
            f"Flat zip entry '{filename}' does not match any file in repo. "
            "This patch likely needs folder paths inside the zip."
        )
    if len(matches) > 1:
        sample = "\n".join([f"- {m.relative_to(repo_root)}" for m in matches[:15]])
        raise RuntimeError(
            f"Flat zip entry '{filename}' is ambiguous ({len(matches)} matches).\n"
            f"Matches (first 15):\n{sample}\n"
            "Repackage the patch zip with folder paths, or rename files to be unique."
        )
    return matches[0]


def _path_allowed(rel: str, allow_prefixes: Sequence[str]) -> bool:
    if not allow_prefixes:
        return True
    for pref in allow_prefixes:
        if rel.startswith(pref):
            return True
    return False


def _copy_atomic(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)


# -----------------------------
# Core planning + checks
# -----------------------------


def plan_ops(
    repo_root: Path,
    extracted_files: List[Path],
    extracted_root: Path,
    flat_zip: bool,
    allow_prefixes: Sequence[str],
    allow_binary: bool,
    dirty_paths: Optional[set[str]] = None,
) -> List[PlannedOp]:
    """Plan operations, compute diffs and hashes."""

    ops: List[PlannedOp] = []

    for src in extracted_files:
        # Determine destination path
        if flat_zip:
            dest = _resolve_flat_dest(repo_root, src.name)
        else:
            rel = src.relative_to(extracted_root)
            dest = repo_root / rel

        rel_str = str(dest.relative_to(repo_root))

        if not _path_allowed(rel_str, allow_prefixes):
            raise RuntimeError(
                f"Patch touches disallowed path: {rel_str}\n"
                f"Allowed prefixes: {', '.join(allow_prefixes) if allow_prefixes else '(none)'}"
            )

        src_bytes = _read_bytes(src)
        sha_src = _sha256_bytes(src_bytes)
        is_text = not _looks_binary(src_bytes)

        if not is_text and not allow_binary:
            raise RuntimeError(
                f"Binary file overwrite blocked: {rel_str}\n"
                "Re-run with --allow-binary if you really intend to overwrite binaries."
            )

        # compute stats
        if dest.exists() and is_text:
            old_lines = _read_text_lines(dest)
            new_lines = _read_text_lines(src)
            stat = _diff_stats(old_lines, new_lines, rel_str)
        elif dest.exists() and not is_text:
            stat = ChangeStat(path=rel_str, added=0, deleted=0)
        else:
            # new file: count lines as added if text
            if is_text:
                new_lines = _read_text_lines(src)
                stat = ChangeStat(path=rel_str, added=len(new_lines), deleted=0)
            else:
                stat = ChangeStat(path=rel_str, added=0, deleted=0)

        is_dirty = bool(dirty_paths and rel_str in dirty_paths)

        ops.append(
            PlannedOp(
                src=src,
                dest=dest,
                is_text=is_text,
                stat=stat,
                sha256_src=sha_src,
                dest_is_dirty=is_dirty,
            )
        )

    # Stable order for reproducibility
    ops.sort(key=lambda o: str(o.dest))
    return ops


def enforce_thresholds(
    ops: Sequence[PlannedOp],
    max_files: int,
    max_added: int,
    max_deleted: int,
    max_del_ratio_pct: int,
) -> None:
    """Raise RuntimeError if thresholds are violated."""

    if len(ops) > max_files:
        raise RuntimeError(f"Too many files changed: {len(ops)} > {max_files}")

    total_added = sum(o.stat.added for o in ops)
    total_deleted = sum(o.stat.deleted for o in ops)
    if total_deleted > max_deleted:
        raise RuntimeError(f"Too many deleted lines: {total_deleted} > {max_deleted}")

    # Per-file deletion ratio check (only for non-trivial changes)
    bad = []
    for o in ops:
        if o.stat.deleted <= 25:
            continue
        if o.stat.del_ratio_pct > max_del_ratio_pct:
            bad.append(f"{o.stat.path} (del={o.stat.deleted}, add={o.stat.added}, ratio={o.stat.del_ratio_pct}%)")
    if bad:
        raise RuntimeError(
            "High per-file deletion ratio detected (likely unintended rewrite):\n" + "\n".join(f"- {b}" for b in bad)
        )


def print_plan(ops: Sequence[PlannedOp]) -> None:
    total_added = sum(o.stat.added for o in ops)
    total_deleted = sum(o.stat.deleted for o in ops)
    print("\nPlanned changes:")
    print("-" * 78)
    for o in ops:
        rel = str(o.dest)
        try:
            rel = str(o.dest.relative_to(Path.cwd()))
        except Exception:
            pass
        marker = "(NEW)" if not o.dest.exists() else ""
        kind = "text" if o.is_text else "bin"
        print(f"{o.stat.path:<60} +{o.stat.added:<5} -{o.stat.deleted:<5} {kind} {marker}")
    print("-" * 78)
    print(f"TOTAL: files={len(ops)}  +{total_added}  -{total_deleted}\n")


def apply_ops(
    repo_root: Path,
    ops: Sequence[PlannedOp],
    iterative: bool,
    auto_accept: bool,
    force_overwrite: bool,
) -> None:
    """Apply planned operations to the repo.

    Policy:
      - If a destination file is dirty and this is a text overwrite, we attempt a
        3-way merge (HEAD vs working tree vs incoming patch) by default.
      - If merging is impossible (binary / not in HEAD), we prompt unless forced.
    """

    for o in ops:
        rel = o.stat.path

        if iterative and not auto_accept:
            if not _confirm(f"Apply file: {rel}?", default_yes=False):
                print(f"SKIP: {rel}")
                continue

        merged = False

        # Prefer merge over clobber when user has local edits.
        if o.dest_is_dirty and not force_overwrite and o.is_text and o.dest.exists():
            base_bytes = _git_show_head_file(repo_root, rel)
            if base_bytes is not None:
                current_bytes = o.dest.read_bytes()
                incoming_bytes = o.src.read_bytes()
                merged_bytes, had_conflicts = _three_way_merge_text(
                    repo_root=repo_root,
                    rel_path=rel,
                    current_bytes=current_bytes,
                    base_bytes=base_bytes,
                    incoming_bytes=incoming_bytes,
                )
                o.dest.parent.mkdir(parents=True, exist_ok=True)
                o.dest.write_bytes(merged_bytes)
                merged = True
                if had_conflicts:
                    _eprint(f"[WARN] Merge conflicts in {rel}. Conflict markers were written.")
            else:
                # Dirty but no base in HEAD: cannot 3-way merge.
                if not auto_accept:
                    _eprint(f"[WARN] {rel} is dirty but not present in HEAD; cannot 3-way merge.")
                    if not _confirm(f"Overwrite dirty file (no merge possible): {rel}?", default_yes=False):
                        print(f"SKIP: {rel}")
                        continue
                _copy_atomic(o.src, o.dest)

        else:
            if o.dest_is_dirty and not force_overwrite and not auto_accept:
                _eprint(f"[WARN] {rel} is locally modified.")
                if not _confirm(f"Overwrite dirty file: {rel}?", default_yes=False):
                    print(f"SKIP: {rel}")
                    continue
            _copy_atomic(o.src, o.dest)

        # verify sha
        dest_bytes = _read_bytes(o.dest)
        sha_dest = _sha256_bytes(dest_bytes)

        # If we merged, sha will differ; only enforce sha for verbatim copies.
        if (not merged) and sha_dest != o.sha256_src:
            raise RuntimeError(
                f"sha256 mismatch after write for {rel}\n"
                f"src:  {o.sha256_src}\n"
                f"dest: {sha_dest}\n"
                "This indicates a filesystem/copy issue. Aborting."
            )


def archive_zip(zip_path: Path, archive_dir: Path, stamp: str) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / f"{stamp}__applied__{zip_path.name}"
    # Copy across filesystems reliably
    shutil.copy2(zip_path, dest)
    return dest


def _find_patch_manifest_in_zip(zip_path: Path) -> Optional[Tuple[str, bytes]]:
    """Find a patch manifest markdown file inside a zip.

    This is intentionally heuristic. We look for common names used by this project.

    Returns:
        (zip_entry_name, content_bytes) or None if not found.
    """

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = [n for n in zf.namelist() if not _is_dir_entry(n)]
            # Prefer our timestamped manifest convention.
            candidates = [
                n
                for n in names
                if n.lower().endswith("__patch_manifest.md")
                or n.lower().endswith("patch_manifest.md")
            ]
            if not candidates:
                return None
            # Choose the shortest path (most likely at zip root), then lexical.
            candidates.sort(key=lambda s: (s.count("/"), len(s), s))
            name = candidates[0]
            with zf.open(name, "r") as f:
                data = f.read()
            return name, data
    except Exception:
        return None


def _archive_manifest(zip_path: Path, archive_dir: Path, stamp: str) -> Optional[Path]:
    """If a manifest exists in the patch zip, copy it into archive_dir/manifests/."""

    found = _find_patch_manifest_in_zip(zip_path)
    if not found:
        return None
    name, data = found
    manifests_dir = archive_dir / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(name).name
    out = manifests_dir / f"{stamp}__{safe_name}"
    out.write_bytes(data)
    return out


def _append_patch_log(archive_dir: Path, entry: dict) -> None:
    """Append a single JSONL entry into archive_dir/patch_log.jsonl."""

    archive_dir.mkdir(parents=True, exist_ok=True)
    log_path = archive_dir / "patch_log.jsonl"
    line = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _append_post_exec_failure_log(
    archive_dir: Path,
    *,
    base_entry: dict,
    post_exec_command: str,
    post_exec_returncode: int,
    rollback_ok: bool,
) -> None:
    failure_entry = dict(base_entry)
    failure_entry["result"] = "FAILED_POST_EXEC_ROLLED_BACK" if rollback_ok else "FAILED_POST_EXEC_ROLLBACK_FAILED"
    failure_entry["post_exec"] = str(post_exec_command or "")
    failure_entry["post_exec_returncode"] = int(post_exec_returncode)
    failure_entry["rollback_ok"] = bool(rollback_ok)
    failure_entry["failure_stage"] = "post_exec"
    _append_patch_log(archive_dir, failure_entry)


def run_post_exec(post_exec: str) -> int:
    """Run a post-exec shell command (string) and stream output."""

    if not post_exec.strip():
        return 0
    # Use bash -lc so env assignments work.
    p = subprocess.run(["bash", "-lc", post_exec])
    return int(p.returncode)


# -----------------------------
# CLI
# -----------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apply_patch",
        formatter_class=argparse.RawTextHelpFormatter,
        description=textwrap.dedent(
            """            Apply a patch ZIP (full-file replacements) OR a unified-diff patch file (.patch/.diff)
            to the current repo with guardrails, review, and revert-last.

            Typical usage:
              infra/bin/apply_patch                      (auto-detect newest .zip/.patch/.diff from Downloads)
              infra/bin/apply_patch --zip X.zip          (apply a specific zip)
              infra/bin/apply_patch metrics_fix.patch    (apply a unified diff patch file)
              infra/bin/apply_patch --revert-last
            """
        ),
    )

    # Convenience: allow passing a patch file path without flags.
    p.add_argument(
        "input_path",
        nargs="?",
        default="",
        help="Optional patch input (.zip or .patch/.diff). If provided, overrides Downloads auto-detect.",
    )

    src = p.add_mutually_exclusive_group(required=False)
    src.add_argument("--zip", type=str, default="", help="Path to patch zip (full-file replacements)")
    src.add_argument("--patch", type=str, default="", help="Path to unified diff patch file (.patch/.diff)")
    src.add_argument("--latest-downloads", action="store_true", help="Use newest patch source from Windows Downloads")
    src.add_argument("--revert-last", action="store_true", help="Revert last applied patch (requires preimage bundles)")

    p.add_argument("--downloads-dir", type=str, default="", help="Override downloads directory")
    p.add_argument("--require-head", type=str, default="", help="Require git HEAD to equal this commit")

    # Safety / scoping
    p.add_argument("--allow-prefix", type=str, default="", help="Comma-separated allowed repo path prefixes")
    p.add_argument("--allow-binary", action="store_true", help="Allow overwriting binary files")

    p.add_argument("--max-files", type=int, default=10)
    p.add_argument("--max-added", type=int, default=250)
    p.add_argument("--max-deleted", type=int, default=250)
    p.add_argument("--max-del-ratio", type=int, default=35)

    p.add_argument("--iterative", action="store_true", help="Prompt per file in ZIP apply")

    # Review
    p.add_argument("--no-review", action="store_true", help="Do not show per-file diffs (not recommended)")
    p.add_argument("--diff-max-lines", type=int, default=800, help="Max diff lines per file during review (0=unlimited)")
    p.add_argument("--yes", action="store_true", help="Auto accept (no prompts)")

    # Dirty policy
    p.add_argument("--allow-dirty", action="store_true", help="Allow dirty working tree (use with care)")
    p.add_argument("--allow_dirty", dest="allow_dirty", action="store_true", help="Alias for --allow-dirty")
    p.add_argument("--strict", action="store_true", help="Require fully clean git tree")
    p.add_argument("--force-overwrite", action="store_true", help="Allow overwriting locally modified files without prompt")
    p.add_argument("--force", dest="force_overwrite", action="store_true", help="Alias for --force-overwrite")

    # MD extraction policy for ZIP patches (default: skip)
    p.set_defaults(skip_md=True)
    p.add_argument("--skip-md", dest="skip_md", action="store_true", help="Skip *.md in patch zip (default)")
    p.add_argument("--include-md", dest="skip_md", action="store_false", help="Include *.md from patch zip")

    # Unified-diff patch-only knobs
    p.add_argument("--permit-deletions", action="store_true", help="Allow file deletions in unified diff patches")

    p.add_argument("--post-exec", type=str, default="", help="Command to run after apply (bash -lc)")
    p.add_argument("--archive-dir", type=str, default="archives/patches", help="Archive folder inside repo")
    p.add_argument("--revert-no-delete-new", action="store_true", help="During revert, do not delete files created by patch")

    return p


def _read_patch_log(archive_dir: Path) -> List[dict]:
    """Read patch_log.jsonl entries.

    Returns:
        List of parsed JSON objects (in file order).
    """

    log_path = archive_dir / "patch_log.jsonl"
    if not log_path.is_file():
        raise FileNotFoundError(f"Patch log not found: {log_path}")
    entries: List[dict] = []
    with log_path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            s = line.strip()
            if not s:
                continue
            try:
                entries.append(json.loads(s))
            except Exception as e:
                raise RuntimeError(f"Could not parse JSONL at {log_path}:{i}: {e}")
    return entries


def _find_last_applied_entry(entries: Sequence[dict]) -> dict:
    """Find the most recent entry with result=='APPLIED'."""

    for e in reversed(entries):
        if str(e.get("result", "")).upper() == "APPLIED":
            return e
    raise RuntimeError("No APPLIED entry found in patch_log.jsonl")


def _write_preimage_bundle(
    repo_root: Path,
    ops: Sequence[PlannedOp],
    archive_dir: Path,
    stamp: str,
) -> Tuple[Path, List[str]]:
    """Create a preimage bundle zip that allows reverting without commits.

    The bundle stores:
      - Previous contents of each destination file that existed prior to apply.
      - A metadata JSON listing files created by the patch.

    Returns:
        (preimage_zip_path, created_files_rel)
    """

    preimages_dir = archive_dir / "preimages"
    preimages_dir.mkdir(parents=True, exist_ok=True)
    out_zip = preimages_dir / f"{stamp}__preimage.zip"

    created_files: List[str] = []

    meta: dict = {
        "ts": stamp,
        "created_files": created_files,
        "saved_files": [],
    }

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for o in ops:
            rel = o.stat.path
            dest = o.dest
            if dest.exists():
                data = dest.read_bytes()
                arcname = f"files/{rel}"
                zf.writestr(arcname, data)
                meta["saved_files"].append(rel)
            else:
                created_files.append(rel)

        zf.writestr("meta.json", json.dumps(meta, sort_keys=True, indent=2).encode("utf-8"))

    return out_zip, created_files


def _restore_from_preimage_bundle(
    repo_root: Path,
    preimage_zip: Path,
    delete_new_files: bool,
    auto_yes: bool,
) -> None:
    """Restore repo files from a preimage bundle."""

    if not preimage_zip.is_file():
        raise FileNotFoundError(f"Preimage bundle not found: {preimage_zip}")

    with zipfile.ZipFile(preimage_zip, "r") as zf:
        # Read metadata
        try:
            meta_raw = zf.read("meta.json")
            meta = json.loads(meta_raw.decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"Preimage bundle missing/invalid meta.json: {e}")

        saved_files = list(meta.get("saved_files", []))
        created_files = list(meta.get("created_files", []))

        # Restore saved files
        for rel in saved_files:
            arcname = f"files/{rel}"
            try:
                data = zf.read(arcname)
            except KeyError:
                raise RuntimeError(f"Preimage bundle missing entry: {arcname}")
            dest = repo_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        # Delete files that were created by the patch
        if delete_new_files and created_files:
            to_delete = [repo_root / rel for rel in created_files]
            if not auto_yes:
                print("\nFiles created by the patch (will be deleted on revert):")
                for p in created_files:
                    print(f"- {p}")
                if not _confirm("Delete these created files?", default_yes=False):
                    print("SKIP deletion of created files.")
                    return

            for p in to_delete:
                if p.exists():
                    # Only delete files; if a directory somehow exists here, refuse.
                    if p.is_dir():
                        raise RuntimeError(f"Refusing to delete directory created-file path: {p}")
                    p.unlink()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    # Convenience:
    # - If a positional input_path is provided, treat it as the patch source.
    if getattr(args, "input_path", ""):
        ip = Path(str(args.input_path)).expanduser().resolve()
        if args.zip or args.patch or args.revert_last or args.latest_downloads:
            # Explicit flags win; input_path is ignored in that case.
            pass
        else:
            if _is_patch_file(ip):
                args.patch = str(ip)
            elif ip.suffix.lower() == ".zip":
                args.zip = str(ip)
            else:
                _eprint(f"[FAIL] Unsupported input_path type: {ip} (expected .zip or .patch/.diff)")
                return 2

    repo_root = Path.cwd()
    # If script is launched from elsewhere, attempt to find repo root.
    # We'll keep it simple: require git repo at CWD.
    if not _is_git_repo(repo_root):
        _eprint("[FAIL] Not inside a git repository. Run this from the repo root.")
        return 3

    head = _git_head(repo_root)
    branch = _git_branch(repo_root)

    print(f"Repo: {repo_root}")
    print(f"HEAD: {head[:12]} ({branch})")

    archive_dir = (repo_root / args.archive_dir)

    # -----------------------------
    # Revert flow
    # -----------------------------
    if args.revert_last:
        # Reverting can overwrite local edits.
        # Default: allow dirty trees, but STRICT mode requires a clean tree.
        if args.strict:
            try:
                if not _git_is_clean(repo_root):
                    _eprint("[FAIL] Working tree is dirty (strict mode).")
                    _eprint("Fix: stash/commit, or re-run without --strict.")
                    return 3
            except Exception as e:
                _eprint(f"[FAIL] Could not determine git cleanliness: {e}")
                return 3

        try:
            entries = _read_patch_log(archive_dir)
            last = _find_last_applied_entry(entries)
        except Exception as e:
            _eprint(f"[FAIL] Could not locate last applied patch: {e}")
            _eprint("Fix: ensure archives/patches/patch_log.jsonl exists and contains APPLIED entries.")
            return 4

        preimage_zip_s = str(last.get("preimage_zip", "")).strip()
        if not preimage_zip_s:
            _eprint("[FAIL] Last APPLIED log entry does not include a preimage bundle.")
            _eprint("Fix: this revert feature requires patches applied with the newer apply_patch that stores preimages.")
            return 4
        preimage_zip = Path(preimage_zip_s)
        if not preimage_zip.is_absolute():
            preimage_zip = (repo_root / preimage_zip).resolve()

        applied_ts = str(last.get("ts", ""))
        applied_zip = str(last.get("archived_zip", last.get("zip", "")))
        print(f"Reverting last applied patch: ts={applied_ts}  zip={applied_zip}")
        print(f"Using preimage bundle: {preimage_zip}")

        if not args.yes:
            if not _confirm("Revert these changes now?", default_yes=False):
                print("ABORT")
                return 0

        try:
            _restore_from_preimage_bundle(
                repo_root=repo_root,
                preimage_zip=preimage_zip,
                delete_new_files=(not args.revert_no_delete_new),
                auto_yes=bool(args.yes),
            )
        except Exception as e:
            _eprint(f"[FAIL] Revert failed: {e}")
            return 5

        # Log the revert operation.
        try:
            entry = {
                "ts": _now_stamp(),
                "repo_root": str(repo_root),
                "head_before": head,
                "branch": branch,
                "reverted_applied_ts": applied_ts,
                "reverted_archived_zip": applied_zip,
                "preimage_zip": str(preimage_zip),
                "delete_new_files": bool(not args.revert_no_delete_new),
                "allow_dirty": bool(args.allow_dirty),
                "result": "REVERTED",
            }
            _append_patch_log(archive_dir, entry)
            print(f"Patch log -> {archive_dir / 'patch_log.jsonl'}")
        except Exception as e:
            _eprint(f"[WARN] Revert succeeded but logging failed: {e}")

        print("OK")
        return 0

    if args.require_head:
        req = args.require_head.strip()
        try:
            req_full_rc, req_full, req_err = _run(["git", "rev-parse", req], cwd=repo_root)
            if req_full_rc != 0:
                _eprint(f"[FAIL] --require-head commit not found: {req} ({req_err.strip()})")
                return 2
            req_full = req_full.strip()
        except Exception as e:
            _eprint(f"[FAIL] Could not resolve --require-head commit: {e}")
            return 2
        if head != req_full:
            _eprint("[FAIL] Baseline mismatch.")
            _eprint(f"  current HEAD: {head}")
            _eprint(f"  required:     {req_full}")
            _eprint("Fix: checkout the expected commit/branch or drop --require-head.")
            return 3

    # Default: allow dirty working trees. Only strict mode requires a clean tree.
    if args.strict:
        try:
            if not _git_is_clean(repo_root):
                _eprint("[FAIL] Working tree is dirty (strict mode).")
                _eprint("Fix: stash/commit, or re-run without --strict.")
                return 3
        except Exception as e:
            _eprint(f"[FAIL] Could not determine git cleanliness: {e}")
            return 3

    # Determine patch/zip source
    patch_path: Optional[Path] = None
    zip_path: Optional[Path] = None

    if getattr(args, "patch", ""):
        patch_path = Path(args.patch).expanduser().resolve()
        if not patch_path.is_file():
            _eprint(f"[FAIL] Patch file not found: {patch_path}")
            return 2
    else:
        if args.zip:
            zip_path = Path(args.zip).expanduser().resolve()
            if not zip_path.is_file():
                _eprint(f"[FAIL] Zip not found: {zip_path}")
                return 2
        else:
            force = args.downloads_dir or os.environ.get("FORCE_DOWNLOADS_DIR", "")
            try:
                downloads = _find_downloads_dir(force=force)
            except Exception as e:
                _eprint(f"[FAIL] {e}")
                return 2
            try:
                newest = _latest_any(downloads)
            except Exception as e:
                _eprint(f"[FAIL] {e}")
                return 2

            # Auto-detect mode from extension.
            if newest.suffix.lower() == ".zip":
                zip_path = newest
            else:
                patch_path = newest

            print(f"Auto-detected patch source: {newest}")

    # Unified diff patch flow
    if patch_path is not None:
        patch_source = patch_path
        print(f"Patch file: {patch_source}")

        # Stage patch into Linux FS to avoid intermittent /mnt/c I/O errors under WSL.
        try:
            patch_path = _stage_input_to_local(patch_path, purpose="unidiff")
        except Exception as e:
            _eprint(f"[FAIL] Could not stage patch file locally: {e}")
            return 2

        print(f"Staged patch file -> {patch_path}")

        allow_prefixes = [p.strip() for p in args.allow_prefix.split(",") if p.strip()]

        patch_text = _read_patch_text(patch_path)
        if (not args.allow_binary) and _patch_has_binary(patch_text):
            _eprint("[FAIL] Binary patch detected (blocked).")
            _eprint("Fix: re-run with --allow-binary if you intend to apply binary patches.")
            return 4
        if (not args.permit_deletions) and _patch_has_deletions(patch_text):
            _eprint("[FAIL] Patch deletes files (blocked).")
            _eprint("Fix: re-run with --permit-deletions if you intend to allow deletions.")
            return 4

        try:
            stats = _patch_numstat(repo_root, patch_path)
        except Exception as e:
            _eprint(f"[FAIL] Could not analyze patch: {e}")
            return 4

        # enforce allow-prefix and thresholds using numstat
        for st in stats:
            if not _path_allowed(st.path, allow_prefixes):
                _eprint(f"[FAIL] Patch touches disallowed path: {st.path}")
                _eprint(f"Allowed prefixes: {', '.join(allow_prefixes) if allow_prefixes else '(none)'}")
                return 4

        # Threshold checks
        if len(stats) > int(args.max_files):
            _eprint(f"[FAIL] Too many files changed: {len(stats)} > {int(args.max_files)}")
            return 4
        total_added = sum(s.added for s in stats)
        total_deleted = sum(s.deleted for s in stats)
        if total_deleted > int(args.max_deleted):
            _eprint(f"[FAIL] Too many deleted lines: {total_deleted} > {int(args.max_deleted)}")
            return 4
        bad = []
        for s in stats:
            if s.deleted > 25 and s.del_ratio_pct > int(args.max_del_ratio):
                bad.append(f"{s.path} (del={s.deleted} add={s.added} ratio={s.del_ratio_pct}%)")
        if bad:
            _eprint("[FAIL] High deletion ratio detected:")
            for b in bad:
                _eprint(f"- {b}")
            return 4

        # Print plan (numstat-based)
        print("\nPlanned changes:")
        print("-" * 78)
        for s in stats:
            print(f"{s.path:<60} +{s.added:<5} -{s.deleted:<5} diff")
        print("-" * 78)
        print(f"TOTAL: files={len(stats)}  +{total_added}  -{total_deleted}\n")

        # Dirty policy (overlap only)
        if args.strict and (not _git_is_clean(repo_root)):
            _eprint("[FAIL] Working tree is dirty (strict mode).")
            return 3

        dirty = _git_dirty_paths(repo_root)
        touched = [s.path for s in stats]
        overlap = sorted(set(touched).intersection(dirty))
        if overlap:
            if args.yes and (not args.force_overwrite):
                _eprint("[FAIL] Patch overlaps locally modified files and --yes was provided.")
                _eprint("Fix: run interactively or add --force-overwrite.")
                for pth in overlap[:20]:
                    _eprint(f"- {pth}")
                if len(overlap) > 20:
                    _eprint(f"  ... (+{len(overlap)-20} more)")
                return 4
            if (not args.yes) and (not args.force_overwrite):
                print("\n[WARN] Patch overlaps with locally modified files:")
                for pth in overlap[:20]:
                    print(f"- {pth}")
                if len(overlap) > 20:
                    print(f"  ... (+{len(overlap)-20} more)")
                if not _confirm("Proceed and overwrite these local modifications?", default_yes=False):
                    print("ABORT")
                    return 0

        # Review: show per-file chunks, then apply or abort (skip-per-file is not supported for unified diff patches)
        if (not args.no_review) and (not args.yes) and sys.stdin.isatty():
            chunks = _split_patch_by_file(patch_text)
            if chunks:
                for rel, lines in chunks:
                    print("=" * 78)
                    print(f"FILE: {rel}")
                    # Print with truncation
                    max_lines = int(args.diff_max_lines)
                    out_lines = lines
                    if max_lines > 0 and len(out_lines) > max_lines:
                        out_lines = out_lines[:max_lines] + [f"[diff truncated: {len(lines)} lines total, showing first {max_lines}]"]
                    for ln in out_lines:
                        print(ln)
                    raw = input("Continue? [Enter]=next, [q]=abort: ").strip().lower()
                    if raw == "q":
                        print("ABORT")
                        return 0

        if (not args.yes) and (not _confirm("Apply this patch now?", default_yes=False)):
            print("ABORT")
            return 0

        stamp = _now_stamp()

        # Preimage BEFORE apply
        try:
            preimage_zip, created_files = _write_preimage_bundle_paths(repo_root, touched, archive_dir, stamp)
            print(f"Preimage bundle -> {preimage_zip}")
        except Exception as e:
            _eprint(f"[FAIL] Could not create preimage bundle: {e}")
            return 5

        # Apply patch
        try:
            _apply_unified_patch(repo_root, patch_path)
        except Exception as e:
            _eprint(f"[FAIL] Apply failed: {e}")
            return 5

        # Archive patch file
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            archived_patch = archive_dir / f"{stamp}__applied__{patch_path.name}"
            shutil.copy2(patch_path, archived_patch)
            print(f"Archived patch -> {archived_patch}")
        except Exception as e:
            _eprint(f"[FAIL] Patch applied but archiving failed: {e}")
            return 5

        log_entry = {
            "ts": stamp,
            "repo_root": str(repo_root),
            "head": head,
            "branch": branch,
            "patch": str(patch_source),
            "archived_patch": str(archived_patch),
            "preimage_zip": str(preimage_zip),
            "created_files": created_files,
            "require_head": args.require_head.strip(),
            "allow_binary": bool(args.allow_binary),
            "allow_prefix": allow_prefixes,
            "thresholds": {
                "max_files": int(args.max_files),
                "max_added": int(args.max_added),
                "max_deleted": int(args.max_deleted),
                "max_del_ratio": int(args.max_del_ratio),
            },
            "strict": bool(args.strict),
            "force_overwrite": bool(args.force_overwrite),
            "no_review": bool(args.no_review),
            "diff_max_lines": int(args.diff_max_lines),
            "totals": {"files": int(len(stats)), "added": int(total_added), "deleted": int(total_deleted)},
            "files": [{"path": s.path, "added": int(s.added), "deleted": int(s.deleted), "del_ratio_pct": int(s.del_ratio_pct)} for s in stats],
            "type": "UNIFIED_DIFF",
        }

        # Post exec
        if args.post_exec.strip():
            print("Running post-exec...")
            rc = run_post_exec(args.post_exec)
            if rc != 0:
                rollback_ok = False
                try:
                    _restore_from_preimage_bundle(
                        repo_root=repo_root,
                        preimage_zip=preimage_zip,
                        delete_new_files=True,
                        auto_yes=bool(args.yes),
                    )
                    rollback_ok = True
                    _eprint("Rolled back patch from preimage bundle after post-exec failure.")
                except Exception as restore_exc:
                    _eprint(f"[WARN] rollback after post-exec failure failed: {restore_exc}")
                try:
                    _append_post_exec_failure_log(
                        archive_dir,
                        base_entry=log_entry,
                        post_exec_command=args.post_exec,
                        post_exec_returncode=rc,
                        rollback_ok=rollback_ok,
                    )
                    print(f"Patch log -> {archive_dir / 'patch_log.jsonl'}")
                except Exception as log_exc:
                    _eprint(f"[WARN] rollback/failure logging failed: {log_exc}")
                _eprint(f"[FAIL] post-exec command failed (exit={rc})")
                return 6
        try:
            success_entry = dict(log_entry)
            success_entry["result"] = "APPLIED"
            success_entry["post_exec"] = args.post_exec.strip()
            _append_patch_log(archive_dir, success_entry)
            print(f"Patch log -> {archive_dir / 'patch_log.jsonl'}")
        except Exception as e:
            _eprint(f"[WARN] Patch applied but logging failed: {e}")

        print("OK")
        return 0

    assert zip_path is not None
    zip_source = zip_path
    print(f"Patch zip: {zip_source}")

    # Stage zip into Linux FS to avoid intermittent /mnt/c I/O errors under WSL.
    try:
        zip_path = _stage_input_to_local(zip_path, purpose="zip")
    except Exception as e:
        _eprint(f"[FAIL] Could not stage zip file locally: {e}")
        return 2

    print(f"Staged patch zip -> {zip_path}")

    allow_prefixes = [p.strip() for p in args.allow_prefix.split(",") if p.strip()]

    try:
        entries = _zip_list(zip_path)
    except Exception as e:
        _eprint(f"[FAIL] Could not read zip: {e}")
        return 4

    flat = _detect_flat_zip([e for e in entries if not _is_dir_entry(e) and not (args.skip_md and _is_md(e))])

    # Extract
    # Keep a small summary for logging (outside temp dir lifetime).
    ops_for_log: List[dict] = []
    totals_for_log: dict = {"files": 0, "added": 0, "deleted": 0}
    stamp_for_log = _now_stamp()
    preimage_zip_for_log: Optional[Path] = None
    created_files_for_log: List[str] = []
    with tempfile.TemporaryDirectory(prefix="eiel_patch_extract_") as td:
        extract_root = Path(td)
        try:
            extracted = _extract_filtered(zip_path, extract_root, skip_md=args.skip_md)
        except Exception as e:
            _eprint(f"[FAIL] Extraction failed: {e}")
            return 4

        if not extracted:
            _eprint("[FAIL] No files extracted from patch zip (maybe it contained only .md).")
            return 4

        # Compute dirty paths once and feed into planning so we can do safe merges.
        try:
            dirty = _git_dirty_paths(repo_root)
        except Exception as e:
            _eprint(f"[FAIL] Could not read git dirty paths: {e}")
            return 3

        # Plan
        try:
            ops = plan_ops(
                repo_root=repo_root,
                extracted_files=extracted,
                extracted_root=extract_root,
                flat_zip=flat,
                allow_prefixes=allow_prefixes,
                allow_binary=args.allow_binary,
                dirty_paths=dirty,
            )
        except Exception as e:
            _eprint(f"[FAIL] Planning failed: {e}")
            return 4

        # Thresholds
        try:
            enforce_thresholds(
                ops,
                max_files=args.max_files,
                max_added=args.max_added,
                max_deleted=args.max_deleted,
                max_del_ratio_pct=args.max_del_ratio,
            )
        except Exception as e:
            _eprint("[FAIL] Safety thresholds exceeded:")
            _eprint(str(e))
            _eprint("Fix: repackage a smaller patch, or raise limits explicitly.")
            return 4

        # Dirty overlap policy:
        # - For text files that exist in HEAD, we can 3-way merge safely.
        # - For everything else (binary, flat-zip ambiguity, file absent in HEAD), prompt/force.
        nonmergeable_overlap: List[str] = []
        for o in ops:
            if not o.dest_is_dirty:
                continue
            if o.is_text and o.dest.exists() and (_git_show_head_file(repo_root, o.stat.path) is not None):
                continue
            nonmergeable_overlap.append(o.stat.path)

        if nonmergeable_overlap and (not args.force_overwrite):
            if args.yes:
                _eprint("[FAIL] Patch overlaps locally modified files that cannot be auto-merged and --yes was provided.")
                _eprint("Fix: run interactively, or add --force-overwrite.")
                for pth in nonmergeable_overlap[:20]:
                    _eprint(f"- {pth}")
                if len(nonmergeable_overlap) > 20:
                    _eprint(f"  ... (+{len(nonmergeable_overlap)-20} more)")
                return 4

            print("\n[WARN] Patch overlaps locally modified files that cannot be auto-merged:")
            for pth in nonmergeable_overlap[:20]:
                print(f"- {pth}")
            if len(nonmergeable_overlap) > 20:
                print(f"  ... (+{len(nonmergeable_overlap)-20} more)")
            if not _confirm("Proceed and overwrite these local modifications?", default_yes=False):
                print("ABORT")
                return 0

        print_plan(ops)

        # Capture a compact plan summary for patch_log.jsonl.
        ops_for_log = [
            {
                "path": o.stat.path,
                "added": int(o.stat.added),
                "deleted": int(o.stat.deleted),
                "del_ratio_pct": int(o.stat.del_ratio_pct),
                "is_text": bool(o.is_text),
                "is_new": bool(not o.dest.exists()),
            }
            for o in ops
        ]
        totals_for_log = {
            "files": int(len(ops)),
            "added": int(sum(o.stat.added for o in ops)),
            "deleted": int(sum(o.stat.deleted for o in ops)),
        }

        if not args.yes:
            if not _confirm("Apply this patch?", default_yes=False):
                print("ABORT")
                return 0

        # Ensure archive dir exists early, then write preimage bundle so we can revert safely.
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            preimage_zip_for_log, created_files_for_log = _write_preimage_bundle(
                repo_root=repo_root,
                ops=ops,
                archive_dir=archive_dir,
                stamp=stamp_for_log,
            )
            print(f"Preimage bundle -> {preimage_zip_for_log}")
        except Exception as e:
            _eprint(f"[FAIL] Could not create preimage bundle (needed for --revert-last): {e}")
            return 5

        # Apply
        try:
            apply_ops(
                repo_root=repo_root,
                ops=ops,
                iterative=args.iterative,
                auto_accept=args.yes,
                force_overwrite=bool(args.force_overwrite),
            )
        except Exception as e:
            _eprint(f"[FAIL] Apply failed: {e}")
            return 5

    # Archive zip
    try:
        archived_to = archive_zip(zip_path, archive_dir, stamp=stamp_for_log)
        print(f"Archived patch zip -> {archived_to}")
    except Exception as e:
        _eprint(f"[FAIL] Patch applied but archiving failed: {e}")
        _eprint("Fix: manually copy the zip to your archive folder.")
        return 5

    # If the patch zip contains a manifest, archive it next to the zips.
    archived_manifest: Optional[Path] = None
    try:
        archived_manifest = _archive_manifest(zip_path, archive_dir, stamp=stamp_for_log)
        if archived_manifest:
            print(f"Archived patch manifest -> {archived_manifest}")
    except Exception as e:
        _eprint(f"[WARN] Could not archive patch manifest: {e}")

    log_entry = {
        "ts": stamp_for_log,
        "repo_root": str(repo_root),
        "head": head,
        "branch": branch,
        "zip": str(zip_source),
        "archived_zip": str(archived_to),
        "archived_manifest": str(archived_manifest) if archived_manifest else "",
        "preimage_zip": str(preimage_zip_for_log) if preimage_zip_for_log else "",
        "created_files": created_files_for_log,
        "require_head": args.require_head.strip(),
        "allow_dirty": bool(args.allow_dirty),
        "allow_binary": bool(args.allow_binary),
        "allow_prefix": allow_prefixes,
        "thresholds": {
            "max_files": int(args.max_files),
            "max_added": int(args.max_added),
            "max_deleted": int(args.max_deleted),
            "max_del_ratio": int(args.max_del_ratio),
        },
        "skip_md": bool(args.skip_md),
        "post_exec": args.post_exec.strip(),
        "totals": totals_for_log,
        "files": ops_for_log,
    }

    # Optional post exec
    if args.post_exec.strip():
        print("Running post-exec...")
        rc = run_post_exec(args.post_exec)
        if rc != 0:
            rollback_ok = False
            try:
                if preimage_zip_for_log is not None:
                    _restore_from_preimage_bundle(
                        repo_root=repo_root,
                        preimage_zip=preimage_zip_for_log,
                        delete_new_files=True,
                        auto_yes=bool(args.yes),
                    )
                    rollback_ok = True
                    _eprint("Rolled back patch from preimage bundle after post-exec failure.")
            except Exception as restore_exc:
                _eprint(f"[WARN] rollback after post-exec failure failed: {restore_exc}")
            try:
                _append_post_exec_failure_log(
                    archive_dir,
                    base_entry=log_entry,
                    post_exec_command=args.post_exec,
                    post_exec_returncode=rc,
                    rollback_ok=rollback_ok,
                )
                print(f"Patch log -> {archive_dir / 'patch_log.jsonl'}")
            except Exception as log_exc:
                _eprint(f"[WARN] rollback/failure logging failed: {log_exc}")
            _eprint(f"[FAIL] post-exec command failed (exit={rc})")
            return 6
    try:
        success_entry = dict(log_entry)
        success_entry["result"] = "APPLIED"
        _append_patch_log(archive_dir, success_entry)
        print(f"Patch log -> {archive_dir / 'patch_log.jsonl'}")
    except Exception as e:
        _eprint(f"[WARN] Patch applied but logging failed: {e}")

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
