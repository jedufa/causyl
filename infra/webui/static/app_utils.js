window.__EIEL_APP_UTILS_JS_LOADED__ = true;

/* infra/webui/static/app_utils.js
 * EIEL Web UI utility helpers extracted from app.js.
 * Last modified: 2026-03-05 Europe/Vienna
 */

"use strict";

(function () {
  function copyTextToClipboard(txt){
    const t = String(txt ?? "");
    try{
      if(globalThis.navigator && navigator.clipboard && typeof navigator.clipboard.writeText === "function"){
        return navigator.clipboard.writeText(t).then(()=>true).catch(()=>false);
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
      return Promise.resolve(true);
    }catch(_e){}
    return Promise.resolve(false);
  }

  function shortId(s, max){
    const t = String(s || "");
    const m = max || 26;
    if(t.length <= m) return t;
    return t.slice(0, m - 1) + "…";
  }

  function _displayHashTail(){
    for(let i = 0; i < arguments.length; i += 1){
      const txt = String(arguments[i] || "").trim();
      if(!txt) continue;
      const toks = txt.split(/[^A-Za-z0-9]+/).filter(Boolean);
      for(let j = toks.length - 1; j >= 0; j -= 1){
        const tok = toks[j];
        if(/^[0-9a-fA-F]{6,12}$/.test(tok)) return tok.slice(-4).toLowerCase();
      }
    }
    const joined = Array.from(arguments).map((v)=>String(v || "").trim()).filter(Boolean).join("||");
    if(!joined) return "0000";
    let h = 0;
    for(let i = 0; i < joined.length; i += 1){
      h = ((h << 5) - h + joined.charCodeAt(i)) | 0;
    }
    return Math.abs(h).toString(16).padStart(4, "0").slice(-4);
  }

  function _displaySlug(){
    const noise = new Set(["experiments","harnesses","harness","generated","scripts","runs","runtime","live","wrapper","wrapperjobs","wrapperjob","payload","python","module","main","default","baseline","seed","gen","run","job","planned"]);
    for(let i = 0; i < arguments.length; i += 1){
      let txt = String(arguments[i] || "").trim();
      if(!txt) continue;
      txt = txt.replace(/\\/g, "/");
      if(txt.includes("/")) txt = txt.split("/").pop();
      if(txt.includes(".") && !txt.startsWith(".")) txt = txt.split(".").pop();
      txt = txt.replace(/(__+)?harness$/i, "");
      txt = txt.replace(/[_-]*(py|json|yaml|yml|log)$/i, "");
      const toks = txt.split(/[^A-Za-z0-9]+/).filter(Boolean).map((t)=>t.toLowerCase());
      const words = [];
      let tailHint = "";
      toks.forEach((tok)=>{
        if(noise.has(tok)) return;
        if(/^\d{6,14}$/.test(tok)) return;
        if(/^seed\d+$/.test(tok) || /^gen\d+$/.test(tok)) return;
        if(/^[0-9a-f]{6,12}$/i.test(tok)){ tailHint = tok; return; }
        if(/[a-z]/i.test(tok)) words.push(tok);
      });
      if(!words.length && tailHint) words.push("item");
      if(!words.length) continue;
      let slug = words.slice(0, 3).join("-");
      if(tailHint && !slug.includes(tailHint.slice(-6).toLowerCase())){
        slug += "-" + tailHint.slice(-6).toLowerCase();
      }
      slug = slug.replace(/-{2,}/g, "-").replace(/^-+|-+$/g, "");
      if(slug) return slug.slice(0, 48);
    }
    return "item";
  }

  function displayId(obj, opts){
    const item = obj || {};
    const options = opts || {};
    const explicit = String(item.display_id || "").trim();
    if(explicit) return explicit;
    const canonical = String(item.canonical_id || item.run_key || item.job_id || item.workflow_id || item.ledger_uid || item.module || item.name || item.path || "").trim();
    const slug = String(item.display_slug || "").trim() || _displaySlug(item.label, item.selected_target, item.launch_target_module, item.module, item.name, canonical);
    const suffix = String(item.display_suffix || "").trim() || _displayHashTail(item.job_spec_hash, item.request_id, item.job_id, item.run_key, item.module, item.name, canonical);
    const fallback = String(options.fallback || "").trim();
    return (slug && suffix) ? `${slug} · ${suffix}` : (slug || fallback || canonical || "item");
  }

  function fmtNum(x, digits){
    if(x === null || x === undefined || x === "") return "";
    const n = Number(x);
    if(!Number.isFinite(n)) return "";
    const d = (digits === undefined) ? 3 : digits;
    return n.toFixed(d).replace(/\.0+$/,"").replace(/(\.[0-9]*?)0+$/,"$1");
  }

  function fmtGen(x){
    const n = parseInt(String(x ?? ""), 10);
    if(!Number.isFinite(n)) return "";
    if(n < 0) return "";
    return String(n);
  }

  function csvListToArray(s){
    const raw = String(s || "").trim();
    if(!raw) return [];
    return raw.split(",").map(x=>x.trim()).filter(Boolean);
  }

  function maybeInt(s){
    const t = String(s ?? "").trim();
    if(!t) return null;
    const n = parseInt(t, 10);
    return Number.isFinite(n) ? n : null;
  }

  function genUuid(){
    try{
      if(globalThis.crypto && typeof globalThis.crypto.randomUUID === "function"){
        return globalThis.crypto.randomUUID();
      }
    }catch(_e){}
    const r = () => Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, "0");
    return `${r()}-${r().slice(0,4)}-${r().slice(0,4)}-${r().slice(0,4)}-${r()}${r().slice(0,4)}`;
  }

  function badge(text, cls, ttKey, escapeHtml){
    const esc = (typeof escapeHtml === "function") ? escapeHtml : (s=>String(s));
    const tt = String(ttKey || "").trim();
    const ttAttr = tt ? ` data-tt="${esc(tt)}" tabindex="0"` : "";
    return `<span class="badge ${cls || ""}"${ttAttr}>${esc(text)}</span>`;
  }

  function chip(text, ttKey, escapeHtml){
    const esc = (typeof escapeHtml === "function") ? escapeHtml : (s=>String(s));
    const tt = String(ttKey || "").trim();
    const ttAttr = tt ? ` data-tt="${esc(tt)}" tabindex="0"` : "";
    return `<span class="chip"${ttAttr}>${esc(text)}</span>`;
  }

  function statusBadgeClass(status){
    const s = String(status || "").toUpperCase();
    if(s === "RUNNING") return "b-running";
    if(s === "PLANNED") return "b-planned";
    if(s === "COMPLETE") return "b-complete";
    if(s === "FAILED") return "b-failed";
    if(s === "STALLED") return "b-stalled";
    if(s === "INCOMPLETE") return "b-incomplete";
    if(s === "POSSIBLY_CORRUPT") return "b-corrupt";
    return "b-unknown";
  }

  function downloadTextFile(filename, text, showToast){
    try{
      const blob = new Blob([String(text || "")], {type: "text/plain;charset=utf-8"});
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = String(filename || "download.txt");
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(()=>{ try{ URL.revokeObjectURL(url); }catch(_e){} }, 5000);
    }catch(e){
      if(typeof showToast === "function"){
        showToast("Download failed", String(e?.message || e));
      }
    }
  }

  window.EIELUtils = {
    copyTextToClipboard,
    shortId,
    displayId,
    fmtNum,
    fmtGen,
    csvListToArray,
    maybeInt,
    genUuid,
    badge,
    chip,
    statusBadgeClass,
    downloadTextFile,
  };
})();
