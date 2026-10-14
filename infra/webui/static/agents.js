/* infra/webui/static/agents.js
 * Last modified: 2026-03-05 Europe/Vienna
 */

(function () {
  "use strict";

  const ROLES = ["builder", "assurer", "generalist"];
  const REFRESH_MS = 2000;
  const IDLE_REFRESH_MS = 6000;
  const CHATTER_POLL_EVERY_N = 3;
  const BASE_BACKOFF_MS = 5000;
  const MAX_BACKOFF_MS = 30000;

  const State = {
    selectedUid: "",
    selectedTitle: "",
    selectedSlug: "",
    selectedExcerpt: "",
    selectedPisMarkdown: "",
    workflowId: "",
    workflowCreatedUtc: "",
    latestWorkflowState: null,
    status: "idle",
    heartbeatUtc: "",
    latestWorkflowEvents: [],
    activeTurnRole: "",
    activeTurnName: "",
    activeTurnSeq: 0,
    activeTurnStartedUtc: "",
    pollingTimerId: null,
    uiTickTimerId: null,
    bindingType: "",
    bindingId: "",
    phaseLabel: "Orchestrator Single-Panel Web UI",
    subphase: "Iteration 1",
    objective: "",
    turn: "",
    iteration: 0,
    maxIterations: 0,
    assignedWorkflowKey: "",
    lastStepUtc: "",
    streamActive: false,
    chatterFilterRole: "all",
    chatterRowsByRole: {},
    chatterMeaningfulOnly: true,
    chatterUnreadByRole: { builder: 0, assurer: 0, generalist: 0 },
    chatterLastUtcByRole: { builder: "", assurer: "", generalist: "" },
    chatterDeferredRowsByRole: null,
    chatterRoleCursor: 0,
    conversationItems: [],
    conversationMeaningfulOnly: true,
    conversationDeferredItems: null,
    conversationFollowBottom: true,
    pollTickSeq: 0,
    setupAutoCollapsed: false,
  };

  const Polling = {
    inFlight: Object.create(null),
    backoffMs: 0,
    cooldownUntilMs: 0,
  };

  const CLOSED_STATUSES = { done: true, deprecated: true, abandoned: true };
  const NO_PIS_UID = "__NO_PIS__";
  const NO_PIS_TASK_ID = "a7k";
  const LAST_PIS_KEY = "eiel_agents_last_pis_uid";
  const EXTRA_INFO_GLOBAL_KEY = "eiel_agents_extra_info_global";
  const EXTRA_INFO_PIS_PREFIX = "eiel_agents_extra_info_pis::";

  function q(id) {
    return document.getElementById(id);
  }

  function requireDom(id) {
    const el = q(id);
    if (!el) throw new Error("Missing required UI element: #" + id);
    return el;
  }

  function requireAnyDom(ids) {
    const list = Array.isArray(ids) ? ids : [];
    for (let i = 0; i < list.length; i += 1) {
      const el = q(list[i]);
      if (el) return el;
    }
    throw new Error("Missing required UI element: one of " + list.join(", "));
  }

  function intInRange(raw, fallback, min, max) {
    const n = parseInt(String(raw == null ? "" : raw).trim(), 10);
    if (!Number.isFinite(n)) return fallback;
    return Math.max(min, Math.min(max, n));
  }

  function getLastPisUid() {
    try {
      return String(window.localStorage.getItem(LAST_PIS_KEY) || "").trim();
    } catch (_e) {
      return "";
    }
  }

  function setLastPisUid(uid) {
    try {
      const v = String(uid || "").trim();
      if (!v) {
        window.localStorage.removeItem(LAST_PIS_KEY);
      } else {
        window.localStorage.setItem(LAST_PIS_KEY, v);
      }
    } catch (_e) {
      /* best effort */
    }
  }

  function extraInfoKeyForUid(uid) {
    const v = String(uid || "").trim();
    return v ? (EXTRA_INFO_PIS_PREFIX + v) : "";
  }

  function readStoredExtraInfo(uid) {
    try {
      const key = extraInfoKeyForUid(uid);
      if (key) {
        const v = window.localStorage.getItem(key);
        if (v != null) return String(v);
      }
      return String(window.localStorage.getItem(EXTRA_INFO_GLOBAL_KEY) || "");
    } catch (_e) {
      return "";
    }
  }

  function persistExtraInfo(uid, text) {
    try {
      const raw = String(text == null ? "" : text);
      window.localStorage.setItem(EXTRA_INFO_GLOBAL_KEY, raw);
      const key = extraInfoKeyForUid(uid);
      if (key) window.localStorage.setItem(key, raw);
    } catch (_e) {
      /* best effort */
    }
  }

  function restoreExtraInfoForUid(uid) {
    const el = q("extraInfo");
    if (!el) return;
    el.value = readStoredExtraInfo(uid);
  }

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;");
  }

  function clipLabel(s, maxLen) {
    const txt = String(s || "");
    const n = intInRange(maxLen, 96, 16, 240);
    if (txt.length <= n) return txt;
    return txt.slice(0, Math.max(0, n - 1)) + "…";
  }

  function isNoPisSelection(uid) {
    return String(uid || "").trim() === NO_PIS_UID;
  }

  function preferredPisSelection(domSelected) {
    const boundLedgerUid = (
      String(State.bindingType || "").trim() === "ledger_uid"
        ? String(State.bindingId || "").trim()
        : ""
    );
    return String(State.selectedUid || boundLedgerUid || getLastPisUid() || domSelected).trim();
  }

  function slugifyLabel(s) {
    const txt = String(s || "").trim().toLowerCase();
    if (!txt) return "";
    return txt
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 80);
  }

  function shortUid(uid) {
    const v = String(uid || "").trim();
    if (!v) return "";
    return v.length <= 4 ? v : v.slice(-4);
  }

  function actorLabel(actor) {
    const v = String(actor || "").trim().toLowerCase();
    if (v === "builder") return "Builder";
    if (v === "assurer") return "Verifier";
    if (v === "generalist") return "Orchestrator";
    if (v === "user") return "User";
    return "System";
  }

  function chatterRowsForDisplay(rows, meaningfulOnly) {
    const list = Array.isArray(rows) ? rows.slice() : [];
    if (!meaningfulOnly) return list;
    return list.filter(function (row) {
      const sender = String((row && row.sender) || "").trim().toLowerCase();
      const content = String((row && row.content) || "").trim();
      if (!content) return false;
      return sender === "assistant";
    });
  }

  function conversationItemsForDisplay(items, meaningfulOnly) {
    const list = Array.isArray(items) ? items.slice() : [];
    if (!meaningfulOnly) return list;
    const nonEmpty = list.filter(function (item) {
      const text = String((item && item.text) || "").trim();
      return !!text;
    });
    const sessionBacked = nonEmpty.filter(function (item) {
      const origin = String((item && item.origin) || "").trim().toLowerCase();
      return origin === "session";
    });
    if (sessionBacked.length) return sessionBacked;
    return nonEmpty.filter(function (item) {
      const kind = String((item && item.kind) || "").trim().toLowerCase();
      return kind !== "status";
    });
  }

  function updateChatterFilterButton() {
    const btn = q("btnToggleChatterFilter");
    if (!btn) return;
    btn.textContent = State.chatterMeaningfulOnly ? "Meaningful Only" : "Show Raw";
  }

  function updateConversationFilterButton() {
    const btn = q("btnToggleConversationFilter");
    if (!btn) return;
    btn.textContent = State.conversationMeaningfulOnly ? "Meaningful Only" : "Show All";
  }

  function conversationItemsFromStateAndEvents(state, events) {
    const st = state && typeof state === "object" ? state : {};
    const evs = Array.isArray(events) ? events : [];
    const items = [];

    function findEventUtc(eventName, role, turn) {
      const wantEvent = String(eventName || "").trim();
      const wantRole = String(role || "").trim().toLowerCase();
      const wantTurn = String(turn || "").trim().toLowerCase();
      for (let i = 0; i < evs.length; i += 1) {
        const row = evs[i] && typeof evs[i] === "object" ? evs[i] : {};
        if (String(row.event || "").trim() !== wantEvent) continue;
        const payload = row.payload && typeof row.payload === "object" ? row.payload : {};
        if (wantRole && String(payload.role || "").trim().toLowerCase() !== wantRole) continue;
        if (wantTurn && String(payload.turn || "").trim().toLowerCase() !== wantTurn) continue;
        return String(row.utc || "").trim();
      }
      return "";
    }

    function latestEventUtc(eventName, role, turn) {
      const wantEvent = String(eventName || "").trim();
      const wantRole = String(role || "").trim().toLowerCase();
      const wantTurn = String(turn || "").trim().toLowerCase();
      let picked = "";
      for (let i = 0; i < evs.length; i += 1) {
        const row = evs[i] && typeof evs[i] === "object" ? evs[i] : {};
        if (String(row.event || "").trim() !== wantEvent) continue;
        const payload = row.payload && typeof row.payload === "object" ? row.payload : {};
        if (wantRole && String(payload.role || "").trim().toLowerCase() !== wantRole) continue;
        if (wantTurn && String(payload.turn || "").trim().toLowerCase() !== wantTurn) continue;
        picked = String(row.utc || "").trim();
      }
      return picked;
    }

    function pushItem(utc, actor, source, target, kind, text, title) {
      const body = String(text || "").trim();
      if (!body) return;
      items.push({
        id: "conv-" + String(items.length + 1),
        utc: String(utc || "").trim(),
        actor: String(actor || "system").trim().toLowerCase() || "system",
        source: String(source || "system").trim().toLowerCase() || "system",
        target: String(target || "").trim().toLowerCase(),
        kind: String(kind || "summary").trim().toLowerCase() || "summary",
        title: String(title || "").trim() || (actorLabel(source) + " -> " + actorLabel(target)),
        text: body,
      });
    }

    const binding = st.binding && typeof st.binding === "object" ? st.binding : {};
    const bindingType = String(binding.type || "").trim().toLowerCase();
    const bindingId = String(binding.id || "").trim();
    const objective = String(st.objective || "").trim();
    const executionDirective = String(st.execution_directive || "").trim();
    const seBrief = String(st.se_brief || "").trim();
    const builderOutput = String(st.builder_output || "").trim();
    const verifierFeedback = String(st.verifier_feedback || "").trim();
    const intentValidationReport = String(st.intent_validation_report || "").trim();
    const reports = Array.isArray(st.reports) ? st.reports : [];
    const maxIterations = intInRange(st.max_iterations, 0, 0, 9999);

    const startLines = [];
    if (bindingType === "ledger_uid" && bindingId) {
      startLines.push("Execute bound PIS `" + bindingId + "`.");
    } else if (bindingType && bindingId) {
      startLines.push("Start workflow bound to `" + bindingType + ":" + bindingId + "`.");
    }
    if (maxIterations > 0) startLines.push("Workflow config: max_iterations=" + String(maxIterations) + ".");
    if (objective) startLines.push(objective);
    if (executionDirective) startLines.push(executionDirective);
    pushItem(findEventUtc("workflow_started") || String(st.created_utc || ""), "user", "user", "generalist", "message", startLines.join("\n\n"), "User -> Orchestrator");
    pushItem(findEventUtc("se_tasking_done") || latestEventUtc("role_turn_finished", "generalist", "se_tasking") || String(st.last_step_utc || ""), "generalist", "generalist", "builder", "message", seBrief, "Orchestrator -> Builder");

    if (builderOutput) {
      pushItem(findEventUtc("builder_run_done") || latestEventUtc("role_turn_finished", "builder", "") || String(st.updated_utc || ""), "builder", "builder", "generalist", "message", builderOutput, "Builder -> Orchestrator");
    } else {
      const builderStarted = findEventUtc("role_turn_started", "builder", "");
      const builderHeartbeat = latestEventUtc("role_turn_heartbeat", "builder", "");
      const statusBits = [];
      if (builderStarted) {
        statusBits.push("Builder turn is active.");
        if (st.active_turn_name || st.turn) statusBits.push("turn=" + String(st.active_turn_name || st.turn || "").trim());
        if (builderHeartbeat) statusBits.push("latest heartbeat=" + builderHeartbeat);
      }
      pushItem(builderHeartbeat || builderStarted, "builder", "builder", "generalist", "status", statusBits.join(" | "), "Builder -> Orchestrator");
    }

    pushItem(findEventUtc("verifier_run_done") || latestEventUtc("role_turn_finished", "assurer", "") || String(st.updated_utc || ""), "assurer", "assurer", "generalist", "message", verifierFeedback, "Verifier -> Orchestrator");

    const summaryLines = [];
    reports.forEach(function (row) {
      const txt = String(row || "").trim();
      if (txt) summaryLines.push(txt);
    });
    const verdict = String(st.verifier_verdict || "").trim();
    if (verdict && verdict.toUpperCase() !== "UNKNOWN") summaryLines.push("Verifier verdict: " + verdict);
    const status = String(st.status || "").trim();
    if (status) summaryLines.push("Workflow status: " + status);
    pushItem(findEventUtc("se_decision_done") || findEventUtc("workflow_cancelled") || findEventUtc("workflow_failed") || String(st.updated_utc || ""), "generalist", "generalist", "user", "summary", summaryLines.join("\n"), "Orchestrator -> User");
    pushItem(findEventUtc("intent_validation_done") || String(st.updated_utc || ""), "generalist", "generalist", "user", "summary", intentValidationReport, "Orchestrator -> User");

    evs.forEach(function (row) {
      const eventName = String((row && row.event) || "").trim();
      const utc = String((row && row.utc) || "").trim();
      const payload = row && row.payload && typeof row.payload === "object" ? row.payload : {};
      const role = String(payload.role || "").trim().toLowerCase();
      const turn = String(payload.turn || "").trim();

      if (eventName === "role_turn_started" && role) {
        pushItem(utc, role, role, role === "generalist" ? "system" : "generalist", "status", "Started `" + (turn || "turn") + "`.", actorLabel(role) + " status");
        return;
      }
      if (eventName === "role_turn_finished" && role) {
        const bits = ["Finished `" + (turn || "turn") + "`."];
        if (typeof payload.ok === "boolean") bits.push("ok=" + (payload.ok ? "true" : "false"));
        if (Number.isFinite(payload.duration_ms)) bits.push("duration_ms=" + String(payload.duration_ms));
        pushItem(utc, role, role, role === "generalist" ? "system" : "generalist", "status", bits.join(" | "), actorLabel(role) + " status");
        return;
      }
      if (eventName === "workflow_cancelled") {
        const reason = String(payload.reason || "").trim();
        pushItem(utc, "system", "system", "user", "status", "Workflow cancelled." + (reason ? (" reason=" + reason) : ""), "Workflow status");
        return;
      }
      if (eventName === "workflow_failed") {
        const error = String(payload.error || "").trim();
        pushItem(utc, "system", "system", "user", "status", "Workflow failed." + (error ? (" error=" + error) : ""), "Workflow status");
      }
    });

    items.sort(function (a, b) {
      const au = String(a.utc || "");
      const bu = String(b.utc || "");
      if (au === bu) return String(a.id || "").localeCompare(String(b.id || ""));
      return au.localeCompare(bu);
    });
    return items;
  }

  async function apiJson(url, opts) {
    const headers = Object.assign({ "Accept": "application/json" }, (opts && opts.headers) || {});
    const r = await fetch(url, Object.assign({ cache: "no-store", headers: headers }, opts || {}));
    let data = {};
    try {
      data = await r.json();
    } catch (_e) {
      data = { error: "non-json response" };
    }
    if (!r.ok) {
      throw new Error(String(data.error || ("HTTP " + r.status)));
    }
    return data;
  }

  function isoToMs(iso) {
    const t = Date.parse(String(iso || ""));
    return Number.isFinite(t) ? t : 0;
  }

  function humanSeconds(ms) {
    const sec = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
    return String(sec) + "s";
  }

  function inferRoleFromTurn(turnName) {
    const t = String(turnName || "").trim().toLowerCase();
    if (!t) return "";
    if (t.indexOf("builder") >= 0) return "builder";
    if (t.indexOf("assurer") >= 0 || t.indexOf("verifier") >= 0) return "assurer";
    if (t.indexOf("generalist") >= 0 || t.indexOf("se_") === 0 || t.indexOf("intent") >= 0 || t.indexOf("orchestrator") >= 0) return "generalist";
    return "";
  }

  function roleElapsedDisplayMap(nowMs) {
    const out = { builder: "idle", assurer: "idle", generalist: "idle" };
    const st = String(State.status || "").trim().toLowerCase();
    if (!(st === "running" || st === "active")) return out;

    let activeRole = String(State.activeTurnRole || "").trim().toLowerCase();
    if (ROLES.indexOf(activeRole) < 0) {
      activeRole = inferRoleFromTurn(State.activeTurnName || State.turn);
    }
    if (ROLES.indexOf(activeRole) < 0) return out;

    const now = Number.isFinite(nowMs) ? Number(nowMs) : Date.now();
    const startedMs = isoToMs(State.activeTurnStartedUtc) || now;
    out[activeRole] = humanSeconds(Math.max(0, now - startedMs));
    return out;
  }

  function elapsedText() {
    const startMs = isoToMs(State.workflowCreatedUtc);
    if (!startMs) return "elapsed: 0s";
    const sec = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
    return "elapsed: " + String(sec) + "s";
  }

  function parseOrchestrationQuery() {
    const search = String((window.location && window.location.search) || "");
    const sp = new URLSearchParams(search);
    return {
      workflowId: String(sp.get("workflow_id") || "").trim(),
      bindingType: String(sp.get("binding_type") || "").trim(),
      bindingId: String(sp.get("binding_id") || "").trim(),
    };
  }

  function isWorkerLimitError(err) {
    const msg = String((err && err.message) || err || "").toLowerCase();
    return msg.indexOf("worker limit reached") >= 0
      || msg.indexOf("too many requests") >= 0
      || msg.indexOf("http 429") >= 0;
  }

  function resetPollingBackoff() {
    Polling.backoffMs = 0;
    Polling.cooldownUntilMs = 0;
  }

  function notePollingError(err) {
    if (!isWorkerLimitError(err)) return false;
    const next = Polling.backoffMs > 0 ? Math.min(MAX_BACKOFF_MS, Polling.backoffMs * 2) : BASE_BACKOFF_MS;
    Polling.backoffMs = next;
    Polling.cooldownUntilMs = Date.now() + next;
    return true;
  }

  function pollingState() {
    return {
      backoffMs: Polling.backoffMs,
      cooldownRemainingMs: Math.max(0, Polling.cooldownUntilMs - Date.now()),
    };
  }

  function isPollingCooldownActive() {
    return Polling.cooldownUntilMs > Date.now();
  }

  function clearPollingTimer() {
    if (State.pollingTimerId) {
      clearTimeout(State.pollingTimerId);
      State.pollingTimerId = null;
    }
  }

  function clearUiTickTimer() {
    if (State.uiTickTimerId) {
      clearInterval(State.uiTickTimerId);
      State.uiTickTimerId = null;
    }
  }

  function orchestrationViewFromState(st) {
    const state = st && typeof st === "object" ? st : {};
    const subphases = Array.isArray(state.subphases) ? state.subphases : [];
    const idx = intInRange(state.subphase_index, 0, 0, Math.max(0, subphases.length - 1));
    const pickedSubphase = String(
      subphases[idx] || state.subphase || state.subphase_label || State.subphase || "Iteration 1"
    ).trim() || "Iteration 1";

    const workflowId = String(state.workflow_id || State.workflowId || "").trim();
    const phase = String(state.phase_label || State.phaseLabel || "Orchestrator Single-Panel Web UI").trim();
    const objective = String(state.objective || State.objective || "").trim() || "-";
    const status = String(state.status || State.status || "idle").trim() || "idle";
    const turn = String(state.turn || State.turn || "").trim() || "-";
    const iter = intInRange(state.iteration, 0, 0, 9999);
    const maxIter = intInRange(state.max_iterations, 0, 0, 9999);
    const activeRole = String(state.active_turn_role || State.activeTurnRole || "").trim() || "-";
    const lastStepUtc = String(state.last_step_utc || State.lastStepUtc || "").trim();

    const binding = state.binding && typeof state.binding === "object" ? state.binding : {};
    const bindingType = String(binding.type || State.bindingType || "").trim();
    const bindingId = String(binding.id || State.bindingId || "").trim();
    const bindingText = (bindingType && bindingId) ? (bindingType + ":" + bindingId) : "-";

    const bits = [
      "status=" + status,
      "turn=" + turn,
      "iter=" + String(iter) + "/" + String(maxIter),
      "active_role=" + activeRole,
    ];
    if (lastStepUtc) bits.push("last_step=" + lastStepUtc);
    bits.push("binding=" + bindingText);

    return {
      workflowId: workflowId,
      phase: phase,
      subphase: pickedSubphase,
      objective: objective,
      execution: bits.join(" | "),
      status: status,
      bindingType: bindingType,
      bindingId: bindingId,
    };
  }

  function deepLinkHref() {
    const usp = new URLSearchParams();
    if (State.workflowId) usp.set("workflow_id", State.workflowId);
    if (State.bindingType) usp.set("binding_type", State.bindingType);
    if (State.bindingId) usp.set("binding_id", State.bindingId);
    const qv = usp.toString();
    return qv ? ("/agents?" + qv) : "/agents";
  }

  function renderOrchestrationContext(view) {
    const v = view && typeof view === "object" ? view : orchestrationViewFromState({});
    const phase = q("orchPhase");
    const subphase = q("orchSubphase");
    const objective = q("orchObjective");
    const workflowId = q("orchWorkflowId");
    const workflowLink = q("orchWorkflowLink");

    if (phase) phase.textContent = String(v.phase || "-");
    if (subphase) subphase.textContent = String(v.subphase || "-");
    if (objective) objective.textContent = String(v.objective || "-");
    renderExecutionStatus(v);
    if (workflowId) workflowId.textContent = String(v.workflowId || "-");
    if (workflowLink) {
      const href = deepLinkHref();
      workflowLink.setAttribute("href", href);
      workflowLink.textContent = href;
    }
  }

  function executionStageFromStatus(status, hasWorkflow) {
    const st = String(status || "").trim().toLowerCase();
    if (st === "running" || st === "active") return "running";
    if (st === "completed" || st === "done") return "completed";
    if (st === "failed") return "failed";
    if (st === "cancelled" || st === "canceled" || st === "stopped") return "stopped";
    if (hasWorkflow) return "assigned";
    return "idle";
  }

  function renderExecutionStatus(view) {
    const host = q("orchExecutionStatus");
    if (!host) return;
    const label = q("orchExecutionLabel");
    const meta = q("orchExecutionMeta");

    const hasWorkflow = !!String((view && view.workflowId) || State.workflowId || "").trim();
    const status = String((view && view.status) || State.status || "idle").trim().toLowerCase();
    const stage = executionStageFromStatus(status, hasWorkflow);

    const stepStates = {
      assigned: "inactive",
      running: "inactive",
      stopped: "inactive",
      completed: "inactive",
      failed: "inactive",
    };

    if (stage === "assigned") {
      stepStates.assigned = "active";
    } else if (stage === "running") {
      stepStates.assigned = "done";
      stepStates.running = "active";
    } else if (stage === "stopped") {
      stepStates.assigned = "done";
      stepStates.running = "done";
      stepStates.stopped = "active";
    } else if (stage === "completed") {
      stepStates.assigned = "done";
      stepStates.running = "done";
      stepStates.completed = "active";
    } else if (stage === "failed") {
      stepStates.assigned = "done";
      stepStates.running = "done";
      stepStates.failed = "active";
    }

    const stepNodes = host.querySelectorAll("[data-exec-step]");
    stepNodes.forEach(function (node) {
      const step = String(node.getAttribute("data-exec-step") || "").trim().toLowerCase();
      const state = stepStates[step] || "inactive";
      node.classList.remove("is-active");
      node.classList.remove("is-done");
      if (state === "active") node.classList.add("is-active");
      if (state === "done") node.classList.add("is-done");
    });

    if (label) label.textContent = "current: " + stage;
    if (meta) meta.textContent = String((view && view.execution) || "-");
  }

  function renderAgentActivity() {
    const st = String(State.status || "").trim().toLowerCase();
    const running = st === "running" || st === "active";
    let activeRole = String(State.activeTurnRole || "").trim().toLowerCase();
    if (ROLES.indexOf(activeRole) < 0) {
      activeRole = inferRoleFromTurn(State.activeTurnName || State.turn);
    }
    const chips = {
      builder: q("agentChipBuilder"),
      assurer: q("agentChipAssurer"),
      generalist: q("agentChipGeneralist"),
    };
    const roleTimes = {
      builder: q("agentTimeBuilder"),
      assurer: q("agentTimeAssurer"),
      generalist: q("agentTimeGeneralist"),
    };
    const elapsed = roleElapsedDisplayMap(Date.now());
    Object.keys(chips).forEach(function (role) {
      const el = chips[role];
      if (!el) return;
      el.classList.remove("active");
      if (running && activeRole === role) {
        el.classList.add("active");
      }
      const et = roleTimes[role];
      if (et) et.textContent = String(elapsed[role] || "idle");
    });
  }

  function renderStatusLines() {
    const wf = q("workflowIdLine");
    const el = q("elapsedLine");
    const hb = q("heartbeatLine");
    const st = q("statusLine");
    if (wf) wf.textContent = "workflow: " + (State.workflowId || "-");
    if (el) el.textContent = elapsedText();
    if (hb) {
      const bits = [];
      bits.push("heartbeat: " + (State.heartbeatUtc || "-"));
      if (State.activeTurnRole) bits.push("role=" + State.activeTurnRole);
      if (State.activeTurnName) bits.push("turn=" + State.activeTurnName);
      if (State.activeTurnSeq) bits.push("seq=" + String(State.activeTurnSeq));
      hb.textContent = bits.join(" | ");
    }
    if (st) {
      const bits = ["status: " + (State.status || "idle")];
      if (State.maxIterations > 0) bits.push("max_iterations=" + String(State.maxIterations));
      if (hasAssignmentDrift()) bits.push("reassign required");
      st.textContent = bits.join(" | ");
    }
    renderOrchestrationContext(orchestrationViewFromState({
      workflow_id: State.workflowId,
      phase_label: State.phaseLabel,
      subphase: State.subphase,
      objective: State.objective,
      status: State.status,
      turn: State.turn || State.activeTurnName,
      iteration: State.iteration,
      max_iterations: State.maxIterations,
      active_turn_role: State.activeTurnRole,
      last_step_utc: State.lastStepUtc,
      binding: { type: State.bindingType, id: State.bindingId },
    }));
    updateActionButtons();
    updateRunSetupPanelState();
    renderAgentActivity();
    renderChatterTabs();
  }

  function setButtonEnabled(id, enabled, enabledTitle, disabledTitle) {
    const btn = q(id);
    if (!btn) return;
    btn.disabled = !enabled;
    btn.setAttribute("aria-disabled", enabled ? "false" : "true");
    btn.title = enabled ? String(enabledTitle || "") : String(disabledTitle || "");
  }

  function updateActionButtons() {
    const selectedUid = String(State.selectedUid || "").trim();
    const hasPis = !!selectedUid && !isNoPisSelection(selectedUid);
    const isNoPis = isNoPisSelection(selectedUid);
    const hasWorkflow = !!String(State.workflowId || "").trim();
    const st = String(State.status || "").trim().toLowerCase();
    const isRunning = st === "running" || st === "active";
    const isTerminal = st === "completed" || st === "done" || st === "cancelled" || st === "canceled" || st === "stopped" || st === "failed";
    const canResumeFrom = st === "cancelled" || st === "canceled" || st === "stopped" || st === "failed";
    const hasStarted = hasWorkflow && (isRunning || isTerminal);
    const assignmentDrift = hasAssignmentDrift();

    const selectedMaxIterations = intInRange(q("maxIterations") && q("maxIterations").value, 4, 0, 8);
    const canAssign = !isRunning && (hasPis || (isNoPis && selectedMaxIterations === 0));
    const canStart = hasWorkflow && !hasStarted && !assignmentDrift;
    const canStop = hasWorkflow && isRunning;
    const canResume = hasWorkflow && canResumeFrom;

    setButtonEnabled(
      "btnAssign",
      canAssign,
      hasPis ? "Bind selected PIS to orchestrator workflow." : "Start orchestrator-only workflow without a PIS.",
      isRunning
        ? "Cannot assign while workflow is running."
        : (isNoPis ? "For No PIS mode, set max_iterations to 0." : "Select a PIS first.")
    );
    setButtonEnabled(
      "btnStart",
      canStart,
      "Start assigned workflow.",
      !hasWorkflow
        ? "Assign workflow first."
        : assignmentDrift
          ? "Current PIS/max_iterations changed. Assign again before starting."
          : "Start is only available before first run."
    );
    setButtonEnabled("btnStop", canStop, "Stop currently running workflow.", hasWorkflow ? "Stop is available only while running." : "No workflow assigned.");
    setButtonEnabled("btnResume", canResume, "Resume stopped/failed workflow.", hasWorkflow ? "Resume is available after stop or failure." : "No workflow assigned.");
  }

  function updateRunSetupPanelState() {
    const panel = q("runSetupPanel");
    if (!panel) return;
    const hasWorkflow = !!String(State.workflowId || "").trim();
    const st = String(State.status || "").trim().toLowerCase();
    const hasStarted = hasWorkflow && (
      st === "running" || st === "active"
      || st === "completed" || st === "done"
      || st === "cancelled" || st === "canceled"
      || st === "stopped" || st === "failed"
    );
    if (hasStarted && !State.setupAutoCollapsed) {
      panel.open = false;
      State.setupAutoCollapsed = true;
    }
  }

  function renderPisPreview() {
    const title = q("pisTitle");
    const box = q("pisPreview");
    if (title) title.textContent = "title: " + (State.selectedTitle || "-");
    if (box) {
      const txt = isNoPisSelection(State.selectedUid)
        ? "No PIS selected. This mode is for orchestrator-only chat/planning and uses task binding."
        : String(State.selectedPisMarkdown || State.selectedExcerpt || "Select a PIS to preview.").trim();
      box.textContent = txt || "No preview available.";
    }
  }

  function renderPisSelectMeta() {
    const meta = q("pisSelectMeta");
    if (!meta) return;
    const title = clipLabel(State.selectedTitle || "-", 56);
    const slug = clipLabel(State.selectedSlug || "-", 24);
    meta.textContent = "title: " + title + " | slug: " + slug;
    meta.title = "title: " + (State.selectedTitle || "-") + " | slug: " + (State.selectedSlug || "-");
  }

  async function loadPisList() {
    const sel = q("pisSelect");
    if (!sel) return;
    const domSelected = String(sel.value || "").trim();
    const stickyUid = preferredPisSelection(domSelected);
    const j = await apiJson("/api/ledger/items?limit=500");
    const items = Array.isArray(j.items) ? j.items : [];
    const rows = items.filter(function (it) {
      return String(it.uid || "").startsWith("PIS-");
    });
    const activeRows = rows.filter(function (it) {
      const st = String(it.status || "").trim().toLowerCase();
      return !CLOSED_STATUSES[st];
    });
    const source = activeRows.length ? activeRows : rows;
    sel.innerHTML = "<option value='" + NO_PIS_UID + "'>No PIS (orchestrator-only chat)</option>"
      + "<option value=''>Select PIS...</option>" + rows.map(function (it) {
      const uid = String(it.uid || "").trim();
      const title = String(it.title || "").trim();
      const status = String(it.status || "").trim();
      const slug = String(it.slug || it.pis_slug || slugifyLabel(title) || "").trim();
      const labelFull = uid + " | " + status + " | " + title;
      const labelShown = shortUid(uid) + " | " + clipLabel((slug || "-"), 22);
      return "<option value='" + esc(uid) + "' data-title='" + esc(title) + "' data-excerpt='" + esc(String(it.pis_excerpt || "")) + "' data-slug='" + esc(slug) + "' title='" + esc(labelFull) + "'>"
        + esc(labelShown)
        + "</option>";
    }).join("");
    const allUids = Object.create(null);
    allUids[NO_PIS_UID] = true;
    rows.forEach(function (it) {
      const uid = String(it.uid || "").trim();
      if (uid) allUids[uid] = true;
    });

    let pickedUid = "";
    if (stickyUid && allUids[stickyUid]) {
      pickedUid = stickyUid;
    } else if (source.length) {
      pickedUid = String(source[0].uid || "").trim();
    }
    sel.value = pickedUid;
    State.selectedUid = pickedUid;
    setLastPisUid(pickedUid);
  }

  async function onSelectPis() {
    const sel = q("pisSelect");
    if (!sel) return;
    const prevUid = String(State.selectedUid || "").trim();
    const extraEl = q("extraInfo");
    if (extraEl) {
      persistExtraInfo(prevUid, String(extraEl.value || ""));
    }
    const uid = String(sel.value || "").trim();
    const opt = sel.selectedOptions && sel.selectedOptions[0];
    State.selectedUid = uid;
    State.selectedTitle = opt ? String(opt.getAttribute("data-title") || "") : "";
    State.selectedSlug = opt ? String(opt.getAttribute("data-slug") || "") : "";
    State.selectedExcerpt = opt ? String(opt.getAttribute("data-excerpt") || "") : "";
    State.selectedPisMarkdown = "";
    if (isNoPisSelection(uid)) {
      State.selectedTitle = "No PIS";
      State.selectedSlug = "orchestrator-only";
      State.selectedExcerpt = "Orchestrator-only chat mode without PIS binding.";
      State.bindingType = "task_id";
      State.bindingId = NO_PIS_TASK_ID;
    } else if (uid) {
      State.bindingType = "ledger_uid";
      State.bindingId = uid;
    }
    setLastPisUid(uid);
    restoreExtraInfoForUid(uid);
    renderPisSelectMeta();
    renderPisPreview();
    renderStatusLines();

    if (!uid || isNoPisSelection(uid)) return;
    try {
      const item = await apiJson("/api/ledger/item/" + encodeURIComponent(uid));
      State.selectedTitle = String(((item.meta || {}).title) || State.selectedTitle || "");
      State.selectedSlug = String(((item.meta || {}).slug) || item.slug || State.selectedSlug || slugifyLabel(State.selectedTitle) || "");
      State.selectedPisMarkdown = String(item.pis_markdown || "");
      renderPisSelectMeta();
      renderPisPreview();
    } catch (_e) {
      if (!State.selectedSlug) State.selectedSlug = slugifyLabel(State.selectedTitle);
      renderPisSelectMeta();
      renderPisPreview();
    }
  }

  function orchestrationPayloadFromUi() {
    const extraInfo = String((q("extraInfo") && q("extraInfo").value) || "").trim();
    persistExtraInfo(State.selectedUid, extraInfo);
    const maxIterations = intInRange(q("maxIterations") && q("maxIterations").value, 4, 0, 8);
    const timeoutSec = intInRange(q("timeoutSec") && q("timeoutSec").value, 7000, 30, 21600);
    const noPis = isNoPisSelection(State.selectedUid);
    const objective = noPis ? "Orchestrator-only chat (no PIS binding)" : "";
    return {
      binding: noPis
        ? { type: "task_id", id: NO_PIS_TASK_ID }
        : { type: "ledger_uid", id: State.selectedUid },
      phase_label: State.phaseLabel,
      objective: objective,
      timeout_s: timeoutSec,
      options: {
        subphases: [State.subphase],
        execution_directive: extraInfo,
        max_iterations: maxIterations,
        heartbeat_interval_s: 30,
      },
    };
  }

  function workflowAssignmentKey(bindingType, bindingId, maxIterations) {
    const bt = String(bindingType || "").trim().toLowerCase();
    const bid = String(bindingId || "").trim();
    const mi = intInRange(maxIterations, 0, 0, 9999);
    if (!bt || !bid) return "";
    return bt + ":" + bid + "|max_iterations=" + String(mi);
  }

  function currentAssignmentKey() {
    const selectedUid = String(State.selectedUid || "").trim();
    const maxIterations = intInRange(q("maxIterations") && q("maxIterations").value, 4, 0, 8);
    if (!selectedUid) return "";
    if (isNoPisSelection(selectedUid)) {
      return workflowAssignmentKey("task_id", NO_PIS_TASK_ID, maxIterations);
    }
    return workflowAssignmentKey("ledger_uid", selectedUid, maxIterations);
  }

  function assignedWorkflowKeyFromState(st) {
    const s = st && typeof st === "object" ? st : {};
    const binding = s.binding && typeof s.binding === "object" ? s.binding : {};
    return workflowAssignmentKey(
      binding.type || State.bindingType || "",
      binding.id || State.bindingId || "",
      s.max_iterations
    );
  }

  function hasAssignmentDrift() {
    if (!String(State.workflowId || "").trim()) return false;
    if (!String(State.assignedWorkflowKey || "").trim()) return false;
    return currentAssignmentKey() !== String(State.assignedWorkflowKey || "").trim();
  }

  function syncOrchestrationControlsFromState() {
    const maxIterationsEl = q("maxIterations");
    const maxIterationsValue = String(State.maxIterations || "").trim();
    if (maxIterationsEl && maxIterationsValue) {
      const hasOption = Array.prototype.some.call(maxIterationsEl.options || [], function (opt) {
        return String((opt && opt.value) || "").trim() === maxIterationsValue;
      });
      if (hasOption) maxIterationsEl.value = maxIterationsValue;
    }
  }

  function hydrateFromOrchestrationState(st) {
    const s = st && typeof st === "object" ? st : {};
    State.workflowCreatedUtc = String(s.created_utc || State.workflowCreatedUtc || "");
    State.status = String(s.status || State.status || "idle");
    State.heartbeatUtc = String(s.last_heartbeat_utc || State.heartbeatUtc || "");
    State.activeTurnRole = String(s.active_turn_role || State.activeTurnRole || "");
    State.activeTurnName = String(s.active_turn_name || State.activeTurnName || "");
    State.activeTurnSeq = Number(s.active_turn_heartbeat_seq || State.activeTurnSeq || 0);
    State.activeTurnStartedUtc = String(s.active_turn_started_utc || State.activeTurnStartedUtc || "");
    State.turn = String(s.turn || State.turn || State.activeTurnName || "");
    State.iteration = intInRange(s.iteration, State.iteration || 0, 0, 9999);
    State.maxIterations = intInRange(s.max_iterations, State.maxIterations || 0, 0, 9999);
    State.lastStepUtc = String(s.last_step_utc || State.lastStepUtc || "");
    State.phaseLabel = String(s.phase_label || State.phaseLabel || "Orchestrator Single-Panel Web UI");

    const subphases = Array.isArray(s.subphases) ? s.subphases : [];
    if (subphases.length) {
      const idx = intInRange(s.subphase_index, 0, 0, subphases.length - 1);
      State.subphase = String(subphases[idx] || State.subphase || "Iteration 1");
    }
    if (Object.prototype.hasOwnProperty.call(s, "objective")) {
      State.objective = String(s.objective || "").trim();
    }

    const binding = s.binding && typeof s.binding === "object" ? s.binding : {};
    const bt = String(binding.type || "").trim();
    const bid = String(binding.id || "").trim();
    if (bt) State.bindingType = bt;
    if (bid) State.bindingId = bid;
    State.assignedWorkflowKey = assignedWorkflowKeyFromState(s);
    syncOrchestrationControlsFromState();
  }

  async function assignToAgent() {
    const selectedUid = String(State.selectedUid || "").trim();
    if (!selectedUid) {
      window.alert("Select a PIS first.");
      return;
    }
    const maxIterations = intInRange(q("maxIterations") && q("maxIterations").value, 4, 0, 8);
    if (isNoPisSelection(selectedUid) && maxIterations !== 0) {
      window.alert("No PIS mode requires max_iterations = 0.");
      return;
    }
    const payload = orchestrationPayloadFromUi();
    State.objective = String(payload.objective || "").trim();
    const j = await apiJson("/api/agents/orchestration/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    State.workflowId = String(j.workflow_id || "");
    State.bindingType = String((payload.binding || {}).type || "");
    State.bindingId = String((payload.binding || {}).id || "");
    const st = (j.state && typeof j.state === "object") ? j.state : {};
    hydrateFromOrchestrationState(st);
    renderStatusLines();
  }

  async function runWorkflow() {
    if (!State.workflowId) {
      window.alert("Assign a workflow first.");
      return;
    }
    if (hasAssignmentDrift()) {
      window.alert("PIS or max_iterations changed after assignment. Assign again before starting.");
      return;
    }
    const timeoutSec = intInRange(q("timeoutSec") && q("timeoutSec").value, 7000, 30, 21600);
    await apiJson("/api/agents/orchestration/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workflow_id: State.workflowId, max_steps: 20, heartbeat_interval_s: 30, timeout_s: timeoutSec }),
    });
    await refreshWorkflowStatus();
    await refreshChatter({ forceChatter: true });
  }

  async function stopWorkflow() {
    if (!State.workflowId) return;
    await apiJson("/api/agents/orchestration/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workflow_id: State.workflowId, reason: "stopped from UI" }),
    });
    await refreshWorkflowStatus();
  }

  async function resumeWorkflow() {
    await runWorkflow();
  }

  function mergeAndRenderChatter(rowsByRole) {
    const box = q("agentChatter");
    if (!box) return;
    const roleFilter = String(State.chatterFilterRole || "all").trim().toLowerCase();
    const merged = [];

    if (roleFilter === "all") {
      ROLES.forEach(function (role) {
        const rows = chatterRowsForDisplay(rowsByRole[role], State.chatterMeaningfulOnly);
        rows.forEach(function (r) {
          merged.push({
            utc: String(r.utc || ""),
            role: role,
            sender: String(r.sender || ""),
            content: String(r.content || ""),
          });
        });
      });
    } else {
      const rows = chatterRowsForDisplay(rowsByRole[roleFilter], State.chatterMeaningfulOnly);
      rows.forEach(function (r) {
        merged.push({
          utc: String(r.utc || ""),
          role: roleFilter,
          sender: String(r.sender || ""),
          content: String(r.content || ""),
        });
      });
    }

    merged.sort(function (a, b) { return isoToMs(a.utc) - isoToMs(b.utc); });
    const newestFirst = merged.slice(-300).reverse();
    const grouped = [];
    newestFirst.forEach(function (m) {
      const prev = grouped.length ? grouped[grouped.length - 1] : null;
      if (prev && prev.role === m.role && prev.sender === m.sender) {
        prev.content = prev.content ? (prev.content + "\n\n" + m.content) : m.content;
        if (isoToMs(m.utc) > isoToMs(prev.utc)) prev.utc = m.utc;
        return;
      }
      grouped.push({
        utc: m.utc,
        role: m.role,
        sender: m.sender,
        content: m.content,
      });
    });

    if (!grouped.length) {
      box.textContent = State.chatterMeaningfulOnly ? "No meaningful chatter yet." : "No chatter yet.";
      return;
    }
    box.innerHTML = grouped.map(function (m) {
      return "<div class='msg role-" + esc(m.role) + "'>"
        + "<div class='msg-meta'>[" + esc(m.utc || "-") + "] " + esc(m.role + "/" + (m.sender || "msg")) + "</div>"
        + "<div class='msg-text'>" + esc(m.content || "") + "</div>"
        + "</div>";
    }).join("");
  }

  function collectChatterRows(rowsByRole, roleFilter) {
    const filter = String(roleFilter || "all").trim().toLowerCase();
    const merged = [];
    if (filter === "all") {
      ROLES.forEach(function (role) {
        const rows = chatterRowsForDisplay(rowsByRole[role], State.chatterMeaningfulOnly);
        rows.forEach(function (r) {
          merged.push({
            utc: String(r.utc || ""),
            role: role,
            sender: String(r.sender || ""),
            content: String(r.content || ""),
          });
        });
      });
    } else {
      const rows = chatterRowsForDisplay(rowsByRole[filter], State.chatterMeaningfulOnly);
      rows.forEach(function (r) {
        merged.push({
          utc: String(r.utc || ""),
          role: filter,
          sender: String(r.sender || ""),
          content: String(r.content || ""),
        });
      });
    }
    merged.sort(function (a, b) { return isoToMs(a.utc) - isoToMs(b.utc); });
    return merged;
  }

  function hasActiveSelectionInBox(id) {
    const box = q(id);
    if (!box || !window.getSelection) return false;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount < 1 || sel.isCollapsed) return false;
    for (let i = 0; i < sel.rangeCount; i += 1) {
      const r = sel.getRangeAt(i);
      let n = r.commonAncestorContainer;
      if (n && n.nodeType === 3) n = n.parentNode;
      if (n && box.contains(n)) return true;
    }
    return false;
  }

  function renderWorkflowConversation(items) {
    const box = q("workflowConversation");
    const meta = q("workflowConversationMeta");
    if (!box) return;
    const remainingBeforeRender = box.scrollHeight - (box.scrollTop + box.clientHeight);
    const shouldFollow = State.conversationFollowBottom || remainingBeforeRender <= 24;
    const rows = conversationItemsForDisplay(items, State.conversationMeaningfulOnly);
    if (meta) {
      if (State.workflowId) {
        meta.textContent = "workflow: " + State.workflowId + " | status: " + (State.status || "idle")
          + (State.conversationMeaningfulOnly ? " | filtered=meaningful" : " | filtered=all");
      } else {
        meta.textContent = "Read-only transcript synthesized from workflow runtime files.";
      }
    }
    if (!rows.length) {
      box.textContent = State.workflowId
        ? (State.conversationMeaningfulOnly ? "No meaningful workflow conversation yet." : "No workflow conversation yet.")
        : "Assign or resolve a workflow to view its conversation.";
      if (shouldFollow) box.scrollTop = box.scrollHeight;
      return;
    }
    box.innerHTML = rows.map(function (item) {
      const actor = String(item.actor || "system").trim().toLowerCase() || "system";
      const title = String(item.title || (actorLabel(item.source) + " -> " + actorLabel(item.target))).trim();
      const utc = String(item.utc || "-").trim() || "-";
      const kind = String(item.kind || "summary").trim().toLowerCase() || "summary";
      return "<div class='wfconv-bubble actor-" + esc(actor) + "'>"
        + "<div class='wfconv-head'>"
        + "<div class='wfconv-head-main'><div class='wfconv-title'>" + esc(title) + "</div><div>" + esc(utc) + "</div></div>"
        + "<div class='wfconv-kind'>" + esc(kind) + "</div>"
        + "</div>"
        + "<div class='wfconv-text'>" + esc(String(item.text || "")) + "</div>"
        + "</div>";
    }).join("");
    if (shouldFollow) box.scrollTop = box.scrollHeight;
  }

  function renderWorkflowConversationGuarded(items) {
    if (hasActiveSelectionInBox("workflowConversation")) {
      State.conversationDeferredItems = Array.isArray(items) ? items.slice() : [];
      return;
    }
    State.conversationDeferredItems = null;
    renderWorkflowConversation(items);
  }

  function flushDeferredConversationIfPossible() {
    if (!State.conversationDeferredItems) return;
    if (hasActiveSelectionInBox("workflowConversation")) return;
    const pending = State.conversationDeferredItems;
    State.conversationDeferredItems = null;
    renderWorkflowConversation(pending);
  }

  async function copyWorkflowConversation() {
    const rows = conversationItemsForDisplay(State.conversationItems, State.conversationMeaningfulOnly);
    if (!rows.length) {
      window.alert("No workflow conversation to copy yet.");
      return;
    }
    const text = rows.map(function (item) {
      const title = String(item.title || "").trim() || (actorLabel(item.source) + " -> " + actorLabel(item.target));
      return "[" + String(item.utc || "-") + "] " + title + "\n" + String(item.text || "");
    }).join("\n\n");

    let copied = false;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        copied = true;
      } catch (_e) {
        copied = false;
      }
    }
    if (!copied) {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "readonly");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      try {
        copied = document.execCommand("copy");
      } catch (_e) {
        copied = false;
      }
      document.body.removeChild(ta);
    }
    if (!copied) {
      window.alert("Copy failed. Please try again.");
      return;
    }
    const btn = q("btnCopyConversation");
    if (btn) {
      const oldLabel = String(btn.textContent || "Copy All");
      btn.textContent = "Copied";
      setTimeout(function () {
        btn.textContent = oldLabel;
      }, 1000);
    }
  }

  async function copyActiveChatter() {
    const role = String(State.chatterFilterRole || "all").trim().toLowerCase();
    const rows = collectChatterRows(State.chatterRowsByRole || {}, role);
    if (!rows.length) {
      window.alert("No chatter to copy yet.");
      return;
    }
    const text = rows.map(function (m) {
      return "[" + (m.utc || "-") + "] " + m.role + "/" + (m.sender || "msg") + "\n" + (m.content || "");
    }).join("\n\n");

    let copied = false;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        copied = true;
      } catch (_e) {
        copied = false;
      }
    }
    if (!copied) {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "readonly");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      try {
        copied = document.execCommand("copy");
      } catch (_e) {
        copied = false;
      }
      document.body.removeChild(ta);
    }
    if (!copied) {
      window.alert("Copy failed. Please try again.");
      return;
    }
    const btn = q("btnCopyChatter");
    if (btn) {
      const oldLabel = String(btn.textContent || "Copy All");
      btn.textContent = "Copied";
      setTimeout(function () {
        btn.textContent = oldLabel;
      }, 1000);
    }
  }

  function renderChatterTabs() {
    updateChatterFilterButton();
    const active = String(State.chatterFilterRole || "all").trim().toLowerCase();
    ["tabAll", "tabBuilder", "tabAssurer", "tabGeneralist"].forEach(function (id) {
      const el = q(id);
      if (!el) return;
      const role = String(el.getAttribute("data-role") || "").trim().toLowerCase();
      const unread = role === "all"
        ? (State.chatterUnreadByRole.builder + State.chatterUnreadByRole.assurer + State.chatterUnreadByRole.generalist)
        : (State.chatterUnreadByRole[role] || 0);
      const st = String(State.status || "").trim().toLowerCase();
      const running = st === "running" || st === "active";
      const isProcessing = running && role !== "all" && role === String(State.activeTurnRole || "").trim().toLowerCase();
      if (role === active) {
        el.classList.add("active");
      } else {
        el.classList.remove("active");
      }
      if (unread > 0 && role !== active) {
        el.classList.add("has-unread");
      } else {
        el.classList.remove("has-unread");
      }
      el.classList.remove("is-processing", "processing-builder", "processing-assurer", "processing-generalist");
      if (isProcessing) {
        el.classList.add("is-processing", "processing-" + role);
      }
      const base = role === "all" ? "All" : (role.charAt(0).toUpperCase() + role.slice(1));
      el.innerHTML = unread > 0
        ? (esc(base) + "<span class='tab-badge'>" + String(unread) + "</span>")
        : esc(base);
    });
  }

  function detectChatterUnread(rowsByRole, rolesToCheck) {
    const roles = (Array.isArray(rolesToCheck) && rolesToCheck.length) ? rolesToCheck : ROLES;
    roles.forEach(function (role) {
      const rows = Array.isArray(rowsByRole[role]) ? rowsByRole[role] : [];
      let latestUtc = "";
      rows.forEach(function (r) {
        const utc = String(r.utc || "");
        if (!latestUtc || isoToMs(utc) > isoToMs(latestUtc)) latestUtc = utc;
      });
      const prev = String(State.chatterLastUtcByRole[role] || "");
      if (latestUtc && (!prev || isoToMs(latestUtc) > isoToMs(prev))) {
        const visible = State.chatterFilterRole === "all" || State.chatterFilterRole === role;
        if (!visible) {
          State.chatterUnreadByRole[role] = intInRange((State.chatterUnreadByRole[role] || 0) + 1, 1, 0, 9999);
        }
      }
      if (latestUtc) State.chatterLastUtcByRole[role] = latestUtc;
    });
  }

  function nextRoundRobinChatterRole() {
    const count = ROLES.length;
    if (!count) return "";
    const idx = intInRange(State.chatterRoleCursor, 0, 0, count - 1);
    const role = String(ROLES[idx] || "").trim().toLowerCase();
    State.chatterRoleCursor = (idx + 1) % count;
    return role;
  }

  function selectChatterRolesForRefresh(forceChatter) {
    if (forceChatter) return ROLES.slice();
    const selected = String(State.chatterFilterRole || "all").trim().toLowerCase();
    if (ROLES.indexOf(selected) >= 0) return [selected];
    const role = nextRoundRobinChatterRole();
    return role ? [role] : [];
  }

  function clearUnreadForCurrentTab() {
    const role = String(State.chatterFilterRole || "all").trim().toLowerCase();
    if (role === "all") {
      State.chatterUnreadByRole.builder = 0;
      State.chatterUnreadByRole.assurer = 0;
      State.chatterUnreadByRole.generalist = 0;
      return;
    }
    if (Object.prototype.hasOwnProperty.call(State.chatterUnreadByRole, role)) {
      State.chatterUnreadByRole[role] = 0;
    }
  }

  function hasActiveSelectionInChatter() {
    return hasActiveSelectionInBox("agentChatter");
  }

  function renderChatterGuarded(rowsByRole) {
    if (hasActiveSelectionInChatter()) {
      State.chatterDeferredRowsByRole = rowsByRole;
      return;
    }
    State.chatterDeferredRowsByRole = null;
    mergeAndRenderChatter(rowsByRole);
  }

  function flushDeferredChatterIfPossible() {
    if (!State.chatterDeferredRowsByRole) return;
    if (hasActiveSelectionInChatter()) return;
    const pending = State.chatterDeferredRowsByRole;
    State.chatterDeferredRowsByRole = null;
    mergeAndRenderChatter(pending);
  }

  async function refreshChatter(opts) {
    const o = opts && typeof opts === "object" ? opts : {};
    const forceChatter = !!o.forceChatter;
    if (!State.selectedUid) {
      State.chatterRowsByRole = {};
      clearUnreadForCurrentTab();
      renderChatterTabs();
      renderChatterGuarded(State.chatterRowsByRole);
      return;
    }
    const rowsByRole = {};
    ROLES.forEach(function (role) {
      rowsByRole[role] = Array.isArray(State.chatterRowsByRole[role]) ? State.chatterRowsByRole[role] : [];
    });
    const rolesToFetch = selectChatterRolesForRefresh(forceChatter);
    for (let i = 0; i < rolesToFetch.length; i += 1) {
      const role = String(rolesToFetch[i] || "").trim().toLowerCase();
      if (ROLES.indexOf(role) < 0) continue;
      try {
        const j = await apiJson("/api/agents/session?role=" + encodeURIComponent(role) + "&ledger_uid=" + encodeURIComponent(State.selectedUid));
        rowsByRole[role] = Array.isArray(j.session) ? j.session : [];
      } catch (e) {
        if (notePollingError(e)) break;
      }
    }
    detectChatterUnread(rowsByRole, rolesToFetch);
    clearUnreadForCurrentTab();
    State.chatterRowsByRole = rowsByRole;
    renderChatterTabs();
    renderChatterGuarded(State.chatterRowsByRole);
  }

  async function refreshWorkflowConversation() {
    if (!State.workflowId) {
      State.conversationItems = [];
      updateConversationFilterButton();
      renderWorkflowConversationGuarded(State.conversationItems);
      return;
    }
    const fallbackState = (State.latestWorkflowState && typeof State.latestWorkflowState === "object")
      ? State.latestWorkflowState
      : {
          workflow_id: State.workflowId,
          created_utc: State.workflowCreatedUtc,
          updated_utc: State.lastStepUtc || State.heartbeatUtc,
          status: State.status,
          objective: State.objective,
          turn: State.turn,
          active_turn_name: State.activeTurnName,
          active_turn_role: State.activeTurnRole,
          binding: { type: State.bindingType, id: State.bindingId },
        };
    const localItems = conversationItemsFromStateAndEvents(fallbackState, State.latestWorkflowEvents);
    if (localItems.length) {
      State.conversationItems = localItems;
      updateConversationFilterButton();
      renderWorkflowConversationGuarded(State.conversationItems);
    }
    try {
      const j = await apiJson("/api/agents/orchestration/conversation?workflow_id=" + encodeURIComponent(State.workflowId) + "&events_limit=240");
      State.conversationItems = Array.isArray(j.items) ? j.items : [];
      updateConversationFilterButton();
      renderWorkflowConversationGuarded(State.conversationItems);
    } catch (e) {
      State.conversationItems = localItems;
      updateConversationFilterButton();
      renderWorkflowConversationGuarded(State.conversationItems);
      if (!localItems.length) {
        const meta = q("workflowConversationMeta");
        if (meta) meta.textContent = "workflow: " + State.workflowId + " | conversation unavailable: " + String((e && e.message) || e || "error");
      }
    }
  }

  async function resolveLatestWorkflow(opts) {
    const o = opts && typeof opts === "object" ? opts : {};
    const byBinding = !!o.byBinding;
    const qs = new URLSearchParams({ limit: "1" });
    if (byBinding) {
      if (!State.bindingType || !State.bindingId) return false;
      qs.set("binding_type", State.bindingType);
      qs.set("binding_id", State.bindingId);
    }
    const j = await apiJson("/api/agents/orchestration/list?" + qs.toString());
    const rows = Array.isArray(j.items) ? j.items : [];
    if (!rows.length) return false;
    const picked = rows[0] && typeof rows[0] === "object" ? rows[0] : {};
    const wf = String(picked.workflow_id || "").trim();
    if (!wf) return false;
    State.workflowId = wf;
    const binding = picked.binding && typeof picked.binding === "object" ? picked.binding : {};
    if (binding.type) State.bindingType = String(binding.type || "").trim();
    if (binding.id) State.bindingId = String(binding.id || "").trim();
    return true;
  }

  async function refreshWorkflowStatus() {
    if (!State.workflowId) {
      try {
        await resolveLatestWorkflow({ byBinding: false });
      } catch (_e) {
        /* best effort */
      }
      if (!State.workflowId) {
        State.latestWorkflowState = null;
        State.latestWorkflowEvents = [];
        renderStatusLines();
        return;
      }
    }
    try {
      const j = await apiJson("/api/agents/orchestration/status?workflow_id=" + encodeURIComponent(State.workflowId) + "&events_limit=240");
      const st = (j.state && typeof j.state === "object") ? j.state : {};
      State.latestWorkflowState = st;
      hydrateFromOrchestrationState(st);
      State.latestWorkflowEvents = Array.isArray(j.events) ? j.events : [];
      if (String(State.bindingType || "").trim() === "ledger_uid" && String(State.bindingId || "").trim()) {
        const sel = q("pisSelect");
        if (sel && String(sel.value || "").trim() !== String(State.bindingId || "").trim()) {
          sel.value = State.bindingId;
          await onSelectPis();
        }
      }
      resetPollingBackoff();
      renderStatusLines();
    } catch (e) {
      State.latestWorkflowState = null;
      State.latestWorkflowEvents = [];
      notePollingError(e);
      renderStatusLines();
    }
  }

  async function runSingleFlight(key, fn) {
    const k = String(key || "default");
    if (Polling.inFlight[k]) return false;
    Polling.inFlight[k] = true;
    try {
      await fn();
      return true;
    } finally {
      delete Polling.inFlight[k];
    }
  }

  function shouldRefreshChatterForTick(tickSeq, forceChatter) {
    if (forceChatter) return true;
    const n = intInRange(CHATTER_POLL_EVERY_N, 3, 1, 60);
    const tick = intInRange(tickSeq, 0, 0, 1000000);
    return tick % n === 0;
  }

  function isRunningLikeStatus(rawStatus) {
    const status = String(rawStatus || "").trim().toLowerCase();
    return status === "running" || status === "active";
  }

  function shouldAutoRefreshChatter(forceChatter) {
    if (forceChatter) return true;
    if (!String(State.selectedUid || "").trim()) return false;
    if (isNoPisSelection(State.selectedUid)) return false;
    if (!String(State.workflowId || "").trim()) return false;
    return isRunningLikeStatus(State.status);
  }

  function basePollDelayMsForStatus(rawStatus) {
    return isRunningLikeStatus(rawStatus) ? REFRESH_MS : IDLE_REFRESH_MS;
  }

  async function refreshAll(opts) {
    const o = opts && typeof opts === "object" ? opts : {};
    const forceChatter = !!o.forceChatter;
    const tickSeq = intInRange(o.tickSeq, State.pollTickSeq || 0, 0, 1000000);
    await refreshWorkflowStatus();
    await refreshWorkflowConversation();
    if (isPollingCooldownActive()) return;
    if (!shouldAutoRefreshChatter(forceChatter)) return;
    if (shouldRefreshChatterForTick(tickSeq, forceChatter)) {
      await refreshChatter({ forceChatter: forceChatter });
    }
  }

  function nextPollDelayMs() {
    const now = Date.now();
    const cooldownMs = Math.max(0, Polling.cooldownUntilMs - now);
    return Math.max(basePollDelayMsForStatus(State.status), cooldownMs || 0);
  }

  function shouldPoll() {
    if (State.streamActive) return false;
    if (document && document.hidden) return false;
    if (isPollingCooldownActive()) return false;
    return true;
  }

  async function pollTick() {
    if (shouldPoll()) {
      State.pollTickSeq = intInRange((State.pollTickSeq || 0) + 1, 1, 1, 1000000);
      await runSingleFlight("poll", async function () {
        await refreshAll({ tickSeq: State.pollTickSeq, forceChatter: false });
      });
    }
    clearPollingTimer();
    State.pollingTimerId = setTimeout(function () {
      pollTick().catch(function (_e) {});
    }, nextPollDelayMs());
  }

  function startPolling() {
    clearPollingTimer();
    State.pollingTimerId = setTimeout(function () {
      pollTick().catch(function (_e) {});
    }, REFRESH_MS);
  }

  function startUiTickTimer() {
    clearUiTickTimer();
    State.uiTickTimerId = setInterval(function () {
      renderStatusLines();
    }, 1000);
  }

  function bindUi() {
    requireDom("pisSelect");
    requireDom("btnAssign");
    requireDom("btnStart");
    requireDom("btnStop");
    requireDom("btnResume");
    requireDom("btnResolveWorkflow");
    requireDom("extraInfo");
    requireDom("maxIterations");
    requireDom("timeoutSec");
    requireDom("workflowIdLine");
    requireDom("elapsedLine");
    requireDom("heartbeatLine");
    requireDom("statusLine");
    requireDom("agentActivity");
    requireDom("agentChipBuilder");
    requireDom("agentChipAssurer");
    requireDom("agentChipGeneralist");
    requireDom("agentTimeBuilder");
    requireDom("agentTimeAssurer");
    requireDom("agentTimeGeneralist");
    requireDom("pisSelectMeta");
    requireDom("pisTitle");
    requireDom("pisPreview");
    requireDom("agentChatter");
    requireDom("chatterTabs");
    requireDom("tabAll");
    requireDom("tabBuilder");
    requireDom("tabAssurer");
    requireDom("tabGeneralist");
    requireDom("btnCopyChatter");
    requireDom("runSetupPanel");
    requireDom("workflowConversation");
    requireDom("workflowConversationMeta");
    requireDom("btnCopyConversation");
    requireDom("orchCard");
    requireDom("orchPhase");
    requireDom("orchSubphase");
    requireDom("orchObjective");
    requireDom("orchExecutionStatus");
    requireDom("orchExecutionLabel");
    requireDom("orchExecutionMeta");
    requireDom("orchWorkflowId");
    requireDom("orchWorkflowLink");

    const sel = q("pisSelect");
    if (sel) sel.addEventListener("change", function () { onSelectPis().catch(function (_e) {}); });
    const extraInfo = q("extraInfo");
    if (extraInfo) {
      extraInfo.addEventListener("input", function () {
        persistExtraInfo(State.selectedUid, String(extraInfo.value || ""));
      });
    }
    const maxIterations = q("maxIterations");
    if (maxIterations) {
      maxIterations.addEventListener("change", function () {
        renderStatusLines();
      });
    }
    const btnAssign = q("btnAssign");
    if (btnAssign) btnAssign.addEventListener("click", function () { assignToAgent().catch(function (e) { window.alert(String((e && e.message) || e || "assign failed")); }); });
    const btnStart = q("btnStart");
    if (btnStart) btnStart.addEventListener("click", function () { runWorkflow().catch(function (e) { window.alert(String((e && e.message) || e || "start failed")); }); });
    const btnStop = q("btnStop");
    if (btnStop) btnStop.addEventListener("click", function () { stopWorkflow().catch(function (e) { window.alert(String((e && e.message) || e || "stop failed")); }); });
    const btnResume = q("btnResume");
    if (btnResume) btnResume.addEventListener("click", function () { resumeWorkflow().catch(function (e) { window.alert(String((e && e.message) || e || "resume failed")); }); });
    const btnResolve = q("btnResolveWorkflow");
    if (btnResolve) {
      btnResolve.addEventListener("click", function () {
        resolveLatestWorkflow({ byBinding: true })
          .then(function (ok) {
            if (ok) return refreshWorkflowStatus();
            return null;
          })
          .catch(function (e) {
            window.alert(String((e && e.message) || e || "resolve failed"));
          });
      });
    }
    const btnRefresh = q("btnRefresh");
    if (btnRefresh) {
      btnRefresh.addEventListener("click", function () {
        runSingleFlight("poll", async function () {
          await refreshAll({ forceChatter: true });
        }).catch(function (_e) {});
      });
    }
    const btnCopyChatter = q("btnCopyChatter");
    if (btnCopyChatter) {
      btnCopyChatter.addEventListener("click", function () {
        copyActiveChatter().catch(function (_e) {
          window.alert("Copy failed. Please try again.");
        });
      });
    }
    const btnCopyConversation = q("btnCopyConversation");
    if (btnCopyConversation) {
      btnCopyConversation.addEventListener("click", function () {
        copyWorkflowConversation().catch(function (_e) {
          window.alert("Copy failed. Please try again.");
        });
      });
    }
    const btnToggleChatterFilter = q("btnToggleChatterFilter");
    if (btnToggleChatterFilter) {
      btnToggleChatterFilter.addEventListener("click", function () {
        State.chatterMeaningfulOnly = !State.chatterMeaningfulOnly;
        renderChatterTabs();
        renderChatterGuarded(State.chatterRowsByRole || {});
      });
    }
    const btnToggleConversationFilter = q("btnToggleConversationFilter");
    if (btnToggleConversationFilter) {
      btnToggleConversationFilter.addEventListener("click", function () {
        State.conversationMeaningfulOnly = !State.conversationMeaningfulOnly;
        updateConversationFilterButton();
        renderWorkflowConversationGuarded(State.conversationItems || []);
      });
    }
    const chatterTabs = q("chatterTabs");
    if (chatterTabs) {
      chatterTabs.addEventListener("click", function (ev) {
        const t = ev.target && ev.target.closest ? ev.target.closest("button[data-role]") : null;
        if (!t) return;
        const role = String(t.getAttribute("data-role") || "all").trim().toLowerCase();
        State.chatterFilterRole = role || "all";
        clearUnreadForCurrentTab();
        renderChatterTabs();
        renderChatterGuarded(State.chatterRowsByRole || {});
      });
    }

    document.addEventListener("selectionchange", function () {
      flushDeferredChatterIfPossible();
      flushDeferredConversationIfPossible();
    });
    const chatterBox = q("agentChatter");
    if (chatterBox) {
      chatterBox.addEventListener("mouseup", flushDeferredChatterIfPossible);
      chatterBox.addEventListener("touchend", flushDeferredChatterIfPossible, { passive: true });
      chatterBox.addEventListener("keyup", flushDeferredChatterIfPossible);
    }
    const workflowConversation = q("workflowConversation");
    if (workflowConversation) {
      const updateConversationFollow = function () {
        const remaining = workflowConversation.scrollHeight - (workflowConversation.scrollTop + workflowConversation.clientHeight);
        State.conversationFollowBottom = remaining <= 24;
      };
      workflowConversation.addEventListener("scroll", updateConversationFollow, { passive: true });
      workflowConversation.addEventListener("mouseup", flushDeferredConversationIfPossible);
      workflowConversation.addEventListener("touchend", flushDeferredConversationIfPossible, { passive: true });
      workflowConversation.addEventListener("keyup", flushDeferredConversationIfPossible);
    }

    window.addEventListener("beforeunload", function () { clearPollingTimer(); clearUiTickTimer(); });
    window.addEventListener("pagehide", function () { clearPollingTimer(); clearUiTickTimer(); });
  }

  async function init() {
    bindUi();

    const parsed = parseOrchestrationQuery();
    if (parsed.workflowId) State.workflowId = parsed.workflowId;
    if (parsed.bindingType) State.bindingType = parsed.bindingType;
    if (parsed.bindingId) State.bindingId = parsed.bindingId;

    await loadPisList();
    restoreExtraInfoForUid(State.selectedUid);
    if (State.selectedUid) {
      await onSelectPis();
    } else {
      renderPisPreview();
    }

    if (!State.workflowId) {
      try {
        await resolveLatestWorkflow();
      } catch (_e) {
        /* best effort */
      }
    }

    renderStatusLines();
    await refreshAll({ forceChatter: true });
    startPolling();
    startUiTickTimer();
  }

  window.__EIEL_AGENTS_TEST__ = {
    setStateForTest: function (patch) {
      const p = patch && typeof patch === "object" ? patch : {};
      Object.keys(p).forEach(function (k) {
        State[k] = p[k];
      });
    },
    parseOrchestrationQuery: parseOrchestrationQuery,
    orchestrationViewFromState: orchestrationViewFromState,
    orchestrationPayloadFromUi: orchestrationPayloadFromUi,
    workflowAssignmentKey: workflowAssignmentKey,
    currentAssignmentKey: currentAssignmentKey,
    assignedWorkflowKeyFromState: assignedWorkflowKeyFromState,
    hasAssignmentDrift: hasAssignmentDrift,
    hydrateFromOrchestrationState: hydrateFromOrchestrationState,
    isNoPisSelection: isNoPisSelection,
    preferredPisSelection: preferredPisSelection,
    roleElapsedDisplayMap: roleElapsedDisplayMap,
    runSingleFlight: runSingleFlight,
    notePollingError: notePollingError,
    resetPollingBackoff: resetPollingBackoff,
    pollingState: pollingState,
    isPollingCooldownActive: isPollingCooldownActive,
    isWorkerLimitError: isWorkerLimitError,
    shouldRefreshChatterForTick: shouldRefreshChatterForTick,
    shouldAutoRefreshChatter: shouldAutoRefreshChatter,
    basePollDelayMsForStatus: basePollDelayMsForStatus,
    selectChatterRolesForRefresh: selectChatterRolesForRefresh,
    chatterRowsForDisplay: chatterRowsForDisplay,
    conversationItemsForDisplay: conversationItemsForDisplay,
    CHATTER_POLL_EVERY_N: CHATTER_POLL_EVERY_N,
    REFRESH_MS: REFRESH_MS,
    IDLE_REFRESH_MS: IDLE_REFRESH_MS,
  };

  document.addEventListener("DOMContentLoaded", function () {
    init().catch(function (e) {
      window.alert("Orchestrator UI init failed: " + String((e && e.message) || e || "error"));
    });
  });
})();
