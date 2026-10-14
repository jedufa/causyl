"""Repair canonical mobile chat transcripts and derived bubble markdown."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


if str(_repo_root()) not in sys.path:
    sys.path.insert(0, str(_repo_root()))

from infra.webui import server_agents
from infra.webui import server_routes_and_main as routes


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile persisted mobile chat caches into canonical sessions and refresh bubble markdown.",
    )
    parser.add_argument(
        "--runs-root",
        default=str((_repo_root() / "runtime" / "live").resolve()),
        help="Runs root used for legacy mobile cache discovery.",
    )
    return parser.parse_args()


def _session_file_for_thread(thread_id: str) -> Path:
    role = routes._mobile_role_for_thread_id(thread_id)
    rr = server_agents._resolve_paths({"type": "task_id", "id": str(thread_id)})
    return server_agents._session_file(rr["chat_dir"], role)


def _summary_payload() -> dict[str, Any]:
    primary = routes._mobile_sessions_primary_path()
    sessions: dict[str, Any] = {}
    known_tids = sorted(set(routes._MOBILE_SESSIONS.keys()) | set(routes._mobile_known_thread_ids()))
    for tid in known_tids:
        history = routes._mobile_effective_history(tid, tail=0)
        session_file = _session_file_for_thread(tid)
        bubble_path = server_agents._mobile_bubble_projection_path(
            binding={"type": "task_id", "id": str(tid)},
            role=routes._mobile_role_for_thread_id(tid),
        )
        sessions[tid] = {
            "messages": len(history),
            "session_file": str(session_file),
            "bubble_file": str(bubble_path or ""),
        }
    return {
        "ok": True,
        "primary_cache": str(primary),
        "threads": sessions,
    }


def main() -> int:
    args = _parse_args()
    cfg = routes.WebUIConfig(runs_root=Path(args.runs_root).resolve())
    routes._mobile_sessions_init(cfg)
    print(json.dumps(_summary_payload(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
