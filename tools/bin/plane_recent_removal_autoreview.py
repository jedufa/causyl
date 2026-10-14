#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.webui.server_codex_runtime import run_codex_oneshot
from tools.bin import plane_critical_regression_pack as pack

OVERRIDE_ENV = "PLANE_ALLOW_RECENT_REMOVAL_REUSE"
_BOOTSTRAP_FILES = (
    "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_agent_bootstrap.md",
    "governance/agents/prompts/_all_codex.md",
    "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context.md",
    "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_assistant_path.md",
)


def _run_git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _status_paths() -> List[str]:
    cp = _run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    raw = cp.stdout or ""
    entries = raw.split("\0")
    out: List[str] = []
    i = 0
    while i < len(entries):
        row = entries[i]
        i += 1
        if not row:
            continue
        if len(row) < 4:
            continue
        code = row[:2]
        path = row[3:]
        target = path
        if "R" in code or "C" in code:
            if i < len(entries):
                target = entries[i]
                i += 1
        if "D" in code:
            continue
        norm = str(target or "").strip()
        if norm:
            out.append(norm)
    return out


def _critical_paths(paths: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for path in paths:
        normalized = str(path or "").strip().replace("\\", "/").lstrip("./")
        if not normalized or normalized in seen:
            continue
        if pack._is_critical_path(normalized):
            seen.add(normalized)
            out.append(normalized)
    return out


def _load_bootstrap_text() -> str:
    chunks: List[str] = []
    for rel in _BOOTSTRAP_FILES:
        path = (REPO_ROOT / rel).resolve()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        chunks.append(f"## {rel}\n{text.strip()}")
    return "\n\n".join(chunks).strip()


def _build_message(*, critical_paths: Sequence[str], findings: Sequence[Dict[str, Any]]) -> str:
    lines = [
        "You are the bounded commit review fallback for the plane.",
        "A governed commit touched critical files and triggered the recent-removal guard.",
        "Your job is to inspect the current diff and the recent-removal findings, then decide whether the commit is safe to proceed.",
        "You may make the narrowest necessary code or test fixes if that is needed to make the change safe.",
        "Stay within the current repo. Do not invent a parallel workflow.",
        "",
        "If the change is safe after your review, reply with:",
        "DECISION: APPROVE",
        "REASON: <one short reason suitable for ALLOW-RECENT-REMOVAL-REUSE>",
        "",
        "If the change is still unsafe or ambiguous after the narrowest honest repair, reply with:",
        "DECISION: BLOCK",
        "REASON: <short reason>",
        "",
        "Critical touched paths:",
    ]
    lines.extend(f"- {path}" for path in critical_paths)
    lines.append("")
    lines.append("Recent-removal findings:")
    for row in findings:
        lines.append(f"- {row.get('path')} @ {row.get('commit')}")
    lines.extend(
        [
            "",
            "Required local checks before approval:",
            "- run the narrowest honest tests for the touched critical paths",
            "- ensure no intentionally removed operator-facing behavior was reintroduced without justification",
            "- if you patch anything, include that in your review and base the decision on the repaired state",
        ]
    )
    return "\n".join(lines).strip()


def _parse_decision(content: str) -> Dict[str, str]:
    text = str(content or "").strip()
    decision_match = re.search(r"(?mi)^DECISION:\s*(APPROVE|BLOCK)\s*$", text)
    reason_match = re.search(r"(?mi)^REASON:\s*(.+?)\s*$", text)
    return {
        "decision": str(decision_match.group(1)).strip() if decision_match else "",
        "reason": str(reason_match.group(1)).strip() if reason_match else "",
        "content": text,
    }


def _run_codex_with_progress(*, message: str, worker_suffix: str) -> Dict[str, Any]:
    bootstrap_text = _load_bootstrap_text()
    result_holder: Dict[str, Any] = {}
    error_holder: Dict[str, BaseException] = {}

    def _target() -> None:
        try:
            result_holder["turn"] = run_codex_oneshot(
                worker_id=f"plane_commit_recent_removal::{worker_suffix}",
                binding_type="operator_request",
                binding_id="governed_commit_recent_removal_guard",
                role="builder",
                message=message,
                bootstrap_text=bootstrap_text,
                cwd=REPO_ROOT,
                timeout_s=900,
            )
        except BaseException as exc:  # pragma: no cover - defensive
            error_holder["exc"] = exc

    line = "[the-plane] Launching Codex fallback for repo_commit_tag_v1: recent_removal_guard"
    if sys.stdout.isatty() and os.environ.get("NO_COLOR") is None:
        line = f"\033[1;35m{line}\033[0m"
    print(line, file=sys.stderr, flush=True)
    print("[the-plane] Codex is reviewing the critical-path reuse and may patch safely before commit continues.", file=sys.stderr, flush=True)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    started = time.time()
    while thread.is_alive():
        thread.join(timeout=5.0)
        if thread.is_alive():
            elapsed = int(time.time() - started)
            print(f"[the-plane] Codex review still running ({elapsed}s elapsed)", file=sys.stderr, flush=True)
    if error_holder:
        raise RuntimeError(str(error_holder["exc"]))
    return dict(result_holder.get("turn") or {})


def main(argv: List[str] | None = None) -> int:
    touched_paths = _status_paths()
    critical_paths = _critical_paths(touched_paths)
    if not critical_paths:
        print(json.dumps({"status": "not_applicable", "reason": "no_critical_paths"}))
        return 0
    if str(os.environ.get(OVERRIDE_ENV) or "").strip():
        print(json.dumps({"status": "approved", "reason": str(os.environ.get(OVERRIDE_ENV) or "").strip(), "source": "env"}))
        return 0
    report = pack._recent_removals_report(
        touched_paths=list(critical_paths),
        since="7 days ago",
        max_commits=12,
    )
    findings = list(report.get("findings") or [])
    if not findings:
        print(json.dumps({"status": "approved", "reason": "", "source": "no_recent_removal_findings"}))
        return 0
    turn = _run_codex_with_progress(
        message=_build_message(critical_paths=critical_paths, findings=findings),
        worker_suffix=str(int(time.time())),
    )
    parsed = _parse_decision(str(turn.get("content") or ""))
    if str(parsed.get("decision") or "").upper() == "APPROVE" and str(parsed.get("reason") or "").strip():
        print(
            json.dumps(
                {
                    "status": "approved",
                    "reason": str(parsed.get("reason") or "").strip(),
                    "source": "codex",
                    "usage": dict(turn.get("usage") or {}),
                }
            )
        )
        return 0
    print(
        json.dumps(
            {
                "status": "blocked",
                "reason": str(parsed.get("reason") or turn.get("stderr") or "codex_review_blocked").strip(),
                "source": "codex",
                "content": str(parsed.get("content") or turn.get("content") or "").strip(),
            }
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
