from __future__ import annotations

import hashlib
import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments_shared import validate_control_action_policy
from experiments_shared import validate_experiment_contract
from infra.webui import server_http_core as http_core
from infra.webui import server_routes_and_main as routes


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str, body: dict | None = None) -> None:
        payload = json.dumps(body or {}).encode("utf-8")
        self.path = path
        self.headers = {"Content-Length": str(len(payload))}
        self.rfile = io.BytesIO(payload)
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg))
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def _payload(req: _DummyReq) -> dict:
    return json.loads(req.wfile.getvalue().decode("utf-8"))


def _patch_repo_root(monkeypatch: pytest.MonkeyPatch, repo_root: Path) -> None:
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: repo_root)
    monkeypatch.setattr(http_core, "_repo_root_from_webui", lambda: repo_root)
    monkeypatch.setattr(http_core, "_admin_repo_root", lambda: repo_root)


def _write_demo_contract_and_policy(repo_root: Path) -> dict:
    project_root = repo_root / "experiments" / "projects" / "demo"
    external_data_root = repo_root.parent / f"{repo_root.name}_external_data" / "demo"
    runner_root = project_root / "_runner"
    runner_root.mkdir(parents=True, exist_ok=True)
    (project_root / "_config").mkdir(parents=True, exist_ok=True)
    (project_root / "_spec" / "registry" / "v1").mkdir(parents=True, exist_ok=True)
    (project_root / "runner.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    contract = {
        "schema_version": "experiment_contract_v1",
        "contract_hash": "",
        "compatibility": {"contract_family": "experiment_contract", "major": 1, "minor": 0},
        "project": {"project_id": "demo", "display_name": "Demo", "owner": "causyl", "tags": ["demo"]},
        "launch": {
            "default_runner_id": "registry_batch",
            "supported_runners": ["registry_batch"],
            "entrypoints": {
                "registry_batch": {
                    "command": ["python", "-m", "experiments.projects.demo.runner"],
                    "working_dir": "experiments/projects/demo",
                    "env": {"PYTHONUNBUFFERED": "1"},
                }
            },
            "args": {"required": ["registry"], "optional": [], "types": {"registry": "path"}},
        },
        "configuration": {
            "profiles_root": "experiments/projects/demo/_config",
            "registry_root": "experiments/projects/demo/_spec/registry/v1",
            "override_modes": ["patch"],
            "default_profile": "baseline",
        },
        "observability": {
            "status_files": [{"path": "run_record.json", "type": "json", "priority": 1}],
            "progress": {"mode": "wrapper", "fields": ["status"]},
            "health_signals": ["exit_code"],
        },
        "control": {
            "pause_supported": True,
            "resume_supported": True,
            "stop_supported": True,
            "restart_supported": False,
            "checkpoint_supported": False,
            "branch_from_checkpoint_supported": True,
            "intervention_actions": ["graceful_stop", "pause", "resume", "branch_from_checkpoint"],
            "unsupported_actions": ["hot_parameter_mutation"],
        },
        "analysis": {"primary_artifacts": ["run_record.json"], "recipes": ["summary_report"], "default_recipe": "summary_report"},
        "resources": {
            "resource_class": "cpu_light",
            "project_semaphore": "cpu/demo",
            "max_parallel_runs": 1,
            "estimated_footprint": {"gpu_count": 0, "cpu_cores": 1, "ram_gb": 1},
        },
        "limitations": [],
        "artifacts": {
            "repo_tracked_outputs": ["run_record.json"],
            "external_data_root": str(external_data_root.resolve()),
            "artifact_manifest_required": False,
        },
        "context_policy": {"required_context_bundle": "lab_core_v1", "additional_context_refs_allowed": True, "forbidden_context_refs": []},
        "validation": {
            "schema_validation_required": True,
            "hash_validation_required": True,
            "prelaunch_checks": ["profiles_root_exists", "registry_root_exists", "runner_exists"],
        },
    }
    contract["contract_hash"] = validate_experiment_contract(contract)["computed_contract_hash"]
    (runner_root / "experiment_contract.json").write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    policy = {
        "schema_version": "control_action_policy_v1",
        "policy_hash": "",
        "project_id": "demo",
        "actions": {
            "pause": {"policy_state": "enabled", "rollout_state": "live", "note": "safe"},
            "branch_from_checkpoint": {"policy_state": "disabled", "rollout_state": "staged", "note": "storage review"},
        },
    }
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]
    (runner_root / "control_action_policy.json").write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return policy


def _write_bridge_token_file(repo_root: Path, *, token_id: str, issuer: str = "local-owner") -> Path:
    path = repo_root / "runtime" / "secrets" / "assistant_bridge_tokens.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "assistant_bridge_tokens_v1",
        "tokens": [
            {
                "token_id": token_id,
                "token": f"{token_id}-bearer",
                "issuer": issuer,
                "audience": "lab",
                "scopes": ["control_pane.access"],
                "created_at": "2026-04-09T04:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "allowed_repo_roots": ["."],
                "allowed_pis_prefixes": [],
            }
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _mint_control_pane_session(repo_root: Path, *, token_id: str) -> str:
    session_id = "controlpane_test_session"
    raw_token = f"abrctl_{session_id}.secret-{token_id}"
    record = {
        "schema_version": "assistant_bridge_control_pane_session_v1",
        "uid": "PIS-20260331-235500Z-T8M3",
        "session_id": session_id,
        "token_id": token_id,
        "issued_at": "2026-04-09T04:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
        "control_pane_path": "/admin/mobile",
        "session_token_sha256": hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        "revoked": False,
    }
    session_path = repo_root / "runtime" / "state" / "assistant_bridge" / "control_pane_sessions" / f"{session_id}.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return raw_token


def _cfg(repo_root: Path) -> routes.WebUIConfig:
    return routes.WebUIConfig(runs_root=repo_root, static_dir=repo_root, admin_token="real-admin-token")


def test_experiment_control_policy_read_allows_owner_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_policy = _write_demo_contract_and_policy(tmp_path)
    token_file = _write_bridge_token_file(tmp_path, token_id="causyl")
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    req = _DummyReq(
        cfg=_cfg(tmp_path),
        path=f"/api/admin/experiment_control_policy?project_id=demo&token={_mint_control_pane_session(tmp_path, token_id='causyl')}",
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200, _payload(req)
    payload = _payload(req)
    assert payload["ok"] is True
    assert payload["authorization"]["actor_kind"] == "owner_session"
    assert payload["policy_hash"] == original_policy["policy_hash"]
    assert payload["control_policy_summary"]["available_actions"] == ["graceful_stop", "pause", "resume"]


def test_experiment_control_policy_mutation_writes_policy_for_owner_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_policy = _write_demo_contract_and_policy(tmp_path)
    token_file = _write_bridge_token_file(tmp_path, token_id="causyl")
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        routes,
        "_refresh_live_runtime_snapshot",
        lambda: {"ok": True, "scope": {"applies_to": "future_work_started_after_refresh"}},
    )
    req = _DummyReq(
        cfg=_cfg(tmp_path),
        path=f"/api/admin/experiment_control_policy?token={_mint_control_pane_session(tmp_path, token_id='causyl')}",
        body={
            "project_id": "demo",
            "action_id": "pause",
            "desired_policy_state": "disabled",
            "expected_policy_hash": original_policy["policy_hash"],
            "confirmation": "Disable pause for future work only.",
            "note": "operator disabled",
        },
    )

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200, _payload(req)
    payload = _payload(req)
    assert payload["ok"] is True
    assert payload["mutation_applied"] is True
    assert payload["confirmation"]["state"] == "applied_and_refreshed"
    policy_path = tmp_path / "experiments" / "projects" / "demo" / "_runner" / "control_action_policy.json"
    written = json.loads(policy_path.read_text(encoding="utf-8"))
    assert written["actions"]["pause"]["policy_state"] == "disabled"
    assert written["actions"]["pause"]["note"] == "operator disabled"


def test_experiment_control_policy_mutation_denies_non_owner_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = _write_demo_contract_and_policy(tmp_path)
    token_file = _write_bridge_token_file(tmp_path, token_id="someone-else")
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    req = _DummyReq(
        cfg=_cfg(tmp_path),
        path=f"/api/admin/experiment_control_policy?token={_mint_control_pane_session(tmp_path, token_id='someone-else')}",
        body={
            "project_id": "demo",
            "action_id": "pause",
            "desired_policy_state": "disabled",
            "expected_policy_hash": policy["policy_hash"],
            "confirmation": "Disable pause.",
        },
    )

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 403
    payload = _payload(req)
    assert payload["error_code"] == "experiment_owner_required"


def test_experiment_control_policy_read_allows_admin_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_demo_contract_and_policy(tmp_path)
    _patch_repo_root(monkeypatch, tmp_path)
    req = _DummyReq(
        cfg=_cfg(tmp_path),
        path="/api/admin/experiment_control_policy?project_id=demo&token=real-admin-token",
    )

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200, _payload(req)
    payload = _payload(req)
    assert payload["ok"] is True
    assert payload["authorization"]["actor_kind"] == "admin"


def test_experiment_control_policy_mutation_rejects_unknown_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = _write_demo_contract_and_policy(tmp_path)
    token_file = _write_bridge_token_file(tmp_path, token_id="causyl")
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    req = _DummyReq(
        cfg=_cfg(tmp_path),
        path=f"/api/admin/experiment_control_policy?token={_mint_control_pane_session(tmp_path, token_id='causyl')}",
        body={
            "project_id": "demo",
            "action_id": "time_travel",
            "desired_policy_state": "disabled",
            "expected_policy_hash": policy["policy_hash"],
            "confirmation": "Block time travel.",
        },
    )

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 400
    payload = _payload(req)
    assert payload["error_code"] == "control_action_policy_conflict"
    assert payload["details"]["errors"][0]["code"] == "unknown_control_action"


def test_experiment_control_policy_mutation_rejects_stale_expected_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_demo_contract_and_policy(tmp_path)
    token_file = _write_bridge_token_file(tmp_path, token_id="causyl")
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    req = _DummyReq(
        cfg=_cfg(tmp_path),
        path=f"/api/admin/experiment_control_policy?token={_mint_control_pane_session(tmp_path, token_id='causyl')}",
        body={
            "project_id": "demo",
            "action_id": "pause",
            "desired_policy_state": "disabled",
            "expected_policy_hash": "sha256:stale",
            "confirmation": "Disable pause with stale view.",
        },
    )

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 409
    payload = _payload(req)
    assert payload["error_code"] == "expected_policy_hash_mismatch"


def test_experiment_control_policy_mutation_reports_refresh_failure_without_false_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = _write_demo_contract_and_policy(tmp_path)
    token_file = _write_bridge_token_file(tmp_path, token_id="causyl")
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        routes,
        "_refresh_live_runtime_snapshot",
        lambda: {
            "ok": False,
            "scope": {"applies_to": "future_work_started_after_refresh"},
            "message": "cached policy retained",
        },
    )
    req = _DummyReq(
        cfg=_cfg(tmp_path),
        path=f"/api/admin/experiment_control_policy?token={_mint_control_pane_session(tmp_path, token_id='causyl')}",
        body={
            "project_id": "demo",
            "action_id": "pause",
            "desired_policy_state": "disabled",
            "expected_policy_hash": policy["policy_hash"],
            "confirmation": "Disable pause even if refresh degrades.",
        },
    )

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200, _payload(req)
    payload = _payload(req)
    assert payload["ok"] is False
    assert payload["mutation_applied"] is True
    assert payload["confirmation"]["state"] == "written_refresh_degraded"
    assert "written" in payload["confirmation"]["summary"].lower()
    assert "refresh" in payload["confirmation"]["summary"].lower()


def test_experiment_control_policy_mutation_allows_only_one_matching_hash_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = _write_demo_contract_and_policy(tmp_path)
    token_file = _write_bridge_token_file(tmp_path, token_id="causyl")
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        routes,
        "_refresh_live_runtime_snapshot",
        lambda: {"ok": True, "scope": {"applies_to": "future_work_started_after_refresh"}},
    )
    session_token = _mint_control_pane_session(tmp_path, token_id="causyl")
    start_gate = threading.Barrier(2)
    results: list[tuple[str, int | None, dict]] = []
    results_lock = threading.Lock()

    def _attempt(note: str) -> None:
        req = _DummyReq(
            cfg=_cfg(tmp_path),
            path=f"/api/admin/experiment_control_policy?token={session_token}",
            body={
                "project_id": "demo",
                "action_id": "pause",
                "desired_policy_state": "disabled",
                "expected_policy_hash": policy["policy_hash"],
                "confirmation": "Disable pause for future work only.",
                "note": note,
            },
        )
        start_gate.wait(timeout=5)
        routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
        with results_lock:
            results.append((note, req._status, _payload(req)))

    first = threading.Thread(target=_attempt, args=("winner-candidate-a",))
    second = threading.Thread(target=_attempt, args=("winner-candidate-b",))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(results) == 2

    successes = [row for row in results if row[1] == 200]
    failures = [row for row in results if row[1] == 409]
    assert len(successes) == 1, results
    assert len(failures) == 1, results

    winning_note, _, success_payload = successes[0]
    losing_note, _, failure_payload = failures[0]
    assert winning_note != losing_note
    assert success_payload["ok"] is True
    assert success_payload["mutation_applied"] is True
    assert failure_payload["error_code"] == "expected_policy_hash_mismatch"

    policy_path = tmp_path / "experiments" / "projects" / "demo" / "_runner" / "control_action_policy.json"
    final_policy = json.loads(policy_path.read_text(encoding="utf-8"))
    validation = validate_control_action_policy(final_policy, expected_project_id="demo")
    assert validation["ok"] is True
    assert final_policy["actions"]["pause"]["policy_state"] == "disabled"
    assert final_policy["actions"]["pause"]["note"] == winning_note
    assert final_policy["actions"]["pause"]["note"] != losing_note
    assert final_policy["policy_hash"] == success_payload["written_policy_hash"]
