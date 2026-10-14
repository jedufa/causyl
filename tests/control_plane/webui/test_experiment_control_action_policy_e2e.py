from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments_shared import resolve_experiment_control_policy
from experiments_shared import refresh_experiment_control_policy_cache
from experiments_shared import validate_control_action_policy
from experiments_shared import validate_experiment_contract
from experiments_shared.control_action_enforcement import evaluate_control_action_request
from experiments_shared.experiment_records import build_initial_run_record
from experiments_shared.experiment_records import write_run_record
from infra.webui import server_control_action_audit as audit
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


def _cfg(repo_root: Path) -> routes.WebUIConfig:
    return routes.WebUIConfig(runs_root=repo_root, static_dir=repo_root, admin_token="real-admin-token")


def _write_bridge_token_file(repo_root: Path, *, token_id: str, issuer: str = "local-owner") -> Path:
    return _write_bridge_token_rows(
        repo_root,
        token_rows=[
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
    )


def _write_bridge_token_rows(repo_root: Path, *, token_rows: list[dict]) -> Path:
    path = repo_root / "runtime" / "secrets" / "assistant_bridge_tokens.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "assistant_bridge_tokens_v1",
        "tokens": token_rows,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _mint_control_pane_session(repo_root: Path, *, token_id: str) -> str:
    session_id = f"controlpane_{token_id}_session"
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


def _write_demo_contract_policy_and_run(repo_root: Path) -> Path:
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
            "restart_supported": True,
            "checkpoint_supported": True,
            "branch_from_checkpoint_supported": True,
            "intervention_actions": [
                "graceful_stop",
                "force_stop",
                "pause",
                "resume",
                "restart",
                "checkpoint",
                "branch_from_checkpoint",
            ],
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
            "graceful_stop": {"policy_state": "enabled", "rollout_state": "live", "note": "default stop"},
            "pause": {"policy_state": "enabled", "rollout_state": "live", "note": "safe pause"},
            "resume": {"policy_state": "enabled", "rollout_state": "live", "note": "resume from checkpoint"},
            "restart": {"policy_state": "enabled", "rollout_state": "live", "note": "relaunch from durable anchor"},
            "checkpoint": {"policy_state": "enabled", "rollout_state": "live", "note": "capture boundary"},
            "branch_from_checkpoint": {"policy_state": "disabled", "rollout_state": "staged", "note": "storage review"},
        },
    }
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]
    (runner_root / "control_action_policy.json").write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    run_dir = repo_root / "wrapper" / "run_001"
    run_dir.mkdir(parents=True, exist_ok=True)
    record = build_initial_run_record(
        run_id="run_001",
        job_id="job-001",
        job_spec_hash="sha256:job",
        project_id="demo",
        created_at_utc="2026-04-09T05:00:00Z",
        launch={"entry_module": "experiments.projects.demo.runner"},
    )
    lifecycle = dict(record.get("lifecycle") or {})
    lifecycle["state"] = "running"
    lifecycle["confidence"] = "durable"
    lifecycle["terminal"] = False
    lifecycle["transition_reason"] = "test_fixture"
    record["lifecycle"] = lifecycle
    write_run_record(run_dir, record)
    return run_dir


def test_experiment_control_action_policy_family_flow_proves_operator_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _write_demo_contract_policy_and_run(tmp_path)
    token_file = _write_bridge_token_file(tmp_path, token_id="causyl")
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        routes,
        "_refresh_live_runtime_snapshot",
        lambda: {
            **refresh_experiment_control_policy_cache(repo_root=tmp_path),
            "ok": True,
            "scope": {
                "applies_to": "future_work_started_after_refresh",
                "does_not_apply_to": "in_flight_turns_or_jobs",
            },
        },
    )
    owner_token = _mint_control_pane_session(tmp_path, token_id="causyl")

    initial_read = _DummyReq(
        cfg=_cfg(tmp_path),
        path=f"/api/admin/experiment_control_policy?project_id=demo&token={owner_token}",
    )
    routes.WebUIRequestHandler.do_GET(initial_read)  # type: ignore[arg-type]
    initial_payload = _payload(initial_read)
    assert initial_read._status == 200, initial_payload
    assert "pause" in initial_payload["control_policy_summary"]["available_actions"]
    assert "branch_from_checkpoint" in initial_payload["control_policy_summary"]["blocked_actions"]
    assert "hot_parameter_mutation" in initial_payload["control_policy_summary"]["unsupported_actions"]

    supported = evaluate_control_action_request("graceful_stop", run_dir, repo_root=tmp_path)
    disabled = evaluate_control_action_request("branch_from_checkpoint", run_dir, repo_root=tmp_path)
    assert supported["ok"] is True
    assert disabled["ok"] is False
    assert disabled["reason_code"] == "action_disabled_by_policy"

    stale_snapshot = resolve_experiment_control_policy({"project_id": "demo"}, repo_root=tmp_path)
    mutate_req = _DummyReq(
        cfg=_cfg(tmp_path),
        path=f"/api/admin/experiment_control_policy?token={owner_token}",
        body={
            "project_id": "demo",
            "action_id": "pause",
            "desired_policy_state": "disabled",
            "expected_policy_hash": initial_payload["policy_hash"],
            "confirmation": "Disable pause for future work only.",
            "note": "operator disabled pause",
        },
    )
    routes.WebUIRequestHandler.do_POST(mutate_req)  # type: ignore[arg-type]
    mutate_payload = _payload(mutate_req)
    assert mutate_req._status == 200, mutate_payload
    assert mutate_payload["ok"] is True
    assert mutate_payload["confirmation"]["effective_scope"] == "future_work_started_after_refresh"

    in_flight = resolve_experiment_control_policy(
        {"project_id": "demo", "control_action_policy_snapshot": stale_snapshot},
        repo_root=tmp_path,
    )
    fresh = resolve_experiment_control_policy({"project_id": "demo"}, repo_root=tmp_path)
    assert "pause" in in_flight["control_policy_summary"]["available_actions"]
    assert "pause" in fresh["control_policy_summary"]["blocked_actions"]

    refreshed_read = _DummyReq(
        cfg=_cfg(tmp_path),
        path=f"/api/admin/experiment_control_policy?project_id=demo&token={owner_token}",
    )
    routes.WebUIRequestHandler.do_GET(refreshed_read)  # type: ignore[arg-type]
    refreshed_payload = _payload(refreshed_read)
    assert refreshed_read._status == 200, refreshed_payload
    assert "pause" in refreshed_payload["control_policy_summary"]["blocked_actions"]
    assert "pause" not in refreshed_payload["control_policy_summary"]["available_actions"]

    restart_request = audit.begin_control_action(
        tmp_path,
        action_id="restart",
        actor={"kind": "owner_session", "token_id": "causyl"},
        writer="test.family_flow",
        request_path="/api/admin/experiment/run/control",
        target={"surface": "the plane", "resource": "experiment_run", "run_dir": str(run_dir)},
        correlation={"experiment_id": "demo", "run_id": "run_001", "job_id": "job-001"},
        policy_snapshot={"policy_hash": fresh["policy"]["policy_hash"]},
        authorization={"state": "authorized"},
        decision={"state": "accepted"},
        executor={"state": "restart_scheduled"},
        verification={"state": "pending_restart_reconciliation", "expected_lifecycle_states": ["running"]},
        result={"outcome": "accepted"},
    )

    summary = audit.reconcile_after_restart(tmp_path, writer="test.startup")
    history = audit.recent_control_action_history(tmp_path, limit=5)
    assert summary["reconciled_count"] == 1
    assert history[0]["audit_id"] == restart_request["audit_id"]
    assert history[0]["target"]["surface"] == "the plane"
    assert history[0]["verification"]["state"] == "realized_state_confirmed"
    assert history[0]["verification"]["run_truth"]["state"] == "running"
    assert history[0]["result"]["outcome"] == "realized"


def test_experiment_control_action_policy_family_flow_denies_forbidden_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_demo_contract_policy_and_run(tmp_path)
    token_file = _write_bridge_token_rows(
        tmp_path,
        token_rows=[
            {
                "token_id": "causyl",
                "token": "causyl-bearer",
                "issuer": "local-owner",
                "audience": "lab",
                "scopes": ["control_pane.access"],
                "created_at": "2026-04-09T04:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "allowed_repo_roots": ["."],
                "allowed_pis_prefixes": [],
            },
            {
                "token_id": "intruder",
                "token": "intruder-bearer",
                "issuer": "intruder-owner",
                "audience": "lab",
                "scopes": ["control_pane.access"],
                "created_at": "2026-04-09T04:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "allowed_repo_roots": ["."],
                "allowed_pis_prefixes": [],
            },
        ],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        routes,
        "_refresh_live_runtime_snapshot",
        lambda: {"ok": True, "scope": {"applies_to": "future_work_started_after_refresh"}},
    )

    owner_token = _mint_control_pane_session(tmp_path, token_id="causyl")
    intruder_token = _mint_control_pane_session(tmp_path, token_id="intruder")

    read_req = _DummyReq(
        cfg=_cfg(tmp_path),
        path=f"/api/admin/experiment_control_policy?project_id=demo&token={owner_token}",
    )
    routes.WebUIRequestHandler.do_GET(read_req)  # type: ignore[arg-type]
    read_payload = _payload(read_req)
    assert read_req._status == 200, read_payload

    unauthorized_req = _DummyReq(
        cfg=_cfg(tmp_path),
        path=f"/api/admin/experiment_control_policy?token={intruder_token}",
        body={
            "project_id": "demo",
            "action_id": "pause",
            "desired_policy_state": "disabled",
            "expected_policy_hash": read_payload["policy_hash"],
            "confirmation": "Disable pause.",
        },
    )
    routes.WebUIRequestHandler.do_POST(unauthorized_req)  # type: ignore[arg-type]
    unauthorized_payload = _payload(unauthorized_req)
    assert unauthorized_req._status == 403
    assert unauthorized_payload["error_code"] == "experiment_owner_required"

    unsupported_req = _DummyReq(
        cfg=_cfg(tmp_path),
        path=f"/api/admin/experiment_control_policy?token={owner_token}",
        body={
            "project_id": "demo",
            "action_id": "hot_parameter_mutation",
            "desired_policy_state": "enabled",
            "expected_policy_hash": read_payload["policy_hash"],
            "confirmation": "Enable unsupported action.",
        },
    )
    routes.WebUIRequestHandler.do_POST(unsupported_req)  # type: ignore[arg-type]
    unsupported_payload = _payload(unsupported_req)
    assert unsupported_req._status == 400
    assert unsupported_payload["error_code"] == "control_action_policy_conflict"
    assert unsupported_payload["details"]["errors"][0]["code"] == "contract_policy_conflict"
