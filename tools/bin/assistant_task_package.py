#!/usr/bin/env python3
"""Generate copy-ready read-only assistant task packages for ChatGPT web."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from types import SimpleNamespace

from tools.bin.scheduler_tty_ui import prompt_menu
from infra.webui.ledger.ledger_manager import ChangeLedger
from infra.webui.server_assistant_bridge import AuthenticatedToken, mint_browser_relay_url


TOKEN_SCHEMA_VERSION = "assistant_bridge_tokens_v1"
DEFAULT_TOKEN_FILE = "runtime/secrets/assistant_bridge_tokens.json"
DEFAULT_BASE_URL = "http://127.0.0.1:8787"
DEFAULT_TTL_MINUTES = 90
DEFAULT_ACCESS_MODE = "read-only"

READ_ONLY_SCOPES = [
    "repo.read",
    "ledger.read",
    "artifacts.read",
]

class UserAbort(Exception):
    """Raised when the operator cancels interactive package generation."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _compact_utc(value: datetime | None = None) -> str:
    return (value or _utc_now()).strftime("%Y%m%dT%H%M%SZ")


def _token_file_path(raw: str) -> Path:
    path = Path(str(raw or DEFAULT_TOKEN_FILE)).expanduser()
    if not path.is_absolute():
        path = (_repo_root() / path).resolve()
    return path


def _load_token_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"schema_version": TOKEN_SCHEMA_VERSION, "tokens": []}
    obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(obj, dict):
        raise ValueError("token file must contain a JSON object")
    tokens = obj.get("tokens")
    if not isinstance(tokens, list):
        raise ValueError("token file must contain a tokens list")
    return {"schema_version": str(obj.get("schema_version") or TOKEN_SCHEMA_VERSION), "tokens": [dict(row) for row in tokens if isinstance(row, dict)]}


def _write_token_file(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(obj, indent=2, ensure_ascii=False))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _redact_package_for_disk(text: str) -> str:
    lines: List[str] = []
    for line in str(text or "").splitlines():
        if line.startswith("Bearer token: "):
            lines.append("Bearer token: [REDACTED]")
            continue
        lines.append(re.sub(r"access_token=[^&\\s]+", "access_token=[REDACTED]", line))
    return "\n".join(lines).rstrip() + "\n"


def _read_input(prompt: str = "") -> str:
    try:
        return input(prompt)
    except EOFError as exc:
        raise UserAbort("input stream closed") from exc
    except KeyboardInterrupt as exc:
        raise UserAbort("canceled by user") from exc


def _require_tty() -> None:
    if not sys.stdin.isatty():
        raise UserAbort("interactive task package generation requires a TTY")


def _prompt(label: str, *, default: str = "", required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        raw = _read_input(f"{label}{suffix}: ").strip()
        if raw:
            return raw
        if default:
            return default
        if not required:
            return ""
        print("Value is required.", file=sys.stderr)


def _recent_pis_choices(limit: int = 12) -> List[Dict[str, str]]:
    active_root = (_repo_root() / "governance" / "ledger" / "items" / "active").resolve()
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    rows: List[Dict[str, str]] = []
    for meta_path in sorted(active_root.rglob("meta.json"), reverse=True):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        uid = str(meta.get("uid") or "").strip()
        if not uid.startswith("PIS-") or uid in seen:
            continue
        seen.add(uid)
        rows.append(
            {
                "uid": uid,
                "status": str(meta.get("status") or "").strip() or "draft",
                "title": str(meta.get("title") or "").strip(),
            }
        )
    return rows[:limit]


def _resolve_pis(uid: str) -> Dict[str, str]:
    ledger = ChangeLedger(repo_root=_repo_root())
    item = ledger.read_item(uid)
    meta = dict(item.get("meta") or {})
    item_dir = ledger.get_item_dir(uid)
    active_root = (_repo_root() / "governance" / "ledger" / "items" / "active").resolve()
    try:
        item_dir.resolve().relative_to(active_root)
    except Exception as exc:
        raise ValueError(f"pis uid is not in the active ledger tree: {uid}") from exc
    status = str(meta.get("status") or "").strip() or "draft"
    return {
        "uid": str(meta.get("uid") or uid).strip(),
        "status": status,
        "title": str(meta.get("title") or "").strip(),
    }


def _render_pis_choice(row: Dict[str, str]) -> str:
    return f"{row['uid']} [{row['status']}] {row['title']}"


def _mint_token(*, path: Path, token_id: str, scopes: List[str], ttl_minutes: int) -> Dict[str, str]:
    obj = _load_token_file(path)
    rows = list(obj["tokens"])
    token_value = secrets.token_urlsafe(32)
    created_at = _utc_now()
    expires_at = created_at + timedelta(minutes=int(ttl_minutes))
    row = {
        "token_id": token_id,
        "token": token_value,
        "issuer": "assistant-task-package",
        "audience": "lab",
        "scopes": list(scopes),
        "created_at": _utc_iso(created_at),
        "expires_at": _utc_iso(expires_at),
        "allowed_repo_roots": ["."],
        "allowed_pis_prefixes": [],
    }
    rows.append(row)
    obj["schema_version"] = TOKEN_SCHEMA_VERSION
    obj["tokens"] = rows
    _write_token_file(path, obj)
    return {
        "token_id": token_id,
        "token": token_value,
        "issuer": row["issuer"],
        "audience": row["audience"],
        "scopes": list(scopes),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
    }


def _token_auth(token: Dict[str, Any]) -> AuthenticatedToken:
    return AuthenticatedToken(
        token_id=str(token["token_id"]),
        issuer=str(token["issuer"]),
        audience=str(token["audience"]),
        scopes=tuple(str(item) for item in list(token["scopes"])),
        created_at=str(token["created_at"]),
        expires_at=str(token["expires_at"]),
        last_used_at="",
        allowed_repo_roots=(".",),
        allowed_pis_prefixes=(),
    )


def _mint_relay_link(*, base_url: str, token: Dict[str, Any], operation: str, params: Dict[str, Any] | None = None, ttl_minutes: int) -> str:
    parsed_base = str(base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    host = parsed_base.replace("https://", "", 1).replace("http://", "", 1).strip().rstrip("/")
    proto = "https" if parsed_base.startswith("https://") else "http"
    handler = SimpleNamespace(headers={"Host": host, "X-Forwarded-Proto": proto})
    minted = mint_browser_relay_url(
        handler,
        repo_root=_repo_root(),
        auth=_token_auth(token),
        ttl_sec=int(ttl_minutes) * 60,
        operation=operation,
        params=dict(params or {}),
    )
    return str(minted["relay_url"])


def _starter_links(*, base_url: str, mode: str, token: Dict[str, Any], ttl_minutes: int, selected_pis: Dict[str, str] | None) -> List[tuple[str, str]]:
    links: List[tuple[str, str]] = []
    links.append(("Auth status", _mint_relay_link(base_url=base_url, token=token, operation="auth_status", ttl_minutes=ttl_minutes)))
    if "ledger.read" in set(token["scopes"]):
        links.append(
            (
                "Active PIS list",
                _mint_relay_link(
                    base_url=base_url,
                    token=token,
                    operation="ledger_list_active",
                    params={"limit": 10},
                    ttl_minutes=ttl_minutes,
                ),
            )
        )
    if selected_pis is not None and "ledger.read" in set(token["scopes"]):
        links.append(
            (
                f"Read {selected_pis['uid']}",
                _mint_relay_link(
                    base_url=base_url,
                    token=token,
                    operation="ledger_read",
                    params={"uid": selected_pis["uid"], "include_events": True, "events_limit": 20},
                    ttl_minutes=ttl_minutes,
                ),
            )
        )
    if selected_pis is not None and "repo.read" in set(token["scopes"]):
        links.append(
            (
                f"Repo search for {selected_pis['uid']}",
                _mint_relay_link(
                    base_url=base_url,
                    token=token,
                    operation="repo_search",
                    params={"query": selected_pis["uid"], "roots": ["governance/ledger", "tools", "tests"], "max_results": 10},
                    ttl_minutes=ttl_minutes,
                ),
            )
        )
    return links


def _starter_file_links(*, base_url: str, token: Dict[str, Any], ttl_minutes: int, selected_pis: Dict[str, str] | None) -> List[tuple[str, str]]:
    if selected_pis is None or "repo.read" not in set(token["scopes"]):
        return []
    uid = selected_pis["uid"]
    candidates: List[str] = []
    try:
        cp = subprocess.run(
            ["rg", "-l", uid, "governance/ledger", "tools", "tests"],
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            check=False,
        )
        for raw in str(cp.stdout or "").splitlines():
            rel = str(raw or "").strip()
            if not rel or rel in candidates:
                continue
            if rel.endswith("uid_index.json"):
                continue
            if "/verified/" in rel:
                continue
            candidates.append(rel)
            if len(candidates) >= 5:
                break
    except Exception:
        candidates = []
    links: List[tuple[str, str]] = []
    for rel in candidates:
        links.append(
            (
                f"File read {rel}",
                _mint_relay_link(
                    base_url=base_url,
                    token=token,
                    operation="file_read",
                    params={"path": rel},
                    ttl_minutes=ttl_minutes,
                ),
            )
        )
    return links


def _task_text(kind: str, *, selected_pis: Dict[str, str] | None, custom_task: str) -> str:
    if kind == "pis":
        assert selected_pis is not None
        return (
            f"Inspect {selected_pis['uid']} ({selected_pis['title']}) and return a git-compatible unified diff, "
            "changed files, focused validation commands, and remaining proof gaps."
        )
    return custom_task.strip()


def _api_block(mode: str) -> str:
    return "\n".join(
        [
            "Direct bearer APIs:",
            "- GET  /api/assistant/auth/status",
            "- POST /api/assistant/repo/search",
            "- POST /api/assistant/file/read",
            "- POST /api/assistant/ledger/list_active",
            "- POST /api/assistant/ledger/read",
            "- POST /api/assistant/auth/browser_relay_url",
            "",
            "Signed relay read APIs:",
            "- POST /api/assistant/auth/browser_relay_url  (mint read relay URL)",
            "- GET  /api/assistant/browser_relay/auth_status",
            "- GET  /api/assistant/browser_relay/repo_search",
            "- GET  /api/assistant/browser_relay/ledger_list_active",
            "- GET  /api/assistant/browser_relay/ledger_read",
            "- GET  /api/assistant/browser_relay/file_read",
        ]
    )


def _workflow_block(mode: str) -> str:
    return "\n".join(
        [
            "Suggested flow:",
            "1. Call auth/status first to confirm the token and scopes.",
            "2. Use ledger/read, repo/search, and file/read to gather the minimum repo context needed for the task.",
            "3. Analyze the current implementation and draft a git-compatible unified diff.",
            "4. Return the unified diff, changed files, focused validation commands, and any remaining proof gaps.",
            "5. Do not apply patches, run tests remotely, or claim validation that was not executed locally.",
        ]
    )


def _output_contract_block() -> str:
    return "\n".join(
        [
            "Required response:",
            "- git-compatible unified diff only",
            "- changed files",
            "- focused local validation commands to run",
            "- known risks or proof gaps",
            "- no claims about patch application or test execution unless provided local results later",
        ]
    )


def _package_text(
    *,
    base_url: str,
    mode: str,
    token: Dict[str, str],
    task: str,
    selected_pis: Dict[str, str] | None,
    starter_links: List[tuple[str, str]],
    starter_file_links: List[tuple[str, str]],
) -> str:
    lines = [
        "You can work against my repo through the plane assistant bridge.",
        "",
        f"Task: {task}",
    ]
    if selected_pis is not None:
        lines.extend(
            [
                f"Target PIS UID: {selected_pis['uid']}",
                f"Target PIS title: {selected_pis['title']}",
                f"Target PIS status: {selected_pis['status']}",
            ]
        )
    lines.extend(
        [
            "",
            f"Base URL: {base_url}",
            f"Bearer token: {token['token']}",
            f"Token id: {token['token_id']}",
            f"Expires at: {token['expires_at']}",
            f"Access mode: {DEFAULT_ACCESS_MODE}",
            "",
            "Rules:",
            "- This bridge is read-only. Do not attempt repo mutation or remote validation.",
            "- Stay within repo root `.`.",
            "- Read only the minimum files needed to support the patch.",
            "- Return a unified diff and local validation commands instead of executing them.",
            "",
            _workflow_block(mode),
            "",
            _output_contract_block(),
            "",
            _api_block(mode),
        ]
    )
    if starter_links:
        lines.extend(["", "Starter links:"])
        for label, url in starter_links:
            lines.append(f"- {label}: {url}")
    if starter_file_links:
        lines.extend(["", "Starter file links:"])
        for label, url in starter_file_links:
            lines.append(f"- {label}: {url}")
    if selected_pis is not None:
        lines.extend(
            [
                "",
                "First step recommendation:",
                f"- Read the target item via ledger/read for `{selected_pis['uid']}` before inspecting code.",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _save_package(text: str, *, token_id: str) -> Path:
    out_dir = (_repo_root() / "runtime" / "state" / "assistant_bridge" / "task_packages").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{token_id}.txt"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_redact_package_for_disk(text), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _interactive_args() -> argparse.Namespace:
    _require_tty()
    kind = prompt_menu(
        "Assistant task package",
        choices=["PIS implementation", "General repo task"],
        default_index=0,
        fallback_reader=_read_input,
    )
    ttl_minutes = int(_prompt("Token validity in minutes", default=str(DEFAULT_TTL_MINUTES)))
    base_url = _prompt("Base URL", default=DEFAULT_BASE_URL)
    selected_pis = None
    custom_task = ""
    if kind == "PIS implementation":
        pis_choices = _recent_pis_choices()
        pis_choices.append({"uid": "", "status": "", "title": "No PIS / enter custom task"})
        selected = prompt_menu(
            "Recent PIS",
            choices=pis_choices,
            render=_render_pis_choice,
            default_index=0,
            fallback_reader=_read_input,
        )
        if selected.get("uid"):
            selected_pis = selected
        else:
            custom_task = _prompt("Task", required=True)
    else:
        custom_task = _prompt("Task", required=True)
    return argparse.Namespace(
        mode=DEFAULT_ACCESS_MODE,
        ttl_minutes=ttl_minutes,
        base_url=base_url,
        selected_pis=selected_pis,
        custom_task=custom_task,
    )


def _run(args: argparse.Namespace) -> int:
    mode = str(args.mode).strip().lower()
    if mode != DEFAULT_ACCESS_MODE:
        raise ValueError("short-term assistant task packages only support read-only mode")
    ttl_minutes = max(5, min(int(args.ttl_minutes), 24 * 60))
    selected_pis = args.selected_pis
    task = _task_text("pis" if selected_pis else "custom", selected_pis=selected_pis, custom_task=str(args.custom_task or ""))
    scopes = list(READ_ONLY_SCOPES)
    stamp = _compact_utc()
    token_id = f"assistant-task-{stamp.lower()}"
    token = _mint_token(path=_token_file_path(DEFAULT_TOKEN_FILE), token_id=token_id, scopes=scopes, ttl_minutes=ttl_minutes)
    starter_links = _starter_links(
        base_url=str(args.base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        mode=mode,
        token=token,
        ttl_minutes=ttl_minutes,
        selected_pis=selected_pis,
    )
    starter_file_links = _starter_file_links(
        base_url=str(args.base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        token=token,
        ttl_minutes=ttl_minutes,
        selected_pis=selected_pis,
    )
    package = _package_text(
        base_url=str(args.base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        mode=mode,
        token=token,
        task=task,
        selected_pis=selected_pis,
        starter_links=starter_links,
        starter_file_links=starter_file_links,
    )
    package_path = _save_package(package, token_id=token_id)
    print(f"Task package: {package_path}")
    print()
    print("Copy everything below:")
    print()
    print(package, end="")
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a copy-ready read-only ChatGPT web task package for the assistant bridge.")
    parser.add_argument("--mode", choices=["read-only"], help="Access mode.")
    parser.add_argument("--ttl-minutes", type=int, default=DEFAULT_TTL_MINUTES, help="Token validity in minutes.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Plane base URL.")
    parser.add_argument("--task", default="", help="Custom task text.")
    parser.add_argument("--pis-uid", default="", help="Optional PIS uid to package.")
    ns = parser.parse_args(argv)
    try:
        if any([ns.mode, ns.task, ns.pis_uid]):
            selected_pis = None
            if ns.pis_uid:
                selected_pis = _resolve_pis(ns.pis_uid)
            mode = ns.mode or DEFAULT_ACCESS_MODE
            task = ns.task or (f"Implement and verify {selected_pis['uid']} ({selected_pis['title']})." if selected_pis else "")
            if not task:
                raise ValueError("provide --task or --pis-uid")
            return _run(
                argparse.Namespace(
                    mode=mode,
                    ttl_minutes=ns.ttl_minutes,
                    base_url=ns.base_url,
                    selected_pis=selected_pis,
                    custom_task=task if selected_pis is None else "",
                )
            )
        return _run(_interactive_args())
    except UserAbort:
        print("Canceled.", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
