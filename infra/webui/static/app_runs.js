window.__EIEL_APP_RUNS_JS_LOADED__ = true;

/* infra/webui/static/app_runs.js
 * EIEL Web UI run-index + overview helpers extracted from app.js.
 * Last modified: 2026-03-05 Europe/Vienna
 */

"use strict";

(function () {
  function create(deps){
    const d = deps || {};
    const State = d.State;
    const escapeHtml = d.escapeHtml;
    const shortId = d.shortId;
    const displayId = d.displayId || function(obj, opts){ return String(obj?.display_id || opts?.fallback || obj?.run_key || "run"); };
    const fmtNum = d.fmtNum;
    const fmtGen = d.fmtGen;
    const badge = d.badge;
    const chip = d.chip;
    const statusBadgeClass = d.statusBadgeClass;
    const detBadgeClass = d.detBadgeClass;
    const apiRunStart = d.apiRunStart;
    const apiRunStop = d.apiRunStop;
    const apiRunResume = d.apiRunResume;
    const apiRunTerminateUnmanaged = d.apiRunTerminateUnmanaged;

    function isCompactOverviewMode(){
      try{
        return !!(window && window.matchMedia && window.matchMedia("(max-width: 900px)").matches);
      }catch(_e){
        return false;
      }
    }

    function runKeyOf(r){
      return String(r?.run_key ?? r?.run_id ?? "");
    }

    function findRun(runKey){
      const rk = String(runKey || "");
      return State.runs.find(r => runKeyOf(r) === rk) || null;
    }

    function findParentRunKey(run){
      if(!run) return null;
      const pk = run.parent_run_key;
      if(pk) return String(pk);
      const pb = run?.lineage?.parent_branch_id;
      if(!pb) return null;
      const p = State.runs.find(r => String(r?.lineage?.branch_id || "") === String(pb));
      return p ? runKeyOf(p) : null;
    }

    function computeCheckpointBranchCounts(parentRunKey){
      const out = new Map();
      const pk = String(parentRunKey || "");
      for(const r of State.runs){
        if(String(r?.parent_run_key || "") !== pk) continue;
        const cpid = String(r?.lineage?.parent_checkpoint_id || "");
        if(!cpid) continue;
        out.set(cpid, (out.get(cpid) || 0) + 1);
      }
      return out;
    }

    function filterOverviewRuns(runs){
      const f = State.overview;
      const qtxt = String(f.search || "").toLowerCase().trim();
      return runs.filter(r => {
        if(!f.showArchived && r.archived && String(r.status||"").toUpperCase() !== "RUNNING") return false;
        if(f.onlyDetPass && String(r.determinism || "").toUpperCase() !== "PASS") return false;
        if(qtxt){
          const hay = [runKeyOf(r), r.label, (r.tags||[]).join(",")].join(" ").toLowerCase();
          if(!hay.includes(qtxt)) return false;
        }
        return true;
      });
    }

    function buildTreeOrder(groupRuns){
      const byKey = new Map();
      for(const r of groupRuns){
        byKey.set(runKeyOf(r), r);
      }
      const kids = new Map();
      for(const r of groupRuns){
        const pk = String(r.parent_run_key || "");
        if(pk && byKey.has(pk)){
          if(!kids.has(pk)) kids.set(pk, []);
          kids.get(pk).push(r);
        }
      }
      const roots = groupRuns.filter(r => {
        const pk = String(r.parent_run_key || "");
        return !pk || !byKey.has(pk);
      });
      roots.sort((a,b)=>runKeyOf(a).localeCompare(runKeyOf(b)));

      const out = [];
      function dfs(node, depth){
        out.push({run: node, depth});
        const list = (kids.get(runKeyOf(node)) || []).slice();
        list.sort((a,b)=>runKeyOf(a).localeCompare(runKeyOf(b)));
        for(const c of list){ dfs(c, depth+1); }
      }
      for(const r of roots){ dfs(r, 0); }
      return out;
    }

    function renderTimeline(run, branchPoints){
      const target = Number(run?.target_generation);
      const current = Number(run?.current_generation);
      if(!Number.isFinite(target) || target <= 0){
        return `<div class="timeline" title="target_generation unavailable"><div class="tl-base"></div></div>`;
      }
      const pct = (Number.isFinite(current) && current >= 0) ? Math.max(0, Math.min(1, current/target)) : 0;
      const fillPct = pct * 100;
      const tickLeft = fillPct;

      let dots = `<div class="tl-dot start" style="left:0%" title="gen 0"></div>`;
      dots += `<div class="tl-dot end" style="left:100%" title="gen ${escapeHtml(String(target))}"></div>`;
      const cps = Array.isArray(run?.checkpoints) ? run.checkpoints : [];
      for(const c of cps){
        const g = Number(c?.generation);
        if(!Number.isFinite(g) || g < 0) continue;
        const lp = Math.max(0, Math.min(100, (g/target)*100));
        const title = `ckpt gen ${g}${c?.checkpoint_id ? " :: " + c.checkpoint_id : ""}`;
        dots += `<div class="tl-dot ckpt" style="left:${lp.toFixed(3)}%" title="${escapeHtml(title)}"></div>`;
      }
      const bps = Array.isArray(branchPoints) ? branchPoints : [];
      if(bps.length){
        const byGen = new Map();
        for(const bp of bps){
          const g = Number(bp?.generation);
          if(!Number.isFinite(g) || g < 0) continue;
          const key = String(Math.floor(g));
          if(!byGen.has(key)) byGen.set(key, []);
          byGen.get(key).push(bp);
        }
        for(const [gKey, arr] of byGen.entries()){
          const g = Number(gKey);
          if(!Number.isFinite(g) || g < 0) continue;
          const lp = Math.max(0, Math.min(100, (g/target)*100));
          const title = (arr.length === 1)
            ? `branch @ gen ${g} → ${shortId(String(arr[0]?.child_run_key || ""), 32)}`
            : `branch @ gen ${g} → ${arr.length} children`;
          dots += `<div class="tl-dot branch" style="left:${lp.toFixed(3)}%" title="${escapeHtml(title)}"></div>`;
        }
      }

      return `<div class="timeline" title="${escapeHtml(`gen ${current}/${target}`)}">
        <div class="tl-base"></div>
        <div class="tl-fill" style="width:${fillPct.toFixed(3)}%"></div>
        <div class="tl-tick" style="left:${tickLeft.toFixed(3)}%" title="current gen ${escapeHtml(String(current))}"></div>
        ${dots}
      </div>`;
    }

    function renderRunControls(run, opts){
      opts = opts || {};
      if(String(run?.source_kind || "") === "scheduled_job"){
        const stopProp = opts.stopPropagation ? "event.stopPropagation(); " : "";
        const txt = (!!opts.iconOnly || isCompactOverviewMode()) ? "↗" : "↗ Schedules";
        return `<a class="btn small" title="Open schedules" onclick="${stopProp}" href="#schedules">${txt}</a>`;
      }
      const rk = runKeyOf(run);
      const status = String(run?.status || "").toUpperCase();
      const cur = Number(run?.current_generation);
      const tgt = Number(run?.target_generation);
      const canResume = (Number.isFinite(cur) && cur >= 0 && Number.isFinite(tgt) && tgt >= 0 && cur < tgt);
      const stopProp = opts.stopPropagation ? "event.stopPropagation(); " : "";
      const iconOnly = !!opts.iconOnly || isCompactOverviewMode();

      if(status === "RUNNING"){
        const txt = iconOnly ? "⏹" : "⏹ Stop";
        return `<button class="btn small danger" title="Stop" onclick="${stopProp}apiRunStop('${escapeHtml(rk)}')">${txt}</button>`;
      }
      if(status === "PLANNED"){
        const txt = iconOnly ? "▶" : "▶ Start";
        return `<button class="btn small ok" title="Start" onclick="${stopProp}apiRunStart('${escapeHtml(rk)}')">${txt}</button>`;
      }
      if(canResume){
        const txt = iconOnly ? "↻" : "↻ Resume";
        const title = "Resume from latest checkpoint (creates a continuation run)";
        return `<button class="btn small" title="${title}" onclick="${stopProp}apiRunResume('${escapeHtml(rk)}')">${txt}</button>`;
      }
      return "";
    }

    function renderRunRow(run, depth, branchPoints){
      const compact = isCompactOverviewMode();
      const rk = runKeyOf(run);
      const display = displayId(run, {fallback: rk});
      const label = String(run?.label || "");
      const tags = Array.isArray(run?.tags) ? run.tags : [];
      const status = String(run?.status || "UNKNOWN");
      const det = run?.determinism;
      const strict = !!run?.strict_mode;
      const ver = (run?.last_verified_generation === null || run?.last_verified_generation === undefined) ? null : run.last_verified_generation;
      const epi = run?.epi;
      const av = run?.av;
      const statusBadges = [badge(status, statusBadgeClass(status))];
      if(run?.epiav_provisional){
        statusBadges.push(badge("POSSIBLY_CORRUPT", statusBadgeClass("POSSIBLY_CORRUPT")));
      }
      const metaBits = [];
      metaBits.push(badge(det, detBadgeClass(det)));
      metaBits.push(chip(strict ? "STRICT" : "NON-STRICT"));
      if(Number.isFinite(Number(ver)) && Number(ver) >= 0) metaBits.push(chip(`Verified ≤ ${ver}`));
      else metaBits.push(chip("Verified: ?"));
      if(tags && tags.length) metaBits.push(chip(`tags: ${tags.slice(0, compact ? 1 : 3).join(",")}${tags.length>(compact ? 1 : 3)?"…":""}`));
      const epiTxt = (epi === null || epi === undefined) ? "EPI: ?" : `EPI: ${fmtNum(epi, 3)}`;
      const avTxt = (av === null || av === undefined) ? "AV: N/A" : `AV: ${fmtNum(av, 3)}`;
      const indentPx = Math.max(0, depth) * 18;
      const rkShort = shortId(display, compact ? 30 : 40);
      const labelShort = label ? shortId(label, compact ? 44 : 80) : "";
      const parentRun = run?.parent_run_key ? findRun(run.parent_run_key) : null;
      const parentDisplay = run?.parent_run_key ? shortId(displayId(parentRun || {run_key: run.parent_run_key}, {fallback: run.parent_run_key}), compact ? 16 : 24) : "";

      return `<div class="run-row" data-run="${escapeHtml(rk)}">
        <div>
          <div class="run-headline">
            <span class="indent" style="width:${indentPx}px"></span>
            <div class="run-title-stack">
              <a class="link run-id mono" href="#run/${encodeURIComponent(rk)}" title="${escapeHtml(rk)}">${escapeHtml(rkShort)}</a>
              ${label ? `<div class="run-label muted" title="${escapeHtml(label)}">${escapeHtml(labelShort)}</div>` : ""}
            </div>
            <div class="run-title-tags">
              ${run?.lineage && Number.isFinite(Number(run.lineage.branch_depth)) ? `<span class="tag">d${escapeHtml(String(run.lineage.branch_depth))}</span>` : ""}
              ${run?.archived ? `<span class="tag">ARCHIVED</span>` : ""}
            </div>
          </div>
          <div class="run-meta" style="margin-top:6px;">
            ${statusBadges.join(" ")}
            ${metaBits.join(" ")}
            ${chip(epiTxt)}
            ${chip(avTxt)}
          </div>
        </div>
        <div class="row" style="justify-content:flex-start;gap:8px;">
          <span class="muted">cur</span> <span class="num">${escapeHtml(fmtGen(run?.current_generation) || "?")}</span>
          <span class="muted">/</span>
          <span class="muted">target</span> <span class="num">${escapeHtml(fmtGen(run?.target_generation) || "?")}</span>
        </div>
        <div class="row" style="justify-content:flex-start;gap:8px;flex-wrap:wrap;">
          ${run?.parent_run_key ? chip(`parent: ${parentDisplay}`) : chip("root")}
          ${run?.parent_run_key && Number.isFinite(Number(run?.lineage?.branch_from_generation)) ? chip(`fork@gen ${Number(run.lineage.branch_from_generation)}`) : ""}
          ${run?.parent_run_key && run?.lineage?.parent_checkpoint_id ? chip(`ckpt ${shortId(String(run.lineage.parent_checkpoint_id), compact ? 12 : 18)}`) : ""}
        </div>
        <div class="run-actions-col">
          ${renderTimeline(run, branchPoints)}
          <div class="row" style="gap:6px; margin-top:8px; justify-content:flex-end;">
            ${renderRunControls(run, {iconOnly: compact})}
          </div>
        </div>
      </div>`;
    }

    if(typeof window !== "undefined"){
      try{
        window.apiRunStart = apiRunStart;
        window.apiRunStop = apiRunStop;
        window.apiRunResume = apiRunResume;
        window.apiRunTerminateUnmanaged = apiRunTerminateUnmanaged;
      }catch(_e){}
    }

    return {
      runKeyOf,
      findRun,
      findParentRunKey,
      computeCheckpointBranchCounts,
      filterOverviewRuns,
      buildTreeOrder,
      renderTimeline,
      renderRunRow,
      renderRunControls,
    };
  }

  window.EIELRunsFactory = { create };
})();
