from __future__ import annotations

import io
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from infra.controlplane import job_budget_cost_ledger_store as budget_store
from infra.webui.ledger.ledger_manager import ChangeLedger
from infra.webui import server_job_budget_admin as budget_admin
from infra.webui import server_ledger_actions as la
from infra.webui import server_routes_and_main as routes


@pytest.fixture(autouse=True)
def _allow_clean_commit_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        la._commit_journal,
        "scan_pending_entries",
        lambda repo=None: {"pending_count": 1, "invalid_count": 0, "valid": [], "invalid": [], "ok": True},
    )
    monkeypatch.setattr(
        la._commit_journal,
        "audit_worktree_coverage",
        lambda repo=None: {"ok": True, "pending_count": 1, "changed_paths": [], "ignored_paths": [], "covered_paths": [], "uncovered_paths": [], "entry_cover_map": {}},
    )


def _write_action_meta(item_dir: Path, actions: list[dict], required_evidence: list | None = None) -> None:
    meta_p = item_dir / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["actions"] = actions
    if required_evidence is not None:
        meta["required_evidence"] = required_evidence
    meta_p.write_text(json.dumps(meta, sort_keys=True, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def _mk_script(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _wait_job(uid: str, job_id: str, timeout_s: float = 15.0) -> dict:
    t0 = time.time()
    while (time.time() - t0) < timeout_s:
        st = la.action_status(uid=uid, job_id=job_id, limit=100)
        job = st.get("job")
        if isinstance(job, dict) and str(job.get("event") or "") == "finished":
            return job
        time.sleep(0.1)
    raise TimeoutError(f"job did not finish: {job_id}")


def test_ledger_action_run_and_mark_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_WEBUI_LEDGER_ACTIONS", "1")

    _mk_script(
        tmp_path,
        "infra/webui/scripts/ok_action.py",
        "print('hello from action')\nprint('done')\n",
    )

    ledger = ChangeLedger(repo_root=tmp_path)
    created = ledger.create_item(title="Action item", slug="action-item", pis_markdown="pis")
    uid = str(created["uid"])
    item_dir = ledger.get_item_dir(uid)

    _write_action_meta(
        item_dir,
        actions=[
            {
                "id": "ok_action",
                "label": "OK Action",
                "kind": "shell",
                "script": "infra/webui/scripts/ok_action.py",
                "args": [],
                "timeout_s": 60,
                "cwd": "${REPO_ROOT}",
            }
        ],
        required_evidence=["action_success:ok_action"],
    )

    started = la.run_action(uid=uid, action_id="ok_action")
    assert started["ok"] is True
    job_id = str(started["job_id"])

    done = _wait_job(uid, job_id)
    assert int(done.get("exit_code", 1)) == 0
    assert str(done.get("status")) == "ok"

    log_p = Path(str(done.get("log_path")))
    result_p = Path(str(done.get("result_path")))
    assert log_p.exists()
    assert result_p.exists()

    # Event append is async relative to finished job row; allow a brief settle window.
    found_event = False
    t0 = time.time()
    while (time.time() - t0) < 5.0:
        lines = [ln for ln in (item_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
        found_event = any(str(json.loads(ln).get("type")) == "ledger_action_run" for ln in lines)
        if found_event:
            break
        time.sleep(0.05)
    assert found_event is True

    mv = la.mark_verified(uid=uid)
    assert mv["ok"] is True
    assert mv["status"] == "verified"


def test_ledger_action_disallows_non_allowlisted_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_WEBUI_LEDGER_ACTIONS", "1")

    _mk_script(tmp_path, "scripts/not_allowed.py", "print('x')\n")

    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Bad action", slug="bad-action", pis_markdown="pis")["uid"])
    item_dir = ledger.get_item_dir(uid)
    _write_action_meta(
        item_dir,
        actions=[
            {
                "id": "bad",
                "script": "scripts/not_allowed.py",
                "args": [],
            }
        ],
    )

    with pytest.raises(Exception, match="allowlisted roots"):
        la.run_action(uid=uid, action_id="bad")


def test_ledger_action_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_WEBUI_LEDGER_ACTIONS", "1")

    _mk_script(
        tmp_path,
        "infra/webui/scripts/slow_action.py",
        "import time\nprint('start')\ntime.sleep(5)\nprint('done')\n",
    )

    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Slow action", slug="slow-action", pis_markdown="pis")["uid"])
    item_dir = ledger.get_item_dir(uid)
    _write_action_meta(
        item_dir,
        actions=[
            {
                "id": "slow",
                "script": "infra/webui/scripts/slow_action.py",
                "args": [],
                "timeout_s": 60,
            }
        ],
    )

    started = la.run_action(uid=uid, action_id="slow")
    job_id = str(started["job_id"])
    cc = la.cancel_action(uid=uid, job_id=job_id)
    assert cc["ok"] is True

    done = _wait_job(uid, job_id)
    assert str(done.get("event")) == "finished"
    # Exit code can vary by platform/signal, but should not be clean success.
    assert int(done.get("exit_code", 0)) != 0


def test_mark_verified_blocks_when_required_evidence_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_WEBUI_LEDGER_ACTIONS", "1")

    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Needs evidence", slug="needs-evidence", pis_markdown="pis")["uid"])
    item_dir = ledger.get_item_dir(uid)
    _write_action_meta(
        item_dir,
        actions=[],
        required_evidence=["action_success:missing_action"],
    )

    mv = la.mark_verified(uid=uid)
    assert mv["ok"] is False
    assert mv["blocked"] is True
    assert "action_success:missing_action" in list(mv.get("missing") or [])


def test_mark_verified_blocks_when_commit_journal_coverage_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)
    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Needs journal", slug="needs-journal", pis_markdown="pis")["uid"])
    item_dir = ledger.get_item_dir(uid)
    artifact = item_dir / "artifacts" / "verification" / "closure.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("{}\n", encoding="utf-8")
    _write_action_meta(item_dir, actions=[], required_evidence=["artifacts/verification/closure.json"])

    monkeypatch.setattr(
        la._commit_journal,
        "scan_pending_entries",
        lambda repo=None: {"pending_count": 0, "invalid_count": 0, "valid": [], "invalid": [], "ok": True},
    )
    monkeypatch.setattr(
        la._commit_journal,
        "audit_worktree_coverage",
        lambda repo=None: {
            "ok": False,
            "pending_count": 0,
            "changed_paths": ["infra/webui/server_agents.py"],
            "ignored_paths": [],
            "covered_paths": [],
            "uncovered_paths": ["infra/webui/server_agents.py"],
            "entry_cover_map": {},
        },
    )

    mv = la.mark_verified(uid=uid)
    assert mv["ok"] is False
    assert mv["blocked"] is True
    assert mv["reason"] == "commit_journal_missing"


def test_ledger_action_rejects_item_dir_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_WEBUI_LEDGER_ACTIONS", "1")

    _mk_script(tmp_path, "infra/webui/scripts/ok_action.py", "print('ok')\n")
    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Token guard", slug="token-guard", pis_markdown="pis")["uid"])
    item_dir = ledger.get_item_dir(uid)
    _write_action_meta(
        item_dir,
        actions=[
            {
                "id": "bad_token",
                "script": "infra/webui/scripts/ok_action.py",
                "args": ["${ITEM_DIR}"],
            }
        ],
    )

    with pytest.raises(Exception, match="unknown substitution variables"):
        la.run_action(uid=uid, action_id="bad_token")


def test_ledger_action_artifact_paths_are_unique_per_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_WEBUI_LEDGER_ACTIONS", "1")

    _mk_script(tmp_path, "infra/webui/scripts/echo_action.py", "print('x')\n")
    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Uniq", slug="uniq", pis_markdown="pis")["uid"])
    item_dir = ledger.get_item_dir(uid)
    _write_action_meta(
        item_dir,
        actions=[
            {
                "id": "echo",
                "script": "infra/webui/scripts/echo_action.py",
                "args": [],
            }
        ],
    )

    # Force same timestamp second to ensure uniqueness comes from job id.
    monkeypatch.setattr(la, "_utc_compact", lambda: "20260304T200000Z")
    a = la.run_action(uid=uid, action_id="echo")
    b = la.run_action(uid=uid, action_id="echo")
    assert a["log_path"] != b["log_path"]
    assert a["result_path"] != b["result_path"]
    _wait_job(uid, str(a["job_id"]))
    _wait_job(uid, str(b["job_id"]))


def test_reconcile_runs_again_for_new_started_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_WEBUI_LEDGER_ACTIONS", "1")

    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Recon", slug="recon", pis_markdown="pis")["uid"])
    item_dir = ledger.get_item_dir(uid)
    jobs_p = item_dir / "artifacts" / "actions" / "jobs.jsonl"
    jobs_p.parent.mkdir(parents=True, exist_ok=True)

    first_started = {
        "schema_version": "ledger_action_job_v1",
        "job_id": "act_1",
        "uid": uid,
        "action_id": "a",
        "event": "started",
        "pid": 999999,
        "utc": "2026-03-04T20:00:00Z",
    }
    jobs_p.write_text(json.dumps(first_started) + "\n", encoding="utf-8")

    la.action_status(uid=uid, limit=100)

    second_started = {
        "schema_version": "ledger_action_job_v1",
        "job_id": "act_2",
        "uid": uid,
        "action_id": "b",
        "event": "started",
        "pid": 999998,
        "utc": "2026-03-04T20:00:01Z",
    }
    with open(jobs_p, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(second_started) + "\n")

    out = la.action_status(uid=uid, limit=200)
    rows = list(out.get("jobs") or [])
    seen = [r for r in rows if str(r.get("event")) == "reconciled" and str(r.get("job_id")) == "act_2"]
    assert seen, "expected second started row to be reconciled on subsequent status call"


def test_reconcile_running_unknown_job_is_rechecked_until_it_becomes_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_WEBUI_LEDGER_ACTIONS", "1")

    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Recon stale", slug="recon-stale", pis_markdown="pis")["uid"])
    item_dir = ledger.get_item_dir(uid)
    jobs_p = item_dir / "artifacts" / "actions" / "jobs.jsonl"
    jobs_p.parent.mkdir(parents=True, exist_ok=True)

    jobs_p.write_text(
        json.dumps(
            {
                "schema_version": "ledger_action_job_v1",
                "job_id": "act_recheck",
                "uid": uid,
                "action_id": "a",
                "event": "started",
                "pid": 424242,
                "utc": "2026-03-04T20:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    pid_states = iter([True, False])
    monkeypatch.setattr(la, "_is_pid_alive", lambda _pid: next(pid_states, False))

    first = la.action_status(uid=uid, limit=100)
    first_rows = list(first.get("jobs") or [])
    assert str(first_rows[-1].get("status") or "") == "running_unknown"

    second = la.action_status(uid=uid, limit=100)
    second_rows = list(second.get("jobs") or [])
    assert str(second_rows[-1].get("event") or "") == "reconciled"
    assert str(second_rows[-1].get("status") or "") == "stale"



def test_direct_status_verified_is_gated_by_required_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(routes._ledger_actions, "_repo_root", lambda: tmp_path)
    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Gate verified", slug="gate-verified", pis_markdown="pis")["uid"])
    item_dir = ledger.get_item_dir(uid)
    _write_action_meta(
        item_dir,
        actions=[],
        required_evidence=["action_success:must_exist"],
    )

    sc, payload = routes._apply_ledger_status_update(uid=uid, status="verified", note="bypass attempt")
    assert int(sc) == 409
    assert bool(payload.get("ok")) is False
    assert bool(payload.get("blocked")) is True

    item = ledger.read_item(uid)
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    assert str(meta.get("status") or "") != "verified"


def test_workflow_advance_blocks_on_verified_gate_and_then_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Workflow advance", slug="workflow-advance", pis_markdown="pis")["uid"])
    item_dir = ledger.get_item_dir(uid)
    _write_action_meta(
        item_dir,
        actions=[{"id": "gate_action", "script": "infra/webui/scripts/create_branch_from_uid.sh", "args": []}],
        required_evidence=["action_success:gate_action"],
    )

    a1 = la.advance_status(uid=uid, note="advance 1")
    assert a1["ok"] is True
    assert str(a1["status"]) == "ready_for_implement"

    a2 = la.advance_status(uid=uid, note="advance 2")
    assert a2["ok"] is True
    assert str(a2["status"]) == "in_progress"

    a3 = la.advance_status(uid=uid, note="advance 3")
    assert a3["ok"] is True
    assert str(a3["status"]) == "ready_for_assure"

    blocked = la.advance_status(uid=uid, note="advance to verified blocked")
    assert blocked["ok"] is False
    assert blocked["blocked"] is True
    assert "required_evidence_missing" in str(blocked.get("reason") or "")

    actions_dir = item_dir / "artifacts" / "actions"
    actions_dir.mkdir(parents=True, exist_ok=True)
    ok_result = actions_dir / "20260304T210000Z__gate_action__manual.result.json"
    ok_result.write_text(json.dumps({"exit_code": 0}) + "\n", encoding="utf-8")

    verified = la.advance_status(uid=uid, note="advance to verified")
    assert verified["ok"] is True
    assert str(verified["status"]) == "verified"

    done = la.advance_status(uid=uid, note="advance to done")
    assert done["ok"] is True
    assert str(done["status"]) == "done"


def test_workflow_advance_rejects_noncanonical_jump_to_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="No jump", slug="no-jump", pis_markdown="pis")["uid"])
    item_dir = ledger.get_item_dir(uid)
    _write_action_meta(
        item_dir,
        actions=[],
        required_evidence=["action_success:must_exist"],
    )

    out = la.advance_status(uid=uid, note="bypass", requested_next="done")
    assert out["ok"] is False
    assert out["blocked"] is True
    assert str(out.get("reason") or "") == "invalid_workflow_transition"
    assert str(out.get("current_status") or "") == "draft"
    assert str(out.get("expected_next_status") or "") == "ready_for_implement"
    assert str(out.get("requested_next_status") or "") == "done"

    item = ledger.read_item(uid)
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    assert str(meta.get("status") or "") == "draft"


def test_workflow_advance_blocks_when_item_is_deferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)

    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Deferred item", slug="deferred-item", pis_markdown="pis")["uid"])
    ledger.update_status(uid, "deferred", note="triaged_for_later")

    out = la.advance_status(uid=uid, note="advance deferred")
    assert out["ok"] is False
    assert out["blocked"] is True
    assert str(out.get("reason") or "") == "no_workflow_next_status"
    assert str(out.get("current_status") or "") == "deferred"


def test_action_log_returns_tail_for_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(la, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_WEBUI_LEDGER_ACTIONS", "1")

    _mk_script(tmp_path, "infra/webui/scripts/log_action.py", "print('line_a')\nprint('line_b')\n")
    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Log tail", slug="log-tail", pis_markdown="pis")["uid"])
    item_dir = ledger.get_item_dir(uid)
    _write_action_meta(
        item_dir,
        actions=[{"id": "log_action", "script": "infra/webui/scripts/log_action.py", "args": []}],
    )

    started = la.run_action(uid=uid, action_id="log_action")
    job_id = str(started["job_id"])
    _wait_job(uid, job_id)

    out = la.action_log(uid=uid, job_id=job_id, lines=50)
    joined = "\n".join(list(out.get("lines") or []))
    assert out["ok"] is True
    assert "line_a" in joined
    assert "line_b" in joined


def test_ledger_meta_changesets_roundtrip(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    uid = str(ledger.create_item(title="Changesets meta", slug="changesets-meta", pis_markdown="pis")["uid"])
    upd = ledger.update_meta(uid, {"changesets": ["cs-001", "cs-abc"]})
    assert upd["ok"] is True
    item = ledger.read_item(uid)
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    assert list(meta.get("changesets") or []) == ["cs-001", "cs-abc"]


def test_ledger_execution_priority_persists_and_sorts_to_front(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    first = str(ledger.create_item(title="First", slug="first", pis_markdown="pis")["uid"])
    second = str(ledger.create_item(title="Second", slug="second", pis_markdown="pis")["uid"])
    third = str(ledger.create_item(title="Third", slug="third", pis_markdown="pis")["uid"])

    upd = ledger.update_meta(second, {"execution_priority": 200})
    assert upd["ok"] is True

    promoted = ledger.prioritize_item(third)
    assert promoted["ok"] is True
    assert int(promoted["execution_priority"]) < 200

    listed = ledger.list_items(limit=10)
    assert [str(item.get("uid") or "") for item in listed[:3]] == [third, second, first]

    item = ledger.read_item(third)
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    assert int(meta.get("execution_priority")) == int(promoted["execution_priority"])


def test_tail_log_handles_large_file_without_full_scan(tmp_path: Path) -> None:
    p = tmp_path / "large.log"
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        for i in range(0, 50000):
            f.write(f"line_{i}\n")
    out = la._tail_log(p, lines=7)  # internal helper is part of AG01 robustness surface.
    assert len(out) == 7
    assert out[0] == "line_49993"
    assert out[-1] == "line_49999"


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str, headers: dict | None = None) -> None:
        self.path = path
        self.headers = {"Host": "127.0.0.1:8787", **(headers or {})}
        self.rfile = io.BytesIO(b"{}")
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg), server_address=("127.0.0.1", 8787))
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def test_ledger_create_requires_admin_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path, admin_enabled=True, admin_token="tok")
    req = _DummyReq(cfg=cfg, path="/api/ledger/item/create")

    def _should_not_parse(_handler, max_bytes: int = 0):
        raise AssertionError("unauthorized request should return before parsing body")

    monkeypatch.setattr(routes, "_read_request_json", _should_not_parse)
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 401
    payload = json.loads(req.wfile.getvalue().decode("utf-8"))
    assert "authentication required" in str(payload.get("error") or "").lower()


def test_title_and_uid_are_extracted_from_pis_markdown() -> None:
    pis = (
        "PIS UID: PIS-20260304-235959Z-ABCD\n\n"
        "Title: Improve ledger-create UX. This sentence should be used.\n"
        "More details follow."
    )
    uid = routes._extract_uid_from_pis_text(pis)
    title = routes._title_from_pis_markdown(pis)
    assert uid == "PIS-20260304-235959Z-ABCD"
    assert "Improve ledger-create UX." in title


def test_ledger_create_item_accepts_uid_override(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    uid = "PIS-20260304-101010Z-Z9K1"
    out = ledger.create_item(title="Override UID item", uid_override=uid, pis_markdown="pis")
    assert out["ok"] is True
    assert str(out["uid"]) == uid


def test_ledger_create_endpoint_accepts_project_scope_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path, admin_enabled=True, admin_token="tok")
    req = _DummyReq(cfg=cfg, path="/api/ledger/item/create", headers={"X-Admin-Token": "tok"})

    monkeypatch.setattr(
        routes,
        "_read_request_json",
        lambda _handler, max_bytes=0: {
            "title": "Emergence migration slice",
            "project_id": "emergence",
            "workstream_type": "project",
            "scope_boundary": "project-local",
            "artifact_roots": ["experiments/projects/emergence", "../emergence__dev_EIEL"],
            "pis_markdown": "pis",
        },
    )
    monkeypatch.setattr(routes, "ChangeLedger", lambda: ChangeLedger(repo_root=tmp_path))

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = json.loads(req.wfile.getvalue().decode("utf-8"))
    uid = str(payload["uid"])
    item = ChangeLedger(repo_root=tmp_path).read_item(uid)
    assert item["meta"]["project_id"] == "emergence"
    assert item["meta"]["workstream_type"] == "project"
    assert item["meta"]["scope_boundary"] == "project-local"
    assert item["meta"]["artifact_roots"] == ["experiments/projects/emergence", "../emergence__dev_EIEL"]


def test_admin_job_budget_allocation_and_inspection_routes_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(budget_admin, "_repo_root", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, admin_enabled=True, admin_token="tok")

    post_req = _DummyReq(cfg=cfg, path="/api/admin/job_budget/allocation", headers={"X-Admin-Token": "tok"})
    monkeypatch.setattr(
        routes,
        "_read_request_json",
        lambda _handler, max_bytes=0: {
            "allocation_uid": "alloc_001",
            "job_uid": "job_123",
            "amount_minor_units": 1_000,
            "unit": "usd_cents",
            "allocation_reason_code": "initial_grant",
            "recorded_at_utc": "2026-04-06T18:00:00Z",
        },
    )

    routes.WebUIRequestHandler.do_POST(post_req)  # type: ignore[arg-type]

    assert post_req._status == 200
    post_payload = json.loads(post_req.wfile.getvalue().decode("utf-8"))
    assert post_payload["deduped"] is False
    assert post_payload["inspection"]["snapshot"]["allocated_minor_units"] == 1_000
    assert post_payload["inspection"]["snapshot"]["remaining_minor_units"] == 1_000

    budget_store.append_cost_event_record(
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "cost_event_uid": "cost_001",
            "job_uid": "job_123",
            "recorded_at_utc": "2026-04-06T18:05:00Z",
            "amount_minor_units": 250,
            "unit": "usd_cents",
            "direction": "debit",
            "cost_reason_code": "usage",
        },
        repo_root=tmp_path,
    )

    get_req = _DummyReq(
        cfg=cfg,
        path="/api/admin/job_budget/ledger?job_uid=job_123",
        headers={"X-Admin-Token": "tok"},
    )
    routes.WebUIRequestHandler.do_GET(get_req)  # type: ignore[arg-type]

    assert get_req._status == 200
    get_payload = json.loads(get_req.wfile.getvalue().decode("utf-8"))
    assert get_payload["snapshot"]["allocated_minor_units"] == 1_000
    assert get_payload["snapshot"]["cost_debit_minor_units"] == 250
    assert get_payload["snapshot"]["remaining_minor_units"] == 750
    assert len(get_payload["allocation_records"]) == 1
    assert len(get_payload["cost_event_records"]) == 1

    list_req = _DummyReq(
        cfg=cfg,
        path="/api/admin/job_budget/jobs?limit=10",
        headers={"X-Admin-Token": "tok"},
    )
    routes.WebUIRequestHandler.do_GET(list_req)  # type: ignore[arg-type]

    assert list_req._status == 200
    list_payload = json.loads(list_req.wfile.getvalue().decode("utf-8"))
    assert list_payload["total_jobs"] == 1
    assert list_payload["jobs"][0]["job_uid"] == "job_123"
    assert list_payload["jobs"][0]["snapshot"]["remaining_minor_units"] == 750


def test_admin_job_budget_allocation_rejects_unauthorized_requests_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(budget_admin, "_repo_root", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, admin_enabled=True, admin_token="tok")
    req = _DummyReq(cfg=cfg, path="/api/admin/job_budget/allocation")

    def _should_not_parse(_handler, max_bytes: int = 0):
        raise AssertionError("unauthorized request should return before parsing body")

    monkeypatch.setattr(routes, "_read_request_json", _should_not_parse)
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 401
    assert not budget_store.ledger_paths_for_job("job_123", repo_root=tmp_path)["allocation_log"].exists()


def test_admin_job_budget_allocation_rejects_malformed_payload_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(budget_admin, "_repo_root", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, admin_enabled=True, admin_token="tok")
    req = _DummyReq(cfg=cfg, path="/api/admin/job_budget/allocation", headers={"X-Admin-Token": "tok"})
    monkeypatch.setattr(
        routes,
        "_read_request_json",
        lambda _handler, max_bytes=0: {
            "allocation_uid": "alloc_bad",
            "job_uid": "job_123",
            "amount_minor_units": -1,
            "unit": "usd_cents",
            "allocation_reason_code": "initial_grant",
        },
    )

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 400
    assert not budget_store.ledger_paths_for_job("job_123", repo_root=tmp_path)["allocation_log"].exists()


def test_admin_job_budget_read_route_requires_admin_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(budget_admin, "_repo_root", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, admin_enabled=True, admin_token="tok")
    req = _DummyReq(cfg=cfg, path="/api/admin/job_budget/ledger?job_uid=job_123")

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 401
