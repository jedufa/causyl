from __future__ import annotations

import contextlib
import functools
import http.server
import io
import json
import re
import socketserver
import subprocess
import threading
import textwrap
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from infra.webui import server_routes_and_main as routes


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str) -> None:
        self.path = path
        self.headers = {}
        self.rfile = io.BytesIO(b"")
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg))
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _run_node_json(script: str) -> dict:
    proc = subprocess.run(
        ["node", "-e", script],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node script failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    out = str(proc.stdout or "").strip()
    assert out, "node script returned empty stdout"
    return json.loads(out)


def _extract_inline_css(html: str) -> str:
    m = re.search(r"<style>(.*?)</style>", html, flags=re.DOTALL)
    assert m, "inline <style> block not found"
    return str(m.group(1))


def _extract_first_rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    assert m, f"css rule missing for selector: {selector}"
    return str(m.group(1))


def _extract_media_block(css: str, media_query: str) -> str:
    marker = f"@media {media_query}"
    idx = css.find(marker)
    assert idx >= 0, f"css media query missing: {media_query}"
    brace_start = css.find("{", idx)
    assert brace_start >= 0, f"css media query missing open brace: {media_query}"
    depth = 0
    for i in range(brace_start, len(css)):
        ch = css[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return css[brace_start + 1:i]
    raise AssertionError(f"css media query missing close brace: {media_query}")


@contextlib.contextmanager
def _serve_webui_root() -> str:
    webui_root = _repo_root() / "infra" / "webui"

    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, _fmt: str, *_args: object) -> None:
            return

    handler = functools.partial(_QuietHandler, directory=str(webui_root))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as server:
        port = int(server.server_address[1])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_agents_page_contains_d1_orchestration_context_and_responsive_layout() -> None:
    html = (_repo_root() / "infra" / "webui" / "static" / "agents.html").read_text(encoding="utf-8")
    css = _extract_inline_css(html)
    assert 'id="orchCard"' in html
    assert 'id="orchPhase"' in html
    assert 'id="orchSubphase"' in html
    assert 'id="orchObjective"' in html
    assert ('id="orchExecutionMeta"' in html) or ('id="orchExecution"' in html)
    assert 'id="orchWorkflowId"' in html
    assert 'id="orchWorkflowLink"' in html
    assert 'id="agentTimeBuilder"' in html
    assert 'id="agentTimeAssurer"' in html
    assert 'id="agentTimeGeneralist"' in html
    assert "Orchestrator Single-Panel Web UI" in html
    assert ">Iteration 1<" in html
    assert '<option value="0">0 (orchestrator-only)</option>' in html
    assert '<option value="4" selected>4 (default)</option>' in html
    assert '<option value="__NO_PIS__">No PIS (orchestrator-only chat)</option>' in html
    assert "Context Label" in html
    assert "PIS Stage" in html
    assert "Sub-phase" not in html
    assert "@media (max-width: 900px)" in css
    assert "@media (max-width: 700px)" in css
    assert "@media (max-width: 480px)" in css

    base_tabs = _extract_first_rule(css, ".chatter-tabs")
    assert "display:flex" in base_tabs
    assert "gap:8px" in base_tabs
    assert "flex-wrap:wrap" in base_tabs
    assert "max-width:100%" in base_tabs

    base_tab = _extract_first_rule(css, ".chatter-tab")
    assert "white-space:normal" not in base_tab
    assert "word-break:break-word" not in base_tab
    assert "line-height:1.2" not in base_tab
    assert "min-height:34px" not in base_tab

    mobile_css = _extract_media_block(css, "(max-width: 480px)")
    mobile_tabs = _extract_first_rule(mobile_css, ".chatter-tabs")
    assert "display:grid" in mobile_tabs
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in mobile_tabs
    assert "gap:6px" in mobile_tabs
    assert "align-items:stretch" in mobile_tabs
    assert "overflow-x:clip" in mobile_tabs

    mobile_tab = _extract_first_rule(mobile_css, ".chatter-tab")
    assert "width:100%" in mobile_tab
    assert "min-width:0" in mobile_tab
    assert "max-width:100%" in mobile_tab
    assert "white-space:normal" in mobile_tab
    assert "word-break:break-word" in mobile_tab
    assert "overflow-wrap:anywhere" in mobile_tab
    assert "line-height:1.2" in mobile_tab
    assert "min-height:34px" in mobile_tab
    assert "box-sizing:border-box" in mobile_tab
    assert ".chatter-tab:focus-visible" in css


def test_agents_tabs_mobile_layout_playwright_smoke() -> None:
    playwright = pytest.importorskip("playwright.sync_api", reason="playwright is not installed in this environment")
    widths = [320, 360, 390, 414, 768]

    with _serve_webui_root() as base_url:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()

            def _route_api(route) -> None:  # type: ignore[no-untyped-def]
                path = urlparse(route.request.url).path
                payload: dict[str, object] = {}
                if path == "/api/ledger/items":
                    payload = {"items": [{"uid": "PIS-20260305-000000Z-A1B2", "status": "READY", "title": "PIS title", "pis_excerpt": "excerpt"}]}
                elif path.startswith("/api/ledger/item/"):
                    payload = {"meta": {"title": "PIS title"}, "pis_markdown": "PIS markdown"}
                elif path == "/api/agents/orchestration/list":
                    payload = {"items": []}
                elif path == "/api/agents/orchestration/status":
                    payload = {"state": {"status": "idle"}}
                elif path == "/api/agents/session":
                    payload = {"session": []}
                route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

            page.route("**/api/**", _route_api)
            page.goto(f"{base_url}/static/agents.html", wait_until="domcontentloaded")
            page.wait_for_selector("#chatterTabs")
            page.evaluate(
                """() => {
                  const longLabel =
                    "PIS-20260305-000000Z-A1B2 long mobile title for overflow resilience verification";
                  const ids = ["tabAll", "tabBuilder", "tabAssurer", "tabGeneralist"];
                  ids.forEach((id, idx) => {
                    const el = document.getElementById(id);
                    if (!el) return;
                    el.textContent = `${el.textContent || "Tab"} | ${longLabel} ${idx}`;
                    el.setAttribute("data-test-long-label", "1");
                  });
                }"""
            )

            for width in widths:
                page.set_viewport_size({"width": width, "height": 900})
                page.wait_for_timeout(80)
                overflow_delta = page.evaluate(
                    """() => {
                      const de = document.documentElement;
                      const body = document.body;
                      const sw = Math.max(de.scrollWidth, body ? body.scrollWidth : 0);
                      return sw - de.clientWidth;
                    }"""
                )
                assert float(overflow_delta) <= 1.0

                # Validate each tab remains on-screen and its label is fully visible.
                tabs = page.eval_on_selector_all(
                    ".chatter-tab",
                    """(els) => els.map((el) => {
                      const r = el.getBoundingClientRect();
                      const tx = (el.textContent || '').trim();
                      return {
                        text: String(tx),
                        width: r.width,
                        height: r.height,
                        clientHeight: el.clientHeight,
                        scrollHeight: el.scrollHeight,
                        clientWidth: el.clientWidth,
                        scrollWidth: el.scrollWidth,
                        rightEdgeDelta: r.right - window.innerWidth
                      };
                    })""",
                )
                assert tabs, "expected tab buttons"
                for tab in tabs:
                    assert str(tab["text"]).strip()
                    assert float(tab["width"]) > 0
                    assert float(tab["height"]) >= 30
                    assert float(tab["scrollHeight"]) <= float(tab["clientHeight"]) + 1.0
                    assert float(tab["scrollWidth"]) <= float(tab["clientWidth"]) + 1.0
                    assert float(tab["rightEdgeDelta"]) <= 1.0

            page.click("#tabBuilder")
            assert page.locator("#tabBuilder").evaluate("el => el.classList.contains('active')") is True
            assert page.locator("#tabAll").evaluate("el => el.classList.contains('active')") is False

            focused_id = ""
            for _ in range(40):
                page.keyboard.press("Tab")
                focused_id = str(page.evaluate("() => (document.activeElement && document.activeElement.id) || ''"))
                if focused_id == "tabAll":
                    break
            assert focused_id == "tabAll"
            focus_outline = page.locator("#tabAll").evaluate(
                """(el) => {
                  const s = getComputedStyle(el);
                  return { style: s.outlineStyle, width: s.outlineWidth };
                }"""
            )
            assert str(focus_outline["style"]) != "none"
            assert str(focus_outline["width"]) not in {"0px", "0"}

            browser.close()


def test_default_agent_prompts_remove_phase_wording() -> None:
    prompts_root = _repo_root() / "governance" / "agents" / "prompts"
    files = [
        prompts_root / "_base.md",
        prompts_root / "role__builder_mobile.md",
        prompts_root / "role__verifier_mobile.md",
        prompts_root / "role__generalist_mobile.md",
        prompts_root / "role__orchestrator_mobile.md",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        assert not re.search(r"\bphase\b", lowered), f"{path} still contains 'phase'"
        assert "subphase" not in lowered, f"{path} still contains 'subphase'"
        assert "sub-phase" not in lowered, f"{path} still contains 'sub-phase'"
        assert "d1" not in lowered, f"{path} still contains 'D1'"


def test_orchestrator_navigation_links_between_main_and_agents_pages() -> None:
    root = _repo_root()
    index_html = (root / "infra" / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    agents_html = (root / "infra" / "webui" / "static" / "agents.html").read_text(encoding="utf-8")

    assert 'id="tab-agents"' in index_html
    assert 'id="agentsEmbedFrame"' in index_html
    assert 'data-src="/agents?embedded=1"' in index_html
    assert 'href="/"' in agents_html
    assert "Main UI" in agents_html


def test_agents_route_and_deeplink_serve_agents_html(tmp_path: Path, monkeypatch) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/agents?workflow_id=wf-20260305t000000z-aaaa1111")
    seen: list[str] = []

    def _serve_static(_h, _base, rel):
        seen.append(str(rel))
        return str(rel) == "agents.html"

    monkeypatch.setattr(routes, "_serve_static_file", _serve_static)
    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert "agents.html" in seen


def test_agents_js_maps_orchestration_execution_context_for_d1() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: {
            location: {
              search: "?workflow_id=wf-20260305t000000z-a1b2c3d4&binding_type=task_id&binding_id=a7k"
            }
          },
          document: {
            addEventListener: ()=>{}
          },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;

        const parsed = t.parseOrchestrationQuery();
        const vmState = t.orchestrationViewFromState({
          workflow_id: "wf-20260305t000000z-a1b2c3d4",
          phase_label: "Orchestrator Single-Panel Web UI",
          subphases: ["D1", "D2"],
          subphase_index: 0,
          objective: "Update web agent page",
          status: "running",
          turn: "builder_run",
          iteration: 1,
          max_iterations: 3,
          active_turn_role: "builder",
          last_step_utc: "2026-03-05T14:30:00Z",
          binding: { type: "task_id", id: "a7k" }
        });

        console.log(JSON.stringify({ parsed, vmState }));
        """
    )
    out = _run_node_json(script)
    parsed = out["parsed"]
    vm_state = out["vmState"]

    assert parsed["workflowId"] == "wf-20260305t000000z-a1b2c3d4"
    assert parsed["bindingType"] == "task_id"
    assert parsed["bindingId"] == "a7k"
    assert vm_state["phase"] == "Orchestrator Single-Panel Web UI"
    assert vm_state["subphase"] == "D1"
    assert vm_state["objective"] == "Update web agent page"
    assert "status=running" in vm_state["execution"]
    assert "turn=builder_run" in vm_state["execution"]
    assert "iter=1/3" in vm_state["execution"]
    assert "active_role=builder" in vm_state["execution"]
    assert "binding=task_id:a7k" in vm_state["execution"]


def test_agents_js_payload_allows_orchestrator_only_max_iterations_zero() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const nodes = {
          extraInfo: { value: "Use orchestrator chat only" },
          maxIterations: { value: "0" },
          timeoutSec: { value: "7000" },
        };

        const ctx = {
          URLSearchParams,
          window: {
            location: { search: "" },
            localStorage: {
              _m: {},
              getItem: function (k) { return this._m[k] || null; },
              setItem: function (k, v) { this._m[k] = String(v); },
              removeItem: function (k) { delete this._m[k]; },
            },
          },
          document: {
            addEventListener: ()=>{},
            getElementById: function (id) { return nodes[id] || null; },
          },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        t.setStateForTest({ selectedUid: "PIS-20260305-000000Z-A1B2", selectedTitle: "Test PIS", subphase: "PIS" });
        const payload = t.orchestrationPayloadFromUi();
        console.log(JSON.stringify(payload));
        """
    )
    out = _run_node_json(script)
    assert int((out.get("options") or {}).get("max_iterations")) == 0


def test_agents_js_payload_uses_task_binding_for_no_pis_mode() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const nodes = {
          extraInfo: { value: "Plan only" },
          maxIterations: { value: "0" },
          timeoutSec: { value: "7000" },
        };

        const ctx = {
          URLSearchParams,
          window: {
            location: { search: "" },
            localStorage: {
              _m: {},
              getItem: function (k) { return this._m[k] || null; },
              setItem: function (k, v) { this._m[k] = String(v); },
              removeItem: function (k) { delete this._m[k]; },
            },
          },
          document: {
            addEventListener: ()=>{},
            getElementById: function (id) { return nodes[id] || null; },
          },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        t.setStateForTest({ selectedUid: "__NO_PIS__", selectedTitle: "No PIS", subphase: "PIS" });
        const payload = t.orchestrationPayloadFromUi();
        console.log(JSON.stringify(payload));
        """
    )
    out = _run_node_json(script)
    assert str((out.get("binding") or {}).get("type") or "") == "task_id"
    assert str((out.get("binding") or {}).get("id") or "") == "a7k"
    assert int((out.get("options") or {}).get("max_iterations")) == 0


def test_agents_js_hydrate_syncs_max_iterations_control_from_workflow_state() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const nodes = {
          maxIterations: {
            value: "3",
            options: [
              { value: "0" },
              { value: "1" },
              { value: "2" },
              { value: "3" },
              { value: "4" },
              { value: "5" }
            ]
          }
        };

        const ctx = {
          URLSearchParams,
          window: {
            location: { search: "" },
            localStorage: {
              _m: {},
              getItem: function (k) { return this._m[k] || null; },
              setItem: function (k, v) { this._m[k] = String(v); },
              removeItem: function (k) { delete this._m[k]; },
            },
          },
          document: {
            addEventListener: ()=>{},
            getElementById: function (id) { return nodes[id] || null; },
          },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        t.hydrateFromOrchestrationState({ max_iterations: 4 });
        console.log(JSON.stringify({ value: nodes.maxIterations.value }));
        """
    )
    out = _run_node_json(script)
    assert str(out.get("value") or "") == "4"


def test_agents_js_detects_assignment_drift_when_max_iterations_changes_after_resolve() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const nodes = {
          maxIterations: {
            value: "3",
            options: [
              { value: "0" },
              { value: "1" },
              { value: "2" },
              { value: "3" },
              { value: "4" },
              { value: "5" }
            ]
          }
        };

        const ctx = {
          URLSearchParams,
          window: {
            location: { search: "" },
            localStorage: {
              _m: {},
              getItem: function (k) { return this._m[k] || null; },
              setItem: function (k, v) { this._m[k] = String(v); },
              removeItem: function (k) { delete this._m[k]; },
            },
          },
          document: {
            addEventListener: ()=>{},
            getElementById: function (id) { return nodes[id] || null; },
          },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        t.setStateForTest({
          workflowId: "wf-123",
          selectedUid: "PIS-20260307-211556Z-2A48",
          bindingType: "ledger_uid",
          bindingId: "PIS-20260307-211556Z-2A48",
        });
        t.hydrateFromOrchestrationState({
          binding: { type: "ledger_uid", id: "PIS-20260307-211556Z-2A48" },
          max_iterations: 3
        });
        nodes.maxIterations.value = "4";
        console.log(JSON.stringify({
          assigned: t.assignedWorkflowKeyFromState({
            binding: { type: "ledger_uid", id: "PIS-20260307-211556Z-2A48" },
            max_iterations: 3
          }),
          current: t.currentAssignmentKey(),
          drift: t.hasAssignmentDrift()
        }));
        """
    )
    out = _run_node_json(script)
    assert str(out.get("assigned") or "") == "ledger_uid:PIS-20260307-211556Z-2A48|max_iterations=3"
    assert str(out.get("current") or "") == "ledger_uid:PIS-20260307-211556Z-2A48|max_iterations=4"
    assert bool(out.get("drift")) is True


def test_agents_js_payload_leaves_ledger_objective_blank_for_backend_canonicalization() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const nodes = {
          extraInfo: { value: "maximum correction cycles before escalation is 4" },
          maxIterations: {
            value: "4",
            options: [
              { value: "0" },
              { value: "1" },
              { value: "2" },
              { value: "3" },
              { value: "4" }
            ]
          },
          timeoutSec: {
            value: "7000",
            options: [{ value: "7000" }]
          }
        };

        const ctx = {
          URLSearchParams,
          window: {
            location: { search: "" },
            localStorage: {
              _m: {},
              getItem: function (k) { return this._m[k] || null; },
              setItem: function (k, v) { this._m[k] = String(v); },
              removeItem: function (k) { delete this._m[k]; },
            },
          },
          document: {
            addEventListener: ()=>{},
            getElementById: function (id) { return nodes[id] || null; },
          },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        t.setStateForTest({
          selectedUid: "PIS-20260307-211556Z-2A48",
          selectedTitle: "Fix workflow iteration mismatch",
          phaseLabel: "WF-01",
          subphase: "PIS",
        });
        const payload = t.orchestrationPayloadFromUi();
        console.log(JSON.stringify(payload));
        """
    )
    out = _run_node_json(script)
    assert str(out.get("objective") or "") == ""
    assert str(((out.get("options") or {}).get("execution_directive")) or "") == "maximum correction cycles before escalation is 4"


def test_agents_js_hydrate_clears_stale_objective_when_backend_returns_blank() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: {
            location: { search: "" },
            localStorage: {
              _m: {},
              getItem: function (k) { return this._m[k] || null; },
              setItem: function (k, v) { this._m[k] = String(v); },
              removeItem: function (k) { delete this._m[k]; },
            },
          },
          document: {
            addEventListener: ()=>{},
            getElementById: ()=>null,
          },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        t.setStateForTest({ objective: "retry up to 3 iterations" });
        t.hydrateFromOrchestrationState({
          binding: { type: "ledger_uid", id: "PIS-20260307-211556Z-2A48" },
          objective: "",
          max_iterations: 4
        });
        console.log(JSON.stringify({ objective: t.orchestrationViewFromState({}).objective || "" }));
        """
    )
    out = _run_node_json(script)
    assert str(out.get("objective") or "") == "-"


def test_agents_js_preferred_selection_uses_bound_ledger_uid_on_refresh() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: {
            location: { search: "" },
            localStorage: {
              _m: {},
              getItem: function (k) { return this._m[k] || null; },
              setItem: function (k, v) { this._m[k] = String(v); },
              removeItem: function (k) { delete this._m[k]; },
            },
          },
          document: {
            addEventListener: ()=>{},
            getElementById: function (_id) { return null; },
          },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        t.setStateForTest({ selectedUid: "", bindingType: "ledger_uid", bindingId: "PIS-20260305-142025Z-33C8" });
        const chosen = t.preferredPisSelection("");
        console.log(JSON.stringify({ chosen }));
        """
    )
    out = _run_node_json(script)
    assert str(out.get("chosen") or "") == "PIS-20260305-142025Z-33C8"


def test_agents_js_preferred_selection_saved_pis_beats_default_no_pis_dom() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: {
            location: { search: "" },
            localStorage: {
              _m: { eiel_agents_last_pis_uid: "PIS-20260305-142025Z-33C8" },
              getItem: function (k) { return this._m[k] || null; },
              setItem: function (k, v) { this._m[k] = String(v); },
              removeItem: function (k) { delete this._m[k]; },
            },
          },
          document: {
            addEventListener: ()=>{},
            getElementById: function (_id) { return null; },
          },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        t.setStateForTest({ selectedUid: "", bindingType: "", bindingId: "" });
        const chosen = t.preferredPisSelection("__NO_PIS__");
        console.log(JSON.stringify({ chosen }));
        """
    )
    out = _run_node_json(script)
    assert str(out.get("chosen") or "") == "PIS-20260305-142025Z-33C8"


def test_agents_js_role_elapsed_display_tracks_only_active_role() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: {
            location: { search: "" },
            localStorage: {
              _m: {},
              getItem: function (k) { return this._m[k] || null; },
              setItem: function (k, v) { this._m[k] = String(v); },
              removeItem: function (k) { delete this._m[k]; },
            },
          },
          document: {
            addEventListener: ()=>{},
            getElementById: function (_id) { return null; },
          },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        t.setStateForTest({
          status: "running",
          activeTurnRole: "builder",
          activeTurnStartedUtc: "2026-03-05T10:00:00Z",
        });
        const map1 = t.roleElapsedDisplayMap(Date.parse("2026-03-05T10:00:05Z"));
        t.setStateForTest({
          status: "running",
          activeTurnRole: "",
          activeTurnName: "verifier_run",
          activeTurnStartedUtc: "2026-03-05T10:00:10Z",
        });
        const map2 = t.roleElapsedDisplayMap(Date.parse("2026-03-05T10:00:17Z"));
        t.setStateForTest({ status: "idle", activeTurnRole: "builder" });
        const map3 = t.roleElapsedDisplayMap(Date.parse("2026-03-05T10:00:20Z"));
        console.log(JSON.stringify({ map1, map2, map3 }));
        """
    )
    out = _run_node_json(script)
    assert str((out.get("map1") or {}).get("builder") or "") == "5s"
    assert str((out.get("map1") or {}).get("assurer") or "") == "idle"
    assert str((out.get("map2") or {}).get("assurer") or "") == "7s"
    assert str((out.get("map2") or {}).get("builder") or "") == "idle"
    assert str((out.get("map3") or {}).get("builder") or "") == "idle"


def test_agents_js_single_flight_guard_skips_overlapping_poll_ticks() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: { location: { search: "" } },
          document: { addEventListener: ()=>{} },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;

        (async () => {
          let release;
          const hold = new Promise((resolve) => { release = resolve; });
          const p1 = t.runSingleFlight("poll", async () => { await hold; });
          const p2 = t.runSingleFlight("poll", async () => {});
          const second = await p2;
          release();
          const first = await p1;
          const third = await t.runSingleFlight("poll", async () => {});
          console.log(JSON.stringify({ first, second, third }));
        })().catch((err) => {
          console.log(JSON.stringify({ error: String(err && err.message || err) }));
          process.exitCode = 1;
        });
        """
    )
    out = _run_node_json(script)
    assert out.get("error") is None
    assert out["first"] is True
    assert out["second"] is False
    assert out["third"] is True


def test_agents_js_worker_limit_backoff_is_applied_and_resets() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: { location: { search: "" } },
          document: { addEventListener: ()=>{}, hidden: false },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;

        t.resetPollingBackoff();
        const accepted1 = t.notePollingError(new Error("worker limit reached"));
        const s1 = t.pollingState();
        const accepted2 = t.notePollingError(new Error("HTTP 429"));
        const s2 = t.pollingState();
        const accepted3 = t.notePollingError(new Error("different error"));
        const recognized = t.isWorkerLimitError(new Error("Too many requests"));
        t.resetPollingBackoff();
        const sReset = t.pollingState();
        console.log(JSON.stringify({ accepted1, accepted2, accepted3, recognized, s1, s2, sReset }));
        """
    )
    out = _run_node_json(script)
    assert out["accepted1"] is True
    assert out["accepted2"] is True
    assert out["accepted3"] is False
    assert out["recognized"] is True
    assert int(out["s1"]["backoffMs"]) >= 5000
    assert int(out["s2"]["backoffMs"]) >= int(out["s1"]["backoffMs"])
    assert int(out["s2"]["cooldownRemainingMs"]) > 0
    assert int(out["sReset"]["backoffMs"]) == 0
    assert int(out["sReset"]["cooldownRemainingMs"]) == 0


def test_agents_js_chatter_polling_is_throttled_with_force_override() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: { location: { search: "" } },
          document: { addEventListener: ()=>{}, hidden: false },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        const n = Number(t.CHATTER_POLL_EVERY_N || 0);
        const a = t.shouldRefreshChatterForTick(1, false);
        const b = t.shouldRefreshChatterForTick(n, false);
        const c = t.shouldRefreshChatterForTick(2, true);
        console.log(JSON.stringify({ n, a, b, c }));
        """
    )
    out = _run_node_json(script)
    assert int(out["n"]) >= 1
    assert out["a"] is False
    assert out["b"] is True
    assert out["c"] is True


def test_agents_js_chatter_role_selection_reduces_per_tick_request_fanout() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: { location: { search: "" } },
          document: { addEventListener: ()=>{}, hidden: false },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        const r1 = t.selectChatterRolesForRefresh(false);
        const r2 = t.selectChatterRolesForRefresh(false);
        const r3 = t.selectChatterRolesForRefresh(false);
        const rf = t.selectChatterRolesForRefresh(true);
        console.log(JSON.stringify({ r1, r2, r3, rf }));
        """
    )
    out = _run_node_json(script)
    assert out["r1"] == ["builder"]
    assert out["r2"] == ["assurer"]
    assert out["r3"] == ["generalist"]
    assert out["rf"] == ["builder", "assurer", "generalist"]


def test_agents_js_cooldown_state_gates_followup_chatter_refresh() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: { location: { search: "" } },
          document: { addEventListener: ()=>{}, hidden: false },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;

        t.resetPollingBackoff();
        const before = t.isPollingCooldownActive();
        t.notePollingError(new Error("worker limit reached"));
        const during = t.isPollingCooldownActive();
        t.resetPollingBackoff();
        const after = t.isPollingCooldownActive();
        console.log(JSON.stringify({ before, during, after }));
        """
    )
    out = _run_node_json(script)
    assert out["before"] is False
    assert out["during"] is True
    assert out["after"] is False


def test_agents_js_refresh_button_uses_single_flight_poll_guard() -> None:
    src = (_repo_root() / "infra" / "webui" / "static" / "agents.js").read_text(encoding="utf-8")
    assert 'runSingleFlight("poll"' in src


def test_agents_js_auto_chatter_refresh_requires_running_workflow_unless_forced() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: { location: { search: "" } },
          document: { addEventListener: ()=>{} },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        t.setStateForTest({ selectedUid: "PIS-1", workflowId: "wf-1", status: "running" });
        const runningEnabled = t.shouldAutoRefreshChatter(false);
        t.setStateForTest({ status: "idle" });
        const idleEnabled = t.shouldAutoRefreshChatter(false);
        const forcedEnabled = t.shouldAutoRefreshChatter(true);

        console.log(JSON.stringify({ runningEnabled, idleEnabled, forcedEnabled }));
        """
    )
    out = _run_node_json(script)
    assert out["runningEnabled"] is True
    assert out["idleEnabled"] is False
    assert out["forcedEnabled"] is True


def test_agents_js_meaningful_chatter_filter_hides_internal_user_prompts_by_default() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: { location: { search: "" } },
          document: { addEventListener: ()=>{} },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        const rows = [
          { sender: "user", content: "Implement this PIS scope in the repo." },
          { sender: "assistant", content: "Builder output summary" },
        ];
        const filtered = t.chatterRowsForDisplay(rows, true);
        const raw = t.chatterRowsForDisplay(rows, false);
        console.log(JSON.stringify({ filtered, raw }));
        """
    )
    out = _run_node_json(script)
    assert len(out["filtered"]) == 1
    assert out["filtered"][0]["sender"] == "assistant"
    assert len(out["raw"]) == 2


def test_agents_js_meaningful_conversation_filter_hides_status_bubbles_by_default() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: { location: { search: "" } },
          document: { addEventListener: ()=>{} },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        const items = [
          { kind: "status", text: "Started `builder`." },
          { kind: "message", text: "Actual builder response" },
          { kind: "summary", text: "Final verifier summary" },
        ];
        const filtered = t.conversationItemsForDisplay(items, true);
        const raw = t.conversationItemsForDisplay(items, false);
        console.log(JSON.stringify({ filtered, raw }));
        """
    )
    out = _run_node_json(script)
    assert [row["kind"] for row in out["filtered"]] == ["message", "summary"]
    assert len(out["raw"]) == 3


def test_agents_js_meaningful_conversation_filter_prefers_session_dialogue() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: { location: { search: "" } },
          document: { addEventListener: ()=>{} },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        const items = [
          { kind: "message", origin: "synthetic", text: "Execute bound PIS `PIS-1`." },
          { kind: "summary", origin: "synthetic", text: "Workflow status: running" },
          { kind: "message", origin: "session", text: "Actual builder discussion" },
        ];
        const filtered = t.conversationItemsForDisplay(items, true);
        console.log(JSON.stringify({ filtered }));
        """
    )
    out = _run_node_json(script)
    assert len(out["filtered"]) == 1
    assert str(out["filtered"][0]["origin"]) == "session"
    assert str(out["filtered"][0]["text"]) == "Actual builder discussion"


def test_agents_js_idle_poll_delay_is_higher_than_active_delay() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const src = fs.readFileSync("infra/webui/static/agents.js", "utf8");

        const ctx = {
          URLSearchParams,
          window: { location: { search: "" } },
          document: { addEventListener: ()=>{} },
          console: { log: ()=>{}, error: ()=>{} },
        };

        vm.createContext(ctx);
        vm.runInContext(src, ctx);
        const t = ctx.window.__EIEL_AGENTS_TEST__;
        const active = t.basePollDelayMsForStatus("running");
        const idle = t.basePollDelayMsForStatus("idle");
        console.log(JSON.stringify({ active, idle, refresh: t.REFRESH_MS, idleConst: t.IDLE_REFRESH_MS }));
        """
    )
    out = _run_node_json(script)
    assert int(out["active"]) == int(out["refresh"])
    assert int(out["idle"]) == int(out["idleConst"])
    assert int(out["idle"]) > int(out["active"])
