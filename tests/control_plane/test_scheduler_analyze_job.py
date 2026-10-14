from tools.bin.scheduler_analyze_job import iteration_brief, recent_sort_key
from tools.bin.scheduler_start_job import parse_selection


def test_parse_selection_supports_single_list_and_ranges():
    assert parse_selection("1,3-5,2", 6) == [1, 3, 4, 5, 2]


def test_recent_sort_key_prefers_active_run_and_latest_timestamp():
    active = {
        "job_id": "a",
        "state": {"active_run_id": "run1", "active_started_utc": "2026-03-16T12:00:00Z"},
        "due": {},
        "created_utc": "2026-03-16T10:00:00Z",
    }
    finished = {
        "job_id": "b",
        "state": {"last_finished_utc": "2026-03-16T13:00:00Z"},
        "due": {},
        "created_utc": "2026-03-16T09:00:00Z",
    }
    assert recent_sort_key(active) < recent_sort_key(finished)


def test_iteration_brief_extracts_runtime_and_previews():
    brief = iteration_brief(
        {
            "iteration": 2,
            "builder": {"content": "builder output text", "meta": {"agent_tech": "ollama", "selected_model": "qwen2.5:3b"}},
            "verifier": {"content": "verifier output text", "meta": {"agent_tech": "codex", "selected_model": "gpt-5.4"}},
        }
    )
    assert brief["iteration"] == 2
    assert brief["builder_runtime"] == "ollama:qwen2.5:3b"
    assert brief["verifier_runtime"] == "codex:gpt-5.4"
    assert "builder output" in brief["builder_preview"]
    assert "verifier output" in brief["verifier_preview"]
