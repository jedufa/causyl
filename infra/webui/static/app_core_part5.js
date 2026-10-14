window.__EIEL_APP_CORE_PART5_LOADED__ = true;
function _windowByGen(points, rangeGens){
  const pts = Array.isArray(points) ? points : [];
  if(!pts.length) return pts;
  const lastGen = Number(pts[pts.length-1]?.generation);
  const rng = Number(rangeGens);
  if(!Number.isFinite(lastGen) || !Number.isFinite(rng) || rng <= 0) return pts;
  const start = lastGen - rng + 1;
  if(!Number.isFinite(start)) return pts;

  // Binary search because generations are monotonic.
  let lo = 0, hi = pts.length;
  while(lo < hi){
    const mid = (lo + hi) >> 1;
    const g = Number(pts[mid]?.generation);
    if(Number.isFinite(g) && g < start) lo = mid + 1;
    else hi = mid;
  }
  return pts.slice(lo);
}

function _tailStats(values, windowN){
  const xs = (Array.isArray(values) ? values : []).filter(v=>Number.isFinite(v));
  if(!xs.length) return null;
  const n = Math.max(1, Math.min(windowN||20, xs.length));
  const tail = xs.slice(-n);
  const mean = tail.reduce((a,b)=>a+b, 0) / tail.length;
  const var0 = tail.reduce((a,b)=>a + (b-mean)*(b-mean), 0) / tail.length;
  const std = Math.sqrt(var0);
  return { n: tail.length, mean, std };
}

function _movingAverage(series, windowN){
  const pts = Array.isArray(series) ? series : [];
  const w = Math.max(1, (windowN|0) || 1);
  const out = [];
  const buf = [];
  let sum = 0;
  let count = 0;

  for(const p of pts){
    const y = Number(p?.y);
    buf.push(y);
    if(Number.isFinite(y)){ sum += y; count += 1; }
    if(buf.length > w){
      const old = buf.shift();
      if(Number.isFinite(old)){ sum -= old; count -= 1; }
    }
    out.push({ x: p.x, y: count ? (sum / count) : null });
  }
  return out;
}

function renderRunDetailView(){
  const rk = State.activeRunKey;
  const root = q("#view-run");
  if(!root) return;
  if(!rk){
    root.innerHTML = `<div class="muted">No run selected.</div>`;
    return;
  }

  const el = ensureRunDetailLayout(rk);
  if(!el) return;

  const run = findRun(rk) || State.runDetail.meta?.run || { run_key: rk };
  const meta = State.runDetail.meta || null;

  // Sync inputs (avoid fighting the user while a dropdown is open)
  const rangeSel = q("#rdRange", el);
  if(rangeSel && document.activeElement !== rangeSel){
    const want = String(State.runDetail.range || 1000);
    if(rangeSel.value !== want) rangeSel.value = want;
  }
  const overlay = q("#rdOverlay", el);
  if(overlay && document.activeElement !== overlay){
    overlay.checked = !!State.runDetail.overlayParent;
    overlay.disabled = !!(State.runDetail.overlayStatus && State.runDetail.overlayStatus.state === "SCHEMA_MISMATCH");
  }
  const smooth = q("#rdSmooth", el);
  if(smooth && document.activeElement !== smooth){
    smooth.checked = !!State.runDetail.smoothEpi;
  }
  const cmdLimit = q("#rdCmdLimit", el);
  if(cmdLimit && document.activeElement !== cmdLimit){
    const want = String(State.runDetail.appliedLimit || 200);
    if(cmdLimit.value !== want) cmdLimit.value = want;
  }
  const apLimit = q("#rdApLimit", el);
  if(apLimit && document.activeElement !== apLimit){
    const want = String(State.runDetail.autopilotLimit || 200);
    if(apLimit.value !== want) apLimit.value = want;
  }
  const acSearch = q("#acSearch", el);
  if(acSearch && document.activeElement !== acSearch){
    const want = String(State.runDetail.appliedSearch || "");
    if(acSearch.value !== want) acSearch.value = want;
  }
  const apSearch = q("#apSearch", el);
  if(apSearch && document.activeElement !== apSearch){
    const want = String(State.runDetail.autopilotSearch || "");
    if(apSearch.value !== want) apSearch.value = want;
  }
  const acStatus = q("#acStatus", el);
  if(acStatus && document.activeElement !== acStatus){
    const want = String(State.runDetail.appliedStatus || "ALL");
    if(acStatus.value !== want) acStatus.value = want;
  }

  const tlBins = q("#rdTlBins", el);
  if(tlBins && document.activeElement !== tlBins){
    const want = String(State.runDetail.timelineBins || 200);
    if(tlBins.value !== want) tlBins.value = want;
  }



  // Pressure autopilot quick submit inputs
  const paEnable = q("#paEnable", el);
  if(paEnable && document.activeElement !== paEnable){
    paEnable.checked = !!State.runDetail.pressureAutopilotEnable;
  }
  const paGen = q("#paGen", el);
  if(paGen && document.activeElement !== paGen){
    const cg = Number(State.runDetail?.meta?.run?.current_generation);
    const fallbackGen = (Number.isFinite(cg) && cg >= 0) ? (cg + 10) : 10;
    const want = String(State.runDetail.pressureAutopilotTargetGen ?? fallbackGen);
    if(paGen.value !== want) paGen.value = want;
  }
  const paInterval = q("#paInterval", el);
  if(paInterval && document.activeElement !== paInterval){
    const want = String(State.runDetail.pressureAutopilotTickInterval ?? 200);
    if(paInterval.value !== want) paInterval.value = want;
  }
  const paProfile = q("#paProfile", el);
  if(paProfile && document.activeElement !== paProfile){
    const want = String(State.runDetail.pressureAutopilotProfile || "balanced");
    if(paProfile.value !== want) paProfile.value = want;
  }
  const paDeltaUp = q("#paDeltaUp", el);
  if(paDeltaUp && document.activeElement !== paDeltaUp){
    const want = String(State.runDetail.pressureAutopilotDeltaUp ?? 0.05);
    if(paDeltaUp.value !== want) paDeltaUp.value = want;
  }
  const paDeltaDown = q("#paDeltaDown", el);
  if(paDeltaDown && document.activeElement !== paDeltaDown){
    const want = String(State.runDetail.pressureAutopilotDeltaDown ?? 0.10);
    if(paDeltaDown.value !== want) paDeltaDown.value = want;
  }
  const paTargetSurvival = q("#paTargetSurvival", el);
  if(paTargetSurvival && document.activeElement !== paTargetSurvival){
    const want = String(State.runDetail.pressureAutopilotTargetSurvival ?? 0.55);
    if(paTargetSurvival.value !== want) paTargetSurvival.value = want;
  }
  const paSurvivalCeiling = q("#paSurvivalCeiling", el);
  if(paSurvivalCeiling && document.activeElement !== paSurvivalCeiling){
    const want = String(State.runDetail.pressureAutopilotSurvivalCeiling ?? 0.80);
    if(paSurvivalCeiling.value !== want) paSurvivalCeiling.value = want;
  }
  const paDeltaUpVal = q("#paDeltaUpVal", el);
  if(paDeltaUpVal) paDeltaUpVal.textContent = String(State.runDetail.pressureAutopilotDeltaUp ?? 0.05);
  const paDeltaDownVal = q("#paDeltaDownVal", el);
  if(paDeltaDownVal) paDeltaDownVal.textContent = String(State.runDetail.pressureAutopilotDeltaDown ?? 0.10);
  const paTargetSurvivalVal = q("#paTargetSurvivalVal", el);
  if(paTargetSurvivalVal) paTargetSurvivalVal.textContent = String(State.runDetail.pressureAutopilotTargetSurvival ?? 0.55);
  const paSurvivalCeilingVal = q("#paSurvivalCeilingVal", el);
  if(paSurvivalCeilingVal) paSurvivalCeilingVal.textContent = String(State.runDetail.pressureAutopilotSurvivalCeiling ?? 0.80);
  if(paInterval){
    paInterval.disabled = !State.runDetail.pressureAutopilotEnable;
  }
  if(paProfile) paProfile.disabled = !State.runDetail.pressureAutopilotEnable;
  if(paDeltaUp) paDeltaUp.disabled = !State.runDetail.pressureAutopilotEnable;
  if(paDeltaDown) paDeltaDown.disabled = !State.runDetail.pressureAutopilotEnable;
  if(paTargetSurvival) paTargetSurvival.disabled = !State.runDetail.pressureAutopilotEnable;
  if(paSurvivalCeiling) paSurvivalCeiling.disabled = !State.runDetail.pressureAutopilotEnable;
  const paPreview = q("#paPreview", el);
  if(paPreview){
    try{
      const cg = Number(State.runDetail?.meta?.run?.current_generation);
      const fallbackGen = (Number.isFinite(cg) && cg >= 0) ? (cg + 10) : 10;
      const tg = _parseIntStrict(State.runDetail.pressureAutopilotTargetGen, fallbackGen);
      const iv = _parseIntStrict(State.runDetail.pressureAutopilotTickInterval, 200);
      const pr = String(State.runDetail.pressureAutopilotProfile || "balanced");
      const du = Number(State.runDetail.pressureAutopilotDeltaUp ?? 0.05);
      const dd = Number(State.runDetail.pressureAutopilotDeltaDown ?? 0.10);
      paPreview.textContent = State.runDetail.pressureAutopilotEnable
        ? `Will apply at gen=${tg}: enable=true, profile=${pr}, tick_interval=${iv}, delta_up=${fmtNum(du,2)}, delta_down=${fmtNum(dd,2)}`
        : `Will apply at gen=${tg}: enable=false`;
    }catch(_e){
      // Preview is best-effort.
    }
  }

  const paActiveInfo = q("#paActiveInfo", el);
  const paActiveGrid = q("#paActiveGrid", el);
  if(paActiveInfo || paActiveGrid){
    try{
      const active = derivePressureAutopilotActiveState();
      const v = active?.values || {};
      const src = String(active?.source || "unknown");
      if(paActiveInfo){
        paActiveInfo.textContent = `Source: ${src}. Load Knobs for a stronger baseline if needed.`;
      }
      if(paActiveGrid){
        const fmt = (x)=>{
          if(typeof x === "boolean") return x ? "true" : "false";
          if(typeof x === "number") return Number.isFinite(x) ? String(x) : "n/a";
          if(x === null || x === undefined || x === "") return "n/a";
          return String(x);
        };
        paActiveGrid.innerHTML = [
          `enable=${fmt(v.pressure_autopilot_enable)} | tick_interval=${fmt(v.pressure_autopilot_tick_interval_gen)} | profile=${fmt(v.pressure_autopilot_profile)}`,
          `delta_up=${fmt(v.pressure_autopilot_delta_up)} | delta_down=${fmt(v.pressure_autopilot_delta_down)}`,
          `target_survival=${fmt(v.pressure_autopilot_target_survival)} | survival_ceiling=${fmt(v.pressure_autopilot_survival_ceiling)}`,
        ].map(line => `<div>${escapeHtml(line)}</div>`).join("");
      }
    }catch(_e){
      if(paActiveInfo) paActiveInfo.textContent = "Unable to derive active values.";
      if(paActiveGrid) paActiveGrid.textContent = "";
    }
  }


  // Header
  const label = meta?.label || run?.label || "";
  const title = q("#rdTitle", el);
  if(title){
    title.innerHTML = `Run: <span class="mono">${escapeHtml(rk)}</span>${label ? ` <span class="muted">(${escapeHtml(label)})</span>` : ""}`;
  }

  const status = String(run?.status || "UNKNOWN").toUpperCase();
  const det = String(meta?.run?.determinism || "UNKNOWN").toUpperCase();
  const strict = !!(run?.strict_mode);
  const ver = run?.last_verified_generation;

  // Latest values from series if present, else fallback to run summary
  const epiPts = State.runDetail.epiav?.points || [];
  const avPts = State.runDetail.epiav?.points || [];
  const latestEpi = Number.isFinite(run?.epi) ? run.epi : Number(epiPts[epiPts.length-1]?.epi);
  const latestAv = Number.isFinite(run?.av) ? run.av : Number(avPts[avPts.length-1]?.av);

  const epiWin = _windowByGen(epiPts, State.runDetail.range);
  const epiStats = _tailStats(epiWin.map(p=>Number(p.epi)), 20);

  const badges = q("#rdBadges", el);
  if(badges){
    badges.innerHTML = [
      badgeForStatus(status),
      badgeForDeterminism(det),
      badgeForStrict(strict),
      chip(`Verified: ${Number.isFinite(ver) && ver >= 0 ? ver : "?"}`, "verified_gen"),
      chip(`EPI: ${Number.isFinite(latestEpi) ? fmtNum(latestEpi) : "N/A"}`, "epi"),
      chip(`AV: ${Number.isFinite(latestAv) ? fmtNum(latestAv) : "N/A"}`, "av"),
      (epiStats ? chip(`EPI μ20: ${fmtNum(epiStats.mean)} (σ ${fmtNum(epiStats.std)})`, "epi") : ""),
    ].filter(Boolean).join("\n");
  }

  const tagsEl = q("#rdTags", el);
  if(tagsEl){
    const tags = (run?.tags || []);
    const tagHtml = (tags.length ? tags.map(t=>`<span class=\"chip\">${escapeHtml(String(t))}</span>`).join(" ") : `<span class=\"muted\">(none)</span>`);
    tagsEl.innerHTML = `<span class=\"muted\">Label:</span> <span>${escapeHtml(label)}</span>
      <span class=\"muted\" style=\"margin-left:10px;\">Tags:</span> ${tagHtml}`;
  }

  const lineage = q("#rdLineage", el);
  if(lineage){
    const parentRun = run?.parent_run_key ? findRun(run.parent_run_key) : null;
    const parentShown = run?.parent_run_key ? displayId(parentRun || {run_key: run.parent_run_key}, {fallback: String(run.parent_run_key)}) : "";
    const parent = run?.parent_run_key ? `<a class=\"mono\" href=\"#/run/${encodeURIComponent(run.parent_run_key)}\">parent: ${escapeHtml(shortId(parentShown, 60))}</a>` : `<span class=\"muted\">parent: —</span>`;
    const depth = `<span class=\"muted\">depth:</span> <span>${escapeHtml(String(run?.depth ?? ""))}</span>`;
    const ck = run?.checkpoint_id ? `<span class=\"chip\">checkpoint: ${escapeHtml(shortId(run.checkpoint_id, 44))}</span>` : ``;
    const rootLink = run?.root_run_key ? `<a class=\"btn\" href=\"#/run/${encodeURIComponent(run.root_run_key)}\">root run</a>` : ``;
    lineage.innerHTML = `${parent} ${depth} ${ck} ${rootLink}`;
  }

  const warn = q("#rdWarnings", el);
  if(warn){
    warn.innerHTML = strict
      ? `<div class="badge b-strict">STRICT mode</div><div class="muted" style="font-size:12px;">Commands should lock apply_phase to avoid determinism drift.</div>`
      : `<div class="badge b-unknown">Non-strict</div><div class="muted" style="font-size:12px;">apply_phase may be inferred from determinism rules.</div>`;
  }

  // Archive button label
  const btnArchive = q("#btnArchive", el);
  if(btnArchive){
    const isArchived = !!run?.archived;
    btnArchive.textContent = isArchived ? "Unarchive" : "Archive";
    btnArchive.classList.toggle("btn-danger", !isArchived);
  }

  // Charts
  drawRunCharts(el, State.runDetail.epiav, State.runDetail.parentEpiav);
  drawTimelineCanvas(el);

  const epiInfo = q("#epiInfo", el);
  if(epiInfo){
    epiInfo.textContent = `Source: epi_av_summary.csv (incremental tail). Parent overlay is best-effort.`;
  }
  const ovInfo = q("#rdOverlayInfo", el);
  if(ovInfo){
    const st = State.runDetail.overlayStatus;
    if(!st){
      ovInfo.textContent = "";
    }else if(st.state === "OK"){
      const shown = displayId(findRun(String(st.parent_run_key || "")) || {run_key: String(st.parent_run_key || "")}, {fallback: String(st.parent_run_key || "")});
      ovInfo.textContent = State.runDetail.overlayParent ? `Parent overlay: ${shortId(shown, 48)}` : "";
    }else if(st.state === "NO_PARENT"){
      ovInfo.textContent = "Parent overlay: no parent run.";
    }else if(st.state === "SCHEMA_MISMATCH"){
      ovInfo.textContent = `Parent overlay disabled: SCHEMA MISMATCH. ${st.message || ""}`;
    }else if(st.state === "UNAVAILABLE"){
      ovInfo.textContent = `Parent overlay unavailable. ${st.message || ""}`;
    }else{
      ovInfo.textContent = `Parent overlay: ${String(st.state || "")}`;
    }
  }

  const avInfo = q("#avInfo", el);
  if(avInfo){
    avInfo.textContent = `AV may be N/A early (window not yet available).`;
  }

  // Metrics + knobs
  renderMetricsPanel(el);
  renderKnobsPanel(el);

  // Tables (preserve inner scroll positions)
  const ckWrap = q("#ckWrap", el);
  const ckSt = ckWrap ? ckWrap.scrollTop : 0;
  renderCheckpointTable(el, rk, State.runDetail.checkpoints);
  if(ckWrap) ckWrap.scrollTop = ckSt;

  const iqWrap = q("#iqWrap", el);
  const iqSt = iqWrap ? iqWrap.scrollTop : 0;
  renderInboxQueueTable(el, State.runDetail.inboxQueue);
  if(iqWrap) iqWrap.scrollTop = iqSt;

  const acWrap = q("#acWrap", el);
  const acSt = acWrap ? acWrap.scrollTop : 0;
  renderAppliedCommandsTable(el);
  if(acWrap) acWrap.scrollTop = acSt;

  const apWrap = q("#apWrap", el);
  const apSt = apWrap ? apWrap.scrollTop : 0;
  renderAutopilotActionsTable(el);
  if(apWrap) apWrap.scrollTop = apSt;

  // Notes (do not clobber user edits mid-typing)
  const notesBox = q("#notesBox", el);
  const notesInfo = q("#notesInfo", el);
  const serverNotes = String(State.runDetail.notes?.text || "");
  if(notesBox && !notesBox.dataset.dirty && document.activeElement !== notesBox){
    if(notesBox.value !== serverNotes) notesBox.value = serverNotes;
  }
  if(notesInfo){
    const updated = State.runDetail.notes?.updated_at ? `Updated: ${escapeHtml(State.runDetail.notes.updated_at)}` : "";
    const dirty = notesBox?.dataset.dirty ? "(unsaved edits)" : "";
    notesInfo.innerHTML = `<span class=\"muted\">${updated} ${dirty}</span>`;
  }
}
function renderMetricsPanel(root){
  const sel = q("#rdMetric", root);
  const info = q("#metricInfo", root);
  const schema = State.runDetail.metricsSchema;
  const colsRaw = Array.isArray(schema?.columns) ? schema.columns : [];
  const cols = colsRaw.filter(c=>c && c !== "generation");

  if(sel){
    const colsSig = cols.join("|");
    const current = String(State.runDetail.metricName || "");

    // Only rebuild the <select> options when the schema changes.
    // Otherwise, we keep the element stable so refresh ticks don’t close the dropdown.
    if(!cols.length){
      if(sel.dataset.colsSig !== colsSig){
        sel.innerHTML = "";
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "(no metrics)";
        sel.appendChild(opt);
        sel.dataset.colsSig = colsSig;
      }
      sel.disabled = true;
    }else{
      sel.disabled = false;
      if(sel.dataset.colsSig !== colsSig){
        const prev = String(sel.value || "");
        sel.innerHTML = "";
        for(const c of cols){
          const opt = document.createElement("option");
          opt.value = c;
          opt.textContent = c;
          sel.appendChild(opt);
        }
        sel.dataset.colsSig = colsSig;

        const want = (current && cols.includes(current))
          ? current
          : (prev && cols.includes(prev) ? prev : (sel.options.length ? sel.options[0].value : ""));
        if(want){
          State.runDetail.metricName = want;
          sel.value = want;
        }
      }else{
        // Keep selection in sync, but don’t fight the user while the element is focused.
        if(document.activeElement !== sel){
          const want = (current && cols.includes(current))
            ? current
            : (sel.options.length ? sel.options[0].value : "");
          if(want && sel.value !== want) sel.value = want;
          if(!current && want) State.runDetail.metricName = want;
        }
      }
    }
  }

  const pts = Array.isArray(State.runDetail.metricSeries?.points) ? State.runDetail.metricSeries.points : [];
  const latest = (State.runDetail.metricSeries && State.runDetail.metricSeries.latest)
    ? State.runDetail.metricSeries.latest
    : (pts.length ? pts[pts.length - 1] : null);

  const prov = !!State.runDetail.metricSeries?.provisional;
  const pe = Number(State.runDetail.metricSeries?.parse_errors || 0);
  const src = State.runDetail.metricSource || schema?.source || null;

  if(info){
    if(!schema){
      info.textContent = "No metrics source found for this run.";
    }else if(!colsRaw.length){
      info.textContent = "Metrics schema loaded, but no columns found.";
    }else{
      const gTxt = (latest && latest.generation !== undefined && latest.generation !== null) ? String(latest.generation) : "?";
      const vTxt = (latest && latest.value !== null && latest.value !== undefined) ? fmtNum(Number(latest.value), 6) : "-";
      let t = `Metric: ${String(State.runDetail.metricName || "")}`;
      if(latest) t += ` · latest @ gen ${gTxt}: ${vTxt}`;
      else t += " · (no data yet)";
      if(prov) t += " · provisional";
      if(pe) t += ` · parse_errors=${pe}`;
      if(src) t += ` · source: ${src}`;
      info.textContent = t;
    }
  }

  drawMetricChart(root, State.runDetail.metricSeries);
}

function renderKnobsPanel(root){
  const info = q("#knobsInfo", root);
  const wrap = q("#knobsTableWrap", root);
  if(!wrap) return;

  const rows = Array.isArray(State.runDetail.knobs) ? State.runDetail.knobs : [];
  const src = State.runDetail.knobsSource || null;

  if(!rows.length){
    wrap.style.display = "none";
    if(info){
      if(State.runDetail.knobs === null){
        info.textContent = "Click Load to read run_config.csv." + (src ? ` Source: ${src}` : "");
      }else{
        info.textContent = "No knobs rows found." + (src ? ` Source: ${src}` : "");
      }
    }
    return;
  }

  wrap.style.display = "block";
  if(info){
    info.textContent = `Loaded ${rows.length} rows` + (src ? ` from ${src}` : "");
  }

  let html = '<div class="table-wrap"><table class="table"><thead><tr><th>Section</th><th>Key</th><th>Value</th></tr></thead><tbody>';
  for(const r of rows){
    html += `<tr><td>${escapeHtml(r?.section || "")}</td><td class="mono">${escapeHtml(r?.key || "")}</td><td class="mono">${escapeHtml(r?.value || "")}</td></tr>`;
  }
  html += '</tbody></table></div>';
  wrap.innerHTML = html;
}

function bytesToHuman(n){
  const x = Number(n);
  if(!Number.isFinite(x) || x < 0) return "";
  const units = ["B","KB","MB","GB","TB"];
  let v = x;
  let i = 0;
  while(v >= 1024 && i < units.length-1){ v /= 1024; i += 1; }
  return `${v.toFixed(v<10 && i>0 ? 2 : 1).replace(/\.0+$/,'')} ${units[i]}`;
}

function renderCheckpointTable(root, runKey, payload){
  const body = q("#ckBody", root);
  if(!body) return;
  const cps = Array.isArray(payload?.checkpoints) ? payload.checkpoints : [];
  const branchCounts = computeCheckpointBranchCounts(runKey);

  if(cps.length === 0){
    body.innerHTML = `<tr><td colspan="7" class="muted">No checkpoints indexed.</td></tr>`;
    return;
  }

  body.innerHTML = cps.map((c, idx)=>{
    const gen = Number(c?.generation);
    const id = String(c?.checkpoint_id || "");
    const exists = !!c?.exists;
    const size = (c?.size_bytes === null || c?.size_bytes === undefined) ? "" : bytesToHuman(c.size_bytes);
    const rel = String(c?.relative_path || "");
    const branches = branchCounts.get(id) || 0;

    const resumeCmd = `python -m engine.main --resume_from "${runKey}/${rel}" --runs_dir <RUNS_ROOT> --run_id <NEW_RUN_ID>`;
    const disabled = exists ? "" : "disabled";

    return `<tr data-ck="${escapeHtml(id)}" data-gen="${escapeHtml(String(gen))}" data-exists="${exists?"1":"0"}">
      <td class="num">${escapeHtml(Number.isFinite(gen)?String(gen):"")}</td>
      <td>${escapeHtml(id)}${exists?"":" <span class=\"badge b-corrupt\">MISSING</span>"}</td>
      <td class="num">${escapeHtml(String(branches))}</td>
      <td class="num">${escapeHtml(size || "")}</td>
      <td>
        <button class="btn small ckCopy" ${disabled} data-copy="${escapeHtml(resumeCmd)}">Copy command</button>
      </td>
      <td class="muted">${escapeHtml(String(c?.label || ""))}</td>
      <td>
        <button class="btn small ckBranch" ${disabled}>Plan branch</button>
        <button class="btn small ckCont" ${disabled}>Continue</button>
      </td>
    </tr>`;
  }).join("\n");

  qa(".ckCopy", body).forEach(btn=>{
    safeOn(btn, "click", async ()=>{
      const txt = btn.getAttribute("data-copy") || "";
      try{
        await navigator.clipboard.writeText(txt);
        showToast("Copied", "Resume command copied to clipboard.");
      }catch(e){
        openModal("Copy resume command", `<div class="muted" style="margin-bottom:10px;">Clipboard access failed. Copy manually:</div><pre style="white-space:pre-wrap;margin:0;">${escapeHtml(txt)}</pre>`);
      }
    });
  });

  qa(".ckBranch", body).forEach(btn=>{
    safeOn(btn, "click", ()=>{
      const tr = btn.closest("tr");
      const ckId = tr ? tr.getAttribute("data-ck") : "";
      const gen = tr ? maybeInt(tr.getAttribute("data-gen")) : null;
      openPlanWizard({kind:"branch", parentRunKey: runKey, checkpointId: ckId || null, checkpointGeneration: gen});
    });
  });
  qa(".ckCont", body).forEach(btn=>{
    safeOn(btn, "click", ()=>{
      const tr = btn.closest("tr");
      const ckId = tr ? tr.getAttribute("data-ck") : "";
      const gen = tr ? maybeInt(tr.getAttribute("data-gen")) : null;
      openPlanWizard({kind:"continuation", parentRunKey: runKey, checkpointId: ckId || null, checkpointGeneration: gen});
    });
  });
}

function renderInboxQueueTable(root, payload){
  const body = q("#iqBody", root);
  if(!body) return;

  const pl = payload || State.runDetail.inboxQueue;
  const items = Array.isArray(pl?.items) ? pl.items : [];
  if(items.length === 0){
    body.innerHTML = `<tr><td colspan="6" class="muted">No inbox commands found.</td></tr>`;
    return;
  }

  // Pending first, then acked, then invalid.
  const rank = (it)=>{
    const st = String(it?.queue_status || "").toUpperCase();
    if(st === "PENDING") return 0;
    if(st === "ACKED") return 1;
    if(st === "INVALID") return 2;
    return 1;
  };

  const sorted = items.slice().sort((a,b)=>{
    const ra = rank(a), rb = rank(b);
    if(ra !== rb) return ra - rb;
    // Prefer most recently touched so operators see what they just submitted.
    const ma = String(a?.mtime_utc || "");
    const mb = String(b?.mtime_utc || "");
    if(ma && mb && ma !== mb) return ma < mb ? 1 : -1;
    const ga = Number(a?.target_generation);
    const gb = Number(b?.target_generation);
    if(Number.isFinite(ga) && Number.isFinite(gb) && ga !== gb) return gb - ga;
    return String(b?.filename||"").localeCompare(String(a?.filename||""));
  });

  body.innerHTML = sorted.map((it, idx)=>{
    const ack = String(it?.ack_status || "").toUpperCase();
    const qst = String(it?.queue_status || "").toUpperCase();

    const stTxt = ack || qst || "PENDING";
    const stClass = (ack === "APPLIED")
      ? "b-pass"
      : (ack === "REJECTED")
        ? "b-fail"
        : (qst === "INVALID")
          ? "b-corrupt"
          : "b-incomplete";

    const gen = (it?.target_generation === null || it?.target_generation === undefined) ? "" : String(it.target_generation);
    const ctype = String(it?.command_type || "");
    const phase = String(it?.apply_phase || "");
    const uuid = String(it?.command_uuid || "");

    // Short human summary (best-effort).
    let summary = "";
    if(it?.parse_error){
      summary = "parse error";
    }else{
      const p = it?.payload;
      if(p && typeof p === "object" && String(p.command_type||"") === "TraitInjectionCommand"){
        const ti = p.trait_injection;
        if(ti && typeof ti === "object"){
          const dur = ti.duration_generations;
          const loci = Array.isArray(ti.locus_tags) ? ti.locus_tags.filter(Boolean).join(",") : "";
          const av = (ti.allele_values && typeof ti.allele_values === "object") ? Object.keys(ti.allele_values).slice(0,3).join(",") : "";
          const bits = [];
          if(dur !== null && dur !== undefined) bits.push(`dur=${dur}`);
          if(loci) bits.push(`loci=${loci}`);
          if(av) bits.push(`alleles=${av}`);
          summary = bits.join(" ");
        }
      }
    }

    return `<tr>
      <td class="muted">${escapeHtml(shortId(uuid, 22))}<div class="muted" style="font-size:11px;margin-top:2px;">${escapeHtml(String(it?.location || ""))}</div></td>
      <td>${badge(stTxt, stClass)}</td>
      <td class="num">${escapeHtml(gen)}</td>
      <td class="muted">${escapeHtml(shortId(phase, 30))}</td>
      <td>${escapeHtml(shortId(ctype || "(unknown)", 26))}</td>
      <td>
        <div class="row" style="gap:8px;align-items:center;flex-wrap:wrap;">
          <button class="btn small iqView" data-i="${idx}">View</button>
          <span class="muted">${escapeHtml(shortId(summary, 70))}</span>
        </div>
      </td>
    </tr>`;
  }).join("\n");

  qa(".iqView", body).forEach(btn=>{
    safeOn(btn, "click", ()=>{
      const i = parseInt(btn.getAttribute("data-i") || "-1", 10);
      if(!Number.isFinite(i) || i < 0 || i >= sorted.length) return;
      const it = sorted[i];

      const metaLines = [];
      metaLines.push(`File: ${it?.filename || ""}`);
      if(it?.mtime_utc) metaLines.push(`mtime: ${it.mtime_utc}`);
      if(it?.size_bytes !== null && it?.size_bytes !== undefined) metaLines.push(`size: ${bytesToHuman(it.size_bytes)}`);
      if(it?.queue_status) metaLines.push(`queue_status: ${it.queue_status}`);
      if(it?.ack_status) metaLines.push(`ack_status: ${it.ack_status}`);

      const payloadObj = it?.payload;
      const pretty = payloadObj ? JSON.stringify(payloadObj, null, 2) : "(payload unavailable / parse error)";
      openModal(
        "Inbox command",
        `<div class="muted" style="white-space:pre-wrap;font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;">${escapeHtml(metaLines.join("\n"))}</div>
         <div style="margin-top:10px;"><pre style="white-space:pre-wrap;margin:0;">${escapeHtml(pretty)}</pre></div>`
      );
    });
  });
}

function renderAppliedCommandsTable(root, payload){
  const body = q("#acBody", root);
  if(!body) return;

  const wrap = q("#acWrap", root);
  const info = q("#acInfo", root);

  const pl = payload || State.runDetail.appliedCommands;
  const rawItems = Array.isArray(pl?.items) ? pl.items : [];

  const w = (pl && pl.window) ? pl.window : (State.runDetail.appliedWindow || null);

  const qtxt = String(State.runDetail.appliedSearch || "").trim().toLowerCase();
  const stf = String(State.runDetail.appliedStatus || "ALL").toUpperCase();

  // Client-side filters (server handles gen paging).
  let items = rawItems.slice();
  if(stf !== "ALL"){
    items = items.filter(it => String(it?.ack_status || "").toUpperCase() === stf);
  }
  if(qtxt){
    items = items.filter(it => {
      const hay = [
        it?.command_uuid,
        it?.command_type,
        it?.apply_phase,
        it?.error_reason,
        it?.command_summary,
        it?.summary,
        it?.status_detail,
      ].filter(Boolean).join(" ").toLowerCase();
      return hay.includes(qtxt);
    });
  }

  // Sort by applied_generation (descending / newest first).
  items.sort((a,b)=> (Number(b?.applied_generation ?? -1) - Number(a?.applied_generation ?? -1)));

  // Update paging controls state.
  const btnOlder = q("#acOlder", root);
  const btnLatest = q("#acLatest", root);
  const btnNewer = q("#acNewer", root);

  const atLatest = (State.runDetail.appliedAfterGen < 0 && State.runDetail.appliedBeforeGen === null);

  if(btnLatest) btnLatest.disabled = atLatest;
  if(btnOlder) btnOlder.disabled = !(w && w.min_generation !== null && w.min_generation !== undefined);
  if(btnNewer) btnNewer.disabled = atLatest || !(w && w.max_generation !== null && w.max_generation !== undefined);

  // Info line (shows server paging window + local filters).
  if(info){
    const src = (pl && pl.source) ? String(pl.source) : "(none)";
    const base = src.split("/").pop();
    const winTxt = (w && w.min_generation !== null && w.min_generation !== undefined)
      ? `gen ${w.min_generation}..${w.max_generation}`
      : "gen n/a";

    const after = (w && w.after_gen !== undefined) ? w.after_gen : State.runDetail.appliedAfterGen;
    const before = (w && w.before_gen !== undefined) ? w.before_gen : State.runDetail.appliedBeforeGen;

    const pe = (pl && pl.parse_errors !== undefined) ? pl.parse_errors : 0;
    const prov = (pl && pl.provisional) ? " provisional" : "";

    const filterTxt = (stf !== "ALL" || qtxt)
      ? ` | filter: ${stf}${qtxt ? ` + "${qtxt}"` : ""}`
      : "";

    info.textContent = `Source: ${base} | window: ${winTxt} | loaded ${rawItems.length}, showing ${items.length} | paging: after=${after}, before=${before} | parse_errors=${pe}${prov}${filterTxt}`;
  }

  const scrollTop = wrap ? wrap.scrollTop : 0;

  if(items.length === 0){
    body.innerHTML = `<tr><td colspan="6" class="muted">No commands (matching the current filters/window).</td></tr>`;
    if(wrap) wrap.scrollTop = scrollTop;
    return;
  }

  // IMPORTANT: this table header is:
  // uuid | status | target_gen | apply_phase | type | summary
  // Renderer must emit exactly those 6 columns in that order.
  body.innerHTML = items.map(it=>{
    const uuid = String(it?.command_uuid || "");
    const pend = !!(uuid && State.runDetail.pendingUuids && State.runDetail.pendingUuids.has(uuid));

    const stRaw = String(it?.ack_status || "UNKNOWN").toUpperCase();
    const st = pend ? "PENDING" : (stRaw || "UNKNOWN");

    // Prefer explicit target_generation; fall back to applied_generation for older schemas.
    const tg = (it?.target_generation === null || it?.target_generation === undefined)
      ? (it?.applied_generation === null || it?.applied_generation === undefined ? "" : String(it.applied_generation))
      : String(it.target_generation);

    const ph = String(it?.apply_phase || "");
    const typ = String(it?.command_type || "");

    // Summary: show rejection reason first, else best-effort details.
    const reason = String(it?.error_reason || "");
    const summary = pend
      ? "(pending)"
      : (reason || String(it?.command_summary || it?.summary || it?.status_detail || ""));

    // Human-friendly UUID: short display + full in title.
    const uuidShort = shortId(uuid, 22);

    // Badge styling (reuse existing b-* classes used elsewhere).
    const stClass = (st === "APPLIED")
      ? "b-pass"
      : (st === "REJECTED")
        ? "b-fail"
        : (st === "PENDING")
          ? "b-incomplete"
          : "b-unknown";

    return `<tr>
      <td class="mono" title="${escapeHtml(uuid)}">${escapeHtml(uuidShort)}</td>
      <td>${badge(st, stClass)}</td>
      <td class="num">${escapeHtml(tg)}</td>
      <td class="muted">${escapeHtml(shortId(ph, 30))}</td>
      <td class="mono">${escapeHtml(shortId(typ || "(unknown)", 32))}</td>
      <td class="muted">${escapeHtml(shortId(summary, 120))}</td>
    </tr>`;
  }).join("\n");

  if(wrap) wrap.scrollTop = scrollTop;
}

function renderAutopilotActionsTable(root, payload){
  const body = q("#apBody", root);
  if(!body) return;

  const wrap = q("#apWrap", root);
  const info = q("#apInfo", root);

  const pl = payload || State.runDetail.autopilotActions;
  const rawItems = Array.isArray(pl?.items) ? pl.items : [];
  const w = (pl && pl.window) ? pl.window : (State.runDetail.autopilotWindow || null);
  const qtxt = String(State.runDetail.autopilotSearch || "").trim().toLowerCase();

  let items = rawItems.slice();
  if(qtxt){
    items = items.filter(it => {
      const updates = Array.isArray(it?.updates) ? it.updates : [];
      const updateTxt = updates.map(u => `${u?.path || ""}:${u?.value}`).join(" ");
      const hay = [
        it?.command_uuid,
        it?.profile,
        it?.axis,
        it?.rule,
        it?.decision_rule,
        it?.ack_status,
        it?.ack_reason,
        updateTxt,
      ].filter(Boolean).join(" ").toLowerCase();
      return hay.includes(qtxt);
    });
  }

  const genOf = (it)=>{
    const g = Number(it?.generation);
    return Number.isFinite(g) ? g : -1;
  };
  items.sort((a,b)=> genOf(b) - genOf(a));

  const btnOlder = q("#apOlder", root);
  const btnLatest = q("#apLatest", root);
  const btnNewer = q("#apNewer", root);
  const atLatest = (State.runDetail.autopilotAfterGen < 0 && State.runDetail.autopilotBeforeGen === null);

  if(btnLatest) btnLatest.disabled = atLatest;
  if(btnOlder) btnOlder.disabled = !(w && w.min_generation !== null && w.min_generation !== undefined);
  if(btnNewer) btnNewer.disabled = atLatest || !(w && w.max_generation !== null && w.max_generation !== undefined);

  if(info){
    const src = (pl && pl.source) ? String(pl.source) : "(none)";
    const base = src.split("/").pop();
    const winTxt = (w && w.min_generation !== null && w.min_generation !== undefined)
      ? `gen ${w.min_generation}..${w.max_generation}`
      : "gen n/a";
    const after = (w && w.after_gen !== undefined) ? w.after_gen : State.runDetail.autopilotAfterGen;
    const before = (w && w.before_gen !== undefined) ? w.before_gen : State.runDetail.autopilotBeforeGen;
    const pe = (pl && pl.parse_errors !== undefined) ? pl.parse_errors : 0;
    const prov = (pl && pl.provisional) ? " provisional" : "";
    const filterTxt = qtxt ? ` | filter: "${qtxt}"` : "";
    info.textContent = `Source: ${base} | window: ${winTxt} | loaded ${rawItems.length}, showing ${items.length} | paging: after=${after}, before=${before} | parse_errors=${pe}${prov}${filterTxt}`;
  }

  const scrollTop = wrap ? wrap.scrollTop : 0;
  if(items.length === 0){
    body.innerHTML = `<tr><td colspan="7" class="muted">No autopilot actions (matching the current filters/window).</td></tr>`;
    if(wrap) wrap.scrollTop = scrollTop;
    return;
  }

  body.innerHTML = items.map((it, idx)=>{
    const g = genOf(it);
    const profile = String(it?.profile || "");
    const axis = String(it?.axis || "");
    const pressure = Number(it?.pressure_level);
    const updates = Array.isArray(it?.updates) ? it.updates : [];
    const rule = String(it?.rule || it?.decision_rule || it?.ack_status || "");
    const updatesTxt = updates.length
      ? updates.slice(0, 2).map(u => `${u?.path || ""}=${u?.value}`).join(" ; ")
      : "(none)";
    return `<tr>
      <td class="num">${escapeHtml(g >= 0 ? String(g) : "")}</td>
      <td class="muted">${escapeHtml(profile)}</td>
      <td class="muted">${escapeHtml(axis)}</td>
      <td class="num">${escapeHtml(Number.isFinite(pressure) ? fmtNum(pressure, 3) : "")}</td>
      <td>${escapeHtml(shortId(rule, 42))}</td>
      <td class="mono">${escapeHtml(shortId(updatesTxt, 96))}</td>
      <td><button class="btn small apView" data-ap-idx="${idx}">JSON</button></td>
    </tr>`;
  }).join("\n");

  qa(".apView", body).forEach(btn=>{
    safeOn(btn, "click", ()=>{
      const idx = intOr(btn.getAttribute("data-ap-idx"), -1);
      if(idx < 0 || idx >= items.length) return;
      const it = items[idx] || {};
      const titleGen = Number.isFinite(Number(it?.generation)) ? ` @gen ${Number(it.generation)}` : "";
      openModal(
        `Autopilot action${titleGen}`,
        `<pre style="white-space:pre-wrap;margin:0;">${escapeHtml(JSON.stringify(it, null, 2))}</pre>`
      );
    });
  });

  if(wrap) wrap.scrollTop = scrollTop;
}


function drawLineChart(canvas, seriesList, opts){
  if(!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width = canvas.clientWidth || 640;
  const h = canvas.height = canvas.clientHeight || 220;
  ctx.clearRect(0,0,w,h);

  const pad = 44; // room for y-axis tick labels
  const plotW = w - pad*2;
  const plotH = h - pad*2;

  const xMinOpt = Number(opts?.xMin);
  const xMaxOpt = Number(opts?.xMax);
  const clampX = Number.isFinite(xMinOpt) && Number.isFinite(xMaxOpt) && (xMaxOpt > xMinOpt);

  // Determine extents (optionally clamped in X).
  let xmin = clampX ? xMinOpt : null;
  let xmax = clampX ? xMaxOpt : null;
  let ymin = null;
  let ymax = null;

  for(const s of seriesList){
    const pts = Array.isArray(s?.points) ? s.points : [];
    for(const p of pts){
      const x = Number(p?.x);
      const y = (p?.y===null || p?.y===undefined) ? null : Number(p.y);
      if(!Number.isFinite(x)) continue;
      if(clampX && (x < xmin || x > xmax)) continue;

      if(!clampX){
        if(xmin===null || x < xmin) xmin = x;
        if(xmax===null || x > xmax) xmax = x;
      }

      if(y===null || !Number.isFinite(y)) continue;
      if(ymin===null || y < ymin) ymin = y;
      if(ymax===null || y > ymax) ymax = y;
    }
  }

  if(xmin===null || xmax===null){ xmin = 0; xmax = 1; }
  if(xmax === xmin) xmax = xmin + 1;
  if(ymin===null || ymax===null){ ymin = 0; ymax = 1; }
  if(ymax === ymin) ymax = ymin + 1e-9;

  const yPad = (ymax - ymin) * 0.08;
  ymin -= yPad;
  ymax += yPad;

  const xScale = x => pad + ((x - xmin)/(xmax - xmin)) * plotW;
  const yScale = y => pad + plotH - ((y - ymin)/(ymax - ymin)) * plotH;

  // Grid
  ctx.strokeStyle = "rgba(160,176,194,0.12)";
  ctx.lineWidth = 1;
  for(let i=0;i<=4;i++){
    const yy = pad + (plotH*i/4);
    ctx.beginPath();
    ctx.moveTo(pad, yy);
    ctx.lineTo(pad + plotW, yy);
    ctx.stroke();
  }


  // Y axis tick labels
  const yr = (ymax - ymin) || 1;
  let digits = 0;
  if (yr < 1) digits = 3;
  else if (yr < 10) digits = 2;
  else if (yr < 100) digits = 1;
  const yTicks = 4;
  ctx.fillStyle = "rgba(160,176,194,0.85)";
  ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace";
  for(let i=0;i<=yTicks;i++){
    const frac = i / yTicks;
    const yVal = ymax - (frac * yr);
    const yy = pad + (frac * plotH);
    const label = Number.isFinite(yVal) ? yVal.toFixed(digits) : "";
    const tw = ctx.measureText(label).width;
    ctx.fillText(label, Math.max(2, pad - tw - 8), yy + 4);
  }

  // Series
  for(const s of seriesList){
    const pts = Array.isArray(s?.points) ? s.points : [];
    ctx.strokeStyle = s.color || "rgba(102,163,255,0.95)";
    ctx.lineWidth = s.width || 2;
    ctx.globalAlpha = (s.alpha===undefined ? 1.0 : s.alpha);
    ctx.beginPath();
    let started = false;
    for(const p of pts){
      const x = Number(p?.x);
      const y = (p?.y===null || p?.y===undefined) ? null : Number(p.y);
      if(!Number.isFinite(x)){ started = false; continue; }
      if(clampX && (x < xmin || x > xmax)){ started = false; continue; }
      if(y===null || !Number.isFinite(y)){ started = false; continue; }
      const xp = xScale(x);
      const yp = yScale(y);
      if(!started){ ctx.moveTo(xp, yp); started = true; }
      else { ctx.lineTo(xp, yp); }
    }
    if(started) ctx.stroke();
    ctx.globalAlpha = 1.0;
  }

  // X axis labels (min/max)
  ctx.fillStyle = "rgba(160,176,194,0.85)";
  ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace";
  ctx.fillText(String(Math.floor(xmin)), pad, h - 8);
  const xmaxTxt = String(Math.floor(xmax));
  const tw = ctx.measureText(xmaxTxt).width;
  ctx.fillText(xmaxTxt, pad + plotW - tw, h - 8);
}

function drawRunCharts(root, epiavPayload, parentPayload){
  const epiCanvas = q("#epiChart", root);
  const avCanvas = q("#avChart", root);
  if(!epiCanvas || !avCanvas) return;

  const ptsAll = Array.isArray(epiavPayload?.points) ? epiavPayload.points : [];
  const pts = _windowByGen(ptsAll, State.runDetail.range);

  // Determine x range from the (windowed) child run.
  let xMin = null, xMax = null;
  for(const p of pts){
    const g = Number(p?.generation);
    if(!Number.isFinite(g)) continue;
    if(!Number.isFinite(xMin) || g < xMin) xMin = g;
    if(!Number.isFinite(xMax) || g > xMax) xMax = g;
  }
  if(!Number.isFinite(xMin) || !Number.isFinite(xMax) || xMin === xMax){
    // If only a single point exists, expand around it so it's visible.
    if(Number.isFinite(xMax)){
      xMin = xMax - 1;
      xMax = xMax + 1;
    }else{
      xMin = 0; xMax = 1;
    }
  }

  // Anchor x-axis to gen 0 when the selected window includes it (early-run clarity).
  const _rng = Number(State.runDetail.range);
  if(Number.isFinite(_rng) && _rng > 0 && Number.isFinite(xMax)){
    const _startWanted = xMax - _rng + 1;
    if(Number.isFinite(_startWanted) && _startWanted <= 0) xMin = 0;
  }

  const pAll = Array.isArray(parentPayload?.points) ? parentPayload.points : [];
  const overlay = !!State.runDetail.overlayParent && pAll.length>0;
  const ppts = overlay
    ? pAll.filter(p=>{
        const g = Number(p?.generation);
        return Number.isFinite(g) && g >= xMin && g <= xMax;
      })
    : [];

  const epiRaw = pts.map(p=>({ x: Number(p.generation), y: Number(p.epi) }));
  const epiList = [
    (State.runDetail.smoothEpi ? { label: "EPI MA20", points: _movingAverage(epiRaw, 20),  color: "rgba(102,163,255,0.35)", width: 3  } : { label: "EPI", points: epiRaw,  color: "rgba(102,163,255,0.95)"  }),
    (overlay ? { label: "parent", points: ppts.map(p=>({ x: Number(p.generation), y: Number(p.epi) })), color: "rgba(160,176,194,0.55)" } : null),
  ].filter(Boolean);

  const avRaw = pts.map(p=>({ x: Number(p.generation), y: (p.av===null || p.av===undefined) ? null : Number(p.av) }));
  const avList = [
    { label: "AV", points: avRaw, color: "rgba(102,163,255,0.95)" },
    (overlay ? { label: "parent", points: ppts.map(p=>({ x: Number(p.generation), y: (p.av===null || p.av===undefined) ? null : Number(p.av) })), color: "rgba(160,176,194,0.55)" } : null),
  ].filter(Boolean);

  const xOpts = { xMin, xMax };
  drawLineChart(epiCanvas, epiList, xOpts);
  drawLineChart(avCanvas, avList, xOpts);
}


function drawTimelineCanvas(root){
  const canvas = q("#rdTimelineCanvas", root);
  const info = q("#rdTimelineInfo", root);
  const sum = State.runDetail.timelineSummary;

  if(!canvas){
    return;
  }

  const ctx = canvas.getContext("2d");
  if(!ctx){
    return;
  }

  const w = canvas.width;
  const h = canvas.height;

  // Clear
  ctx.clearRect(0, 0, w, h);

  if(!sum || !Array.isArray(sum.bins) || !sum.bins.length){
    if(info) info.textContent = "No timeline data (events/metrics missing).";
    // faint baseline
    ctx.globalAlpha = 0.2;
    ctx.fillRect(0, h/2, w, 1);
    ctx.globalAlpha = 1.0;
    return;
  }

  const bins = sum.bins;
  const genMax = Number(sum.generation_max || 0);
  const bw = w / bins.length;

  // Lane layout
  const laneH = Math.floor(h / 3);
  const lane0Y = 0;
  const lane1Y = laneH;
  const lane2Y = laneH * 2;

  // Background grid
  ctx.globalAlpha = 0.08;
  for(let i=0;i<=10;i++){
    const x = Math.floor((i/10)*w);
    ctx.fillRect(x, 0, 1, h);
  }
  ctx.globalAlpha = 1.0;

  // Pressure lane background: shock bands
  for(let i=0;i<bins.length;i++){
    const b = bins[i] || {};
    const pf = b.pressure_flags || {};
    if(pf.shock){
      ctx.globalAlpha = 0.12;
      ctx.fillStyle = "#ff4d4d";
      ctx.fillRect(i*bw, lane1Y, bw, laneH);
      ctx.globalAlpha = 1.0;
    }
    if(pf.multiplier_change){
      ctx.globalAlpha = 0.7;
      ctx.fillStyle = "#b26bff";
      ctx.fillRect(i*bw + bw/2, lane1Y, 2, laneH);
      ctx.globalAlpha = 1.0;
    }
  }

  // Control events: vertical markers with height by count
  for(let i=0;i<bins.length;i++){
    const b = bins[i] || {};
    const cc = b.control_counts || {};
    const u = Number(cc.user||0);
    const a = Number(cc.autopilot||0);
    const s = Number(cc.system||0);
    const total = u+a+s;
    if(total <= 0) continue;

    const maxBar = laneH - 10;
    const scale = Math.min(1, total / 5);
    const barH = Math.max(2, Math.floor(maxBar * scale));
    const x0 = i*bw;

    // Stack-ish tiny bars.
    const drawBar = (val, color, xoff)=>{
      if(val <= 0) return;
      ctx.fillStyle = color;
      const hh = Math.max(2, Math.floor(barH * Math.min(1, val/total)));
      ctx.fillRect(x0 + xoff, lane0Y + (laneH - hh), Math.max(2, bw/4), hh);
    };
    drawBar(u, "#4da3ff", bw*0.10);
    drawBar(a, "#ffa14d", bw*0.40);
    drawBar(s, "#aaaaaa", bw*0.70);
  }

  // Labels
  ctx.fillStyle = "#cfcfcf";
  ctx.globalAlpha = 0.9;
  ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace";
  ctx.fillText("Control events (user/auto/system)", 8, 14);
  ctx.fillText("Pressure (shock band, multiplier Δ ticks)", 8, lane1Y + 14);
  ctx.fillText(`gen 0 .. ${genMax}`, 8, lane2Y + 14);
  ctx.globalAlpha = 1.0;

  // Bottom lane: simple baseline
  ctx.globalAlpha = 0.25;
  ctx.fillStyle = "#cfcfcf";
  ctx.fillRect(0, lane2Y + laneH/2, w, 1);
  ctx.globalAlpha = 1.0;

  if(info){
    const srcE = sum.sources ? sum.sources.events : null;
    const srcM = sum.sources ? sum.sources.metrics : null;
    info.textContent = `bins=${bins.length} (width≈${sum.bin_width||"?"} gen) · events=${srcE? "yes":"no"} · metrics=${srcM? "yes":"no"}`;
  }
}


function drawMetricChart(root, metricPayload){
  const canvas = q("#metricChart", root);
  if(!canvas) return;

  const ptsAll = Array.isArray(metricPayload?.points) ? metricPayload.points : [];
  const pts = _windowByGen(ptsAll, State.runDetail.range);
  const series = pts.map(p=>({x: Number(p.generation), y: p.value}));

  // Prefer a stable xMax based on run status, so the axis doesn’t “snap”
  // when the server returns incremental deltas (or temporary empty batches).
  const curGen = Number(State.runDetail?.meta?.run?.current_generation);
  let xMin = null;
  let xMax = null;

  for(const p of pts){
    const g = Number(p?.generation);
    if(!Number.isFinite(g)) continue;
    if(xMin===null || g < xMin) xMin = g;
    if(xMax===null || g > xMax) xMax = g;
  }

  if(Number.isFinite(curGen)){
    if(xMax===null || curGen > xMax) xMax = curGen;
    if(xMin===null) xMin = curGen;
  }

  const _rng = Number(State.runDetail.range);
  if(Number.isFinite(_rng) && _rng > 0 && xMax!==null && Number.isFinite(xMax)){
    const startWanted = xMax - _rng + 1;
    xMin = Math.max(0, startWanted);
  }

  const xOpts = (xMin!==null && xMax!==null && Number.isFinite(xMin) && Number.isFinite(xMax) && xMax > xMin)
    ? {xMin, xMax}
    : {};

  drawLineChart(canvas, [{points: series, color: "rgba(102,163,255,0.95)", width: 2, alpha: 1.0}], xOpts);
}
