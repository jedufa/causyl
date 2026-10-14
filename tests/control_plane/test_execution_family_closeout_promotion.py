from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import scheduled_jobs as sched
from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger
from infra.webui.ledger.ledger_manager import ChangeLedger


def _write_parent_integration(parent_uid: str, payload: dict, *, repo_root: Path) -> None:
    ledger = ChangeLedger(repo_root=repo_root)
    parent_dir = ledger.get_item_dir(parent_uid)
    meta_path = parent_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["integration_meta"] = payload
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_closeout_grade_evidence(uid: str, *, repo_root: Path) -> None:
    ledger = ChangeLedger(repo_root=repo_root)
    item_dir = ledger.get_item_dir(uid)
    meta_path = item_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["evidence"] = {"claim": f"{uid} verified with durable evidence"}
    meta["changesets"] = [{"kind": "implementation", "summary": f"{uid} changed durable files", "files": ["foo.py"]}]
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_evidence_only(uid: str, *, repo_root: Path) -> None:
    ledger = ChangeLedger(repo_root=repo_root)
    item_dir = ledger.get_item_dir(uid)
    meta_path = item_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["evidence"] = {"claim": f"{uid} verified with durable evidence"}
    meta["changesets"] = []
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_event_with_hash_chain(item_dir: Path, event_type: str, payload: dict, *, utc: str) -> None:
    events_path = item_dir / "events.jsonl"
    existing = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if events_path.exists() else []
    prev_hash = str(existing[-1].get("hash") or "") if existing else ""
    event = {
        "schema_version": "ledger_event_v1",
        "event_id": f"evt-{len(existing) + 1}",
        "utc": utc,
        "type": event_type,
        "payload": payload,
        "prev_hash": prev_hash,
    }
    body = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    event["hash"] = __import__("hashlib").sha256(prev_hash.encode("utf-8") + body).hexdigest()
    existing.append(event)
    events_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in existing), encoding="utf-8")


def test_change_ledger_append_event_chains_from_last_hashed_row(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    uid = "PIS-20260410-000100Z-EV01"
    ledger.create_item(title="Event Item", uid_override=uid, pis_markdown="# Event\n")
    item_dir = ledger.get_item_dir(uid)
    events_path = item_dir / "events.jsonl"
    existing = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    hashed_prev = str(existing[-1]["hash"])
    with events_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"utc": "2026-04-10T00:02:00Z", "type": "legacy_noise", "payload": {"note": "no hash"}}) + "\n")

    ledger.append_event(
        uid,
        event_type="family_parent_reconciled",
        payload={"closeout_child_uid": "PIS-20260410-000101Z-EV02", "status": "verified"},
    )

    rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    appended = rows[-1]
    assert appended["type"] == "family_parent_reconciled"
    assert appended["prev_hash"] == hashed_prev
    assert sched._execution_family_closeout_events_chain_error(item_dir) == ""


def test_execution_family_closeout_machine_promotes_final_child_parent_and_intent(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)
    parent_uid = "PIS-20260410-010000Z-PA01"
    implementation_uid = "PIS-20260410-010001Z-IM01"
    validation_uid = "PIS-20260410-010002Z-VA01"
    final_uid = "PIS-20260410-010003Z-CL01"
    intent_uid = "IS-20260410-010004Z-IN01"

    ledger.create_item(title="Parent", uid_override=parent_uid, pis_markdown="# Parent\n")
    ledger.create_item(title="Implementation", uid_override=implementation_uid, pis_markdown="# Implementation\n")
    ledger.create_item(title="Validation", uid_override=validation_uid, pis_markdown="# Validation\n")
    ledger.create_item(title="Closeout", uid_override=final_uid, pis_markdown="# Closeout\n")
    ledger.update_status(implementation_uid, "verified", note="test setup")
    ledger.update_status(validation_uid, "verified", note="test setup")
    _write_closeout_grade_evidence(implementation_uid, repo_root=tmp_path)
    _write_closeout_grade_evidence(validation_uid, repo_root=tmp_path)
    intents.create_sheet(
        title="Intent",
        intent_markdown="# Intent\n",
        uid_override=intent_uid,
        validating_pis_uids=[validation_uid],
        derived_pis_uids=[parent_uid],
    )
    _write_parent_integration(
        parent_uid,
        {
            "intent_sheet_uid": intent_uid,
            "intent_validation_child_uid": validation_uid,
            "required_final_child_uid": final_uid,
            "required_final_child_kind": "intent_family_closeout",
            "child_uids": [implementation_uid, validation_uid, final_uid],
        },
        repo_root=tmp_path,
    )
    spec = {
        "job_id": "pis_cl01_closeout",
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": parent_uid,
                "source_intent_uid": intent_uid,
                "required_final_child_uid": final_uid,
                "child_uids": [implementation_uid, validation_uid, final_uid],
                "child_job_ids": [],
            }
        },
    }

    closeout_truth = sched._execution_family_closeout_truth_check(spec, repo_root=tmp_path)
    assert closeout_truth["ok"] is False
    assert sorted(closeout_truth["errors"]) == sorted(
        [
            f"child_not_verified:{final_uid}:draft",
            f"required_final_child_not_verified:{final_uid}:draft",
            f"parent_not_verified:{parent_uid}:draft",
            f"source_intent_not_verified:{intent_uid}:draft",
        ]
    )

    result = sched._execution_family_closeout_promote_verified_family(
        spec,
        closeout_truth=closeout_truth,
        repo_root=tmp_path,
    )

    assert result["ok"] is True
    assert sorted(result["promoted"]) == sorted(
        [
            f"final_child:{final_uid}",
            f"parent:{parent_uid}",
            f"parent_reconciled:{parent_uid}",
            f"source_intent:{intent_uid}",
        ]
    )
    assert ledger.read_item(final_uid)["meta"]["status"] == "verified"
    assert ledger.read_item(parent_uid)["meta"]["status"] == "verified"
    assert intents.read_sheet(intent_uid)["meta"]["status"] == "verified"
    parent_events = sched._execution_family_closeout_events(ledger.get_item_dir(parent_uid))
    latest_parent_reconcile = sched._execution_family_closeout_latest_event(parent_events, "family_parent_reconciled")
    assert latest_parent_reconcile["payload"]["closeout_child_uid"] == final_uid
    assert latest_parent_reconcile["payload"]["status"] == "verified"
    assert sched._execution_family_closeout_truth_check(spec, repo_root=tmp_path)["ok"] is True


def test_execution_family_closeout_machine_refuses_to_promote_with_unverified_sibling(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    parent_uid = "PIS-20260410-020000Z-PA02"
    implementation_uid = "PIS-20260410-020001Z-IM02"
    final_uid = "PIS-20260410-020003Z-CL02"
    intent_uid = "IS-20260410-020004Z-IN02"

    ledger.create_item(title="Parent", uid_override=parent_uid, pis_markdown="# Parent\n")
    ledger.create_item(title="Implementation", uid_override=implementation_uid, pis_markdown="# Implementation\n")
    ledger.create_item(title="Closeout", uid_override=final_uid, pis_markdown="# Closeout\n")
    IntentSheetLedger(repo_root=tmp_path).create_sheet(
        title="Intent",
        intent_markdown="# Intent\n",
        uid_override=intent_uid,
        validating_pis_uids=[implementation_uid],
        derived_pis_uids=[parent_uid],
    )
    _write_parent_integration(
        parent_uid,
        {
            "intent_sheet_uid": intent_uid,
            "intent_validation_child_uid": implementation_uid,
            "required_final_child_uid": final_uid,
            "required_final_child_kind": "intent_family_closeout",
            "child_uids": [implementation_uid, final_uid],
        },
        repo_root=tmp_path,
    )
    spec = {
        "job_id": "pis_cl02_closeout",
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": parent_uid,
                "source_intent_uid": intent_uid,
                "required_final_child_uid": final_uid,
                "child_uids": [implementation_uid, final_uid],
                "child_job_ids": [],
            }
        },
    }

    closeout_truth = sched._execution_family_closeout_truth_check(spec, repo_root=tmp_path)
    result = sched._execution_family_closeout_promote_verified_family(
        spec,
        closeout_truth=closeout_truth,
        repo_root=tmp_path,
    )

    assert result["ok"] is False
    assert result["reason"] == "blocking_closeout_truth_errors"
    assert f"child_not_verified:{implementation_uid}:draft" in result["errors"]


def test_execution_family_closeout_machine_refuses_verified_sibling_without_closeout_grade_evidence(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    parent_uid = "PIS-20260410-030000Z-PA03"
    implementation_uid = "PIS-20260410-030001Z-IM03"
    final_uid = "PIS-20260410-030003Z-CL03"
    intent_uid = "IS-20260410-030004Z-IN03"

    ledger.create_item(title="Parent", uid_override=parent_uid, pis_markdown="# Parent\n")
    ledger.create_item(title="Implementation", uid_override=implementation_uid, pis_markdown="# Implementation\n")
    ledger.create_item(title="Closeout", uid_override=final_uid, pis_markdown="# Closeout\n")
    ledger.update_status(implementation_uid, "verified", note="test setup")
    IntentSheetLedger(repo_root=tmp_path).create_sheet(
        title="Intent",
        intent_markdown="# Intent\n",
        uid_override=intent_uid,
        validating_pis_uids=[implementation_uid],
        derived_pis_uids=[parent_uid],
    )
    _write_parent_integration(
        parent_uid,
        {
            "intent_sheet_uid": intent_uid,
            "intent_validation_child_uid": implementation_uid,
            "required_final_child_uid": final_uid,
            "required_final_child_kind": "intent_family_closeout",
            "child_uids": [implementation_uid, final_uid],
        },
        repo_root=tmp_path,
    )
    spec = {
        "job_id": "pis_cl03_closeout",
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": parent_uid,
                "source_intent_uid": intent_uid,
                "required_final_child_uid": final_uid,
                "child_uids": [implementation_uid, final_uid],
                "child_job_ids": [],
            }
        },
    }

    closeout_truth = sched._execution_family_closeout_truth_check(spec, repo_root=tmp_path)

    assert closeout_truth["ok"] is False
    assert f"child_missing_evidence:{implementation_uid}" in closeout_truth["errors"]
    assert f"child_missing_changesets:{implementation_uid}" not in closeout_truth["errors"]


def test_execution_family_closeout_truth_check_handles_required_final_child_after_verified_siblings(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)
    parent_uid = "PIS-20260410-040000Z-PA04"
    implementation_uid = "PIS-20260410-040001Z-IM04"
    validation_uid = "PIS-20260410-040002Z-VA04"
    final_uid = "PIS-20260410-040003Z-CL04"
    intent_uid = "IS-20260410-040004Z-IN04"

    ledger.create_item(title="Parent", uid_override=parent_uid, pis_markdown="# Parent\n")
    ledger.create_item(title="Implementation", uid_override=implementation_uid, pis_markdown="# Implementation\n")
    ledger.create_item(title="Validation", uid_override=validation_uid, pis_markdown="# Validation\n")
    ledger.create_item(title="Closeout", uid_override=final_uid, pis_markdown="# Closeout\n")
    ledger.update_status(implementation_uid, "verified", note="test setup")
    ledger.update_status(validation_uid, "verified", note="test setup")
    _write_closeout_grade_evidence(implementation_uid, repo_root=tmp_path)
    _write_closeout_grade_evidence(validation_uid, repo_root=tmp_path)
    _write_closeout_grade_evidence(final_uid, repo_root=tmp_path)
    intents.create_sheet(
        title="Intent",
        intent_markdown="# Intent\n",
        uid_override=intent_uid,
        validating_pis_uids=[validation_uid],
        derived_pis_uids=[parent_uid],
    )
    _write_parent_integration(
        parent_uid,
        {
            "intent_sheet_uid": intent_uid,
            "intent_validation_child_uid": validation_uid,
            "required_final_child_uid": final_uid,
            "required_final_child_kind": "intent_family_closeout",
            "child_uids": [implementation_uid, validation_uid, final_uid],
        },
        repo_root=tmp_path,
    )
    spec = {
        "job_id": "pis_cl04_closeout",
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": parent_uid,
                "source_intent_uid": intent_uid,
                "required_final_child_uid": final_uid,
                "child_uids": [implementation_uid, validation_uid, final_uid],
                "child_job_ids": [],
            }
        },
    }

    closeout_truth = sched._execution_family_closeout_truth_check(spec, repo_root=tmp_path)

    assert closeout_truth["ok"] is False
    assert sorted(closeout_truth["errors"]) == sorted(
        [
            f"child_not_verified:{final_uid}:draft",
            f"required_final_child_not_verified:{final_uid}:draft",
            f"parent_not_verified:{parent_uid}:draft",
            f"source_intent_not_verified:{intent_uid}:draft",
        ]
    )


def test_execution_family_closeout_truth_check_ignores_non_path_evidence_values_on_final_child(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)
    parent_uid = "PIS-20260410-050000Z-PA05"
    implementation_uid = "PIS-20260410-050001Z-IM05"
    validation_uid = "PIS-20260410-050002Z-VA05"
    final_uid = "PIS-20260410-050003Z-CL05"
    intent_uid = "IS-20260410-050004Z-IN05"

    ledger.create_item(title="Parent", uid_override=parent_uid, pis_markdown="# Parent\n")
    ledger.create_item(title="Implementation", uid_override=implementation_uid, pis_markdown="# Implementation\n")
    ledger.create_item(title="Validation", uid_override=validation_uid, pis_markdown="# Validation\n")
    ledger.create_item(title="Closeout", uid_override=final_uid, pis_markdown="# Closeout\n")
    ledger.update_status(implementation_uid, "verified", note="test setup")
    ledger.update_status(validation_uid, "verified", note="test setup")
    _write_closeout_grade_evidence(implementation_uid, repo_root=tmp_path)
    _write_closeout_grade_evidence(validation_uid, repo_root=tmp_path)
    final_dir = ledger.get_item_dir(final_uid)
    artifact_path = final_dir / "artifacts" / "closeout.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("closeout", encoding="utf-8")
    repo_relative_validation_path = (
        tmp_path
        / "governance"
        / "ledger"
        / "patch_instruction_sheets"
        / "causyl"
        / "verified"
        / "2026"
        / "04"
        / "PIS-20260410-050002Z-VA05__validation"
        / "artifacts"
        / "intent_validation.json"
    )
    repo_relative_validation_path.parent.mkdir(parents=True, exist_ok=True)
    repo_relative_validation_path.write_text("{}", encoding="utf-8")
    final_meta_path = final_dir / "meta.json"
    final_meta = json.loads(final_meta_path.read_text(encoding="utf-8"))
    final_meta["required_evidence"] = ["artifacts/closeout.md"]
    final_meta["evidence"] = {
        "closeout_artifact_md": "artifacts/closeout.md",
        "closeout_decision": "closable",
        "intent_validation_child_uid": validation_uid,
        "intent_validation_artifact": "governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/PIS-20260410-050002Z-VA05__validation/artifacts/intent_validation.json",
        "source_intent_uid": intent_uid,
        "status_recommendation": "verified",
    }
    final_meta_path.write_text(json.dumps(final_meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ledger.append_event(final_uid, event_type="verification_checked", payload={"artifact": "artifacts/closeout.md", "result": "pass"})
    ledger.update_status(final_uid, "verified", note="test setup")
    intents.create_sheet(
        title="Intent",
        intent_markdown="# Intent\n",
        uid_override=intent_uid,
        validating_pis_uids=[validation_uid],
        derived_pis_uids=[parent_uid],
    )
    _write_parent_integration(
        parent_uid,
        {
            "intent_sheet_uid": intent_uid,
            "intent_validation_child_uid": validation_uid,
            "required_final_child_uid": final_uid,
            "required_final_child_kind": "intent_family_closeout",
            "child_uids": [implementation_uid, validation_uid, final_uid],
        },
        repo_root=tmp_path,
    )
    spec = {
        "job_id": "pis_cl05_closeout",
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": parent_uid,
                "source_intent_uid": intent_uid,
                "required_final_child_uid": final_uid,
                "child_uids": [implementation_uid, validation_uid, final_uid],
                "child_job_ids": [],
            }
        },
    }

    closeout_truth = sched._execution_family_closeout_truth_check(spec, repo_root=tmp_path)

    assert f"required_final_child_missing_evidence:{final_uid}:closable" not in closeout_truth["errors"]
    assert f"required_final_child_missing_evidence:{final_uid}:{validation_uid}" not in closeout_truth["errors"]
    assert f"required_final_child_missing_evidence:{final_uid}:{intent_uid}" not in closeout_truth["errors"]
    assert (
        "required_final_child_missing_evidence:"
        f"{final_uid}:governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/"
        "PIS-20260410-050002Z-VA05__validation/artifacts/intent_validation.json"
    ) not in closeout_truth["errors"]


def test_execution_family_closeout_truth_check_rejects_stale_final_child_artifact_refs(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)
    parent_uid = "PIS-20260410-055000Z-PA55"
    implementation_uid = "PIS-20260410-055001Z-IM55"
    validation_uid = "PIS-20260410-055002Z-VA55"
    final_uid = "PIS-20260410-055003Z-CL55"
    intent_uid = "IS-20260410-055004Z-IN55"

    for uid, title in (
        (parent_uid, "Parent"),
        (implementation_uid, "Implementation"),
        (validation_uid, "Validation"),
        (final_uid, "Closeout"),
    ):
        ledger.create_item(title=title, uid_override=uid, pis_markdown=f"# {title}\n")
    ledger.update_status(implementation_uid, "verified", note="test setup")
    ledger.update_status(validation_uid, "verified", note="test setup")
    ledger.update_status(final_uid, "verified", note="test setup")
    ledger.update_status(parent_uid, "verified", note="test setup")
    _write_closeout_grade_evidence(implementation_uid, repo_root=tmp_path)
    _write_evidence_only(validation_uid, repo_root=tmp_path)
    _write_closeout_grade_evidence(final_uid, repo_root=tmp_path)
    intents.create_sheet(title="Intent", intent_markdown="# Intent\n", uid_override=intent_uid, validating_pis_uids=[validation_uid], derived_pis_uids=[parent_uid])
    intents.update_status(intent_uid, "verified", note="test setup")
    final_dir = ledger.get_item_dir(final_uid)
    stale_ref = (
        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
        f"{validation_uid}__validation/artifacts/intent_validation.json"
    )
    relocated_artifact = (
        tmp_path
        / "governance"
        / "ledger"
        / "patch_instruction_sheets"
        / "causyl"
        / "verified"
        / "2026"
        / "04"
        / f"{validation_uid}__validation"
        / "artifacts"
        / "intent_validation.json"
    )
    relocated_artifact.parent.mkdir(parents=True, exist_ok=True)
    relocated_artifact.write_text("{}", encoding="utf-8")
    final_meta = json.loads((final_dir / "meta.json").read_text(encoding="utf-8"))
    final_meta["required_evidence"] = ["artifacts/closeout.md"]
    (final_dir / "artifacts" / "closeout.md").parent.mkdir(parents=True, exist_ok=True)
    (final_dir / "artifacts" / "closeout.md").write_text("closeout", encoding="utf-8")
    final_meta["evidence"] = {"closeout_artifact_md": "artifacts/closeout.md", "intent_validation_artifact": stale_ref}
    (final_dir / "meta.json").write_text(json.dumps(final_meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _append_event_with_hash_chain(final_dir, "status_changed", {"status": "verified"}, utc="2026-04-10T05:50:01Z")
    _append_event_with_hash_chain(final_dir, "ledger_status_changed", {"status": "verified"}, utc="2026-04-10T05:50:02Z")
    _append_event_with_hash_chain(final_dir, "verification_checked", {"result": "pass"}, utc="2026-04-10T05:50:03Z")
    _write_parent_integration(
        parent_uid,
        {
            "intent_sheet_uid": intent_uid,
            "intent_validation_child_uid": validation_uid,
            "required_final_child_uid": final_uid,
            "required_final_child_kind": "intent_family_closeout",
            "child_uids": [implementation_uid, validation_uid, final_uid],
        },
        repo_root=tmp_path,
    )
    spec = {
        "job_id": "pis_cl55_closeout",
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": parent_uid,
                "source_intent_uid": intent_uid,
                "required_final_child_uid": final_uid,
                "child_uids": [implementation_uid, validation_uid, final_uid],
                "child_job_ids": [],
            }
        },
    }

    closeout_truth = sched._execution_family_closeout_truth_check(spec, repo_root=tmp_path)

    assert (
        f"required_final_child_stale_evidence_ref:{final_uid}:{stale_ref}"
        f"->governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/{validation_uid}__validation/artifacts/intent_validation.json"
    ) in closeout_truth["errors"]


def test_execution_family_closeout_truth_check_rejects_invalid_child_event_chain(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)
    parent_uid = "PIS-20260410-057000Z-PA57"
    implementation_uid = "PIS-20260410-057001Z-IM57"
    final_uid = "PIS-20260410-057003Z-CL57"
    intent_uid = "IS-20260410-057004Z-IN57"

    ledger.create_item(title="Parent", uid_override=parent_uid, pis_markdown="# Parent\n")
    ledger.create_item(title="Implementation", uid_override=implementation_uid, pis_markdown="# Implementation\n")
    ledger.create_item(title="Closeout", uid_override=final_uid, pis_markdown="# Closeout\n")
    ledger.update_status(implementation_uid, "verified", note="test setup")
    _write_closeout_grade_evidence(implementation_uid, repo_root=tmp_path)
    impl_dir = ledger.get_item_dir(implementation_uid)
    (impl_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "ledger_event_v1",
                "event_id": "evt-1",
                "utc": "2026-04-10T05:50:01Z",
                "type": "status_changed",
                "payload": {"status": "verified"},
                "prev_hash": "",
                "hash": "corrupt",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    intents.create_sheet(title="Intent", intent_markdown="# Intent\n", uid_override=intent_uid, validating_pis_uids=[implementation_uid], derived_pis_uids=[parent_uid])
    _write_parent_integration(
        parent_uid,
        {
            "intent_sheet_uid": intent_uid,
            "intent_validation_child_uid": implementation_uid,
            "required_final_child_uid": final_uid,
            "required_final_child_kind": "intent_family_closeout",
            "child_uids": [implementation_uid, final_uid],
        },
        repo_root=tmp_path,
    )
    spec = {
        "job_id": "pis_cl57_closeout",
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": parent_uid,
                "source_intent_uid": intent_uid,
                "required_final_child_uid": final_uid,
                "child_uids": [implementation_uid, final_uid],
                "child_job_ids": [],
            }
        },
    }

    closeout_truth = sched._execution_family_closeout_truth_check(spec, repo_root=tmp_path)

    assert f"child_event_chain_invalid:{implementation_uid}:line_1_hash_mismatch" in closeout_truth["errors"]


def test_execution_family_closeout_truth_check_rejects_invalid_parent_event_chain(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)
    parent_uid = "PIS-20260410-058000Z-PA58"
    implementation_uid = "PIS-20260410-058001Z-IM58"
    validation_uid = "PIS-20260410-058002Z-VA58"
    final_uid = "PIS-20260410-058003Z-CL58"
    intent_uid = "IS-20260410-058004Z-IN58"

    for uid, title in (
        (parent_uid, "Parent"),
        (implementation_uid, "Implementation"),
        (validation_uid, "Validation"),
        (final_uid, "Closeout"),
    ):
        ledger.create_item(title=title, uid_override=uid, pis_markdown=f"# {title}\n")
    ledger.update_status(implementation_uid, "verified", note="test setup")
    ledger.update_status(validation_uid, "verified", note="test setup")
    ledger.update_status(final_uid, "verified", note="test setup")
    ledger.update_status(parent_uid, "verified", note="test setup")
    _write_closeout_grade_evidence(implementation_uid, repo_root=tmp_path)
    _write_evidence_only(validation_uid, repo_root=tmp_path)
    _write_closeout_grade_evidence(final_uid, repo_root=tmp_path)
    intents.create_sheet(
        title="Intent",
        intent_markdown="# Intent\n",
        uid_override=intent_uid,
        validating_pis_uids=[validation_uid],
        derived_pis_uids=[parent_uid],
    )
    intents.update_status(intent_uid, "verified", note="test setup")
    _write_parent_integration(
        parent_uid,
        {
            "intent_sheet_uid": intent_uid,
            "intent_validation_child_uid": validation_uid,
            "required_final_child_uid": final_uid,
            "required_final_child_kind": "intent_family_closeout",
            "child_uids": [implementation_uid, validation_uid, final_uid],
        },
        repo_root=tmp_path,
    )
    parent_dir = ledger.get_item_dir(parent_uid)
    (parent_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "ledger_event_v1",
                "event_id": "evt-1",
                "utc": "2026-04-10T05:58:01Z",
                "type": "family_parent_reconciled",
                "payload": {"closeout_child_uid": final_uid, "status": "verified"},
                "prev_hash": "",
                "hash": "corrupt",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    spec = {
        "job_id": "pis_cl58_closeout",
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": parent_uid,
                "source_intent_uid": intent_uid,
                "required_final_child_uid": final_uid,
                "child_uids": [implementation_uid, validation_uid, final_uid],
                "child_job_ids": [],
            }
        },
    }

    closeout_truth = sched._execution_family_closeout_truth_check(spec, repo_root=tmp_path)
    promotion = sched._execution_family_closeout_promote_verified_family(
        spec,
        closeout_truth=closeout_truth,
        repo_root=tmp_path,
    )

    assert f"parent_event_chain_invalid:{parent_uid}:line_1_hash_mismatch" in closeout_truth["errors"]
    assert promotion["ok"] is False
    assert promotion["reason"] == "blocking_closeout_truth_errors"


def test_execution_family_closeout_formal_integrity_defect_allows_missing_validating_pis_link() -> None:
    closeout_truth = {
        "ok": False,
        "errors": [
            "child_not_verified:PIS-20260410-058003Z-CL58:draft",
            "required_final_child_not_verified:PIS-20260410-058003Z-CL58:draft",
            "parent_event_chain_invalid:PIS-20260410-058000Z-PA58:line_1_hash_mismatch",
            "parent_not_verified:PIS-20260410-058000Z-PA58:draft",
            "source_intent_not_verified:IS-20260410-058004Z-IN58:draft",
            "source_intent_missing_validating_pis:IS-20260410-058004Z-IN58:PIS-20260410-058002Z-VA58",
        ],
        "details": {
            "parent_uid": "PIS-20260410-058000Z-PA58",
            "source_intent_uid": "IS-20260410-058004Z-IN58",
            "required_final_child_uid": "PIS-20260410-058003Z-CL58",
            "intent_validation_child_uid": "PIS-20260410-058002Z-VA58",
        },
    }

    assert sched._execution_family_closeout_formal_integrity_defect(closeout_truth) is True


def test_execution_family_closeout_promotion_repairs_missing_validating_link(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)
    parent_uid = "PIS-20260410-058000Z-PA58"
    implementation_uid = "PIS-20260410-058001Z-IM58"
    validation_uid = "PIS-20260410-058002Z-VA58"
    final_uid = "PIS-20260410-058003Z-CL58"
    intent_uid = "IS-20260410-058004Z-IN58"

    ledger.create_item(title="Parent", uid_override=parent_uid, pis_markdown="# Parent\n")
    ledger.create_item(title="Implementation", uid_override=implementation_uid, pis_markdown="# Implementation\n")
    ledger.create_item(title="Validation", uid_override=validation_uid, pis_markdown="# Validation\n")
    ledger.create_item(title="Closeout", uid_override=final_uid, pis_markdown="# Closeout\n")
    ledger.update_status(implementation_uid, "verified", note="test setup")
    ledger.update_status(validation_uid, "verified", note="test setup")
    _write_closeout_grade_evidence(implementation_uid, repo_root=tmp_path)
    _write_evidence_only(validation_uid, repo_root=tmp_path)
    intents.create_sheet(
        title="Intent",
        intent_markdown="# Intent\n",
        uid_override=intent_uid,
        validating_pis_uids=[],
        derived_pis_uids=[],
    )
    _write_parent_integration(
        parent_uid,
        {
            "intent_sheet_uid": intent_uid,
            "intent_validation_child_uid": validation_uid,
            "required_final_child_uid": final_uid,
            "required_final_child_kind": "intent_family_closeout",
            "child_uids": [implementation_uid, validation_uid, final_uid],
        },
        repo_root=tmp_path,
    )
    spec = {
        "job_id": "pis_cl58_closeout",
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": parent_uid,
                "source_intent_uid": intent_uid,
                "required_final_child_uid": final_uid,
                "child_uids": [implementation_uid, validation_uid, final_uid],
                "child_job_ids": [],
            }
        },
    }

    closeout_truth = sched._execution_family_closeout_truth_check(spec, repo_root=tmp_path)

    assert sorted(closeout_truth["errors"]) == sorted(
        [
            f"child_not_verified:{final_uid}:draft",
            f"required_final_child_not_verified:{final_uid}:draft",
            f"parent_not_verified:{parent_uid}:draft",
            f"source_intent_missing_validating_pis:{intent_uid}:{validation_uid}",
            f"source_intent_not_verified:{intent_uid}:draft",
        ]
    )

    result = sched._execution_family_closeout_promote_verified_family(
        spec,
        closeout_truth=closeout_truth,
        repo_root=tmp_path,
    )

    assert result["ok"] is True
    assert f"source_intent_meta:{intent_uid}" in result["promoted"]
    repaired_intent = intents.read_sheet(intent_uid)["meta"]
    assert repaired_intent["status"] == "verified"
    assert repaired_intent["validating_pis_uids"] == [validation_uid]
    assert repaired_intent["derived_pis_uids"] == [parent_uid]


def test_execution_family_closeout_truth_check_allows_validation_child_without_changesets(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)
    parent_uid = "PIS-20260410-060000Z-PA06"
    implementation_uid = "PIS-20260410-060001Z-IM06"
    validation_uid = "PIS-20260410-060002Z-VA06"
    final_uid = "PIS-20260410-060003Z-CL06"
    intent_uid = "IS-20260410-060004Z-IN06"

    ledger.create_item(title="Parent", uid_override=parent_uid, pis_markdown="# Parent\n")
    ledger.create_item(title="Implementation", uid_override=implementation_uid, pis_markdown="# Implementation\n")
    ledger.create_item(title="Validation", uid_override=validation_uid, pis_markdown="# Validation\n")
    ledger.create_item(title="Closeout", uid_override=final_uid, pis_markdown="# Closeout\n")
    ledger.update_status(implementation_uid, "verified", note="test setup")
    ledger.update_status(validation_uid, "verified", note="test setup")
    ledger.update_status(final_uid, "verified", note="test setup")
    ledger.update_status(parent_uid, "verified", note="test setup")
    _write_closeout_grade_evidence(implementation_uid, repo_root=tmp_path)
    _write_evidence_only(validation_uid, repo_root=tmp_path)
    _write_closeout_grade_evidence(final_uid, repo_root=tmp_path)
    intents.create_sheet(
        title="Intent",
        intent_markdown="# Intent\n",
        uid_override=intent_uid,
        validating_pis_uids=[validation_uid],
        derived_pis_uids=[parent_uid],
    )
    intents.update_status(intent_uid, "verified", note="test setup")
    _write_parent_integration(
        parent_uid,
        {
            "intent_sheet_uid": intent_uid,
            "intent_validation_child_uid": validation_uid,
            "required_final_child_uid": final_uid,
            "required_final_child_kind": "intent_family_closeout",
            "child_uids": [implementation_uid, validation_uid, final_uid],
        },
        repo_root=tmp_path,
    )
    spec = {
        "job_id": "pis_cl06_closeout",
        "prompt_meta": {
            "execution_family_closeout": {
                "parent_uid": parent_uid,
                "source_intent_uid": intent_uid,
                "required_final_child_uid": final_uid,
                "child_uids": [implementation_uid, validation_uid, final_uid],
                "child_job_ids": [],
            }
        },
    }

    closeout_truth = sched._execution_family_closeout_truth_check(spec, repo_root=tmp_path)

    assert closeout_truth["ok"] is True
    assert closeout_truth["details"]["child_handoff_relationships"][implementation_uid] == "implementation_child"
    assert closeout_truth["details"]["child_handoff_relationships"][validation_uid] == "intent_validation_child"
