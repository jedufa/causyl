from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from infra.webui import server_agents_swarm as swarm
from infra.webui import server_routes_and_main as routes


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str, body: dict | None = None) -> None:
        self.path = path
        self.headers = {}
        raw = json.dumps(body or {}).encode("utf-8")
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg))
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def _read_payload(req: _DummyReq) -> dict:
    return json.loads(req.wfile.getvalue().decode("utf-8"))


def test_start_swarm_sync_persists_finished_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    worktrees_root = tmp_path / "worktrees"
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(swarm, "_worktrees_root", lambda: worktrees_root)
    monkeypatch.setattr(swarm, "_current_git_ref", lambda: "deadbeef")
    monkeypatch.setattr(
        swarm,
        "_capture_repo_overlay",
        lambda: {"tracked_patch": "", "tracked_dirty": False, "untracked_files": [], "untracked_count": 0},
    )

    def _fake_provision(*, swarm_id: str, worker_id: str, base_ref: str, overlay: dict) -> Path:
        path = worktrees_root / swarm_id / worker_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(swarm, "_provision_worktree", _fake_provision)

    def _fake_codex(**kwargs):
        assert kwargs["role"] == "swarm_lead"
        return {
            "ok": True,
            "exit_code": 0,
            "content": json.dumps(
                {
                    "summary": "Split into two slices.",
                    "good_enough_bar": "Core path only.",
                    "cut_rules": ["Cut polish first."],
                    "maker_slices": [
                        {
                            "id": "slice-a",
                            "title": "API",
                            "objective": "Do API part",
                            "scope": "API only",
                            "expected_output": "Patch",
                            "lease_s": 60,
                        },
                        {
                            "id": "slice-b",
                            "title": "UI",
                            "objective": "Do UI part",
                            "scope": "UI only",
                            "expected_output": "Patch",
                            "lease_s": 60,
                        },
                    ],
                }
            ),
            "stderr": "",
            "stdout": "",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr(swarm, "run_codex_oneshot", _fake_codex)
    monkeypatch.setattr(swarm, "_load_prompt_text", lambda _name: "prompt")

    def _fake_worker_turn(**kwargs):
        artifacts_dir = Path(kwargs["artifacts_dir"])
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        patch_path = artifacts_dir / f"{kwargs['kind']}-{kwargs['slice_spec']['id']}.patch"
        patch_path.write_text("diff --git a/x b/x\n", encoding="utf-8")
        return {
            "worker_id": str(kwargs["slice_spec"]["id"]),
            "kind": str(kwargs["kind"]),
            "slice_id": str(kwargs["slice_spec"]["id"]),
            "title": str(kwargs["slice_spec"].get("title") or ""),
            "status": "delivered",
            "lease_s": int(kwargs["lease_s"]),
            "worktree_path": str(kwargs["worktree"]),
            "started_utc": "2026-03-12T00:00:00Z",
            "updated_utc": "2026-03-12T00:00:00Z",
            "finished_utc": "2026-03-12T00:00:01Z",
            "summary": "done",
            "known_gaps": [],
            "changed_files": [f"{kwargs['slice_spec']['id']}.py"],
            "local_checks": [],
            "patch_path": str(patch_path),
            "patch_has_changes": True,
            "patch_size_bytes": int(patch_path.stat().st_size),
            "raw_output_path": str(artifacts_dir / "raw.txt"),
            "summary_path": str(artifacts_dir / "summary.json"),
            "error": "",
            "token_usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
        }

    monkeypatch.setattr(swarm, "_run_worker_turn", _fake_worker_turn)
    monkeypatch.setattr(
        swarm,
        "_run_integration",
        lambda **kwargs: {
            "worktree_path": str(worktrees_root / kwargs["swarm_id"] / "integration"),
            "applied": [{"worker_id": "slice-a"}, {"worker_id": "slice-b"}],
            "failed": [],
            "combined_patch_path": str(runtime_root / "combined.patch"),
            "combined_patch_has_changes": True,
            "changed_files": ["slice-a.py", "slice-b.py"],
        },
    )

    result = swarm.start_swarm(
        binding={"type": "task_id", "id": "mb3"},
        objective="Build the swarm runtime",
        options={"max_makers": 2, "max_finishers": 1},
        run_async=False,
    )

    assert result["ok"] is True
    state = result["state"]
    assert state["status"] == "finished"
    assert state["phase"] == "completed"
    assert len(state["workers"]) == 2
    assert state["token_usage"]["total_tokens"] == 75
    summary_path = Path(str(state["artifacts"]["summary_path"]))
    assert summary_path.exists()
    assert "token_usage.total_tokens: 75" in summary_path.read_text(encoding="utf-8")
    status_payload = swarm.get_status(swarm_id=str(result["swarm_id"]))
    assert status_payload["state"]["status"] == "finished"


def test_normalize_maker_slices_assigns_unique_slice_artifacts_for_shared_report() -> None:
    rows = swarm._normalize_maker_slices_for_output_paths(
        objective=(
            "Audit the repo and produce a consolidated report at "
            "docs/operator/codebase_audit_swarm_20260401.md."
        ),
        maker_slices=[
            {"id": "S1", "title": "Runtime Audit", "expected_output": "Slice findings"},
            {"id": "S2", "title": "Mobile Audit", "expected_output": "Slice findings"},
            {"id": "S4", "title": "Audit Report Consolidation", "expected_output": "Final report"},
        ],
    )

    assert rows[0]["artifact_path"] == "docs/operator/codebase_audit_swarm_20260401__S1.md"
    assert rows[1]["artifact_path"] == "docs/operator/codebase_audit_swarm_20260401__S2.md"
    assert rows[2]["artifact_path"] == "docs/operator/codebase_audit_swarm_20260401.md"
    assert rows[2]["shared_output_owner"] is True
    assert rows[0]["shared_output_owner"] is False


def test_compose_maker_message_warns_nonowners_off_shared_report() -> None:
    message = swarm._compose_maker_message(
        objective="Audit the repo",
        slice_spec={
            "id": "S1",
            "title": "Runtime Audit",
            "artifact_path": "docs/operator/codebase_audit_swarm_20260401__S1.md",
            "shared_output_owner": False,
        },
        state={"deadline_utc": "2026-04-01T21:00:00Z"},
    )
    assert "Do not write the shared final report path directly" in message
    assert "docs/operator/codebase_audit_swarm_20260401__S1.md" in message


def test_compose_lead_message_uses_configured_maker_lease_default() -> None:
    message = swarm._compose_lead_message(
        objective="Audit deeply.",
        binding={"type": "task_id", "id": "mpl"},
        state={"config": {"wall_clock_s": 7200, "max_makers": 4, "max_finishers": 2, "maker_lease_s": 1200, "max_total_tokens": 0}},
    )
    assert "Use maker_lease_s=1200 by default" in message


def test_get_status_marks_orphaned_running_swarm_interrupted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    swarm_id = "test-swarm"
    state_path = runtime_root / f"swarm__{swarm_id}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": swarm.SCHEMA_STATE,
                "swarm_id": swarm_id,
                "binding": {"type": "task_id", "id": "mb3"},
                "objective": "x",
                "status": "running",
                "phase": "makers",
                "created_utc": "2026-03-12T00:00:00Z",
                "updated_utc": "2026-03-12T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    payload = swarm.get_status(swarm_id=swarm_id)

    assert payload["state"]["status"] == "interrupted"
    assert payload["state"]["phase"] == "interrupted"
    assert payload["state"]["runner_state"] == "detached"
    assert payload["thread_alive"] is False
    assert "runner" in " ".join(payload["state"]["known_gaps"])


def test_refresh_swarm_runtime_state_keeps_inline_runner_attached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    swarm_id = "inline-swarm"
    state_path = runtime_root / f"swarm__{swarm_id}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": swarm.SCHEMA_STATE,
                "swarm_id": swarm_id,
                "binding": {"type": "task_id", "id": "mb3"},
                "objective": "x",
                "status": "running",
                "phase": "planning",
                "created_utc": "2026-03-12T00:00:00Z",
                "updated_utc": "2026-03-12T00:00:00Z",
                "runner_mode": "inline",
                "runner_pid": int(__import__('os').getpid()),
            }
        ),
        encoding="utf-8",
    )

    payload = swarm.get_status(swarm_id=swarm_id)

    assert payload["state"]["status"] == "running"
    assert payload["state"]["phase"] == "planning"
    assert payload["state"]["runner_state"] == "attached"


def test_get_status_marks_timed_out_swarm_when_deadline_expired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    swarm_id = "test-timeout"
    state_path = runtime_root / f"swarm__{swarm_id}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": swarm.SCHEMA_STATE,
                "swarm_id": swarm_id,
                "binding": {"type": "task_id", "id": "mb3"},
                "objective": "x",
                "status": "running",
                "phase": "makers",
                "created_utc": "2026-03-12T00:00:00Z",
                "updated_utc": "2026-03-12T00:00:00Z",
                "deadline_utc": "2026-03-12T00:00:01Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(swarm, "_utc_now", lambda: swarm._parse_utc("2026-03-12T00:00:10Z"))  # type: ignore[arg-type]

    payload = swarm.get_status(swarm_id=swarm_id)

    assert payload["state"]["status"] == "timed_out"
    assert payload["state"]["phase"] == "timed_out"
    assert "wall-clock deadline" in " ".join(payload["state"]["known_gaps"])


def test_start_swarm_reads_live_policy_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake_policy(key: str, default: int, **_kwargs: object) -> int:
        calls.append(key)
        mapping = {
            "agents.swarm.wall_clock_s": 901,
            "agents.swarm.maker_lease_s": 121,
            "agents.swarm.finisher_lease_s": 77,
            "agents.swarm.max_makers": 3,
            "agents.swarm.max_finishers": 1,
            "agents.swarm.max_total_tokens": 43210,
        }
        return int(mapping[key])

    monkeypatch.setattr(swarm._runtime_policy, "get_runtime_policy_int", _fake_policy)
    monkeypatch.setattr(swarm, "_run_swarm_thread", lambda _swarm_id: None)

    result = swarm.start_swarm(
        binding={"type": "task_id", "id": "mb3"},
        objective="Use live defaults",
        run_async=False,
    )

    config = result["state"]["config"]
    assert config["wall_clock_s"] == 901
    assert config["maker_lease_s"] == 121
    assert config["finisher_lease_s"] == 77
    assert config["max_makers"] == 3
    assert config["max_finishers"] == 1
    assert config["max_total_tokens"] == 43210
    assert calls.count("agents.swarm.wall_clock_s") >= 1


def test_start_swarm_sync_fails_when_total_tokens_exceed_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    worktrees_root = tmp_path / "worktrees"
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(swarm, "_worktrees_root", lambda: worktrees_root)
    monkeypatch.setattr(swarm, "_current_git_ref", lambda: "deadbeef")
    monkeypatch.setattr(
        swarm,
        "_capture_repo_overlay",
        lambda: {"tracked_patch": "", "tracked_dirty": False, "untracked_files": [], "untracked_count": 0},
    )

    def _fake_provision(*, swarm_id: str, worker_id: str, base_ref: str, overlay: dict) -> Path:
        path = worktrees_root / swarm_id / worker_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(swarm, "_provision_worktree", _fake_provision)
    monkeypatch.setattr(swarm, "_load_prompt_text", lambda _name: "prompt")
    monkeypatch.setattr(
        swarm,
        "run_codex_oneshot",
        lambda **_kwargs: {
            "ok": True,
            "exit_code": 0,
            "content": json.dumps(
                {
                    "summary": "One slice.",
                    "good_enough_bar": "Stay small.",
                    "cut_rules": [],
                    "maker_slices": [
                        {
                            "id": "slice-a",
                            "title": "API",
                            "objective": "Do API part",
                            "scope": "API only",
                            "expected_output": "Patch",
                            "lease_s": 60,
                        }
                    ],
                }
            ),
            "stderr": "",
            "stdout": "",
            "usage": {"input_tokens": 80, "output_tokens": 40, "total_tokens": 120},
        },
    )

    def _fake_worker_turn(**kwargs):
        artifacts_dir = Path(kwargs["artifacts_dir"])
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        patch_path = artifacts_dir / "slice-a.patch"
        patch_path.write_text("diff --git a/x b/x\n", encoding="utf-8")
        return {
            "worker_id": "slice-a",
            "kind": str(kwargs["kind"]),
            "slice_id": "slice-a",
            "title": "API",
            "status": "delivered",
            "lease_s": int(kwargs["lease_s"]),
            "worktree_path": str(kwargs["worktree"]),
            "started_utc": "2026-03-12T00:00:00Z",
            "updated_utc": "2026-03-12T00:00:00Z",
            "finished_utc": "2026-03-12T00:00:01Z",
            "summary": "done",
            "known_gaps": [],
            "changed_files": ["slice-a.py"],
            "local_checks": [],
            "patch_path": str(patch_path),
            "patch_has_changes": True,
            "patch_size_bytes": int(patch_path.stat().st_size),
            "raw_output_path": str(artifacts_dir / "raw.txt"),
            "summary_path": str(artifacts_dir / "summary.json"),
            "error": "",
            "token_usage": {"input_tokens": 120, "output_tokens": 70, "total_tokens": 190},
        }

    monkeypatch.setattr(swarm, "_run_worker_turn", _fake_worker_turn)
    monkeypatch.setattr(
        swarm,
        "_run_integration",
        lambda **kwargs: {
            "worktree_path": str(worktrees_root / kwargs["swarm_id"] / "integration"),
            "applied": [{"worker_id": "slice-a"}],
            "failed": [],
            "combined_patch_path": str(runtime_root / "combined.patch"),
            "combined_patch_has_changes": True,
            "changed_files": ["slice-a.py"],
        },
    )

    result = swarm.start_swarm(
        binding={"type": "task_id", "id": "mb3"},
        objective="Cap the swarm tokens",
        options={"max_makers": 1, "max_finishers": 1, "max_total_tokens": 200},
        run_async=False,
    )

    assert result["ok"] is True
    state = result["state"]
    assert state["status"] == "failed"
    assert state["token_usage"]["total_tokens"] == 310
    assert any("max_total_tokens=200" in str(item) for item in list(state.get("known_gaps") or []))


def test_start_swarm_falls_back_to_sequential_makers_when_threadpool_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    worktrees_root = tmp_path / "worktrees"
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(swarm, "_worktrees_root", lambda: worktrees_root)
    monkeypatch.setattr(swarm, "_current_git_ref", lambda: "deadbeef")
    monkeypatch.setattr(
        swarm,
        "_capture_repo_overlay",
        lambda: {"tracked_patch": "", "tracked_dirty": False, "untracked_files": [], "untracked_count": 0},
    )
    monkeypatch.setattr(swarm, "_load_prompt_text", lambda _name: "prompt")

    def _fake_provision(*, swarm_id: str, worker_id: str, base_ref: str, overlay: dict) -> Path:
        path = worktrees_root / swarm_id / worker_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(swarm, "_provision_worktree", _fake_provision)
    monkeypatch.setattr(
        swarm,
        "run_codex_oneshot",
        lambda **_kwargs: {
            "ok": True,
            "exit_code": 0,
            "content": json.dumps(
                {
                    "summary": "Two slices.",
                    "good_enough_bar": "Core path only.",
                    "cut_rules": [],
                    "maker_slices": [
                        {"id": "slice-a", "title": "A", "objective": "A", "scope": "A", "expected_output": "Patch", "lease_s": 60},
                        {"id": "slice-b", "title": "B", "objective": "B", "scope": "B", "expected_output": "Patch", "lease_s": 60},
                    ],
                }
            ),
            "stderr": "",
            "stdout": "",
            "usage": {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10},
        },
    )

    def _fake_worker_turn(**kwargs):
        artifacts_dir = Path(kwargs["artifacts_dir"])
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        patch_path = artifacts_dir / f"{kwargs['slice_spec']['id']}.patch"
        patch_path.write_text("diff --git a/x b/x\n", encoding="utf-8")
        return {
            "worker_id": str(kwargs["slice_spec"]["id"]),
            "kind": str(kwargs["kind"]),
            "slice_id": str(kwargs["slice_spec"]["id"]),
            "title": str(kwargs["slice_spec"].get("title") or ""),
            "status": "delivered",
            "lease_s": int(kwargs["lease_s"]),
            "worktree_path": str(kwargs["worktree"]),
            "started_utc": "2026-03-12T00:00:00Z",
            "updated_utc": "2026-03-12T00:00:00Z",
            "finished_utc": "2026-03-12T00:00:01Z",
            "summary": "done",
            "known_gaps": [],
            "changed_files": [f"{kwargs['slice_spec']['id']}.py"],
            "local_checks": [],
            "patch_path": str(patch_path),
            "patch_has_changes": True,
            "patch_size_bytes": int(patch_path.stat().st_size),
            "raw_output_path": str(artifacts_dir / "raw.txt"),
            "summary_path": str(artifacts_dir / "summary.json"),
            "error": "",
            "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr(swarm, "_run_worker_turn", _fake_worker_turn)
    monkeypatch.setattr(
        swarm,
        "_run_integration",
        lambda **kwargs: {
            "worktree_path": str(worktrees_root / kwargs["swarm_id"] / "integration"),
            "applied": [{"worker_id": "slice-a"}, {"worker_id": "slice-b"}],
            "failed": [],
            "combined_patch_path": str(runtime_root / "combined.patch"),
            "combined_patch_has_changes": True,
            "changed_files": ["slice-a.py", "slice-b.py"],
        },
    )

    original_pool = swarm.ThreadPoolExecutor

    class _ExplodingPool:
        def __init__(self, *args, **kwargs):
            self._pool = original_pool(max_workers=1)

        def __enter__(self):
            return self

        def submit(self, *args, **kwargs):
            raise RuntimeError("cannot schedule new futures after interpreter shutdown")

        def __exit__(self, exc_type, exc, tb):
            self._pool.shutdown(wait=True)
            return False

    monkeypatch.setattr(swarm, "ThreadPoolExecutor", _ExplodingPool)

    result = swarm.start_swarm(
        binding={"type": "task_id", "id": "mb3"},
        objective="Sequential fallback",
        options={"max_makers": 2, "max_finishers": 1},
        run_async=False,
    )

    assert result["ok"] is True
    state = result["state"]
    assert state["status"] == "finished"
    events = swarm.get_status(swarm_id=str(result["swarm_id"]))["events"]
    assert any(str(row.get("event") or "") == "maker_parallelism_degraded" for row in events)


def test_start_swarm_generates_unique_ids_for_same_objective(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(swarm, "_run_swarm_thread", lambda _swarm_id: None)

    first = swarm.start_swarm(
        binding={"type": "task_id", "id": "mb3"},
        objective="same objective",
        run_async=False,
    )
    second = swarm.start_swarm(
        binding={"type": "task_id", "id": "mb3"},
        objective="same objective",
        run_async=False,
    )

    assert first["swarm_id"] != second["swarm_id"]


def test_capture_repo_overlay_filters_runtime_junk(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run_git(args: list[str], **_kwargs: object):
        if args == ["diff", "--binary", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:4] == ["ls-files", "--others", "--exclude-standard", "-z"]:
            return SimpleNamespace(
                returncode=0,
                stdout="\0".join(
                    [
                        "runtime/state/noise.txt",
                        "governance/agents/runtime/swarm/junk.json",
                        "tools/new_script.py",
                        "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/NewScreen.kt",
                        "",
                    ]
                ),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(swarm, "_run_git", _fake_run_git)

    overlay = swarm._capture_repo_overlay()

    assert overlay["untracked_files"] == [
        "tools/new_script.py",
        "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/NewScreen.kt",
    ]
    assert overlay["untracked_skipped_count"] == 2


def test_capture_repo_overlay_keeps_top_level_allowed_text_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run_git(args: list[str], **_kwargs: object):
        if args == ["diff", "--binary", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:4] == ["ls-files", "--others", "--exclude-standard", "-z"]:
            return SimpleNamespace(
                returncode=0,
                stdout="\0".join(
                    [
                        "BENCHMARK_SMOKE_NOTE.txt",
                        "runtime/state/noise.txt",
                        "",
                    ]
                ),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(swarm, "_run_git", _fake_run_git)

    overlay = swarm._capture_repo_overlay()

    assert overlay["untracked_files"] == ["BENCHMARK_SMOKE_NOTE.txt"]
    assert overlay["untracked_skipped_count"] == 1


def test_capture_repo_overlay_tolerates_untracked_scan_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run_git(args: list[str], **_kwargs: object):
        if args == ["diff", "--binary", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="diff --git a/x b/x\n", stderr="")
        if args[:4] == ["ls-files", "--others", "--exclude-standard", "-z"]:
            raise TimeoutError("git ls-files timed out")
        raise AssertionError(args)

    monkeypatch.setattr(swarm, "_run_git", _fake_run_git)

    overlay = swarm._capture_repo_overlay()

    assert overlay["tracked_dirty"] is True
    assert overlay["untracked_files"] == []
    assert "timed out" in overlay["untracked_scan_error"]


def test_run_swarm_thread_cleans_worktrees_after_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    cleanup_calls: list[str] = []
    monkeypatch.setattr(swarm, "_execute_swarm", lambda _swarm_id: None)
    monkeypatch.setattr(swarm, "_cleanup_swarm_worktrees", lambda _swarm_id: cleanup_calls.append(_swarm_id) or {})

    swarm._run_swarm_thread("sw-clean")

    assert cleanup_calls == ["sw-clean"]


def test_write_patch_artifact_includes_untracked_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    patch_path = tmp_path / "artifact.patch"

    def _fake_run_git(args: list[str], **_kwargs: object):
        if args == ["diff", "--binary", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:4] == ["ls-files", "--others", "--exclude-standard", "-z"]:
            return SimpleNamespace(returncode=0, stdout="tests/tools/new_case.py\0", stderr="")
        raise AssertionError(args)

    def _fake_subprocess_run(args: list[str], **_kwargs: object):
        assert args[:5] == ["git", "diff", "--binary", "--no-index", "--"]
        return SimpleNamespace(returncode=1, stdout="diff --git a/tests/tools/new_case.py b/tests/tools/new_case.py\n", stderr="")

    monkeypatch.setattr(swarm, "_run_git", _fake_run_git)
    monkeypatch.setattr(swarm.subprocess, "run", _fake_subprocess_run)

    result = swarm._write_patch_artifact(worktree=worktree, dst=patch_path)

    assert result["has_changes"] is True
    assert "new_case.py" in patch_path.read_text(encoding="utf-8")


def test_select_finisher_targets_skips_clean_delivered_worker() -> None:
    workers = [
        {
            "worker_id": "slice-1",
            "status": "delivered",
            "patch_has_changes": True,
            "known_gaps": [],
            "needs_finisher": False,
        }
    ]

    targets = swarm._select_finisher_targets(workers, max_finishers=1)

    assert targets == []


def test_resolve_finisher_result_preserves_maker_patch_when_finisher_has_no_patch() -> None:
    maker = {
        "worker_id": "slice-1",
        "slice_id": "slice-1",
        "title": "Slice 1",
        "status": "delivered",
        "known_gaps": [],
        "patch_path": "/tmp/maker.patch",
        "summary_path": "/tmp/maker.summary.json",
        "raw_output_path": "/tmp/maker.raw.txt",
    }
    finisher = {
        "worker_id": "slice-1",
        "status": "failed_with_evidence",
        "summary": "timed out",
        "error": "timed out",
        "known_gaps": ["timed out"],
        "local_checks": [],
        "patch_path": "/tmp/finisher.patch",
        "patch_has_changes": False,
        "patch_size_bytes": 0,
        "raw_output_path": "/tmp/finisher.raw.txt",
        "summary_path": "/tmp/finisher.summary.json",
        "finished_utc": "2026-03-12T00:00:00Z",
    }

    resolved = swarm._resolve_finisher_result(maker=maker, finisher=finisher)

    assert resolved["patch_path"] == "/tmp/maker.patch"
    assert resolved["status"] == "delivered"
    assert resolved["finisher_attempt"]["status"] == "failed_with_evidence"
    assert "timed out" in resolved["known_gaps"]


def test_resolve_finisher_result_does_not_adopt_non_delivered_finisher_patch() -> None:
    maker = {
        "worker_id": "slice-1",
        "slice_id": "slice-1",
        "title": "Slice 1",
        "status": "delivered",
        "known_gaps": [],
        "patch_path": "/tmp/maker.patch",
        "summary_path": "/tmp/maker.summary.json",
        "raw_output_path": "/tmp/maker.raw.txt",
    }
    finisher = {
        "worker_id": "slice-1",
        "status": "failed_with_evidence",
        "summary": "patch generated but checks failed",
        "error": "integration failed",
        "known_gaps": ["checks failed"],
        "local_checks": [],
        "patch_path": "/tmp/finisher.patch",
        "patch_has_changes": True,
        "patch_size_bytes": 123,
        "raw_output_path": "/tmp/finisher.raw.txt",
        "summary_path": "/tmp/finisher.summary.json",
        "finished_utc": "2026-03-12T00:00:00Z",
    }

    resolved = swarm._resolve_finisher_result(maker=maker, finisher=finisher)

    assert resolved["patch_path"] == "/tmp/maker.patch"
    assert resolved["status"] == "delivered"
    assert "kept the maker patch" in " ".join(resolved["known_gaps"]).lower()


def test_run_worker_turn_rejects_unsupported_delivered_prefix_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    worktree = tmp_path / "worktree"
    artifacts_dir = tmp_path / "artifacts"
    worktree.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(swarm, "_load_prompt_text", lambda _name: "prompt")
    monkeypatch.setattr(
        swarm,
        "run_codex_oneshot",
        lambda **_kwargs: {
            "ok": True,
            "exit_code": 0,
            "content": json.dumps(
                {
                    "status": "delivered_but_failed_validation",
                    "summary": "done",
                    "changed_files": ["tools/example.py"],
                    "local_checks": [],
                    "known_gaps": [],
                }
            ),
            "stderr": "",
            "stdout": "",
        },
    )
    monkeypatch.setattr(swarm, "_git_changed_paths", lambda _worktree: ["tools/example.py"])
    monkeypatch.setattr(
        swarm,
        "_write_patch_artifact",
        lambda **_kwargs: {"has_changes": True, "size_bytes": 42},
    )

    result = swarm._run_worker_turn(
        swarm_id="swarm_test",
        objective="Do work",
        kind="maker",
        slice_spec={"id": "slice-a", "title": "API", "objective": "Do API part"},
        prompt_name="swarm_maker.md",
        worktree=worktree,
        lease_s=60,
        artifacts_dir=artifacts_dir,
        state={"swarm_id": "swarm_test"},
    )

    assert result["status"] == "failed_with_evidence"
    assert "unsupported status" in " ".join(result["known_gaps"]).lower()


def test_run_worker_turn_rejects_delivered_status_without_code_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    worktree = tmp_path / "worktree"
    artifacts_dir = tmp_path / "artifacts"
    worktree.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(swarm, "_load_prompt_text", lambda _name: "prompt")
    monkeypatch.setattr(
        swarm,
        "run_codex_oneshot",
        lambda **_kwargs: {
            "ok": True,
            "exit_code": 0,
            "content": json.dumps(
                {
                    "status": "delivered",
                    "summary": "done",
                    "changed_files": ["tools/example.py"],
                    "local_checks": [],
                    "known_gaps": [],
                    "needs_finisher": False,
                }
            ),
            "stderr": "",
            "stdout": "",
        },
    )
    monkeypatch.setattr(swarm, "_git_changed_paths", lambda _worktree: [])

    def _fake_write_patch_artifact(*, worktree: Path, dst: Path) -> dict[str, object]:
        dst.write_text("", encoding="utf-8")
        return {"path": str(dst), "size_bytes": 0, "has_changes": False}

    monkeypatch.setattr(swarm, "_write_patch_artifact", _fake_write_patch_artifact)

    worker = swarm._run_worker_turn(
        swarm_id="sw-1",
        objective="Ship code",
        kind="maker",
        slice_spec={"id": "slice-1", "title": "Slice 1"},
        prompt_name="swarm_maker.md",
        worktree=worktree,
        lease_s=60,
        artifacts_dir=artifacts_dir,
        state={"deadline_utc": "2026-03-12T01:00:00Z"},
    )

    assert worker["status"] == "failed_with_evidence"
    assert "produced no code changes" in " ".join(worker["known_gaps"])


def test_run_worker_turn_runtime_failure_overrides_reported_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    worktree = tmp_path / "worktree"
    artifacts_dir = tmp_path / "artifacts"
    worktree.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(swarm, "_load_prompt_text", lambda _name: "prompt")
    monkeypatch.setattr(
        swarm,
        "run_codex_oneshot",
        lambda **_kwargs: {
            "ok": False,
            "exit_code": 1,
            "content": json.dumps(
                {
                    "status": "delivered",
                    "summary": "claimed done",
                    "changed_files": ["tools/example.py"],
                    "local_checks": [],
                    "known_gaps": [],
                    "needs_finisher": False,
                }
            ),
            "stderr": "worker timed out",
            "stdout": "",
        },
    )
    monkeypatch.setattr(swarm, "_git_changed_paths", lambda _worktree: ["tools/example.py"])

    def _fake_write_patch_artifact(*, worktree: Path, dst: Path) -> dict[str, object]:
        dst.write_text("diff --git a/tools/example.py b/tools/example.py\n", encoding="utf-8")
        return {"path": str(dst), "size_bytes": int(dst.stat().st_size), "has_changes": True}

    monkeypatch.setattr(swarm, "_write_patch_artifact", _fake_write_patch_artifact)

    worker = swarm._run_worker_turn(
        swarm_id="sw-2",
        objective="Ship code",
        kind="maker",
        slice_spec={"id": "slice-2", "title": "Slice 2"},
        prompt_name="swarm_maker.md",
        worktree=worktree,
        lease_s=60,
        artifacts_dir=artifacts_dir,
        state={"deadline_utc": "2026-03-12T01:00:00Z"},
    )

    assert worker["status"] == "failed_with_evidence"
    assert "worker timed out" in " ".join(worker["known_gaps"])


def test_json_fallback_extracts_trailing_worker_summary_from_codex_transcript() -> None:
    transcript = """OpenAI Codex v0.117.0 (research preview)
--------
user
System instructions:
...
codex
I checked the file and here is the result.
{"status":"delivered","summary":"done","changed_files":["docs/operator/swarm_smoke_note.md"],"local_checks":[],"known_gaps":[]}
tokens used
54,300
"""

    parsed = swarm._json_fallback(transcript)

    assert parsed["status"] == "delivered"
    assert parsed["summary"] == "done"
    assert parsed["changed_files"] == ["docs/operator/swarm_smoke_note.md"]


def test_json_fallback_prefers_full_worker_summary_over_nested_local_check_dict() -> None:
    transcript = """OpenAI Codex v0.117.0 (research preview)
codex
{"status":"delivered","summary":"done","changed_files":["docs/operator/swarm_smoke_note.md"],"local_checks":[{"command":"sed -n '1,20p' docs/operator/swarm_smoke_note.md","result":"passed","notes":"ok"}],"known_gaps":[]}
tokens used
1234
"""

    parsed = swarm._json_fallback(transcript)

    assert parsed["status"] == "delivered"
    assert parsed["summary"] == "done"
    assert parsed["local_checks"][0]["command"] == "sed -n '1,20p' docs/operator/swarm_smoke_note.md"


def test_run_worker_turn_preserves_structured_delivery_when_wrapper_reports_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    worktree = tmp_path / "worktree"
    artifacts_dir = tmp_path / "artifacts"
    worktree.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(swarm, "_load_prompt_text", lambda _name: "prompt")
    transcript = """OpenAI Codex v0.117.0 (research preview)
codex
{"status":"delivered","summary":"done","changed_files":["docs/operator/swarm_smoke_note.md"],"local_checks":[],"known_gaps":[]}
tokens used
1234
"""
    monkeypatch.setattr(
        swarm,
        "run_codex_oneshot",
        lambda **_kwargs: {
            "ok": False,
            "exit_code": 1,
            "content": transcript,
            "stderr": transcript,
            "stdout": "",
        },
    )
    monkeypatch.setattr(swarm, "_git_changed_paths", lambda _worktree: ["docs/operator/swarm_smoke_note.md"])

    def _fake_write_patch_artifact(*, worktree: Path, dst: Path) -> dict[str, object]:
        dst.write_text("diff --git a/docs/operator/swarm_smoke_note.md b/docs/operator/swarm_smoke_note.md\n", encoding="utf-8")
        return {"path": str(dst), "size_bytes": int(dst.stat().st_size), "has_changes": True}

    monkeypatch.setattr(swarm, "_write_patch_artifact", _fake_write_patch_artifact)

    worker = swarm._run_worker_turn(
        swarm_id="sw-3",
        objective="Ship code",
        kind="maker",
        slice_spec={"id": "slice-3", "title": "Slice 3"},
        prompt_name="swarm_maker.md",
        worktree=worktree,
        lease_s=60,
        artifacts_dir=artifacts_dir,
        state={"deadline_utc": "2026-03-12T01:00:00Z"},
    )

    assert worker["status"] == "delivered_with_known_gaps"
    assert worker["patch_has_changes"] is True
    assert "transcript wrapper" in " ".join(worker["known_gaps"])


def test_compose_finisher_message_sanitizes_large_maker_transcript() -> None:
    transcript = "OpenAI Codex v0.117.0 (research preview)\nheader\ncodex\n{\"status\":\"delivered\"}\nERROR: turn/start failed\n"

    message = swarm._compose_finisher_message(
        objective="Ship the bounded smoke note.",
        slice_spec={"id": "slice-01", "title": "Primary delivery slice"},
        maker_summary={
            "worker_id": "slice-01",
            "status": "delivered_with_known_gaps",
            "summary": transcript,
            "known_gaps": [transcript],
            "error": transcript,
            "changed_files": ["docs/operator/swarm_smoke_note.md"],
            "patch_has_changes": True,
        },
        state={"deadline_utc": "2026-03-12T01:00:00Z"},
    )

    assert "OpenAI Codex v0.117.0" not in message
    assert "ERROR: turn/start failed" in message


def test_start_swarm_sync_fails_when_integration_has_no_code_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    worktrees_root = tmp_path / "worktrees"
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(swarm, "_worktrees_root", lambda: worktrees_root)
    monkeypatch.setattr(swarm, "_current_git_ref", lambda: "deadbeef")
    monkeypatch.setattr(
        swarm,
        "_capture_repo_overlay",
        lambda: {"tracked_patch": "", "tracked_dirty": False, "untracked_files": [], "untracked_count": 0},
    )

    def _fake_provision(*, swarm_id: str, worker_id: str, base_ref: str, overlay: dict) -> Path:
        path = worktrees_root / swarm_id / worker_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(swarm, "_provision_worktree", _fake_provision)
    monkeypatch.setattr(
        swarm,
        "run_codex_oneshot",
        lambda **_kwargs: {
            "ok": True,
            "exit_code": 0,
            "content": json.dumps(
                {
                    "summary": "One slice.",
                    "good_enough_bar": "Core path only.",
                    "cut_rules": ["Cut polish first."],
                    "maker_slices": [
                        {
                            "id": "slice-a",
                            "title": "API",
                            "objective": "Do API part",
                            "scope": "API only",
                            "expected_output": "Patch",
                            "lease_s": 60,
                        }
                    ],
                }
            ),
            "stderr": "",
            "stdout": "",
        },
    )
    monkeypatch.setattr(swarm, "_load_prompt_text", lambda _name: "prompt")

    def _fake_worker_turn(**kwargs):
        artifacts_dir = Path(kwargs["artifacts_dir"])
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        patch_path = artifacts_dir / "maker-slice-a.patch"
        patch_path.write_text("diff --git a/x b/x\n", encoding="utf-8")
        return {
            "worker_id": "slice-a",
            "kind": "maker",
            "slice_id": "slice-a",
            "title": "API",
            "status": "delivered",
            "lease_s": int(kwargs["lease_s"]),
            "worktree_path": str(kwargs["worktree"]),
            "started_utc": "2026-03-12T00:00:00Z",
            "updated_utc": "2026-03-12T00:00:00Z",
            "finished_utc": "2026-03-12T00:00:01Z",
            "summary": "done",
            "known_gaps": [],
            "changed_files": ["slice-a.py"],
            "local_checks": [],
            "patch_path": str(patch_path),
            "patch_has_changes": True,
            "patch_size_bytes": int(patch_path.stat().st_size),
            "raw_output_path": str(artifacts_dir / "raw.txt"),
            "summary_path": str(artifacts_dir / "summary.json"),
            "error": "",
        }

    monkeypatch.setattr(swarm, "_run_worker_turn", _fake_worker_turn)
    monkeypatch.setattr(
        swarm,
        "_run_integration",
        lambda **kwargs: {
            "worktree_path": str(worktrees_root / kwargs["swarm_id"] / "integration"),
            "applied": [{"worker_id": "slice-a"}],
            "failed": [],
            "combined_patch_path": str(runtime_root / "combined.patch"),
            "combined_patch_has_changes": False,
            "changed_files": [],
        },
    )

    result = swarm.start_swarm(
        binding={"type": "task_id", "id": "mb3"},
        objective="Build the swarm runtime",
        options={"max_makers": 1, "max_finishers": 1},
        run_async=False,
    )

    assert result["state"]["status"] == "failed"
    assert "Integration produced no code changes" in " ".join(result["state"]["known_gaps"])


def test_start_swarm_sync_finishes_when_known_gap_delivery_integrates_real_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    worktrees_root = tmp_path / "worktrees"
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(swarm, "_worktrees_root", lambda: worktrees_root)
    monkeypatch.setattr(swarm, "_current_git_ref", lambda: "deadbeef")
    monkeypatch.setattr(
        swarm,
        "_capture_repo_overlay",
        lambda: {"tracked_patch": "", "tracked_dirty": False, "untracked_files": [], "untracked_count": 0},
    )

    def _fake_provision(*, swarm_id: str, worker_id: str, base_ref: str, overlay: dict) -> Path:
        path = worktrees_root / swarm_id / worker_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(swarm, "_provision_worktree", _fake_provision)
    monkeypatch.setattr(
        swarm,
        "run_codex_oneshot",
        lambda **_kwargs: {
            "ok": True,
            "exit_code": 0,
            "content": json.dumps(
                {
                    "summary": "One slice.",
                    "good_enough_bar": "Core path only.",
                    "cut_rules": ["Cut polish first."],
                    "maker_slices": [
                        {
                            "id": "slice-a",
                            "title": "API",
                            "objective": "Do API part",
                            "scope": "API only",
                            "expected_output": "Patch",
                            "lease_s": 60,
                        }
                    ],
                }
            ),
            "stderr": "",
            "stdout": "",
        },
    )
    monkeypatch.setattr(swarm, "_load_prompt_text", lambda _name: "prompt")

    def _fake_worker_turn(**kwargs):
        artifacts_dir = Path(kwargs["artifacts_dir"])
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        patch_path = artifacts_dir / "maker-slice-a.patch"
        patch_path.write_text("diff --git a/x b/x\n", encoding="utf-8")
        return {
            "worker_id": "slice-a",
            "kind": "maker",
            "slice_id": "slice-a",
            "title": "API",
            "status": "delivered_with_known_gaps",
            "lease_s": int(kwargs["lease_s"]),
            "worktree_path": str(kwargs["worktree"]),
            "started_utc": "2026-03-12T00:00:00Z",
            "updated_utc": "2026-03-12T00:00:00Z",
            "finished_utc": "2026-03-12T00:00:01Z",
            "summary": "done with wrapper noise",
            "known_gaps": ["transcript wrapper noise"],
            "changed_files": ["slice-a.py"],
            "local_checks": [],
            "patch_path": str(patch_path),
            "patch_has_changes": True,
            "patch_size_bytes": int(patch_path.stat().st_size),
            "raw_output_path": str(artifacts_dir / "raw.txt"),
            "summary_path": str(artifacts_dir / "summary.json"),
            "error": "",
            "needs_finisher": False,
        }

    monkeypatch.setattr(swarm, "_run_worker_turn", _fake_worker_turn)
    monkeypatch.setattr(
        swarm,
        "_run_integration",
        lambda **kwargs: {
            "worktree_path": str(worktrees_root / kwargs["swarm_id"] / "integration"),
            "applied": [{"worker_id": "slice-a"}],
            "failed": [],
            "combined_patch_path": str(runtime_root / "combined.patch"),
            "combined_patch_has_changes": True,
            "changed_files": ["slice-a.py"],
        },
    )

    result = swarm.start_swarm(
        binding={"type": "task_id", "id": "mb3"},
        objective="Build the swarm runtime",
        options={"max_makers": 1, "max_finishers": 0},
        run_async=False,
    )

    assert result["state"]["status"] == "partial"
    assert "transcript wrapper noise" in " ".join(result["state"]["known_gaps"])


def test_start_swarm_sync_demotes_failed_worker_mix_to_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime_swarm"
    worktrees_root = tmp_path / "worktrees"
    monkeypatch.setattr(swarm, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(swarm, "_worktrees_root", lambda: worktrees_root)
    monkeypatch.setattr(swarm, "_current_git_ref", lambda: "deadbeef")
    monkeypatch.setattr(
        swarm,
        "_capture_repo_overlay",
        lambda: {"tracked_patch": "", "tracked_dirty": False, "untracked_files": [], "untracked_count": 0},
    )
    monkeypatch.setattr(
        swarm,
        "run_codex_oneshot",
        lambda **_kwargs: {
            "ok": True,
            "exit_code": 0,
            "content": json.dumps(
                {
                    "summary": "Two slices.",
                    "good_enough_bar": "Core path only.",
                    "cut_rules": ["Cut polish first."],
                    "maker_slices": [
                        {"id": "slice-a", "title": "A", "objective": "A", "scope": "A", "expected_output": "Patch", "lease_s": 60},
                        {"id": "slice-b", "title": "B", "objective": "B", "scope": "B", "expected_output": "Patch", "lease_s": 60},
                    ],
                }
            ),
            "stderr": "",
            "stdout": "",
        },
    )

    def _fake_provision(*, swarm_id: str, worker_id: str, base_ref: str, overlay: dict) -> Path:
        path = worktrees_root / swarm_id / worker_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(swarm, "_provision_worktree", _fake_provision)

    def _fake_worker_turn(**kwargs):
        artifacts_dir = Path(kwargs["artifacts_dir"])
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        worker_id = str(kwargs["slice_spec"]["id"])
        patch_path = artifacts_dir / f"{worker_id}.patch"
        patch_path.write_text(f"diff --git a/{worker_id} b/{worker_id}\n", encoding="utf-8")
        status = "delivered" if worker_id == "slice-a" else "failed_with_evidence"
        return {
            "worker_id": worker_id,
            "kind": "maker",
            "slice_id": worker_id,
            "title": worker_id,
            "status": status,
            "lease_s": int(kwargs["lease_s"]),
            "worktree_path": str(kwargs["worktree"]),
            "started_utc": "2026-03-12T00:00:00Z",
            "updated_utc": "2026-03-12T00:00:00Z",
            "finished_utc": "2026-03-12T00:00:01Z",
            "summary": "done",
            "known_gaps": ([] if status == "delivered" else ["tests failed"]),
            "changed_files": [f"{worker_id}.py"],
            "local_checks": [],
            "patch_path": str(patch_path),
            "patch_has_changes": True,
            "patch_size_bytes": int(patch_path.stat().st_size),
            "raw_output_path": str(artifacts_dir / "raw.txt"),
            "summary_path": str(artifacts_dir / "summary.json"),
            "error": ("" if status == "delivered" else "tests failed"),
            "needs_finisher": False,
        }

    monkeypatch.setattr(swarm, "_run_worker_turn", _fake_worker_turn)
    monkeypatch.setattr(
        swarm,
        "_run_integration",
        lambda **kwargs: {
            "worktree_path": str(worktrees_root / kwargs["swarm_id"] / "integration"),
            "applied": [{"worker_id": "slice-a"}, {"worker_id": "slice-b"}],
            "failed": [],
            "combined_patch_path": str(runtime_root / "combined.patch"),
            "combined_patch_has_changes": True,
            "changed_files": ["slice-a.py", "slice-b.py"],
        },
    )

    result = swarm.start_swarm(
        binding={"type": "task_id", "id": "mb3"},
        objective="Build the swarm runtime",
        options={"max_makers": 2, "max_finishers": 0},
        run_async=False,
    )

    assert result["state"]["status"] == "partial"
    assert "tests failed" in " ".join(result["state"]["known_gaps"])


def test_swarm_routes_start_and_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)

    monkeypatch.setattr(
        routes._agent_swarm,
        "start_swarm",
        lambda **kwargs: {"ok": True, "swarm_id": "sw-1", "state": {"status": "queued", "phase": "queued"}},
    )
    monkeypatch.setattr(
        routes._agent_swarm,
        "cancel_swarm",
        lambda **kwargs: {"ok": True, "swarm_id": "sw-1", "state": {"status": "cancelled", "phase": "cancelled"}},
    )

    start_req = _DummyReq(
        cfg=cfg,
        path="/api/agents/swarm/start",
        body={"binding": {"type": "task_id", "id": "mb3"}, "objective": "Swarm this"},
    )
    monkeypatch.setattr(
        routes,
        "_read_request_json",
        lambda _req, max_bytes=0: {"binding": {"type": "task_id", "id": "mb3"}, "objective": "Swarm this"},
    )
    routes.WebUIRequestHandler.do_POST(start_req)  # type: ignore[arg-type]
    assert start_req._status == 200
    assert _read_payload(start_req)["swarm_id"] == "sw-1"

    cancel_req = _DummyReq(cfg=cfg, path="/api/agents/swarm/cancel", body={"swarm_id": "sw-1", "reason": "stop"})
    monkeypatch.setattr(
        routes,
        "_read_request_json",
        lambda _req, max_bytes=0: {"swarm_id": "sw-1", "reason": "stop"},
    )
    routes.WebUIRequestHandler.do_POST(cancel_req)  # type: ignore[arg-type]
    assert cancel_req._status == 200
    assert _read_payload(cancel_req)["state"]["status"] == "cancelled"


def test_mobile_chat_stream_swarm_trigger_starts_background_swarm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    prompts_dir = tmp_path / "governance" / "agents" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "role__generalist_mobile.md").write_text("generalist mobile prompt", encoding="utf-8")

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE", {})
    monkeypatch.setattr(routes, "_MOBILE_BOOTSTRAP_PROMPT_CACHE_KEY", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSION_CAPS", {})
    monkeypatch.setattr(routes, "_MOBILE_SESSIONS_PATH", None)
    monkeypatch.setattr(
        routes,
        "_read_request_json",
        lambda _req, max_bytes=0: {"message": "Swarm it: build the swarm runtime", "agent_key": "a3"},
    )
    monkeypatch.setattr(routes._agents, "session_history_rows", lambda **kwargs: [])
    monkeypatch.setattr(routes._agents, "append_session_message", lambda **kwargs: dict(kwargs))
    monkeypatch.setattr(
        routes._agent_swarm,
        "start_swarm",
        lambda **kwargs: {"ok": True, "swarm_id": "sw-123", "state": {"phase": "queued"}},
    )

    req = _DummyReq(cfg=cfg, path="/api/mobile/chat/stream")
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    body = req.wfile.getvalue().decode("utf-8")
    assert "Started swarm sw-123." in body
    assert "data: [DONE]" in body
