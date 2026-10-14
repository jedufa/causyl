"""Checkpoint-branching helpers shared across experiment projects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from experiments_shared.experiment_records.canonical_json import dump_canonical_to_file, dumps_canonical

F_SYS = "F-SYS"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_hex(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _canonical_bytes(obj: Any) -> bytes:
    return dumps_canonical(obj).encode("utf-8")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _uuid5_from(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(name)))


@dataclass(frozen=True)
class BundleInfo:
    bundle_dir: Path
    generation_index: int
    checkpoint_id: str
    branch_id: str
    campaign_id: str
    submission_sequence_number: int
    event_log_hash: str
    wal_hash: str
    code_commit_hash: str


def _load_bundle_v2_info(bundle_dir: Path) -> BundleInfo:
    meta_p = bundle_dir / "metadata.json"
    if not meta_p.exists():
        raise FileNotFoundError(f"missing metadata.json in checkpoint bundle: {bundle_dir}")
    meta = _read_json(meta_p)
    gen = int(meta.get("generation_index", 0) or 0)
    ckpt_id = str(meta.get("checkpoint_id", "") or "") or str(bundle_dir.name)
    branch_id = str(meta.get("branch_id", "") or "") or "default"
    campaign_id = str(meta.get("campaign_id", "") or "") or _uuid5_from(f"campaign:{ckpt_id}")
    seq = int(meta.get("submission_sequence_number", 0) or 0)
    evh = str(meta.get("event_log_hash", "") or "") or hashlib.sha256(b"").hexdigest()
    walh = str(meta.get("wal_hash", "") or "") or hashlib.sha256(b"").hexdigest()
    commit = str(meta.get("code_commit_hash", "") or "") or "unknown"
    return BundleInfo(
        bundle_dir=bundle_dir,
        generation_index=gen,
        checkpoint_id=ckpt_id,
        branch_id=branch_id,
        campaign_id=campaign_id,
        submission_sequence_number=seq,
        event_log_hash=evh,
        wal_hash=walh,
        code_commit_hash=commit,
    )


def _find_checkpoint_bundle_by_gen(parent_run_dir: Path, checkpoint_gen: int) -> Path:
    subdir = parent_run_dir / "checkpoint_bundles_v2"
    cand = subdir / f"bundle_gen{int(checkpoint_gen):06d}"
    if cand.exists() and cand.is_dir():
        return cand
    raise FileNotFoundError(f"checkpoint bundle not found for gen={checkpoint_gen}: {cand}")


def _compute_baseline_config_hash(parent_run_dir: Path) -> str:
    cfg_p = parent_run_dir / "config.json"
    obj: Any = {}
    if cfg_p.exists():
        try:
            obj = json.loads(cfg_p.read_text(encoding="utf-8"))
        except Exception:
            obj = {}
    return f"sha256:{_sha256_hex(_canonical_bytes(obj))}"


def _read_parent_branch_meta(parent_run_dir: Path) -> Optional[Dict[str, Any]]:
    p = parent_run_dir / "branch_meta.json"
    if not p.exists():
        return None
    try:
        obj = _read_json(p)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _find_prefix_len_matching_sha256(path: Path, target_hex: str) -> Optional[int]:
    target = str(target_hex or "").strip().lower()
    if not target:
        return None
    h = hashlib.sha256()
    n = 0
    with path.open("rb") as f:
        while True:
            line = f.readline()
            if not line:
                break
            h.update(line)
            n += len(line)
            if h.hexdigest() == target:
                return n
    if n == 0 and hashlib.sha256(b"").hexdigest() == target:
        return 0
    return None


def _copy_file_prefix(src: Path, dst: Path, prefix_len: int) -> None:
    _safe_mkdir(dst.parent)
    with src.open("rb") as fsrc, dst.open("wb") as fdst:
        remaining = int(prefix_len)
        while remaining > 0:
            chunk = fsrc.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            fdst.write(chunk)
            remaining -= len(chunk)


def _wal_snapshot_cutpoint(wal_dir: Path, target_hex: str) -> Optional[Tuple[List[str], Optional[Tuple[str, int]]]]:
    target = str(target_hex or "").strip().lower()
    if not target:
        return None
    if hashlib.sha256(b"").hexdigest() == target:
        return ([], None)
    try:
        files = sorted([fn for fn in os.listdir(wal_dir) if fn.endswith(".jsonl")])
    except Exception:
        files = []
    h = hashlib.sha256()
    included: List[str] = []
    for fn in files:
        h.update(fn.encode("utf-8"))
        h.update(b"\n")
        fp = wal_dir / fn
        if not fp.exists():
            continue
        off = 0
        with fp.open("rb") as f:
            while True:
                line = f.readline()
                if not line:
                    break
                h.update(line)
                off += len(line)
                hc = h.copy()
                hc.update(b"\n")
                if hc.hexdigest() == target:
                    return (included + [fn], (fn, off))
        h.update(b"\n")
        included.append(fn)
        if h.hexdigest() == target:
            return (list(included), (fn, off))
    return None


def _sha256_wal_dir(run_dir: Path) -> str:
    wdir = run_dir / "wal"
    if not wdir.exists() or not wdir.is_dir():
        return hashlib.sha256(b"").hexdigest()
    files = sorted([fn for fn in os.listdir(wdir) if fn.endswith(".jsonl")])
    h = hashlib.sha256()
    for fn in files:
        h.update(fn.encode("utf-8"))
        h.update(b"\n")
        with (wdir / fn).open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        h.update(b"\n")
    return h.hexdigest()


def _materialize_wal_snapshot(parent_run_dir: Path, child_run_dir: Path, target_wal_hash: str) -> None:
    child_wal_dir = child_run_dir / "wal"
    _safe_mkdir(child_wal_dir)
    parent_wal_dir = parent_run_dir / "wal"
    if not parent_wal_dir.exists() or not parent_wal_dir.is_dir():
        if hashlib.sha256(b"").hexdigest() != str(target_wal_hash or "").strip().lower():
            raise RuntimeError(f"{F_SYS}: parent has no wal/ dir but checkpoint wal_hash is non-empty")
        return
    cut = _wal_snapshot_cutpoint(parent_wal_dir, target_wal_hash)
    if cut is None:
        raise RuntimeError(f"{F_SYS}: unable to compute WAL cutpoint for target wal_hash={target_wal_hash}")
    files_included, last = cut
    if not files_included and last is None:
        return
    last_file, last_off = last if last is not None else (None, 0)
    for fn in files_included:
        src = parent_wal_dir / fn
        dst = child_wal_dir / fn
        if fn == last_file:
            _copy_file_prefix(src, dst, int(last_off))
        else:
            _safe_mkdir(dst.parent)
            shutil.copy2(src, dst)
    got = _sha256_wal_dir(child_run_dir)
    exp = str(target_wal_hash or "").strip().lower()
    if got != exp:
        raise RuntimeError(f"{F_SYS}: WAL snapshot hash mismatch: expected {exp} got {got}")


def _materialize_event_log_snapshot(parent_run_dir: Path, child_run_dir: Path, target_event_hash: str) -> None:
    child_events_dir = child_run_dir / "events"
    _safe_mkdir(child_events_dir)
    child_path = child_events_dir / "command_events.jsonl"
    parent_path = parent_run_dir / "events" / "command_events.jsonl"
    target = str(target_event_hash or "").strip().lower() or hashlib.sha256(b"").hexdigest()
    if not parent_path.exists():
        child_path.write_bytes(b"")
        if hashlib.sha256(b"").hexdigest() != target:
            raise RuntimeError(f"{F_SYS}: parent has no event log but checkpoint event_log_hash is non-empty")
        return
    pref = _find_prefix_len_matching_sha256(parent_path, target)
    if pref is None:
        raise RuntimeError(f"{F_SYS}: unable to find event log prefix matching event_log_hash={target}")
    _copy_file_prefix(parent_path, child_path, int(pref))
    got = _sha256_hex(child_path.read_bytes())
    if got != target:
        raise RuntimeError(f"{F_SYS}: event log snapshot hash mismatch: expected {target} got {got}")


def _materialize_plans_snapshot(parent_run_dir: Path, child_run_dir: Path, *, checkpoint_generation: int) -> None:
    parent_plans = parent_run_dir / "plans"
    child_plans = child_run_dir / "plans"
    _safe_mkdir(child_plans)
    if not parent_plans.exists() or not parent_plans.is_dir():
        return
    plan_files = sorted([p for p in parent_plans.glob("plan_*_v*.json") if p.is_file()])
    kept: List[Tuple[str, int]] = []
    for p in plan_files:
        try:
            d = _read_json(p)
            if int(d.get("created_at_generation", 0) or 0) <= int(checkpoint_generation):
                shutil.copy2(p, child_plans / p.name)
                pid = str(d.get("plan_id", "") or "")
                ver = int(d.get("version", 0) or 0)
                if pid and ver > 0:
                    kept.append((pid, ver))
        except Exception:
            continue
    if kept:
        kept.sort(key=lambda t: int(t[1]))
        plan_id, version = kept[-1]
        (child_plans / "current_plan.json").write_text(json.dumps({"plan_id": plan_id, "version": int(version)}, indent=2, sort_keys=True), encoding="utf-8")


def _copy_checkpoint_bundle(bundle_dir: Path, child_run_dir: Path) -> Path:
    rel_parent = bundle_dir.parent.name
    if rel_parent != "checkpoint_bundles_v2":
        rel_parent = "checkpoint_bundles_v2"
    dst_root = child_run_dir / rel_parent
    _safe_mkdir(dst_root)
    dst = dst_root / bundle_dir.name
    shutil.copytree(bundle_dir, dst)
    return dst


def _write_run_manifest(child_run_dir: Path, *, determinism_strict: bool, determinism_raw: str) -> None:
    dump_canonical_to_file(
        {
            "schema_version": "run_manifest_v1",
            "created_at_utc": _utc_now_iso(),
            "determinism": {"strict": bool(determinism_strict), "source_env": "DEBUG_DETERMINISM", "raw": str(determinism_raw)},
        },
        str(child_run_dir / "run_manifest.json"),
    )


def _write_run_determinism_status(child_run_dir: Path, *, determinism_strict: bool) -> None:
    dump_canonical_to_file(
        {
            "schema_version": "run_det_status_v1",
            "strict_mode": bool(determinism_strict),
            "phase4_last_result": "UNKNOWN",
            "last_verified_generation": -1,
        },
        str(child_run_dir / "run_determinism_status.json"),
    )


def branch_from_checkpoint(
    *,
    parent_run_dir: str,
    checkpoint_path: Optional[str] = None,
    checkpoint_gen: Optional[int] = None,
    child_out_dir: str,
    branch_id: Optional[str] = None,
    notes: str = "",
) -> str:
    parent = Path(parent_run_dir).resolve()
    if not parent.exists() or not parent.is_dir():
        raise FileNotFoundError(f"parent_run_dir does not exist: {parent}")
    child = Path(child_out_dir).resolve()
    if child.exists():
        raise FileExistsError(f"child_out_dir already exists: {child}")
    if checkpoint_path:
        bundle_dir = Path(checkpoint_path).resolve()
    elif checkpoint_gen is not None:
        bundle_dir = _find_checkpoint_bundle_by_gen(parent, int(checkpoint_gen))
    else:
        raise ValueError("must provide --checkpoint_path or --checkpoint_gen")
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        raise FileNotFoundError(f"checkpoint bundle dir does not exist: {bundle_dir}")

    bundle = _load_bundle_v2_info(bundle_dir)
    checkpoint_generation = int(bundle.generation_index)
    det_raw = str(os.environ.get("DEBUG_DETERMINISM", "0") or "0").strip()
    det_strict = det_raw == "1"
    parent_bm = _read_parent_branch_meta(parent)
    parent_branch_id = str(parent_bm.get("branch_id") or "") or None if isinstance(parent_bm, dict) else str(bundle.branch_id or "") or None
    root_branch_id = str(parent_bm.get("root_branch_id") or "") or None if isinstance(parent_bm, dict) else parent_branch_id
    root_checkpoint_id = str(parent_bm.get("root_checkpoint_id") or "") or None if isinstance(parent_bm, dict) else str(bundle.checkpoint_id)
    parent_depth = int(parent_bm.get("branch_depth", 0) or 0) if isinstance(parent_bm, dict) else 0
    if not root_branch_id:
        root_branch_id = str(bundle.branch_id)
    if not root_checkpoint_id:
        root_checkpoint_id = str(bundle.checkpoint_id)
    child_branch_id = str(branch_id) if branch_id else _uuid5_from(f"branch:{bundle.campaign_id}:{parent_branch_id}:{bundle.checkpoint_id}:{child.name}")
    campaign_id = str(bundle.campaign_id)
    baseline_hash = _compute_baseline_config_hash(parent)
    _safe_mkdir(child)
    child_bundle_dir = _copy_checkpoint_bundle(bundle_dir, child)
    _materialize_event_log_snapshot(parent, child, bundle.event_log_hash)
    _materialize_wal_snapshot(parent, child, bundle.wal_hash)
    _materialize_plans_snapshot(parent, child, checkpoint_generation=checkpoint_generation)
    _safe_mkdir(child / "inbox")
    submission_start = int(bundle.submission_sequence_number)
    submission_next = int(submission_start) + 1
    dump_canonical_to_file(
        {
            "schema_version": "branch_meta_v1",
            "campaign_id": str(campaign_id),
            "branch_id": str(child_branch_id),
            "parent_branch_id": str(parent_branch_id) if parent_branch_id else None,
            "root_branch_id": str(root_branch_id),
            "parent_checkpoint_id": str(bundle.checkpoint_id),
            "root_checkpoint_id": str(root_checkpoint_id),
            "branch_depth": int(parent_depth) + 1,
            "created_at_generation": int(checkpoint_generation),
            "created_at_utc": _utc_now_iso(),
            "baseline_config_hash": str(baseline_hash),
            "code_commit_hash": str(bundle.code_commit_hash or "unknown"),
            "submission_sequence_start": int(submission_start),
            "submission_sequence_next": int(submission_next),
            "notes": str(notes or ""),
        },
        str(child / "branch_meta.json"),
    )
    _write_run_manifest(child, determinism_strict=det_strict, determinism_raw=det_raw)
    _write_run_determinism_status(child, determinism_strict=det_strict)
    dump_canonical_to_file(
        {
            "schema_version": "branch_checkpoint_pointer_v1",
            "checkpoint_dir": str(os.path.relpath(child_bundle_dir, child)),
            "checkpoint_generation": int(checkpoint_generation),
        },
        str(child / "resume_from.json"),
    )
    return str(child)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Branch a run directory from a checkpoint bundle")
    p.add_argument("--parent_run_dir", required=True, type=str)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--checkpoint_path", type=str, default=None)
    group.add_argument("--checkpoint_gen", type=int, default=None)
    p.add_argument("--child_out_dir", required=True, type=str)
    p.add_argument("--branch_id", type=str, default=None)
    p.add_argument("--notes", type=str, default="")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        out = branch_from_checkpoint(
            parent_run_dir=str(args.parent_run_dir),
            checkpoint_path=str(args.checkpoint_path) if args.checkpoint_path else None,
            checkpoint_gen=int(args.checkpoint_gen) if args.checkpoint_gen is not None else None,
            child_out_dir=str(args.child_out_dir),
            branch_id=str(args.branch_id) if args.branch_id else None,
            notes=str(args.notes or ""),
        )
        sys.stdout.write(out + "\n")
        return 0
    except Exception as e:
        sys.stderr.write(f"[{F_SYS}] branch_from_checkpoint failed: {e}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
