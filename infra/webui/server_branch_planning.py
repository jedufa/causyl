"""WebUI server module (extracted from server.py to keep things maintainable)."""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_run_discovery import *  # noqa: F403,F401
from infra.webui.server_ui_metadata import *  # noqa: F403,F401
from infra.webui.server_run_meta import *  # noqa: F403,F401
# -----------------------------------------------------------------------------
# Branch / continuation planning (plan-only, creates new run folders)
# -----------------------------------------------------------------------------


def _load_checkpoint_bundle_metadata(bundle_dir: Path) -> Optional[Dict[str, Any]]:
    """Read checkpoint bundle v2 metadata.json (best-effort)."""
    p = bundle_dir / "metadata.json"
    return _read_json(p)


def _select_checkpoint_from_index(idx: Dict[str, Any], *, checkpoint_id: Optional[str], generation: Optional[int]) -> Optional[Dict[str, Any]]:
    """Select a checkpoint entry from checkpoints_index_v1 dict."""
    cps = idx.get("checkpoints") if isinstance(idx.get("checkpoints"), list) else []
    if checkpoint_id:
        for c in cps:
            if isinstance(c, dict) and str(c.get("checkpoint_id", "") or "") == str(checkpoint_id):
                return c
    if generation is not None:
        for c in cps:
            if not isinstance(c, dict):
                continue
            try:
                if int(c.get("generation")) == int(generation):
                    return c
            except Exception:
                continue
    return None




def _carryover_telemetry_history(parent_sim_root: Path, new_sim_root: Path, checkpoint_gen: int) -> Dict[str, Any]:
    """Copy + trim parent telemetry artifacts into new run (best-effort)."""
    copied = []
    trimmed = []
    errors = []

    def _trim_csv_inplace(dst: Path) -> bool:
        try:
            header = _read_first_line(dst)
            if not header:
                return False
            cols = [c.strip() for c in header.split(',')]
            gen_idx = None
            for i, c in enumerate(cols):
                if c in ('gen','generation','Generation'):
                    gen_idx = i
                    break
            if gen_idx is None:
                return False
            tmp = dst.with_suffix(dst.suffix + '.tmp')
            with open(dst,'r',encoding='utf-8',newline='') as fin, open(tmp,'w',encoding='utf-8',newline='') as fout:
                fout.write(fin.readline())
                for line in fin:
                    if not line.strip():
                        continue
                    parts = line.rstrip("\r\n").split(",")
                    if len(parts)<=gen_idx:
                        continue
                    try:
                        g=int(float(parts[gen_idx]))
                    except Exception:
                        continue
                    if g<=int(checkpoint_gen):
                        fout.write(line)
            os.replace(tmp,dst)
            return True
        except Exception as e:
            errors.append(f'trim_csv:{dst.name}:{e}')
            try:
                tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
            except Exception:
                pass
            return False

    candidates = [
        ('metrics.csv','metrics.csv'),
        ('epi_av_timeseries.csv','epi_av_timeseries.csv'),
        ('viz/metrics_summary.csv','viz/metrics_summary.csv'),
        ('viz/epi_av_summary.csv','viz/epi_av_summary.csv'),
        ('viz/behaviors_agg_summary.csv','viz/behaviors_agg_summary.csv'),
    ]
    for srel, drel in candidates:
        src = parent_sim_root / srel
        dst = new_sim_root / drel
        try:
            if not src.exists() or not src.is_file() or src.is_symlink():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(drel)
            if dst.suffix.lower()=='.csv':
                if _trim_csv_inplace(dst):
                    trimmed.append(drel)
        except Exception as e:
            errors.append(f'copy:{srel}:{e}')
    return {'copied': copied, 'trimmed': trimmed, 'errors': errors}
def create_planned_run(
    *,
    runs_root: Path,
    parent_run_key: str,
    checkpoint_id: Optional[str],
    checkpoint_generation: Optional[int],
    run_kind: str,
    target_generation: Optional[int],
    label: Optional[str],
    tags: Optional[List[str]],
    changesets: Optional[List[str]],
    override_determinism_fail: bool = False,
) -> Dict[str, Any]:
    """Create a planned run folder skeleton (branch/continuation).

    This MUST NOT mutate the parent run directory.

    Returns:
        dict with new_run_key and created files.
    """
    rk_parent = normalize_run_key(parent_run_key)
    run_root, sim_root = resolve_run_dirs(runs_root, rk_parent)

    # Determinism gate.
    det_obj, _det_src = read_determinism_status(sim_root)
    det_badge, det_strict, _lvg = _det_badge_from_det_status(det_obj)
    if det_badge == "FAIL" and not override_determinism_fail:
        raise ValueError("determinism FAIL blocks branch planning (override required)")

    # Load checkpoints index.
    ck_idx, ck_src = read_checkpoints_index(sim_root)
    if not ck_idx or str(ck_idx.get("schema_version")) != "checkpoints_index_v1":
        raise ValueError("missing or incompatible checkpoints_index.json")

    ck = _select_checkpoint_from_index(ck_idx, checkpoint_id=checkpoint_id, generation=checkpoint_generation)
    if not ck:
        raise ValueError("checkpoint not found in index")

    rel_path = str(ck.get("relative_path", "") or "")
    if not rel_path:
        raise ValueError("checkpoint index entry missing relative_path")

    bundle_dir = (sim_root / Path(rel_path)).resolve()
    safe_bundle = _safe_resolve_under(sim_root, bundle_dir)
    if safe_bundle is None:
        raise ValueError("checkpoint path escapes run directory")

    if not safe_bundle.exists() or not safe_bundle.is_dir():
        raise ValueError("checkpoint bundle missing on disk")

    meta = _load_checkpoint_bundle_metadata(safe_bundle)
    if not meta or str(meta.get("schema_version")) != "checkpoint_bundle_v2":
        raise ValueError("checkpoint bundle metadata missing or incompatible")

    try:
        gen = int(meta.get("generation_index"))
    except Exception as e:
        raise ValueError("checkpoint metadata missing generation_index") from e

    ckpt_id = str(meta.get("checkpoint_id", "") or "")
    if not ckpt_id:
        ckpt_id = str(ck.get("checkpoint_id", "") or "")

    # Lineage: prefer parent branch_meta, fallback to checkpoint metadata.
    parent_bm, _bm_src = read_branch_meta_any(run_root, sim_root)
    parent_root_branch_id = str(parent_bm.get("root_branch_id")) if isinstance(parent_bm, dict) and parent_bm.get("root_branch_id") else None
    parent_root_ckpt_id = str(parent_bm.get("root_checkpoint_id")) if isinstance(parent_bm, dict) and parent_bm.get("root_checkpoint_id") else None
    parent_branch_id = str(parent_bm.get("branch_id")) if isinstance(parent_bm, dict) and parent_bm.get("branch_id") else None
    parent_depth = int(parent_bm.get("branch_depth")) if isinstance(parent_bm, dict) and parent_bm.get("branch_depth") is not None else 0

    # Fallbacks from checkpoint metadata.
    ck_branch_id = str(meta.get("branch_id", "") or "")
    ck_campaign_id = str(meta.get("campaign_id", "") or "")
    ck_ssn = _safe_int(meta.get("control_plane", {}).get("submission_sequence_number") if isinstance(meta.get("control_plane"), dict) else meta.get("submission_sequence_number"), 0)
    ck_code_commit = str(meta.get("code_commit_hash", "unknown") or "unknown")

    if not parent_branch_id:
        parent_branch_id = ck_branch_id or None

    if not parent_root_branch_id:
        parent_root_branch_id = parent_branch_id or ck_branch_id or None

    if not parent_root_ckpt_id:
        parent_root_ckpt_id = ckpt_id or None

    # Child identifiers.
    child_branch_id = str(uuid.uuid4())

    # Output folder under runs_root/planned.
    planned_root = (Path(runs_root) / "planned").resolve()
    planned_root.mkdir(parents=True, exist_ok=True)

    parent_base = Path(rk_parent).name
    suffix = uuid.uuid4().hex[:8]
    safe_kind = run_kind if run_kind in {"branch", "continuation"} else "branch"

    child_id = f"planned_{safe_kind}__{parent_base}__gen{gen:06d}__{suffix}"
    child_root = planned_root / child_id
    if child_root.exists():
        raise FileExistsError(f"planned run already exists: {child_root}")


    # Stable run key for the planned folder (relative to runs_root).
    child_key = normalize_run_key(_posix_relpath(child_root, Path(runs_root)))

    # Create skeleton dirs.
    (child_root / "ui").mkdir(parents=True, exist_ok=False)
    (child_root / "viz").mkdir(parents=True, exist_ok=False)
    (child_root / "inbox").mkdir(parents=True, exist_ok=False)

    # ui/branch_meta.json (UI-owned canonical)
    bm_obj: Dict[str, Any] = {
        "schema_version": "branch_meta_v1",
        "campaign_id": ck_campaign_id or (str(parent_bm.get("campaign_id")) if isinstance(parent_bm, dict) else ""),
        "branch_id": child_branch_id,
        "parent_branch_id": parent_branch_id,
        "root_branch_id": parent_root_branch_id,
        "parent_checkpoint_id": ckpt_id,
        "root_checkpoint_id": parent_root_ckpt_id,
        "branch_depth": int(parent_depth) + 1,
        "created_at_generation": int(gen),
        "created_at_utc": _now_utc_iso(),
        "baseline_config_hash": _compute_baseline_config_hash(sim_root),
        "code_commit_hash": ck_code_commit,
        "submission_sequence_start": int(ck_ssn),
        "submission_sequence_next": int(ck_ssn) + 1,
        "notes": "",
        # UI extras (non-lineage)
        "label": (str(label) if label else None),
        "tags": [str(t) for t in (tags or [])],
        "created_from": {"tool": "webui", "ui_version": UI_SCHEMA_VERSION, "parent_run_key": rk_parent},
    }
    atomic_write_canonical_json(child_root / "ui" / "branch_meta.json", bm_obj)

    # ui/ui_meta.json (archive + label/tags)
    atomic_write_canonical_json(
        child_root / "ui" / "ui_meta.json",
        {
            "schema_version": _UI_META_SCHEMA,
            "archived": False,
            "label": (str(label) if label else None),
            "tags": [str(t) for t in (tags or [])],
            "updated_at_utc": _now_utc_iso(),
        },
    )

    # ui/branch_plan.json
    atomic_write_canonical_json(
        child_root / "ui" / "branch_plan.json",
        {
            "schema_version": "branch_plan_v1",
            "ui_version": UI_SCHEMA_VERSION,
            "created_at_utc": _now_utc_iso(),
            "kind": safe_kind,
            "parent_run_key": rk_parent,
            "checkpoint_id": ckpt_id,
            "checkpoint_generation": int(gen),
            "relative_checkpoint_path": rel_path.replace("\\", "/"),
            "changesets": [str(c) for c in (changesets or [])],
        },
    )

    # Carry over derived telemetry history for immediate UI continuity (best-effort).
    try:
        _carryover_telemetry_history(sim_root, child_root, int(gen))
    except Exception:
        pass

    # resume_from.json pointer (legacy convenience)
    atomic_write_canonical_json(
        child_root / "resume_from.json",
        {
            "schema_version": "branch_checkpoint_pointer_v1",
            "checkpoint_dir": rel_path.replace("\\", "/"),
            "checkpoint_generation": int(gen),
        },
    )

    # ui/start_cmd.json (deterministic argv array; no shell strings)
    # This is operational metadata only; it must not influence determinism.
    try:
        additional_gens = int(tg) - int(gen)
        if additional_gens < 0:
            additional_gens = 0
    except Exception:
        additional_gens = None

    # Absolute resume_from path points to the parent checkpoint bundle.
    resume_from_abs = str(safe_bundle)

    argv = [
        "python", "-m", "engine.main",
        "--runs_dir", str(runs_root),
        "--run_id", child_key,
        "--resume_from", resume_from_abs,
        "--allow_existing_run_dir",
    ]
    if additional_gens is not None and additional_gens > 0:
        argv += ["--generations", str(int(additional_gens))]

    atomic_write_canonical_json(
        child_root / "ui" / "start_cmd.json",
        {
            "schema_version": "start_cmd_v1",
            "created_at_utc": _now_utc_iso(),
            "argv": argv,
            "env_add": {"EIEL_RUNS_ROOT": str(runs_root)},
            "cwd": str(_repo_root_from_webui()),
        },
    )

    # viz/run_status.json planned
    parent_status, _psrc = read_run_status(sim_root)
    parent_target = _safe_int(parent_status.get("target_generation"), -1) if parent_status else -1

    tg = int(target_generation) if target_generation is not None else (parent_target if parent_target > 0 else int(gen))

    rs_obj: Dict[str, Any] = {
        "schema_version": "run_status_v1",
        "status": "planned",
        "current_generation": int(gen),
        "target_generation": int(tg),
        "last_update_timestamp": _now_utc_iso(),
        "last_error": None,
        "branch_state": "PLANNED",
        "run_kind": safe_kind,
    }
    atomic_write_canonical_json(child_root / "viz" / "run_status.json", rs_obj)

    # viz/run_determinism_status.json initialized (UNKNOWN)
    det_obj_out: Dict[str, Any] = {
        "schema_version": "run_det_status_v1",
        "strict_mode": bool(det_strict),
        "phase4_last_result": "UNKNOWN",
        "last_verified_generation": -1,
        "harness_profile": None,
        "last_update_timestamp": _now_utc_iso(),
    }
    atomic_write_canonical_json(child_root / "viz" / "run_determinism_status.json", det_obj_out)

    # run_manifest.json placeholder
    atomic_write_canonical_json(
        child_root / "run_manifest.json",
        {
            "schema_version": "run_manifest_v1",
            "created_at_utc": _now_utc_iso(),
            "determinism": {
                "strict": bool(det_strict),
                "source_env": "DEBUG_DETERMINISM",
                "raw": str(os.environ.get("DEBUG_DETERMINISM", "0") or "0"),
            },
            "planned": True,
            "planned_kind": safe_kind,
            "planned_from": {"parent_run_key": rk_parent, "checkpoint_id": ckpt_id, "checkpoint_generation": int(gen)},
        },
    )

    # Optional notes stub.
    if label or tags or changesets:
        note = f"# Planned {safe_kind}\n\nParent: {rk_parent}\nCheckpoint: {ckpt_id} (gen {gen})\n"
        if label:
            note += f"Label: {label}\n"
        if tags:
            note += f"Tags: {', '.join(tags)}\n"
        if changesets:
            note += f"ChangeSets: {', '.join(changesets)}\n"
        atomic_write_text(child_root / "ui" / "notes.md", note)

    # Telemetry continuity: copy + trim parent history into new run so charts do not reset.
    _ = _carryover_telemetry_history(sim_root, child_root, gen)


    return {
        "ok": True,
        "status": "ok",
        "new_run_key": child_key,
        "new_run_id": child_id,
        "path": child_key,
        "created": {
            "ui_branch_meta": "ui/branch_meta.json",
            "ui_branch_plan": "ui/branch_plan.json",
            "viz_run_status": "viz/run_status.json",
            "run_manifest": "run_manifest.json",
        },
        "checkpoint": {"checkpoint_id": ckpt_id, "generation": int(gen), "relative_path": rel_path.replace("\\", "/")},
    }


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
