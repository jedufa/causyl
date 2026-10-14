window.__EIEL_APP_API_JS_LOADED__ = true;

/* infra/webui/static/app_api.js
 * EIEL Web UI API wrappers extracted from app.js.
 * Last modified: 2026-03-05 Europe/Vienna
 */

"use strict";

(function () {
  function _fallbackEncoder(v){
    return encodeURIComponent(String(v || ""));
  }

  function create(deps){
    const d = deps || {};
    const fetchJson = d.fetchJson;
    const postJson = d.postJson;
    const encRunKey = (typeof d.encRunKey === "function") ? d.encRunKey : _fallbackEncoder;
    if(typeof fetchJson !== "function" || typeof postJson !== "function"){
      throw new Error("EIELApiFactory.create requires fetchJson and postJson");
    }

    return {
















      async apiHealth(){ return await fetchJson("/api/health"); },
      async apiRuns(){
        const data = await fetchJson("/api/runs?include_archived=1");
        if(Array.isArray(data)) return data;
        if(data && Array.isArray(data.runs)) return data.runs;
        return [];
      },




      async apiChangeSets(){
        const data = await fetchJson("/api/changesets");
        if(Array.isArray(data)) return data;
        if(data && Array.isArray(data.changesets)) return data.changesets;
        return [];
      },
      async apiSystemRuns(){ return await fetchJson("/api/system/runs"); },
      async apiSystemProcesses(includeAll){
        const q = new URLSearchParams();
        if(includeAll) q.set("include_all", "1");
        const qs = q.toString();
        return await fetchJson("/api/system/processes" + (qs ? ("?" + qs) : ""));
      },
      async apiRunFileTail(runKey, which, maxBytes){
        const q = new URLSearchParams();
        q.set("key", String(runKey || "").trim());
        q.set("which", String(which || "").trim());
        if(maxBytes != null) q.set("max_bytes", String(maxBytes));
        return await fetchJson("/api/run/file_tail?" + q.toString());
      },
      async apiSystemTerminateProcess(pid){ return await postJson("/api/system/processes/terminate", {pid}); },
      async apiSystemKillProcess(pid){ return await postJson("/api/system/processes/kill", {pid}); },
      async apiPatchBacklog(){ return await fetchJson("/api/patch_backlog"); },
      async apiPatchBacklogSave(doc){ return await postJson("/api/patch_backlog", doc || {}); },
      async apiLedgerItems(limit){
        const lim = (limit!=null) ? ("?limit=" + encodeURIComponent(String(limit))) : "";
        return await fetchJson("/api/ledger/items" + lim);
      },
      async apiLedgerItem(uid){ return await fetchJson("/api/ledger/item/" + encodeURIComponent(String(uid||"").trim())); },
      async apiLedgerCreate(payload){ return await postJson("/api/ledger/item/create", payload || {}); },
      async apiLedgerStatus(uid, status, note){
        return await postJson("/api/ledger/item/" + encodeURIComponent(String(uid||"").trim()) + "/status", {status, note});
      },
      async apiLedgerAdvance(uid, payload){
        return await postJson("/api/ledger/item/" + encodeURIComponent(String(uid||"").trim()) + "/advance", payload || {});
      },
      async apiLedgerUpdateMeta(uid, meta){
        return await postJson("/api/ledger/item/" + encodeURIComponent(String(uid||"").trim()) + "/update_meta", meta || {});
      },
      async apiLedgerUpdatePis(uid, pis_markdown){
        return await postJson("/api/ledger/item/" + encodeURIComponent(String(uid||"").trim()) + "/update_pis", {pis_markdown});
      },
      async apiLedgerCreateRevision(uid, payload){
        return await postJson("/api/ledger/item/" + encodeURIComponent(String(uid||"").trim()) + "/create_revision", payload || {});
      },
      async apiLedgerCommits(uid, limit){
        const lim = (limit!=null) ? ("?limit=" + encodeURIComponent(String(limit))) : "";
        return await fetchJson("/api/ledger/item/" + encodeURIComponent(String(uid||"").trim()) + "/commits" + lim);
      },
      async apiLedgerActionRun(payload){ return await postJson("/api/ledger/action/run", payload || {}); },
      async apiLedgerActionStatus(uid, jobId, limit){
        const q = new URLSearchParams();
        q.set("uid", String(uid || "").trim());
        if(jobId) q.set("job_id", String(jobId));
        if(limit != null) q.set("limit", String(limit));
        return await fetchJson("/api/ledger/action/status?" + q.toString());
      },
      async apiLedgerActionLog(uid, jobId, lines){
        const q = new URLSearchParams();
        q.set("uid", String(uid || "").trim());
        q.set("job_id", String(jobId || "").trim());
        if(lines != null) q.set("lines", String(lines));
        return await fetchJson("/api/ledger/action/log?" + q.toString());
      },
      async apiLedgerActionCancel(uid, jobId){ return await postJson("/api/ledger/action/cancel", {uid, job_id: jobId}); },
      async apiLedgerActionForceCancel(uid, jobId){ return await postJson("/api/ledger/action/force_cancel", {uid, job_id: jobId}); },
      async apiLedgerMarkVerified(uid){
        return await postJson("/api/ledger/item/" + encodeURIComponent(String(uid||"").trim()) + "/mark_verified", {});
      },
      async apiAgentsSendTo(payload){ return await postJson("/api/agents/send_to", payload || {}); },
      async apiAgentsHandoff(payload){ return await postJson("/api/agents/handoff", payload || {}); },
      async apiAgentsSession(role, binding){
        const q = new URLSearchParams();
        q.set("role", String(role || "").trim());
        const b = binding || {};
        if(String(b.type || "") === "ledger_uid") q.set("ledger_uid", String(b.id || ""));
        else q.set("task_id", String(b.id || ""));
        return await fetchJson("/api/agents/session?" + q.toString());
      },
      async apiGitStatus(){ return await fetchJson("/api/admin/git/status"); },
      async apiBacklogGptSubmit(payload){ return await postJson("/api/admin/backlog/gpt_submit", payload || {}); },
      async apiRunMeta(runKey){ return await fetchJson(`/api/run/${encRunKey(runKey)}/meta`); },
      async apiRunEpiAv(runKey, afterGen, limit){
        const ag = (afterGen === undefined || afterGen === null) ? -1 : afterGen;
        const lim = (limit === undefined || limit === null) ? 1000 : limit;
        return await fetchJson(`/api/run/${encRunKey(runKey)}/epiav?after_gen=${encodeURIComponent(String(ag))}&limit=${encodeURIComponent(String(lim))}`);
      },
      async apiRunCheckpoints(runKey){ return await fetchJson(`/api/run/${encRunKey(runKey)}/checkpoints`); },
      async apiRunAppliedCommands(runKey, opts){
        let lim = 200;
        let afterGen = null;
        let beforeGen = null;
        if(typeof opts === "number"){
          lim = opts;
        }else if(opts && typeof opts === "object"){
          lim = (opts.limit === undefined || opts.limit === null) ? 200 : opts.limit;
          afterGen = (opts.afterGen === undefined) ? null : opts.afterGen;
          beforeGen = (opts.beforeGen === undefined) ? null : opts.beforeGen;
        }
        let url = `/api/run/${encRunKey(runKey)}/applied_commands?limit=${encodeURIComponent(String(lim))}`;
        if(afterGen !== null && afterGen !== undefined){
          url += `&after_gen=${encodeURIComponent(String(afterGen))}`;
        }
        if(beforeGen !== null && beforeGen !== undefined){
          url += `&before_gen=${encodeURIComponent(String(beforeGen))}`;
        }
        return await fetchJson(url);
      },
      async apiRunAutopilotActions(runKey, opts){
        let lim = 200;
        let afterGen = null;
        let beforeGen = null;
        if(typeof opts === "number"){
          lim = opts;
        }else if(opts && typeof opts === "object"){
          lim = (opts.limit === undefined || opts.limit === null) ? 200 : opts.limit;
          afterGen = (opts.afterGen === undefined) ? null : opts.afterGen;
          beforeGen = (opts.beforeGen === undefined) ? null : opts.beforeGen;
        }
        let url = `/api/run/${encRunKey(runKey)}/autopilot_actions?limit=${encodeURIComponent(String(lim))}`;
        if(afterGen !== null && afterGen !== undefined){
          url += `&after_gen=${encodeURIComponent(String(afterGen))}`;
        }
        if(beforeGen !== null && beforeGen !== undefined){
          url += `&before_gen=${encodeURIComponent(String(beforeGen))}`;
        }
        return await fetchJson(url);
      },
      async apiRunTimelineSummary(runKey, bins){
        const b = (bins === null || bins === undefined) ? 200 : Number(bins);
        return await fetchJson(`/api/run/${encRunKey(runKey)}/timeline_summary?bins=${encodeURIComponent(String(b))}`);
      },
      async apiRunTimelineRange(runKey, genMin, genMax, kind){
        const t = String(kind || "control");
        return await fetchJson(`/api/run/${encRunKey(runKey)}/timeline_range?gen_min=${encodeURIComponent(String(genMin))}&gen_max=${encodeURIComponent(String(genMax))}&type=${encodeURIComponent(t)}&limit=200`);
      },
      async apiRunInboxQueue(runKey, limit){
        const lim = (limit === undefined || limit === null) ? 200 : limit;
        return await fetchJson(`/api/run/${encRunKey(runKey)}/inbox?limit=${encodeURIComponent(String(lim))}`);
      },
      async apiRunMetricsSchema(runKey){ return await fetchJson(`/api/run/${encRunKey(runKey)}/metrics_schema`); },
      async apiRunMetricSeries(runKey, metric, afterGen, limit){
        const m = String(metric || "");
        const ag = (afterGen === undefined || afterGen === null) ? -1 : afterGen;
        const lim = (limit === undefined || limit === null) ? 1000 : limit;
        return await fetchJson(`/api/run/${encRunKey(runKey)}/metrics_series?metric=${encodeURIComponent(m)}&after_gen=${encodeURIComponent(String(ag))}&limit=${encodeURIComponent(String(lim))}`);
      },
      async apiRunTraits(runKey){ return await fetchJson(`/api/run/${encRunKey(runKey)}/traits`); },
      async apiRunKnobs(runKey){ return await fetchJson(`/api/run/${encRunKey(runKey)}/knobs`); },
      async apiRunNotes(runKey){ return await fetchJson(`/api/run/${encRunKey(runKey)}/notes`); },
      async apiWriteNotes(runKey, text){ return await postJson(`/api/run/${encRunKey(runKey)}/notes`, {text: String(text || "")}); },
      async apiArchive(runKey, archived){
        const action = archived ? "archive" : "unarchive";
        return await postJson(`/api/run/${encRunKey(runKey)}/${action}`, {});
      },
      async apiArchiveRun(runKey, opts){ return await postJson(`/api/run/${encRunKey(runKey)}/archive`, opts || {}); },
      async apiUnarchiveRun(runKey){ return await postJson(`/api/run/${encRunKey(runKey)}/unarchive`, {}); },
      async apiWriteUiMeta(runKey, patch){ return await postJson(`/api/run/${encRunKey(runKey)}/ui_meta`, patch || {}); },
      async apiExport(runKey, includeCheckpoints){
        return await postJson(`/api/run/${encRunKey(runKey)}/export`, {include_checkpoints: !!includeCheckpoints});
      },
      async apiExportSnapshot(runKey, name, includeCheckpoints){
        return await postJson(`/api/run/${encRunKey(runKey)}/export`, {
          include_checkpoints: !!includeCheckpoints,
          name: String(name||"").trim(),
        });
      },
      async apiSubmitCommand(runKey, payload){
        return await postJson(`/api/run/${encRunKey(runKey)}/submit_command`, payload || {});
      },
      async apiPlanBranch(payload){ return await postJson(`/api/branch/plan`, payload || {}); },
    };
  }

  window.EIELApiFactory = { create };
})();
