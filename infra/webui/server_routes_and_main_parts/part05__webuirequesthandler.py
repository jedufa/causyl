from infra.webui import server_request_security as _request_security

class WebUIRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler (stdlib)."""
    def do_GET(self) -> None:  # noqa: N802
        cfg: WebUIConfig = self.server.state.cfg  # type: ignore[attr-defined]
        if not _request_security.guard_request(self, cfg, "GET", lambda token: _assistant_bridge.validate_control_pane_access_token(_admin_repo_root(), token)):
            return
        state: _ServerState = self.server.state  # type: ignore[attr-defined]

        u = urlparse(self.path)
        path = u.path or "/"
        qs = parse_qs(u.query or "")
        web_surface_enabled = _web_surface_enabled()

        if not web_surface_enabled:
            if path not in {"/api/health"} and not path.startswith("/api/assistant/"):
                _json_response(
                    self,
                    HTTPStatus.NOT_FOUND,
                    {
                        "schema_version": "web_surface_gate_v1",
                        "utc": _now_utc_iso(),
                        "ok": False,
                        "error_code": "web_surface_disabled",
                        "error": "web surface is disabled",
                    },
                )
                return

        # ------------------------------------------------------------------
        # Change Ledger (governance/ledger/patch_instruction_sheets) POST endpoints
        # ------------------------------------------------------------------


        if path.startswith("/api/ledger/item/"):
            rest = path[len("/api/ledger/item/") :].strip("/")

            # /api/ledger/item/<UID>/commits
            if rest.endswith("/commits"):
                uid = rest[:-len("/commits")].strip("/")

                try:
                    lim = int((qs.get("limit") or ["50"])[0] or "50")
                except Exception:
                    lim = 50

                try:
                    ledger = ChangeLedger()
                    commits = ledger.list_commits(uid, limit=lim)

                    _json_response(
                        self,
                        HTTPStatus.OK,
                        {
                            "schema_version": "ledger_commits_v1",
                            "utc": _now_utc_iso(),
                            "uid": uid,
                            "commits": commits,
                        },
                    )

                except FileNotFoundError:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
                except Exception as e:
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})

                return

            # /api/ledger/item/<UID>
            uid = rest

            try:
                ledger = ChangeLedger()
                payload = ledger.read_item(uid)
                payload["schema_version"] = "ledger_item_view_v1"
                payload["utc"] = _now_utc_iso()

                _json_response(self, HTTPStatus.OK, payload)

            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})

            return
        if path.startswith("/api/idea_notes/"):
            uid = path[len("/api/idea_notes/") :].strip("/")
            try:
                from infra.webui.ledger.idea_note_manager import IdeaNoteLedger

                ledger = IdeaNoteLedger()
                payload = ledger.read_note(uid)
                payload["schema_version"] = "idea_note_view_v1"
                payload["utc"] = _now_utc_iso()
                payload["backlog_kind"] = "idea_note"
                _json_response(self, HTTPStatus.OK, payload)
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "idea note not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return
        if _route_dispatch.dispatch_front_door_get(
            routes_module=sys.modules[__name__],
            handler=self,
            state=state,
            cfg=cfg,
            path=path,
            qs=qs,
        ):
            return



        # API.
        if path == "/api/health":
            host_health = _lab_assistant_host_health_payload()
            self_check = _lab_assistant_self_check_payload()
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "utc": _now_utc_iso(),
                    "ui_version": UI_SCHEMA_VERSION,
                    "lab_assistant_host": host_health,
                    "lab_assistant_host_status": str(host_health.get("status") or ""),
                    "lab_assistant_host_mode": str(host_health.get("mode") or ""),
                    "lab_assistant_self_check": self_check,
                    "lab_assistant_self_check_status": str(self_check.get("status") or ""),
                    "lab_assistant_self_check_mode": str(self_check.get("mode") or ""),
                },
            )
            return







        if path == "/api/admin/experiment_control_policy":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                project_id = str((qs.get("project_id") or [""])[0] or "").strip()
                payload = _experiment_control_policy_admin.read_surface(
                    self,
                    cfg,
                    qs,
                    repo_root=_repo_root_from_webui(),
                    project_id=project_id,
                )
                _json_response(self, HTTPStatus.OK, payload)
            except _experiment_control_policy_admin.ExperimentControlPolicyAdminError as exc:
                _json_response(self, exc.status, _experiment_control_policy_admin._status_payload(exc))
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(e)})
            return

        if _route_dispatch.dispatch_assistant_get(
            routes_module=sys.modules[__name__],
            handler=self,
            state=state,
            cfg=cfg,
            path=path,
            qs=qs,
        ):
            return

        if path == "/api/admin/lab_assistant/health":
            if not _admin_guard(self, cfg, qs):
                return
            snapshot_payload = _lab_assistant_diagnostic_snapshot_payload()
            payload = _attach_diagnostic_metadata(
                dict(_lab_assistant_host_health_payload()),
                surface_id="host_health",
                snapshot_payload=snapshot_payload,
            )
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/admin/lab_assistant/self_check":
            if not _admin_guard(self, cfg, qs):
                return
            snapshot_payload = _lab_assistant_diagnostic_snapshot_payload()
            payload = _attach_diagnostic_metadata(
                dict(_lab_assistant_self_check_payload()),
                surface_id="self_check",
                snapshot_payload=snapshot_payload,
            )
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/admin/lab_assistant/state":
            if not _admin_guard(self, cfg, qs):
                return
            snapshot_payload = _lab_assistant_diagnostic_snapshot_payload()
            payload = _attach_diagnostic_metadata(
                dict(_lab_assistant_state_payload()),
                surface_id="assistant_state",
                snapshot_payload=snapshot_payload,
            )
            status_code = HTTPStatus.OK if bool(payload.get("assistant_state")) else HTTPStatus.CONFLICT
            _json_response(self, status_code, payload)
            return

        if path == "/api/admin/lab_assistant/task_register":
            if not _admin_guard(self, cfg, qs):
                return
            snapshot_payload = _lab_assistant_diagnostic_snapshot_payload()
            payload = _attach_diagnostic_metadata(
                dict(_lab_assistant_task_register_payload()),
                surface_id="task_register",
                snapshot_payload=snapshot_payload,
            )
            status_code = HTTPStatus.OK if bool(payload.get("task_register")) else HTTPStatus.CONFLICT
            _json_response(self, status_code, payload)
            return

        if path == "/api/admin/lab_assistant/delegation_policy":
            if not _admin_guard(self, cfg, qs):
                return
            _json_response(self, HTTPStatus.OK, _lab_assistant_delegation_policy_payload())
            return

        if path == "/api/admin/lab_assistant/snapshot":
            if not _admin_guard(self, cfg, qs):
                return
            _json_response(self, HTTPStatus.OK, _lab_assistant_operator_snapshot_payload())
            return

        if path == "/api/admin/lab_assistant/resource_concurrency":
            if not _admin_guard(self, cfg, qs):
                return
            _json_response(self, HTTPStatus.OK, _lab_assistant_resource_concurrency_payload())
            return

        if path == "/api/admin/lab_assistant/web_operations":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                workflow_limit = int((qs.get("workflow_limit") or ["50"])[0] or "50")
            except Exception:
                workflow_limit = 50
            try:
                control_limit = int((qs.get("control_limit") or ["30"])[0] or "30")
            except Exception:
                control_limit = 30
            try:
                payload = _lab_web_ops.operations_console_snapshot(
                    _repo_root_from_webui(),
                    workflow_limit=workflow_limit,
                    control_limit=control_limit,
                )
                _json_response(self, HTTPStatus.OK, payload)
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/admin/lab_assistant/delegation_proof":
            if not _admin_guard(self, cfg, qs):
                return
            snapshot_payload = _lab_assistant_diagnostic_snapshot_payload()
            payload = _attach_diagnostic_metadata(
                dict(_lab_assistant_delegation_proof_payload()),
                surface_id="delegation_proof",
                snapshot_payload=snapshot_payload,
            )
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/admin/lab_assistant/execution_truth_ownership":
            if not _admin_guard(self, cfg, qs):
                return
            snapshot_payload = _lab_assistant_diagnostic_snapshot_payload()
            payload = _attach_diagnostic_metadata(
                dict(_lab_assistant_execution_truth_ownership_payload()),
                surface_id="execution_truth_ownership",
                snapshot_payload=snapshot_payload,
            )
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/admin/lab_assistant/backend_api_boundary":
            if not _admin_guard(self, cfg, qs):
                return
            snapshot_payload = _lab_assistant_diagnostic_snapshot_payload()
            payload = _attach_diagnostic_metadata(
                dict(_lab_assistant_backend_api_boundary_payload()),
                surface_id="backend_api_boundary",
                snapshot_payload=snapshot_payload,
            )
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/admin/lab_assistant/split_topology_contract":
            if not _admin_guard(self, cfg, qs):
                return
            _json_response(self, HTTPStatus.OK, _lab_assistant_split_topology_payload())
            return

        if path == "/api/admin/lab_assistant/full_framework_scenarios":
            if not _admin_guard(self, cfg, qs):
                return
            _json_response(self, HTTPStatus.OK, _lab_assistant_full_framework_scenarios_payload())
            return

        if path == "/api/admin/recursive_planning/inspection":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                from infra.controlplane import recursive_planning_receipts as _recursive_planning_receipts

                repo_root = _repo_root_from_webui()
                plan_id = str((qs.get("plan_id") or [""])[0] or "").strip()
                binding_type = str((qs.get("binding_type") or [""])[0] or "").strip()
                binding_id = str((qs.get("binding_id") or [""])[0] or "").strip()
                limit = int((qs.get("limit") or ["50"])[0] or "50")
                if plan_id:
                    projection = _recursive_planning_receipts.reconcile_plan_state(plan_id, repo_root=repo_root)
                    status_code = HTTPStatus.OK if projection["status"] != "missing" else HTTPStatus.NOT_FOUND
                    _json_response(
                        self,
                        status_code,
                        {
                            "schema_version": "recursive_planning_inspection_view_v1",
                            "utc": _now_utc_iso(),
                            "ok": projection["status"] != "missing",
                            "projection": projection,
                        },
                    )
                    return
                listing = _recursive_planning_receipts.list_plan_projections(
                    repo_root=repo_root,
                    limit=limit,
                    binding_type=binding_type,
                    binding_id=binding_id,
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "recursive_planning_inspection_list_v1",
                        "utc": _now_utc_iso(),
                        **listing,
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/admin/job_budget/jobs":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                from infra.webui import server_job_budget_admin as _job_budget_admin

                limit = int((qs.get("limit") or ["200"])[0] or "200")
                payload = _job_budget_admin.list_budget_jobs(limit=limit)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "admin_job_budget_jobs_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/admin/job_budget/ledger":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                from infra.webui import server_job_budget_admin as _job_budget_admin

                job_uid = str((qs.get("job_uid") or [""])[0] or "").strip()
                if not job_uid:
                    raise ValueError("job_uid is required")
                payload = _job_budget_admin.inspect_job_budget_ledger(job_uid=job_uid)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "admin_job_budget_ledger_v1", "utc": _now_utc_iso(), **payload},
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "job budget ledger not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/runtime_policy":
            _json_response(
                self,                HTTPStatus.OK,
                {
                    "schema_version": "mobile_runtime_policy_v1",
                    "utc": _now_utc_iso(),
                    **_mobile_runtime_policy_payload(),
                },
            )
            return

        if path == "/api/mobile/assistant_shell":
            try:
                agent_key = _coerce_mobile_agent_key(str((qs.get("agent_key") or ["a"])[0] or "a"))
                default_tid = str(_mobile_agent_spec(agent_key).get("thread_id") or "mbl")
                tid = _coerce_mobile_task_id(str((qs.get("thread_id") or [default_tid])[0] or default_tid))
                _json_response(
                    self,
                    HTTPStatus.OK,
                    _mobile_assistant_shell_payload(selected_agent_key=agent_key, selected_tid=tid),
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/chat/session_stats":
            try:
                agent_key = _coerce_mobile_agent_key(str((qs.get("agent_key") or ["a"])[0] or "a"))
                default_tid = str(_mobile_agent_spec(agent_key).get("thread_id") or "mbl")
                tid = _coerce_mobile_task_id(str((qs.get("thread_id") or [default_tid])[0] or default_tid))
                payload = _mobile_session_stats(tid, repo_root=Path(cfg.runs_root).resolve())
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "mobile_chat_session_stats_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/chat/session_history":
            try:
                agent_key = _coerce_mobile_agent_key(str((qs.get("agent_key") or ["a"])[0] or "a"))
                default_tid = str(_mobile_agent_spec(agent_key).get("thread_id") or "mbl")
                tid = _coerce_mobile_task_id(str((qs.get("thread_id") or [default_tid])[0] or default_tid))
                payload = _mobile_session_history_payload(tid, repo_root=Path(cfg.runs_root).resolve())
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "mobile_chat_session_history_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/chat/session_state":
            try:
                agent_key = _coerce_mobile_agent_key(str((qs.get("agent_key") or ["a"])[0] or "a"))
                default_tid = str(_mobile_agent_spec(agent_key).get("thread_id") or "mbl")
                tid = _coerce_mobile_task_id(str((qs.get("thread_id") or [default_tid])[0] or default_tid))
                payload = _mobile_session_state_payload(tid)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "mobile_chat_session_state_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/backlog":
            try:
                lim = int((qs.get("limit") or ["250"])[0] or "250")
            except Exception:
                lim = 250
            try:
                topic = str((qs.get("topic") or [""])[0] or "")
                group_mode = str((qs.get("group") or ["status"])[0] or "status")
                _json_response(
                    self,
                    HTTPStatus.OK,
                    _mobile_backlog_item_payload(limit=lim, topic_filter=topic, group_mode=group_mode),
                )
            except Exception as e:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            return

        if path == "/api/mobile/cockpit":
            try:
                lim = int((qs.get("limit") or ["250"])[0] or "250")
            except Exception:
                lim = 250
            try:
                topic = str((qs.get("topic") or [""])[0] or "")
                group_mode = str((qs.get("group") or ["status"])[0] or "status")
                selected_uids = [str(value or "").strip() for value in list(qs.get("selected_uid") or []) if str(value or "").strip()]
                _json_response(
                    self,
                    HTTPStatus.OK,
                    _mobile_cockpit.cockpit_payload(
                        limit=lim,
                        topic_filter=topic,
                        group_mode=group_mode,
                        selected_uids=selected_uids,
                    ),
                )
            except Exception as e:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            return

        if path == "/api/mobile/backlog/execution_queue":
            try:
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "mobile_execution_queue_status_v1", "utc": _now_utc_iso(), **_execution_queue.status()},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            return

        if path == "/api/mobile/plane/history":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                limit = int((qs.get("limit") or ["100"])[0] or "100")
            except Exception:
                limit = 100
            try:
                payload = _lab_front_door_history_payload(limit=limit)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "mobile_plane_history_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            return

        # ------------------------------------------------------------------
        # Change Ledger endpoints (governance/ledger/patch_instruction_sheets)
        # ------------------------------------------------------------------

        if path == "/api/ledger/items":
            try:
                lim = int((qs.get("limit") or ["200"])[0] or "200")
            except Exception:
                lim = 200
            try:
                ledger = ChangeLedger()
                items = ledger.list_items(limit=lim)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "ledger_items_list_v1",
                        "utc": _now_utc_iso(),
                        "items": items,
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            return

        if path == "/api/ledger/action/status":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                uid = str((qs.get("uid") or [""])[0] or "").strip()
                if not uid:
                    raise ValueError("uid is required")
                job_id = str((qs.get("job_id") or [""])[0] or "").strip()
                limit = int((qs.get("limit") or ["80"])[0] or "80")
                payload = _ledger_actions.action_status(uid=uid, job_id=job_id, limit=limit)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "ledger_action_status_v1", "utc": _now_utc_iso(), **payload},
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/ledger/action/log":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                uid = str((qs.get("uid") or [""])[0] or "").strip()
                job_id = str((qs.get("job_id") or [""])[0] or "").strip()
                lines = int((qs.get("lines") or ["200"])[0] or "200")
                if not uid:
                    raise ValueError("uid is required")
                payload = _ledger_actions.action_log(uid=uid, job_id=job_id, lines=lines)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "ledger_action_log_v1", "utc": _now_utc_iso(), **payload},
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/ledger/workflow/status":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                uid = str((qs.get("uid") or [""])[0] or "").strip()
                if not uid:
                    raise ValueError("uid is required")
                payload = _ledger_actions.workflow_status(uid=uid)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "ledger_workflow_status_v1", "utc": _now_utc_iso(), **payload},
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/prompts":
            try:
                payload = _agents.get_prompts_payload()
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_prompts_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            return

        if path == "/api/agents/session":
            try:
                role = str((qs.get("role") or [""])[0] or "").strip()
                task_id = str((qs.get("task_id") or [""])[0] or "").strip()
                ledger_uid = str((qs.get("ledger_uid") or [""])[0] or "").strip()
                if ledger_uid:
                    binding = {"type": "ledger_uid", "id": ledger_uid}
                elif task_id:
                    binding = {"type": "task_id", "id": task_id}
                else:
                    raise ValueError("either task_id or ledger_uid is required")
                tail = int((qs.get("tail") or ["80"])[0] or "80")
                inbox_tail = int((qs.get("inbox_tail") or ["50"])[0] or "50")
                payload = _agents.read_session(role=role, binding=binding, tail=tail, inbox_tail=inbox_tail)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_session_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "ledger item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/task_ids":
            try:
                limit = int((qs.get("limit") or ["120"])[0] or "120")
                payload = _agents.list_task_ids(limit=limit)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_task_ids_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/idea_notes":
            try:
                lim = int((qs.get("limit") or ["200"])[0] or "200")
            except Exception:
                lim = 200
            try:
                from infra.webui.ledger.idea_note_manager import IdeaNoteLedger

                ledger = IdeaNoteLedger()
                items = ledger.list_notes(limit=lim)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "idea_notes_list_v1",
                        "utc": _now_utc_iso(),
                        "items": items,
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/artifact":
            try:
                role = str((qs.get("role") or [""])[0] or "").strip()
                task_id = str((qs.get("task_id") or [""])[0] or "").strip()
                ledger_uid = str((qs.get("ledger_uid") or [""])[0] or "").strip()
                kind = str((qs.get("kind") or [""])[0] or "").strip()
                if ledger_uid:
                    binding = {"type": "ledger_uid", "id": ledger_uid}
                elif task_id:
                    binding = {"type": "task_id", "id": task_id}
                else:
                    raise ValueError("either task_id or ledger_uid is required")
                art = _agents.resolve_resume_artifact(role=role, binding=binding, kind=kind)
                _file_response(
                    self,
                    HTTPStatus.OK,
                    Path(str(art.get("path") or "")),
                    content_type="text/plain; charset=utf-8",
                    download_name=str(art.get("name") or "artifact.txt"),
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "artifact not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/orchestration/status":
            try:
                workflow_id = str((qs.get("workflow_id") or [""])[0] or "").strip()
                events_limit = int((qs.get("events_limit") or ["120"])[0] or "120")
                payload = _agent_orch.get_status(workflow_id=workflow_id, events_limit=events_limit)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_orchestration_status_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "workflow not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/swarm/status":
            try:
                swarm_id = str((qs.get("swarm_id") or [""])[0] or "").strip()
                events_limit = int((qs.get("events_limit") or ["120"])[0] or "120")
                payload = _agent_swarm.get_status(swarm_id=swarm_id, events_limit=events_limit)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_swarm_status_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "swarm not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/benchmark/status":
            try:
                benchmark_id = str((qs.get("benchmark_id") or [""])[0] or "").strip()
                payload = _agent_benchmark.get_benchmark_status(benchmark_id=benchmark_id)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_benchmark_status_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "benchmark not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/orchestration/list":
            try:
                lim = int((qs.get("limit") or ["100"])[0] or "100")
                bt = str((qs.get("binding_type") or [""])[0] or "").strip()
                bid = str((qs.get("binding_id") or [""])[0] or "").strip()
                binding = {"type": bt, "id": bid} if bt and bid else None
                payload = _agent_orch.list_workflows(binding=binding, limit=lim)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_orchestration_list_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/swarm/list":
            try:
                lim = int((qs.get("limit") or ["100"])[0] or "100")
                bt = str((qs.get("binding_type") or [""])[0] or "").strip()
                bid = str((qs.get("binding_id") or [""])[0] or "").strip()
                binding = {"type": bt, "id": bid} if bt and bid else None
                payload = _agent_swarm.list_swarms(binding=binding, limit=lim)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_swarm_list_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/orchestration/conversation":
            try:
                workflow_id = str((qs.get("workflow_id") or [""])[0] or "").strip()
                events_limit = int((qs.get("events_limit") or ["240"])[0] or "240")
                payload = _agent_orch.get_conversation(workflow_id=workflow_id, events_limit=events_limit)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_orchestration_conversation_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "workflow not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/schedules/health":
            try:
                repo_root = _repo_root_from_webui()
                try:
                    scheduler_health = _scheduler_liveness_payload_or_disabled(repo_root=repo_root)
                except Exception as exc:
                    scheduler_health = {"health": "degraded", "reason": f"self_heal_probe_failed:{type(exc).__name__}", "error": str(exc)}
                try:
                    watchdog_health = _scheduled_jobs.ensure_watchdog_service_liveness(repo_root=repo_root)
                except Exception as exc:
                    watchdog_health = {"health": "degraded", "reason": f"self_heal_probe_failed:{type(exc).__name__}", "error": str(exc)}
                try:
                    healer_health = _scheduled_jobs.ensure_healer_service_liveness(repo_root=repo_root)
                except Exception as exc:
                    healer_health = {"health": "degraded", "reason": f"self_heal_probe_failed:{type(exc).__name__}", "error": str(exc)}
                scheduler_status = _scheduled_jobs.scheduler_service_status()
                watchdog_status = _scheduled_jobs.watchdog_service_status()
                healer_status = _scheduled_jobs.healer_service_status()
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "scheduled_health_v1",
                        "utc": _now_utc_iso(),
                        "scheduler": scheduler_status,
                        "scheduler_health": scheduler_health,
                        "watchdog": watchdog_status,
                        "watchdog_health": watchdog_health,
                        "healer": healer_status,
                        "healer_health": healer_health,
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/schedules/watchdog/latest":
            try:
                status = _scheduled_jobs.watchdog_service_status()
                receipt = status.get("last_receipt", {})
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "scheduled_watchdog_latest_v1",
                        "utc": _now_utc_iso(),
                        **receipt,
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/admin/scheduler_watchdog/status":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "admin_scheduler_watchdog_status_v1",
                        "utc": _now_utc_iso(),
                        **_lab_web_ops.watchdog_snapshot(_repo_root_from_webui()),
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/admin/scheduler_watchdog/latest":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                mode = str((qs.get("mode") or [""])[0] or "").strip()
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "admin_scheduler_watchdog_latest_v1",
                        "utc": _now_utc_iso(),
                        "latest": _lab_web_ops.watchdog_latest_snapshot(_repo_root_from_webui(), mode=mode),
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/admin/scheduler_watchdog/activity":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "admin_scheduler_watchdog_activity_v1",
                        "utc": _now_utc_iso(),
                        "activity": _lab_web_ops.watchdog_activity_snapshot(
                            _repo_root_from_webui(),
                            event_limit=int((qs.get("event_limit") or ["12"])[0] or "12"),
                            control_limit=int((qs.get("control_limit") or ["12"])[0] or "12"),
                            log_lines=int((qs.get("log_lines") or ["80"])[0] or "80"),
                            action_limit=int((qs.get("action_limit") or ["20"])[0] or "20"),
                        ),
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/schedules/healer/latest":
            try:
                status = _scheduled_jobs.healer_service_status()
                receipt = status.get("last_receipt", {})
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "scheduled_healer_latest_v1",
                        "utc": _now_utc_iso(),
                        **receipt,
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/schedules/jobs":
            try:
                _json_response(
                    self,
                    HTTPStatus.OK,
                    _schedules_jobs_payload(repo_root=_repo_root_from_webui(), wait_timeout_s=8.0),
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/schedules/job":
            try:
                job_id = str((qs.get("job_id") or [""])[0] or "").strip()
                if not job_id:
                    raise ValueError("job_id required")
                repo_root = _repo_root_from_webui()
                spec = _scheduled_jobs.load_job(job_id, repo_root=repo_root)
                state_row = _scheduled_jobs.load_state(job_id, repo_root=repo_root)
                job_payload = _scheduled_jobs.job_summary(spec, state=state_row, repo_root=repo_root)
                job_payload["prompt"] = str(spec.get("prompt") or "")
                if isinstance(spec.get("prompt_meta"), dict) and spec.get("prompt_meta"):
                    job_payload["prompt_meta"] = dict(spec.get("prompt_meta") or {})
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "scheduled_job_view_v1",
                        "utc": _now_utc_iso(),
                        "job": job_payload,
                        "scheduler_projection_contract": _scheduler_projection_contract_payload(),
                        "receipts": _scheduled_jobs.list_receipts(job_id, limit=20, repo_root=repo_root),
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "scheduled job not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/schedules/receipt":
            try:
                job_id = str((qs.get("job_id") or [""])[0] or "").strip()
                run_id = str((qs.get("run_id") or [""])[0] or "").strip()
                if not job_id or not run_id:
                    raise ValueError("job_id and run_id required")
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "scheduled_job_receipt_view_v1",
                        "utc": _now_utc_iso(),
                        "receipt": _scheduled_jobs.load_receipt(job_id, run_id),
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "scheduled receipt not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return


        # ------------------------------------------------------------------
        # Admin mobile plane endpoints
        # ------------------------------------------------------------------

        if path == "/api/admin/mobile_apk/manifest":
            if not _mobile_update_guard(self, cfg):
                return
            rr = _admin_repo_root()
            channel = str((qs.get("channel") or [""])[0] or "").strip().lower()
            if channel not in _MOBILE_APK_CHANNEL_META:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "channel must be debug or release"})
                return
            active_job = _mobile_apk_running_build_job(state, channel)
            release = _mobile_apk_find_release(rr, channel)
            latest_job = _mobile_apk_latest_build_job(state, cfg, channel)
            release_integrity = _mobile_apk_release_integrity(rr, channel) if release is not None else {}
            if release is None and active_job is None and latest_job is None:
                _json_response(
                    self,
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": f"apk not found for channel={channel}",
                        "channel": channel,
                    },
                )
                return
            payload: Dict[str, Any] = {
                "schema_version": "mobile_apk_manifest_v1",
                "utc": _now_utc_iso(),
                "channel": channel,
                "release_available": bool(release is not None),
            }
            if active_job is not None:
                payload["active_build_job"] = active_job.to_dict()
            if latest_job is not None:
                payload["latest_build_job"] = latest_job
            if release is not None:
                payload.update(
                    {
                        "channel": release["channel"],
                        "package_name": release["package_name"],
                        "version_name": release["version_name"],
                        "version_code": release["version_code"],
                        "apk_name": release["apk_name"],
                        "apk_size_bytes": release["apk_size_bytes"],
                        "apk_sha256": release["apk_sha256"],
                        "built_at": release["built_at"],
                        "source_path": release["source_path"],
                        "download_path": f"/api/admin/mobile_apk/download?channel={release['channel']}",
                        "release_integrity": release_integrity,
                    }
                )
            _json_response(
                self,
                HTTPStatus.OK,
                payload,
            )
            return

        if path == "/api/admin/mobile_apk/build_status":
            if not _mobile_update_guard(self, cfg):
                return
            try:
                jid = str((qs.get("job_id") or [""])[0] or "").strip()
                if not jid:
                    raise ValueError("missing job_id")
                job_payload = _mobile_apk_job_payload_by_id(state, cfg, jid)
                if job_payload is None:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "job not found"})
                    return
                if not str(job_payload.get("kind") or "").startswith("mobile_apk_build:"):
                    _json_response(self, HTTPStatus.FORBIDDEN, {"error": "job is not a mobile apk build"})
                    return
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "mobile_apk_build_status_v1", "utc": _now_utc_iso(), "job": job_payload},
                )
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        if path == "/api/admin/mobile_apk/cancel":
            if not _mobile_update_guard(self, cfg):
                return
            try:
                status, payload = _mobile_apk_cancel_response_payload(self, state=state, cfg=cfg, qs=qs)
                _json_response(self, status, payload)
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        if path == "/api/admin/mobile_apk/download":
            if not _mobile_update_guard(self, cfg):
                return
            rr = _admin_repo_root()
            channel = str((qs.get("channel") or [""])[0] or "").strip().lower()
            if channel not in _MOBILE_APK_CHANNEL_META:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "channel must be debug or release"})
                return
            release = _mobile_apk_find_release(rr, channel)
            if release is None:
                _json_response(
                    self,
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": f"apk not found for channel={channel}",
                        "channel": channel,
                    },
                )
                return
            _file_response(
                self,
                HTTPStatus.OK,
                Path(release["path"]),
                content_type="application/vnd.android.package-archive",
                download_name=str(release["apk_name"]),
                extra_headers={"X-EIEL-Apk-Sha256": str(release["apk_sha256"])},
            )
            return

        if path == "/api/admin/config":
            if not _admin_guard(self, cfg, qs):
                return
            rr = _admin_repo_root()
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "admin_config_v1",
                    "utc": _now_utc_iso(),
                    "admin_enabled": bool(cfg.admin_enabled),
                    "repo_root": str(rr),
                    "archives_dir": str(_admin_archives_dir(rr)),
                },
            )
            return

        if path == "/api/admin/archive/list":
            if not _admin_guard(self, cfg, qs):
                return
            rr = _admin_repo_root()
            arcs = _admin_list_archives(rr)
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "admin_archives_list_v1",
                    "utc": _now_utc_iso(),
                    "archives": arcs,
                },
            )
            return

        if path == "/api/admin/archive/create":
            if not _admin_guard(self, cfg, qs):
                return
            debug = str((qs.get("debug") or ["0"])[0] or "0").strip().lower() in {"1", "true", "yes"}
            rr = _admin_repo_root()
            zip_path, stdout, stderr, rc = _admin_run_snapshot_script(rr, debug=debug)
            if not zip_path:
                _text_response(
                    self,
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    f"snapshot script failed (rc={rc})\n\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}\n",
                    content_type="text/plain; charset=utf-8",
                )
                return
            _file_response(
                self,
                HTTPStatus.OK,
                zip_path,
                content_type="application/zip",
                download_name=zip_path.name,
            )
            return

        if path.startswith("/api/admin/archive/download/"):
            if not _admin_guard(self, cfg, qs):
                return
            fname = path.split("/api/admin/archive/download/", 1)[1]
            fname = str(fname or "").strip().lstrip("/")
            if not _COMPONENT_RE.match(fname):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid archive name"})
                return
            rr = _admin_repo_root()
            adir = _admin_archives_dir(rr)
            fp = (adir / fname).resolve()
            safe = _safe_resolve_under(adir, fp)
            if not safe or not safe.exists() or not safe.is_file():
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "archive not found"})
                return
            _file_response(self, HTTPStatus.OK, safe, content_type="application/zip", download_name=safe.name)
            return

        if path == "/api/admin/fs/list":
            if not _admin_guard(self, cfg, qs):
                return
            rel = str((qs.get("path") or [""])[0] or "").strip()
            rr = _admin_repo_root()
            root = _admin_fs_resolve(rr, rel)
            if not root or not root.exists() or not root.is_dir() or root.is_symlink():
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "directory not found"})
                return

            include_hidden = str((qs.get("include_hidden") or ["0"])[0] or "0").strip().lower() in {"1", "true", "yes"}

            entries: List[Dict[str, Any]] = []
            try:
                for child in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                    name = child.name
                    if not include_hidden and name.startswith('.'):
                        continue
                    if child.is_symlink():
                        continue
                    try:
                        st = child.stat()
                        entries.append(
                            {
                                "name": name,
                                "path": _posix_relpath(child, rr),
                                "is_dir": bool(child.is_dir()),
                                "bytes": int(st.st_size),
                                "mtime": float(st.st_mtime),
                            }
                        )
                    except Exception:
                        entries.append({"name": name, "path": _posix_relpath(child, rr), "is_dir": bool(child.is_dir()), "bytes": 0, "mtime": 0.0})
            except Exception as e:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"list failed: {e}"})
                return

            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "admin_fs_list_v1",
                    "utc": _now_utc_iso(),
                    "repo_root": str(rr),
                    "dir": _posix_relpath(root, rr),
                    "entries": entries,
                },
            )
            return

        if path == "/api/admin/fs/read":
            if not _admin_guard(self, cfg, qs):
                return
            rel = str((qs.get("path") or [""])[0] or "").strip()
            rr = _admin_repo_root()
            fp = _admin_fs_resolve(rr, rel)
            if not fp or not fp.exists() or not fp.is_file() or fp.is_symlink():
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "file not found"})
                return

            max_bytes = 200_000
            try:
                data = fp.read_bytes()
            except Exception as e:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"read failed: {e}"})
                return

            truncated = False
            if len(data) > max_bytes:
                data = data[:max_bytes]
                truncated = True
            text = data.decode("utf-8", errors="replace")
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "admin_fs_read_v1",
                    "utc": _now_utc_iso(),
                    "path": _posix_relpath(fp, rr),
                    "bytes": int(fp.stat().st_size) if fp.exists() else len(data),
                    "truncated": bool(truncated),
                    "text": text,
                },
            )
            return

        if path == "/api/admin/fs/download":
            if not _admin_guard(self, cfg, qs):
                return
            rel = str((qs.get("path") or [""])[0] or "").strip()
            rr = _admin_repo_root()
            fp = _admin_fs_resolve(rr, rel)
            if not fp or not fp.exists() or not fp.is_file() or fp.is_symlink():
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "file not found"})
                return
            _file_response(self, HTTPStatus.OK, fp, content_type="application/octet-stream", download_name=fp.name)
            return

        if path == "/api/admin/cmd/status":
            if not _admin_guard(self, cfg, qs):
                return
            jid = str((qs.get("job_id") or [""])[0] or "").strip()
            if not jid:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "missing job_id"})
                return
            with state.lock:
                job = state.admin_cmd_jobs.get(jid)
            if not job:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "job not found"})
                return
            job_dict = job.to_dict()
            # If this is a completed git_run job that auto-created a repo snapshot archive,
            # surface a download hint for the UI.
            try:
                if job_dict.get("kind") == "git_run" and (job_dict.get("running") is False) and int(job_dict.get("returncode") or 0) == 0:
                    out = str(job_dict.get("stdout") or "")
                    m = re.search(r"SNAPSHOT_ZIP_PATH=(archives/[^\s]+\.zip)", out)
                    if m:
                        relp = m.group(1)
                        fname = relp.split("/", 1)[-1]
                        if _COMPONENT_RE.match(fname):
                            job_dict["archive"] = {
                                "rel_path": relp,
                                "filename": fname,
                                "download_url": f"/api/admin/archive/download/{fname}",
                            }
            except Exception:
                pass

            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "admin_cmd_status_v1",
                    "utc": _now_utc_iso(),
                    "job": job_dict,
                },
            )
            return

        if path == "/api/admin/cmd/list":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                limit = int((qs.get("limit") or ["50"])[0] or "50")
            except Exception:
                limit = 50
            limit = max(1, min(200, limit))
            with state.lock:
                jobs = list(state.admin_cmd_jobs.values())
            jobs.sort(key=lambda j: float(getattr(j, "t_started", 0.0) or 0.0), reverse=True)
            out = []
            for j in jobs[:limit]:
                out.append(
                    {
                        "job_id": j.job_id,
                        "kind": j.kind,
                        "cmd": j.cmd,
                        "cwd_rel": j.cwd_rel,
                        "utc_started": j.utc_started,
                        "utc_finished": j.utc_finished,
                        "running": bool(j.running),
                        "killed": bool(j.killed),
                        "returncode": None if j.returncode is None else int(j.returncode),
                    }
                )
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "admin_cmd_list_v1",
                    "utc": _now_utc_iso(),
                    "jobs": out,
                },
            )
            return

        # ------------------------------------------------------------------
        # Admin: manifest job control (durable, restart-safe)
        # ------------------------------------------------------------------
        if path == "/api/admin/manifests":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                jobs = _jobreg.reconcile_all(cfg.runs_root)
            except Exception as e:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
                return
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "admin_manifests_list_v1",
                    "utc": _now_utc_iso(),
                    "jobs": jobs,
                },
            )
            return

        if path == "/api/admin/git/status":
            if not _admin_guard(self, cfg, qs):
                return
            rr = _admin_repo_root()
            env = _admin_cmd_env()
            _json_response(self, HTTPStatus.OK, _admin_git_status_payload(repo_root=rr, env=env))
            return

        if path == "/api/changesets":
            cs = list_changesets(cfg.runs_root)
            _json_response(self, HTTPStatus.OK, {"schema_version": "changesets_list_v1", "utc": _now_utc_iso(), "changesets": cs})
            return

        if path == "/api/system/runs":
            # Unified active-runs liveness view for the operational plane runtime.
            try:
                from infra.webui import run_registry as _rr  # noqa: WPS433
            except Exception:
                import infra.webui.run_registry as _rr  # type: ignore  # noqa: WPS433
            regp = _registry_path(cfg)
            reg = _rr.load_registry(regp)
            runs = reg.get("runs") if isinstance(reg.get("runs"), dict) else {}
            hb_stale = int(os.environ.get("WEBUI_HEARTBEAT_STALE_SECONDS", "30") or "30")
            managed_payload_roots = _collect_managed_payload_roots(cfg)
            managed: List[Dict[str, Any]] = []
            for rk2, rec in runs.items():
                if not isinstance(rec, dict):
                    continue
                run_dir = Path(str(rec.get("run_dir") or ""))
                if not run_dir.exists():
                    continue
                _rrk = str(rec.get("run_key") or rk2)
                pid = int(rec.get("pid") or _read_pid_file(run_dir/"ui"/"pid.txt"))
                hb = _heartbeat_info(run_dir)
                alive = _pid_alive(pid) if pid else False
                age = hb.get("age_s")
                st = "STOPPED"
                if alive:
                    if age is None:
                        st = "RUNNING"
                    else:
                        st = "HUNG" if int(age) > hb_stale else "RUNNING"
                else:
                    st = str(rec.get("state") or "STOPPED").upper()
                ident = _display_identity(
                    canonical_id=_rrk,
                    label=str(Path(_rrk).name),
                    module=str(Path(_rrk).name),
                    stable_hint=str(Path(_rrk).name),
                    fallback="run",
                )
                managed.append({
                    "run_key": _rrk,
                    **ident,
                    "run_dir": str(run_dir),
                    "state": st,
                    "pid": pid if pid else None,
                    "gen": hb.get("gen"),
                    "heartbeat_age_s": age,
                    "source": "webui",
                    "started_at_utc": rec.get("started_at_utc"),
                })
            # External runs: scan filesystem runs that are RUNNING but not in managed set.
            external: List[Dict[str, Any]] = []
            for rk3, _p in iter_candidate_run_roots(cfg.runs_root, scan_depth=cfg.scan_depth):
                try:
                    run_root3, sim_root3 = resolve_run_dirs(cfg.runs_root, rk3)
                except Exception:
                    continue
                if _managed_marker_path(run_root3).exists():
                    continue
                if _run_belongs_to_managed_payload(run_root3, managed_payload_roots):
                    try:
                        _mark_run_root_managed(run_root3)
                    except Exception:
                        pass
                    continue
                st_obj, _ = read_run_status(sim_root3)
                badge, _extra = _status_badge_from_run_status(st_obj)
                if badge != "RUNNING":
                    continue
                hb = _heartbeat_info(sim_root3)
                rk3_norm = normalize_run_key(rk3)
                ident = _display_identity(
                    canonical_id=rk3_norm,
                    label=str(Path(rk3_norm).name),
                    module=str(Path(rk3_norm).name),
                    stable_hint=str(Path(rk3_norm).name),
                    fallback="run",
                )
                external.append({
                    "run_key": rk3_norm,
                    **ident,
                    "run_dir": str(run_root3),
                    "state": "RUNNING",
                    "pid": None,
                    "gen": hb.get("gen"),
                    "heartbeat_age_s": hb.get("age_s"),
                    "source": "external",
                })
            active = list(managed) + list(external)
            active.sort(key=lambda x: str(x.get("run_key") or ""))
            # Keep managed/unmanaged keys for backward compatibility, but provide
            # the canonical unified "active" list for newer UI code.
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "system_runs_v1",
                    "utc": _now_utc_iso(),
                    "active": active,
                    "managed": managed,
                    "unmanaged": external,
                    "max_concurrent": int(os.environ.get("WEBUI_MAX_CONCURRENT_RUNS", "2") or "2"),
                },
            )
            return

        if path == "/api/system/processes":
            try:
                include_all = str((qs.get("include_all") or ["0"])[0] or "0").strip().lower() in {"1", "true", "yes"}
                payload = _system_processes_payload(cfg, include_all=include_all)
                _json_response(self, HTTPStatus.OK, payload)
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        if path == "/api/patch_backlog":
            pb_path = _patch_backlog_path(cfg)
            obj = _read_json(pb_path)
            if not isinstance(obj, dict):
                # One-time read fallback from legacy runs-root location.
                obj = _read_json(_legacy_patch_backlog_path(cfg))
            if not isinstance(obj, dict):
                obj = {"schema_version":"patch_backlog_v1","items":[]}
            if obj.get("schema_version") != "patch_backlog_v1":
                obj = {"schema_version":"patch_backlog_v1","items":[]}
            if not isinstance(obj.get("items"), list):
                obj["items"] = []
            _json_response(self, HTTPStatus.OK, obj)
            return
        # ------------------------------------------------------------------
        # Admin mobile plane endpoints
        # ------------------------------------------------------------------

        if path == "/api/admin/patch/apply":
            # Upload endpoint: must be POST.
            _json_response(self, HTTPStatus.METHOD_NOT_ALLOWED, {"error": "use POST"})
            return

        if path == "/api/admin/patch/inspect":
            # Upload endpoint: must be POST.
            _json_response(self, HTTPStatus.METHOD_NOT_ALLOWED, {"error": "use POST"})
            return

        if path == "/api/admin/fs/replace":
            if not _admin_guard(self, cfg, qs):
                return
            rel = str((qs.get("path") or [""])[0] or "").strip()
            rr = _admin_repo_root()
            fp = _admin_fs_resolve(rr, rel)
            if not fp:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid path"})
                return

            try:
                data = _read_request_bytes(self, max_bytes=20 * 1024 * 1024)
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

            tmp = fp.with_suffix(fp.suffix + ".tmp")
            try:
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(data)
                if fp.suffix.lower() == ".py":
                    py_compile.compile(str(tmp), doraise=True)
                os.replace(tmp, fp)
            except Exception as e:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": f"replace failed: {e}"})
                return

            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "admin_fs_replace_v1",
                    "utc": _now_utc_iso(),
                    "path": _posix_relpath(fp, rr),
                    "bytes": int(len(data)),
                    "compiled": bool(fp.suffix.lower() == ".py"),
                },
            )
            return

        if path == "/api/admin/fs/zip":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=512 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                paths = obj.get("paths")
                if not isinstance(paths, list) or not paths:
                    raise ValueError("missing paths")
                name = str(obj.get("name") or "repo_selection.zip").strip() or "repo_selection.zip"
                if not name.lower().endswith(".zip"):
                    name += ".zip"
                include_hidden = bool(obj.get("include_hidden", False))

                rr = _admin_repo_root()

                MAX_FILES = int(obj.get("max_files") or 20000)
                MAX_TOTAL_BYTES = int(obj.get("max_total_bytes") or 1_000_000_000)

                rel_inputs = []
                for raw in paths:
                    rel = str(raw or "").strip()
                    if rel:
                        rel_inputs.append(rel)
                rel_inputs = sorted(set(rel_inputs))

                def _is_hidden_path(relp):
                    for part in relp.parts:
                        if str(part).startswith('.'):
                            return True
                    return False

                files = {}
                total_bytes = 0

                for rel in rel_inputs:
                    fp = _admin_fs_resolve(rr, rel)
                    if not fp or (not fp.exists()):
                        continue
                    if fp.is_symlink():
                        continue
                    if fp.is_file():
                        relp = Path(_posix_relpath(fp, rr))
                        if (not include_hidden) and _is_hidden_path(relp):
                            continue
                        arcname = relp.as_posix()
                        if arcname not in files:
                            files[arcname] = fp
                            try:
                                total_bytes += int(fp.stat().st_size)
                            except Exception:
                                pass
                        continue
                    if fp.is_dir():
                        for root, dirs, fnames in os.walk(fp, topdown=True, followlinks=False):
                            root_p = Path(root)
                            if not include_hidden:
                                dirs[:] = [d for d in dirs if not d.startswith('.')]
                            dirs.sort(); fnames.sort()
                            for fn in fnames:
                                if (not include_hidden) and fn.startswith('.'):
                                    continue
                                fpath = root_p / fn
                                try:
                                    if fpath.is_symlink() or (not fpath.is_file()):
                                        continue
                                except Exception:
                                    continue
                                relp = Path(_posix_relpath(fpath, rr))
                                if (not include_hidden) and _is_hidden_path(relp):
                                    continue
                                arcname = relp.as_posix()
                                if arcname in files:
                                    continue
                                files[arcname] = fpath
                                try:
                                    total_bytes += int(fpath.stat().st_size)
                                except Exception:
                                    pass
                                if len(files) > MAX_FILES:
                                    raise ValueError("Too many files selected via folders; reduce selection.")
                                if total_bytes > MAX_TOTAL_BYTES:
                                    raise ValueError("Selection too large; reduce selection.")
                        continue

                if not files:
                    raise ValueError("no files to zip")

                fd, tmp_name = tempfile.mkstemp(prefix="webui_sel_", suffix=".zip")
                os.close(fd)
                tmp_zip = Path(tmp_name)
                try:
                    with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
                        for arcname in sorted(files.keys()):
                            z.write(files[arcname], arcname=arcname)
                    _file_response(self, HTTPStatus.OK, tmp_zip, content_type="application/zip", download_name=name)
                    return
                finally:
                    try:
                        tmp_zip.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except Exception:
                        pass

            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        # -----------------------------
        # Run Builder (Phase 5e Tier-1)
        # -----------------------------








        if path == "/api/runs":
            include_archived = str((qs.get("include_archived") or ["0"])[0] or "0") in {"1", "true", "yes"}
            only_pass = str((qs.get("only_determinism_pass") or ["0"])[0] or "0") in {"1", "true", "yes"}

            # Rebuild run index on each scan (restart-safe and avoids stale paths).
            state.clear_run_index()

            archived_keys_by_zip = _archived_keys_index(cfg)

            runs: List[Dict[str, Any]] = []
            for rk, p in iter_candidate_run_roots(cfg.runs_root, scan_depth=cfg.scan_depth):
                try:
                    run_root, sim_root = resolve_run_dirs(cfg.runs_root, rk)
                except Exception:
                    continue

                # Remember resolved paths for later /api/run/... requests.
                try:
                    state.set_run_index_item(rk, run_root, sim_root)
                except Exception:
                    # Index is best-effort; do not fail the listing.
                    pass
                try:
                    rsum = build_run_summary(state, rk, run_root, sim_root, archived_keys_by_zip=archived_keys_by_zip)
                except Exception:
                    continue

                if not include_archived and bool(rsum.get("archived", False)):
                    continue
                if only_pass and str(rsum.get("determinism")) != "PASS":
                    continue
                runs.append(rsum)
            if not only_pass:
                try:
                    runs.extend(_scheduled_job_matrix_rows(repo_root=_repo_root_from_webui()))
                except Exception:
                    pass

            _build_parent_links(runs)

            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "runs_index_v1",
                    "ui_version": UI_SCHEMA_VERSION,
                    "utc": _now_utc_iso(),
                    "runs": runs,
                },
            )
            return

        # ------------------------------------------------------------------
        # Legacy Phase 5c endpoints (kept for backward compatibility)
        # ------------------------------------------------------------------

        if path == "/api/run":
            key = str((qs.get("key") or [""])[0] or "")
            try:
                rk = normalize_run_key(key)
                run_root, sim_root = resolve_run_dirs(cfg.runs_root, rk)
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "run not found"})
                return
            except ValueError as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return


            # Best-effort archive index for legacy endpoint (restart-safe; avoids reliance on /api/runs).
            try:
                archived_keys_by_zip = _archived_keys_index(cfg)
            except Exception:
                archived_keys_by_zip = set()

            rsum = build_run_summary(state, rk, run_root, sim_root, archived_keys_by_zip=archived_keys_by_zip)
            _build_parent_links([rsum])

            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "legacy_run_v1",
                    "utc": _now_utc_iso(),
                    "run": rsum,
                    "ui_meta": read_ui_meta(run_root),
                    "notes_excerpt": notes_excerpt(run_root),
                },
            )
            return

        if path == "/api/run/metrics":
            # Read-only tail view of selected CSVs (legacy convenience).
            key = str((qs.get("key") or [""])[0] or "")
            which = str((qs.get("file") or ["metrics.csv"])[0] or "metrics.csv")
            try:
                limit = int((qs.get("limit") or ["200"])[0] or "200")
            except Exception:
                limit = 200
            limit = max(1, min(limit, 2000))

            allow = {"metrics.csv", "behaviors.csv", "epi_av_timeseries.csv", "viz/epi_av_summary.csv"}
            if which not in allow:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "file not allowed"})
                return

            try:
                rk = normalize_run_key(key)
                _run_root, sim_root = resolve_run_dirs(cfg.runs_root, rk)
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "run not found"})
                return
            except ValueError as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

            p = sim_root / which
            if not p.exists() or not p.is_file() or p.is_symlink():
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": f"missing {which}"})
                return

            header = _read_first_line(p)
            cols = [c.strip() for c in header.split(",") if c.strip()]
            rows: List[Dict[str, Any]] = []

            tail = tail_lines(p, max_lines=limit + 5)
            buf = io.StringIO()
            if header:
                buf.write(header + "\n")
            for line in tail:
                if not line.strip():
                    continue
                if header and line.strip() == header.strip():
                    continue
                buf.write(line.rstrip("\r\n") + "\n")
            buf.seek(0)

            try:
                reader = csv.DictReader(buf) if header else csv.DictReader(buf, fieldnames=cols)
                for row in reader:
                    if row:
                        rows.append(row)
            except Exception:
                rows = []

            if len(rows) > limit:
                rows = rows[-limit:]

            _json_response(self, HTTPStatus.OK, {"run_key": rk, "file": which, "columns": cols, "rows": rows})
            return

        if path == "/api/run/file_tail":
            key = str((qs.get("key") or [""])[0] or "")
            which = str((qs.get("which") or [""])[0] or "")
            try:
                max_bytes = int((qs.get("max_bytes") or ["16384"])[0] or "16384")
            except Exception:
                max_bytes = 16384
            max_bytes = max(1024, min(max_bytes, 1024 * 1024))

            try:
                rk = normalize_run_key(key)
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "run not found"})
                return
            except ValueError as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

            indexed: Optional[Tuple[Path, Path]] = None
            try:
                indexed = state.get_run_index_item(rk)
            except Exception:
                indexed = None

            if indexed is not None:
                run_root, sim_root = indexed
            else:
                try:
                    run_root, sim_root = resolve_run_dirs(cfg.runs_root, rk)
                except FileNotFoundError:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "run not found"})
                    return
                except ValueError as e:
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                    return

            stdout_cands: List[Path] = []
            seen_stdout: Set[str] = set()

            def _add_stdout_cand(p: Path) -> None:
                try:
                    key = str(p.resolve())
                except Exception:
                    key = str(p)
                if key in seen_stdout:
                    return
                seen_stdout.add(key)
                stdout_cands.append(p)

            for base in (sim_root, run_root):
                cur = Path(base)
                while True:
                    _add_stdout_cand(cur / "stdout_stderr.log")
                    try:
                        if cur.resolve() == Path(cfg.runs_root).resolve():
                            break
                    except Exception:
                        if str(cur) == str(cfg.runs_root):
                            break
                    parent = cur.parent
                    if parent == cur:
                        break
                    cur = parent

            mapping = {
                "stdout": stdout_cands,
                "manifest": [sim_root / "manifest.json"],
                "run_record": [run_root / "run_record.json"],
                "run_meta": [run_root / "run_meta.json"],
                "event_log": [sim_root / "event_log.jsonl"],
                "wal": [sim_root / "command_wal.jsonl"],
            }
            if which not in mapping:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "which not allowed"})
                return

            target_path = None
            for cand in list(mapping[which] or []):
                if cand.exists() and cand.is_file() and (not cand.is_symlink()):
                    target_path = cand
                    break
            if target_path is None:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": f"missing {which}"})
                return

            data = tail_bytes(target_path, max_bytes=max_bytes)
            txt = data.decode("utf-8", errors="replace")
            _json_response(self, HTTPStatus.OK, {"run_key": rk, "which": which, "text": txt})
            return

        # /api/run/{run_id}/...
        # Alias endpoints: /api/runs/<run_id>/start|stop|resume
        if path.startswith("/api/runs/"):
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 4:
                rk = normalize_run_key("/".join(parts[2:-1]))
                action = parts[-1]
                if action in ("start", "stop", "resume"):
                    # Compatibility-only shim: never execute side effects via GET.
                    _json_response(
                        self,
                        HTTPStatus.METHOD_NOT_ALLOWED,
                        {"error": "use POST", "hint": f"/api/run/{rk}/{action}"},
                    )
                    return

        if path.startswith("/api/run/"):
            try:
                res = self._handle_run_get(state, cfg, path, qs)
                if res:
                    return
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "run not found"})
                return
            except ValueError as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return
            except Exception as e:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
                return

        _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _handle_run_get(self, state: _ServerState, cfg: WebUIConfig, path: str, qs: Dict[str, List[str]]) -> bool:
        """Handle GET endpoints under /api/run/."""
        # path: /api/run/<...>
        parts = [p for p in path.split("/") if p]
        # [api, run, <run_key parts...> , action, (maybe filename)]
        if len(parts) < 4:
            return False
        if parts[0] != "api" or parts[1] != "run":
            return False

        # File downloads: .../export/<filename> OR .../archive/<filename>
        if len(parts) >= 5 and parts[-2] in ("export", "archive"):
            kind = parts[-2]
            filename = parts[-1]
            run_key = "/".join(parts[2:-2])
            action = "export_download" if kind == "export" else "archive_download"
        else:
            action = parts[-1]
            filename = None
            run_key = "/".join(parts[2:-1])

        run_key = unquote(run_key)
        rk = normalize_run_key(run_key)

        # Prefer the scan-built run index so nested run keys resolve consistently.
        indexed: Optional[Tuple[Path, Path]] = None
        try:
            indexed = state.get_run_index_item(rk)
        except Exception:
            indexed = None

        if indexed is not None:
            run_root, sim_root = indexed
        else:
            run_root, sim_root = resolve_run_dirs(cfg.runs_root, rk)

        if action == "meta":
            # Best-effort archive index (restart-safe; avoids reliance on /api/runs).
            try:
                archived_keys_by_zip = _archived_keys_index(cfg)
            except Exception:
                archived_keys_by_zip = set()

            rsum = build_run_summary(state, rk, run_root, sim_root, archived_keys_by_zip=archived_keys_by_zip)
            _build_parent_links([rsum])

            bm_obj, bm_src = read_branch_meta_any(run_root, sim_root)
            ui_meta = read_ui_meta(run_root)
            frozen = _detect_trait_apply_phase_frozen(sim_root)
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "run_meta_v1",
                    "ui_version": UI_SCHEMA_VERSION,
                    "utc": _now_utc_iso(),
                    "run": rsum,
                    "branch_meta": bm_obj,
                    "branch_meta_source": bm_src,
                    "ui_meta": ui_meta,
                    "notes_excerpt": notes_excerpt(run_root),
                    "trait_injection_apply_phase_frozen": frozen,
                },
            )
            return True

        if action == "status":
            obj, src = read_run_status(sim_root)
            if obj is None:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "run_status not found"})
                return True
            _json_response(self, HTTPStatus.OK, {"schema_version": "run_status_payload_v1", "source": src, "data": obj})
            return True

        if action == "notes":
            txt = read_notes(run_root)
            _json_response(self, HTTPStatus.OK, {"schema_version": "notes_v1", "run_key": rk, "text": txt})
            return True

        if action == "checkpoints":
            idx, src = read_checkpoints_index(sim_root)
            cps: List[Dict[str, Any]] = []
            missing = 0
            idx_list = idx.get("checkpoints") if (idx and isinstance(idx.get("checkpoints"), list)) else None
            used = "index"
            scan_list: List[Dict[str, Any]] = []

            if idx_list:
                for c in idx_list:
                    if not isinstance(c, dict):
                        continue
                    rel = str(c.get("relative_path", "") or "")
                    p = (sim_root / Path(rel)).resolve()
                    exists = _safe_resolve_under(sim_root, p) is not None and p.exists()
                    if not exists:
                        missing += 1
                    item = dict(c)
                    item["exists"] = bool(exists)
                    cps.append(item)
            else:
                # Best-effort DSIP-safe fallback when the engine hasn't produced an index card
                # or produced an empty one. Supports both checkpoint_bundles_v2 and legacy checkpoints/.
                used = "scan"
                scan_list = _scan_checkpoints_fallback(sim_root)
                # If an index exists but is empty, annotate source as scan_fallback for debugging.
                if idx_list is not None:
                    src = "scan_fallback"
                else:
                    src = src or "scan"
                for c in scan_list:
                    rel = str(c.get("relative_path", "") or "")
                    p = (sim_root / Path(rel)).resolve()
                    exists = _safe_resolve_under(sim_root, p) is not None and p.exists()
                    if not exists:
                        missing += 1
                    item = dict(c)
                    item["exists"] = bool(exists)
                    cps.append(item)

            idx_path = select_checkpoints_index_path(sim_root)
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "checkpoints_payload_v1",
                    "source": src,
                    "run_key": rk,
                    "missing_count": int(missing),
                    "index_source": (idx_path.as_posix() if idx_path else None),
                    "index_count": (len(idx_list) if idx_list is not None else None),
                    "scan_count": (len(scan_list) if used == "scan" else 0),
                    "used_source": used,
                    "checkpoints": cps,
                },
            )
            return True

        if action == "epiav":
            after_gen = int((qs.get("after_gen") or ["-1"])[0])
            limit = int((qs.get("limit") or ["1000"])[0])

            cache = _load_epiav_cache(state, sim_root)
            if cache is None:
                # Try latest-only fallback
                src = select_epiav_source(sim_root)
                if src.get("kind") == "json_latest":
                    epi = _read_json(Path(src.get("epi_path")))
                    av = _read_json(Path(src.get("av_path")))
                    latest = {
                        "generation": _safe_int(epi.get("generation"), -1) if epi else -1,
                        "epi": float(epi.get("epi")) if epi and isinstance(epi.get("epi"), (int, float)) else (float(epi.get("epi_value")) if epi and isinstance(epi.get("epi_value"), (int, float)) else None),
                        "av": float(av.get("av")) if av and isinstance(av.get("av"), (int, float)) else (float(av.get("av_value")) if av and isinstance(av.get("av_value"), (int, float)) else None),
                    }
                    _json_response(
                        self,
                        HTTPStatus.OK,
                        {
                            "schema_version": "epiav_payload_v1",
                            "source_kind": "json_latest",
                            "source": "json_latest",
                            "provisional": False,
                            "parse_errors": 0,
                            "header": None,
                            "has_epi": True,
                            "has_av": True,
                            "points": [latest] if latest.get("generation", -1) >= 0 else [],
                        },
                    )
                    return True

                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "epiav source missing"})
                return True

            pts = cache.get_points(after_gen=after_gen, limit=limit)
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "epiav_payload_v1",
                    "source_kind": "csv",
                    "source": str(cache.path),
                    "provisional": bool(cache.cursor.provisional) or (cache.cursor.parse_errors > 0),
                    "parse_errors": int(cache.cursor.parse_errors),
                    "header": (list(cache.header) if cache.header else None),
                    "has_epi": bool(cache.epi_idx is not None),
                    "has_av": bool(cache.av_idx is not None),
                    "points": pts,
                },
            )
            return True



        if action == "metrics_schema":
            # Union schema: expose columns from both viz/metrics_summary.csv and metrics.csv when present.
            # The summary file is often intentionally narrow; the full file contains additional knobs/traits.
            summary_path = sim_root / "viz" / "metrics_summary.csv"
            full_path = sim_root / "metrics.csv"

            sources = {"summary": None, "full": None}
            cols = []
            seen = set()

            # Prefer ordering: summary columns first, then any extras from full metrics.csv.
            for kind, p in (("summary", summary_path), ("full", full_path)):
                if not (p.exists() and p.is_file() and (not p.is_symlink())):
                    continue
                try:
                    cache = _get_metrics_cache(state, p)
                    cache.ensure_header()
                except Exception:
                    continue
                sources[kind] = str(p)
                for c in (cache.header or []):
                    cc = str(c).strip()
                    if (not cc) or (cc == "generation"):
                        continue
                    if cc in seen:
                        continue
                    cols.append(cc)
                    seen.add(cc)

            if not cols:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "metrics source missing"})
                return True

            header = ["generation"] + cols
            default_src = sources.get("summary") or sources.get("full")
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "metrics_schema_v1",
                    "source": default_src,
                    "sources": sources,
                    "columns": header,
                },
            )
            return True

        if action == "metrics_series":
            metric = str((qs.get("metric") or [""])[0] or "").strip()
            after_gen = _safe_int((qs.get("after_gen") or ["-1"])[0], -1)
            limit = _safe_int((qs.get("limit") or ["1000"])[0], 1000)
            debug = str((qs.get("debug") or qs.get("diag") or ["0"])[0] or "0").strip().lower() in {"1", "true", "yes", "y", "on"}
            if not metric:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "metric required"})
                return True

            summary_path = sim_root / "viz" / "metrics_summary.csv"
            full_path = sim_root / "metrics.csv"

            has_any = False
            chosen = None

            # Prefer summary if it contains the requested metric; otherwise fall back to full metrics.csv.
            for p in (summary_path, full_path):
                if not (p.exists() and p.is_file() and (not p.is_symlink())):
                    continue
                has_any = True
                try:
                    cache = _get_metrics_cache(state, p)
                    cache.ensure_header()
                except Exception:
                    continue
                if metric in (cache.col_index or {}):
                    chosen = cache
                    break

            if not has_any:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "metrics source missing"})
                return True
            if chosen is None:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": f"metric not found: {metric}"})
                return True

            chosen_cache = chosen
            did_fallback = False
            try:
                payload = chosen_cache.get_points(metric, after_gen=after_gen, limit=limit)
            except ValueError as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return True

            # If we picked viz/metrics_summary.csv but it has no rows yet, fall back to metrics.csv
            # (DSIP-safe: read-only).
            try:
                if (
                    chosen_cache.path == summary_path
                    and (not payload.get("points"))
                    and full_path.exists()
                    and full_path.is_file()
                    and (not full_path.is_symlink())
                ):
                    full_cache = _get_metrics_cache(state, full_path)
                    full_cache.ensure_header()
                    if metric in (full_cache.col_index or {}):
                        chosen_cache = full_cache
                        did_fallback = True
                        payload = chosen_cache.get_points(metric, after_gen=after_gen, limit=limit)
            except Exception:
                # best-effort fallback only
                pass

            cursor = getattr(chosen_cache, "cursor", None)
            resp: Dict[str, Any] = {
                "schema_version": "metrics_series_v1",
                "source": str(chosen_cache.path),
                "metric": metric,
                "provisional": bool(cursor and cursor.provisional),
                "parse_errors": int(cursor.parse_errors) if cursor else 0,
                "points": payload.get("points", []),
                "last_generation": int(((payload.get("latest") or {}).get("generation", -1)) or -1),
            }

            if debug:
                # FDP v3: instrument payload shape + finiteness. Never writes to determinism-critical files.
                pts = resp.get("points") if isinstance(resp.get("points"), list) else []
                total = len(pts)
                none_count = 0
                finite_count = 0
                non_finite_count = 0
                bad_value_count = 0
                y_min: Optional[float] = None
                y_max: Optional[float] = None
                key_union = set()
                type_counts: Dict[str, int] = {}

                for p in pts[: min(1000, total)]:
                    if isinstance(p, dict):
                        key_union.update(p.keys())
                        v = p.get("value")
                    else:
                        bad_value_count += 1
                        continue

                    if v is None:
                        none_count += 1
                        type_counts["none"] = type_counts.get("none", 0) + 1
                        continue

                    tname = type(v).__name__
                    type_counts[tname] = type_counts.get(tname, 0) + 1

                    vv: Optional[float] = None
                    if isinstance(v, bool):
                        vv = float(int(v))
                    elif isinstance(v, (int, float)):
                        vv = float(v)
                    else:
                        try:
                            vv = float(v)  # best-effort if server emitted strings
                        except Exception:
                            vv = None

                    if vv is None:
                        bad_value_count += 1
                        continue

                    if math.isfinite(vv):
                        finite_count += 1
                        y_min = vv if (y_min is None or vv < y_min) else y_min
                        y_max = vv if (y_max is None or vv > y_max) else y_max
                    else:
                        non_finite_count += 1

                diag = {
                    "utc": _now_utc_iso(),
                    "run_key": rk,
                    "metric": metric,
                    "source": str(chosen_cache.path),
                    "after_gen": int(after_gen),
                    "limit": int(limit),
                    "did_fallback": bool(did_fallback),
                    "points_total": int(total),
                    "points_none": int(none_count),
                    "points_finite": int(finite_count),
                    "points_non_finite": int(non_finite_count),
                    "points_bad": int(bad_value_count),
                    "y_min": y_min,
                    "y_max": y_max,
                    "point_keys": sorted([str(k) for k in key_union])[:32],
                    "value_types": type_counts,
                    "first": (pts[0] if total else None),
                    "last": (pts[-1] if total else None),
                    "cursor": {
                        "provisional": bool(cursor and cursor.provisional),
                        "parse_errors": int(cursor.parse_errors) if cursor else 0,
                        "offset": int(getattr(cursor, "offset", 0) or 0) if cursor else 0,
                        "size": int(getattr(cursor, "size", 0) or 0) if cursor else 0,
                    },
                }
                resp["diag"] = diag

                try:
                    print(
                        f"[webui][metrics_series] rk={rk} metric={metric} src={resp.get('source')} pts={total} finite={finite_count} none={none_count} non_finite={non_finite_count} bad={bad_value_count} fallback={did_fallback}",
                        file=sys.stderr,
                    )
                except Exception:
                    pass

                # Optional persisted log for postmortems.
                try:
                    cache_dir = _webui_runtime_root().resolve()
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    log_path = cache_dir / "metrics_series_debug.jsonl"
                    line = json.dumps(diag, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                    with _METRICS_SERIES_DEBUG_LOCK:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(line + "\n")
                except Exception:
                    pass

            _json_response(self, HTTPStatus.OK, resp)
            return True

        if action == "traits":
            # Trait discovery prefers the full metrics.csv (it contains gen_*_mean columns),
            # even if viz/metrics_summary.csv is present and narrower.
            full_path = sim_root / "metrics.csv"
            src_path = None
            if full_path.exists() and full_path.is_file() and (not full_path.is_symlink()):
                src_path = full_path
            else:
                src = select_metrics_source(sim_root)
                if src.get("kind") == "csv":
                    src_path = Path(src.get("path"))

            if src_path is None or (not src_path.exists()):
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "metrics source missing"})
                return True

            info = build_trait_catalog_from_metrics(src_path)
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "traits_catalog_v1",
                    "source": info.get("source"),
                    "traits": info.get("traits", []),
                    "current_means": info.get("current_means", {}),
                },
            )
            return True

        if action == "knobs":
            rows, src = read_run_config_rows(sim_root)
            if src is None:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "run_config.csv not found"})
                return True
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "run_knobs_v1",
                    "run_key": rk,
                    "source": src,
                    "rows": rows,
                },
            )
            return True

        if action == "inbox":
            limit = int((qs.get("limit") or ["200"])[0])
            payload = build_inbox_queue(state, sim_root, limit=limit)
            payload["run_key"] = rk
            _json_response(self, HTTPStatus.OK, payload)
            return True
        if action == "applied_commands":
            try:
                limit = int((qs.get("limit") or ["200"])[0])
            except Exception:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid limit"})
                return True

            # Gen paging (UI timeline usability):
            # - after_gen: return items with gen > after_gen
            # - before_gen: return items with gen < before_gen (older paging)
            try:
                after_gen = int((qs.get("after_gen") or ["-1"])[0])
            except Exception:
                after_gen = -1

            before_gen: Optional[int] = None
            if "before_gen" in qs:
                try:
                    before_gen = int((qs.get("before_gen") or [""])[0])
                except Exception:
                    before_gen = None

            def _hydrate_ack_stub_from_inbox(cmd_uuid: Optional[str]) -> Dict[str, Any]:
                """Best-effort: enrich ack-only applied_cmd stubs with type/phase/target_gen.

                The engine may write rejection notices into acks/ without duplicating full
                metadata in the ack payload. Operators still need to see what they submitted.

                We look for a matching JSON command file in inbox_processed/ then inbox/.
                This function is DSIP-safe (read-only, no symlink following).
                """

                out: Dict[str, Any] = {}
                cu = str(cmd_uuid or "").strip()
                if not cu:
                    return out

                # Prefer inbox_processed because many engines move commands immediately.
                for dname in ("inbox_processed", "inbox"):
                    d = sim_root / dname
                    if not (d.exists() and d.is_dir() and (not d.is_symlink())):
                        continue
                    # Filename convention: gen000123__Type__<uuid>.json
                    # But don't depend on it: fall back to scanning a small tail.
                    pats = [f"*__{cu}.json", f"*{cu}*.json"]
                    cand: List[Path] = []
                    for pat in pats:
                        cand.extend(list(d.glob(pat)))
                    cand = [p for p in cand if p.is_file() and (not p.is_symlink())]
                    # Deterministic, keep newest-ish by name.
                    cand = sorted(set(cand), key=lambda x: x.name)
                    if not cand:
                        continue
                    # Take the last one (most recent by name).
                    p = cand[-1]
                    obj = _read_json(p)
                    if not isinstance(obj, dict):
                        continue

                    out["command_type"] = obj.get("command_type")
                    # Some command schemas keep apply_phase nested.
                    ap: Optional[str] = None
                    ti = obj.get("trait_injection")
                    if isinstance(ti, dict):
                        apv = str(ti.get("apply_phase", "") or "").strip()
                        ap = apv or None
                    out["apply_phase"] = ap
                    try:
                        out["target_generation"] = int(obj.get("target_generation"))
                    except Exception:
                        out["target_generation"] = None
                    return out
                return out

            cache = _load_applied_cmd_cache(state, sim_root)
            if cache is not None:
                # Use the full in-memory cache for paging/filtering (bounded by JsonlCache.max_items).
                # If the source is the authoritative ControlPlane event log, normalize command_event_v1
                # records into applied_cmd_v1 for UI compatibility.
                normalized: List[Dict[str, Any]] = []
                applied_uuids: set = set()
                for it in cache.items:
                    if not isinstance(it, dict):
                        continue
                    if str(it.get("schema_version")) == "command_event_v1":
                        try:
                            n = _normalize_command_event_v1_to_applied_cmd(it)
                            normalized.append(n)
                            cu = n.get("command_uuid")
                            if cu:
                                applied_uuids.add(str(cu))
                        except Exception:
                            # Never crash the endpoint because of one bad line.
                            continue
                    else:
                        normalized.append(it)

                # If we're sourcing from events, also include reject notices from acks/
                # (without duplicating anything already present in events).
                try:
                    if str(cache.path).endswith("/events/command_events.jsonl"):
                        acks_dir = sim_root / "acks"
                        if acks_dir.exists() and acks_dir.is_dir():
                            for p in sorted(
                                list(acks_dir.glob("ack_*.json")) + list(acks_dir.glob("ack_inbox_reject__*.json")),
                                key=lambda x: x.name,
                            ):
                                obj = _read_json(p)
                                if not obj:
                                    continue
                                cu = str(obj.get("command_uuid") or "")
                                if cu and (cu in applied_uuids):
                                    continue
                                cu2 = obj.get("command_uuid")
                                extra = _hydrate_ack_stub_from_inbox(str(cu2 or ""))
                                normalized.append(
                                    {
                                        "schema_version": "applied_cmd_v1",
                                        "command_uuid": cu2,
                                        "command_type": extra.get("command_type"),
                                        "apply_phase": extra.get("apply_phase"),
                                        "target_generation": extra.get("target_generation"),
                                        "applied_generation": None,
                                        "payload_hash": None,
                                        "ack_status": obj.get("status"),
                                        "error_reason": obj.get("reason"),
                                        "timestamp": None,
                                        "source": "ack",
                                    }
                                )
                except Exception:
                    pass

                items, window = filter_applied_command_items(normalized, after_gen=after_gen, before_gen=before_gen, limit=limit)

                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "applied_commands_payload_v1",
                        "source": str(cache.path),
                        "provisional": bool(cache.cursor.provisional) or (cache.cursor.parse_errors > 0),
                        "parse_errors": int(cache.cursor.parse_errors),
                        "window": window,
                        "items": items,
                    },
                )
                return True

            # Fallback: read acks/ (and optionally event log) for minimal visibility.
            acks_dir = sim_root / "acks"
            items: List[Dict[str, Any]] = []
            if acks_dir.exists() and acks_dir.is_dir():
                for p in sorted(
                    list(acks_dir.glob("ack_*.json")) + list(acks_dir.glob("ack_inbox_reject__*.json")),
                    key=lambda x: x.name,
                ):
                    obj = _read_json(p)
                    if not obj:
                        continue
                    cu2 = obj.get("command_uuid")
                    extra = _hydrate_ack_stub_from_inbox(str(cu2 or ""))
                    items.append(
                        {
                            "schema_version": "applied_cmd_v1",
                            "command_uuid": cu2,
                            "command_type": extra.get("command_type"),
                            "apply_phase": extra.get("apply_phase"),
                            "target_generation": extra.get("target_generation"),
                            "applied_generation": None,
                            "payload_hash": None,
                            "ack_status": obj.get("status"),
                            "error_reason": obj.get("reason"),
                            "timestamp": None,
                            "source": "ack",
                        }
                    )
            if limit and len(items) > limit:
                items = items[-limit:]

            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "applied_commands_payload_v1",
                    "source": "acks_fallback",
                    "items": items,
                },
            )
            return True


        if action == "timeline_summary":
            try:
                bins = int((qs.get("bins") or ["200"])[0])
            except Exception:
                bins = 200
            summary = build_timeline_summary(state, sim_root, bins=bins)
            _json_response(self, HTTPStatus.OK, summary)
            return True

        if action == "timeline_range":
            try:
                gen_min = int((qs.get("gen_min") or ["0"])[0])
                gen_max = int((qs.get("gen_max") or ["0"])[0])
            except Exception:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid gen_min/gen_max"})
                return True
            kind = str((qs.get("type") or ["control"])[0] or "control").strip().lower()
            if kind not in {"control", "pressure"}:
                kind = "control"
            try:
                limit = int((qs.get("limit") or ["200"])[0])
            except Exception:
                limit = 200
            payload = build_timeline_range(sim_root, gen_min=gen_min, gen_max=gen_max, kind=kind, limit=limit)
            _json_response(self, HTTPStatus.OK, payload)
            return True

        if action == "autopilot_actions":
            try:
                limit = int((qs.get("limit") or ["200"])[0])
            except Exception:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid limit"})
                return True

            try:
                after_gen = int((qs.get("after_gen") or ["-1"])[0])
            except Exception:
                after_gen = -1

            before_gen: Optional[int] = None
            if "before_gen" in qs:
                try:
                    before_gen = int((qs.get("before_gen") or [""])[0])
                except Exception:
                    before_gen = None

            cache = _load_autopilot_cache(state, sim_root)
            if cache is None:
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "autopilot_actions_payload_v1",
                        "source": None,
                        "provisional": False,
                        "parse_errors": 0,
                        "window": {
                            "after_gen": after_gen,
                            "before_gen": before_gen,
                            "min_generation": None,
                            "max_generation": None,
                        },
                        "items": [],
                        "note": "autopilot decisions log not found (autopilot may be disabled or has not ticked yet).",
                    },
                )
                return True

            raw_items = [it for it in cache.items if isinstance(it, dict)]
            items, window = filter_autopilot_items(raw_items, after_gen=after_gen, before_gen=before_gen, limit=limit)

            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "autopilot_actions_payload_v1",
                    "source": str(cache.path),
                    "provisional": bool(cache.cursor.provisional) or (cache.cursor.parse_errors > 0),
                    "parse_errors": int(cache.cursor.parse_errors),
                    "window": window,
                    "items": items,
                },
            )
            return True


        if action == "export_download":
            if not filename:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "missing filename"})
                return True
            if not re.match(r"^[A-Za-z0-9._-]+\.zip$", filename):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid filename"})
                return True

            p = (ui_dir(run_root) / "exports" / filename).resolve()
            safe = _safe_resolve_under(ui_dir(run_root) / "exports", p)
            if safe is None or not p.exists() or not p.is_file():
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "export not found"})
                return True

            _file_response(
                self,
                HTTPStatus.OK,
                p,
                content_type="application/zip",
                download_name=filename,
            )
            return True

        if action == "archive_download":
            if not filename:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "missing filename"})
                return True
            if not re.match(r"^[A-Za-z0-9._-]+\.zip$", filename):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid filename"})
                return True

            adir = runs_archives_dir(cfg.runs_root)
            p = (adir / filename).resolve()
            safe = _safe_resolve_under(adir, p)
            if safe is None or not p.exists() or not p.is_file():
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "archive not found"})
                return True

            _file_response(
                self,
                HTTPStatus.OK,
                p,
                content_type="application/zip",
                download_name=filename,
            )
            return True

        return False

    def do_POST(self) -> None:  # noqa: N802
        cfg: WebUIConfig = self.server.state.cfg  # type: ignore[attr-defined]
        if not _request_security.guard_request(self, cfg, "POST", lambda token: _assistant_bridge.validate_control_pane_access_token(_admin_repo_root(), token)):
            return
        state: _ServerState = self.server.state  # type: ignore[attr-defined]

        u = urlparse(self.path)
        path = u.path or "/"
        qs = parse_qs(u.query or "")
        web_surface_enabled = _web_surface_enabled()

        if not web_surface_enabled and not path.startswith("/api/assistant/"):
            _json_response(
                self,
                HTTPStatus.NOT_FOUND,
                {
                    "schema_version": "web_surface_gate_v1",
                    "utc": _now_utc_iso(),
                    "ok": False,
                    "error_code": "web_surface_disabled",
                    "error": "web surface is disabled",
                },
            )
            return

        if _route_dispatch.dispatch_assistant_post(
            routes_module=sys.modules[__name__],
            handler=self,
            state=state,
            cfg=cfg,
            path=path,
            qs=qs,
        ):
            return




        # ------------------------------------------------------------------
        # Admin mobile plane POST endpoints
        # ------------------------------------------------------------------
        if path == "/api/ledger/item/create":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=512 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                pis_md = str(obj.get("pis_markdown") or obj.get("pis_md") or "")
                title = str(obj.get("title") or "").strip()
                if not title:
                    title = _title_from_pis_markdown(pis_md)
                if not title:
                    title = "(untitled)"
                slug = obj.get("slug")
                tags = obj.get("tags") if isinstance(obj.get("tags"), list) else []
                project_id = obj.get("project_id")
                workstream_type = obj.get("workstream_type")
                scope_boundary = obj.get("scope_boundary")
                artifact_roots = obj.get("artifact_roots") if isinstance(obj.get("artifact_roots"), list) else None
                supersedes_uid = str(obj.get("supersedes_uid") or "").strip() or None
                regression_of_uid = str(obj.get("regression_of_uid") or "").strip() or None
                uid_hint = str(obj.get("uid") or "").strip() or _extract_uid_from_pis_text(pis_md)

                ledger = ChangeLedger()
                if uid_hint:
                    try:
                        existing = ledger.read_item(uid_hint)
                        ex_meta = existing.get("meta") if isinstance(existing.get("meta"), dict) else {}
                        _json_response(
                            self,
                            HTTPStatus.OK,
                            {
                                "schema_version": "ledger_create_v1",
                                "utc": _now_utc_iso(),
                                "ok": True,
                                "existing": True,
                                "uid": str(ex_meta.get("uid") or uid_hint),
                                "slug": str(ex_meta.get("slug") or ""),
                                "dir": str(existing.get("dir") or ""),
                                "created_utc": str(ex_meta.get("created_utc") or ""),
                            },
                        )
                        return
                    except FileNotFoundError:
                        pass
                res = ledger.create_item(
                    title=title,
                    slug=(str(slug).strip() if slug is not None else None),
                    tags=[str(t).strip() for t in tags if str(t).strip()],
                    pis_markdown=pis_md,
                    supersedes_uid=supersedes_uid,
                    regression_of_uid=regression_of_uid,
                    uid_override=(uid_hint or None),
                    project_id=(str(project_id).strip() if project_id is not None else None),
                    workstream_type=(str(workstream_type).strip() if workstream_type is not None else None),
                    scope_boundary=(str(scope_boundary).strip() if scope_boundary is not None else None),
                    artifact_roots=([str(p).strip() for p in artifact_roots if str(p).strip()] if artifact_roots is not None else None),
                )
                _json_response(self, HTTPStatus.OK, {"schema_version": "ledger_create_v1", "utc": _now_utc_iso(), **res})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/idea_notes/create":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                from infra.webui.ledger.idea_note_manager import IdeaNoteLedger

                obj = _read_request_json(self, max_bytes=512 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                idea_md = str(obj.get("idea_markdown") or obj.get("idea_md") or "")
                title = str(obj.get("title") or "").strip()
                if not title:
                    title = _title_from_pis_markdown(idea_md)
                if not title:
                    title = "(untitled)"
                slug = obj.get("slug")
                tags = obj.get("tags") if isinstance(obj.get("tags"), list) else []
                project_id = obj.get("project_id")
                workstream_type = obj.get("workstream_type")
                scope_boundary = obj.get("scope_boundary")
                artifact_roots = obj.get("artifact_roots") if isinstance(obj.get("artifact_roots"), list) else None
                related_intent_sheet_uids = obj.get("related_intent_sheet_uids") if isinstance(obj.get("related_intent_sheet_uids"), list) else None
                related_pis_uids = obj.get("related_pis_uids") if isinstance(obj.get("related_pis_uids"), list) else None
                promotion_target_uid = str(obj.get("promotion_target_uid") or "").strip() or None
                confidence = str(obj.get("confidence") or "").strip()
                maturity = str(obj.get("maturity") or "").strip()
                why_now = str(obj.get("why_now") or "").strip() or None
                priority_signal = str(obj.get("priority_signal") or "").strip() or None
                resume_context = str(obj.get("resume_context") or "").strip() or None
                resume_blockers = obj.get("resume_blockers") if isinstance(obj.get("resume_blockers"), list) else None
                uid_hint = str(obj.get("uid") or "").strip() or None

                ledger = IdeaNoteLedger()
                if uid_hint:
                    try:
                        existing = ledger.read_note(uid_hint)
                        ex_meta = existing.get("meta") if isinstance(existing.get("meta"), dict) else {}
                        _json_response(
                            self,
                            HTTPStatus.OK,
                            {
                                "schema_version": "idea_note_create_v1",
                                "utc": _now_utc_iso(),
                                "ok": True,
                                "existing": True,
                                "uid": str(ex_meta.get("uid") or uid_hint),
                                "slug": str(ex_meta.get("slug") or ""),
                                "dir": str(existing.get("dir") or ""),
                                "created_utc": str(ex_meta.get("created_utc") or ""),
                            },
                        )
                        return
                    except FileNotFoundError:
                        pass
                res = ledger.create_note(
                    title=title,
                    idea_markdown=idea_md,
                    slug=(str(slug).strip() if slug is not None else None),
                    tags=[str(t).strip() for t in tags if str(t).strip()],
                    uid_override=uid_hint,
                    project_id=(str(project_id).strip() if project_id is not None else None),
                    workstream_type=(str(workstream_type).strip() if workstream_type is not None else None),
                    scope_boundary=(str(scope_boundary).strip() if scope_boundary is not None else None),
                    artifact_roots=([str(p).strip() for p in artifact_roots if str(p).strip()] if artifact_roots is not None else None),
                    related_intent_sheet_uids=([str(x).strip() for x in related_intent_sheet_uids if str(x).strip()] if related_intent_sheet_uids is not None else None),
                    related_pis_uids=([str(x).strip() for x in related_pis_uids if str(x).strip()] if related_pis_uids is not None else None),
                    promotion_target_uid=promotion_target_uid,
                    confidence=confidence,
                    maturity=maturity,
                    why_now=why_now,
                    priority_signal=priority_signal,
                    resume_context=resume_context,
                    resume_blockers=([str(x).strip() for x in resume_blockers if str(x).strip()] if resume_blockers is not None else None),
                )
                _json_response(self, HTTPStatus.OK, {"schema_version": "idea_note_create_v1", "utc": _now_utc_iso(), **res})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path.startswith("/api/idea_notes/") and path.endswith("/action"):
            if not _admin_guard(self, cfg, qs):
                return
            uid = path[len("/api/idea_notes/") : -len("/action")].strip("/")
            try:
                from infra.webui.ledger.idea_note_manager import IdeaNoteLedger

                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                action = str(obj.get("action") or "").strip()
                note = str(obj.get("note") or "").strip()
                why_now = obj.get("why_now")
                priority_signal = obj.get("priority_signal")
                resume_context = obj.get("resume_context")
                resume_blockers = obj.get("resume_blockers")
                ledger = IdeaNoteLedger()
                result = ledger.apply_lifecycle_action(
                    uid,
                    action,
                    note=note,
                    why_now=(str(why_now).strip() if why_now is not None else None),
                    priority_signal=(str(priority_signal).strip() if priority_signal is not None else None),
                    resume_context=(str(resume_context).strip() if resume_context is not None else None),
                    resume_blockers=([str(x).strip() for x in resume_blockers if str(x).strip()] if isinstance(resume_blockers, list) else None),
                )
                detail = ledger.read_note(uid)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "idea_note_action_v1",
                        "utc": _now_utc_iso(),
                        "action": action,
                        **result,
                        "meta": detail.get("meta") or {},
                        "backlog_kind": "idea_note",
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "idea note not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/ledger/action/run":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=256 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                uid = str(obj.get("uid") or "").strip()
                action_id = str(obj.get("action_id") or "").strip()
                task_id = str(obj.get("task_id") or "").strip()
                git_ref = str(obj.get("git_ref") or "").strip()
                runs_root = str(obj.get("runs_root") or "").strip()
                payload = _ledger_actions.run_action(uid=uid, action_id=action_id, task_id=task_id, git_ref=git_ref, runs_root=runs_root)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "ledger_action_run_v1", "utc": _now_utc_iso(), **payload},
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/ledger/action/cancel":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _ledger_actions.cancel_action(
                    uid=str(obj.get("uid") or "").strip(),
                    job_id=str(obj.get("job_id") or "").strip(),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "ledger_action_cancel_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/ledger/action/force_cancel":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _ledger_actions.force_cancel_action(
                    uid=str(obj.get("uid") or "").strip(),
                    job_id=str(obj.get("job_id") or "").strip(),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "ledger_action_force_cancel_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/admin/job_budget/allocation":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                from infra.webui import server_job_budget_admin as _job_budget_admin

                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _job_budget_admin.record_budget_allocation(
                    allocation_uid=str(obj.get("allocation_uid") or "").strip(),
                    job_uid=str(obj.get("job_uid") or "").strip(),
                    amount_minor_units=int(obj.get("amount_minor_units")),
                    unit=str(obj.get("unit") or "").strip(),
                    allocation_reason_code=str(obj.get("allocation_reason_code") or "").strip(),
                    recorded_at_utc=str(obj.get("recorded_at_utc") or "").strip(),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "admin_job_budget_allocation_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/chat/session_cap":
            try:
                obj = _read_request_json(self, max_bytes=64 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                agent_key = _coerce_mobile_agent_key(obj.get("agent_key"))
                default_tid = str(_mobile_agent_spec(agent_key).get("thread_id") or "mbl")
                tid = _coerce_mobile_task_id(obj.get("thread_id") or default_tid)
                cap = int(obj.get("max_turns") or 0)
                if cap < 1:
                    raise ValueError("max_turns must be >= 1")
                max_turns = _mobile_session_set_cap(tid, cap)
                payload = _mobile_session_stats(tid)
                payload["max_turns"] = int(max_turns)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "mobile_chat_session_cap_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/chat/reset":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=64 * 1024)
                if not isinstance(obj, dict):
                    obj = {}
                payload = _mobile_reset_all_sessions(clear_agents=bool(obj.get("clear_agents", True)))
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "mobile_chat_reset_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/chat/cancel":
            try:
                obj = _read_request_json(self, max_bytes=64 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                agent_key_raw = str(obj.get("agent_key") or "").strip()
                agent_spec = _mobile_agent_spec(agent_key_raw or "a")
                tid = _coerce_mobile_task_id(obj.get("thread_id") or agent_spec.get("thread_id") or "mbl")
                role = _mobile_role_for_thread_id(tid)
                reason = str(obj.get("reason") or "cancelled from mobile").strip() or "cancelled from mobile"
                payload = _mobile_cancel_active_stream(tid=tid, role=role, reason=reason)
                status = str(payload.get("status") or "").strip().lower()
                http_status = HTTPStatus.OK if bool(payload.get("ok")) else HTTPStatus.CONFLICT
                if status == "not_found":
                    http_status = HTTPStatus.CONFLICT
                _json_response(
                    self,
                    http_status,
                    {"schema_version": "mobile_chat_cancel_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/plane/run":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                prompt = str(obj.get("prompt") or obj.get("request") or "").strip()
                if not prompt:
                    raise ValueError("prompt is required")
                plan = bool(obj.get("plan"))
                verbose = bool(obj.get("verbose"))
                options = {
                    "patch_path": str(obj.get("patch_path") or "").strip(),
                    "zip_path": str(obj.get("zip_path") or "").strip(),
                    "report_file": str(obj.get("report_file") or "").strip(),
                    "snapshot_file": str(obj.get("snapshot_file") or "").strip(),
                    "receipts_file": str(obj.get("receipts_file") or "").strip(),
                    "watchdog_file": str(obj.get("watchdog_file") or "").strip(),
                    "iterations_file": str(obj.get("iterations_file") or "").strip(),
                    "fallback_events_file": str(obj.get("fallback_events_file") or "").strip(),
                    "target_pis_uid": str(obj.get("target_pis_uid") or "").strip(),
                    "target_uid": str(obj.get("target_uid") or "").strip(),
                    "job_id": str(obj.get("job_id") or "").strip(),
                    "task_id": str(obj.get("task_id") or "").strip(),
                    "current_status": str(obj.get("current_status") or "").strip(),
                    "expected_status": str(obj.get("expected_status") or "").strip(),
                    "verifier_verdict": str(obj.get("verifier_verdict") or "").strip(),
                    "validation_commands": list(obj.get("validation_commands") or []),
                    "allow_fallback": bool(obj.get("allow_fallback")),
                    "no_fallback": bool(obj.get("no_fallback")),
                    "allow_recovery": bool(obj.get("allow_recovery")),
                }
                timeout_sec = 6 * 60 * 60.0
                _append_labsh_history_line(prompt)
                cmd = _mobile_plane_run_command(prompt=prompt, plan=plan, verbose=verbose, options=options)
                job = _admin_cmd_start_job(
                    state,
                    cfg,
                    cmd=cmd,
                    cwd_rel="",
                    kind="mobile_plane",
                    timeout_sec=timeout_sec,
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "mobile_plane_run_v1", "utc": _now_utc_iso(), "job": job.to_dict()},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path in ("/api/mobile/chat/stream", "/api/agents/chat/stream"):
            try:
                obj = _read_request_json(self, max_bytes=512 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                prompt = str(obj.get("message") or obj.get("prompt") or "").strip()
                if not prompt:
                    raise ValueError("message is required")
                agent_key = _coerce_mobile_agent_key(obj.get("agent_key"))
                agent_spec = _mobile_agent_spec(agent_key)
                tid = _coerce_mobile_task_id(obj.get("thread_id") or agent_spec.get("thread_id") or "mbl")
                binding = {"type": "task_id", "id": tid}
                swarm_objective = _extract_swarm_objective(prompt)
                benchmark_requested = bool(_BENCHMARK_TRIGGER_RE.match(prompt))
                benchmark_objective = (
                    _resolve_bound_benchmark_objective(
                        binding=binding,
                        explicit_objective=_extract_benchmark_objective(prompt),
                    )
                    if benchmark_requested
                    else ""
                )
                model_input = _mobile_build_prompt(tid, prompt)
                default_timeout_s = _default_mobile_chat_timeout_s()
                timeout_s = int(obj.get("timeout_s") or default_timeout_s)
                timeout_s = max(30, min(6 * 60 * 60, timeout_s))
                model_name = str(obj.get("model") or _default_mobile_chat_model() or "").strip()
                fallback_models_raw = obj.get("fallback_models")
                fallback_models: List[str] = []
                if isinstance(fallback_models_raw, list):
                    for item in fallback_models_raw:
                        txt = str(item or "").strip()
                        if txt and txt not in fallback_models:
                            fallback_models.append(txt)
                elif isinstance(fallback_models_raw, str):
                    for part in str(fallback_models_raw).split(","):
                        txt = str(part or "").strip()
                        if txt and txt not in fallback_models:
                            fallback_models.append(txt)
                else:
                    fallback_models = list(_default_mobile_chat_fallback_models())
                fallback_model = str(obj.get("fallback_model") or "").strip()
                if fallback_model and fallback_model not in fallback_models:
                    fallback_models.append(fallback_model)
                fallback_on_quota = bool(obj.get("fallback_on_quota", True))
                reasoning_effort = _scheduled_jobs._normalize_reasoning_effort(
                    obj.get("reasoning_effort") or _default_mobile_chat_reasoning_effort(model=model_name) or "",
                    model=model_name,
                    field_name="reasoning_effort",
                )
                plane_route = (
                    _plane_mobile_route_prompt(prompt)
                    if agent_key == "p"
                    else {
                        "kind": "codex",
                        "route": _PLANE_ROUTE_BACKEND_SEND_TURN,
                        "reply": "",
                        "applied": False,
                        "backend": "codex",
                    }
                )
                plane_route_kind = str(plane_route.get("kind") or "codex").strip().lower() or "codex"
                plane_direct_reply = str(plane_route.get("reply") or "").strip() if plane_route_kind == "direct_reply" else None
                plane_boundary_accepted = False
                request_id_prefix = "mobile_plane_send_" if agent_key == "p" else "mobile_send_"
                request_id = str(obj.get("request_id") or "").strip() or f"{request_id_prefix}{uuid.uuid4().hex[:12]}"
                idempotency_key = str(obj.get("idempotency_key") or "").strip() or request_id
                if agent_key == "p" and plane_route_kind == "codex" and not swarm_objective and not benchmark_requested:
                    boundary_payload = {
                        "binding": dict(binding),
                        "role": str(agent_spec.get("role") or "generalist"),
                        "message": prompt,
                        "options": {
                            "timeout_s": timeout_s,
                            **({"model": model_name} if model_name else {}),
                            **({"fallback_models": list(fallback_models)} if fallback_models else {}),
                            **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
                        },
                    }
                    boundary_result = _process_boundary_mutation(
                        command_id="send_turn",
                        payload=boundary_payload,
                        request_id=request_id,
                        idempotency_key=idempotency_key,
                    )
                    boundary_status = str(boundary_result.get("status") or "").strip().lower()
                    if boundary_status != _backend_api_boundary.COMMAND_STATUS_ACCEPTED:
                        _json_response(
                            self,
                            _backend_boundary_http_status(boundary_result),
                            {
                                "schema_version": "mobile_chat_stream_boundary_v1",
                                "utc": _now_utc_iso(),
                                "ok": False,
                                "boundary_result": boundary_result,
                            },
                        )
                        return
                    plane_boundary_accepted = True

                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                write_lock = threading.Lock()

                def _emit(evt: dict) -> bool:
                    raw = f"data: {json.dumps(evt, ensure_ascii=False)}\n\n".encode("utf-8")
                    try:
                        with write_lock:
                            self.wfile.write(raw)
                            self.wfile.flush()
                        return True
                    except Exception:
                        return False

                def _emit_done() -> bool:
                    try:
                        with write_lock:
                            self.wfile.write(b"data: [DONE]\n\n")
                            self.wfile.flush()
                        return True
                    except Exception:
                        return False

                def _emit_keepalive() -> bool:
                    try:
                        with write_lock:
                            self.wfile.write(b'data: {"event":"keepalive"}\n\n')
                            self.wfile.flush()
                        return True
                    except Exception:
                        return False

                def _record_plane_short_turn(
                    *,
                    assistant_text: str,
                    assistant_status: str,
                    state_reason: str,
                ) -> Dict[str, Any]:
                    from infra.webui import server_lab_assistant_turns as _lab_turns

                    transport_id = str(request_id or "").strip()
                    resume_turn_id = str(obj.get("resume_turn_id") or "").strip()
                    turn_open = _lab_turns.begin_or_resume_turn(
                        _repo_root_from_webui(),
                        binding=binding,
                        role=role,
                        message=prompt,
                        writer="mobile_chat_stream.plane_short",
                        resend_key=idempotency_key,
                        resume_turn_id=resume_turn_id,
                        advisory_transport_id=transport_id,
                    )
                    turn = dict(turn_open.get("turn") or {})
                    turn_id = str(turn.get("turn_id") or "").strip()
                    if not turn_id:
                        return {"turn_id": "", "duplicate_suppressed": False}
                    if (
                        bool(turn_open.get("duplicate_suppressed"))
                        and str(turn.get("state") or "").strip().lower() in {"completed", "failed", "cancelled", "timed_out"}
                    ):
                        return {"turn_id": turn_id, "duplicate_suppressed": True}
                    _lab_turns.transition_turn(
                        _repo_root_from_webui(),
                        turn_id=turn_id,
                        writer="mobile_chat_stream.plane_short",
                        new_state="running",
                        state_reason="plane short reply accepted",
                        advisory_transport_id=transport_id,
                    )
                    if assistant_text:
                        _lab_turns.append_turn_output_event(
                            _repo_root_from_webui(),
                            turn_id=turn_id,
                            writer="mobile_chat_stream.plane_short",
                            event_type="output_delta",
                            text=assistant_text,
                        )
                    terminal_state = "completed" if str(assistant_status or "").strip().lower() == "ok" else "failed"
                    terminal_event = _lab_turns.append_turn_output_event(
                        _repo_root_from_webui(),
                        turn_id=turn_id,
                        writer="mobile_chat_stream.plane_short",
                        event_type="terminal",
                        text=assistant_text,
                        terminal_state=terminal_state,
                    )
                    _lab_turns.transition_turn(
                        _repo_root_from_webui(),
                        turn_id=turn_id,
                        writer="mobile_chat_stream.plane_short",
                        new_state=terminal_state,
                        state_reason=state_reason,
                        advisory_transport_id=transport_id,
                    )
                    try:
                        _lab_turns.mark_turn_final_commit_pending(
                            _repo_root_from_webui(),
                            turn_id=turn_id,
                            writer="mobile_chat_stream.plane_short",
                            turn_event_seq=int(terminal_event.get("event_seq") or 0),
                        )
                    except Exception:
                        pass
                    return {"turn_id": turn_id, "duplicate_suppressed": False}

                control_id = ""
                try:
                    role = str(agent_spec.get("role") or "generalist")
                    if plane_route_kind == "direct_reply":
                        plane_reply = str(plane_route.get("reply") or "")
                        turn_result = _record_plane_short_turn(
                            assistant_text=plane_reply,
                            assistant_status="ok",
                            state_reason="plane direct status reply completed",
                        )
                        turn_id = str(turn_result.get("turn_id") or "").strip()
                        if turn_id:
                            _emit({"type": "start", "turn_id": turn_id, "plane_routed": True})
                        _emit({"delta": plane_reply})
                        _emit_done()
                        return
                    if plane_route_kind == "mutation":
                        plane_reply = str(plane_route.get("reply") or "")
                        plane_mutation_applied = bool(plane_route.get("applied"))
                        plane_backend = str(plane_route.get("backend") or "scheduler_mutation").strip() or "scheduler_mutation"
                        assistant_status = "ok" if plane_mutation_applied else "error"
                        turn_result = _record_plane_short_turn(
                            assistant_text=plane_reply,
                            assistant_status=assistant_status,
                            state_reason="plane mutation reply completed" if plane_mutation_applied else "plane mutation reply failed",
                        )
                        turn_id = str(turn_result.get("turn_id") or "").strip()
                        if turn_id:
                            _emit({"type": "start", "turn_id": turn_id, "plane_routed": True})
                        _emit({"delta": plane_reply})
                        _emit_done()
                        return
                    if swarm_objective:
                        _agents.append_session_message(
                            role=role,
                            binding=binding,
                            sender="user",
                            content=prompt,
                            meta={"source": "mobile_chat_stream"},
                        )
                        payload = _agent_swarm.start_swarm(
                            binding=binding,
                            objective=swarm_objective,
                            options={"started_by_role": role},
                        )
                        swarm_state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
                        assistant_text = (
                            f"Started swarm {str(payload.get('swarm_id') or '').strip()}. "
                            f"Phase: {str(swarm_state.get('phase') or 'queued').strip() or 'queued'}. "
                            "It is running in isolated worktrees in the background."
                        ).strip()
                        _agents.append_session_message(
                            role=role,
                            binding=binding,
                            sender="assistant",
                            content=assistant_text,
                            meta={"source": "mobile_chat_stream", "swarm_id": str(payload.get("swarm_id") or "")},
                        )
                        _emit({"delta": assistant_text})
                        _emit_done()
                        return
                    if benchmark_requested:
                        _agents.append_session_message(
                            role=role,
                            binding=binding,
                            sender="user",
                            content=prompt,
                            meta={"source": "mobile_chat_stream"},
                        )
                        if not benchmark_objective:
                            assistant_text = _bound_benchmark_missing_objective_chat_text()
                            _agents.append_session_message(
                                role=role,
                                binding=binding,
                                sender="assistant",
                                content=assistant_text,
                                meta={"source": "mobile_chat_stream"},
                            )
                            _emit({"delta": assistant_text})
                            _emit_done()
                            return

                        def _on_benchmark_complete(status: Dict[str, Any]) -> None:
                            followup = str(status.get("report_text") or "").strip()
                            if not followup:
                                followup = (
                                    f"Benchmark {str(status.get('benchmark_id') or '').strip()} finished: "
                                    f"{str(status.get('status') or 'completed').strip() or 'completed'}."
                                )
                                summary_path = str(status.get("summary_path") or "").strip()
                                if summary_path:
                                    followup += f"\nReport: {summary_path}"
                            _agents.append_session_message(
                                role=role,
                                binding=binding,
                                sender="assistant",
                                content=followup,
                                meta={
                                    "source": "mobile_chat_stream",
                                    "benchmark_id": str(status.get("benchmark_id") or ""),
                                    "benchmark_status": str(status.get("status") or ""),
                                },
                            )

                        payload = _agent_benchmark.start_background_benchmark(
                            objective=benchmark_objective,
                            binding=binding,
                            label=f"Chat benchmark {tid}",
                            on_complete=_on_benchmark_complete,
                        )
                        benchmark_id = str(payload.get("benchmark_id") or "").strip()
                        assistant_text = (
                            f"Started benchmark {benchmark_id}. "
                            "It is comparing workflow and swarm from the same git ref in disposable worktrees. "
                            "I will append the report here when it finishes."
                        ).strip()
                        _agents.append_session_message(
                            role=role,
                            binding=binding,
                            sender="assistant",
                            content=assistant_text,
                            meta={"source": "mobile_chat_stream", "benchmark_id": benchmark_id},
                        )
                        _emit({"delta": assistant_text})
                        _emit_done()
                        return
                    control = _mobile_try_claim_active_stream(tid=tid, role=role, request_id=request_id)
                    if control is None:
                        _emit({"event": "error", "error": "another turn is already running for this tab"})
                        _emit_done()
                        return
                    control_id = str(control.get("control_id") or "").strip()
                    stream_options = {
                        "timeout_s": timeout_s,
                        "fallback_on_quota": fallback_on_quota,
                        "prompt_file": str(agent_spec.get("prompt_file") or "").strip(),
                        "role_prompt_text": _mobile_bootstrap_prompt_text(agent_key),
                        "current_message_only": True,
                        "defer_host_verification": True,
                        **({"model": model_name} if model_name else {}),
                        **({"fallback_models": list(fallback_models)} if fallback_models else {}),
                        **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
                        **({"transport_id": request_id} if request_id else {}),
                    }
                    client_connected = True
                    keepalive_stop = threading.Event()
                    keepalive_interval_s = float(globals().get("_MOBILE_STREAM_KEEPALIVE_INTERVAL_S", 5.0) or 5.0)

                    def _keepalive_loop() -> None:
                        while not keepalive_stop.wait(keepalive_interval_s):
                            if not _emit_keepalive():
                                keepalive_stop.set()
                                return

                    keepalive_thread = threading.Thread(target=_keepalive_loop, daemon=True)
                    keepalive_thread.start()
                    try:
                        for evt in _agents.send_message_stream_events(
                            role=role,
                            binding=binding,
                            message=prompt,
                            options=stream_options,
                        ):
                            if bool(control.get("superseded")):
                                break
                            et = str(evt.get("type") or "").strip().lower()
                            if et in {"commentary", "progress"}:
                                text = str(evt.get("commentary") or evt.get("progress") or evt.get("text") or evt.get("content") or "").strip()
                                if text and client_connected:
                                    payload = {
                                        "event": "commentary",
                                        "commentary": text,
                                        **({"phase": str(evt.get("phase") or "").strip()} if str(evt.get("phase") or "").strip() else {}),
                                    }
                                    client_connected = _emit(payload)
                            elif et == "delta":
                                delta_payload = {"delta": str(evt.get("delta") or "")}
                                if plane_boundary_accepted:
                                    delta_payload["plane_routed"] = True
                                if client_connected:
                                    client_connected = _emit(delta_payload)
                            elif et == "start":
                                if client_connected:
                                    start_payload = {
                                        "type": "start",
                                        "turn_id": str(evt.get("turn_id") or ""),
                                    }
                                    if plane_boundary_accepted:
                                        start_payload["plane_routed"] = True
                                    client_connected = _emit(start_payload)
                            elif et == "done":
                                assistant_title = str(evt.get("assistant_title") or "").strip()
                                assistant = evt.get("assistant") if isinstance(evt.get("assistant"), dict) else {}
                                assistant_content = str(assistant.get("content") or evt.get("content") or "").strip()
                                followup = evt.get("assistant_followup") if isinstance(evt.get("assistant_followup"), dict) else {}
                                deferred_host_content = str(evt.get("deferred_host_verification_content") or "").strip()
                                if deferred_host_content:
                                    host_dispatch = _mobile_dispatch_host_verification_with_progress(
                                        content=deferred_host_content,
                                        role=role,
                                        binding=binding,
                                        session_file=Path(str(evt.get("deferred_host_verification_session_file") or "")),
                                        emit_progress=lambda text: _emit(
                                            {
                                                "event": "commentary",
                                                "commentary": str(text or ""),
                                                "phase": "host_verification_wait",
                                            }
                                        ),
                                    )
                                    followup = dict(host_dispatch.get("assistant_followup") or followup)
                                followup_content = str(followup.get("content") or "").strip()
                                if not assistant_title:
                                    meta = assistant.get("meta") if isinstance(assistant.get("meta"), dict) else {}
                                    assistant_title = str(meta.get("chat_header_title") or "").strip()
                                if assistant_title and client_connected:
                                    client_connected = _emit({"event": "metadata", "chat_title": assistant_title})
                                if bool(evt.get("ok")) and assistant_content:
                                    _mobile_session_append(tid, user_text=prompt, assistant_text=assistant_content)
                                    if followup_content:
                                        _mobile_session_append_assistant(tid, assistant_text=followup_content)
                                cancel_requested = bool(control.get("cancel_requested"))
                                if cancel_requested and bool(evt.get("ok")):
                                    _mobile_complete_active_stream(
                                        tid=tid,
                                        role=role,
                                        terminal_status="completed",
                                        terminal_message="The server turn finished before the cancel request landed.",
                                        control_id=control_id,
                                    )
                                elif cancel_requested:
                                    _mobile_complete_active_stream(
                                        tid=tid,
                                        role=role,
                                        terminal_status="cancelled",
                                        terminal_message="Server turn cancelled.",
                                        control_id=control_id,
                                    )
                                elif bool(evt.get("ok")):
                                    _mobile_complete_active_stream(
                                        tid=tid,
                                        role=role,
                                        terminal_status="completed",
                                        terminal_message="Stream completed.",
                                        control_id=control_id,
                                    )
                                else:
                                    _mobile_complete_active_stream(
                                        tid=tid,
                                        role=role,
                                        terminal_status="interrupted",
                                        terminal_message=str(evt.get("error") or "stream failed"),
                                        control_id=control_id,
                                    )
                                done_payload = {
                                    "type": "done",
                                    "ok": bool(evt.get("ok")),
                                }
                                error_text = str(evt.get("error") or "").strip()
                                if error_text:
                                    done_payload["error"] = error_text
                                if plane_boundary_accepted:
                                    done_payload["plane_routed"] = True
                                if client_connected:
                                    client_connected = _emit(done_payload)
                                    _emit_done()
                                break
                    finally:
                        keepalive_stop.set()
                        keepalive_thread.join(timeout=0.2)
                        _mobile_release_active_stream(tid=tid, role=role, control_id=control_id)
                except Exception as e:
                    _mobile_complete_active_stream(
                        tid=tid,
                        role=role,
                        terminal_status="interrupted",
                        terminal_message=str(e),
                        control_id=control_id,
                    )
                    _mobile_release_active_stream(tid=tid, role=role, control_id=control_id)
                    _emit({"event": "error", "error": str(e)})
                    _emit_done()
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/send":
            request_id = ""
            idempotency_key = ""
            boundary_result: Dict[str, Any] = {}
            try:
                obj = _read_request_json(self, max_bytes=512 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                role = obj.get("role")
                binding = _coerce_agents_binding(obj.get("binding"), raw_thread_id=obj.get("thread_id"))
                msg = obj.get("message")
                options = obj.get("options") if isinstance(obj.get("options"), dict) else {}
                request_id = str(obj.get("request_id") or "").strip() or f"agents_send_{uuid.uuid4().hex[:12]}"
                idempotency_key = str(obj.get("idempotency_key") or "").strip()
                boundary_payload = {
                    "binding": dict(binding),
                    "role": str(role or ""),
                    "message": str(msg or ""),
                    "options": dict(options),
                }
                boundary_result = _process_boundary_mutation(
                    command_id="send_turn",
                    payload=boundary_payload,
                    request_id=request_id,
                    idempotency_key=idempotency_key,
                )
                boundary_status = str(boundary_result.get("status") or "").strip().lower()
                if boundary_status != _backend_api_boundary.COMMAND_STATUS_ACCEPTED:
                    _json_response(
                        self,
                        _backend_boundary_http_status(boundary_result),
                        {
                            "schema_version": "agents_send_v1",
                            "utc": _now_utc_iso(),
                            "ok": False,
                            "boundary_result": boundary_result,
                        },
                    )
                    return
                payload = _agents.send_message(role=str(role or ""), binding=binding, message=str(msg or ""), options=options)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_send_v1",
                        "utc": _now_utc_iso(),
                        "boundary_result": boundary_result,
                        **payload,
                    },
                )
            except TimeoutError:
                timeout_result = _boundary_ambiguous_timeout_result(
                    command_id="send_turn",
                    request_id=request_id,
                    idempotency_key=idempotency_key,
                    accepted_result=boundary_result,
                )
                _json_response(
                    self,
                    _backend_boundary_http_status(timeout_result),
                    {
                        "schema_version": "agents_send_v1",
                        "utc": _now_utc_iso(),
                        "ok": False,
                        "boundary_result": timeout_result,
                    },
                )
            except _agents.PromptConfigError as e:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "ledger item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/prompts":
            try:
                obj = _read_request_json(self, max_bytes=1024 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _agents.update_prompt_text(
                    target=str(obj.get("target") or ""),
                    text=str(obj.get("text") or ""),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_prompts_update_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/send_stream":
            try:
                obj = _read_request_json(self, max_bytes=512 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                role = str(obj.get("role") or "generalist")
                binding = _coerce_agents_binding(obj.get("binding"), raw_thread_id=obj.get("thread_id"))
                msg = str(obj.get("message") or obj.get("prompt") or "")
                options = obj.get("options") if isinstance(obj.get("options"), dict) else {}

                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                def _emit(evt: dict) -> bool:
                    raw = f"data: {json.dumps(evt, ensure_ascii=False)}\n\n".encode("utf-8")
                    try:
                        self.wfile.write(raw)
                        self.wfile.flush()
                        return True
                    except Exception:
                        return False

                try:
                    for evt in _agents.send_message_stream_events(
                        role=role,
                        binding=binding,
                        message=msg,
                        options=options,
                    ):
                        if str(evt.get("type") or "") == "done":
                            assistant_title = str(evt.get("assistant_title") or "").strip()
                            if assistant_title and not _emit({"event": "metadata", "chat_title": assistant_title}):
                                break
                        if not _emit(evt):
                            break
                except _agents.PromptConfigError as e:
                    _emit({"type": "done", "ok": False, "error": str(e), "code": "PROMPT_CONFIG_ERROR"})
                except Exception as e:
                    _emit({"type": "done", "ok": False, "error": str(e), "code": "SEND_STREAM_ERROR"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/send_to":
            try:
                obj = _read_request_json(self, max_bytes=256 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _agents.send_to_role(
                    from_role=str(obj.get("from_role") or ""),
                    to_role=str(obj.get("to_role") or ""),
                    binding=obj.get("binding"),
                    message=str(obj.get("message") or ""),
                    also_enqueue=bool(obj.get("also_enqueue", True)),
                    request_context=(obj.get("request_context") if isinstance(obj.get("request_context"), dict) else {}),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_send_to_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "ledger item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/handoff":
            try:
                obj = _read_request_json(self, max_bytes=512 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _agents.export_handoff(
                    role=str(obj.get("role") or ""),
                    binding=obj.get("binding"),
                    selection=(obj.get("selection") if isinstance(obj.get("selection"), dict) else {"mode": "last_n", "n": 12}),
                    title=str(obj.get("title") or ""),
                    status=str(obj.get("status") or "READY"),
                    scope=str(obj.get("scope") or ""),
                    inputs=(obj.get("inputs") if isinstance(obj.get("inputs"), list) else []),
                    outputs=(obj.get("outputs") if isinstance(obj.get("outputs"), list) else []),
                    next_action=str(obj.get("next_action") or ""),
                    verify=(obj.get("verify") if isinstance(obj.get("verify"), list) else []),
                    notes=str(obj.get("notes") or ""),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_handoff_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "ledger item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/handoff_delete":
            try:
                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _agents.delete_latest_handoff(
                    role=str(obj.get("role") or ""),
                    binding=obj.get("binding"),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_handoff_delete_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "ledger item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/attach_to_ledger":
            try:
                obj = _read_request_json(self, max_bytes=256 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _agents.attach_to_ledger(
                    task_id=str(obj.get("task_id") or ""),
                    ledger_uid=str(obj.get("ledger_uid") or ""),
                    merge_strategy=str(obj.get("merge_strategy") or "copy"),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_attach_to_ledger_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "ledger item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/schedules/job/save":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=512 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                saved = _scheduled_jobs.save_job(obj)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "scheduled_job_save_v1",
                        "utc": _now_utc_iso(),
                        "job": _scheduled_jobs.job_summary(saved),
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/verification_workflow/launch":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=512 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                run_now = bool(obj.pop("run_now", False))
                payload = _verification_workflow_launch.create_scheduler_backed_verification_launch(
                    obj,
                    repo_root=_repo_root_from_webui(),
                    run_now=run_now,
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        **payload,
                        "schema_version": "verification_workflow_launch_v1",
                        "utc": _now_utc_iso(),
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/schedules/job/toggle":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                job_id = str(obj.get("job_id") or "").strip()
                mutation_guard = _scheduler_mutation_observation_guard(
                    action_id="job.toggle",
                    job_id=job_id,
                    observation_evidence=obj.get("observation_evidence") if isinstance(obj.get("observation_evidence"), dict) else {},
                    allow_stale_observation=bool(obj.get("allow_stale_observation")),
                )
                if not bool(mutation_guard.get("allowed")):
                    _json_response(
                        self,
                        HTTPStatus.CONFLICT,
                        {
                            "schema_version": "scheduled_job_toggle_v1",
                            "utc": _now_utc_iso(),
                            "ok": False,
                            "error": str(mutation_guard.get("reason_code") or "stale_observation_requires_ack"),
                            "mutation_guard": mutation_guard,
                        },
                    )
                    return
                saved = _scheduled_jobs.set_job_enabled(
                    job_id,
                    bool(obj.get("enabled")),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "scheduled_job_toggle_v1",
                        "utc": _now_utc_iso(),
                        "job": _scheduled_jobs.job_summary(saved),
                        "mutation_guard": mutation_guard,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "scheduled job not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/schedules/job/update_run_profile":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                max_iterations_raw = obj.get("max_iterations")
                saved = _scheduled_jobs.update_job_run_profile(
                    str(obj.get("job_id") or "").strip(),
                    model=str(obj.get("model") or "").strip() or None,
                    max_iterations=None if max_iterations_raw in (None, "") else int(max_iterations_raw),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "scheduled_job_update_run_profile_v1",
                        "utc": _now_utc_iso(),
                        "job": _scheduled_jobs.job_summary(saved),
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "scheduled job not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/schedules/run_now":
            if not _admin_guard(self, cfg, qs):
                return
            request_id = ""
            idempotency_key = ""
            boundary_result: Dict[str, Any] = {}
            try:
                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                job_id = str(obj.get("job_id") or "").strip()
                request_id = str(obj.get("request_id") or "").strip() or f"schedules_run_now_{uuid.uuid4().hex[:12]}"
                idempotency_key = str(obj.get("idempotency_key") or "").strip()
                boundary_result = _process_boundary_mutation(
                    command_id="job.trigger",
                    payload={"job_id": job_id},
                    request_id=request_id,
                    idempotency_key=idempotency_key,
                )
                boundary_status = str(boundary_result.get("status") or "").strip().lower()
                if boundary_status != _backend_api_boundary.COMMAND_STATUS_ACCEPTED:
                    _json_response(
                        self,
                        _backend_boundary_http_status(boundary_result),
                        {
                            "schema_version": "scheduled_job_run_now_v1",
                            "utc": _now_utc_iso(),
                            "ok": False,
                            "boundary_result": boundary_result,
                        },
                    )
                    return
                mutation_guard = _scheduler_mutation_observation_guard(
                    action_id="job.trigger",
                    job_id=job_id,
                    observation_evidence=obj.get("observation_evidence") if isinstance(obj.get("observation_evidence"), dict) else {},
                    allow_stale_observation=bool(obj.get("allow_stale_observation")),
                )
                if not bool(mutation_guard.get("allowed")):
                    _json_response(
                        self,
                        HTTPStatus.CONFLICT,
                        {
                            "schema_version": "scheduled_job_run_now_v1",
                            "utc": _now_utc_iso(),
                            "ok": False,
                            "error": str(mutation_guard.get("reason_code") or "stale_observation_requires_ack"),
                            "boundary_result": boundary_result,
                            "mutation_guard": mutation_guard,
                        },
                    )
                    return
                payload = _scheduled_jobs.request_run_now(
                    job_id,
                    request_source="manual",
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "scheduled_job_run_now_v1",
                        "utc": _now_utc_iso(),
                        "boundary_result": boundary_result,
                        "mutation_guard": mutation_guard,
                        **payload,
                    },
                )
            except TimeoutError:
                timeout_result = _boundary_ambiguous_timeout_result(
                    command_id="job.trigger",
                    request_id=request_id,
                    idempotency_key=idempotency_key,
                    accepted_result=boundary_result,
                )
                _json_response(
                    self,
                    _backend_boundary_http_status(timeout_result),
                    {
                        "schema_version": "scheduled_job_run_now_v1",
                        "utc": _now_utc_iso(),
                        "ok": False,
                        "boundary_result": timeout_result,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "scheduled job not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/schedules/tick":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=64 * 1024)
                if not isinstance(obj, dict):
                    obj = {}
                payload = _scheduled_jobs.scheduler_tick(limit=int(obj.get("limit") or 10))
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "scheduled_jobs_tick_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/schedules/job/cancel":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=256 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                job_id = str(obj.get("job_id") or "").strip()
                mutation_guard = _scheduler_mutation_observation_guard(
                    action_id="job.cancel",
                    job_id=job_id,
                    observation_evidence=obj.get("observation_evidence") if isinstance(obj.get("observation_evidence"), dict) else {},
                    allow_stale_observation=bool(obj.get("allow_stale_observation")),
                )
                if not bool(mutation_guard.get("allowed")):
                    _json_response(
                        self,
                        HTTPStatus.CONFLICT,
                        {
                            "schema_version": "scheduled_job_cancel_v1",
                            "utc": _now_utc_iso(),
                            "ok": False,
                            "error": str(mutation_guard.get("reason_code") or "stale_observation_requires_ack"),
                            "mutation_guard": mutation_guard,
                        },
                    )
                    return
                result = _scheduled_jobs.cancel_job(
                    job_id,
                    reason=str(obj.get("reason") or "operator_cancel"),
                    evidence=obj.get("evidence") if isinstance(obj.get("evidence"), dict) else {},
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "scheduled_job_cancel_v1",
                        "utc": _now_utc_iso(),
                        "mutation_guard": mutation_guard,
                        **result,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "scheduled job not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/schedules/job/postpone":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=256 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                job_id = str(obj.get("job_id") or "").strip()
                mutation_guard = _scheduler_mutation_observation_guard(
                    action_id="job.postpone",
                    job_id=job_id,
                    observation_evidence=obj.get("observation_evidence") if isinstance(obj.get("observation_evidence"), dict) else {},
                    allow_stale_observation=bool(obj.get("allow_stale_observation")),
                )
                if not bool(mutation_guard.get("allowed")):
                    _json_response(
                        self,
                        HTTPStatus.CONFLICT,
                        {
                            "schema_version": "scheduled_job_postpone_v1",
                            "utc": _now_utc_iso(),
                            "ok": False,
                            "error": str(mutation_guard.get("reason_code") or "stale_observation_requires_ack"),
                            "mutation_guard": mutation_guard,
                        },
                    )
                    return
                result = _scheduled_jobs.postpone_job(
                    job_id,
                    days=int(obj.get("days") or 1),
                    reason=str(obj.get("reason") or "operator_postpone"),
                    evidence=obj.get("evidence") if isinstance(obj.get("evidence"), dict) else {},
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "scheduled_job_postpone_v1",
                        "utc": _now_utc_iso(),
                        "mutation_guard": mutation_guard,
                        **result,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "scheduled job not found"})
            except ValueError as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/clear":
            try:
                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _agents.clear_session_data(
                    role=str(obj.get("role") or ""),
                    binding=obj.get("binding"),
                    target=str(obj.get("target") or "session"),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_clear_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "ledger item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/orchestration/start":
            try:
                obj = _read_request_json(self, max_bytes=512 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                opts = (obj.get("options") if isinstance(obj.get("options"), dict) else {})
                if obj.get("heartbeat_interval_s") is not None and opts.get("heartbeat_interval_s") is None:
                    opts["heartbeat_interval_s"] = obj.get("heartbeat_interval_s")
                if obj.get("timeout_s") is not None and opts.get("timeout_s") is None:
                    opts["timeout_s"] = obj.get("timeout_s")
                payload = _agent_orch.start_workflow(
                    binding=obj.get("binding"),
                    phase_label=str(obj.get("phase_label") or ""),
                    objective=str(obj.get("objective") or ""),
                    options=opts,
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_orchestration_start_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "workflow not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/swarm/start":
            try:
                obj = _read_request_json(self, max_bytes=512 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _agent_swarm.start_swarm(
                    binding=obj.get("binding"),
                    objective=str(obj.get("objective") or ""),
                    options=(obj.get("options") if isinstance(obj.get("options"), dict) else {}),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_swarm_start_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "swarm not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/benchmark/start":
            try:
                obj = _read_request_json(self, max_bytes=512 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                binding = _coerce_agents_binding(obj.get("binding"), raw_thread_id=obj.get("thread_id"))
                objective = _resolve_bound_benchmark_objective(
                    binding=binding,
                    explicit_objective=str(obj.get("objective") or ""),
                )
                if not objective:
                    raise ValueError(_bound_benchmark_missing_objective_error())
                payload = _agent_benchmark.start_background_benchmark(
                    objective=objective,
                    binding=binding,
                    label=str(obj.get("label") or "").strip() or None,
                    git_ref=str(obj.get("git_ref") or "HEAD").strip() or "HEAD",
                    check_commands=obj.get("check_commands") if isinstance(obj.get("check_commands"), list) else None,
                    workflow_enabled=bool(obj.get("workflow_enabled", True)),
                    swarm_enabled=bool(obj.get("swarm_enabled", True)),
                    workflow_options=obj.get("workflow_options") if isinstance(obj.get("workflow_options"), dict) else None,
                    swarm_options=obj.get("swarm_options") if isinstance(obj.get("swarm_options"), dict) else None,
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_benchmark_start_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "benchmark not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/orchestration/step":
            try:
                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _agent_orch.step_workflow(
                    workflow_id=str(obj.get("workflow_id") or "").strip(),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_orchestration_step_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "workflow not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        # Resume/drive an existing persisted workflow. This endpoint never
        # creates a new workflow; governance/agents/runtime/orchestration is
        # the authoritative source of truth for what continues here.
        if path == "/api/agents/orchestration/run":
            try:
                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _agent_orch.run_workflow(
                    workflow_id=str(obj.get("workflow_id") or "").strip(),
                    max_steps=int(obj.get("max_steps") or 20),
                    heartbeat_interval_s=(int(obj.get("heartbeat_interval_s")) if obj.get("heartbeat_interval_s") is not None else None),
                    timeout_s=(int(obj.get("timeout_s")) if obj.get("timeout_s") is not None else None),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_orchestration_run_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "workflow not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/orchestration/cancel":
            try:
                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _agent_orch.cancel_workflow(
                    workflow_id=str(obj.get("workflow_id") or "").strip(),
                    reason=str(obj.get("reason") or "").strip(),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_orchestration_cancel_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "workflow not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/agents/swarm/cancel":
            try:
                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _agent_swarm.cancel_swarm(
                    swarm_id=str(obj.get("swarm_id") or "").strip(),
                    reason=str(obj.get("reason") or "").strip(),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "agents_swarm_cancel_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "swarm not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/backlog/status":
            try:
                obj = _read_request_json(self, max_bytes=64 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                uid = str(obj.get("uid") or "").strip()
                if not uid.startswith("PIS-"):
                    raise ValueError("uid must be a PIS item")
                st = str(obj.get("status") or "").strip()
                note = str(obj.get("note") or "")
                sc, payload = _apply_ledger_status_update(uid=uid, status=st, note=note)
                _json_response(
                    self,
                    sc,
                    {
                        "schema_version": "mobile_backlog_status_update_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/backlog/prioritize":
            try:
                obj = _read_request_json(self, max_bytes=64 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                uid = str(obj.get("uid") or "").strip()
                sc, payload = _apply_mobile_backlog_priority_update(uid=uid)
                _json_response(
                    self,
                    sc,
                    {
                        "schema_version": "mobile_backlog_priority_update_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/backlog/execution_queue/start":
            try:
                obj = _read_request_json(self, max_bytes=64 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                workflow_options = obj.get("workflow_options") if isinstance(obj.get("workflow_options"), dict) else {}
                if obj.get("max_iterations") is not None and workflow_options.get("max_iterations") is None:
                    workflow_options["max_iterations"] = obj.get("max_iterations")
                if obj.get("execution_directive") is not None and workflow_options.get("execution_directive") is None:
                    workflow_options["execution_directive"] = obj.get("execution_directive")
                if obj.get("commit_timeout_s") is not None and workflow_options.get("commit_timeout_s") is None:
                    workflow_options["commit_timeout_s"] = obj.get("commit_timeout_s")
                payload = _execution_queue.start_queue(
                    options={
                        "phase_label": str(obj.get("phase_label") or ""),
                        "workflow_options": workflow_options,
                        "commit_each_success": bool(obj.get("commit_each_success", True)),
                        "selected_uids": list(obj.get("selected_uids") or []),
                        "selection_mode": str(obj.get("selection_mode") or "selected_order"),
                    }
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "mobile_execution_queue_start_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/cockpit/campaigns/launch":
            try:
                obj = _read_request_json(self, max_bytes=128 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                workflow_options = obj.get("workflow_options") if isinstance(obj.get("workflow_options"), dict) else {}
                if obj.get("max_iterations") is not None and workflow_options.get("max_iterations") is None:
                    workflow_options["max_iterations"] = obj.get("max_iterations")
                payload = _mobile_cockpit.launch_campaign(
                    selected_uids=list(obj.get("selected_uids") or []),
                    dependency_mode=str(obj.get("dependency_mode") or "selected_order"),
                    phase_label=str(obj.get("phase_label") or ""),
                    workflow_options=workflow_options,
                    commit_each_success=bool(obj.get("commit_each_success", True)),
                    origin_surface="the_plane_mobile_backlog",
                )
                status_code = HTTPStatus.OK if str((payload.get("campaign") or {}).get("launch_outcome") or "") != "rejected" else HTTPStatus.BAD_REQUEST
                _json_response(self, status_code, payload)
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/cockpit/artifacts/handoff":
            try:
                obj = _read_request_json(self, max_bytes=256 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                ledger_uid = str(obj.get("ledger_uid") or "").strip()
                if not ledger_uid.startswith("PIS-"):
                    raise ValueError("ledger_uid must be a PIS item")
                payload = _agents.export_handoff(
                    role=str(obj.get("role") or "builder"),
                    binding={"type": "ledger_uid", "id": ledger_uid},
                    selection=(obj.get("selection") if isinstance(obj.get("selection"), dict) else {"mode": "last_n", "n": 12}),
                    title=str(obj.get("title") or "Mobile cockpit handoff"),
                    status=str(obj.get("status") or "READY"),
                    scope=str(obj.get("scope") or "Artifact mobility export from the plane mobile cockpit."),
                    inputs=(obj.get("inputs") if isinstance(obj.get("inputs"), list) else []),
                    outputs=(obj.get("outputs") if isinstance(obj.get("outputs"), list) else []),
                    next_action=str(obj.get("next_action") or ""),
                    verify=(obj.get("verify") if isinstance(obj.get("verify"), list) else []),
                    notes=str(obj.get("notes") or ""),
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "mobile_cockpit_artifact_handoff_v1",
                        "utc": _now_utc_iso(),
                        **payload,
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/cockpit/archive/create":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=64 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                debug = bool(obj.get("debug"))
                rr = _admin_repo_root()
                zip_path, stdout, stderr, rc = _admin_run_snapshot_script(rr, debug=debug)
                if not zip_path:
                    _json_response(
                        self,
                        HTTPStatus.BAD_REQUEST,
                        {
                            "schema_version": "mobile_cockpit_archive_create_v1",
                            "utc": _now_utc_iso(),
                            "ok": False,
                            "error": "archive snapshot failed",
                            "returncode": int(rc),
                            "stdout": stdout,
                            "stderr": stderr,
                        },
                    )
                    return
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "mobile_cockpit_archive_create_v1",
                        "utc": _now_utc_iso(),
                        "ok": True,
                        "snapshot_id": zip_path.name,
                        "path": str(zip_path),
                        "download_url": f"/api/admin/archive/download/{zip_path.name}",
                    },
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/mobile/backlog/execution_queue/stop":
            try:
                obj = _read_request_json(self, max_bytes=32 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                payload = _execution_queue.stop_queue(mode=str(obj.get("mode") or "after_current"))
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "mobile_execution_queue_stop_v1", "utc": _now_utc_iso(), **payload},
                )
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return


        # ------------------------------------------------------------------
        # Change Ledger mutation endpoints
        # ------------------------------------------------------------------

        if path.startswith("/api/ledger/item/"):
            rest = path[len("/api/ledger/item/"):].strip("/")
            parts = rest.split("/")

            uid = parts[0]
            action = parts[1] if len(parts) > 1 else None

            ledger = ChangeLedger()

            if action == "status":
                if not _admin_guard(self, cfg, qs):
                    return
                try:
                    obj = _read_request_json(self, max_bytes=64 * 1024)
                    if not isinstance(obj, dict):
                        raise ValueError("body must be a JSON object")
                    st = str(obj.get("status") or "").strip()
                    note = str(obj.get("note") or "")
                    sc, payload = _apply_ledger_status_update(uid=uid, status=st, note=note)
                    _json_response(self, sc, payload)
                except FileNotFoundError:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
                except Exception as e:
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

            if action == "update_meta":
                if not _admin_guard(self, cfg, qs):
                    return
                try:
                    obj = _read_request_json(self, max_bytes=128 * 1024)
                    if not isinstance(obj, dict):
                        raise ValueError("body must be a JSON object")
                    res = ledger.update_meta(uid, obj)
                    _json_response(self, HTTPStatus.OK, {"schema_version": "ledger_update_meta_v1", "utc": _now_utc_iso(), **res})
                except FileNotFoundError:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
                except Exception as e:
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

            if action == "update_pis":
                if not _admin_guard(self, cfg, qs):
                    return
                try:
                    obj = _read_request_json(self, max_bytes=1024 * 1024)
                    if not isinstance(obj, dict):
                        raise ValueError("body must be a JSON object")
                    pis_md = str(obj.get("pis_markdown") or obj.get("pis_md") or "")
                    res = ledger.update_pis(uid, pis_md)
                    _json_response(self, HTTPStatus.OK, {"schema_version": "ledger_update_pis_v1", "utc": _now_utc_iso(), **res})
                except FileNotFoundError:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
                except Exception as e:
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

            if action == "create_revision":
                if not _admin_guard(self, cfg, qs):
                    return
                try:
                    obj = _read_request_json(self, max_bytes=64 * 1024)
                    if not isinstance(obj, dict):
                        obj = {}
                    title = obj.get("title")
                    slug = obj.get("slug")
                    deprecate_old = bool(obj.get("deprecate_old", True))
                    res = ledger.create_revision(
                        uid,
                        title=(str(title).strip() if title is not None else None),
                        slug=(str(slug).strip() if slug is not None else None),
                        deprecate_old=deprecate_old,
                    )
                    _json_response(self, HTTPStatus.OK, {"schema_version": "ledger_create_revision_v1", "utc": _now_utc_iso(), **res})
                except FileNotFoundError:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
                except Exception as e:
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

            if action == "mark_verified":
                if not _admin_guard(self, cfg, qs):
                    return
                try:
                    payload = _ledger_actions.mark_verified(uid=uid)
                    status_code = HTTPStatus.OK if bool(payload.get("ok")) else HTTPStatus.CONFLICT
                    _json_response(
                        self,
                        status_code,
                        {"schema_version": "ledger_mark_verified_v1", "utc": _now_utc_iso(), **payload},
                    )
                except FileNotFoundError:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
                except Exception as e:
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

            if action == "advance":
                if not _admin_guard(self, cfg, qs):
                    return
                try:
                    obj = _read_request_json(self, max_bytes=64 * 1024)
                    if not isinstance(obj, dict):
                        obj = {}
                    note = str(obj.get("note") or "")
                    requested_next = str(obj.get("next_status") or "")
                    payload = _ledger_actions.advance_status(uid=uid, note=note, requested_next=requested_next)
                    status_code = HTTPStatus.OK if bool(payload.get("ok")) else HTTPStatus.CONFLICT
                    _json_response(
                        self,
                        status_code,
                        {"schema_version": "ledger_advance_status_v1", "utc": _now_utc_iso(), **payload},
                    )
                except FileNotFoundError:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "item not found"})
                except Exception as e:
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return



        if path == "/api/admin/backlog/gpt_submit":
            # Desktop backlog QoL: batch-select PIS entries and submit to GPT.
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=256 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                uids = obj.get("uids")
                if not isinstance(uids, list) or not uids:
                    raise ValueError("missing uids")
                uids_s = [str(x).strip() for x in uids if str(x).strip()]
                if not uids_s:
                    raise ValueError("uids empty")
                if len(uids_s) > 20:
                    raise ValueError("too many uids (max 20)")

                model = str(obj.get("model") or "gpt-5.4").strip() or "gpt-5.4"

                # Load backlog.
                pb_path = _patch_backlog_path(cfg)
                backlog = _read_json(pb_path)
                if not isinstance(backlog, dict):
                    backlog = _read_json(_legacy_patch_backlog_path(cfg))
                if not isinstance(backlog, dict) or backlog.get("schema_version") != "patch_backlog_v1":
                    backlog = {"schema_version": "patch_backlog_v1", "items": []}
                items = backlog.get("items") if isinstance(backlog.get("items"), list) else []
                items = list(items)

                # Resolve latest repo snapshot archive (./archives) by mtime.
                rr = _admin_repo_root()
                arcs = _admin_list_archives(rr)
                if not arcs:
                    raise FileNotFoundError("no repo snapshot archives found under ./archives")
                arc_name = str(arcs[0].get("name") or "").strip()
                if not arc_name:
                    raise FileNotFoundError("latest archive entry missing name")
                arc_path = (Path(rr) / "archives" / arc_name).resolve()
                safe_arc = _safe_resolve_under(Path(rr).resolve(), arc_path)
                if not safe_arc or not safe_arc.exists() or not safe_arc.is_file():
                    raise FileNotFoundError(f"archive not found: {arc_name}")

                # Derive baseline id.
                baseline_id = ""
                m = re.search(r"__baseline-([0-9]+)__", arc_name)
                if m:
                    baseline_id = f"baseline-{m.group(1)}"
                if not baseline_id:
                    rc_d, out_d, _err_d = _run_git(["describe", "--tags", "--always"])
                    desc = str(out_d or "").strip()
                    baseline_id = desc if desc else "HEAD"


                # Prepare output directory early so we can persist errors even if
                # OpenAI requests fail.
                out_dir = _backlog_gpt_outputs_dir(cfg)
                out_dir.mkdir(parents=True, exist_ok=True)
                utc = _now_utc_iso()
                ts = utc.replace(":", "").replace("-", "")

                # Upload archive once.
                try:
                    from infra.webui import gpt_client as _gpt  # noqa: WPS433
                except Exception:
                    import infra.webui.gpt_client as _gpt  # type: ignore  # noqa: WPS433

                up = _gpt.upload_file(str(safe_arc), purpose="user_data")

                def _find_item_idx(uid: str) -> int:
                    for j, it in enumerate(items):
                        if str((it or {}).get("id") or (it or {}).get("uid") or "").strip() == uid:
                            return j
                    return -1

                results: List[Dict[str, Any]] = []
                submitted = 0
                for uid in uids_s:
                    idx = _find_item_idx(uid)
                    if idx < 0:
                        results.append({"uid": uid, "integration_state": "error", "error": "uid not found"})
                        continue
                    it = items[idx] if isinstance(items[idx], dict) else {}
                    pis_md = str(it.get("pis_markdown") or "").strip()
                    title = str(it.get("title") or "").strip()

                    prompt = (
                        "You are a Code Base Patch Integrator.\n\n"
                        f"INTEGRATE NOW: Apply the attached PIS on the {baseline_id} ({arc_name}).\n\n"
                        "End.\n\n"
                        "---\n\n"
                        f"PIS UID: {uid}\n"
                        f"PIS Title: {title}\n\n"
                        f"{pis_md}\n"
                    )

                    integration_state = "done"
                    err_msg = ""
                    out_rel = ""
                    gpt_text = ""
                    try:

                        # NOTE: OpenAI rejects .zip as an "input_file" for context
                        # stuffing. Repo snapshot archives are .zip, so we attach
                        # them to the code_interpreter container instead.
                        if str(arc_name).lower().endswith(".zip"):
                            tools_payload = [
                                {
                                    "type": "code_interpreter",
                                    "container": {"type": "auto", "file_ids": [str(up.file_id)]},
                                }
                            ]
                            resp = _gpt.create_response(model=model, input_text=prompt, tools=tools_payload)
                        else:
                            resp = _gpt.create_response(model=model, input_text=prompt, file_id=up.file_id)
                        gpt_text = _gpt.extract_output_text(resp)
                        fn = f"{uid}__{ts}.md"
                        fp = (out_dir / fn).resolve()
                        fp.write_text(
                            "\n".join(
                                [
                                    f"# Backlog GPT Output\n",
                                    f"- uid: {uid}",
                                    f"- title: {title}",
                                    f"- utc: {utc}",
                                    f"- model: {model}",
                                    f"- baseline_id: {baseline_id}",
                                    f"- archive: {arc_name}",
                                    f"- file_id: {up.file_id}",
                                    "\n---\n",
                                    gpt_text.strip() if gpt_text else json.dumps(resp, ensure_ascii=False, indent=2),
                                    "\n",
                                ]
                            ),
                            encoding="utf-8",
                        )
                        out_rel = _posix_relpath(fp, Path(cfg.runs_root))
                        submitted += 1
                    except Exception as e:
                        integration_state = "error"
                        err_msg = str(e)

                        # Persist error output so the UI/debugging can see it.
                        try:
                            fn = f"{uid}__{ts}.md"
                            fp = (out_dir / fn).resolve()
                            fp.write_text(
                                "\n".join(
                                    [
                                        f"# Backlog GPT Output (ERROR)\n",
                                        f"- uid: {uid}",
                                        f"- title: {title}",
                                        f"- utc: {utc}",
                                        f"- model: {model}",
                                        f"- baseline_id: {baseline_id}",
                                        f"- archive: {arc_name}",
                                        f"- file_id: {getattr(up, 'file_id', '')}",
                                        "\n---\n",
                                        err_msg,
                                        "\n",
                                    ]
                                ),
                                encoding="utf-8",
                            )
                            out_rel = _posix_relpath(fp, Path(cfg.runs_root))
                        except Exception:
                            # Never let persistence failures mask the original error.
                            pass

                    # Update backlog item status + meta.
                    meta = dict(it.get("integration_meta") or {}) if isinstance(it.get("integration_meta"), dict) else {}
                    meta.update(
                        {
                            "integration_state": integration_state,
                            "archive": arc_name,
                            "baseline_id": baseline_id,
                            "model": model,
                            "output_path": out_rel,
                            "utc": utc,
                            "error": err_msg,
                        }
                    )
                    it2 = dict(it)
                    it2["status"] = "currently_in_integration"
                    it2["updated_utc"] = utc
                    it2["integration_meta"] = meta
                    items[idx] = it2

                    results.append({"uid": uid, "integration_state": integration_state, "output_path": out_rel, "error": err_msg})

                backlog["items"] = items
                atomic_write_canonical_json(pb_path, backlog)

                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "utc": utc,
                        "archive": arc_name,
                        "baseline_id": baseline_id,
                        "submitted": int(submitted),
                        "results": results,
                    },
                )
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        if path == "/api/admin/cmd/run":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=256 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                cmd = str(obj.get("cmd") or "").strip()
                if not cmd:
                    raise ValueError("missing cmd")
                cwd = str(obj.get("cwd") or "").strip()
                try:
                    timeout_sec = float(obj.get("timeout_sec", 60.0) or 60.0)
                except Exception:
                    timeout_sec = 60.0
                timeout_sec = float(max(1.0, min(6 * 60 * 60, timeout_sec)))  # cap at 6h
                stdin_txt = obj.get("stdin")
                stdin_bytes = None
                if isinstance(stdin_txt, str) and stdin_txt:
                    stdin_bytes = stdin_txt.encode("utf-8")
                job = _admin_cmd_start_job(state, cfg, cmd=cmd, cwd_rel=cwd, kind="cmd", timeout_sec=timeout_sec, stdin_bytes=stdin_bytes)
                _json_response(self, HTTPStatus.OK, {"schema_version": "admin_cmd_run_v1", "utc": _now_utc_iso(), "job": job.to_dict()})
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        if path == "/api/admin/mobile_apk/build":
            if not _mobile_update_guard(self, cfg):
                return
            try:
                obj = _read_request_json(self, max_bytes=64 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                channel = str(obj.get("channel") or "").strip().lower()
                if channel not in _MOBILE_APK_CHANNEL_META:
                    raise ValueError("channel must be debug or release")
                running = _mobile_apk_running_build_job(state, channel)
                if running is not None:
                    _json_response(
                        self,
                        HTTPStatus.OK,
                        {"schema_version": "mobile_apk_build_v1", "utc": _now_utc_iso(), "job": running.to_dict(), "reused": True},
                    )
                    return
                rr = _admin_repo_root()
                cmd = _mobile_apk_build_command(rr, channel)
                job = _admin_cmd_start_job(
                    state,
                    cfg,
                    cmd=cmd,
                    cwd_rel="",
                    kind=_mobile_apk_build_kind(channel),
                    timeout_sec=3600.0,
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"schema_version": "mobile_apk_build_v1", "utc": _now_utc_iso(), "job": job.to_dict(), "reused": False},
                )
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        if path == "/api/admin/mobile_apk/cancel":
            if not _mobile_update_guard(self, cfg):
                return
            try:
                status, payload = _mobile_apk_cancel_response_payload(self, state=state, cfg=cfg, qs=qs)
                _json_response(self, status, payload)
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        if path == "/api/admin/cmd/kill":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                jid = ""
                ctype = str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if ctype == "application/json":
                    obj = _read_request_json(self, max_bytes=64 * 1024)
                    if isinstance(obj, dict):
                        jid = str(obj.get("job_id") or "").strip()
                if not jid:
                    jid = str((qs.get("job_id") or [""])[0] or "").strip()
                if not jid:
                    raise ValueError("missing job_id")
                ok = _admin_cmd_kill_job(state, jid)
                with state.lock:
                    job = state.admin_cmd_jobs.get(jid)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "admin_cmd_kill_v1",
                        "utc": _now_utc_iso(),
                        "ok": bool(ok),
                        "job": job.to_dict() if job else None,
                    },
                )
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        if path in {"/api/system/processes/terminate", "/api/system/processes/kill"}:
            try:
                obj = _read_request_json(self, max_bytes=64 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                pid = int(obj.get("pid") or 0)
                hard = path.endswith("/kill")
                payload = _system_signal_process(cfg, pid=pid, hard=hard)
                _json_response(self, HTTPStatus.OK, payload)
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        # ------------------------------------------------------------------
        # Admin: manifest job control (durable, restart-safe)
        # ------------------------------------------------------------------
        if path in {"/api/admin/manifests/stop", "/api/admin/manifests/pause", "/api/admin/manifests/resume"}:
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=64 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                job_id = str(obj.get("job_id") or "").strip()
                if not job_id:
                    raise ValueError("missing job_id")
                job = _jobreg.load_job(cfg.runs_root, job_id)
                if not job:
                    raise FileNotFoundError("job not found")

                # Reconcile early so we can infer missing fields (e.g. report_dir)
                # from the manifest path and update liveness flags.
                try:
                    job = _jobreg.reconcile_job(job)
                except Exception:
                    pass

                report_dir = str(job.get("report_dir") or "").strip()
                if not report_dir:
                    # Best-effort: cannot control without a report directory.
                    raise ValueError("job has no report_dir (cannot write sentinels)")

                report_dir_p = Path(report_dir).resolve()
                # Safety: report_dir must live under repo root OR runs_root.
                # (Stops directory traversal in case a record is corrupted.)
                rr = _admin_repo_root()
                safe = _safe_resolve_under(rr, report_dir_p) or _safe_resolve_under(Path(cfg.runs_root).resolve(), report_dir_p)
                if safe is None:
                    raise ValueError("unsafe report_dir")

                if path.endswith("/pause"):
                    _jobreg.write_pause_sentinel(report_dir_p, requested_by="webui")
                    job["state"] = "PAUSED"
                    job["updated_at_utc"] = _now_utc_iso()
                    _jobreg.write_job(cfg.runs_root, job)
                    _json_response(self, HTTPStatus.OK, {"ok": True, "utc": _now_utc_iso(), "job": job})
                    return

                if path.endswith("/resume"):
                    ok = _jobreg.remove_pause_sentinel(report_dir_p)
                    # Do not force RUNNING if process is already dead.
                    job = _jobreg.reconcile_job(job)
                    if str(job.get("state") or "").upper() == "PAUSED" and bool(job.get("pid_alive", True)):
                        job["state"] = "RUNNING"
                    job["updated_at_utc"] = _now_utc_iso()
                    _jobreg.write_job(cfg.runs_root, job)
                    _json_response(self, HTTPStatus.OK, {"ok": bool(ok), "utc": _now_utc_iso(), "job": job})
                    return

                # /stop
                mode = str(obj.get("mode") or "graceful").strip().lower()
                if mode not in {"graceful", "force"}:
                    raise ValueError("mode must be 'graceful' or 'force'")
                _jobreg.write_stop_sentinel(report_dir_p, mode=mode, requested_by="webui", reason=str(obj.get("reason") or ""))
                job["state"] = "STOP_REQUESTED" if mode == "graceful" else "STOPPING"
                job["updated_at_utc"] = _now_utc_iso()

                # Force: kill process group.
                if mode == "force":
                    pid = int(job.get("pid") or 0)
                    if pid > 0:
                        try:
                            os.killpg(pid, signal.SIGTERM)
                        except Exception:
                            try:
                                os.kill(pid, signal.SIGTERM)
                            except Exception:
                                pass
                        time.sleep(0.3)
                        try:
                            os.killpg(pid, signal.SIGKILL)
                        except Exception:
                            try:
                                os.kill(pid, signal.SIGKILL)
                            except Exception:
                                pass

                job = _jobreg.reconcile_job(job)
                _jobreg.write_job(cfg.runs_root, job)
                _json_response(self, HTTPStatus.OK, {"ok": True, "utc": _now_utc_iso(), "job": job})
                return
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "job not found"})
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return


        if path == "/api/admin/git/run":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=256 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")

                do_commit = bool(obj.get("do_commit", True))
                do_tag = bool(obj.get("do_tag", True))
                msg = str(obj.get("message") or "").strip()
                message_mode = str(obj.get("message_mode") or ("manual" if msg else "journal")).strip().lower()

                if not do_commit or not do_tag:
                    raise ValueError("admin git run now requires both commit and tag via git-commit-tag.sh")
                if message_mode != "journal":
                    raise ValueError("admin git run now requires journal mode via git-commit-tag.sh")

                rr = _admin_repo_root()
                script = _admin_find_git_script(rr, "git-commit-tag.sh")
                safe_script = _safe_resolve_under(rr, script)
                if not safe_script or not safe_script.exists():
                    raise FileNotFoundError(f"missing unified git runner: {script}")

                try:
                    timeout_sec = float(obj.get("timeout_sec", 900.0) or 900.0)
                except Exception:
                    timeout_sec = 900.0

                parts: List[str] = []
                parts.append('echo "[git] unified actor path: tools/git/git-commit-tag.sh"')
                parts.append(f"bash {shlex.quote(str(safe_script))}")

                # QoL: if tagging succeeded, auto-create a debug-only repo snapshot archive.
                # This is tooling-only convenience and does not affect simulation determinism.
                archive_script = rr / "tools" / "bin" / "archive_complete_repo_snapshot.sh"
                safe_archive = _safe_resolve_under(rr, archive_script)
                if safe_archive and safe_archive.exists():
                    parts.append('echo "[auto-archive] creating debug snapshot..."')
                    parts.append(f"bash {shlex.quote(str(safe_archive))} --debug")
                else:
                    parts.append('echo "[auto-archive] SKIP (archive_complete_repo_snapshot.sh missing)"')
                seq = " && ".join(parts) if parts else "true"
                cmd = f"bash -lc {shlex.quote(seq)}"
                stdin_bytes = None

                job = _admin_cmd_start_job(
                    state,
                    cfg,
                    cmd=cmd,
                    cwd_rel="",
                    kind="git_run",
                    timeout_sec=float(max(5.0, min(6 * 60 * 60, timeout_sec))),
                    stdin_bytes=stdin_bytes,
                )
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "admin_git_run_v1",
                        "utc": _now_utc_iso(),
                        "message_mode": "journal",
                        "job": job.to_dict(),
                    },
                )
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        if path == "/api/admin/git/commit":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                raise ValueError("deprecated endpoint: use /api/admin/git/run backed by tools/git/git-commit-tag.sh")
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        if path == "/api/admin/git/tag":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                raise ValueError("deprecated endpoint: use /api/admin/git/run backed by tools/git/git-commit-tag.sh")
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        if path == "/api/admin/server/restart":
            if not _admin_guard(self, cfg, qs):
                return
            repo_root = _repo_root_from_webui()
            actor = _admin_control_action_actor(self, cfg, qs, repo_root=repo_root)
            try:
                obj = _read_request_json(self, max_bytes=64 * 1024)
            except Exception:
                obj = {}
            force = bool((obj or {}).get("force"))
            preflight = _admin_restart_preflight(cfg, state=self.server.state)
            if not preflight.get("safe_to_restart"):
                audit_entry = _control_action_audit.begin_control_action(
                    repo_root,
                    action_id="server_restart",
                    actor=actor,
                    params={"force": force},
                    writer="api.admin.server.restart",
                    request_path=path,
                    target={"surface": "the plane", "resource": "webui_server"},
                    policy_snapshot={"restart_preflight": dict(preflight or {})},
                    authorization={"state": "authorized", "method": "admin_guard"},
                    decision={"state": "denied", "reason_code": "restart_blocked_active_work"},
                    executor={"state": "not_started"},
                    verification={
                        "state": "denied",
                        "summary": "Restart was denied by preflight because active work or live scheduler claims remain.",
                        "verified_at_utc": _now_utc_iso(),
                    },
                    result={"outcome": "denied", "reason_code": "restart_blocked_active_work"},
                )
                _control_action_audit.finalize_control_action(
                    repo_root,
                    audit_id=str(audit_entry.get("audit_id") or ""),
                    action_id="server_restart",
                    actor=actor,
                    params={"force": force},
                    writer="api.admin.server.restart",
                    request_path=path,
                    target={"surface": "the plane", "resource": "webui_server"},
                    policy_snapshot={"restart_preflight": dict(preflight or {})},
                    authorization={"state": "authorized", "method": "admin_guard"},
                    decision={"state": "denied", "reason_code": "restart_blocked_active_work"},
                    executor={"state": "not_started"},
                    verification={
                        "state": "denied",
                        "summary": "Restart was denied by preflight because active work or live scheduler claims remain.",
                        "verified_at_utc": _now_utc_iso(),
                    },
                    result={"outcome": "denied", "reason_code": "restart_blocked_active_work"},
                    requested_at_utc=str(audit_entry.get("requested_at_utc") or ""),
                    finalized=True,
                )
                _json_response(
                    self,
                    HTTPStatus.CONFLICT,
                    {
                        **preflight,
                        "ok": False,
                        "error": "restart_blocked_active_work",
                        "message": "Restart blocked because the plane still has active work or live scheduler claims.",
                        "force_requested": force,
                    },
                )
                return
            audit_entry = _control_action_audit.begin_control_action(
                repo_root,
                action_id="server_restart",
                actor=actor,
                params={"force": force},
                writer="api.admin.server.restart",
                request_path=path,
                target={"surface": "the plane", "resource": "webui_server"},
                policy_snapshot={"restart_preflight": dict(preflight or {})},
                authorization={"state": "authorized", "method": "admin_guard"},
                decision={"state": "accepted", "reason_code": "restart_scheduled"},
                executor={"state": "restart_scheduled"},
                verification={
                    "state": "pending_restart_reconciliation",
                    "summary": "Restart was accepted and scheduled. Startup reconciliation must confirm the interrupted handoff honestly.",
                },
                result={"outcome": "accepted", "reason_code": "restart_scheduled"},
            )
            def _restart_later() -> None:
                try:
                    time.sleep(0.6)
                    _admin_restart_webui(cfg)
                except Exception as exc:
                    _control_action_audit.finalize_control_action(
                        repo_root,
                        audit_id=str(audit_entry.get("audit_id") or ""),
                        action_id="server_restart",
                        actor=actor,
                        params={"force": force},
                        writer="api.admin.server.restart",
                        request_path=path,
                        target={"surface": "the plane", "resource": "webui_server"},
                        policy_snapshot={"restart_preflight": dict(preflight or {})},
                        authorization={"state": "authorized", "method": "admin_guard"},
                        decision={"state": "accepted", "reason_code": "restart_launch_failed"},
                        executor={"state": "restart_launch_failed", "error": str(exc)},
                        verification={
                            "state": "failed",
                            "summary": "Restart scheduling failed before the process handoff completed.",
                            "verified_at_utc": _now_utc_iso(),
                        },
                        result={"outcome": "failed", "reason_code": "restart_launch_failed", "detail": str(exc)},
                        requested_at_utc=str(audit_entry.get("requested_at_utc") or ""),
                        finalized=True,
                    )
                    return
            threading.Thread(target=_restart_later, daemon=True).start()
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "admin_server_restart_v1",
                    "utc": _now_utc_iso(),
                    "ok": True,
                    "message": "restarting",
                    "force": force,
                    "force_requested": force,
                    "preflight": preflight,
                },
            )
            return

        if path == "/api/admin/server/refresh_runtime":
            if not _admin_guard(self, cfg, qs):
                return
            repo_root = _repo_root_from_webui()
            actor = _admin_control_action_actor(self, cfg, qs, repo_root=repo_root)
            audit_entry = _control_action_audit.begin_control_action(
                repo_root,
                action_id="refresh_runtime",
                actor=actor,
                params={},
                writer="api.admin.server.refresh_runtime",
                request_path=path,
                target={"surface": "the plane", "resource": "runtime_policy_snapshots"},
                authorization={"state": "authorized", "method": "admin_guard"},
                decision={"state": "accepted", "reason_code": "refresh_requested"},
                executor={"state": "running"},
                verification={"state": "pending", "summary": "Runtime refresh was requested and is executing."},
            )
            try:
                payload = _refresh_live_runtime_snapshot()
                _control_action_audit.finalize_control_action(
                    repo_root,
                    audit_id=str(audit_entry.get("audit_id") or ""),
                    action_id="refresh_runtime",
                    actor=actor,
                    params={},
                    writer="api.admin.server.refresh_runtime",
                    request_path=path,
                    target={"surface": "the plane", "resource": "runtime_policy_snapshots"},
                    policy_snapshot={
                        "refresh_scope": dict(payload.get("scope") or {}),
                        "runtime_refresh_boundary": dict(payload.get("runtime_refresh_boundary") or {}),
                    },
                    authorization={"state": "authorized", "method": "admin_guard"},
                    decision={"state": "accepted", "reason_code": "refresh_requested"},
                    executor={"state": "completed" if bool(payload.get("ok")) else "completed_with_degraded_refresh"},
                    verification={
                        "state": "confirmed" if bool(payload.get("ok")) else "degraded",
                        "summary": str(payload.get("message") or "").strip(),
                        "verified_at_utc": str(payload.get("utc") or _now_utc_iso()),
                    },
                    result={
                        "outcome": "applied" if bool(payload.get("ok")) else "degraded",
                        "reason_code": "runtime_refresh_ok" if bool(payload.get("ok")) else "runtime_refresh_degraded",
                    },
                    requested_at_utc=str(audit_entry.get("requested_at_utc") or ""),
                    finalized=True,
                )
                _json_response(self, HTTPStatus.OK, payload)
            except Exception as e:
                _control_action_audit.finalize_control_action(
                    repo_root,
                    audit_id=str(audit_entry.get("audit_id") or ""),
                    action_id="refresh_runtime",
                    actor=actor,
                    params={},
                    writer="api.admin.server.refresh_runtime",
                    request_path=path,
                    target={"surface": "the plane", "resource": "runtime_policy_snapshots"},
                    authorization={"state": "authorized", "method": "admin_guard"},
                    decision={"state": "accepted", "reason_code": "refresh_requested"},
                    executor={"state": "failed", "error": str(e)},
                    verification={
                        "state": "failed",
                        "summary": f"Runtime refresh failed: {e}",
                        "verified_at_utc": _now_utc_iso(),
                    },
                    result={"outcome": "failed", "reason_code": "runtime_refresh_failed", "detail": str(e)},
                    requested_at_utc=str(audit_entry.get("requested_at_utc") or ""),
                    finalized=True,
                )
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/admin/experiment_control_policy":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=1024 * 1024)
                payload = _experiment_control_policy_admin.mutate_surface(
                    self,
                    cfg,
                    qs,
                    dict(obj or {}),
                    repo_root=_repo_root_from_webui(),
                    refresh_runtime=_refresh_live_runtime_snapshot,
                )
                _json_response(self, HTTPStatus.OK, payload)
            except _experiment_control_policy_admin.ExperimentControlPolicyAdminError as exc:
                _json_response(self, exc.status, _experiment_control_policy_admin._status_payload(exc))
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(e)})
            return

        if path == "/api/admin/lab_assistant/web_operations/control":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=1024 * 1024)
                action_id = str((obj or {}).get("action_id") or "").strip()
                if not action_id:
                    raise ValueError("action_id is required")
                params = dict((obj or {}).get("params") or {})
                writer = str((obj or {}).get("writer") or "web_operations_console").strip()
                payload = _lab_web_ops.invoke_runtime_control(
                    _repo_root_from_webui(),
                    action_id=action_id,
                    params=params,
                    writer=writer,
                )
                _json_response(self, HTTPStatus.OK, payload)
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/admin/scheduler_watchdog/control":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=1024 * 1024)
                action_id = str((obj or {}).get("action_id") or "").strip()
                if not action_id:
                    raise ValueError("action_id is required")
                writer = str((obj or {}).get("writer") or "scheduler_watchdog_admin").strip()
                payload = _lab_web_ops.invoke_runtime_control(
                    _repo_root_from_webui(),
                    action_id=action_id,
                    params=dict((obj or {}).get("params") or {}),
                    writer=writer,
                )
                _json_response(self, HTTPStatus.OK, payload)
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/admin/lab_assistant/export":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=1024 * 1024)
                payload = _lab_host.export_host_baseline(
                    _repo_root_from_webui(),
                    note=str((obj or {}).get("note") or ""),
                )
                _json_response(self, HTTPStatus.OK, payload)
            except Exception as e:
                _json_response(
                    self,
                    HTTPStatus.BAD_REQUEST,
                    {"schema_version": "lab_assistant_host_export_v1", "utc": _now_utc_iso(), "ok": False, "error": str(e)},
                )
            return

        if path == "/api/admin/lab_assistant/restore":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=1024 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                manifest_path = Path(str(obj.get("manifest_path") or "").strip()).expanduser()
                if not str(manifest_path):
                    raise ValueError("manifest_path is required")
                target_root_raw = str(obj.get("target_repo_root") or "").strip()
                if not target_root_raw:
                    raise ValueError("target_repo_root is required and may not be the live repo root")
                target_root = Path(target_root_raw).expanduser()
                if target_root.resolve() == _repo_root_from_webui().resolve():
                    raise ValueError("refusing to restore into the live repo root")
                payload = _lab_host.restore_host_baseline(
                    manifest_path=manifest_path,
                    target_repo_root=target_root,
                    note=str(obj.get("note") or ""),
                )
                _json_response(self, HTTPStatus.OK, payload)
            except Exception as e:
                _json_response(
                    self,
                    HTTPStatus.BAD_REQUEST,
                    {"schema_version": "lab_assistant_host_restore_v1", "utc": _now_utc_iso(), "ok": False, "error": str(e)},
                )
            return

        if path == "/api/admin/patch/apply":
            # Same handler as the GET router, but correctly mounted under POST.
            if not _admin_guard(self, cfg, qs):
                return

            ctype = str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            allow_dirty = True
            force_overwrite = False
            verify_syntax = True
            permit_deletions = False
            allow_binary = False

            rr = _admin_repo_root()

            tmp_path: Optional[Path] = None
            try:
                if ctype == "application/json":
                    obj = _read_request_json(self, max_bytes=5 * 1024 * 1024)
                    if not isinstance(obj, dict):
                        raise ValueError("body must be a JSON object")
                    mode = str(obj.get("mode") or "").strip().lower() or "diff"
                    allow_dirty = bool(obj.get("allow_dirty", True))
                    force_overwrite = bool(obj.get("force_overwrite", False))
                    verify_syntax = bool(obj.get("verify_syntax", True))
                    permit_deletions = bool(obj.get("permit_deletions", False))
                    allow_binary = bool(obj.get("allow_binary", False))

                    if mode not in {"diff", "patch"}:
                        raise ValueError("mode must be 'diff'")
                    patch_text = obj.get("text")
                    if not isinstance(patch_text, str) or not patch_text.strip():
                        raise ValueError("missing patch text")

                    fd, tmp_name = tempfile.mkstemp(prefix="webui_patch_", suffix=".patch")
                    os.close(fd)
                    tmp_path = Path(tmp_name)
                    tmp_path.write_text(patch_text, encoding="utf-8")

                    cmd = [sys.executable, "-m", "tools.patching.apply_patch", "--patch", str(tmp_path), "--yes", "--no-review"]
                    if allow_dirty:
                        cmd.append("--allow-dirty")
                    if force_overwrite:
                        cmd.append("--force-overwrite")
                    if permit_deletions:
                        cmd.append("--permit-deletions")
                    if allow_binary:
                        cmd.append("--allow-binary")
                    if verify_syntax:
                        cmd += ["--post-exec", "python -m tools.scripts.verify_syntax"]

                else:
                    fname = str(self.headers.get("X-Filename") or "patch.zip").strip() or "patch.zip"
                    raw = _read_request_bytes(self, max_bytes=50 * 1024 * 1024)
                    if not raw:
                        raise ValueError("empty upload")

                    suffix = ".zip"
                    lower = fname.lower()
                    if lower.endswith(".patch") or lower.endswith(".diff"):
                        suffix = ".patch"
                    fd, tmp_name = tempfile.mkstemp(prefix="webui_patch_", suffix=suffix)
                    os.close(fd)
                    tmp_path = Path(tmp_name)
                    tmp_path.write_bytes(raw)

                    if suffix == ".zip":
                        cmd = [sys.executable, "-m", "tools.patching.apply_patch", "--zip", str(tmp_path), "--yes", "--no-review"]
                    else:
                        cmd = [sys.executable, "-m", "tools.patching.apply_patch", "--patch", str(tmp_path), "--yes", "--no-review"]

                    # Query-string overrides for uploads.
                    try:
                        allow_dirty = str((qs.get("allow_dirty") or ["1"])[0] or "1").strip().lower() in {"1", "true", "yes"}
                    except Exception:
                        allow_dirty = True
                    try:
                        force_overwrite = str((qs.get("force_overwrite") or ["0"])[0] or "0").strip().lower() in {"1", "true", "yes"}
                    except Exception:
                        force_overwrite = False
                    try:
                        verify_syntax = str((qs.get("verify_syntax") or ["1"])[0] or "1").strip().lower() in {"1", "true", "yes"}
                    except Exception:
                        verify_syntax = True
                    try:
                        allow_binary = str((qs.get("allow_binary") or ["0"])[0] or "0").strip().lower() in {"1", "true", "yes"}
                    except Exception:
                        allow_binary = False
                    try:
                        permit_deletions = str((qs.get("permit_deletions") or ["0"])[0] or "0").strip().lower() in {"1", "true", "yes"}
                    except Exception:
                        permit_deletions = False

                    if allow_dirty:
                        cmd.append("--allow-dirty")
                    if force_overwrite:
                        cmd.append("--force-overwrite")
                    if permit_deletions:
                        cmd.append("--permit-deletions")
                    if allow_binary:
                        cmd.append("--allow-binary")
                    if verify_syntax:
                        cmd += ["--post-exec", "python -m tools.scripts.verify_syntax"]

                p = subprocess.run(cmd, cwd=str(rr), capture_output=True, text=True)
                status_code = HTTPStatus.OK if int(p.returncode) == 0 else HTTPStatus.BAD_REQUEST
                _json_response(
                    self,
                    status_code,
                    {
                        "schema_version": "admin_patch_apply_v1",
                        "utc": _now_utc_iso(),
                        "ok": bool(int(p.returncode) == 0),
                        "returncode": int(p.returncode),
                        "stdout": str(p.stdout or ""),
                        "stderr": str(p.stderr or ""),
                        "allow_dirty": bool(allow_dirty),
                        "force_overwrite": bool(force_overwrite),
                        "verify_syntax": bool(verify_syntax),
                        "permit_deletions": bool(permit_deletions),
                        "allow_binary": bool(allow_binary),
                    },
                )
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return
            finally:
                if tmp_path is not None:
                    try:
                        tmp_path.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except Exception:
                        pass
            # (handler returns above)
            return

        if path == "/api/admin/patch/inspect":
            if not _admin_guard(self, cfg, qs):
                return

            rr = _admin_repo_root()
            ctype = str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()

            tmp_path: Optional[Path] = None
            try:
                if ctype == "application/json":
                    obj = _read_request_json(self, max_bytes=5 * 1024 * 1024)
                    if not isinstance(obj, dict):
                        raise ValueError("body must be a JSON object")
                    mode = str(obj.get("mode") or "").strip().lower() or "diff"
                    txt = str(obj.get("text") or "")
                    if mode not in {"diff", "patch"}:
                        raise ValueError("mode must be 'diff'")
                    if not txt.strip():
                        raise ValueError("missing text")

                    # We deliberately keep this cheap: summarize without invoking git.
                    head = txt[:4000]
                    _json_response(
                        self,
                        HTTPStatus.OK,
                        {
                            "schema_version": "admin_patch_inspect_v1",
                            "utc": _now_utc_iso(),
                            "kind": "diff",
                            "chars": int(len(txt)),
                            "lines": int(txt.count("\\n") + 1),
                            "preview_head": head,
                        },
                    )
                    return

                # Binary upload: zip/patch/diff. Only zip has rich inspect.
                fname = str(self.headers.get("X-Filename") or "patch.zip").strip() or "patch.zip"
                raw = _read_request_bytes(self, max_bytes=50 * 1024 * 1024)
                if not raw:
                    raise ValueError("empty upload")

                lower = fname.lower()
                is_zip = lower.endswith(".zip")

                if not is_zip:
                    # Non-zip uploads: summarize bytes only.
                    _json_response(
                        self,
                        HTTPStatus.OK,
                        {
                            "schema_version": "admin_patch_inspect_v1",
                            "utc": _now_utc_iso(),
                            "kind": "bytes",
                            "filename": fname,
                            "bytes": int(len(raw)),
                            "note": "non-zip upload: no file listing available",
                        },
                    )
                    return

                import io as _io

                zf = zipfile.ZipFile(_io.BytesIO(raw), "r")
                entries = []
                skipped = 0
                for zi in zf.infolist():
                    name = str(zi.filename or "").replace("\\", "/")
                    if not name or name.endswith("/"):
                        continue
                    # Reject traversal.
                    if name.startswith("/") or ".." in name.split("/"):
                        skipped += 1
                        continue

                    # Determine overwrite risk (repo root).
                    dst = _admin_fs_resolve(rr, name)
                    will_overwrite = bool(dst and dst.exists() and dst.is_file())

                    skip_reason = None
                    if name.lower().endswith(".md"):
                        # apply_patch tool defaults to skipping markdown (unless explicitly enabled).
                        skip_reason = "markdown likely skipped by patch tool"

                    entries.append(
                        {
                            "path": name,
                            "bytes": int(zi.file_size or 0),
                            "compressed_bytes": int(zi.compress_size or 0),
                            "will_overwrite": bool(will_overwrite),
                            "skip_reason": skip_reason,
                        }
                    )

                entries.sort(key=lambda d: str(d.get("path") or ""))
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "schema_version": "admin_patch_inspect_v1",
                        "utc": _now_utc_iso(),
                        "kind": "zip",
                        "filename": fname,
                        "bytes": int(len(raw)),
                        "entry_count": int(len(entries)),
                        "skipped_entries": int(skipped),
                        "entries": entries[:5000],
                    },
                )
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return
            finally:
                if tmp_path is not None:
                    try:
                        tmp_path.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except Exception:
                        pass

        if path == "/api/admin/fs/replace":
            if not _admin_guard(self, cfg, qs):
                return
            rel = str((qs.get("path") or [""])[0] or "").strip()
            rr = _admin_repo_root()
            fp = _admin_fs_resolve(rr, rel)
            if not fp:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid path"})
                return

            try:
                data = _read_request_bytes(self, max_bytes=20 * 1024 * 1024)
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

            tmp = fp.with_suffix(fp.suffix + ".tmp")
            try:
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(data)
                if fp.suffix.lower() == ".py":
                    py_compile.compile(str(tmp), doraise=True)
                os.replace(tmp, fp)
            except Exception as e:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": f"replace failed: {e}"})
                return

            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "schema_version": "admin_fs_replace_v1",
                    "utc": _now_utc_iso(),
                    "path": _posix_relpath(fp, rr),
                    "bytes": int(len(data)),
                    "compiled": bool(fp.suffix.lower() == ".py"),
                },
            )
            return

        if path == "/api/admin/fs/zip":
            if not _admin_guard(self, cfg, qs):
                return
            try:
                obj = _read_request_json(self, max_bytes=512 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                paths = obj.get("paths")
                if not isinstance(paths, list) or not paths:
                    raise ValueError("missing paths")
                include_hidden = bool(obj.get("include_hidden", False))
                name = str(obj.get("name") or "repo_selection.zip").strip() or "repo_selection.zip"
                if not name.lower().endswith(".zip"):
                    name += ".zip"

                rr = _admin_repo_root()
                fd, tmp_name = tempfile.mkstemp(prefix="webui_sel_", suffix=".zip")
                os.close(fd)
                tmp_zip = Path(tmp_name)

                # Guardrails.
                MAX_FILES = int(os.environ.get("WEBUI_ZIP_MAX_FILES", "20000") or "20000")
                MAX_TOTAL_BYTES = int(os.environ.get("WEBUI_ZIP_MAX_TOTAL_BYTES", "1000000000") or "1000000000")

                def _is_hidden_rel(rel_posix: str) -> bool:
                    if include_hidden:
                        return False
                    parts = [p for p in rel_posix.split("/") if p]
                    return any(p.startswith(".") for p in parts)

                def _collect_files(selected_rel: List[str]) -> List[Path]:
                    """Resolve selected paths under rr, expand directories recursively."""
                    out_files: List[Path] = []
                    seen_abs: set[str] = set()
                    # Deterministic processing of selected items.
                    for raw in sorted([str(x or "").strip() for x in selected_rel]):
                        if not raw:
                            continue
                        fp = _admin_fs_resolve(rr, raw)
                        if not fp or (not fp.exists()) or fp.is_symlink():
                            continue
                        if fp.is_file():
                            relp = _posix_relpath(fp, rr)
                            if _is_hidden_rel(relp):
                                continue
                            key = str(fp.resolve())
                            if key in seen_abs:
                                continue
                            seen_abs.add(key)
                            out_files.append(fp)
                            continue
                        if fp.is_dir():
                            # Walk deterministically, never follow symlinks.
                            for root_dir, dirnames, filenames in os.walk(fp, topdown=True, followlinks=False):
                                # Prune hidden dirs if include_hidden is off.
                                if not include_hidden:
                                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                                dirnames.sort()
                                filenames.sort()
                                for fn in filenames:
                                    if (not include_hidden) and fn.startswith("."):
                                        continue
                                    ap = Path(root_dir) / fn
                                    if ap.is_symlink() or (not ap.is_file()):
                                        continue
                                    relp = _posix_relpath(ap, rr)
                                    if _is_hidden_rel(relp):
                                        continue
                                    key = str(ap.resolve())
                                    if key in seen_abs:
                                        continue
                                    seen_abs.add(key)
                                    out_files.append(ap)
                    # Deterministic order inside zip.
                    out_files.sort(key=lambda p: _posix_relpath(p, rr))
                    return out_files

                try:
                    files = _collect_files(paths)
                    if len(files) > MAX_FILES:
                        raise ValueError("Too many files selected via folders; reduce selection.")
                    total_bytes = 0
                    with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
                        for fp in files:
                            try:
                                st = fp.stat()
                                total_bytes += int(st.st_size)
                                if total_bytes > MAX_TOTAL_BYTES:
                                    raise ValueError("Selected content too large; reduce selection.")
                            except Exception:
                                pass
                            arcname = _posix_relpath(fp, rr)
                            # Normalize to forward slashes.
                            arcname = arcname.replace("\\", "/")
                            z.write(fp, arcname=arcname)

                    # Helpful diagnostics when debugging "empty zip" complaints.
                    extra_headers = {
                        "X-Zip-Entries": str(len(files)),
                        "X-Zip-Bytes-Uncompressed": str(int(total_bytes)),
                    }
                    _file_response(
                        self,
                        HTTPStatus.OK,
                        tmp_zip,
                        content_type="application/zip",
                        download_name=name,
                        extra_headers=extra_headers,
                    )
                    return
                finally:
                    try:
                        tmp_zip.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except Exception:
                        pass

            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return


        # -----------------------------
        # Run Builder (Phase 5e Tier-1)
        # -----------------------------





        if path == "/api/patch_backlog":
            pb_path = _patch_backlog_path(cfg)
            try:
                obj = _read_request_json(self, max_bytes=2 * 1024 * 1024)
                if not isinstance(obj, dict):
                    raise ValueError("body must be JSON object")
                if not isinstance(obj.get("items"), list):
                    raise ValueError("missing items")
                obj["schema_version"] = "patch_backlog_v1"
                atomic_write_canonical_json(pb_path, obj)
                _json_response(self, HTTPStatus.OK, {"status":"ok","utc":_now_utc_iso(),"items": len(obj.get("items") or [])})
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return







        # Legacy: POST /api/run/submit_command?key=<run_key>
        if path == "/api/run/submit_command":
            qs = parse_qs(u.query or "")
            key = str((qs.get("key") or [""])[0] or "")
            try:
                rk = normalize_run_key(key)
                _run_root, sim_root = resolve_run_dirs(cfg.runs_root, rk)
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "run not found"})
                return
            except ValueError as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

            try:
                obj = _read_request_json(self)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")
                res = submit_inbox_command(sim_root, obj)
                _json_response(self, HTTPStatus.OK, res)
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        # POST /api/branch/plan
        if path == "/api/branch/plan":
            try:
                obj = _read_request_json(self)
                if not isinstance(obj, dict):
                    raise ValueError("body must be a JSON object")

                res = create_planned_run(
                    runs_root=cfg.runs_root,
                    parent_run_key=str(obj.get("parent_run_key") or ""),
                    checkpoint_id=(str(obj.get("checkpoint_id")) if obj.get("checkpoint_id") else None),
                    checkpoint_generation=(int(obj.get("checkpoint_generation")) if obj.get("checkpoint_generation") is not None else None),
                    run_kind=str(obj.get("run_kind") or "branch"),
                    target_generation=(int(obj.get("target_generation")) if obj.get("target_generation") is not None else None),
                    label=(str(obj.get("label")) if obj.get("label") else None),
                    tags=([str(t) for t in obj.get("tags")] if isinstance(obj.get("tags"), list) else None),
                    changesets=([str(c) for c in obj.get("changesets")] if isinstance(obj.get("changesets"), list) else None),
                    override_determinism_fail=bool(obj.get("override_determinism_fail", False)),
                )
                _json_response(self, HTTPStatus.OK, res)
                return
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "parent run not found"})
                return
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

        # /api/run/{run_id}/...
        if path.startswith("/api/run/"):
            try:
                res = self._handle_run_post(state, cfg, path)
                if res:
                    return
            except FileNotFoundError:
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "run not found"})
                return
            except ValueError as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return
            except Exception as e:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
                return

        _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _handle_run_post(self, state: _ServerState, cfg: WebUIConfig, path: str) -> bool:
        parts = [p for p in path.split("/") if p]
        if len(parts) < 4:
            return False
        if parts[0] != "api" or parts[1] != "run":
            return False

        action = parts[-1]
        run_key = unquote("/".join(parts[2:-1]))
        rk = normalize_run_key(run_key)

        indexed: Optional[Tuple[Path, Path]] = None
        try:
            indexed = state.get_run_index_item(rk)
        except Exception:
            indexed = None
        if indexed is not None:
            run_root, sim_root = indexed
        else:
            run_root, sim_root = resolve_run_dirs(cfg.runs_root, rk)

        if action == "notes":
            obj = _read_request_json(self)
            if not isinstance(obj, dict):
                raise ValueError("body must be JSON object")
            txt = obj.get("text")
            if not isinstance(txt, str):
                raise ValueError("notes.text must be string")
            write_notes(run_root, txt)
            _json_response(self, HTTPStatus.OK, {"status": "ok"})
            return True

        if action == "archive":
            # Create a deterministic zip archive of the entire run folder.
            # By default, we also mark the run as archived in ui_meta.json.
            obj = _read_request_json(self) or {}
            if obj is None:
                obj = {}
            if not isinstance(obj, dict):
                raise ValueError("body must be JSON object")

            # Guardrails: never allow "archived but still running" ghosts.
            # If a run is RUNNING, reject by default. With force=true we allow
            # the zip creation, but avoid hiding by default.
            force = bool(obj.get("force", False))
            delete_after = bool(obj.get("delete_after", False))

            mark_archived_raw = obj.get("mark_archived", None)
            mark_archived = bool(mark_archived_raw) if mark_archived_raw is not None else True

            rs_obj, _rs_src = read_run_status(sim_root)
            status_badge, _extra = _status_badge_from_run_status(rs_obj)

            # Prefer process liveness over stale status badges.
            pid_candidates: List[int] = []
            try:
                pid0 = _read_pid_file(Path(run_root) / "ui" / "pid.txt")
                if int(pid0) > 0:
                    pid_candidates.append(int(pid0))
            except Exception:
                pass
            if isinstance(rs_obj, dict):
                try:
                    pid1 = _safe_int(rs_obj.get("pid"), 0)
                    if int(pid1) > 0 and int(pid1) not in pid_candidates:
                        pid_candidates.append(int(pid1))
                except Exception:
                    pass
            try:
                for p in _discover_run_bound_pids(Path(run_root).resolve()):
                    if int(p) > 0 and int(p) not in pid_candidates:
                        pid_candidates.append(int(p))
            except Exception:
                pass

            pid_alive_any = any(_pid_alive(int(p)) for p in pid_candidates if int(p) > 0)
            derived_state = _derive_run_state(run_root, sim_root)
            is_running = bool(pid_alive_any) or (str(derived_state).upper() == "RUNNING")
            if is_running:
                if delete_after:
                    raise ValueError(
                        f"run is RUNNING; refusing to archive with delete_after=true. Stop first (use /api/run/{rk}/stop)."
                    )
                if not force:
                    raise ValueError(
                        f"run is RUNNING; stop first (use /api/run/{rk}/stop). Set force=true to override zip-only without hiding."
                    )
                # force=true: allow zip creation, but avoid hiding by default.
                if mark_archived_raw is None:
                    mark_archived = False

            # Transient writes can race with archive enumeration right after stop.
            # Retry briefly before surfacing failure.
            res = None
            last_archive_err: Optional[Exception] = None
            for i in range(3):
                try:
                    res = create_run_archive_zip(
                        run_key=rk,
                        run_root=run_root,
                        runs_root=cfg.runs_root,
                        delete_after=delete_after,
                    )
                    last_archive_err = None
                    break
                except RuntimeError as e:
                    msg = str(e)
                    if ("changed during archive" not in msg) and ("disappeared during archive" not in msg):
                        raise
                    last_archive_err = e
                    time.sleep(0.2)
            if res is None:
                assert last_archive_err is not None
                raise last_archive_err

            meta = write_ui_meta(run_root, archived=True if mark_archived else None)
            res["ui_meta"] = meta
            res["archive_guard"] = {
                "force": bool(force),
                "delete_after": bool(delete_after),
                "mark_archived": bool(mark_archived),
                "status_badge": status_badge,
                "derived_state": str(derived_state),
                "pid_candidates": [int(x) for x in pid_candidates],
                "pid_alive_any": bool(pid_alive_any),
            }
            _json_response(self, HTTPStatus.OK, res)
            return True

        if action == "unarchive":
            meta = write_ui_meta(run_root, archived=False)
            _json_response(self, HTTPStatus.OK, {"status": "ok", "ui_meta": meta})
            return True

        if action == "export":
            obj = _read_request_json(self) or {}
            if obj is None:
                obj = {}
            if not isinstance(obj, dict):
                raise ValueError("body must be JSON object")
            include_ck = bool(obj.get("include_checkpoints", False))
            nm = obj.get("name")
            name = str(nm) if isinstance(nm, str) else None
            res = create_export_zip(rk, run_root, sim_root, include_checkpoints=include_ck, name=name)
            res["download_url"] = f"/api/run/{rk}/export/{res['filename']}"
            _json_response(self, HTTPStatus.OK, res)
            return True

        if action == "submit_command":
            obj = _read_request_json(self)
            if not isinstance(obj, dict):
                raise ValueError("body must be JSON object")

            # Optional changesets expansion: if body has changesets_ids and no command payload.
            if isinstance(obj.get("changesets"), list) and not obj.get("command_type"):
                # Submit all commands from selected changesets.
                submitted: List[Dict[str, Any]] = []
                errors: List[str] = []
                for cs_id in obj.get("changesets"):
                    cs = _load_changeset_by_id(cfg.runs_root, str(cs_id))
                    if not cs:
                        errors.append(f"changeset not found: {cs_id}")
                        continue
                    cmds = cs.get("commands") if isinstance(cs.get("commands"), list) else []
                    for cmd in cmds:
                        if not isinstance(cmd, dict):
                            continue
                        try:
                            submitted.append(submit_inbox_command(sim_root, cmd))
                        except Exception as e:
                            errors.append(str(e))
                _json_response(self, HTTPStatus.OK, {"status": "ok", "submitted": submitted, "errors": errors})
                return True

            res = submit_inbox_command(sim_root, obj)
            _json_response(self, HTTPStatus.OK, res)
            return True

        # --- Phase 5e: process control (start/stop/resume) ---
        if action in ("start", "stop", "resume", "terminate_unmanaged"):
            try:
                if action == "start":
                    res = _start_run(state, cfg, rk)
                elif action == "stop":
                    res = _stop_run(state, rk)
                elif action == "terminate_unmanaged":
                    res = _terminate_unmanaged_run(state, rk)
                else:  # resume
                    res = _resume_run(state, cfg, rk)
                _json_response(self, HTTPStatus.OK, res)
            except Exception as e:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return True

        return False

    def end_headers(self) -> None:
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Content-Security-Policy", "frame-ancestors 'self'; base-uri 'self'; object-src 'none'")
        super().end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        # Log only method, query-free path and numeric status. Malformed request
        # diagnostics can also contain credential URLs, so never render fmt/args.
        path = str(getattr(self, "path", "/") or "/").split("?", 1)[0]
        path = "".join(c for c in path if 32 <= ord(c) < 127)[:1024]
        method = str(getattr(self, "command", "?") or "?")
        status = next((str(value) for value in args[:2] if str(value).isdigit()), "0")
        if _QUIET_POLLING_LOGS and method in {"GET", "HEAD"} and 200 <= int(status) < 400 and path in _QUIET_POLLING_PATHS:
            return
        sys.stderr.write("%s - - [%s] %s %s %s\n" % (self.client_address[0], self.log_date_time_string(), method, path, status))
