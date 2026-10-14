from __future__ import annotations

import io
import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest

from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_routes_and_main as routes


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)


def _bootstrap_host(root: Path) -> None:
    host.bootstrap_host_baseline(root)


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str) -> None:
        self.path = path
        self.headers = {}
        self.rfile = io.BytesIO(b"{}")
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg))
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def _read_payload(req: _DummyReq) -> dict:
    return json.loads(req.wfile.getvalue().decode("utf-8"))


def _update_state_in_subprocess(repo_root: str, writer: str, expected_generation: int, out_q: multiprocessing.queues.Queue) -> None:
    try:
        res = state.update_assistant_state(
            Path(repo_root),
            expected_generation=expected_generation,
            writer=writer,
            current_state="thinking",
            state_reason=f"updated by {writer}",
        )
    except Exception as exc:
        out_q.put({"ok": False, "writer": writer, "error": str(exc)})
        return
    out_q.put(
        {
            "ok": True,
            "writer": writer,
            "changed": bool(res.get("changed")),
            "generation": int(res["assistant_state"]["write_generation"]),
        }
    )


def test_assistant_identity_survives_reload(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    first = state.load_or_init_assistant_state(tmp_path)
    second = state.load_or_init_assistant_state(tmp_path)

    assert first["assistant_id"] == second["assistant_id"]
    assert second["current_state"] in state.ASSISTANT_ALLOWED_STATES


def test_assistant_state_rejects_incompatible_schema(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    root = tmp_path / "runtime" / "state" / "lab_assistant" / "assistant_state"
    root.mkdir(parents=True, exist_ok=True)
    (root / "assistant_state.json").write_text(json.dumps({"schema_version": "wrong"}), encoding="utf-8")

    with pytest.raises(state.AssistantStateError, match="incompatible schema_version"):
        state.load_or_init_assistant_state(tmp_path)


def test_assistant_events_are_append_only_and_stale_writes_are_rejected(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    current = state.load_or_init_assistant_state(tmp_path)
    res = state.update_assistant_state(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="test.writer",
        current_state="thinking",
        state_reason="working",
    )

    assert res["changed"] is True
    with pytest.raises(state.AssistantStateError, match="stale write rejected"):
        state.update_assistant_state(
            tmp_path,
            expected_generation=int(current["write_generation"]),
            writer="stale.writer",
            current_state="failed",
        )

    events = (tmp_path / "runtime" / "state" / "lab_assistant" / "assistant_state" / "assistant_events.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(events) >= 3
    assert "assistant_initialized" in events[0]
    assert "assistant_state_changed" in events[1] or "assistant_state_changed" in events[2]
    assert "assistant_write_rejected" in events[-1]


def test_precedence_resolver_is_explicit_and_chat_never_wins() -> None:
    task_truth = state.resolve_source_precedence(
        topic="task_truth",
        ledger_value="ledger_done",
        runtime_value="runtime_guess",
        chat_value="chat_claim",
    )
    assistant_state = state.resolve_source_precedence(
        topic="assistant_state",
        runtime_value="blocked",
        ledger_value="not_applicable",
        chat_value="looks_ok",
    )

    assert task_truth["winner_source"] == "ledger"
    assert task_truth["value"] == "ledger_done"
    assert task_truth["conflict"] is True
    assert "chat" in task_truth["conflict_sources"]
    assert assistant_state["winner_source"] == "runtime_state"
    assert assistant_state["value"] == "blocked"
    assert assistant_state["chat_is_canonical"] is False


def test_identity_conflict_is_logged_and_blocks_idle_state(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    canonical = state.load_or_init_assistant_state(tmp_path)
    root = tmp_path / "runtime" / "state" / "lab_assistant" / "assistant_state"
    extra = dict(canonical)
    extra["assistant_id"] = "assistant_conflict"
    (root / "assistant_state_extra.json").write_text(json.dumps(extra), encoding="utf-8")

    current = state.load_or_init_assistant_state(tmp_path)
    events = (root / "assistant_events.jsonl").read_text(encoding="utf-8")

    assert current["assistant_id"] == canonical["assistant_id"]
    assert current["current_state"] == "blocked"
    assert current["recovery_marker"]["kind"] == "identity_conflict_detected"
    assert "identity_conflict_detected" in events


def test_admin_lab_assistant_state_route_returns_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/state")
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "lab_assistant_state_snapshot_v1"
    assert payload["assistant_state"]["assistant_id"]
    assert payload["precedence_samples"]["task_truth"]["winner_source"] == "ledger"


def test_load_or_init_does_not_rebootstrap_host_truth(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    first = host.bootstrap_host_baseline(tmp_path)

    state.load_or_init_assistant_state(tmp_path)
    current = host.host_health_snapshot(tmp_path, initialize_if_missing=False)

    assert first["mode"] == "clean_boot"
    assert current["mode"] == "clean_boot"
    assert current["last_boot_id"] == first["boot_id"]


def test_missing_canonical_with_alternate_candidate_fails_closed(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    root = tmp_path / "runtime" / "state" / "lab_assistant" / "assistant_state"
    root.mkdir(parents=True, exist_ok=True)
    backup = {
        "schema_version": state.ASSISTANT_STATE_SCHEMA_VERSION,
        "assistant_id": "assistant_existing",
        "assistant_label": "Jeff",
        "current_state": "idle",
        "state_reason": "existing",
        "state_since_utc": "2026-03-12T00:00:00Z",
        "last_turn_utc": "",
        "recovery_marker": {},
        "write_generation": 0,
        "last_writer": "test",
        "last_written_utc": "2026-03-12T00:00:00Z",
        "task_register_generation_hint": None,
        "delegation_index_cache": {"schema_version": "assistant_delegation_index_cache_v1", "derived_only": True, "entries": []},
    }
    (root / "assistant_state_backup.json").write_text(json.dumps(backup), encoding="utf-8")

    with pytest.raises(state.AssistantStateError, match="candidates exist without canonical winner"):
        state.load_or_init_assistant_state(tmp_path)

    assert not (root / "assistant_state.json").exists()


def test_admin_lab_assistant_state_route_is_read_only_when_state_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/state")
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    payload = _read_payload(req)
    assert req._status == 409
    assert payload["assistant_state"] is None
    assert not (tmp_path / "runtime" / "state" / "lab_assistant" / "assistant_state" / "assistant_state.json").exists()


def test_interprocess_cas_rejects_stale_writer(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    current = state.load_or_init_assistant_state(tmp_path)
    ctx = multiprocessing.get_context("fork")
    out_q = ctx.Queue()
    args = (str(tmp_path), int(current["write_generation"]), out_q)
    p1 = ctx.Process(target=_update_state_in_subprocess, args=(args[0], "writer.one", args[1], out_q))
    p2 = ctx.Process(target=_update_state_in_subprocess, args=(args[0], "writer.two", args[1], out_q))

    p1.start()
    p2.start()
    p1.join(timeout=10)
    p2.join(timeout=10)

    assert p1.exitcode == 0
    assert p2.exitcode == 0

    results = [out_q.get(timeout=5), out_q.get(timeout=5)]
    successes = [item for item in results if item["ok"]]
    failures = [item for item in results if not item["ok"]]

    assert len(successes) == 1
    assert len(failures) == 1
    assert "stale write rejected" in failures[0]["error"]

    final = state.load_or_init_assistant_state(tmp_path)
    assert int(final["write_generation"]) == int(current["write_generation"]) + 1
    assert final["last_writer"] == successes[0]["writer"]


def test_malformed_event_rows_fail_closed_on_snapshot_reads(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    events_path = tmp_path / "runtime" / "state" / "lab_assistant" / "assistant_state" / "assistant_events.jsonl"
    with events_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("{not-json}\n")
        handle.write('{"schema_version":"lab_assistant_event_v1","event":"truncated"\n')

    with pytest.raises(state.AssistantStateError, match="assistant_events.jsonl is malformed at line 2"):
        state.read_assistant_state_snapshot(tmp_path, initialize_if_missing=False)


def test_malformed_alternate_identity_candidate_fails_closed(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    root = tmp_path / "runtime" / "state" / "lab_assistant" / "assistant_state"
    (root / "assistant_state_extra.json").write_text('{"schema_version":"lab_assistant_state_v1"', encoding="utf-8")

    with pytest.raises(state.AssistantStateError, match="assistant_state_extra.json is malformed"):
        state.load_or_init_assistant_state(tmp_path)


def test_identity_conflict_blocks_non_idle_state(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    current = state.load_or_init_assistant_state(tmp_path)
    updated = state.update_assistant_state(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="test.writer",
        current_state="thinking",
        state_reason="working",
    )["assistant_state"]
    root = tmp_path / "runtime" / "state" / "lab_assistant" / "assistant_state"
    extra = dict(updated)
    extra["assistant_id"] = "assistant_conflict"
    (root / "assistant_state_extra.json").write_text(json.dumps(extra), encoding="utf-8")

    current = state.load_or_init_assistant_state(tmp_path)

    assert current["current_state"] == "blocked"
    assert current["state_reason"] == "assistant identity conflict detected"
    assert current["recovery_marker"]["kind"] == "identity_conflict_detected"


def test_truncated_state_json_fails_closed_for_loader_and_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    _bootstrap_host(tmp_path)
    root = tmp_path / "runtime" / "state" / "lab_assistant" / "assistant_state"
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "assistant_state.json"
    state_path.write_text('{"schema_version":"lab_assistant_state_v1","assistant_id":"assistant_bad"', encoding="utf-8")

    with pytest.raises(state.AssistantStateError, match="assistant_state.json is malformed"):
        state.load_or_init_assistant_state(tmp_path)

    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/state")
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    payload = _read_payload(req)
    assert req._status == 409
    assert payload["assistant_state"] is None
    assert payload["ok"] is False
    assert "assistant_state.json is malformed" in payload["error"]
