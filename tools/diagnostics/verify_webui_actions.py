"""tools.diagnostics.verify_webui_actions

Headless verification script for WebUI "Applied Commands" visibility.

Implements PIS:
- PIS-20260303-092531Z-4J8M-add-webui-command-visibility-smoke-test

What it checks
--------------
This tool creates a minimal synthetic run directory under a provided runs_root,
then verifies that the WebUI endpoint:

  GET /api/run/<run_key>/applied_commands

surfaces command/action records when the authoritative source is:
  1) events/command_events.jsonl (preferred)
  2) viz/applied_commands_log.jsonl (fallback)
  3) acks/ack_*.json and acks/ack_inbox_reject__*.json (fallback)

It runs headless, prints a concise PASS/FAIL summary, and exits non-zero on any
failure.

Usage
-----
Live-server mode (recommended):
  python -m tools.diagnostics.verify_webui_actions --runs_root ./runtime/live --webui_base_url http://127.0.0.1:8787

In-process mode (starts a temporary server):
  python -m tools.diagnostics.verify_webui_actions --runs_root ./runtime/live

"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from urllib.request import Request, urlopen


def _now_utc_iso() -> str:
    """Return current UTC ISO string."""
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _w(p: Path, txt: str) -> None:
    """Write text to file (utf-8), creating parents."""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(txt, encoding="utf-8")


def _write_json(p: Path, obj: Dict[str, Any]) -> None:
    """Write JSON with stable formatting."""
    _w(p, json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _http_get_json(url: str) -> Dict[str, Any]:
    """GET a JSON URL and return parsed object."""
    req = Request(url, headers={"Accept": "application/json", "Cache-Control": "no-store"}, method="GET")
    with urlopen(req, timeout=10) as r:
        data = r.read().decode("utf-8", errors="replace")
    return json.loads(data)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _make_min_run(runs_root: Path, run_rel: str) -> Tuple[Path, str]:
    """Create a minimal run root + return (sim_root, run_key)."""
    run_key = str(run_rel).replace("\\", "/").strip().strip("/")
    if not run_key:
        run_key = f"verify_webui_actions/{uuid.uuid4().hex[:8]}"

    run_root = (runs_root / Path(run_key)).resolve()
    run_root.mkdir(parents=True, exist_ok=True)

    # Minimal sim layout (non-wrapper run): sim_root == run_root
    sim_root = run_root
    (sim_root / "viz").mkdir(parents=True, exist_ok=True)
    (sim_root / "events").mkdir(parents=True, exist_ok=True)
    (sim_root / "acks").mkdir(parents=True, exist_ok=True)
    (sim_root / "inbox").mkdir(parents=True, exist_ok=True)
    (sim_root / "inbox_processed").mkdir(parents=True, exist_ok=True)

    # Minimal run status so WebUI doesn't treat this as junk.
    _write_json(
        sim_root / "viz" / "run_status.json",
        {
            "schema_version": "run_status_v1",
            "status": "complete",
            "current_generation": 0,
            "target_generation": 0,
            "utc": _now_utc_iso(),
        },
    )

    return sim_root, run_key


def _write_events_case(sim_root: Path) -> Dict[str, str]:
    """Write an events/command_events.jsonl with accepted+rejected records."""
    p = sim_root / "events" / "command_events.jsonl"
    u1 = str(uuid.uuid4())
    u2 = str(uuid.uuid4())

    a = {
        "schema_version": "command_event_v1",
        "event_id": str(uuid.uuid4()),
        "command_uuid": u1,
        "command_type": "PressureUpdateCommand",
        "target_generation": 123,
        "apply_phase": "post-selection-pre-mutation",
        "decision": "APPLIED",
        "ack_status": "ACKED",
        "payload_hash": "sha256:deadbeef",
        "utc": _now_utc_iso(),
    }
    r = {
        "schema_version": "command_event_v1",
        "event_id": str(uuid.uuid4()),
        "command_uuid": u2,
        "command_type": "PressureUpdateCommand",
        "target_generation": 124,
        "apply_phase": "post-reproduction-pre-selection",
        "decision": "REJECTED",
        "ack_status": "REJECTED",
        "payload_hash": "sha256:cafebabe",
        "error_reason": "apply_phase conflicts with frozen",
        "utc": _now_utc_iso(),
    }

    _w(p, json.dumps(a, ensure_ascii=False) + "\n" + json.dumps(r, ensure_ascii=False) + "\n")
    return {"accepted": u1, "rejected": u2}


def _write_viz_case(sim_root: Path) -> Dict[str, str]:
    """Write viz/applied_commands_log.jsonl with one record."""
    p = sim_root / "viz" / "applied_commands_log.jsonl"
    u = str(uuid.uuid4())
    rec = {
        "schema_version": "applied_cmd_v1",
        "command_uuid": u,
        "command_type": "PressureUpdateCommand",
        "apply_phase": "post-selection-pre-mutation",
        "applied_generation": 7,
        "payload_hash": "sha256:beadfeed",
        "ack_status": "ACKED",
        "timestamp": _now_utc_iso(),
        "source": "viz",
    }
    _w(p, json.dumps(rec, ensure_ascii=False) + "\n")
    return {"viz": u}


def _write_acks_case(sim_root: Path) -> Dict[str, str]:
    """Write acks/ack_*.json + acks/ack_inbox_reject__*.json."""
    acks = sim_root / "acks"
    u_ok = str(uuid.uuid4())
    u_rej = str(uuid.uuid4())
    _write_json(
        acks / f"ack_{u_ok}.json",
        {
            "schema_version": "ack_v1",
            "command_uuid": u_ok,
            "status": "ACKED",
            "reason": "",
        },
    )
    _write_json(
        acks / f"ack_inbox_reject__{uuid.uuid4().hex[:8]}.json",
        {
            "schema_version": "ack_v1",
            "command_uuid": u_rej,
            "status": "REJECTED",
            "reason": "invalid apply_phase",
        },
    )
    return {"ack_ok": u_ok, "ack_rej": u_rej}


def _clear_sources(sim_root: Path) -> None:
    """Remove events/viz files to force fallback selection."""
    for p in [
        sim_root / "events" / "command_events.jsonl",
        sim_root / "viz" / "applied_commands_log.jsonl",
    ]:
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass


def _call_applied(base_url: str, run_key: str) -> Dict[str, Any]:
    """Call WebUI applied_commands endpoint."""
    rk = quote(run_key, safe="")
    url = f"{base_url.rstrip('/')}/api/run/{rk}/applied_commands?limit=200"
    return _http_get_json(url)


def _expect_uuids(payload: Dict[str, Any], uuids: List[str], *, label: str) -> None:
    """Assert payload includes all uuids in items[]."""
    items = payload.get("items")
    _assert(isinstance(items, list), f"{label}: items missing or not a list")
    seen = set()
    for it in items:
        if isinstance(it, dict) and it.get("command_uuid"):
            seen.add(str(it.get("command_uuid")))
    for u in uuids:
        _assert(u in seen, f"{label}: missing command_uuid {u}")


def _start_inproc_server(runs_root: Path) -> Tuple[str, Any]:
    """Start an in-process WebUI server on an ephemeral port.

    Returns (base_url, server) where server is a ThreadingHTTPServer.
    """
    import threading
    from infra.webui.server import WebUIConfig, WebUIRequestHandler, _ServerState  # type: ignore
    from http.server import ThreadingHTTPServer

    cfg = WebUIConfig(runs_root=Path(runs_root).resolve(), host="127.0.0.1", port=0, scan_depth=4)
    srv = ThreadingHTTPServer((cfg.host, 0), WebUIRequestHandler)
    srv.state = _ServerState(cfg)  # type: ignore[attr-defined]
    host, port = srv.server_address

    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    t.start()
    return f"http://{host}:{port}", srv


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint."""
    ap = argparse.ArgumentParser(description="Verify WebUI applied command visibility (headless)")
    ap.add_argument("--runs_root", required=True, help="Runs root used by WebUI")
    ap.add_argument("--webui_base_url", default="", help="Base URL for a running WebUI (if empty, start in-process)")
    ap.add_argument("--run_relpath", default="", help="Optional run relpath under runs_root (default: temp)")
    args = ap.parse_args(argv)

    runs_root = Path(args.runs_root).expanduser().resolve()
    runs_root.mkdir(parents=True, exist_ok=True)

    base_url = str(args.webui_base_url or "").strip()
    srv = None
    if not base_url:
        base_url, srv = _start_inproc_server(runs_root)

    # Create temp run.
    sim_root, run_key = _make_min_run(runs_root, args.run_relpath)

    failures: List[str] = []

    def _run_case(name: str, fn) -> None:
        try:
            fn()
            print(f"PASS: {name}")
        except Exception as e:
            failures.append(f"FAIL: {name} :: {e}")
            print(f"FAIL: {name} :: {e}")

    def case_events() -> None:
        _clear_sources(sim_root)
        # Clear acks to ensure we are validating events path.
        for p in (sim_root / "acks").glob("*.json"):
            try:
                p.unlink()
            except Exception:
                pass
        uu = _write_events_case(sim_root)
        # Give server a moment to see filesystem.
        time.sleep(0.2)
        payload = _call_applied(base_url, run_key)
        _assert(int(payload.get("parse_errors", 0) or 0) == 0, "events: parse_errors nonzero")
        _expect_uuids(payload, [uu["accepted"], uu["rejected"]], label="events")

    def case_viz() -> None:
        _clear_sources(sim_root)
        for p in (sim_root / "acks").glob("*.json"):
            try:
                p.unlink()
            except Exception:
                pass
        uu = _write_viz_case(sim_root)
        time.sleep(0.2)
        payload = _call_applied(base_url, run_key)
        _expect_uuids(payload, [uu["viz"]], label="viz")

    def case_acks() -> None:
        _clear_sources(sim_root)
        for p in (sim_root / "acks").glob("*.json"):
            try:
                p.unlink()
            except Exception:
                pass
        uu = _write_acks_case(sim_root)
        time.sleep(0.2)
        payload = _call_applied(base_url, run_key)
        _expect_uuids(payload, [uu["ack_ok"], uu["ack_rej"]], label="acks")

    _run_case("events/command_events.jsonl (authoritative)", case_events)
    _run_case("viz/applied_commands_log.jsonl (fallback)", case_viz)
    _run_case("acks/*.json (fallback)", case_acks)

    # Cleanup in-process server.
    if srv is not None:
        try:
            srv.shutdown()
        except Exception:
            pass

    if failures:
        print("\n".join(failures))
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
