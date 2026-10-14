#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.core import PipelineError
from infra.pipeline.diff_utils import ParsedDiffFile, normalize_diff_text, parse_unified_diff
from infra.pipeline.patch_ops import apply_unified_diff_to_workspace, preflight_validated_patch
from infra.pipeline.path_policy import normalize_repo_relative_path


@dataclass(frozen=True)
class SectionRewrite:
    old_path: Optional[str]
    new_path: Optional[str]
    target_path: str


@dataclass(frozen=True)
class NormalizedPatch:
    content: str
    target_files: List[str]
    rewrites: List[SectionRewrite]


@dataclass(frozen=True)
class FileDiagnosis:
    path: str
    status: str
    reason: str
    user_message: str
    action_hint: str
    detail: str
    rewritten_from: Optional[str] = None


def _run(cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), cwd=str(cwd), text=True, capture_output=True, check=False)


def _repo_root() -> Path:
    cp = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "Not inside a git worktree.")
    return Path(cp.stdout.strip()).resolve()


def _quote_diff_path(path: Optional[str], *, side: str) -> str:
    if path in {None, "/dev/null"}:
        return "/dev/null"
    text = str(path)
    if any(ch.isspace() for ch in text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{side}/{escaped}"'
    return f"{side}/{text}"


def _diff_header_for_section(section: SectionRewrite) -> str:
    return f"diff --git {_quote_diff_path(section.old_path, side='a')} {_quote_diff_path(section.new_path, side='b')}"


def _header_path_for_side(path: Optional[str], *, side: str) -> str:
    if path in {None, "/dev/null"}:
        return "/dev/null"
    return f"{side}/{path}"


def _path_parts(value: str) -> Tuple[str, ...]:
    return tuple(part for part in PurePosixPath(value.replace("\\", "/")).parts if part not in {"", "."})


def _candidate_paths(repo_root: Path, raw_path: Optional[str]) -> List[str]:
    if raw_path in {None, "", "/dev/null"}:
        return []
    raw = str(raw_path).strip().replace("\\", "/")
    candidates: List[str] = []
    seen: set[str] = set()
    parts = _path_parts(raw)
    for index in range(0, len(parts)):
        candidate = "/".join(parts[index:])
        if not candidate:
            continue
        try:
            normalized = normalize_repo_relative_path(str(repo_root), candidate)
        except PipelineError:
            continue
        if normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)
    if not candidates:
        try:
            normalized = normalize_repo_relative_path(str(repo_root), raw)
        except PipelineError:
            return []
        return [normalized]
    return candidates


def _score_candidate(repo_root: Path, candidate: str, *, prefer_existing_file: bool) -> Tuple[int, int]:
    path = repo_root / candidate
    score = 0
    if path.exists():
        score += 100 if prefer_existing_file else 40
    if path.is_file():
        score += 20
    if path.parent.exists():
        score += 10
    depth_penalty = candidate.count("/")
    return score, -depth_penalty


def _select_candidate(repo_root: Path, options: Iterable[str], *, prefer_existing_file: bool) -> Optional[str]:
    unique = list(dict.fromkeys(str(opt) for opt in options if str(opt).strip()))
    if not unique:
        return None
    if prefer_existing_file:
        existing = [item for item in unique if (repo_root / item).exists()]
        if not existing:
            return None
        unique = existing
    ranked = sorted(
        unique,
        key=lambda item: _score_candidate(repo_root, item, prefer_existing_file=prefer_existing_file),
        reverse=True,
    )
    return ranked[0]


def _normalize_section_paths(repo_root: Path, section: ParsedDiffFile) -> SectionRewrite:
    if section.is_new_file:
        target = _select_candidate(repo_root, _candidate_paths(repo_root, section.new_path), prefer_existing_file=False)
        if not target:
            raise RuntimeError(f"Unable to normalize new-file path: {section.new_path!r}")
        return SectionRewrite(old_path="/dev/null", new_path=target, target_path=target)

    if section.is_deleted_file:
        target = _select_candidate(repo_root, _candidate_paths(repo_root, section.old_path), prefer_existing_file=True)
        if not target:
            raise RuntimeError(f"Unable to normalize deleted-file path: {section.old_path!r}")
        return SectionRewrite(old_path=target, new_path="/dev/null", target_path=target)

    old_candidates = _candidate_paths(repo_root, section.old_path)
    new_candidates = _candidate_paths(repo_root, section.new_path)
    target = _select_candidate(repo_root, [*new_candidates, *old_candidates], prefer_existing_file=True)
    if not target:
        raise RuntimeError(f"Unable to normalize diff path: old={section.old_path!r} new={section.new_path!r}")
    return SectionRewrite(old_path=target, new_path=target, target_path=target)


def _rewrite_patch_headers(normalized_text: str, rewrites: Sequence[SectionRewrite]) -> str:
    lines = normalized_text.splitlines()
    out: List[str] = []
    section_index = -1
    current: Optional[SectionRewrite] = None

    for line in lines:
        if line.startswith("diff --git "):
            section_index += 1
            if section_index >= len(rewrites):
                raise RuntimeError("Patch contains more diff sections than expected.")
            current = rewrites[section_index]
            out.append(_diff_header_for_section(current))
            continue
        if line.startswith("--- "):
            if current is None:
                section_index += 1
                if section_index >= len(rewrites):
                    raise RuntimeError("Patch contains more file headers than expected.")
                current = rewrites[section_index]
            out.append(f"--- {_header_path_for_side(current.old_path, side='a')}")
            continue
        if line.startswith("+++ "):
            if current is None:
                section_index += 1
                if section_index >= len(rewrites):
                    raise RuntimeError("Patch contains more file headers than expected.")
                current = rewrites[section_index]
            out.append(f"+++ {_header_path_for_side(current.new_path, side='b')}")
            continue
        out.append(line)

    rewritten = "\n".join(out)
    if not rewritten.endswith("\n"):
        rewritten += "\n"
    return rewritten


def normalize_patch_for_repo(repo_root: Path, patch_text: str) -> NormalizedPatch:
    normalized_text = normalize_diff_text(patch_text)
    sections = parse_unified_diff(normalized_text)
    rewrites = [_normalize_section_paths(repo_root, section) for section in sections]
    rewritten = _rewrite_patch_headers(normalized_text, rewrites)
    parsed_again = parse_unified_diff(rewritten)
    target_files = [section.target_path for section in parsed_again]
    return NormalizedPatch(content=rewritten, target_files=target_files, rewrites=rewrites)


def _split_patch_by_file(patch_text: str) -> List[Tuple[str, str]]:
    lines = patch_text.splitlines()
    chunks: List[Tuple[str, List[str]]] = []
    current_lines: List[str] = []
    current_path: Optional[str] = None

    def flush() -> None:
        nonlocal current_lines, current_path
        if current_path is not None and current_lines:
            chunks.append((current_path, list(current_lines)))
        current_lines = []
        current_path = None

    for line in lines:
        if line.startswith("diff --git "):
            flush()
            parts = line.split()
            current_path = parts[3][2:] if len(parts) >= 4 and parts[3].startswith("b/") else (parts[3] if len(parts) >= 4 else "UNKNOWN")
            current_lines = [line]
            continue
        if current_lines:
            current_lines.append(line)
    flush()
    return [(path, "\n".join(chunk) + "\n") for path, chunk in chunks]


def _classify_check_failure(path: str, detail: str, *, repo_root: Path) -> Tuple[str, str, str]:
    lowered = detail.lower()
    if "already exists in working directory" in lowered:
        return (
            "blocked",
            "new file already exists",
            "Remove or rename the existing file, or regenerate the patch against the current repo state.",
        )
    if "does not exist in index" in lowered:
        return (
            "blocked",
            "base file is missing from Git index",
            "Regenerate the patch from the same branch/commit, or check whether the file was renamed or removed.",
        )
    if "no such file or directory" in lowered:
        return (
            "blocked",
            "target file is missing",
            "Check whether the path is wrong, or regenerate the patch after updating path references.",
        )
    if "patch does not apply" in lowered or "patch failed" in lowered or "while searching for" in lowered:
        return (
            "blocked",
            "file changed since patch was created",
            "Regenerate the patch against the current file contents, or apply the change manually.",
        )
    if "outside repository" in lowered or "unsafe path" in lowered or "invalid path" in lowered:
        return (
            "blocked",
            "patch points outside the repository",
            "Fix the patch paths so they resolve inside the repo root.",
        )
    if not (repo_root / path).exists():
        return (
            "blocked",
            "target file is missing",
            "Check whether the path is wrong, or regenerate the patch after updating path references.",
        )
    if "corrupt patch" in lowered or "malformed patch" in lowered or "bad git-diff" in lowered:
        return (
            "blocked",
            "patch format is malformed",
            "Regenerate the patch in standard unified-diff format.",
        )
    if "cannot apply binary patch" in lowered:
        return (
            "blocked",
            "binary patch is not supported",
            "Export the change as text diff or update the file manually.",
        )
    return (
        "blocked",
        "Git could not apply this patch cleanly",
        "Inspect the verbose output or regenerate the patch from the current repo state.",
    )


def _run_patch_check(repo_root: Path, patch_text: str) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".diff", delete=False) as handle:
        handle.write(patch_text)
        patch_path = Path(handle.name)
    try:
        return _run(
            ["git", "apply", "--check", "--recount", "--inaccurate-eof", "--unidiff-zero", str(patch_path)],
            cwd=repo_root,
        )
    finally:
        patch_path.unlink(missing_ok=True)


def _build_diagnosis(repo_root: Path, normalized: NormalizedPatch) -> Dict[str, object]:
    by_path: Dict[str, SectionRewrite] = {item.target_path: item for item in normalized.rewrites}
    items: List[FileDiagnosis] = []
    rewritten_paths: List[Dict[str, str]] = []

    for target_path, chunk in _split_patch_by_file(normalized.content):
        rewrite = by_path.get(target_path)
        rewritten_from: Optional[str] = None
        if rewrite is not None:
            raw_source = rewrite.new_path if rewrite.new_path not in {None, "/dev/null"} else rewrite.old_path
            if raw_source and raw_source != target_path:
                rewritten_from = raw_source
        cp = _run_patch_check(repo_root, chunk)
        if cp.returncode == 0:
            status = "clean"
            reason = "clean"
            user_message = "patch would apply cleanly"
            action_hint = ""
            detail = "patch would apply cleanly"
        else:
            detail = (cp.stderr or cp.stdout or "git apply --check failed").strip()
            status, user_message, action_hint = _classify_check_failure(target_path, detail, repo_root=repo_root)
            reason = user_message
        items.append(
            FileDiagnosis(
                path=target_path,
                status=status,
                reason=reason,
                user_message=user_message,
                action_hint=action_hint,
                detail=detail,
                rewritten_from=rewritten_from,
            )
        )
        if rewritten_from:
            rewritten_paths.append({"from": rewritten_from, "to": target_path})

    clean = [item for item in items if item.status == "clean"]
    blocked = [item for item in items if item.status != "clean"]
    return {
        "ok": not blocked,
        "files": [item.__dict__ for item in items],
        "summary": {
            "total_files": len(items),
            "clean_files": len(clean),
            "blocked_files": len(blocked),
        },
        "rewritten_paths": rewritten_paths,
    }


def _print_human_report(report: Dict[str, object], *, verbose: bool, applied: bool = False, dropped: bool = False) -> None:
    summary = dict(report.get("summary") or {})
    rewritten = list(report.get("rewritten_paths") or [])
    files = [dict(item) for item in list(report.get("files") or [])]
    blocked = [item for item in files if str(item.get("status")) != "clean"]
    clean = [item for item in files if str(item.get("status")) == "clean"]
    status = "ready" if not blocked else "failed"
    if applied:
        status = "applied"
    elif dropped:
        status = "dropped"
    print(f"Patch diagnosis: {status}")
    print("")
    if rewritten:
        print("Rewritten paths:")
        for item in rewritten:
            print(f"- {item['from']} -> {item['to']}")
        print("")
    if blocked:
        print("Blocked files:")
        for item in blocked:
            line = f"- {item['path']}: {item['user_message']}"
            if verbose:
                line += f" ({item['detail']})"
            print(line)
            hint = str(item.get("action_hint") or "").strip()
            if hint:
                print(f"  next: {hint}")
        print("")
    if clean:
        print(f"Clean files: {len(clean)}")
        if verbose:
            for item in clean:
                print(f"- {item['path']}: clean")
        print("")
    print("Outcome:")
    print(f"- {summary.get('clean_files', 0)} files would apply cleanly" if not applied else f"- {summary.get('clean_files', 0)} files applied cleanly")
    print(f"- {summary.get('blocked_files', 0)} files are blocked")
    if not applied and not dropped:
        print("- patch was not applied")
    elif applied:
        print("- patch was applied")
    else:
        print("- patch was dropped")


def _confirm_apply() -> bool:
    try:
        answer = input("Proceed to apply the patch? [Y/n] ").strip().lower()
    except EOFError:
        return False
    return answer in {"", "y", "yes"}


def _post_apply_verify_targets(changed_files: Sequence[str]) -> List[str]:
    targets: List[str] = []
    for item in changed_files:
        path = str(item or "").strip()
        if not path:
            continue
        if path.endswith(".py") or path.endswith(".sh"):
            targets.append(path)
    return list(dict.fromkeys(targets))


def _run_post_apply_verify(repo_root: Path, changed_files: Sequence[str]) -> Dict[str, object]:
    targets = _post_apply_verify_targets(changed_files)
    if not targets:
        return {"ok": True, "ran": False, "targets": [], "stdout": "", "stderr": "", "returncode": 0}
    cmd = [sys.executable, "-m", "tools.scripts.verify_syntax", "--fix"]
    for target in targets:
        cmd.extend(["--file", target])
    cp = subprocess.run(cmd, cwd=str(repo_root), text=True, capture_output=True, check=False)
    return {
        "ok": cp.returncode == 0,
        "ran": True,
        "targets": targets,
        "stdout": cp.stdout,
        "stderr": cp.stderr,
        "returncode": cp.returncode,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.git.support.git_apply",
        description="Normalize, validate, dry-run, and apply a unified diff to the current git repo.",
    )
    parser.add_argument("diff_file", help="Path to the diff/patch file to apply.")
    parser.add_argument("--yes", action="store_true", help="Apply without interactive confirmation.")
    parser.add_argument("--verbose", action="store_true", help="Show raw per-file git apply details.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    mode: Optional[str] = None
    if raw_args and raw_args[0] in {"check", "apply"}:
        mode = raw_args[0]
        raw_args = raw_args[1:]
    parser = build_arg_parser()
    args = parser.parse_args(raw_args)

    repo_root = _repo_root()
    diff_path = Path(args.diff_file).expanduser().resolve()
    if not diff_path.exists() or not diff_path.is_file():
        print(f"ERROR: diff file not found: {diff_path}", file=sys.stderr)
        return 2

    try:
        raw_patch = diff_path.read_text(encoding="utf-8")
        normalized = normalize_patch_for_repo(repo_root, raw_patch)
        preflight_validated_patch(
            repo_root=str(repo_root),
            patch_text=normalized.content,
            expected_target_files=normalized.target_files,
        )
    except (OSError, PipelineError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    report = _build_diagnosis(repo_root, normalized)
    report["target_files"] = list(normalized.target_files)

    mode = mode or "prompt"
    if mode == "check":
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_human_report(report, verbose=args.verbose)
        return 0 if bool(report.get("ok")) else 1

    if not bool(report.get("ok")):
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_human_report(report, verbose=args.verbose)
        return 1

    if mode == "prompt" and not args.yes:
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_human_report(report, verbose=args.verbose)
            print("")
        if not _confirm_apply():
            if args.json:
                dropped = dict(report)
                dropped["applied"] = False
                dropped["dropped"] = True
                print(json.dumps(dropped, indent=2, sort_keys=True))
            else:
                _print_human_report(report, verbose=args.verbose, dropped=True)
            return 0

    result_payload = apply_unified_diff_to_workspace(
        repo_root=str(repo_root),
        workspace_path=str(repo_root),
        patch_text=normalized.content,
        target_files=normalized.target_files,
        git_path="git",
        timeout_seconds=180,
        git_apply_args=["--recount", "--inaccurate-eof", "--unidiff-zero"],
    )
    if str(result_payload.get("apply_status") or "") != "applied_cleanly":
        detail = str((result_payload.get("diagnostics") or {}).get("failure_detail") or result_payload.get("logs") or "git apply failed")
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1

    applied_report = dict(report)
    applied_report["applied"] = True
    applied_report["files_changed"] = list(result_payload.get("changed_files") or [])
    post_apply_verify = _run_post_apply_verify(repo_root, list(result_payload.get("changed_files") or []))
    applied_report["post_apply_verify"] = post_apply_verify
    if args.json:
        print(json.dumps(applied_report, indent=2, sort_keys=True))
    else:
        _print_human_report(applied_report, verbose=args.verbose, applied=True)
        if post_apply_verify.get("ran"):
            print("")
            print("Post-apply syntax verification:")
            for target in list(post_apply_verify.get("targets") or []):
                print(f"- {target}")
            stdout_text = str(post_apply_verify.get("stdout") or "")
            stderr_text = str(post_apply_verify.get("stderr") or "")
            if stdout_text:
                print(stdout_text.rstrip())
            if stderr_text:
                print(stderr_text.rstrip(), file=sys.stderr)
    if not bool(post_apply_verify.get("ok")):
        print("ERROR: patch applied, but post-apply syntax verification failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
