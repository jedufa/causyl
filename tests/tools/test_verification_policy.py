import pytest

from infra.webui import server_verification_policy as policy


def _snapshot_base():
    return {
        "freshness": {"stale": False},
        "operator_summary": {"truth_conflicts": []},
        "delegation_proof": {
            "summary": {"overall_status": "ok"},
            "freshness": {"stale": False},
        },
        "active_tasks": [],
        "completed_tasks": [],
    }


def test_claim_gate_allows_when_proof_fresh_ok_and_no_pending_targets():
    snap = _snapshot_base()
    res = policy.evaluate_claim_gate(snap)
    assert res["allowed"] is True
    assert res["reasons"] == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["freshness"].update({"stale": True}),
        lambda s: s.update({"delegation_proof": {"freshness": {"stale": True}, "summary": {"overall_status": "ok"}}}),
        lambda s: s.update({"delegation_proof": {"freshness": {"stale": False}, "summary": {"overall_status": "missing"}}}),
    ],
)
def test_claim_gate_blocks_on_stale_or_missing_proof(mutate):
    snap = _snapshot_base()
    mutate(snap)
    res = policy.evaluate_claim_gate(snap)
    assert res["allowed"] is False
    assert any(
        reason in set(res["reasons"]) for reason in {"durable_truth_stale", "stale_proof", "proof_overall_missing"}
    )


def test_claim_gate_blocks_when_required_proof_targets_pending():
    snap = _snapshot_base()
    snap["active_tasks"] = [
        {
            "acceptance_contract": {
                "proof_targets": [
                    {"required": True, "status": "pending"},
                    {"required": True, "status": "satisfied"},
                    {"required": False, "status": "pending"},
                ]
            }
        }
    ]
    res = policy.evaluate_claim_gate(snap)
    assert res["allowed"] is False
    assert "pending_required_proof_targets" in set(res["reasons"]) 

