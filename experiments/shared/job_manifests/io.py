"""Project-neutral helpers for reading job manifests and resolving outputs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

GENERIC_OUTPUT_ROOT_ENV = "EXPERIMENT_WRAPPER_ROOT"
LEGACY_OUTPUT_ROOT_ENVS: tuple[str, ...] = ()


def utc_now_iso() -> str:
    """Return current time as ISO8601 UTC timestamp (Z)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_manifest(path: str) -> Dict[str, Any]:
    """Load a manifest from JSON or YAML."""
    if not os.path.exists(path):
        raise RuntimeError(f"MANIFEST_NOT_FOUND: {path}")

    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        raise RuntimeError(f"MANIFEST_READ_FAIL: {path}: {e}")

    if ext == ".json":
        try:
            return json.loads(raw)
        except Exception as e:
            raise RuntimeError(f"MANIFEST_PARSE_FAIL: JSON: {path}: {e}")

    if ext in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "MANIFEST_PARSE_FAIL: YAML requested but PyYAML is not installed. "
                "Install pyyaml or use JSON. "
                f"Details: {e}"
            )
        try:
            obj = yaml.safe_load(raw)
            if obj is None:
                obj = {}
            if not isinstance(obj, dict):
                raise RuntimeError("YAML root must be a mapping/object")
            return obj
        except Exception as e:
            raise RuntimeError(f"MANIFEST_PARSE_FAIL: YAML: {path}: {e}")

    try:
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"MANIFEST_PARSE_FAIL: unknown extension {ext}: {path}: {e}")


def repo_root_from_manifest_file(this_file: str, *, levels_up: int = 2) -> str:
    """Best-effort repo-root discovery from a manifest helper path."""
    here = os.path.abspath(os.path.dirname(this_file))
    root = here
    for _ in range(int(levels_up)):
        root = os.path.abspath(os.path.join(root, os.pardir))
    return root


def resolve_output_root(cli_output_root: str, manifest_output_root: str, env_key: str, repo_root: str) -> str:
    """Resolve output root according to contract order."""
    if cli_output_root:
        return os.path.abspath(os.path.expanduser(cli_output_root))
    if manifest_output_root:
        return os.path.abspath(os.path.expanduser(manifest_output_root))
    env_candidates = [str(env_key or "").strip(), GENERIC_OUTPUT_ROOT_ENV, *LEGACY_OUTPUT_ROOT_ENVS]
    for key in env_candidates:
        if key and os.environ.get(key):
            return os.path.abspath(os.path.expanduser(os.environ[key]))
    return os.path.abspath(os.path.join(repo_root, "runtime", "live", "wrapper_jobs"))
