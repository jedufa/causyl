from __future__ import annotations

import datetime as dt
import io
import json

from tools.bin import scheduler_live_watch as watch


def test_fmt_duration_is_human_readable() -> None:
    assert watch._fmt_duration(9) == "9s"
    assert watch._fmt_duration(65) == "1m 05s"
    assert watch._fmt_duration(3665) == "1h 01m 05s"


def test_fmt_job_block_active_shows_timeout_progress(monkeypatch) -> None:
    fixed_now = dt.datetime(2026, 3, 27, 8, 45, 0, tzinfo=dt.timezone.utc)

    class _FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(watch.dt, "datetime", _FixedDateTime)
    row = {
        "job_id": "job_active_001",
        "job_kind": "build_verify_loop",
        "prompt": "Run watchdog audit.",
        "policy": {"max_runtime_seconds": 1800},
        "loop": {"max_iterations": 6},
        "runtime": {"model": "gpt-5.4"},
        "state": {
            "active_run_id": "sj_live_001",
            "last_run_id": "sj_prev_001",
            "active_started_utc": "2026-03-27T08:30:00Z",
            "active_loop_iteration": 2,
            "active_loop_stage": "verifier",
            "active_loop_completed_iterations": 1,
            "last_status": "running",
        },
        "due": {"reason": "active_run"},
    }

    lines = watch._fmt_job_block("ACTIVE", row, tz_name="UTC", color=False, active_marker="*", mode="active")
    joined = "\n".join(lines)
    assert "iteration: 2/6, verifier running, completed=1/6" in joined
    assert "timeout: elapsed=15m 00s remaining=15m 00s limit=30m 00s" in joined
    assert "progress: [##########----------] 50%" in joined


def test_fmt_job_block_previous_shows_completed_iterations(tmp_path, monkeypatch) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text('{"loop":{"completed_iterations":2}}', encoding="utf-8")
    row = {
        "job_id": "job_prev_001",
        "job_kind": "build_verify_loop",
        "loop": {"max_iterations": 6},
        "state": {
            "last_status": "ok",
            "last_run_id": "sj_prev_001",
            "last_finished_utc": "2026-03-27T08:45:00Z",
            "last_receipt_path": str(receipt_path),
        },
        "due": {"reason": "already_executed"},
    }

    lines = watch._fmt_job_block("PREVIOUS", row, tz_name="UTC", color=False, mode="previous")
    joined = "\n".join(lines)
    assert "iterations: completed 2/6" in joined


def test_family_identifier_counts_implementation_children_separately(monkeypatch) -> None:
    metas = {
        "PIS-PARENT-7198": {
            "slug": "research-evidence-pack-contr",
            "integration_meta": {
                "child_uids": [
                    "PIS-CHILD-0001",
                    "PIS-CHILD-0002",
                    "PIS-VALID-B40E",
                    "PIS-CLOSE-A321",
                ],
                "implementation_child_uids": [
                    "PIS-CHILD-0001",
                    "PIS-CHILD-0002",
                ],
                "intent_sheet_uid": "IS-20260403-212914Z-5E8C",
            },
        },
        "PIS-CHILD-0002": {
            "integration_meta": {
                "parent_uid": "PIS-PARENT-7198",
                "intent_sheet_uid": "IS-20260403-212914Z-5E8C",
                "relationship": "execution_child",
            }
        },
        "PIS-VALID-B40E": {
            "integration_meta": {
                "parent_uid": "PIS-PARENT-7198",
                "intent_sheet_uid": "IS-20260403-212914Z-5E8C",
                "relationship": "intent_validation_suite",
            }
        },
        "PIS-CLOSE-A321": {
            "integration_meta": {
                "parent_uid": "PIS-PARENT-7198",
                "intent_sheet_uid": "IS-20260403-212914Z-5E8C",
                "relationship": "intent_family_closeout",
            }
        },
        "IS-20260403-212914Z-5E8C": {
            "slug": "research-evidence-pack-and-literature-compendium-contract",
            "integration_meta": {"sheet_id": "IS-12"},
        },
    }
    monkeypatch.setattr(watch, "_ledger_meta_for_uid", lambda uid: metas.get(str(uid).strip().upper(), {}))

    implementation_row = {
        "job_id": "pis_0002_child",
        "binding": {"type": "ledger_uid", "id": "PIS-CHILD-0002"},
        "display_title": "child 2",
    }
    validation_row = {
        "job_id": "pis_b40e_validation",
        "binding": {"type": "ledger_uid", "id": "PIS-VALID-B40E"},
        "display_title": "Validate Research Evidence Pack",
    }
    closeout_row = {
        "job_id": "pis_a321_close",
        "binding": {"type": "ledger_uid", "id": "PIS-CLOSE-A321"},
        "display_title": "Close Research Evidence Pack",
    }

    assert watch._job_family_identifier(implementation_row) == "[PIS 2 of 2 | IS-12]"
    assert watch._job_family_identifier(validation_row) == "[E2E 1 of 1 | IS-12]"
    assert watch._job_family_identifier(closeout_row) == "[Closeout 1 of 1 | IS-12]"


def test_fmt_job_block_active_shows_token_estimate(monkeypatch) -> None:
    monkeypatch.setattr(
        watch,
        "_token_summary_for_row",
        lambda row, mode: {
            "inbound_tokens_est": 1200,
            "outbound_tokens_est": 300,
            "total_tokens_est": 1500,
            "source": "active_transcript_window",
        },
    )
    row = {
        "job_id": "job_active_tokens",
        "job_kind": "build_verify_loop",
        "policy": {"max_runtime_seconds": 1800},
        "loop": {"max_iterations": 4},
        "state": {
            "active_run_id": "sj_live_tok",
            "active_started_utc": "2026-03-27T08:30:00Z",
            "active_loop_iteration": 1,
            "active_loop_stage": "builder",
            "active_loop_completed_iterations": 0,
            "last_status": "running",
        },
        "due": {"reason": "active_run"},
    }

    lines = watch._fmt_job_block("ACTIVE", row, tz_name="UTC", color=False, mode="active")
    joined = "\n".join(lines)
    assert "tokens_so_far_est: total=1,500 in=1,200 out=300 source=active_transcript_window" in joined


def test_fmt_job_block_previous_shows_token_estimate_by_iteration(monkeypatch) -> None:
    monkeypatch.setattr(
        watch,
        "_token_summary_for_row",
        lambda row, mode: {
            "inbound_tokens_est": 2000,
            "outbound_tokens_est": 500,
            "total_tokens_est": 2500,
            "source": "receipt_loop_stage_bytes",
            "per_iteration": [
                {"iteration": 1, "total_tokens_est": 900},
                {"iteration": 2, "total_tokens_est": 1600},
            ],
        },
    )
    row = {
        "job_id": "job_prev_tokens",
        "job_kind": "build_verify_loop",
        "loop": {"max_iterations": 4},
        "state": {
            "last_status": "failed_verification",
            "last_run_id": "sj_prev_tok",
            "last_finished_utc": "2026-03-27T08:45:00Z",
            "last_receipt_path": "/tmp/fake.json",
        },
        "due": {"reason": "already_executed"},
    }

    lines = watch._fmt_job_block("PREVIOUS", row, tz_name="UTC", color=False, mode="previous")
    joined = "\n".join(lines)
    assert "tokens_est: total=2,500 in=2,000 out=500 source=receipt_loop_stage_bytes" in joined
    assert "tokens_by_iteration_est: i1=900 i2=1,600" in joined


def test_compact_job_line_active_shows_token_estimate(monkeypatch) -> None:
    monkeypatch.setattr(
        watch,
        "_token_summary_for_row",
        lambda row, mode: {
            "inbound_tokens_est": 1200,
            "outbound_tokens_est": 300,
            "total_tokens_est": 1500,
            "source": "active_transcript_window",
        },
    )
    row = {
        "job_id": "job_active_tokens",
        "display_title": "Active token job",
        "state": {
            "active_run_id": "sj_live_tok",
            "active_started_utc": "2026-03-27T08:30:00Z",
        },
        "due": {"reason": "active_run"},
    }

    line = watch._compact_job_line(row, index=1, tz_name="UTC")
    assert "tok=1,500" in line


def test_compact_job_line_previous_shows_token_estimate(monkeypatch) -> None:
    monkeypatch.setattr(
        watch,
        "_token_summary_for_row",
        lambda row, mode: {
            "inbound_tokens_est": 2000,
            "outbound_tokens_est": 500,
            "total_tokens_est": 2500,
            "source": "receipt_loop_stage_bytes",
        },
    )
    row = {
        "job_id": "job_prev_tokens",
        "display_title": "Previous token job",
        "state": {
            "last_receipt_path": "/tmp/fake.json",
        },
        "due": {"reason": "already_executed"},
    }

    line = watch._compact_job_line(row, index=1, tz_name="UTC")
    assert "tok=2,500" in line


def test_compact_job_line_explicit_pass_hides_inner_loop_status() -> None:
    row = {
        "job_id": "pis_bbf4_20260502_define-the-quebec-search-corrido_p2of3",
        "display_title": "pass 2/3 Define the Quebec search corridor",
        "job_kind": "build_verify_loop",
        "loop": {"max_iterations": 1},
        "state": {
            "active_run_id": "sj_live_001",
            "active_started_utc": "2026-03-27T09:00:00Z",
            "active_loop_iteration": 1,
            "active_loop_stage": "builder",
            "last_status": "running",
        },
        "due": {"reason": "active_run"},
    }

    line = watch._compact_job_line(row, index=1, tz_name="UTC")
    assert "pass 2/3" in line
    assert "iter=1/1/builder" not in line


def test_main_handles_timeout_as_normal_watch_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        watch,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "url": "http://example.invalid:8787",
                "interval": 0.5,
                "prefix": "",
                "timezone": "UTC",
                "no_color": True,
                "no_clear": True,
                "pipeline_limit": 5,
                "include_done": False,
                "oneshot": False,
                "startup_grace": 0.0,
            },
        )(),
    )
    monkeypatch.setattr(watch, "_api_get", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("timed out")))
    monkeypatch.setattr(watch.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))

    rc = watch.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "Scheduler Live Watch error: timed out" in out
    assert "unexpected error" not in out


def test_main_uses_local_fallback_when_local_jobs_api_times_out(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        watch,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "url": "http://localhost:8787",
                "interval": 0.5,
                "prefix": "",
                "timezone": "UTC",
                "no_color": True,
                "no_clear": True,
                "pipeline_limit": 5,
                "include_done": False,
                "oneshot": True,
                "startup_grace": 0.0,
                "verbose": False,
            },
        )(),
    )

    def _fake_api_get(base_url: str, path: str) -> dict:
        if path == "/api/schedules/jobs":
            raise TimeoutError("timed out")
        if path == "/api/schedules/health":
            return {"scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""}}
        return {}

    monkeypatch.setattr(watch, "_api_get", _fake_api_get)
    monkeypatch.setattr(
        watch,
        "_local_jobs_payload",
        lambda base_url, reason="": {
            "jobs": [
                {
                    "job_id": "job_local_fallback_001",
                    "enabled": True,
                    "display_title": "Local fallback job",
                    "state": {"active_run_id": "sj_local_001", "last_status": "running", "active_started_utc": "2026-03-27T09:10:00Z"},
                    "due": {"reason": "active_run"},
                    "policy": {"max_runtime_seconds": 1800},
                }
            ],
            "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
            "summary": f"watch local fallback active: {reason}",
            "local_fallback": True,
        },
    )

    rc = watch.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "Local fallback job" in out
    assert "scope: 1 jobs active=1 blocked=0 error=0" in out
    assert "Scheduler Live Watch error:" not in out


def test_render_separates_auxiliary_reconcile_sidecars_from_family_next_in_line(monkeypatch) -> None:
    monkeypatch.setattr(watch, "_fmt_time", lambda raw, tz_name: "")
    monkeypatch.setattr(watch, "_short_clock", lambda raw, tz_name: "")
    monkeypatch.setattr(watch, "_token_summary_for_row", lambda row, mode: {})
    monkeypatch.setattr(watch, "_active_family_scope_line", lambda row, rows: {})
    monkeypatch.setattr(watch, "_scheduler_v2_summary_line", lambda payload, color: "")
    monkeypatch.setattr(watch, "_scheduler_v2_detail_lines", lambda payload, color, limit=6: [])
    monkeypatch.setattr(watch, "_watchdog_summary_lines", lambda status, tz_name, verbose=False: [])
    monkeypatch.setattr(watch, "_watchdog_busy_line", lambda status: "")
    monkeypatch.setattr(watch, "_service_next_run", lambda status: ("", ""))
    monkeypatch.setattr(watch, "_fresh_watchdog_status", lambda status: status)
    monkeypatch.setattr(watch, "_fresh_healer_status", lambda status: status)
    monkeypatch.setattr(watch, "_active_count_disagreement_line", lambda payload, computed_active_count, color: "")
    monkeypatch.setattr(watch, "_compact_job_line", lambda row, index, tz_name, truth=None: f"{index:02d}. {row['job_id']}")

    rows = [
        {
            "job_id": "pis_d4f6_20260412_add-cli-and-agent-trigger-entryp",
            "display_title": "d4f6",
            "enabled": True,
            "depends_on": ["pis_c3e5_20260412_implement-deterministic-pis-impr"],
            "binding": {"type": "ledger_uid", "id": "PIS-20260412-062103Z-D4F6"},
            "state": {"active_run_id": "sj_live_d4f6", "last_status": "running"},
            "due": {"reason": "active_run"},
            "prompt_meta": {"execution_family_contract": {"parent_uid": "PIS-20260412-060451Z-EB85"}},
        },
        {
            "job_id": "pis_e5g7_20260412_validate-pis-improvement-automat",
            "display_title": "e5g7",
            "enabled": True,
            "depends_on": ["pis_d4f6_20260412_add-cli-and-agent-trigger-entryp"],
            "binding": {"type": "ledger_uid", "id": "PIS-20260412-062104Z-E5G7"},
            "state": {"active_run_id": "", "last_status": ""},
            "due": {"reason": "dependency_pending:pis_d4f6_20260412_add-cli-and-agent-trigger-entryp"},
            "prompt_meta": {"execution_family_contract": {"parent_uid": "PIS-20260412-060451Z-EB85"}},
        },
        {
            "job_id": "verify_c3e5_20260412_status_reconcile",
            "display_title": "Verify c3e5 ledger status reconciliation",
            "enabled": True,
            "depends_on": [],
            "binding": {"type": "ledger_uid", "id": "PIS-20260412-062102Z-C3E5"},
            "state": {"active_run_id": "", "run_now_requested_utc": "2026-04-12T19:08:59Z"},
            "due": {"reason": "run_now_requested", "due_utc": "2026-04-12T19:08:59Z"},
            "prompt_meta": {
                "generated_by_closeout_truth_reconcile": True,
                "generated_from_parent_uid": "PIS-20260412-060451Z-EB85",
            },
        },
    ]

    output = watch._render(
        "http://localhost:8787",
        {"jobs": rows, "scheduler": {}, "watchdog": {}, "healer": {}},
        color=False,
        verbose=False,
        pipeline_limit=10,
    )

    next_block = output.split("NEXT IN LINE", 1)[1]
    assert "pis_e5g7_20260412_validate-pis-improvement-automat" in next_block
    assert "verify_c3e5_20260412_status_reconcile" not in next_block.split("AUXILIARY", 1)[0]
    assert "AUXILIARY (showing 1)" in output
    assert "verify_c3e5_20260412_status_reconcile" in output.split("AUXILIARY", 1)[1]


def test_render_compact_family_rows_prefer_v2_child_status_reason(monkeypatch) -> None:
    payload = {
        "jobs": [
            {
                "job_id": "pis_d4f6_20260412_add-cli-and-agent-trigger-entryp",
                "enabled": True,
                "display_title": "d4f6",
                "depends_on": ["pis_c3e5_20260412_implement-deterministic-pis-impr"],
                "binding": {"type": "ledger_uid", "id": "PIS-20260412-062103Z-D4F6"},
                "state": {"active_run_id": "sj_live_d4f6", "active_started_utc": "2026-03-27T09:10:00Z", "last_status": "running"},
                "due": {"reason": "active_run"},
                "policy": {"max_runtime_seconds": 1800},
            },
            {
                "job_id": "pis_e5g7_20260412_validate-pis-improvement-automat",
                "enabled": True,
                "display_title": "e5g7",
                "depends_on": ["pis_a1c3", "pis_b2d4", "pis_c3e5_20260412_implement-deterministic-pis-impr", "pis_d4f6_20260412_add-cli-and-agent-trigger-entryp"],
                "binding": {"type": "ledger_uid", "id": "PIS-20260412-062104Z-E5G7"},
                "state": {"active_run_id": "", "last_status": ""},
                "due": {"reason": "dependency_pending:pis_c3e5_20260412_implement-deterministic-pis-impr"},
            },
        ],
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
        "watchdog": {"running": True, "interval_s": 900, "last_receipt_utc": "2026-03-27T09:19:00Z", "last_error": ""},
        "healer": {"running": True, "interval_s": 300, "last_receipt_utc": "2026-03-27T09:18:00Z", "last_error": ""},
        "scheduler_v2": {
            "shadow_payload": {
                "evaluation": {
                    "projection": {
                        "family_parent_uid": "PIS-20260412-060451Z-EB85",
                        "family_status": "active",
                        "current_stage": "implement",
                        "family_progress": {"completed_children": 3, "total_children": 6, "active_children": 1, "blocked_children": 0, "failed_children": 0},
                        "active_job": {
                            "child_uid": "PIS-20260412-062103Z-D4F6",
                            "job_id": "pis_d4f6_20260412_add-cli-and-agent-trigger-entryp",
                            "child_status": "active",
                            "status_reason": "running",
                        },
                        "next_jobs": [
                            {
                                "child_uid": "PIS-20260412-062104Z-E5G7",
                                "job_id": "pis_e5g7_20260412_validate-pis-improvement-automat",
                                "child_status": "waiting",
                                "status_reason": "dependency_pending:PIS-20260412-062103Z-D4F6",
                            }
                        ],
                    }
                }
            }
        },
    }

    watch._fresh_watchdog_status = lambda status: status
    watch._fresh_healer_status = lambda status: status
    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False)

    assert "wait=D4F6" in rendered
    assert "wait=C3E5" not in rendered


def test_render_nonverbose_hides_primary_chain_after_completion(monkeypatch) -> None:
    parent_uid = "PIS-20260502-141242Z-1DCD"
    metas = {
        parent_uid: {
            "slug": "quebec-family-lake-house-search-2027",
            "integration_meta": {
                "intent_sheet_uid": "IS-20260502-141234Z-90A1",
                "execution_mode": "explicit_pass_chain_v1",
                "recommended_execution_order": [
                    "PIS-20260502-141914Z-BBF4",
                    "PIS-20260502-141918Z-FDBF",
                    "PIS-20260502-141921Z-8DCF",
                ],
            },
        },
        "PIS-20260502-141914Z-BBF4": {"title": "Define the Quebec search corridor", "integration_meta": {"relationship": "execution_child"}},
        "PIS-20260502-141918Z-FDBF": {"title": "Classify motorboat and fishing status", "integration_meta": {"relationship": "execution_child"}},
        "PIS-20260502-141921Z-8DCF": {"title": "Discover reunion-scale Quebec rental listings", "integration_meta": {"relationship": "execution_child"}},
        "IS-20260502-141234Z-90A1": {"integration_meta": {"sheet_id": "90A1"}},
    }
    monkeypatch.setattr(watch, "_ledger_meta_for_uid", lambda uid: metas.get(str(uid).strip().upper(), {}))
    monkeypatch.setattr(watch, "_fmt_time", lambda raw, tz_name: "")
    monkeypatch.setattr(watch, "_short_clock", lambda raw, tz_name: "")
    monkeypatch.setattr(watch, "_token_summary_for_row", lambda row, mode: {})
    monkeypatch.setattr(watch, "_active_count_disagreement_line", lambda payload, computed_active_count, color: "")
    monkeypatch.setattr(watch, "_watchdog_summary_lines", lambda status, tz_name, verbose=False: [])
    monkeypatch.setattr(watch, "_watchdog_busy_line", lambda status: "")
    monkeypatch.setattr(watch, "_service_next_run", lambda status: ("", ""))
    monkeypatch.setattr(watch, "_fresh_watchdog_status", lambda status: status)
    monkeypatch.setattr(watch, "_fresh_healer_status", lambda status: status)

    payload = {
        "jobs": [
            {
                "job_id": "pis_bbf4_20260502_define-the-quebec-search-corrido_p2of3",
                "enabled": True,
                "display_title": "bbf4 pass 2/3 Define the Quebec search corridor",
                "binding": {"type": "ledger_uid", "id": "PIS-20260502-141914Z-BBF4"},
                "state": {"last_status": "ok", "run_now_requested_utc": "2026-05-03T00:58:20Z"},
                "due": {"reason": "run_now_requested"},
                "prompt_meta": {"explicit_pass_chain": {"projection_for_child_uid": "PIS-20260502-141914Z-BBF4"}},
            },
            {
                "job_id": "pis_fdbf_20260502_classify-motorboat-and-fishing-s_p2of3",
                "enabled": True,
                "display_title": "fdbf pass 2/3 Classify motorboat and fishing status",
                "binding": {"type": "ledger_uid", "id": "PIS-20260502-141918Z-FDBF"},
                "state": {"last_status": "ok", "run_now_requested_utc": "2026-05-03T01:31:13Z"},
                "due": {"reason": "run_now_requested"},
                "prompt_meta": {"explicit_pass_chain": {"projection_for_child_uid": "PIS-20260502-141918Z-FDBF"}},
            },
            {
                "job_id": "pis_8dcf_20260502_discover-reunion-scale-quebec-re_p3of3",
                "enabled": True,
                "display_title": "8dcf pass 3/3 Discover reunion-scale Quebec rental listings",
                "binding": {"type": "ledger_uid", "id": "PIS-20260502-141921Z-8DCF"},
                "state": {"last_status": "ok"},
                "due": {"reason": "already_executed"},
                "prompt_meta": {"explicit_pass_chain": {"projection_for_child_uid": "PIS-20260502-141921Z-8DCF"}},
            },
        ],
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-05-03T05:13:47Z", "last_error": ""},
        "watchdog": {"running": True, "interval_s": 900, "last_receipt_utc": "2026-05-03T05:13:40Z", "last_error": ""},
        "healer": {"running": False, "interval_s": 300, "last_receipt_utc": "", "last_error": ""},
        "scheduler_v2": {
            "status": "ready",
            "projection_equivalent": True,
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "shadow_payload": {
                "evaluation": {
                    "projection": {
                        "family_parent_uid": parent_uid,
                        "family_status": "completed",
                        "current_stage": "closeout",
                        "family_progress": {"completed_children": 3, "total_children": 3, "active_children": 0, "blocked_children": 0, "failed_children": 0},
                        "children": [
                            {"child_uid": "PIS-20260502-141914Z-BBF4", "title": "Define the Quebec search corridor", "child_status": "retired", "status_reason": "terminal_success"},
                            {"child_uid": "PIS-20260502-141918Z-FDBF", "title": "Classify motorboat and fishing status", "child_status": "retired", "status_reason": "terminal_success"},
                            {"child_uid": "PIS-20260502-141921Z-8DCF", "title": "Discover reunion-scale Quebec rental listings", "child_status": "retired", "status_reason": "terminal_success"},
                        ],
                        "active_job": {},
                        "next_jobs": [],
                    }
                }
            },
        },
    }

    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False, pipeline_limit=10)
    assert "PRIMARY CHAIN" not in rendered
    assert "NEXT IN LINE" not in rendered


def test_render_uses_v2_primary_active_row_for_previous_and_active_sections(monkeypatch) -> None:
    monkeypatch.setattr(watch, "_fresh_watchdog_status", lambda status: status)
    monkeypatch.setattr(watch, "_fresh_healer_status", lambda status: status)
    payload = {
        "jobs": [
            {
                "job_id": "pis_c3e5",
                "enabled": True,
                "display_title": "c3e5",
                "binding": {"type": "ledger_uid", "id": "PIS-20260412-062102Z-C3E5"},
                "state": {"last_status": "ok", "last_finished_utc": "2026-04-12T20:00:00Z"},
                "due": {"reason": "already_executed"},
            },
            {
                "job_id": "pis_d4f6",
                "enabled": True,
                "display_title": "d4f6",
                "binding": {"type": "ledger_uid", "id": "PIS-20260412-062103Z-D4F6"},
                "state": {"last_status": "ok", "last_finished_utc": "2026-04-12T20:05:00Z"},
                "due": {"reason": "already_executed"},
            },
            {
                "job_id": "pis_e5g7",
                "enabled": True,
                "display_title": "e5g7",
                "binding": {"type": "ledger_uid", "id": "PIS-20260412-062104Z-E5G7"},
                "state": {"last_status": ""},
                "due": {"reason": "dependency_pending:pis_c3e5"},
            },
        ],
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
        "watchdog": {},
        "healer": {},
        "scheduler_v2": {
            "status": "ready",
            "projection_equivalent": True,
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "shadow_payload": {
                "evaluation": {
                    "projection": {
                        "family_parent_uid": "PIS-20260412-060451Z-EB85",
                        "family_status": "active",
                        "current_stage": "implement",
                        "family_progress": {"completed_children": 2, "total_children": 6, "active_children": 1, "blocked_children": 1, "failed_children": 0},
                        "active_job": {
                            "child_uid": "PIS-20260412-062103Z-D4F6",
                            "job_id": "pis_d4f6",
                            "child_status": "active",
                            "status_reason": "active_run_present",
                        },
                        "next_jobs": [
                            {
                                "child_uid": "PIS-20260412-062104Z-E5G7",
                                "job_id": "pis_e5g7",
                                "child_status": "waiting",
                                "status_reason": "dependency_pending:PIS-20260412-062103Z-D4F6",
                            }
                        ],
                    }
                }
            },
        },
    }

    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False)
    assert "ACTIVE\n  01. [active]" in rendered
    assert "d4f6" in rendered
    assert "PREVIOUS" not in rendered


def test_render_family_progress_prefers_v2_projection_counts(monkeypatch) -> None:
    monkeypatch.setattr(watch, "_fresh_watchdog_status", lambda status: status)
    monkeypatch.setattr(watch, "_fresh_healer_status", lambda status: status)
    monkeypatch.setattr(
        watch,
        "_active_family_scope_line",
        lambda row, rows: {
            "pis_uid4": "OLD1",
            "intent_uid4": "IS-99",
            "progress": "stale legacy progress should not render",
        },
    )
    monkeypatch.setattr(
        watch,
        "_ledger_integration_for_uid",
        lambda uid: {"intent_sheet_uid": "IS-20260403-212914Z-AED0"} if str(uid).strip().upper() == "PIS-20260412-060451Z-EB85" else {},
    )
    payload = {
        "jobs": [],
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
        "watchdog": {},
        "healer": {},
        "scheduler_v2": {
            "status": "ready",
            "projection_equivalent": True,
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "shadow_payload": {
                "evaluation": {
                    "projection": {
                        "family_parent_uid": "PIS-20260412-060451Z-EB85",
                        "family_status": "active",
                        "current_stage": "implement",
                        "family_progress": {"completed_children": 2, "total_children": 6, "active_children": 1, "blocked_children": 2, "failed_children": 0},
                    }
                }
            },
        },
    }

    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False)
    assert "family_progress: v2 family progress: completed=2/6 active=1 blocked=2 failed=0 (EB85)" in rendered
    assert "stale legacy progress should not render" not in rendered


def test_color_enabled_defaults_on_without_tty(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert watch._color_enabled(no_color=False) is True


def test_color_enabled_respects_explicit_disable(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert watch._color_enabled(no_color=True) is False


def test_color_enabled_respects_no_color_env(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert watch._color_enabled(no_color=False) is False


def test_fmt_job_block_blocked_shows_reason_and_action_hint() -> None:
    row = {
        "job_id": "job_blocked_001",
        "state": {
            "last_status": "blocked",
            "last_nonlaunch_reason": "repo_guard_commit_journal_invalid",
        },
        "due": {"reason": "run_now_requested", "due_utc": "2026-03-27T09:13:48Z"},
    }

    lines = watch._fmt_job_block("NEXT", row, tz_name="UTC", color=False)
    joined = "\n".join(lines)
    assert "blocked_reason: repo_guard_commit_journal_invalid" in joined
    assert "action_hint: fix invalid commit-journal entries" in joined


def test_project_row_truth_prefers_shared_projection_over_legacy_fields() -> None:
    truth = watch._project_row_truth(
        {
            "job_id": "projection_override_001",
            "enabled": True,
            "state": {"last_status": "ok"},
            "due": {"eligible": True, "reason": "due", "due_utc": "2026-03-27T09:13:48Z"},
            "scheduler_projection_v1": {
                "projected_status": "blocked",
                "projection_reason": "dependency_pending:build_001",
                "durable_conflict": False,
            },
        },
        {"projection_override_001": {}},
        now_ts=0.0,
        memo={},
    )

    assert truth["status"] == "blocked"
    assert truth["reason"] == "dependency_pending:build_001"
    assert truth["completed_success"] is False


def test_fmt_job_block_ambiguous_projection_shows_degraded_truth() -> None:
    row = {
        "job_id": "job_ambiguous_001",
        "enabled": True,
        "state": {"last_status": "ok", "active_run_id": "sj_ambiguous_001"},
        "due": {"reason": "active_run", "due_utc": "2026-03-27T09:13:48Z"},
        "scheduler_projection_v1": {
            "projected_status": "ambiguous",
            "projection_reason": "active_run_conflicts_with_terminal_lifecycle",
            "durable_conflict": True,
        },
    }

    lines = watch._fmt_job_block("NEXT", row, tz_name="UTC", color=False)
    joined = "\n".join(lines)
    assert "status: ambiguous" in joined
    assert "truth_state: degraded" in joined
    assert "projection_reason: active_run_conflicts_with_terminal_lifecycle" in joined


def test_render_shows_watchdog_and_healer_next_run(monkeypatch) -> None:
    fixed_now = dt.datetime(2026, 3, 27, 9, 20, 0, tzinfo=dt.timezone.utc)
    real_datetime = dt.datetime

    class _FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

        @classmethod
        def fromtimestamp(cls, ts, tz=None):
            return real_datetime.fromtimestamp(ts, tz=tz)

    monkeypatch.setattr(watch.dt, "datetime", _FixedDateTime)
    monkeypatch.setattr(watch, "_fresh_watchdog_status", lambda status: status)
    monkeypatch.setattr(watch, "_fresh_healer_status", lambda status: status)


def test_render_scope_counts_only_actionable_jobs() -> None:
    snapshot = {
        "up": True,
        "heartbeat_utc": "2026-03-27T09:20:00Z",
        "tick_interval_seconds": 30,
        "jobs": [
            {
                "job_id": "job_done_retired",
                "enabled": False,
                "state": {"last_status": "ok", "active_run_id": "", "run_now_requested_utc": ""},
                "due": {"reason": "disabled"},
            },
            {
                "job_id": "job_actionable",
                "enabled": True,
                "state": {"last_status": "", "active_run_id": "", "run_now_requested_utc": ""},
                "due": {"reason": "due"},
            },
        ],
        "watchdog": {},
        "healer": {},
    }

    rendered = watch._render("http://localhost:8787", snapshot, tz_name="UTC", color=False, verbose=False, pipeline_limit=5, include_done=False)

    assert "scope: 1 jobs active=0 blocked=0 error=0" in rendered


def test_render_keeps_projection_driven_family_rows_even_when_specs_are_disabled() -> None:
    snapshot = {
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:20:00Z", "last_error": ""},
        "watchdog": {},
        "healer": {},
        "jobs": [
            {
                "job_id": "pis_active_80c5",
                "enabled": False,
                "display_title": "80c5 Define Canonical Verifier Verdict",
                "depends_on": ["pis_done_5c96"],
                "state": {
                    "active_run_id": "sj_live_001",
                    "active_started_utc": "2026-03-27T09:10:00Z",
                    "active_loop_iteration": 1,
                    "active_loop_stage": "builder",
                    "active_loop_completed_iterations": 0,
                    "last_status": "running",
                },
                "due": {"reason": "disabled"},
                "policy": {"max_runtime_seconds": 1800},
                "scheduler_projection_v1": {
                    "projected_status": "active",
                    "projection_reason": "active_run_present",
                    "durable_conflict": False,
                },
            },
            {
                "job_id": "pis_next_93a9",
                "enabled": False,
                "display_title": "93a9 Wire Builder and Verifier Prompt Contracts",
                "depends_on": ["pis_active_80c5"],
                "state": {"last_status": ""},
                "due": {"reason": "disabled"},
                "scheduler_projection_v1": {
                    "projected_status": "idle",
                    "projection_reason": "disabled",
                    "durable_conflict": False,
                },
            },
            {
                "job_id": "pis_done_5c96",
                "enabled": False,
                "display_title": "5c96 Define Canonical Builder Turn Sections",
                "depends_on": [],
                "state": {"last_status": "ok"},
                "due": {"reason": "disabled"},
                "scheduler_projection_v1": {
                    "projected_status": "completed_once",
                    "projection_reason": "terminal_success",
                    "durable_conflict": False,
                },
            },
        ],
    }

    rendered = watch._render("http://localhost:8787", snapshot, tz_name="UTC", color=False, verbose=False, pipeline_limit=5, include_done=False)

    assert "scope: 1 jobs active=1 blocked=0 error=0" in rendered
    assert "80c5 Define Canonical Verifier Verdict" in rendered
    assert "NEXT IN LINE (showing 1)" in rendered
    assert "93a9 Wire Builder and Verifier Prompt Contracts" in rendered


def test_render_hides_plain_disabled_non_family_rows_from_next_in_line() -> None:
    payload = {
        "jobs": [
            {
                "job_id": "job_disabled_001",
                "enabled": False,
                "display_title": "Disabled",
                "state": {"last_status": ""},
                "due": {"reason": "disabled"},
            }
        ],
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
        "watchdog": {},
        "healer": {},
    }

    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False, verbose=False, include_done=False, pipeline_limit=5)

    assert "scope: 0 jobs active=0 blocked=0 error=0" in rendered
    assert "NEXT IN LINE" not in rendered
    assert "Disabled disabled" not in rendered


def test_render_prioritizes_same_family_next_rows_ahead_of_unrelated_rows(monkeypatch) -> None:
    metas = {
        "PIS-PARENT-D4C5": {
            "integration_meta": {
                "child_uids": ["PIS-DONE-5C96", "PIS-ACTIVE-80C5", "PIS-NEXT-93A9"],
                "implementation_child_uids": ["PIS-DONE-5C96", "PIS-ACTIVE-80C5", "PIS-NEXT-93A9"],
                "intent_sheet_uid": "IS-20260403-212914Z-9C4E",
            }
        },
        "PIS-ACTIVE-80C5": {"integration_meta": {"parent_uid": "PIS-PARENT-D4C5", "intent_sheet_uid": "IS-20260403-212914Z-9C4E", "relationship": "execution_child"}},
        "PIS-NEXT-93A9": {"integration_meta": {"parent_uid": "PIS-PARENT-D4C5", "intent_sheet_uid": "IS-20260403-212914Z-9C4E", "relationship": "execution_child"}},
        "PIS-OTHER-E9DE": {"integration_meta": {"parent_uid": "PIS-PARENT-41E3", "intent_sheet_uid": "IS-20260403-212914Z-41E3", "relationship": "intent_validation_suite"}},
    }
    monkeypatch.setattr(watch, "_ledger_meta_for_uid", lambda uid: metas.get(str(uid).strip().upper(), {}))

    snapshot = {
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:20:00Z", "last_error": ""},
        "watchdog": {},
        "healer": {},
        "jobs": [
            {
                "job_id": "pis_active_80c5",
                "enabled": False,
                "binding": {"type": "ledger_uid", "id": "PIS-ACTIVE-80C5"},
                "display_title": "80c5 Define Canonical Verifier Verdict",
                "depends_on": ["pis_done_5c96"],
                "state": {"active_run_id": "sj_live_001", "active_started_utc": "2026-03-27T09:10:00Z", "last_status": "running"},
                "due": {"reason": "disabled"},
                "policy": {"max_runtime_seconds": 1800},
                "scheduler_projection_v1": {"projected_status": "active", "projection_reason": "active_run_present", "durable_conflict": False},
            },
            {
                "job_id": "pis_next_93a9",
                "enabled": False,
                "binding": {"type": "ledger_uid", "id": "PIS-NEXT-93A9"},
                "display_title": "93a9 Wire Builder and Verifier Prompt Contracts",
                "depends_on": ["pis_active_80c5"],
                "state": {"last_status": ""},
                "due": {"reason": "disabled"},
                "scheduler_projection_v1": {"projected_status": "idle", "projection_reason": "disabled", "durable_conflict": False},
            },
            {
                "job_id": "pis_other_e9de",
                "enabled": False,
                "binding": {"type": "ledger_uid", "id": "PIS-OTHER-E9DE"},
                "display_title": "e9de Validate Other Family",
                "depends_on": [],
                "state": {"last_status": ""},
                "due": {"reason": "disabled"},
                "scheduler_projection_v1": {"projected_status": "idle", "projection_reason": "disabled", "durable_conflict": False},
            },
            {
                "job_id": "pis_done_5c96",
                "enabled": False,
                "binding": {"type": "ledger_uid", "id": "PIS-DONE-5C96"},
                "display_title": "5c96 Done",
                "depends_on": [],
                "state": {"last_status": "ok"},
                "due": {"reason": "disabled"},
                "scheduler_projection_v1": {"projected_status": "completed_once", "projection_reason": "terminal_success", "durable_conflict": False},
            },
        ],
    }

    rendered = watch._render("http://localhost:8787", snapshot, tz_name="UTC", color=False, verbose=False, pipeline_limit=5, include_done=False)

    assert "93a9 Wire Builder and Verifier Prompt Contracts" in rendered
    assert "e9de Validate Other Family" not in rendered


def test_render_excludes_verified_other_family_and_verified_helper_residue(monkeypatch) -> None:
    metas = {
        "PIS-PARENT-D4C5": {
            "integration_meta": {
                "child_uids": ["PIS-DONE-5C96", "PIS-ACTIVE-80C5", "PIS-NEXT-93A9"],
                "implementation_child_uids": ["PIS-DONE-5C96", "PIS-ACTIVE-80C5", "PIS-NEXT-93A9"],
                "intent_sheet_uid": "IS-20260403-212914Z-9C4E",
            }
        },
        "PIS-DONE-5C96": {"status": "verified", "integration_meta": {"parent_uid": "PIS-PARENT-D4C5", "intent_sheet_uid": "IS-20260403-212914Z-9C4E", "relationship": "execution_child"}},
        "PIS-ACTIVE-80C5": {"integration_meta": {"parent_uid": "PIS-PARENT-D4C5", "intent_sheet_uid": "IS-20260403-212914Z-9C4E", "relationship": "execution_child"}},
        "PIS-NEXT-93A9": {"integration_meta": {"parent_uid": "PIS-PARENT-D4C5", "intent_sheet_uid": "IS-20260403-212914Z-9C4E", "relationship": "execution_child"}},
        "PIS-OTHER-E9DE": {"status": "verified", "integration_meta": {"parent_uid": "PIS-PARENT-41E3", "intent_sheet_uid": "IS-20260403-212914Z-41E3", "relationship": "intent_validation_suite"}},
    }
    monkeypatch.setattr(watch, "_ledger_meta_for_uid", lambda uid: metas.get(str(uid).strip().upper(), {}))

    snapshot = {
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:20:00Z", "last_error": ""},
        "watchdog": {},
        "healer": {},
        "jobs": [
            {
                "job_id": "pis_active_80c5",
                "enabled": False,
                "binding": {"type": "ledger_uid", "id": "PIS-ACTIVE-80C5"},
                "display_title": "80c5 Define Canonical Verifier Verdict",
                "depends_on": ["pis_done_5c96"],
                "state": {"active_run_id": "sj_live_001", "active_started_utc": "2026-03-27T09:10:00Z", "last_status": "running"},
                "due": {"reason": "disabled"},
                "policy": {"max_runtime_seconds": 1800},
                "scheduler_projection_v1": {"projected_status": "active", "projection_reason": "active_run_present", "durable_conflict": False},
            },
            {
                "job_id": "pis_next_93a9",
                "enabled": False,
                "binding": {"type": "ledger_uid", "id": "PIS-NEXT-93A9"},
                "display_title": "93a9 Wire Builder and Verifier Prompt Contracts",
                "depends_on": ["pis_active_80c5"],
                "state": {"last_status": ""},
                "due": {"reason": "disabled"},
                "scheduler_projection_v1": {"projected_status": "idle", "projection_reason": "disabled", "durable_conflict": False},
            },
            {
                "job_id": "verify_5c96_status_reconcile",
                "enabled": False,
                "binding": {"type": "ledger_uid", "id": "PIS-DONE-5C96"},
                "display_title": "Verify 5c96 ledger status reconciliation",
                "depends_on": [],
                "state": {"last_status": "error"},
                "due": {"reason": "disabled"},
                "scheduler_projection_v1": {"projected_status": "failed", "projection_reason": "error", "durable_conflict": False},
            },
            {
                "job_id": "pis_other_e9de",
                "enabled": False,
                "binding": {"type": "ledger_uid", "id": "PIS-OTHER-E9DE"},
                "display_title": "e9de Validate Other Family",
                "depends_on": [],
                "state": {"last_status": ""},
                "due": {"reason": "disabled"},
                "scheduler_projection_v1": {"projected_status": "idle", "projection_reason": "disabled", "durable_conflict": False},
            },
        ],
    }

    rendered = watch._render("http://localhost:8787", snapshot, tz_name="UTC", color=False, verbose=False, pipeline_limit=5, include_done=False)

    assert "93a9 Wire Builder and Verifier Prompt Contracts" in rendered
    assert "e9de Validate Other Family" not in rendered
    assert "Verify 5c96 ledger status reconciliation" not in rendered


def test_render_shows_scheduler_v2_surface_bundle_summary(monkeypatch) -> None:
    fixed_now = dt.datetime(2026, 3, 27, 9, 20, 0, tzinfo=dt.timezone.utc)

    class _FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(watch.dt, "datetime", _FixedDateTime)
    monkeypatch.setattr(watch, "_fresh_watchdog_status", lambda status: status)
    monkeypatch.setattr(watch, "_fresh_healer_status", lambda status: status)
    payload = {
        "jobs": [],
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
        "watchdog": {},
        "healer": {},
        "scheduler_v2": {
            "status": "ready_without_family",
            "projection_equivalent": True,
            "ownership": {"mode": "v2_shadow", "canonical_owner": "legacy_scheduler"},
            "process": {"public_ports": []},
        },
    }

    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False)
    assert "scheduler_v2: status=ready_without_family mode=v2_shadow owner=legacy_scheduler projection_equivalent=True ports=0" in rendered


def test_render_shows_scheduler_v2_rich_projection_details(monkeypatch) -> None:
    fixed_now = dt.datetime(2026, 3, 27, 9, 20, 0, tzinfo=dt.timezone.utc)

    class _FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(watch.dt, "datetime", _FixedDateTime)
    monkeypatch.setattr(watch, "_fresh_watchdog_status", lambda status: status)
    monkeypatch.setattr(watch, "_fresh_healer_status", lambda status: status)
    payload = {
        "jobs": [],
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
        "watchdog": {},
        "healer": {},
        "scheduler_v2": {
            "status": "ready",
            "projection_equivalent": True,
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "process": {"public_ports": []},
            "shadow_payload": {
                "evaluation": {
                    "admitted": [
                        {
                            "child_uid": "PIS-20260410-181002Z-AA01",
                            "job_id": "pis_aa01",
                            "decision": "admit",
                            "reason": "dependencies_satisfied",
                            "gate": "continue",
                            "unmet_dependencies": [],
                        },
                        {
                            "child_uid": "PIS-20260410-181005Z-IN04",
                            "job_id": "pis_in04",
                            "decision": "blocked",
                            "reason": "dependency_pending:PIS-20260410-181002Z-AA01",
                            "gate": "continue",
                            "unmet_dependencies": ["PIS-20260410-181002Z-AA01"],
                        },
                    ],
                    "projection": {
                        "family_parent_uid": "PIS-20260410-181001Z-PA01",
                        "family_status": "queued",
                        "current_stage": "implement",
                        "family_progress": {
                            "completed_children": 0,
                            "total_children": 6,
                            "active_children": 0,
                            "blocked_children": 1,
                            "failed_children": 0,
                        },
                        "children": [
                            {
                                "child_uid": "PIS-20260410-181002Z-AA01",
                                "title": "Explore A",
                                "fanout_role": "parallel_exploration",
                            },
                            {
                                "child_uid": "PIS-20260410-181005Z-IN04",
                                "title": "Integrate fanout",
                                "fanout_role": "fan_in_integration",
                            },
                        ],
                    },
                }
            },
        },
    }

    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False, pipeline_limit=5)
    assert "v2_family: parent=PIS-20260410-181001Z-PA01 status=queued stage=implement progress=0/6 active=0 blocked=1 failed=0" in rendered
    assert "v2_roles: fan_in_integration=IN04 parallel_exploration=AA01" in rendered
    assert "V2 NEXT (showing 2)" in rendered
    assert "01. [admit] AA01 Explore A gate=continue role=parallel_exploration reason=dependencies_satisfied" in rendered


def test_render_does_not_smooth_stale_jobs_unavailable_into_v2_family_activity(monkeypatch) -> None:
    fixed_now = dt.datetime(2026, 3, 27, 9, 20, 0, tzinfo=dt.timezone.utc)

    class _FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(watch.dt, "datetime", _FixedDateTime)
    monkeypatch.setattr(watch, "_fresh_watchdog_status", lambda status: status)
    monkeypatch.setattr(watch, "_fresh_healer_status", lambda status: status)
    payload = {
        "jobs": [],
        "jobs_unavailable": True,
        "snapshot_stale": True,
        "summary": "Jobs view is temporarily unavailable; scheduler service status is still reported from the plane.",
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
        "watchdog": {},
        "healer": {},
        "scheduler_v2": {
            "status": "ready",
            "projection_equivalent": True,
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "process": {"public_ports": []},
            "shadow_payload": {
                "evaluation": {
                    "projection": {
                        "family_parent_uid": "PIS-20260410-181001Z-PA01",
                        "family_status": "active",
                        "current_stage": "implement",
                        "family_progress": {
                            "completed_children": 2,
                            "total_children": 6,
                            "active_children": 1,
                            "blocked_children": 1,
                            "failed_children": 0,
                        },
                    },
                    "admitted": [
                        {
                            "child_uid": "PIS-20260410-181002Z-AA01",
                            "job_id": "pis_active_1",
                            "decision": "active",
                            "reason": "active_run_present",
                            "gate": "continue",
                            "unmet_dependencies": [],
                        },
                        {
                            "child_uid": "PIS-20260410-181005Z-IN04",
                            "job_id": "pis_next_2",
                            "decision": "blocked",
                            "reason": "dependency_pending:PIS-20260410-181002Z-AA01",
                            "gate": "continue",
                            "unmet_dependencies": ["PIS-20260410-181002Z-AA01"],
                        },
                    ],
                }
            },
        },
    }

    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False)
    assert "observation: status=degraded reasons=jobs_unavailable,snapshot_stale" in rendered
    assert "Jobs view is temporarily unavailable; scheduler service status is still reported from the plane." in rendered
    assert "v2_family:" not in rendered
    assert "v2_active:" not in rendered
    assert "V2 NEXT" not in rendered
    assert "family_progress: v2 family progress" not in rendered
    assert "ACTIVE\n  (none)" in rendered


def test_render_respects_bundle_level_observation_advisory_even_when_top_level_observation_looks_fresh(monkeypatch) -> None:
    fixed_now = dt.datetime(2026, 3, 27, 9, 20, 0, tzinfo=dt.timezone.utc)

    class _FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(watch.dt, "datetime", _FixedDateTime)
    monkeypatch.setattr(watch, "_fresh_watchdog_status", lambda status: status)
    monkeypatch.setattr(watch, "_fresh_healer_status", lambda status: status)
    payload = {
        "jobs": [],
        "observation_integrity": {"status": "fresh", "advisory_only": False, "reasons": [], "summary": "", "last_error": ""},
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
        "watchdog": {},
        "healer": {},
        "scheduler_v2": {
            "status": "ready",
            "projection_equivalent": True,
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "process": {"public_ports": []},
            "observation_advisory": {
                "status": "degraded",
                "advisory_only": True,
                "reasons": ["cross_time_skew", "jobs_unavailable"],
                "summary": "scheduler_v2 bundle is fresher than the operator snapshot",
                "last_error": "simulated_bundle_skew",
            },
            "shadow_payload": {
                "evaluation": {
                    "projection": {
                        "family_parent_uid": "PIS-20260410-181001Z-PA01",
                        "family_status": "active",
                        "current_stage": "implement",
                    },
                    "admitted": [
                        {
                            "child_uid": "PIS-20260410-181002Z-AA01",
                            "job_id": "pis_active_1",
                            "decision": "active",
                            "reason": "active_run_present",
                            "gate": "continue",
                            "unmet_dependencies": [],
                        }
                    ],
                }
            },
        },
    }

    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False)
    assert "observation: status=degraded reasons=cross_time_skew,jobs_unavailable" in rendered
    assert "v2_family:" not in rendered
    assert "v2_active:" not in rendered


def test_render_shows_watchdog_intervention_lines(monkeypatch) -> None:
    fixed_now = dt.datetime(2026, 3, 27, 9, 20, 0, tzinfo=dt.timezone.utc)
    real_datetime = dt.datetime

    class _FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

        @classmethod
        def fromtimestamp(cls, ts, tz=None):
            return real_datetime.fromtimestamp(ts, tz=tz)

    monkeypatch.setattr(watch.dt, "datetime", _FixedDateTime)
    monkeypatch.setattr(watch, "_fresh_watchdog_status", lambda status: status)
    monkeypatch.setattr(watch, "_fresh_healer_status", lambda status: status)
    payload = {
        "jobs": [],
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
        "watchdog": {
            "running": True,
            "interval_s": 900,
            "last_receipt_utc": "2026-03-27T09:19:00Z",
            "last_error": "",
            "last_receipt": {
                "generated_utc": "2026-03-27T09:19:00Z",
                "health": {"health": "degraded", "reason": "dependency_blocked_chain"},
                "findings": [{"kind": "stalled_progress_chain_unrecovered"}],
                "actions": [
                    {"action": "resume_stalled_dependency_head", "job_id": "head_1", "ok": True, "idle_minutes": 8},
                    {"action": "scheduler_tick_for_progress_recovery", "job_id": "head_1", "tick": {"executed_count": 1}},
                ],
            },
        },
        "healer": {"running": True, "interval_s": 300, "last_receipt_utc": "2026-03-27T09:18:00Z", "last_error": ""},
    }

    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False)
    assert "summary: last_run=2026-03-27 09:19:00 UTC health=degraded reason=dependency_blocked_chain" in rendered
    assert "findings: stalled_progress_chain_unrecovered" in rendered
    assert "actions: resume_stalled_dependency_head, scheduler_tick_for_progress_recovery" in rendered
    assert "intervention: resumed head_1 ok=true idle_minutes=8" in rendered
    assert "intervention: scheduler tick for head_1 executed_count=1" in rendered


def test_render_default_pipeline_is_compact_single_line() -> None:
    payload = {
        "jobs": [
            {
                "job_id": "job_active_001",
                "job_kind": "build_verify_loop",
                "enabled": True,
                "binding": {"type": "ledger_uid", "id": "PIS-20260327-124630Z-RWVP"},
                "policy": {"max_runtime_seconds": 1800},
                "loop": {"max_iterations": 4},
                "state": {
                    "active_run_id": "sj_live_001",
                    "active_started_utc": "2026-03-27T09:00:00Z",
                    "active_loop_iteration": 2,
                    "active_loop_stage": "builder",
                    "active_loop_completed_iterations": 1,
                    "last_status": "running",
                },
                "due": {"reason": "active_run"},
            }
        ],
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
        "watchdog": {"running": True, "interval_s": 900, "last_receipt_utc": "2026-03-27T09:19:00Z", "last_error": ""},
        "healer": {"running": True, "interval_s": 300, "last_receipt_utc": "2026-03-27T09:18:00Z", "last_error": ""},
    }

    watch._fresh_watchdog_status = lambda status: status
    watch._fresh_healer_status = lambda status: status
    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False)
    assert "ACTIVE" in rendered
    assert "PIPELINE (execution order" not in rendered
    assert "NEXT IN LINE" not in rendered
    assert "01. [active]" in rendered
    assert "active_run_present" in rendered


def test_render_surfaces_scheduler_active_count_disagreement() -> None:
    payload = {
        "jobs": [
            {
                "job_id": "job_active_001",
                "job_kind": "build_verify_loop",
                "enabled": True,
                "display_title": "Active job",
                "policy": {"max_runtime_seconds": 1800},
                "loop": {"max_iterations": 1},
                "state": {
                    "active_run_id": "sj_live_001",
                    "active_started_utc": "2026-03-27T09:00:00Z",
                    "active_loop_iteration": 1,
                    "active_loop_stage": "builder",
                    "active_loop_completed_iterations": 0,
                    "last_status": "running",
                },
                "due": {"reason": "dependency_pending:job_dep"},
                "lifecycle_projection": {
                    "job_shape": "verification_loop_job",
                    "raw_state": "running",
                    "canonical_state": "running_iteration",
                    "summary_bucket": "active_progress",
                    "terminal_summary_class": "",
                    "recovery_class": "rerun_from_last_iteration_boundary",
                    "interruption_state": "interrupted",
                    "recognized": True,
                    "active_run": True,
                },
            }
        ],
        "scheduler": {
            "running": True,
            "interval_s": 30,
            "last_tick_utc": "2026-03-27T09:19:50Z",
            "last_error": "",
            "last_result": {"active_count": 0},
        },
        "watchdog": {"running": True, "interval_s": 900, "last_receipt_utc": "2026-03-27T09:19:00Z", "last_error": ""},
        "healer": {"running": True, "interval_s": 300, "last_receipt_utc": "2026-03-27T09:18:00Z", "last_error": ""},
    }

    watch._fresh_watchdog_status = lambda status: status
    watch._fresh_healer_status = lambda status: status
    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False)

    assert "scope: 1 jobs active=1 blocked=0 error=0" in rendered
    assert "watch_consistency: row truth active=1 but scheduler.last_result active_count=0" in rendered
    assert "01. [active]" in rendered


def test_render_uses_durable_active_truth_when_projection_is_ambiguous() -> None:
    payload = {
        "jobs": [
            {
                "job_id": "job_active_001",
                "job_kind": "build_verify_loop",
                "enabled": True,
                "display_title": "Active job",
                "policy": {"max_runtime_seconds": 1800},
                "state": {
                    "active_run_id": "sj_live_001",
                    "active_started_utc": "2026-03-27T09:00:00Z",
                    "last_status": "running",
                },
                "due": {"reason": "dependency_pending:job_dep"},
                "scheduler_projection_v1": {
                    "projected_status": "ambiguous",
                    "projection_reason": "running_status_without_active_run",
                    "durable_conflict": True,
                },
            }
        ],
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
        "watchdog": {"running": True, "interval_s": 900, "last_receipt_utc": "2026-03-27T09:19:00Z", "last_error": ""},
        "healer": {"running": True, "interval_s": 300, "last_receipt_utc": "2026-03-27T09:18:00Z", "last_error": ""},
    }

    watch._fresh_watchdog_status = lambda status: status
    watch._fresh_healer_status = lambda status: status
    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False)

    assert "ACTIVE\n  01. [active]" in rendered
    assert "active_run_present" in rendered


def test_pick_active_prefers_primary_family_job_over_reconcile_sidecar() -> None:
    rows = [
        {
            "job_id": "verify_child_001_status_reconcile",
            "enabled": True,
            "state": {"active_run_id": "sj_sidecar", "active_started_utc": "2026-03-27T09:00:00Z", "last_status": "running"},
            "due": {"reason": "active_run"},
            "prompt_meta": {"generated_by_closeout_truth_reconcile": True},
        },
        {
            "job_id": "pis_primary_001",
            "enabled": True,
            "state": {"active_run_id": "sj_primary", "active_started_utc": "2026-03-27T09:01:00Z", "last_status": "running"},
            "due": {"reason": "active_run"},
        },
    ]

    picked = watch._pick_active(rows)

    assert picked is not None
    assert picked["job_id"] == "pis_primary_001"


def test_fresh_healer_status_does_not_revive_stale_report_when_service_is_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(watch, "REPO_ROOT", tmp_path)
    latest_report = (
        tmp_path
        / "governance"
        / "agents"
        / "runtime"
        / "scheduled_jobs"
        / "pipeline_healer"
        / "latest_report.json"
    )
    latest_report.parent.mkdir(parents=True, exist_ok=True)
    latest_report.write_text(
        json.dumps(
            {
                "generated_utc": "2026-04-09T06:31:31Z",
                "schema_version": "scheduler_pipeline_healer_receipt_v1",
            }
        ),
        encoding="utf-8",
    )

    status = {
        "running": False,
        "interval_s": 300,
        "started_utc": "",
        "last_receipt_utc": "",
        "last_receipt": {},
        "last_error": "",
    }

    refreshed = watch._fresh_healer_status(status)

    assert refreshed == status


def test_pipeline_rows_demote_status_reconcile_helpers_after_primary_work() -> None:
    rows = [
        {
            "job_id": "pis_close_001",
            "enabled": True,
            "display_title": "Close family",
            "depends_on": [],
            "schedule": {"mode": "once", "run_at": "2026-03-27T09:10:00Z"},
            "state": {"last_status": ""},
            "due": {"eligible": True, "reason": "due", "due_utc": "2026-03-27T09:10:00Z"},
        },
        {
            "job_id": "verify_child_001_status_reconcile",
            "enabled": True,
            "display_title": "Verify child status reconciliation",
            "depends_on": [],
            "schedule": {"mode": "once", "run_at": "2026-03-27T09:00:00Z"},
            "state": {"last_status": ""},
            "due": {"eligible": True, "reason": "due", "due_utc": "2026-03-27T09:00:00Z"},
        },
        {
            "job_id": "pis_blocked_001",
            "enabled": True,
            "display_title": "Blocked primary",
            "depends_on": [],
            "schedule": {"mode": "once", "run_at": "2026-03-27T08:55:00Z"},
            "state": {"last_status": "deferred"},
            "due": {"eligible": False, "reason": "global_concurrency_cap_reached", "due_utc": "2026-03-27T08:55:00Z"},
        },
    ]

    ordered = watch._pipeline_rows(rows, include_done=False, limit=10)

    assert [item["row"]["job_id"] for item in ordered] == [
        "pis_close_001",
        "pis_blocked_001",
        "verify_child_001_status_reconcile",
    ]


def test_pick_previous_excludes_currently_active_row() -> None:
    rows = [
        {
            "job_id": "job_prev_001",
            "state": {
                "active_run_id": "",
                "last_finished_utc": "2026-03-27T09:10:00Z",
                "last_status": "ok",
            },
            "due": {"reason": "already_executed"},
        },
        {
            "job_id": "job_active_001",
            "state": {
                "active_run_id": "sj_live_001",
                "active_started_utc": "2026-03-27T09:15:00Z",
                "last_finished_utc": "2026-03-27T09:20:00Z",
                "last_status": "running",
            },
            "due": {"reason": "active_run"},
            "lifecycle_projection": {
                "job_shape": "verification_loop_job",
                "raw_state": "running",
                "canonical_state": "running_iteration",
                "summary_bucket": "active_progress",
                "terminal_summary_class": "",
                "recovery_class": "rerun_from_last_iteration_boundary",
                "interruption_state": "interrupted",
                "recognized": True,
                "active_run": True,
            },
        },
    ]

    picked = watch._pick_previous(rows)
    assert picked is not None
    assert picked["job_id"] == "job_prev_001"


def test_render_nonverbose_hides_primary_chain_and_shows_next_in_line(monkeypatch) -> None:
    payload = {
        "jobs": [
            {
                "job_id": "pis_bbf4_20260502_define-the-quebec-search-corrido_p2of3",
                "enabled": True,
                "display_title": "pass 2/3 Define the Quebec search corridor",
                "state": {"active_run_id": "sj_live_001", "active_started_utc": "2026-03-27T09:00:00Z", "last_status": "running"},
                "due": {"reason": "active_run"},
            },
            {
                "job_id": "pis_bbf4_20260502_define-the-quebec-search-corrido_p3of3",
                "enabled": True,
                "display_title": "pass 3/3 Define the Quebec search corridor",
                "state": {"last_status": ""},
                "due": {"reason": "dependency_pending:pis_bbf4_20260502_define-the-quebec-search-corrido_p2of3"},
            },
        ],
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
        "watchdog": {"running": False, "interval_s": 900, "last_receipt_utc": "", "last_error": ""},
        "healer": {"running": False, "interval_s": 300, "last_receipt_utc": "", "last_error": ""},
    }

    monkeypatch.setattr(watch, "_fresh_watchdog_status", lambda status: status)
    monkeypatch.setattr(watch, "_fresh_healer_status", lambda status: status)
    monkeypatch.setattr(
        watch,
        "_family_chain_items",
        lambda scope_row, rows: [
            {
                "job_id": "pis_bbf4_20260502_define-the-quebec-search-corrido_p1of3",
                "display_title": "pass 1/3 Define the Quebec search corridor",
                "job_kind": "build_verify_loop",
                "loop": {"max_iterations": 1},
                "state": {"last_status": "ok"},
                "binding": {"type": "ledger_uid", "id": "PIS-20260502-141914Z-BBF4"},
                "due": {},
            },
            rows[0],
            rows[1],
        ],
    )

    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False, verbose=False, pipeline_limit=10)

    assert "PRIMARY CHAIN" not in rendered
    assert "NEXT IN LINE" in rendered
    assert "pass 1/3 Define the Quebec search corridor" not in rendered
