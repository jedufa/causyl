from __future__ import annotations

import argparse
import urllib.error

from tools.bin import agents_orchestrator_cli as cli


def test_http_json_timeout_returns_structured_error(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(cli.urllib.request, "urlopen", _boom)
    out = cli._http_json(method="GET", url="http://127.0.0.1:9999/api/x", request_timeout_s=11)
    assert out["ok"] is False
    assert str(out.get("code")) == "CLIENT_TIMEOUT"
    assert "11" in str(out.get("error") or "")


def test_http_json_connection_error_returns_structured_error(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(cli.urllib.request, "urlopen", _boom)
    out = cli._http_json(method="GET", url="http://127.0.0.1:9999/api/x")
    assert out["ok"] is False
    assert str(out.get("code")) == "CONNECTION_ERROR"
    assert "connection" in str(out.get("error") or "").lower()


def test_parser_exposes_request_timeout_argument() -> None:
    ap = cli._parser()
    args = ap.parse_args(["--request-timeout-s", "123", "list"])
    assert int(args.request_timeout_s) == 123


def test_run_parser_exposes_live_heartbeat_argument() -> None:
    ap = cli._parser()
    args = ap.parse_args(["run", "--workflow-id", "wf-abc", "--live-heartbeat-s", "7"])
    assert int(args.live_heartbeat_s) == 7


def test_start_parser_defaults_max_iterations_to_four() -> None:
    ap = cli._parser()
    args = ap.parse_args(["start"])
    assert int(args.max_iterations) == 4


def test_start_accepts_optional_pis_first_args(monkeypatch) -> None:
    seen = {}

    def _fake_http_json(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(cli, "_http_json", _fake_http_json)
    ns = argparse.Namespace(
        base_url="http://127.0.0.1:8000",
        request_timeout_s=120,
        binding_type="ledger_uid",
        target="",
        binding_id="PIS-20260305-130935Z-34B2",
        phase_label="",
        objective="",
        subphases="",
        pis_subphase_count=4,
        execution_directive="PIS has 4 sub-phases",
        max_iterations=3,
        heartbeat_interval_s=60,
        timeout_s=7000,
    )
    out = cli.cmd_start(ns)
    assert out["ok"] is True
    body = seen.get("body") if isinstance(seen.get("body"), dict) else {}
    assert body.get("phase_label") == ""
    assert body.get("objective") == ""
    opts = body.get("options") if isinstance(body.get("options"), dict) else {}
    assert int(opts.get("pis_subphase_count") or 0) == 4
    assert str(opts.get("execution_directive") or "") == "PIS has 4 sub-phases"
    assert "subphases" not in opts


def test_start_shortcut_target_defaults_to_ledger_uid(monkeypatch) -> None:
    seen = {}

    def _fake_http_json(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(cli, "_http_json", _fake_http_json)
    ns = argparse.Namespace(
        base_url="http://127.0.0.1:8000",
        request_timeout_s=120,
        binding_type="ledger_uid",
        target="PIS-20260305-130935Z-34B2",
        binding_id="",
        phase_label="",
        objective="",
        subphases="",
        pis_subphase_count=None,
        execution_directive="",
        max_iterations=3,
        heartbeat_interval_s=60,
        timeout_s=7000,
    )
    out = cli.cmd_start(ns)
    assert out["ok"] is True
    body = seen.get("body") if isinstance(seen.get("body"), dict) else {}
    b = body.get("binding") if isinstance(body.get("binding"), dict) else {}
    assert b.get("type") == "ledger_uid"
    assert b.get("id") == "PIS-20260305-130935Z-34B2"


def test_run_shortcut_positional_workflow_id(monkeypatch) -> None:
    seen = {}

    def _fake_http_json(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(cli, "_http_json", _fake_http_json)
    ns = argparse.Namespace(
        base_url="http://127.0.0.1:8000",
        request_timeout_s=120,
        workflow_id="",
        workflow_id_pos="wf-abc",
        max_steps=20,
        heartbeat_interval_s=None,
        timeout_s=None,
        live_heartbeat_s=0,
    )
    out = cli.cmd_run(ns)
    assert out["ok"] is True
    body = seen.get("body") if isinstance(seen.get("body"), dict) else {}
    assert body.get("workflow_id") == "wf-abc"
