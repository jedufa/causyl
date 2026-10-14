from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import compiled_artifacts as ca
from infra.controlplane import compiled_failure_policy as cfp
from infra.controlplane import scheduled_jobs as sched


def test_repeated_structural_failure_disposition_stops_identical_builder_failures() -> None:
    iterations = [
        {
            "builder": {"meta": {"compiled_output_classification": {"kind": "builder_unstructured", "reason": "missing_builder_structure"}}},
            "verifier": {},
        },
        {
            "builder": {"meta": {"compiled_output_classification": {"kind": "builder_unstructured", "reason": "missing_builder_structure"}}},
            "verifier": {},
        },
    ]
    result = cfp.repeated_structural_failure_disposition(iterations)
    assert result["stop"] is True
    assert result["class"] == "compiled_builder_contract_violation"
    assert result["retryable"] is False


def test_persist_compiled_artifacts_writes_shadow_prompt_and_classification_files(tmp_path: Path) -> None:
    result = ca.persist_compiled_artifacts(
        runtime_root=tmp_path,
        job_id="job_1",
        run_id="run_1",
        compiled_execution={
            "enabled": True,
            "ir": {"ok": True, "normalized_ir": {"task_id": "job_1"}},
            "manifest": {"ok": True, "manifest": {"files": []}},
            "prompts": {
                "builder": {
                    "prompt_text": (
                        "Compiled Role: builder\n\n"
                        "Builder Contract:\n"
                        "- contract_version: builder_turn_contract_v1\n"
                        "- result_packet_version: builder_result_packet_v1\n"
                        "- contract_doc: docs/contracts/contract_builder_turn_output.md\n"
                        "- contract_revision: rev123\n"
                        "- required_sections: Changed files, Validation Commands Run\n"
                    ),
                    "prompt_sha256": "abc",
                    "contract_version": "builder_turn_contract_v1",
                    "result_packet_version": "builder_result_packet_v1",
                    "contract_doc_rel": "docs/contracts/contract_builder_turn_output.md",
                    "contract_revision": "rev123",
                    "contract": {"required_sections": ["Changed files", "Validation Commands Run"]},
                },
            },
        },
        loop={
            "iterations": [
                {
                    "iteration": 1,
                    "builder": {"meta": {"compiled_output_classification": {"kind": "builder_structured_artifact", "reason": "recognized_builder_structure"}}},
                    "verifier": {"meta": {"compiled_output_classification": {"kind": "verifier_structured_verdict", "reason": "recognized_verdict_header"}}},
                }
            ]
        },
    )
    artifact_dir = Path(result["artifact_dir"])
    assert artifact_dir.exists()
    assert Path(result["paths"]["compiled_execution_shadow"]).exists()
    assert Path(result["paths"]["normalized_execution_ir"]).exists()
    assert Path(result["paths"]["ir_validation"]).exists()
    assert Path(result["paths"]["context_manifest"]).exists()
    assert "Builder Contract:" in Path(result["paths"]["compiled_prompts"]["builder"]).read_text(encoding="utf-8")
    assert Path(result["paths"]["compiled_prompt_metadata"]["builder"]).exists()
    metadata = json.loads(Path(result["paths"]["compiled_prompt_metadata"]["builder"]).read_text(encoding="utf-8"))
    assert metadata["contract_revision"] == "rev123"
    contract = json.loads(Path(result["paths"]["compiled_prompt_contracts"]["builder"]).read_text(encoding="utf-8"))
    assert contract["required_sections"] == ["Changed files", "Validation Commands Run"]
    classes = json.loads(Path(result["paths"]["compiled_classifications"]).read_text(encoding="utf-8"))
    assert classes["iterations"][0]["builder"]["kind"] == "builder_structured_artifact"
    assert Path(result["paths"]["compiled_retry_decisions"]).exists()


def test_persist_compiled_artifacts_rejects_malformed_prompt_contract_payload(tmp_path: Path) -> None:
    try:
        ca.persist_compiled_artifacts(
            runtime_root=tmp_path,
            job_id="job_bad",
            run_id="run_bad",
            compiled_execution={
                "enabled": True,
                "prompts": {
                    "builder": {
                        "prompt_text": "Builder Contract:\n- contract_version: builder_turn_contract_v1\n",
                        "prompt_sha256": "abc",
                        "contract_version": "builder_turn_contract_v1",
                        "result_packet_version": "builder_result_packet_v1",
                        "contract_doc_rel": "docs/contracts/contract_builder_turn_output.md",
                        "contract_revision": "rev123",
                    },
                },
            },
        )
    except ValueError as exc:
        assert "compiled prompt artifact invalid for builder" in str(exc)
        assert "missing_contract_payload" in str(exc)
    else:
        raise AssertionError("expected malformed compiled prompt artifact to fail closed")


def test_build_verify_loop_records_compiled_retry_disposition_and_persists_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    seen = {"builder": 0}

    def _send(*, role: str, binding: dict, message: str, options: dict | None = None):
        if role == "builder":
            seen["builder"] += 1
            return {"ok": True, "assistant": {"content": "unstructured builder output", "status": "ok", "meta": {}}}
        return {"ok": True, "assistant": {"content": "VERDICT: FAIL\nirrelevant", "status": "ok", "meta": {}}}

    monkeypatch.setattr(sched._agents, "send_message", _send)
    monkeypatch.setattr(sched, "_git_status_porcelain", lambda **kwargs: "")
    monkeypatch.setattr(sched, "_bind_scheduler_launch", lambda **kwargs: {"ok": True})

    sched.save_job(
        {
            "job_id": "compiled_failure_loop_001",
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-20260329-151108Z-32B6"},
            "prompt": "Verify the bound item.",
            "schedule": {"mode": "once", "run_at": "2026-03-29T16:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 300},
            "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 3, "pass_marker": "VERDICT: PASS"},
        },
        repo_root=tmp_path,
    )
    result = sched.execute_job("compiled_failure_loop_001", repo_root=tmp_path, trigger="manual", force=True, now_utc="2026-03-29T16:05:00Z")
    assert result["ok"] is False
    receipt = sched.load_receipt("compiled_failure_loop_001", result["run_id"], repo_root=tmp_path)
    assert receipt["compiled_retry_disposition"]["class"] in {
        "compiled_builder_contract_violation",
        "compiled_verifier_contract_violation",
    }
    assert receipt["compiled_retry_disposition"]["source"] == "compiled_structural_policy"
    assert receipt["job_retry"]["final_failure_class"] == "proof_gap"
    persistence = receipt["compiled_execution"]["artifact_persistence"]
    assert Path(persistence["paths"]["compiled_execution_shadow"]).exists()
    assert Path(persistence["paths"]["compiled_classifications"]).exists()
    assert 1 <= seen["builder"] <= 2
