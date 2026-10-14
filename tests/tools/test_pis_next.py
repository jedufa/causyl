from __future__ import annotations

from infra.webui.ledger.ledger_manager import ChangeLedger
from tools.bin.pis_next import next_pises


def test_next_pises_prefers_execution_priority_and_excludes_closed_statuses(tmp_path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    a = ledger.create_item(title="Later PIS", slug="later-pis", pis_markdown="# Later\n")
    b = ledger.create_item(title="Earlier PIS", slug="earlier-pis", pis_markdown="# Earlier\n")
    c = ledger.create_item(title="Verified PIS", slug="verified-pis", pis_markdown="# Verified\n")
    d = ledger.create_item(title="Deferred PIS", slug="deferred-pis", pis_markdown="# Deferred\n")
    ledger.update_meta(str(a["uid"]), {"execution_priority": 200})
    ledger.update_meta(str(b["uid"]), {"execution_priority": 100})
    ledger.update_status(str(c["uid"]), "verified")
    ledger.update_status(str(d["uid"]), "deferred")
    ledger.update_meta(str(c["uid"]), {"execution_priority": 1})
    ledger.update_meta(str(d["uid"]), {"execution_priority": 0})

    rows = next_pises(repo_root=tmp_path, limit=5, project_id="causyl")
    assert [row["uid"] for row in rows] == [str(b["uid"]), str(a["uid"])]
