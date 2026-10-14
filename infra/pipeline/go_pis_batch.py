from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .core import ensure_directory, save_json_atomic, save_text_atomic, utc_now_iso


class GoPisBatchError(RuntimeError):
    pass


class BatchInterruptController:
    def __init__(self) -> None:
        self.sigint_count = 0
        self.stop_requested = False
        self.current_proc: Optional[subprocess.Popen[str]] = None

    def set_current_proc(self, proc: Optional[subprocess.Popen[str]]) -> None:
        self.current_proc = proc

    def _kill_current_process_group(self, force: bool = False) -> None:
        proc = self.current_proc
        if proc is None:
            return
        try:
            if proc.poll() is not None:
                return

            if force:
                os.killpg(proc.pid, signal.SIGKILL)
                return

            os.killpg(proc.pid, signal.SIGTERM)

            deadline = time.time() + 3.0
            while time.time() < deadline:
                if proc.poll() is not None:
                    return
                time.sleep(0.05)

            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except Exception:
            try:
                if proc.poll() is None:
                    if force:
                        proc.kill()
                    else:
                        proc.terminate()
            except Exception:
                pass

    def request_stop(self) -> None:
        self.stop_requested = True
        self._kill_current_process_group(force=False)

    def handle_sigint(self, signum: int, frame: object) -> None:
        self.sigint_count += 1
        if self.sigint_count == 1:
            self.request_stop()
            sys.stderr.write(
                "\n[go_pis_batch] Ctrl-C received. Killing the ongoing go run, then bundling and cleaning up completed artifacts...\n"
            )
            sys.stderr.flush()
            return

        sys.stderr.write("\n[go_pis_batch] Second Ctrl-C received. Forcing immediate exit.\n")
        sys.stderr.flush()
        self._kill_current_process_group(force=True)
        raise KeyboardInterrupt


def _slugify(text: str, max_len: int = 80) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(text or "").strip()).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return (cleaned or "item")[:max_len].strip("-") or "item"


def _extract_pis_uid(pis_path: Path) -> str:
    parent = pis_path.parent.name
    if parent.startswith("PIS-"):
        return parent.split("__", 1)[0]
    try:
        for line in pis_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("**UID:**"):
                return line.split("**UID:**", 1)[1].strip()
    except Exception:
        return parent or pis_path.stem
    return parent or pis_path.stem


def _extract_pis_title(pis_path: Path) -> str:
    try:
        lines = pis_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    in_title = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("## 1. Title"):
            in_title = True
            continue
        if in_title:
            if not line:
                continue
            if line.startswith("## "):
                break
            return line
    return ""


def _extract_promptset_title(path: Path) -> str:
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line.startswith("# "):
                return line[2:].strip()
    except Exception:
        return ""
    return ""


def _extract_item_uid(path: Path, item_kind: str) -> str:
    if item_kind == "pis":
        return _extract_pis_uid(path)
    return path.stem


def _extract_item_title(path: Path, item_kind: str) -> str:
    if item_kind == "pis":
        return _extract_pis_title(path)
    if item_kind == "prompt":
        return _extract_promptset_title(path)
    return ""


def _find_uid_index(files: List[Path], uid: str, label: str) -> int:
    needle = str(uid or "").strip()
    if not needle:
        raise GoPisBatchError(f"{label} value is empty")
    for idx, path in enumerate(files):
        if _extract_item_uid(path, "pis") == needle:
            return idx
    raise GoPisBatchError(f"{label} UID not found in discovered files: {needle}")


def _parse_subset(subset: str, total: int) -> Tuple[int, int]:
    raw = str(subset or "").strip()
    if not raw:
        raise GoPisBatchError("--subset cannot be empty")
    if "-" not in raw:
        raise GoPisBatchError("--subset must use START-END format, for example 5-25")

    left, right = raw.split("-", 1)
    try:
        start = int(left.strip())
        end = int(right.strip())
    except ValueError as exc:
        raise GoPisBatchError("--subset must contain integers, for example 5-25") from exc

    if start < 1 or end < 1:
        raise GoPisBatchError("--subset indices are 1-based and must be >= 1")
    if start > end:
        raise GoPisBatchError("--subset start must be <= end")
    if start > total:
        raise GoPisBatchError(f"--subset start {start} is beyond the discovered file count {total}")

    end = min(end, total)
    return start, end


def _apply_selection(
    files: List[Path],
    *,
    subset: str = "",
    first_uid: str = "",
    last_uid: str = "",
) -> List[Path]:
    if subset and (first_uid or last_uid):
        raise GoPisBatchError("Use either --subset or --first/--last, not both")

    if subset:
        start, end = _parse_subset(subset, len(files))
        return files[start - 1:end]

    if first_uid or last_uid:
        start_idx = 0
        end_idx = len(files) - 1

        if first_uid:
            start_idx = _find_uid_index(files, first_uid, "--first")
        if last_uid:
            end_idx = _find_uid_index(files, last_uid, "--last")

        if start_idx > end_idx:
            raise GoPisBatchError("--first appears after --last in the discovered file order")

        return files[start_idx:end_idx + 1]

    return files


def discover_pis_files(
    *,
    repo_root: str | Path,
    items_dir: str | Path = "governance/ledger/patch_instruction_sheets",
    pis_glob: str = "*/pis.md",
    match: str = "",
    limit: int = 0,
    subset: str = "",
    first_uid: str = "",
    last_uid: str = "",
) -> List[Path]:
    return discover_batch_files(
        repo_root=repo_root,
        items_dir=items_dir,
        items_glob=pis_glob,
        item_kind="pis",
        match=match,
        limit=limit,
        subset=subset,
        first_uid=first_uid,
        last_uid=last_uid,
    )


def discover_batch_files(
    *,
    repo_root: str | Path,
    items_dir: str | Path,
    items_glob: str,
    item_kind: str,
    match: str = "",
    limit: int = 0,
    subset: str = "",
    first_uid: str = "",
    last_uid: str = "",
) -> List[Path]:
    if subset and (first_uid or last_uid):
        raise GoPisBatchError("Use either --subset or --first/--last, not both")

    root = Path(repo_root).resolve()
    base = (root / Path(items_dir)).resolve()
    if not base.exists():
        raise GoPisBatchError(f"Batch items directory not found: {base}")
    files = [
        path.resolve()
        for path in sorted(base.glob(items_glob))
        if path.is_file() and not path.name.startswith("selection_manifest.")
    ]

    needle = str(match or "").strip().lower()
    if needle:
        filtered: List[Path] = []
        for path in files:
            blob = (
                f"{path.parent.name}\n"
                f"{_extract_item_uid(path, item_kind)}\n"
                f"{_extract_item_title(path, item_kind)}\n"
                f"{path.as_posix()}"
            ).lower()
            if needle in blob:
                filtered.append(path)
        files = filtered

    if subset:
        start, end = _parse_subset(subset, len(files))
        files = files[start - 1:end]
    elif first_uid or last_uid:
        start_idx = 0
        end_idx = len(files) - 1

        if first_uid:
            start_idx = _find_item_index(files, first_uid, "--first", item_kind)
        if last_uid:
            end_idx = _find_item_index(files, last_uid, "--last", item_kind)

        if start_idx > end_idx:
            raise GoPisBatchError("--first appears after --last in the discovered file order")

        files = files[start_idx:end_idx + 1]

    if limit > 0:
        files = files[:limit]
    return files


def _find_item_index(files: List[Path], uid: str, label: str, item_kind: str) -> int:
    needle = str(uid or "").strip()
    if not needle:
        raise GoPisBatchError(f"{label} value is empty")
    for idx, path in enumerate(files):
        if _extract_item_uid(path, item_kind) == needle:
            return idx
    raise GoPisBatchError(f"{label} item id not found in discovered files: {needle}")


def _repo_rel(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except Exception:
        return path.resolve().as_posix()


def _write_text(path: Path, content: str) -> None:
    ensure_directory(path.parent)
    path.write_text(content, encoding="utf-8")


def _format_duration(seconds: float) -> str:
    total = max(0, int(round(float(seconds or 0.0))))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _batch_eta_seconds(*, completed: int, total: int, elapsed_seconds: float) -> float:
    if completed <= 0 or total <= completed or elapsed_seconds <= 0:
        return 0.0
    average = float(elapsed_seconds) / float(completed)
    return max(0.0, average * float(total - completed))


def _extract_json_from_mixed_output(text: str) -> Dict[str, Any]:
    blob = str(text or "").strip()
    if not blob:
        raise GoPisBatchError("go returned empty output")

    try:
        parsed = json.loads(blob)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    lines = [line for line in blob.splitlines() if line.strip()]
    if lines:
        try:
            parsed = json.loads(lines[-1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    for start in range(len(blob)):
        if blob[start] != "{":
            continue
        candidate = blob[start:].strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    raise GoPisBatchError("go returned mixed output but no parseable JSON payload was found")


def _invoke_go(
    *,
    repo_root: Path,
    go_script: Path,
    prompt_text: str,
    output_dir: Path,
    provider: str = "",
    model: str = "",
    preserve_semantic_cache: bool = False,
    deterministic_only: bool = False,
    pipeline_step_stop: int = 0,
    extra_env: Optional[Mapping[str, str]] = None,
    interruptor: Optional[BatchInterruptController] = None,
) -> Dict[str, Any]:
    cmd = [sys.executable, str(go_script), "--repo-root", str(repo_root), "--json", "--output-dir", str(output_dir)]
    if provider:
        cmd.extend(["--provider", provider])
    if model:
        cmd.extend(["--model", model])
    if preserve_semantic_cache:
        cmd.append("--preserve-semantic-cache")
    if deterministic_only:
        cmd.append("--deterministic-only")
    if pipeline_step_stop:
        cmd.extend(["--pipeline-step-stop", str(int(pipeline_step_stop))])

    env = dict(os.environ)
    env["PIPELINE_FORCE_LIVE_PROGRESS"] = "1"
    for key, value in dict(extra_env or {}).items():
        if value is None:
            continue
        env[str(key)] = str(value)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=False,
        bufsize=0,
        cwd=str(repo_root),
        env=env,
        start_new_session=True,
    )

    if interruptor is not None:
        interruptor.set_current_proc(proc)

    assert proc.stdin is not None
    assert proc.stdout is not None

    stdout_chunks: List[bytes] = []

    try:
        try:
            proc.stdin.write(prompt_text.encode("utf-8"))
        except BrokenPipeError:
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

        while True:
            chunk = proc.stdout.read(1)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            stdout_chunks.append(chunk)
        proc.wait()
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        if interruptor is not None:
            interruptor.set_current_proc(None)

    merged_output = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    interrupted = bool(interruptor and interruptor.stop_requested and int(proc.returncode or 0) != 0)

    payload: Dict[str, Any] = {
        "command": cmd,
        "returncode": int(proc.returncode or 0),
        "stdout": merged_output,
        "stderr": "",
        "interrupted": interrupted,
    }

    if proc.returncode == 0:
        payload["go_result"] = _extract_json_from_mixed_output(merged_output)

    return payload


def _add_path_to_zip(zf: zipfile.ZipFile, repo_root: Path, path: Path, added: set[str]) -> None:
    if not path.exists():
        return
    if path.is_file():
        arcname = _repo_rel(repo_root, path)
        if arcname in added:
            return
        zf.write(path, arcname)
        added.add(arcname)
        return
    for child in sorted(path.rglob("*")):
        if not child.is_file():
            continue
        arcname = _repo_rel(repo_root, child)
        if arcname in added:
            continue
        zf.write(child, arcname)
        added.add(arcname)


def _cleanup_created_paths(paths: Iterable[Path]) -> List[str]:
    deleted: List[str] = []
    for path in sorted({p.resolve() for p in paths}, key=lambda p: len(p.as_posix().split("/")), reverse=True):
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=False)
        else:
            path.unlink(missing_ok=True)
        deleted.append(path.as_posix())
    return deleted


def _batch_summary_markdown(manifest: Mapping[str, Any]) -> str:
    entries = list(manifest.get("entries") or [])
    item_label = str(manifest.get("item_label") or "item")
    item_id_field = str(manifest.get("item_id_field") or "item_id")
    item_path_field = str(manifest.get("item_path_field") or "item_path")
    succeeded = sum(1 for entry in entries if entry.get("status") == "ok")
    failed = sum(1 for entry in entries if entry.get("status") == "failed")
    interrupted = sum(1 for entry in entries if entry.get("status") == "interrupted")
    skipped = sum(1 for entry in entries if entry.get("status") == "skipped")
    lines = [
        f"# go {item_label} batch summary",
        "",
        f"- batch_id: `{manifest.get('batch_id')}`",
        f"- total_items: `{len(entries)}`",
        f"- elapsed: `{manifest.get('elapsed_human') or '(unknown)'}`",
        f"- succeeded: `{succeeded}`",
        f"- failed: `{failed}`",
        f"- interrupted: `{interrupted}`",
        f"- skipped: `{skipped}`",
        f"- archive_path: `{manifest.get('archive_path')}`",
        "",
        f"## {item_label.title()}s",
        "",
    ]
    for idx, entry in enumerate(entries, start=1):
        lines.extend(
            [
                f"### {idx}. {entry.get(item_id_field)}",
                f"- status: `{entry.get('status')}`",
                f"- {item_path_field}: `{entry.get(item_path_field)}`",
                f"- title: `{entry.get('title') or '(untitled)'}`",
                f"- report_dir: `{entry.get('report_dir')}`" if entry.get("report_dir") else "- report_dir: `(none)`",
                f"- job_ids: {', '.join(f'`{job_id}`' for job_id in list(entry.get('job_ids') or [])) or '`(none)`'}",
                f"- elapsed: `{entry.get('elapsed_human') or '(unknown)'}`",
            ]
        )
        if entry.get("error"):
            lines.append(f"- error: `{entry.get('error')}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run_pis_batch(
    *,
    repo_root: str | Path,
    items_dir: str | Path = "governance/ledger/patch_instruction_sheets",
    pis_glob: str = "*/pis.md",
    item_kind: str = "pis",
    go_script: str | Path = "infra/bin/go",
    work_dir: str | Path = "runtime/reports/go_batches",
    archive_dir: str | Path = "runtime/reports/go_batch_archives",
    match: str = "",
    limit: int = 0,
    subset: str = "",
    first_uid: str = "",
    last_uid: str = "",
    provider: str = "",
    model: str = "",
    preserve_semantic_cache: bool = False,
    deterministic_only: bool = False,
    pipeline_step_stop: int = 0,
    keep_generated: bool = False,
    batch_label: str = "",
    extra_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    if item_kind not in {"pis", "prompt"}:
        raise GoPisBatchError(f"Unsupported --item-kind: {item_kind}")
    go_path = (root / Path(go_script)).resolve() if not Path(go_script).is_absolute() else Path(go_script).resolve()
    if not go_path.exists():
        raise GoPisBatchError(f"go script not found: {go_path}")
    item_files = discover_batch_files(
        repo_root=root,
        items_dir=items_dir,
        items_glob=pis_glob,
        item_kind=item_kind,
        match=match,
        limit=limit,
        subset=subset,
        first_uid=first_uid,
        last_uid=last_uid,
    )
    if not item_files:
        raise GoPisBatchError("No batch input files matched the requested selection")

    stamp = time.strftime("%Y.%m.%d-%H%M%S", time.gmtime())
    label_slug = _slugify(batch_label, max_len=48)
    batch_id = f"go-pis-batch-{label_slug}-{stamp}" if label_slug else f"go-pis-batch-{stamp}"
    batch_work_root = (root / Path(work_dir) / batch_id).resolve()
    batch_archive_root = (root / Path(archive_dir)).resolve()
    ensure_directory(batch_work_root)
    ensure_directory(batch_archive_root)
    reports_root = batch_work_root / "reports"
    logs_root = batch_work_root / "logs"
    ensure_directory(reports_root)
    ensure_directory(logs_root)

    created_paths: List[Path] = []
    entries: List[Dict[str, Any]] = []
    batch_started = time.time()
    total_items = len(item_files)

    interruptor = BatchInterruptController()
    previous_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, interruptor.handle_sigint)

    try:
        for index, item_path in enumerate(item_files, start=1):
            if interruptor.stop_requested:
                break

            rel_item = _repo_rel(root, item_path)
            uid = _extract_item_uid(item_path, item_kind)
            title = _extract_item_title(item_path, item_kind)
            label = _slugify(f"{uid} {item_path.parent.name}", max_len=96)
            report_dir = reports_root / label
            prompt_text = item_path.read_text(encoding="utf-8", errors="replace")
            elapsed_before = time.time() - batch_started
            eta_before = _batch_eta_seconds(completed=index - 1, total=total_items, elapsed_seconds=elapsed_before)
            sys.stdout.write(
                f"[go_pis_batch] item {index}/{total_items} starting id={uid} "
                f"completed={index - 1} remaining={total_items - index + 1} "
                f"elapsed={_format_duration(elapsed_before)} "
                f"eta={_format_duration(eta_before) if eta_before > 0 else 'unknown'}\n"
            )
            sys.stdout.flush()
            item_started = time.time()
            payload = _invoke_go(
                repo_root=root,
                go_script=go_path,
                prompt_text=prompt_text,
                output_dir=report_dir,
                provider=provider,
                model=model,
                preserve_semantic_cache=preserve_semantic_cache,
                deterministic_only=deterministic_only,
                pipeline_step_stop=pipeline_step_stop,
                extra_env=extra_env,
                interruptor=interruptor,
            )
            stdout_path = logs_root / f"{index:04d}-{label}.stdout.txt"
            stderr_path = logs_root / f"{index:04d}-{label}.stderr.txt"
            _write_text(stdout_path, str(payload.get("stdout") or ""))
            _write_text(stderr_path, str(payload.get("stderr") or ""))
            created_paths.extend([stdout_path, stderr_path])
            if report_dir.exists():
                created_paths.append(report_dir)

            interrupted = bool(payload.get("interrupted"))
            returncode = int(payload.get("returncode", 1))
            status = "interrupted" if interrupted else ("ok" if returncode == 0 else "failed")
            item_elapsed = time.time() - item_started

            entry: Dict[str, Any] = {
                "index": index,
                "item_id": uid,
                "title": title,
                "item_path": rel_item,
                "report_dir": _repo_rel(root, report_dir) if report_dir.exists() else "",
                "stdout_log": _repo_rel(root, stdout_path),
                "stderr_log": _repo_rel(root, stderr_path),
                "status": status,
                "returncode": returncode,
                "elapsed_seconds": round(item_elapsed, 2),
                "elapsed_human": _format_duration(item_elapsed),
                "job_ids": [],
                "run_ids": [],
                "error": "",
            }
            if item_kind == "pis":
                entry["pis_uid"] = uid
                entry["pis_path"] = rel_item

            if returncode == 0:
                go_result = dict(payload.get("go_result") or {})
                runs = dict(go_result.get("runs") or {})
                entry["go_output_dir"] = str(go_result.get("output_dir") or "")
                entry["comparison_report_json"] = str(go_result.get("comparison_report_json") or "")
                entry["comparison_report_md"] = str(go_result.get("comparison_report_md") or "")
                job_ids = [str(run.get("job_id") or "").strip() for run in runs.values() if str(run.get("job_id") or "").strip()]
                run_ids = [str(run.get("run_id") or "").strip() for run in runs.values() if str(run.get("run_id") or "").strip()]
                entry["job_ids"] = job_ids
                entry["run_ids"] = run_ids
                for job_id in job_ids:
                    job_dir = root / "jobs" / job_id
                    if job_dir.exists():
                        created_paths.append(job_dir)
            else:
                if interrupted:
                    entry["error"] = "Interrupted by user via Ctrl-C; current go run was terminated and batch finalized from completed artifacts."
                else:
                    entry["error"] = f"go failed with returncode={returncode}"
            entries.append(entry)
            elapsed_after = time.time() - batch_started
            eta_after = _batch_eta_seconds(completed=index, total=total_items, elapsed_seconds=elapsed_after)
            sys.stdout.write(
                f"[go_pis_batch] item {index}/{total_items} finished id={uid} status={status} "
                f"completed={index} remaining={total_items - index} "
                f"item_elapsed={_format_duration(item_elapsed)} "
                f"batch_elapsed={_format_duration(elapsed_after)} "
                f"eta={_format_duration(eta_after) if eta_after > 0 else 'unknown'}\n"
            )
            sys.stdout.flush()

            if interrupted:
                break
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    manifest: Dict[str, Any] = {
        "batch_id": batch_id,
        "repo_root": str(root),
        "item_kind": item_kind,
        "item_label": "pis" if item_kind == "pis" else "prompt",
        "item_id_field": "pis_uid" if item_kind == "pis" else "item_id",
        "item_path_field": "pis_path" if item_kind == "pis" else "item_path",
        "items_dir": _repo_rel(root, (root / Path(items_dir)).resolve()),
        "pis_glob": pis_glob,
        "match": match,
        "limit": int(limit or 0),
        "subset": subset or None,
        "first_uid": first_uid or None,
        "last_uid": last_uid or None,
        "provider": provider or None,
        "model": model or None,
        "preserve_semantic_cache": bool(preserve_semantic_cache),
        "deterministic_only": bool(deterministic_only),
        "pipeline_step_stop": int(pipeline_step_stop or 0) or None,
        "keep_generated": bool(keep_generated),
        "batch_label": batch_label or None,
        "work_dir": _repo_rel(root, batch_work_root),
        "archive_dir": _repo_rel(root, batch_archive_root),
        "interrupted": bool(interruptor.stop_requested),
        "elapsed_seconds": round(time.time() - batch_started, 2),
        "elapsed_human": _format_duration(time.time() - batch_started),
        "entries": entries,
    }

    manifest_path = batch_work_root / "batch_manifest.json"
    summary_md_path = batch_work_root / "batch_summary.md"
    archive_path = batch_archive_root / f"{batch_id}.zip"
    manifest["archive_path"] = _repo_rel(root, archive_path)
    manifest["archive_abspath"] = str(archive_path)
    save_json_atomic(manifest_path, manifest)
    save_text_atomic(summary_md_path, _batch_summary_markdown(manifest))
    created_paths.extend([manifest_path, summary_md_path])

    added: set[str] = set()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        _add_path_to_zip(zf, root, manifest_path, added)
        _add_path_to_zip(zf, root, summary_md_path, added)
        for entry in entries:
            report_dir_rel = str(entry.get("report_dir") or "").strip()
            if report_dir_rel:
                _add_path_to_zip(zf, root, root / report_dir_rel, added)
            for rel in [str(entry.get("stdout_log") or "").strip(), str(entry.get("stderr_log") or "").strip()]:
                if rel:
                    _add_path_to_zip(zf, root, root / rel, added)
            for job_id in list(entry.get("job_ids") or []):
                _add_path_to_zip(zf, root, root / "jobs" / str(job_id), added)

    deleted_paths: List[str] = []
    if not keep_generated:
        deleted_paths = _cleanup_created_paths(created_paths)
        for parent in [logs_root, reports_root, batch_work_root]:
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()

    manifest["deleted_paths"] = deleted_paths
    if manifest["interrupted"]:
        manifest["status"] = "interrupted"
    elif all(entry.get("status") == "ok" for entry in entries):
        manifest["status"] = "ok"
    else:
        manifest["status"] = "partial_failure"
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="go_pis_batch",
        description="Run infra/bin/go over every ledger PIS markdown file, archive the generated jobs/reports, and optionally clean them up.",
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--items-dir", default="governance/ledger/patch_instruction_sheets")
    parser.add_argument("--pis-glob", default="*/pis.md")
    parser.add_argument("--item-kind", choices=["pis", "prompt"], default="pis")
    parser.add_argument("--go-script", default="infra/bin/go")
    parser.add_argument("--work-dir", default="runtime/reports/go_batches")
    parser.add_argument("--archive-dir", default="runtime/reports/go_batch_archives")
    parser.add_argument("--match", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--subset", default="")
    parser.add_argument("--first", dest="first_uid", default="")
    parser.add_argument("--last", dest="last_uid", default="")
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--preserve-semantic-cache", action="store_true")
    parser.add_argument("--deterministic-only", action="store_true")
    parser.add_argument("--pipeline-step-stop", type=int, default=0)
    parser.add_argument("--keep-generated", action="store_true")
    parser.add_argument("--batch-label", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = run_pis_batch(
            repo_root=args.repo_root,
            items_dir=args.items_dir,
            pis_glob=args.pis_glob,
            item_kind=args.item_kind,
            go_script=args.go_script,
            work_dir=args.work_dir,
            archive_dir=args.archive_dir,
            match=args.match,
            limit=args.limit,
            subset=args.subset,
            first_uid=args.first_uid,
            last_uid=args.last_uid,
            provider=args.provider,
            model=args.model,
            preserve_semantic_cache=args.preserve_semantic_cache,
            deterministic_only=args.deterministic_only,
            pipeline_step_stop=args.pipeline_step_stop,
            keep_generated=args.keep_generated,
            batch_label=args.batch_label,
        )
    except GoPisBatchError as exc:
        sys.stderr.write(f"go_pis_batch: {exc}\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("go_pis_batch: forced exit on second Ctrl-C\n")
        return 130

    if args.json:
        sys.stdout.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(f"batch_id={manifest.get('batch_id')}\n")
        sys.stdout.write(f"status={manifest.get('status')}\n")
        sys.stdout.write(f"archive_path={manifest.get('archive_path')}\n")
        sys.stdout.write(f"items={len(list(manifest.get('entries') or []))}\n")
        sys.stdout.write(f"deleted_paths={len(list(manifest.get('deleted_paths') or []))}\n")
    return 0 if manifest.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
