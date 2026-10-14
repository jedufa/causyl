"""infra.webui.launchers

Helper utilities for launching jobs from the WebUI.

Design goal
-----------
Keep the WebUI "builder launch" endpoint dumb and safe:
it should translate a *limited* allowlisted command string into a
"managed" wrapper invocation (tools.run_job) that creates a durable
report_dir, supports stop sentinels, and records metadata.

This module intentionally does NOT execute any process. It only:
- parses/validates supported command patterns
- builds the argv list for a run_job wrapper invocation
- derives deterministic metadata (job_id, wrapper_dir, etc.)

Security posture
----------------
- No shell execution.
- Only supports: python -m <module> ...
- Module allowlist is enforced by the caller (infra.webui.server).
"""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _repo_root_from_webui() -> Path:
    return Path(__file__).resolve().parents[2]


def _webui_runtime_root() -> Path:
    return (Path(_repo_root_from_webui()) / "runtime" / "state" / "webui").resolve()


@dataclass(frozen=True)
class ManagedLaunchPlan:
    """Planned managed launch output."""

    requested_cmd_str: str
    requested_tokens: List[str]
    module: str
    forwarded_args: List[str]

    job_id: str
    git_ref: str
    seed: int

    job_spec: Dict[str, Any]
    job_spec_hash: str
    job_spec_path: Path

    wrapper_root: Path
    wrapper_dir: Path
    effective_cmd: List[str]


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def parse_python_module_cmd(cmd_str: str) -> Tuple[str, List[str], List[str]]:
    """Parse a restricted "python -m <module> ..." command.

    Parameters
    ----------
    cmd_str:
        Shell-like command string.

    Returns
    -------
    (module, tokens, forwarded_args)

    Raises
    ------
    ValueError
        If the command is not in the supported shape.
    """
    s = str(cmd_str or "").strip()
    if not s:
        raise ValueError("missing cmd")
    toks = shlex.split(s)
    if len(toks) < 3:
        raise ValueError("invalid cmd (too short)")
    if toks[1] != "-m":
        raise ValueError("only 'python -m <module> ...' commands are supported")
    module = str(toks[2] or "").strip()
    if not module:
        raise ValueError("missing module name")
    forwarded = [str(x) for x in toks[3:]]
    return module, toks, forwarded


def build_managed_launch_plan(
    *,
    cmd_str: str,
    repo_root: Path,
    runs_root: Path,
    job_prefix: str = "webui",
    seed: Optional[int] = None,
    created_at_utc: Optional[str] = None,
) -> ManagedLaunchPlan:
    """Translate a builder cmd into a managed tools.run_job invocation.

    Parameters
    ----------
    cmd_str:
        The user-facing command string (must be python -m <module> ...).
    repo_root:
        Repository root.
    runs_root:
        Runs root (used for durable job registry + job_spec location).
    job_prefix:
        Prefix for job ids.
    seed:
        Optional seed to record in wrapper metadata.
    created_at_utc:
        Optional UTC timestamp to stamp into job_spec (and therefore wrapper_dir naming).

    Returns
    -------
    ManagedLaunchPlan
        Plan including effective_cmd and wrapper_dir.
    """

    module, toks, forwarded = parse_python_module_cmd(cmd_str)

    from infra.controlplane import job_registry as _jobreg
    from infra.webui.server import _builder_git_head_sha as _builder_git_head_hash, _builder_git_resolve_ref, _now_utc_iso

    git_ref = str(_builder_git_head_hash(Path(repo_root)) or "").strip().lower()
    if not git_ref:
        git_ref = str(_builder_git_resolve_ref(Path(repo_root), "HEAD") or "").strip().lower()
    if not git_ref:
        raise ValueError(
            "could not resolve git commit (no .git and no snapshot manifest commit in "
            "archives/manifests/SNAPSHOT_MANIFEST.txt)"
        )

    job_id = _jobreg.new_job_id(str(job_prefix or "webui").strip() or "webui")
    seed_i = _safe_int(seed, 0)

    # Wrapper jobs are full run artifacts; keep them under runtime/live.
    wrapper_root = (Path(repo_root).resolve() / "runtime" / "live" / "wrapper_jobs").resolve()

    from tools.core.run_job import _make_wrapper_folder_name  # type: ignore
    from tools.joblib.hashing import job_spec_hash

    ca = str(created_at_utc or "").strip() or _now_utc_iso()

    job_spec: Dict[str, Any] = {
        "schema_version": "v1",
        "job_id": str(job_id),
        "git_ref": str(git_ref),
        "entrypoint": {"kind": "python_module", "module": str(module)},
        "cli_args": [str(x) for x in forwarded],
        "seed": int(seed_i),
        "created_at_utc": str(ca),
        "canonicalization": {
            "json": "sort_keys=True,separators=(',',':'),ensure_ascii=False",
            "hash": "sha256(utf8(canonical_json(job_spec)))",
        },
        "job_spec_hash": "",
    }
    job_spec["job_spec_hash"] = job_spec_hash(job_spec)
    job_spec_hash_hex = str(job_spec["job_spec_hash"])

    wrapper_name = _make_wrapper_folder_name(
        str(job_id),
        job_spec_hash_hex,
        str(git_ref)[:7],
        created_at_utc=str(ca),
    )
    wrapper_dir = (Path(wrapper_root) / wrapper_name).resolve()

    job_spec_dir = (_webui_runtime_root() / "builder_job_specs").resolve()
    job_spec_path = job_spec_dir / f"job_spec__{job_id}.json"

    effective_cmd = [
        sys.executable,
        "-m",
        "tools.run_job",
        "--job_spec",
        str(job_spec_path),
        "--wrapper_root",
        str(wrapper_root),
    ]

    return ManagedLaunchPlan(
        requested_cmd_str=str(cmd_str),
        requested_tokens=[str(x) for x in toks],
        module=str(module),
        forwarded_args=[str(x) for x in forwarded],
        job_id=str(job_id),
        git_ref=str(git_ref),
        seed=int(seed_i),
        job_spec=dict(job_spec),
        job_spec_hash=str(job_spec_hash_hex),
        job_spec_path=Path(job_spec_path),
        wrapper_root=Path(wrapper_root),
        wrapper_dir=Path(wrapper_dir),
        effective_cmd=effective_cmd,
    )
