#!/usr/bin/env python3
"""CLI wrapper for /api/agents/orchestration endpoints."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
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
            "error": (
                f"request timed out after {timeout_s}s; "
                "workflow may still be running server-side. "
                "Use `status` to check progress."
            ),
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
    bid = str(args.binding_id or "").strip()
    target = str(getattr(args, "target", "") or "").strip()
    if not bid and target:
        bid = target
    if bt not in {"task_id", "ledger_uid"}:
        raise SystemExit("--binding-type must be task_id|ledger_uid")
    if not bid:
        raise SystemExit("--binding-id is required")
    return {"type": bt, "id": bid}


def cmd_start(args: argparse.Namespace) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "binding": _build_binding(args),
        "phase_label": str(args.phase_label),
        "objective": str(args.objective),
        "options": {
            "max_iterations": int(args.max_iterations),
            "heartbeat_interval_s": int(args.heartbeat_interval_s),
            "timeout_s": int(args.timeout_s),
        },
    }
    subs = [s.strip() for s in str(args.subphases or "").split(",") if s.strip()]
    if subs:
        body["options"]["subphases"] = subs
    if args.pis_subphase_count is not None:
        body["options"]["pis_subphase_count"] = int(args.pis_subphase_count)
    if str(args.execution_directive or "").strip():
        body["options"]["execution_directive"] = str(args.execution_directive).strip()
    return _http_json(
        method="POST",
        url=f"{args.base_url.rstrip('/')}/api/agents/orchestration/start",
        body=body,
        request_timeout_s=int(args.request_timeout_s),
    )


def cmd_run(args: argparse.Namespace) -> Dict[str, Any]:
    workflow_id = str(args.workflow_id or "").strip() or str(getattr(args, "workflow_id_pos", "") or "").strip()
    if not workflow_id:
        raise SystemExit("--workflow-id is required (or provide positional workflow_id)")
    body: Dict[str, Any] = {
        "workflow_id": workflow_id,
        "max_steps": int(args.max_steps),
    }
    if args.heartbeat_interval_s is not None:
        body["heartbeat_interval_s"] = int(args.heartbeat_interval_s)
    if args.timeout_s is not None:
        body["timeout_s"] = int(args.timeout_s)
    hb_interval = int(args.live_heartbeat_s)
    stop_evt = threading.Event()
    hb_thread: Optional[threading.Thread] = None
    if hb_interval > 0:
        hb_thread = threading.Thread(
            target=_run_heartbeat_loop,
            kwargs={
                "base_url": str(args.base_url),
                "workflow_id": workflow_id,
                "request_timeout_s": int(min(60, max(5, int(args.request_timeout_s)))),
                "interval_s": hb_interval,
                "stop_evt": stop_evt,
            },
            daemon=True,
            name="agents-orch-run-heartbeat",
        )
        hb_thread.start()
    try:
        return _http_json(
            method="POST",
            url=f"{args.base_url.rstrip('/')}/api/agents/orchestration/run",
            body=body,
            request_timeout_s=int(args.request_timeout_s),
        )
    finally:
        stop_evt.set()
        if hb_thread is not None:
            hb_thread.join(timeout=2.0)


def cmd_step(args: argparse.Namespace) -> Dict[str, Any]:
    workflow_id = str(args.workflow_id or "").strip() or str(getattr(args, "workflow_id_pos", "") or "").strip()
    if not workflow_id:
        raise SystemExit("--workflow-id is required (or provide positional workflow_id)")
    body = {"workflow_id": workflow_id}
    return _http_json(
        method="POST",
        url=f"{args.base_url.rstrip('/')}/api/agents/orchestration/step",
        body=body,
        request_timeout_s=int(args.request_timeout_s),
    )


def cmd_status(args: argparse.Namespace) -> Dict[str, Any]:
    workflow_id = str(args.workflow_id or "").strip() or str(getattr(args, "workflow_id_pos", "") or "").strip()
    if not workflow_id:
        raise SystemExit("--workflow-id is required (or provide positional workflow_id)")
    q = urllib.parse.urlencode({"workflow_id": workflow_id, "events_limit": int(args.events_limit)})
    return _http_json(
        method="GET",
        url=f"{args.base_url.rstrip('/')}/api/agents/orchestration/status?{q}",
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
        url=f"{args.base_url.rstrip('/')}/api/agents/orchestration/list?{q}",
        request_timeout_s=int(args.request_timeout_s),
    )


def cmd_cancel(args: argparse.Namespace) -> Dict[str, Any]:
    workflow_id = str(args.workflow_id or "").strip() or str(getattr(args, "workflow_id_pos", "") or "").strip()
    if not workflow_id:
        raise SystemExit("--workflow-id is required (or provide positional workflow_id)")
    body = {"workflow_id": workflow_id, "reason": str(args.reason or "")}
    return _http_json(
        method="POST",
        url=f"{args.base_url.rstrip('/')}/api/agents/orchestration/cancel",
        body=body,
        request_timeout_s=int(args.request_timeout_s),
    )


def _run_heartbeat_loop(
    *,
    base_url: str,
    workflow_id: str,
    request_timeout_s: int,
    interval_s: int,
    stop_evt: threading.Event,
) -> None:
    while not stop_evt.wait(max(1, int(interval_s))):
        q = urllib.parse.urlencode({"workflow_id": str(workflow_id), "events_limit": 30})
        st = _http_json(
            method="GET",
            url=f"{base_url.rstrip('/')}/api/agents/orchestration/status?{q}",
            request_timeout_s=int(request_timeout_s),
        )
        if not bool(st.get("ok")):
            msg = str(st.get("error") or "status unavailable")
            sys.stderr.write(f"[heartbeat] {time.strftime('%Y-%m-%d %H:%M:%S')} status_error={msg}\n")
            sys.stderr.flush()
            continue
        state = st.get("state") if isinstance(st.get("state"), dict) else {}
        events = st.get("events") if isinstance(st.get("events"), list) else []
        last_event = events[-1] if events else {}
        ev_name = str(last_event.get("event") or "-")
        ev_utc = str(last_event.get("utc") or "-")
        status = str(state.get("status") or "-")
        turn = str(state.get("turn") or "-")
        itr = int(state.get("iteration") or 0)
        max_itr = int(state.get("max_iterations") or 0)
        sys.stderr.write(
            f"[heartbeat] {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"status={status} turn={turn} iteration={itr}/{max_itr} "
            f"last_event={ev_name} last_event_utc={ev_utc}\n"
        )
        sys.stderr.flush()


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Agent orchestration CLI")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="WebUI base URL (default: http://127.0.0.1:8000)")
    ap.add_argument(
        "--request-timeout-s",
        type=int,
        default=DEFAULT_REQUEST_TIMEOUT_S,
        help=f"HTTP request timeout in seconds (default: {DEFAULT_REQUEST_TIMEOUT_S})",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="Start new workflow")
    p_start.add_argument("target", nargs="?", default="", help="PIS UID or task_id (shortcut for --binding-id)")
    p_start.add_argument("--binding-type", default="ledger_uid", choices=["task_id", "ledger_uid"])
    p_start.add_argument("--binding-id", default="")
    p_start.add_argument("--phase-label", default="", help="Optional context label; auto-derived from PIS if omitted")
    p_start.add_argument("--objective", default="", help="Optional objective; auto-derived from PIS if omitted")
    p_start.add_argument("--subphases", default="", help="Optional comma list, e.g. P1,P2; leave empty for single PIS stage")
    p_start.add_argument("--pis-subphase-count", type=int, default=None, help="Optional count to auto-create P1..Pn stages")
    p_start.add_argument(
        "--execution-directive",
        default="",
        help='Optional extra instruction (e.g. "PIS has 4 sub-phases, execute sequentially").',
    )
    p_start.add_argument(
        "--max-iterations",
        type=int,
        default=4,
        help="Iteration budget for the builder/verifier loop (backend clamps to 1..8).",
    )
    p_start.add_argument("--heartbeat-interval-s", type=int, default=60)
    p_start.add_argument("--timeout-s", type=int, default=7000)
    p_start.set_defaults(func=cmd_start)

    p_run = sub.add_parser("run", help="Run workflow for N steps")
    p_run.add_argument("workflow_id_pos", nargs="?", default="", help="Workflow ID (shortcut for --workflow-id)")
    p_run.add_argument("--workflow-id", default="")
    p_run.add_argument("--max-steps", type=int, default=20)
    p_run.add_argument("--heartbeat-interval-s", type=int)
    p_run.add_argument("--timeout-s", type=int)
    p_run.add_argument(
        "--live-heartbeat-s",
        type=int,
        default=20,
        help="Print terminal heartbeat every N seconds while waiting for run response; 0 disables",
    )
    p_run.set_defaults(func=cmd_run)

    p_step = sub.add_parser("step", help="Run one workflow step")
    p_step.add_argument("workflow_id_pos", nargs="?", default="", help="Workflow ID (shortcut for --workflow-id)")
    p_step.add_argument("--workflow-id", default="")
    p_step.set_defaults(func=cmd_step)

    p_status = sub.add_parser("status", help="Get workflow status")
    p_status.add_argument("workflow_id_pos", nargs="?", default="", help="Workflow ID (shortcut for --workflow-id)")
    p_status.add_argument("--workflow-id", default="")
    p_status.add_argument("--events-limit", type=int, default=120)
    p_status.set_defaults(func=cmd_status)

    p_list = sub.add_parser("list", help="List workflows")
    p_list.add_argument("--binding-type", choices=["task_id", "ledger_uid"])
    p_list.add_argument("--binding-id")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.set_defaults(func=cmd_list)

    p_cancel = sub.add_parser("cancel", help="Cancel workflow")
    p_cancel.add_argument("workflow_id_pos", nargs="?", default="", help="Workflow ID (shortcut for --workflow-id)")
    p_cancel.add_argument("--workflow-id", default="")
    p_cancel.add_argument("--reason", default="")
    p_cancel.set_defaults(func=cmd_cancel)

    return ap


def main(argv: Optional[list[str]] = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)
    out = args.func(args)
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    sys.stdout.write("\n")
    ok = bool(out.get("ok", True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
