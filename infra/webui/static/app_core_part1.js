window.__EIEL_APP_CORE_PART1_LOADED__ = true;
window.__EIEL_APP_CORE_JS_LOADED__ = true;

/* infra/webui/static/app_core.js
 * EIEL Phase 5d Tier-1 Web UI
 *
 * Frontend goals:
 * - boring, robust, vanilla JS
 * - read-only on determinism-critical artifacts
 * - all writes are UI-only (/ui) or inbox submissions (/inbox) via server
 *
 * NOTE: This file must never crash on missing DOM nodes.
 * (Because browsers are great at failing in creative ways.)
 *
 * Last modified: 2026-03-05 Europe/Vienna
 */

"use strict";

// Boot marker: used by index.html to detect broken /static/app_core.js loads.
// If /static/app_core.js 404s (or is cached incorrectly), the UI would otherwise look "empty".
// Marker moved to the first line (before any parsing/execution surprises).
// Keep legacy marker assignment for compatibility.
try{ window.__EIEL_APP_CORE_JS_LOADED__ = true; }catch(_e){}

// -----------------------------
// Shared helpers
// -----------------------------

const __shared = (window.EIELShared && typeof window.EIELShared === "object") ? window.EIELShared : {};
const q = __shared.q || function(sel, root){ return (root || document).querySelector(sel); };
const qa = __shared.qa || function(sel, root){ return Array.from((root || document).querySelectorAll(sel)); };
const safeOn = __shared.safeOn || function(el, evt, fn){ if(el) el.addEventListener(evt, fn); };
const escapeHtml = __shared.escapeHtml || function(s){ return String(s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); };
const badgeForStatus = __shared.badgeForStatus || function(status){ return `<span class="badge badge-muted">${escapeHtml(String(status || ""))}</span>`; };
const badgeForDeterminism = __shared.badgeForDeterminism || function(det){ return `<span class="badge badge-muted">${escapeHtml(String(det || ""))}</span>`; };
const badgeForStrict = __shared.badgeForStrict || function(strict){ return strict ? '<span class="badge badge-accent">STRICT</span>' : '<span class="badge badge-muted">NON-STRICT</span>'; };
const showGlobalError = __shared.showGlobalError || function(title, msg){ try{ console.error(String(title || "Error"), String(msg || "")); }catch(_e){} };
const clearGlobalError = __shared.clearGlobalError || function(){};
const showToast = __shared.showToast || function(title, body){ try{ console.log(String(title || ""), String(body || "")); }catch(_e){} };
const toast = __shared.toast || function(title, body, ms){ showToast(title, body, ms); };
const setHealth = __shared.setHealth || function(_ok){};
const openModal = __shared.openModal || function(_title, _bodyHtml, _onOpen){};
const closeModal = __shared.closeModal || function(){};
const fetchText = __shared.fetchText || (async function(url, opts){ const r = await fetch(url, opts); return await r.text(); });
const fetchJson = __shared.fetchJson || (async function(url, opts){ return JSON.parse(await fetchText(url, opts)); });
const postJson = __shared.postJson || (async function(url, obj){ return await fetchJson(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(obj || {})}); });

const __utils = (window.EIELUtils && typeof window.EIELUtils === "object") ? window.EIELUtils : {};
const copyTextToClipboard = __utils.copyTextToClipboard || (async function(txt){
  const t = String(txt ?? "");
  try{
    if(globalThis.navigator && navigator.clipboard && typeof navigator.clipboard.writeText === "function"){
      await navigator.clipboard.writeText(t);
      return true;
    }
  }catch(_e){}
  try{
    const ta = document.createElement("textarea");
    ta.value = t;
    ta.setAttribute("readonly", "readonly");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    ta.style.top = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    return true;
  }catch(_e){}
  return false;
});
const shortId = __utils.shortId || function(s, max){ const t = String(s || ""); const m = max || 26; return t.length <= m ? t : (t.slice(0, m - 1) + "…"); };
const displayId = __utils.displayId || function(obj, opts){ const fallback = String(opts?.fallback || ""); return String(obj?.display_id || fallback || obj?.run_key || obj?.job_id || obj?.name || "item"); };
const fmtNum = __utils.fmtNum || function(x, digits){ if(x === null || x === undefined || x === "") return ""; const n = Number(x); if(!Number.isFinite(n)) return ""; const d = (digits === undefined) ? 3 : digits; return n.toFixed(d).replace(/\.0+$/,"").replace(/(\.[0-9]*?)0+$/,"$1"); };
const fmtGen = __utils.fmtGen || function(x){ const n = parseInt(String(x ?? ""), 10); return (Number.isFinite(n) && n >= 0) ? String(n) : ""; };
const csvListToArray = __utils.csvListToArray || function(s){ const raw = String(s || "").trim(); return raw ? raw.split(",").map(x=>x.trim()).filter(Boolean) : []; };
const maybeInt = __utils.maybeInt || function(s){ const t = String(s ?? "").trim(); if(!t) return null; const n = parseInt(t, 10); return Number.isFinite(n) ? n : null; };
const genUuid = __utils.genUuid || function(){ try{ if(globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") return globalThis.crypto.randomUUID(); }catch(_e){} const r = () => Math.floor(Math.random()*0xffffffff).toString(16).padStart(8,"0"); return `${r()}-${r().slice(0,4)}-${r().slice(0,4)}-${r().slice(0,4)}-${r()}${r().slice(0,4)}`; };
const badge = __utils.badge ? ((text, cls, ttKey)=>__utils.badge(text, cls, ttKey, escapeHtml)) : function(text, cls, ttKey){ const tt = String(ttKey||"").trim(); const ttAttr = tt ? ` data-tt="${escapeHtml(tt)}" tabindex="0"` : ""; return `<span class="badge ${cls || ""}"${ttAttr}>${escapeHtml(text)}</span>`; };
const chip = __utils.chip ? ((text, ttKey)=>__utils.chip(text, ttKey, escapeHtml)) : function(text, ttKey){ const tt = String(ttKey||"").trim(); const ttAttr = tt ? ` data-tt="${escapeHtml(tt)}" tabindex="0"` : ""; return `<span class="chip"${ttAttr}>${escapeHtml(text)}</span>`; };
const statusBadgeClass = __utils.statusBadgeClass || function(status){ const s = String(status || "").toUpperCase(); if(s === "RUNNING") return "b-running"; if(s === "PLANNED") return "b-planned"; if(s === "COMPLETE") return "b-complete"; if(s === "FAILED") return "b-failed"; if(s === "STALLED") return "b-stalled"; if(s === "INCOMPLETE") return "b-incomplete"; if(s === "POSSIBLY_CORRUPT") return "b-corrupt"; return "b-unknown"; };
const downloadTextFile = __utils.downloadTextFile ? ((filename, text)=>__utils.downloadTextFile(filename, text, showToast)) : function(filename, text){ try{ const blob = new Blob([String(text||"")], {type:"text/plain;charset=utf-8"}); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = String(filename||"download.txt"); document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>{ try{ URL.revokeObjectURL(url); }catch(_e){} }, 5000);}catch(e){ showToast("Download failed", String(e?.message||e)); } };
const __api = (window.EIELApiFactory && typeof window.EIELApiFactory.create === "function")
  ? window.EIELApiFactory.create({
      fetchJson: fetchJson,
      postJson: postJson,
      encRunKey: function(v){ return encodeURIComponent(String(v || "")); },
    })
  : {};
function _missingApi(name){
  return async function(){
    throw new Error("Missing API wrapper: " + String(name || "unknown"));
  };
}

// -----------------------------
// -----------------------------

async function apiSystemProcesses(includeAll){
  return await ((__api.apiSystemProcesses || _missingApi("apiSystemProcesses"))(includeAll));
}
async function apiRunFileTail(runKey, which, maxBytes){
  return await ((__api.apiRunFileTail || _missingApi("apiRunFileTail"))(runKey, which, maxBytes));
}
async function apiSystemTerminateProcess(pid){
  return await ((__api.apiSystemTerminateProcess || _missingApi("apiSystemTerminateProcess"))(pid));
}
async function apiSystemKillProcess(pid){
  return await ((__api.apiSystemKillProcess || _missingApi("apiSystemKillProcess"))(pid));
}

function _parseIntStrict(val, fallback){
  const n = parseInt(String(val ?? "").trim(), 10);
  return Number.isFinite(n) ? n : fallback;
}

function _parseFloatStrict(val, fallback){
  const n = Number.parseFloat(String(val ?? "").trim());
  return Number.isFinite(n) ? n : fallback;
}

function _parseKnobValue(raw){
  const s = String(raw ?? "").trim();
  if(!s) return s;
  const lo = s.toLowerCase();
  if(lo === "true") return true;
  if(lo === "false") return false;
  const n = Number(s);
  if(Number.isFinite(n)) return n;
  return s;
}

function derivePressureAutopilotActiveState(){
  const keys = [
    "pressure_autopilot_enable",
    "pressure_autopilot_tick_interval_gen",
    "pressure_autopilot_profile",
    "pressure_autopilot_delta_up",
    "pressure_autopilot_delta_down",
    "pressure_autopilot_target_survival",
    "pressure_autopilot_survival_ceiling",
  ];
  const pathToKey = new Map(keys.map(k => [`sim_cfg.${k}`, k]));

  const defaults = {
    pressure_autopilot_enable: false,
    pressure_autopilot_tick_interval_gen: 500,
    pressure_autopilot_profile: "balanced",
    pressure_autopilot_delta_up: 0.05,
    pressure_autopilot_delta_down: 0.10,
    pressure_autopilot_target_survival: 0.55,
    pressure_autopilot_survival_ceiling: 0.80,
  };

  const values = { ...defaults };
  let usedKnobs = false;
  let usedApplied = false;

  const knobs = Array.isArray(State?.runDetail?.knobs) ? State.runDetail.knobs : [];
  for(const r of knobs){
    const section = String(r?.section || "").trim().toLowerCase();
    const key = String(r?.key || "").trim();
    if(section !== "sim") continue;
    if(!keys.includes(key)) continue;
    values[key] = _parseKnobValue(r?.value);
    usedKnobs = true;
  }

  const appliedPayload = State?.runDetail?.appliedCommands;
  const itemsRaw = Array.isArray(appliedPayload?.items) ? appliedPayload.items : [];
  const items = itemsRaw.slice().sort((a,b)=>{
    const ga = Number(a?.applied_generation ?? a?.generation_applied ?? a?.target_generation ?? -1);
    const gb = Number(b?.applied_generation ?? b?.generation_applied ?? b?.target_generation ?? -1);
    return ga - gb;
  });
  for(const it of items){
    const st = String(it?.ack_status || "").toUpperCase();
    if(st && st !== "APPLIED" && st !== "ACCEPTED") continue;
    const payload = (it?.command_payload && typeof it.command_payload === "object")
      ? it.command_payload
      : ((it?.payload && typeof it.payload === "object") ? it.payload : it);
    const updates = Array.isArray(payload?.updates) ? payload.updates : [];
    for(const u of updates){
      const path = String(u?.path || "");
      const key = pathToKey.get(path);
      if(!key) continue;
      values[key] = u?.value;
      usedApplied = true;
    }
  }

  let source = "defaults";
  if(usedKnobs && usedApplied) source = "run_config + applied commands";
  else if(usedApplied) source = "applied commands";
  else if(usedKnobs) source = "run_config";

  return { values, source };
}

function buildPressureAutopilotPayload(){
  const enable = !!State.runDetail.pressureAutopilotEnable;
  // Default target gen should be near-future generation, not 1. Using 1 causes
  // immediate processing/rejection on any non-trivial run and makes the UI
  // look broken.
  const fallbackGen = (()=>{
    const cg = Number(State.runDetail?.meta?.run?.current_generation);
    return (Number.isFinite(cg) && cg >= 0) ? (cg + 10) : 10;
  })();
  const targetGen = _parseIntStrict(State.runDetail.pressureAutopilotTargetGen, fallbackGen);
  const interval = _parseIntStrict(State.runDetail.pressureAutopilotTickInterval, 200);
  const profile = String(State.runDetail.pressureAutopilotProfile || "balanced").trim().toLowerCase();
  const deltaUp = _parseFloatStrict(State.runDetail.pressureAutopilotDeltaUp, 0.05);
  const deltaDown = _parseFloatStrict(State.runDetail.pressureAutopilotDeltaDown, 0.10);
  const targetSurvival = _parseFloatStrict(State.runDetail.pressureAutopilotTargetSurvival, 0.55);
  const survivalCeiling = _parseFloatStrict(State.runDetail.pressureAutopilotSurvivalCeiling, 0.80);

  if(!Number.isFinite(targetGen) || targetGen < 0){
    throw new Error("Apply at gen must be >= 0");
  }
  if(enable){
    if(!Number.isFinite(interval) || interval < 1){
      throw new Error("Tick interval must be >= 1");
    }
    if(!["balanced","cycle","scarcity","cost","shock"].includes(profile)){
      throw new Error("Profile must be one of: balanced, cycle, scarcity, cost, shock");
    }
    if(!Number.isFinite(deltaUp) || deltaUp <= 0.0){
      throw new Error("delta_up must be > 0");
    }
    if(!Number.isFinite(deltaDown) || deltaDown <= 0.0){
      throw new Error("delta_down must be > 0");
    }
    if(!Number.isFinite(targetSurvival) || targetSurvival <= 0.0 || targetSurvival >= 1.0){
      throw new Error("target_survival must be in (0,1)");
    }
    if(!Number.isFinite(survivalCeiling) || survivalCeiling <= 0.0 || survivalCeiling >= 1.0){
      throw new Error("survival_ceiling must be in (0,1)");
    }
    return {
      schema_version: "cmd_v1",
      command_type: "PressureUpdateCommand",
      target_generation: targetGen,
      updates: [
        { path: "sim_cfg.pressure_autopilot_enable", value: true },
        { path: "sim_cfg.pressure_autopilot_tick_interval_gen", value: interval },
        { path: "sim_cfg.pressure_autopilot_profile", value: profile },
        { path: "sim_cfg.pressure_autopilot_delta_up", value: deltaUp },
        { path: "sim_cfg.pressure_autopilot_delta_down", value: deltaDown },
        { path: "sim_cfg.pressure_autopilot_target_survival", value: targetSurvival },
        { path: "sim_cfg.pressure_autopilot_survival_ceiling", value: survivalCeiling },
      ],
    };
  }

  return {
    schema_version: "cmd_v1",
    command_type: "PressureUpdateCommand",
    target_generation: targetGen,
    updates: [
      { path: "sim_cfg.pressure_autopilot_enable", value: false },
    ],
  };
}

// -----------------------------
// -----------------------------

// Minimal self-tests (PIS-7J2Q AC6): runs in-browser and reports to console.
// This is not a full test suite, but it does catch regressions in the two
// string-manipulation contracts we care about: env prefix + wrapper_root.

// -----------------------------
// -----------------------------

function encRunKey(runKey){
  return encodeURIComponent(String(runKey || ""));
}

// -----------------------------
// Formatting
// -----------------------------

function detBadgeClass(det){
  const d = String(det || "").toUpperCase();
  if(d === "PASS") return "b-pass";
  if(d === "FAIL") return "b-fail";
  return "b-unknown";
}

// -----------------------------
// Global app state
// -----------------------------

const State = {
  runs: [],            // full runs index (include archived)
  changesets: [],
  lastRunsUtc: null,
  lastChangeSetsUtc: null,
  planChangeSets: new Set(),

  // Operational plane runtime
  systemRuns: null,
  lastSystemRunsUtc: null,
  runControl: {
    harnesses: [],
    lastHarnessesUtc: null,
    selectedHarnessModule: "",
    recentJobs: [],
    activeJobId: "",
    launchStatus: null,
    launchLogText: "",
    lastLaunchUtc: null,
    processes: [],
    lastProcessesUtc: null,
    includeAllProcesses: false,
    processBusyPid: 0,
    processBusyAction: "",
  },
  pressureSearch: {
    campaigns: [],
    activeCampaignId: "pressure_search_001",
    activeCampaign: null,
    lastLoadedUtc: null,
    busyAction: "",
    noteDraft: "",
    durationDays: 28,
    maxActiveChildren: 1,
    pollSeconds: 60,
    lastLaunchStatus: null,
  },

  // Patch backlog
  patchBacklog: null,
  lastPatchBacklogUtc: null,
  backlogDraft: {
    md: "",
    lastSavedUtc: null,
  },
  patchBacklogSig: null,
  patchBacklogRenderedSig: null,
  backlogUi: {
    expandedIds: new Set(),
    scrollTops: {},
  },
  // Backlog multi-select (desktop)
  backlogSelections: new Set(),
  // Change Ledger (governance/ledger)
  backlogMode: "ledger", // "ledger" (default) | "legacy"
  ledger: {
    items: null,
    lastItemsUtc: null,
    itemsSig: null,
    itemsRenderedSig: null,
    activeUid: null,
    activeItem: null,       // full item payload from /api/ledger/item/<uid>
    activeLoadedUtc: null,
    editor: {
      pisDraft: "",
      metaDraft: null,
      dirtyPis: false,
      dirtyMeta: false,
    },
    ui: {
      search: "",
      status: "ALL",
      tag: "",
      showDeprecated: false,
      showReopened: true,
      listScrollTop: 0,
    }
  },

  // View and filters
  view: "backlog",      // backlog|agents|mobile|schedules|mobile-update
  activeRunKey: null,

  overview: {
    showArchived: false,
    search: "",
    onlyDetPass: false,
    filtersOpen: false,
    runControlOpen: true,
    launchLogOpen: false,
    processesOpen: false,
    systemRunsOpen: false,
    selectedRunOpen: true,
    lineageOpen: {},
    processTreeOpen: {},
    selectedRealRunKey: "",
    selectedRunStdoutKey: "",
    selectedRunStdoutText: "",
    selectedRunStdoutLoadedUtc: null,
    selectedRunStdoutLoading: false,
    selectedRunStdoutScrollTop: 0,
    selectedRunStdoutStickToBottom: true,
    launchLogScrollTop: 0,
  },
  matrix: {
    showArchived: false,
    search: "",
    status: "ALL",
    determinism: "ALL",
    strict: "ALL",
    tag: "",
    onlyDetPass: false,
    sortKey: "run",
    sortDir: 1,
  },
  changesetsView: {
    search: "",
  },
  runDetail: {
    range: 1000,
    overlayParent: true,
    overlayStatus: null,
    smoothEpi: true,
    showAdvanced: false,
    pendingUuids: new Set(),
    lastLoadedRunKey: null,
    // Incremental EPI/AV tail state (client-side). Server already tails safely.
    epiavLastGen: -1,
    epiavSource: null,
    parentEpiavLastGen: -1,
    parentEpiavSource: null,
    lastRange: 1000,
    lastParentRunKey: null,
    meta: null,
    epiav: null,
    parentEpiav: null,
    checkpoints: null,
    appliedCommands: null,
    appliedLimit: 200,
    appliedAfterGen: -1,
    appliedBeforeGen: null,
    appliedSearch: "",
    appliedStatus: "ALL",
    appliedWindow: null,
    inboxQueue: null,
    notes: null,
    // Autopilot (optional): autopilot/pressure_autopilot_decisions.jsonl
    autopilotActions: null,
    autopilotLimit: 200,
    autopilotAfterGen: -1,
    autopilotBeforeGen: null,
    autopilotSearch: "",
    autopilotWindow: null,
    // Metrics (optional): metrics.csv / viz/metrics_summary.csv
    metricsSchema: null,
    metricName: "alive",
    metricLastGen: -1,
    metricSeries: null,
    metricSource: null,
    lastMetricName: null,
    // Knobs (optional): run_config.csv
    knobs: null,
    knobsSource: null,
    // Quick action: PressureUpdateCommand helper UI state
    // NOTE: target gen default is set after meta loads (current_gen + 10).
    // Keep null here so we can distinguish "never touched" from a real user choice.
    pressureAutopilotEnable: true,
    pressureAutopilotTargetGen: null,
    pressureAutopilotTargetGenTouched: false,
    pressureAutopilotTickInterval: 200,
    pressureAutopilotProfile: "balanced",
    pressureAutopilotDeltaUp: 0.05,
    pressureAutopilotDeltaDown: 0.10,
    pressureAutopilotTargetSurvival: 0.55,
    pressureAutopilotSurvivalCeiling: 0.80,
    lastError: null,
    // Timeline (control + pressure) summary
    timelineBins: 200,
    timelineSummary: null,
    timelineLastSig: null,
    timelineSelection: null,
  },

  // Polling bookkeeping
  tick: 0,
  inFlight: false,
  refreshToken: 0,
};

// -----------------------------
// API wrappers
// -----------------------------

async function apiHealth(){
  return await ((__api.apiHealth || _missingApi("apiHealth"))());
}

async function apiRuns(){
  return await ((__api.apiRuns || _missingApi("apiRuns"))());
}

async function apiChangeSets(){
  return await ((__api.apiChangeSets || _missingApi("apiChangeSets"))());
}

async function apiSystemRuns(){
  return await ((__api.apiSystemRuns || _missingApi("apiSystemRuns"))());
}

async function apiPatchBacklog(){
  return await ((__api.apiPatchBacklog || _missingApi("apiPatchBacklog"))());
}

async function apiPatchBacklogSave(doc){
  return await ((__api.apiPatchBacklogSave || _missingApi("apiPatchBacklogSave"))(doc));
}


async function apiLedgerItems(limit){
  return await ((__api.apiLedgerItems || _missingApi("apiLedgerItems"))(limit));
}

async function apiLedgerItem(uid){
  return await ((__api.apiLedgerItem || _missingApi("apiLedgerItem"))(uid));
}

async function apiLedgerCreate(payload){
  return await ((__api.apiLedgerCreate || _missingApi("apiLedgerCreate"))(payload));
}

async function apiLedgerStatus(uid, status, note){
  return await ((__api.apiLedgerStatus || _missingApi("apiLedgerStatus"))(uid, status, note));
}

async function apiLedgerAdvance(uid, payload){
  return await ((__api.apiLedgerAdvance || _missingApi("apiLedgerAdvance"))(uid, payload));
}

async function apiLedgerUpdateMeta(uid, meta){
  return await ((__api.apiLedgerUpdateMeta || _missingApi("apiLedgerUpdateMeta"))(uid, meta));
}

async function apiLedgerUpdatePis(uid, pis_markdown){
  return await ((__api.apiLedgerUpdatePis || _missingApi("apiLedgerUpdatePis"))(uid, pis_markdown));
}

async function apiLedgerCreateRevision(uid, payload){
  return await ((__api.apiLedgerCreateRevision || _missingApi("apiLedgerCreateRevision"))(uid, payload));
}

async function apiLedgerCommits(uid, limit){
  return await ((__api.apiLedgerCommits || _missingApi("apiLedgerCommits"))(uid, limit));
}

async function apiLedgerActionRun(payload){
  return await ((__api.apiLedgerActionRun || _missingApi("apiLedgerActionRun"))(payload));
}

async function apiLedgerActionStatus(uid, jobId, limit){
  return await ((__api.apiLedgerActionStatus || _missingApi("apiLedgerActionStatus"))(uid, jobId, limit));
}

async function apiLedgerActionLog(uid, jobId, lines){
  return await ((__api.apiLedgerActionLog || _missingApi("apiLedgerActionLog"))(uid, jobId, lines));
}

async function apiLedgerActionCancel(uid, jobId){
  return await ((__api.apiLedgerActionCancel || _missingApi("apiLedgerActionCancel"))(uid, jobId));
}

async function apiLedgerActionForceCancel(uid, jobId){
  return await ((__api.apiLedgerActionForceCancel || _missingApi("apiLedgerActionForceCancel"))(uid, jobId));
}

async function apiLedgerMarkVerified(uid){
  return await ((__api.apiLedgerMarkVerified || _missingApi("apiLedgerMarkVerified"))(uid));
}

async function apiAgentsSendTo(payload){
  return await ((__api.apiAgentsSendTo || _missingApi("apiAgentsSendTo"))(payload));
}

async function apiAgentsHandoff(payload){
  return await ((__api.apiAgentsHandoff || _missingApi("apiAgentsHandoff"))(payload));
}

async function apiAgentsSession(role, binding){
  return await ((__api.apiAgentsSession || _missingApi("apiAgentsSession"))(role, binding));
}

async function apiGitStatus(){
  return await ((__api.apiGitStatus || _missingApi("apiGitStatus"))());
}

async function apiBacklogGptSubmit(payload){
  return await ((__api.apiBacklogGptSubmit || _missingApi("apiBacklogGptSubmit"))(payload));
}

async function apiRunMeta(runKey){
  return await ((__api.apiRunMeta || _missingApi("apiRunMeta"))(runKey));
}

async function apiRunEpiAv(runKey, afterGen, limit){
  return await ((__api.apiRunEpiAv || _missingApi("apiRunEpiAv"))(runKey, afterGen, limit));
}

async function apiRunCheckpoints(runKey){
  return await ((__api.apiRunCheckpoints || _missingApi("apiRunCheckpoints"))(runKey));
}

async function apiRunAppliedCommands(runKey, opts){
  return await ((__api.apiRunAppliedCommands || _missingApi("apiRunAppliedCommands"))(runKey, opts));
}

async function apiRunAutopilotActions(runKey, opts){
  return await ((__api.apiRunAutopilotActions || _missingApi("apiRunAutopilotActions"))(runKey, opts));
}


async function apiRunTimelineSummary(runKey, bins){
  return await ((__api.apiRunTimelineSummary || _missingApi("apiRunTimelineSummary"))(runKey, bins));
}

async function apiRunTimelineRange(runKey, genMin, genMax, kind){
  return await ((__api.apiRunTimelineRange || _missingApi("apiRunTimelineRange"))(runKey, genMin, genMax, kind));
}


async function apiRunInboxQueue(runKey, limit){
  return await ((__api.apiRunInboxQueue || _missingApi("apiRunInboxQueue"))(runKey, limit));
}

async function apiRunMetricsSchema(runKey){
  return await ((__api.apiRunMetricsSchema || _missingApi("apiRunMetricsSchema"))(runKey));
}

async function apiRunMetricSeries(runKey, metric, afterGen, limit){
  return await ((__api.apiRunMetricSeries || _missingApi("apiRunMetricSeries"))(runKey, metric, afterGen, limit));
}

async function apiRunTraits(runKey){
  return await ((__api.apiRunTraits || _missingApi("apiRunTraits"))(runKey));
}

async function apiRunKnobs(runKey){
  return await ((__api.apiRunKnobs || _missingApi("apiRunKnobs"))(runKey));
}
async function apiRunNotes(runKey){
  return await ((__api.apiRunNotes || _missingApi("apiRunNotes"))(runKey));
}

async function apiWriteNotes(runKey, text){
  return await ((__api.apiWriteNotes || _missingApi("apiWriteNotes"))(runKey, text));
}

async function apiArchive(runKey, archived){
  return await ((__api.apiArchive || _missingApi("apiArchive"))(runKey, archived));
}

// Full run archive zip (stored under runtime/artifacts/archives).
async function apiArchiveRun(runKey, opts){
  return await ((__api.apiArchiveRun || _missingApi("apiArchiveRun"))(runKey, opts));
}
async function apiUnarchiveRun(runKey){
  return await ((__api.apiUnarchiveRun || _missingApi("apiUnarchiveRun"))(runKey));
}

async function apiWriteUiMeta(runKey, patch){
  return await ((__api.apiWriteUiMeta || _missingApi("apiWriteUiMeta"))(runKey, patch));
}

async function apiExport(runKey, includeCheckpoints){
  return await ((__api.apiExport || _missingApi("apiExport"))(runKey, includeCheckpoints));
}

// Run analysis snapshot export (UI-owned export directory).
// Supports optional name + include_checkpoints flag.
async function apiExportSnapshot(runKey, name, includeCheckpoints){
  return await ((__api.apiExportSnapshot || _missingApi("apiExportSnapshot"))(runKey, name, includeCheckpoints));
}

async function apiSubmitCommand(runKey, payload){
  return await ((__api.apiSubmitCommand || _missingApi("apiSubmitCommand"))(runKey, payload));
}

async function apiPlanBranch(payload){
  return await ((__api.apiPlanBranch || _missingApi("apiPlanBranch"))(payload));
}
