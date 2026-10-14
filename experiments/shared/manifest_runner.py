"""Shared local batch executor for explicit experiment job manifests."""
from __future__ import annotations

import argparse
import subprocess
import os
import platform
import re
import sys
import time
import json
import signal
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

_THIS_FILE = os.path.abspath(__file__)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(_THIS_FILE), os.pardir, os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from experiments_shared.experiment_records.canonical_json import dump_canonical_to_file
from experiments_shared.experiment_records.run_lifecycle import read_run_record, summarize_run_record
from experiments_shared.gpu import build_slot_devices, resolve_gpu_execution_plan
from experiments_shared.gpu import GpuInfo, enrich_run_meta, list_gpus_via_nvidia_smi, looks_like_oom
from experiments_shared.job_manifests.io import load_manifest as _load_manifest, resolve_output_root as _resolve_output_root, utc_now_iso as _utc_now_iso
from experiments_shared.job_manifests.job_spec import build_job_spec as _build_job_spec
from experiments_shared.job_manifests.progress import ProgressTracker
from experiments_shared.job_manifests.types import JobResult
from experiments_shared.job_manifests.wrapper_exec import _run_one_job_via_wrapper, _find_wrapper_dir_for_job, _read_json


def _dbg(msg: str) -> None:
    """Emit a debug line to stderr when EXPERIMENT_DEBUG_RUN_MANIFEST=1."""
    if os.environ.get("EXPERIMENT_DEBUG_RUN_MANIFEST", "").strip() != "1":
        return
    try:
        sys.stderr.write(f"[manifest_runner debug] {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


def _extract_fail_summary_from_decision_debug(obj: Any) -> str:
    """Extract the best available FAIL_SUMMARY from a decision_debug object.

    The decision_debug schema may store fail_summary at the top level or per-layer.
    We prefer the most recent non-empty layer fail_summary if top-level is missing.

    Parameters
    ----------
    obj:
        Parsed JSON object (dict) from decision_debug.json, error.json context, or report.json.debug.decision.

    Returns
    -------
    str
        Best-effort FAIL_SUMMARY line, or empty string if not found.
    """
    try:
        if isinstance(obj, dict):
            fs = str(obj.get("fail_summary", "") or "")
            if fs:
                return fs
            layers = obj.get("layers", None)
            if isinstance(layers, list):
                for layer in reversed(layers):
                    if isinstance(layer, dict):
                        lfs = str(layer.get("fail_summary", "") or "")
                        if lfs:
                            return lfs
    except Exception:
        return ""
    return ""


def _extract_fail_summary(*, wrapper_dir: str, fallback_code: str) -> str:
    """Extract a human-actionable failure summary for a wrapper job.

    Search order (best-first):
      1) wrapper_dir/decision_debug.json (top-level or last layer)
      2) wrapper_dir/error.json -> context.decision_debug (top-level or last layer)
      3) wrapper_dir/payload/report.json -> debug.decision.fail_summary
      4) fallback_code (e.g., failure_code)

    Parameters
    ----------
    wrapper_dir:
        Absolute wrapper job directory.
    fallback_code:
        A coarse failure code if no decision-level summary is found.

    Returns
    -------
    str
        Failure summary string (never empty).
    """
    fs = ""

    # 1) decision_debug.json
    try:
        dd_path = os.path.join(wrapper_dir, "decision_debug.json")
        if os.path.exists(dd_path):
            dd = _read_json(dd_path)
            fs = _extract_fail_summary_from_decision_debug(dd)
    except Exception:
        fs = fs or ""

    # 2) error.json -> context.decision_debug
    if not fs:
        try:
            err_path = os.path.join(wrapper_dir, "error.json")
            if os.path.exists(err_path):
                err = _read_json(err_path)
                ctx = err.get("context", {}) if isinstance(err, dict) else {}
                dd = ctx.get("decision_debug", {}) if isinstance(ctx, dict) else {}
                fs = _extract_fail_summary_from_decision_debug(dd)
        except Exception:
            fs = fs or ""

    # 3) payload/report.json -> debug.decision.fail_summary
    if not fs:
        try:
            rpt_path = os.path.join(wrapper_dir, "payload", "report.json")
            if os.path.exists(rpt_path):
                rpt = _read_json(rpt_path)
                dbg = rpt.get("debug", {}) if isinstance(rpt, dict) else {}
                dec = dbg.get("decision", {}) if isinstance(dbg, dict) else {}
                if isinstance(dec, dict):
                    fs = str(dec.get("fail_summary", "") or "")
        except Exception:
            fs = fs or ""

    if not fs:
        fs = fallback_code or "FAILED (no fail_summary)"

    return str(fs)


def _job_result_status_from_run_record(*, wrapper_dir: str, wrapper_root: str, rc: int) -> Dict[str, Any]:
    """Resolve wrapper terminal result from canonical run_record truth first."""
    run_record_path = os.path.join(wrapper_dir, "run_record.json")
    run_record_rel = os.path.relpath(run_record_path, wrapper_root).replace(os.sep, "/")
    if not os.path.exists(run_record_path):
        return {
            "run_record_rel": "",
            "status": "failed",
            "exit_code": int(rc),
            "failure_code": "MISSING_RUN_RECORD",
        }
    try:
        record = read_run_record(run_record_path)
        summary = summarize_run_record(record)
    except Exception as exc:
        return {
            "run_record_rel": run_record_rel,
            "status": "failed",
            "exit_code": int(rc),
            "failure_code": f"INVALID_RUN_RECORD:{exc}",
        }

    state = str(summary.get("state") or "").strip().lower()
    if state == "succeeded":
        status = "ok"
    elif state == "skipped":
        status = "skipped"
    else:
        status = "failed"
    return {
        "run_record_rel": run_record_rel,
        "status": status,
        "exit_code": int(summary.get("exit_code") if summary.get("exit_code") is not None else rc),
        "failure_code": str(summary.get("failure_code") or "").strip(),
    }


_GIT_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_GIT_HEX_SHORT_RE = re.compile(r"^[0-9a-f]{7,39}$")


def _validate_git_ref_immutable(*, repo_root: str, git_ref: str) -> None:
    """Validate that git_ref is immutable under the experiment contract.

    Rules
    -----
    Allowed:
      - Full 40-hex commit SHA
      - Annotated git tag (git object type 'tag') that resolves to a commit

    Disallowed:
      - Short SHAs
      - Branch names
      - Any '*dirty*' marker refs

    This preflight prevents instant wrapper failures that create no evidence.
    """
    ref = (git_ref or "").strip()
    if not ref:
        raise RuntimeError("MANIFEST_INVALID: missing required field: git_ref")

    if "dirty" in ref.lower():
        raise RuntimeError(f"MANIFEST_INVALID_GIT_REF: git_ref may not contain 'dirty' for manifest runs (got: {ref})")

    if _GIT_HEX_SHORT_RE.match(ref) and not _GIT_HEX_40_RE.match(ref):
        raise RuntimeError(
            f"MANIFEST_INVALID_GIT_REF: short SHA not allowed (got: {ref}). "
            "Use full 40-character commit hash from: git rev-parse HEAD"
        )

    # Full SHA is acceptable without consulting git.
    if _GIT_HEX_40_RE.match(ref):
        return

    # Non-hex refs must be annotated tags. Reject branches and lightweight tags.
    try:
        t = subprocess.check_output(["git", "cat-file", "-t", ref], cwd=repo_root, text=True).strip()
    except Exception as e:
        raise RuntimeError(f"MANIFEST_INVALID_GIT_REF: cannot resolve git_ref '{ref}' (git cat-file failed)") from e

    if t != "tag":
        raise RuntimeError(
            f"MANIFEST_INVALID_GIT_REF: git_ref '{ref}' is type '{t}', not an annotated tag. "
            "Use a full 40-char SHA or an annotated tag."
        )

    # Ensure it resolves to a commit.
    try:
        _ = subprocess.check_output(["git", "rev-parse", f"{ref}^{{commit}}"], cwd=repo_root, text=True).strip()
    except Exception as e:
        raise RuntimeError(f"MANIFEST_INVALID_GIT_REF: annotated tag '{ref}' does not resolve to a commit") from e


def _build_node_report(*, node_id: str, created_at_utc: str, jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build node_report.json object (schema v1)."""
    return {
        "schema_version": "v1",
        "node_id": node_id,
        "created_at_utc": created_at_utc,
        "system": {
            "hostname": platform.node(),
            "os": platform.platform(),
            "python": sys.version.split()[0],
        },
        "jobs": jobs,
    }



def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI args for manifest runner."""
    p = argparse.ArgumentParser(description="Phase 2 local manifest runner (calls Phase 1 wrapper)")
    p.add_argument("--manifest", required=True, help="Path to manifest v1 (YAML or JSON)")
        # ------------------------------------------------------------
    # Compute-slot controls (v2 naming)
    #
    # Design goals:
    # - Make GPU packing deterministic (no reliance on nvidia-smi unless explicitly requested).
    # - Preserve a 2-flag mental model:
    #     * --gpus_available_for_sims selects GPU devices or forces CPU mode
    #     * --sims_compute_unit controls parallel sims per compute unit:
    #         - GPU mode: sims per GPU device
    #         - CPU mode: max parallel wrapper jobs (CPU worker slots)
    #
    # Defaults:
    # - If --gpus_available_for_sims is NOT provided, default to 'cuda:0'
    #   (GPU-first default; this matches typical local usage).
    # - If --sims_compute_unit is NOT provided, default to 2.
    # ------------------------------------------------------------
    p.add_argument(
        "--gpus_available_for_sims",
        default=None,
        help=(
            "Compute targets for simulations. "
            "Accepted: 'cpu' (force CPU mode), 'auto' (use detected GPUs else CPU), "
            "or explicit comma-separated CUDA device list like 'cuda:0,cuda:1'. "
            "If not provided, defaults to 'cuda:0' (GPU-first)."
        ),
    )
    p.add_argument(
        "--sims_compute_unit",
        type=int,
        default=2,
        help=(
            "Parallel sims per compute unit (default: 2). "
            "GPU mode: sims per GPU device. CPU mode: max parallel wrapper jobs."
        ),
    )

    # ------------------------------------------------------------
    # Legacy / deprecated flags (kept for backward compatibility)
    # ------------------------------------------------------------
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help=(
            "[DEPRECATED] Concurrency control. Previously: GPUs-to-use when packing active; "
            "otherwise: max concurrent wrapper jobs. Prefer --gpus_available_for_sims and "
            "--sims_compute_unit."
        ),
    )
    p.add_argument(
        "--sims_per_gpu",
        type=int,
        default=2,
        help="[DEPRECATED] Prefer --sims_compute_unit.",
    )
    p.add_argument(
        "--allow_3_per_gpu",
        action="store_true",
        help="[DEPRECATED] No longer required. Use --sims_compute_unit 3 to request 3 sims per GPU.",
    )
    p.add_argument(
        "--packing_degradation_threshold",
        type=float,
        default=1.35,
        help="[DEPRECATED] Kept for compatibility; backoff controller is not active in v2 naming mode.",
    )
    p.add_argument(
        "--packing_disable_degradation_backoff",
        action="store_true",
        help="[DEPRECATED] Kept for compatibility; backoff controller is not active in v2 naming mode.",
    )
    p.add_argument(
        "--packing_devices",
        default="",
        help="[DEPRECATED] Prefer --gpus_available_for_sims with explicit cuda list.",
    )
    p.add_argument("--timeout_sec", type=int, default=0, help="Optional timeout passed to wrapper (0 = none)")

    # IMPORTANT CONTRACT NOTE
    # -----------------------
    # We intentionally default this flag to "" (unset) so that:
    #   1) manifest.output_root can take effect when present
    #   2) the environment variable (EXPERIMENT_WRAPPER_ROOT by default) can take effect
    #   3) only an *explicit* CLI flag overrides those
    #
    # Older Phase-2 runner versions used default="runtime/live/wrapper_jobs", which made the
    # CLI flag always "truthy" and therefore *always* override manifest.output_root.
    # That created confusing behavior where manifests specifying output_root were
    # silently ignored unless users passed a different path.
    p.add_argument(
        "--output_root",
        default="",
        help=(
            "Wrapper output root. If omitted, uses manifest.output_root, then env var "
            "EXPERIMENT_WRAPPER_ROOT, "
            "then repo default (<repo_root>/runtime/live/wrapper_jobs)."
        ),
    )
    p.add_argument("--env_output_root_key", default="EXPERIMENT_WRAPPER_ROOT", help="Primary env var name for output root")
    p.add_argument("--report_dir", default="", help="Directory to write node_report + job_specs (default: runs/manifests/<ts>__<name>)")
    p.add_argument("--node_id", default="", help="Optional node identifier (default: hostname)")
    p.add_argument("--dry_run", action="store_true", help="Build job specs + node report, but do not execute jobs")
    p.add_argument("--follow", action="store_true", help="Stream stdout_stderr.log of a selected job to console (non-blocking)",)
    p.add_argument("--follow_job", default="", help="Specific job_id to follow (works without --follow; overrides --follow default behavior)",)
    # ------------------------------------------------------------
    # Observability / Logging
    # ------------------------------------------------------------
    p.add_argument("--log_level", default="", help="Base log level for this runner process (INFO/DEBUG/TRACE). Overrides EXPERIMENT_LOG for this process only.",)
    p.add_argument("--log", action="append", default=[], help="Per-logger override like 'experiment.runner=DEBUG' (repeatable). Passed through via EXPERIMENT_LOG_OVERRIDES.",)
    p.add_argument("--progress-mode", dest="progress_mode", default="compact", choices=["compact", "detailed", "off"],
            help=(
                "Progress UI mode. "
                "compact=single-line baseline output (default); "
                "detailed=operator dashboard with worker + GPU visibility; "
                "off=disable progress printing."
            ),  )
    return p.parse_args(argv)



def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint."""
    args = _parse_args(argv)
    _dbg("entered main; argv=%s" % ("None" if argv is None else "..."))
    _dbg(f"args.manifest={getattr(args,'manifest',None)} progress_mode={getattr(args,'progress_mode',None)} concurrency={getattr(args,'concurrency',None)}")
    repo_root = _REPO_ROOT
    _dbg(f"repo_root={repo_root}")

    manifest = _load_manifest(args.manifest)
    _dbg("manifest loaded")
    if int(manifest.get("manifest_api", 0) or 0) != 1:
        raise RuntimeError(f"MANIFEST_UNSUPPORTED_API: manifest_api must be 1 (got {manifest.get('manifest_api')})")

    git_ref = manifest.get("git_ref", "")
    _validate_git_ref_immutable(repo_root=str(repo_root), git_ref=str(git_ref))

    manifest_output_root = str(manifest.get("output_root", "") or "")
    wrapper_root = _resolve_output_root(args.output_root, manifest_output_root, args.env_output_root_key, repo_root)
    os.makedirs(wrapper_root, exist_ok=True)

    # report_dir default
    if args.report_dir:
        report_dir = os.path.abspath(os.path.expanduser(args.report_dir))
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base = os.path.splitext(os.path.basename(args.manifest))[0]
        report_dir = os.path.join(repo_root, "runs", "manifests", f"{ts}__{base}")
        report_dir = os.path.abspath(report_dir)
    os.makedirs(report_dir, exist_ok=True)

    # ------------------------------------------------------------
    # Control-plane sentinels (STOP/PAUSE)
    # ------------------------------------------------------------
    control_dir = os.path.join(report_dir, ".control")
    try:
        os.makedirs(control_dir, exist_ok=True)
    except Exception:
        pass
    stop_sentinel = os.path.join(control_dir, "STOP")
    pause_sentinel = os.path.join(control_dir, "PAUSE")

    # A stable, human-readable run identifier for decision-debug packets.
    # This is NOT the wrapper folder; it identifies the manifest invocation.
    pipeline_run_id = os.path.basename(os.path.abspath(report_dir))

    # ------------------------------------------------------------
    # Observability: configure runner logging (console + JSONL)
    # ------------------------------------------------------------
    if args.log_level:
        os.environ["EXPERIMENT_LOG"] = str(args.log_level).strip()
    if args.log:
        prior = os.environ.get("EXPERIMENT_LOG_OVERRIDES", "").strip()
        merged_parts = []
        if prior:
            merged_parts.append(prior)
        merged_parts.extend([str(x) for x in args.log if str(x).strip()])
        os.environ["EXPERIMENT_LOG_OVERRIDES"] = ",".join(merged_parts)

    from experiments_shared.runner_logging import create_logger, log_event
    logger = create_logger(report_dir, level=os.environ.get("EXPERIMENT_LOG", "INFO"))
    log_event(logger, event="RUNNER_START", manifest=args.manifest, report_dir=report_dir, wrapper_root=wrapper_root)


    jobs_in: List[Dict[str, Any]] = list(manifest.get("jobs", []))
    _dbg(f"jobs_in={len(jobs_in)}")
    if not jobs_in:
        raise RuntimeError("MANIFEST_INVALID: no jobs[] provided")

    # Validate unique job_id
    seen = set()
    for j in jobs_in:
        jid = str(j.get("job_id", "") or "")
        if not jid:
            raise RuntimeError("MANIFEST_INVALID: job missing job_id")
        if jid in seen:
            raise RuntimeError(f"MANIFEST_INVALID: duplicate job_id: {jid}")
        seen.add(jid)

    # ------------------------------------------------------------
    # Follow logic (optional live streaming of one job's wrapper log)
    #
    # Behavior:
    #   - If --follow_job is set: follow that job_id (even if --follow is not set)
    #   - Else if --follow is set: follow first job in manifest order (deterministic)
    #   - Else: no streaming (default behavior unchanged)
    # ------------------------------------------------------------
    follow_job_id: str = ""
    if getattr(args, "follow_job", ""):
        follow_job_id = str(args.follow_job)
    elif bool(getattr(args, "follow", False)) and jobs_in:
        follow_job_id = str(jobs_in[0]["job_id"])

    # Validate follow_job_id if provided.
    if follow_job_id and follow_job_id not in seen:
        raise RuntimeError(f"MANIFEST_INVALID: --follow_job refers to unknown job_id: {follow_job_id}")

    # Follow thread stop signal (Option B: stop when the selected job finishes).
    import threading
    follow_stop_event = threading.Event()

    def _tail_file_live(path: str, stop_event: "threading.Event") -> None:
        """Stream a log file to stdout until stop_event is set and file is exhausted."""
        import time

        # Wait for file to exist (job may still be starting)
        for _ in range(600):  # ~60s
            if os.path.exists(path):
                break
            if stop_event.is_set():
                return
            time.sleep(0.1)

        if not os.path.exists(path):
            return

        # Mimic: tail -n 50 then follow
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            for line in lines[-50:]:
                sys.stderr.write(line)
            sys.stderr.flush()
        except Exception:
            pass

        # Follow new content
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(0, os.SEEK_END)
                idle_ticks = 0
                while True:
                    line = f.readline()
                    if line:
                        idle_ticks = 0
                        sys.stderr.write(line)
                        sys.stderr.flush()
                        continue

                    if stop_event.is_set():
                        idle_ticks += 1
                        if idle_ticks >= 5:  # ~1s after stop signal
                            return
                    time.sleep(0.2)
        except Exception:
            return

    def _start_follow_thread(wrapper_root: str, job_id: str, job_spec_hash12: str) -> "threading.Thread":
        """Start a daemon thread to follow stdout_stderr.log for one wrapper job."""
        import time

        def _worker() -> None:
            log_path = ""
            for _ in range(900):  # ~90s
                if follow_stop_event.is_set():
                    return
                d = _find_wrapper_dir_for_job(wrapper_root, job_id, job_spec_hash12)
                if d:
                    cand = os.path.join(d, "stdout_stderr.log")
                    if os.path.exists(cand):
                        log_path = cand
                        break
                time.sleep(0.1)
            if not log_path:
                return
            _tail_file_live(log_path, follow_stop_event)

        t = threading.Thread(target=_worker, name=f"follow:{job_id}", daemon=True)
        t.start()
        return t

    # Build job specs now (deterministic).
    job_specs: Dict[str, Dict[str, Any]] = {}
    job_spec_paths: Dict[str, str] = {}

    for j in jobs_in:
        job_id = str(j["job_id"])
        module = str(j.get("module", "") or "")
        if not module:
            raise RuntimeError(f"MANIFEST_INVALID: job {job_id} missing module")
        args_dict = dict(j.get("args", {}) or {})

        # Seed handling
        # -------------
        # Some entrypoints in this repo do NOT accept a --seed CLI flag.
        # Historically, this runner hard-required args.seed which forced
        # run_manifest manifests to include --seed in cli_args, causing
        # failures for seedless modules.
        #
        # Contract we enforce now:
        # - If args.seed is present: record it in job_spec.json AND pass it
        #   through to the entrypoint via cli_args (because _render_cli_args
        #   will render --seed).
        # - If args.seed is missing: record a NOMINAL seed (0) in job_spec.json
        #   to preserve schema/evidence, but do NOT pass --seed to the entrypoint.
        if "seed" in args_dict:
            seed = int(args_dict["seed"])
        else:
            seed = 0

        spec = _build_job_spec(job_id=job_id, git_ref=str(git_ref), module=module, args_dict=args_dict, seed=seed)
        job_specs[job_id] = spec
        spec_path = os.path.join(report_dir, f"job_spec__{job_id}.json")
        dump_canonical_to_file(spec, spec_path)
        job_spec_paths[job_id] = spec_path

    # Phase 3: keep canonical job definitions for retries (job specs must be regenerated per attempt).
    job_defs: Dict[str, Dict[str, Any]] = {}
    for j in jobs_in:
        jid = str(j["job_id"])
        job_defs[jid] = {
            "module": str(j.get("module", "") or ""),
            "args_dict": dict(j.get("args", {}) or {}),
        }


    # Run jobs via wrapper with concurrency.
    node_id = args.node_id.strip() or platform.node()
    node_created_at = _utc_now_iso()
    node_jobs_out: List[Dict[str, Any]] = []


        # ------------------------------------------------------------
    # Compute-slot plan resolution (v2 naming)
    # ------------------------------------------------------------
    raw_argv = (argv if argv is not None else sys.argv[1:]) or []
    raw_argv_set = set([str(x) for x in raw_argv])

    # Resolve v2 inputs with GPU-first defaults.
    gpus_available_for_sims = str(getattr(args, "gpus_available_for_sims", None) or "").strip()
    if not gpus_available_for_sims:
        gpus_available_for_sims = "cuda:0"

    sims_compute_unit = int(getattr(args, "sims_compute_unit", 2) or 2)
    if sims_compute_unit < 1:
        sims_compute_unit = 1
    if sims_compute_unit > 3:
        sims_compute_unit = 3

    # Legacy detection (if user explicitly used legacy flags, keep legacy semantics).
    legacy_used = any(
        flag in raw_argv_set
        for flag in (
            "--packing_devices",
            "--sims_per_gpu",
            "--allow_3_per_gpu",
            "--packing_degradation_threshold",
            "--packing_disable_degradation_backoff",
        )
    )

    plan = resolve_gpu_execution_plan(
        raw_gpu_request=gpus_available_for_sims,
        sims_compute_unit=sims_compute_unit,
        legacy_used=legacy_used,
        legacy_packing_devices_raw=str(args.packing_devices or ""),
        legacy_sims_per_gpu=int(args.sims_per_gpu or 2),
        legacy_allow_3_per_gpu=bool(args.allow_3_per_gpu),
        legacy_concurrency=int(args.concurrency or 1),
        job_resource_devices=[str((j.get("resources", {}) or {}).get("device", "") or "") for j in jobs_in],
    )
    packing_devices: List[str] = list(plan["packing_devices"])

    # Build GPU info map (best-effort).
    gpu_info_by_device: Dict[str, Optional[GpuInfo]] = {}
    for gi in list_gpus_via_nvidia_smi():
        gpu_info_by_device[gi.device] = gi
    for d in packing_devices:
        gpu_info_by_device.setdefault(d, None)

    conc = int(plan["concurrency"])
    packing_used_per_device = int(plan["packing_used_per_device"])
    _dbg(f"gpu-plan: semantics={plan['semantics']} mode={plan['mode']} request={plan['gpu_request']!r}")
    _dbg(f"gpu-plan: packing_devices={packing_devices} packing_used_per_device={packing_used_per_device} conc={conc}")

    # Runner execution plan (human + machine readable)
    try:
        if logger is not None:
            mode = str(plan["mode"])
            conc_semantics = str(plan["semantics"])
            sims_per = int(packing_used_per_device)
            gpu_spec = str(plan["gpu_request"])

            log_event(
                logger,
                event="EXECUTION_PLAN",
                mode=mode,
                semantics=conc_semantics,
                gpus_available_for_sims=str(gpu_spec),
                sims_compute_unit=int(sims_per),
                computed_concurrency=int(conc),
                packing_devices=list(packing_devices),
                jobs_in=int(len(jobs_in)),
            )
            if int(len(jobs_in)) < int(conc):
                log_event(
                    logger,
                    level="WARNING",
                    event="CONCURRENCY_UNUSED",
                    reason="jobs_in < computed_concurrency; extra slots will idle",
                    jobs_in=int(len(jobs_in)),
                    computed_concurrency=int(conc),
                )
    except Exception:
        pass




    def _write_job_spec_for_attempt(job_id: str, attempt_index: int) -> Tuple[Dict[str, Any], str]:
        """Build and write a fresh job_spec.json for a specific attempt.

        Notes
        -----
        - The wrapper run folder name includes job_spec_hash.
        - job_spec contains created_at_utc, so each attempt yields a distinct hash,
          preserving atomicity without overwriting prior evidence.
        """
        jdef = job_defs[job_id]
        module = str(jdef["module"])
        args_dict = dict(jdef["args_dict"])

        if "seed" in args_dict:
            seed = int(args_dict["seed"])
        else:
            seed = 0

        spec = _build_job_spec(job_id=job_id, git_ref=str(git_ref), module=module, args_dict=args_dict, seed=seed)
        spec_path = os.path.join(report_dir, f"job_spec__{job_id}__attempt{attempt_index:02d}.json")
        dump_canonical_to_file(spec, spec_path)
        return spec, spec_path


    def _exec_job(job_id: str, *, device: str, packing_used: int, attempt_index: int) -> JobResult:
        """Execute one job attempt via wrapper on a specific device."""
        spec, spec_path = _write_job_spec_for_attempt(job_id, attempt_index)
        t0 = time.time()

        # Decision-debug context packet (runner layer). This will be merged downstream.
        # Keep it compact because it lives in an env var.
        try:
            dbg_ctx = {
                "schema": "experiment.decision_debug.v1",
                "pipeline_run_id": str(pipeline_run_id),
                "manifest": str(os.path.abspath(args.manifest)),
                "git_ref": str(git_ref),
                "job_id": str(job_id),
                "job_spec_hash": str(spec.get("job_spec_hash", "")),
                "seed": int(spec.get("seed", 0) or 0),
                "device": str(device or ""),
                "wrapper_root": str(wrapper_root),
                "report_dir": str(report_dir),
            }
            dbg_ctx_json = json.dumps(dbg_ctx, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        except Exception:
            dbg_ctx_json = ""

        if args.dry_run:
            rc = 0
            wrapper_out = "DRY RUN: wrapper not executed"
        else:
            rc, wrapper_out = _run_one_job_via_wrapper(
                repo_root=repo_root,
                wrapper_py=os.path.join(repo_root, "experiments_shared", "run_job.py"),
                wrapper_root=wrapper_root,
                job_spec_path=spec_path,
                device=device,
                timeout_sec=int(args.timeout_sec or 0),
                debug_ctx_json=dbg_ctx_json,
                control_stop_path=stop_sentinel,
            )

        t1 = time.time()
        wall = round(t1 - t0, 3)

        # Locate wrapper directory produced by wrapper
        wrapper_dir = _find_wrapper_dir_for_job(wrapper_root, job_id, str(spec["job_spec_hash"])[:12])
        if not wrapper_dir:
            # Wrapper failed before creating its forensic run folder.
            # Persist the wrapper stdout/stderr into the runner report_dir so the
            # operator has *some* evidence instead of a useless RUN_EVIDENCE_WRITE_FAIL.
            try:
                out_path = os.path.join(report_dir, f"wrapper_out__{job_id}__attempt{attempt_index:02d}.log")
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(wrapper_out or "")
            except Exception:
                pass

                return JobResult(
                    job_id=job_id,
                    job_spec_hash=str(spec["job_spec_hash"]),
                    status="failed",
                    exit_code=int(rc),
                    failure_code="RUN_EVIDENCE_WRITE_FAIL",
                    wall_time_sec=float(wall),
                    run_folder_rel="",
                    wrapper_dir_abs="",
                    run_record_rel="",
                    run_meta_rel="",
                    report_path_rel="",
                    artifact_manifest_rel="",
                )

        run_record_info = _job_result_status_from_run_record(wrapper_dir=wrapper_dir, wrapper_root=wrapper_root, rc=rc)
        run_record_rel = str(run_record_info.get("run_record_rel") or "")
        run_meta_path = os.path.join(wrapper_dir, "run_meta.json")
        run_meta_rel = os.path.relpath(run_meta_path, wrapper_root).replace(os.sep, "/") if os.path.exists(run_meta_path) else ""
        run_folder_rel = os.path.relpath(wrapper_dir, wrapper_root).replace(os.sep, "/")

        failure_code = str(run_record_info.get("failure_code") or "")
        status = str(run_record_info.get("status") or "failed")
        exit_code = int(run_record_info.get("exit_code", rc) or rc)

        oom_suspected = False
        gpu_info = gpu_info_by_device.get(device)

        if os.path.exists(run_meta_path):
            rm = _read_json(run_meta_path)
            if not run_record_rel:
                rm_status = str(rm.get("status", "") or "").strip().lower()
                if rm_status == "complete":
                    status = "ok"
                elif rm_status == "skipped":
                    status = "skipped"
                else:
                    status = "failed"
                failure_code = str(rm.get("failure_code", "") or "")
                exit_code = int(rm.get("exit_code", exit_code) or exit_code)

            # If the wrapper wrote a decision debug packet, surface a compact FAIL_SUMMARY
            # line in runner logs for operator sanity.
            try:
                dd_path = os.path.join(wrapper_dir, "decision_debug.json")
                if os.path.exists(dd_path) and status == "failed":
                    dd = _read_json(dd_path)
                    fs = str(dd.get("fail_summary", "") or "")
                    if fs:
                        _dbg(fs)
            except Exception:
                pass

            oom_suspected = looks_like_oom(wrapper_out)

            # Enrich run_meta.json with Phase 3 fields.
            try:
                rm2 = enrich_run_meta(
                    rm,
                    gpu_info=gpu_info,
                    device=device,
                    packing_used=int(packing_used),
                    oom_suspected=oom_suspected,
                )
                dump_canonical_to_file(rm2, run_meta_path)
            except Exception:
                pass
        else:
            if not run_record_rel:
                status = "failed"
                failure_code = "MISSING_RUN_META"

        if status == "failed" and oom_suspected:
            if failure_code:
                failure_code = f"OOM|{failure_code}"
            else:
                failure_code = "OOM"

        report_path = os.path.join(wrapper_dir, "payload", "report.json")
        report_path_rel = os.path.relpath(report_path, wrapper_root).replace(os.sep, "/") if os.path.exists(report_path) else ""

        artifact_manifest_path = os.path.join(wrapper_dir, "artifact_manifest.json")
        artifact_manifest_rel = os.path.relpath(artifact_manifest_path, wrapper_root).replace(os.sep, "/") if os.path.exists(artifact_manifest_path) else ""

        return JobResult(
            job_id=job_id,
            job_spec_hash=str(spec["job_spec_hash"]),
            status=status,
            exit_code=int(exit_code),
            failure_code=str(failure_code or ""),
            wall_time_sec=float(wall),
            run_folder_rel=str(run_folder_rel),
            wrapper_dir_abs=str(wrapper_dir),
            run_record_rel=str(run_record_rel),
            run_meta_rel=str(run_meta_rel),
            report_path_rel=str(report_path_rel),
            artifact_manifest_rel=str(artifact_manifest_rel),
        )



    # ------------------------------------------------------------
    # Execute jobs via wrapper (basic scheduler)
    #
    # NOTE
    # - The Phase 3 detailed dashboard implementation is not wired here because
    #   this module currently lacks the helper functions it references
    #   (_eta_model_detailed, _render_progress_detailed). When --progress-mode
    #   is "detailed", we fall back to a compact progress line.
    # - This restores the core behavior: jobs actually run.
    # ------------------------------------------------------------
    results: List[JobResult] = []

    progress_mode = str(getattr(args, "progress_mode", "compact") or "compact").strip().lower()
    if progress_mode not in ("compact", "detailed", "off"):
        progress_mode = "compact"

    # Build a deterministic slot->device mapping.
    slot_devices = build_slot_devices(
        packing_devices=packing_devices,
        packing_used_per_device=packing_used_per_device,
        concurrency=int(conc),
    )

    def _progress(msg: str) -> None:
        """Emit progress lines unless progress is disabled."""
        if progress_mode == "off":
            return
        try:
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
        except Exception:
            pass

    # Start optional follow thread (best-effort).
    follow_thread = None
    if follow_job_id:
        try:
            spec0 = job_specs.get(follow_job_id, {})
            follow_thread = _start_follow_thread(wrapper_root, follow_job_id, str(spec0.get("job_spec_hash", ""))[:12])
        except Exception:
            follow_thread = None

    t_manifest0 = time.time()
    completed = 0
    total = len(jobs_in)

    # Cooperative stop/pause semantics:
    # - STOP: stop launching new jobs. Running wrapper processes will observe the
    #   STOP sentinel (passed into _run_one_job_via_wrapper) and terminate.
    # - PAUSE: stop launching new jobs while allowing already-running jobs to
    #   complete. Resume when PAUSE sentinel is removed.
    from concurrent.futures import wait, FIRST_COMPLETED

    pending_jobs: List[str] = [str(j["job_id"]) for j in jobs_in]
    job_devices: Dict[str, str] = {}
    job_packing_used: Dict[str, int] = {}
    for i, j in enumerate(jobs_in):
        job_id = str(j["job_id"])
        dev = slot_devices[i % len(slot_devices)] if slot_devices else ""
        packing_used = int(packing_used_per_device) if dev else 1
        job_devices[job_id] = dev
        job_packing_used[job_id] = packing_used

    stop_requested = False

    with ThreadPoolExecutor(max_workers=int(conc)) as ex:
        fut_to_job: Dict[Any, str] = {}

        while pending_jobs or fut_to_job:
            # STOP / PAUSE checks
            if (not stop_requested) and os.path.exists(stop_sentinel):
                stop_requested = True
                _progress("STOP_REQUESTED: sentinel detected; stopping job launches and terminating active wrappers")

            paused = (not stop_requested) and os.path.exists(pause_sentinel)
            if paused:
                _progress("PAUSED: sentinel detected; waiting to resume")

            # Launch new jobs up to concurrency limit.
            while (not stop_requested) and (not paused) and pending_jobs and (len(fut_to_job) < int(conc)):
                job_id = pending_jobs.pop(0)
                dev = job_devices.get(job_id, "")
                packing_used = int(job_packing_used.get(job_id, 1) or 1)
                fut = ex.submit(_exec_job, job_id, device=dev, packing_used=packing_used, attempt_index=1)
                fut_to_job[fut] = job_id

            # Wait for at least one job completion, or poll sentinels.
            if fut_to_job:
                done, _not_done = wait(set(fut_to_job.keys()), timeout=0.5, return_when=FIRST_COMPLETED)
            else:
                done = set()
                time.sleep(0.2)

            for fut in list(done):
                job_id = fut_to_job.pop(fut, "")
                dev = job_devices.get(job_id, "")
                try:
                    res = fut.result()
                except Exception as e:
                    # Treat unexpected exceptions as a failed job.
                    res = JobResult(
                        job_id=job_id or "",
                        job_spec_hash="",
                        status="failed",
                        exit_code=99,
                        failure_code=f"MANIFEST_RUNNER_EXCEPTION: {e}",
                        wall_time_sec=0.0,
                        run_folder_rel="",
                        wrapper_dir_abs="",
                        run_record_rel="",
                        run_meta_rel="",
                        report_path_rel="",
                        artifact_manifest_rel="",
                    )

                results.append(res)
                completed += 1

                if progress_mode != "off":
                    elapsed = int(time.time() - t_manifest0)
                    _progress(
                        f"PROGRESS {int((completed/total)*100)}% ({completed}/{total}) | elapsed {elapsed//60:02d}:{elapsed%60:02d} | last={job_id} dev={dev or 'cpu'} status={res.status}"
                    )

                if str(res.status) == "failed":
                    try:
                        wrapper_dir = str(res.wrapper_dir_abs or "")
                        fs = _extract_fail_summary(wrapper_dir=wrapper_dir, fallback_code=str(res.failure_code or ""))
                        sys.stderr.write(
                            f"FAIL_SUMMARY | job_id={job_id} dev={dev or 'cpu'} | {fs} | "
                            f"see={os.path.join(wrapper_dir, 'stdout_stderr.log')}\n"
                        )
                        sys.stderr.flush()
                    except Exception:
                        pass

            # If paused and no running jobs, idle until unpaused or stop.
            if paused and (not fut_to_job) and (not stop_requested):
                time.sleep(0.5)

    # Stop follow thread (best-effort).
    try:
        follow_stop_event.set()
    except Exception:
        pass

    for res in results:
        node_jobs_out.append(
            {
                "job_id": str(res.job_id),
                "job_spec_hash": str(res.job_spec_hash),
                "status": str(res.status),
                "exit_code": int(res.exit_code),
                "failure_code": str(res.failure_code or ""),
                "wall_time_sec": float(res.wall_time_sec or 0.0),
                "run_folder_rel": str(res.run_folder_rel or ""),
                "run_record_rel": str(res.run_record_rel or ""),
                "run_meta_rel": str(res.run_meta_rel or ""),
                "report_path_rel": str(res.report_path_rel or ""),
                "artifact_manifest_rel": str(res.artifact_manifest_rel or ""),
            }
        )

    try:
        dump_canonical_to_file(
            {"attempts": [r.__dict__ for r in results], "packing_devices": packing_devices, "sims_per_gpu_initial": int(packing_used_per_device)},
            os.path.join(report_dir, "attempt_history.json"),
        )
    except Exception:
        pass

    node_report = _build_node_report(node_id=node_id, created_at_utc=node_created_at, jobs=node_jobs_out)
    dump_canonical_to_file(node_report, os.path.join(report_dir, "node_report.json"))

    # Exit nonzero only if a job truly failed.
    # "skipped" is an analysis-safe outcome (e.g., not implemented yet).
    any_failed = any(str(j.get("status", "")) == "failed" for j in node_jobs_out)
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
