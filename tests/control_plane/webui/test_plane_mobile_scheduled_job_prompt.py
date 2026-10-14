from infra.webui.server_routes_and_main import _plane_mobile_schedule_job_reply


def test_plane_mobile_schedule_job_reply_creates_pipeline_job(monkeypatch):
    captured = {}

    def fake_save_job(payload):
        captured["payload"] = dict(payload)
        return {
            "job_id": payload["job_id"],
            "display_title": payload.get("display_title", ""),
            "job_kind": payload.get("job_kind", ""),
            "runtime": dict(payload.get("runtime") or {}),
            "schedule": dict(payload.get("schedule") or {}),
        }

    def fake_request_run_now(job_id, request_source="manual"):
        captured["run_now"] = {"job_id": job_id, "request_source": request_source}
        return {"ok": True, "queued": True, "job_id": job_id}

    monkeypatch.setattr("infra.webui.server_routes_and_main._scheduled_jobs.save_job", fake_save_job)
    monkeypatch.setattr("infra.webui.server_routes_and_main._scheduled_jobs.request_run_now", fake_request_run_now)

    prompt = """
create job
{
  "job_id": "mobile-pipeline-job",
  "display_title": "Mobile Pipeline Job",
  "job_kind": "build_verify_loop",
  "binding": {"type": "ledger_uid", "id": "PIS-20260330-114500Z-ABCD"},
  "schedule": {"mode": "once", "run_at": "2026-03-30T12:00:00Z", "timezone": "UTC"},
  "prompt": "Implement the bound PIS and verify it.",
  "runtime": {
    "executor_agent_tech": "pipeline",
    "pipeline_executor_mode": "full_auto",
    "model": "gpt-5.4"
  },
  "loop": {"max_iterations": 4},
  "run_now": true
}
""".strip()

    reply = _plane_mobile_schedule_job_reply(prompt)

    assert reply is not None
    assert "Created scheduled job `mobile-pipeline-job`" in reply
    assert "executor=pipeline" in reply
    assert "pipeline_mode=full_auto" in reply
    assert "model=gpt-5.4" in reply
    assert captured["payload"]["job_id"] == "mobile-pipeline-job"
    assert captured["payload"]["runtime"]["executor_agent_tech"] == "pipeline"
    assert captured["run_now"]["job_id"] == "mobile-pipeline-job"


def test_plane_mobile_schedule_job_reply_returns_usage_for_bad_payload():
    reply = _plane_mobile_schedule_job_reply("create job\nnot json")

    assert reply is not None
    assert "create a scheduled job from the plane" in reply
    assert "Error:" in reply
