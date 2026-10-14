#!/usr/bin/env python3
"""Allocate monotonic Android app versions for local and server-side builds."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict


def _repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[2]


def _state_dir(repo_root: Path) -> Path:
    """Return the runtime state directory used for APK version allocation."""
    return (repo_root / "runtime" / "state" / "mobile_apk").resolve()


def _state_path(repo_root: Path) -> Path:
    """Return the JSON state path for allocated Android version codes."""
    return (_state_dir(repo_root) / "version_state.json").resolve()


def _lock_path(repo_root: Path) -> Path:
    """Return the lock file path guarding version allocation."""
    return (_state_dir(repo_root) / "version_state.lock").resolve()


def _utc_now_iso() -> str:
    """Return current UTC time in a stable Zulu format."""
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_state(path: Path) -> Dict[str, Any]:
    """Read the prior allocator state, tolerating missing or malformed files."""
    if not path.exists() or not path.is_file():
        return {"schema_version": "android_version_state_v1", "last_version_code": 0}
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {"schema_version": "android_version_state_v1", "last_version_code": 0}
    if not isinstance(raw, dict):
        return {"schema_version": "android_version_state_v1", "last_version_code": 0}
    raw["schema_version"] = "android_version_state_v1"
    try:
        raw["last_version_code"] = int(raw.get("last_version_code") or 0)
    except Exception:
        raw["last_version_code"] = 0
    return raw


def _write_state(path: Path, state: Dict[str, Any]) -> None:
    """Write allocator state atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _version_name(*, base_version: str, channel: str, version_code: int) -> str:
    """Build a visible versionName string for the given channel and code."""
    safe_base = str(base_version or "0.1.0").strip() or "0.1.0"
    safe_channel = str(channel or "").strip().lower()
    if safe_channel == "debug":
        return f"{safe_base}-debug+{int(version_code)}"
    return f"{safe_base}+{int(version_code)}"


def allocate_version(*, repo_root: Path, base_version: str, channel: str) -> Dict[str, Any]:
    """Allocate and persist the next monotonic version code."""
    sdir = _state_dir(repo_root)
    sdir.mkdir(parents=True, exist_ok=True)
    with open(_lock_path(repo_root), "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        state = _read_state(_state_path(repo_root))
        last_code = int(state.get("last_version_code") or 0)
        candidate = int(time.time())
        version_code = max(candidate, last_code + 1)
        payload = {
            "schema_version": "android_version_state_v1",
            "last_version_code": version_code,
            "last_channel": str(channel or "").strip().lower(),
            "last_base_version": str(base_version or "").strip() or "0.1.0",
            "updated_utc": _utc_now_iso(),
        }
        _write_state(_state_path(repo_root), payload)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return {
        "schema_version": "android_build_version_v1",
        "base_version_name": str(base_version or "").strip() or "0.1.0",
        "channel": str(channel or "").strip().lower(),
        "version_code": version_code,
        "version_name": _version_name(base_version=base_version, channel=channel, version_code=version_code),
        "allocated_utc": _utc_now_iso(),
    }


def _shell_quote(value: Any) -> str:
    """Quote a value for safe shell eval."""
    text = str(value or "")
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(_repo_root()))
    parser.add_argument("--base-version", default="0.1.0")
    parser.add_argument("--channel", default="debug", choices=("debug", "release"))
    parser.add_argument("--format", default="json", choices=("json", "shell"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = _parse_args(list(argv or sys.argv[1:]))
    payload = allocate_version(
        repo_root=Path(args.repo_root).resolve(),
        base_version=args.base_version,
        channel=args.channel,
    )
    if args.format == "shell":
        print(f"EIEL_APP_VERSION_CODE={_shell_quote(payload['version_code'])}")
        print(f"EIEL_APP_VERSION_NAME={_shell_quote(payload['version_name'])}")
        print(f"EIEL_APP_VERSION_ALLOCATED_UTC={_shell_quote(payload['allocated_utc'])}")
    else:
        print(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
