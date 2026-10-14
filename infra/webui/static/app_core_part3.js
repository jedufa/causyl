window.__EIEL_APP_CORE_PART3_LOADED__ = true;
// -----------------------------
// Patch Backlog view
// -----------------------------


function _stableStringify(v){
  // Minimal stable stringify: sorts object keys recursively so signatures don't flicker.
  // Not bulletproof, but good enough to skip pointless re-renders.
  const seen = new WeakSet();
  const walk = (x)=>{
    if(x === null || typeof x !== "object") return x;
    if(seen.has(x)) return null; // avoid cycles; shouldn't exist in API docs
    seen.add(x);
    if(Array.isArray(x)) return x.map(walk);
    const out = {};
    Object.keys(x).sort().forEach((k)=>{ out[k] = walk(x[k]); });
    return out;
  };
  try{ return JSON.stringify(walk(v)); }catch(_e){ return JSON.stringify(v); }
}

function _hashString32(s){
  // Simple non-crypto hash for change detection.
  let h = 2166136261 >>> 0;
  const str = String(s||"");
  for(let i=0;i<str.length;i++){
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return ("00000000" + h.toString(16)).slice(-8);
}

async function refreshPatchBacklog(){
  const doc = await apiPatchBacklog();
  const sig = _hashString32(_stableStringify(doc));
  const changed = (String(sig) !== String(State.patchBacklogSig||""));
  State.patchBacklogSig = sig;
  if(changed || !State.patchBacklog){
    State.patchBacklog = doc;
    State.lastPatchBacklogUtc = new Date().toISOString();
  }
  return {changed, sig};
}


async function refreshLedgerItems(){
  const doc = await apiLedgerItems(500);
  const sig = _hashString32(_stableStringify(doc));
  const changed = (String(sig) !== String(State.ledger.itemsSig||""));
  State.ledger.itemsSig = sig;
  if(changed || !State.ledger.items){
    State.ledger.items = doc;
    State.ledger.lastItemsUtc = new Date().toISOString();
  }
  return {changed, sig};
}

async function refreshLedgerActive(){
  const uid = String(State.ledger.activeUid||"").trim();
  if(!uid) return {ok:false};
  const payload = await apiLedgerItem(uid);
  State.ledger.activeItem = payload;
  State.ledger.activeLoadedUtc = new Date().toISOString();
  // Reset editor drafts on load unless user is actively editing.
  try{
    if(!State.ledger.editor.dirtyPis){
      State.ledger.editor.pisDraft = String(payload?.pis_markdown || "");
    }
    if(!State.ledger.editor.dirtyMeta){
      State.ledger.editor.metaDraft = JSON.parse(JSON.stringify(payload?.meta || {}));
    }
  }catch(_e){}
  return {ok:true};
}

function _formatBacklogMarkdown(doc){
  const items = (doc && Array.isArray(doc.items)) ? doc.items : [];
  const stamp = new Date().toISOString();
  let out = `# Ledger (PIS)\n\nGenerated: \n\n`;
  for(let i=0;i<items.length;i++){
    const it = items[i] || {};
    const md = String(it.pis_markdown || "").trim();
    if(!md) continue;
    out += md + "\n\n---\n\n";
  }
  return out.trim() + "\n";
}

async function backlogCopyAll(){
  const doc = State.patchBacklog || {items:[]};
  const txt = _formatBacklogMarkdown(doc);
  const ok = await copyTextToClipboard(txt);
  const items = Array.isArray(doc.items) ? doc.items.length : 0;
  if(ok){ showToast("Copied", `Copied ${items} items`); return; }
  // Fallback: select text in a temporary textarea so user can long-press copy.
  try{
    const ta = document.createElement("textarea");
    ta.value = txt;
    ta.style.width = "100%";
    ta.style.height = "220px";
    const root = q("#backlogCopyFallback");
    if(root){ root.innerHTML = ""; root.appendChild(ta); ta.focus(); ta.select(); }
    showToast("Copy blocked", "Text selected, long-press to copy");
  }catch(e){
    showToast("Copy failed", String(e));
  }
}


function backlogExportAll(){
  const doc = State.patchBacklog || {items:[]};
  const txt = _formatBacklogMarkdown(doc);
  const d = new Date();
  const pad = (n)=>String(n).padStart(2,"0");
  const ts = `${d.getUTCFullYear()}${pad(d.getUTCMonth()+1)}${pad(d.getUTCDate())}T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}Z`;
  const fn = `patch-backlog-export_${ts}.md`;
  downloadTextFile(fn, txt);
  showToast("Exported", fn);
}




// Patch Backlog status options (single source of truth).
// IMPORTANT:
// - Only expose canonical values in the dropdown to avoid duplicate entries.
// - Keep backward-compatible normalization so older stored values don't "snap back".
const BACKLOG_STATUS_OPTIONS = [
  { value: 'planned', label: 'planned' },
  { value: 'currently_in_integration', label: 'Currently in Integration' },
  { value: 'implemented', label: 'implemented' },
  { value: 'verified', label: 'verified' },
  { value: 'blocked', label: 'blocked' },
];

function backlogNormalizeStatus(v){
  const raw = String(v || '').trim().toLowerCase();
  if(raw === 'in_integration') return 'currently_in_integration';
  if(raw === 'done') return 'implemented';
  // Pass-through for known values (or unknown future values).
  return raw || 'planned';
}

function backlogStatusLabel(v){
  const norm = backlogNormalizeStatus(v);
  const hit = BACKLOG_STATUS_OPTIONS.find(x=>x.value===norm);
  return hit ? hit.label : norm;
}

// Deterministic backlog ordering (view-only; does not mutate persisted JSON order).
function backlogStatusRank(statusNorm){
  const s = backlogNormalizeStatus(statusNorm);
  if(s === "currently_in_integration") return 0;
  if(s === "planned") return 1;
  if(s === "implemented") return 2;
  if(s === "verified") return 3;
  if(s === "blocked") return 4;
  return 99;
}

function backlogParseUidUtc(uidStr){
  try{
    const s = String(uidStr || "").trim();
    const m = s.match(/^PIS-(\d{8})-(\d{6})Z-/);
    if(!m) return null;
    const ymd = m[1];
    const hms = m[2];
    const iso = `${ymd.slice(0,4)}-${ymd.slice(4,6)}-${ymd.slice(6,8)}T${hms.slice(0,2)}:${hms.slice(2,4)}:${hms.slice(4,6)}Z`;
    const t = Date.parse(iso);
    return Number.isFinite(t) ? t : null;
  }catch(_e){
    return null;
  }
}

function backlogItemRecencyMs(item){
  try{
    const it = item || {};
    const u = it.updated_utc ? Date.parse(String(it.updated_utc)) : NaN;
    if(Number.isFinite(u)) return u;
    const c = it.created_utc ? Date.parse(String(it.created_utc)) : NaN;
    if(Number.isFinite(c)) return c;
    const p = backlogParseUidUtc(String(it.uid || it.id || ""));
    if(Number.isFinite(p)) return p;
    return 0;
  }catch(_e){
    return 0;
  }
}

function backlogSortedItems(items){
  const arr = Array.isArray(items) ? items.slice() : [];
  arr.sort((a,b)=>{
    const ra = backlogStatusRank(a && (a.status||""));
    const rb = backlogStatusRank(b && (b.status||""));
    if(ra !== rb) return ra - rb;
    const ta = backlogItemRecencyMs(a);
    const tb = backlogItemRecencyMs(b);
    if(ta !== tb) return tb - ta;
    const ida = String((a && (a.id || a.uid)) || "");
    const idb = String((b && (b.id || b.uid)) || "");
    if(ida < idb) return -1;
    if(ida > idb) return 1;
    return 0;
  });
  return arr;
}


// -----------------------------
// Auto-refresh UX guard (mobile + forms)
// -----------------------------

function _isModalOpen(){
  try{
    const overlay = q("#modalOverlay");
    return !!(overlay && !overlay.classList.contains("hidden"));
  }catch(_e){
    return false;
  }
}

function _isUserEditing(){
  try{
    if(_isModalOpen()) return true;
    const ae = document.activeElement;
    if(!ae) return false;
    const tag = String(ae.tagName || "").toUpperCase();
    if(tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    if(ae.isContentEditable) return true;
  }catch(_e){}
  return false;
}

function _isUserInteracting(){
  // Reading isn't "editing", but we still shouldn't collapse things the user opened.
  try{
    if(State.view === "backlog" && State.backlogUi && State.backlogUi.expandedIds && State.backlogUi.expandedIds.size > 0) return true;
  }catch(_e){}
  return false;
}




function _ledgerStatusBadgeClass(st){
  const s = String(st||"").toLowerCase();
  if(s === "done") return "b-pass";
  if(s === "deferred") return "b-muted";
  if(s === "ready_for_implement") return "b-unknown";
  if(s === "in_progress") return "b-unknown";
  if(s === "ready_for_assure") return "b-warn";
  if(s === "verified") return "b-pass";
  if(s === "implemented") return "b-pass";
  if(s === "blocked") return "b-fail";
  if(s === "abandoned") return "b-muted";
  if(s === "reopened") return "b-warn";
  if(s === "deprecated") return "b-muted";
  return "b-unknown";
}

function _ledgerFilterItems(items){
  const ui = (State.ledger && State.ledger.ui) ? State.ledger.ui : {};
  const qtxt = String(ui.search||"").trim().toLowerCase();
  const st = String(ui.status||"ALL").trim().toUpperCase();
  const tag = String(ui.tag||"").trim().toLowerCase();
  const showDep = (ui.showDeprecated === true);
  const showReop = (ui.showReopened !== false);

  const out = [];
  for(const it of (items||[])){
    if(!it) continue;
    const status = String(it.status||"");
    if(!showDep && String(status).toLowerCase()==="deprecated") continue;
    if(!showReop && String(status).toLowerCase()==="reopened") continue;
    if(st !== "ALL" && String(status).toUpperCase() !== st) continue;

    const title = String(it.title||"");
    const uid = String(it.uid||"");
    const slug = String(it.slug||"");
    const tags = Array.isArray(it.tags) ? it.tags.map(x=>String(x||"")) : [];
    if(tag){
      let has = false;
      for(const t of tags){
        if(String(t).toLowerCase().includes(tag)){ has = true; break; }
      }
      if(!has) continue;
    }
    if(qtxt){
      const hay = (title+" "+uid+" "+slug+" "+tags.join(" ")).toLowerCase();
      if(!hay.includes(qtxt)) continue;
    }
    out.push(it);
  }
  // Stable sort: most recently updated first
  out.sort((a,b)=>{
    const au = String(a.updated_utc||"");
    const bu = String(b.updated_utc||"");
    if(au < bu) return 1;
    if(au > bu) return -1;
    const at = String(a.title||"");
    const bt = String(b.title||"");
    return at.localeCompare(bt);
  });
  return out;
}

function openLedgerCreateModal(){
  const body = `
    <div class="col" style="gap:10px;">
      <div class="row" style="gap:10px; flex-wrap:wrap;">
        <div class="col" style="min-width:240px; flex:1;">
          <div class="label">Title</div>
          <input id="lgNewTitle" class="input" placeholder="Optional (auto-derived from PIS first sentence)"/>
        </div>
        <div class="col" style="min-width:200px; width:260px;">
          <div class="label">Slug (optional)</div>
          <input id="lgNewSlug" class="input" placeholder="kebab-case"/>
        </div>
      </div>
      <div class="row" style="gap:10px; flex-wrap:wrap;">
        <div class="col" style="min-width:240px; flex:1;">
          <div class="label">Tags (comma-separated)</div>
          <input id="lgNewTags" class="input" placeholder="determinism, webui, ..."/>
        </div>
      </div>
      <div class="col">
        <div class="label">PIS markdown</div>
        <textarea id="lgNewPis" class="textarea mono" style="min-height:220px;" placeholder="# Goal\n..."></textarea>
        <div class="muted" style="font-size:12px; margin-top:6px;">If markdown contains a valid <span class="mono">PIS-YYYYMMDD-HHMMSSZ-XXXX</span> UID, it will be reused.</div>
      </div>
      <div class="row" style="justify-content:flex-end; gap:10px;">
        <button id="lgNewCancel" class="btn">Cancel</button>
        <button id="lgNewCreate" class="btn primary">Create</button>
      </div>
    </div>
  `;
  openModal("New Ledger Item", body, ()=>{
    const t = q("#lgNewTitle");
    const s = q("#lgNewSlug");
    const tags = q("#lgNewTags");
    const pis = q("#lgNewPis");
    const btnC = q("#lgNewCreate");
    const btnX = q("#lgNewCancel");
    safeOn(btnX, "click", closeModal);
    safeOn(btnC, "click", async ()=>{
      try{
        const title = String(t?.value||"").trim();
        const slug = String(s?.value||"").trim();
        const tagsArr = String(tags?.value||"").split(",").map(x=>String(x||"").trim()).filter(Boolean);
        const pisMd = String(pis?.value||"");
        const res = await apiLedgerCreate({title, slug: slug||undefined, tags: tagsArr, pis_markdown: pisMd});
        closeModal();
        await refreshLedgerItems();
        const uid = String(res?.uid||"").trim();
        if(uid){
          State.ledger.activeUid = uid;
          State.ledger.editor.dirtyPis = false;
          State.ledger.editor.dirtyMeta = false;
          await refreshLedgerActive();
        }
        renderLedgerBacklogView();
        showToast(res?.existing ? "Existing ledger item opened." : "Ledger item created.");
      }catch(e){
        showGlobalError("Ledger create failed", e?.message||String(e));
      }
    });
    try{ t?.focus(); }catch(_e){}
  });
}

async function _ledgerSelectUid(uid){
  const u = String(uid||"").trim();
  if(!u) return;
  if(String(State.ledger.activeUid||"") === u && State.ledger.activeItem) return;
  State.ledger.activeUid = u;
  State.ledger.activeItem = null;
  State.ledger.editor.dirtyPis = false;
  State.ledger.editor.dirtyMeta = false;
  State.ledger.editor.pisDraft = "";
  State.ledger.editor.metaDraft = null;
  try{
    await refreshLedgerActive();
  }catch(e){
    showGlobalError("Ledger load failed", e?.message||String(e));
  }
}

function renderLedgerBacklogView(){
  const root = q("#view-backlog");
  if(!root) return;

  // Kick lazy load
  if(!State.ledger.items){
    refreshLedgerItems().then(()=>renderLedgerBacklogView()).catch(()=>{});
  }

  const doc = State.ledger.items || {items:[]};
  const raw = Array.isArray(doc.items) ? doc.items : [];
  const items = _ledgerFilterItems(raw);

  const active = String(State.ledger.activeUid||"").trim();
  const activeItem = State.ledger.activeItem;
  const meta = activeItem && activeItem.meta ? activeItem.meta : null;

  const status = String(meta?.status || "");
  const title = String(meta?.title || "");
  const tags = Array.isArray(meta?.tags) ? meta.tags.map(x=>String(x||"")).join(", ") : "";
  const changesetCsv = Array.isArray(meta?.changesets) ? meta.changesets.map(x=>String(x||"")).join(", ") : "";
  const release = String(meta?.release_state || "draft");
  const supersedes = String(meta?.supersedes_uid || "");
  const regressionOf = String(meta?.regression_of_uid || "");
  const actions = Array.isArray(meta?.actions) ? meta.actions : [];
  const reqEvidence = Array.isArray(meta?.required_evidence) ? meta.required_evidence : [];
  const changesets = Array.isArray(meta?.changesets) ? meta.changesets : [];
  const updated = String(meta?.updated_utc || "");
  const created = String(meta?.created_utc || "");
  const commitTemplate = `${String(active || "").trim()}: ${String(title || "").trim() || "(title)"}`;
  const wfSteps = ["draft", "ready_for_implement", "in_progress", "ready_for_assure", "verified", "done"];
  const wfCur = wfSteps.indexOf(String(status || "").toLowerCase());
  const pisDraft = (State.ledger.editor && typeof State.ledger.editor.pisDraft === "string") ? State.ledger.editor.pisDraft : String(activeItem?.pis_markdown || "");
  const badgeCls = _ledgerStatusBadgeClass(status);

  root.innerHTML = `
    <div class="card">
      <div class="row" style="justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap;">
        <div class="col" style="gap:2px;">
          <div class="h2">Change Ledger</div>
          <div class="muted">Repo-local change records (governance/ledger/patch_instruction_sheets)</div>
        </div>
        <div class="row" style="gap:8px; flex-wrap:wrap;">
          <button id="lgRefresh" class="btn">Refresh</button>
          <button id="lgNew" class="btn primary">New</button>
        </div>
      </div>
    </div>

    <div class="row" style="gap:12px; align-items:stretch; margin-top:10px; flex-wrap:wrap;">
      <div class="card" style="flex: 1 1 340px; max-width: 520px; min-width:280px;">
        <div class="row" style="gap:8px; flex-wrap:wrap; align-items:center;">
          <input id="lgSearch" class="input" style="flex:1; min-width:160px;" placeholder="Search title/uid/tag" value="${escapeHtml(String(State.ledger.ui.search||""))}"/>
          <select id="lgStatus" class="select" style="width:150px;">
            ${["ALL","draft","deferred","ready_for_implement","in_progress","ready_for_assure","ready","in_integration","implemented","verified","done","blocked","abandoned","reopened","deprecated"].map(s=>{
              const val = s;
              const lbl = (s==="ALL") ? "ALL" : s;
              const sel = (String(State.ledger.ui.status||"ALL").toUpperCase()===String(val).toUpperCase()) ? "selected" : "";
              return `<option value="${escapeHtml(val)}" ${sel}>${escapeHtml(lbl)}</option>`;
            }).join("")}
          </select>
        </div>
        <div class="row" style="gap:8px; flex-wrap:wrap; margin-top:8px;">
          <input id="lgTag" class="input" style="flex:1; min-width:160px;" placeholder="Filter tag contains..." value="${escapeHtml(String(State.ledger.ui.tag||""))}"/>
          <label class="row muted" style="gap:6px; align-items:center;">
            <input id="lgShowDep" type="checkbox" ${State.ledger.ui.showDeprecated===true ? "checked":""}/>
            show deprecated
          </label>
          <label class="row muted" style="gap:6px; align-items:center;">
            <input id="lgShowReop" type="checkbox" ${State.ledger.ui.showReopened!==false ? "checked":""}/>
            show reopened
          </label>
        </div>

        <div class="muted" style="margin-top:8px;">${items.length} items</div>

        <div id="lgList" style="margin-top:8px; max-height:60vh; overflow:auto; padding-right:4px;"></div>
      </div>

      <div class="card" style="flex: 2 1 560px; min-width:320px;">
        ${active ? `
          <div class="row" style="justify-content:space-between; gap:10px; flex-wrap:wrap;">
            <div class="col" style="gap:4px;">
              <div class="h3">${escapeHtml(title || active)}</div>
              <div class="muted mono">${escapeHtml(active)}</div>
              <div class="row" style="gap:8px; flex-wrap:wrap; align-items:center; margin-top:6px;">
                <span class="badge ${badgeCls}">${escapeHtml(status||"")}</span>
                <span class="muted">updated ${escapeHtml(updated||"")}</span>
                <span class="muted">created ${escapeHtml(created||"")}</span>
              </div>
            </div>
            <div class="row" style="gap:8px; flex-wrap:wrap; align-items:flex-start;">
              <button id="lgRevision" class="btn">Create revision</button>
            </div>
          </div>

          <div class="wf-lane" style="margin-top:12px;">
            ${wfSteps.map((st, idx)=>{
              const cls = (idx < wfCur) ? "done" : (idx === wfCur ? "active" : "");
              const num = String(idx + 1);
              return `<div class="wf-step ${cls}">
                <span class="wf-dot">${escapeHtml(num)}</span>
                <span class="wf-label">${escapeHtml(st)}</span>
              </div>`;
            }).join("")}
          </div>

          <div class="row" style="gap:12px; align-items:flex-start; margin-top:12px; flex-wrap:wrap;">
            <div class="col" style="min-width:260px; flex:1;">
              <div class="label">Status</div>
              <select id="lgStatusEdit" class="select" style="width:220px;">
                ${["draft","deferred","ready_for_implement","in_progress","ready_for_assure","ready","in_integration","implemented","verified","done","blocked","abandoned","reopened","deprecated"].map(s=>{
                  const sel = (String(status||"")===s) ? "selected" : "";
                  return `<option value="${escapeHtml(s)}" ${sel}>${escapeHtml(s)}</option>`;
                }).join("")}
              </select>
              <div class="label" style="margin-top:10px;">Title</div>
              <input id="lgTitleEdit" class="input" value="${escapeHtml(title)}"/>

              <div class="label" style="margin-top:10px;">Tags (comma-separated)</div>
              <input id="lgTagsEdit" class="input" value="${escapeHtml(tags)}"/>

              <div class="label" style="margin-top:10px;">Changesets (comma-separated IDs)</div>
              <input id="lgChangesetsEdit" class="input mono" value="${escapeHtml(changesetCsv)}"/>

              <div class="label" style="margin-top:10px;">Release state</div>
              <select id="lgReleaseEdit" class="select" style="width:220px;">
                ${["draft","candidate","released"].map(s=>{
                  const sel = (String(release||"draft")===s) ? "selected" : "";
                  return `<option value="${escapeHtml(s)}" ${sel}>${escapeHtml(s)}</option>`;
                }).join("")}
              </select>

              <div class="label" style="margin-top:10px;">Supersedes UID (optional)</div>
              <input id="lgSupersedesEdit" class="input" value="${escapeHtml(supersedes)}"/>

              <div class="label" style="margin-top:10px;">Regression of UID (optional)</div>
              <input id="lgRegressionEdit" class="input" value="${escapeHtml(regressionOf)}"/>

              <div class="row" style="gap:8px; margin-top:12px; flex-wrap:wrap;">
                <button id="lgSaveMeta" class="btn primary">Save meta</button>
                <button id="lgReload" class="btn">Reload</button>
                <button id="lgAdvance" class="btn">Advance Workflow</button>
                <button id="lgMarkVerified" class="btn">Mark VERIFIED (gated)</button>
                <button class="btn small" data-lg-req-role="builder">Request Builder</button>
                <button class="btn small" data-lg-req-role="assurer">Request Assurer</button>
                <button class="btn small" data-lg-req-role="generalist">Request Generalist</button>
              </div>
            </div>

            <div class="col" style="min-width:360px; flex:2;">
              <div class="row" style="justify-content:space-between; align-items:center; gap:8px;">
                <div class="label">PIS markdown</div>
                <div class="row" style="gap:8px;">
                  <button id="lgSavePis" class="btn primary">Save PIS</button>
                  <button id="lgRevertPis" class="btn">Revert</button>
                </div>
              </div>
              <textarea id="lgPisEdit" class="textarea mono" style="min-height:340px;">${escapeHtml(pisDraft||"")}</textarea>

              <div class="label" style="margin-top:10px;">Preview (read-only)</div>
              <pre class="mono-pre" style="max-height:35vh; overflow:auto; border:1px solid var(--line); border-radius:12px; padding:10px; background:rgba(0,0,0,0.06);">${escapeHtml(pisDraft||"")}</pre>

              <div class="label" style="margin-top:10px;">Actions</div>
              <div id="lgActionsPane" class="col" style="gap:8px;">
                ${actions.length ? actions.map(a=>{
                  const aid = String(a?.id||"").trim();
                  const lbl = String(a?.label||aid||"action");
                  return `<div class="card" style="margin:0; padding:10px;">
                    <div class="row" style="justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap;">
                      <div><span class="mono">${escapeHtml(aid)}</span> - ${escapeHtml(lbl)}</div>
                      <button class="btn small" data-lg-act-run="${escapeHtml(aid)}">Run</button>
                    </div>
                  </div>`;
                }).join("") : `<div class="muted">No actions configured in meta.actions</div>`}
              </div>

              <div class="label" style="margin-top:10px;">Required Evidence (for VERIFIED)</div>
              <pre class="mono-pre" style="max-height:22vh; overflow:auto; border:1px solid var(--line); border-radius:12px; padding:10px; background:rgba(0,0,0,0.06);">${escapeHtml(JSON.stringify(reqEvidence, null, 2))}</pre>
              <div id="lgEvidenceChecklist" class="muted" style="margin-top:8px;"></div>

              <div class="label" style="margin-top:10px;">Changesets</div>
              <pre id="lgChangesetsOut" class="mono-pre" style="max-height:18vh; overflow:auto; border:1px solid var(--line); border-radius:12px; padding:10px; background:rgba(0,0,0,0.06);">${escapeHtml(JSON.stringify(changesets, null, 2))}</pre>

              <div class="label" style="margin-top:10px;">Resume Work</div>
              <div class="row" style="gap:8px; flex-wrap:wrap;">
                <button class="btn small" data-lg-handoff-role="builder">Convert Builder Last Response</button>
                <button class="btn small" data-lg-handoff-role="assurer">Convert Assurer Last Response</button>
                <button class="btn small" data-lg-handoff-role="generalist">Convert Generalist Last Response</button>
              </div>
              <div id="lgResumeOut" class="muted mono" style="margin-top:8px;"></div>

              <div class="label" style="margin-top:10px;">Recent Action Jobs</div>
              <div class="row" style="gap:8px;">
                <button id="lgActionRefresh" class="btn small">Refresh Jobs</button>
              </div>
              <div id="lgActionJobsOut" class="col" style="max-height:28vh; overflow:auto; border:1px solid var(--line); border-radius:12px; padding:8px; background:rgba(0,0,0,0.06); gap:8px;"></div>
              <div class="label" style="margin-top:10px;">Action Log Viewer</div>
              <pre id="lgActionLogOut" class="mono-pre" style="max-height:20vh; overflow:auto; border:1px solid var(--line); border-radius:12px; padding:10px; background:rgba(0,0,0,0.06);"></pre>
              <div class="label" style="margin-top:10px;">Action Result Viewer</div>
              <pre id="lgActionResultOut" class="mono-pre" style="max-height:20vh; overflow:auto; border:1px solid var(--line); border-radius:12px; padding:10px; background:rgba(0,0,0,0.06);"></pre>

              <div class="label" style="margin-top:10px;">Git + Commits</div>
              <div class="row" style="gap:8px; flex-wrap:wrap;">
                <button id="lgCopyCommitTemplate" class="btn small">Copy Commit Template</button>
                <button id="lgRefreshCommits" class="btn small">Refresh Commits</button>
                <button id="lgRefreshGitStatus" class="btn small">Refresh Git</button>
              </div>
              <pre id="lgGitStatusOut" class="mono-pre" style="max-height:14vh; overflow:auto; border:1px solid var(--line); border-radius:12px; padding:10px; background:rgba(0,0,0,0.06);"></pre>
              <div id="lgCommitsOut" class="col" style="max-height:18vh; overflow:auto; border:1px solid var(--line); border-radius:12px; padding:8px; background:rgba(0,0,0,0.06); gap:8px;"></div>
            </div>
          </div>
        ` : `
          <div class="muted">Select an item on the left.</div>
        `}
      </div>
    </div>
  `;

  // Handlers
  safeOn(q("#lgSwitchLegacy", root), "click", ()=>{
    State.backlogMode = "legacy";
    renderBacklogView();
  });
  safeOn(q("#lgRefresh", root), "click", async ()=>{
    try{
      await refreshLedgerItems();
      if(State.ledger.activeUid) await refreshLedgerActive();
      renderLedgerBacklogView();
    }catch(e){ showGlobalError("Ledger refresh failed", e?.message||String(e)); }
  });
  safeOn(q("#lgNew", root), "click", ()=> openLedgerCreateModal());

  const search = q("#lgSearch", root);
  safeOn(search, "input", ()=>{
    State.ledger.ui.search = String(search.value||"");
    renderLedgerBacklogView();
  });

  const stSel = q("#lgStatus", root);
  safeOn(stSel, "change", ()=>{
    State.ledger.ui.status = String(stSel.value||"ALL");
    renderLedgerBacklogView();
  });

  const tag = q("#lgTag", root);
  safeOn(tag, "input", ()=>{
    State.ledger.ui.tag = String(tag.value||"");
    renderLedgerBacklogView();
  });

  const cbDep = q("#lgShowDep", root);
  safeOn(cbDep, "change", ()=>{
    State.ledger.ui.showDeprecated = !!cbDep.checked;
    renderLedgerBacklogView();
  });
  const cbRe = q("#lgShowReop", root);
  safeOn(cbRe, "change", ()=>{
    State.ledger.ui.showReopened = !!cbRe.checked;
    renderLedgerBacklogView();
  });

  // Render list
  const list = q("#lgList", root);
  if(list){
    list.innerHTML = items.map(it=>{
      const uid = String(it.uid||"");
      const title = String(it.title||uid);
      const status = String(it.status||"");
      const badge = _ledgerStatusBadgeClass(status);
      const upd = String(it.updated_utc||"");
      const tagStr = Array.isArray(it.tags) ? it.tags.map(x=>String(x||"")).join(", ") : "";
      const sel = (uid && uid === active) ? "style=\"border:2px solid var(--accent);\"" : "";
      return `
        <div class="card" ${sel} style="margin-top:8px; cursor:pointer;" data-lg-uid="${escapeHtml(uid)}">
          <div class="row" style="justify-content:space-between; gap:8px; align-items:flex-start;">
            <div class="col" style="gap:4px; min-width:200px;">
              <div class="row" style="gap:8px; align-items:center; flex-wrap:wrap;">
                <span class="badge ${badge}">${escapeHtml(status||"")}</span>
                <div class="mono muted" style="font-size:12px;">${escapeHtml(uid)}</div>
              </div>
              <div style="font-weight:600; margin-top:2px;">${escapeHtml(title)}</div>
              ${tagStr ? `<div class="muted" style="font-size:12px;">tags: ${escapeHtml(tagStr)}</div>` : ``}
            </div>
            <div class="muted" style="font-size:12px; white-space:nowrap;">${escapeHtml(upd||"")}</div>
          </div>
        </div>
      `;
    }).join("");

    // list click
    list.querySelectorAll("[data-lg-uid]").forEach(el=>{
      el.addEventListener("click", async ()=>{
        const uid = String(el.getAttribute("data-lg-uid")||"").trim();
        await _ledgerSelectUid(uid);
        renderLedgerBacklogView();
      });
    });

    // restore scroll
    try{ list.scrollTop = Number(State.ledger.ui.listScrollTop||0); }catch(_e){}
    list.addEventListener("scroll", ()=>{
      State.ledger.ui.listScrollTop = list.scrollTop || 0;
    });
  }

  // Active item handlers
  if(active){
    const st = q("#lgStatusEdit", root);
    safeOn(st, "change", async ()=>{
      try{
        const newSt = String(st.value||"").trim();
        await apiLedgerStatus(active, newSt, "");
        await refreshLedgerItems();
        await refreshLedgerActive();
        renderLedgerBacklogView();
        showToast("Status updated.");
      }catch(e){ showGlobalError("Status update failed", e?.message||String(e)); }
    });

    const btnRev = q("#lgRevision", root);
    safeOn(btnRev, "click", async ()=>{
      try{
        const res = await apiLedgerCreateRevision(active, {deprecate_old:true});
        const newUid = String(res?.uid||res?.new_uid||"").trim();
        await refreshLedgerItems();
        if(newUid){
          await _ledgerSelectUid(newUid);
        }else{
          await refreshLedgerActive();
        }
        renderLedgerBacklogView();
        showToast("Revision created.");
      }catch(e){ showGlobalError("Create revision failed", e?.message||String(e)); }
    });

    const btnReload = q("#lgReload", root);
    safeOn(btnReload, "click", async ()=>{
      try{
        State.ledger.editor.dirtyPis = false;
        State.ledger.editor.dirtyMeta = false;
        await refreshLedgerActive();
        renderLedgerBacklogView();
      }catch(e){ showGlobalError("Reload failed", e?.message||String(e)); }
    });

    safeOn(q("#lgMarkVerified", root), "click", async ()=>{
      try{
        const res = await apiLedgerMarkVerified(active);
        if(!res || !res.ok){
          const why = String(res?.reason || "");
          const miss = Array.isArray(res?.missing) ? res.missing.join(", ") : "";
          showToast("Mark VERIFIED blocked", (why + (miss ? " :: " + miss : "")) || "required evidence missing");
          return;
        }
        await refreshLedgerActive();
        await refreshLedgerItems();
        renderLedgerBacklogView();
        showToast("Status updated to verified.");
      }catch(e){
        showGlobalError("Mark VERIFIED failed", e?.message||String(e));
      }
    });

    safeOn(q("#lgAdvance", root), "click", async ()=>{
      try{
        const note = window.prompt("Advance note (optional):", "workflow_advance_ui") || "";
        const res = await apiLedgerAdvance(active, {note: String(note||"")});
        if(!res || !res.ok){
          const why = String(res?.reason || "advance blocked");
          const miss = Array.isArray(res?.missing) ? res.missing.join(", ") : "";
          showToast("Advance blocked", (why + (miss ? " :: " + miss : "")));
          return;
        }
        await refreshLedgerActive();
        await refreshLedgerItems();
        renderLedgerBacklogView();
        showToast("Workflow advanced", `${String(res?.current_status||"?")} -> ${String(res?.status||res?.next_status||"?")}`);
      }catch(e){
        showGlobalError("Advance failed", e?.message || String(e));
      }
    });

    qa("[data-lg-req-role]", root).forEach(btn=>{
      safeOn(btn, "click", async ()=>{
        try{
          const toRole = String(btn.getAttribute("data-lg-req-role") || "").trim();
          const nextAction = window.prompt("Required next action:", "Continue with the next workflow step.") || "";
          const sess = await apiAgentsSession(toRole, {type: "ledger_uid", id: active});
          const latestHandoffPath = String(sess?.resume?.latest_handoff?.path || "").trim();
          const latestHandoffNext = String(sess?.resume?.latest_handoff?.next_action || "").trim();
          const msg = [
            `LEDGER_UID: ${active}`,
            `STATUS: ${status || "unknown"}`,
            `LATEST_HANDOFF: ${latestHandoffPath || "(none)"}`,
            `NEXT_ACTION: ${nextAction || "(not provided)"}`,
            `LATEST_HANDOFF_NEXT_ACTION: ${latestHandoffNext || "(none)"}`,
            "Please pick this up and respond with a HANDOFF-ready summary.",
          ].join("\\n");
          await apiAgentsSendTo({
            from_role: "generalist",
            to_role: toRole,
            binding: {type: "ledger_uid", id: active},
            message: msg,
            also_enqueue: true,
            request_context: {
              ledger_uid: active,
              status: status || "unknown",
              latest_handoff_path: latestHandoffPath,
              required_next_action: nextAction || "",
            },
          });
          showToast("Agent request sent", `to ${toRole}`);
        }catch(e){
          showGlobalError("Agent request failed", e?.message || String(e));
        }
      });
    });

    const pis = q("#lgPisEdit", root);
    safeOn(pis, "input", ()=>{
      State.ledger.editor.pisDraft = String(pis.value||"");
      State.ledger.editor.dirtyPis = true;
    });

    safeOn(q("#lgRevertPis", root), "click", ()=>{
      State.ledger.editor.dirtyPis = false;
      State.ledger.editor.pisDraft = String(activeItem?.pis_markdown || "");
      renderLedgerBacklogView();
    });

    safeOn(q("#lgSavePis", root), "click", async ()=>{
      try{
        const md = String(State.ledger.editor.pisDraft||"");
        await apiLedgerUpdatePis(active, md);
        State.ledger.editor.dirtyPis = false;
        await refreshLedgerActive();
        await refreshLedgerItems();
        renderLedgerBacklogView();
        showToast("PIS saved.");
      }catch(e){ showGlobalError("Save PIS failed", e?.message||String(e)); }
    });

    safeOn(q("#lgSaveMeta", root), "click", async ()=>{
      try{
        const titleEl = q("#lgTitleEdit", root);
        const tagsEl = q("#lgTagsEdit", root);
        const csEl = q("#lgChangesetsEdit", root);
        const relEl = q("#lgReleaseEdit", root);
        const supEl = q("#lgSupersedesEdit", root);
        const regEl = q("#lgRegressionEdit", root);
        const body = {
          title: String(titleEl?.value||"").trim(),
          tags: String(tagsEl?.value||"").split(",").map(x=>String(x||"").trim()).filter(Boolean),
          changesets: String(csEl?.value||"").split(",").map(x=>String(x||"").trim()).filter(Boolean),
          release_state: String(relEl?.value||"draft").trim(),
          supersedes_uid: String(supEl?.value||"").trim() || null,
          regression_of_uid: String(regEl?.value||"").trim() || null,
        };
        await apiLedgerUpdateMeta(active, body);
        State.ledger.editor.dirtyMeta = false;
        await refreshLedgerActive();
        await refreshLedgerItems();
        renderLedgerBacklogView();
        showToast("Meta saved.");
      }catch(e){ showGlobalError("Save meta failed", e?.message||String(e)); }
    });

    const jobsOut = q("#lgActionJobsOut", root);
    const logOut = q("#lgActionLogOut", root);
    const resultOut = q("#lgActionResultOut", root);
    const resumeOut = q("#lgResumeOut", root);
    const evidenceOut = q("#lgEvidenceChecklist", root);
    const commitsOut = q("#lgCommitsOut", root);
    const gitOut = q("#lgGitStatusOut", root);
    let _jobsCache = [];

    async function _refreshResume(){
      if(!resumeOut) return;
      try{
        const roles = ["builder", "assurer", "generalist"];
        const sessions = await Promise.all(roles.map((r)=>apiAgentsSession(r, {type: "ledger_uid", id: active}).catch(()=>null)));
        const byRole = [];
        for(let i=0;i<roles.length;i++){
          const role = roles[i];
          const s = sessions[i] && typeof sessions[i] === "object" ? sessions[i] : {};
          const h = s?.resume?.latest_handoff || {};
          const f = s?.resume?.latest_failed_action || {};
          byRole.push({
            role,
            handoffPath: String(h?.path || ""),
            nextAction: String(h?.next_action || ""),
            failedLog: String(f?.log_path || ""),
          });
        }
        const latest = byRole
          .filter((x)=>x.handoffPath)
          .sort((a,b)=>String(a.handoffPath).localeCompare(String(b.handoffPath)))
          .slice(-1)[0];
        const latestFail = byRole
          .filter((x)=>x.failedLog)
          .sort((a,b)=>String(a.failedLog).localeCompare(String(b.failedLog)))
          .slice(-1)[0];

        const chunks = [];
        if(latest && latest.handoffPath){
          const qh = new URLSearchParams({role: latest.role, ledger_uid: active, kind: "latest_handoff"}).toString();
          chunks.push(`latest_handoff: <a target="_blank" href="/api/agents/artifact?${qh}">${escapeHtml(String(latest.handoffPath).split("/").slice(-1)[0])}</a>`);
          if(latest.nextAction) chunks.push(`next_action: ${escapeHtml(latest.nextAction)}`);
        }else{
          chunks.push("latest_handoff: (none)");
        }
        if(latestFail && latestFail.failedLog){
          const qf = new URLSearchParams({role: latestFail.role, ledger_uid: active, kind: "latest_failed_action_log"}).toString();
          chunks.push(`latest_failed_action_log: <a target="_blank" href="/api/agents/artifact?${qf}">${escapeHtml(String(latestFail.failedLog).split("/").slice(-1)[0])}</a>`);
        }else{
          chunks.push("latest_failed_action_log: (none)");
        }
        resumeOut.innerHTML = chunks.join("<br/>");
      }catch(e){
        resumeOut.textContent = "resume_error: " + String(e?.message || e);
      }
    }

    function _renderEvidenceChecklist(){
      if(!evidenceOut) return;
      try{
        const req = Array.isArray(reqEvidence) ? reqEvidence : [];
        if(!req.length){
          evidenceOut.innerHTML = "<span class='muted'>No required evidence.</span>";
          return;
        }
        const lines = req.map((ev)=>{
          const raw = String(ev || "");
          if(raw.startsWith("action_success:")){
            const aid = raw.slice("action_success:".length);
            const ok = _jobsCache.some((j)=>String(j?.event||"")==="finished" && String(j?.action_id||"")===aid && Number(j?.exit_code)===0);
            return `${ok ? "OK" : "PENDING"} - ${escapeHtml(raw)}`;
          }
          return `UNKNOWN - ${escapeHtml(raw)}`;
        });
        evidenceOut.innerHTML = `<pre class="mono-pre" style="max-height:14vh; overflow:auto; border:1px solid var(--line); border-radius:12px; padding:10px; background:rgba(0,0,0,0.04);">${lines.join("\n")}</pre>`;
      }catch(e){
        evidenceOut.textContent = "evidence_error: " + String(e?.message || e);
      }
    }

    function _renderJobCards(rows){
      if(!jobsOut) return;
      const list = Array.isArray(rows) ? rows.slice().reverse() : [];
      if(!list.length){
        jobsOut.innerHTML = "<div class='muted'>No action jobs.</div>";
        _renderEvidenceChecklist();
        return;
      }
      jobsOut.innerHTML = list.map((j)=>{
        const jid = String(j?.job_id || "");
        const aid = String(j?.action_id || "");
        const ev = String(j?.event || "");
        const st = String(j?.status || "");
        const rc = String(j?.exit_code ?? "");
        return `<div class="card" style="margin:0; padding:10px;">
          <div class="mono" style="font-size:12px;">${escapeHtml(jid)}</div>
          <div class="muted" style="font-size:12px;">action=${escapeHtml(aid)} event=${escapeHtml(ev)} status=${escapeHtml(st)} rc=${escapeHtml(rc)}</div>
          <div class="row" style="gap:8px; margin-top:8px; flex-wrap:wrap;">
            <button class="btn small" data-lg-view-log="${escapeHtml(jid)}">View Log</button>
            <button class="btn small" data-lg-view-result="${escapeHtml(jid)}">View Result</button>
            <button class="btn small" data-lg-cancel-job="${escapeHtml(jid)}">Cancel</button>
            <button class="btn small danger" data-lg-force-job="${escapeHtml(jid)}">Force Cancel</button>
          </div>
        </div>`;
      }).join("");

      qa("[data-lg-view-log]", jobsOut).forEach((btn)=>{
        safeOn(btn, "click", async ()=>{
          const jid = String(btn.getAttribute("data-lg-view-log") || "").trim();
          if(!jid || !logOut) return;
          logOut.textContent = "[loading]";
          try{
            const out = await apiLedgerActionLog(active, jid, 200);
            logOut.textContent = `job=${jid}\nlog_path=${String(out?.log_path || "")}\n---\n${Array.isArray(out?.lines) ? out.lines.join("\n") : ""}`;
          }catch(e){
            logOut.textContent = "error: " + String(e?.message || e);
          }
        });
      });

      qa("[data-lg-view-result]", jobsOut).forEach((btn)=>{
        safeOn(btn, "click", ()=>{
          const jid = String(btn.getAttribute("data-lg-view-result") || "").trim();
          if(!jid || !resultOut) return;
          const row = _jobsCache.find((x)=>String(x?.job_id || "") === jid);
          resultOut.textContent = JSON.stringify(row || {}, null, 2);
        });
      });

      qa("[data-lg-cancel-job]", jobsOut).forEach((btn)=>{
        safeOn(btn, "click", async ()=>{
          const jid = String(btn.getAttribute("data-lg-cancel-job") || "").trim();
          if(!jid) return;
          try{
            await apiLedgerActionCancel(active, jid);
            showToast("Cancel requested", jid);
            _refreshActionJobs().catch(()=>{});
          }catch(e){
            showGlobalError("Cancel failed", e?.message || String(e));
          }
        });
      });

      qa("[data-lg-force-job]", jobsOut).forEach((btn)=>{
        safeOn(btn, "click", async ()=>{
          const jid = String(btn.getAttribute("data-lg-force-job") || "").trim();
          if(!jid) return;
          try{
            await apiLedgerActionForceCancel(active, jid);
            showToast("Force cancel requested", jid);
            _refreshActionJobs().catch(()=>{});
          }catch(e){
            showGlobalError("Force cancel failed", e?.message || String(e));
          }
        });
      });
      _renderEvidenceChecklist();
    }

    async function _refreshActionJobs(){
      if(!jobsOut) return;
      try{
        const stx = await apiLedgerActionStatus(active, "", 40);
        _jobsCache = Array.isArray(stx?.jobs) ? stx.jobs : [];
        _renderJobCards(_jobsCache);
      }catch(e){
        jobsOut.innerHTML = "<div class='muted'>error: " + escapeHtml(String(e?.message || e)) + "</div>";
      }
    }
    safeOn(q("#lgActionRefresh", root), "click", _refreshActionJobs);
    _refreshActionJobs().catch(()=>{});

    async function _refreshCommits(){
      if(!commitsOut) return;
      commitsOut.innerHTML = "<div class='muted'>loading commits...</div>";
      try{
        const rows = await apiLedgerCommits(active, 20);
        if(!Array.isArray(rows) || !rows.length){
          commitsOut.innerHTML = "<div class='muted'>No linked commits for this UID.</div>";
          return;
        }
        commitsOut.innerHTML = rows.map((c)=>{
          const sha = String(c?.sha || c?.hash || "").trim();
          const subj = String(c?.subject || c?.title || "").trim();
          const dt = String(c?.date || c?.utc || "").trim();
          const shortSha = sha ? sha.slice(0, 12) : "(unknown)";
          return `<div class="card" style="margin:0; padding:8px;">
            <div class="mono">${escapeHtml(shortSha)}</div>
            <div>${escapeHtml(subj || "(no subject)")}</div>
            <div class="muted" style="font-size:12px;">${escapeHtml(dt)}</div>
          </div>`;
        }).join("");
      }catch(e){
        commitsOut.innerHTML = "<div class='muted'>error: " + escapeHtml(String(e?.message || e)) + "</div>";
      }
    }

    async function _refreshGitStatus(){
      if(!gitOut) return;
      const fallback = `HEAD: ${"(unknown)"}\ndirty: unknown`;
      gitOut.textContent = fallback;
      try{
        const st = await apiGitStatus();
        const porcelain = String(st?.status_porcelain || "");
        const dirty = porcelain.trim().length > 0;
        gitOut.textContent = [
          `branch: ${String(st?.branch || "")}`,
          `head: ${String(st?.head || "")}`,
          `describe: ${String(st?.describe || "")}`,
          `dirty: ${dirty ? "yes" : "no"}`,
        ].join("\n");
      }catch(_e){}
    }
    safeOn(q("#lgRefreshCommits", root), "click", _refreshCommits);
    safeOn(q("#lgRefreshGitStatus", root), "click", _refreshGitStatus);
    _refreshCommits().catch(()=>{});
    _refreshGitStatus().catch(()=>{});

    safeOn(q("#lgCopyCommitTemplate", root), "click", async ()=>{
      const ok = await copyTextToClipboard(commitTemplate);
      showToast("Commit template", ok ? "Copied to clipboard" : "Copy failed");
    });

    qa("[data-lg-handoff-role]", root).forEach((btn)=>{
      safeOn(btn, "click", async ()=>{
        try{
          const role = String(btn.getAttribute("data-lg-handoff-role") || "").trim();
          if(!role) return;
          const title = `${role} handoff for ${active}`;
          const nextAction = window.prompt("NEXT_ACTION (single imperative):", "Continue with the next workflow step.") || "";
          const out = await apiAgentsHandoff({
            role,
            binding: {type: "ledger_uid", id: active},
            selection: {mode: "last_n", n: 1},
            title,
            status: "READY",
            scope: "Converted from latest assistant response for workflow handoff",
            inputs: ["latest assistant response"],
            outputs: ["handoff artifact markdown"],
            next_action: String(nextAction || "").trim(),
            verify: [],
            notes: "Generated from the ledger view.",
          });
          showToast("Handoff created", String(out?.artifact_path || ""));
          _refreshResume().catch(()=>{});
        }catch(e){
          showGlobalError("Create handoff failed", e?.message || String(e));
        }
      });
    });

    _refreshResume().catch(()=>{});

    qa("[data-lg-act-run]", root).forEach(btn=>{
      safeOn(btn, "click", async ()=>{
        try{
          const aid = String(btn.getAttribute("data-lg-act-run") || "").trim();
          if(!aid) return;
          const tidRaw = window.prompt("Optional TASK_ID (3 chars):", "") || "";
          const task_id = String(tidRaw).trim().toLowerCase();
          const res = await apiLedgerActionRun({uid: active, action_id: aid, task_id});
          showToast("Action started", `${aid} job=${String(res?.job_id||"")}`);
          _refreshActionJobs().catch(()=>{});
        }catch(e){
          showGlobalError("Action run failed", e?.message || String(e));
        }
      });
    });
  }

  // Render signature for refresh loop
  try{ State.ledger.itemsRenderedSig = String(State.ledger.itemsSig||""); }catch(_e){}
}
