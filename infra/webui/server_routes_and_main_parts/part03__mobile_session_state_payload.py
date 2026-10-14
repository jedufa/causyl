from infra.webui import server_lab_assistant_turns as _lab_turns
from infra.webui.ledger.idea_note_manager import IdeaNoteLedger as _IdeaNoteLedger


def _mobile_turn_stream_state_payload(tid: str) -> Optional[Dict[str, Any]]:
    role = _mobile_role_for_thread_id(tid)
    binding = {"type": "task_id", "id": str(tid)}
    try:
        projection = _lab_turns.recent_turn_projections(
            _repo_root_from_webui(),
            limit=8,
            binding=binding,
            role=role,
        )
    except Exception:
        projection = {}
    rows = list(projection.get("turns") or []) if isinstance(projection, dict) else []
    if rows:
        current = next(
            (
                row for row in rows
                if str(row.get("state") or "").strip().lower() in {"submitted", "running", "awaiting_resume"}
            ),
            rows[0],
        )
        turn_id = str(current.get("turn_id") or "").strip()
        if turn_id:
            try:
                replay = _lab_turns.replay_turn_events(_repo_root_from_webui(), turn_id=turn_id, after_event_seq=0)
            except Exception:
                replay = {}
            state = str(current.get("replay_status") or current.get("state") or "").strip().lower()
            if state in {"submitted", "running", "awaiting_resume", "interrupted"}:
                return {
                    "status": state,
                    "content": str(replay.get("content") or "").strip(),
                    "started_utc": str(current.get("created_utc") or "").strip(),
                    "updated_utc": str(current.get("updated_utc") or "").strip(),
                    "turn_id": turn_id,
                    "continuity_proven": bool(current.get("continuity_proven")),
                    "resume_cursor": dict(current.get("resume_cursor") or {}),
                    "source": "durable_turn_projection",
                }
    return None


def _mobile_session_state_payload(tid: str) -> Dict[str, Any]:
    payload = _mobile_session_history_payload(tid)
    stream_state = _mobile_turn_stream_state_payload(tid)
    return {**payload, "stream_state": stream_state}


def _mobile_backlog_open_statuses() -> set[str]:
    return {
        "draft",
        "ready_for_implement",
        "ready_to_implement",
        "ready",
    }


def _mobile_backlog_release_states() -> set[str]:
    return {"released"}


def _mobile_backlog_group_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"topic", "status"}:
        return mode
    return "status"


def _mobile_backlog_project_topic_tags(item: Dict[str, Any]) -> List[str]:
    topic_tags = item.get("topic_tags")
    tags = item.get("tags")
    source = topic_tags if isinstance(topic_tags, list) else tags if isinstance(tags, list) else []
    return _ledger_schema.normalize_topic_tags(source)


def _mobile_backlog_idea_rows(*, limit: int) -> List[Dict[str, Any]]:
    ledger = _IdeaNoteLedger(repo_root=_repo_root_from_webui())
    out: List[Dict[str, Any]] = []
    for raw in ledger.list_notes(limit=max(1, min(int(limit), 500))):
        item = dict(raw or {})
        status = str(item.get("status") or "").strip().lower()
        if status not in {"draft", "active", "exploring", "parked"}:
            continue
        item["release_state"] = "pre_intent"
        item["topic_tags"] = _mobile_backlog_project_topic_tags(item)
        item["backlog_kind"] = "idea_note"
        out.append(item)
    return out


def _mobile_backlog_group_rows(*, items: List[Dict[str, Any]], group_mode: str) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[str]] = {}
    if group_mode == "topic":
        for item in items:
            uid = str(item.get("uid") or "").strip()
            tags = list(item.get("topic_tags") or [])
            if not uid:
                continue
            if not tags:
                buckets.setdefault("untagged", []).append(uid)
                continue
            for tag in tags:
                buckets.setdefault(str(tag), []).append(uid)
    else:
        for item in items:
            uid = str(item.get("uid") or "").strip()
            if not uid:
                continue
            st = str(item.get("status") or "").strip().lower() or "unknown"
            if st.startswith("ready"):
                key = "ready"
            elif st == "draft":
                key = "draft"
            else:
                key = st
            buckets.setdefault(key, []).append(uid)
    rows: List[Dict[str, Any]] = []
    for key in sorted(buckets.keys()):
        rows.append({"key": key, "count": len(buckets[key]), "uids": buckets[key][:120]})
    rows.sort(key=lambda row: int(row.get("count") or 0), reverse=True)
    return rows


def _mobile_backlog_scheduled_projection(
    *, items: List[Dict[str, Any]]
) -> Dict[str, List[Dict[str, Any]]]:
    by_uid = {str(item.get("uid") or "").strip(): item for item in items if str(item.get("uid") or "").strip()}
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not by_uid:
        return out
    try:
        jobs = _scheduled_jobs.list_jobs()
    except Exception:
        return out
    for job in jobs:
        binding = job.get("binding") if isinstance(job.get("binding"), dict) else {}
        if str(binding.get("type") or "").strip().lower() != "ledger_uid":
            continue
        uid = str(binding.get("id") or "").strip()
        if uid not in by_uid:
            continue
        item_tags = list(by_uid[uid].get("topic_tags") or [])
        row = {
            "job_id": str(job.get("job_id") or ""),
            "job_kind": str(job.get("job_kind") or ""),
            "enabled": bool(job.get("enabled")),
            "due_reason": str((job.get("due") or {}).get("reason") or ""),
            "topic_tags": item_tags,
        }
        out.setdefault(uid, []).append(row)
    return out


def _mobile_backlog_item_payload(limit: int = 250, *, topic_filter: str = "", group_mode: str = "status") -> Dict[str, Any]:
    ledger = ChangeLedger()
    limit_norm = max(1, min(int(limit), 500))
    items = list(ledger.list_items(limit=limit_norm)) + _mobile_backlog_idea_rows(limit=limit_norm)
    topic_filter_norm = str(topic_filter or "").strip().lower()
    if topic_filter_norm:
        normalized_topic = _ledger_schema.normalize_topic_tags([topic_filter_norm])
        topic_filter_norm = str(normalized_topic[0]) if normalized_topic else ""
    group_mode_norm = _mobile_backlog_group_mode(group_mode)
    open_statuses = _mobile_backlog_open_statuses()
    closed_release_states = _mobile_backlog_release_states()
    filtered: List[Dict[str, Any]] = []
    for raw in items:
        item = dict(raw or {})
        item.setdefault("backlog_kind", "pis" if str(item.get("uid") or "").startswith("PIS-") else "idea_note")
        status = str(item.get("status") or "").strip().lower()
        release_state = str(item.get("release_state") or "").strip().lower()
        is_open = status in open_statuses or release_state not in closed_release_states
        if item.get("backlog_kind") == "idea_note":
            is_open = status in {"draft", "active", "exploring", "parked"}
        if not is_open:
            continue
        item["topic_tags"] = _mobile_backlog_project_topic_tags(item)
        if topic_filter_norm and topic_filter_norm not in set(item.get("topic_tags") or []):
            continue
        filtered.append(item)
    topic_catalog: Dict[str, int] = {}
    for item in filtered:
        tags = list(item.get("topic_tags") or [])
        if not tags:
            topic_catalog["untagged"] = topic_catalog.get("untagged", 0) + 1
        for tag in tags:
            topic_catalog[str(tag)] = topic_catalog.get(str(tag), 0) + 1
    topic_catalog_rows = [
        {"tag": tag, "count": int(count)}
        for tag, count in sorted(topic_catalog.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
    ]
    draft_count = sum(1 for item in filtered if str(item.get("status") or "").strip().lower() == "draft")
    ready_count = max(0, len(filtered) - draft_count)
    groups = _mobile_backlog_group_rows(items=filtered, group_mode=group_mode_norm)
    scheduled_bindings = _mobile_backlog_scheduled_projection(items=filtered)
    return {
        "schema_version": "mobile_backlog_v1",
        "utc": _now_utc_iso(),
        "items": filtered,
        "filters": {"topic": topic_filter_norm, "group_mode": group_mode_norm},
        "topic_catalog": topic_catalog_rows,
        "groups": groups,
        "scheduled_bindings": scheduled_bindings,
        "counts": {
            "draft": int(draft_count),
            "ready": int(ready_count),
            "total": int(len(filtered)),
        },
    }




class _RotatingTeeStream:
    """Mirror writes to console and a rotating on-disk log file."""

    def __init__(self, stream: Any, log_path: Path, *, max_bytes: int = 10 * 1024 * 1024, backups: int = 3) -> None:
        self._stream = stream
        self._log_path = Path(log_path).resolve()
        self._max_bytes = max(1024, int(max_bytes))
        self._backups = max(1, int(backups))
        self._fp: Optional[Any] = None
        self._open_log()

    @property
    def encoding(self) -> str:
        return str(getattr(self._stream, "encoding", "utf-8") or "utf-8")

    def _open_log(self) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(self._log_path, "a", encoding="utf-8", buffering=1)

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if self._fp is None:
            self._open_log()
        assert self._fp is not None
        try:
            cur = int(self._fp.tell())
        except Exception:
            cur = int(self._log_path.stat().st_size) if self._log_path.exists() else 0
        if cur + max(0, int(incoming_bytes)) < self._max_bytes:
            return
        try:
            self._fp.close()
        except Exception:
            pass
        self._fp = None
        for i in range(self._backups - 1, 0, -1):
            src = self._log_path.parent / f"{self._log_path.name}.{i}"
            dst = self._log_path.parent / f"{self._log_path.name}.{i + 1}"
            if src.exists():
                os.replace(src, dst)
        if self._log_path.exists():
            os.replace(self._log_path, self._log_path.parent / f"{self._log_path.name}.1")
        self._open_log()

    def write(self, data: str) -> int:
        text = str(data or "")
        try:
            self._stream.write(text)
        except Exception:
            pass
        if text:
            payload = text.encode("utf-8", errors="replace")
            self._rotate_if_needed(len(payload))
            try:
                if self._fp is None:
                    self._open_log()
                assert self._fp is not None
                self._fp.write(text)
            except Exception:
                pass
        return len(text)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass
        try:
            if self._fp is not None:
                self._fp.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return bool(self._stream.isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        return int(self._stream.fileno())


def _install_persistent_stdio_log(cfg: WebUIConfig) -> Path:
    """Route stdout/stderr through a rotating file under runtime/state/webui."""
    log_path = (_webui_runtime_root() / "webui_server.log").resolve()
    if not isinstance(sys.stdout, _RotatingTeeStream):
        sys.stdout = _RotatingTeeStream(sys.stdout, log_path)  # type: ignore[assignment]
    if not isinstance(sys.stderr, _RotatingTeeStream):
        sys.stderr = _RotatingTeeStream(sys.stderr, log_path)  # type: ignore[assignment]
    return log_path








def _extract_flag_value(tokens: List[str], flag: str, *, from_idx: int = 0) -> str:
    toks = [str(x) for x in list(tokens or [])]
    try:
        i = toks.index(str(flag), max(0, int(from_idx)))
        if i >= 0 and i < (len(toks) - 1):
            return str(toks[i + 1] or "").strip()
    except Exception:
        return ""
    return ""


def _module_path_under_repo(repo_root: Path, module: str) -> Path:
    mod = str(module or "").strip()
    return (Path(repo_root) / (mod.replace(".", "/") + ".py")).resolve()
















def _mark_run_root_managed(run_root: Path) -> None:
    """Best-effort: stamp managed marker for a specific run root."""
    rr = Path(run_root).resolve()
    mm = _managed_marker_path(rr)
    mm.parent.mkdir(parents=True, exist_ok=True)
    if mm.exists():
        return
    atomic_write_canonical_json(
        mm,
        {
            "schema_version": "webui_managed_v1",
            "managed": True,
            "origin": "webui",
            "created_at_utc": _now_utc_iso(),
        },
    )


def _collect_managed_payload_roots(cfg: WebUIConfig) -> List[Path]:
    """Return payload roots for managed builder jobs (best-effort)."""
    out: List[Path] = []
    seen: set[str] = set()
    try:
        jobs = _jobreg.reconcile_all(cfg.runs_root)
    except Exception:
        jobs = []
    runs_root_resolved = Path(cfg.runs_root).resolve()
    repo_root = _repo_root_from_webui()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if str(job.get("launch_mode") or "").strip().lower() != "managed":
            continue
        rep_raw = str(job.get("report_dir") or "").strip()
        if not rep_raw:
            continue
        try:
            rep = Path(rep_raw).expanduser().resolve()
        except Exception:
            continue
        safe_rep = _safe_resolve_under(runs_root_resolved, rep) or _safe_resolve_under(repo_root, rep)
        if not safe_rep:
            continue
        payload_root = (safe_rep / "payload").resolve()
        if (not payload_root.exists()) or (not payload_root.is_dir()):
            continue
        key = str(payload_root)
        if key in seen:
            continue
        seen.add(key)
        out.append(payload_root)
    return out


def _run_belongs_to_managed_payload(run_root: Path, payload_roots: List[Path]) -> bool:
    """Return True if run_root is under any managed payload root."""
    try:
        rr = Path(run_root).resolve()
    except Exception:
        return False
    for pr in payload_roots:
        try:
            base = Path(pr).resolve()
        except Exception:
            continue
        if rr == base:
            return True
        try:
            if rr.is_relative_to(base):
                return True
        except Exception:
            continue
    return False


def _read_text_tail(path: Path, *, max_bytes: int = 4096) -> str:
    """Return a best-effort UTF-8 tail from a log file."""
    p = Path(path)
    try:
        size = int(p.stat().st_size)
        start = max(0, size - max(1, int(max_bytes)))
        with p.open("rb") as fh:
            if start > 0:
                fh.seek(start)
            raw = fh.read(max(1, int(max_bytes)))
        txt = raw.decode("utf-8", errors="replace")
        # If we started mid-file, skip a partial first line when possible.
        if start > 0:
            nl = txt.find("\n")
            if nl >= 0:
                txt = txt[nl + 1 :]
        return str(txt).strip()
    except Exception:
        return ""


def _read_json_object(path: Path, *, max_bytes: int = 512 * 1024) -> Dict[str, Any]:
    """Read a small JSON object from disk (best-effort)."""
    p = Path(path)
    try:
        if (not p.exists()) or (not p.is_file()) or p.is_symlink():
            return {}
        if int(p.stat().st_size) > int(max_bytes):
            return {}
        obj = json.loads(p.read_text(encoding="utf-8"))
        return dict(obj) if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _first_present_value(*vals: Any) -> Any:
    """Return first non-empty candidate while preserving numeric zero."""
    for v in vals:
        if v is None:
            continue
        if isinstance(v, str) and (not v.strip()):
            continue
        return v
    return None


def _node_report_rel_to_abs(*, base_dir: Path, raw_path: Any) -> str:
    """Resolve node_report relative paths under report_dir (best-effort, safe)."""
    txt = str(raw_path or "").strip()
    if not txt:
        return ""
    try:
        p = Path(txt)
        if p.is_absolute():
            resolved = p.resolve()
            safe = _safe_resolve_under(base_dir, resolved)
            return str(safe) if safe else ""
        resolved = (base_dir / p).resolve()
        safe = _safe_resolve_under(base_dir, resolved)
        return str(safe) if safe else ""
    except Exception:
        return ""


def _node_report_diagnostics(node_report: Dict[str, Any], *, report_dir: Path) -> Dict[str, Any]:
    """Extract terminal diagnostics from node_report.json jobs list (best-effort)."""
    jobs = node_report.get("jobs")
    if not isinstance(jobs, list):
        return {}

    # Prefer an explicit failed/non-zero record; otherwise fallback to last job.
    best: Optional[Dict[str, Any]] = None
    best_score = -1
    for entry in jobs:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "").strip().lower()
        score = 0
        if status in {"failed", "skipped"}:
            score += 3
        try:
            if int(entry.get("exit_code")) != 0:
                score += 2
        except Exception:
            pass
        if str(entry.get("failure_code") or "").strip():
            score += 1
        if score >= best_score:
            best_score = score
            best = entry

    if not isinstance(best, dict):
        return {}

    exit_code: Optional[int] = None
    try:
        raw_ec = best.get("exit_code")
        if raw_ec is not None and str(raw_ec).strip() != "":
            exit_code = int(raw_ec)
    except Exception:
        exit_code = None

    failure_detail = str(
        _first_present_value(
            best.get("failure_detail"),
            best.get("status_detail"),
            best.get("error"),
            best.get("message"),
            best.get("failure_reason"),
            best.get("fail_summary"),
        )
        or ""
    ).strip()

    return {
        "exit_code": exit_code,
        "failure_code": str(best.get("failure_code") or "").strip(),
        "failure_detail": failure_detail,
        "run_path": _node_report_rel_to_abs(
            base_dir=report_dir,
            raw_path=_first_present_value(
                best.get("run_folder_rel"),
                best.get("run_path_rel"),
                best.get("run_dir_rel"),
            ),
        ),
        "report_path": _node_report_rel_to_abs(
            base_dir=report_dir,
            raw_path=_first_present_value(
                best.get("report_path_rel"),
                best.get("report_rel"),
            ),
        ),
        "log_path": _node_report_rel_to_abs(
            base_dir=report_dir,
            raw_path=_first_present_value(
                best.get("log_path_rel"),
                best.get("stdout_stderr_rel"),
            ),
        ),
    }








def _extract_uid_from_pis_text(text: str) -> str:
    m = _PIS_UID_RE.search(str(text or ""))
    return str(m.group(0)).strip() if m else ""


def _title_from_pis_markdown(text: str) -> str:
    raw = str(text or "")
    if not raw.strip():
        return ""
    parts: list[str] = []
    for ln in raw.splitlines():
        s = str(ln or "").strip()
        if not s:
            continue
        lo = s.lower()
        if lo.startswith("pis uid:") or lo.startswith("uid:"):
            continue
        if s.startswith("```"):
            continue
        s = re.sub(r"^[#>\-\*\d\.\)\s]+", "", s).strip()
        if not s:
            continue
        parts.append(s)
        if len(parts) >= 6:
            break
    txt = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if not txt:
        return ""
    m = re.search(r"([^.!?]{3,}?[.!?])(?:\s|$)", txt)
    if m:
        txt = m.group(1).strip()
    return txt[:180].strip()


def _apply_ledger_status_update(*, uid: str, status: str, note: str) -> tuple[HTTPStatus, dict]:
    st = str(status or "").strip()
    if not st:
        raise ValueError("missing status")
    # Verified must always go through required-evidence gating.
    if st.lower() == "verified":
        payload = _ledger_actions.mark_verified(uid=uid)
        code = HTTPStatus.OK if bool(payload.get("ok")) else HTTPStatus.CONFLICT
        return code, {"schema_version": "ledger_mark_verified_v1", "utc": _now_utc_iso(), **payload}

    ledger = ChangeLedger()
    res = ledger.update_status(uid, st, note=str(note or ""))
    try:
        ledger.append_event(
            uid,
            event_type="ledger_status_changed",
            payload={
                "uid": uid,
                "status": str(res.get("status") or st),
                "reason": "manual_status_update",
                "note": str(note or ""),
                "utc": _now_utc_iso(),
            },
        )
    except Exception:
        pass
    return HTTPStatus.OK, {"schema_version": "ledger_status_v1", "utc": _now_utc_iso(), **res}
