"""Shared wrapper-process execution helpers for manifest-driven runs."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import signal
from typing import Any, Dict, Optional, Tuple, List

def _run_one_job_via_wrapper(
    *,
    repo_root: str,
    wrapper_py: str,
    wrapper_root: str,
    job_spec_path: str,
    device: str,
    timeout_sec: int,
    debug_ctx_json: str = "",
    # Optional control-plane sentinel files (manifest runner)
    # If control_stop_path exists while the wrapper is running, we will attempt
    # to terminate the wrapper process group and return early.
    control_stop_path: str = "",
) -> Tuple[int, str]:
    """Execute the shared run_job wrapper for one job spec.

    Returns
    -------
    (returncode, wrapper_stdout)
        Wrapper return code, and captured wrapper stdout/stderr text.
    """
    cmd = [
        sys.executable,
        "-u",
        "-m",
        "experiments_shared.run_job",
        "--job_spec",
        job_spec_path,
        "--wrapper_root",
        wrapper_root,
    ]
    if device:
        cmd.extend(["--device", device])
    if timeout_sec and timeout_sec > 0:
        cmd.extend(["--timeout_sec", str(timeout_sec)])

    env = dict(os.environ)
    env["PYTHONPATH"] = repo_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    # Optional decision-debug context packet passed down the whole stack.
    # This is intentionally a single JSON string to avoid env var sprawl.
    if debug_ctx_json:
        env["EXPERIMENT_DEBUG_CTX"] = str(debug_ctx_json)

    # We use Popen to support polling for STOP sentinel without requiring the
    # operator to guess PIDs.
    p = subprocess.Popen(
        cmd,
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,  # gives the wrapper a process group
    )

    out_parts: List[str] = []
    stop_path = str(control_stop_path or "").strip()
    t0 = time.time()

    def _read_available() -> None:
        """Drain available stdout (best-effort)."""
        try:
            if p.stdout is None:
                return
            # Non-blocking-ish: read small chunks if available.
            # In text mode, .readline() can block; use .read with small size and
            # a short timeout via poll loop.
            chunk = p.stdout.read(0)  # noop, keeps mypy calm
        except Exception:
            return

    try:
        while True:
            rc = p.poll()
            if rc is not None:
                # Process finished; collect remaining output.
                try:
                    tail = ""
                    if p.stdout is not None:
                        tail = p.stdout.read() or ""
                    if tail:
                        out_parts.append(tail)
                except Exception:
                    pass
                return int(rc), "".join(out_parts)

            # Timeout enforcement.
            if timeout_sec and timeout_sec > 0 and (time.time() - t0) > float(timeout_sec):
                try:
                    os.killpg(int(p.pid), signal.SIGTERM)
                except Exception:
                    try:
                        p.terminate()
                    except Exception:
                        pass
                time.sleep(0.5)
                try:
                    os.killpg(int(p.pid), signal.SIGKILL)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
                try:
                    out, _ = p.communicate(timeout=2)
                    out_parts.append(out or "")
                except Exception:
                    pass
                return 124, "".join(out_parts)

            # STOP sentinel check.
            if stop_path and os.path.exists(stop_path):
                try:
                    os.killpg(int(p.pid), signal.SIGTERM)
                except Exception:
                    try:
                        p.terminate()
                    except Exception:
                        pass
                # Brief grace period then hard kill.
                time.sleep(1.0)
                try:
                    os.killpg(int(p.pid), signal.SIGKILL)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
                try:
                    out, _ = p.communicate(timeout=5)
                    out_parts.append(out or "")
                except Exception:
                    pass
                return 130, "".join(out_parts)

            # Collect incremental output (best-effort, non-blocking).
            try:
                if p.stdout is not None:
                    # read any currently buffered lines without blocking too long
                    line = p.stdout.readline()
                    if line:
                        out_parts.append(line)
            except Exception:
                pass

            time.sleep(0.2)
    finally:
        try:
            if p.stdout is not None:
                p.stdout.close()
        except Exception:
            pass



def _find_wrapper_dir_for_job(wrapper_root: str, job_id: str, job_spec_hash12: str) -> Optional[str]:
    """Find the wrapper run folder created by tools/run_job.py.

    Folder naming may vary by contract version. We match by metadata files:
      - job_spec.json job_id + job_spec_hash
      - run_record.json fallback fields
      - run_meta.json fallback fields

    If multiple candidates exist, we select the lexicographically greatest name
    as a stable newest-ish tiebreak.

    Returns
    -------
    str|None
        Absolute path to wrapper directory if found.
    """
    try:
        roots = [p for p in os.listdir(wrapper_root)]
    except FileNotFoundError:
        return None
    cands: List[str] = []
    for name in sorted(roots):
        d = os.path.join(wrapper_root, name)
        if not os.path.isdir(d):
            continue
        js_path = os.path.join(d, "job_spec.json")
        rr_path = os.path.join(d, "run_record.json")
        rm_path = os.path.join(d, "run_meta.json")
        js = {}
        rr = {}
        rm = {}
        try:
            if os.path.isfile(js_path):
                js = _read_json(js_path)
        except Exception:
            js = {}
        try:
            if os.path.isfile(rr_path):
                rr = _read_json(rr_path)
        except Exception:
            rr = {}
        try:
            if os.path.isfile(rm_path):
                rm = _read_json(rm_path)
        except Exception:
            rm = {}
        j_job_id = str(js.get("job_id") or rr.get("job_id") or rm.get("job_id") or "").strip()
        j_hash = str(js.get("job_spec_hash") or rr.get("job_spec_hash") or rm.get("job_spec_hash") or "").strip()[:12]
        if j_job_id == str(job_id) and j_hash == str(job_spec_hash12):
            cands.append(name)
    if not cands:
        return None
    cands.sort()
    return os.path.join(wrapper_root, cands[-1])



def _read_json(path: str) -> Dict[str, Any]:
    """Read a JSON file as dict."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
