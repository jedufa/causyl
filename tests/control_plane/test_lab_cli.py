from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from experiments_shared.action_plane import operator_interface
from infra.bin import lab


def test_lab_action_help_covers_every_supported_surface_action() -> None:
    catalog = operator_interface.build_operator_catalog_v1()
    action_ids = {row["action_id"] for row in catalog.get("supported_surface_verbs", [])}
    missing = sorted(action_id for action_id in action_ids if action_id not in lab._ACTION_HELP)
    assert missing == []


def test_lab_cli_autodetects_single_patch_and_executes(monkeypatch, tmp_path: Path, capsys) -> None:
    patch = tmp_path / "change.patch"
    patch.write_text("diff --git a/a.txt b/a.txt\n", encoding="utf-8")
    captured = {}

    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        captured["payload"] = dict(payload)
        captured["repo_root"] = str(repo_root)
        return {
            "supported": True,
            "operator_surface": {
                "executive_surface": {
                    "state": "BLOCKED",
                    "primary_next_action": {"label": "Apply reconciled patch"},
                    "confidence": "Medium",
                    "short_reason": "File mismatch detected; deterministic reconciliation available",
                },
                "recommendations": {"primary": {"label": "Apply reconciled patch", "reason": "Known deterministic repair path exists"}},
            },
            "summary": "blocked",
        }

    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    monkeypatch.setattr(lab, "_default_patch_search_roots", lambda cwd: [cwd])
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["fix this patch"])
    assert rc == 0
    assert captured["payload"]["patch_path"] == str(patch.resolve())
    assert captured["payload"]["authority_scope"] == "repo_mutation"
    out = capsys.readouterr().out
    assert "STATE: BLOCKED" in out
    assert "NEXT ACTION: Apply reconciled patch" in out


def test_lab_cli_requires_explicit_patch_when_multiple_exist(monkeypatch, tmp_path: Path, capsys) -> None:
    (tmp_path / "a.patch").write_text("diff --git a/a b/a\n", encoding="utf-8")
    (tmp_path / "b.diff").write_text("diff --git a/b b/b\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["fix this patch"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Multiple patch files were found" in err


def test_lab_cli_autodetects_single_patch_from_downloads(monkeypatch, tmp_path: Path, capsys) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    patch = downloads / "change.patch"
    patch.write_text("diff --git a/a.txt b/a.txt\n", encoding="utf-8")
    captured = {}

    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        captured["payload"] = dict(payload)
        return {
            "supported": True,
            "operator_surface": {
                "executive_surface": {
                    "state": "BLOCKED",
                    "primary_next_action": {"label": "Apply reconciled patch"},
                    "confidence": "Medium",
                    "short_reason": "Downloads patch detected",
                }
            },
            "summary": "blocked",
        }

    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    monkeypatch.setattr(lab, "_default_patch_search_roots", lambda cwd: [cwd, downloads])
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["apply this patch"])
    assert rc == 0
    assert captured["payload"]["patch_path"] == str(patch.resolve())
    out = capsys.readouterr().out
    assert "STATE: BLOCKED" in out


def test_lab_cli_requires_explicit_patch_when_current_dir_and_downloads_conflict(monkeypatch, tmp_path: Path, capsys) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (tmp_path / "a.patch").write_text("diff --git a/a b/a\n", encoding="utf-8")
    (downloads / "b.diff").write_text("diff --git a/b b/b\n", encoding="utf-8")
    monkeypatch.setattr(lab, "_default_patch_search_roots", lambda cwd: [cwd, downloads])
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["apply this patch"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Multiple patch files were found across the current directory and /mnt/c/home/user/Downloads" in err


def test_lab_cli_plan_mode_calls_plan(monkeypatch, tmp_path: Path, capsys) -> None:
    captured = {}

    def fake_plan(payload, *, repo_root=None, contract_repo_root=None):
        captured["payload"] = dict(payload)
        return {
            "supported": True,
            "command_parse": {
                "selected_action_id": "status_reconcile_v1",
                "execution_shape": "playbook_sequence",
                "summary": "Would call status_reconcile_v1.",
                "would_call_commands": ["execute_status_reconcile_v1(...)"],
                "agreement_with_final_selection": True,
            },
            "operator_surface": {
                "executive_surface": {
                    "state": "RUNNING",
                    "primary_next_action": {"label": "Apply patch"},
                    "confidence": "High",
                    "short_reason": "The plane would route this request.",
                }
            },
            "summary": "plan",
        }

    monkeypatch.setattr(lab, "plan_operator_request_v1", fake_plan)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["--plan", "verify outcome", "--current-status", "running", "--expected-status", "blocked"])
    assert rc == 0
    assert captured["payload"]["current_status"] == "running"
    assert captured["payload"]["expected_status"] == "blocked"
    out = capsys.readouterr().out
    assert "COMMPARSE: status_reconcile_v1" in out
    assert "STATE: RUNNING" in out


def test_lab_build_payload_strips_force_codex_and_enables_override(tmp_path: Path) -> None:
    args = lab.build_parser().parse_args([])
    payload = lab._build_payload(args, "force codex review this implementation", tmp_path)
    assert payload["request_text"] == "review this implementation"
    assert payload["force_codex"] is True
    assert payload["allow_fallback"] is True


def test_lab_split_chained_prompt_handles_commas_and_context() -> None:
    parts = lab._split_chained_prompt("pause the scheduler, inspect backlog, then resume it")
    assert parts == ["pause the scheduler", "inspect backlog", "scheduler resume"]


def test_lab_split_chained_prompt_contextualizes_status_followup() -> None:
    parts = lab._split_chained_prompt("restart the webui server and then show me status")
    assert parts == ["restart the webui server", "server status"]


def test_lab_split_chained_prompt_handles_after_that_and_do_this() -> None:
    parts = lab._split_chained_prompt("resume scheduler job. then do this: check status after that inspect backlog")
    assert parts == ["resume scheduler job", "inspect scheduler", "inspect backlog"]


def test_lab_split_chained_prompt_strips_lab_prefix_and_contextualizes_check_it() -> None:
    parts = lab._split_chained_prompt("restart the webui server. then do this: lab> check it")
    assert parts == ["restart the webui server", "server status"]


def test_lab_plan_does_not_require_patch_artifact_for_patch_prompt(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_plan(payload, *, repo_root=None, contract_repo_root=None):
        return {
            "supported": True,
            "command_parse": {
                "selected_action_id": "patch_ingest_repair_v1",
                "execution_shape": "deterministic_action",
                "summary": "Would call patch_ingest_repair_v1.",
                "would_call_commands": ["execute_patch_ingest_repair_v1(...)"],
                "agreement_with_final_selection": True,
            },
            "operator_surface": {
                "executive_surface": {
                    "state": "RUNNING",
                    "primary_next_action": {"label": "patch_ingest_repair_v1"},
                    "confidence": "High",
                    "short_reason": "The plane would route this request.",
                }
            },
            "summary": "plan",
        }

    monkeypatch.setattr(lab, "plan_operator_request_v1", fake_plan)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["--plan", "apply this patch"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "COMMPARSE: patch_ingest_repair_v1" in out


def test_lab_plan_does_not_require_zip_artifact_for_import_prompt(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_plan(payload, *, repo_root=None, contract_repo_root=None):
        return {
            "supported": True,
            "command_parse": {
                "selected_action_id": "repo_import_pis_zip_v1",
                "execution_shape": "deterministic_action",
                "summary": "Would call repo_import_pis_zip_v1.",
                "would_call_commands": ["python -m tools.bin.pis_import_zip --zip-path <path>"],
                "agreement_with_final_selection": True,
            },
            "operator_surface": {
                "executive_surface": {
                    "state": "RUNNING",
                    "primary_next_action": {"label": "repo_import_pis_zip_v1"},
                    "confidence": "High",
                    "short_reason": "The plane would route this request.",
                }
            },
            "summary": "plan",
        }

    monkeypatch.setattr(lab, "plan_operator_request_v1", fake_plan)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["--plan", "integrate pis from downloads"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "COMMPARSE: repo_import_pis_zip_v1" in out


def test_lab_plan_surfaces_parse_disagreement_details(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_plan(payload, *, repo_root=None, contract_repo_root=None):
        return {
            "supported": True,
            "command_parse": {
                "selected_action_id": "implementation_prepare_v1",
                "execution_shape": "single_script",
                "summary": "Parser disagreement detected; primary candidate is implementation_prepare_v1.",
                "would_call_commands": ["python -m tools.bin.intent_sheet_next --limit 5"],
                "agreement_with_final_selection": True,
                "disagreement_summary": "semantic_embedding=implementation_prepare_v1, semantic_router=implement_recursive_v1",
            },
            "operator_surface": {
                "executive_surface": {
                    "state": "RUNNING",
                    "primary_next_action": {"label": "implementation_prepare_v1"},
                    "confidence": "High",
                    "short_reason": "The plane would route this request.",
                }
            },
            "summary": "plan",
        }

    monkeypatch.setattr(lab, "plan_operator_request_v1", fake_plan)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["--plan", "implement the next intent sheet"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PARSE AGREEMENT: yes" in out
    assert "PARSE DISAGREEMENT: semantic_embedding=implementation_prepare_v1, semantic_router=implement_recursive_v1" in out


def test_lab_render_human_omits_parse_disagreement_when_all_candidates_agree(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_plan(payload, *, repo_root=None, contract_repo_root=None):
        return {
            "supported": True,
            "command_parse": {
                "selected_action_id": "service_recover_v1",
                "execution_shape": "script_sequence",
                "summary": "Service recover/restart sequence chosen from server wrappers.",
                "would_call_commands": ["bash infra/user/server-status", "bash infra/user/server-start"],
                "agreement_with_final_selection": True,
                "disagreement_summary": "",
            },
            "operator_surface": {
                "executive_surface": {
                    "state": "RUNNING",
                    "primary_next_action": {"label": "service_recover_v1"},
                    "confidence": "High",
                    "short_reason": "The plane would route this request.",
                }
            },
            "summary": "plan",
        }

    monkeypatch.setattr(lab, "plan_operator_request_v1", fake_plan)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["--plan", "recover service"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PARSE DISAGREEMENT:" not in out


def test_lab_validate_implementation_prompt_autodetects_target_uid(monkeypatch, tmp_path: Path, capsys) -> None:
    captured = {}

    def fake_plan(payload, *, repo_root=None, contract_repo_root=None):
        captured["payload"] = dict(payload)
        return {
            "supported": True,
            "command_parse": {
                "selected_action_id": "status_reconcile_v1",
                "execution_shape": "playbook_sequence",
                "summary": "Would call status_reconcile_v1.",
                "would_call_commands": ["execute_status_reconcile_v1(...)"],
                "agreement_with_final_selection": True,
            },
            "operator_surface": {
                "executive_surface": {
                    "state": "RUNNING",
                    "primary_next_action": {"label": "status_reconcile_v1"},
                    "confidence": "High",
                    "short_reason": "The plane would route this request.",
                }
            },
            "summary": "plan",
        }

    monkeypatch.setattr(lab, "plan_operator_request_v1", fake_plan)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["--plan", "validate the implementation for PIS-20260405-041630Z-B8E2"])
    assert rc == 0
    assert captured["payload"]["target_uid"] == "PIS-20260405-041630Z-B8E2"
    out = capsys.readouterr().out
    assert "COMMPARSE: status_reconcile_v1" in out


def test_lab_cli_defaults_to_fallback_for_execute_mode(monkeypatch, tmp_path: Path, capsys) -> None:
    captured = {}

    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        captured["payload"] = dict(payload)
        return {
            "supported": True,
            "operator_surface": {
                "executive_surface": {
                    "state": "PASS",
                    "primary_next_action": {"label": "Handled"},
                    "confidence": "Medium",
                    "short_reason": "Fallback enabled by default.",
                }
            },
            "summary": "ok",
        }

    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["some unsupported request"])
    assert rc == 0
    assert captured["payload"]["allow_fallback"] is True
    out = capsys.readouterr().out
    assert "STATE: PASS" in out


def test_lab_cli_no_fallback_disables_default(monkeypatch, tmp_path: Path, capsys) -> None:
    captured = {}

    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        captured["payload"] = dict(payload)
        return {
            "supported": False,
            "operator_surface": {
                "executive_surface": {
                    "state": "REVIEW_REQUIRED",
                    "primary_next_action": {"label": "Manual review required"},
                    "confidence": "Low",
                    "short_reason": "No fallback.",
                }
            },
            "summary": "no fallback",
        }

    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["--no-fallback", "some unsupported request"])
    assert rc == 1
    assert captured["payload"]["allow_fallback"] is False
    out = capsys.readouterr().out
    assert "STATE: REVIEW_REQUIRED" in out


def test_lab_cli_json_mode_prints_full_json(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        return {"supported": True, "summary": "ok", "operator_surface": {"executive_surface": {"state": "PASS"}}}

    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["--json", "explain blocked state"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["supported"] is True
    assert payload["operator_surface"]["executive_surface"]["state"] == "PASS"
    assert payload["front_door_payload"]["request_text"] == "explain blocked state"


def test_lab_cli_prints_codex_result_block_when_fallback_used(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        return {
            "supported": True,
            "summary": "Handled via Codex.",
            "execution": {"stdout": "Built the missing wrapper and validated it."},
            "operator_surface": {
                "executive_surface": {
                    "state": "PASS",
                    "primary_next_action": {"label": "Request handled via Codex fallback"},
                    "confidence": "Medium",
                    "short_reason": "Handled via Codex.",
                },
                "codex_transparency": {"codex_used": True},
            },
        }

    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["some unsupported request"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STATE: PASS" in out
    assert "CODEX RESULT:" in out
    assert "Built the missing wrapper and validated it." in out


def test_lab_cli_prints_codex_launch_message_for_blocked_patch_terminal(monkeypatch, tmp_path: Path, capsys) -> None:
    patch = tmp_path / "change.patch"
    patch.write_text("diff --git a/a.txt b/a.txt\n", encoding="utf-8")

    def fake_turn(**kwargs):
        return {
            "ok": True,
            "exit_code": 0,
            "content": "Resolved patch conflict via Codex fallback.",
            "stderr": "",
            "stdout": "Resolved patch conflict via Codex fallback.",
            "usage": {"input_tokens": 6, "output_tokens": 10, "total_tokens": 16},
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
    monkeypatch.setattr(lab, "_default_patch_search_roots", lambda cwd: [cwd])
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["fix this patch"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[the-plane] Launching Codex fallback for patch_apply_chain_v1: git_apply_conflict" in out
    assert "CODEX RESULT:" in out
    assert "Resolved patch conflict via Codex fallback." in out


def test_lab_cli_help_prompt_prints_usage(capsys) -> None:
    rc = lab.main(["help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Human-facing front door for the plane." in out
    assert "Examples:" in out
    assert "Known verbs:" in out
    assert "  - commit" in out
    assert "  - archive weekly" in out
    assert "Built-ins and modifiers:" in out
    assert "force codex <request>" in out
    assert 'lab "do commit then archive debug then archive weekly"' in out


def test_lab_cli_help_archive_prints_action_help(capsys) -> None:
    rc = lab.main(["help archive"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Verb Help: archive" in out
    assert "Action: repo_archive_snapshot_v1" in out
    assert "Known forms:" in out
    assert "  - archive debug" in out
    assert "Underlying commands:" in out
    assert "bash infra/bin/archive --weekly" in out


def test_lab_cli_help_implement_prints_action_help(capsys) -> None:
    rc = lab.main(["help implement"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Verb Help: implement" in out
    assert "Action: implement_recursive_v1" in out
    assert "Summary:" in out
    assert "Known forms:" in out
    assert "  - implement" in out
    assert "Supported arguments:" in out
    assert "  - --plan-only" in out
    assert "  - --decompose-only" in out
    assert "  - --max-children <N>" in out
    assert "  - --parallel safe|off" in out
    assert "  - --proof-profile focused|full" in out
    assert "  - --close auto|manual" in out
    assert "  - --start-now true|false" in out
    assert "Argument help:" in out
    assert 'lab "implement u4e2"' in out
    assert 'lab "implement next PIS"' in out
    assert 'lab "implement next intent"' in out
    assert "--plan-only" in out or "python -m tools.bin.implement_recursive -h" in out
    assert "python -m tools.bin.implement_recursive -h" in out


def test_lab_cli_help_inspect_lists_matching_verbs(capsys) -> None:
    rc = lab.main(["help inspect"])
    assert rc == 0
    out = capsys.readouterr().out
    assert 'Multiple verbs match "inspect":' in out
    assert "  - inspect scheduler" in out
    assert "  - inspect server" in out


def test_lab_cli_semantic_implementation_review_becomes_deterministic_intake(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["validate the implementation"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STATE: REVIEW_REQUIRED" in out
    assert "NEXT ACTION:" in out
    assert "CODEX RESULT:" not in out


def test_lab_cli_ctrl_c_returns_clean_cancel(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        raise KeyboardInterrupt()

    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["validate the implementation"])
    assert rc == 130
    err = capsys.readouterr().err
    assert "Canceled." in err


def test_lab_cli_writes_session_jsonl_and_bubble(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(lab, "_repo_root", lambda: repo_root)

    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        return {
            "supported": True,
            "summary": "handled",
            "selection": {"action_id": "server_status_v1"},
            "operator_surface": {
                "executive_surface": {
                    "state": "PASS",
                    "primary_next_action": {"label": "Review server status"},
                    "confidence": "High",
                    "short_reason": "Server status retrieved.",
                }
            },
        }

    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    rc = lab.main(["server status"])
    assert rc == 0
    session_root = repo_root / "governance" / "agents" / "sessions" / "lab_front_door"
    session_files = list(session_root.glob("*/session__lab.jsonl"))
    assert len(session_files) == 1
    text = session_files[0].read_text(encoding="utf-8")
    assert '"sender": "user"' in text
    assert '"sender": "assistant"' in text
    assert "server status" in text
    bubble_root = repo_root / "governance" / "agents" / "lab_bubbles"
    bubble_files = list(bubble_root.glob("*.md"))
    assert len(bubble_files) == 1
    bubble_text = bubble_files[0].read_text(encoding="utf-8")
    assert "# Lab / " in bubble_text
    assert "server status" in bubble_text
    assert "STATE: PASS" in bubble_text


def test_lab_cli_plan_pis_zip_import_routes_to_import_action(monkeypatch, tmp_path: Path, capsys) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    zip_path = downloads / "phase_bundle_pis.zip"
    zip_path.write_text("zip", encoding="utf-8")
    monkeypatch.setattr(lab, "_default_pis_zip_search_roots", lambda cwd: [downloads, cwd])
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["--plan", "integrate zipped PISes from Downloads directory"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STATE: RUNNING" in out
    assert "NEXT ACTION: repo_import_pis_zip_v1" in out


def test_lab_cli_execute_pis_zip_import_uses_import_tool(monkeypatch, tmp_path: Path, capsys) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    zip_path = downloads / "phase_bundle_pis.zip"
    zip_path.write_text("zip", encoding="utf-8")
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append({"cmd": list(command), "cwd": str(cwd), "stream_output": stream_output})
        if stream_output:
            print("{\"ok\": true, \"imported\": []}")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="{\"ok\": true, \"imported\": []}\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.setattr(lab, "_default_pis_zip_search_roots", lambda cwd: [downloads, cwd])
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["integrate zipped PISes from Downloads directory"])
    assert rc == 0
    assert calls
    assert calls[0]["cmd"] == ["python", "-m", "tools.bin.pis_import_zip", str(zip_path.resolve())]
    out = capsys.readouterr().out
    assert "STATE: PASS" in out
    assert "{\"ok\": true, \"imported\": []}" in out


def test_lab_cli_pis_zip_prefers_downloads_default_folder(monkeypatch, tmp_path: Path, capsys) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    zip_path = downloads / "only_pis_bundle.zip"
    zip_path.write_text("zip", encoding="utf-8")

    def fake_plan(payload, *, repo_root=None, contract_repo_root=None):
        assert payload["zip_path"] == str(zip_path.resolve())
        return {
            "supported": True,
            "operator_surface": {
                "executive_surface": {
                    "state": "RUNNING",
                    "primary_next_action": {"label": "repo_import_pis_zip_v1"},
                    "confidence": "High",
                    "short_reason": "The plane would route this request to repo_import_pis_zip_v1.",
                }
            },
            "summary": "plan",
        }

    monkeypatch.setattr(lab, "plan_operator_request_v1", fake_plan)
    monkeypatch.setattr(lab, "_default_pis_zip_search_roots", lambda cwd: [downloads, cwd])
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["--plan", "integrate zipped PISes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "NEXT ACTION: repo_import_pis_zip_v1" in out


def test_lab_cli_plan_integrate_pis_from_downloads_routes_to_import_action(monkeypatch, tmp_path: Path, capsys) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    zip_path = downloads / "only_pis_bundle.zip"
    zip_path.write_text("zip", encoding="utf-8")

    def fake_plan(payload, *, repo_root=None, contract_repo_root=None):
        assert payload["zip_path"] == str(zip_path.resolve())
        return {
            "supported": True,
            "operator_surface": {
                "executive_surface": {
                    "state": "RUNNING",
                    "primary_next_action": {"label": "repo_import_pis_zip_v1"},
                    "confidence": "High",
                    "short_reason": "The plane would route this request to repo_import_pis_zip_v1.",
                }
            },
            "summary": "plan",
        }

    monkeypatch.setattr(lab, "plan_operator_request_v1", fake_plan)
    monkeypatch.setattr(lab, "_default_pis_zip_search_roots", lambda cwd: [downloads, cwd])
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["--plan", "integrate pis from downloads"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "NEXT ACTION: repo_import_pis_zip_v1" in out


def test_lab_cli_execution_failure_surfaces_review_required(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        raise ValueError("example failure")

    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["verify outcome"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "STATE: REVIEW_REQUIRED" in out
    assert "REASON: example failure" in out


def test_lab_cli_plan_commit_routes_to_git_commit_tag(capsys) -> None:
    rc = lab.main(["--plan", "commit"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STATE: RUNNING" in out
    assert "NEXT ACTION: repo_commit_tag_v1" in out


def test_lab_cli_execute_commit_uses_git_commit_tag(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append({"cmd": list(command), "cwd": str(cwd), "stream_output": stream_output})
        if stream_output:
            print("[INFO] using baseline-123")
            print("[OK] committed and tagged baseline-123")
            print("[WARN] journal note", file=sys.stderr)
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="[INFO] using baseline-123\n[OK] committed and tagged baseline-123\n",
            stderr="[WARN] journal note\n",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["commit"])
    assert rc == 0
    assert calls
    assert calls[0]["cmd"] == ["bash", "tools/git/git-commit-tag.sh"]
    assert calls[0]["stream_output"] is True
    captured = capsys.readouterr()
    out = captured.out
    assert "STATE: PASS" in out
    assert "[INFO] using baseline-123" in out
    assert "[OK] committed and tagged baseline-123" in out
    assert "[WARN] journal note" in captured.err


def test_lab_cli_plan_archive_weekly_routes_to_archive_action(capsys) -> None:
    rc = lab.main(["--plan", "archive weekly"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STATE: RUNNING" in out
    assert "NEXT ACTION: repo_archive_snapshot_v1" in out


def test_lab_cli_plan_chain_routes_multiple_steps(monkeypatch, capsys) -> None:
    seen = []

    def fake_plan(payload, *, repo_root=None, contract_repo_root=None):
        text = str(payload["request_text"])
        seen.append(text)
        action = "repo_commit_tag_v1" if text == "commit" else "repo_archive_snapshot_v1"
        return {
            "supported": True,
            "request_text": text,
            "operator_surface": {
                "executive_surface": {
                    "state": "RUNNING",
                    "primary_next_action": {"label": action},
                    "confidence": "High",
                    "short_reason": f"The plane would route this request to {action}.",
                }
            },
            "summary": "plan",
        }

    monkeypatch.setattr(lab, "plan_operator_request_v1", fake_plan)
    rc = lab.main(["--plan", "do", "commit", "then", "archive", "debug"])
    assert rc == 0
    assert seen == ["commit", "archive debug"]
    out = capsys.readouterr().out
    assert "[1/2] commit" in out
    assert "[2/2] archive debug" in out
    assert "NEXT ACTION: repo_commit_tag_v1" in out
    assert "NEXT ACTION: repo_archive_snapshot_v1" in out


def test_lab_cli_execute_archive_uses_archive_wrapper(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append({"cmd": list(command), "cwd": str(cwd), "stream_output": stream_output})
        if stream_output:
            print("[OK] snapshot archive baseline-123 finished")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="[OK] snapshot archive baseline-123 finished\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["archive debug"])
    assert rc == 0
    assert calls
    assert calls[0]["cmd"] == ["bash", "infra/bin/archive", "--debug"]
    assert calls[0]["stream_output"] is True
    out = capsys.readouterr().out
    assert "STATE: PASS" in out
    assert "[OK] snapshot archive baseline-123 finished" in out


def test_lab_cli_execute_archive_weekly_uses_weekly_flag(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append({"cmd": list(command), "cwd": str(cwd), "stream_output": stream_output})
        if stream_output:
            print("[OK] snapshot archive baseline-123 finished")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="[OK] snapshot archive baseline-123 finished\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["archive weekly"])
    assert rc == 0
    assert calls
    assert calls[0]["cmd"] == ["bash", "infra/bin/archive", "--weekly"]
    assert calls[0]["stream_output"] is True
    out = capsys.readouterr().out
    assert "STATE: PASS" in out
    assert "[OK] snapshot archive baseline-123 finished" in out


def test_lab_cli_execute_chain_runs_steps_in_order(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append(list(command))
        if stream_output:
            print(f"RUN {' '.join(command)}")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout=f"RUN {' '.join(command)}\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["do", "commit", "then", "archive", "debug"])
    assert rc == 0
    assert calls == [
        ["bash", "tools/git/git-commit-tag.sh"],
        ["bash", "infra/bin/archive", "--debug"],
    ]
    out = capsys.readouterr().out
    assert "[1/2] commit" in out
    assert "[2/2] archive debug" in out
    assert "RUN bash tools/git/git-commit-tag.sh" in out
    assert "RUN bash infra/bin/archive --debug" in out


def test_lab_cli_plan_new_verbs_route_to_expected_actions(capsys) -> None:
    scenarios = {
        "server status": "server_status_v1",
        "server start": "server_start_v1",
        "server stop": "server_stop_v1",
        "server watch": "server_watch_v1",
        "server watchdog": "server_watchdog_v1",
        "recover service": "service_recover_v1",
        "run safe maintenance": "safe_maintenance_v1",
        "prepare implementation": "implementation_prepare_v1",
        "what are the next PISes to implement": "implementation_prepare_pis_v1",
        "show commit journal": "repo_show_commit_journal_v1",
        "show recent commits": "repo_show_recent_commits_v1",
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
        rc = lab.main(["--plan", prompt])
        assert rc == 0
        out = capsys.readouterr().out
        assert "STATE: RUNNING" in out
        assert f"NEXT ACTION: {action_id}" in out


def test_lab_cli_execute_server_status_uses_server_status_script(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append(list(command))
        if stream_output:
            print("[server_status] ready pid=123")
            print("[server_status] http_ready=1")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="[server_status] ready pid=123\n[server_status] http_ready=1\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["server status"])
    assert rc == 0
    assert calls == [["bash", "infra/user/server-status"]]
    out = capsys.readouterr().out
    assert "[server_status] ready pid=123" in out
    assert "REASON: Server status retrieved." in out
    assert "STATE: PASS" in out


def test_lab_cli_execute_server_watch_uses_user_wrapper(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append(list(command))
        if stream_output:
            print("[server_watch] dashboard running")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="[server_watch] dashboard running\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["server watch"])
    assert rc == 0
    assert calls == [["bash", "infra/user/server-watch", "--interval", "2", "--pipeline-limit", "40"]]
    out = capsys.readouterr().out
    assert "[server_watch] dashboard running" in out
    assert "REASON: Server watch started." in out


def test_lab_cli_execute_server_watchdog_uses_user_wrapper(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append(list(command))
        if stream_output:
            print("[server_watchdog] opened")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="[server_watchdog] opened\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["server watchdog"])
    assert rc == 0
    assert calls == [["bash", "infra/user/server-watchdog"]]
    out = capsys.readouterr().out
    assert "[server_watchdog] opened" in out
    assert "REASON: Server watchdog opened." in out


def test_lab_cli_execute_scheduler_watch_uses_user_wrapper(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append(list(command))
        if stream_output:
            print("[scheduler_watch] dashboard running")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="[scheduler_watch] dashboard running\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["scheduler watch"])
    assert rc == 0
    assert calls == [["bash", "infra/user/scheduler-watch", "--interval", "2", "--pipeline-limit", "40"]]
    out = capsys.readouterr().out
    assert "[scheduler_watch] dashboard running" in out
    assert "REASON: Scheduler watch started." in out


def test_lab_cli_execute_scheduler_disable_all_uses_user_wrapper(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append(list(command))
        if stream_output:
            print("[disable_all_scheduled_jobs] disabled=12")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="[disable_all_scheduled_jobs] disabled=12\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["scheduler disable all jobs"])
    assert rc == 0
    assert calls == [["bash", "infra/user/scheduler-disable-all"]]
    out = capsys.readouterr().out
    assert "[disable_all_scheduled_jobs] disabled=12" in out
    assert "REASON: All scheduler jobs disabled." in out
    assert "STATE: PASS" in out


def test_lab_cli_execute_show_commit_journal_uses_commit_journal_view(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append(list(command))
        if stream_output:
            print("pending journal entries")
        return subprocess.CompletedProcess(args=list(command), returncode=0, stdout="pending journal entries\n", stderr="")

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["show commit journal"])
    assert rc == 0
    assert calls == [["python", "-m", "tools.git.support.commit_journal", "view"]]
    out = capsys.readouterr().out
    assert "pending journal entries" in out
    assert "STATE: PASS" in out


def test_lab_cli_execute_recover_service_uses_server_scripts(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append(list(command))
        if command == ["bash", "infra/user/server-status"]:
            if stream_output:
                print("[server_status] stopped")
            return subprocess.CompletedProcess(args=list(command), returncode=0, stdout="[server_status] stopped\n", stderr="")
        if stream_output:
            print("[server_start] server started")
        return subprocess.CompletedProcess(args=list(command), returncode=0, stdout="[server_start] server started\n", stderr="")

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["recover service"])
    assert rc == 0
    assert calls == [["bash", "infra/user/server-status"], ["bash", "infra/user/server-start"]]
    out = capsys.readouterr().out
    assert "[server_status] stopped" in out
    assert "[server_start] server started" in out
    assert "STATE: PASS" in out


@pytest.mark.parametrize("prompt", ["restart server", "server restart"])
def test_lab_cli_execute_restart_server_uses_user_wrapper(monkeypatch, tmp_path: Path, capsys, prompt: str) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append(list(command))
        if stream_output:
            print("[server_restart] stopping server")
            print("[server_restart] cooldown 5s")
            print("[server_restart] starting server")
            print("[server_restart] done")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="[server_restart] stopping server\n[server_restart] cooldown 5s\n[server_restart] starting server\n[server_restart] done\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main([prompt])
    assert rc == 0
    assert calls == [["bash", "infra/user/server-restart"]]
    out = capsys.readouterr().out
    assert "[server_restart] stopping server" in out
    assert "[server_restart] starting server" in out
    assert "REASON: Service restart completed." in out


def test_lab_cli_restart_server_readiness_timeout_does_not_launch_codex_fallback(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append(list(command))
        if command == ["bash", "infra/user/server-restart"]:
            if stream_output:
                print("[server_restart] stopping server")
                print("[server_restart] starting server")
                print("[server_start] readiness timeout after 60s url=http://127.0.0.1:8787/api/health")
            return subprocess.CompletedProcess(
                args=list(command),
                returncode=1,
                stdout=(
                    "[server_restart] stopping server\n"
                    "[server_restart] starting server\n"
                    "[server_start] readiness timeout after 60s url=http://127.0.0.1:8787/api/health\n"
                ),
                stderr="",
            )
        if command == ["bash", "infra/user/server-status"]:
            if stream_output:
                print("[server_status] starting pid=646023")
                print("[server_status] http_ready=0")
            return subprocess.CompletedProcess(
                args=list(command),
                returncode=0,
                stdout="[server_status] starting pid=646023\n[server_status] http_ready=0\n",
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    def fail_codex(*args, **kwargs):
        raise AssertionError("Codex fallback should not run for a restart readiness timeout")

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.setattr(operator_interface, "run_codex_oneshot", fail_codex)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["server restart"])
    assert rc == 0
    assert calls == [["bash", "infra/user/server-restart"], ["bash", "infra/user/server-status"]]
    out = capsys.readouterr().out
    assert "STATE: REVIEW_REQUIRED" in out
    assert "readiness is still pending after timeout" in out
    assert "[the-plane] Launching Codex fallback" not in out


def test_lab_cli_execute_inspect_scheduler_uses_human_health_check(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append(list(command))
        if stream_output:
            print("[scheduler_health_check] status=OK")
            print("[scheduler_health_check] scheduler_running=true jobs_total=4 active_jobs=0 blocked_enabled_jobs=0 error_enabled_jobs=0")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="[scheduler_health_check] status=OK\n[scheduler_health_check] scheduler_running=true jobs_total=4 active_jobs=0 blocked_enabled_jobs=0 error_enabled_jobs=0\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["inspect scheduler"])
    assert rc == 0
    assert calls == [["bash", "infra/user/scheduler-health"]]
    out = capsys.readouterr().out
    assert "[scheduler_health_check] status=OK" in out
    assert "STATE: PASS" in out


def test_lab_cli_execute_safe_maintenance_uses_human_watchdog_output(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_capture(*, command, cwd, stream_output):
        calls.append(list(command))
        if stream_output:
            print("# Scheduler Watchdog")
            print("- mode: audit")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="# Scheduler Watchdog\n- mode: audit\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["run safe maintenance"])
    assert rc == 0
    assert calls == [["python", "infra/bin/scheduler_watchdog.py", "--mode", "audit"]]
    out = capsys.readouterr().out
    assert "# Scheduler Watchdog" in out
    assert "STATE: PASS" in out


def test_lab_cli_plan_import_intent_sheet_zip_from_downloads(monkeypatch, tmp_path: Path, capsys) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    zip_path = downloads / "intent_sheet_bundle.zip"
    zip_path.write_text("zip", encoding="utf-8")

    def fake_plan(payload, *, repo_root=None, contract_repo_root=None):
        assert payload["zip_path"] == str(zip_path.resolve())
        return {
            "supported": True,
            "operator_surface": {
                "executive_surface": {
                    "state": "RUNNING",
                    "primary_next_action": {"label": "repo_import_intent_sheet_zip_v1"},
                    "confidence": "High",
                    "short_reason": "The plane would route this request to repo_import_intent_sheet_zip_v1.",
                }
            },
            "summary": "plan",
        }

    monkeypatch.setattr(lab, "plan_operator_request_v1", fake_plan)
    monkeypatch.setattr(lab, "_default_pis_zip_search_roots", lambda cwd: [downloads, cwd])
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["--plan", "import intent sheet zip from downloads"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "NEXT ACTION: repo_import_intent_sheet_zip_v1" in out


def test_lab_cli_plan_next_pises_prompt_routes_to_implementation_prepare(capsys) -> None:
    rc = lab.main(["--plan", "what are the next PISes I should implement?"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STATE: RUNNING" in out
    assert "NEXT ACTION: implementation_prepare_pis_v1" in out


def test_lab_cli_plan_next_intent_sheet_to_implement_routes_to_implementation_prepare(capsys) -> None:
    rc = lab.main(["--plan", "what is the next intent sheet to implement?"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STATE: RUNNING" in out
    assert "NEXT ACTION: implementation_prepare_v1" in out


def test_lab_cli_plan_next_intent_sheet_routes_to_implementation_prepare(capsys) -> None:
    rc = lab.main(["--plan", "what is the next intent sheet?"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STATE: RUNNING" in out
    assert "NEXT ACTION: implementation_prepare_v1" in out


def test_lab_cli_plan_implement_the_next_intent_sheet_routes_to_recursive_implement(capsys) -> None:
    rc = lab.main(["--plan", "implement the next intent sheet"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STATE: RUNNING" in out
    assert "NEXT ACTION: implement_recursive_v1" in out


def test_lab_cli_plan_implement_the_next_intent_sheet_with_politeness_routes_to_recursive_implement(capsys) -> None:
    rc = lab.main(["--plan", "implement the next intent sheet please"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STATE: RUNNING" in out
    assert "NEXT ACTION: implement_recursive_v1" in out


def test_intent_safety_row_resolves_next_intent_to_first_ranked_candidate(monkeypatch) -> None:
    monkeypatch.setattr(
        lab._intent_sheet_next,
        "next_sheets",
        lambda **kwargs: [
            {
                "uid": "IS-20260403-212913Z-348E",
                "title": "Continuation Packet and Long-Horizon Memory Contract",
                "parallel_safety": {"safe": True, "reason": "resume_existing_family"},
            },
            {
                "uid": "IS-20260403-212913Z-E983",
                "title": "Experiment Scaffolding Automation",
                "parallel_safety": {"safe": True, "reason": "no_active_families"},
            },
        ],
    )
    row = lab._intent_safety_row("NEXT_INTENT")
    assert row["uid"] == "IS-20260403-212913Z-348E"
    assert bool((row.get("parallel_safety") or {}).get("safe")) is True


def test_intent_safety_row_next_safe_intent_skips_resume_existing_family(monkeypatch) -> None:
    monkeypatch.setattr(
        lab._intent_sheet_next,
        "next_sheets",
        lambda **kwargs: [
            {
                "uid": "IS-20260403-212913Z-348E",
                "title": "Continuation Packet and Long-Horizon Memory Contract",
                "parallel_safety": {"safe": True, "reason": "resume_existing_family"},
            },
            {
                "uid": "IS-20260403-212913Z-E983",
                "title": "Experiment Scaffolding Automation",
                "parallel_safety": {"safe": True, "reason": "no_active_families"},
            },
        ],
    )
    row = lab._intent_safety_row("NEXT_SAFE_INTENT")
    assert row["uid"] == "IS-20260403-212913Z-E983"


def test_lab_cli_plan_implement_uid_prompt_routes_to_recursive_implement(capsys) -> None:
    rc = lab.main(["--plan", "implement IS-20260403-212913Z-5817"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STATE: RUNNING" in out
    assert "NEXT ACTION: implement_recursive_v1" in out


def test_lab_cli_execute_implement_uid_prompt_autodetects_target_uid(monkeypatch, tmp_path: Path, capsys) -> None:
    captured = {}

    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        captured["payload"] = dict(payload)
        return {
            "supported": True,
            "selection": {"action_id": "implement_recursive_v1"},
            "operator_surface": {
                "executive_surface": {
                    "state": "PASS",
                    "primary_next_action": {"label": "Review implementation chain"},
                    "confidence": "High",
                    "short_reason": "Implementation chain was started with the default build-verify-repair contract.",
                }
            },
            "summary": "ok",
        }

    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    monkeypatch.setattr(lab, "_implementation_launch_confirmation", lambda **kwargs: True)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["implement", "IS-20260403-212913Z-5817"])
    assert rc == 0
    assert captured["payload"]["target_uid"] == "IS-20260403-212913Z-5817"
    out = capsys.readouterr().out
    assert "NEXT ACTION: Review implementation chain" in out
    assert "REASON: Implementation chain was started with the default build-verify-repair contract." in out


def test_lab_cli_execute_implement_suffix_prompt_autodetects_target_ref(monkeypatch, tmp_path: Path, capsys) -> None:
    captured = {}

    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        captured["payload"] = dict(payload)
        return {
            "supported": True,
            "selection": {"action_id": "implement_recursive_v1"},
            "operator_surface": {
                "executive_surface": {
                    "state": "PASS",
                    "primary_next_action": {"label": "Review implementation chain"},
                    "confidence": "High",
                    "short_reason": "Implementation chain was started with the default build-verify-repair contract.",
                }
            },
            "summary": "ok",
        }

    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    monkeypatch.setattr(lab, "_implementation_launch_confirmation", lambda **kwargs: True)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["implement", "u4e2"])
    assert rc == 0
    assert captured["payload"]["target_uid"] == "u4e2"
    out = capsys.readouterr().out
    assert "NEXT ACTION: Review implementation chain" in out


def test_lab_cli_execute_implement_next_pis_autodetects_selector(monkeypatch, tmp_path: Path, capsys) -> None:
    captured = {}

    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        captured["payload"] = dict(payload)
        return {
            "supported": True,
            "selection": {"action_id": "implement_recursive_v1"},
            "operator_surface": {
                "executive_surface": {
                    "state": "PASS",
                    "primary_next_action": {"label": "Review implementation chain"},
                    "confidence": "High",
                    "short_reason": "Implementation chain was started with the default build-verify-repair contract.",
                }
            },
            "summary": "ok",
        }

    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    monkeypatch.setattr(lab, "_implementation_launch_confirmation", lambda **kwargs: True)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["implement", "next", "PIS"])
    assert rc == 0
    assert captured["payload"]["target_uid"] == "next_pis"


def test_lab_cli_execute_implement_forwards_recursive_flags(monkeypatch, tmp_path: Path, capsys) -> None:
    captured = {}

    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        captured["payload"] = dict(payload)
        return {
            "supported": True,
            "selection": {"action_id": "implement_recursive_v1"},
            "operator_surface": {
                "executive_surface": {
                    "state": "PASS",
                    "primary_next_action": {"label": "Review implementation chain"},
                    "confidence": "High",
                    "short_reason": "Implementation chain was started with the default build-verify-repair contract.",
                }
            },
            "summary": "ok",
        }

    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    monkeypatch.setattr(lab, "_implementation_launch_confirmation", lambda **kwargs: True)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["implement", "u4e2", "--plan-only", "--decompose-only", "--max-children", "7", "--parallel", "safe", "--proof-profile", "full", "--close", "manual", "--start-now", "false"])
    assert rc == 0
    assert captured["payload"]["target_uid"] == "u4e2"
    assert captured["payload"]["plan_only"] is True
    assert captured["payload"]["decompose_only"] is True
    assert captured["payload"]["max_children"] == 7
    assert captured["payload"]["parallel"] == "safe"
    assert captured["payload"]["proof_profile"] == "full"
    assert captured["payload"]["close_mode"] == "manual"
    assert captured["payload"]["start_now"] is False


def test_lab_cli_execute_next_pises_prompt_routes_to_pis_prepare(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_capture(*, command, cwd, stream_output):
        assert command[:4] == ["python", "-m", "tools.bin.pis_next", "--limit"]
        if stream_output:
            print("1. PIS-EXAMPLE | Example PIS | status=draft | prio=1")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="1. PIS-EXAMPLE | Example PIS | status=draft | prio=1\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["what", "are", "the", "next", "PISes", "to", "implement"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1. PIS-EXAMPLE | Example PIS | status=draft | prio=1" in out
    assert "NEXT ACTION: Review listed PIS items" in out


def test_lab_cli_execute_next_pises_prompt_shows_human_friendly_list(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_capture(*, command, cwd, stream_output):
        assert "--json" not in command
        if stream_output:
            print("1. IS-EXAMPLE | Example Intent | status=draft | prio=1 | wave=W0 | critical_path")
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="1. IS-EXAMPLE | Example Intent | status=draft | prio=1 | wave=W0 | critical_path\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["what are the next PISes I should implement?"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1. IS-EXAMPLE | Example Intent | status=draft | prio=1 | wave=W0 | critical_path" in out
    assert "NEXT ACTION: Review listed PIS items" in out
    assert "REASON: Next implementation candidates are listed below." in out


def test_lab_cli_human_output_ends_with_turn_delimiter(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_capture(*, command, cwd, stream_output):
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="1. IS-EXAMPLE | Example Intent | status=draft | prio=1 | wave=W0 | critical_path\n",
            stderr="",
        )

    monkeypatch.setattr(operator_interface, "_run_command_capture", fake_capture)
    monkeypatch.chdir(tmp_path)
    rc = lab.main(["what is the next intent sheet?"])
    assert rc == 0
    out = capsys.readouterr().out
    assert lab._TURN_DELIMITER in out
    assert out.rstrip().endswith(lab._TURN_DELIMITER)
