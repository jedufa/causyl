from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from tools import runtime_policy as _runtime_policy

from .surfaces import build_internal_surface_bundle


BACKEND_POLICY_KEY = "scheduler.backend"
BACKEND_ENV = "EIEL_SCHEDULER_BACKEND"


def selected_backend(raw: Any | None = None) -> str:
    text = str(
        raw
        if raw is not None
        else _runtime_policy.get_runtime_policy_text(BACKEND_POLICY_KEY, "v2", env_names=(BACKEND_ENV,))
    ).strip().lower()
    if text in {"v2", "sched_v2", "intent_family_v2"}:
        return "v2"
    if text in {"legacy", "v1", "scheduled_jobs"}:
        return "legacy"
    if text == "auto":
        return "v2"
    raise ValueError(f"unsupported scheduler backend: {text}")


def v2_surface_bundle(
    *,
    repo_root: Path | None = None,
    parent_uid: str = "",
    run_at_utc: str = "2026-04-10T00:00:00Z",
    mode: str | None = None,
) -> Mapping[str, Any]:
    return build_internal_surface_bundle(
        repo_root=repo_root,
        parent_uid=parent_uid,
        run_at_utc=run_at_utc,
        mode=mode,
    )


def require_legacy_for_mutation(action: str, *, backend: str | None = None) -> None:
    selected = selected_backend(backend)
    if selected != "legacy":
        raise SystemExit(
            f"{action} is legacy-only right now. v2 is the default scheduler backend; "
            "re-run with --backend legacy only if you intentionally need the legacy job store."
        )
