/* infra/webui/static/mobile.js
 * Last modified: 2026-03-01 Europe/Vienna
 *
 * Minimal JS for the Mobile Admin page.
 * No frameworks. No drama.
 */

(function () {
  "use strict";

  function el(id) { return document.getElementById(id); }

  function optEl(id) { return document.getElementById(id); }

  function bind(id, ev, fn) {
    const e = optEl(id);
    if (!e) return false;
    try { e.addEventListener(ev, fn); } catch (_e) { return false; }
    return true;
  }

  function val(id, defVal) {
    const e = optEl(id);
    if (!e) return (defVal === undefined ? "" : defVal);
    const v = ("value" in e) ? e.value : e.textContent;
    return String(v || "").trim();
  }


  function safeOn(node, ev, fn) {
    try {
      if (node) node.addEventListener(ev, fn);
    } catch (_e) {}
  }

  function setManifestsMsg(s) {
    const m = optEl("manifestsMsg");
    if (!m) return;
    m.textContent = String(s || "");
  }

  const state = {
    repoRoot: "",
    gitBaselineSuggested: "",
    // Admin command runner state.
    activeCmdJobId: "",
    activeGitJobId: "",
    pollTimers: {},
    terminalActiveKind: "",

    // UI refresh loop
    autoRefreshTimer: null,
    autoRefreshTick: 0,
    autoRefreshMode: "normal",
    autoRefreshMs: 4000,
    pauseWhenHidden: true,
    hasEverLoadedHealth: false,
    hasEverLoadedGit: false,
    tokenFromUrl: "",
  };

  function _isEditingGuarded() {
    // If the user is interacting with an input/select/textarea, don't
    // bulldoze the DOM with auto-refresh re-renders.
    try {
      const ae = document.activeElement;
      if (ae && ae.tagName) {
        const t = String(ae.tagName || "").toUpperCase();
        if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT") return true;
        if (ae.isContentEditable) return true;
      }
    } catch (_e) {}
    return false;
  }

  function _modeToMs(mode){
    const m = String(mode || "").toLowerCase();
    if (m === "fast") return 2000;
    if (m === "battery") return 8000;
    return 4000;
  }

  function _computeDefaultMode(){
    try{
      const c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      if (c && c.saveData) return "battery";
    }catch(_e){}
    return "normal";
  }

  function _updateRefreshStateLabel(){
    const n = optEl("refreshModeState");
    if (!n) return;
    n.textContent = `mode=${state.autoRefreshMode} interval=${Math.round(state.autoRefreshMs/1000)}s hidden_pause=${state.pauseWhenHidden ? "1":"0"}`;
  }

  function applyRefreshModeFromUi(){
    const m = val("refreshMode", state.autoRefreshMode || "normal");
    state.autoRefreshMode = (m === "fast" || m === "battery") ? m : "normal";
    state.autoRefreshMs = _modeToMs(state.autoRefreshMode);
    state.pauseWhenHidden = !!(optEl("refreshPauseHidden") && optEl("refreshPauseHidden").checked);
    _updateRefreshStateLabel();
    _startAutoRefresh();
  }

  function _startAutoRefresh() {
    try {
      if (state.autoRefreshTimer) clearInterval(state.autoRefreshTimer);
    } catch (_e) {}
    state.autoRefreshTick = 0;
    state.autoRefreshTimer = setInterval(async () => {
      if (state.pauseWhenHidden && document.hidden) return;
      state.autoRefreshTick = (state.autoRefreshTick + 1) >>> 0;
      try { refreshHealth(); } catch (_e) {}
      if (_isEditingGuarded()) return;
      try {
        const gitEvery = (state.autoRefreshMode === "fast") ? 3 : (state.autoRefreshMode === "battery" ? 8 : 5);
        if (state.autoRefreshTick % gitEvery === 0) {
          try { await gitRefresh(); } catch (_e) {}
        }
      } catch (_e) {}
    }, Math.max(1000, Number(state.autoRefreshMs || 4000)));
  }

  function tokenValue() {
    const input = optEl("adminToken");
    const fromInput = input ? String(input.value || "").trim() : "";
    return fromInput || state.tokenFromUrl || "";
  }

  function tokenQuery(_prefix) {
    // Browser login handles downloads; API calls use headers. Never expose an
    // operator credential in a URL, browser history or copied download link.
    return "";
  }

  function tokenHeaders() {
    const tok = tokenValue();
    return tok ? { "X-Admin-Token": tok } : {};
  }

  function fmtBytes(n) {
    const x = Number(n || 0);
    if (!isFinite(x)) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let v = x;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return (i === 0 ? String(v) : v.toFixed(1)) + " " + units[i];
  }

  function fmtTime(epoch) {
    const t = Number(epoch || 0) * 1000;
    if (!isFinite(t) || t <= 0) return "";
    const d = new Date(t);
    return d.toLocaleString();
  }

  async function apiJson(url, opts) {
    const r = await fetch(url, Object.assign({
      cache: "no-store",
      headers: Object.assign({ "Accept": "application/json" }, tokenHeaders(), (opts && opts.headers) ? opts.headers : {}),
    }, opts || {}));
    const ct = (r.headers.get("Content-Type") || "").toLowerCase();
    let data = null;
    if (ct.includes("application/json")) {
      data = await r.json();
    } else {
      data = { raw: await r.text() };
    }
    if (!r.ok) {
      const msg =
        (data && (data.message || data.error))
          ? String(data.message || data.error)
          : ("HTTP " + r.status);
      const err = new Error(msg);
      err.payload = data;
      err.status = r.status;
      throw err;
    }
    return data;
  }

  async function refreshHealth() {
    const badge = el("healthBadge");
    if (!badge) return;
    const note = optEl("serverStatusNote");

    setClass(badge, "updating", true);
    if (!state.hasEverLoadedHealth && !String(badge.textContent || "").trim()) {
      badge.className = "badge badge-muted";
      badge.textContent = "?";
    }

    try {
      const j = await apiJson("/api/health");
      const st = String(j.status || "ok");
      badge.className = (st.toLowerCase() === "ok") ? "badge badge-ok" : "badge badge-warn";
      setText("healthBadge", st);
      if (note) note.textContent = "";
      setClass(badge, "stale", false);
      state.hasEverLoadedHealth = true;
    } catch (_e) {
      if (!state.hasEverLoadedHealth) {
        badge.className = "badge badge-danger";
        badge.textContent = "bad";
      }
      if (note) note.textContent = "Health refresh failed.";
      setClass(badge, "stale", true);
    } finally {
      setClass(badge, "updating", false);
    }
  }

  async function loadConfig() {
    const rrSpan = optEl("repoRoot");
    if (rrSpan) rrSpan.textContent = "?";
    try {
      const j = await apiJson("/api/admin/config" + tokenQuery(false));
      state.repoRoot = j.repo_root || "";
      if (rrSpan) rrSpan.textContent = state.repoRoot || "?";
    } catch (e) {
      if (rrSpan) rrSpan.textContent = "ERROR";
      appendPatchOutput("[config] " + String(e));
    }
  }

  function renderArchives(list) {
    const root = el("archivesList");
    root.innerHTML = "";
    if (!list || !list.length) {
      root.innerHTML = "<div class='file-row'><div class='file-name muted'>No archives found.</div></div>";
      return;
    }
    for (const a of list) {
      const row = document.createElement("div");
      row.className = "file-row";

      const name = document.createElement("div");
      name.className = "file-name";
      name.innerHTML = "<div class='mono'>" + escapeHtml(a.name || "") + "</div><div class='muted'>" +
        escapeHtml(fmtBytes(a.bytes)) + " · " + escapeHtml(fmtTime(a.mtime)) + "</div>";

      const actions = document.createElement("div");
      actions.className = "file-actions";

      const dl = document.createElement("a");
      dl.className = "btn small";
      dl.textContent = "Download";
      dl.href = "/api/admin/archive/download/" + encodeURIComponent(a.name || "") + tokenQuery(false);

      actions.appendChild(dl);
      row.appendChild(name);
      row.appendChild(actions);
      root.appendChild(row);
    }
  }

  function renderManifests(jobs) {
    const root = el("manifestsList");
    if (!root) return;
    root.innerHTML = "";
    setManifestsMsg("");
    if (!jobs || !jobs.length) {
      root.innerHTML = "<div class='file-row'><div class='file-name muted'>No manifest jobs found.</div></div>";
      return;
    }

    for (const j of jobs) {
      const row = document.createElement("div");
      row.className = "file-row";

      const name = document.createElement("div");
      name.className = "file-name";
      const jid = String(j.job_id || "");
      const st = String(j.state || "");
      const pid = (j.pid !== undefined && j.pid !== null) ? String(j.pid) : "";
      const rd = String(j.report_dir || "");
      const man = String(j.manifest || "");
      name.innerHTML =
        "<div class='mono'>" + escapeHtml(jid) + "</div>" +
        "<div class='muted'>" +
          "state=" + escapeHtml(st) +
          (pid ? (" · pid=" + escapeHtml(pid)) : "") +
          (rd ? (" · report_dir=" + escapeHtml(rd)) : "") +
          (man ? (" · manifest=" + escapeHtml(man)) : "") +
        "</div>";

      const actions = document.createElement("div");
      actions.className = "file-actions";

      const btnStop = document.createElement("button");
      btnStop.className = "btn danger small";
      btnStop.textContent = "Stop";
      btnStop.disabled = (String(st).toUpperCase() === "STOPPED" || String(st).toUpperCase() === "LOST");
      safeOn(btnStop, "click", async () => {
        try {
          setManifestsMsg("");
          btnStop.disabled = true;
          await apiJson("/api/admin/manifests/stop" + tokenQuery(false), {
            method: "POST",
            headers: Object.assign({ "Content-Type": "application/json" }, tokenHeaders()),
            body: JSON.stringify({ job_id: jid, mode: "graceful" }),
          });
          await refreshManifests();
        } catch (e) {
          setManifestsMsg("Stop failed: " + String(e));
          appendPatchOutput("[manifests] stop failed: " + String(e));
          btnStop.disabled = false;
        }
      });

      const btnForce = document.createElement("button");
      btnForce.className = "btn danger small";
      btnForce.textContent = "Force";
      btnForce.disabled = btnStop.disabled;
      safeOn(btnForce, "click", async () => {
        try {
          setManifestsMsg("");
          btnForce.disabled = true;
          await apiJson("/api/admin/manifests/stop" + tokenQuery(false), {
            method: "POST",
            headers: Object.assign({ "Content-Type": "application/json" }, tokenHeaders()),
            body: JSON.stringify({ job_id: jid, mode: "force" }),
          });
          await refreshManifests();
        } catch (e) {
          setManifestsMsg("Force stop failed: " + String(e));
          appendPatchOutput("[manifests] force stop failed: " + String(e));
          btnForce.disabled = false;
        }
      });

      actions.appendChild(btnStop);
      actions.appendChild(btnForce);

      row.appendChild(name);
      row.appendChild(actions);
      root.appendChild(row);
    }
  }

  async function refreshManifests() {
    try {
      setManifestsMsg("");
      const j = await apiJson("/api/admin/manifests" + tokenQuery(false));
      renderManifests(j.jobs || []);
    } catch (e) {
      // Keep last render; just log.
      setManifestsMsg("Refresh failed: " + String(e));
      appendPatchOutput("[manifests] " + String(e));
    }
  }

  async function refreshLedgerActionJobs(){
    const uid = String(val("lgUidInput", state.ledgerActionUid || "") || "").trim();
    const linesN = Math.max(20, Math.min(2000, Number(val("lgTailLines", "200") || 200)));
    state.ledgerActionUid = uid;
    const list = optEl("lgJobsList");
    const st = optEl("lgJobsState");
    if (!list) return;
    if (!uid) {
      list.innerHTML = "<div class='job-row muted'>Enter a ledger UID to load jobs.</div>";
      if (st) st.textContent = "idle";
      return;
    }
    try{
      if (st) st.textContent = "loading jobs...";
      const j = await apiJson("/api/ledger/action/status?uid=" + encodeURIComponent(uid) + "&limit=60" + tokenQuery(true));
      const jobs = Array.isArray(j.jobs) ? j.jobs.slice().reverse() : [];
      if (!jobs.length) {
        list.innerHTML = "<div class='job-row muted'>No action jobs.</div>";
        if (st) st.textContent = "no jobs";
        return;
      }
      list.innerHTML = jobs.map((it)=>{
        const jid = String(it.job_id || "");
        const aid = String(it.action_id || "");
        const ev = String(it.event || "");
        const sc = String(it.status || "");
        const rc = String(it.exit_code ?? "");
        return `<div class=\"job-row\" data-lg-job=\"${escapeHtml(jid)}\">
          <div class=\"mono\" style=\"font-size:12px;\">${escapeHtml(jid)}</div>
          <div class=\"small-note\">action=${escapeHtml(aid)} · event=${escapeHtml(ev)} · status=${escapeHtml(sc)} · rc=${escapeHtml(rc)}</div>
          <div class=\"row-big\" style=\"margin-top:8px;\"><button class=\"btn big\" data-lg-view=\"${escapeHtml(jid)}\">View Log</button></div>
        </div>`;
      }).join("");
      list.querySelectorAll("button[data-lg-view]").forEach((b)=>{
        safeOn(b, "click", async ()=>{
          const jid = String(b.getAttribute("data-lg-view") || "").trim();
          if (!jid) return;
          state.ledgerActionJobId = jid;
          await viewLedgerActionLog(uid, jid, linesN);
        });
      });
      if (st) st.textContent = `jobs=${jobs.length}`;
      if (state.ledgerActionJobId) {
        await viewLedgerActionLog(uid, state.ledgerActionJobId, linesN);
      }
    }catch(e){
      list.innerHTML = "<div class='job-row muted'>ERROR: " + escapeHtml(String(e)) + "</div>";
      if (st) st.textContent = "error";
    }
  }

  async function viewLedgerActionLog(uid, jobId, lines){
    const out = optEl("lgJobLogOut");
    if (!out) return;
    const u = String(uid || "").trim();
    const j = String(jobId || "").trim();
    if (!u || !j) {
      out.textContent = "Select a job first.";
      return;
    }
    out.textContent = "[loading log tail]";
    try{
      const resp = await apiJson(
        "/api/ledger/action/log?uid=" + encodeURIComponent(u) +
        "&job_id=" + encodeURIComponent(j) +
        "&lines=" + encodeURIComponent(String(lines || 200)) +
        tokenQuery(true)
      );
      const head = `uid=${u}\njob=${j}\nlog_path=${String(resp.log_path||"")}\n---\n`;
      out.textContent = head + (Array.isArray(resp.lines) ? resp.lines.join("\n") : "");
    }catch(e){
      out.textContent = "ERROR: " + String(e);
    }
  }

  async function refreshArchives() {
    try {
      const j = await apiJson("/api/admin/archive/list" + tokenQuery(false));
      renderArchives(j.archives || []);
    } catch (e) {
      appendPatchOutput("[archives] " + String(e));
    }
  }

  function createArchiveDownload() {
    const debug = !!el("chkArchiveDebug").checked;
    const url = "/api/admin/archive/create?debug=" + (debug ? "1" : "0") + tokenQuery(true);
    // Direct navigation triggers a real download (no blob gymnastics).
    window.location.href = url;
    // Best-effort refresh after a short delay.
    setTimeout(refreshArchives, 1500);
  }

  function appendPatchOutput(s) {
    const out = el("patchOutput");
    if (!out) {
      try { console.warn(String(s || "")); } catch (_e) {}
      return;
    }
    out.textContent = (out.textContent || "") + String(s || "") + "\n";
    out.scrollTop = out.scrollHeight;
  }

  function clearPatchOutput() {
    const out = el("patchOutput");
    if (out) out.textContent = "";
  }

  function patchFlagsQuery() {
    const allowDirty = !!el("chkAllowDirty").checked;
    const forceOverwrite = !!el("chkForceOverwrite").checked;
    const verifySyntax = !!el("chkVerifySyntax").checked;
    const qs = [
      "allow_dirty=" + (allowDirty ? "1" : "0"),
      "force_overwrite=" + (forceOverwrite ? "1" : "0"),
      "verify_syntax=" + (verifySyntax ? "1" : "0"),
    ].join("&");
    return "?" + qs;
  }

  async function applyPatchUpload() {
    const fileInput = el("patchFile");
    if (!fileInput.files || !fileInput.files.length) {
      appendPatchOutput("[patch] choose a file first");
      return;
    }
    const f = fileInput.files[0];

    appendPatchOutput("[patch] uploading: " + f.name + " (" + fmtBytes(f.size) + ")");
    try {
      const buf = await f.arrayBuffer();
      const r = await fetch("/api/admin/patch/apply" + patchFlagsQuery(), {
        method: "POST",
        headers: Object.assign({
          "Content-Type": "application/octet-stream",
          "X-Filename": f.name,
        }, tokenHeaders()),
        body: buf,
      });
      let j = null;
      try{ j = await r.json(); }catch(_e){ j = {raw: await r.text().catch(()=>"")}; }
      if(!r.ok){
        const msg = (j && j.error) ? j.error : ("HTTP " + r.status);
        appendPatchOutput("[patch] HTTP " + r.status + " :: " + msg);
        return;
      }
      appendPatchOutput("[patch] rc=" + String(j.returncode));
      if (j.stdout) appendPatchOutput(j.stdout);
      if (j.stderr) appendPatchOutput(j.stderr);
    } catch (e) {
      appendPatchOutput("[patch] ERROR: " + String(e));
    }
  }


  async function inspectPatchUpload() {
    const fileInput = el("patchFile");
    if (!fileInput.files || !fileInput.files.length) {
      appendPatchOutput("[patch] choose a file first");
      return;
    }
    const f = fileInput.files[0];
    appendPatchOutput("[patch] inspect: " + f.name + " (" + fmtBytes(f.size) + ")");
    try {
      const buf = await f.arrayBuffer();
      const r = await fetch("/api/admin/patch/inspect" + patchFlagsQuery(), {
        method: "POST",
        headers: Object.assign({
          "Content-Type": "application/octet-stream",
          "X-Filename": f.name,
        }, tokenHeaders()),
        body: buf,
      });
      let j = null;
      try{ j = await r.json(); }catch(_e){ j = {raw: await r.text().catch(()=>"")}; }
      if(!r.ok){
        const msg = (j && j.error) ? j.error : ("HTTP " + r.status);
        appendPatchOutput("[patch] HTTP " + r.status + " :: " + msg);
        return;
      }

      if(j.kind === "zip" && Array.isArray(j.entries)){
        appendPatchOutput("[patch] zip entries=" + String(j.entry_count || j.entries.length));
        for(const it of j.entries.slice(0, 200)){
          const path = it.path || "";
          const bytes = (it.bytes !== undefined) ? (" " + fmtBytes(it.bytes)) : "";
          const ow = it.will_overwrite ? " OVERWRITE" : "";
          const skip = it.skip_reason ? (" SKIP(" + it.skip_reason + ")") : "";
          appendPatchOutput(" - " + path + bytes + ow + skip);
        }
        if(j.entries.length > 200){
          appendPatchOutput("[patch] ... truncated preview (first 200 entries)");
        }
      }else if(j.kind === "diff"){
        appendPatchOutput("[patch] diff chars=" + String(j.chars) + " lines=" + String(j.lines));
        if(j.preview_head) appendPatchOutput(j.preview_head);
      }else{
        appendPatchOutput("[patch] inspect: " + JSON.stringify(j, null, 2));
      }
    } catch (e) {
      appendPatchOutput("[patch] ERROR: " + String(e));
    }
  }

  async function applyPatchText() {
    const txt = (el("patchText").value || "").trim();
    if (!txt) {
      appendPatchOutput("[patch] paste a diff first");
      return;
    }
    const allowDirty = !!el("chkAllowDirty").checked;
    const forceOverwrite = !!el("chkForceOverwrite").checked;
    const verifySyntax = !!el("chkVerifySyntax").checked;
    const permitDeletions = !!el("chkPermitDeletions").checked;

    appendPatchOutput("[patch] applying pasted diff (" + txt.length + " chars)");
    try {
      const j = await apiJson("/api/admin/patch/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: "diff",
          text: txt,
          allow_dirty: allowDirty,
          force_overwrite: forceOverwrite,
          verify_syntax: verifySyntax,
          permit_deletions: permitDeletions,
        }),
      });
      appendPatchOutput("[patch] rc=" + String(j.returncode));
      if (j.stdout) appendPatchOutput(j.stdout);
      if (j.stderr) appendPatchOutput(j.stderr);
    } catch (e) {
      appendPatchOutput("[patch] ERROR: " + String(e));
    }
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Expose for debugging / console sanity checks (mobile browsers hide scope).
  try { window.escapeHtml = escapeHtml; } catch (_e) {}


  async function fsList(dir) {
    const rel = (dir || "").trim();
    const includeHidden = !!el("chkFsHidden").checked;
    const qs = "/api/admin/fs/list?path=" + encodeURIComponent(rel) + "&include_hidden=" + (includeHidden ? "1" : "0");
    try {
      const j = await apiJson(qs + tokenQuery(true));
      state.fsDir = j.dir || "";
      el("fsPath").value = state.fsDir;
      renderFsList(j.entries || []);
    } catch (e) {
      el("fsList").innerHTML = "<div class='file-row'><div class='file-name muted'>ERROR: " + escapeHtml(String(e)) + "</div></div>";
      updateFsSelCount();
    }
  }


  function _normFsPath(p){
    return String(p||"").replaceAll("\\\\","/").replaceAll(/\/+$/g, "");
  }

  function updateFsSelCount(){
    try{
      const n = (state.fsSelections && state.fsSelections.size) ? state.fsSelections.size : 0;
      const elc = el("fsSelCount");
      if(elc) elc.textContent = String(n);
    }catch(_e){}
  }

  function renderFsList(entries) {
    const root = el("fsList");
    root.innerHTML = "";
    if (!entries || !entries.length) {
      root.innerHTML = "<div class='file-row'><div class='file-name muted'>No entries.</div></div>";
      updateFsSelCount();
      return;
    }

    for (const ent of entries) {
      const row = document.createElement("div");
      row.className = "file-row";

      const chk = document.createElement("input");
      chk.type = "checkbox";
      // Allow selecting folders as well as files (server performs recursion).
      chk.disabled = false;
      const p = _normFsPath(ent.path || "");
      chk.dataset.path = p;
      try{ chk.checked = state.fsSelections.has(p); }catch(_e){};
      safeOn(chk, "change", ()=>{
        try{
          if(chk.checked) state.fsSelections.add(p);
          else state.fsSelections.delete(p);
        }catch(_e){}
        updateFsSelCount();
      });

      const name = document.createElement("div");
      name.className = "file-name";
      const icon = ent.is_dir ? "📁" : "📄";
      name.innerHTML = "<div class='mono'>" + icon + " " + escapeHtml(ent.name || "") + "</div>" +
        "<div class='muted'>" + escapeHtml(fmtBytes(ent.bytes)) + (ent.is_dir ? "" : (" · " + escapeHtml(fmtTime(ent.mtime)))) + "</div>";

      const actions = document.createElement("div");
      actions.className = "file-actions";

      if (ent.is_dir) {
        const openBtn = document.createElement("button");
        openBtn.className = "btn small";
        openBtn.textContent = "Open";
        openBtn.addEventListener("click", () => fsList(ent.path || ""));
        actions.appendChild(openBtn);
      } else {
        const viewBtn = document.createElement("button");
        viewBtn.className = "btn small";
        viewBtn.textContent = "View";
        viewBtn.addEventListener("click", () => fsView(ent.path || ""));
        actions.appendChild(viewBtn);

        const dl = document.createElement("a");
        dl.className = "btn small";
        dl.textContent = "Download";
        dl.href = "/api/admin/fs/download?path=" + encodeURIComponent(ent.path || "") + tokenQuery(true);
        actions.appendChild(dl);
      }

      row.appendChild(chk);
      row.appendChild(name);
      row.appendChild(actions);
      root.appendChild(row);
    }
  }

  async function fsView(path) {
    state.fsSelectedFile = path || "";
    el("replaceNote").textContent = state.fsSelectedFile ? ("Selected: " + state.fsSelectedFile) : "";
    el("fsPreview").textContent = "loading...";
    try {
      const j = await apiJson("/api/admin/fs/read?path=" + encodeURIComponent(path || "") + tokenQuery(true));
      const head = "[preview] " + (j.path || path || "") + (j.truncated ? " (truncated)" : "") + "\n";
      el("fsPreview").textContent = head + (j.text || "");
    } catch (e) {
      el("fsPreview").textContent = "ERROR: " + String(e);
    }
  }

  function fsUp() {
    const cur = (el("fsPath").value || "").trim().replaceAll("\\", "/").replaceAll(/\/+$/g, "");
    if (!cur) {
      fsList("");
      return;
    }
    const parts = cur.split("/").filter(Boolean);
    parts.pop();
    fsList(parts.join("/"));
  }

  async function zipSelected() {
    const name = (el("zipName").value || "").trim();
    const sel = Array.from(state.fsSelections || []).filter(Boolean).sort();
    if (!sel.length) {
      el("fsPreview").textContent = "Select some files first.";
      return;
    }

    try {
      const r = await fetch("/api/admin/fs/zip" + tokenQuery(false), {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, tokenHeaders()),
        body: JSON.stringify({
          paths: sel,
          name: name || "repo_selection.zip",
          include_hidden: !!el("chkFsHidden").checked,
        }),
      });
      if (!r.ok) {
        const j = await r.json();
        throw new Error(j && j.error ? j.error : ("HTTP " + r.status));
      }

      const blob = await r.blob();
      const cd = r.headers.get("Content-Disposition") || "";
      let outName = "repo_selection.zip";
      const m = cd.match(/filename=\"([^\"]+)\"/i);
      if (m && m[1]) outName = m[1];
      if (name) outName = name.toLowerCase().endsWith(".zip") ? name : (name + ".zip");

      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = outName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // NOTE (Android Chrome): revoking immediately can produce a 0-byte download.
      // Give the browser time to actually start reading the blob.
      setTimeout(()=>{ try{ URL.revokeObjectURL(url); }catch(_e){} }, 10_000);
    } catch (e) {
      el("fsPreview").textContent = "ZIP ERROR: " + String(e);
    }
  }

  function fsClearSelection(){
    try{
      if(state.fsSelections && state.fsSelections.clear) state.fsSelections.clear();
      else state.fsSelections = new Set();
    }catch(_e){ state.fsSelections = new Set(); }

    try{
      const root = el("fsList");
      if(root){
        root.querySelectorAll("input[type=checkbox]").forEach((c)=>{ try{ c.checked = false; }catch(_e){} });
      }
    }catch(_e){}

    updateFsSelCount();
    try{ const p = el("fsPreview"); if(p) p.textContent = "Selection cleared."; }catch(_e){}
  }


  async function replaceFile() {
    const path = state.fsSelectedFile;
    if (!path) {
      el("replaceNote").textContent = "Pick a file (View) first.";
      return;
    }
    const fi = el("replaceFile");
    if (!fi.files || !fi.files.length) {
      el("replaceNote").textContent = "Choose a replacement file first.";
      return;
    }
    const f = fi.files[0];
    el("replaceNote").textContent = "Uploading replacement for: " + path;

    try {
      const buf = await f.arrayBuffer();
      const r = await fetch("/api/admin/fs/replace?path=" + encodeURIComponent(path) + tokenQuery(true), {
        method: "POST",
        headers: Object.assign({
          "Content-Type": "application/octet-stream",
          "X-Filename": f.name,
        }, tokenHeaders()),
        body: buf,
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j && j.error ? j.error : ("HTTP " + r.status));
      el("replaceNote").textContent = "Replaced: " + (j.path || path) + " (" + fmtBytes(j.bytes) + ")";
      // Refresh preview.
      fsView(path);
    } catch (e) {
      el("replaceNote").textContent = "Replace ERROR: " + String(e);
    }
  }



  // ---------------------------------------------------------------------------
  // Admin command runner (Option A terminal)
  // ---------------------------------------------------------------------------

  function setText(id, s) {
    const n = el(id);
    if (!n) return;

    // Diff-aware update: avoid DOM writes if value didn't change (reduces flicker).
    const newText = String(s ?? "");
    if (n.textContent === newText) return;

    // "Smart" autoscroll:
    // - If the user is already at (or near) the bottom, keep sticking to bottom as new output arrives.
    // - If the user scrolled up to read, do NOT yank them back down.
    const isScrollable = n.classList && (n.classList.contains("scrollbox") || n.tagName === "PRE");
    let wasNearBottom = false;
    if (isScrollable) {
      const thresholdPx = 24;
      wasNearBottom = (n.scrollTop + n.clientHeight) >= (n.scrollHeight - thresholdPx);
    }

    n.textContent = newText;

    if (isScrollable && wasNearBottom) {
      n.scrollTop = n.scrollHeight;
    }

    // Keep Mobile Admin Terminal buttons in sync with output changes.
    // Safe: no-ops if elements don't exist.
    try {
      if (id === "cmdOutput") {
        updateCopyOutputButtonState();
      }
    } catch (_e) {}
  }

  // ---------------------------------------------------------------------------
  // Mobile Admin Terminal: Copy Output + Clear Input (QoL)
  // ---------------------------------------------------------------------------

  function getCmdOutputText() {
    try {
      const n = optEl("cmdOutput");
      return n ? String(n.textContent || "") : "";
    } catch (_e) { return ""; }
  }

  function updateCopyOutputButtonState() {
    try {
      const btn = optEl("btnCopyTermOutput");
      if (!btn) return;
      const txt = getCmdOutputText();
      btn.disabled = (txt.trim().length === 0);
    } catch (_e) {}
  }

  function updateClearInputButtonState() {
    try {
      const btn = optEl("btnClearTermInput");
      const input = optEl("cmdText");
      if (!btn || !input) return;
      btn.disabled = (String(input.value || "").trim().length === 0);
    } catch (_e) {}
  }

  async function onCopyTermOutput() {
    const status = optEl("termCopyStatus");
    const txt = getCmdOutputText();
    if (!txt.trim()) {
      updateCopyOutputButtonState();
      if (status) status.textContent = "";
      return;
    }

    let ok = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(txt);
        ok = true;
      }
    } catch (_e) { ok = false; }

    // Fallback: execCommand copy.
    if (!ok) {
      try {
        const ta = document.createElement("textarea");
        ta.value = txt;
        ta.setAttribute("readonly", "true");
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        ok = !!document.execCommand("copy");
        document.body.removeChild(ta);
      } catch (_e) { ok = false; }
    }

    if (ok) {
      if (status) {
        status.textContent = "Copied.";
        try { status._copyTimer && clearTimeout(status._copyTimer); } catch (_e) {}
        try { status._copyTimer = setTimeout(()=>{ try{ status.textContent = ""; }catch(_e){} }, 1500); } catch (_e) {}
      }
      return;
    }

    // Last-resort UX: select the output text so manual copy is immediate.
    try {
      const out = optEl("cmdOutput");
      if (out) {
        const r = document.createRange();
        r.selectNodeContents(out);
        const sel = window.getSelection();
        if (sel) {
          sel.removeAllRanges();
          sel.addRange(r);
        }
      }
    } catch (_e) {}

    if (status) {
      status.textContent = "Copy failed. Output selected.";
      try { status._copyTimer && clearTimeout(status._copyTimer); } catch (_e) {}
      try { status._copyTimer = setTimeout(()=>{ try{ status.textContent = ""; }catch(_e){} }, 2000); } catch (_e) {}
    }
  }

  function onClearTermInput(ev) {
    try { if (ev && ev.preventDefault) ev.preventDefault(); } catch (_e) {}
    try {
      const input = optEl("cmdText");
      if (!input) return;
      input.value = "";
      input.focus();
    } catch (_e) {}
    updateClearInputButtonState();
  }

  function setClass(elOrId, className, enabled) {
    const n = (typeof elOrId === "string") ? el(elOrId) : elOrId;
    if (!n || !n.classList) return;
    try {
      if (enabled) n.classList.add(className);
      else n.classList.remove(className);
    } catch (_e) {}
  }


  function isDangerousCmd(cmd) {
    const s = String(cmd || "").toLowerCase();
    if (!s) return false;
    const pats = [
      /\brm\s+-rf\b/,
      /\bmkfs\b/,
      /\bdd\s+if=/,
      /:\(\)\s*\{\s*:\|:\s*&\s*\};:/, // fork bomb
    ];
    return pats.some((re) => re.test(s));
  }

  function renderJob(job) {
    if (!job) return "";
    const lines = [];
    lines.push("kind: " + (job.kind || "cmd"));
    lines.push("cmd:  " + (job.cmd || ""));
    lines.push("cwd:  " + (job.cwd_rel || "") + (job.cwd_abs ? ("  (" + job.cwd_abs + ")") : ""));
    lines.push("pid:  " + String(job.pid || 0) + "   running=" + String(!!job.running) + "   killed=" + String(!!job.killed) + "   rc=" + String(job.returncode));
    lines.push("started: " + (job.utc_started || "") + "   finished: " + (job.utc_finished || ""));
    lines.push("duration_sec: " + String(job.duration_sec || 0));
    if (job.error) lines.push("error: " + job.error);
    lines.push("");
    lines.push("--- stdout ---");
    lines.push(String(job.stdout || ""));
    if (job.stdout_truncated) lines.push("[stdout truncated]");
    lines.push("");
    lines.push("--- stderr ---");
    lines.push(String(job.stderr || ""));
    if (job.stderr_truncated) lines.push("[stderr truncated]");
    return lines.join("\n");
  }

  function stopPolling(key) {
    const t = state.pollTimers[key];
    if (t) {
      clearInterval(t);
      delete state.pollTimers[key];
    }
  }

  function startPolling(jobId, outId, jobIdId, onDone) {
    const jid = String(jobId || "").trim();
    if (!jid) return;
    setText(jobIdId, jid);

    stopPolling(outId);
    state.pollTimers[outId] = setInterval(async () => {
      try {
        const j = await apiJson("/api/admin/cmd/status?job_id=" + encodeURIComponent(jid) + tokenQuery(true));
        const job = j.job || j;
        setText(outId, renderJob(job));
        if (job && job.running === false) {
          stopPolling(outId);
          if (onDone) onDone(job);
        }
      } catch (e) {
        setText(outId, "ERROR: " + String(e));
        stopPolling(outId);
      }
    }, 650);
  }

  async function cmdRun(cmdOverride) {
    const cmd = (cmdOverride !== undefined) ? String(cmdOverride || "") : String(el("cmdText").value || "");
    const s = cmd.trim();
    if (!s) {
      setText("cmdOutput", "Type a command first.");
      return;
    }
    if (isDangerousCmd(s)) {
      const ok = window.confirm("This command looks destructive. Run anyway?\n\n" + s);
      if (!ok) return;
    }

    const cwd = "";
    const timeoutSec = 7000;

    setText("cmdOutput", "[starting]\n" + s);
    try {
      const j = await apiJson("/api/admin/cmd/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cmd: s, cwd: cwd, timeout_sec: timeoutSec }),
      });
      const job = j.job || {};
      state.activeCmdJobId = String(job.job_id || "");
      state.terminalActiveKind = "cmd";
      startPolling(state.activeCmdJobId, "cmdOutput", "cmdJobId", () => {
        state.activeCmdJobId = "";
        if (state.terminalActiveKind === "cmd") state.terminalActiveKind = "";
      });
    } catch (e) {
      setText("cmdOutput", "RUN ERROR: " + String(e));
    }
  }

  async function cmdKill() {
    const jid = String(state.activeCmdJobId || "").trim();
    if (!jid) {
      setText("cmdOutput", "No active job.");
      return;
    }
    try {
      await apiJson("/api/admin/cmd/kill", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jid }),
      });
      // Let poll loop observe the exit.
      setText("cmdOutput", (el("cmdOutput").textContent || "") + "\n[kill requested]");
    } catch (e) {
      setText("cmdOutput", "KILL ERROR: " + String(e));
    }
  }

  function cmdClear() {
    stopPolling("cmdOutput");
    state.activeCmdJobId = "";
    if (state.terminalActiveKind === "cmd") state.terminalActiveKind = "";
    setText("cmdJobId", "-");
    setText("cmdOutput", "");
  }

  function clearCommandAndTerminal() {
    const input = optEl("cmdText");
    if (input) input.value = "";
    cmdClear();
  }

  async function copyCommandText() {
    const input = optEl("cmdText");
    const txt = input ? String(input.value || "") : "";
    if (!txt.trim()) return;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(txt);
        return;
      }
    } catch (_e) {}
    try {
      if (input) {
        input.focus();
        input.select();
        document.execCommand("copy");
      }
    } catch (_e) {}
  }

  async function runGitStatusCommand() {
    await cmdRun("git status");
  }

  async function ctrlCTerminal() {
    const jid = String(state.activeCmdJobId || state.activeGitJobId || "").trim();
    if (!jid) {
      setText("cmdOutput", (optEl("cmdOutput") ? String(el("cmdOutput").textContent || "") : "") + "\n[no active job]");
      return;
    }
    try {
      await apiJson("/api/admin/cmd/kill", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jid }),
      });
      setText("cmdOutput", (optEl("cmdOutput") ? String(el("cmdOutput").textContent || "") : "") + "\n[ctrl-c requested]");
    } catch (e) {
      setText("cmdOutput", "CTRL-C ERROR: " + String(e));
    }
  }

  // ---------------------------------------------------------------------------
  // Git (dedicated container)
  // ---------------------------------------------------------------------------

  async function gitRefresh() {
    setClass("cardGit", "updating", true);
    try {
      const j = await apiJson("/api/admin/git/status" + tokenQuery(false));
      state.gitBaselineSuggested = String(j.baseline_tag_suggested || j.baseline_tag_max || "").trim();
      setText("gitBranchWorktree", j.branch || "-");
      setText("gitHeadId", j.head || "-");
      setText("gitBaselineName", state.gitBaselineSuggested || "-");
      _setGitRunEnabled();
      setClass("cardGit", "stale", false);
      state.hasEverLoadedGit = true;
    } catch (e) {
      if (!state.hasEverLoadedGit) {
        setText("gitBranchWorktree", "ERROR");
        setText("gitHeadId", "-");
        setText("gitBaselineName", "-");
      }
      setClass("cardGit", "stale", true);
    } finally {
      setClass("cardGit", "updating", false);
    }
  }

  function gitClearJobOut() {
    stopPolling("gitJobOut");
    state.activeGitJobId = "";
    setText("gitJobId", "-");
    setText("gitJobOut", "");
  }

  function _normalizeBaselineId(raw) {
    const s = String(raw || "").trim();
    if (!s) return "";
    // Accept "200" or "baseline-200".
    if (/^\d+$/.test(s)) return "baseline-" + s;
    return s;
  }

  function _renderGitJournalStatus(journal) {
    const j = journal && typeof journal === "object" ? journal : {};
    const pending = Number(j.pending_count || 0);
    const invalid = Number(j.invalid_count || 0);
    const uncovered = Array.isArray(j.uncovered_paths) ? j.uncovered_paths.length : 0;
    if (pending <= 0) return "Journal: empty";
    if (invalid > 0) return `Journal: ${pending} pending, ${invalid} invalid`;
    if (j.ready && uncovered > 0) return `Journal: ready + ${uncovered} reconstructed paths`;
    if (j.ready) return `Journal: ready (${pending} pending)`;
    if (j.coverage_ok === false && uncovered > 0) return `Journal: ${pending} pending, ${uncovered} uncovered paths`;
    return `Journal: ${pending} pending`;
  }

  function _setGitRunEnabled() {
    const btn = optEl("btnCommit");
    if (!btn) return;
    btn.disabled = !state.gitBaselineSuggested;
  }

  async function gitRunOps() {
    const btn = optEl("btnCommit");
    const baseline = _normalizeBaselineId(state.gitBaselineSuggested);
    if (!baseline) {
      setText("cmdOutput", "Missing baseline suggestion.");
      return;
    }

    if (btn) btn.disabled = true;
    setText("cmdOutput", "[starting commit job]\n");

    try {
      const j = await apiJson("/api/admin/git/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          baseline: baseline,
          do_commit: true,
          do_tag: false,
          message_mode: "journal",
          message: "",
        }),
      });
      const job = j.job || {};
      state.activeGitJobId = String(job.job_id || "");
      state.terminalActiveKind = "git";
      startPolling(state.activeGitJobId, "cmdOutput", "gitJobId", () => {
        state.activeGitJobId = "";
        if (state.terminalActiveKind === "git") state.terminalActiveKind = "";
        gitRefresh();
        _setGitRunEnabled();
      });
    } catch (e) {
      setText("cmdOutput", "COMMIT ERROR: " + String(e));
      if (btn) btn.disabled = false;
    }
  }

  async function gitKill() {
    const jid = String(state.activeGitJobId || "").trim();
    if (!jid) {
      setText("gitJobOut", "No active git job.");
      return;
    }
    try {
      await apiJson("/api/admin/cmd/kill", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jid }),
      });
      setText("gitJobOut", (el("gitJobOut").textContent || "") + "\n[kill requested]");
    } catch (e) {
      setText("gitJobOut", "KILL ERROR: " + String(e));
    }
  }

  // ---------------------------------------------------------------------------
  // Server restart
  // ---------------------------------------------------------------------------

  async function restartServer() {
    const ok = window.confirm(
      "Restart the plane now? Only do this when it is idle. Active tasks, open loops, or live scheduler claims will block the restart.",
    );
    if (!ok) return;

    const badge = el("healthBadge");
    const note = optEl("serverStatusNote");

    try {
      await apiJson("/api/admin/server/restart", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } catch (e) {
      const payload = (e && e.payload) ? e.payload : {};
      const blockers = Array.isArray(payload.blockers)
        ? payload.blockers.map((row) => String((row && row.summary) || (row && row.kind) || "")).filter(Boolean)
        : [];
      const detail = blockers.length ? (": " + blockers.join("; ")) : "";
      if (note) {
        note.textContent = String((payload && payload.message) || String(e || "restart failed")) + detail;
      }
      appendPatchOutput("[restart] " + String((payload && payload.message) || e) + detail);
      try { await refreshHealth(); } catch (_e) {}
      return;
    }

    if (badge) {
      badge.className = "badge badge-muted";
      badge.textContent = "restarting";
    }
    if (note) {
      note.textContent = "Restart accepted. Waiting for the plane to come back.";
    }

    // Poll /api/health until it comes back, then reload.
    let tries = 0;
    const tick = async () => {
      tries += 1;
      try {
        await fetch("/api/health", { cache: "no-store" });
        window.location.reload();
      } catch (_e) {
        if (tries < 40) {
          setTimeout(tick, 500);
        } else {
          window.location.reload();
        }
      }
    };
    setTimeout(tick, 700);
  }

  async function refreshRuntime() {
    const out = await apiJson("/api/admin/server/refresh_runtime", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    appendPatchOutput(
      "[runtime] " +
        String(
          (out && out.message) ||
            "Live runtime settings refreshed for future work. In-flight turns/jobs keep prior settings.",
        ),
    );
    return out;
  }

function wire() {
    bind("btnServerRefresh", "click", async () => {
      try {
        await refreshRuntime();
        refreshHealth();
        await loadConfig();
        await gitRefresh();
      } catch (_e) {}
    });
    bind("btnRestartServer", "click", restartServer);
    bind("btnCommit", "click", gitRunOps);
    bind("btnSendCmd", "click", () => cmdRun());
    bind("btnDeploySite", "click", () => {
      window.location.href = "/mobile/update?channel=debug";
    });
    bind("btnCmdClearAll", "click", clearCommandAndTerminal);
    bind("btnCopyCommand", "click", copyCommandText);
    bind("btnGitStatusCmd", "click", runGitStatusCommand);
    bind("btnCtrlC", "click", ctrlCTerminal);
    _setGitRunEnabled();
  }

  async function initRefresh() {
    try {
      refreshHealth();
      await loadConfig();
      await gitRefresh();
      _setGitRunEnabled();
    } catch (e) {
      try { setText("cmdOutput", (optEl("cmdOutput") ? String(el("cmdOutput").textContent || "") : "") + "\n[initRefresh] " + String(e)); } catch (_e) {}
      try { appendPatchOutput("[initRefresh] " + String(e)); } catch (_e) {}
    }
  }


  function boot() {
    try{
      const params = new URLSearchParams(window.location.search);
      state.tokenFromUrl = String(params.get("token") || "").trim();
      const dmode = _computeDefaultMode();
      state.autoRefreshMode = dmode;
      state.autoRefreshMs = _modeToMs(dmode);
      const modeEl = optEl("refreshMode");
      if (modeEl) modeEl.value = dmode;
      const cb = optEl("refreshPauseHidden");
      if (cb) cb.checked = true;
      state.pauseWhenHidden = true;
      _updateRefreshStateLabel();
    }catch(_e){}
    try { wire(); } catch (_e) {}
    // Kick initial refresh after current call stack so late DOM exists.
    try { setTimeout(()=>{ try{ initRefresh(); }catch(_e){} }, 0); } catch (_e) { try { initRefresh(); } catch (_e2) {} }
    try { _startAutoRefresh(); } catch (_e) {}
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    try { setTimeout(boot, 0); } catch (_e) { boot(); }
  }
})();
