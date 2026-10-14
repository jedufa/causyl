def _apply_mobile_backlog_priority_update(*, uid: str) -> tuple[HTTPStatus, dict]:
    safe_uid = str(uid or "").strip()
    if not safe_uid.startswith("PIS-"):
        raise ValueError("uid must be a PIS item")
    ledger = ChangeLedger()
    res = ledger.prioritize_item(safe_uid)
    try:
        ledger.append_event(
            safe_uid,
            event_type="mobile_backlog_prioritized",
            payload={
                "uid": safe_uid,
                "execution_priority": res.get("execution_priority"),
                "reason": "mobile_execution_pane_select",
                "utc": _now_utc_iso(),
            },
        )
    except Exception:
        pass
    return HTTPStatus.OK, {"schema_version": "mobile_backlog_priority_update_v1", "utc": _now_utc_iso(), **res}


def _web_surface_control_file() -> Path:
    return (_repo_root_from_webui() / "runtime" / "state" / "webui" / "web_surface_control.env").resolve()


def _web_surface_enabled() -> bool:
    path = _web_surface_control_file()
    if not path.exists() or not path.is_file():
        return True
    raw = path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        text = str(line or "").strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key_text = str(key).strip()
        if key_text.startswith("export "):
            key_text = key_text[len("export ") :].strip()
        if key_text == "EIEL_WEB_SURFACE_ENABLED":
            return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
    return True
