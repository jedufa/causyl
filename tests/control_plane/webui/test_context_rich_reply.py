from __future__ import annotations

import json
from pathlib import Path

from infra.webui import server_context_rich_reply as rich
from infra.webui.ledger.ledger_manager import ChangeLedger


def test_classify_reply_mode_distinguishes_cheap_context_rich_and_escalate() -> None:
    binding = {"type": "ledger_uid", "id": "PIS-20260329-121518Z-1D0C"}

    cheap = rich.classify_reply_mode(message="What is this PIS?", binding={"type": "task_id", "id": "abc"})
    strategy = rich.classify_reply_mode(message="How would you implement this PIS?", binding=binding)
    execute = rich.classify_reply_mode(message="Implement this PIS now and patch the repo.", binding=binding)

    assert cheap["mode"] == "cheap_reply"
    assert strategy["mode"] == "context_rich_reply"
    assert execute["mode"] == "escalate_to_task"


def test_resolve_pis_binding_prefers_bound_ledger_uid_and_reads_metadata(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    parent = ledger.create_item(title="Parent Contract", slug="parent-contract", pis_markdown="# Parent\n")
    child = ledger.create_item(
        title="Child Contract",
        slug="child-contract",
        pis_markdown=f"# Child\n\nParent relation: {parent['uid']}\n",
    )

    binding = {"type": "ledger_uid", "id": child["uid"]}
    resolved = rich.resolve_pis_binding(repo_root=Path(tmp_path), binding=binding, message="How would you implement this PIS?")

    assert resolved["resolved"] is True
    assert resolved["uid"] == child["uid"]
    assert resolved["status"] == "draft"
    assert resolved["parent_uid"] == parent["uid"]



def test_classify_reply_mode_keeps_ambiguous_strategy_requests_on_cheap_path() -> None:
    route = rich.classify_reply_mode(message="How would you implement it?", binding={"type": "task_id", "id": "abc"})

    assert route["mode"] == "cheap_reply"
    assert route["ambiguity_level"] == "high"


def test_format_bundle_context_enforces_token_budget() -> None:
    text = "\n".join(f"line {idx}" for idx in range(1, 200))
    context_bundle = {
        "files": [
            {
                "path": "tools/demo.py",
                "bundle_role": "symbol",
                "snippets": [{"start_line": 1, "end_line": 180, "content": text}],
            },
            {
                "path": "tests/test_demo.py",
                "bundle_role": "test",
                "snippets": [{"start_line": 1, "end_line": 20, "content": "def test_demo():\n    assert True\n"}],
            },
        ]
    }

    rendered, files, tests = rich._format_bundle_context(context_bundle, budget={"max_files": 6, "max_symbols": 2, "max_tests": 2, "max_total_bytes": 1000, "max_total_tokens": 120})

    assert "tests/test_demo.py" in files
    assert tests == ["tests/test_demo.py"]
    assert "budget_usage:" in rendered


def test_context_rich_reply_eval_set_routes_match_expectations() -> None:
    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "context_rich_reply_eval_set.json"
    rows = json.loads(fixture.read_text(encoding="utf-8"))

    observed = [rich.classify_reply_mode(message=row["message"], binding=row["binding"])["mode"] for row in rows]

    assert observed == [row["expected_mode"] for row in rows]
