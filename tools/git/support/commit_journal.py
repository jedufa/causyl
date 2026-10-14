"""Append-only commit journal for agent-authored change fragments.

This module stores small immutable fragment files under
``runtime/state/commit_journal`` and composes them into a commit message on
demand. The journal is operational state, not versioned source, so consuming
entries does not dirty the worktree.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


SCHEMA_ENTRY = "commit_journal_entry_v1"
SCHEMA_PLAN = "commit_journal_compose_plan_v1"
SCHEMA_RECEIPT = "commit_journal_receipt_v1"
ENTRY_OUTCOMES = {"applied", "partial", "note_only"}
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{6,128}$")
_JOURNAL_RUNTIME_PREFIX = "runtime/state/commit_journal/"
_WORKTREE_NOISE_PREFIXES = (
    ".pytest_tmp/",
    ".tooling/",
    "governance/agents/runtime/",
    "governance/agents/sessions/",
    "infra/mobile/android/.gradle/",
    "runtime/state/",
)
_LABEL_PRIORITY = {
    "host verification flow": 0,
    "web server flow": 0,
    "git/journal tooling": 0,
    "Android app logic": 1,
    "tooling launcher": 1,
    "agent prompt contract": 1,
    "Android app config": 2,
    "mobile code": 2,
    "test coverage": 3,
    "documentation": 4,
    "Android project docs": 4,
    "governance docs": 4,
    "ledger records": 6,
    "repo code": 5,
}
_LABEL_SUBJECT_PHRASES = {
    "host verification flow": "mobile host verification",
    "web server flow": "mobile plane",
    "git/journal tooling": "journal git flow",
    "Android app logic": "Android admin tooling",
    "Android app config": "Android app config",
    "tooling launcher": "Android startup env",
    "agent prompt contract": "mobile agent prompts",
    "test coverage": "verification coverage",
    "documentation": "operator docs",
    "Android project docs": "Android docs",
    "mobile code": "mobile app code",
    "governance docs": "governance docs",
    "ledger records": "ledger records",
    "repo code": "repo code",
}
_STRONG_SUBJECT_PHRASES = {
    "mobile host verification",
    "mobile plane",
    "journal git flow",
    "Android admin tooling",
    "Android startup env",
}
_GENERIC_ENTRY_TITLE_RE = re.compile(r"^(update|fix|changes?|misc|cleanup|refactor|wip|work)\b", re.IGNORECASE)


class JournalError(RuntimeError):
    """Raised when journal data is missing or invalid."""


def repo_root() -> Path:
    """Resolve the repository root from this module path."""
    return Path(__file__).resolve().parents[3]


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_compact() -> str:
    """Return a compact UTC timestamp suitable for file names and ids."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ").lower()


def _safe_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "").strip().lower()).strip("-")
    return slug or "entry"


def journal_root(*, repo: Path | None = None) -> Path:
    """Return the runtime commit-journal root."""
    root = (Path(repo or repo_root()).resolve() / "runtime" / "state" / "commit_journal").resolve()
    for child in ("pending", "committed", "failed", "compose"):
        (root / child).mkdir(parents=True, exist_ok=True)
    return root


def _pending_dir(*, repo: Path | None = None) -> Path:
    return (journal_root(repo=repo) / "pending").resolve()


def _committed_dir(*, repo: Path | None = None) -> Path:
    return (journal_root(repo=repo) / "committed").resolve()


def _failed_dir(*, repo: Path | None = None) -> Path:
    return (journal_root(repo=repo) / "failed").resolve()


def _compose_dir(*, repo: Path | None = None) -> Path:
    return (journal_root(repo=repo) / "compose").resolve()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = (path.parent / f".{path.name}.tmp").resolve()
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def _normalize_text_list(values: list[str] | tuple[str, ...] | None, *, field: str, allow_empty: bool = False) -> list[str]:
    out: list[str] = []
    for raw in list(values or []):
        text = str(raw or "").strip()
        if not text:
            continue
        if text not in out:
            out.append(text)
    if not allow_empty and not out:
        raise JournalError(f"{field} must contain at least one non-empty item")
    return out


def _normalize_binding(raw: Any) -> dict[str, str]:
    obj = raw if isinstance(raw, dict) else {}
    binding_type = str(obj.get("type") or "").strip()
    binding_id = str(obj.get("id") or "").strip()
    if not binding_type and not binding_id:
        return {"type": "", "id": ""}
    if binding_type not in {"task_id", "ledger_uid"}:
        raise JournalError("binding.type must be task_id or ledger_uid when provided")
    if not binding_id:
        raise JournalError("binding.id is required when binding.type is provided")
    return {"type": binding_type, "id": binding_id}


def _entry_digest(entry: dict[str, Any]) -> str:
    payload = dict(entry)
    payload.pop("entry_digest", None)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _entry_id_from_pending_path(path: Path) -> str:
    stem = str(path.stem or "").strip()
    if not _ID_RE.match(stem):
        raise JournalError(f"pending fragment filename does not encode a valid entry id: {path.name}")
    return stem


def normalize_entry(obj: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one commit-journal fragment."""
    if not isinstance(obj, dict):
        raise JournalError("entry must be an object")
    created_utc = str(obj.get("created_utc") or "").strip() or utc_now_iso()
    title = str(obj.get("title") or "").strip()
    if not title:
        raise JournalError("title is required")
    entry_id = str(obj.get("entry_id") or "").strip() or f"cjn_{utc_compact()}__{_safe_slug(title)[:48]}"
    if not _ID_RE.match(entry_id):
        raise JournalError("entry_id must match ^[A-Za-z0-9._-]{6,128}$")
    outcome = str(obj.get("outcome") or "applied").strip().lower()
    if outcome not in ENTRY_OUTCOMES:
        raise JournalError(f"outcome must be one of: {', '.join(sorted(ENTRY_OUTCOMES))}")
    author = obj.get("author") if isinstance(obj.get("author"), dict) else {}
    normalized = {
        "schema_version": SCHEMA_ENTRY,
        "entry_id": entry_id,
        "created_utc": created_utc,
        "author": {
            "role": str(author.get("role") or obj.get("agent_role") or "").strip(),
            "source": str(author.get("source") or obj.get("source") or "codex").strip(),
            "binding": _normalize_binding(author.get("binding") if isinstance(author.get("binding"), dict) else obj.get("binding")),
        },
        "title": title,
        "summary_bullets": _normalize_text_list(obj.get("summary_bullets"), field="summary_bullets"),
        "files": _normalize_text_list(obj.get("files"), field="files", allow_empty=True),
        "validation": _normalize_text_list(obj.get("validation"), field="validation", allow_empty=True),
        "notes": _normalize_text_list(obj.get("notes"), field="notes", allow_empty=True),
        "outcome": outcome,
        "scope_hint": str(obj.get("scope_hint") or "").strip(),
    }
    normalized["entry_digest"] = _entry_digest(normalized)
    return normalized


def write_entry(obj: dict[str, Any], *, repo: Path | None = None) -> Path:
    """Write one normalized fragment into the pending journal."""
    normalized = normalize_entry(obj)
    out_path = (_pending_dir(repo=repo) / f"{normalized['entry_id']}.json").resolve()
    if out_path.exists():
        raise JournalError(f"entry already exists: {out_path.name}")
    _atomic_write_json(out_path, normalized)
    return out_path


def _load_entry(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - json parser details are not important
        return None, f"invalid JSON: {exc}"
    if not isinstance(raw, dict):
        return None, "entry must be an object"
    if not str(raw.get("entry_id") or "").strip():
        try:
            raw = dict(raw)
            raw["entry_id"] = _entry_id_from_pending_path(path)
        except Exception as exc:
            return None, str(exc)
    try:
        normalized = normalize_entry(raw)
    except Exception as exc:
        return None, str(exc)
    current_digest = str(raw.get("entry_digest") or "").strip()
    if current_digest and current_digest != normalized["entry_digest"]:
        return None, "entry_digest does not match entry content"
    return normalized, None


def scan_pending_entries(*, repo: Path | None = None) -> dict[str, Any]:
    """Scan pending fragments and report valid/invalid entries."""
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    seen_ids: dict[str, Path] = {}
    for path in sorted(_pending_dir(repo=repo).glob("*.json")):
        entry, error = _load_entry(path)
        if error:
            invalid.append({"path": str(path), "error": error})
            continue
        assert entry is not None
        prev = seen_ids.get(entry["entry_id"])
        if prev is not None:
            invalid.append(
                {
                    "path": str(path),
                    "error": f"duplicate entry_id {entry['entry_id']} also used by {prev}",
                }
            )
            continue
        seen_ids[entry["entry_id"]] = path
        valid.append({"path": str(path), "entry": entry})
    valid.sort(key=lambda item: (str(item["entry"].get("created_utc") or ""), str(item["entry"].get("entry_id") or "")))
    return {
        "ok": not invalid,
        "valid": valid,
        "invalid": invalid,
        "pending_count": len(valid),
        "invalid_count": len(invalid),
    }


def quarantine_invalid_entries(*, repo: Path | None = None) -> list[dict[str, Any]]:
    """Move malformed pending fragments into the failed area with reason files."""
    report = scan_pending_entries(repo=repo)
    moved: list[dict[str, Any]] = []
    invalid_dir = (_failed_dir(repo=repo) / "invalid").resolve()
    invalid_dir.mkdir(parents=True, exist_ok=True)
    for item in report["invalid"]:
        src = Path(str(item["path"])).resolve()
        if not src.exists():
            continue
        stamp = utc_compact()
        dst = (invalid_dir / f"{stamp}__{src.name}").resolve()
        shutil.move(str(src), str(dst))
        reason_path = (invalid_dir / f"{dst.name}.reason.json").resolve()
        _atomic_write_json(
            reason_path,
            {
                "schema_version": "commit_journal_invalid_fragment_v1",
                "moved_utc": utc_now_iso(),
                "original_path": str(src),
                "quarantined_path": str(dst),
                "error": str(item["error"] or "").strip(),
            },
        )
        moved.append(
            {
                "path": str(dst),
                "reason_path": str(reason_path),
                "error": str(item["error"] or "").strip(),
                "original_path": str(src),
            }
        )
    return moved


def invalid_fragment_targets(
    invalid: list[dict[str, Any]] | None,
    *,
    repo: Path | None = None,
    limit: int = 20,
) -> list[dict[str, str]]:
    """Return bounded, scheduler-safe invalid fragment targets."""
    root = Path(repo or repo_root()).resolve()
    targets: list[dict[str, str]] = []
    for item in list(invalid or []):
        raw_path = str(item.get("path") or "").strip()
        raw_error = str(item.get("error") or "").strip()
        if not raw_path:
            continue
        try:
            resolved = Path(raw_path).resolve()
        except Exception:
            resolved = Path(raw_path)
        rel_path = ""
        with contextlib.suppress(Exception):
            rel_path = resolved.relative_to(root).as_posix()
        target = {
            "path": raw_path,
            "rel_path": rel_path or raw_path,
            "filename": Path(raw_path).name,
            "error": raw_error,
        }
        if target not in targets:
            targets.append(target)
        if len(targets) >= max(1, int(limit)):
            break
    return targets


def _scope_from_entry(entry: dict[str, Any]) -> str:
    explicit = str(entry.get("scope_hint") or "").strip()
    if explicit:
        return explicit
    files = list(entry.get("files") or [])
    if not files:
        return "repo"
    path = str(files[0])
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"mobile", "tools", "governance", "docs", "experiments"}:
        return "/".join(parts[:2])
    return parts[0] if parts else "repo"


def _outcome_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts = {outcome: 0 for outcome in sorted(ENTRY_OUTCOMES)}
    for entry in entries:
        outcome = str(entry.get("outcome") or "applied").strip().lower()
        if outcome not in counts:
            continue
        counts[outcome] += 1
    return {key: value for key, value in counts.items() if value}


def _normalize_rel_path(path: str) -> str:
    text = str(path or "").strip().replace("\\", "/")
    text = re.sub(r"/{2,}", "/", text).strip("/")
    return text


def _is_relevant_worktree_path(path: str) -> bool:
    rel = _normalize_rel_path(path)
    if not rel or rel.startswith(_JOURNAL_RUNTIME_PREFIX):
        return False
    for prefix in _WORKTREE_NOISE_PREFIXES:
        normalized_prefix = prefix.rstrip("/")
        if rel == normalized_prefix or rel.startswith(prefix):
            return False
    if rel.startswith("governance/ledger/patch_instruction_sheets/") and rel.endswith("/events.jsonl"):
        return False
    if "/artifacts/agent_chat/" in rel:
        return False
    return True


def _collect_git_changed_paths(*, repo: Path | None = None) -> list[str]:
    root = Path(repo or repo_root()).resolve()
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=str(root),
        capture_output=True,
        text=False,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise JournalError(f"git status failed while auditing commit journal coverage: {stderr or proc.returncode}")
    raw = proc.stdout.decode("utf-8", errors="replace")
    parts = raw.split("\0")
    changed: list[str] = []
    idx = 0
    while idx < len(parts):
        token = parts[idx]
        idx += 1
        if not token:
            continue
        if len(token) < 4:
            raise JournalError(f"unexpected git status record while auditing journal: {token!r}")
        status = token[:2]
        path = _normalize_rel_path(token[3:])
        if not path:
            continue
        tracked_path = path
        if "R" in status or "C" in status:
            if idx >= len(parts):
                raise JournalError("truncated git status rename/copy record while auditing journal")
            tracked_path = _normalize_rel_path(parts[idx])
            idx += 1
        if tracked_path and tracked_path not in changed:
            changed.append(tracked_path)
    return changed


def audit_worktree_coverage(*, repo: Path | None = None) -> dict[str, Any]:
    report = scan_pending_entries(repo=repo)
    if report["invalid"]:
        details = "; ".join(f"{Path(item['path']).name}: {item['error']}" for item in report["invalid"])
        raise JournalError(f"pending journal contains invalid fragments: {details}")
    all_changed_paths = _collect_git_changed_paths(repo=repo)
    changed_paths = [path for path in all_changed_paths if _is_relevant_worktree_path(path)]
    ignored_paths = [path for path in all_changed_paths if path not in changed_paths]
    covered_paths: list[str] = []
    entry_cover_map: dict[str, list[str]] = {}
    for item in report["valid"]:
        entry = dict(item["entry"])
        entry_paths = [_normalize_rel_path(path) for path in list(entry.get("files") or []) if _normalize_rel_path(path)]
        entry_cover_map[str(entry["entry_id"])] = entry_paths
        for rel in entry_paths:
            if rel not in covered_paths:
                covered_paths.append(rel)
    uncovered = [path for path in changed_paths if path not in covered_paths]
    return {
        "ok": not uncovered,
        "pending_count": report["pending_count"],
        "changed_paths": changed_paths,
        "ignored_paths": ignored_paths,
        "covered_paths": covered_paths,
        "uncovered_paths": uncovered,
        "entry_cover_map": entry_cover_map,
    }


def _scope_from_path(path: str) -> str:
    parts = [part for part in _normalize_rel_path(path).split("/") if part]
    if len(parts) >= 2 and parts[0] in {"mobile", "tools", "governance", "docs", "experiments", "tests"}:
        return "/".join(parts[:2])
    return parts[0] if parts else "repo"


def _path_signal_rank(path: str) -> int:
    label = _path_summary_label(path)
    return _LABEL_PRIORITY.get(label, 99)


def _path_mtime(path: str, *, repo: Path | None = None) -> float:
    rel = _normalize_rel_path(path)
    if not rel:
        return 0.0
    target = (Path(repo or repo_root()).resolve() / rel).resolve()
    try:
        return float(target.stat().st_mtime)
    except OSError:
        return 0.0


def _sort_paths_by_recent_touch(paths: list[str], *, repo: Path | None = None) -> list[str]:
    normalized = [_normalize_rel_path(path) for path in paths if _normalize_rel_path(path)]
    return sorted(
        normalized,
        key=lambda path: (_path_signal_rank(path), -_path_mtime(path, repo=repo), path),
    )


def _fallback_subject_from_paths(paths: list[str]) -> str:
    ranked_paths = _sort_paths_by_recent_touch(paths)
    if not ranked_paths:
        return "Update repo"
    phrase_counts: dict[str, int] = {}
    phrase_priority: dict[str, int] = {}
    phrase_order: dict[str, int] = {}
    for path in ranked_paths:
        label = _path_summary_label(path)
        phrase = _LABEL_SUBJECT_PHRASES.get(label) or label
        if not phrase:
            continue
        phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1
        phrase_priority[phrase] = min(phrase_priority.get(phrase, 99), _LABEL_PRIORITY.get(label, 99))
        phrase_order.setdefault(phrase, len(phrase_order))
    all_ordered = sorted(
        phrase_counts,
        key=lambda phrase: (-phrase_counts[phrase], phrase_priority.get(phrase, 99), phrase_order.get(phrase, 99), phrase),
    )
    preferred = [phrase for phrase in all_ordered if phrase in _STRONG_SUBJECT_PHRASES]
    ordered = list(preferred[:3])
    for phrase in all_ordered:
        if len(ordered) >= 3:
            break
        if phrase not in ordered:
            ordered.append(phrase)
    top = ordered[:3]
    if not top:
        scopes: list[str] = []
        seen: set[str] = set()
        for path in ranked_paths:
            scope = _scope_from_path(path)
            if scope not in seen:
                seen.add(scope)
                scopes.append(scope)
        if not scopes:
            return "Update repo"
        if len(scopes) == 1:
            return f"Update {scopes[0]}"
        return f"Update {scopes[0]} and {scopes[1]}"
    verb = "Harden" if any(phrase in _STRONG_SUBJECT_PHRASES for phrase in top) else "Update"
    if len(top) == 1:
        return f"{verb} {top[0]}"
    if len(top) == 2:
        return f"{verb} {top[0]} and {top[1]}"
    return f"{verb} {top[0]}, {top[1]}, and {top[2]}"


def _text_has_specific_signal(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    words = [part for part in re.split(r"\s+", raw) if part]
    if len(words) >= 6:
        return True
    patterns = (
        r"[`/\\]",
        r"\.[A-Za-z0-9]{1,5}\b",
        r"[A-Za-z_][A-Za-z0-9_]*\(",
        r"[A-Za-z_][A-Za-z0-9_]*::[A-Za-z_][A-Za-z0-9_]*",
        r"[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]+",
        r"\d",
    )
    return any(re.search(pattern, raw) for pattern in patterns)


def _entry_needs_reconstructed_context(entry: dict[str, Any]) -> bool:
    bullets = [str(item).strip() for item in list(entry.get("summary_bullets") or []) if str(item).strip()]
    title = str(entry.get("title") or "").strip()
    if any(_text_has_specific_signal(item) for item in bullets):
        return False
    if bullets and not _GENERIC_ENTRY_TITLE_RE.match(title):
        return False
    return True


def _git_status_for_path(path: str, *, repo: Path | None = None) -> str:
    rel = _normalize_rel_path(path)
    if not rel:
        return ""
    root = Path(repo or repo_root()).resolve()
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "--", rel],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    for line in proc.stdout.splitlines():
        if len(line) >= 3:
            return line[:2]
    return ""


def _git_diff_for_path(path: str, *, repo: Path | None = None) -> str:
    rel = _normalize_rel_path(path)
    if not rel:
        return ""
    root = Path(repo or repo_root()).resolve()
    proc = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--unified=0", "--", rel],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout


def _read_path_head(path: str, *, repo: Path | None = None, max_lines: int = 80) -> str:
    rel = _normalize_rel_path(path)
    if not rel:
        return ""
    target = (Path(repo or repo_root()).resolve() / rel).resolve()
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            lines: list[str] = []
            for _ in range(max_lines):
                line = handle.readline()
                if not line:
                    break
                lines.append(line)
        return "".join(lines)
    except OSError:
        return ""


def _path_summary_label(path: str) -> str:
    rel = _normalize_rel_path(path)
    if rel.startswith("docs/"):
        return "documentation"
    if rel.startswith("governance/agents/prompts/"):
        return "agent prompt contract"
    if rel.startswith("governance/ledger/patch_instruction_sheets/"):
        return "ledger records"
    if rel.startswith("tools/git/"):
        return "git/journal tooling"
    if rel.startswith("infra/host/"):
        return "host verification flow"
    if rel.startswith("infra/webui/"):
        return "web server flow"
    if rel.startswith("infra/bin/"):
        return "tooling launcher"
    if rel.startswith("tests/") or "/src/test/" in rel or "/src/androidTest/" in rel:
        return "test coverage"
    if rel.startswith("infra/mobile/android/app/src/main/java/"):
        return "Android app logic"
    if rel.startswith("infra/mobile/android/app/src/main/"):
        return "Android app config"
    if rel.startswith("infra/mobile/android/"):
        return "Android project docs"
    if rel.startswith("mobile/"):
        return "mobile code"
    if rel.startswith("governance/"):
        return "governance docs"
    return "repo code"


def _change_verb_for_status(status: str, *, path: str, repo: Path | None = None) -> str:
    code = (status or "").strip()
    rel = _normalize_rel_path(path)
    exists = bool(rel) and (Path(repo or repo_root()).resolve() / rel).exists()
    if code == "??":
        return "Add"
    if "D" in code and not exists:
        return "Remove"
    if "A" in code and "M" not in code:
        return "Add"
    return "Update"


_SYMBOL_PATTERNS = (
    re.compile(r"^\s*(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
    re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
    re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+|open\s+|override\s+|suspend\s+|inline\s+|tailrec\s+)*fun\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
    re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
    re.compile(r"^\s*(?:const\s+)?(?:val|var)\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
)


def _normalize_symbol_label(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    for pattern in _SYMBOL_PATTERNS:
        match = pattern.match(raw)
        if match:
            return match.group(1)
    simple_call = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)(?:\(\))?", raw)
    if simple_call:
        return simple_call.group(1)
    scoped_ref = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)+", raw)
    if scoped_ref:
        return raw
    return ""


def _extract_changed_symbols(path: str, *, repo: Path | None = None) -> list[str]:
    diff_text = _git_diff_for_path(path, repo=repo)
    candidates: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            parts = line.split("@@")
            if len(parts) >= 3:
                label = _normalize_symbol_label(parts[2])
                if label and label not in candidates:
                    candidates.append(label)
        if line.startswith("+") and not line.startswith("+++"):
            label = _normalize_symbol_label(line[1:])
            if label and label not in candidates:
                candidates.append(label)
    if candidates:
        return candidates[:3]
    head_text = _read_path_head(path, repo=repo)
    for line in head_text.splitlines():
        label = _normalize_symbol_label(line)
        if label and label not in candidates:
            candidates.append(label)
        if len(candidates) >= 3:
            break
    return candidates[:3]


def _build_reconstructed_path_bullet(path: str, *, repo: Path | None = None) -> str:
    rel = _normalize_rel_path(path)
    if not rel:
        return "Reconstructed change from filtered worktree state"
    verb = _change_verb_for_status(_git_status_for_path(rel, repo=repo), path=rel, repo=repo)
    label = _path_summary_label(rel)
    symbols = _extract_changed_symbols(rel, repo=repo)
    if symbols:
        return f"{verb} {label} in {rel} ({', '.join(symbols)})"
    return f"{verb} {label} in {rel}"


def _build_worktree_fallback_entry(
    uncovered_paths: list[str],
    *,
    ignored_paths: list[str] | None = None,
    repo: Path | None = None,
    full_reconstruction: bool = False,
    generic_enrichment: bool = False,
) -> dict[str, Any] | None:
    paths = _sort_paths_by_recent_touch(uncovered_paths, repo=repo)
    if not paths:
        return None
    bullets: list[str] = []
    for path in paths[:8]:
        bullets.append(_build_reconstructed_path_bullet(path, repo=repo))
    if len(paths) > 8:
        remaining = paths[8:]
        remaining_scopes: list[str] = []
        for path in remaining:
            scope = _scope_from_path(path)
            if scope not in remaining_scopes:
                remaining_scopes.append(scope)
        scope_list = ", ".join(remaining_scopes[:3])
        extra = ""
        if len(remaining_scopes) > 3:
            extra = f", +{len(remaining_scopes) - 3} more scopes"
        bullets.append(
            f"Reconstructed additional coverage for {len(remaining)} more changed files ({scope_list}{extra})"
        )
    notes = [
        (
            "No pending journal fragments were available, so this message was synthesized from filtered changed files."
            if full_reconstruction
            else (
                "These bullets were synthesized from filtered changed files because pending journal fragments were too generic."
                if generic_enrichment
                else "These bullets were synthesized from filtered changed files because pending journal fragments did not name every changed path."
            )
        )
    ]
    if ignored_paths:
        notes.append(
            f"Ignored {len(ignored_paths)} generated/runtime paths while composing the commit summary."
        )
    return normalize_entry(
        {
            "title": _fallback_subject_from_paths(paths),
            "summary_bullets": bullets,
            "files": paths,
            "notes": notes,
            "outcome": "applied",
            "scope_hint": _scope_from_path(paths[0]),
        }
    )


def _subject_from_entries(entries: list[dict[str, Any]]) -> tuple[str, str]:
    if not entries:
        raise JournalError("no entries to compose")
    prioritized = [entry for entry in entries if str(entry.get("outcome") or "applied").strip().lower() != "note_only"] or list(entries)
    if len(prioritized) == 1:
        return str(prioritized[0]["title"]).strip(), "entry_title"
    prioritized_files: list[str] = []
    for entry in prioritized:
        for path in list(entry.get("files") or []):
            rel = _normalize_rel_path(path)
            if rel and rel not in prioritized_files:
                prioritized_files.append(rel)
    counts: dict[str, int] = {}
    ordered_scopes: list[str] = []
    scope_order: dict[str, int] = {}
    for entry in prioritized:
        scope = _scope_from_entry(entry)
        counts[scope] = counts.get(scope, 0) + 1
        if scope not in ordered_scopes:
            scope_order[scope] = len(ordered_scopes)
            ordered_scopes.append(scope)
    ordered_scopes.sort(key=lambda scope: (-counts[scope], scope_order[scope]))
    top = ordered_scopes[:2]
    if len(top) == 1:
        scoped_subject = f"Update {top[0]}"
    else:
        scoped_subject = f"Update {top[0]} and {top[1]}"
    if prioritized_files:
        semantic_subject = _fallback_subject_from_paths(prioritized_files)
        if semantic_subject and semantic_subject != "Update repo":
            return semantic_subject, "file_semantic"
    return scoped_subject, "scope_fallback"


def _render_message(entries: list[dict[str, Any]], *, subject_prefix: str = "", subject_override: str = "") -> str:
    subject, _subject_source = _subject_from_entries(entries)
    override = str(subject_override or "").strip()
    if override:
        subject = override
    prefix = str(subject_prefix or "").strip()
    if prefix:
        subject = f"{prefix} {subject}"
    summary_lines: list[str] = []
    validation_lines: list[str] = []
    note_lines: list[str] = []
    for entry in entries:
        outcome = str(entry.get("outcome") or "applied").strip().lower()
        for bullet in list(entry.get("summary_bullets") or []):
            line = f"- {str(bullet).strip()}"
            target = note_lines if outcome == "note_only" else summary_lines
            if line not in target:
                target.append(line)
        if outcome == "partial":
            line = f"- Partial outcome: {str(entry.get('title') or '').strip()}"
            if line not in note_lines:
                note_lines.append(line)
        elif outcome == "note_only":
            line = f"- Note only: {str(entry.get('title') or '').strip()}"
            if line not in note_lines:
                note_lines.append(line)
        for item in list(entry.get("validation") or []):
            line = f"- {str(item).strip()}"
            if line not in validation_lines:
                validation_lines.append(line)
        for item in list(entry.get("notes") or []):
            line = f"- {str(item).strip()}"
            if line not in note_lines:
                note_lines.append(line)
    if not summary_lines:
        for entry in entries:
            for bullet in list(entry.get("summary_bullets") or []):
                line = f"- {str(bullet).strip()}"
                if line not in summary_lines:
                    summary_lines.append(line)
    if not summary_lines:
        raise JournalError("entries did not provide any summary bullets")
    lines = [subject, ""]
    lines.extend(summary_lines)
    if validation_lines:
        lines.extend(["", "Validation:"])
        lines.extend(validation_lines)
    if note_lines:
        lines.extend(["", "Notes:"])
        lines.extend(note_lines)
    return "\n".join(lines).rstrip() + "\n"


def create_compose_plan(
    *,
    subject_prefix: str = "",
    subject_override: str = "",
    require_full_coverage: bool = False,
    allow_worktree_fallback: bool = False,
    repo: Path | None = None,
) -> dict[str, Any]:
    """Build one compose plan from all valid pending fragments."""
    report = scan_pending_entries(repo=repo)
    if report["invalid"]:
        details = "; ".join(f"{Path(item['path']).name}: {item['error']}" for item in report["invalid"])
        raise JournalError(f"pending journal contains invalid fragments: {details}")
    records = list(report["valid"])
    coverage_report: dict[str, Any] | None = None
    if require_full_coverage or allow_worktree_fallback:
        coverage_report = audit_worktree_coverage(repo=repo)
    if not records and not allow_worktree_fallback:
        raise JournalError("no pending commit journal entries")
    if coverage_report is not None and require_full_coverage and not coverage_report["ok"] and not allow_worktree_fallback:
        if not coverage_report["ok"]:
            missing = ", ".join(list(coverage_report["uncovered_paths"])[:12])
            extra = ""
            if len(list(coverage_report["uncovered_paths"])) > 12:
                extra = f" (+{len(list(coverage_report['uncovered_paths'])) - 12} more)"
            raise JournalError(f"pending journal does not cover all changed files: {missing}{extra}")
    real_entries = [dict(item["entry"]) for item in records]
    entries = list(real_entries)
    synthesized_entries: list[dict[str, Any]] = []
    if allow_worktree_fallback and coverage_report is not None:
        fallback_paths = list(coverage_report["uncovered_paths"])
        full_reconstruction = False
        generic_enrichment = False
        if not records:
            fallback_paths = list(coverage_report["changed_paths"])
            full_reconstruction = True
        elif coverage_report["changed_paths"] and real_entries and all(_entry_needs_reconstructed_context(entry) for entry in real_entries):
            fallback_paths = list(coverage_report["changed_paths"])
            generic_enrichment = True
        if not records and not fallback_paths:
            raise JournalError("no pending commit journal entries and no relevant changed files to reconstruct")
        fallback_entry = _build_worktree_fallback_entry(
            fallback_paths,
            ignored_paths=list(coverage_report.get("ignored_paths") or []),
            repo=repo,
            full_reconstruction=full_reconstruction,
            generic_enrichment=generic_enrichment,
        )
        if fallback_entry is not None:
            entries.append(fallback_entry)
            synthesized_entries.append(
                {
                    "title": str(fallback_entry["title"]),
                    "files": list(fallback_entry.get("files") or []),
                }
            )
    if not entries:
        raise JournalError("no pending commit journal entries")
    resolved_subject_override = str(subject_override or "").strip()
    subject_text, subject_source = _subject_from_entries(entries)
    if not resolved_subject_override and coverage_report is not None and subject_source == "scope_fallback":
        semantic_from_worktree = _fallback_subject_from_paths(list(coverage_report["changed_paths"]))
        if semantic_from_worktree and semantic_from_worktree != "Update repo" and semantic_from_worktree != subject_text:
            resolved_subject_override = semantic_from_worktree
    message = _render_message(entries, subject_prefix=subject_prefix, subject_override=resolved_subject_override)
    plan_id = f"cjp_{utc_compact()}"
    plan = {
        "schema_version": SCHEMA_PLAN,
        "plan_id": plan_id,
        "created_utc": utc_now_iso(),
        "subject_prefix": str(subject_prefix or "").strip(),
        "subject_override": resolved_subject_override,
        "message": message,
        "subject_source": subject_source if not resolved_subject_override else ("subject_override" if str(subject_override or "").strip() else "worktree_semantic"),
        "entries": [
            {
                "entry_id": entry["entry_id"],
                "entry_digest": entry["entry_digest"],
                "path": str(item["path"]),
                "title": entry["title"],
            }
            for item, entry in zip(records, real_entries)
        ],
    }
    if coverage_report is not None:
        plan["coverage"] = {
            "changed_paths": list(coverage_report["changed_paths"]),
            "ignored_paths": list(coverage_report.get("ignored_paths") or []),
            "covered_paths": list(coverage_report["covered_paths"]),
            "uncovered_paths": list(coverage_report["uncovered_paths"]),
        }
    if synthesized_entries:
        plan["synthesized_entries"] = synthesized_entries
    return plan


def write_compose_plan(plan: dict[str, Any], *, path: Path | None = None, repo: Path | None = None) -> Path:
    """Persist one compose plan."""
    out_path = Path(path).resolve() if path else (_compose_dir(repo=repo) / f"{plan['plan_id']}.json").resolve()
    _atomic_write_json(out_path, plan)
    return out_path


def write_latest_compose_outputs(plan: dict[str, Any], *, repo: Path | None = None) -> dict[str, str]:
    """Refresh the stable preview files for the latest composed message."""
    compose_dir = _compose_dir(repo=repo)
    plan_path = (compose_dir / "latest_commit_plan.json").resolve()
    message_path = (compose_dir / "latest_commit_message.txt").resolve()
    _atomic_write_json(plan_path, plan)
    _atomic_write_text(message_path, str(plan.get("message") or ""))
    return {
        "plan_path": str(plan_path),
        "message_path": str(message_path),
    }


def consume_compose_plan(plan_path: Path, *, commit_sha: str, repo: Path | None = None) -> dict[str, Any]:
    """Archive the exact fragments used by one successful commit."""
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if str(plan.get("schema_version") or "") != SCHEMA_PLAN:
        raise JournalError("invalid compose plan schema_version")
    sha = str(commit_sha or "").strip()
    if not sha:
        raise JournalError("commit_sha is required")
    commit_dir = (_committed_dir(repo=repo) / sha).resolve()
    commit_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = (commit_dir / "receipt.json").resolve()
    if receipt_path.exists():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise JournalError(f"existing commit journal receipt is unreadable: {receipt_path.name}: {exc}") from exc
        if str(receipt.get("commit_sha") or "").strip() == sha and str(receipt.get("plan_id") or "").strip() == str(plan.get("plan_id") or "").strip():
            return {
                "ok": True,
                "receipt_path": str(receipt_path),
                "consumed_paths": [str(path) for path in sorted(commit_dir.glob("*.json")) if path.name != "receipt.json"],
                "commit_dir": str(commit_dir),
                "already_consumed": True,
            }
    moves: list[tuple[Path, Path]] = []
    archived_paths: list[str] = []
    for item in list(plan.get("entries") or []):
        src = Path(str(item.get("path") or "")).resolve()
        expected_id = str(item.get("entry_id") or "").strip()
        expected_digest = str(item.get("entry_digest") or "").strip()
        dst = (commit_dir / src.name).resolve()
        if dst.exists():
            entry, error = _load_entry(dst)
            if error:
                raise JournalError(f"archived fragment is invalid during consume recovery: {dst.name}: {error}")
            assert entry is not None
            if entry["entry_id"] != expected_id or entry["entry_digest"] != expected_digest:
                raise JournalError(f"archived fragment does not match compose plan: {dst.name}")
            archived_paths.append(str(dst))
            continue
        if not src.exists():
            raise JournalError(f"pending fragment missing during consume: {src}")
        entry, error = _load_entry(src)
        if error:
            raise JournalError(f"pending fragment became invalid during consume: {src.name}: {error}")
        assert entry is not None
        if entry["entry_id"] != expected_id or entry["entry_digest"] != expected_digest:
            raise JournalError(f"pending fragment changed after compose: {src.name}")
        moves.append((src, dst))
    for src, dst in moves:
        shutil.move(str(src), str(dst))
        archived_paths.append(str(dst))
    receipt = {
        "schema_version": SCHEMA_RECEIPT,
        "commit_sha": sha,
        "consumed_utc": utc_now_iso(),
        "plan_id": str(plan.get("plan_id") or "").strip(),
        "entry_ids": [str(item.get("entry_id") or "").strip() for item in list(plan.get("entries") or [])],
        "entry_count": len(list(plan.get("entries") or [])),
        "message": str(plan.get("message") or ""),
        "compose_plan_path": str((commit_dir / f"{str(plan.get('plan_id') or 'plan')}.json").resolve()),
    }
    _atomic_write_json(Path(receipt["compose_plan_path"]), plan)
    _atomic_write_json(receipt_path, receipt)
    used_plan_path = (_compose_dir(repo=repo) / f"{str(plan.get('plan_id') or 'plan')}__consumed.json").resolve()
    _atomic_write_json(used_plan_path, receipt)
    return {
        "ok": True,
        "receipt_path": str(receipt_path),
        "consumed_paths": archived_paths,
        "commit_dir": str(commit_dir),
    }


def _cmd_record(args: argparse.Namespace) -> int:
    binding_type = str(args.binding_type or "").strip()
    binding_id = str(args.binding_id or "").strip()
    if args.task_id:
        binding_type, binding_id = "task_id", str(args.task_id).strip()
    if args.ledger_uid:
        binding_type, binding_id = "ledger_uid", str(args.ledger_uid).strip()
    entry = {
        "entry_id": str(args.entry_id or "").strip(),
        "title": str(args.title or "").strip(),
        "summary_bullets": list(args.bullet or []),
        "files": list(args.file or []),
        "validation": list(args.validation or []),
        "notes": list(args.note or []),
        "scope_hint": str(args.scope_hint or "").strip(),
        "outcome": str(args.outcome or "applied").strip(),
        "author": {
            "role": str(args.agent_role or "").strip(),
            "source": str(args.source or "codex").strip(),
            "binding": {"type": binding_type, "id": binding_id},
        },
    }
    out_path = write_entry(entry)
    sys.stdout.write(str(out_path) + "\n")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    report = scan_pending_entries()
    entries = [dict(item["entry"]) for item in report["valid"]]
    payload = {
        "schema_version": "commit_journal_status_v1",
        "checked_utc": utc_now_iso(),
        "pending_count": report["pending_count"],
        "invalid_count": report["invalid_count"],
        "outcome_counts": _outcome_counts(entries),
        "pending_entry_ids": [str(item["entry"]["entry_id"]) for item in report["valid"]],
        "invalid": report["invalid"],
    }
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return 0 if report["ok"] else 1


def _cmd_audit(args: argparse.Namespace) -> int:
    report = audit_worktree_coverage()
    payload = {
        "schema_version": "commit_journal_audit_v1",
        "checked_utc": utc_now_iso(),
        "pending_count": report["pending_count"],
        "changed_paths": report["changed_paths"],
        "ignored_paths": report["ignored_paths"],
        "covered_paths": report["covered_paths"],
        "uncovered_paths": report["uncovered_paths"],
        "entry_cover_map": report["entry_cover_map"],
    }
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return 0 if report["ok"] else 1


def _cmd_doctor(args: argparse.Namespace) -> int:
    report = scan_pending_entries()
    entries = [dict(item["entry"]) for item in report["valid"]]
    payload: dict[str, Any] = {
        "schema_version": "commit_journal_doctor_v1",
        "checked_utc": utc_now_iso(),
        "pending_count": report["pending_count"],
        "invalid_count": report["invalid_count"],
        "outcome_counts": _outcome_counts(entries),
        "invalid": report["invalid"],
    }
    if args.quarantine_invalid and report["invalid"]:
        payload["quarantined"] = quarantine_invalid_entries()
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return 0 if report["ok"] else 1


def _render_entries_view(entries: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    total = len(entries)
    for idx, entry in enumerate(entries, start=1):
        author = entry.get("author") if isinstance(entry.get("author"), dict) else {}
        binding = author.get("binding") if isinstance(author.get("binding"), dict) else {}
        role = str(author.get("role") or "").strip() or "unknown"
        source = str(author.get("source") or "").strip() or "unknown"
        binding_type = str(binding.get("type") or "").strip()
        binding_id = str(binding.get("id") or "").strip()
        binding_text = f"{binding_type}:{binding_id}" if binding_type and binding_id else "unbound"
        lines.append(f"[{idx}/{total}] {str(entry.get('title') or '').strip()}")
        lines.append(
            "  created: "
            f"{str(entry.get('created_utc') or '').strip()}  "
            f"outcome: {str(entry.get('outcome') or '').strip()}  "
            f"role: {role}  source: {source}  binding: {binding_text}"
        )
        files = [str(path).strip() for path in list(entry.get("files") or []) if str(path).strip()]
        if files:
            lines.append("  files:")
            for path in files:
                lines.append(f"    - {path}")
        bullets = [str(item).strip() for item in list(entry.get("summary_bullets") or []) if str(item).strip()]
        if bullets:
            lines.append("  summary:")
            for bullet in bullets:
                lines.append(f"    - {bullet}")
        validation = [str(item).strip() for item in list(entry.get("validation") or []) if str(item).strip()]
        if validation:
            lines.append("  validation:")
            for item in validation:
                lines.append(f"    - {item}")
        notes = [str(item).strip() for item in list(entry.get("notes") or []) if str(item).strip()]
        if notes:
            lines.append("  notes:")
            for item in notes:
                lines.append(f"    - {item}")
        lines.append("")
    if not lines:
        return "No pending commit journal entries.\n"
    return "\n".join(lines).rstrip() + "\n"


def _cmd_view(args: argparse.Namespace) -> int:
    report = scan_pending_entries()
    if report["invalid"]:
        details = "; ".join(f"{Path(item['path']).name}: {item['error']}" for item in report["invalid"])
        raise JournalError(f"pending journal contains invalid fragments: {details}")
    entries = [dict(item["entry"]) for item in report["valid"]]
    sys.stdout.write(_render_entries_view(entries))
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    allow_worktree_fallback = bool(args.allow_worktree_fallback)
    if not bool(getattr(args, "journal_only", False)) and not bool(args.require_full_coverage):
        allow_worktree_fallback = True
    try:
        plan = create_compose_plan(
            subject_prefix=str(args.subject_prefix or "").strip(),
            subject_override=str(args.subject_override or "").strip(),
            require_full_coverage=bool(args.require_full_coverage),
            allow_worktree_fallback=allow_worktree_fallback,
        )
    except JournalError as exc:
        fallback_error = "git status failed while auditing commit journal coverage"
        if (
            allow_worktree_fallback
            and not bool(args.allow_worktree_fallback)
            and not bool(args.require_full_coverage)
            and fallback_error in str(exc)
        ):
            plan = create_compose_plan(
                subject_prefix=str(args.subject_prefix or "").strip(),
                subject_override=str(args.subject_override or "").strip(),
                require_full_coverage=False,
                allow_worktree_fallback=False,
            )
        else:
            raise
    plan_path = write_compose_plan(plan, path=Path(args.plan_out).resolve() if args.plan_out else None)
    write_latest_compose_outputs(plan)
    message = str(plan.get("message") or "")
    if args.message_out:
        _atomic_write_text(Path(args.message_out).resolve(), message)
    else:
        sys.stdout.write(message)
    if args.print_plan_path:
        sys.stderr.write(str(plan_path) + "\n")
    return 0


def _cmd_consume(args: argparse.Namespace) -> int:
    result = consume_compose_plan(Path(str(args.plan)).resolve(), commit_sha=str(args.commit_sha or "").strip())
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append-only commit journal for agent-authored fragments.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_record = sub.add_parser("record", help="Append one immutable pending fragment.")
    p_record.add_argument("--entry-id", default="", help="Optional explicit entry id.")
    p_record.add_argument("--title", required=True, help="One-line change title for this fragment.")
    p_record.add_argument("--bullet", action="append", default=[], help="One concrete change bullet. Repeatable.")
    p_record.add_argument("--file", action="append", default=[], help="One touched file path. Repeatable.")
    p_record.add_argument("--validation", action="append", default=[], help="One validation command or note. Repeatable.")
    p_record.add_argument("--note", action="append", default=[], help="One caveat or follow-up note. Repeatable.")
    p_record.add_argument("--agent-role", default="", help="Role that produced the fragment.")
    p_record.add_argument("--source", default="codex", help="Source label for the fragment.")
    p_record.add_argument("--binding-type", default="", help="Optional binding type: task_id or ledger_uid.")
    p_record.add_argument("--binding-id", default="", help="Optional binding id.")
    p_record.add_argument("--task-id", default="", help="Shortcut for binding-type=task_id.")
    p_record.add_argument("--ledger-uid", default="", help="Shortcut for binding-type=ledger_uid.")
    p_record.add_argument("--scope-hint", default="", help="Optional scope hint for subject generation.")
    p_record.add_argument("--outcome", default="applied", choices=sorted(ENTRY_OUTCOMES))
    p_record.set_defaults(func=_cmd_record)

    p_status = sub.add_parser("status", help="Summarize pending journal state.")
    p_status.set_defaults(func=_cmd_status)

    p_audit = sub.add_parser("audit", help="Verify that pending fragments cover the current git worktree changes.")
    p_audit.set_defaults(func=_cmd_audit)

    p_doctor = sub.add_parser("doctor", help="Report invalid fragments and optionally quarantine them.")
    p_doctor.add_argument("--quarantine-invalid", action="store_true", help="Move invalid pending fragments into failed/invalid.")
    p_doctor.set_defaults(func=_cmd_doctor)

    p_view = sub.add_parser("view", help="Render pending journal entries as a human-readable terminal view.")
    p_view.set_defaults(func=_cmd_view)

    p_render = sub.add_parser("render", help="Compose a commit message and compose plan from pending fragments.")
    p_render.add_argument("--subject-prefix", default="", help="Optional prefix to prepend to the commit subject.")
    p_render.add_argument("--subject-override", default="", help="Optional replacement for the composed commit subject.")
    p_render.add_argument("--message-out", default="", help="Optional file path to write the rendered message.")
    p_render.add_argument("--plan-out", default="", help="Optional file path to write the compose plan.")
    p_render.add_argument("--print-plan-path", action="store_true", help="Print the compose-plan path to stderr.")
    p_render.add_argument(
        "--require-full-coverage",
        action="store_true",
        help="Fail if pending fragments do not cover all changed git worktree paths.",
    )
    p_render.add_argument(
        "--allow-worktree-fallback",
        action="store_true",
        help="Synthesize summary bullets for uncovered changed files instead of failing on coverage gaps.",
    )
    p_render.add_argument(
        "--journal-only",
        action="store_true",
        help="Compose strictly from pending journal fragments and disable the default changed-file fallback.",
    )
    p_render.set_defaults(func=_cmd_render)

    p_consume = sub.add_parser("consume", help="Archive the fragments used by a successful commit.")
    p_consume.add_argument("--plan", required=True, help="Compose plan path produced by render.")
    p_consume.add_argument("--commit-sha", required=True, help="Commit SHA that consumed the fragments.")
    p_consume.set_defaults(func=_cmd_consume)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except JournalError as exc:
        sys.stderr.write(f"commit journal error: {exc}\n")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
