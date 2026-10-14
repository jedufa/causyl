from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.backend_advisor import advice as backend_advice
from tools.context_suppliers import compress as context_compress
from tools.git import guard as git_guard
from tools.git.support import commit_journal
from tools.governance_hygiene import lint as governance_lint
from tools.lesson_index import build as lesson_build
from tools.operator_digest import build as digest_build
from tools.patch_helpers import apply as patch_apply
from tools.regression_suite import run as regression_run
from tools.repo_intel import map as repo_map
from tools.verification_bundle import build as verification_build
from infra.webui.ledger.ledger_manager import ChangeLedger


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_context_bundle_is_deterministic_and_budgeted(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "note.md"
    target.write_text("\n".join(f"line {idx}" for idx in range(1, 50)), encoding="utf-8")

    first = context_compress.build_context_bundle(repo_root=tmp_path, target_files=["docs/note.md"], budget_tokens=64)
    second = context_compress.build_context_bundle(repo_root=tmp_path, target_files=["docs/note.md"], budget_tokens=64)

    assert first["output_hash"] == second["output_hash"]
    assert first["outputs"]["budget_report"]["used_tokens"] <= 64
    assert first["outputs"]["context_bundle"][0]["path"] == "docs/note.md"


def test_repo_impact_map_discovers_related_tests(tmp_path: Path) -> None:
    (tmp_path / "tools" / "git" / "support").mkdir(parents=True)
    (tmp_path / "tests" / "tools").mkdir(parents=True)
    (tmp_path / "tools" / "git" / "support" / "commit_journal.py").write_text("def commit_journal_helper():\n    return True\n", encoding="utf-8")
    (tmp_path / "tests" / "tools" / "test_commit_journal.py").write_text(
        "from tools.git.support import commit_journal\n\n\ndef test_commit_journal_helper():\n    assert True\n",
        encoding="utf-8",
    )

    payload = repo_map.build_impact_map(repo_root=tmp_path, target_files=["tools/git/support/commit_journal.py"], policy_path="config/runtime_policy.properties")

    test_paths = [row["path"] for row in payload["outputs"]["test_matrix"]]
    assert "tests/tools/test_commit_journal.py" in test_paths
    assert payload["outputs"]["module_graph"][0]["path"] == "tools/git/support/commit_journal.py"


def test_patch_helper_builds_unified_diff_from_recipe(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("print('old')\n", encoding="utf-8")
    recipe = {"recipe_id": "literal-rewrite", "operations": [{"op": "replace_literal", "old": "old", "new": "new"}]}

    payload = patch_apply.build_patch_diff(repo_root=tmp_path, recipe=recipe, target_files=["sample.py"])

    diff_text = payload["outputs"]["patch_diff"]
    assert "--- a/sample.py" in diff_text
    assert "+print('new')" in diff_text


def test_verification_bundle_fails_closed_without_selectors() -> None:
    payload = verification_build.build_verification_bundle(repo_root=Path("."), target_pis_uid="PIS-TEST", selectors=[], impact_map=None, require_evidence=True)
    assert any(row["code"] == "missing_selectors" for row in payload["errors"])
    assert payload["outputs"]["proof_scorecard"]["proof_score"] == "blocked"


def test_git_guard_reports_dirty_tree_and_commit_journal(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    tracked.write_text("dirty\n", encoding="utf-8")
    commit_journal.write_entry(
        {
            "title": "test journal entry",
            "summary_bullets": ["changed tracked file"],
            "files": ["tracked.txt"],
            "validation": ["pytest tests"],
            "notes": [],
            "outcome": "partial",
        },
        repo=tmp_path,
    )

    payload = git_guard.build_git_guard_report(repo_root=tmp_path, require_clean=True, require_commit_journal=True)

    assert any(row["code"] == "dirty_tree" for row in payload["errors"])
    assert payload["outputs"]["safety_report"]["pending_commit_journal_entries"]


def test_backend_advisor_uses_quota_and_latency_tie_breakers(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "runtime_policy.properties").write_text(
        "mobile.chat_model=gpt-5.4\nmobile.chat_fallback_models=gpt-5.4-mini,deepseek-reasoner\n",
        encoding="utf-8",
    )
    telemetry = {
        "backends": [
            {"model": "gpt-5.4", "backend": "primary", "latency_ms": 800, "reliability": 0.99, "quota_ok": False},
            {"model": "gpt-5.4-mini", "backend": "fallback", "latency_ms": 200, "reliability": 0.95, "quota_ok": True},
        ]
    }

    payload = backend_advice.build_backend_advice(repo_root=tmp_path, target_pis_uid="PIS-TEST", budget=1000, backend_telemetry=telemetry)

    assert payload["outputs"]["preferred_runner"]["model"] == "gpt-5.4-mini"
    assert payload["outputs"]["fallback_sequence"]


def test_governance_hygiene_detects_missing_children(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    parent = ledger.create_item(title="Parent", slug="parent", pis_markdown="# Parent\n")
    child = ledger.create_item(title="Child", slug="child", pis_markdown="# Child\n\n- tool_name: sample\n- category: demo\n- tier: T0\n- determinism: D1\n- failure_semantics: WARN_ONLY\n")

    payload = governance_lint.lint_governance(repo_root=tmp_path, parent_uid=parent["uid"], child_set=[child["uid"], "PIS-20990101-000000Z-XXXX"], context_refs=["docs/missing.md"])

    issues = {(row["uid"], row["issue"]) for row in payload["outputs"]["lint_report"]}
    assert ("PIS-20990101-000000Z-XXXX", "missing_child") in issues
    assert (parent["uid"], "missing_context_ref") in issues


def test_operator_digest_parses_report_and_flags_risk(tmp_path: Path) -> None:
    report_dir = tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "reports" / "demo_job"
    report_dir.mkdir(parents=True)
    report_file = report_dir / "sj_20260314T234015Z_deadbeef.md"
    report_file.write_text(
        "# Scheduled Job Decision: demo_job\n\n- status: blocked\n- run_id: sj_20260314T234015Z_deadbeef\n- reason: dependency_not_ok\n",
        encoding="utf-8",
    )

    payload = digest_build.build_operator_digest(repo_root=tmp_path, job_ids=["demo_job"])

    assert payload["outputs"]["timeline"][0]["status"] == "blocked"
    assert payload["outputs"]["risk_list"][0]["reason"] == "dependency_not_ok"


def test_lesson_index_is_stable_and_links_parent(tmp_path: Path) -> None:
    digest = {
        "outputs": {
            "risk_list": [
                {"job_id": "demo_job", "run_id": "sj_demo", "status": "blocked", "reason": "missing proof"},
            ]
        }
    }

    payload = lesson_build.build_lesson_index(repo_root=tmp_path, parent_uid="PIS-PARENT", job_digests=[digest], operator_notes=["remember fallback chain"])

    assert payload["outputs"]["linked_pis"] == ["PIS-PARENT"]
    assert len(payload["outputs"]["lesson_entries"]) == 2
    assert payload["outputs"]["lesson_entries"][0]["severity"] == "high"


def test_regression_suite_fail_closed_stops_after_verification_bundle() -> None:
    payload = regression_run.run_regression_suite(
        repo_root=_repo_root(),
        parent_uid="PIS-20260314-200630Z-T9L5",
        child_pis_ids=[
            "PIS-20260315-090000Z-C03A",
            "PIS-20260315-090200Z-CA4D",
            "PIS-20260315-090600Z-E90B",
        ],
    )

    assert payload["outputs"]["regression_result"] == "fail"
    assert "verification_bundle" in payload["outputs"]["blocking_steps"]
