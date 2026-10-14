window.__EIEL_APP_CORE_PART4_LOADED__ = true;
function renderBacklogView(){
  const root = q("#view-backlog");
  if(!root) return;
  renderLedgerBacklogView();
  return;
  const doc = State.patchBacklog;
  const rawItems = (doc && Array.isArray(doc.items)) ? doc.items : [];
  const items = backlogSortedItems(rawItems);
  root.innerHTML = `
    <div class="card">
      <div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap;">
        <div>
          <div class="title">Ledger</div>
          <div class="muted">Items: ${escapeHtml(String(items.length))} ${State.lastPatchBacklogUtc ? `(updated ${escapeHtml(State.lastPatchBacklogUtc)})` : ""}</div>
        </div>
        <div class="row" style="gap:8px;align-items:center;flex-wrap:wrap;justify-content:flex-end;">
          <span class="muted" id="backlogSelCount">Selected: 0</span>
          <button class="btn small" id="backlogSendSelected" disabled>Send selected to GPT</button>
          <button class="btn small" id="backlogClearSelected" disabled>Clear selection</button>
          <button class="btn small" id="backlogRepair" data-tt="backlog_repair">Repair IDs/Titles</button>
          <button class="btn small" id="backlogSwitchLedger">Ledger</button>
          <button class="btn small" id="backlogAdd">Add</button>
          <button class="btn small" id="backlogRefresh">Refresh</button>
        </div>
      </div>
      <div id="backlogList" style="margin-top:10px;"></div>
    </div>`;
  const list = q("#backlogList", root);

  const _updateSelectionUi = ()=>{
    try{
      const n = State.backlogSelections ? State.backlogSelections.size : 0;
      const sc = q("#backlogSelCount", root);
      if(sc) sc.textContent = `Selected: ${n}`;
      const btnSend = q("#backlogSendSelected", root);
      const btnClr = q("#backlogClearSelected", root);
      if(btnSend) btnSend.disabled = !(n > 0);
      if(btnClr) btnClr.disabled = !(n > 0);
    }catch(_e){}
  };

  // Ensure state exists.
  try{ if(!State.backlogSelections) State.backlogSelections = new Set(); }catch(_e){}
  _updateSelectionUi();
  if(list){
    if(!items.length){
      list.innerHTML = `<div class="muted">No backlog items.</div>`;
    }else{
      list.innerHTML = items.map((it, idx)=> {
        const rawId = String(it.id || it.uid || "");
        const id = escapeHtml(rawId);
        const title = escapeHtml(String(it.title || ""));
        const stNormRaw = backlogNormalizeStatus(it.status || "planned");
        const st = escapeHtml(String(stNormRaw));
        const tags = Array.isArray(it.tags) ? it.tags.map(x=>escapeHtml(String(x))).join(", ") : "";
        const md = escapeHtml(String(it.pis_markdown || ""));
        const isBlocked = String(stNormRaw||"").toLowerCase()==="blocked";
        const isDone = (String(stNormRaw||"") === "implemented" || String(stNormRaw||"") === "verified");
        const badgeClass = escapeHtml(isBlocked ? "b-fail" : "b-unknown");
        return `<div class="card" style="margin-top:8px;" data-bl-key="${escapeHtml(encodeURIComponent(rawId))}">
          <div class="row" style="justify-content:space-between;gap:10px;flex-wrap:wrap; align-items:flex-start;">
            <div class="bl-item-main ${isDone ? "bl-item-done" : ""}" style="min-width:220px;">
              <div class="mono">${id}</div>
              <div style="font-weight:600;">${title}</div>
              ${tags ? `<div class="muted" style="margin-top:6px;">${tags}</div>` : ``}
            </div>
            <div class="row" style="gap:8px;align-items:center;flex-wrap:wrap; justify-content:flex-end;">
              <label class="muted" style="display:flex;align-items:center;gap:6px;">
                <input type="checkbox" data-bl-action="select" style="width:16px;height:16px;"/>
                <span>Select</span>
              </label>
              <span class="badge ${badgeClass}">${escapeHtml(backlogStatusLabel(stNormRaw))}</span>
              <select class="input" style="height:34px; padding:4px 8px;" data-bl-action="status">
                ${BACKLOG_STATUS_OPTIONS.map(o=>`<option value="${escapeHtml(o.value)}" ${String(stNormRaw)===o.value?"selected":""}>${escapeHtml(o.label)}</option>`).join("")}
              </select>
              <button class="btn small" data-bl-action="toggle">View</button>
              <button class="btn small" data-bl-action="copy">Copy</button>
              <button class="btn small" data-bl-action="download">Download</button>
              <button class="btn small danger" data-bl-action="delete">Delete</button>
            </div>
          </div>
          <div class="muted" style="margin-top:6px; display:none;" data-bl-md-wrap>
            <pre class="mono-pre" style="max-height:45vh; overflow:auto; border:1px solid var(--line); border-radius:12px; padding:10px; background:rgba(0,0,0,0.06);">${md}</pre>
          </div>
        </div>`;
      }).join("");
      // Restore expanded "View" panels + internal scroll positions across refresh re-renders.
      try{
        const ui = State.backlogUi || {expandedIds:new Set(), scrollTops:{}};
        State.backlogUi = ui;
        const expanded = ui.expandedIds || new Set();
        const scrollTops = ui.scrollTops || {};
        // Prune expansions for items that no longer exist.
        const present = new Set();
        list.querySelectorAll("[data-bl-key]").forEach((card)=>{
          const key = card.getAttribute("data-bl-key") || "";
          const rawId = key ? decodeURIComponent(String(key)) : "";
          if(rawId) present.add(rawId);
        });
        expanded.forEach((id)=>{ if(!present.has(id)) expanded.delete(id); });
        // Apply restored state + wire scroll listeners.
        list.querySelectorAll("[data-bl-key]").forEach((card)=>{
          const key = card.getAttribute("data-bl-key") || "";
          const rawId = key ? decodeURIComponent(String(key)) : "";
          const wrap = card.querySelector("[data-bl-md-wrap]");
          const pre = wrap ? wrap.querySelector("pre") : null;
          const sel = card.querySelector("input[type=checkbox][data-bl-action=select]");
          if(wrap && expanded.has(rawId)) wrap.style.display = "block";
          if(sel){
            try{ sel.checked = !!(State.backlogSelections && State.backlogSelections.has(rawId)); }catch(_e){}
          }
          if(pre){
            // Restore scrollTop if we have it.
            const st = scrollTops[rawId];
            if(Number.isFinite(st)) pre.scrollTop = st;
            safeOn(pre, "scroll", ()=>{
              try{ scrollTops[rawId] = pre.scrollTop; }catch(_e){}
            });
          }
        });
      }catch(_e){}

    }

  // Per-item controls (status/edit/delete/view/export)
  const _getItems = ()=> (State.patchBacklog && Array.isArray(State.patchBacklog.items)) ? State.patchBacklog.items.slice() : [];
  const _findIdx = (arr, id)=> arr.findIndex(x=>String(x.id||x.uid||"")===String(id||""));
  if(list){
    safeOn(list, "click", async (ev)=>{
      const t = ev.target;
      if(!t) return;
      const act = t.getAttribute && t.getAttribute("data-bl-action");
      if(!act) return;
      const card = t.closest && t.closest("[data-bl-key]");
      const key = card ? card.getAttribute("data-bl-key") : "";
      const rawId = key ? decodeURIComponent(String(key)) : "";
            const itemsNow = _getItems();
      const i = _findIdx(itemsNow, rawId);
      if(i<0) return;
      const it = itemsNow[i];

      if(act === "toggle"){
        const wrap = card.querySelector("[data-bl-md-wrap]");
        if(wrap) wrap.style.display = (wrap.style.display==="none"||!wrap.style.display) ? "block" : "none";
        return;
      }
      if(act === "copy"){
        const md = String(it.pis_markdown || "");
        try{
          const ok = await copyTextToClipboard(md);
          if(!ok) throw new Error("Clipboard blocked");
          showToast("Copied", "PIS markdown copied to clipboard.");
        }catch(e){
          // fallback: show in a modal-like textarea
          openModal("Copy (manual)", `<textarea class="input" style="height:280px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;" readonly>${escapeHtml(md)}</textarea>`, ()=>{});
        }
        return;
      }
      if(act === "download"){
        const md = String(it.pis_markdown || "");
        const fn = `${String(it.id||it.uid||"backlog-item")}.md`;
        downloadTextFile(fn, md);
        return;
      }
      if(act === "delete"){
        if(!confirm(`Delete backlog item ${String(it.id||it.uid||"")}?`)) return;
        itemsNow.splice(i,1);
        try{
          await _savePatchBacklog({items: itemsNow});
          renderBacklogView();
        }catch(e){
          showToast("Delete failed", e.message||String(e));
        }
        return;
      }
    });

    safeOn(list, "change", async (ev)=>{
      const t = ev.target;
      if(!t) return;
      const act = t.getAttribute && t.getAttribute("data-bl-action");
      if(act !== "status" && act !== "select") return;
      const card = t.closest && t.closest("[data-bl-key]");
      const key = card ? card.getAttribute("data-bl-key") : "";
      const rawId = key ? decodeURIComponent(String(key)) : "";

      if(act === "select"){
        try{
          if(!State.backlogSelections) State.backlogSelections = new Set();
          if(!!t.checked) State.backlogSelections.add(rawId);
          else State.backlogSelections.delete(rawId);
        }catch(_e){}
        // Update toolbar label without full re-render.
        try{
          const n = State.backlogSelections ? State.backlogSelections.size : 0;
          const sc = q("#backlogSelCount", root);
          if(sc) sc.textContent = `Selected: ${n}`;
          const btnSend = q("#backlogSendSelected", root);
          const btnClr = q("#backlogClearSelected", root);
          if(btnSend) btnSend.disabled = !(n > 0);
          if(btnClr) btnClr.disabled = !(n > 0);
        }catch(_e){}
        return;
      }

            const itemsNow = _getItems();
      const i = _findIdx(itemsNow, rawId);
      if(i<0) return;
      itemsNow[i] = Object.assign({}, itemsNow[i], {
        status: backlogNormalizeStatus(String(t.value||"planned")),
        updated_utc: new Date().toISOString(),
      });
      try{
        await _savePatchBacklog({items: itemsNow});
        renderBacklogView();
      }catch(e){
        showToast("Status update failed", e.message||String(e));
      }
    });
  }

  }
  safeOn(q("#backlogRefresh"), "click", async ()=>{ try{ await refreshPatchBacklog(); renderBacklogView(); }catch(e){ showToast("Refresh failed", e.message||String(e)); } });
    safeOn(q("#backlogSwitchLedger"), "click", ()=>{ State.backlogMode = "ledger"; renderBacklogView(); });
safeOn(q("#backlogAdd"), "click", ()=>{ openBacklogAddModal(); });
  safeOn(q("#backlogRepair"), "click", async ()=>{
    try{
      const cur = State.patchBacklog && Array.isArray(State.patchBacklog.items) ? State.patchBacklog.items : [];
      const res = _repairBacklogItems(cur);
      // Update selection keys if ids changed.
      try{
        if(res.id_map && State.backlogSelections && State.backlogSelections.size){
          const next = new Set();
          for(const k of State.backlogSelections){
            const kk = String(k||"");
            next.add(res.id_map[kk] || kk);
          }
          State.backlogSelections = next;
        }
      }catch(_e){}

      if(res.changed > 0){
        await _savePatchBacklog({items: res.items});
        showToast("Ledger repaired", `Updated ${res.changed} field(s).`);
      }else{
        showToast("Ledger ok", "No UID/title repairs needed.");
      }
      if(res.notes && res.notes.length){
        console.warn("[backlog][repair]", res.notes);
      }
      renderBacklogView();
    }catch(e){
      showToast("Repair failed", e.message||String(e));
    }
  });

  // Batch submit to GPT (admin endpoint).
  safeOn(q("#backlogClearSelected"), "click", ()=>{
    try{ if(State.backlogSelections) State.backlogSelections.clear(); }catch(_e){}
    renderBacklogView();
  });

  safeOn(q("#backlogSendSelected"), "click", async ()=>{
    try{
      const uids = State.backlogSelections ? Array.from(State.backlogSelections) : [];
      if(!uids.length){ showToast("Nothing selected", "Select at least one backlog item first."); return; }
      showToast("Submitting…", `Sending ${uids.length} item(s) to GPT…`);
      const res = await apiBacklogGptSubmit({uids});
      const archive = res && res.archive ? String(res.archive) : "";
      showToast("Submitted", `${uids.length} item(s) submitted${archive ? ` (archive ${archive})` : ""}.`);
      await refreshPatchBacklog();
      renderBacklogView();
    }catch(e){
      showToast("Submit failed", e.message||String(e));
    }
  });

  // Kick a lazy load if needed.
  if(!State.patchBacklog){ refreshPatchBacklog().then(()=>renderBacklogView()).catch(()=>{}); }
  // Mark current backlog signature as rendered, so background refresh doesn't constantly rebuild DOM.
  try{ State.patchBacklogRenderedSig = String(State.patchBacklogSig||""); }catch(_e){}
}


function _genPisUidAuto(){
  // Required format: PIS-YYYYMMDD-HHMMSSZ-RAND4-slug
  const d = new Date();
  const pad = (n)=>String(n).padStart(2,"0");
  const y = d.getUTCFullYear();
  const mo = pad(d.getUTCMonth()+1);
  const da = pad(d.getUTCDate());
  const hh = pad(d.getUTCHours());
  const mm = pad(d.getUTCMinutes());
  const ss = pad(d.getUTCSeconds());
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
  let r = "";
  for(let i=0;i<4;i++) r += chars[Math.floor(Math.random()*chars.length)];
  const slug = "backlog-auto-generated";
  return `PIS-${y}${mo}${da}-${hh}${mm}${ss}Z-${r}-${slug}`;
}

function _persistBacklogDraft(md){
  try{
    State.backlogDraft.md = String(md||"");
    State.backlogDraft.lastSavedUtc = new Date().toISOString();
    localStorage.setItem("patchBacklogDraftMd", State.backlogDraft.md);
    localStorage.setItem("patchBacklogDraftUtc", State.backlogDraft.lastSavedUtc);
  }catch(_e){}
}

function _loadBacklogDraft(){
  try{
    const md = localStorage.getItem("patchBacklogDraftMd");
    const utc = localStorage.getItem("patchBacklogDraftUtc");
    if(md !== null) State.backlogDraft.md = md;
    if(utc) State.backlogDraft.lastSavedUtc = utc;
  }catch(_e){}
}

function _extractPisUidFromMarkdown(md){
  const text = String(md||"");
  if(!text.trim()) return "";
  const lines = text.split(/\r?\n/);

  // Canonical UID pattern used across this repo.
  // Example: PIS-20260303-134101Z-16D0-backlog-auto-generated
  const UID_RE = /\b(PIS-\d{8}-\d{6}Z-[A-Z0-9]{4}-[A-Za-z0-9._-]+)\b/;

  // Scan only the first N lines (fast + stable).
  const N = Math.min(60, lines.length);
  for(let i=0;i<N;i++){
    const ln0 = String(lines[i]||"");
    const ln = ln0.trim();
    if(!ln) continue;

    // Common formats:
    // - UID: PIS-...
    // - **UID:** PIS-...
    // - # PIS-...
    // - PIS-...   (bare)
    let m = ln.match(/^(?:\*{1,2}\s*)?(?:UID|UUID)\s*:\s*(.+)$/i);
    if(m){
      const tail = String(m[1]||"");
      const mm = tail.match(UID_RE);
      if(mm) return String(mm[1]||"").trim();
    }

    // Headings / bare uid line.
    m = ln.match(/^\s*#+\s*(.+)$/);
    if(m){
      const mm = String(m[1]||"").match(UID_RE);
      if(mm) return String(mm[1]||"").trim();
    }

    // Plain line containing the UID (bare).
    const mm2 = ln.match(UID_RE);
    if(mm2 && mm2[1]){
      return String(mm2[1]).trim();
    }
  }
  return "";
}

function _extractPisTitleFromMarkdown(md){
  const text = String(md||"").trim();
  if(!text) return "";
  const lines = text.split(/\r?\n/);

  function isJunkTitle(s){
    const t = String(s||"").trim();
    if(!t) return true;
    const low = t.toLowerCase();
    if(low === "1) title" || low === "1. title" || low === "title") return true;
    if(low === "# patch instruction sheet (pis)" || low === "patch instruction sheet (pis)") return true;
    if(/^[-_=]{3,}\s*$/.test(t)) return true;
    return false;
  }

  // 1) Prefer explicit "Title" section markers.
  for(let i=0;i<lines.length;i++){
    const ln = String(lines[i]||"").trim();

    // Accept variants:
    // "1) Title", "1. Title", "## 1) Title"
    if(/^\s*(?:#{1,6}\s*)?\d+\s*[\)\.]\s*Title\s*$/i.test(ln)){
      for(let j=i+1;j<lines.length;j++){
        const cand = String(lines[j]||"").trim();
        if(!cand) continue;
        if(isJunkTitle(cand)) continue;
        return cand;
      }
    }
  }

  // 2) First meaningful markdown H1/H2 line.
  for(let i=0;i<lines.length;i++){
    const ln = String(lines[i]||"").trim();
    const m = ln.match(/^\s*#{1,3}\s+(.+)\s*$/);
    if(!m) continue;
    const cand = String(m[1]||"").trim();
    // Skip UID-like headings.
    if(/\bPIS-\d{8}-\d{6}Z-[A-Z0-9]{4}-/.test(cand)) continue;
    if(isJunkTitle(cand)) continue;
    return cand;
  }

  // 3) First non-empty non-UID non-separator line.
  for(let i=0;i<lines.length;i++){
    const ln = String(lines[i]||"").trim();
    if(!ln) continue;
    if(/^(?:\*{1,2}\s*)?(?:UID|UUID)\s*:/i.test(ln)) continue;
    if(/\bPIS-\d{8}-\d{6}Z-[A-Z0-9]{4}-/.test(ln) && ln.length < 120) continue;
    if(/^[-_=]{3,}\s*$/.test(ln)) continue;
    if(isJunkTitle(ln)) continue;
    return ln;
  }

  return "";
}

function _extractBacklogItemFromMarkdown(md){
  const text = String(md||"").trim();
  if(!text) return null;

  const id = _extractPisUidFromMarkdown(text) || _genPisUidAuto();
  const title = _extractPisTitleFromMarkdown(text) || id;

  return {
    id,
    title,
    status: "planned",
    pis_markdown: text,
    tags: [],
    created_utc: new Date().toISOString(),
    updated_utc: new Date().toISOString(),
  };
}



function _repairBacklogItems(items){
  // Returns {items, changed, id_map, notes}
  const out = Array.isArray(items) ? items.map(x=> (x && typeof x === "object") ? {...x} : x) : [];
  const used = new Set();
  for(const it of out){
    if(it && typeof it === "object"){
      const k = String(it.id || it.uid || "").trim();
      if(k) used.add(k);
    }
  }

  let changed = 0;
  const id_map = {}; // oldId -> newId
  const notes = [];

  for(const it of out){
    if(!it || typeof it !== "object") continue;

    const md = String(it.pis_markdown || "").trim();
    if(!md) continue;

    const uid = _extractPisUidFromMarkdown(md);
    const ttl = _extractPisTitleFromMarkdown(md);

    const curId = String(it.id || it.uid || "").trim();
    if(uid && curId && uid !== curId){
      if(!used.has(uid)){
        used.delete(curId);
        used.add(uid);
        id_map[curId] = uid;
        it.id = uid;
        changed++;
      }else{
        notes.push(`UID collision: cannot rename ${curId} -> ${uid}`);
      }
    }

    if(ttl){
      const curTitle = String(it.title || "").trim();
      if(ttl !== curTitle){
        it.title = ttl;
        changed++;
      }
    }

    if(changed){
      it.updated_utc = new Date().toISOString();
    }
  }

  return {items: out, changed, id_map, notes};
}

async function _savePatchBacklog(doc){
  const body = {
    schema_version: "patch_backlog_v1",
    items: Array.isArray(doc.items) ? doc.items : [],
  };
  await apiPatchBacklogSave(body);
  await refreshPatchBacklog();
}

function openBacklogAddModal(){
  openModal(
    "Add Ledger Item",
    `<div class="muted">Paste a full PIS markdown (or its key header). It will be stored in <span class="mono">runtime/state/webui/patch-backlog.json</span>.</div>
     <div style="margin-top:10px;">
       <div class="muted" style="margin-bottom:6px;">Load a .md file (optional)</div>
       <input id="blFile" class="input" type="file" multiple accept=".md,.txt,text/markdown,text/plain"/>
       <div class="muted" style="margin-top:6px;">Choose one file to load into the textbox, or select multiple files to import directly into the backlog list.</div>
     </div>
     <div style="margin-top:10px;">
       <textarea id="blMd" class="input" style="height:220px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;"></textarea>
     </div>
     <div class="row" style="margin-top:10px; gap:10px; justify-content:flex-end;">
       <button class="btn" id="blCancel">Cancel</button>
       <button class="btn ok" id="blSave">Save</button>
     </div>
     <div class="muted" id="blOut" style="margin-top:10px;"></div>`,
    async (modal)=>{
      const ta = q("#blMd", modal);
      const out = q("#blOut", modal);
      const fi = q("#blFile", modal);

      const _readFileIntoTextarea = (file)=>{
        try{
          if(!file){ return; }
          const maxBytes = 2 * 1024 * 1024;
          if(file.size && file.size > maxBytes){
            if(out) out.textContent = `File too large (${Math.round(file.size/1024)} KB). Limit is ~2MB.`;
            return;
          }
          const r = new FileReader();
          r.onload = ()=>{
            const txt = String(r.result || "");
            if(ta) ta.value = txt;
            _persistBacklogDraft(txt);
            if(out) out.textContent = `Loaded ${file.name || "file"}.`;
          };
          r.onerror = ()=>{ if(out) out.textContent = 'Could not read file.'; };
          r.readAsText(file);
        }catch(e){ if(out) out.textContent = 'Load failed: ' + String(e?.message||e); }
      };

      safeOn(fi, "change", async ()=>{
        try{
          const files = (fi && fi.files) ? Array.from(fi.files) : [];
          if(!files.length) return;

          // Single-file behavior: load into textarea for manual edit + Save.
          if(files.length === 1){
            _readFileIntoTextarea(files[0]);
            return;
          }

          // Multi-file import: create N backlog items and persist immediately.
          const cur = State.patchBacklog && Array.isArray(State.patchBacklog.items) ? State.patchBacklog.items.slice() : [];
          let added = 0;
          const maxBytes = 2 * 1024 * 1024;

          out.textContent = `Importing ${files.length} files…`;
          for(const f of files){
            try{
              if(!f) continue;
              if(f.size && f.size > maxBytes){
                // Skip oversized files but continue importing the rest.
                continue;
              }
              const text = String(await f.text());
              const item = _extractBacklogItemFromMarkdown(text);
              if(!item) continue;
              cur.push(item);
              added += 1;
            }catch(_e){
              // continue
            }
          }

          if(!added){
            out.textContent = "No valid files imported.";
            return;
          }

          await _savePatchBacklog({items: cur});
          out.textContent = `Imported ${added} items.`;
          closeModal();
          renderBacklogView();
        }catch(e){
          if(out) out.textContent = 'Import failed: ' + String(e?.message||e);
        }
      });

      // Desktop drag-and-drop (harmless on mobile).
      try{
        const dropTarget = ta;
        if(dropTarget){
          dropTarget.addEventListener('dragover', (ev)=>{ try{ ev.preventDefault(); }catch(_e){} });
          dropTarget.addEventListener('drop', (ev)=>{
            try{ ev.preventDefault(); }catch(_e){}
            const dt = ev.dataTransfer;
            const f = dt && dt.files && dt.files.length ? dt.files[0] : null;
            _readFileIntoTextarea(f);
          });
        }
      }catch(_e){}
      try{ if(ta && State.backlogDraft && State.backlogDraft.md){ ta.value = State.backlogDraft.md; } }catch(_e){}
      safeOn(ta, "input", ()=>{ _persistBacklogDraft(ta.value||""); });
      safeOn(q("#blCancel", modal), "click", ()=>closeModal());
      safeOn(q("#blSave", modal), "click", async ()=>{
        const md = String(ta?.value||"").trim();
        if(!md){ out.textContent = "Paste markdown first."; return; }
        const item = _extractBacklogItemFromMarkdown(md);
        if(!item){ out.textContent = "Could not parse item."; return; }
        const cur = State.patchBacklog && Array.isArray(State.patchBacklog.items) ? State.patchBacklog.items.slice() : [];
        cur.push(item);
        out.textContent = "Saving…";
        try{
          await _savePatchBacklog({items: cur});
          try{ _persistBacklogDraft(""); }catch(_e){}
          out.textContent = "Saved.";
          closeModal();
          renderBacklogView();
        }catch(e){
          out.textContent = "Save failed: " + String(e?.message||e);
        }
      });
    }
  );
}

function renderRunDetailSkeleton(){
  const root = q("#view-run");
  if(!root) return;
  const rk = State.activeRunKey;
  if(!rk){
    root.innerHTML = `<div class="muted">No run selected.</div>`;
    return;
  }
  const el = ensureRunDetailLayout(rk);
  if(!el) return;
  const title = q("#rdTitle", el);
  if(title) title.innerHTML = `Run: <span class="mono">${escapeHtml(rk)}</span>`;
  const badges = q("#rdBadges", el);
  if(badges) badges.innerHTML = `<span class="muted">Loading…</span>`;
}

function openExportSnapshotModal(runKey){
  const defaultName = `snapshot_${runKey.replace(/[^a-zA-Z0-9._-]+/g, "_").slice(-32)}_${Date.now()}`;
  openModal(
    "Export Snapshot",
    `<div class="muted">Create a zip snapshot from <b>${escapeHtml(runKey)}</b> (UI-owned export directory).</div>
     <div style="margin-top:12px;">
       <div class="muted" style="margin-bottom:6px;">Snapshot name</div>
       <input id="snapName" class="input" value="${escapeHtml(defaultName)}" />
     </div>
     <div style="margin-top:12px;">
       <label class="muted" style="display:flex;align-items:center;gap:8px;">
         <input id="snapIncludeCk" type="checkbox" />
         Include checkpoints
       </label>
     </div>
     <div class="row" style="margin-top:12px;gap:10px;justify-content:flex-end;">
       <button class="btn" id="snapDo">Export</button>
     </div>
     <div class="muted" id="snapOut" style="margin-top:10px;font-size:12px;"></div>`,
    async (modal)=>{
      const btn = q("#snapDo", modal);
      const nameEl = q("#snapName", modal);
      const incEl = q("#snapIncludeCk", modal);
      const out = q("#snapOut", modal);
      safeOn(btn, "click", async ()=>{
        const nm = String(nameEl?.value || "").trim();
        if(!nm){
          out.textContent = "Name required.";
          return;
        }
        out.textContent = "Exporting…";
        try{
          if(btn) btn.disabled = true;
          const resp = await apiExportSnapshot(runKey, nm, !!(incEl && incEl.checked));
          const dl = String(resp?.download_url || '');
          const fn = String(resp?.filename || '');
          if(dl){
            out.innerHTML = `Done: <a class="mono" href="${escapeHtml(dl)}" download>Download ${escapeHtml(fn||'export.zip')}</a>`;
          }else{
            out.innerHTML = `Done: <span class="mono">${escapeHtml(resp?.relpath || resp?.relpath || '')}</span>`;
          }
          await refreshRuns();
        }catch(e){
          out.textContent = `Export failed: ${String(e?.message || e)}`;
        }finally{
          if(btn) btn.disabled = false;
        }
      });
    }
  );
}

function openEditMetaModal(runKey){
  const meta = State.runDetail.meta || null;
  const label0 = (meta?.run?.label || "");
  const tags0 = (Array.isArray(meta?.run?.tags) ? meta.run.tags : []).join(", ");

  openModal(
    "Edit Label/Tags",
    `<div class="muted">Run: <b>${escapeHtml(runKey)}</b> (stored in <span class="mono">ui_meta.json</span> alongside viz artifacts).</div>
     <div style="margin-top:12px;">
       <div class="muted" style="margin-bottom:6px;">Label</div>
       <input id="metaLabel" class="input" value="${escapeHtml(label0)}" />
     </div>
     <div style="margin-top:12px;">
       <div class="muted" style="margin-bottom:6px;">Tags (comma-separated)</div>
       <input id="metaTags" class="input" value="${escapeHtml(tags0)}" />
     </div>
     <div class="row" style="margin-top:12px;gap:10px;justify-content:flex-end;">
       <button class="btn" id="metaSave">Save</button>
     </div>
     <div class="muted" id="metaOut" style="margin-top:10px;font-size:12px;"></div>`,
    async (modal)=>{
      const btn = q("#metaSave", modal);
      const lab = q("#metaLabel", modal);
      const tag = q("#metaTags", modal);
      const out = q("#metaOut", modal);
      safeOn(btn, "click", async ()=>{
        const label = String(lab?.value || "").trim();
        const tags = String(tag?.value || "").split(",").map(s=>s.trim()).filter(Boolean);
        out.textContent = "Saving…";
        try{
          await apiWriteUiMeta(runKey, { label, tags });
          out.textContent = "Saved.";
          await refreshRunDetail(false);
          await refreshRuns();
        }catch(e){
          out.textContent = `Save failed: ${String(e?.message || e)}`;
        }
      });
    }
  );
}

function wireRunDetailEvents(root){
  const rk = State.activeRunKey;

  const rangeSel = q("#rdRange", root);
  if(rangeSel){
    rangeSel.value = String(State.runDetail.range || 1000);
    rangeSel.addEventListener("change", ()=>{
      const v = parseInt(rangeSel.value, 10);
      if(Number.isFinite(v) && v>0){
        State.runDetail.range = v;
        State.runDetail.lastMetricName = null;
        refreshRunDetail(false);
      }
    });
  }

  const overlay = q("#rdOverlay", root);
  if(overlay){
    overlay.checked = !!State.runDetail.overlayParent;
    overlay.addEventListener("change", ()=>{
      State.runDetail.overlayParent = overlay.checked;
      refreshRunDetail(false);
    });
  }

  const smooth = q("#rdSmooth", root);
  if(smooth){
    smooth.checked = !!State.runDetail.smoothEpi;
    smooth.addEventListener("change", ()=>{
      State.runDetail.smoothEpi = smooth.checked;
      renderRunDetailView();
    });
  }

  const metricSel = q("#rdMetric", root);
  if(metricSel){
    metricSel.addEventListener("change", ()=>{
      State.runDetail.metricName = metricSel.value;
      State.runDetail.lastMetricName = null;
      refreshRunDetail(false);
    });
  }

  const cmdLimit = q("#rdCmdLimit", root);
  if(cmdLimit){
    cmdLimit.value = String(State.runDetail.appliedLimit || 200);
    cmdLimit.addEventListener("change", ()=>{
      const v = parseInt(cmdLimit.value, 10);
      if(Number.isFinite(v) && v>0){
        State.runDetail.appliedLimit = v;
        refreshRunDetail(false);
      }
    });
  }

  const apLimit = q("#rdApLimit", root);
  if(apLimit){
    apLimit.value = String(State.runDetail.autopilotLimit || 200);
    apLimit.addEventListener("change", ()=>{
      const v = parseInt(apLimit.value, 10);
      if(Number.isFinite(v) && v > 0){
        State.runDetail.autopilotLimit = v;
        refreshRunDetail(false);
      }
    });
  }

  // Applied commands timeline usability (filters + gen paging)
  const acSearch = q("#acSearch", root);
  if(acSearch){
    acSearch.addEventListener("input", ()=>{
      State.runDetail.appliedSearch = acSearch.value || "";
      renderAppliedCommandsTable(root);
    });
  }

  const acStatus = q("#acStatus", root);
  if(acStatus){
    acStatus.addEventListener("change", ()=>{
      State.runDetail.appliedStatus = acStatus.value || "ALL";
      renderAppliedCommandsTable(root);
    });
  }

  const acOlder = q("#acOlder", root);
  if(acOlder){
    acOlder.addEventListener("click", ()=>{
      const w = State.runDetail.appliedWindow;
      const minGen = (w && w.min_generation !== undefined) ? Number(w.min_generation) : NaN;
      if(Number.isFinite(minGen)){
        State.runDetail.appliedBeforeGen = Math.floor(minGen);
        State.runDetail.appliedAfterGen = -1;
        refreshRunDetail(false);
      }
    });
  }

  const acLatest = q("#acLatest", root);
  if(acLatest){
    acLatest.addEventListener("click", ()=>{
      State.runDetail.appliedAfterGen = -1;
      State.runDetail.appliedBeforeGen = null;
      refreshRunDetail(false);
    });
  }

  const acNewer = q("#acNewer", root);
  if(acNewer){
    acNewer.addEventListener("click", ()=>{
      const w = State.runDetail.appliedWindow;
      const maxGen = (w && w.max_generation !== undefined) ? Number(w.max_generation) : NaN;
      if(Number.isFinite(maxGen)){
        State.runDetail.appliedAfterGen = Math.floor(maxGen);
        State.runDetail.appliedBeforeGen = null;
        refreshRunDetail(false);
      }
    });
  }

  const acJump = q("#acJumpGen", root);
  if(acJump){
    const jump = ()=>{
      const v = intOr(acJump.value, null);
      if(v !== null && v !== undefined){
        State.runDetail.appliedAfterGen = Math.max(-1, v - 1);
        State.runDetail.appliedBeforeGen = null;
        refreshRunDetail(false);
      }
    };
    acJump.addEventListener("keydown", (e)=>{
      if(e.key === "Enter"){
        e.preventDefault();
        jump();
      }
    });
    acJump.addEventListener("change", ()=>jump());
  }

  // Autopilot actions timeline usability (search + gen paging)
  const apSearch = q("#apSearch", root);
  if(apSearch){
    apSearch.addEventListener("input", ()=>{
      State.runDetail.autopilotSearch = apSearch.value || "";
      renderAutopilotActionsTable(root);
    });
  }

  const apOlder = q("#apOlder", root);
  if(apOlder){
    apOlder.addEventListener("click", ()=>{
      const w = State.runDetail.autopilotWindow;
      const minGen = (w && w.min_generation !== undefined) ? Number(w.min_generation) : NaN;
      if(Number.isFinite(minGen)){
        State.runDetail.autopilotBeforeGen = Math.floor(minGen);
        State.runDetail.autopilotAfterGen = -1;
        refreshRunDetail(false);
      }
    });
  }

  const apLatest = q("#apLatest", root);
  if(apLatest){
    apLatest.addEventListener("click", ()=>{
      State.runDetail.autopilotAfterGen = -1;
      State.runDetail.autopilotBeforeGen = null;
      refreshRunDetail(false);
    });
  }

  const apNewer = q("#apNewer", root);
  if(apNewer){
    apNewer.addEventListener("click", ()=>{
      const w = State.runDetail.autopilotWindow;
      const maxGen = (w && w.max_generation !== undefined) ? Number(w.max_generation) : NaN;
      if(Number.isFinite(maxGen)){
        State.runDetail.autopilotAfterGen = Math.floor(maxGen);
        State.runDetail.autopilotBeforeGen = null;
        refreshRunDetail(false);
      }
    });
  }

  const apJump = q("#apJumpGen", root);
  if(apJump){
    const jump = ()=>{
      const v = intOr(apJump.value, null);
      if(v !== null && v !== undefined){
        State.runDetail.autopilotAfterGen = Math.max(-1, v - 1);
        State.runDetail.autopilotBeforeGen = null;
        refreshRunDetail(false);
      }
    };
    apJump.addEventListener("keydown", (e)=>{
      if(e.key === "Enter"){
        e.preventDefault();
        jump();
      }
    });
    apJump.addEventListener("change", ()=>jump());
  }

  const btnArchive = q("#btnArchive", root);
  safeOn(btnArchive, "click", async ()=>{
    const rk = State.activeRunKey;
    if(!rk) return;
    const run = findRun(rk);
    const isArchived = !!run?.archived;
    try{
      if(isArchived){
        await apiUnarchiveRun(rk);
        await refreshRuns();
        await refreshRunDetail(false);
        toast("Unarchived run.", rk);
        return;
      }

      toast("Archiving…", rk, 1800);
      const resp = await apiArchiveRun(rk);
      await refreshRuns();
      await refreshRunDetail(false);

      const fn = String(resp?.filename || "");
      const rel = String(resp?.relpath || "");
      const sha = String(resp?.archive_sha256 || "");
      const info = rel ? rel : (fn ? fn : "(archive created)");
      toast("Archived run.", sha ? `${info} :: sha256=${sha.slice(0, 12)}…` : info, 5200);
    }catch(e){
      showGlobalError("Archive failed", String(e?.message || e));
    }
  });

  safeOn(q("#btnExport", root), "click", ()=>{
    const rk = State.activeRunKey;
    if(!rk) return;
    openExportSnapshotModal(rk);
  });

  safeOn(q("#btnEditMeta", root), "click", ()=>{
    const rk = State.activeRunKey;
    if(!rk) return;
    openEditMetaModal(rk);
  });

  safeOn(q("#btnCmdWizard", root), "click", ()=>{
    const rk = State.activeRunKey;
    if(!rk) return;
    const meta = State.runDetail.meta;
    const det = String(meta?.run?.determinism || "UNKNOWN").toUpperCase();
    const strict = !!(meta?.run?.strict_mode);
    const frozen = meta?.trait_injection_apply_phase_frozen || meta?.frozen_apply_phase || null;
    // Default to next generation to avoid "time travel" commands.
    const targetGen = (meta?.run?.current_generation ?? meta?.current_generation ?? 0);
    const defGen = (Number.isFinite(Number(targetGen)) && Number(targetGen) >= 0) ? (Number(targetGen) + 1) : 1;
    openCommandWizard(rk, { runDeterminism: det, strictMode: strict, frozenApplyPhase: frozen, defaultTargetGeneration: defGen });
  });

  safeOn(q("#paHelp", root), "click", ()=>{
    openModal(
      "Autopilot Help",
      `<div class="muted" style="white-space:normal;line-height:1.45;">
        <div><b>What it does</b></div>
        <div style="margin-top:4px;">Pressure autopilot periodically evaluates run signals (survival, diversity, EPI/AV) and submits <span class="mono">PressureUpdateCommand</span> updates to make the environment harder or easier.</div>
        <div style="margin-top:10px;"><b>How to use</b></div>
        <div style="margin-top:4px;">1) Set <span class="mono">Apply at gen</span> to current generation + 10.</div>
        <div>2) Keep <span class="mono">Enable=true</span>.</div>
        <div>3) Choose a <span class="mono">Profile</span> (balanced/cycle/scarcity/cost/shock).</div>
        <div>4) Tune <span class="mono">Tick interval</span> and step sizes.</div>
        <div>5) Click <span class="mono">Send</span>, then watch <span class="mono">Autopilot Actions</span>.</div>
        <div style="margin-top:10px;"><b>Control meanings</b></div>
        <div><span class="mono">Tick interval</span>: how often decisions run (in generations).</div>
        <div><span class="mono">Increase/Reduce Pressure Step</span>: per-decision step size. Bigger step = bigger change each decision.</div>
        <div><span class="mono">Target survival</span>: desired cohort survival level.</div>
        <div><span class="mono">Survival ceiling</span>: above this, autopilot tends to push pressure up.</div>
        <div style="margin-top:10px;"><b>Profiles (one-liner)</b></div>
        <div><span class="mono">balanced</span>: general-purpose; adjusts scarcity + cost + shock together.</div>
        <div><span class="mono">cycle</span>: phased mode; rotates one axis at a time when progress stalls.</div>
        <div><span class="mono">scarcity</span>: resource pressure only (food availability).</div>
        <div><span class="mono">cost</span>: efficiency pressure only (movement cost).</div>
        <div><span class="mono">shock</span>: resilience pressure only (shock frequency).</div>
        <div style="margin-top:10px;"><b>Rule of thumb</b></div>
        <div>Overall adjustment rate is roughly step-size divided by interval. Faster response comes from larger steps, shorter interval, or both.</div>
      </div>`
    );
  });

  // Pressure autopilot quick submit
  const paEnable = q("#paEnable", root);
  const paGen = q("#paGen", root);
  const paInterval = q("#paInterval", root);
  const paProfile = q("#paProfile", root);
  const paDeltaUp = q("#paDeltaUp", root);
  const paDeltaDown = q("#paDeltaDown", root);
  const paTargetSurvival = q("#paTargetSurvival", root);
  const paSurvivalCeiling = q("#paSurvivalCeiling", root);
  const paProfileHint = q("#paProfileHint", root);
  const paDeltaUpVal = q("#paDeltaUpVal", root);
  const paDeltaDownVal = q("#paDeltaDownVal", root);
  const paTargetSurvivalVal = q("#paTargetSurvivalVal", root);
  const paSurvivalCeilingVal = q("#paSurvivalCeilingVal", root);
  const paPreview = q("#paPreview", root);
  const profileHint = (p)=>{
    const k = String(p || "balanced").toLowerCase();
    if(k === "balanced") return "Profile (balanced): general-purpose, adjusts scarcity + cost + shock together.";
    if(k === "cycle") return "Profile (cycle): phased mode, rotates scarcity/cost/shock when progress stalls.";
    if(k === "scarcity") return "Profile (scarcity): resource pressure only, adjusts food availability.";
    if(k === "cost") return "Profile (cost): efficiency pressure only, adjusts movement cost.";
    if(k === "shock") return "Profile (shock): resilience pressure only, adjusts shock frequency.";
    return "Profile: n/a";
  };

  const updatePaUi = ()=>{
    if(paEnable) paEnable.checked = !!State.runDetail.pressureAutopilotEnable;
    // Default to current generation + 10 when meta is available.
    const cg = Number(State.runDetail?.meta?.run?.current_generation);
    const fallbackGen = (Number.isFinite(cg) && cg >= 0) ? (cg + 10) : 10;
    if(paGen && document.activeElement !== paGen) paGen.value = String(State.runDetail.pressureAutopilotTargetGen ?? fallbackGen);
    if(paInterval && document.activeElement !== paInterval) paInterval.value = String(State.runDetail.pressureAutopilotTickInterval ?? 200);
    if(paProfile && document.activeElement !== paProfile) paProfile.value = String(State.runDetail.pressureAutopilotProfile || "balanced");
    if(paDeltaUp && document.activeElement !== paDeltaUp) paDeltaUp.value = String(State.runDetail.pressureAutopilotDeltaUp ?? 0.05);
    if(paDeltaDown && document.activeElement !== paDeltaDown) paDeltaDown.value = String(State.runDetail.pressureAutopilotDeltaDown ?? 0.10);
    if(paTargetSurvival && document.activeElement !== paTargetSurvival) paTargetSurvival.value = String(State.runDetail.pressureAutopilotTargetSurvival ?? 0.55);
    if(paSurvivalCeiling && document.activeElement !== paSurvivalCeiling) paSurvivalCeiling.value = String(State.runDetail.pressureAutopilotSurvivalCeiling ?? 0.80);
    if(paInterval) paInterval.disabled = !State.runDetail.pressureAutopilotEnable;
    if(paProfile) paProfile.disabled = !State.runDetail.pressureAutopilotEnable;
    if(paDeltaUp) paDeltaUp.disabled = !State.runDetail.pressureAutopilotEnable;
    if(paDeltaDown) paDeltaDown.disabled = !State.runDetail.pressureAutopilotEnable;
    if(paTargetSurvival) paTargetSurvival.disabled = !State.runDetail.pressureAutopilotEnable;
    if(paSurvivalCeiling) paSurvivalCeiling.disabled = !State.runDetail.pressureAutopilotEnable;
    if(paDeltaUpVal) paDeltaUpVal.textContent = String(State.runDetail.pressureAutopilotDeltaUp ?? 0.05);
    if(paDeltaDownVal) paDeltaDownVal.textContent = String(State.runDetail.pressureAutopilotDeltaDown ?? 0.10);
    if(paTargetSurvivalVal) paTargetSurvivalVal.textContent = String(State.runDetail.pressureAutopilotTargetSurvival ?? 0.55);
    if(paSurvivalCeilingVal) paSurvivalCeilingVal.textContent = String(State.runDetail.pressureAutopilotSurvivalCeiling ?? 0.80);
    if(paProfileHint) paProfileHint.textContent = profileHint(State.runDetail.pressureAutopilotProfile || "balanced");

    if(paPreview){
      try{
        const p = buildPressureAutopilotPayload();
        const en = !!State.runDetail.pressureAutopilotEnable;
        const tg = _parseIntStrict(State.runDetail.pressureAutopilotTargetGen, fallbackGen);
        const iv = _parseIntStrict(State.runDetail.pressureAutopilotTickInterval, 200);
        const pr = String(State.runDetail.pressureAutopilotProfile || "balanced");
        const du = Number(State.runDetail.pressureAutopilotDeltaUp ?? 0.05);
        const dd = Number(State.runDetail.pressureAutopilotDeltaDown ?? 0.10);
        const ts = Number(State.runDetail.pressureAutopilotTargetSurvival ?? 0.55);
        const sc = Number(State.runDetail.pressureAutopilotSurvivalCeiling ?? 0.80);
        paPreview.textContent = en
          ? `Will apply at gen=${tg}: enable=true, profile=${pr}, tick_interval=${iv}, delta_up=${fmtNum(du,2)}, delta_down=${fmtNum(dd,2)}, target_survival=${fmtNum(ts,2)}, ceiling=${fmtNum(sc,2)}`
          : `Will apply at gen=${tg}: enable=false`;
        // Keep preview payload around for debugging without making users stare at JSON.
        paPreview.dataset.payload = JSON.stringify(p);
      }catch(e){
        paPreview.textContent = `Invalid input: ${String(e?.message || e)}`;
        paPreview.dataset.payload = "";
      }
    }
  };

  if(paEnable){
    paEnable.checked = !!State.runDetail.pressureAutopilotEnable;
    paEnable.addEventListener("change", ()=>{
      State.runDetail.pressureAutopilotEnable = !!paEnable.checked;
    

  // Timeline controls (control + pressure)
  const tlBins = q("#rdTlBins", root);
  if(tlBins){
    tlBins.value = String(State.runDetail.timelineBins || 200);
    tlBins.addEventListener("change", ()=>{
      const v = parseInt(tlBins.value, 10);
      if(Number.isFinite(v) && v > 0){
        State.runDetail.timelineBins = v;
        // Force refresh to pull new binning.
        refreshRunDetail(true);
      }
    });
  }

  const tlCanvas = q("#rdTimelineCanvas", root);
  const tlTip = q("#rdTimelineTip", root);
  const tlDrawer = q("#rdTimelineDrawer", root);
  const tlDrawerClose = q("#rdTimelineDrawerClose", root);
  const tlDrawerTitle = q("#rdTimelineDrawerTitle", root);
  const tlDrawerBody = q("#rdTimelineDrawerBody", root);
  const tlShowControl = q("#rdTlShowControl", root);
  const tlShowPressure = q("#rdTlShowPressure", root);

  const hideTip = ()=>{ if(tlTip){ tlTip.style.display = "none"; } };
  const showTip = (x,y,txt)=>{
    if(!tlTip) return;
    tlTip.textContent = txt;
    tlTip.style.left = `${x}px`;
    tlTip.style.top = `${y}px`;
    tlTip.style.display = "block";
  };

  const openDrawer = ()=>{
    if(tlDrawer) tlDrawer.style.display = "block";
  };
  const closeDrawer = ()=>{
    if(tlDrawer) tlDrawer.style.display = "none";
  };
  safeOn(tlDrawerClose, "click", ()=>closeDrawer());

  let lastHoverBin = null;

  if(tlCanvas){
    tlCanvas.addEventListener("mousemove", (e)=>{
      const sum = State.runDetail.timelineSummary;
      if(!sum || !Array.isArray(sum.bins) || !sum.bins.length){ hideTip(); return; }
      const rect = tlCanvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const bins = sum.bins;
      const w = rect.width || tlCanvas.width;
      const bx = Math.max(0, Math.min(bins.length-1, Math.floor((x / w) * bins.length)));
      const b = bins[bx];
      if(!b){ hideTip(); return; }
      lastHoverBin = bx;
      const cc = b.control_counts || {};
      const pf = b.pressure_flags || {};
      const txt = `gen ${b.gen_min}..${b.gen_max} · user:${cc.user||0} auto:${cc.autopilot||0} sys:${cc.system||0} · shock:${pf.shock?1:0} multΔ:${pf.multiplier_change?1:0}`;
      showTip(x+10, y+10, txt);
    });
    tlCanvas.addEventListener("mouseleave", ()=>{ hideTip(); lastHoverBin=null; });

    tlCanvas.addEventListener("click", async ()=>{
      const sum = State.runDetail.timelineSummary;
      if(!sum || !Array.isArray(sum.bins) || lastHoverBin===null){ return; }
      const b = sum.bins[lastHoverBin];
      if(!b) return;
      State.runDetail.timelineSelection = {gen_min: b.gen_min, gen_max: b.gen_max};
      openDrawer();
      if(tlDrawerTitle) tlDrawerTitle.textContent = `gen ${b.gen_min}..${b.gen_max}`;
      if(tlDrawerBody) tlDrawerBody.textContent = "Loading…";
      try{
        const rk = State.activeRunKey;
        const kind = "control";
        const res = await apiRunTimelineRange(rk, b.gen_min, b.gen_max, kind);
        if(tlDrawerBody) tlDrawerBody.textContent = JSON.stringify(res, null, 2);
      }catch(err){
        if(tlDrawerBody) tlDrawerBody.textContent = `Load failed: ${String(err?.message||err)}`;
      }
    });
  }

  safeOn(tlShowControl, "click", async ()=>{
    const sel = State.runDetail.timelineSelection;
    if(!sel) return;
    if(tlDrawerBody) tlDrawerBody.textContent = "Loading…";
    try{
      const rk = State.activeRunKey;
      const res = await apiRunTimelineRange(rk, sel.gen_min, sel.gen_max, "control");
      if(tlDrawerBody) tlDrawerBody.textContent = JSON.stringify(res, null, 2);
    }catch(err){
      if(tlDrawerBody) tlDrawerBody.textContent = `Load failed: ${String(err?.message||err)}`;
    }
  });

  safeOn(tlShowPressure, "click", async ()=>{
    const sel = State.runDetail.timelineSelection;
    if(!sel) return;
    if(tlDrawerBody) tlDrawerBody.textContent = "Loading…";
    try{
      const rk = State.activeRunKey;
      const res = await apiRunTimelineRange(rk, sel.gen_min, sel.gen_max, "pressure");
      if(tlDrawerBody) tlDrawerBody.textContent = JSON.stringify(res, null, 2);
    }catch(err){
      if(tlDrawerBody) tlDrawerBody.textContent = `Load failed: ${String(err?.message||err)}`;
    }
  });

  updatePaUi();
    });
  }
  if(paGen){
    const cg = Number(State.runDetail?.meta?.run?.current_generation);
    const fallbackGen = (Number.isFinite(cg) && cg >= 0) ? (cg + 10) : 10;
    paGen.value = String(State.runDetail.pressureAutopilotTargetGen ?? fallbackGen);
    paGen.addEventListener("change", ()=>{
      State.runDetail.pressureAutopilotTargetGenTouched = true;
      State.runDetail.pressureAutopilotTargetGen = _parseIntStrict(paGen.value, fallbackGen);
      updatePaUi();
    });
  }
  if(paInterval){
    paInterval.value = String(State.runDetail.pressureAutopilotTickInterval ?? 200);
    paInterval.addEventListener("change", ()=>{
      State.runDetail.pressureAutopilotTickInterval = _parseIntStrict(paInterval.value, 200);
      updatePaUi();
    });
  }
  if(paProfile){
    paProfile.value = String(State.runDetail.pressureAutopilotProfile || "balanced");
    paProfile.addEventListener("change", ()=>{
      State.runDetail.pressureAutopilotProfile = String(paProfile.value || "balanced");
      updatePaUi();
    });
  }
  if(paDeltaUp){
    paDeltaUp.value = String(State.runDetail.pressureAutopilotDeltaUp ?? 0.05);
    paDeltaUp.addEventListener("input", ()=>{
      State.runDetail.pressureAutopilotDeltaUp = Number.parseFloat(String(paDeltaUp.value || "0.05"));
      updatePaUi();
    });
  }
  if(paDeltaDown){
    paDeltaDown.value = String(State.runDetail.pressureAutopilotDeltaDown ?? 0.10);
    paDeltaDown.addEventListener("input", ()=>{
      State.runDetail.pressureAutopilotDeltaDown = Number.parseFloat(String(paDeltaDown.value || "0.10"));
      updatePaUi();
    });
  }
  if(paTargetSurvival){
    paTargetSurvival.value = String(State.runDetail.pressureAutopilotTargetSurvival ?? 0.55);
    paTargetSurvival.addEventListener("input", ()=>{
      State.runDetail.pressureAutopilotTargetSurvival = Number.parseFloat(String(paTargetSurvival.value || "0.55"));
      updatePaUi();
    });
  }
  if(paSurvivalCeiling){
    paSurvivalCeiling.value = String(State.runDetail.pressureAutopilotSurvivalCeiling ?? 0.80);
    paSurvivalCeiling.addEventListener("input", ()=>{
      State.runDetail.pressureAutopilotSurvivalCeiling = Number.parseFloat(String(paSurvivalCeiling.value || "0.80"));
      updatePaUi();
    });
  }

  safeOn(q("#paCopy", root), "click", async ()=>{
    try{
      const payload = buildPressureAutopilotPayload();
      const ok = await copyTextToClipboard(JSON.stringify(payload, null, 2));
      showToast("Copied", ok ? "PressureUpdateCommand JSON copied" : "Copy failed");
    }catch(e){
      showToast("Invalid input", String(e?.message || e));
    }
  });

  safeOn(q("#paSend", root), "click", async ()=>{
    const rk = State.activeRunKey;
    if(!rk) return;
    let payload;
    try{ payload = buildPressureAutopilotPayload(); }
    catch(e){ showToast("Invalid input", String(e?.message || e)); return; }

    const meta = State.runDetail.meta;
    const det = String(meta?.run?.determinism || "UNKNOWN").toUpperCase();
    if(det === "FAIL"){
      if(!confirm("This run is determinism FAIL. Submitting commands is allowed, but you probably know what you're doing. Continue?")) return;
    }
    if(!confirm("Submit PressureUpdateCommand to inbox?")) return;

    try{
      const res = await apiSubmitCommand(rk, payload);
      if(res && res.command_uuid){
        State.runDetail.pendingUuids.add(String(res.command_uuid));
        showToast("Submitted", `pending ACK: ${res.command_uuid}`);
      }else{
        showToast("Submitted", "Command submitted.");
      }
      await refreshRunDetail(false);
    }catch(e){
      showToast("Submit failed", String(e?.message || e));
    }
  });

  updatePaUi();

  safeOn(q("#btnPlanBranch", root), "click", ()=>{
    const rk = State.activeRunKey;
    if(!rk) return;
    openPlanWizard({ kind: "branch", parentRunKey: rk, checkpointId: "" });
  });

  safeOn(q("#btnPlanCont", root), "click", ()=>{
    const rk = State.activeRunKey;
    if(!rk) return;
    openPlanWizard({ kind: "continuation", parentRunKey: rk, checkpointId: "" });
  });

  safeOn(q("#btnSaveNotes", root), "click", async ()=>{
    const rk = State.activeRunKey;
    if(!rk) return;
    const box = q("#notesBox", root);
    const txt = String(box?.value || "");
    try{
      await apiWriteNotes(rk, txt);
      if(box) box.dataset.dirty = "";
      toast("Notes saved.");
      State.runDetail.notes = await apiRunNotes(rk);
      renderRunDetailView();
    }catch(e){
      showGlobalError("Save notes failed", String(e?.message || e));
    }
  });

  const notesBox = q("#notesBox", root);
  if(notesBox){
    notesBox.addEventListener("input", ()=>{ notesBox.dataset.dirty = "1"; });
  }

  safeOn(q("#btnLoadKnobs", root), "click", async ()=>{
    const rk = State.activeRunKey;
    if(!rk) return;
    try{
      const kp = await apiRunKnobs(rk);
      State.runDetail.knobs = Array.isArray(kp?.rows) ? kp.rows : [];
      State.runDetail.knobsSource = kp?.source || null;
      toast("Knobs loaded.");
      renderRunDetailView();
    }catch(e){
      showGlobalError("Knobs load failed", String(e?.message || e));
    }
  });
}
