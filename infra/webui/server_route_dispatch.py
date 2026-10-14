from __future__ import annotations

from typing import Any, Callable, Dict, List


def _first_match(
    routes: List[Callable[..., bool]],
    *,
    handler: Any,
    state: Any,
    cfg: Any,
    path: str,
    qs: Dict[str, List[str]],
) -> bool:
    for route in routes:
        if route(handler=handler, state=state, cfg=cfg, path=path, qs=qs):
            return True
    return False


def dispatch_front_door_get(*, routes_module: Any, handler: Any, state: Any, cfg: Any, path: str, qs: Dict[str, List[str]]) -> bool:
    def _serve_index(**kwargs: Any) -> bool:
        if path in {"/", ""}:
            return bool(routes_module._serve_static_file(handler, cfg.static_dir, "index.html"))
        return False

    def _serve_static(**kwargs: Any) -> bool:
        if not path.startswith("/static/"):
            return False
        rel = path[len("/static/") :]
        return bool(routes_module._serve_static_file(handler, cfg.static_dir, rel))

    def _serve_mobile(**kwargs: Any) -> bool:
        mapping = {
            "/mobile": "mobile.html",
            "/debug/mobile": "mobile.html",
            "/admin/mobile": "mobile.html",
            "/mobile/update": "mobile_update.html",
            "/debug/mobile/update": "mobile_update.html",
            "/admin/mobile/update": "mobile_update.html",
            "/mobile/backlog": "mobile_backlog.html",
            "/debug/mobile/backlog": "mobile_backlog.html",
            "/admin/mobile/backlog": "mobile_backlog.html",
            "/mobile/cockpit": "mobile_backlog.html",
            "/debug/mobile/cockpit": "mobile_backlog.html",
            "/admin/mobile/cockpit": "mobile_backlog.html",
            "/agents": "agents.html",
            "/schedules": "schedules.html",
        }
        filename = mapping.get(path)
        if not filename:
            return False
        return bool(routes_module._serve_static_file(handler, cfg.static_dir, filename))

    def _serve_docs(**kwargs: Any) -> bool:
        if path != "/docs" and not path.startswith("/docs/"):
            return False
        if routes_module._serve_docs_file(handler, cfg.docs_root, path):
            return True
        routes_module._bytes_response(
            handler,
            routes_module.HTTPStatus.NOT_FOUND,
            b"docs not found",
            content_type="text/plain; charset=utf-8",
        )
        return True

    def _serve_project_docs(**kwargs: Any) -> bool:
        if path in {"/projects/docs", "/projects/docs/", "/experiments/docs", "/experiments/docs/"}:
            return bool(routes_module._serve_project_docs_file(handler, routes_module._repo_root_from_webui(), path))
        if path.startswith("/projects/") or path.startswith("/experiments/projects/"):
            if "/docs" not in path:
                return False
            if routes_module._serve_project_docs_file(handler, routes_module._repo_root_from_webui(), path):
                return True
            routes_module._bytes_response(
                handler,
                routes_module.HTTPStatus.NOT_FOUND,
                b"project docs not found",
                content_type="text/plain; charset=utf-8",
            )
            return True
        return False

    return _first_match([_serve_index, _serve_static, _serve_mobile, _serve_docs, _serve_project_docs], handler=handler, state=state, cfg=cfg, path=path, qs=qs)


def dispatch_assistant_get(*, routes_module: Any, handler: Any, state: Any, cfg: Any, path: str, qs: Dict[str, List[str]]) -> bool:
    if path in {"/api/assistant/probe/ping", "/api/assistant/probe/capabilities"}:
        status_code, payload = routes_module._assistant_probe.handle_get_request(
            handler,
            repo_root=routes_module._repo_root_from_webui(),
            path=path,
            qs=qs,
        )
        routes_module._json_response(handler, status_code, payload)
        return True
    if path == "/api/assistant/auth/status" or path.startswith("/api/assistant/browser_relay/"):
        status_code, payload = routes_module._assistant_bridge.handle_get_request(
            handler,
            repo_root=routes_module._repo_root_from_webui(),
            path=path,
            qs=qs,
        )
        routes_module._json_response(handler, status_code, payload)
        return True
    return False


def dispatch_assistant_post(*, routes_module: Any, handler: Any, state: Any, cfg: Any, path: str, qs: Dict[str, List[str]]) -> bool:
    if path == "/api/assistant/probe/echo":
        status_code, payload = routes_module._assistant_probe.handle_post_request(
            handler,
            repo_root=routes_module._repo_root_from_webui(),
            path=path,
            qs=qs,
        )
        routes_module._json_response(handler, status_code, payload)
        return True
    if path.startswith("/api/assistant/"):
        status_code, payload = routes_module._assistant_bridge.handle_post_request(
            handler,
            repo_root=routes_module._repo_root_from_webui(),
            path=path,
            qs=qs,
        )
        routes_module._json_response(handler, status_code, payload)
        return True
    return False
