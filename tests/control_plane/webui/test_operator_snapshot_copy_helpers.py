from infra.webui import server_lab_assistant_operator_snapshot_copy as copy


def test_status_copy_from_status_maps_waiting_on_operator() -> None:
    payload = copy.status_copy_from_status("waiting_on_operator")
    assert payload["copy_key"] == "waiting_on_operator"
    assert payload["label"] == "Waiting on operator"


def test_overall_status_copy_prefers_truth_conflict() -> None:
    payload = copy.overall_status_copy(
        operator_state="running",
        backend_projection_state="fresh",
        stale_sources=[],
        truth_conflicts=[{"kind": "mismatch"}],
        blocked_tasks=[],
        active_tasks=[{"task_id": "t1"}],
        completed_tasks=[],
    )
    assert payload["copy_key"] == "truth_conflict"
