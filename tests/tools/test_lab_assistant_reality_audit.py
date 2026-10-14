from __future__ import annotations

import json
from pathlib import Path

from tools.diagnostics import lab_assistant_reality_audit as audit


def test_gap_matrix_fail_closes_critical_unknowns() -> None:
    sections = {
        "entrypoints": audit.SectionResult(name="entrypoints", status=audit.STATUS_UNKNOWN, summary="", evidence=[]),
        "task_representation": audit.SectionResult(name="task_representation", status=audit.STATUS_UNKNOWN, summary="", evidence=[]),
        "routing": audit.SectionResult(name="routing", status=audit.STATUS_UNKNOWN, summary="", evidence=[]),
        "status": audit.SectionResult(name="status", status=audit.STATUS_UNKNOWN, summary="", evidence=[]),
        "persistence_recovery": audit.SectionResult(name="persistence_recovery", status=audit.STATUS_UNKNOWN, summary="", evidence=[]),
    }
    trace_a = audit.TraceResult(name="A", status=audit.STATUS_UNKNOWN, summary="")
    trace_b = audit.TraceResult(name="B", status=audit.STATUS_UNKNOWN, summary="", recovery={"status": audit.STATUS_UNKNOWN})

    matrix = audit.build_gap_matrix(trace_a, trace_b, sections)
    by_name = {row["capability"]: row for row in matrix}

    assert by_name["unified entrypoint"]["status"] == audit.STATUS_FAIL
    assert by_name["single task object"]["status"] == audit.STATUS_FAIL
    assert by_name["operator truth"]["status"] == audit.STATUS_FAIL


def test_identity_verdict_requires_both_traces() -> None:
    happy = audit.TraceResult(
        name="A",
        status=audit.STATUS_PASS,
        summary="",
        identity_chain={
            "task_id": "task1",
            "delegation_request_id": "req1",
            "workflow_id": "wf1",
            "correlation_id": "corr1",
        },
    )
    failure = audit.TraceResult(
        name="B",
        status=audit.STATUS_PASS,
        summary="",
        identity_chain={
            "task_id": "task2",
            "delegation_request_id": "req2",
            "workflow_id": "wf2",
            "correlation_id": "corr2",
        },
        recovery={"status": audit.STATUS_PASS},
    )
    assert audit.identity_verdict(happy, failure) == audit.STATUS_PASS

    failure_bad = audit.TraceResult(name="B", status=audit.STATUS_FAIL, summary="", identity_chain={"task_id": "task2"}, recovery={"status": audit.STATUS_FAIL})
    assert audit.identity_verdict(happy, failure_bad) == audit.STATUS_FAIL


def test_report_generation_writes_markdown_and_json(tmp_path: Path) -> None:
    report_md = tmp_path / "report.md"
    report_json = tmp_path / "report.json"

    rc = audit.main([
        "--repo-root",
        str(tmp_path),
        "--report-md",
        str(report_md),
        "--report-json",
        str(report_json),
    ])

    assert rc == 0
    assert report_md.exists()
    assert report_json.exists()

    md_text = report_md.read_text(encoding="utf-8")
    data = json.loads(report_json.read_text(encoding="utf-8"))

    assert "Lab Assistant Reality Audit (HARDENED)" in md_text
    assert data["identity_chain_verdict"] == audit.STATUS_FAIL
    assert any(row["capability"] == "operator truth" for row in data["gap_matrix"])


def test_top_roi_fixes_promote_identity_status_and_recovery_to_max(tmp_path: Path) -> None:
    gap_matrix = [
        {"capability": "unified entrypoint", "status": audit.STATUS_FAIL, "evidence": "x", "pain": "high"},
        {"capability": "traceability", "status": audit.STATUS_FAIL, "evidence": "x", "pain": "high"},
        {"capability": "central decision logic", "status": audit.STATUS_FAIL, "evidence": "x", "pain": "high"},
        {"capability": "recovery", "status": audit.STATUS_FAIL, "evidence": "x", "pain": "high"},
        {"capability": "operator truth", "status": audit.STATUS_FAIL, "evidence": "x", "pain": "high"},
    ]
    fixes = audit.top_roi_fixes(tmp_path, gap_matrix)
    assert fixes
    assert fixes[0]["priority"] == "MAX"
    assert any("operator truth" in row["impacted_gaps"] for row in fixes)
