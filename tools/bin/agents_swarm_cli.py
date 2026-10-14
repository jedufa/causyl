#!/usr/bin/env python3
"""CLI wrapper for /api/agents/swarm endpoints."""

from __future__ import annotations

import argparse
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


DEFAULT_BASE_URL = "http://0.0.0.0:8787"
DEFAULT_REQUEST_TIMEOUT_S = 7200


def _http_json(
    *,
    method: str,
    url: str,
    body: Optional[Dict[str, Any]] = None,
    request_timeout_s: int = DEFAULT_REQUEST_TIMEOUT_S,
) -> Dict[str, Any]:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url=url, method=method.upper(), data=data, headers=headers)
    timeout_s = max(5, int(request_timeout_s))
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {"ok": True}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            obj = json.loads(raw)
        except Exception:
            obj = {"error": raw or str(e)}
        obj.setdefault("ok", False)
        obj.setdefault("http_status", int(e.code))
        return obj
    except (TimeoutError, socket.timeout):
        return {
            "ok": False,
            "error": f"request timed out after {timeout_s}s",
            "code": "CLIENT_TIMEOUT",
        }
    except urllib.error.URLError as e:
        return {
            "ok": False,
            "error": f"connection error: {str(e.reason) if getattr(e, 'reason', None) else str(e)}",
            "code": "CONNECTION_ERROR",
        }


def _build_binding(args: argparse.Namespace) -> Dict[str, str]:
    bt = str(args.binding_type or "ledger_uid").strip()
    bid = str(args.binding_id or "").strip() or str(getattr(args, "target", "") or "").strip()
    if bt not in {"task_id", "ledger_uid"}:
        raise SystemExit("--binding-type must be task_id|ledger_uid")
    if not bid:
        raise SystemExit("--binding-id is required")
    return {"type": bt, "id": bid}


def cmd_start(args: argparse.Namespace) -> Dict[str, Any]:
    body = {
        "binding": _build_binding(args),
        "objective": str(args.objective or "").strip(),
        "options": {
            "started_by_role": str(args.started_by_role or "generalist").strip(),
            "wall_clock_s": int(args.wall_clock_s),
            "maker_lease_s": int(args.maker_lease_s),
            "finisher_lease_s": int(args.finisher_lease_s),
            "max_makers": int(args.max_makers),
            "max_finishers": int(args.max_finishers),
        },
    }
    return _http_json(
        method="POST",
        url=f"{args.base_url.rstrip('/')}/api/agents/swarm/start",
        body=body,
        request_timeout_s=int(args.request_timeout_s),
    )


def cmd_status(args: argparse.Namespace) -> Dict[str, Any]:
    q = urllib.parse.urlencode(
        {
            "swarm_id": str(args.swarm_id or "").strip() or str(getattr(args, "swarm_id_pos", "") or "").strip(),
            "events_limit": int(args.events_limit),
        }
    )
    return _http_json(
        method="GET",
        url=f"{args.base_url.rstrip('/')}/api/agents/swarm/status?{q}",
        request_timeout_s=int(args.request_timeout_s),
    )


def cmd_list(args: argparse.Namespace) -> Dict[str, Any]:
    params: Dict[str, Any] = {"limit": int(args.limit)}
    if args.binding_type and args.binding_id:
        params["binding_type"] = str(args.binding_type)
        params["binding_id"] = str(args.binding_id)
    q = urllib.parse.urlencode(params)
    return _http_json(
        method="GET",
        url=f"{args.base_url.rstrip('/')}/api/agents/swarm/list?{q}",
        request_timeout_s=int(args.request_timeout_s),
    )


def cmd_cancel(args: argparse.Namespace) -> Dict[str, Any]:
    body = {
        "swarm_id": str(args.swarm_id or "").strip() or str(getattr(args, "swarm_id_pos", "") or "").strip(),
        "reason": str(args.reason or "").strip(),
    }
    return _http_json(
        method="POST",
        url=f"{args.base_url.rstrip('/')}/api/agents/swarm/cancel",
        body=body,
        request_timeout_s=int(args.request_timeout_s),
    )


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Agent swarm CLI")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--request-timeout-s", type=int, default=DEFAULT_REQUEST_TIMEOUT_S)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="Start a new swarm run")
    p_start.add_argument("target", nargs="?", default="", help="Binding id shortcut")
    p_start.add_argument("--binding-type", default="ledger_uid", choices=["task_id", "ledger_uid"])
    p_start.add_argument("--binding-id", default="")
    p_start.add_argument("--objective", required=True)
    p_start.add_argument("--started-by-role", default="generalist")
    p_start.add_argument("--wall-clock-s", type=int, default=3600)
    p_start.add_argument("--maker-lease-s", type=int, default=300)
    p_start.add_argument("--finisher-lease-s", type=int, default=180)
    p_start.add_argument("--max-makers", type=int, default=4)
    p_start.add_argument("--max-finishers", type=int, default=2)
    p_start.set_defaults(func=cmd_start)

    p_status = sub.add_parser("status", help="Get swarm status")
    p_status.add_argument("swarm_id_pos", nargs="?", default="")
    p_status.add_argument("--swarm-id", default="")
    p_status.add_argument("--events-limit", type=int, default=120)
    p_status.set_defaults(func=cmd_status)

    p_list = sub.add_parser("list", help="List swarm runs")
    p_list.add_argument("--binding-type", default="")
    p_list.add_argument("--binding-id", default="")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.set_defaults(func=cmd_list)

    p_cancel = sub.add_parser("cancel", help="Cancel a swarm run")
    p_cancel.add_argument("swarm_id_pos", nargs="?", default="")
    p_cancel.add_argument("--swarm-id", default="")
    p_cancel.add_argument("--reason", default="")
    p_cancel.set_defaults(func=cmd_cancel)
    return ap


def main() -> int:
    args = _parser().parse_args()
    payload = args.func(args)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if bool(payload.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
