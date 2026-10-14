from __future__ import annotations

from pathlib import Path
import json

from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger
from tools.bin.intent_sheet_next import next_sheets


def test_next_sheets_prefers_execution_priority_then_critical_path(tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    a = ledger.create_sheet(
        title="Later sheet",
        intent_markdown="# Intent Sheet\n\nlater\n",
        uid_override="IS-20260403-220000Z-L8R1",
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
    )
    b = ledger.create_sheet(
        title="Earlier sheet",
        intent_markdown="# Intent Sheet\n\nearlier\n",
        uid_override="IS-20260403-220001Z-E4R1",
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
    )
    c = ledger.create_sheet(
        title="Verified sheet",
        intent_markdown="# Intent Sheet\n\nverified\n",
        uid_override="IS-20260403-220002Z-V3R1",
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
        status="verified",
    )
    d = ledger.create_sheet(
        title="Deferred sheet",
        intent_markdown="# Intent Sheet\n\ndeferred\n",
        uid_override="IS-20260403-220003Z-D3F1",
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
        status="deferred",
    )
    ledger.update_meta(a["uid"], {"execution_priority": 202, "recommended_wave": "W2", "recommended_order_in_wave": 2})
    ledger.update_meta(b["uid"], {"execution_priority": 101, "recommended_wave": "W1", "recommended_order_in_wave": 1, "critical_path": True})
    ledger.update_meta(c["uid"], {"execution_priority": 1})
    ledger.update_meta(d["uid"], {"execution_priority": 0})

    rows = next_sheets(repo_root=tmp_path, limit=5, project_id="causyl")

    assert [row["uid"] for row in rows] == [b["uid"], a["uid"]]


def test_next_sheets_treats_started_but_idle_family_as_safe_resume(tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    created = ledger.create_sheet(
        title="Started sheet",
        intent_markdown="# Intent Sheet\n\nstarted\n",
        uid_override="IS-20260403-220010Z-S1A1",
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
    )
    parent_uid = "PIS-20260407-055533Z-3826"
    ledger.update_meta(
        created["uid"],
        {
            "execution_priority": 101,
            "derived_pis_uids": [parent_uid],
            "recommended_wave": "W1",
            "recommended_order_in_wave": 1,
        },
    )

    inbox = tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "implement_3826_started-sheet.json").write_text(
        json.dumps(
            {
                "job_id": "implement_3826_started-sheet",
                "binding": {"type": "ledger_uid", "id": parent_uid},
                "schedule": {"mode": "once", "run_at": "2026-04-07T05:55:34Z", "timezone": "UTC"},
            }
        ),
        encoding="utf-8",
    )
    state_root = tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "implement_3826_started-sheet.json").write_text(
        json.dumps(
            {
                "job_id": "implement_3826_started-sheet",
                "active_run_id": "",
                "last_status": "ok",
            }
        ),
        encoding="utf-8",
    )

    rows = next_sheets(repo_root=tmp_path, limit=5, project_id="causyl")

    assert rows[0]["uid"] == created["uid"]
    assert rows[0]["parallel_safety"]["safe"] is True
    assert rows[0]["parallel_safety"]["reason"] == "resume_existing_family"


def test_next_sheets_set_safe_only_excludes_resume_existing_family_candidates(tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    resumed = ledger.create_sheet(
        title="Resumed family",
        intent_markdown="# Intent Sheet\n\nresume\n",
        uid_override="IS-20260403-220020Z-RSM1",
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
    )
    fresh = ledger.create_sheet(
        title="Experiment Artifact Boundary Contract",
        intent_markdown="# Intent Sheet\n\nartifact\n",
        uid_override="IS-20260403-220021Z-FSH1",
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
    )
    north = ledger.create_sheet(
        title="Mission and North-Star Contract",
        intent_markdown="# Intent Sheet\n\nnorth star\n",
        uid_override="IS-20260403-220022Z-FSH2",
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
    )
    ledger.update_meta(
        resumed["uid"],
        {
            "execution_priority": 101,
            "recommended_wave": "W1",
            "recommended_order_in_wave": 1,
            "derived_pis_uids": ["PIS-20260407-055533Z-3826"],
        },
    )
    ledger.update_meta(fresh["uid"], {"execution_priority": 102, "recommended_wave": "W1", "recommended_order_in_wave": 2})
    ledger.update_meta(north["uid"], {"execution_priority": 103, "recommended_wave": "W1", "recommended_order_in_wave": 3})

    inbox = tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "implement_3826_resumed-family.json").write_text(
        json.dumps(
            {
                "job_id": "implement_3826_resumed-family",
                "binding": {"type": "ledger_uid", "id": "PIS-20260407-055533Z-3826"},
                "schedule": {"mode": "once", "run_at": "2026-04-07T05:55:34Z", "timezone": "UTC"},
            }
        ),
        encoding="utf-8",
    )
    state_root = tmp_path / "governance" / "agents" / "runtime" / "scheduled_jobs" / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "implement_3826_resumed-family.json").write_text(
        json.dumps({"job_id": "implement_3826_resumed-family", "active_run_id": "", "last_status": "ok"}),
        encoding="utf-8",
    )

    rows = next_sheets(repo_root=tmp_path, limit=3, project_id="causyl", safe_only=True, set_safe_only=True)

    assert resumed["uid"] not in [row["uid"] for row in rows]
    assert fresh["uid"] in [row["uid"] for row in rows]
    assert all(row["parallel_safety"]["reason"] != "resume_existing_family" for row in rows)


def test_next_sheets_skips_draft_intent_with_already_verified_parent_family(tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    stale = ledger.create_sheet(
        title="Stale draft with verified family",
        intent_markdown="# Intent Sheet\n\nstale\n",
        uid_override="IS-20260403-220030Z-STL1",
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
    )
    fresh = ledger.create_sheet(
        title="Fresh draft",
        intent_markdown="# Intent Sheet\n\nfresh\n",
        uid_override="IS-20260403-220031Z-FSH3",
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
    )
    ledger.update_meta(stale["uid"], {"execution_priority": 101, "derived_pis_uids": ["PIS-20260407-055533Z-3826"]})
    ledger.update_meta(fresh["uid"], {"execution_priority": 102})

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "04" / "PIS-20260407-055533Z-3826__verified-family"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "meta.json").write_text(json.dumps({"uid": "PIS-20260407-055533Z-3826", "status": "verified"}, indent=2) + "\n", encoding="utf-8")
    child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "04" / "PIS-20260407-055534Z-C111__verified-child"
    child_dir.mkdir(parents=True, exist_ok=True)
    (child_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": "PIS-20260407-055534Z-C111",
                "status": "verified",
                "parent_uid": "PIS-20260407-055533Z-3826",
                "integration_meta": {"relationship": "execution_child", "sequence_index": 1},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (child_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "decomposed_from_parent",
                "payload": {"parent_uid": "PIS-20260407-055533Z-3826", "relationship": "execution_child", "critical_path_rank": 1, "depends_on": []},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rows = next_sheets(repo_root=tmp_path, limit=5, project_id="causyl")

    assert stale["uid"] not in [row["uid"] for row in rows]
    assert fresh["uid"] in [row["uid"] for row in rows]
