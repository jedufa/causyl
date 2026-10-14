/* infra/webui/static/tooltip.js
 * Shared delayed tooltip helper.
 *
 * Contract:
 * - Any element with data-tt="<key>" gets a tooltip.
 * - Hover (desktop) or focus (keyboard) shows after delay.
 * - Touch: long-press (~550ms) shows.
 * - Tooltip hides on leave/blur/escape/scroll or if target disappears.
 */

(function(){
  "use strict";

  // Tiny health flag so app.js (or humans with DevTools) can tell whether this
  // script actually loaded.
  try{ window.__TT_LOADED = true; }catch(_e){}

  const TT_DELAY_MS = 750;
  const TT_FOCUS_DELAY_MS = 150;
  const TT_LONGPRESS_MS = 550;

  const DEFINITIONS = {
    // =========================
    // Core statuses / run state
    // =========================
    status_running: "RUNNING: the runner is advancing generations right now.",
    status_planned: "PLANNED: the run exists (queued/declared) but hasn't started executing yet.",
    status_starting: "STARTING: process is launching / warming up (expect short delay).",
    status_paused: "PAUSED: execution is intentionally held. No generations advance until resumed.",
    status_complete: "COMPLETE: run reached its planned end condition (e.g., target generations).",
    status_incomplete: "INCOMPLETE: run ended early without meeting its planned end condition.",
    status_failed: "FAILED: run exited with an error. Check stderr/logs and last events for the root cause.",
    status_stalled: "STALLED: runner is alive but progress is not moving. Common causes: deadlock, I/O wait, lock contention.",
    status_unknown: "UNKNOWN: status could not be determined from available heartbeat/state files.",

    // =========================
    // Determinism / replay
    // =========================
    det_pass: "Determinism PASS: replay/hash checks matched. Same inputs produced the same outputs under current rules.",
    det_fail: "Determinism FAIL: replay/hash checks mismatched. Some source of nondeterminism or drift exists.",
    det_unknown: "Determinism UNKNOWN: no replay/hash verdict recorded for this run (yet).",

    replay: "Replay: re-run from the same initial state and compare outcomes (hashes/events) to confirm determinism.",
    hash: "Hash: compact fingerprint of run state/artifacts used to detect drift and verify replay determinism.",
    wal: "WAL (write-ahead log): append-only record of events/actions used for traceability and replay audits.",

    // =========================
    // Governance / locks
    // =========================
    strict: "STRICT mode: governance rules enforced (locks, traceability, required metadata, safe-boundary application).",
    non_strict: "NON-STRICT mode: governance rules relaxed. Faster iteration, higher risk of messy/irreproducible runs.",

    campaign_freeze: "Campaign freeze: the campaign configuration is frozen. Changes require a new campaign or an explicit unlock procedure.",
    plan_lock: "Plan lock: intervention plan is locked to preserve traceability. New actions may be rejected until unlocked.",
    phase_freeze: "Phase freeze: only specific phases can accept actions. Commands targeting other phases are rejected.",
    safe_boundary: "Safe boundary: a permitted point in the sim loop where interventions may be applied without corrupting invariants.",

    // =========================
    // Identity / addressing
    // =========================
    baseline: "Baseline: the code/config baseline this run was created from (used for reproducibility and comparison).",
    git_ref: "git_ref: the repository commit hash used to build/launch the run. Required for reproducible replay.",
    seed: "Seed: the deterministic RNG seed for the run (must be recorded to reproduce).",
    run_id: "Run ID: unique identifier for this run instance (often includes experiment name, baseline, seed, timestamp).",
    manifest: "Manifest: a batch definition that launches many runs with controlled variations (sweep / campaign).",
    managed: "Managed run: launched and supervised by the harness/manifest system (lifecycle owned by the plane).",
    unmanaged: "Unmanaged run: started manually; UI can observe but may not control lifecycle the same way.",

    // =========================
    // Metrics (EIEL-focused)
    // =========================
    epi: "EPI (Evolution Progress Index): 0–100 aggregate score of how well organisms perform under the evaluation pressures. Higher is better.",
    av: "AV (Adaptation Velocity): how fast EPI is changing. Positive = improving, near-zero = plateau, negative = regression.",
    verified_gen: "Verified generation: highest generation index that has been determinism-verified (hash/replay checks).",
    diversity: "Diversity: how varied the population is (genetic/behavioral). Too low risks premature convergence.",
    novelty: "Novelty: how much new behavior/structure is appearing versus repeating known patterns.",
    fitness: "Fitness: selection score used by the evolutionary loop (exact definition is experiment-specific).",

    // =========================
    // Plane actions
    // =========================
    checkpoint: "Checkpoint: saved state snapshot at a generation boundary. Useful for debugging, replay, or recovery (can be huge).",
    inbox_queue: "Inbox: queued commands waiting to be applied at the next safe boundary (or rejected by locks).",
    applied_commands: "Applied commands: interventions that were accepted and executed (should also appear in event/WAL logs).",
    changeset: "ChangeSet: named bundle of parameter changes/actions applied together for traceability.",
    expectation_note: "Expectation note: short rationale for an action (why you're doing it and what you expect to happen).",
    autopilot_enable: "Enable/disable pressure autopilot. Disabled means no new autopilot interventions are scheduled.",
    autopilot_apply_gen: "Generation at which this command is applied. Use current_generation + 1 to avoid time-travel rejects.",
    autopilot_tick_interval: "How often the autopilot evaluates and may schedule updates. Lower = more frequent interventions.",
    autopilot_profile: "Which pressure axis is controlled: balanced (all), cycle (rotates), or single-axis scarcity/cost/shock.",
    autopilot_delta_up: "Per decision, how much pressure increases when autopilot decides to make conditions harder.",
    autopilot_delta_down: "Per decision, how much pressure decreases when autopilot decides to make conditions easier.",
    autopilot_target_survival: "Controller's target cohort survival rate. Higher target generally makes the environment easier.",
    autopilot_survival_ceiling: "If survival exceeds this level, controller tends to increase pressure more decisively.",
    autopilot_active_values: "Best-effort current values derived from applied commands, with run_config fallback when available.",

    // =========================
    // Views (navigation)
    // =========================
    view_overview: "Overview: single-run dashboard (status, key metrics, recent events, and controls).",
    view_matrix: "Matrix: compare many runs in a grid to spot outliers, regressions, missing data, and suspicious patterns.",
    view_backlog: "Ledger: patch/PIS queue. Track status, select items, and trigger integration workflows.",
    view_docs: "Docs: project documentation (MkDocs site).",
    view_admin: "Admin: infrastructure/health (containers, disk, terminal, diagnostics)."
  };

  function _getDef(key){
    const k = String(key||"").trim();
    return DEFINITIONS[k] || "";
  }

  function _applyNativeTitles(root){
    // Even if our custom tooltip box doesn't show for some reason, `title`
    // gives a baseline tooltip everywhere.
    try{
      const scope = root && root.querySelectorAll ? root : document;
      const nodes = scope.querySelectorAll("[data-tt]");
      for(const n of nodes){
        try{
          if(n.getAttribute("title")) continue;
          const key = n.getAttribute("data-tt");
          const def = _getDef(key);
          if(def) n.setAttribute("title", def);
        }catch(_e){}
      }
    }catch(_e){}
  }

  function _ensureBox(){
    let box = document.getElementById("__tt_box");
    if(box) return box;
    box = document.createElement("div");
    box.id = "__tt_box";
    box.className = "tt-box hidden";
    box.setAttribute("role", "tooltip");
    document.body.appendChild(box);
    return box;
  }

  let activeTarget = null;
  let showTimer = null;
  let longPressTimer = null;
  let lastPointer = {x: 0, y: 0};
  let pendingKey = "";
  let pendingMode = ""; // 'hover' | 'focus'

  function _updatePointerFromEvent(ev){
    try{
      if(!ev) return;
      // Mouse events
      if(typeof ev.clientX === "number" && typeof ev.clientY === "number"){
        lastPointer.x = ev.clientX;
        lastPointer.y = ev.clientY;
        return;
      }
      // Touch events
      const t = (ev.touches && ev.touches[0]) || (ev.changedTouches && ev.changedTouches[0]);
      if(t && typeof t.clientX === "number" && typeof t.clientY === "number"){
        lastPointer.x = t.clientX;
        lastPointer.y = t.clientY;
      }
    }catch(_e){}
  }

  function _resolveTargetAtPointer(){
    try{
      const el = document.elementFromPoint(lastPointer.x, lastPointer.y);
      return _closestTT(el);
    }catch(_e){
      return null;
    }
  }

  function _hide(){
    const box = document.getElementById("__tt_box");
    if(!box) return;
    box.classList.add("hidden");
    box.textContent = "";
    if(activeTarget){
      try{ activeTarget.removeAttribute("aria-describedby"); }catch(_e){}
    }
    activeTarget = null;
  }

  function _position(box, target){
    const r = target.getBoundingClientRect();
    const pad = 10;
    // default below-left
    let x = r.left;
    let y = r.bottom + 8;

    // measure
    box.style.left = "0px";
    box.style.top = "0px";
    box.classList.remove("hidden");
    const bw = box.offsetWidth;
    const bh = box.offsetHeight;

    const vw = window.innerWidth || 0;
    const vh = window.innerHeight || 0;

    // clamp within viewport
    if(x + bw + pad > vw) x = Math.max(pad, vw - bw - pad);
    if(y + bh + pad > vh){
      // flip above
      y = Math.max(pad, r.top - bh - 8);
    }
    x = Math.max(pad, x);
    y = Math.max(pad, y);

    box.style.left = Math.round(x) + "px";
    box.style.top = Math.round(y) + "px";
  }

  function _showFor(target){
    if(!target || !target.isConnected) return;
    const key = target.getAttribute("data-tt");
    const def = _getDef(key);
    if(!def) return;

    const box = _ensureBox();
    box.textContent = def;
    activeTarget = target;

    // link for a11y
    try{ target.setAttribute("aria-describedby", "__tt_box"); }catch(_e){}

    _position(box, target);
  }

  function _showPending(){
    // During auto-refresh, the original DOM node is frequently replaced
    // while the pointer stays in the same screen position.
    // Instead of trusting a stale node reference, re-resolve the target.
    try{
      if(pendingMode === "focus"){
        // Focus-based tooltips should respect the focused element.
        const t = _closestTT(document.activeElement);
        if(t){
          const k = String(t.getAttribute("data-tt")||"").trim();
          if(!pendingKey || k === pendingKey) return _showFor(t);
        }
        return;
      }

      const t = _resolveTargetAtPointer();
      if(!t) return;
      const k = String(t.getAttribute("data-tt")||"").trim();
      if(pendingKey && k !== pendingKey) return;
      _showFor(t);
    }catch(_e){}
  }

  function _scheduleShow(target, delayMs){
    try{ if(showTimer) clearTimeout(showTimer); }catch(_e){}
    pendingKey = String((target && target.getAttribute) ? (target.getAttribute("data-tt")||"") : "").trim();
    showTimer = setTimeout(()=>{ _showPending(); }, Math.max(0, delayMs|0));
  }

  function _cancelTimers(){
    try{ if(showTimer) clearTimeout(showTimer); }catch(_e){}
    try{ if(longPressTimer) clearTimeout(longPressTimer); }catch(_e){}
    showTimer = null;
    longPressTimer = null;
  }

  function _closestTT(el){
    if(!el) return null;
    try{ return el.closest ? el.closest("[data-tt]") : null; }catch(_e){ return null; }
  }

  // Pointer hover (more reliable than mouseenter/mouseleave across browsers)
  // NOTE: mouseenter does not bubble; some browsers are inconsistent about
  // document-level capture listeners for it. pointerover/out is the boring
  // reliable choice.
  function _sameTT(a, b){
    const ta = _closestTT(a);
    const tb = _closestTT(b);
    return !!(ta && tb && ta === tb);
  }

  document.addEventListener("pointerover", (ev)=>{
    _updatePointerFromEvent(ev);
    // Avoid re-triggering when moving within the same tooltip-bearing element.
    if(_sameTT(ev.target, ev.relatedTarget)) return;
    const t = _closestTT(ev.target);
    if(!t) return;
    // Only hover-delay for mouse/pen. Touch uses long-press below.
    const pt = String(ev.pointerType || "");
    if(pt === "touch") return;
    _cancelTimers();
    pendingMode = "hover";
    _scheduleShow(t, TT_DELAY_MS);
  }, {capture:true, passive:true});

  document.addEventListener("pointerout", (ev)=>{
    _updatePointerFromEvent(ev);
    if(_sameTT(ev.target, ev.relatedTarget)) return;
    const t = _closestTT(ev.target);
    if(!t) return;
    _cancelTimers();
    _hide();
  }, {capture:true, passive:true});

  // Track pointer position continuously so re-resolution works even if
  // the element is replaced under the cursor.
  document.addEventListener("pointermove", (ev)=>{ _updatePointerFromEvent(ev); }, {passive:true, capture:true});

  // Keyboard focus
  document.addEventListener("focusin", (ev)=>{
    const t = _closestTT(ev.target);
    if(!t) return;
    _cancelTimers();
    pendingMode = "focus";
    _scheduleShow(t, TT_FOCUS_DELAY_MS);
  }, true);

  document.addEventListener("focusout", (ev)=>{
    const t = _closestTT(ev.target);
    if(!t) return;
    _cancelTimers();
    _hide();
  }, true);

  // Touch long-press (use pointer events so it also works on hybrid devices)
  let longPressStart = {x:0, y:0};
  function _dist2(a,b){ const dx=a.x-b.x; const dy=a.y-b.y; return dx*dx+dy*dy; }

  document.addEventListener("pointerdown", (ev)=>{
    const pt = String(ev.pointerType || "");
    if(pt !== "touch") return;
    _updatePointerFromEvent(ev);
    const t = _closestTT(ev.target);
    if(!t) return;
    _cancelTimers();
    pendingMode = "hover";
    pendingKey = String(t.getAttribute("data-tt")||"").trim();
    longPressStart = {x:lastPointer.x, y:lastPointer.y};
    longPressTimer = setTimeout(()=>{ _showFor(t); }, TT_LONGPRESS_MS);
  }, {passive:true, capture:true});

  document.addEventListener("pointerup", (ev)=>{
    const pt = String(ev.pointerType || "");
    if(pt !== "touch") return;
    _cancelTimers();
    _hide();
  }, {passive:true, capture:true});

  document.addEventListener("pointercancel", (ev)=>{
    const pt = String(ev.pointerType || "");
    if(pt !== "touch") return;
    _cancelTimers();
    _hide();
  }, {passive:true, capture:true});

  // If user moves finger, cancel long-press (prevents accidental tooltips while scrolling)
  document.addEventListener("pointermove", (ev)=>{
    const pt = String(ev.pointerType || "");
    if(pt !== "touch") return;
    _updatePointerFromEvent(ev);
    if(longPressTimer){
      // ~10px threshold
      if(_dist2(longPressStart, lastPointer) > 100){
        _cancelTimers();
        _hide();
      }
    }
  }, {passive:true, capture:true});

  // Global dismiss
  document.addEventListener("keydown", (ev)=>{
    if(ev.key === "Escape") _hide();
  });
  window.addEventListener("scroll", ()=>{ _hide(); }, {passive:true});

  // Auto-refresh safety: if active target disappears, hide.
  setInterval(()=>{
    if(activeTarget && !activeTarget.isConnected){
      // Try to keep the tooltip alive across rerenders.
      const oldKey = String(activeTarget.getAttribute("data-tt")||"").trim();
      const t = _resolveTargetAtPointer();
      const newKey = t ? String(t.getAttribute("data-tt")||"").trim() : "";
      if(t && oldKey && newKey === oldKey){
        _showFor(t);
      }else{
        _hide();
      }
    }
  }, 250);

  // Initial + best-effort dynamic title injection.
  _applyNativeTitles(document);
  try{
    const mo = new MutationObserver((mutations)=>{
      for(const m of mutations){
        for(const n of (m.addedNodes||[])){
          if(n && n.querySelectorAll) _applyNativeTitles(n);
        }
      }
    });
    mo.observe(document.documentElement, {subtree:true, childList:true});
  }catch(_e){}

})();
