from __future__ import annotations

import json
import io
import runpy
import signal
import sys
from pathlib import Path

from infra.pipeline import automation
from infra.pipeline.user_progress import describe_progress_event
from tools.projflow.app import ProjFlowApp


def test_make_raw_request_preserves_full_objective() -> None:
    prompt = (
        "Extend normalize_text with a keyword-only lowercase=False parameter in app.py. "
        "Keep the existing trimming behavior by default. When lowercase=True, return the trimmed "
        "text in lowercase. Update tests/test_app.py by adding these exact tests: "
        "test_default_behavior_preserves_case, "
        "test_lowercase_true_lowercases_trimmed_text, "
        "and test_none_still_returns_empty_string."
    )

    raw = automation.make_raw_request(prompt_text=prompt, repo_root=".", job_id="JOB-test")

    assert raw["objective"] == prompt
    assert raw["description"] == prompt
    assert raw["user_notes"] == prompt
    assert len(raw["title"]) <= 220
    assert "test_none_still_returns_empty_string" in raw["objective"]
    assert "test_none_still_returns_empty_string" not in raw["title"]


def test_run_full_auto_emits_progress_events(monkeypatch, tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    job_id = "JOB-progress-001"
    run_id = "RUN-progress-001"
    job_dir = jobs_root / job_id
    patch_plan = job_dir / "runs" / run_id / "07_patch_plan.json"
    patch_plan.parent.mkdir(parents=True, exist_ok=True)
    patch_plan.write_text("{}\n", encoding="utf-8")

    config = {"_derived": {"jobs_root": str(jobs_root)}}
    monkeypatch.setattr(automation, "load_pipeline_config", lambda **kwargs: config)

    class FakeRunner:
        def __init__(self, *, repo_root, stages=None, config_overrides=None, progress_callback=None) -> None:
            self.progress_callback = progress_callback

        def run(self, job_id: str) -> dict:
            assert self.progress_callback is not None
            self.progress_callback(
                "pipeline_started",
                {"job_id": job_id, "run_id": run_id, "total_stages": 1, "start_stage": "script_1_job_intake", "stop_after_stage": "script_1_job_intake"},
            )
            self.progress_callback(
                "stage_start",
                {"job_id": job_id, "run_id": run_id, "stage": "script_1_job_intake", "stage_index": 1, "total_stages": 1},
            )
            self.progress_callback(
                "stage_complete",
                {"job_id": job_id, "run_id": run_id, "stage": "script_1_job_intake", "stage_index": 1, "total_stages": 1, "execution_duration_ms": 12},
            )
            self.progress_callback(
                "pipeline_complete",
                {"job_id": job_id, "run_id": run_id, "status": "WAITING_REVIEW", "current_stage": "script_7_patch_plan", "next_stage": None},
            )
            return {"run_id": run_id, "job_dir": str(job_dir), "status": "WAITING_REVIEW"}

    monkeypatch.setattr(automation, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(automation, "render_review", lambda **kwargs: {"markdown_path": str(job_dir / "review.md")})
    monkeypatch.setattr(automation, "persist_operator_decision", lambda **kwargs: {"decision": "APPROVE"})
    monkeypatch.setattr(
        automation,
        "build_run_summary",
        lambda **kwargs: {"job_id": job_id, "run_id": run_id, "status": kwargs.get("status"), "summary_paths": {}, "selected_files": ["infra/bin/go"]},
    )
    monkeypatch.setattr(
        automation,
        "persist_run_summary",
        lambda **kwargs: {"json_path": str(job_dir / "summary.json"), "markdown_path": str(job_dir / "summary.md")},
    )

    events: list[str] = []
    payloads: list[dict] = []

    def _capture(event: str, payload: dict) -> None:
        events.append(event)
        payloads.append(dict(payload))

    summary = automation.run_full_auto(
        repo_root=tmp_path,
        prompt_text="Show progress.",
        job_id=job_id,
        config_overrides={"semantic": {"mode": "off"}},
        auto_approve=True,
        progress_callback=_capture,
    )

    assert summary["status"] == "WAITING_REVIEW"
    assert events == [
        "job_prepared",
        "pipeline_started",
        "stage_start",
        "stage_complete",
        "pipeline_complete",
        "review_render_start",
        "review_render_complete",
        "operator_decision_saved",
        "summary_persisted",
    ]
    assert payloads[0]["job_id"] == job_id
    assert payloads[-1]["status"] == "WAITING_REVIEW"
    job_progress = json.loads((job_dir / "job_progress.json").read_text(encoding="utf-8"))
    assert job_progress["job_id"] == job_id
    assert job_progress["headline"] == "Finished"
    assert job_progress["status"] == "waiting_review"
    assert job_progress["summary_json"] == str(job_dir / "summary.json")


def test_projflow_app_prints_progress(monkeypatch, tmp_path: Path, capsys) -> None:
    def _fake_run_full_auto(*, progress_callback=None, **kwargs):
        assert progress_callback is not None
        progress_callback("job_prepared", {"job_id": "JOB-projflow-001"})
        progress_callback("stage_start", {"stage": "script_1_job_intake", "stage_index": 1, "total_stages": 7})
        progress_callback("stage_complete", {"stage": "script_1_job_intake", "stage_index": 1, "total_stages": 7, "execution_duration_ms": 34})
        progress_callback("pipeline_complete", {"status": "WAITING_REVIEW"})
        return {
            "job_id": "JOB-projflow-001",
            "run_id": "RUN-projflow-001",
            "status": "WAITING_REVIEW",
            "summary_paths": {"json_path": str(tmp_path / "summary.json"), "markdown_path": str(tmp_path / "summary.md")},
            "selected_files": ["infra/bin/go"],
            "stage_timings": [{"stage": "script_1_job_intake", "stage_duration_ms": 34}],
            "retrieval_provenance": {},
            "artifacts": {},
        }

    monkeypatch.setattr("tools.projflow.app.run_full_auto", _fake_run_full_auto)
    app = ProjFlowApp(repo_root=tmp_path)

    rc = app.run(["show", "progress"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "projflow prepared JOB-projflow-001" in captured.err
    assert "projflow stage 1/7 started: script_1_job_intake" in captured.err
    assert "projflow stage 1/7 complete: script_1_job_intake (34 ms)" in captured.err
    assert "projflow pipeline complete status=WAITING_REVIEW" in captured.err


def test_go_script_prints_progress(monkeypatch, tmp_path: Path, capsys) -> None:
    def _fake_run_full_auto(*, progress_callback=None, **kwargs):
        assert progress_callback is not None
        progress_callback("job_prepared", {"job_id": "JOB-go-001"})
        progress_callback("stage_start", {"stage": "script_1_job_intake", "stage_index": 1, "total_stages": 7})
        progress_callback("stage_complete", {"stage": "script_1_job_intake", "stage_index": 1, "total_stages": 7, "execution_duration_ms": 21})
        progress_callback("pipeline_complete", {"status": "WAITING_REVIEW"})
        return {
            "job_id": "JOB-go-001",
            "run_id": "RUN-go-001",
            "status": "WAITING_REVIEW",
            "elapsed_time_ms": 21,
            "selected_files": ["infra/bin/go"],
            "implementation_targets": ["infra/bin/go"],
            "selected_modify_files": ["infra/bin/go"],
            "stage_timings": [{"stage": "script_1_job_intake", "stage_duration_ms": 21}],
            "retrieval_provenance": {"strategy": "deterministic", "semantic_impact": {}},
            "artifacts": {},
        }

    monkeypatch.setattr("infra.pipeline.automation.run_full_auto", _fake_run_full_auto)
    monkeypatch.setattr("infra.pipeline.semantic.semantic_cli_overrides", lambda **kwargs: {"semantic": {"mode": kwargs.get("mode") or "off"}})

    output_dir = tmp_path / "go-output"
    argv = ["go", "--repo-root", str(tmp_path), "--output-dir", str(output_dir), "--json", "show", "progress"]
    monkeypatch.setattr(sys, "argv", argv)

    try:
        runpy.run_path(str((Path(__file__).resolve().parents[2] / "tools" / "bin" / "go")), run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0

    captured = capsys.readouterr()
    assert "go 1/3 [deterministic] starting" in captured.err
    assert "go 1/3 [deterministic] prepared JOB-go-001" in captured.err
    assert "go 1/3 [deterministic] stage 1/7 started: script_1_job_intake" in captured.err
    assert "go 1/3 [deterministic] stage 1/7 complete: script_1_job_intake (21 ms)" in captured.err
    assert "go 1/3 [deterministic] finished status=WAITING_REVIEW" in captured.err
    payload = json.loads(captured.out)
    assert payload["output_dir"] == str(output_dir)


def test_go_script_graceful_ctrl_c_returns_partial_results(monkeypatch, tmp_path: Path, capsys) -> None:
    handlers: dict[int, object] = {}
    call_count = {"value": 0}

    def _fake_signal(sig: int, handler: object):
        previous = handlers.get(sig, signal.SIG_DFL)
        handlers[sig] = handler
        return previous

    def _fake_run_full_auto(*, progress_callback=None, stop_requested=None, **kwargs):
        assert progress_callback is not None
        assert stop_requested is not None
        call_count["value"] += 1
        progress_callback("job_prepared", {"job_id": "JOB-go-interrupt"})
        progress_callback("stage_start", {"stage": "script_1_job_intake", "stage_index": 1, "total_stages": 7})
        handler = handlers[signal.SIGINT]
        assert callable(handler)
        handler(signal.SIGINT, None)
        assert stop_requested() is True
        progress_callback("pipeline_complete", {"status": "FINALIZED"})
        progress_callback(
            "summary_persisted",
            {"job_id": "JOB-go-interrupt", "run_id": "RUN-go-interrupt", "status": "FINALIZED", "summary_json": str(tmp_path / "summary.json"), "summary_md": str(tmp_path / "summary.md")},
        )
        return {
            "job_id": "JOB-go-interrupt",
            "run_id": "RUN-go-interrupt",
            "status": "FINALIZED",
            "elapsed_time_ms": 21,
            "selected_files": ["infra/bin/go"],
            "implementation_targets": ["infra/bin/go"],
            "selected_modify_files": ["infra/bin/go"],
            "stage_timings": [{"stage": "script_1_job_intake", "stage_duration_ms": 21}],
            "retrieval_provenance": {"strategy": "deterministic", "semantic_impact": {}},
            "artifacts": {},
        }

    monkeypatch.setattr(signal, "signal", _fake_signal)
    monkeypatch.setattr("infra.pipeline.automation.run_full_auto", _fake_run_full_auto)
    monkeypatch.setattr("infra.pipeline.semantic.semantic_cli_overrides", lambda **kwargs: {"semantic": {"mode": kwargs.get("mode") or "off"}})

    output_dir = tmp_path / "go-output"
    argv = ["go", "--repo-root", str(tmp_path), "--output-dir", str(output_dir), "--json", "show", "progress"]
    monkeypatch.setattr(sys, "argv", argv)

    try:
        runpy.run_path(str((Path(__file__).resolve().parents[2] / "tools" / "bin" / "go")), run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 130

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["interrupted"] is True
    assert list(payload["runs"].keys()) == ["deterministic"]
    assert call_count["value"] == 1
    assert "Ctrl-C received. Finishing the current attempt and returning the best available result." in captured.err
    assert "stopping after the current mode and returning partial results" in captured.err


def test_describe_progress_event_makes_retry_intent_actionable() -> None:
    info = describe_progress_event(
        "retry_prepared",
        {
            "strategy": "plan_revision",
            "failure_summary": "contract verification found missing test evidence for the A3 mobile tab integration",
            "strategy_note": "Revise the patch plan and execution contract so the next patch changes the right files and checks.",
            "upstream_mutations": ["reenter stage 7 with retry constraints and previous failure patterns"],
            "stagnation_detected": True,
            "same_level_attempts": 4,
            "failure_cluster_repeat_count": 4,
            "stagnant_retries_remaining": 1,
            "delta_from_previous": 0.0,
        },
    )

    assert info["headline"] == "Retrying via plan revision"
    assert "Goal:" in info["detail"]
    assert "Expected change:" in info["detail"]
    assert "Progress:" in info["detail"]
    assert "same failure x4" in info["detail"]
    assert "one more non-improving retry before stop" in info["detail"]


def test_compact_progress_renderer_highlights_retry_goal_and_expected_change() -> None:
    namespace = runpy.run_path(str((Path(__file__).resolve().parents[2] / "tools" / "bin" / "go")))
    renderer_cls = namespace["CompactProgressRenderer"]
    renderer = renderer_cls(prefix="jeff")

    lines = renderer.build_lines(
        event="retry_prepared",
        payload={},
        progress={
            "headline": "Retrying via plan revision",
            "elapsed_ms": 125000,
            "status": "retrying",
            "next_attempt_index": 3,
            "max_attempts": 30,
            "model_tokens_total": 38081,
            "max_model_tokens_total": 100000,
            "files_under_consideration": [
                "mobile/src/main/java/com/example/A3Tab.kt",
                "infra/bin/jeff",
                "tests/mobile/test_a3_pipeline.py",
            ],
            "retry_strategy": "plan_revision",
            "retry_reentry_stage": "script_7_patch_plan",
            "expected_change": "Revise the patch plan and execution contract so the next patch changes the right files and checks.",
            "convergence_summary": "stalled, delta +0.000, same failure x4, one more non-improving retry before stop",
        },
    )

    assert len(lines) == 3
    assert "attempt 3/30" in lines[0]
    assert "total tokens to date: 38,081/100,000" in lines[0]
    assert "working set:" in lines[1]
    assert "convergence:" in lines[2]
    assert "same failure x4" in lines[2]
    assert "one more non-improving retry before stop" in lines[2]
    assert "next: plan revision" in lines[2]


def test_jeff_wrapper_defaults_to_deterministic_only_and_compact_progress(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_execve(path: str, argv: list[str], env: dict[str, str]) -> None:
        captured["path"] = path
        captured["argv"] = list(argv)
        captured["env"] = dict(env)
        raise SystemExit(0)

    monkeypatch.setattr("os.execve", _fake_execve)
    monkeypatch.setattr(sys, "argv", ["jeff", "--repo-root", str(tmp_path), "show", "compact", "progress"])
    fake_stdin = io.StringIO("")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    try:
        runpy.run_path(str((Path(__file__).resolve().parents[2] / "tools" / "bin" / "jeff")), run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0

    argv = list(captured["argv"])
    assert argv[0].endswith("infra/bin/go")
    assert "--compact-progress" in argv
    assert "--deterministic-only" in argv
    assert captured["env"]["PIPELINE_PROGRESS_STYLE"] == "compact"
