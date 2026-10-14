"""Shared deterministic job-spec construction for batch experiment runs."""

from __future__ import annotations

from typing import Any, Dict, List

from experiments_shared.experiment_records.hashing import job_spec_hash
from experiments_shared.job_manifests.io import utc_now_iso


def render_cli_args(args_dict: Dict[str, Any]) -> List[str]:
    """Render structured args dict into a deterministic argv token list."""
    tokens: List[str] = []
    for key in sorted(args_dict.keys()):
        value = args_dict[key]
        if value is None:
            continue
        flag = f"--{key}"
        if isinstance(value, bool):
            if value:
                tokens.append(flag)
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                if item is None:
                    continue
                tokens.extend([flag, str(item)])
            continue
        tokens.extend([flag, str(value)])
    return tokens


def build_job_spec(*, job_id: str, git_ref: str, module: str, args_dict: Dict[str, Any], seed: int) -> Dict[str, Any]:
    """Build a deterministic job_spec.json object."""
    cli_args = render_cli_args(args_dict)
    spec: Dict[str, Any] = {
        "schema_version": "v1",
        "job_id": str(job_id),
        "git_ref": str(git_ref),
        "entrypoint": {"kind": "python_module", "module": str(module)},
        "cli_args": [str(x) for x in cli_args],
        "seed": int(seed),
        "created_at_utc": utc_now_iso(),
        "canonicalization": {
            "json": "sort_keys=True,separators=(',',':'),ensure_ascii=False",
            "hash": "sha256(utf8(canonical_json(job_spec)))",
        },
        "job_spec_hash": "",
    }
    spec["job_spec_hash"] = job_spec_hash(spec)
    return spec
