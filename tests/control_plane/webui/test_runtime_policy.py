from __future__ import annotations

from pathlib import Path
import subprocess

from tools import runtime_policy
from infra.webui import server_agents
from infra.webui import server_agents_orchestrator as orch
from infra.webui import server_routes_and_main as routes


def test_runtime_policy_loader_reads_properties(tmp_path: Path, monkeypatch) -> None:
    policy_path = tmp_path / "config" / "runtime_policy.properties"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text("mobile.chat_turn_timeout_s=321\n", encoding="utf-8")
    monkeypatch.setattr(runtime_policy, "_repo_root", lambda: tmp_path)
    runtime_policy.load_runtime_policy.cache_clear()

    assert runtime_policy.get_runtime_policy_int("mobile.chat_turn_timeout_s", 900) == 321

    runtime_policy.load_runtime_policy.cache_clear()


def test_runtime_policy_loader_reloads_when_file_changes(tmp_path: Path, monkeypatch) -> None:
    policy_path = tmp_path / "config" / "runtime_policy.properties"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runtime_policy, "_repo_root", lambda: tmp_path)
    runtime_policy.load_runtime_policy.cache_clear()

    policy_path.write_text("mobile.chat_turn_timeout_s=321\n", encoding="utf-8")
    assert runtime_policy.get_runtime_policy_int("mobile.chat_turn_timeout_s", 900) == 321

    policy_path.write_text("mobile.chat_turn_timeout_s=654\n", encoding="utf-8")
    assert runtime_policy.get_runtime_policy_int("mobile.chat_turn_timeout_s", 900) == 654


def test_runtime_refresh_boundary_inventory_classifies_keys_and_env_overrides(tmp_path: Path, monkeypatch) -> None:
    policy_path = tmp_path / "config" / "runtime_policy.properties"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        "mobile.chat_turn_timeout_s=321\n"
        "scheduler.default_model=gpt-5.4\n"
        "assistant.api_enabled=true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_policy, "_repo_root", lambda: tmp_path)
    runtime_policy.load_runtime_policy.cache_clear()
    monkeypatch.setenv("EIEL_MOBILE_CHAT_TIMEOUT_S", "654")

    inventory = runtime_policy.runtime_refresh_boundary_inventory()

    assert "mobile.chat_turn_timeout_s" in inventory["future_turn_only_keys"]
    assert "scheduler.default_model" in inventory["future_job_only_keys"]
    assert "assistant.api_enabled" in inventory["restart_required_keys"]
    assert inventory["env_overrides_active"][0]["key"] == "mobile.chat_turn_timeout_s"


def test_mobile_chat_timeout_prefers_env_over_policy(monkeypatch) -> None:
    runtime_policy.load_runtime_policy.cache_clear()
    monkeypatch.setattr(runtime_policy, "load_runtime_policy", lambda: {"mobile.chat_turn_timeout_s": "321"})
    monkeypatch.setenv("EIEL_MOBILE_CHAT_TIMEOUT_S", "654")

    assert routes._default_mobile_chat_timeout_s() == 654


def test_mobile_runtime_policy_payload_reflects_current_loader_values(monkeypatch) -> None:
    runtime_policy.load_runtime_policy.cache_clear()
    monkeypatch.setattr(
        runtime_policy,
        "load_runtime_policy",
        lambda: {
            "mobile.chat_turn_timeout_s": "654",
            "mobile.chat_idle_timeout_s": "111",
            "mobile.workflow_timeout_s": "222",
            "mobile.chat_model": "gpt-5.4",
            "mobile.chat_reasoning_effort": "xhigh",
            "mobile.chat_fallback_models": "gpt-5.4-mini,gpt-5.2-codex",
        },
    )
    assert routes._mobile_runtime_policy_payload() == {
        "chat_turn_timeout_s": 654,
        "chat_idle_timeout_s": 111,
        "workflow_timeout_s": 222,
        "chat_model": "gpt-5.4",
        "chat_reasoning_effort": "xhigh",
        "chat_fallback_models": ["gpt-5.4-mini", "gpt-5.2-codex"],
    }


def test_admin_runtime_refresh_payload_includes_scheduler_runtime_policy(monkeypatch) -> None:
    runtime_policy.load_runtime_policy.cache_clear()
    monkeypatch.setattr(routes._agents, "refresh_live_runtime", lambda: {"ok": True})
    monkeypatch.setattr(routes._agent_orch, "current_runtime_defaults", lambda: {"control_mode": "deterministic"})
    monkeypatch.setattr(
        routes,
        "_refresh_experiment_control_policy_cache",
        lambda: {"ok": True, "cached_project_count": 1, "refreshed_projects": [{"project_id": "demo", "ok": True}]},
    )
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "current_runtime_resource_policy",
        lambda: {"ok": True, "global_concurrency_cap": 2, "per_role_cap": {"builder": 1}, "resource_class_caps": {}},
    )

    payload = routes._refresh_live_runtime_snapshot()

    assert payload["scheduler_runtime_policy"]["global_concurrency_cap"] == 2
    assert payload["experiment_control_policy_refresh"]["cached_project_count"] == 1
    assert "experiment_control_action_policy" in payload["scope"]["live_refreshable"]

def test_codex_timeout_uses_policy_when_env_missing(monkeypatch) -> None:
    runtime_policy.load_runtime_policy.cache_clear()
    monkeypatch.setattr(runtime_policy, "load_runtime_policy", lambda: {"agents.codex_turn_timeout_s": "777"})
    monkeypatch.delenv("EIEL_AGENTS_CODEX_TIMEOUT_S", raising=False)

    assert server_agents._default_codex_timeout_s() == 777


def test_codex_worker_caps_use_runtime_policy(monkeypatch, tmp_path: Path) -> None:
    runtime_policy.load_runtime_policy.cache_clear()
    monkeypatch.setattr(
        runtime_policy,
        "load_runtime_policy",
        lambda: {
            "agents.codex_max_workers": "222",
            "agents.codex_max_workers_per_role": "66",
        },
    )
    monkeypatch.setattr(server_agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_CODEX_TURN_CMD", "cat")
    monkeypatch.delenv("EIEL_CODEX_MAX_WORKERS", raising=False)
    monkeypatch.delenv("EIEL_CODEX_MAX_WORKERS_PER_ROLE", raising=False)
    monkeypatch.setattr(server_agents, "_CODEX_MANAGER", None)

    mgr = server_agents._codex_manager()

    assert mgr.max_workers == 222
    assert mgr.max_workers_per_role == 66

    monkeypatch.setattr(server_agents, "_CODEX_MANAGER", None)


def test_existing_codex_manager_reconfigures_live_from_runtime_policy(monkeypatch, tmp_path: Path) -> None:
    values = {
        "agents.codex_max_workers": "111",
        "agents.codex_max_workers_per_role": "22",
    }
    runtime_policy.load_runtime_policy.cache_clear()
    monkeypatch.setattr(runtime_policy, "load_runtime_policy", lambda: dict(values))
    monkeypatch.setattr(server_agents, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("EIEL_CODEX_TURN_CMD", "cat")
    monkeypatch.delenv("EIEL_CODEX_MAX_WORKERS", raising=False)
    monkeypatch.delenv("EIEL_CODEX_MAX_WORKERS_PER_ROLE", raising=False)
    monkeypatch.setattr(server_agents, "_CODEX_MANAGER", None)

    mgr1 = server_agents._codex_manager()
    assert mgr1.max_workers == 111
    assert mgr1.max_workers_per_role == 22

    values["agents.codex_max_workers"] = "333"
    values["agents.codex_max_workers_per_role"] = "77"

    mgr2 = server_agents._codex_manager()
    assert mgr2 is mgr1
    assert mgr2.max_workers == 333
    assert mgr2.max_workers_per_role == 77

    monkeypatch.setattr(server_agents, "_CODEX_MANAGER", None)


def test_orchestrator_runtime_defaults_reflect_current_policy(monkeypatch) -> None:
    runtime_policy.load_runtime_policy.cache_clear()
    monkeypatch.setattr(
        runtime_policy,
        "load_runtime_policy",
        lambda: {
            "agents.workflow.builder_slice_timeout_s": "444",
            "agents.workflow.builder_progress_timeout_s": "99",
            "agents.workflow.control_mode": "agentic",
            "agents.workflow.max_infra_retries": "7",
        },
    )

    defaults = orch.current_runtime_defaults()

    assert defaults["max_infra_retries"] == 7
    assert defaults["role_slice_timeouts"]["builder"] == 444
    assert defaults["role_progress_timeouts"]["builder"] == 99
    assert defaults["control_mode"] == "agentic"


def test_agent_tech_resolution_prefers_explicit_selection() -> None:
    assert server_agents._resolve_agent_tech({"agent_tech": "codex", "model": "deepseek-reasoner"}) == "codex"


def test_agent_tech_resolution_infers_provider_from_model_name(monkeypatch) -> None:
    monkeypatch.setattr(server_agents, "_agents_backend", lambda: "llm")

    assert server_agents._resolve_agent_tech({"model": "deepseek-reasoner"}) == "deepseek"
    assert server_agents._resolve_agent_tech({"model": "claude-haiku-4-5"}) == "claude"
    assert server_agents._resolve_agent_tech({"model": "gpt-5.4"}) == "openai"


def test_deepseek_oneshot_caps_transport_timeout(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = list(cmd)
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"choices":[{"message":{"content":"ok"}}]}', stderr="")

    monkeypatch.setattr(server_agents.shutil, "which", lambda name: "/usr/bin/curl" if name == "curl" else "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds_test_key")
    monkeypatch.setattr(server_agents.subprocess, "run", _fake_run)

    result = server_agents._run_deepseek_oneshot(
        model_input="ping",
        instructions="respond",
        model="deepseek-reasoner",
        timeout_s=1800,
    )

    assert result["ok"] is True
    assert captured["timeout"] == 95
    assert "--connect-timeout" in captured["cmd"]
    assert "--max-time" in captured["cmd"]
    max_time_idx = captured["cmd"].index("--max-time")
    assert captured["cmd"][max_time_idx + 1] == "90"
