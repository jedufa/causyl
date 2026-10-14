#!/usr/bin/env python3
"""Staged provider health checker for lab scheduler/model routing triage."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short_error(text: str, *, limit: int = 140) -> str:
    s = str(text or "").strip().replace("\n", " ")
    return s if len(s) <= limit else (s[: limit - 1] + "…")


def _dns_resolve(hostname: str, *, timeout_s: float = 3.0) -> Tuple[bool, str]:
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout_s)
    try:
        infos = socket.getaddrinfo(hostname, None)
        ips = sorted({str(entry[4][0]) for entry in infos if entry and len(entry) >= 5})
        if not ips:
            return False, "resolved_empty"
        sample = ", ".join(ips[:2])
        more = "" if len(ips) <= 2 else f" (+{len(ips)-2})"
        return True, f"ok ({sample}{more})"
    except Exception as e:  # pragma: no cover - environment dependent
        return False, _short_error(str(e))
    finally:
        socket.setdefaulttimeout(old_timeout)


def _tcp_connect(hostname: str, port: int, *, timeout_s: float = 3.0) -> Tuple[bool, str]:
    try:
        with socket.create_connection((hostname, port), timeout=timeout_s):
            return True, "ok"
    except Exception as e:  # pragma: no cover - environment dependent
        return False, _short_error(str(e))


def _http_probe(url: str, *, timeout_s: float = 5.0) -> Tuple[bool, str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            code = int(getattr(resp, "status", 0) or 0)
            return True, f"http_{code}"
    except urllib.error.HTTPError as e:
        return True, f"http_{int(getattr(e, 'code', 0) or 0)}"
    except Exception as e:  # pragma: no cover - environment dependent
        return False, _short_error(str(e))


def _binary_probe(binary_name: str) -> Tuple[bool, str, str]:
    path = str(shutil.which(binary_name) or "").strip()
    if not path:
        return False, "", "missing_binary"
    try:
        cp = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        txt = str(cp.stdout or cp.stderr or "").strip().splitlines()
        first = txt[0].strip() if txt else ""
        if cp.returncode == 0 and first:
            return True, path, first
        return True, path, _short_error(first or f"version_probe_exit_{int(cp.returncode)}")
    except Exception as e:  # pragma: no cover - environment dependent
        return True, path, f"version_probe_error:{_short_error(str(e))}"


def _env_present(name: str) -> bool:
    return bool(str(os.environ.get(name) or "").strip())


def _safe_import_server_agents():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from infra.webui import server_agents as _agents  # type: ignore

    return _agents


def _probe_gemini(model: str, timeout_s: int) -> Tuple[bool, str, float]:
    _agents = _safe_import_server_agents()
    start = time.monotonic()
    try:
        res = _agents._run_gemini_oneshot(
            model_input="Reply exactly: OK",
            instructions="Return exactly OK.",
            model=model,
            timeout_s=int(timeout_s),
        )
    except Exception as e:
        return False, _short_error(str(e)), time.monotonic() - start
    ok = bool(res.get("ok"))
    txt = str(res.get("error") or res.get("content") or "").strip()
    return ok, _short_error(txt or ("ok" if ok else "empty_response")), time.monotonic() - start


def _probe_claude(model: str, timeout_s: int) -> Tuple[bool, str, float]:
    _agents = _safe_import_server_agents()
    start = time.monotonic()
    try:
        res = _agents._run_claude_oneshot(
            model_input="Reply exactly: OK",
            instructions="Return exactly OK.",
            model=model,
            reasoning_effort="low",
            timeout_s=int(timeout_s),
        )
    except Exception as e:
        return False, _short_error(str(e)), time.monotonic() - start
    ok = bool(res.get("ok"))
    txt = str(res.get("error") or res.get("content") or "").strip()
    return ok, _short_error(txt or ("ok" if ok else "empty_response")), time.monotonic() - start


def _probe_xai(model: str, timeout_s: int) -> Tuple[bool, str, float]:
    _agents = _safe_import_server_agents()
    start = time.monotonic()
    try:
        res = _agents._run_xai_oneshot(
            model_input="Reply exactly: OK",
            instructions="Return exactly OK.",
            model=model,
            timeout_s=int(timeout_s),
        )
    except Exception as e:
        return False, _short_error(str(e)), time.monotonic() - start
    ok = bool(res.get("ok"))
    txt = str(res.get("error") or res.get("content") or "").strip()
    return ok, _short_error(txt or ("ok" if ok else "empty_response")), time.monotonic() - start


def _probe_ollama(model: str, timeout_s: int) -> Tuple[bool, str, float]:
    _agents = _safe_import_server_agents()
    start = time.monotonic()
    try:
        res = _agents._run_ollama_oneshot(
            model_input="Reply exactly: OK",
            instructions="Return exactly OK.",
            model=model,
            timeout_s=int(timeout_s),
        )
    except Exception as e:
        return False, _short_error(str(e)), time.monotonic() - start
    ok = bool(res.get("ok"))
    txt = str(res.get("error") or res.get("content") or "").strip()
    return ok, _short_error(txt or ("ok" if ok else "empty_response")), time.monotonic() - start


def _probe_codex_cli(model: str, timeout_s: int) -> Tuple[bool, str, float]:
    codex_bin = str(shutil.which("codex") or "").strip()
    start = time.monotonic()
    if not codex_bin:
        return False, "codex CLI not found", 0.0
    cmd = [
        codex_bin,
        "--dangerously-bypass-approvals-and-sandbox",
        "exec",
        "--model",
        str(model).strip(),
        "-C",
        str(REPO_ROOT),
        "--skip-git-repo-check",
        "-",
    ]
    try:
        cp = subprocess.run(
            cmd,
            input="Reply exactly: OK",
            capture_output=True,
            text=True,
            check=False,
            timeout=max(30, int(timeout_s)),
        )
    except Exception as e:
        return False, _short_error(str(e)), time.monotonic() - start
    out = str(cp.stdout or "").strip()
    err = str(cp.stderr or "").strip()
    ok = bool(cp.returncode == 0 and out)
    if ok:
        return True, _short_error(out), time.monotonic() - start
    return False, _short_error(err or out or f"codex_exit_{int(cp.returncode)}"), time.monotonic() - start


@dataclass
class ProviderCase:
    provider: str
    model: str
    remote_host: Optional[str] = None
    remote_port: int = 443
    remote_probe_url: Optional[str] = None
    required_env: Optional[str] = None
    required_binary: Optional[str] = None
    local_host: Optional[str] = None
    local_port: Optional[int] = None
    probe_kind: str = "none"  # none|gemini|claude|xai|ollama|codex


def _bucket_for(
    *,
    dns_ok: Optional[bool],
    tcp_ok: Optional[bool],
    local_ok: Optional[bool],
    local_note: str,
    bin_ok: Optional[bool],
    probe_ran: bool,
    probe_ok: bool,
    probe_reason: str,
) -> str:
    local_txt = str(local_note or "").lower()
    probe_txt = str(probe_reason or "").lower()
    if local_ok is False and "operation not permitted" in local_txt:
        return "transport_blocked_by_runtime_policy"
    if local_ok is False:
        return "local_dependency_missing"
    if bin_ok is False:
        return "local_dependency_missing"
    if dns_ok is False:
        return "provider_unreachable_dns"
    if tcp_ok is False:
        return "provider_unreachable_network"
    if not probe_ran:
        return "reachable_preflight_only"
    if probe_ok:
        return "healthy_end_to_end"
    if "timed out" in probe_txt or "timeout" in probe_txt:
        return "reachable_but_slow_or_interactive"
    if "operation not permitted" in probe_txt:
        return "transport_blocked_by_runtime_policy"
    return "model_or_runtime_failure"


def _run_case(case: ProviderCase, *, timeout_s: int, run_model_probes: bool) -> Dict[str, Any]:
    dns_ok: Optional[bool] = None
    dns_note = ""
    tcp_ok: Optional[bool] = None
    tcp_note = ""
    http_ok: Optional[bool] = None
    http_note = ""
    env_ok: Optional[bool] = None
    env_note = ""
    bin_ok: Optional[bool] = None
    bin_note = ""
    local_ok: Optional[bool] = None
    local_note = ""
    probe_ok = False
    probe_note = "not_run"
    probe_s = 0.0
    probe_ran = False

    if case.required_env:
        env_ok = _env_present(case.required_env)
        env_note = "present" if env_ok else "missing"

    if case.required_binary:
        b_ok, b_path, b_detail = _binary_probe(case.required_binary)
        bin_ok = b_ok
        if b_ok:
            bin_note = f"{b_path} ({b_detail})"
        else:
            bin_note = f"{b_path or case.required_binary}: {b_detail}"

    if case.remote_host:
        dns_ok, dns_note = _dns_resolve(case.remote_host)
        if dns_ok:
            tcp_ok, tcp_note = _tcp_connect(case.remote_host, int(case.remote_port))
        else:
            tcp_ok = False
            tcp_note = "skipped_dns_failed"
    if case.remote_probe_url:
        http_ok, http_note = _http_probe(case.remote_probe_url)

    if case.local_host and case.local_port:
        local_ok, local_note = _tcp_connect(case.local_host, int(case.local_port))

    can_probe = run_model_probes
    if case.required_env and env_ok is False:
        can_probe = False
    if case.required_binary and bin_ok is False:
        can_probe = False
    if case.remote_host and dns_ok is False:
        can_probe = False
    if case.remote_host and tcp_ok is False:
        can_probe = False
    if case.local_host and local_ok is False:
        can_probe = False

    if can_probe:
        probe_ran = True
        if case.probe_kind == "gemini":
            probe_ok, probe_note, probe_s = _probe_gemini(case.model, timeout_s)
        elif case.probe_kind == "claude":
            probe_ok, probe_note, probe_s = _probe_claude(case.model, timeout_s)
        elif case.probe_kind == "xai":
            probe_ok, probe_note, probe_s = _probe_xai(case.model, timeout_s)
        elif case.probe_kind == "ollama":
            probe_ok, probe_note, probe_s = _probe_ollama(case.model, timeout_s)
        elif case.probe_kind == "codex":
            probe_ok, probe_note, probe_s = _probe_codex_cli(case.model, timeout_s)
        else:
            probe_ok = False
            probe_note = "probe_kind_not_supported"

    bucket = _bucket_for(
        dns_ok=dns_ok,
        tcp_ok=tcp_ok,
        local_ok=local_ok,
        local_note=local_note,
        bin_ok=bin_ok,
        probe_ran=probe_ran,
        probe_ok=probe_ok,
        probe_reason=probe_note,
    )
    status = "PASS" if bucket == "healthy_end_to_end" else "FAIL"
    return {
        "provider": case.provider,
        "model": case.model,
        "status": status,
        "bucket": bucket,
        "dns_ok": dns_ok,
        "dns_note": dns_note,
        "tcp_ok": tcp_ok,
        "tcp_note": tcp_note,
        "http_ok": http_ok,
        "http_note": http_note,
        "env_ok": env_ok,
        "env_note": env_note,
        "binary_ok": bin_ok,
        "binary_note": bin_note,
        "local_ok": local_ok,
        "local_note": local_note,
        "probe_ran": probe_ran,
        "probe_ok": probe_ok,
        "probe_duration_s": round(probe_s, 2),
        "probe_note": probe_note,
    }


def _cases() -> List[ProviderCase]:
    return [
        ProviderCase(
            provider="openai",
            model="gpt-5.4",
            remote_host="api.openai.com",
            remote_probe_url="https://api.openai.com/v1/models",
            required_env="OPENAI_API_KEY",            probe_kind="none",
        ),
        ProviderCase(
            provider="xai",
            model="grok-code-fast-1",
            remote_host="api.x.ai",
            remote_probe_url="https://api.x.ai/v1/models",
            required_env="XAI_API_KEY",
            required_binary="curl",
            probe_kind="xai",
        ),
        ProviderCase(
            provider="gemini",
            model="gemini-2.5-pro",
            remote_host="generativelanguage.googleapis.com",
            remote_probe_url="https://generativelanguage.googleapis.com",
            required_binary="gemini",
            probe_kind="gemini",
        ),
        ProviderCase(
            provider="claude",
            model="claude-haiku-4-5",
            remote_host="api.anthropic.com",
            remote_probe_url="https://api.anthropic.com",
            required_binary="claude",
            probe_kind="claude",
        ),
        ProviderCase(
            provider="codex",
            model="gpt-5.4",
            remote_host="chatgpt.com",
            remote_probe_url="https://chatgpt.com",
            required_binary="codex",            probe_kind="codex",
        ),
        ProviderCase(
            provider="ollama",
            model="qwen3.5",
            local_host="127.0.0.1",
            local_port=11434,
            remote_probe_url="http://127.0.0.1:11434/api/tags",
            required_binary="curl",
            probe_kind="ollama",
        ),
    ]


def _markdown_report(results: List[Dict[str, Any]], *, timeout_s: int, run_model_probes: bool) -> str:
    lines: List[str] = []
    lines.append(f"# Provider Staged Health Check - {_utc_now()}")
    lines.append("")
    lines.append("## Policy")
    lines.append(f"- run_model_probes: `{str(run_model_probes).lower()}`")
    lines.append(f"- probe_timeout_s: `{int(timeout_s)}`")
    lines.append("- classification buckets: `provider_unreachable_dns`, `provider_unreachable_network`, `local_dependency_missing`, `reachable_but_slow_or_interactive`, `transport_blocked_by_runtime_policy`, `model_or_runtime_failure`, `healthy_end_to_end`")
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append("| Provider | Model | Status | Bucket | Key Evidence |")
    lines.append("|---|---|---|---|---|")
    for row in results:
        evidence_parts: List[str] = []
        if row.get("dns_ok") is not None:
            evidence_parts.append(f"dns={row.get('dns_note')}")
        if row.get("tcp_ok") is not None:
            evidence_parts.append(f"tcp={row.get('tcp_note')}")
        if row.get("local_ok") is not None:
            evidence_parts.append(f"local={row.get('local_note')}")
        if row.get("binary_ok") is not None:
            evidence_parts.append(f"bin={_short_error(str(row.get('binary_note') or ''))}")
        if row.get("env_ok") is not None:
            evidence_parts.append(f"env={row.get('env_note')}")
        if row.get("probe_ran"):
            evidence_parts.append(f"probe={_short_error(str(row.get('probe_note') or ''))} ({row.get('probe_duration_s')}s)")
        elif run_model_probes:
            evidence_parts.append("probe=skipped_preflight_failed")
        lines.append(
            f"| {row.get('provider')} | {row.get('model')} | {row.get('status')} | {row.get('bucket')} | {'; '.join(evidence_parts)} |"
        )
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run staged provider health checks.")
    parser.add_argument("--timeout-s", type=int, default=90, help="Per-provider model probe timeout in seconds.")
    parser.add_argument(
        "--no-model-probes",
        action="store_true",
        help="Only run preflight checks (DNS/TCP/local daemon/binary/env).",
    )
    parser.add_argument(
        "--output-md",
        type=str,
        default="runtime/reports/provider_staged_health_2026-03-15.md",
        help="Markdown output path.",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default="runtime/reports/provider_staged_health_2026-03-15.json",
        help="JSON output path.",
    )
    args = parser.parse_args()

    run_model_probes = not bool(args.no_model_probes)
    results: List[Dict[str, Any]] = []
    for case in _cases():
        results.append(_run_case(case, timeout_s=int(args.timeout_s), run_model_probes=run_model_probes))

    md_text = _markdown_report(results, timeout_s=int(args.timeout_s), run_model_probes=run_model_probes)
    out_md = (REPO_ROOT / str(args.output_md)).resolve()
    out_json = (REPO_ROOT / str(args.output_json)).resolve()
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md_text, encoding="utf-8")
    out_json.write_text(json.dumps({"generated_utc": _utc_now(), "results": results}, indent=2), encoding="utf-8")

    print(md_text)
    print(f"wrote: {out_md}")
    print(f"wrote: {out_json}")
    failed = [r for r in results if r.get("status") != "PASS"]
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
