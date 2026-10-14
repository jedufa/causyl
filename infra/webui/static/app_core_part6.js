window.__EIEL_APP_CORE_PART6_LOADED__ = true;
// -----------------------------
// Wizards
// -----------------------------

function openCommandWizard(runKey, ctx){
  const frozen = ctx?.frozenApplyPhase || null;
  const det = String(ctx?.runDeterminism || "UNKNOWN").toUpperCase();
  const defaultTarget = (ctx?.defaultTargetGeneration === null || ctx?.defaultTargetGeneration === undefined) ? "" : String(ctx.defaultTargetGeneration);

  const applyOpts = [
    "post-reproduction-pre-selection",
    "post-selection-pre-mutation",
  ];

  const uuid = genUuid();

  // Raw JSON defaults should not time-travel. Use the same default target generation
  // as the wizard input (usually current_gen + 1).
  const rawDefaultObj = {
    command_type: "TraitInjectionCommand",
    schema_version: "cmd_v2",
    target_generation: (defaultTarget ? _parseIntStrict(defaultTarget, 1) : 1),
    trait_injection: {
      schema_version: "trait_injection_v2",
      duration_generations: 1,
      apply_phase: frozen || "post-selection-pre-mutation",
      locus_tags: [],
      allele_values: {},
      target_agent_ids: [],
    },
  };
  const rawDefaultJson = JSON.stringify(rawDefaultObj, null, 2);

  const changesetList = (State.changesets || []).map(cs=>{
    const id = String(cs.id||"");
    const checked = State.planChangeSets.has(id);
    return `<label class="row" style="gap:8px;align-items:flex-start;">
      <input class="wizCs" type="checkbox" value="${escapeHtml(id)}" ${checked?"checked":""}/>
      <div>
        <div style="font-weight:700;">${escapeHtml(id)} <span class="muted" style="font-weight:400;">${escapeHtml(shortId(cs.label||"", 40))}</span></div>
        <div class="muted" style="font-size:12px;margin-top:2px;">${escapeHtml(shortId(changesetCmdSummary(cs), 70))}</div>
      </div>
    </label>`;
  }).join("\n");

  openModal(
    "Submit Command Wizard",
    `<div class="muted">Run: <b>${escapeHtml(runKey)}</b></div>
     <div class="row" style="margin-top:10px;gap:10px;">
       ${det === "FAIL" ? `<div class="badge b-fail">Determinism FAIL</div>` : (det === "PASS" ? `<div class="badge b-pass">Determinism PASS</div>` : `<div class="badge b-unknown">Determinism ${escapeHtml(det)}</div>`)}
       ${frozen ? `<div class="badge b-incomplete">apply_phase frozen: ${escapeHtml(frozen)}</div>` : `<div class="badge b-unknown">apply_phase not detected</div>`}
     </div>

     <div class="card" style="margin-top:12px;background:rgba(255,255,255,0.02);">
       <div style="font-weight:800;">1) Choose command type</div>
       <div class="row" style="margin-top:10px;gap:14px;">
         <label class="row" style="gap:6px;"><input type="radio" name="cmdType" value="trait" checked/> <span>Trait Injection (cmd_v2)</span></label>
         <label class="row" style="gap:6px;"><input type="radio" name="cmdType" value="changesets"/> <span>Apply ChangeSets</span></label>
         <label class="row" style="gap:6px;"><input type="radio" name="cmdType" value="raw"/> <span>Raw JSON (Advanced)</span></label>
       </div>
     </div>

     <div class="card" id="traitBlock" style="margin-top:12px;background:rgba(255,255,255,0.02);">
       <div style="font-weight:800;">2) Configure Trait Injection</div>
       <div class="grid-2" style="margin-top:10px;">
         <div>
           <div class="muted" style="margin-bottom:6px;">command_uuid</div>
           <input id="wizUuid" class="input" value="${escapeHtml(uuid)}"/>
         </div>
         <div>
           <div class="muted" style="margin-bottom:6px;">target_generation</div>
           <input id="wizTargetGen" class="input" type="number" min="0" value="${escapeHtml(defaultTarget)}"/>
         </div>
         <div>
           <div class="muted" style="margin-bottom:6px;">duration_generations</div>
           <input id="wizDuration" class="input" type="number" min="1" value="1"/>
         </div>
         <div>
           <div class="muted" style="margin-bottom:6px;">apply_phase</div>
           <select id="wizPhase" class="input">
             ${applyOpts.map(p=>`<option value="${escapeHtml(p)}" ${(frozen && frozen===p)?"selected":""}>${escapeHtml(p)}</option>`).join("")}
           </select>
           ${frozen ? `<div class="muted" style="margin-top:6px;font-size:12px;">Other phases are blocked by campaign freeze.</div>` : `<div class="muted" style="margin-top:6px;font-size:12px;">Engine may reject if campaign froze apply_phase.</div>`}
         </div>
       </div>

       <div class="grid-2" style="margin-top:10px;">
         <div>
           <div class="muted" style="margin-bottom:6px;">locus_tags (comma-separated)</div>
           <input id="wizLoci" class="input" placeholder="e.g. aggression_bias, risk" value=""/>
         </div>
         <div>
           <div class="muted" style="margin-bottom:6px;">target_agent_ids (comma-separated, optional)</div>
           <input id="wizAgents" class="input" placeholder="e.g. 12,13,14" value=""/>
         </div>
       </div>

       <div style="margin-top:10px;">
         <div class="muted" style="margin-bottom:6px;">allele_values (JSON object)</div>
         <textarea id="wizAlleles" class="textarea" style="min-height:120px;" spellcheck="false">{\n  "aggression_bias": 0.5\n}</textarea>
         <div class="muted" style="margin-top:6px;font-size:12px;">This must be valid JSON. Keys should match your locus tags (engine will validate further).</div>
       </div>

       <div class="card" style="margin-top:12px;background:rgba(0,0,0,0.12);">
         <div class="row" style="justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
           <div style="font-weight:800;">Trait picker</div>
           <button id="wizLoadTraits" class="btn small">Reload traits</button>
         </div>
         <div id="wizTraitStatus" class="muted" style="margin-top:6px;font-size:12px;">Loading traits from metrics…</div>
         <div id="wizTraitPicker" class="grid-2 hidden" style="margin-top:10px;">
           <div>
             <div class="muted" style="margin-bottom:6px;">Trait</div>
             <select id="wizTraitSelect" class="input"></select>
           </div>
           <div>
             <div class="muted" style="margin-bottom:6px;">Current mean</div>
             <div id="wizTraitCurrent" class="chip">-</div>
           </div>
           <div>
             <div class="muted" style="margin-bottom:6px;">New value</div>
             <input id="wizTraitNew" class="input" type="number" step="any" value=""/>
           </div>
           <div style="display:flex;align-items:flex-end;">
             <button id="wizTraitAdd" class="btn ok" style="width:100%;">Add</button>
           </div>
         </div>
         <div class="muted" style="margin-top:8px;font-size:12px;">Adds selection to <span class="mono">locus_tags</span> and <span class="mono">allele_values</span>.</div>
       </div>
     </div>

     <div class="card hidden" id="csBlock" style="margin-top:12px;background:rgba(255,255,255,0.02);">
       <div style="font-weight:800;">2) Select ChangeSets</div>
       <div class="muted" style="margin-top:8px;">Submitting ChangeSets will enqueue each command into the run's inbox.</div>
       <div style="margin-top:10px;display:flex;flex-direction:column;gap:10px;max-height:260px;overflow:auto;">
         ${changesetList || `<div class="muted">No ChangeSets available.</div>`}
       </div>
     </div>

     <div class="card hidden" id="rawBlock" style="margin-top:12px;background:rgba(255,255,255,0.02);">
       <div style="font-weight:800;">2) Raw JSON (Advanced)</div>
       <div class="muted" style="margin-top:8px;">If you paste nonsense here, the engine will reject it. The UI will not save you.</div>
       <textarea id="wizRaw" class="textarea" style="min-height:180px;" spellcheck="false">${escapeHtml(rawDefaultJson)}</textarea>
     </div>

     <div class="card" style="margin-top:12px;background:rgba(255,255,255,0.02);">
       <div style="font-weight:800;">3) Preview</div>
       <div id="wizPreview" class="muted" style="margin-top:10px;white-space:pre-wrap;font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;">(click Preview)</div>
       <div class="row" style="margin-top:12px;gap:10px;justify-content:flex-end;">
         <button id="wizDoPreview" class="btn">Preview</button>
         <button id="wizSubmit" class="btn ok">Submit</button>
       </div>
     </div>
     <div class="row" style="margin-top:12px;gap:10px;justify-content:flex-end;">
       <button id="wizClose" class="btn">Close</button>
     </div>`
  );

  // Wire type switching.
  function setType(t){
    const trait = q("#traitBlock");
    const cs = q("#csBlock");
    const raw = q("#rawBlock");
    if(trait) trait.classList.toggle("hidden", t !== "trait");
    if(cs) cs.classList.toggle("hidden", t !== "changesets");
    if(raw) raw.classList.toggle("hidden", t !== "raw");

    // apply_phase freeze enforcement in UI
    if(t === "trait" && frozen){
      const sel = q("#wizPhase");
      if(sel){
        qa("option", sel).forEach(opt=>{
          opt.disabled = (opt.value !== frozen);
        });
        sel.value = frozen;
      }
    }
  }
  qa("input[name=cmdType]").forEach(r=>{
    safeOn(r, "change", ()=>{ setType(r.value); });
  });
  setType("trait");


  // Trait picker (best-effort): uses metrics-derived gen_*_mean columns.
  let traitList = [];

  function updateTraitCurrent(fillNew){
    const sel = q("#wizTraitSelect");
    const cur = q("#wizTraitCurrent");
    const nv = q("#wizTraitNew");
    const tag = sel ? String(sel.value || "").trim() : "";
    const t = traitList.find(x => String(x?.locus_tag || "") === tag);
    const mean = (t && t.mean !== null && t.mean !== undefined) ? Number(t.mean) : null;

    if(cur){
      cur.textContent = (mean===null || !Number.isFinite(mean)) ? "-" : fmtNum(mean, 6);
    }
    if(nv && fillNew){
      nv.value = (mean===null || !Number.isFinite(mean)) ? "" : String(mean);
    }
  }

  async function loadTraits(){
    const status = q("#wizTraitStatus");
    const picker = q("#wizTraitPicker");
    if(status) status.textContent = "Loading traits from metrics…";
    try{
      const res = await apiRunTraits(runKey);
      traitList = Array.isArray(res?.traits) ? res.traits : [];
      const gen = res?.generation;
      const prov = !!res?.provisional;

      if(!traitList.length){
        if(status) status.textContent = "No gen_*_mean traits found (or metrics missing).";
        if(picker) picker.classList.add("hidden");
        return;
      }

      if(status){
        let t = `Loaded ${traitList.length} traits`;
        if(gen !== null && gen !== undefined) t += ` (from gen ${gen})`;
        if(prov) t += " · provisional";
        status.textContent = t;
      }
      if(picker) picker.classList.remove("hidden");

      const sel = q("#wizTraitSelect");
      if(sel){
        sel.innerHTML = traitList.map(t => `<option value="${escapeHtml(t?.locus_tag || "")}">${escapeHtml(t?.locus_tag || "")}</option>`).join("");
        if(traitList[0]?.locus_tag) sel.value = traitList[0].locus_tag;
      }
      updateTraitCurrent(true);
    }catch(_e){
      if(status) status.textContent = "Trait list unavailable (no metrics source).";
      if(picker) picker.classList.add("hidden");
    }
  }

  safeOn(q("#wizLoadTraits"), "click", async ()=>{ await loadTraits(); });
  safeOn(q("#wizTraitSelect"), "change", ()=>{ updateTraitCurrent(true); });
  safeOn(q("#wizTraitAdd"), "click", ()=>{
    const sel = q("#wizTraitSelect");
    const tag = sel ? String(sel.value || "").trim() : "";
    if(!tag){
      showToast("Trait picker", "No trait selected");
      return;
    }

    const nv = q("#wizTraitNew");
    const v = nv ? Number(nv.value) : NaN;
    if(!Number.isFinite(v)){
      showToast("Trait picker", "Enter a numeric new value");
      return;
    }

    // Update locus_tags.
    const lociEl = q("#wizLoci");
    const lociRaw = lociEl ? String(lociEl.value || "") : "";
    const loci = lociRaw.split(",").map(x=>x.trim()).filter(Boolean);
    if(!loci.includes(tag)) loci.push(tag);
    if(lociEl) lociEl.value = loci.join(", ");

    // Update allele_values JSON.
    const allelesEl = q("#wizAlleles");
    let obj = {};
    try{
      const raw = allelesEl ? String(allelesEl.value || "").trim() : "";
      obj = raw ? JSON.parse(raw) : {};
      if(!obj || typeof obj !== "object" || Array.isArray(obj)) obj = {};
    }catch(_err){
      showToast("Trait picker", "allele_values is not valid JSON");
      return;
    }
    obj[tag] = v;
    if(allelesEl) allelesEl.value = JSON.stringify(obj, null, 2);

    showToast("Trait added", `${tag} = ${v}`);
  });

  // Auto-load once to make the dropdown actually useful.
  loadTraits();

  safeOn(q("#wizClose"), "click", closeModal);

  function buildPayload(){
    const typeRadio = qa("input[name=cmdType]").find(x=>x.checked);
    const t = typeRadio ? typeRadio.value : "trait";

    if(t === "changesets"){
      const ids = qa(".wizCs").filter(x=>x.checked).map(x=>String(x.value||""));
      if(ids.length === 0) throw new Error("Select at least one ChangeSet");
      return {changesets: ids};
    }

    if(t === "raw"){
      const rawTxt = q("#wizRaw") ? String(q("#wizRaw").value||"") : "";
      const obj = JSON.parse(rawTxt);
      if(!obj || typeof obj !== "object") throw new Error("Raw JSON must be an object");
      return obj;
    }

    // Trait Injection
    const cu = q("#wizUuid") ? String(q("#wizUuid").value||"").trim() : "";
    const tg = q("#wizTargetGen") ? parseInt(String(q("#wizTargetGen").value||"0"),10) : 0;
    const dur = q("#wizDuration") ? parseInt(String(q("#wizDuration").value||"1"),10) : 1;
    const phase = q("#wizPhase") ? String(q("#wizPhase").value||"") : "";
    const loci = csvListToArray(q("#wizLoci") ? q("#wizLoci").value : "");
    const agentsRaw = csvListToArray(q("#wizAgents") ? q("#wizAgents").value : "");
    const agents = agentsRaw.map(x=>{
      const n = parseInt(x,10);
      return Number.isFinite(n) ? n : x;
    });
    const allelesTxt = q("#wizAlleles") ? String(q("#wizAlleles").value||"{}").trim() : "{}";
    const alleles = JSON.parse(allelesTxt || "{}");
    if(!alleles || typeof alleles !== "object" || Array.isArray(alleles)) throw new Error("allele_values must be a JSON object");
    if(!Number.isFinite(tg) || tg < 0) throw new Error("target_generation must be >= 0");
    if(!Number.isFinite(dur) || dur < 1) throw new Error("duration_generations must be >= 1");
    if(frozen && phase !== frozen) throw new Error(`apply_phase must match frozen value: ${frozen}`);

    return {
      command_type: "TraitInjectionCommand",
      schema_version: "cmd_v2",
      command_uuid: cu || undefined,
      target_generation: tg,
      trait_injection: {
        schema_version: "trait_injection_v2",
        duration_generations: dur,
        apply_phase: phase,
        locus_tags: loci,
        allele_values: alleles,
        target_agent_ids: agents,
      },
    };
  }

  safeOn(q("#wizDoPreview"), "click", ()=>{
    try{
      const payload = buildPayload();
      const preview = q("#wizPreview");
      if(preview) preview.textContent = JSON.stringify(payload, null, 2);
    }catch(e){
      const preview = q("#wizPreview");
      if(preview) preview.textContent = String(e?.message || e);
    }
  });

  safeOn(q("#wizSubmit"), "click", async ()=>{
    let payload;
    try{ payload = buildPayload(); }
    catch(e){ showToast("Invalid input", e?.message||String(e)); return; }

    if(det === "FAIL"){
      if(!confirm("This run is determinism FAIL. Submitting commands is allowed, but you probably know what you're doing. Continue?")) return;
    }
    if(!confirm("Submit command(s) to inbox?")) return;

    try{
      const res = await apiSubmitCommand(runKey, payload);
      closeModal();
      // res can be single submission, or changesets expanded submission.
      if(res && res.command_uuid){
        State.runDetail.pendingUuids.add(String(res.command_uuid));
        showToast("Submitted", `pending ACK: ${res.command_uuid}`);
      }else if(res && Array.isArray(res.submitted)){
        for(const s of res.submitted){
          if(s && s.command_uuid) State.runDetail.pendingUuids.add(String(s.command_uuid));
        }
        showToast("Submitted", `pending ACK: ${res.submitted.length} command(s)`);
      }else{
        showToast("Submitted", "Command submitted.");
      }

      // Refresh applied commands panel soon.
      await refreshRunDetail(false);
    }catch(e){
      showToast("Submit failed", e?.message||String(e));
    }
  });
}

function openPlanWizard(opts){
  const kind = opts?.kind === "continuation" ? "continuation" : "branch";
  const parentRunKey = String(opts?.parentRunKey || "");
  const presetCkId = opts?.checkpointId || null;
  const presetCkGen = (opts?.checkpointGeneration === undefined) ? null : opts.checkpointGeneration;

  const parentRun = findRun(parentRunKey);
  const det = String(parentRun?.determinism || "UNKNOWN").toUpperCase();
  const detFail = det === "FAIL";

  openModal(
    kind === "branch" ? "Create Branch Plan" : "Create Continuation Plan",
    `<div class="muted">Parent run: <b>${escapeHtml(parentRunKey)}</b></div>
     <div class="row" style="margin-top:10px;gap:10px;">
       ${det === "PASS" ? badge("Determinism PASS", "b-pass") : (det === "FAIL" ? badge("Determinism FAIL", "b-fail") : badge(`Determinism ${det}`, "b-unknown"))}
     </div>

     <div class="card" style="margin-top:12px;background:rgba(255,255,255,0.02);">
       <div style="font-weight:800;">1) Select checkpoint</div>
       <div id="planCkptLoading" class="muted" style="margin-top:8px;">Loading checkpoints…</div>
       <div id="planCkptBlock" class="hidden" style="margin-top:10px;">
         <select id="planCheckpoint" class="input" style="width:100%;"></select>
         <div id="planCkptWarn" class="muted" style="margin-top:8px;font-size:12px;"></div>
       </div>
     </div>

     <div class="card" style="margin-top:12px;background:rgba(255,255,255,0.02);">
       <div style="font-weight:800;">2) Label / tags</div>
       <div class="grid-2" style="margin-top:10px;">
         <div>
           <div class="muted" style="margin-bottom:6px;">label</div>
           <input id="planLabel" class="input" placeholder="optional" value=""/>
         </div>
         <div>
           <div class="muted" style="margin-bottom:6px;">tags (comma-separated)</div>
           <input id="planTags" class="input" placeholder="optional" value=""/>
         </div>
         <div>
           <div class="muted" style="margin-bottom:6px;">target_generation (optional)</div>
           <input id="planTarget" class="input" type="number" min="0" placeholder="default: inherit" value=""/>
         </div>
         <div>
           <label class="row" style="gap:8px;margin-top:24px;">
             <input id="planOverride" type="checkbox" ${detFail?"":"disabled"}/>
             <span class="muted">Override determinism FAIL block</span>
           </label>
         </div>
       </div>
     </div>

     <div class="card" style="margin-top:12px;background:rgba(255,255,255,0.02);">
       <div style="font-weight:800;">3) Select ChangeSets (optional)</div>
       <div class="muted" style="margin-top:8px;">Selected ChangeSets are recorded in the plan metadata. This action plans only and does not launch.</div>
       <div style="margin-top:10px;display:flex;flex-direction:column;gap:10px;max-height:240px;overflow:auto;">
         ${(State.changesets||[]).map(cs=>{
           const id = String(cs.id||"");
           const checked = State.planChangeSets.has(id);
           return `<label class="row" style="gap:8px;align-items:flex-start;">
             <input class="planCs" type="checkbox" value="${escapeHtml(id)}" ${checked?"checked":""}/>
             <div>
               <div style="font-weight:700;">${escapeHtml(id)} <span class="muted" style="font-weight:400;">${escapeHtml(shortId(cs.label||"", 40))}</span></div>
               <div class="muted" style="font-size:12px;margin-top:2px;">${escapeHtml(shortId(changesetCmdSummary(cs), 70))}</div>
             </div>
           </label>`;
         }).join("\n") || `<div class="muted">No ChangeSets available.</div>`}
       </div>
     </div>

     <div class="card" style="margin-top:12px;background:rgba(255,255,255,0.02);">
       <div style="font-weight:800;">4) Preview + create</div>
       <div id="planPreview" class="muted" style="margin-top:10px;white-space:pre-wrap;font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;">(click Preview)</div>
       <div class="row" style="margin-top:12px;gap:10px;justify-content:flex-end;">
         <button id="planDoPreview" class="btn">Preview</button>
         <button id="planCreate" class="btn ok">Create plan</button>
       </div>
     </div>

     <div class="row" style="margin-top:12px;gap:10px;justify-content:flex-end;">
       <button id="planClose" class="btn">Close</button>
     </div>`
  );

  safeOn(q("#planClose"), "click", closeModal);

  // Load checkpoints
  (async ()=>{
    try{
      const cp = await apiRunCheckpoints(parentRunKey);
      const cps = Array.isArray(cp?.checkpoints) ? cp.checkpoints : [];
      const sel = q("#planCheckpoint");
      const loading = q("#planCkptLoading");
      const block = q("#planCkptBlock");
      const warn = q("#planCkptWarn");
      if(loading) loading.classList.add("hidden");
      if(block) block.classList.remove("hidden");
      if(!sel) return;
      if(cps.length === 0){
        sel.innerHTML = `<option value="">(no checkpoints indexed)</option>`;
        if(warn) warn.textContent = "No checkpoints are available. Planning is blocked.";
        return;
      }
      sel.innerHTML = cps.map(c=>{
        const id = String(c?.checkpoint_id || "");
        const gen = Number(c?.generation);
        const exists = !!c?.exists;
        const label = `gen ${Number.isFinite(gen)?gen:"?"} :: ${id}${exists?"":" (MISSING)"}`;
        const val = JSON.stringify({checkpoint_id: id, checkpoint_generation: Number.isFinite(gen)?gen:null, exists: exists});
        return `<option value='${escapeHtml(val)}' ${(presetCkId && presetCkId===id) ? "selected" : ""}>${escapeHtml(label)}</option>`;
      }).join("\n");
      // If preset by generation and no preset id, try pick.
      if(!presetCkId && Number.isFinite(Number(presetCkGen))){
        const idx = cps.findIndex(c => Number(c?.generation) === Number(presetCkGen));
        if(idx >= 0) sel.selectedIndex = idx;
      }

      function updateWarn(){
        if(!warn) return;
        try{
          const v = JSON.parse(String(sel.value||"{}"));
          if(!v || !v.exists){
            warn.textContent = "Selected checkpoint is missing on disk. Planning is blocked.";
          }else{
            warn.textContent = "";
          }
        }catch(_e){ warn.textContent = ""; }
      }
      safeOn(sel, "change", updateWarn);
      updateWarn();
    }catch(e){
      const loading = q("#planCkptLoading");
      if(loading) loading.textContent = `Failed to load checkpoints: ${e?.message||String(e)}`;
    }
  })();

  function buildPlanPayload(){
    const sel = q("#planCheckpoint");
    if(!sel) throw new Error("checkpoint selector missing");
    const v = JSON.parse(String(sel.value||"{}"));
    if(!v || !v.checkpoint_id) throw new Error("No checkpoint selected");
    if(!v.exists) throw new Error("Selected checkpoint is MISSING on disk");

    const label = q("#planLabel") ? String(q("#planLabel").value||"").trim() : "";
    const tags = csvListToArray(q("#planTags") ? q("#planTags").value : "");
    const target = maybeInt(q("#planTarget") ? q("#planTarget").value : "");
    const override = q("#planOverride") ? !!q("#planOverride").checked : false;
    const changesets = qa(".planCs").filter(x=>x.checked).map(x=>String(x.value||""));
    const runKind = kind;

    if(detFail && !override) throw new Error("Parent determinism is FAIL. Check override to proceed.");

    return {
      parent_run_key: parentRunKey,
      checkpoint_id: String(v.checkpoint_id),
      checkpoint_generation: (v.checkpoint_generation === null || v.checkpoint_generation === undefined) ? null : Number(v.checkpoint_generation),
      run_kind: runKind,
      target_generation: target,
      label: label || null,
      tags: tags.length ? tags : null,
      changesets: changesets.length ? changesets : null,
      override_determinism_fail: !!override,
    };
  }

  safeOn(q("#planDoPreview"), "click", ()=>{
    const out = q("#planPreview");
    if(!out) return;
    try{
      out.textContent = JSON.stringify(buildPlanPayload(), null, 2);
    }catch(e){
      out.textContent = String(e?.message||e);
    }
  });

  safeOn(q("#planCreate"), "click", async ()=>{
    let payload;
    try{ payload = buildPlanPayload(); }
    catch(e){ showToast("Invalid plan", e?.message||String(e)); return; }

    const summary = `${kind} plan from ${parentRunKey} @ ${payload.checkpoint_id}`;
    if(!confirm(`Create new planned run folder?\n\n${summary}`)) return;
    try{
      const res = await apiPlanBranch(payload);
      closeModal();
      showToast("Plan created", res.new_run_key || "(unknown)");
      if(res.new_run_key){
        // Refresh runs index and navigate.
        await refreshRuns();
        location.hash = `#run/${encodeURIComponent(String(res.new_run_key))}`;
      }
    }catch(e){
      showToast("Plan failed", e?.message||String(e));
    }
  });
}

// -----------------------------
// View routing
// -----------------------------

const PROJECT_DEFAULT_VIEW = {causyl: "backlog"};
function inferProjectFromView(_view){ return "causyl"; }
function getSelectedProject(){ return "causyl"; }
function syncProjectShellUi(_project){
  const nav = q("#projectNavCausyl");
  if(nav) nav.classList.remove("hidden");
}
function setProjectShell(_project){
  State.projectShell = "causyl";
  syncProjectShellUi("causyl");
  return "causyl";
}

function setActiveTab(tabId){
  ["#tab-overview", "#tab-matrix", "#tab-builder", "#tab-pressure-search", "#tab-live-simulations", "#tab-backlog", "#tab-agents", "#tab-mobile", "#tab-schedules", "#tab-mobile-update"].forEach(id=>{
    const el = q(id);
    if(el) el.classList.toggle("active", id === tabId);
  });
}

function showView(which){
  const ids = ["overview","matrix","changesets","builder","pressure-search","live-simulations","backlog","agents","mobile","schedules","mobile-update","run"];
  for(const v of ids){
    const sec = q(`#view-${v}`);
    if(sec) sec.classList.toggle("hidden", v !== which);
  }
  if(which === "overview") setActiveTab("#tab-overview");
  if(which === "matrix") setActiveTab("#tab-matrix");
  if(which === "builder") setActiveTab("#tab-builder");
  if(which === "pressure-search") setActiveTab("#tab-pressure-search");
  if(which === "live-simulations") setActiveTab("#tab-live-simulations");
  if(which === "backlog") setActiveTab("#tab-backlog");
  if(which === "agents") setActiveTab("#tab-agents");
  if(which === "mobile") setActiveTab("#tab-mobile");
  if(which === "schedules") setActiveTab("#tab-schedules");
  if(which === "mobile-update") setActiveTab("#tab-mobile-update");
  if(which === "run") setActiveTab(null);
}

function loadEmbeddedFrame(id, fallbackSrc){
  const frame = q(id);
  if(!frame || frame.getAttribute("src")) return;
  const src = String(frame.getAttribute("data-src") || fallbackSrc || "").trim();
  if(src) frame.setAttribute("src", src);
}

function parseHash(){
  const h = (location.hash || "#backlog").replace(/^#/, "");
  const views = ["backlog", "agents", "mobile", "schedules", "mobile-update"];
  return {view: views.includes(h) ? h : "backlog"};
}

function onHashChange(){
  const x = parseHash();
  setProjectShell(inferProjectFromView(x.view));
  State.view = x.view;
  State.activeRunKey = x.runKey || null;
  showView(State.view);
  if(State.view === "matrix") renderMatrixView();
  if(State.view === "backlog") renderBacklogView();
  if(State.view === "agents") renderEmbeddedShellView("agents");
  if(State.view === "mobile") renderEmbeddedShellView("mobile");
  if(State.view === "schedules") renderEmbeddedShellView("schedules");
  if(State.view === "mobile-update") renderEmbeddedShellView("mobile-update");
  if(State.view === "run"){
    renderRunDetailSkeleton(State.activeRunKey);
    refreshRunDetail(true).catch(()=>{});
  }
}

function renderEmbeddedShellView(kind){
  const mode = String(kind || "").trim().toLowerCase();
  if(mode === "agents"){
    loadEmbeddedFrame("#agentsEmbedFrame", "/agents?embedded=1");
    return;
  }
  if(mode === "mobile"){
    loadEmbeddedFrame("#mobileEmbedFrame", "/mobile?embedded=1&pane=admin");
    return;
  }
  if(mode === "schedules"){
    loadEmbeddedFrame("#schedulesEmbedFrame", "/schedules?embedded=1");
    return;
  }
  if(mode === "mobile-update"){
    loadEmbeddedFrame("#mobileUpdateEmbedFrame", "/mobile/update?embedded=1&channel=debug");
  }
}

// -----------------------------
// Refresh loops
// -----------------------------

async function refreshRuns(){
  const runs = await apiRuns();
  State.runs = runs;
  State.lastRunsUtc = new Date().toISOString();
}

async function refreshChangeSets(){
  const cs = await apiChangeSets();
  State.changesets = cs;
  State.lastChangeSetsUtc = new Date().toISOString();
}

async function refreshSystemRuns(){
  const data = await apiSystemRuns();
  State.systemRuns = data;
  State.lastSystemRunsUtc = new Date().toISOString();
}

async function refreshRunDetail(force){
  const rk = State.activeRunKey;
  if(!rk) return;

  const limit = State.runDetail.range || 1000;
  const runChanged = (State.runDetail.lastLoadedRunKey !== rk);
  const rangeChanged = (State.runDetail.lastRange !== limit);

  // Reset incremental tail state when switching runs or changing range.
  if(force || runChanged || rangeChanged){
    State.runDetail.lastLoadedRunKey = rk;
    State.runDetail.lastRange = limit;

    State.runDetail.epiav = null;
    State.runDetail.epiavLastGen = -1;
    State.runDetail.epiavSource = null;

    State.runDetail.parentEpiav = null;
    State.runDetail.parentEpiavLastGen = -1;
    State.runDetail.parentEpiavSource = null;
    State.runDetail.lastParentRunKey = null;
    // Optional metrics/knobs panels: reset only the bits that depend on run/range.
    if(runChanged){
      State.runDetail.metricsSchema = null;
      State.runDetail.metricName = "alive";
      State.runDetail.lastMetricName = null;
      State.runDetail.knobs = null;
      State.runDetail.knobsSource = null;
      State.runDetail.inboxQueue = null;
      State.runDetail.timelineSummary = null;
      State.runDetail.timelineLastSig = null;
      State.runDetail.timelineSelection = null;
      State.runDetail.appliedAfterGen = -1;
      State.runDetail.appliedBeforeGen = null;
      State.runDetail.appliedWindow = null;
      State.runDetail.appliedSearch = "";
      State.runDetail.appliedStatus = "ALL";
      State.runDetail.autopilotAfterGen = -1;
      State.runDetail.autopilotBeforeGen = null;
      State.runDetail.autopilotWindow = null;
      State.runDetail.autopilotSearch = "";
      State.runDetail.pressureAutopilotProfile = "balanced";
      State.runDetail.pressureAutopilotDeltaUp = 0.05;
      State.runDetail.pressureAutopilotDeltaDown = 0.10;
      State.runDetail.pressureAutopilotTargetSurvival = 0.55;
      State.runDetail.pressureAutopilotSurvivalCeiling = 0.80;
      State.runDetail.overlayStatus = null;
    }
    // Range impacts series windows.
    State.runDetail.metricSeries = null;
    State.runDetail.metricLastGen = -1;
    State.runDetail.metricSource = null;
  }

  // Prevent stale updates.
  const token = ++State.refreshToken;

  // Always load meta/checkpoints/commands/notes (fast, cached server-side where needed)
  try{
    const results = await Promise.allSettled([
      apiRunMeta(rk),
      apiRunCheckpoints(rk),
      apiRunAppliedCommands(rk, {limit: (State.runDetail.appliedLimit || 200), afterGen: State.runDetail.appliedAfterGen, beforeGen: State.runDetail.appliedBeforeGen}),
      apiRunAutopilotActions(rk, {limit: (State.runDetail.autopilotLimit || 200), afterGen: State.runDetail.autopilotAfterGen, beforeGen: State.runDetail.autopilotBeforeGen}),
      apiRunInboxQueue(rk, 200),
      apiRunNotes(rk),
      apiRunTimelineSummary(rk, State.runDetail.timelineBins || 200),
    ]);

    if(State.refreshToken !== token) return;

    // Best-effort assignment: one failing endpoint must not blank the whole page.
    const names = ["meta","checkpoints","appliedCommands","autopilotActions","inboxQueue","notes","timelineSummary"];
    const firstErr = results.find(r => r && r.status === "rejected");

    // Reset lastError unless something failed this cycle.
    State.runDetail.lastError = firstErr ? (firstErr.reason?.message || String(firstErr.reason)) : null;

    for(let i=0;i<results.length && i<names.length;i++){
      const r = results[i];
      const k = names[i];
      if(!r) continue;
      if(r.status === "fulfilled"){
        State.runDetail[k] = r.value;
      }
    }

    // Derived convenience windows (only if payloads exist)
    const ac = State.runDetail.appliedCommands;
    const ap = State.runDetail.autopilotActions;
    State.runDetail.autopilotWindow = ap?.window || null;
    State.runDetail.appliedWindow = ac?.window || null;
  }catch(e){
    // Keep whatever we already had, but show error.
    State.runDetail.lastError = e?.message || String(e);
  }

  // EPI/AV series (heavier). Use incremental tail (after_gen) so we don't
  // re-transfer the whole series every refresh.
  try{
    const afterGen = (force || runChanged || rangeChanged) ? -1 : (Number.isFinite(Number(State.runDetail.epiavLastGen)) ? Number(State.runDetail.epiavLastGen) : -1);
    const epiavNew = await apiRunEpiAv(rk, afterGen, limit);
    if(State.refreshToken !== token) return;

    // Merge/replace depending on source continuity.
    if(afterGen < 0 || !State.runDetail.epiav || (State.runDetail.epiavSource && epiavNew.source && State.runDetail.epiavSource !== epiavNew.source)){
      State.runDetail.epiav = epiavNew;
    }else{
      const prevPts = Array.isArray(State.runDetail.epiav.points) ? State.runDetail.epiav.points : [];
      const newPts = Array.isArray(epiavNew.points) ? epiavNew.points : [];
      if(newPts.length){
        let merged = prevPts.concat(newPts);
        if(limit && merged.length > limit) merged = merged.slice(merged.length - limit);
        State.runDetail.epiav.points = merged;
      }
      // Refresh metadata even if no new points.
      State.runDetail.epiav.schema_version = epiavNew.schema_version || State.runDetail.epiav.schema_version;
      State.runDetail.epiav.source = epiavNew.source || State.runDetail.epiav.source;
      State.runDetail.epiav.provisional = epiavNew.provisional;
      State.runDetail.epiav.parse_errors = epiavNew.parse_errors;
      State.runDetail.epiav.source_kind = (epiavNew.source_kind !== undefined) ? epiavNew.source_kind : State.runDetail.epiav.source_kind;
      State.runDetail.epiav.header = (epiavNew.header !== undefined) ? epiavNew.header : State.runDetail.epiav.header;
      if(epiavNew.has_epi !== undefined) State.runDetail.epiav.has_epi = epiavNew.has_epi;
      if(epiavNew.has_av !== undefined) State.runDetail.epiav.has_av = epiavNew.has_av;
    }

    const ptsFinal = Array.isArray(State.runDetail.epiav?.points) ? State.runDetail.epiav.points : [];
    State.runDetail.epiavSource = State.runDetail.epiav?.source || epiavNew.source || null;
    if(ptsFinal.length){
      const g = Number(ptsFinal[ptsFinal.length - 1].generation);
      if(Number.isFinite(g)) State.runDetail.epiavLastGen = g;
    }

    // Parent overlay best-effort (incremental), but never silent.
    if(State.runDetail.overlayParent){
      const runObj = (State.runDetail.meta && State.runDetail.meta.run) || findRun(rk);
      const pk = findParentRunKey(runObj);

      if(!pk){
        State.runDetail.overlayStatus = {state: "NO_PARENT", message: "no parent run"};
        State.runDetail.parentEpiav = null;
        State.runDetail.parentEpiavLastGen = -1;
        State.runDetail.parentEpiavSource = null;
        State.runDetail.lastParentRunKey = null;
      }else{
        const parentChanged = (State.runDetail.lastParentRunKey !== pk);
        const pAfter = parentChanged ? -1 : State.runDetail.parentEpiavLastGen;
        State.runDetail.lastParentRunKey = pk;

        try{
          const pNew = await apiRunEpiAv(pk, pAfter, 1000);

          if(parentChanged || pAfter < 0 || !State.runDetail.parentEpiavSource || State.runDetail.parentEpiavSource !== pNew.source){
            State.runDetail.parentEpiav = pNew;
            State.runDetail.parentEpiavLastGen = maxGenFromPoints(pNew?.points);
            State.runDetail.parentEpiavSource = pNew.source;
          }else{
            const pPts = Array.isArray(pNew?.points) ? pNew.points : [];
            if(pPts.length){
              State.runDetail.parentEpiav.points = (State.runDetail.parentEpiav.points || []).concat(pPts);
              State.runDetail.parentEpiavLastGen = Math.max(State.runDetail.parentEpiavLastGen, maxGenFromPoints(pPts));
            }
            State.runDetail.parentEpiav.schema_version = pNew.schema_version;
            State.runDetail.parentEpiav.source = pNew.source;
            State.runDetail.parentEpiav.provisional = pNew.provisional;
            State.runDetail.parentEpiav.parse_errors = pNew.parse_errors;
            State.runDetail.parentEpiav.source_kind = (pNew.source_kind !== undefined) ? pNew.source_kind : State.runDetail.parentEpiav.source_kind;
            State.runDetail.parentEpiav.header = (pNew.header !== undefined) ? pNew.header : State.runDetail.parentEpiav.header;
            if(pNew.has_epi !== undefined) State.runDetail.parentEpiav.has_epi = pNew.has_epi;
            if(pNew.has_av !== undefined) State.runDetail.parentEpiav.has_av = pNew.has_av;
          }

          // Overlay compatibility check (schema mismatch should not fail silently).
          const childHasEpi = !!(State.runDetail.epiav && State.runDetail.epiav.has_epi);
          const childHasAv = !!(State.runDetail.epiav && State.runDetail.epiav.has_av);
          const parentHasEpi = !!(State.runDetail.parentEpiav && State.runDetail.parentEpiav.has_epi);
          const parentHasAv = !!(State.runDetail.parentEpiav && State.runDetail.parentEpiav.has_av);
          const parentPts = Array.isArray(State.runDetail.parentEpiav?.points) ? State.runDetail.parentEpiav.points : [];

          if((childHasEpi && !parentHasEpi) || (childHasAv && !parentHasAv)){
            const msg = `schema mismatch: child has epi/av=${childHasEpi}/${childHasAv}, parent has epi/av=${parentHasEpi}/${parentHasAv}`;
            State.runDetail.overlayStatus = {state: "SCHEMA_MISMATCH", message: msg, parent_run_key: pk};
            // Disable overlay, since it is guaranteed to be misleading.
            State.runDetail.overlayParent = false;
            State.runDetail.parentEpiav = null;
            State.runDetail.parentEpiavLastGen = -1;
            State.runDetail.parentEpiavSource = null;
          }else if(!parentPts.length){
            State.runDetail.overlayStatus = {state: "UNAVAILABLE", message: "parent epi/av series is empty", parent_run_key: pk};
            // Keep overlay checkbox as-is, but no data to draw.
            State.runDetail.parentEpiav = null;
            State.runDetail.parentEpiavLastGen = -1;
            State.runDetail.parentEpiavSource = null;
          }else{
            State.runDetail.overlayStatus = {state: "OK", message: "", parent_run_key: pk};
          }
        }catch(pe){
          State.runDetail.overlayStatus = {state: "UNAVAILABLE", message: String(pe), parent_run_key: pk};
          State.runDetail.parentEpiav = null;
          State.runDetail.parentEpiavLastGen = -1;
          State.runDetail.parentEpiavSource = null;
        }
      }
    }else{
      State.runDetail.parentEpiav = null;
      State.runDetail.parentEpiavLastGen = -1;
      State.runDetail.parentEpiavSource = null;
      State.runDetail.lastParentRunKey = null;
      // Keep schema-mismatch status visible (overlay got auto-disabled).
      if(!(State.runDetail.overlayStatus && State.runDetail.overlayStatus.state === "SCHEMA_MISMATCH")){
        State.runDetail.overlayStatus = null;
      }
    }

  }catch(_e){
    // ignore; charts will show "No data"
  }

  // Optional metrics series (population/fitness/etc). Best-effort.
  try{
    if(!State.runDetail.metricsSchema){
      const schema = await apiRunMetricsSchema(rk);
      if(State.refreshToken !== token) return;
      State.runDetail.metricsSchema = schema;

      const cols = Array.isArray(schema?.columns) ? schema.columns : [];
      if(cols.length){
        let m = State.runDetail.metricName;
        if(!cols.includes(m)){
          if(cols.includes("alive")) m = "alive";
          else if(cols.includes("mean_fitness")) m = "mean_fitness";
          else m = cols.find(c => c && c !== "generation") || cols[0];
          State.runDetail.metricName = m;
        }
      }
    }

    const metricChanged = (State.runDetail.lastMetricName !== State.runDetail.metricName);
    if(metricChanged){
      State.runDetail.metricSeries = null;
      State.runDetail.metricLastGen = -1;
      State.runDetail.metricSource = null;
      State.runDetail.lastMetricName = State.runDetail.metricName;
    }

    const mAfter = (force || runChanged || rangeChanged || metricChanged)
      ? -1
      : (Number.isFinite(Number(State.runDetail.metricLastGen)) ? Number(State.runDetail.metricLastGen) : -1);

    const mNew = await apiRunMetricSeries(rk, State.runDetail.metricName, mAfter, limit);
    if(State.refreshToken !== token) return;

    if(mAfter < 0 || !State.runDetail.metricSeries){
      State.runDetail.metricSeries = mNew;
    }else{
      const prevPts = Array.isArray(State.runDetail.metricSeries.points) ? State.runDetail.metricSeries.points : [];
      const newPts = Array.isArray(mNew.points) ? mNew.points : [];
      if(newPts.length){
        let merged = prevPts.concat(newPts);
        if(limit && merged.length > limit) merged = merged.slice(merged.length - limit);
        State.runDetail.metricSeries.points = merged;
      }
      State.runDetail.metricSeries.schema_version = mNew.schema_version || State.runDetail.metricSeries.schema_version;
      State.runDetail.metricSeries.source = mNew.source || State.runDetail.metricSeries.source;
      State.runDetail.metricSeries.provisional = mNew.provisional;
      State.runDetail.metricSeries.parse_errors = mNew.parse_errors;
      State.runDetail.metricSeries.metric = mNew.metric || State.runDetail.metricSeries.metric;
      State.runDetail.metricSeries.latest = mNew.latest || State.runDetail.metricSeries.latest;
    }

    const mPtsFinal = Array.isArray(State.runDetail.metricSeries?.points) ? State.runDetail.metricSeries.points : [];
    State.runDetail.metricSource = State.runDetail.metricSeries?.source || mNew.source || null;
    if(mPtsFinal.length){
      const g = Number(mPtsFinal[mPtsFinal.length - 1].generation);
      if(Number.isFinite(g)) State.runDetail.metricLastGen = g;
    }
  }catch(_me){
    // ignore
  }

  renderRunDetailView();
}

async function refreshActive(userInitiated){
  if(State.inFlight) return;
  State.inFlight = true;
  clearGlobalError();

  // Mobile reality: auto-refresh is great until it bulldozes whatever you're typing.
  // When the user is interacting with a form/select/modal, avoid re-rendering the
  // current view on background ticks.
  const deferRender = (!userInitiated) && (_isUserEditing() || _isUserInteracting());

  try{
    await apiHealth();
    setHealth(true);
  }catch(_e){
    setHealth(false);
  }

  try{
    if(!deferRender){
      if(State.view === "backlog"){
        if(userInitiated || (State.tick % 5 === 0) || !State.ledger.items){
          try{ await refreshLedgerItems(); }catch(_e){}
        }
        const needs = userInitiated || !State.ledger.itemsRenderedSig || (String(State.ledger.itemsRenderedSig) !== String(State.ledger.itemsSig||""));
        if(needs) renderBacklogView();
      }
    }
  }catch(e){
    showGlobalError("UI refresh error", e?.message || String(e));
  }finally{
    State.inFlight = false;
  }
}

function getPollMs(){
  const poll = q("#pollMs");
  const ms = poll ? parseInt(String(poll.value||"1500"),10) : 1500;
  return Math.max(250, Number.isFinite(ms) ? ms : 1500);
}

function _hasSelectionRefreshLock(){
  try{
    const sel = window.getSelection ? window.getSelection() : null;
    if(sel && !sel.isCollapsed && String(sel.toString() || "").trim()) return true;
  }catch(_e){}
  try{
    const ae = document.activeElement;
    if(ae && ae.tagName){
      const t = String(ae.tagName || "").toUpperCase();
      if(t === "INPUT" || t === "TEXTAREA" || t === "SELECT") return true;
      if(ae.isContentEditable) return true;
    }
  }catch(_e){}
  return false;
}

function initPolling(){
  const poll = q("#pollMs");
  let timer = null;

  function start(){
    const ms = getPollMs();
    if(timer) clearInterval(timer);
    timer = setInterval(()=>{
      if(_hasSelectionRefreshLock()) return;
      State.tick += 1;
      refreshActive().catch(()=>{});
    }, ms);
  }

  safeOn(poll, "change", start);
  start();
}

// -----------------------------
// Startup
// -----------------------------

function initTabs(){
  safeOn(q("#tab-overview"), "click", (e)=>{ e.preventDefault(); location.hash = "#overview"; });
  safeOn(q("#tab-matrix"), "click", (e)=>{ e.preventDefault(); location.hash = "#matrix"; });
  safeOn(q("#tab-builder"), "click", (e)=>{ e.preventDefault(); location.hash = "#builder"; });
  safeOn(q("#tab-pressure-search"), "click", (e)=>{ e.preventDefault(); location.hash = "#pressure-search"; });
  safeOn(q("#tab-live-simulations"), "click", (e)=>{ e.preventDefault(); location.hash = "#live-simulations"; });
  safeOn(q("#tab-backlog"), "click", (e)=>{ e.preventDefault(); location.hash = "#backlog"; });
  safeOn(q("#tab-agents"), "click", (e)=>{ e.preventDefault(); location.hash = "#agents"; });
  safeOn(q("#tab-mobile"), "click", (e)=>{ e.preventDefault(); location.hash = "#mobile"; });
  safeOn(q("#tab-schedules"), "click", (e)=>{ e.preventDefault(); location.hash = "#schedules"; });
  safeOn(q("#tab-mobile-update"), "click", (e)=>{ e.preventDefault(); location.hash = "#mobile-update"; });
  safeOn(q("#projectSelect"), "change", (e)=>{
    const nextProject = "causyl";
    setProjectShell(nextProject);
    const nextView = PROJECT_DEFAULT_VIEW[nextProject] || "backlog";
    if(State.view !== nextView || !location.hash){
      location.hash = `#${nextView}`;
      return;
    }
    onHashChange();
  });
}

function initModal(){
  safeOn(q("#modal-close"), "click", closeModal);
  safeOn(q("#modalOverlay"), "click", (e)=>{
    if(e.target && e.target.id === "modalOverlay") closeModal();
  });

  // ESC closes the modal (if open)
  window.addEventListener("keydown", (e)=>{
    if(!e || e.key !== "Escape") return;
    const overlay = q("#modalOverlay");
    if(overlay && !overlay.classList.contains("hidden")) closeModal();
  });
}

// -----------------------------
// Mobile menu (responsive topbar)
// -----------------------------

function _setMenuOpen(open){
  const body = document.body;
  const btn = q("#btnMenu");
  const overlay = q("#menuOverlay");
  if(!body) return;
  body.classList.toggle("menu-open", !!open);
  if(btn){
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    btn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  }
  if(overlay){
    overlay.classList.toggle("hidden", !open);
    overlay.setAttribute("aria-hidden", open ? "false" : "true");
  }
}

function closeMenu(){ _setMenuOpen(false); }
function toggleMenu(){
  const open = document.body && document.body.classList.contains("menu-open");
  _setMenuOpen(!open);
}

function initMobileMenu(){
  const btn = q("#btnMenu");
  const btnClose = q("#btnMenuClose");
  const overlay = q("#menuOverlay");

  safeOn(btn, "click", (e)=>{ e.preventDefault(); toggleMenu(); });
  safeOn(btnClose, "click", (e)=>{ e.preventDefault(); closeMenu(); });
  safeOn(overlay, "click", (e)=>{ e.preventDefault(); closeMenu(); });

  // ESC closes the menu
  window.addEventListener("keydown", (e)=>{
    if(e && e.key === "Escape") closeMenu();
  });
}

function init(){
  try{
    try{ window.__EIEL_APP_JS_INIT_STARTED__ = true; }catch(_e){}
    try{
      const savedProject = String(localStorage.getItem("causyl.webui.project_shell") || "").trim().toLowerCase();
      if(savedProject === "causyl"){
        State.projectShell = savedProject;
      }
    }catch(_e){}
    syncProjectShellUi(getSelectedProject());
    initTabs();
    initModal();
    initMobileMenu();
    _loadBacklogDraft();

    safeOn(q("#btnRefresh"), "click", ()=>{ refreshActive(true).catch(()=>{}); });
    window.addEventListener("hashchange", ()=>{ closeMenu(); onHashChange(); });

    // First render
    onHashChange();
    refreshActive().catch(()=>{});
    initPolling();

    try{ window.__EIEL_APP_JS_BOOTED__ = true; }catch(_e){}
  }catch(e){
    try{ window.__EIEL_APP_JS_BOOT_ERROR__ = String(e && (e.stack || e.message) || e); }catch(_e){}
    throw e;
  }
}

document.addEventListener("DOMContentLoaded", init);
