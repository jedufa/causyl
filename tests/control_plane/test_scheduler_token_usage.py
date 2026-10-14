from __future__ import annotations

import json
from pathlib import Path

from tools.bin import scheduler_token_usage as usage


def test_estimate_tokens_from_text_and_bytes() -> None:
    assert usage._estimate_tokens_from_text("abcd") == 1
    assert usage._estimate_tokens_from_bytes(40) == 10


def test_estimate_run_tokens_prefers_request_payload_bytes(tmp_path: Path, monkeypatch) -> None:
    item_dir = tmp_path / "governance" / "ledger" / "items" / "PIS-20260328-120000Z-ABCD__demo"
    chat_dir = item_dir / "artifacts" / "agent_chat"
    chat_dir.mkdir(parents=True, exist_ok=True)
    (chat_dir / "session__builder.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "utc": "2026-03-28T12:00:10Z",
                        "sender": "user",
                        "content": "prompt text here",
                    }
                ),
                json.dumps(
                    {
                        "utc": "2026-03-28T12:00:20Z",
                        "sender": "assistant",
                        "content": "assistant reply",
                        "meta": {"request_payload_bytes": 400, "selected_model": "gpt-5.4"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(usage, "REPO_ROOT", tmp_path)
    receipt = {
        "binding": {"type": "ledger_uid", "id": "PIS-20260328-120000Z-ABCD"},
        "started_utc": "2026-03-28T12:00:00Z",
        "finished_utc": "2026-03-28T12:01:00Z",
    }
    out = usage._estimate_run_tokens(receipt)
    assert out["inbound_tokens_est"] == 100
    assert out["outbound_tokens_est"] > 0
    assert out["estimation_source"] == "request_payload_bytes"


def test_render_table_groups_by_job_kind() -> None:
    text = usage._render_table(
        [
            {
                "job_id": "a",
                "display_title": "Alpha job",
                "job_kind": "single_turn",
                "status": "ok",
                "inbound_tokens_est": 10,
                "outbound_tokens_est": 5,
                "total_tokens_est": 15,
                "estimation_source": "message_text",
                "models_seen": ["deepseek-reasoner"],
                "started_utc": "2026-03-28T12:00:00Z",
                "finished_utc": "2026-03-28T12:01:00Z",
            },
            {
                "job_id": "b",
                "display_title": "Beta job",
                "job_kind": "build_verify_loop",
                "status": "ok",
                "inbound_tokens_est": 20,
                "outbound_tokens_est": 10,
                "total_tokens_est": 30,
                "estimation_source": "request_payload_bytes",
                "models_seen": ["gpt-5.4"],
                "started_utc": "2026-03-28T12:02:00Z",
                "finished_utc": "2026-03-28T12:03:00Z",
            },
        ],
        group_by="job_kind",
    )
    assert "Scheduled Job Token Estimate" in text
    assert "single_turn" in text
    assert "build_verify_loop" in text
    assert "Alpha job [a]" in text
    assert "Beta job [b]" in text
    assert "Inbound:" in text
