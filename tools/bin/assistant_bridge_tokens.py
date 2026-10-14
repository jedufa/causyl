#!/usr/bin/env python3
"""Manage local assistant bridge tokens."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from tools.bin.scheduler_tty_ui import prompt_menu, prompt_yes_no


TOKEN_SCHEMA_VERSION = "assistant_bridge_tokens_v1"
DEFAULT_TOKEN_FILE = "runtime/secrets/assistant_bridge_tokens.json"
DEFAULT_FULL_SCOPES = [
    "repo.read",
    "ledger.read",
    "session.open",
    "diff.apply",
    "validation.run",
    "artifacts.read",
    "control_pane.access",
]
DEFAULT_READONLY_SCOPES = [
    "repo.read",
    "ledger.read",
    "artifacts.read",
]


class UserAbort(Exception):
    """Raised when the operator cancels interactive token management."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


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


def _scopes_from_preset(preset: str) -> List[str]:
    if preset == "full":
        return list(DEFAULT_FULL_SCOPES)
    if preset == "readonly":
        return list(DEFAULT_READONLY_SCOPES)
    raise ValueError(f"unknown scope preset: {preset}")


def _parse_csv(raw: str) -> List[str]:
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


def _find_token(rows: List[Dict[str, Any]], token_id: str) -> Dict[str, Any]:
    wanted = str(token_id or "").strip()
    for row in rows:
        if str(row.get("token_id") or "").strip() == wanted:
            return row
    raise ValueError(f"token_id not found: {wanted}")


def _read_input(prompt: str = "") -> str:
    try:
        return input(prompt)
    except EOFError as exc:
        raise UserAbort("input stream closed") from exc
    except KeyboardInterrupt as exc:
        raise UserAbort("canceled by user") from exc


def _require_tty() -> None:
    if not sys.stdin.isatty():
        raise UserAbort("interactive token management requires a TTY")


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


def _row_is_revoked(row: Dict[str, Any]) -> bool:
    return bool(row.get("revoked") is True or str(row.get("status") or "").strip().lower() == "revoked")


def _access_type(row: Dict[str, Any]) -> str:
    scopes = {str(item).strip() for item in list(row.get("scopes") or []) if str(item).strip()}
    if any(scope in scopes for scope in {"session.open", "diff.apply", "validation.run", "relay.write_file", "relay.delete_file", "relay.apply_diff", "relay.validation.run"}):
        return "full"
    return "read-only"


def _token_summary(row: Dict[str, Any]) -> str:
    state = "revoked" if _row_is_revoked(row) else "active"
    return f"{row.get('token_id')} [{_access_type(row)} | {state}] expires={row.get('expires_at') or '-'}"


def _print_token_table(path: Path, rows: List[Dict[str, Any]]) -> None:
    print(f"Assistant bridge tokens: {path}")
    if not rows:
        print("No tokens configured.")
        return
    id_width = max(len("Token ID"), max(len(str(row.get("token_id") or "")) for row in rows))
    exp_width = max(len("Expires"), max(len(str(row.get("expires_at") or "-")) for row in rows))
    type_width = max(len("Access"), max(len(_access_type(row)) for row in rows))
    print(f"{'Token ID'.ljust(id_width)}  {'Expires'.ljust(exp_width)}  {'Access'.ljust(type_width)}")
    print(f"{'-' * id_width}  {'-' * exp_width}  {'-' * type_width}")
    for row in rows:
        token_id = str(row.get('token_id') or '')
        expires_at = str(row.get('expires_at') or '-')
        access = _access_type(row)
        if _row_is_revoked(row):
            access = f"{access} (revoked)"
        print(f"{token_id.ljust(id_width)}  {expires_at.ljust(exp_width)}  {access}")


def _interactive_action(path: Path) -> argparse.Namespace:
    _require_tty()
    obj = _load_token_file(path)
    rows = list(obj["tokens"])
    action = prompt_menu(
        "Assistant bridge token action",
        choices=["list", "create", "revoke", "revoke-all", "rotate"],
        default_index=0,
        fallback_reader=_read_input,
    )
    if action == "list":
        return argparse.Namespace(cmd="list", file=str(path), func=_list_tokens)
    if action == "create":
        preset = prompt_menu(
            "Scope preset",
            choices=["full", "readonly"],
            default_index=0,
            fallback_reader=_read_input,
        )
        token_id = _prompt("Token id", required=True)
        issuer = _prompt("Issuer", default="local-operator")
        audience = _prompt("Audience", default="lab")
        expires_days = _prompt("Expires in days", default="365")
        allowed_repo_roots = _prompt("Allowed repo roots (csv)", default=".")
        allowed_pis_prefixes = _prompt("Allowed PIS prefixes (csv)", default="")
        custom_scopes = ""
        if prompt_yes_no("Override scopes manually", default=False, fallback_reader=_read_input):
            custom_scopes = _prompt("Scopes csv", default="")
        return argparse.Namespace(
            cmd="create",
            file=str(path),
            token_id=token_id,
            scope_preset=preset,
            scopes=custom_scopes,
            issuer=issuer,
            audience=audience,
            expires_days=int(expires_days),
            allowed_repo_roots=allowed_repo_roots,
            allowed_pis_prefixes=allowed_pis_prefixes,
            func=_create_token,
        )
    if action == "revoke-all":
        active_rows = [row for row in rows if not _row_is_revoked(row)]
        if not active_rows:
            raise UserAbort("no active tokens available for this action")
        if not prompt_yes_no("Revoke all active assistant bridge tokens", default=False, fallback_reader=_read_input):
            raise UserAbort("canceled by user")
        return argparse.Namespace(cmd="revoke-all", file=str(path), func=_revoke_all_tokens)
    active_rows = [row for row in rows if not _row_is_revoked(row)]
    if not active_rows:
        raise UserAbort("no active tokens available for this action")
    selected = prompt_menu(
        "Select token",
        choices=active_rows,
        render=_token_summary,
        default_index=0,
        fallback_reader=_read_input,
    )
    token_id = str(selected.get("token_id") or "").strip()
    if action == "revoke":
        if not prompt_yes_no(f"Revoke {token_id}", default=False, fallback_reader=_read_input):
            raise UserAbort("canceled by user")
        return argparse.Namespace(cmd="revoke", file=str(path), token_id=token_id, func=_revoke_token)
    new_token_id = _prompt("New token id", default=f"{token_id}-rotated")
    expires_days = _prompt("Expires in days", default="365")
    if not prompt_yes_no(f"Rotate {token_id} -> {new_token_id}", default=False, fallback_reader=_read_input):
        raise UserAbort("canceled by user")
    return argparse.Namespace(
        cmd="rotate",
        file=str(path),
        token_id=token_id,
        new_token_id=new_token_id,
        expires_days=int(expires_days),
        func=_rotate_token,
    )


def _create_token(args: argparse.Namespace) -> int:
    path = _token_file_path(args.file)
    obj = _load_token_file(path)
    rows = list(obj["tokens"])
    token_id = str(args.token_id).strip()
    if any(str(row.get("token_id") or "").strip() == token_id for row in rows):
        raise ValueError(f"token_id already exists: {token_id}")
    scopes = _parse_csv(args.scopes) if args.scopes else _scopes_from_preset(args.scope_preset)
    created_at = _utc_now()
    expires_at = created_at + timedelta(days=int(args.expires_days))
    token_value = secrets.token_urlsafe(32)
    row = {
        "token_id": token_id,
        "token": token_value,
        "issuer": str(args.issuer or "local-operator").strip() or "local-operator",
        "audience": str(args.audience or "lab").strip() or "lab",
        "scopes": scopes,
        "created_at": _utc_iso(created_at),
        "expires_at": _utc_iso(expires_at),
        "allowed_repo_roots": _parse_csv(args.allowed_repo_roots) or ["."],
        "allowed_pis_prefixes": _parse_csv(args.allowed_pis_prefixes),
    }
    rows.append(row)
    obj["schema_version"] = TOKEN_SCHEMA_VERSION
    obj["tokens"] = rows
    _write_token_file(path, obj)
    print(json.dumps({"ok": True, "token_id": token_id, "token": token_value, "file": str(path), "scopes": scopes, "expires_at": row["expires_at"]}, indent=2, ensure_ascii=False))
    return 0


def _list_tokens(args: argparse.Namespace) -> int:
    path = _token_file_path(args.file)
    obj = _load_token_file(path)
    _print_token_table(path, list(obj["tokens"]))
    return 0


def _revoke_token(args: argparse.Namespace) -> int:
    path = _token_file_path(args.file)
    obj = _load_token_file(path)
    row = _find_token(obj["tokens"], args.token_id)
    row["revoked"] = True
    row["status"] = "revoked"
    row["revoked_at"] = _utc_iso(_utc_now())
    _write_token_file(path, obj)
    print(json.dumps({"ok": True, "token_id": args.token_id, "revoked": True, "file": str(path)}, indent=2, ensure_ascii=False))
    return 0


def _revoke_all_tokens(args: argparse.Namespace) -> int:
    path = _token_file_path(args.file)
    obj = _load_token_file(path)
    revoked_ids: List[str] = []
    now_utc = _utc_iso(_utc_now())
    for row in obj["tokens"]:
        if _row_is_revoked(row):
            continue
        row["revoked"] = True
        row["status"] = "revoked"
        row["revoked_at"] = now_utc
        revoked_ids.append(str(row.get("token_id") or ""))
    _write_token_file(path, obj)
    print(json.dumps({"ok": True, "revoked_count": len(revoked_ids), "token_ids": revoked_ids, "file": str(path)}, indent=2, ensure_ascii=False))
    return 0


def _rotate_token(args: argparse.Namespace) -> int:
    path = _token_file_path(args.file)
    obj = _load_token_file(path)
    rows = obj["tokens"]
    current = _find_token(rows, args.token_id)
    current["revoked"] = True
    current["status"] = "revoked"
    current["revoked_at"] = _utc_iso(_utc_now())
    new_token_id = str(args.new_token_id or f"{args.token_id}-rotated").strip()
    if any(str(row.get("token_id") or "").strip() == new_token_id for row in rows):
        raise ValueError(f"new token_id already exists: {new_token_id}")
    created_at = _utc_now()
    expires_at = created_at + timedelta(days=int(args.expires_days))
    token_value = secrets.token_urlsafe(32)
    new_row = {
        "token_id": new_token_id,
        "token": token_value,
        "issuer": str(current.get("issuer") or "local-operator"),
        "audience": str(current.get("audience") or "lab"),
        "scopes": list(current.get("scopes") or []),
        "created_at": _utc_iso(created_at),
        "expires_at": _utc_iso(expires_at),
        "allowed_repo_roots": list(current.get("allowed_repo_roots") or ["."]),
        "allowed_pis_prefixes": list(current.get("allowed_pis_prefixes") or []),
        "rotates": str(args.token_id),
    }
    rows.append(new_row)
    _write_token_file(path, obj)
    print(json.dumps({"ok": True, "rotated_from": args.token_id, "token_id": new_token_id, "token": token_value, "file": str(path), "expires_at": new_row["expires_at"]}, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Manage local assistant bridge tokens.")
    p.add_argument("--file", default=DEFAULT_TOKEN_FILE, help="token file path")
    sub = p.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="list configured tokens")
    p_list.set_defaults(func=_list_tokens)

    p_create = sub.add_parser("create", help="create a new token")
    p_create.add_argument("token_id")
    p_create.add_argument("--scope-preset", choices=("full", "readonly"), default="full")
    p_create.add_argument("--scopes", default="", help="comma-separated scope override")
    p_create.add_argument("--issuer", default="local-operator")
    p_create.add_argument("--audience", default="lab")
    p_create.add_argument("--expires-days", type=int, default=365)
    p_create.add_argument("--allowed-repo-roots", default=".")
    p_create.add_argument("--allowed-pis-prefixes", default="")
    p_create.set_defaults(func=_create_token)

    p_revoke = sub.add_parser("revoke", help="revoke an existing token")
    p_revoke.add_argument("token_id")
    p_revoke.set_defaults(func=_revoke_token)

    p_revoke_all = sub.add_parser("revoke-all", help="revoke all active tokens")
    p_revoke_all.set_defaults(func=_revoke_all_tokens)

    p_rotate = sub.add_parser("rotate", help="revoke a token and create a replacement")
    p_rotate.add_argument("token_id")
    p_rotate.add_argument("--new-token-id", default="")
    p_rotate.add_argument("--expires-days", type=int, default=365)
    p_rotate.set_defaults(func=_rotate_token)
    return p


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    try:
        raw_argv = list(sys.argv[1:] if argv is None else argv)
        if not raw_argv:
            path = _token_file_path(DEFAULT_TOKEN_FILE)
            args = _interactive_action(path)
            return int(args.func(args))
        args = parser.parse_args(raw_argv)
        if not getattr(args, "cmd", ""):
            parser.error("a subcommand is required")
        return int(args.func(args))
    except UserAbort as exc:
        print(f"Canceled: {exc}", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
