from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from experiments_shared.action_plane import operator_interface
from infra.webui.ledger.ledger_manager import ChangeLedger
from tools.bin import plane


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _offline_embedding_backend(monkeypatch):
    # A developer's running provider must not change routing or receive fixtures.
    # The online-backend tests install their explicit synthetic replacements.
    monkeypatch.setattr(operator_interface, "_ollama_embed_is_online", lambda: False)
    def reject_provider_call(texts):
        raise AssertionError("Tests must replace the embedding provider")
    monkeypatch.setattr(operator_interface, "_ollama_embed_texts", reject_provider_call)


def _bootstrap_minimal_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO_ROOT / "governance" / "contracts", root / "governance" / "contracts")
    for relative in [
        "infra/bin/patch_validation_helper.py",
        "infra/bin/apply_patch",
        "infra/bin/safe_exec",
        "infra/bin/scheduler_health_check.sh",
        "infra/bin/scheduler_watchdog.py",
        "infra/bin/server_status.sh",
        "infra/bin/schedule_job.py",
    ]:
        source = REPO_ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return root


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True, capture_output=True, text=True)


def _build_patch(root: Path, relative_path: str, before: str, after: str) -> Path:
    file_path = root / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(before, encoding="utf-8")
    subprocess.run(["git", "add", relative_path], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=root, check=True, capture_output=True, text=True)
    file_path.write_text(after, encoding="utf-8")
    diff = subprocess.run(["git", "diff", "--", relative_path], cwd=root, check=True, capture_output=True, text=True).stdout
    patch_path = root / "change.patch"
    patch_path.write_text(diff, encoding="utf-8")
    subprocess.run(["git", "checkout", "--", relative_path], cwd=root, check=True, capture_output=True, text=True)
    return patch_path


def test_plane_cli_lists_implemented_verbs(capsys) -> None:
    rc = plane.main(["verbs", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    verbs = {row["operator_verb"] for row in payload["implemented_verbs"]}
    assert "apply incoming patch" in verbs
    assert "assess item proof" in verbs
    assert "inspect job" in verbs


def test_plane_cli_request_plans_patch_route(tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    patch_path = repo_root / "sample.patch"
    patch_path.write_text("diff --git a/a.txt b/a.txt\n", encoding="utf-8")
    rc = plane.main([
        "request",
        "apply incoming patch",
        "--repo-root",
        str(repo_root),
        "--patch-path",
        str(patch_path),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan"
    assert payload["selection"]["action_id"] == "patch_ingest_repair_v1"
    assert payload["command_parse"]["selected_action_id"] == "patch_ingest_repair_v1"
    assert payload["command_parse"]["agreement_with_final_selection"] is True
    assert payload["supported"] is True


def test_plane_plan_patch_prompt_without_attached_patch_still_routes_to_patch_family() -> None:
    payload = operator_interface.plan_operator_request_v1({"request_text": "apply this patch"})
    assert payload["selection"]["action_id"] == "patch_ingest_repair_v1"
    assert payload["command_parse"]["selected_action_id"] == "patch_ingest_repair_v1"
    assert payload["selection"]["selection_origin"] in {"semantic_router", "commparse_heuristic", "commparse_direct", "verb_alias", "phase1_backstop"}


def test_plane_plan_semantic_router_handles_plain_next_intent_sheet_query() -> None:
    operator_interface._OLLAMA_PROBE_STATE["checked_at"] = 0.0
    payload = operator_interface.plan_operator_request_v1({"request_text": "what is the next intent sheet?"})
    assert payload["selection"]["action_id"] == "implementation_prepare_v1"
    assert payload["selection"]["selection_origin"] in {"semantic_embedding", "semantic_router"}
    assert payload["command_parse"]["selected_action_id"] == "implementation_prepare_v1"


def test_plane_plan_semantic_router_handles_next_pis_query() -> None:
    operator_interface._OLLAMA_PROBE_STATE["checked_at"] = 0.0
    payload = operator_interface.plan_operator_request_v1({"request_text": "which PIS is next?"})
    assert payload["selection"]["action_id"] == "implementation_prepare_pis_v1"
    assert payload["selection"]["selection_origin"] in {"semantic_embedding", "semantic_router"}


def test_plane_plan_embedding_router_short_circuits_when_ollama_offline(monkeypatch) -> None:
    monkeypatch.setattr(operator_interface, "_ollama_embed_is_online", lambda: False)
    payload = operator_interface.plan_operator_request_v1({"request_text": "what is the next intent sheet?"})
    assert payload["selection"]["action_id"] == "implementation_prepare_v1"
    assert payload["selection"]["selection_origin"] == "semantic_router"


def test_plane_plan_offline_router_handles_service_recover_paraphrase(monkeypatch) -> None:
    monkeypatch.setattr(operator_interface, "_ollama_embed_is_online", lambda: False)
    payload = operator_interface.plan_operator_request_v1({"request_text": "recover the service"})
    assert payload["selection"]["action_id"] == "service_recover_v1"
    assert payload["selection"]["selection_origin"] == "semantic_router"


def test_plane_plan_proof_gap_request_resolves_family_progress_uid4_to_target_pis(tmp_path: Path) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    ledger = ChangeLedger(repo_root=repo_root)
    ledger.create_item(
        title="Builder and Verifier Prompt Contract",
        slug="builder-and-verifier-prompt",
        uid_override="PIS-20260411-180354Z-D4C5",
        pis_markdown="# acceptance\n\n- prove the family honestly\n",
        project_id="causyl",
    )

    payload = operator_interface.plan_operator_request_v1(
        {
            "request_text": "family_progress: PIS implemented: 0 of 4, verified: 1 of 6 (D4C5 in progress)",
        },
        repo_root=repo_root,
    )

    assert payload["selection"]["action_id"] == "pis_assess_proof_gap_v1"
    assert operator_interface._resolved_target_uid(
        {"request_text": "family_progress: PIS implemented: 0 of 4, verified: 1 of 6 (D4C5 in progress)"},
        repo_root=repo_root,
    ) == "PIS-20260411-180354Z-D4C5"


def test_plane_plan_offline_router_handles_failed_and_blocked_job_paraphrases(monkeypatch) -> None:
    monkeypatch.setattr(operator_interface, "_ollama_embed_is_online", lambda: False)
    failed = operator_interface.plan_operator_request_v1({"request_text": "failed job nightly_job_001"})
    blocked = operator_interface.plan_operator_request_v1({"request_text": "blocked job nightly_job_001"})
    assert failed["selection"]["action_id"] == "job_inspect_bounded_recovery_v1"
    assert blocked["selection"]["action_id"] == "job_inspect_bounded_recovery_v1"
    assert failed["selection"]["selection_origin"] == "semantic_router"
    assert blocked["selection"]["selection_origin"] == "semantic_router"


def test_plane_plan_offline_router_handles_bundle_import_paraphrases(monkeypatch) -> None:
    monkeypatch.setattr(operator_interface, "_ollama_embed_is_online", lambda: False)
    intent_payload = operator_interface.plan_operator_request_v1({"request_text": "bring in the intent sheet bundle from downloads"})
    pis_payload = operator_interface.plan_operator_request_v1({"request_text": "bring in the PIS bundle from downloads"})
    assert intent_payload["selection"]["action_id"] == "repo_import_intent_sheet_zip_v1"
    assert pis_payload["selection"]["action_id"] == "repo_import_pis_zip_v1"
    assert intent_payload["selection"]["selection_origin"] == "semantic_router"
    assert pis_payload["selection"]["selection_origin"] == "semantic_router"


def test_plane_plan_patch_prompt_does_not_fall_into_generic_blocked_chain_heuristic(monkeypatch) -> None:
    monkeypatch.setattr(operator_interface, "_ollama_embed_is_online", lambda: False)
    payload = operator_interface.plan_operator_request_v1({"request_text": "what should I do with this patch"})
    assert payload["selection"]["action_id"] == "patch_ingest_repair_v1"
    assert payload["selection"]["selection_origin"] == "commparse_heuristic"


def test_plane_plan_embedding_router_uses_ollama_when_online(monkeypatch) -> None:
    monkeypatch.setattr(operator_interface, "_ollama_embed_is_online", lambda: True)
    monkeypatch.setattr(
        operator_interface,
        "_EMBEDDED_ROUTE_PROTOTYPES",
        {
            "implementation_prepare_v1": ["what is the next intent sheet to implement"],
            "implementation_prepare_pis_v1": ["which PIS should I implement next"],
            "scheduler_watch_v1": ["watch scheduler"],
        },
    )

    def fake_embed(texts):
        assert texts[-1] == "what is the next intent sheet?"
        return [
            [1.0, 0.0],
            [0.0, 1.0],
            [0.0, 0.1],
            [1.0, 0.0],
        ]

    monkeypatch.setattr(operator_interface, "_ollama_embed_texts", fake_embed)
    payload = operator_interface.plan_operator_request_v1({"request_text": "what is the next intent sheet?"})
    assert payload["selection"]["action_id"] == "implementation_prepare_v1"
    assert payload["selection"]["selection_origin"] == "semantic_embedding"
    assert payload["selection"]["matched_rule_id"] == "embedding_semantic_match"


def test_plane_plan_embedding_router_falls_back_when_embed_call_fails(monkeypatch) -> None:
    monkeypatch.setattr(operator_interface, "_ollama_embed_is_online", lambda: True)
    monkeypatch.setattr(operator_interface, "_ollama_embed_texts", lambda texts: (_ for _ in ()).throw(RuntimeError("offline now")))
    payload = operator_interface.plan_operator_request_v1({"request_text": "what is the next intent sheet?"})
    assert payload["selection"]["action_id"] == "implementation_prepare_v1"
    assert payload["selection"]["selection_origin"] == "semantic_router"


def test_plane_plan_integrate_pis_from_downloads_routes_to_zip_import() -> None:
    payload = operator_interface.plan_operator_request_v1({"request_text": "integrate pis from downloads"})
    assert payload["selection"]["action_id"] == "repo_import_pis_zip_v1"


def test_plane_plan_explain_blocked_state_without_report_routes_to_operator_surface() -> None:
    payload = operator_interface.plan_operator_request_v1({"request_text": "explain this blocked state"})
    assert payload["selection"]["action_id"] == "blocked_chain_explain_and_recommend_v1"


def test_plane_plan_campaign_breakdown_prompt_goes_codex_first_instead_of_proof_gap() -> None:
    payload = operator_interface.plan_operator_request_v1(
        {
            "request_text": "Create a job that breaks down IS-20260403-212913Z-5817 into a parent PIS, then children PISes, and then schedules their implementation. Use a builder-verifier loop (build, verify, build, finish)."
        }
    )
    assert payload["supported"] is False
    assert payload["selection"]["unsupported_reason"] == "multi_step_campaign_planning_requires_codex"


def test_plane_plan_pause_the_scheduler_routes_to_scheduler_pause() -> None:
    payload = operator_interface.plan_operator_request_v1({"request_text": "pause the scheduler"})
    assert payload["selection"]["action_id"] == "scheduler_pause_v1"


def test_plane_force_codex_override_bypasses_deterministic_route(tmp_path: Path) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    payload = operator_interface.plan_operator_request_v1(
        {
            "request_text": "review this implementation",
            "force_codex": True,
            "allow_fallback": True,
        },
        repo_root=repo_root,
    )
    assert payload["supported"] is False
    assert payload["selection"]["unsupported_reason"] == "force_codex_override"


def test_plane_validate_implementation_with_target_uid_routes_to_deterministic_slice() -> None:
    payload = operator_interface.plan_operator_request_v1(
        {
            "request_text": "validate the implementation for PIS-20260405-041630Z-B8E2",
            "target_uid": "PIS-20260405-041630Z-B8E2",
        }
    )
    assert payload["supported"] is True
    assert payload["selection"]["action_id"] == "implementation_validate_v1"
    assert payload["selection"]["matched_rule_id"] == "heuristic_implementation_validate_deterministic_slice"


def test_implementation_validate_infers_target_from_active_ledger_session_artifact(tmp_path: Path, monkeypatch) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    first = repo_root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260406-053813Z-9C4B__first/artifacts/agent_chat/session__builder.jsonl"
    second = repo_root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260406-053813Z-9F91__second/artifacts/agent_chat/session__builder.jsonl"
    first.parent.mkdir(parents=True, exist_ok=True)
    second.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("{}\n", encoding="utf-8")
    second.write_text("{}\n", encoding="utf-8")
    os.utime(first, (1, 1))
    os.utime(second, (2, 2))

    monkeypatch.delenv("EIEL_LAB_SESSION_ID", raising=False)
    monkeypatch.delenv("EIEL_LABSH_SESSION_ID", raising=False)

    result = operator_interface._execute_implementation_validate_v1(
        {"request_text": "validate the implementation"},
        repo_root=repo_root,
    )

    assert result["resolved_target_uid"] == "PIS-20260406-053813Z-9F91"
    assert result["resolved_target_source"] == "active_ledger_session_artifact"
    assert result["report"]["action_family"] == "status_reconcile"
    assert result["report"]["final_chain_outcome"] == "completed_with_proof_gap"


def test_known_failure_codex_message_mentions_commparse_improvement() -> None:
    message = operator_interface._build_known_failure_codex_message(
        {"request_text": "server status", "authority_scope": "read_only"},
        action_id="server_status_v1",
        failure_reason="bad_route",
    )
    assert "update commparse" in message.lower()


def test_learning_from_fallback_events_emits_commparse_signal_for_force_override(tmp_path: Path) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    learning = operator_interface._learning_from_fallback_events(
        {
            "fallback_events": [
                {
                    "action_family": "operator_surface_routing",
                    "intent": "review this implementation",
                    "failure_class": "force_codex_override",
                    "deterministic_steps_attempted": ["request_decomposition_v1", "commparse_v1", "operator_router_v1"],
                    "fallback_reason": "unsupported_request_surface_gap",
                    "commparse_related_gap": True,
                    "selection_origin": "commparse_heuristic",
                    "pre_escalation_checklist": {
                        "deterministic_path_attempted": True,
                        "recovery_attempted": False,
                        "evidence_gathered": True,
                    },
                    "post_fallback_outcome": "success_after_codex_fallback",
                    "solution_path_signature": "operator_surface::review this implementation",
                    "bounded_authority_safe": True,
                    "validator_path_clear": True,
                    "expected_future_codex_cost": 3.0,
                    "operator_friction_cost": 2.0,
                    "estimated_build_cost": 2.0,
                    "estimated_maintenance_cost": 0.5,
                }
            ]
        },
        repo_root=repo_root,
    )
    signals = list(learning.get("commparse_improvement_signals") or [])
    assert signals
    assert signals[0]["target"] == "commparse"
    assert signals[0]["failure_class"] == "force_codex_override"


def test_learning_from_fallback_events_uses_recorded_history_for_opportunity(tmp_path: Path) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    seed = {
        "action_family": "operator_surface_routing",
        "intent": "server restart",
        "failure_class": "unsupported_request",
        "deterministic_steps_attempted": ["request_decomposition_v1", "commparse_v1", "operator_router_v1"],
        "fallback_reason": "unsupported_request_surface_gap",
        "commparse_related_gap": True,
        "selection_origin": "commparse_heuristic",
        "pre_escalation_checklist": {
            "deterministic_path_attempted": True,
            "recovery_attempted": False,
            "evidence_gathered": True,
        },
        "post_fallback_outcome": "success_after_codex_fallback",
        "solution_path_signature": "operator_surface::server_restart",
        "bounded_authority_safe": True,
        "validator_path_clear": True,
        "expected_future_codex_cost": 8.0,
        "operator_friction_cost": 4.0,
        "estimated_build_cost": 1.0,
        "estimated_maintenance_cost": 0.5,
    }
    operator_interface.record_fallback_event_v1(seed, repo_root=repo_root)
    learning = operator_interface._learning_from_fallback_events(
        {"fallback_events": [dict(seed)]},
        repo_root=repo_root,
    )
    opportunity = dict(learning.get("improvement_opportunity") or {})
    assert opportunity["decision"] == "promote_opportunity"
    assert len(list(learning.get("aggregated_fallback_events") or [])) >= 2


def test_plane_cli_request_executes_patch_apply(tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    _init_git_repo(repo_root)
    patch_path = _build_patch(repo_root, "hello.txt", "hello\n", "hello world\n")
    rc = plane.main([
        "request",
        "apply incoming patch",
        "--execute",
        "--repo-root",
        str(repo_root),
        "--patch-path",
        str(patch_path),
        "--expected-target-file",
        "hello.txt",
        "--validation-command",
        "git diff -- hello.txt",
        "--verifier-verdict",
        "PASS",
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["supported"] is True
    assert payload["terminal_class"] in {"success_deterministic", "success_after_deterministic_recovery"}
    assert payload["operator_surface"]["status_class"] == "pass"
    assert (repo_root / "hello.txt").read_text(encoding="utf-8") == "hello world\n"


def test_plane_cli_run_phase3_action_directly(tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    report_path = repo_root / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "action_family": "patch_apply",
                "status": "blocked",
                "terminal_reason": "proof_gap_present",
                "step_records": [{"proof_gaps": ["missing_validation_commands"]}],
            }
        ),
        encoding="utf-8",
    )
    rc = plane.main([
        "run",
        "--action-id",
        "blocked_chain_explain_and_recommend_v1",
        "--repo-root",
        str(repo_root),
        "--report-file",
        str(report_path),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["supported"] is True
    assert payload["selection"]["action_id"] == "blocked_chain_explain_and_recommend_v1"
    assert payload["operator_surface"]["status_class"] == "blocked"
    assert payload["operator_surface"]["recommended_actions"]


def test_plane_cli_request_verify_outcome_routes_to_status_reconcile(tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    rc = plane.main([
        "request",
        "verify outcome",
        "--execute",
        "--repo-root",
        str(repo_root),
        "--target-uid",
        "PIS-EXAMPLE",
        "--current-status",
        "running",
        "--expected-status",
        "blocked",
        "--validation-command",
        "python -c \"print('status')\"",
        "--verifier-verdict",
        "PASS",
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selection"]["action_id"] == "status_reconcile_v1"
    assert payload["terminal_class"] in {"success_deterministic", "success_after_deterministic_recovery"}
    assert payload["operator_surface"]["status_class"] == "pass"


def test_plane_cli_request_explain_blocked_state_uses_operator_flow(tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    report_path = repo_root / "blocked_report.json"
    report_path.write_text(
        json.dumps(
            {
                "chain_id": "patch_apply_chain_v1::1",
                "playbook_id": "patch_apply_chain_v1",
                "action_family": "patch_apply",
                "final_chain_outcome": "blocked",
                "final_verifier_verdict": "",
                "terminal_reason": "patch_contract_invalid",
                "step_records": [{"proof_gaps": ["missing_validation_commands"]}],
                "failure_escalation_log": [],
            }
        ),
        encoding="utf-8",
    )
    rc = plane.main([
        "request",
        "explain blocked state",
        "--execute",
        "--repo-root",
        str(repo_root),
        "--report-file",
        str(report_path),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selection"]["action_id"] == "blocked_chain_explain_and_recommend_v1"
    assert payload["operator_surface"]["status_class"] == "blocked"
    assert payload["operator_surface"]["recommended_actions"][0]["action_id"] == "patch_apply_chain_v1"


def test_plane_cli_request_surfaces_codex_fallback_plan_when_blocked(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    patch_path = repo_root / "broken.patch"
    patch_path.write_text("diff --git a/missing.txt b/missing.txt\n", encoding="utf-8")

    def fake_turn(**kwargs):
        return {
            "ok": True,
            "exit_code": 0,
            "content": "Prepared bounded fallback plan for blocked patch.",
            "stderr": "",
            "stdout": "Prepared bounded fallback plan for blocked patch.",
            "usage": {"input_tokens": 4, "output_tokens": 8, "total_tokens": 12},
        }

    monkeypatch.setattr(operator_interface, "run_codex_oneshot", fake_turn)
    rc = plane.main([
        "request",
        "apply incoming patch",
        "--execute",
        "--allow-fallback",
        "--repo-root",
        str(repo_root),
        "--patch-path",
        str(patch_path),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    fallback = payload["codex_fallback_request"]
    assert payload["selection"]["action_id"] == "patch_ingest_repair_v1"
    assert payload["terminal_class"] == "success_after_codex_fallback"
    assert payload["operator_surface"]["codex_transparency"]["codex_used"] is True
    assert fallback["action_family"] == "patch_apply"
    assert fallback["trigger_reason"] == "deterministic_execution_failed"


def test_plane_cli_request_plans_commit_route(capsys) -> None:
    rc = plane.main([
        "request",
        "commit",
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan"
    assert payload["selection"]["action_id"] == "repo_commit_tag_v1"
    assert payload["supported"] is True


def test_plane_cli_request_executes_commit_failure_with_review_required_surface(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)

    def fake_capture(*, command, cwd, stream_output):
        assert command == ["bash", "tools/git/git-commit-tag.sh"]
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=1,
            stdout="",
            stderr="governed commit blocked by recent-removal guard",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    rc = plane.main([
        "request",
        "commit",
        "--execute",
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selection"]["action_id"] == "repo_commit_tag_v1"
    assert payload["terminal_class"] == "blocked_requires_review"
    assert payload["operator_surface"]["status_class"] == "blocked"
    assert payload["operator_surface"]["status_summary"] == "governed commit blocked by recent-removal guard"
    assert payload["operator_surface"]["executive_surface"]["state"] == "REVIEW_REQUIRED"
    assert payload["operator_surface"]["primary_explanation"]["primary_reason"] == "governed commit blocked by recent-removal guard"
    assert payload["operator_surface"]["executive_surface"]["short_reason"] == "governed commit blocked by recent-removal guard"
    assert payload["operator_surface"]["executive_surface"]["primary_next_action"]["label"] == "Manual review required"


def test_plane_cli_request_plans_archive_weekly_route(capsys) -> None:
    rc = plane.main([
        "request",
        "archive weekly",
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan"
    assert payload["selection"]["action_id"] == "repo_archive_snapshot_v1"
    assert payload["supported"] is True


def test_plane_cli_request_plans_pis_zip_import_route(tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    zip_path = repo_root / "pises.zip"
    zip_path.write_text("zip", encoding="utf-8")
    rc = plane.main([
        "request",
        "integrate zipped PISes from Downloads directory",
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan"
    assert payload["selection"]["action_id"] == "repo_import_pis_zip_v1"
    assert payload["supported"] is True


def test_plane_cli_request_plans_new_surface_verbs(capsys) -> None:
    scenarios = {
        "server status": "server_status_v1",
        "server start": "server_start_v1",
        "server stop": "server_stop_v1",
        "server watch": "server_watch_v1",
        "server watchdog": "server_watchdog_v1",
        "recover service": "service_recover_v1",
        "run safe maintenance": "safe_maintenance_v1",
        "prepare implementation": "implementation_prepare_v1",
        "show commit journal": "repo_show_commit_journal_v1",
        "show recent commits": "repo_show_recent_commits_v1",
        "import intent sheet zip": "repo_import_intent_sheet_zip_v1",
        "inspect scheduler": "scheduler_inspect_v1",
        "recover scheduler": "scheduler_recover_v1",
        "scheduler disable all jobs": "scheduler_disable_all_v1",
        "scheduler enable all jobs": "scheduler_enable_all_v1",
        "scheduler watch": "scheduler_watch_v1",
        "scheduler pause": "scheduler_pause_v1",
        "scheduler resume": "scheduler_resume_v1",
        "inspect backlog": "backlog_inspect_v1",
        "verify implementation": "status_reconcile_v1",
    }
    for prompt, action_id in scenarios.items():
        rc = plane.main(["request", prompt, "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["mode"] == "plan"
        assert payload["selection"]["action_id"] == action_id
        assert payload["supported"] is True


def test_plane_cli_request_executes_server_status(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)

    def fake_capture(*, command, cwd, stream_output):
        assert command == ["bash", "infra/user/server-status"]
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="[server_status] ready pid=123\n[server_status] http_ready=1\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    rc = plane.main([
        "request",
        "server status",
        "--execute",
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selection"]["action_id"] == "server_status_v1"
    assert payload["terminal_class"] == "success_deterministic"
    assert payload["operator_surface"]["status_class"] == "pass"
    assert payload["operator_surface"]["status_summary"] == "Server status retrieved."


def test_plane_cli_request_plans_next_pises_prompt_to_implementation_prepare(capsys) -> None:
    rc = plane.main(["request", "what are the next PISes I should implement?", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan"
    assert payload["selection"]["action_id"] == "implementation_prepare_pis_v1"
    assert payload["supported"] is True


def test_plane_cli_request_plans_next_intent_sheet_to_implement_prompt_to_implementation_prepare(capsys) -> None:
    rc = plane.main(["request", "what is the next intent sheet to implement?", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan"
    assert payload["selection"]["action_id"] == "implementation_prepare_v1"
    assert payload["supported"] is True


def test_plane_cli_request_plans_next_pises_to_implement_prompt_to_pis_prepare(capsys) -> None:
    rc = plane.main(["request", "what are the next PISes to implement", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan"
    assert payload["selection"]["action_id"] == "implementation_prepare_pis_v1"
    assert payload["supported"] is True


def test_plane_cli_request_plans_implement_uid_prompt_to_recursive_implement(capsys) -> None:
    rc = plane.main(["request", "implement IS-20260403-212913Z-5817", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan"
    assert payload["selection"]["action_id"] == "implement_recursive_v1"
    assert payload["supported"] is True


def test_plane_cli_request_executes_recursive_implement_with_human_surface(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)

    def fake_capture(*, command, cwd, stream_output):
        assert command[:4] == ["python", "-m", "tools.bin.implement_recursive", "--uid"]
        assert command[4] == "IS-20260403-212913Z-5817"
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout=(
                "target_uid: IS-20260403-212913Z-5817\n"
                "resolved_kind: intent_sheet\n"
                "mode: decompose_parent_then_queue_children\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    rc = plane.main([
        "request",
        "implement IS-20260403-212913Z-5817",
        "--execute",
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selection"]["action_id"] == "implement_recursive_v1"
    assert payload["operator_surface"]["executive_surface"]["primary_next_action"]["label"] == "Review implementation chain"
    assert payload["operator_surface"]["executive_surface"]["short_reason"] == "Implementation chain was started with the default build-verify-repair contract."


def test_plane_cli_request_executes_recursive_implement_with_flags(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)

    def fake_capture(*, command, cwd, stream_output):
        assert command == [
            "python", "-m", "tools.bin.implement_recursive",
            "--uid", "U4E2",
            "--plan-only",
            "--decompose-only",
            "--max-children", "7",
            "--parallel", "safe",
            "--proof-profile", "full",
            "--close", "manual",
            "--start-now", "false",
        ]
        return subprocess.CompletedProcess(args=list(command), returncode=0, stdout="planned\n", stderr="")

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    rc = plane.main([
        "request",
        "implement u4e2",
        "--execute",
        "--repo-root",
        str(repo_root),
        "--plan-only",
        "--decompose-only",
        "--max-children", "7",
        "--parallel", "safe",
        "--proof-profile", "full",
        "--close", "manual",
        "--start-now", "false",
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selection"]["action_id"] == "implement_recursive_v1"


def test_plane_cli_request_plans_implement_suffix_prompt_to_recursive_implement(capsys) -> None:
    rc = plane.main(["request", "implement u4e2", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan"
    assert payload["selection"]["action_id"] == "implement_recursive_v1"
    assert payload["supported"] is True


def test_plane_cli_request_plans_implement_next_pis_prompt_to_recursive_implement(capsys) -> None:
    rc = plane.main(["request", "implement next PIS", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan"
    assert payload["selection"]["action_id"] == "implement_recursive_v1"
    assert payload["supported"] is True


def test_plane_cli_request_executes_implementation_prepare_with_human_surface(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)

    def fake_capture(*, command, cwd, stream_output):
        assert "--json" in command
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="1. IS-EXAMPLE | Example Intent | status=draft | prio=1 | wave=W0 | critical_path\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    rc = plane.main([
        "request",
        "what are the next PISes I should implement?",
        "--execute",
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selection"]["action_id"] == "implementation_prepare_pis_v1"
    assert payload["operator_surface"]["executive_surface"]["primary_next_action"]["label"] == "Review listed PIS items"
    assert payload["operator_surface"]["executive_surface"]["short_reason"] == "Next implementation candidates are listed below."


def test_plane_cli_request_executes_pis_prepare_with_human_surface(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)

    def fake_capture(*, command, cwd, stream_output):
        assert "--json" in command
        assert command[:4] == ["python", "-m", "tools.bin.pis_next", "--limit"]
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="1. PIS-EXAMPLE | Example PIS | status=draft | prio=1\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    rc = plane.main([
        "request",
        "what are the next PISes to implement",
        "--execute",
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selection"]["action_id"] == "implementation_prepare_pis_v1"
    assert payload["operator_surface"]["executive_surface"]["primary_next_action"]["label"] == "Review listed PIS items"


def test_plane_cli_unsupported_request_uses_codex_fallback(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)

    def fake_turn(**kwargs):
        return {
            "ok": True,
            "exit_code": 0,
            "content": "Handled unsupported request and added support path.",
            "stderr": "",
            "stdout": "Handled unsupported request and added support path.",
            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        }

    monkeypatch.setattr(operator_interface, "run_codex_oneshot", fake_turn)
    rc = plane.main([
        "request",
        "some brand new operator verb",
        "--execute",
        "--allow-fallback",
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["supported"] is True
    assert payload["terminal_class"] == "success_after_codex_fallback"
    assert payload["operator_surface"]["codex_transparency"]["codex_used"] is True
    assert payload["learning_artifacts"]["fallback_events"][0]["action_family"] == "operator_surface_routing"


def test_plane_cli_what_is_the_next_intent_sheet_routes_to_implementation_prepare(capsys) -> None:
    rc = plane.main(["request", "what is the next intent sheet?", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selection"]["action_id"] == "implementation_prepare_v1"


def test_plane_cli_implement_the_next_intent_sheet_routes_to_recursive_implement(capsys) -> None:
    rc = plane.main(["request", "implement the next intent sheet", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selection"]["action_id"] == "implement_recursive_v1"


def test_plane_cli_implement_the_next_intent_sheet_with_politeness_routes_to_recursive_implement(capsys) -> None:
    rc = plane.main(["request", "implement the next intent sheet please", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selection"]["action_id"] == "implement_recursive_v1"


def test_plane_command_parse_reports_disagreement_when_parsers_conflict(monkeypatch) -> None:
    monkeypatch.setattr(operator_interface, "_direct_selection", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        operator_interface,
        "_embedding_semantic_selection",
        lambda *args, **kwargs: {
            "action_id": "implementation_prepare_v1",
            "operator_verb": "prepare implementation",
            "confidence_bucket": "medium",
            "matched_rule_id": "embedding_semantic_match",
            "fallback_eligibility_class": "none",
            "required_evidence_gatherers": [],
            "supported": True,
        },
    )
    monkeypatch.setattr(
        operator_interface,
        "_semantic_selection",
        lambda *args, **kwargs: {
            "action_id": "implement_recursive_v1",
            "operator_verb": "implement",
            "confidence_bucket": "medium",
            "matched_rule_id": "semantic_implement_next_intent_sheet",
            "fallback_eligibility_class": "none",
            "required_evidence_gatherers": [],
            "supported": True,
        },
    )
    monkeypatch.setattr(operator_interface, "_heuristic_selection", lambda *args, **kwargs: {})
    monkeypatch.setattr(operator_interface, "_verb_alias_selection", lambda *args, **kwargs: {})
    monkeypatch.setattr(operator_interface, "_phase1_backstop_selection", lambda *args, **kwargs: {})
    payload = operator_interface.plan_operator_request_v1({"request_text": "implement the next intent sheet"})
    assert payload["selection"]["action_id"] == "implement_recursive_v1"
    assert payload["command_parse"]["disagreement_present"] is True
    assert payload["command_parse"]["agreement_with_final_selection"] is True
    assert payload["command_parse"]["candidate_action_ids"] == ["implement_recursive_v1", "implementation_prepare_v1"]


def test_plane_explicit_action_id_remains_authoritative_over_parser_consensus() -> None:
    payload = operator_interface.plan_operator_request_v1(
        {
            "request_text": "what is the next intent sheet?",
            "action_id": "scheduler_pause_v1",
        }
    )
    assert payload["selection"]["action_id"] == "scheduler_pause_v1"
    assert payload["selection"]["selection_origin"] == "commparse_direct"
    assert payload["command_parse"]["selected_action_id"] == "scheduler_pause_v1"
    assert payload["command_parse"]["agreement_with_final_selection"] is True


def test_plane_cli_known_action_exception_uses_codex_fallback(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)

    def fake_turn(**kwargs):
        return {
            "ok": True,
            "exit_code": 0,
            "content": "Recovered from deterministic failure via Codex.",
            "stderr": "",
            "stdout": "Recovered from deterministic failure via Codex.",
            "usage": {"input_tokens": 5, "output_tokens": 10, "total_tokens": 15},
        }

    def boom(request, *, repo_root=None):
        raise ValueError("pis_assess_proof_gap_v1 requires target_pis_uid or pis_markdown")

    monkeypatch.setattr(operator_interface, "run_codex_oneshot", fake_turn)
    monkeypatch.setitem(operator_interface._DIRECT_ACTIONS, "pis_assess_proof_gap_v1", boom)
    rc = plane.main([
        "request",
        "assess item",
        "--execute",
        "--allow-fallback",
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["supported"] is True
    assert payload["terminal_class"] == "success_after_codex_fallback"
    assert payload["operator_surface"]["codex_transparency"]["codex_used"] is True
    assert payload["codex_fallback_request"]["trigger_reason"] == "deterministic_execution_failed"
    assert payload["learning_artifacts"]["fallback_events"][0]["action_family"] == "pis_assess_proof_gap"


def test_plane_cli_blocked_patch_terminal_uses_codex_fallback(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    patch = repo_root / "change.patch"
    patch.write_text("diff --git a/a.txt b/a.txt\n", encoding="utf-8")

    def fake_turn(**kwargs):
        return {
            "ok": True,
            "exit_code": 0,
            "content": "Resolved patch conflict via Codex fallback and added a bounded repair path.",
            "stderr": "",
            "stdout": "Resolved patch conflict via Codex fallback and added a bounded repair path.",
            "usage": {"input_tokens": 7, "output_tokens": 14, "total_tokens": 21},
        }

    def blocked_patch(request, *, repo_root=None):
        return {
            "ok": False,
            "playbook_id": "patch_apply_chain_v1",
            "report": {
                "final_chain_outcome": "blocked",
                "terminal_reason": "git_apply_conflict",
                "final_verifier_verdict": "",
                "step_records": [],
            },
        }

    def fake_classify(payload, *, repo_root=None, contract_repo_root=None):
        return {
            "schema_version": "plane_request_classification_v1",
            "surface_name": "the plane",
            "request_text": str(payload.get("request_text") or ""),
            "decomposition": {},
            "selection": {
                "action_id": "patch_apply_chain_v1",
                "operator_verb": "patch apply chain v1",
                "confidence_bucket": "medium",
                "matched_rule_id": "test_forced_patch_apply_chain",
                "fallback_eligibility_class": "allowed",
                "required_evidence_gatherers": [],
                "supported": True,
            },
        }

    monkeypatch.setattr(operator_interface, "run_codex_oneshot", fake_turn)
    monkeypatch.setattr(operator_interface, "classify_operator_request_v1", fake_classify)
    monkeypatch.setitem(operator_interface._DIRECT_ACTIONS, "patch_apply_chain_v1", blocked_patch)
    rc = plane.main([
        "request",
        "fix this patch",
        "--execute",
        "--allow-fallback",
        "--patch-path",
        str(patch),
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["supported"] is True
    assert payload["terminal_class"] == "success_after_codex_fallback"
    assert payload["operator_surface"]["codex_transparency"]["codex_used"] is True
    assert payload["codex_fallback_request"]["failure_class"] == "git_apply_conflict"
    assert payload["learning_artifacts"]["fallback_events"][0]["action_family"] == "patch_apply"


def test_plane_cli_semantic_implementation_review_becomes_deterministic_intake(tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    rc = plane.main([
        "request",
        "validate the implementation",
        "--execute",
        "--allow-fallback",
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["supported"] is True
    assert payload["terminal_class"] == "blocked_requires_review"
    assert payload["operator_surface"]["executive_surface"]["state"] == "REVIEW_REQUIRED"
    assert payload["execution"]["terminal_reason"] == "missing_target_uid_for_implementation_validation"


def test_plane_cli_plan_semantic_implementation_review_routes_to_validation_intake(tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    rc = plane.main([
        "request",
        "review this implementation",
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["supported"] is True
    assert payload["selection"]["action_id"] == "implementation_validate_v1"


def test_plane_cli_resume_scheduler_job_routes_to_scheduler_resume(tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    rc = plane.main([
        "request",
        "resume scheduler job",
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["supported"] is True
    assert payload["selection"]["action_id"] == "scheduler_resume_v1"


def test_plane_cli_implementation_validate_does_not_false_pass_when_reconcile_has_no_validation_commands(tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    rc = plane.main([
        "request",
        "validate the implementation for PIS-20260405-041630Z-B8E2",
        "--execute",
        "--target-uid",
        "PIS-20260405-041630Z-B8E2",
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["supported"] is True
    assert payload["execution"]["dispatch"]["request"]["action_family"] == "status_reconcile"
    assert payload["terminal_class"] == "proof_gap_present"
    assert payload["operator_surface"]["executive_surface"]["state"] == "REVIEW_REQUIRED"


def test_plane_cli_implementation_validate_executes_status_reconcile_family(tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    rc = plane.main([
        "request",
        "validate the implementation for PIS-20260405-041630Z-B8E2",
        "--execute",
        "--target-uid",
        "PIS-20260405-041630Z-B8E2",
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution"]["dispatch"]["request"]["action_family"] == "status_reconcile"
    assert payload["supported"] is True
    assert payload["execution"]["dispatch"]["routing"]["action_family"] == "status_reconcile"
    assert payload["selection"]["action_id"] == "implementation_validate_v1"


def test_plane_cli_codex_fallback_includes_recent_lab_session_context(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = _bootstrap_minimal_repo(tmp_path)
    session_id = "lab_test_context"
    session_dir = repo_root / "governance" / "agents" / "sessions" / "lab_front_door" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "session__lab.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schema_version": "lab_chat_message_v1",
                        "kind": "message",
                        "sender": "user",
                        "phase": "final",
                        "content": "please fix the same patch conflict",
                        "utc": "2026-04-06T00:00:00Z",
                    }
                ),
                json.dumps(
                    {
                        "schema_version": "lab_chat_message_v1",
                        "kind": "message",
                        "sender": "assistant",
                        "phase": "commentary",
                        "content": "I am checking the conflicting files first.",
                        "utc": "2026-04-06T00:00:01Z",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    seen = {}

    def fake_turn(**kwargs):
        seen["message"] = str(kwargs.get("message") or "")
        return {
            "ok": True,
            "exit_code": 0,
            "content": "Handled with context.",
            "stderr": "",
            "stdout": "Handled with context.",
            "usage": {"input_tokens": 9, "output_tokens": 12, "total_tokens": 21},
        }

    monkeypatch.setattr(operator_interface, "run_codex_oneshot", fake_turn)
    monkeypatch.setenv("EIEL_LAB_SESSION_ID", session_id)
    payload_path = repo_root / "force_codex_payload.json"
    payload_path.write_text(json.dumps({"force_codex": True}), encoding="utf-8")
    rc = plane.main([
        "request",
        "validate the implementation",
        "--execute",
        "--allow-fallback",
        "--payload-file",
        str(payload_path),
        "--repo-root",
        str(repo_root),
        "--json",
    ])
    assert rc == 0
    assert "Recent operator conversation context" in seen["message"]
    assert "please fix the same patch conflict" in seen["message"]
