from __future__ import annotations

from .models import JobInfo, StageView


def hr() -> None:
    print("\n" + "-" * 72)


def header(title: str) -> None:
    hr()
    print(title)
    hr()


def show_job(job: JobInfo, repo_root: str) -> None:
    print(f"Job: {job.job_id}")
    print(f"Repo: {repo_root}")
    print(f"Status: {job.status}")
    print(f"Current stage: {job.current_stage or '-'}")
    print(f"Retry count: {job.retry_count}")
    print(f"Latest artifact: {job.latest_artifact or '-'}")
    print(f"Last update: {job.updated_at or '-'}")


def show_stages(views: list[StageView]) -> None:
    for idx, view in enumerate(views, start=1):
        tail = f" ({view.key})"
        if view.runnable:
            status = "runnable"
        elif view.completed:
            status = "completed"
        else:
            status = f"locked: {view.locked_reason}"
        print(f"{idx:>2}. [{view.marker}] {view.label}{tail} -> {status}")


def show_result_summary(summary: str, artifact_path: str | None = None, log_path: str | None = None) -> None:
    header("Stage Result")
    print(summary)
    if artifact_path:
        print(f"\nArtifact: {artifact_path}")
    if log_path:
        print(f"Log: {log_path}")


def show_failure(stderr_excerpt: str, log_path: str | None = None) -> None:
    header("Stage failed")
    print(stderr_excerpt.strip() or "Unknown failure")
    if log_path:
        print(f"\nLog: {log_path}")
