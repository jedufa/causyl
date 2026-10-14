window.__EIEL_APP_SHARED_JS_LOADED__ = true;

/* infra/webui/static/app_shared.js
 * EIEL Web UI shared helpers extracted from app.js.
 * Last modified: 2026-03-05 Europe/Vienna
 */

"use strict";

(function () {
  function q(sel, root){
    return (root || document).querySelector(sel);
  }
  function qa(sel, root){
    return Array.from((root || document).querySelectorAll(sel));
  }
  function safeOn(el, evt, fn){
    if(!el) return;
    el.addEventListener(evt, fn);
  }

  function escapeHtml(s){
    return String(s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
  }

  function badgeForStatus(status){
    const s = String(status || "").toUpperCase();
    if(!s) return '<span class="badge badge-muted" data-tt="status_unknown" tabindex="0">?</span>';
    if(s === "COMPLETE") return '<span class="badge badge-ok" data-tt="status_complete" tabindex="0">COMPLETE</span>';
    if(s === "RUNNING") return '<span class="badge badge-running" data-tt="status_running" tabindex="0">RUNNING</span>';
    if(s === "FAILED") return '<span class="badge badge-danger" data-tt="status_failed" tabindex="0">FAILED</span>';
    if(s === "INCOMPLETE") return '<span class="badge badge-warn" data-tt="status_incomplete" tabindex="0">INCOMPLETE</span>';
    if(s === "STALLED") return '<span class="badge badge-muted" data-tt="status_stalled" tabindex="0">STALLED</span>';
    if(s === "PLANNED") return '<span class="badge badge-planned" data-tt="status_planned" tabindex="0">PLANNED</span>';
    if(s === "POSSIBLY_CORRUPT") return '<span class="badge badge-warn" data-tt="status_unknown" tabindex="0">POSSIBLY_CORRUPT</span>';
    return `<span class="badge badge-muted" data-tt="status_unknown" tabindex="0">${escapeHtml(s)}</span>`;
  }

  function badgeForDeterminism(det){
    const s = String(det || "").toUpperCase();
    if(!s) return '<span class="badge badge-muted" data-tt="det_unknown" tabindex="0">UNKNOWN</span>';
    if(s === "PASS") return '<span class="badge badge-ok" data-tt="det_pass" tabindex="0">PASS</span>';
    if(s === "FAIL") return '<span class="badge badge-danger" data-tt="det_fail" tabindex="0">FAIL</span>';
    return '<span class="badge badge-muted" data-tt="det_unknown" tabindex="0">UNKNOWN</span>';
  }

  function badgeForStrict(strict){
    return strict
      ? '<span class="badge badge-accent" data-tt="strict" tabindex="0">STRICT</span>'
      : '<span class="badge badge-muted" data-tt="non_strict" tabindex="0">NON-STRICT</span>';
  }

  function showGlobalError(title, msg){
    const box = q("#globalError");
    if(!box) return;
    const t = q("#globalErrorTitle");
    const b = q("#globalErrorBody");
    if(t) t.textContent = String(title ?? "Error");
    if(b) b.textContent = String(msg ?? "");
    if(!t || !b) box.textContent = `${title ?? "Error"}\n${msg ?? ""}`;
    box.classList.remove("hidden");
  }

  function clearGlobalError(){
    const box = q("#globalError");
    if(!box) return;
    box.classList.add("hidden");
  }

  function showToast(title, body, ms){
    const root = q("#toastRoot");
    if(!root) return;
    const el = document.createElement("div");
    el.className = "toast";
    el.innerHTML = `<div class="t-title">${escapeHtml(title ?? "")}</div><div class="t-body">${escapeHtml(body ?? "")}</div>`;
    root.appendChild(el);
    const timeout = Math.max(1200, ms || 2600);
    setTimeout(()=>{ try{ el.remove(); }catch(_e){} }, timeout);
  }

  function toast(title, body, ms){
    showToast(String(title ?? ""), String(body ?? ""), ms);
  }

  function setHealth(ok){
    const dot = q("#healthDot");
    if(!dot) return;
    dot.classList.toggle("ok", !!ok);
    dot.classList.toggle("bad", !ok);
  }

  let _lastFocusBeforeModal = null;

  function openModal(title, bodyHtml, onOpen){
    const overlay = q("#modalOverlay");
    const titleEl = q("#modal-title");
    const bodyEl = q("#modal-body");
    const modalEl = q("#modal");
    const closeBtn = q("#modal-close");

    if(titleEl) titleEl.textContent = String(title ?? "Modal");
    if(bodyEl) bodyEl.innerHTML = bodyHtml ?? "";
    if(!overlay) return;

    try{ _lastFocusBeforeModal = document.activeElement; }catch(_e){ _lastFocusBeforeModal = null; }
    overlay.classList.remove("hidden");
    overlay.setAttribute("aria-hidden", "false");
    try{ document.body.classList.add("modal-open"); }catch(_e){}
    try{ overlay.scrollTop = 0; }catch(_e){}
    try{ if(bodyEl) bodyEl.scrollTop = 0; }catch(_e){}
    try{
      if(modalEl && modalEl.scrollIntoView){
        modalEl.scrollIntoView({block:"start", inline:"nearest"});
      }
    }catch(_e){}

    if(typeof onOpen === "function"){
      try{
        const scope = modalEl || bodyEl || overlay;
        onOpen(scope);
      }catch(e){
        try{ console.error("[modal] onOpen failed:", e); }catch(_e){}
      }
    }

    try{ if(closeBtn && closeBtn.focus) closeBtn.focus({preventScroll:true}); }catch(_e){}
  }

  function closeModal(){
    const overlay = q("#modalOverlay");
    if(overlay){
      overlay.classList.add("hidden");
      overlay.setAttribute("aria-hidden", "true");
    }
    try{ document.body.classList.remove("modal-open"); }catch(_e){}
    try{
      const prev = _lastFocusBeforeModal;
      _lastFocusBeforeModal = null;
      if(prev && prev.focus) prev.focus({preventScroll:true});
    }catch(_e){}
  }

  async function fetchText(url, opts){
    const res = await fetch(url, opts);
    const txt = await res.text().catch(()=>"");
    if(!res.ok){
      throw new Error(`${res.status} ${res.statusText}${txt ? " :: " + txt : ""}`.trim());
    }
    return txt;
  }

  async function fetchJson(url, opts){
    const txt = await fetchText(url, opts);
    try{ return JSON.parse(txt); }catch(e){
      throw new Error(`Invalid JSON from ${url}: ${e}`);
    }
  }

  async function postJson(url, obj){
    return await fetchJson(url, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(obj || {}),
    });
  }

  window.EIELShared = {
    q,
    qa,
    safeOn,
    escapeHtml,
    badgeForStatus,
    badgeForDeterminism,
    badgeForStrict,
    showGlobalError,
    clearGlobalError,
    showToast,
    toast,
    setHealth,
    openModal,
    closeModal,
    fetchText,
    fetchJson,
    postJson,
  };
})();
