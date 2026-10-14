def delete_latest_handoff(*, role: str, binding: Dict[str, Any]) -> Dict[str, Any]:
    rr = _resolve_paths(_normalize_binding(binding))
    _validate_role(role)
    hd = rr["handoff_dir"]
    if not hd.exists() or not hd.is_dir() or hd.is_symlink():
        return {"ok": True, "deleted": False, "reason": "handoff_dir_missing"}
    files = sorted([p for p in hd.glob("*.md") if p.is_file() and not p.is_symlink()])
    if not files:
        return {"ok": True, "deleted": False, "reason": "no_handoff"}
    p = files[-1]
    lock = _file_lock(p)
    with lock:
        if p.exists():
            p.unlink()
    return {"ok": True, "deleted": True, "path": str(p)}


def list_task_ids(*, limit: int = 200) -> Dict[str, Any]:
    root = (_repo_root() / "governance" / "agents" / "sessions").resolve()
    out: List[Dict[str, Any]] = []
    if not root.exists() or not root.is_dir() or root.is_symlink():
        return {"ok": True, "task_ids": []}
    for d in sorted(root.iterdir()):
        try:
            if not d.is_dir() or d.is_symlink():
                continue
            tid = str(d.name or "").strip().lower()
            if not TASK_ID_RE.match(tid):
                continue
            mt = int(d.stat().st_mtime)
            s_count = 0
            i_count = 0
            for role in sorted(ROLE_SET):
                s_count += len(_read_jsonl(_session_file(d, role)))
                i_count += len(_read_jsonl(_inbox_file(d, role)))
            out.append({"task_id": tid, "session_count": s_count, "inbox_count": i_count, "mtime_epoch": mt})
        except Exception:
            continue
    out.sort(key=lambda x: int(x.get("mtime_epoch", 0)), reverse=True)
    lim = max(1, min(2000, int(limit)))
    return {"ok": True, "task_ids": out[:lim]}


def _handoff_header(*, binding: Dict[str, str], role: str, status: str, scope: str, inputs: List[str], outputs: List[str], next_action: str, verify: List[str], notes: str) -> str:
    st = str(status or "READY").strip().upper()
    if st not in HANDOFF_READY_VALUES:
        st = "READY"
    bind_s = f"{binding['type']}:{binding['id']}"
    lines = [
        "HANDOFF: YES",
        f"BINDING: {bind_s}",
        f"ROLE: {role}",
        f"UTC: {_utc_now_iso()}",
        f"STATUS: {st}",
        f"SCOPE: {scope.strip() if scope.strip() else '(not provided)'}",
        "INPUTS:",
    ]
    if inputs:
        for it in inputs:
            lines.append(f"- {it}")
    else:
        lines.append("- (not provided)")
    lines.append("OUTPUTS:")
    if outputs:
        for it in outputs:
            lines.append(f"- {it}")
    else:
        lines.append("- (not provided)")
    lines.append("NEXT_ACTION:")
    lines.append(f"- {next_action.strip() if next_action.strip() else '(not provided)'}")
    lines.append("VERIFY:")
    if verify:
        for it in verify:
            lines.append(f"- {it}")
    else:
        lines.append("- (not provided)")
    lines.append("NOTES:")
    lines.append(f"- {notes.strip() if notes.strip() else '(optional)'}")
    return "\n".join(lines)


def export_handoff(*, role: str, binding: Dict[str, Any], selection: Dict[str, Any], title: str = "", status: str = "READY", scope: str = "", inputs: Optional[List[str]] = None, outputs: Optional[List[str]] = None, next_action: str = "", verify: Optional[List[str]] = None, notes: str = "") -> Dict[str, Any]:
    rr = _resolve_paths(_normalize_binding(binding))
    r = _validate_role(role)
    sfile = _session_file(rr["chat_dir"], r)
    all_rows = _read_jsonl(sfile)
    selected = _select_rows(all_rows, selection)

    bind_short = _safe_name(rr["binding"]["id"])
    fname = f"{_utc_compact()}__{r}__{bind_short}.md"
    out_path = (rr["handoff_dir"] / fname).resolve()
    if rr["binding"]["type"] == "task_id":
        out_path.parent.mkdir(parents=True, exist_ok=True)

    hdr = _handoff_header(
        binding=rr["binding"],
        role=r,
        status=status,
        scope=scope,
        inputs=[str(x) for x in (inputs or []) if str(x).strip()],
        outputs=[str(x) for x in (outputs or []) if str(x).strip()],
        next_action=next_action,
        verify=[str(x) for x in (verify or []) if str(x).strip()],
        notes=notes,
    )
    body_lines = [hdr, "", "---", ""]
    if str(title or "").strip():
        body_lines.append(f"# {str(title).strip()}")
        body_lines.append("")
    body_lines.append("## Curated Transcript Excerpt")
    body_lines.append("")
    for i, row in enumerate(selected):
        body_lines.append(
            f"{i + 1}. [{str(row.get('utc') or '')}] {str(row.get('sender') or 'unknown')}:"
        )
        body_lines.append(str(row.get("content") or ""))
        body_lines.append("")
    txt = "\n".join(body_lines).rstrip() + "\n"

    out_path.write_text(txt, encoding="utf-8")
    sha = _sha256_hex_bytes(txt.encode("utf-8"))

    ledger_event = None
    if rr["binding"]["type"] == "ledger_uid":
        ledger = ChangeLedger(repo_root=_repo_root())
        rel = out_path.relative_to(rr["base_dir"]).as_posix()
        payload = {
            "uid": rr["binding"]["id"],
            "role": r,
            "artifact_path": rel,
            "sha256": sha,
            "created_utc": _utc_now_iso(),
        }
        ledger_event = ledger.append_event(rr["binding"]["id"], event_type="agent_handoff_created", payload=payload)

    return {
        "ok": True,
        "binding": rr["binding"],
        "role": r,
        "artifact_path": str(out_path),
        "artifact_sha256": sha,
        "ledger_event": ledger_event,
    }


def attach_to_ledger(*, task_id: str, ledger_uid: str, merge_strategy: str = "copy") -> Dict[str, Any]:
    tid = str(task_id or "").strip().lower()
    if not TASK_ID_RE.match(tid):
        raise ValueError("task_id must match ^[a-z0-9]{3}$")
    strategy = str(merge_strategy or "copy").strip().lower()
    if strategy not in ("copy", "move"):
        raise ValueError("merge_strategy must be copy or move")

    src = _resolve_paths({"type": "task_id", "id": tid})
    dst = _resolve_paths({"type": "ledger_uid", "id": str(ledger_uid or "").strip()})

    copied: List[str] = []
    moved: List[str] = []
    for role in sorted(ROLE_SET):
        for p in (_session_file(src["chat_dir"], role), _inbox_file(src["chat_dir"], role), _stream_state_file(src["chat_dir"], role)):
            if not p.exists() or not p.is_file() or p.is_symlink():
                continue
            tgt = (dst["chat_dir"] / p.name).resolve()
            tgt.parent.mkdir(parents=True, exist_ok=True)
            locks = _lock_many([p, tgt])
            try:
                if p.name.endswith(".jsonl"):
                    src_lines = _read_lines(p)
                    _append_lines(tgt, src_lines)
                else:
                    shutil.copy2(str(p), str(tgt))
                if strategy == "move":
                    try:
                        p.unlink()
                    except Exception:
                        pass
                    moved.append(p.name)
                else:
                    copied.append(p.name)
            finally:
                for lk in reversed(locks):
                    lk.release()

    src_handoff = src["handoff_dir"]
    if src_handoff.exists() and src_handoff.is_dir() and not src_handoff.is_symlink():
        for p in sorted(src_handoff.glob("*.md")):
            if not p.is_file() or p.is_symlink():
                continue
            tgt = (dst["handoff_dir"] / p.name).resolve()
            tgt.parent.mkdir(parents=True, exist_ok=True)
            final_tgt = tgt
            if final_tgt.exists():
                stem = final_tgt.stem
                suf = final_tgt.suffix
                i = 1
                while final_tgt.exists():
                    final_tgt = (final_tgt.parent / f"{stem}__m{i}{suf}").resolve()
                    i += 1
            locks = _lock_many([p, final_tgt])
            try:
                if strategy == "move":
                    shutil.move(str(p), str(final_tgt))
                    moved.append(f"handoff/{final_tgt.name}")
                else:
                    shutil.copy2(str(p), str(final_tgt))
                    copied.append(f"handoff/{final_tgt.name}")
            finally:
                for lk in reversed(locks):
                    lk.release()

    ptr = (src["base_dir"] / "BOUND_TO_LEDGER_UID.txt").resolve()
    ptr.write_text(f"{str(ledger_uid).strip()}\n", encoding="utf-8")

    return {
        "ok": True,
        "old_binding": {"type": "task_id", "id": tid},
        "new_binding": {"type": "ledger_uid", "id": str(ledger_uid).strip()},
        "merge_strategy": strategy,
        "copied": copied,
        "moved": moved,
        "source_dir": str(src["base_dir"]),
        "target_dir": str(dst["base_dir"]),
    }
