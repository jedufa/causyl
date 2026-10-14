"""Shared single-job wrapper runtime for experiment projects."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import platform
import subprocess
import sys
import time
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_THIS_FILE = os.path.abspath(__file__)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(_THIS_FILE), os.pardir, os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from experiments_shared.experiment_records import (
    RunRecordError,
    build_artifact_manifest,
    dump_canonical_to_file,
    job_spec_hash,
    read_run_record,
    record_heartbeat,
    record_intervention,
    record_launch,
    record_terminal,
    reconcile_run_record,
)
from experiments_shared.git_info import GitResolution, is_immutable_git_ref, resolve_git
from infra.controlplane import job_budget_cost_ledger_store

GENERIC_WRAPPER_ROOT_ENV = "EXPERIMENT_WRAPPER_ROOT"
GENERIC_RUNS_DIR_ENV = "EXPERIMENT_RUNS_DIR"
GENERIC_OUTPUTS_DIR_ENV = "EXPERIMENT_OUTPUTS_DIR"
RUN_RECORD_HEARTBEAT_INTERVAL_SEC = 1.0
JOB_LIFECYCLE_COST_UNIT = "lifecycle_events"
JOB_LIFECYCLE_COST_MINOR_UNITS = 1


def _append_wrapper_note(log_path: str, title: str, detail: str) -> None:
    """Append a best-effort diagnostic block into the wrapper stdout log."""
    try:
        with open(log_path, "ab") as logf:
            logf.write(f"\n\n=== {str(title or '').strip() or 'WRAPPER NOTE'} ===\n".encode("utf-8", errors="replace"))
            logf.write((str(detail or "").strip() + "\n").encode("utf-8", errors="replace"))
    except Exception:
        pass


def _remember_lifecycle_error(errors: List[str], *, stage: str, exc: BaseException, log_path: str = "") -> None:
    """Record one lifecycle-write failure for later fail-closed validation."""
    msg = f"{str(stage or 'run_record')}:{exc.__class__.__name__}:{str(exc or '').strip()}".strip(":")
    errors.append(msg)
    if log_path:
        _append_wrapper_note(log_path, "RUN_RECORD WRITE FAILURE", msg)


def _post_job_lifecycle_cost_checkpoint(
    *,
    repo_root: str,
    run_record: Dict[str, Any],
    checkpoint_id: str,
    cost_reason_code: str,
    direction: str = "debit",
    amount_minor_units: int = JOB_LIFECYCLE_COST_MINOR_UNITS,
) -> Dict[str, Any]:
    job_id = str(run_record.get("job_id") or "").strip()
    run_id = str(run_record.get("run_id") or "").strip()
    if not job_id or not run_id:
        raise ValueError("run_record must include stable job_id and run_id for lifecycle cost posting")
    recorded_at_utc = str((run_record.get("lifecycle") or {}).get("last_transition_utc") or "").strip()
    if not recorded_at_utc:
        recorded_at_utc = str(run_record.get("updated_at_utc") or "").strip()
    if not recorded_at_utc:
        raise ValueError("run_record must include a durable checkpoint timestamp for lifecycle cost posting")
    event = {
        "schema_version": "job_budget_cost_ledger_record_v1",
        "cost_event_uid": f"{run_id}:{checkpoint_id}",
        "job_uid": job_id,
        "recorded_at_utc": recorded_at_utc,
        "amount_minor_units": int(amount_minor_units),
        "unit": JOB_LIFECYCLE_COST_UNIT,
        "direction": str(direction).strip() or "debit",
        "cost_reason_code": str(cost_reason_code).strip() or "usage",
    }
    return job_budget_cost_ledger_store.append_cost_event_record_once(event, repo_root=Path(repo_root))


def _expected_terminal_state_for_status(status: str) -> str:
    raw = str(status or "").strip().lower()
    if raw == "complete":
        return "succeeded"
    if raw == "skipped":
        return "skipped"
    if raw == "failed":
        return "failed"
    return ""


def _validate_final_run_record(
    wrapper_dir: str,
    *,
    run_meta: Dict[str, Any],
) -> tuple[bool, str]:
    """Verify that canonical lifecycle truth exists and matches final wrapper truth."""
    try:
        record = read_run_record(wrapper_dir)
    except Exception as exc:
        return False, f"invalid_or_missing_run_record:{exc}"

    problems: List[str] = []
    if str(record.get("job_id") or "").strip() != str(run_meta.get("job_id") or "").strip():
        problems.append("job_id_mismatch")
    expected_hash = str(run_meta.get("job_spec_hash") or "").strip()
    if expected_hash and str(record.get("job_spec_hash") or "").strip() != expected_hash:
        problems.append("job_spec_hash_mismatch")
    expected_state = _expected_terminal_state_for_status(str(run_meta.get("status") or ""))
    actual_state = str(((record.get("lifecycle") or {}).get("state")) or "").strip()
    if expected_state and actual_state != expected_state:
        problems.append(f"state_mismatch:{actual_state or 'missing'}!= {expected_state}")
    if expected_state and not bool(((record.get("lifecycle") or {}).get("terminal"))):
        problems.append("terminal_flag_missing")
    if expected_state == "succeeded" and str(((record.get("lifecycle") or {}).get("confidence")) or "").strip() != "durable":
        problems.append("success_not_durable")
    if problems:
        return False, ",".join(problems)
    return True, ""


def _read_stop_sentinel(report_dir: str) -> Optional[Dict[str, Any]]:
    """Read a STOP sentinel from <report_dir>/.control/STOP (best-effort).

    Parameters
    ----------
    report_dir:
        Wrapper directory path.

    Returns
    -------
    dict | None
        Parsed JSON sentinel payload if present and readable; otherwise None.

    Notes
    -----
    This is intentionally tolerant: any parsing/read error is treated as
    "stop requested" with default mode=graceful.
    """
    try:
        from pathlib import Path
        import json

        p = Path(report_dir).resolve() / ".control" / "STOP"
        if not p.exists() or not p.is_file() or p.is_symlink():
            return None
        try:
            obj = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            if isinstance(obj, dict):
                return obj
        except Exception:
            # Treat unreadable sentinel as stop requested.
            return {"mode": "graceful"}
    except Exception:
        return {"mode": "graceful"}
    return {"mode": "graceful"}


def _terminate_process_group(pid: int, *, force: bool, logf: Any = None) -> None:
    """Terminate a process group (best-effort).

    Parameters
    ----------
    pid:
        Process id (also treated as pgid when start_new_session=True).
    force:
        If True, escalate to SIGKILL after SIGTERM.
    logf:
        Optional binary file handle for logging.
    """
    if pid <= 0:
        return
    try:
        if logf:
            logf.write(b"\n\n=== STOP REQUESTED: sending SIGTERM ===\n")
    except Exception:
        pass

    # Try killing the process group first.
    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            return

    # Give it a moment.
    time.sleep(0.5)

    if not force:
        return

    try:
        if logf:
            logf.write(b"=== STOP REQUESTED: escalating SIGKILL ===\n")
    except Exception:
        pass

    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            return


def _extract_fail_summary_from_report(report_path: str) -> str:
    """Extract a FAIL_SUMMARY from a report.json (best-effort).

    Parameters
    ----------
    report_path:
        Absolute path to report.json.

    Returns
    -------
    str
        A single-line summary if present, else "".
    """
    import json

    try:
        if not os.path.exists(report_path):
            return ""
        with open(report_path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            # Preferred location for decision debug.
            dbg = obj.get("debug") or {}
            if isinstance(dbg, dict):
                dd = dbg.get("decision") or {}
                if isinstance(dd, dict):
                    fs = str(dd.get("fail_summary", "") or "")
                    if fs:
                        return fs
            # Fallbacks used by older modules.
            fs2 = str(obj.get("fail_summary", "") or "")
            if fs2:
                return fs2
        return ""
    except Exception:
        return ""


def _utc_now_iso() -> str:
    """Return current time as ISO8601 UTC timestamp (Z)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _tail_text_file(path: str, *, max_bytes: int = 16384) -> str:
    """Return a UTF-8 decoded tail of a file (best-effort).

    Parameters
    ----------
    path:
        File path.
    max_bytes:
        Max bytes to read from the end of the file.

    Returns
    -------
    str:
        The decoded tail (may be truncated). Returns "" if unreadable.
    """
    try:
        if not os.path.exists(path):
            return ""
        size = os.path.getsize(path)
        start = max(0, size - int(max_bytes))
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read()
        # If we started mid-line, drop the partial first line for readability.
        if start > 0:
            nl = data.find(b"\n")
            if nl != -1:
                data = data[nl + 1 :]
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _detect_unknown_knob_error(stdout_tail: str) -> tuple[bool, List[str]]:
    """Detect the common "unknown knob" failure pattern.

    This repo intentionally validates knob overrides strictly against the
    SimConfig/Genome schema. When registries evolve faster than the baseline,
    we still want runs to be *forensically recorded* without being treated as
    a scientific failure.

    We therefore detect the canonical error shapes produced by the knob
    override layer.

    Parameters
    ----------
    stdout_tail:
        Tail of wrapper stdout/stderr log.

    Returns
    -------
    (is_unknown_knob, missing_fields)
        is_unknown_knob is True when the output strongly indicates a missing
        config field.
        missing_fields is a best-effort list of field names.
    """
    text = str(stdout_tail or "")
    if not text:
        return (False, [])

    # Typical pattern:
    #   KeyError: 'unknown field: identity_gate_enable'
    # Sometimes nested in tracebacks; sometimes quoted differently.
    missing: List[str] = []

    # Primary extractor: "unknown field: <name>".
    try:
        import re

        for m in re.finditer(r"unknown field:\s*([A-Za-z0-9_\.]+)", text):
            missing.append(str(m.group(1)))
    except Exception:
        missing = missing or []

    # Secondary heuristics: "unknown knob" or "field does not exist".
    text_l = text.lower()
    looks_like_unknown = (
        "unknown field:" in text_l
        or "unknown knob" in text_l
        or "field does not exist" in text_l
        or "does not exist on" in text_l
    )

    if looks_like_unknown:
        # Best effort de-dup while preserving order.
        seen = set()
        out: List[str] = []
        for x in missing:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return (True, out)

    return (False, [])



def _utc_now_compact() -> str:
    """Return a compact UTC timestamp suitable for folder names."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _utc_iso_to_compact(iso_utc: str) -> str:
    """Convert UTC ISO-8601 timestamp to compact folder-safe form."""
    txt = str(iso_utc or "").strip()
    if not txt:
        return ""
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00")).astimezone(timezone.utc)
        return dt.strftime("%Y%m%d_%H%M%S")
    except Exception:
        return ""


def _repo_root_from_this_file() -> str:
    """Best-effort repo root discovery.

    Why this exists:
      - Historically we had tools/run_job.py (1 level under repo root).
      - We later moved the implementation into tools/_core/run_job.py.
      - A naive "parent dir" assumption would incorrectly treat <repo>/tools
        as repo root, which then changes subprocess cwd, relative path
        resolution, and PYTHONPATH precedence.

    Strategy:
      Walk upwards from this file and pick the first directory that *looks*
      like the repo root.
    """
    start = os.path.abspath(os.path.dirname(__file__))

    def _looks_like_repo_root(p: str) -> bool:
        # Prefer strong markers.
        if os.path.isfile(os.path.join(p, "pyproject.toml")):
            return True
        if os.path.isdir(os.path.join(p, ".git")):
            return True
        # Fallback heuristics for this repo.
        return (
            os.path.isdir(os.path.join(p, "tools"))
            and os.path.isdir(os.path.join(p, "experiments"))
            and os.path.isdir(os.path.join(p, "experiments", "shared"))
        )

    cur = start
    for _ in range(8):
        if _looks_like_repo_root(cur):
            return cur
        parent = os.path.abspath(os.path.join(cur, os.pardir))
        if parent == cur:
            break
        cur = parent

    # Last resort: assume two levels up from tools/_core/run_job.py.
    # This matches the common layout: <repo>/tools/_core/run_job.py.
    return os.path.abspath(os.path.join(start, os.pardir, os.pardir))


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI args for the job wrapper."""
    p = argparse.ArgumentParser(description="Phase 1 job wrapper (forensic run folder)")

    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--job_spec", help="Path to a job_spec.json (v1)")
    src.add_argument("--module", help="Python module to run via python -m")

    # If using --module mode, we must have enough fields to build a job spec.
    p.add_argument("--git_ref", help="Immutable git ref: 40-hex commit or annotated tag")
    p.add_argument("--job_id", help="Stable job id")
    p.add_argument("--seed", type=int, help="Seed (required by schema; may be nominal for batch jobs)")

    p.add_argument(
        "--wrapper_root",
        default=None,
        help=(
            "Directory under which wrapper folders are created "
            "(default: $EXPERIMENT_WRAPPER_ROOT, "
            "then <repo_root>/runtime/live/wrapper_jobs)"
        ),
    )

    p.add_argument(
        "--timeout_sec",
        type=int,
        default=0,
        help="Optional timeout in seconds (0 = no timeout)",
    )

    p.add_argument(
        "--device",
        default="",
        help="Optional device hint recorded in run_meta.json (e.g., cpu, cuda:0)",
    )

    p.add_argument(
        "--dry_run",
        action="store_true",
        help="Only build the wrapper folder + job_spec.json/run_meta.json; do not execute the module.",
    )

    # Everything after -- is forwarded as cli_args tokens.
    p.add_argument("cli_args", nargs=argparse.REMAINDER, help="Args forwarded to the module (prefix with --)")

    return p.parse_args(argv)


def _load_job_spec(path: str) -> Dict[str, Any]:
    """Load a job_spec.json from disk.

    Parameters
    ----------
    path:
        Path to JSON file.

    Returns
    -------
    dict
        Parsed JSON object.

    Raises
    ------
    RuntimeError
        If file cannot be read or parsed.
    """
    import json

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise RuntimeError(f"SPEC_INVALID_JOB_SPEC: could not read {path}: {e}")


def _build_job_spec_from_flags(args: argparse.Namespace) -> Dict[str, Any]:
    """Build a job_spec.json object from CLI flags."""
    if not args.git_ref or not args.job_id or args.seed is None:
        raise RuntimeError("SPEC_INVALID_JOB_SPEC: --git_ref, --job_id, and --seed are required with --module")

    # Forwarded args include the leading '--' that argparse keeps in REMAINDER.
    cli_args = list(args.cli_args)
    if cli_args and cli_args[0] == "--":
        cli_args = cli_args[1:]

    job_spec: Dict[str, Any] = {
        "schema_version": "v1",
        "job_id": str(args.job_id),
        "git_ref": str(args.git_ref),
        "entrypoint": {"kind": "python_module", "module": str(args.module)},
        "cli_args": [str(x) for x in cli_args],
        "seed": int(args.seed),
        "created_at_utc": _utc_now_iso(),
        "canonicalization": {
            "json": "sort_keys=True,separators=(',',':'),ensure_ascii=False",
            "hash": "sha256(utf8(canonical_json(job_spec)))",
        },
        "job_spec_hash": "",  # filled below
    }

    job_spec["job_spec_hash"] = job_spec_hash(job_spec)
    return job_spec


def _validate_entrypoint_module(module: str) -> None:
    """Fail fast if the module cannot be imported."""
    spec = importlib.util.find_spec(module)
    if spec is None:
        raise RuntimeError(f"RUN_ENTRYPOINT_NOT_FOUND: python module not found: {module}")


def _classify_failure(
    *,
    returncode: Optional[int],
    timed_out: bool,
    stdout_tail: str = "",
) -> tuple[str, str]:
    """Classify a failure into Phase 0 failure codes.

    Parameters
    ----------
    returncode:
        Subprocess return code.
    timed_out:
        True if a timeout was hit.

    Returns
    -------
    (failure_code, failure_detail)
    """
    if timed_out:
        return "INFRA_TIMEOUT", "subprocess exceeded timeout"

    if returncode is None:
        return "RUN_EXIT_NONZERO", "subprocess exit code unavailable"

    # Negative return codes mean terminated by signal on POSIX.
    if returncode < 0:
        return "INFRA_PROCESS_KILLED", f"subprocess terminated by signal {-returncode}"

    if returncode != 0:
        if returncode == 2:
            # In Python, rc==2 is *often* argparse/usage. In this repo, rc==2 is
            # also used by some harness/tests to mean "test failed".
            # We disambiguate using a tiny heuristic on captured output.
            tail = (stdout_tail or "")
            tail_l = tail.lower()

            looks_like_usage = ("usage:" in tail_l) or ("error: the following arguments" in tail_l)
            if looks_like_usage:
                return "RUN_CLI_USAGE", "subprocess exited with code 2 (argparse/usage)"

            # If the harness ran to completion, treat rc=2 as a *test failure*,
            # not a CLI usage error.
            if ("HARNESS_START" in tail) and ("HARNESS_END" in tail):
                return "TEST_FAILED", "harness completed but reported failure (exit code 2)"

            return "RUN_EXIT_NONZERO", "subprocess exited with code 2"
        return "RUN_EXIT_NONZERO", f"subprocess exited with code {returncode}"

    return "", ""


def _write_run_meta(
    path: str,
    run_meta: Dict[str, Any],
) -> None:
    """Write run_meta.json canonically."""
    dump_canonical_to_file(run_meta, path)


def _make_wrapper_folder_name(
    job_id: str,
    job_spec_hash_hex: str,
    gitsha7: str,
    *,
    created_at_utc: str = "",
) -> str:
    """Build a compact wrapper folder name (deterministic for a given job spec)."""
    short_hash = str(job_spec_hash_hex or "")[:10]
    short_git = str(gitsha7 or "")[:7]
    jid_short = hashlib.sha256(str(job_id or "").encode("utf-8")).hexdigest()[:8]
    ts = _utc_iso_to_compact(created_at_utc) or _utc_now_compact()
    return f"wj_{ts}_{jid_short}_{short_hash}_{short_git}"


def _enforce_wrapper_path_budget(*, wrapper_root: str, wrapper_name: str, max_rel_chars: int = 180) -> None:
    """Fail fast when wrapper path segments exceed conservative budgets."""
    if len(str(wrapper_name or "")) > 96:
        raise RuntimeError(f"RUN_PATH_TOO_LONG: wrapper folder name exceeds 96 chars ({len(wrapper_name)})")
    rel = str(wrapper_name or "")
    if len(rel) > int(max_rel_chars):
        raise RuntimeError(
            f"RUN_PATH_TOO_LONG: wrapper relative path exceeds {int(max_rel_chars)} chars ({len(rel)})"
        )
    full = os.path.join(str(wrapper_root or ""), rel)
    if len(full) > 380:
        raise RuntimeError(f"RUN_PATH_TOO_LONG: wrapper absolute path exceeds 380 chars ({len(full)})")


def _ensure_empty_dir(path: str) -> None:
    """Create a directory, failing if it already exists."""
    if os.path.exists(path):
        raise RuntimeError(f"RUN_EVIDENCE_WRITE_FAIL: wrapper folder already exists: {path}")
    os.makedirs(path, exist_ok=False)


def run_one_job(argv: Optional[List[str]] = None) -> int:
    """Main entrypoint used by __main__.

    Returns
    -------
    int
        Process exit code (0 on success).
    """
    args = _parse_args(argv)
    repo_root = _repo_root_from_this_file()

    # Load or build the job spec.
    if args.job_spec:
        job_spec = _load_job_spec(args.job_spec)
    else:
        job_spec = _build_job_spec_from_flags(args)

    # Validate minimal schema assumptions we rely on.
    try:
        job_id = str(job_spec["job_id"])
        git_ref = str(job_spec["git_ref"])
        entry_module = str(job_spec["entrypoint"]["module"])
        cli_args = list(job_spec.get("cli_args", []))
        seed = int(job_spec["seed"])
        job_spec_hash_hex = str(job_spec["job_spec_hash"])
    except Exception as e:
        raise RuntimeError(f"SPEC_INVALID_JOB_SPEC: missing required fields: {e}")

    # Validate git ref immutability.
    ok, reason = is_immutable_git_ref(git_ref, repo_root)
    if not ok:
        raise RuntimeError(reason)

    git: GitResolution = resolve_git(git_ref, repo_root)

    # Validate entrypoint module exists.
    _validate_entrypoint_module(entry_module)

    wrapper_root = args.wrapper_root
    if not wrapper_root:
        wrapper_root = (
            os.environ.get(GENERIC_WRAPPER_ROOT_ENV)
            or os.path.join(repo_root, "runtime", "live", "wrapper_jobs")
        )
    wrapper_root = os.path.abspath(os.path.expanduser(wrapper_root))
    os.makedirs(wrapper_root, exist_ok=True)

    wrapper_name = _make_wrapper_folder_name(
        job_id,
        job_spec_hash_hex,
        git.resolved_commit[:7],
        created_at_utc=str(job_spec.get("created_at_utc") or ""),
    )
    _enforce_wrapper_path_budget(wrapper_root=wrapper_root, wrapper_name=wrapper_name)
    wrapper_dir = os.path.join(wrapper_root, wrapper_name)

    # Create wrapper folder (atomic, no overwrite).
    _ensure_empty_dir(wrapper_dir)

    payload_dir = os.path.join(wrapper_dir, "payload")
    os.makedirs(payload_dir, exist_ok=False)

    # Write job_spec.json (as provided).
    dump_canonical_to_file(job_spec, os.path.join(wrapper_dir, "job_spec.json"))

    # Write run_meta.json at start (schema requires end_time_utc + exit_code + failure_code fields).
    run_meta: Dict[str, Any] = {
        "schema_version": "v1",
        "job_id": job_id,
        "job_spec_hash": job_spec_hash_hex,
        "attempt_id": 1,
        "status": "started",
        "start_time_utc": _utc_now_iso(),
        "end_time_utc": "",
        "exit_code": 0,
        "failure_code": "",
        "failure_detail": "",
        "run_folder_rel": os.path.relpath(wrapper_dir, wrapper_root).replace(os.sep, "/"),
        "payload_root_rel": "payload",
        "logs": {"stdout_stderr_log": "stdout_stderr.log"},
        "resources": {"device": str(args.device) if args.device else ""},
        "git": {
            "resolved_commit": git.resolved_commit,
            "describe": git.describe,
            "dirty": bool(git.dirty),
        },
        "host": {
            "hostname": platform.node(),
            "os": platform.platform(),
            "python": sys.version.split()[0],
        },
    }
    # Prepare subprocess command.
    cmd = [sys.executable, "-u", "-m", entry_module, *[str(x) for x in cli_args]]

    # Ensure we ALWAYS create the evidence log file early.
    # This prevents "mysterious" failures where the wrapper dies before stdout/stderr capture begins.
    log_path = os.path.join(wrapper_dir, "stdout_stderr.log")
    try:
        with open(log_path, "ab") as _touch:
            _touch.write(b"=== WRAPPER LAUNCH ===\n")
            _touch.write(f"utc={_utc_now_iso()}\n".encode("utf-8", errors="replace"))
            _touch.write(f"cmd={cmd}\n".encode("utf-8", errors="replace"))
            _touch.write(f"seed={seed}\n".encode("utf-8", errors="replace"))
    except Exception:
        # If we can't even write the log, we still continue; run_meta will report the failure below.
        pass

    _write_run_meta(os.path.join(wrapper_dir, "run_meta.json"), run_meta)

    lifecycle_write_errors: List[str] = []
    launch_record_ok = True
    try:
        launch_record = record_launch(
            wrapper_dir,
            job_id=str(job_id),
            job_spec_hash=str(job_spec_hash_hex),
            created_at_utc=str(run_meta.get("start_time_utc") or ""),
            launch={
                "project_id": str(job_spec.get("project_id") or ""),
                "git_ref": str(git_ref),
                "resolved_commit": str(git.resolved_commit),
                "describe": str(git.describe),
                "dirty": bool(git.dirty),
                "entry_module": str(entry_module),
                "cli_args": [str(x) for x in cli_args],
                "seed": int(seed),
                "contract_hash": str(job_spec.get("contract_hash") or ""),
                "wrapper_root": str(wrapper_root),
                "run_dir_rel": str(run_meta.get("run_folder_rel") or ""),
                "payload_dir_rel": str(run_meta.get("payload_root_rel") or "payload"),
                "host": dict(run_meta.get("host") or {}),
                "resources": dict(run_meta.get("resources") or {}),
            },
        )
        try:
            _post_job_lifecycle_cost_checkpoint(
                repo_root=repo_root,
                run_record=launch_record,
                checkpoint_id="launch",
                cost_reason_code="reservation",
            )
        except Exception as exc:
            _append_wrapper_note(log_path, "JOB COST LEDGER WRITE FAILURE", f"launch:{exc}")
    except Exception as exc:
        launch_record_ok = False
        _remember_lifecycle_error(lifecycle_write_errors, stage="launch", exc=exc, log_path=log_path)

    timed_out = False
    rc: int = 0
    t0 = time.time()
    exc_for_error_json: Optional[BaseException] = None

    try:
        if not launch_record_ok:
            raise RunRecordError("canonical run_record launch write failed")
        # Ensure entrypoints write under payload/.
        # NOTE: We must NOT change the engine, so we rely on existing repo conventions.
        # New projects should target the neutral EXPERIMENT_* variables.
        # The wrapper exports project-neutral output paths.
        env = dict(os.environ)
        env[GENERIC_RUNS_DIR_ENV] = payload_dir
        env[GENERIC_OUTPUTS_DIR_ENV] = payload_dir

        # Provide a dedicated log directory for engine/tests JSONL logs (lives under payload/).
        env["EXPERIMENT_LOG_DIR"] = os.path.join(payload_dir, "logs")
        os.makedirs(env["EXPERIMENT_LOG_DIR"], exist_ok=True)

        # Critical: keep module resolution working.
        # If we run the subprocess with cwd=wrapper_dir, "python -m experiments.runs.*" fails
        # because repo packages are not on sys.path.
        # We therefore run from repo_root and explicitly add repo_root to PYTHONPATH.
        cwd = repo_root
        env["PYTHONPATH"] = repo_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

        if args.dry_run:
            with open(log_path, "ab") as logf:
                logf.write(b"DRY RUN: subprocess not executed\n")
            rc = 0
        else:
            # Use Popen instead of subprocess.run so we can implement stop-sentinel
            # support for *any* managed job (not only manifest runner).
            with open(log_path, "ab") as logf:
                try:
                    p = subprocess.Popen(
                        cmd,
                        cwd=cwd,
                        env=env,
                        stdout=logf,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )

                    try:
                        heartbeat_record = record_heartbeat(
                            wrapper_dir,
                            heartbeat_at_utc=_utc_now_iso(),
                            note="wrapper_subprocess_started",
                        )
                        if int(((heartbeat_record.get("lifecycle") or {}).get("heartbeat_count")) or 0) == 1:
                            try:
                                _post_job_lifecycle_cost_checkpoint(
                                    repo_root=repo_root,
                                    run_record=heartbeat_record,
                                    checkpoint_id="running_started",
                                    cost_reason_code="usage",
                                )
                            except Exception as exc:
                                _append_wrapper_note(log_path, "JOB COST LEDGER WRITE FAILURE", f"running_started:{exc}")
                    except Exception as exc:
                        _remember_lifecycle_error(lifecycle_write_errors, stage="heartbeat_start", exc=exc, log_path=log_path)

                    timeout_sec = int(args.timeout_sec or 0)
                    t_deadline = (time.time() + float(timeout_sec)) if timeout_sec > 0 else 0.0
                    next_heartbeat_at = time.time() + float(RUN_RECORD_HEARTBEAT_INTERVAL_SEC)

                    stop_seen = False
                    stop_force = False

                    # Poll loop: check process exit, timeout, and STOP sentinel.
                    while True:
                        prc = p.poll()
                        if prc is not None:
                            rc = int(prc)
                            break

                        # Stop sentinel.
                        sent = _read_stop_sentinel(wrapper_dir)
                        if sent is not None:
                            if not stop_seen:
                                try:
                                    record_intervention(
                                        wrapper_dir,
                                        action=("force_stop_requested" if str((sent or {}).get("mode") or "graceful").strip().lower() == "force" else "graceful_stop_requested"),
                                        actor="operator",
                                        reason="wrapper stop sentinel observed",
                                        details={"mode": str((sent or {}).get("mode") or "graceful")},
                                        repair=False,
                                        recorded_at_utc=_utc_now_iso(),
                                    )
                                except Exception as exc:
                                    _remember_lifecycle_error(lifecycle_write_errors, stage="intervention_stop", exc=exc, log_path=log_path)
                            stop_seen = True
                            stop_force = str((sent or {}).get("mode") or "graceful").strip().lower() == "force"
                            _terminate_process_group(int(p.pid or 0), force=bool(stop_force), logf=logf)
                            # Wait a little for termination to be observed.
                            time.sleep(0.25)
                            continue

                        # Timeout.
                        if t_deadline and time.time() >= t_deadline:
                            timed_out = True
                            rc = 124
                            try:
                                logf.write(b"\n\n=== TIMEOUT ===\n")
                                logf.write(f"timeout_sec={timeout_sec}\n".encode("utf-8", errors="replace"))
                            except Exception:
                                pass
                            # Escalate to kill.
                            _terminate_process_group(int(p.pid or 0), force=True, logf=logf)
                            break

                        if time.time() >= next_heartbeat_at:
                            try:
                                record_heartbeat(
                                    wrapper_dir,
                                    heartbeat_at_utc=_utc_now_iso(),
                                    note="wrapper_poll_heartbeat",
                                )
                            except Exception as exc:
                                _remember_lifecycle_error(lifecycle_write_errors, stage="heartbeat_poll", exc=exc, log_path=log_path)
                            next_heartbeat_at = time.time() + float(RUN_RECORD_HEARTBEAT_INTERVAL_SEC)

                        # Small sleep to avoid busy looping.
                        time.sleep(0.25)

                    if stop_seen and rc == 0:
                        # A graceful stop may lead to a clean exit code; still annotate.
                        try:
                            logf.write(b"\n\n=== STOP COMPLETED ===\n")
                            logf.write(f"mode={'force' if stop_force else 'graceful'}\n".encode("utf-8", errors="replace"))
                        except Exception:
                            pass

                except Exception as e:
                    timed_out = False
                    rc = 125
                    exc_for_error_json = e
                    try:
                        logf.write(b"\n\n=== WRAPPER ERROR ===\n")
                        logf.write(str(e).encode("utf-8", errors="replace"))
                        logf.write(b"\n")
                    except Exception:
                        pass
    except Exception as e:
        # Any exception after wrapper_dir creation should still produce a finalized run_meta + evidence.
        timed_out = False
        rc = 125
        exc_for_error_json = e
        try:
            with open(log_path, "ab") as logf:
                logf.write(b"\n\n=== WRAPPER FATAL (pre-subprocess) ===\n")
                logf.write(str(e).encode("utf-8", errors="replace"))
                logf.write(b"\n")
        except Exception:
            pass

    t1 = time.time()

    # Update run_meta.json at end.
    run_meta["end_time_utc"] = _utc_now_iso()
    run_meta["exit_code"] = int(rc)

    # Tail once, reuse everywhere (classification + error.json context).
    stdout_tail = _tail_text_file(log_path, max_bytes=16384)

    # ------------------------------------------------------------
    # Unknown-knob policy
    # ------------------------------------------------------------
    # Default behavior remains STRICT: unknown fields are failures.
    # For fast-moving registries (Chaos Week campaigns), operators may
    # temporarily allow "skip" so these jobs show up as NOT_IMPLEMENTED
    # instead of poisoning analysis with infra-failure noise.
    #
    # Enable with:
    #   EXPERIMENT_UNKNOWN_KNOB_POLICY=skip
    #
    # Any other value (or unset) keeps strict behavior.
    unknown_knob_policy = str(os.environ.get("EXPERIMENT_UNKNOWN_KNOB_POLICY", "")).strip().lower()
    allow_skip_unknown_knobs = unknown_knob_policy == "skip"
    is_unknown_knob, missing_knobs = _detect_unknown_knob_error(stdout_tail)

    lifecycle_contract_broken = (not launch_record_ok) or bool(lifecycle_write_errors)

    if rc == 0 and not timed_out and not lifecycle_contract_broken:
        run_meta["status"] = "complete"
        run_meta["failure_code"] = ""
        run_meta["failure_detail"] = ""
    else:
        # Graceful mode: treat unknown config fields as "not implemented yet".
        if lifecycle_contract_broken:
            run_meta["status"] = "failed"
            run_meta["failure_code"] = "RUN_EVIDENCE_WRITE_FAIL"
            detail_parts: List[str] = []
            if not launch_record_ok:
                detail_parts.append("invalid_or_missing_run_record:launch_write_failed")
            if lifecycle_write_errors:
                detail_parts.append("lifecycle_write_errors=" + "; ".join(lifecycle_write_errors))
            if exc_for_error_json is not None and not isinstance(exc_for_error_json, RunRecordError):
                detail_parts.append(str(exc_for_error_json))
            run_meta["failure_detail"] = " | ".join(part for part in detail_parts if part)
        elif allow_skip_unknown_knobs and is_unknown_knob:
            run_meta["status"] = "skipped"
            run_meta["failure_code"] = "NOT_IMPLEMENTED_KNOB"
            if missing_knobs:
                run_meta["failure_detail"] = "unknown knob(s): " + ", ".join(missing_knobs)
                run_meta["missing_knobs"] = list(missing_knobs)
            else:
                run_meta["failure_detail"] = "unknown knob override (field missing in baseline)"
                run_meta["missing_knobs"] = []
        else:
            run_meta["status"] = "failed"
            failure_code, failure_detail = _classify_failure(returncode=rc, timed_out=timed_out, stdout_tail=stdout_tail)
            run_meta["failure_code"] = failure_code
            run_meta["failure_detail"] = failure_detail

    # Add a tiny timing hint (not in schema, but schema allows extra properties).
    run_meta["timing"] = {"wall_time_sec": round(t1 - t0, 3)}

    _write_run_meta(os.path.join(wrapper_dir, "run_meta.json"), run_meta)

    # Decision-debug: finalize packet + persist. Also append a compact block to stdout_stderr.log.
    fail_summary = ""
    try:
        report_path = os.path.join(wrapper_dir, "payload", "report.json")
        fail_summary = _extract_fail_summary_from_report(report_path)
    except Exception:
        fail_summary = ""

    try:
        terminal_record = record_terminal(
            wrapper_dir,
            status=str(run_meta.get("status") or "failed"),
            exit_code=int(run_meta.get("exit_code", rc) or rc),
            failure_code=str(run_meta.get("failure_code") or ""),
            failure_detail=str(run_meta.get("failure_detail") or ""),
            ended_at_utc=str(run_meta.get("end_time_utc") or ""),
            transition_reason="wrapper_terminal_update",
        )
        try:
            _post_job_lifecycle_cost_checkpoint(
                repo_root=repo_root,
                run_record=terminal_record,
                checkpoint_id=f"terminal:{str((terminal_record.get('lifecycle') or {}).get('state') or 'failed').strip() or 'failed'}",
                cost_reason_code="usage",
            )
        except Exception as exc:
            _append_wrapper_note(log_path, "JOB COST LEDGER WRITE FAILURE", f"terminal:{exc}")
    except Exception as exc:
        _remember_lifecycle_error(lifecycle_write_errors, stage="terminal", exc=exc, log_path=log_path)

    try:
        reconcile_run_record(
            wrapper_dir,
            run_meta=dict(run_meta),
            observed_at_utc=str(run_meta.get("end_time_utc") or ""),
            record_repair_event=True,
        )
    except Exception as exc:
        _remember_lifecycle_error(lifecycle_write_errors, stage="reconcile", exc=exc, log_path=log_path)

    run_record_ok, run_record_detail = _validate_final_run_record(wrapper_dir, run_meta=dict(run_meta))
    if not run_record_ok:
        prior_code = str(run_meta.get("failure_code") or "").strip()
        prior_detail = str(run_meta.get("failure_detail") or "").strip()
        pieces = [piece for piece in [prior_detail, run_record_detail] if piece]
        if lifecycle_write_errors:
            pieces.append("lifecycle_write_errors=" + "; ".join(lifecycle_write_errors))
        run_meta["status"] = "failed"
        run_meta["failure_code"] = "RUN_EVIDENCE_WRITE_FAIL"
        run_meta["failure_detail"] = " | ".join(pieces) if pieces else (prior_code or "canonical run_record validation failed")
        run_meta["end_time_utc"] = str(run_meta.get("end_time_utc") or _utc_now_iso())
        run_meta["exit_code"] = int(run_meta.get("exit_code", rc) or rc or 1)
        _write_run_meta(os.path.join(wrapper_dir, "run_meta.json"), run_meta)
        _append_wrapper_note(log_path, "RUN_RECORD VALIDATION FAILURE", str(run_meta.get("failure_detail") or ""))


    # Build artifact_manifest.json.
    manifest = build_artifact_manifest(wrapper_dir=wrapper_dir, job_id=job_id, job_spec_hash=job_spec_hash_hex)
    dump_canonical_to_file(manifest, os.path.join(wrapper_dir, "artifact_manifest.json"))

    # Return code policy:
    # - complete => 0
    # - skipped  => 0 (analysis-safe; still produces forensic evidence)
    # - failed   => 1
    st = str(run_meta.get("status", ""))
    if st in ("complete", "skipped"):
        return 0
    return 1


def main() -> int:
    """CLI entrypoint."""
    try:
        return run_one_job()
    except Exception as e:
        # If wrapper_dir couldn't be created, we have nowhere to write run_meta.
        # This is still a contract-class failure.
        sys.stderr.write(f"FATAL: {e}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
