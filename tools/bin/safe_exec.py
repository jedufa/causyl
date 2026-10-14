#!/usr/bin/env python3
"""Run commands safely and redact secret-like output before printing.

Usage:
  python infra/bin/safe_exec.py -- <cmd> [args...]
  python infra/bin/safe_exec.py <cmd> [args...]
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from typing import Iterable


_RE_ENV_ASSIGN = re.compile(
    r"(?im)^([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)=([^\n\r]+)$"
)
_RE_AUTH_BEARER = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s\"']+)")
_RE_BEARER_ANY = re.compile(r"(?i)(\bbearer\s+)([^\s\"']+)")
_RE_OPENAI_KEY = re.compile(r"\b(sk-(?:proj-)?[A-Za-z0-9_-]{20,})\b")
_RE_JSON_KV = re.compile(
    r"(?i)(\"?(?:api[_-]?key|token|secret|password|authorization)\"?\s*[:=]\s*\"?)([^\"\s,}\]]+)(\"?)"
)


def _mask_value(raw: str) -> str:
    s = str(raw or "")
    if not s:
        return "<REDACTED>"
    dig = hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"<REDACTED:{dig}>"


def redact_text(text: str) -> str:
    out = str(text or "")

    def _env_sub(m: re.Match[str]) -> str:
        return f"{m.group(1)}={_mask_value(m.group(2))}"

    def _kv_sub(m: re.Match[str]) -> str:
        return f"{m.group(1)}{_mask_value(m.group(2))}{m.group(3)}"

    out = _RE_ENV_ASSIGN.sub(_env_sub, out)
    out = _RE_AUTH_BEARER.sub(lambda m: f"{m.group(1)}{_mask_value(m.group(2))}", out)
    out = _RE_BEARER_ANY.sub(lambda m: f"{m.group(1)}{_mask_value(m.group(2))}", out)
    out = _RE_JSON_KV.sub(_kv_sub, out)
    out = _RE_OPENAI_KEY.sub(lambda m: _mask_value(m.group(1)), out)
    return out


def _parse_argv(argv: Iterable[str]) -> list[str]:
    ap = argparse.ArgumentParser(description="Run command and redact output")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    ns = ap.parse_args(list(argv))
    cmd = list(ns.cmd or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise SystemExit("safe_exec.py: missing command")
    return cmd


def main(argv: Iterable[str] | None = None) -> int:
    cmd = _parse_argv(sys.argv[1:] if argv is None else argv)
    cp = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    out = redact_text(str(cp.stdout or ""))
    err = redact_text(str(cp.stderr or ""))
    if out:
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
    if err:
        sys.stderr.write(err)
        if not err.endswith("\n"):
            sys.stderr.write("\n")
    return int(cp.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
