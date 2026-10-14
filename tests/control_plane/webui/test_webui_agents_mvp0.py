from __future__ import annotations

import io
import json
import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from infra.webui.ledger.ledger_manager import ChangeLedger
from infra.webui import server_agents as agents
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_routes_and_main as routes


def _write_prompt_tree(root: Path) -> None:
    p = root / "governance" / "agents" / "prompts"
    (p / "task_templates").mkdir(parents=True, exist_ok=True)
    (p / "_base.md").write_text("base prompt\n", encoding="utf-8")
    (p / "_all_codex.md").write_text("shared prompt\n", encoding="utf-8")
    (p / "role__builder_mobile.md").write_text("builder prompt\n", encoding="utf-8")
    (p / "role__verifier_mobile.md").write_text("assurer prompt\n", encoding="utf-8")
    (p / "role__generalist_mobile.md").write_text("generalist prompt\n", encoding="utf-8")
    (p / "role__orchestrator_mobile.md").write_text("orchestrator prompt\n", encoding="utf-8")
    (p / "task_templates" / "task__ledger_bound.md").write_text(
        "uid={uid} title={title} status={status}\n", encoding="utf-8"
    )


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


def _make_post_req(cfg: routes.WebUIConfig, path: str, payload: dict) -> _DummyReq:
    req = _DummyReq(cfg=cfg, path=path)
    req.rfile = io.BytesIO(json.dumps(payload).encode("utf-8"))
    req.headers = {"Content-Length": str(req.rfile.getbuffer().nbytes)}
    return req


def _wait_for(predicate, *, timeout_s: float = 5.0) -> None:
    deadline = time.time() + max(0.1, timeout_s)
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    assert predicate()


@pytest.fixture(autouse=True)
def _default_agents_backend_llm(monkeypatch) -> None:
    monkeypatch.setattr(agents, "_agents_backend", lambda: "llm")


def test_agents_task_session_send_to_and_handoff(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    # Session exists and is empty initially.
    s0 = agents.read_session(role="builder", binding={"type": "task_id", "id": "a7k"})
    assert s0["ok"] is True
    assert s0["session_count"] == 0

    # Send-to persists to target inbox.
    r = agents.send_to_role(
        from_role="builder",
        to_role="assurer",
        binding={"type": "task_id", "id": "a7k"},
        message="please verify",
        also_enqueue=True,
    )
    assert r["ok"] is True

    s1 = agents.read_session(role="assurer", binding={"type": "task_id", "id": "a7k"})
    assert s1["inbox_count"] == 1
    assert "please verify" in str(s1["inbox"][-1]["content"])

    # Handoff file is generated with required header fields.
    h = agents.export_handoff(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        selection={"mode": "last_n", "n": 12},
        title="Task handoff",
        status="READY",
        scope="mvp0 scope",
        inputs=["input-a"],
        outputs=["output-a"],
        next_action="Do next step.",
        verify=["pytest -q tests/control_plane/webui/test_webui_agents_mvp0.py"],
        notes="none",
    )
    assert h["ok"] is True
    hp = Path(h["artifact_path"])
    assert hp.exists()
    txt = hp.read_text(encoding="utf-8")
    assert "HANDOFF: YES" in txt
    assert "BINDING: task_id:a7k" in txt
    assert "ROLE: builder" in txt


def test_agents_send_message_and_ledger_handoff_event(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    # Mock GPT call path.
    seen: dict = {}

    def _fake_create_response(**kwargs):
        seen.update(kwargs)
        return {"output_text": "assistant reply"}

    monkeypatch.setattr(agents, "create_response", _fake_create_response)
    monkeypatch.setattr(agents, "extract_output_text", lambda resp: str(resp.get("output_text") or ""))
    (tmp_path / "README.md").write_text("repo readme marker\n", encoding="utf-8")

    ledger = ChangeLedger(repo_root=tmp_path)
    created = ledger.create_item(title="Agent test", slug="agent-test", pis_markdown="pis body")
    uid = str(created["uid"])

    rs = agents.send_message(
        role="builder",
        binding={"type": "ledger_uid", "id": uid},
        message="implement this from README.md",
        options={"model": "gpt-5", "reasoning_effort": "medium"},
    )
    assert rs["ok"] is True
    assert "assistant reply" in str(rs["assistant"]["content"])
    assert str(seen.get("model") or "") == "gpt-5"
    assert str(seen.get("reasoning_effort") or "") == "medium"
    assert "README.md" in str(seen.get("input_text") or "")
    assert bool(rs["assistant"].get("meta", {}).get("repo_context_enabled")) is True
    assert str(rs["assistant"].get("meta", {}).get("reply_route_mode") or "") == "cheap_reply"

    # Attach task data into ledger-bound artifacts.
    agents.send_to_role(
        from_role="generalist",
        to_role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="task data",
    )
    at = agents.attach_to_ledger(task_id="a7k", ledger_uid=uid, merge_strategy="copy")
    assert at["ok"] is True

    # Ledger-bound handoff writes artifact + appends agent_handoff_created event.
    ho = agents.export_handoff(
        role="builder",
        binding={"type": "ledger_uid", "id": uid},
        selection={"mode": "last_n", "n": 5},
        title="Ledger handoff",
        status="READY",
        scope="ledger scope",
        next_action="continue",
    )
    assert ho["ok"] is True
    assert ho["ledger_event"]["event_type"] == "agent_handoff_created"

    d = ledger.get_item_dir(uid)
    events_p = d / "events.jsonl"
    lines = [ln for ln in events_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    last = json.loads(lines[-1])
    assert str(last.get("type")) == "agent_handoff_created"


def test_send_message_attaches_blessed_intake_and_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    def _fake_create_response(**kwargs):
        return {"output_text": "assistant reply"}

    monkeypatch.setattr(agents, "create_response", _fake_create_response)
    monkeypatch.setattr(agents, "extract_output_text", lambda resp: str(resp.get("output_text") or ""))

    raw_request = "Implement a stable canonical task-id and fingerprint path in the intake helper."
    first = agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message=raw_request,
        options={"model": "gpt-5"},
    )
    second = agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message=raw_request,
        options={"model": "gpt-5"},
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert bool(first.get("operator_intake") and second.get("operator_intake"))
    first_intake = dict(first["operator_intake"])
    second_intake = dict(second["operator_intake"])
    assert bool(first_intake.get("ok")) is True
    assert bool(first_intake.get("persisted")) is True
    assert str(first_intake.get("task_id")) == str(second_intake.get("task_id"))

    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task_rows = [row for row in task_register["active_tasks"] if str(row.get("task_id")) == str(first_intake.get("task_id"))]
    assert len(task_rows) == 1
    assert bool(task_rows[0].get("request_fingerprint")) is True
    assert str(task_rows[0].get("source")) == "operator_intake"
    assert str(task_rows[0].get("intake_path")) == "canonical"


def test_send_message_uses_context_rich_reply_route_for_strategy_questions(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    seen: dict = {}

    def _fake_create_response(**kwargs):
        seen.update(kwargs)
        return {"output_text": "context-rich reply"}

    monkeypatch.setattr(agents, "create_response", _fake_create_response)
    monkeypatch.setattr(agents, "extract_output_text", lambda resp: str(resp.get("output_text") or ""))
    monkeypatch.setattr(
        agents._context_rich_reply,
        "build_context_rich_reply_material",
        lambda **_kwargs: {
            "route_mode": "context_rich_reply",
            "route_reasons": ["implementation_strategy_request", "ledger_bound_turn"],
            "pis_binding": {"uid": "PIS-TEST", "status": "draft"},
            "instructions_addendum": "Required sections:\n1. Likely Files/Modules\n2. Implementation Approach",
            "model_context_text": "PIS Binding:\n- uid: PIS-TEST\n\nRepository Context:\n- file: src/example.py",
            "repo_context_files": ["src/example.py", "tests/test_example.py"],
            "pipeline_context_used": True,
            "pipeline_error": "",
            "cache_hit": False,
            "likely_tests": ["tests/test_example.py"],
        },
    )

    ledger = ChangeLedger(repo_root=tmp_path)
    created = ledger.create_item(title="Agent test", slug="agent-test", pis_markdown="pis body")

    result = agents.send_message(
        role="builder",
        binding={"type": "ledger_uid", "id": str(created["uid"])},
        message="How would you implement this PIS?",
        options={"model": "gpt-5"},
    )

    assert result["ok"] is True
    meta = dict(result["assistant"]["meta"])
    assert meta["reply_route_mode"] == "context_rich_reply"
    assert meta["pipeline_context_used"] is True
    assert meta["repo_context_files"] == ["src/example.py", "tests/test_example.py"]
    assert meta["likely_tests"] == ["tests/test_example.py"]
    assert "Likely Files/Modules" in str(seen.get("instructions") or "")
    assert "PIS Binding:" in str(seen.get("input_text") or "")


def test_send_scheduled_message_bypasses_context_rich_reply_for_scheduled_turns(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    seen: dict = {}

    def _fake_create_response(**kwargs):
        seen.update(kwargs)
        return {"output_text": "scheduled builder output"}

    monkeypatch.setattr(agents, "create_response", _fake_create_response)
    monkeypatch.setattr(agents, "extract_output_text", lambda resp: str(resp.get("output_text") or ""))

    def _unexpected_context_rich_reply(**_kwargs):
        raise AssertionError("scheduled turns must bypass context-rich reply preparation")

    monkeypatch.setattr(
        agents._context_rich_reply,
        "build_context_rich_reply_material",
        _unexpected_context_rich_reply,
    )

    result = agents.send_scheduled_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="Scheduled builder contract body",
        options={"model": "gpt-5.4"},
    )

    assert result["ok"] is True
    assert str(seen.get("input_text") or "") == "Scheduled builder contract body"
    meta = dict(result["assistant"]["meta"])
    assert meta["reply_route_mode"] == "scheduled_execution_bypass"


def test_send_scheduled_message_forces_codex_backend_for_scheduled_codex_turns(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "llm")

    seen_backends: list[str] = []

    class _FakeMgr:
        def ensure_worker(self, **kwargs):
            seen_backends.append(str(agents.os.environ.get("EIEL_AGENTS_BACKEND") or ""))
            return {"ok": True, "started": True, "worker_id": "w1", "protocol": "oneshot"}

        def send_turn(self, **kwargs):
            seen_backends.append(str(agents.os.environ.get("EIEL_AGENTS_BACKEND") or ""))
            return {"ok": True, "worker_id": "w1", "protocol": "oneshot", "content": "scheduled codex reply", "exit_code": 0}

    monkeypatch.setattr(agents, "_codex_manager", lambda: _FakeMgr())

    result = agents.send_scheduled_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="Scheduled builder contract body",
        options={"agent_tech": "codex", "model": "gpt-5.3-codex"},
    )

    assert result["ok"] is True
    assert seen_backends == ["codex", "codex"]
    assert str(agents.os.environ.get("EIEL_AGENTS_BACKEND") or "") == "llm"
    assert "scheduled codex reply" in str(result["assistant"]["content"] or "")


def test_send_message_marks_execution_requests_for_escalation(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    seen: dict = {}

    def _fake_create_response(**kwargs):
        seen.update(kwargs)
        return {"output_text": "bounded explanation"}

    monkeypatch.setattr(agents, "create_response", _fake_create_response)
    monkeypatch.setattr(agents, "extract_output_text", lambda resp: str(resp.get("output_text") or ""))
    monkeypatch.setattr(
        agents._context_rich_reply,
        "build_context_rich_reply_material",
        lambda **_kwargs: {
            "route_mode": "escalate_to_task",
            "route_reasons": ["execution_verbs_present"],
            "pis_binding": {"uid": "PIS-TEST", "status": "draft"},
            "instructions_addendum": "Escalation Note:\n- this should become a managed task/run",
            "model_context_text": "Repository Context:\n- file: src/example.py",
            "repo_context_files": ["src/example.py"],
            "pipeline_context_used": False,
            "pipeline_error": "",
            "cache_hit": False,
            "likely_tests": [],
        },
    )

    ledger = ChangeLedger(repo_root=tmp_path)
    created = ledger.create_item(title="Agent test", slug="agent-test", pis_markdown="pis body")

    result = agents.send_message(
        role="builder",
        binding={"type": "ledger_uid", "id": str(created["uid"])},
        message="Implement this PIS now and patch the repo.",
        options={"model": "gpt-5"},
    )

    assert result["ok"] is True
    meta = dict(result["assistant"]["meta"])
    assert meta["reply_route_mode"] == "escalate_to_task"
    assert meta["reply_route_reasons"] == ["execution_verbs_present"]
    assert "managed task/run" in str(seen.get("instructions") or "")


def test_send_message_rejects_malformed_intake_before_persistence(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(agents._lab_intake, "analyze_request_intake", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("simulated malformed intake")))

    result = agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="simulate malformed intake path",
        options={"model": "gpt-5"},
    )

    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]

    assert result["ok"] is False
    assert result["reason"] == "malformed_input"
    assert result["operator_intake"]["ok"] is False
    assert result["operator_intake"]["persisted"] is False
    assert result["operator_intake"]["reason"] == "malformed_input"
    assert task_register["write_generation"] == 0
    assert task_register["active_tasks"] == []


def test_send_message_with_task_register_write_failure_does_not_mark_intake_accepted(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)
    before = register.load_or_init_task_register(tmp_path)

    def fail_write(*_args, **_kwargs) -> None:
        raise RuntimeError("simulated task register write failure")

    monkeypatch.setattr(register, "_write_json_atomic", fail_write)

    result = agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="Persist a deterministic task id and reject this write failure branch safely.",
        options={"model": "gpt-5"},
    )
    after = register.load_or_init_task_register(tmp_path)

    assert result["ok"] is False
    assert result["reason"] == "intake_capture_failed"
    assert result["operator_intake"]["ok"] is False
    assert result["operator_intake"]["persisted"] is False
    assert result["operator_intake"]["reason"] == "intake_capture_failed"
    assert after["write_generation"] == before["write_generation"]
    assert after["active_tasks"] == []


def test_mobile_bubble_projection_updates_for_known_generalist_agent(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    monkeypatch.setattr(agents, "create_response", lambda **kwargs: {"output_text": "assistant reply"})
    monkeypatch.setattr(agents, "extract_output_text", lambda resp: str(resp.get("output_text") or ""))

    rs = agents.send_message(
        role="generalist",
        binding={"type": "task_id", "id": "mbl"},
        message="hello mobile generalist",
        options={"model": "gpt-5"},
    )

    assert rs["ok"] is True
    bubble = tmp_path / "governance" / "agents" / "mobile_bubbles" / "generalist_a.md"
    assert bubble.exists()
    txt = bubble.read_text(encoding="utf-8")
    assert "task_id:mbl" in txt
    assert "hello mobile generalist" in txt
    assert "assistant reply" in txt


def test_attach_to_ledger_merges_without_overwriting_existing_session(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    created = ledger.create_item(title="Attach merge test", slug="attach-merge-test", pis_markdown="pis body")
    uid = str(created["uid"])
    item_dir = ledger.get_item_dir(uid)

    dst_chat = item_dir / "artifacts" / "agent_chat"
    dst_chat.mkdir(parents=True, exist_ok=True)
    dst_session = dst_chat / "session__builder.jsonl"
    dst_session.write_text(json.dumps({"content": "DEST_OLD"}) + "\n", encoding="utf-8")

    src_task = tmp_path / "governance" / "agents" / "sessions" / "a7k"
    src_task.mkdir(parents=True, exist_ok=True)
    src_session = src_task / "session__builder.jsonl"
    src_session.write_text(json.dumps({"content": "SRC_NEW"}) + "\n", encoding="utf-8")

    res = agents.attach_to_ledger(task_id="a7k", ledger_uid=uid, merge_strategy="copy")
    assert res["ok"] is True

    lines = [ln for ln in dst_session.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0]).get("content") == "DEST_OLD"
    assert json.loads(lines[1]).get("content") == "SRC_NEW"


def test_missing_prompt_raises_prompt_config_error(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    # Remove required role prompt to force explicit prompt config failure.
    (tmp_path / "governance" / "agents" / "prompts" / "role__builder_mobile.md").unlink()

    try:
        agents.send_message(
            role="builder",
            binding={"type": "task_id", "id": "a7k"},
            message="hello",
            options={"model": "gpt-5"},
        )
        assert False, "expected PromptConfigError"
    except agents.PromptConfigError as e:
        assert "missing prompt file" in str(e)


def test_legacy_prompt_names_redirect_to_mobile_files(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    builder_bundle = agents.load_prompt_bundle(
        "builder",
        {"type": "task_id", "id": "a7k"},
        prompt_file="role__builder.md",
    )
    generalist_bundle = agents.load_prompt_bundle(
        "generalist",
        {"type": "task_id", "id": "a7k"},
        prompt_file="role__generalist.md",
    )
    assurer_bundle = agents.load_prompt_bundle(
        "assurer",
        {"type": "task_id", "id": "a7k"},
        prompt_file="role__assurer.md",
    )

    assert builder_bundle["role"]["path"].endswith("role__builder_mobile.md")
    assert generalist_bundle["role"]["path"].endswith("role__generalist_mobile.md")
    assert assurer_bundle["role"]["path"].endswith("role__verifier_mobile.md")
    payload = agents.get_prompts_payload()
    assert payload["legacy_redirects"]["role__generalist.md"] == "role__generalist_mobile.md"
    assert payload["legacy_redirects"]["role__assurer.md"] == "role__verifier_mobile.md"


def test_legacy_generalist_prompt_alias_uses_generalist_mobile_in_workflow_mode(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    bundle = agents.load_prompt_bundle(
        "generalist",
        {"type": "task_id", "id": "a7k"},
        prompt_file="role__generalist.md",
        workflow=True,
    )

    assert bundle["role"]["path"].endswith("role__generalist_mobile.md")


def test_load_prompt_bundle_expands_shared_include_without_base_text(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    prompts = tmp_path / "governance" / "agents" / "prompts"
    (prompts / "_base.md").write_text("legacy base note\n", encoding="utf-8")
    (prompts / "_all_codex.md").write_text("shared rules\n", encoding="utf-8")
    (prompts / "role__builder_mobile.md").write_text(
        "{{include:_all_codex.md}}\nrole builder instructions\n",
        encoding="utf-8",
    )

    bundle = agents.load_prompt_bundle("builder", {"type": "task_id", "id": "a7k"})

    assert bundle["instructions"].startswith("shared rules")
    assert "role builder instructions" in str(bundle["instructions"])
    assert "legacy base note" not in str(bundle["instructions"])


def test_get_prompts_payload_exposes_all_codex_target(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    payload = agents.get_prompts_payload()

    assert payload["all_codex"]["text"] == "shared prompt\n"
    assert "all_codex" in payload["editable_targets"]


def test_read_prompt_file_redirects_missing_legacy_generalist_path(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    prompt = agents._read_prompt_file(tmp_path / "governance" / "agents" / "prompts" / "role__generalist.md")

    assert prompt["path"].endswith("role__generalist_mobile.md")


def test_canonical_prompt_file_redirects_legacy_names_without_alias_files() -> None:
    assert agents.canonical_prompt_file("role__generalist.md", role="generalist", workflow=True) == "role__generalist_mobile.md"
    assert agents.canonical_prompt_file("role__assurer.md", role="assurer", workflow=True) == "role__verifier_mobile.md"
    assert (
        agents.canonical_prompt_file(
            "/home/user/causyl/governance/agents/prompts/role__generalist.md",
            role="generalist",
            workflow=True,
        )
        == "role__generalist_mobile.md"
    )
    assert agents.canonical_prompt_file("", role="generalist", workflow=True) == "role__generalist_mobile.md"


def test_stream_send_persists_assistant_row_and_clears_stream_state(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)
    (tmp_path / "NOTES.md").write_text("stream repo marker\n", encoding="utf-8")

    seen: dict = {}

    def _fake_stream_response_events(**kwargs):
        seen.update(kwargs)
        yield {"type": "response.output_text.delta", "delta": "hello "}
        yield {"type": "response.output_text.delta", "delta": "world"}
        yield {"type": "response.completed", "response": {"output_text": "hello world"}}

    monkeypatch.setattr(agents, "stream_response_events", _fake_stream_response_events)

    evts = list(
        agents.send_message_stream_events(
            role="builder",
            binding={"type": "task_id", "id": "a7k"},
            message="stream this and inspect NOTES.md",
            options={"model": "gpt-5.4", "reasoning_effort": "minimal"},
        )
    )
    assert any(str(e.get("type")) == "delta" for e in evts)
    assert str(evts[-1].get("type")) == "done"
    assert bool(evts[-1].get("ok")) is True

    s = agents.read_session(role="builder", binding={"type": "task_id", "id": "a7k"})
    assert s["session_count"] == 2
    assert "hello world" in str(s["session"][-1]["content"])
    assert s.get("stream_state") in ({}, None)
    assert str(seen.get("model") or "") == "gpt-5.4"
    assert str(seen.get("reasoning_effort") or "") == "minimal"
    assert "Repository workspace context:" in str(seen.get("input_text") or "")
    assert "NOTES.md" in str(seen.get("input_text") or "")

def test_send_message_stream_events_error_done_includes_operator_intake(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    def _broken_stream_response_events(**_kwargs):
        raise RuntimeError("simulated stream failure")

    monkeypatch.setattr(agents, "stream_response_events", _broken_stream_response_events)

    events = list(
        agents.send_message_stream_events(
            role="builder",
            binding={"type": "task_id", "id": "a7k"},
            message="Review and repair the stream failure path in live intake.",
            options={"model": "gpt-5"},
        )
    )

    done = events[-1]
    assert str(done.get("type")) == "done"
    assert bool(done.get("ok")) is False
    assert bool(done.get("operator_intake")) is True
    assert done["operator_intake"].get("ok") is True
    assert bool(done["operator_intake"].get("persisted")) is True


def test_mobile_chat_cancel_clears_stream_state_and_persists_cancelled_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_agents_backend", lambda: "codex")
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_LOADED", False)
    monkeypatch.setattr(routes, "_MOBILE_ACTIVE_STREAMS", {})

    streamed = threading.Event()
    release = threading.Event()
    cancel_calls: list[tuple[dict, str, str]] = []

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            return None

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            yield {"type": "delta", "delta": "partial", "content": "partial"}
            streamed.set()
            assert release.wait(timeout=5)
            yield {"type": "done", "ok": False, "error": "cancelled"}

        def stop_worker(self, *, binding, role, reason):
            cancel_calls.append((dict(binding), str(role), str(reason)))
            release.set()
            return {"ok": True, "stopped": True, "worker_id": "mbl:generalist"}

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    payloads = {
        "/api/mobile/chat/stream": {"message": "hello", "agent_key": "a"},
        "/api/mobile/chat/cancel": {"thread_id": "mbl"},
    }
    monkeypatch.setattr(routes, "_read_request_json", lambda handler, max_bytes=0: dict(payloads[handler.path]))

    stream_req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    stream_thread = threading.Thread(target=routes.WebUIRequestHandler.do_POST, args=(stream_req,))  # type: ignore[arg-type]
    stream_thread.start()
    assert streamed.wait(timeout=5)
    _wait_for(lambda: (routes._mobile_session_state_payload("mbl").get("stream_state") or {}).get("content") == "partial")

    active_state = routes._mobile_session_state_payload("mbl")
    assert active_state["stream_state"]["status"] == "running"
    assert active_state["stream_state"]["content"] == "partial"

    cancel_req = _DummyReq(cfg=cfg, path="/api/mobile/chat/cancel")
    routes.WebUIRequestHandler.do_POST(cancel_req)  # type: ignore[arg-type]
    cancel_payload = _read_payload(cancel_req)

    assert cancel_req._status == 200
    assert str(cancel_payload.get("status") or "") == "cancelled"
    assert str(cancel_payload.get("message") or "") == "Server turn cancelled."
    assert cancel_calls == [({"type": "task_id", "id": "mbl"}, "generalist", "cancelled from mobile")]

    stream_thread.join(timeout=5)
    assert not stream_thread.is_alive()

    final_state = routes._mobile_session_state_payload("mbl")
    assert final_state["stream_state"] is None
    assert final_state["history"][-1] == {"role": "assistant", "content": "partial", "status": "cancelled"}

    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_LOADED", False)
    routes._mobile_sessions_init(cfg)

    restored = routes._mobile_session_history_payload("mbl")
    assert restored["history"][-1] == {"role": "assistant", "content": "partial", "status": "cancelled"}


def test_mobile_chat_interruption_persists_non_success_terminal_state_across_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_agents_backend", lambda: "codex")
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_LOADED", False)
    monkeypatch.setattr(routes, "_MOBILE_ACTIVE_STREAMS", {})

    streamed = threading.Event()
    release = threading.Event()

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            return None

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            yield {"type": "delta", "delta": "partial", "content": "partial"}
            streamed.set()
            assert release.wait(timeout=5)
            return

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    monkeypatch.setattr(routes, "_read_request_json", lambda handler, max_bytes=0: {"message": "hello", "agent_key": "a"})

    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    thread = threading.Thread(target=routes.WebUIRequestHandler.do_POST, args=(req,))  # type: ignore[arg-type]
    thread.start()
    assert streamed.wait(timeout=5)
    _wait_for(lambda: (routes._mobile_session_state_payload("mbl").get("stream_state") or {}).get("content") == "partial")
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    payload = routes._mobile_session_state_payload("mbl")
    assert payload["stream_state"] is None
    assert payload["history"][-1] == {"role": "assistant", "content": "partial", "status": "failed"}

    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_LOADED", False)
    routes._mobile_sessions_init(cfg)

    restored = routes._mobile_session_history_payload("mbl")
    assert restored["history"][-1] == {"role": "assistant", "content": "partial", "status": "failed"}


def test_mobile_chat_cancel_requested_persists_explicit_stop_race_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_agents_backend", lambda: "codex")
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_LOADED", False)
    monkeypatch.setattr(routes, "_MOBILE_ACTIVE_STREAMS", {})
    monkeypatch.setattr(routes, "_MOBILE_CANCEL_WAIT_S", 0.01)

    streamed = threading.Event()
    release = threading.Event()
    cancel_calls: list[tuple[dict, str, str]] = []

    class _Mgr:
        def ensure_worker(self, *, binding, role, bootstrap_text):
            return None

        def stream_turn(self, *, binding, role, message, timeout_s, model="", reasoning_effort=""):
            yield {"type": "delta", "delta": "partial", "content": "partial"}
            streamed.set()
            assert release.wait(timeout=5)
            time.sleep(0.05)
            yield {"type": "done", "ok": True, "content": "partial complete"}

        def stop_worker(self, *, binding, role, reason):
            cancel_calls.append((dict(binding), str(role), str(reason)))
            release.set()
            return {"ok": True, "stopped": True, "worker_id": "mbl:generalist"}

    monkeypatch.setattr(routes._agents, "_codex_manager", lambda: _Mgr())

    payloads = {
        "/api/mobile/chat/stream": {"message": "hello", "agent_key": "a"},
        "/api/mobile/chat/cancel": {"thread_id": "mbl"},
    }
    monkeypatch.setattr(routes, "_read_request_json", lambda handler, max_bytes=0: dict(payloads[handler.path]))

    stream_req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    stream_thread = threading.Thread(target=routes.WebUIRequestHandler.do_POST, args=(stream_req,))  # type: ignore[arg-type]
    stream_thread.start()
    assert streamed.wait(timeout=5)
    _wait_for(lambda: (routes._mobile_session_state_payload("mbl").get("stream_state") or {}).get("content") == "partial")

    cancel_req = _DummyReq(cfg=cfg, path="/api/mobile/chat/cancel")
    routes.WebUIRequestHandler.do_POST(cancel_req)  # type: ignore[arg-type]
    cancel_payload = _read_payload(cancel_req)

    assert cancel_req._status == 200
    assert str(cancel_payload.get("status") or "") == "cancel_requested"
    assert cancel_calls == [({"type": "task_id", "id": "mbl"}, "generalist", "cancelled from mobile")]

    stream_thread.join(timeout=5)
    assert not stream_thread.is_alive()

    final_state = routes._mobile_session_state_payload("mbl")
    assert final_state["stream_state"] is None
    assert final_state["history"][-1] == {"role": "assistant", "content": "partial complete"}

    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_LOADED", False)
    routes._mobile_sessions_init(cfg)

    restored = routes._mobile_session_history_payload("mbl")
    assert restored["history"][-1] == {"role": "assistant", "content": "partial complete"}


def test_stream_send_surfaces_agent_supplied_chat_header_title(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    def _fake_stream_response_events(**kwargs):
        yield {"type": "response.output_text.delta", "delta": "Ship the fix.\n"}
        yield {"type": "response.output_text.delta", "delta": "CHAT_HEADER_TITLE: Admin Toggle Fix"}
        yield {
            "type": "response.completed",
            "response": {"output_text": "Ship the fix.\nCHAT_HEADER_TITLE: Admin Toggle Fix"},
        }

    monkeypatch.setattr(agents, "stream_response_events", _fake_stream_response_events)

    evts = list(
        agents.send_message_stream_events(
            role="builder",
            binding={"type": "task_id", "id": "a7k"},
            message="stream this",
            options={"model": "gpt-5"},
        )
    )

    done = evts[-1]
    assert str(done.get("type")) == "done"
    assert str(done.get("assistant_title")) == "Admin Toggle Fix"
    session = agents.read_session(role="builder", binding={"type": "task_id", "id": "a7k"})
    assert str(session["session"][-1]["content"]) == "Ship the fix."
    assert str(session["session"][-1]["meta"]["chat_header_title"]) == "Admin Toggle Fix"


def test_send_message_dispatches_direct_host_verification_request(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    monkeypatch.setattr(
        agents,
        "create_response",
        lambda **kwargs: {
            "output_text": (
                "Need host proof.\n"
                "HOST_VERIFICATION_REQUEST_JSON:\n"
                '{"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest","test_selector":"com.eiel.chat.HostVerificationSentinelTest"},"reason":"direct agent proof"}'
            )
        },
    )
    monkeypatch.setattr(agents, "extract_output_text", lambda resp: str(resp.get("output_text") or ""))

    seen: dict = {}

    def _fake_run_host_verification_request(request: dict) -> dict:
        seen["request"] = request
        return {
            "request_id": "vrf_direct_1",
            "workflow_id": str(request.get("workflow_id") or ""),
            "ok": True,
            "status": "passed",
            "action_id": "android_unit_test",
            "started_utc": "2026-03-09T00:00:00Z",
            "finished_utc": "2026-03-09T00:00:02Z",
            "duration_ms": 2000,
            "exit_code": 0,
            "cwd": str(tmp_path / "mobile" / "android"),
            "argv": ["./gradlew", ":app:testDebugUnitTest", "--tests", "com.eiel.chat.HostVerificationSentinelTest"],
            "evidence": {
                "stdout_path": str(tmp_path / "stdout.txt"),
                "stderr_path": str(tmp_path / "stderr.txt"),
                "artifact_paths": [str(tmp_path / "artifact.xml")],
                "summary": "sentinel passed",
            },
            "failure_kind": "",
        }

    monkeypatch.setattr(agents, "_run_host_verification_request", _fake_run_host_verification_request)

    res = agents.send_message(
        role="generalist",
        binding={"type": "task_id", "id": "mbl"},
        message="patch and verify",
        options={"model": "gpt-5"},
    )

    assert res["ok"] is True
    assert bool(res["host_verification_requested"]) is True
    assert bool(res["host_verification_result"]["ok"]) is True
    assert "Host verification result:" in str(res["assistant_followup"]["content"])
    assert str(seen["request"]["requested_by_role"]) == "generalist"
    assert str(((seen["request"].get("action") or {}).get("params") or {}).get("test_selector")) == "com.eiel.chat.HostVerificationSentinelTest"

    session = agents.read_session(role="generalist", binding={"type": "task_id", "id": "mbl"})
    assert int(session["session_count"]) == 3
    assert str(session["session"][1]["content"]) == "Need host proof."
    assert "sentinel passed" in str(session["session"][-1]["content"])
    assert "stdout_path" not in str(session["session"][-1]["content"])


def test_send_message_dispatches_host_verification_via_server_worker(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    monkeypatch.setattr(
        agents,
        "create_response",
        lambda **kwargs: {
            "output_text": (
                "Need host proof.\n"
                "HOST_VERIFICATION_REQUEST_JSON:\n"
                '{"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest","test_selector":"com.eiel.chat.HostVerificationSentinelTest"},"reason":"server worker proof"}'
            )
        },
    )
    monkeypatch.setattr(agents, "extract_output_text", lambda resp: str(resp.get("output_text") or ""))

    seen: dict = {}

    def _fake_run_host_verification_request_inline(request: dict) -> dict:
        seen["thread_name"] = threading.current_thread().name
        seen["request"] = request
        return {
            "request_id": "vrf_direct_worker_1",
            "workflow_id": str(request.get("workflow_id") or ""),
            "ok": True,
            "status": "passed",
            "action_id": "android_unit_test",
            "started_utc": "2026-03-09T00:00:00Z",
            "finished_utc": "2026-03-09T00:00:02Z",
            "duration_ms": 2000,
            "exit_code": 0,
            "cwd": str(tmp_path / "mobile" / "android"),
            "argv": ["./gradlew", ":app:testDebugUnitTest", "--tests", "com.eiel.chat.HostVerificationSentinelTest"],
            "evidence": {
                "stdout_path": str(tmp_path / "stdout.txt"),
                "stderr_path": str(tmp_path / "stderr.txt"),
                "artifact_paths": [str(tmp_path / "artifact.xml")],
                "summary": "sentinel passed via worker",
            },
            "failure_kind": "",
            "runner": {"host": "local", "runner_version": "v1"},
        }

    monkeypatch.setattr(agents, "_run_host_verification_request_inline", _fake_run_host_verification_request_inline)

    agents.configure_host_verification_dispatch(mode="server_worker")
    try:
        res = agents.send_message(
            role="generalist",
            binding={"type": "task_id", "id": "mbl"},
            message="patch and verify through the server worker",
            options={"model": "gpt-5"},
        )
    finally:
        agents.configure_host_verification_dispatch(mode="inline")

    assert res["ok"] is True
    assert bool(res["host_verification_requested"]) is True
    assert bool(res["host_verification_result"]["ok"]) is True
    assert str((res["host_verification_result"].get("runner") or {}).get("dispatch") or "") == "server_worker"
    assert str(seen["thread_name"]) == "webui_host_verification_worker"
    assert str(seen["request"]["requested_by_role"]) == "generalist"
    job_dir = Path(str((seen["request"].get("artifacts") or {}).get("root_dir") or "")).resolve()
    state = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    assert str(state["status"]) == "finished"
    assert str(state["phase"]) == "completed"
    assert str(state["runner_dispatch"]) == "server_worker"
    assert int(state["timeout_s"]) == 3600
    assert str(state["timeout_source"]) == "request_policy"
    assert str(state["progress_note"]) != ""
    assert str(state["deadline_utc"]) != ""
    assert int(state["remaining_s"]) >= 0
    assert str(state["job_dir"]) == str(job_dir)
    assert str(state["request_path"]).endswith("/request.json")
    assert str(state["result_path"]).endswith("/result.json")
    trace_rows = agents._read_jsonl(job_dir / "trace.jsonl")
    assert any(str(row.get("event") or "") == "dispatch_enqueued_for_worker" for row in trace_rows)
    assert any(str(row.get("event") or "") == "filesystem_dispatch_finished" for row in trace_rows)
    worker_status = agents._read_json(agents._host_verification_worker_status_path())
    assert str(worker_status.get("state") or "") in {"disabled", "running", "idle", "scan_complete"}

    session = agents.read_session(role="generalist", binding={"type": "task_id", "id": "mbl"})
    assert int(session["session_count"]) == 3
    assert str(session["session"][1]["content"]) == "Need host proof."
    assert "sentinel passed via worker" in str(session["session"][-1]["content"])
    assert "artifact_paths" not in str(session["session"][-1]["content"])


def test_extract_host_verification_request_accepts_timeout_override() -> None:
    payload = agents._extract_host_verification_request(
        "Need host proof.\n"
        "HOST_VERIFICATION_REQUEST_JSON:\n"
        '{"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest","test_selector":"com.eiel.chat.HostVerificationSentinelTest"},"reason":"slow host run","timeout_s":3600}'
    )

    assert str(payload["action_id"]) == "android_unit_test"
    assert str((payload.get("params") or {}).get("test_selector") or "") == "com.eiel.chat.HostVerificationSentinelTest"
    assert int(payload["timeout_s"]) == 3600


def test_extract_host_verification_request_caps_timeout_override_at_one_hour() -> None:
    payload = agents._extract_host_verification_request(
        "Need host proof.\n"
        "HOST_VERIFICATION_REQUEST_JSON:\n"
        '{"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest","test_selector":"com.eiel.chat.HostVerificationSentinelTest"},"reason":"very slow host run","timeout_s":99999}'
    )

    assert int(payload["timeout_s"]) == 3600


def test_extract_host_verification_request_accepts_stall_timeout_override() -> None:
    payload = agents._extract_host_verification_request(
        "Need host proof.\n"
        "HOST_VERIFICATION_REQUEST_JSON:\n"
        '{"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest","test_selector":"com.eiel.chat.HostVerificationSentinelTest"},"reason":"slow host run","timeout_s":3600,"stall_timeout_s":1200}'
    )

    assert int(payload["timeout_s"]) == 3600
    assert int(payload["stall_timeout_s"]) == 1200


def test_send_message_rejects_unscoped_android_host_verification_request(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    monkeypatch.setattr(
        agents,
        "create_response",
        lambda **kwargs: {
            "output_text": (
                "Need host proof.\n"
                "HOST_VERIFICATION_REQUEST_JSON:\n"
                '{"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest"},"reason":"broad proof"}'
            )
        },
    )
    monkeypatch.setattr(agents, "extract_output_text", lambda resp: str(resp.get("output_text") or ""))

    res = agents.send_message(
        role="generalist",
        binding={"type": "task_id", "id": "mbl"},
        message="patch and verify",
        options={"model": "gpt-5"},
    )

    assert res["ok"] is True
    assert bool(res["host_verification_requested"]) is True
    assert bool(res["host_verification_result"]["ok"]) is False
    assert str(res["host_verification_result"]["failure_kind"]) == "invalid_request"
    assert "test_selector or test_selectors" in str(res["assistant_followup"]["content"])


def test_server_worker_recovers_filesystem_queued_host_verification_job(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    request = {
        "workflow_id": "agent-direct-task_id-mbl-generalist",
        "turn": "direct_agent_reply",
        "requested_by_role": "generalist",
        "binding": {"type": "task_id", "id": "mbl"},
        "action": {
            "action_id": "android_unit_test",
            "params": {
                "gradle_task": ":app:testDebugUnitTest",
                "test_selector": "com.eiel.chat.HostVerificationSentinelTest",
            },
        },
        "artifacts": {"capture_stdout": True, "capture_stderr": True},
        "policy": {"timeout_s": 3600, "allow_network": False, "require_clean_exit": True},
        "reason": "recover queued host verification job",
        "created_utc": "2026-03-09T00:00:00Z",
    }
    normalized = agents._verification_contract.normalize_request(request, repo=tmp_path)
    job_dir = Path(str((normalized.get("artifacts") or {}).get("root_dir") or "")).resolve()
    agents._verification_contract.write_json_atomic((job_dir / "request.json").resolve(), normalized)
    agents._write_host_verification_state(
        job_dir,
        status="queued",
        request=normalized,
        message="queued before web server restart",
        runner_dispatch="server_worker",
    )

    seen: dict = {}

    def _fake_run_host_verification_request_inline(request: dict) -> dict:
        seen["request"] = request
        return {
            "request_id": str(request.get("request_id") or ""),
            "workflow_id": str(request.get("workflow_id") or ""),
            "ok": True,
            "status": "passed",
            "action_id": "android_unit_test",
            "started_utc": "2026-03-09T00:00:00Z",
            "finished_utc": "2026-03-09T00:00:02Z",
            "duration_ms": 2000,
            "exit_code": 0,
            "cwd": str(tmp_path / "mobile" / "android"),
            "argv": ["./gradlew", ":app:testDebugUnitTest", "--tests", "com.eiel.chat.HostVerificationSentinelTest"],
            "evidence": {
                "stdout_path": str(tmp_path / "stdout.txt"),
                "stderr_path": str(tmp_path / "stderr.txt"),
                "artifact_paths": [str(tmp_path / "artifact.xml")],
                "summary": "recovered queued verification passed",
            },
            "failure_kind": "",
            "runner": {"host": "local", "runner_version": "v1"},
        }

    monkeypatch.setattr(agents, "_run_host_verification_request_inline", _fake_run_host_verification_request_inline)

    assert agents._process_next_filesystem_host_verification_job(dispatch="server_worker") is True
    assert str(seen["request"]["request_id"]) == str(normalized["request_id"])

    state = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    result = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
    assert str(state["status"]) == "finished"
    assert str(state["phase"]) == "completed"
    assert str(state["runner_dispatch"]) == "server_worker"
    assert str(((result.get("runner") or {}).get("dispatch") or "")) == "server_worker"
    assert "recovered queued verification passed" in str(state["message"])
    assert str(state["progress_note"]) != ""
    trace_rows = agents._read_jsonl(job_dir / "trace.jsonl")
    assert any(str(row.get("event") or "") == "filesystem_scan_found_queued_job" for row in trace_rows)
    assert any(str(row.get("event") or "") == "filesystem_dispatch_finished" for row in trace_rows)
    global_trace_rows = agents._read_jsonl(agents._host_verification_trace_log_path())
    assert any(str(row.get("request_id") or "") == str(normalized["request_id"]) for row in global_trace_rows)


def test_notify_host_verification_waiter_delivers_result_to_registered_queue(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    request = {
        "workflow_id": "agent-direct-task_id-mbl-generalist",
        "turn": "direct_agent_reply",
        "requested_by_role": "generalist",
        "binding": {"type": "task_id", "id": "mbl"},
        "action": {
            "action_id": "android_unit_test",
            "params": {
                "gradle_task": ":app:testDebugUnitTest",
                "test_selector": "com.eiel.chat.HostVerificationSentinelTest",
            },
        },
        "artifacts": {"capture_stdout": True, "capture_stderr": True},
        "policy": {"timeout_s": 3600, "allow_network": False, "require_clean_exit": True},
        "reason": "queued job should wake waiting caller",
        "created_utc": "2026-03-09T00:00:00Z",
    }
    normalized = agents._verification_contract.normalize_request(request, repo=tmp_path)
    job_dir = Path(str((normalized.get("artifacts") or {}).get("root_dir") or "")).resolve()
    agents._verification_contract.write_json_atomic((job_dir / "request.json").resolve(), normalized)
    agents._write_host_verification_state(
        job_dir,
        status="queued",
        request=normalized,
        message="queued before worker picked it up",
        runner_dispatch="server_worker",
    )

    reply_q: "queue.Queue[dict]" = queue.Queue(maxsize=1)
    agents._register_host_verification_reply_queue(str(normalized["request_id"]), reply_q)

    result = {
        "request_id": str(normalized["request_id"]),
        "workflow_id": str(normalized["workflow_id"]),
        "ok": True,
        "status": "passed",
        "action_id": "android_unit_test",
        "started_utc": "2026-03-09T00:00:00Z",
        "finished_utc": "2026-03-09T00:00:02Z",
        "duration_ms": 2000,
        "exit_code": 0,
        "cwd": str(tmp_path / "mobile" / "android"),
        "argv": ["./gradlew", ":app:testDebugUnitTest", "--tests", "com.eiel.chat.HostVerificationSentinelTest"],
        "evidence": {
            "stdout_path": str(tmp_path / "stdout.txt"),
            "stderr_path": str(tmp_path / "stderr.txt"),
            "artifact_paths": [str(tmp_path / "artifact.xml")],
            "summary": "filesystem queue result delivered",
        },
        "failure_kind": "",
        "runner": {"host": "local", "runner_version": "v1"},
    }

    assert agents._notify_host_verification_waiter(request=normalized, result=result, source="test_case") is True
    delivered = reply_q.get(timeout=1.0)
    assert bool(delivered["ok"]) is True
    assert str(delivered["request_id"]) == str(normalized["request_id"])


def test_send_message_stream_dispatches_direct_host_verification_request(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    def _fake_stream_response_events(**kwargs):
        yield {"type": "response.output_text.delta", "delta": "Need host proof.\nHOST_VERIFICATION_REQUEST_JSON:\n"}
        yield {
            "type": "response.output_text.delta",
            "delta": (
                '{"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest","test_selector":"com.eiel.chat.HostVerificationSentinelTest"},"reason":"stream direct proof"}'
            ),
        }
        yield {
            "type": "response.completed",
            "response": {
                "output_text": (
                    "Need host proof.\n"
                    "HOST_VERIFICATION_REQUEST_JSON:\n"
                    '{"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest","test_selector":"com.eiel.chat.HostVerificationSentinelTest"},"reason":"stream direct proof"}'
                )
            },
        }

    monkeypatch.setattr(agents, "stream_response_events", _fake_stream_response_events)
    monkeypatch.setattr(
        agents,
        "_run_host_verification_request",
        lambda request: {
            "request_id": "vrf_direct_stream",
            "workflow_id": str(request.get("workflow_id") or ""),
            "ok": False,
            "status": "failed",
            "action_id": "android_unit_test",
            "started_utc": "2026-03-09T00:00:00Z",
            "finished_utc": "2026-03-09T00:00:03Z",
            "duration_ms": 3000,
            "exit_code": 1,
            "cwd": str(tmp_path / "mobile" / "android"),
            "argv": ["./gradlew", ":app:testDebugUnitTest"],
            "evidence": {
                "stdout_path": str(tmp_path / "stdout.txt"),
                "stderr_path": str(tmp_path / "stderr.txt"),
                "artifact_paths": [],
                "summary": "targeted test failed",
            },
            "failure_kind": "test_failed",
        },
    )

    evts = list(
        agents.send_message_stream_events(
            role="builder",
            binding={"type": "task_id", "id": "mbu"},
            message="stream patch and verify",
            options={"model": "gpt-5"},
        )
    )

    done = evts[-1]
    assert str(done["type"]) == "done"
    assert bool(done["host_verification_requested"]) is True
    assert str(done["host_verification_result"]["failure_kind"]) == "test_failed"
    assert "Host verification result:" in str(done["assistant_followup"]["content"])

    session = agents.read_session(role="builder", binding={"type": "task_id", "id": "mbu"})
    assert int(session["session_count"]) == 3
    assert "targeted test failed" in str(session["session"][-1]["content"])


def test_resume_artifact_resolution_latest_handoff(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    h = agents.export_handoff(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        selection={"mode": "last_n", "n": 1},
        title="Resume test handoff",
        status="READY",
        scope="scope",
        next_action="Run tests.",
    )
    assert h["ok"] is True
    art = agents.resolve_resume_artifact(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        kind="latest_handoff",
    )
    assert str(art["name"]).endswith(".md")
    assert Path(str(art["path"])).exists()


def test_send_to_role_ledger_binding_appends_agent_request_event(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Request event test", slug="request-event-test", pis_markdown="pis")["uid"])

    res = agents.send_to_role(
        from_role="generalist",
        to_role="builder",
        binding={"type": "ledger_uid", "id": uid},
        message="please continue this task",
        also_enqueue=True,
        request_context={"status": "ready_for_implement", "required_next_action": "Implement patch."},
    )
    assert res["ok"] is True
    assert isinstance(res.get("ledger_event"), dict)
    assert str(res["ledger_event"].get("event_type")) == "agent_request_sent"

    item_dir = ledger.get_item_dir(uid)
    lines = [ln for ln in (item_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    last = json.loads(lines[-1])
    assert str(last.get("type")) == "agent_request_sent"


def test_clear_session_data_clears_transcript_and_inbox(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    agents.send_to_role(
        from_role="builder",
        to_role="assurer",
        binding={"type": "task_id", "id": "a7k"},
        message="msg to inbox",
    )
    sfile = tmp_path / "governance" / "agents" / "sessions" / "a7k" / "session__builder.jsonl"
    sfile.parent.mkdir(parents=True, exist_ok=True)
    sfile.write_text(json.dumps({"sender": "assistant", "content": "old"}) + "\n", encoding="utf-8")

    c1 = agents.clear_session_data(role="builder", binding={"type": "task_id", "id": "a7k"}, target="session")
    assert c1["ok"] is True
    assert int(c1["session_count"]) == 0

    c2 = agents.clear_session_data(role="assurer", binding={"type": "task_id", "id": "a7k"}, target="inbox")
    assert c2["ok"] is True
    assert int(c2["inbox_count"]) == 0


def test_task_id_listing_and_delete_latest_handoff(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    agents.send_to_role(
        from_role="builder",
        to_role="assurer",
        binding={"type": "task_id", "id": "a7k"},
        message="queued",
    )
    agents.export_handoff(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        selection={"mode": "last_n", "n": 1},
        title="handoff one",
        status="READY",
        scope="scope",
        next_action="next",
    )

    listed = agents.list_task_ids(limit=50)
    assert listed["ok"] is True
    assert any(str(x.get("task_id")) == "a7k" for x in listed["task_ids"])

    deleted = agents.delete_latest_handoff(role="builder", binding={"type": "task_id", "id": "a7k"})
    assert deleted["ok"] is True
    assert bool(deleted["deleted"]) is True

    none_left = agents.delete_latest_handoff(role="builder", binding={"type": "task_id", "id": "a7k"})
    assert none_left["ok"] is True
    assert bool(none_left["deleted"]) is False


def test_update_prompt_text_roundtrip(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    upd = agents.update_prompt_text(target="role:builder", text="builder prompt updated")
    assert upd["ok"] is True
    assert str(upd["target"]) == "role:builder"

    payload = agents.get_prompts_payload()
    assert "builder prompt updated" in str(payload["roles"]["builder"]["text"])


def test_get_prompts_payload_includes_orchestrator_mobile_prompt(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    payload = agents.get_prompts_payload()

    assert payload["roles"]["orchestrator"]["text"] == "orchestrator prompt\n"
    assert "role:orchestrator" in list(payload["editable_targets"])


def test_send_message_codex_backend_uses_runtime_manager(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    seen: dict = {}

    class _FakeMgr:
        def ensure_worker(self, **kwargs):
            seen["ensure"] = kwargs
            return {"ok": True, "started": True, "worker_id": "w1", "protocol": "oneshot"}

        def send_turn(self, **kwargs):
            seen["send"] = kwargs
            return {"ok": True, "worker_id": "w1", "protocol": "oneshot", "content": "codex reply", "exit_code": 0}

    monkeypatch.setattr(agents, "_agents_backend", lambda: "codex")
    monkeypatch.setattr(agents, "_codex_manager", lambda: _FakeMgr())

    res = agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="check repo context",
        options={"model": "gpt-5.3-codex", "reasoning_effort": "medium"},
    )
    assert res["ok"] is True
    assert "codex reply" in str(res["assistant"]["content"])
    assert str(res["assistant"]["meta"]["backend"]) == "codex"
    assert "Conversation context" in str(seen["send"]["message"])
    assert int(seen["send"]["timeout_s"]) == 7000
    assert str(seen["send"]["model"]) == "gpt-5.3-codex"
    assert str(seen["send"]["reasoning_effort"]) == "medium"


def test_send_message_codex_backend_attaches_canonical_intake(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    class _FakeMgr:
        def ensure_worker(self, **kwargs):
            return {"ok": True, "started": True, "worker_id": "w1", "protocol": "oneshot"}

        def send_turn(self, **kwargs):
            return {"ok": True, "worker_id": "w1", "protocol": "oneshot", "content": "codex canonical reply", "exit_code": 0}

    monkeypatch.setattr(agents, "_agents_backend", lambda: "codex")
    monkeypatch.setattr(agents, "_codex_manager", lambda: _FakeMgr())

    result = agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="Implement a stable canonical task-id and fingerprint path in the intake helper.",
        options={"model": "gpt-5.3-codex", "reasoning_effort": "medium"},
    )

    assert result["ok"] is True
    assert bool(result.get("operator_intake")) is True
    first_intake = result["operator_intake"]
    assert bool(first_intake.get("ok")) is True
    assert bool(first_intake.get("persisted")) is True

    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    task_rows = [row for row in task_register["active_tasks"] if str(row.get("task_id")) == str(first_intake.get("task_id"))]
    assert len(task_rows) == 1
    assert str(task_rows[0].get("source")) == "operator_intake"
    assert str(task_rows[0].get("intake_path")) == "canonical"


def test_send_message_codex_backend_falls_back_on_quota_error(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    seen_models: list[str] = []

    class _FakeMgr:
        def ensure_worker(self, **kwargs):
            return {"ok": True, "started": True, "worker_id": "w1", "protocol": "oneshot"}

        def send_turn(self, **kwargs):
            model = str(kwargs.get("model") or "")
            seen_models.append(model)
            if model == "gpt-5.3-codex":
                raise agents.CodexRuntimeError("insufficient_quota for gpt-5.3-codex")
            return {"ok": True, "worker_id": "w1", "protocol": "oneshot", "content": "codex reply", "exit_code": 0}

    monkeypatch.setattr(agents, "_agents_backend", lambda: "codex")
    monkeypatch.setattr(agents, "_codex_manager", lambda: _FakeMgr())

    res = agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="check repo context",
        options={
            "model": "gpt-5.3-codex",
            "fallback_models": ["gpt-5.3-codex-spark", "gpt-5.2-codex"],
            "reasoning_effort": "medium",
        },
    )
    assert res["ok"] is True
    assert seen_models == ["gpt-5.3-codex", "gpt-5.3-codex-spark"]
    assert str(res["assistant"]["meta"]["selected_model"]) == "gpt-5.3-codex-spark"


def test_send_message_codex_backend_falls_back_on_quota_result(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    seen_models: list[str] = []

    class _FakeMgr:
        def ensure_worker(self, **kwargs):
            return {"ok": True, "started": True, "worker_id": "w1", "protocol": "oneshot"}

        def send_turn(self, **kwargs):
            model = str(kwargs.get("model") or "")
            seen_models.append(model)
            if model == "gpt-5.3-codex":
                return {
                    "ok": False,
                    "worker_id": "w1",
                    "protocol": "oneshot",
                    "content": "ERROR: You've hit your usage limit for GPT-5.3-Codex.",
                    "error": "ERROR: You've hit your usage limit for GPT-5.3-Codex.",
                    "exit_code": 1,
                }
            return {"ok": True, "worker_id": "w1", "protocol": "oneshot", "content": "codex reply", "exit_code": 0}

    monkeypatch.setattr(agents, "_agents_backend", lambda: "codex")
    monkeypatch.setattr(agents, "_codex_manager", lambda: _FakeMgr())

    res = agents.send_message(
        role="builder",
        binding={"type": "task_id", "id": "a7k"},
        message="check repo context",
        options={
            "model": "gpt-5.3-codex",
            "fallback_models": ["gpt-5.3-codex-spark", "gpt-5.2-codex"],
            "reasoning_effort": "medium",
        },
    )
    assert res["ok"] is True
    assert seen_models == ["gpt-5.3-codex", "gpt-5.3-codex-spark"]
    assert str(res["assistant"]["meta"]["selected_model"]) == "gpt-5.3-codex-spark"


def test_send_message_stream_codex_backend_uses_runtime_manager(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_AGENTS_CODEX_TIMEOUT_S", "1234")
    seen: dict = {}

    class _FakeMgr:
        def ensure_worker(self, **kwargs):
            return {"ok": True, "started": True, "worker_id": "w2", "protocol": "oneshot"}

        def stream_turn(self, **kwargs):
            seen["stream"] = kwargs
            yield {"type": "start", "worker_id": "w2"}
            yield {"type": "delta", "delta": "hello ", "content": "hello "}
            yield {"type": "delta", "delta": "world", "content": "hello world"}
            yield {"type": "done", "ok": True, "content": "hello world", "worker_id": "w2"}

    monkeypatch.setattr(agents, "_agents_backend", lambda: "codex")
    monkeypatch.setattr(agents, "_codex_manager", lambda: _FakeMgr())

    evts = list(
        agents.send_message_stream_events(
            role="builder",
            binding={"type": "task_id", "id": "a7k"},
            message="stream codex",
            options={"model": "gpt-5.3-codex", "reasoning_effort": "medium"},
        )
    )
    assert any(str(e.get("type")) == "delta" for e in evts)
    assert str(evts[-1].get("type")) == "done"
    assert bool(evts[-1].get("ok")) is True
    assert int(seen["stream"]["timeout_s"]) == 1234
    assert str(seen["stream"]["model"]) == "gpt-5.3-codex"
    assert str(seen["stream"]["reasoning_effort"]) == "medium"
    s = agents.read_session(role="builder", binding={"type": "task_id", "id": "a7k"})
    assert "hello world" in str(s["session"][-1]["content"])


def test_send_message_stream_codex_backend_falls_back_on_quota_result(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    seen_models: list[str] = []

    class _FakeMgr:
        def ensure_worker(self, **kwargs):
            return {"ok": True, "started": True, "worker_id": "w2", "protocol": "oneshot"}

        def stream_turn(self, **kwargs):
            model = str(kwargs.get("model") or "")
            seen_models.append(model)
            yield {"type": "start", "worker_id": "w2"}
            if model == "gpt-5.3-codex":
                yield {
                    "type": "done",
                    "ok": False,
                    "content": "",
                    "error": "ERROR: You've hit your usage limit for GPT-5.3-Codex.",
                    "worker_id": "w2",
                }
                return
            yield {"type": "delta", "delta": "hello ", "content": "hello "}
            yield {"type": "delta", "delta": "world", "content": "hello world"}
            yield {"type": "done", "ok": True, "content": "hello world", "worker_id": "w2"}

    monkeypatch.setattr(agents, "_agents_backend", lambda: "codex")
    monkeypatch.setattr(agents, "_codex_manager", lambda: _FakeMgr())

    evts = list(
        agents.send_message_stream_events(
            role="builder",
            binding={"type": "task_id", "id": "a7k"},
            message="stream codex",
            options={
                "model": "gpt-5.3-codex",
                "fallback_models": ["gpt-5.3-codex-spark", "gpt-5.2-codex"],
                "reasoning_effort": "medium",
            },
        )
    )
    assert any(str(e.get("type")) == "delta" for e in evts)
    assert str(evts[-1].get("type")) == "done"
    assert bool(evts[-1].get("ok")) is True
    assert seen_models == ["gpt-5.3-codex", "gpt-5.3-codex-spark"]


def test_mobile_bubble_projection_updates_for_known_builder_stream_agent(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    class _FakeMgr:
        def ensure_worker(self, **kwargs):
            return {"ok": True, "started": True, "worker_id": "w2", "protocol": "oneshot"}

        def stream_turn(self, **kwargs):
            yield {"type": "start", "worker_id": "w2"}
            yield {"type": "delta", "delta": "hello ", "content": "hello "}
            yield {"type": "delta", "delta": "builder", "content": "hello builder"}
            yield {"type": "done", "ok": True, "content": "hello builder", "worker_id": "w2"}

    monkeypatch.setattr(agents, "_agents_backend", lambda: "codex")
    monkeypatch.setattr(agents, "_codex_manager", lambda: _FakeMgr())

    evts = list(
        agents.send_message_stream_events(
            role="builder",
            binding={"type": "task_id", "id": "mbu"},
            message="stream to mobile builder",
            options={},
        )
    )

    assert str(evts[-1].get("type")) == "done"
    assert bool(evts[-1].get("ok")) is True
    bubble = tmp_path / "governance" / "agents" / "mobile_bubbles" / "builder.md"
    assert bubble.exists()
    txt = bubble.read_text(encoding="utf-8")
    assert "stream to mobile builder" in txt
    assert "hello builder" in txt


def test_mobile_session_history_payload_preserves_plane_routed_flag(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    agents.append_session_message(
        role="generalist",
        binding={"type": "task_id", "id": "mbl"},
        sender="user",
        content="through the plane",
        meta={"turn_id": "turn_plane_1", "plane_routed": True, "plane_route_label": "Plane"},
    )

    payload = routes._mobile_session_history_payload("mbl")

    assert payload["history"][-1] == {"role": "user", "content": "through the plane", "plane_routed": True}


def test_mobile_session_history_payload_does_not_infer_plane_routed_from_transport_only(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    agents.append_session_message(
        role="generalist",
        binding={"type": "task_id", "id": "mpl"},
        sender="user",
        content="older plane transport row",
        meta={"source": "mobile_chat_stream"},
    )

    payload = routes._mobile_session_history_payload("mpl")

    assert payload["history"][-1] == {"role": "user", "content": "older plane transport row"}


def test_send_message_stream_events_falls_back_to_non_streaming_provider_execution(tmp_path: Path, monkeypatch) -> None:
    _write_prompt_tree(tmp_path)
    monkeypatch.setattr(agents, "_repo_root", lambda: tmp_path)

    calls: list[dict[str, object]] = []

    def _fake_send_message(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
        calls.append({"role": role, "binding": dict(binding), "message": message, "options": dict(options or {})})
        return {
            "ok": True,
            "assistant": {"content": "deepseek ok", "status": "ok", "meta": {"selected_model": "deepseek-reasoner"}},
            "binding": dict(binding),
            "turn": {"turn_id": "turn_deepseek_001", "started_utc": "2026-03-30T12:00:00Z"},
            "operator_intake": {"ok": True, "persisted": True},
        }

    monkeypatch.setattr(agents, "send_message", _fake_send_message)

    events = list(
        agents.send_message_stream_events(
            role="builder",
            binding={"type": "task_id", "id": "a7k"},
            message="stream this through deepseek",
            options={"model": "deepseek-reasoner"},
        )
    )

    assert calls == [
        {
            "role": "builder",
            "binding": {"type": "task_id", "id": "a7k"},
            "message": "stream this through deepseek",
            "options": {"model": "deepseek-reasoner"},
        }
    ]
    assert [str(evt.get("type") or "") for evt in events] == ["start", "delta", "done"]
    assert events[0]["turn_id"] == "turn_deepseek_001"
    assert events[1]["content"] == "deepseek ok"
    assert bool(events[2]["ok"]) is True
    assert events[2]["assistant"]["content"] == "deepseek ok"
