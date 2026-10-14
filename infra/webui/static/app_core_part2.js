window.__EIEL_APP_CORE_PART2_LOADED__ = true;
// -----------------------------
// Run indexing helpers
// -----------------------------

let __runs = null;
function _runsModule(){
  if(__runs) return __runs;
  const f = (window.EIELRunsFactory && typeof window.EIELRunsFactory.create === "function")
    ? window.EIELRunsFactory
    : null;
  if(!f) throw new Error("Missing run helpers module");
  __runs = f.create({
    State,
    escapeHtml,
    shortId,
    displayId,
    fmtNum,
    fmtGen,
    badge,
    chip,
    statusBadgeClass,
    detBadgeClass,
    apiRunStart: apiRunStart,
    apiRunStop: apiRunStop,
    apiRunResume: apiRunResume,
    apiRunTerminateUnmanaged: apiRunTerminateUnmanaged,
  });
  return __runs;
}
function runKeyOf(r){ return _runsModule().runKeyOf(r); }
function findRun(runKey){ return _runsModule().findRun(runKey); }
function findParentRunKey(run){ return _runsModule().findParentRunKey(run); }
function computeCheckpointBranchCounts(parentRunKey){ return _runsModule().computeCheckpointBranchCounts(parentRunKey); }

function buildTreeOrder(groupRuns){ return _runsModule().buildTreeOrder(groupRuns); }
function renderTimeline(run, branchPoints){ return _runsModule().renderTimeline(run, branchPoints); }
function renderRunRow(run, depth, branchPoints){ return _runsModule().renderRunRow(run, depth, branchPoints); }

async function apiRunStart(rk){
  try{
    const res = await postJson(`/api/run/${encodeURIComponent(rk)}/start`, {});
    const pid = res && res.pid ? ` (pid ${res.pid})` : "";
    showToast("Run started"+pid, rk);
    refreshRuns();
  }catch(e){ showGlobalError("Start failed", e.message); }
}

async function apiRunStop(rk){
  try{
    await postJson(`/api/run/${encodeURIComponent(rk)}/stop`, {});
    showToast("Stop signal sent", rk);
  }catch(e){ showGlobalError("Stop failed", e.message); }
}

async function apiRunResume(rk){
  try{
    const res = await postJson(`/api/run/${encodeURIComponent(rk)}/resume`, {});
    const newKey = String(res?.new_run_key || res?.run_key || "").trim();
    if(newKey && newKey !== rk){
      showToast("Continuation started", `${rk} → ${newKey}`);
    }else{
      showToast("Run resumed", rk);
    }
    refreshRuns();
  }catch(e){ showGlobalError("Resume failed", e.message); }
}

async function apiRunTerminateUnmanaged(rk){
  try{
    const ok = window.confirm(`Terminate external run "${rk}" with SIGTERM?`);
    if(!ok) return;
    await postJson(`/api/run/${encodeURIComponent(rk)}/terminate_unmanaged`, {});
    showToast("Terminate signal sent", rk);
    refreshRuns();
  }catch(e){ showGlobalError("Terminate failed", e.message); }
}

function renderSystemRunsPanel(){
  const compact = _overviewIsCompactMode();
  const data = State.systemRuns || {};
  const active = Array.isArray(data.active)
    ? data.active
    : ([]).concat(Array.isArray(data.managed) ? data.managed : [], Array.isArray(data.unmanaged) ? data.unmanaged : []);
  if(!active.length){
    return `<div class="muted">No runs detected.</div>`;
  }
  if(compact){
    return `<div class="ov-system-list">
      ${active.map((r)=>{
        const rkRaw = String(r.run_key || "");
        const shown = displayId(r, {fallback: rkRaw});
        const st = String(r.state || "");
        const pid = r.pid ? String(r.pid) : "";
        const gen = (r.gen===null||r.gen===undefined) ? "" : String(r.gen);
        const age = (r.heartbeat_age_s===null||r.heartbeat_age_s===undefined) ? "n/a" : `${String(r.heartbeat_age_s)}s`;
        const source = String(r.source || r.origin || "").trim().toLowerCase();
        const actionBtn = (source === "external")
          ? `<button class="btn small danger js-stop-external" data-run-key="${escapeHtml(rkRaw)}" title="Stop external run (SIGTERM)">⏹</button>`
          : "";
        return `<div class="ov-system-item">
          <div class="ov-system-main">
            <a class="link mono ov-system-key" href="#run/${encodeURIComponent(rkRaw)}" title="${escapeHtml(rkRaw)}">${escapeHtml(shortId(shown, 28))}</a>
            <div class="row" style="gap:6px;">
              <span class="badge ${st==="HUNG"?"b-fail":(st==="RUNNING"?"b-pass":"b-unknown")}">${escapeHtml(st)}</span>
              ${source ? `<span class="tag">${escapeHtml(source)}</span>` : ""}
            </div>
          </div>
          <div class="ov-system-stats muted">
            <span>pid ${escapeHtml(pid || "n/a")}</span>
            <span>gen ${escapeHtml(gen || "?")}</span>
            <span>hb ${escapeHtml(age)}</span>
          </div>
          ${actionBtn ? `<div class="ov-system-action">${actionBtn}</div>` : ""}
        </div>`;
      }).join("")}
    </div>`;
  }
  const rows = [];
  for(const r of active){
    const rk = escapeHtml(String(r.run_key||""));
    const shown = escapeHtml(displayId(r, {fallback: String(r.run_key || "")}));
    const st = escapeHtml(String(r.state||""));
    const pid = r.pid ? escapeHtml(String(r.pid)) : "";
    const gen = (r.gen===null||r.gen===undefined) ? "" : escapeHtml(String(r.gen));
    const age = (r.heartbeat_age_s===null||r.heartbeat_age_s===undefined) ? "n/a" : escapeHtml(String(r.heartbeat_age_s));
    const source = String(r.source || r.origin || "").trim().toLowerCase();
    const rkRaw = String(r.run_key || "");
    const actionBtn = (source === "external")
      ? `<button class="btn small danger js-stop-external" data-run-key="${escapeHtml(rkRaw)}" title="Stop external run (SIGTERM)">Stop</button>`
      : "";
    rows.push(`<tr><td><a href="#run/${encodeURIComponent(rkRaw)}" title="${rk}">${shown}</a></td><td><span class="badge ${st==="HUNG"?"b-fail":(st==="RUNNING"?"b-pass":"b-unknown")}">${st}</span></td><td class="mono">${pid}</td><td class="mono">${gen}</td><td class="mono">${age}s</td><td>${actionBtn}</td></tr>`);
  }
  return `<div class="table-wrap"><table class="table"><thead><tr><th>Run</th><th>State</th><th>PID</th><th>Gen</th><th>Heartbeat</th><th>Action</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}

// -----------------------------
// Rendering: matrix
// -----------------------------

function filterMatrixRuns(runs){
  const f = State.matrix;
  const qtxt = String(f.search || "").toLowerCase().trim();
  const tag = String(f.tag || "").toLowerCase().trim();
  return runs.filter(r=>{
    if(!f.showArchived && r.archived) return false;
    if(f.onlyDetPass && String(r.determinism || "").toUpperCase() !== "PASS") return false;

    if(f.status && f.status !== "ALL" && String(r.status || "").toUpperCase() !== String(f.status)) return false;
    if(f.determinism && f.determinism !== "ALL" && String(r.determinism || "").toUpperCase() !== String(f.determinism)) return false;
    if(f.strict && f.strict !== "ALL"){
      const strictTxt = r.strict_mode ? "STRICT" : "NON-STRICT";
      if(strictTxt !== f.strict) return false;
    }

    if(tag){
      const tags = Array.isArray(r.tags) ? r.tags.map(x=>String(x).toLowerCase()) : [];
      if(!tags.some(t=>t.includes(tag))) return false;
    }

    if(qtxt){
      const hay = [runKeyOf(r), r.label, (r.tags||[]).join(","), r.notes_excerpt].join(" ").toLowerCase();
      if(!hay.includes(qtxt)) return false;
    }
    return true;
  });
}

function matrixSortValue(run, key){
  const r = run;
  switch(key){
    case "run": return runKeyOf(r);
    case "label": return String(r.label || "");
    case "status": return String(r.status || "");
    case "determinism": return String(r.determinism || "");
    case "strict": return r.strict_mode ? 1 : 0;
    case "verified": return Number.isFinite(Number(r.last_verified_generation)) ? Number(r.last_verified_generation) : -1;
    case "current": return Number.isFinite(Number(r.current_generation)) ? Number(r.current_generation) : -1;
    case "target": return Number.isFinite(Number(r.target_generation)) ? Number(r.target_generation) : -1;
    case "epi": return Number.isFinite(Number(r.epi)) ? Number(r.epi) : -Infinity;
    case "av": return Number.isFinite(Number(r.av)) ? Number(r.av) : -Infinity;
    case "parent": return String(r.parent_run_key || "");
    case "depth": return Number.isFinite(Number(r?.lineage?.branch_depth)) ? Number(r.lineage.branch_depth) : 0;
    case "notes": return String(r.notes_excerpt || "");
    default: return "";
  }
}

function renderMatrixView(){
  const root = q("#view-matrix");
  if(!root) return;

  const runs = filterMatrixRuns(State.runs);
  const sk = State.matrix.sortKey;
  const sd = State.matrix.sortDir;
  const sorted = runs.slice().sort((a,b)=>{
    const va = matrixSortValue(a, sk);
    const vb = matrixSortValue(b, sk);
    const na = (typeof va === "number");
    const nb = (typeof vb === "number");
    if(na && nb){
      return (va - vb) * sd;
    }
    return String(va).localeCompare(String(vb)) * sd;
  });

  const sortArrow = (key)=>{
    if(State.matrix.sortKey !== key) return "";
    return State.matrix.sortDir > 0 ? " ▲" : " ▼";
  };

  root.innerHTML = `
    <div class="forest-controls">
      <input id="mxSearch" class="input small" placeholder="Search run / label / tag / notes" value="${escapeHtml(State.matrix.search)}"/>
      <select id="mxStatus" class="input small">
        ${["ALL","PLANNED","RUNNING","COMPLETE","FAILED","STALLED","INCOMPLETE"].map(x=>`<option value="${x}" ${State.matrix.status===x?"selected":""}>Status: ${x}</option>`).join("")}
      </select>
      <select id="mxDet" class="input small">
        ${["ALL","PASS","FAIL","UNKNOWN"].map(x=>`<option value="${x}" ${State.matrix.determinism===x?"selected":""}>Det: ${x}</option>`).join("")}
      </select>
      <select id="mxStrict" class="input small">
        ${["ALL","STRICT","NON-STRICT"].map(x=>`<option value="${x}" ${State.matrix.strict===x?"selected":""}>Strict: ${x}</option>`).join("")}
      </select>
      <input id="mxTag" class="input small" placeholder="Tag filter" value="${escapeHtml(State.matrix.tag)}"/>
      <label class="row" style="gap:6px;">
        <input id="mxShowArchived" type="checkbox" ${State.matrix.showArchived?"checked":""}/>
        <span class="muted">Show archived</span>
      </label>
      <label class="row" style="gap:6px;">
        <input id="mxOnlyPass" type="checkbox" ${State.matrix.onlyDetPass?"checked":""}/>
        <span class="muted">PASS only</span>
      </label>
      <span class="muted">Rows: ${escapeHtml(String(sorted.length))}</span>
    </div>

    <div class="card" style="overflow:auto;">
      <div class="table-wrap"><table class="table" id="mxTable">
        <thead>
          <tr>
            <th>Act</th>
            <th data-sort="run">Run${sortArrow("run")}</th>
            <th data-sort="label">Label${sortArrow("label")}</th>
            <th data-sort="status">Status${sortArrow("status")}</th>
            <th data-sort="determinism">Determinism${sortArrow("determinism")}</th>
            <th data-sort="strict">Strict${sortArrow("strict")}</th>
            <th data-sort="verified">Verified Gen${sortArrow("verified")}</th>
            <th data-sort="current">Current Gen${sortArrow("current")}</th>
            <th data-sort="target">Target Gen${sortArrow("target")}</th>
            <th data-sort="epi" data-tt="epi" tabindex="0">EPI${sortArrow("epi")}</th>
            <th data-sort="av" data-tt="av" tabindex="0">AV${sortArrow("av")}</th>
            <th data-sort="parent">Parent${sortArrow("parent")}</th>
            <th data-sort="depth">Depth${sortArrow("depth")}</th>
            <th data-sort="notes">Notes${sortArrow("notes")}</th>
          </tr>
        </thead>
        <tbody>
          ${sorted.map(r=>{
            const rk = runKeyOf(r);
            const isScheduled = String(r.source_kind || "") === "scheduled_job";
            const strictTxt = r.strict_mode ? "STRICT" : "NON-STRICT";
            const ver = (Number.isFinite(Number(r.last_verified_generation)) && Number(r.last_verified_generation) >= 0) ? String(r.last_verified_generation) : "?";
            const epi = (r.epi === null || r.epi === undefined) ? "?" : fmtNum(r.epi, 3);
            const av = (r.av === null || r.av === undefined) ? "N/A" : fmtNum(r.av, 3);
            const parent = r.parent_run_key ? shortId(r.parent_run_key, 20) : "—";
            const depth = (r?.lineage && Number.isFinite(Number(r.lineage.branch_depth))) ? String(r.lineage.branch_depth) : "";
            const notes = String(r.notes_excerpt || "");
            return `<tr data-run="${escapeHtml(rk)}">
              <td>${renderRunControls(r, {stopPropagation:true, iconOnly:true})}</td>
              <td><a class="link" href="${isScheduled ? "#schedules" : `#run/${encodeURIComponent(rk)}`}">${escapeHtml(shortId(isScheduled ? String(r.job_id || rk) : rk, 40))}</a>${isScheduled?" <span class=\"tag\">JOB</span>":""}${r.archived?" <span class=\"tag\">ARCHIVED</span>":""}</td>
              <td class="muted">${escapeHtml(shortId(r.label || "", 30))}</td>
              <td>${badge(String(r.status||"UNKNOWN"), statusBadgeClass(r.status||"UNKNOWN"))}${r.epiav_provisional?" "+badge("POSSIBLY_CORRUPT", statusBadgeClass("POSSIBLY_CORRUPT")):""}</td>
              <td>${badge(String(r.determinism||"UNKNOWN"), detBadgeClass(r.determinism||"UNKNOWN"))}</td>
              <td>${escapeHtml(strictTxt)}</td>
              <td class="num">${escapeHtml(ver)}</td>
              <td class="num">${escapeHtml(fmtGen(r.current_generation)||"?")}</td>
              <td class="num">${escapeHtml(fmtGen(r.target_generation)||"?")}</td>
              <td class="num">${escapeHtml(epi||"?")}</td>
              <td class="num">${escapeHtml(av||"N/A")}</td>
              <td class="muted">${escapeHtml(parent)}</td>
              <td class="num">${escapeHtml(depth)}</td>
              <td class="muted">${escapeHtml(shortId(notes, 42))}</td>
            </tr>`;
          }).join("\n")}
        </tbody>
      </table></div>
    </div>
  `;

  // Wire filters.
  safeOn(q("#mxSearch", root), "input", (e)=>{ State.matrix.search = String(e.target.value||""); renderMatrixView(); });
  safeOn(q("#mxStatus", root), "change", (e)=>{ State.matrix.status = String(e.target.value||"ALL"); renderMatrixView(); });
  safeOn(q("#mxDet", root), "change", (e)=>{ State.matrix.determinism = String(e.target.value||"ALL"); renderMatrixView(); });
  safeOn(q("#mxStrict", root), "change", (e)=>{ State.matrix.strict = String(e.target.value||"ALL"); renderMatrixView(); });
  safeOn(q("#mxTag", root), "input", (e)=>{ State.matrix.tag = String(e.target.value||""); renderMatrixView(); });
  safeOn(q("#mxShowArchived", root), "change", (e)=>{ State.matrix.showArchived = !!e.target.checked; renderMatrixView(); });
  safeOn(q("#mxOnlyPass", root), "change", (e)=>{ State.matrix.onlyDetPass = !!e.target.checked; renderMatrixView(); });

  // Wire sorting.
  qa("th[data-sort]", root).forEach(th=>{
    safeOn(th, "click", ()=>{
      const k = th.getAttribute("data-sort");
      if(!k) return;
      if(State.matrix.sortKey === k){
        State.matrix.sortDir = -State.matrix.sortDir;
      }else{
        State.matrix.sortKey = k;
        State.matrix.sortDir = 1;
      }
      renderMatrixView();
    });
  });

  // Row click (excluding links).
  qa("tbody tr", root).forEach(tr=>{
    safeOn(tr, "click", (e)=>{
      const a = e.target && e.target.closest ? e.target.closest("a") : null;
      if(a) return;
      const rk = tr.getAttribute("data-run");
      if(rk) location.hash = `#run/${encodeURIComponent(rk)}`;
    });
  });
}

// -----------------------------
// Rendering: ChangeSets library
// -----------------------------

function filterChangeSets(items){
  const qtxt = String(State.changesetsView.search || "").toLowerCase().trim();
  if(!qtxt) return items;
  return items.filter(cs=>{
    const hay = [cs.id, cs.label, cs.description, cs.expectation_note].join(" ").toLowerCase();
    return hay.includes(qtxt);
  });
}

function changesetCmdSummary(cs){
  const cmds = Array.isArray(cs?.commands) ? cs.commands : [];
  const counts = new Map();
  for(const c of cmds){
    const t = String(c?.command_type || "unknown");
    counts.set(t, (counts.get(t)||0)+1);
  }
  const parts = Array.from(counts.entries()).sort((a,b)=>a[0].localeCompare(b[0])).map(([k,v])=>`${k} (${v})`);
  return parts.length ? parts.join(", ") : "(no commands)";
}

function renderChangeSetsView(){
  const root = q("#view-changesets");
  if(!root) return;
  const items = filterChangeSets(State.changesets || []);

  const selectedCount = State.planChangeSets.size;
  root.innerHTML = `
    <div class="forest-controls">
      <input id="csSearch" class="input small" placeholder="Search ChangeSets" value="${escapeHtml(State.changesetsView.search)}"/>
      <span class="muted">Found: ${escapeHtml(String(items.length))}</span>
      <span class="muted">Plan selection: ${escapeHtml(String(selectedCount))}</span>
      <button id="csClear" class="btn small" ${selectedCount?"":"disabled"}>Clear selection</button>
    </div>
    <div id="csList"></div>
  `;

  safeOn(q("#csSearch", root), "input", (e)=>{ State.changesetsView.search = String(e.target.value||""); renderChangeSetsView(); });
  safeOn(q("#csClear", root), "click", ()=>{ State.planChangeSets = new Set(); renderChangeSetsView(); showToast("Selection cleared", "ChangeSets plan selection cleared."); });

  const list = q("#csList", root);
  if(!list) return;
  if(items.length === 0){
    list.innerHTML = `<div class="card"><div class="muted">No ChangeSets found. Expected at runtime/state/webui/changesets/*.json</div></div>`;
    return;
  }

  list.innerHTML = items.map(cs=>{
    const id = String(cs.id || cs.changeset_id || "");
    const label = String(cs.label || "");
    const desc = String(cs.description || "");
    const exp = String(cs.expectation_note || "");
    const cmdSum = changesetCmdSummary(cs);
    const checked = State.planChangeSets.has(id);
    return `<div class="card" data-cs="${escapeHtml(id)}">
      <div class="row" style="justify-content:space-between;align-items:flex-start;">
        <div>
          <div style="font-weight:800;">${escapeHtml(id)} <span class="muted" style="font-weight:400;">${escapeHtml(label)}</span></div>
          <div class="muted" style="margin-top:6px;white-space:pre-wrap;">${escapeHtml(desc)}</div>
        </div>
        <label class="row" style="gap:6px;align-items:center;">
          <input type="checkbox" class="csPick" ${checked?"checked":""}/>
          <span class="muted">Use in plan</span>
        </label>
      </div>
      <div class="row" style="margin-top:10px;">
        ${chip(`commands: ${cmdSum}`)}
        ${exp ? chip(`expectation: ${shortId(exp, 40)}`) : chip("expectation: (none)")}
      </div>
      <div class="row" style="margin-top:12px;gap:8px;">
        <button class="btn small csView">View JSON</button>
        <button class="btn small csApply">Apply to run…</button>
      </div>
    </div>`;
  }).join("\n");

  qa(".card[data-cs]", list).forEach(card=>{
    const id = card.getAttribute("data-cs") || "";
    safeOn(q(".csPick", card), "change", (e)=>{
      if(e.target.checked){
        State.planChangeSets.add(id);
      }else{
        State.planChangeSets.delete(id);
      }
      renderChangeSetsView();
    });

    safeOn(q(".csView", card), "click", ()=>{
      const found = (State.changesets || []).find(x => String(x.id||"") === id) || null;
      openModal(`ChangeSet: ${id}`, `<pre style="white-space:pre-wrap;margin:0;">${escapeHtml(JSON.stringify(found, null, 2))}</pre>`);
    });

    safeOn(q(".csApply", card), "click", ()=>{
      openApplyChangeSetsModal([id]);
    });
  });
}

function openApplyChangeSetsModal(ids){
  const runs = State.runs.filter(r=>!r.archived);
  if(runs.length === 0){
    showToast("No runs", "No non-archived runs available.");
    return;
  }
  const opts = runs.map(r=>{
    const rk = runKeyOf(r);
    const label = r.label ? ` (${r.label})` : "";
    return `<option value="${escapeHtml(rk)}">${escapeHtml(shortId(rk, 50) + label)}</option>`;
  }).join("");

  openModal(
    "Apply ChangeSets to a run",
    `<div class="row" style="gap:10px;align-items:center;">
      <div class="muted">ChangeSets:</div>
      ${ids.map(x=>chip(x)).join(" ")}
    </div>
    <div style="margin-top:14px;">
      <div class="muted" style="margin-bottom:6px;">Target run</div>
      <select id="csApplyRun" class="input" style="width:100%;">${opts}</select>
    </div>
    <div style="margin-top:14px;" class="muted">
      This submits the ChangeSet commands into the run's inbox. It does not edit engine files.
    </div>
    <div class="row" style="margin-top:16px;gap:10px;justify-content:flex-end;">
      <button id="csApplyCancel" class="btn">Cancel</button>
      <button id="csApplyGo" class="btn ok">Submit</button>
    </div>`
  );

  safeOn(q("#csApplyCancel"), "click", closeModal);
  safeOn(q("#csApplyGo"), "click", async ()=>{
    const rk = q("#csApplyRun") ? String(q("#csApplyRun").value || "") : "";
    if(!rk){
      showToast("Missing run", "Select a target run.");
      return;
    }
    if(!confirm(`Submit ${ids.length} ChangeSet(s) to run ${rk}?`)) return;
    try{
      const res = await apiSubmitCommand(rk, {changesets: ids});
      closeModal();
      const submitted = Array.isArray(res.submitted) ? res.submitted.length : 0;
      const errs = Array.isArray(res.errors) ? res.errors.length : 0;
      showToast("Submitted", `Submitted ${submitted} command(s). Errors: ${errs}.`);
      // Track pending UUIDs best-effort.
      const pend = State.runDetail.pendingUuids;
      if(pend && res.submitted){
        for(const s of res.submitted){
          if(s && s.command_uuid) pend.add(String(s.command_uuid));
        }
      }
    }catch(e){
      showToast("Submit failed", e?.message || String(e));
    }
  });
}

// -----------------------------
// Rendering: pressure search
// -----------------------------

// -----------------------------
// Rendering: run detail
// -----------------------------

function ensureRunDetailLayout(runKey){
  const root = q("#view-run");
  if(!root) return null;

  // Only rebuild the layout when we switch runs. Otherwise, keep DOM stable so
  // scroll position and open dropdowns don’t get nuked every refresh tick.
  if(root.dataset.runKey === runKey && q("#rdTitle", root)) return root;

  root.dataset.runKey = runKey;
  root.innerHTML = `
    <div class="card">
      <div class="run-header">
        <div>
          <div id="rdTitle" class="run-title"></div>
          <div id="rdBadges" class="row" style="margin-top:10px;gap:8px;flex-wrap:wrap;"></div>
          <div class="muted" style="margin-top:10px;font-size:12px;">
            Verified gen = last generation that passed determinism verification (replay). Live gen may be newer.
          </div>
          <div id="rdTags" class="row" style="margin-top:10px;gap:8px;flex-wrap:wrap;"></div>
          <div id="rdLineage" class="row" style="margin-top:10px;gap:10px;align-items:center;"></div>
          <div id="rdWarnings" class="row" style="margin-top:12px;gap:10px;align-items:center;"></div>
        </div>
        <div class="row" style="gap:8px;flex-wrap:wrap;justify-content:flex-end;">
          <button class="btn" id="btnPlanBranch">Plan Branch...</button>
          <button class="btn" id="btnPlanCont">Plan Continuation...</button>
          <button class="btn" id="btnCmdWizard">Submit Intervention...</button>
          <button class="btn" id="btnExport">Export Snapshot...</button>
          <button class="btn" id="btnEditMeta">Edit Label/Tags...</button>
          <button class="btn btn-danger" id="btnArchive">Archive</button>
        </div>
      </div>
    </div>

    <div class="grid2" style="margin-top:14px;">
      <div class="card">
        <div class="card-header">
          <div class="card-title">EPI over time</div>
          <div class="row" style="gap:10px;align-items:center;">
            <select id="rdRange" class="input">
              <option value="200">last 200 gen</option>
              <option value="1000">last 1000 gen</option>
              <option value="5000">last 5000 gen</option>
              <option value="20000">last 20000 gen</option>
            </select>
            <label class="row" style="gap:6px;align-items:center;">
              <input type="checkbox" id="rdOverlay" checked />
              <span>Compare parent</span>
            </label>
            <label class="row" style="gap:6px;align-items:center;">
              <input type="checkbox" id="rdSmooth" checked />
              <span>Smooth</span>
            </label>
          </div>
        </div>
        <canvas id="epiChart" width="800" height="260"></canvas>
        <div class="muted" id="epiInfo" style="margin-top:8px;font-size:12px;"></div>
        <div class="muted" id="rdOverlayInfo" style="margin-top:6px;font-size:12px;"></div>
      </div>
      <div class="card">
        <div class="card-header">
          <div class="card-title">AV over time</div>
          <div class="muted" style="font-size:12px;">null AV shows as gaps</div>
        </div>
        <canvas id="avChart" width="800" height="260"></canvas>
        <div class="muted" id="avInfo" style="margin-top:8px;font-size:12px;"></div>
      </div>
    </div>

    <div class="card" style="margin-top:14px;">
      <div class="card-header" style="align-items:flex-start;">
        <div>
          <div class="card-title">Control + Pressure Timeline</div>
          <div class="muted" style="font-size:12px;">Binned view from command_events.jsonl + metrics.csv. Hover for details, click a bin for raw payloads.</div>
        </div>
        <div class="row" style="gap:8px;align-items:center;">
          <select id="rdTlBins" class="input">
            <option value="100">bins: 100</option>
            <option value="200" selected>bins: 200</option>
            <option value="400">bins: 400</option>
          </select>
        </div>
      </div>
      <div class="timeline-wrap">
        <canvas id="rdTimelineCanvas" width="1200" height="180"></canvas>
        <div id="rdTimelineTip" class="timeline-tip" style="display:none;"></div>
      </div>
      <div id="rdTimelineInfo" class="muted" style="margin-top:8px;font-size:12px;"></div>
      <div id="rdTimelineDrawer" class="timeline-drawer" style="display:none;">
        <div class="timeline-drawer-head">
          <div class="mono" id="rdTimelineDrawerTitle"></div>
          <button class="btn small" id="rdTimelineDrawerClose">Close</button>
        </div>
        <div class="row" style="gap:10px;flex-wrap:wrap;margin-top:8px;">
          <button class="btn small" id="rdTlShowControl">Control events</button>
          <button class="btn small" id="rdTlShowPressure">Pressure rows</button>
        </div>
        <pre id="rdTimelineDrawerBody" class="mono" style="margin-top:10px;max-height:260px;overflow:auto;background:#0f1115;border:1px solid #222;border-radius:10px;padding:10px;color:#d6d6d6;"></pre>
      </div>
    </div>

    <div class="grid2" style="margin-top:14px;">
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">Metrics</div>
            <div class="muted" style="font-size:12px;">Plot any column from metrics.csv (and viz/metrics_summary.csv if present).</div>
          </div>
          <select id="rdMetric" class="input"></select>
        </div>
        <canvas id="metricChart" width="800" height="260"></canvas>
        <div class="muted" id="metricInfo" style="margin-top:8px;font-size:12px;"></div>
      </div>
      <div class="card">
        <div class="card-header">
          <div class="card-title">Knobs</div>
          <button class="btn" id="btnLoadKnobs">Load</button>
        </div>
        <div class="muted" id="knobsInfo" style="font-size:12px;margin-bottom:8px;">Read-only view of run_config.csv (engine config). Click Load to read run_config.csv.</div>
        <div id="knobsTableWrap" style="max-height:320px;overflow:auto;"></div>
      </div>
    </div>

    <div class="card" style="margin-top:14px;">
      <div class="card-header"><div class="card-title">Checkpoint Explorer</div></div>
      <div id="ckInfo" class="muted" style="font-size:12px;margin-bottom:8px;"></div>
      <div id="ckWrap" style="max-height:320px;overflow:auto;">
        <div class="table-wrap"><table class="table">
          <thead>
            <tr>
              <th>gen</th><th>checkpoint_id</th><th>path</th><th style="width:140px;">copy</th><th style="width:210px;">plan</th>
            </tr>
          </thead>
          <tbody id="ckBody"></tbody>
        </table></div>
      </div>
    </div>

    <div class="grid2" style="margin-top:14px;">
      <div class="card">
        <div class="card-header"><div class="card-title">Inbox Queue</div></div>
        <div id="iqInfo" class="muted" style="font-size:12px;margin-bottom:8px;"></div>
        <div id="iqWrap" style="max-height:320px;overflow:auto;">
          <div class="table-wrap"><table class="table">
            <thead><tr><th>uuid</th><th>status</th><th>target_gen</th><th>apply_phase</th><th>type</th><th>summary</th></tr></thead>
            <tbody id="iqBody"></tbody>
          </table></div>
        </div>
      </div>
      <div class="card">
        <div class="card-header" style="align-items:flex-start;">
          <div>
            <div class="card-title">Applied Commands</div>
            <div class="row" style="gap:8px;margin-top:6px;flex-wrap:wrap;">
              <input id="acSearch" class="input small" placeholder="search uuid/type/reason" style="width:240px;">
              <select id="acStatus" class="input small">
                <option value="ALL">ALL</option>
                <option value="APPLIED">APPLIED</option>
                <option value="REJECTED">REJECTED</option>
              </select>
              <button class="btn small" id="acOlder">Older</button>
              <button class="btn small" id="acLatest">Latest</button>
              <button class="btn small" id="acNewer">Newer</button>
              <input id="acJumpGen" class="input small" type="number" min="0" placeholder="from gen" style="width:120px;">
            </div>
          </div>
          <select id="rdCmdLimit" class="input">
            <option value="200">last 200</option>
            <option value="1000">last 1000</option>
            <option value="5000">last 5000</option>
            <option value="20000">last 20000</option>
          </select>
        </div>
        <div id="acInfo" class="muted" style="font-size:12px;margin-bottom:8px;"></div>
        <div id="acWrap" style="max-height:320px;overflow:auto;">
          <div class="table-wrap"><table class="table">
            <thead><tr><th>uuid</th><th>status</th><th>target_gen</th><th>apply_phase</th><th>type</th><th>summary</th></tr></thead>
            <tbody id="acBody"></tbody>
          </table></div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:14px;">
      <div class="card-header" style="align-items:flex-start;">
        <div>
          <div class="row" style="gap:8px;align-items:center;">
            <div class="card-title">Pressure Autopilot (Quick Submit)</div>
            <button id="paHelp" class="btn small" title="Autopilot help" style="min-width:28px;padding:0 8px;">?</button>
          </div>
          <div class="muted" style="font-size:12px;">Build and submit a <span class="mono">PressureUpdateCommand</span> without copy/pasting JSON.</div>
        </div>
      </div>

      <div class="row" style="gap:12px;align-items:flex-end;flex-wrap:wrap;margin-top:10px;">
        <label class="row" style="gap:8px;align-items:center;">
          <input id="paEnable" type="checkbox" checked />
          <span data-tt="autopilot_enable">Enable</span>
        </label>

        <div>
          <div class="muted" style="margin-bottom:6px;font-size:12px;"><span data-tt="autopilot_apply_gen">Apply at gen</span></div>
	          <input id="paGen" class="input small" type="number" min="0" step="1" value="" style="width:130px;" />
        </div>

        <div>
          <div class="muted" style="margin-bottom:6px;font-size:12px;"><span data-tt="autopilot_tick_interval">Tick interval (gens)</span></div>
          <input id="paInterval" class="input small" type="number" min="1" step="1" value="200" style="width:160px;" />
        </div>

        <div>
          <div class="muted" style="margin-bottom:6px;font-size:12px;"><span data-tt="autopilot_profile">Profile</span></div>
          <select id="paProfile" class="input small" style="width:140px;">
            <option value="balanced">balanced</option>
            <option value="cycle">cycle</option>
            <option value="scarcity">scarcity</option>
            <option value="cost">cost</option>
            <option value="shock">shock</option>
          </select>
        </div>

        <div class="row" style="gap:10px;align-items:center;">
          <button id="paSend" class="btn ok">Send</button>
          <button id="paCopy" class="btn">Copy JSON</button>
        </div>
      </div>
      <div id="paProfileHint" class="muted" style="margin-top:6px;font-size:12px;"></div>
      <div class="muted" style="margin-top:6px;font-size:12px;">
        Profile selects which pressure axis is controlled; it does not auto-change the sliders below.
      </div>

      <div class="row" style="gap:14px;align-items:flex-end;flex-wrap:wrap;margin-top:10px;">
        <div>
          <div class="muted" style="margin-bottom:6px;font-size:12px;"><span data-tt="autopilot_delta_up">Increase Pressure Step</span>: <span id="paDeltaUpVal" class="mono">0.05</span></div>
          <input id="paDeltaUp" type="range" min="0.01" max="0.25" step="0.01" value="0.05" style="width:170px;" />
        </div>
        <div>
          <div class="muted" style="margin-bottom:6px;font-size:12px;"><span data-tt="autopilot_delta_down">Reduce Pressure Step</span>: <span id="paDeltaDownVal" class="mono">0.10</span></div>
          <input id="paDeltaDown" type="range" min="0.01" max="0.25" step="0.01" value="0.10" style="width:170px;" />
        </div>
        <div>
          <div class="muted" style="margin-bottom:6px;font-size:12px;"><span data-tt="autopilot_target_survival">Target survival</span>: <span id="paTargetSurvivalVal" class="mono">0.55</span></div>
          <input id="paTargetSurvival" type="range" min="0.20" max="0.90" step="0.01" value="0.55" style="width:170px;" />
        </div>
        <div>
          <div class="muted" style="margin-bottom:6px;font-size:12px;"><span data-tt="autopilot_survival_ceiling">Survival ceiling</span>: <span id="paSurvivalCeilingVal" class="mono">0.80</span></div>
          <input id="paSurvivalCeiling" type="range" min="0.50" max="0.99" step="0.01" value="0.80" style="width:170px;" />
        </div>
      </div>

      <div class="card" style="margin-top:10px;padding:10px;border:1px solid rgba(255,255,255,0.08);">
        <div class="muted" style="font-size:12px;margin-bottom:6px;"><span data-tt="autopilot_active_values">Currently active values (derived)</span></div>
        <div id="paActiveInfo" class="muted" style="font-size:12px;margin-bottom:6px;">Loading…</div>
        <div id="paActiveGrid" class="mono" style="font-size:12px;line-height:1.45;"></div>
      </div>

      <div id="paPreview" class="muted" style="margin-top:10px;font-size:12px;"></div>
      <div class="muted" style="margin-top:6px;font-size:12px;">Note: this submits to the run inbox. It does not edit engine files. Hover labels for help.</div>
    </div>

<div class="card" style="margin-top:14px;">
  <div class="card-header" style="align-items:flex-start;">
    <div>
      <div class="card-title">Autopilot Actions</div>
      <div class="muted" style="font-size:12px;">Decisions emitted by the per-run pressure autopilot (read-only). If empty, autopilot may be disabled or has not ticked yet.</div>
      <div class="row" style="gap:8px;margin-top:6px;flex-wrap:wrap;">
        <input id="apSearch" class="input small" placeholder="search rule/axis/uuid" style="width:240px;">
        <button class="btn small" id="apOlder">Older</button>
        <button class="btn small" id="apLatest">Latest</button>
        <button class="btn small" id="apNewer">Newer</button>
        <input id="apJumpGen" class="input small" type="number" min="0" placeholder="from gen" style="width:120px;">
      </div>
    </div>
    <select id="rdApLimit" class="input">
      <option value="200">last 200</option>
      <option value="1000">last 1000</option>
      <option value="5000">last 5000</option>
      <option value="20000">last 20000</option>
    </select>
  </div>
  <div id="apInfo" class="muted" style="font-size:12px;margin-bottom:8px;"></div>
  <div id="apWrap" style="max-height:320px;overflow:auto;">
    <div class="table-wrap"><table class="table">
      <thead><tr><th>gen</th><th>profile</th><th>axis</th><th>pressure</th><th>rule</th><th>updates</th><th style="width:140px;">view</th></tr></thead>
      <tbody id="apBody"></tbody>
    </table></div>
  </div>
</div>

<div class="card" style="margin-top:14px;">
      <div class="card-header">
        <div class="card-title">Notes</div>
        <button class="btn" id="btnSaveNotes">Save</button>
      </div>
      <textarea id="notesBox" class="input" style="width:100%;min-height:110px;"></textarea>
      <div id="notesInfo" class="muted" style="margin-top:6px;font-size:12px;"></div>
    </div>
  `;

  wireRunDetailEvents(root);
  return root;
}
