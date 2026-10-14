(function () {
  "use strict";

  const State = {
    jobs: [],
    selectedJobId: "",
    scheduler: null,
    watchdog: null,
    healer: null,
    observationIntegrity: null,
    schedulerV2: null,
  };

  function q(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function clone(value) {
    try {
      return JSON.parse(JSON.stringify(value || {}));
    } catch (_err) {
      return {};
    }
  }

  async function apiGet(url) {
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    const payload = await res.json();
    if (!res.ok) throw new Error(String(payload.error || res.status));
    return payload;
  }

  async function apiPost(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body || {}),
    });
    const payload = await res.json();
    if (!res.ok) throw new Error(String(payload.error || res.status));
    return payload;
  }

  function selectedJob() {
    return State.jobs.find(function (row) {
      return String(row.job_id || "") === String(State.selectedJobId || "");
    }) || null;
  }

  function renderJobs() {
    const root = q("jobList");
    if (!root) return;
    if (!State.jobs.length) {
      root.textContent = "No scheduled jobs yet.";
      return;
    }
    root.innerHTML = State.jobs.map(function (row) {
      const due = row.due || {};
      const projection = row.scheduler_projection_v1 || {};
      const active = String(row.job_id || "") === String(State.selectedJobId || "");
      const status = String(row.scheduler_status || projection.projected_status || "idle");
      const reason = String(row.scheduler_status_reason || projection.projection_reason || due.reason || "");
      const degraded = Boolean(row.scheduler_truth_degraded || projection.durable_conflict || status === "ambiguous");
      return (
        '<button class="sched-row' + (active ? ' active' : '') + '" data-job-id="' + esc(row.job_id) + '">' +
        '<div class="sched-head"><div class="sched-title">' + esc(row.job_id) + '</div><span class="sched-status ' + esc(status) + '">' + esc(status) + '</span></div>' +
        '<div class="sched-meta"><span>kind=' + esc(row.job_kind || "single_turn") + '</span><span>role=' + esc(row.role) + '</span><span>binding=' + esc((row.binding || {}).type) + ':' + esc((row.binding || {}).id) + '</span></div>' +
        '<div class="sched-meta"><span>due=' + esc(due.eligible ? "yes" : "no") + '</span><span>next=' + esc(due.due_utc || "-") + '</span><span>reason=' + esc(reason || "-") + '</span>' + (degraded ? '<span>truth=degraded</span>' : '') + '</div>' +
        '<div>' + esc(row.prompt_preview || "") + '</div>' +
        '</button>'
      );
    }).join("");
    root.querySelectorAll("[data-job-id]").forEach(function (el) {
      el.addEventListener("click", function () {
        const jobId = String(el.getAttribute("data-job-id") || "");
        if (!jobId) return;
        State.selectedJobId = jobId;
        renderJobs();
        loadJob(jobId).catch(function (err) {
          window.alert(String(err));
        });
      });
    });
  }

  function fillForm() {
    const row = selectedJob();
    const spec = row || {};
    const binding = spec.binding || {};
    const schedule = spec.schedule || {};
    const policy = spec.policy || {};
    const expected = spec.expected_output || {};
    const runtime = spec.runtime || {};
    q("jobId").value = String(spec.job_id || "");
    q("role").value = String(spec.role || "generalist");
    q("bindingType").value = String(binding.type || "task_id");
    q("bindingId").value = String(binding.id || "");
    q("mode").value = String(schedule.mode || "once");
    q("timezone").value = String(schedule.timezone || "UTC");
    q("enabled").value = String(Boolean(spec.enabled));
    q("runAt").value = String(schedule.run_at || "");
    q("timeOfDay").value = String(schedule.time_of_day || "03:30");
    q("everyMinutes").value = String(schedule.every_minutes || 30);
    q("startAtUtc").value = String(schedule.start_at_utc || "");
    q("maxRuntime").value = String(policy.max_runtime_seconds || 1800);
    q("prompt").value = String(spec.prompt || spec.prompt_preview || "");
    q("contextRefs").value = Array.isArray(spec.context_refs) ? spec.context_refs.join(", ") : "";
    q("requiredContextBundle").value = String(spec.required_context_bundle || "");
    q("additionalContextRefs").value = Array.isArray(spec.additional_context_refs) ? spec.additional_context_refs.join(", ") : "";
    q("forbiddenContextRefs").value = Array.isArray(spec.forbidden_context_refs) ? spec.forbidden_context_refs.join(", ") : "";
    q("runtimeAgentTech").value = String(runtime.agent_tech || "");
    q("runtimeModel").value = String(runtime.model || "");
    q("runtimeReasoningEffort").value = String(runtime.reasoning_effort || "");
    q("runtimeFallbackModels").value = Array.isArray(runtime.fallback_models) ? runtime.fallback_models.join(", ") : "";
    q("runtimeFallbackAgentTechs").value = Array.isArray(runtime.fallback_agent_techs) ? runtime.fallback_agent_techs.join(", ") : "";
    q("outKind").value = String(expected.kind || "report");
    q("outFormat").value = String(expected.format || "markdown");
    syncMode();
  }

  function syncMode() {
    const mode = String(q("mode").value || "once");
    q("runAtWrap").style.display = mode === "once" ? "" : "none";
    q("timeOfDayWrap").style.display = mode === "daily" ? "" : "none";
    q("intervalWrap").style.display = mode === "interval" ? "" : "none";
  }

  function formPayload() {
    const base = clone(selectedJob() || {});
    delete base.state;
    delete base.due;
    delete base.runtime_role;

    const mode = String(q("mode").value || "once");
    const existingBinding = base.binding && typeof base.binding === "object" ? base.binding : {};
    const existingPolicy = base.policy && typeof base.policy === "object" ? base.policy : {};
    const existingRuntime = base.runtime && typeof base.runtime === "object" ? base.runtime : {};
    const existingExpected = base.expected_output && typeof base.expected_output === "object" ? base.expected_output : {};

    const schedule = { mode: mode, timezone: String(q("timezone").value || "UTC").trim() || "UTC" };
    if (mode === "once") {
      schedule.run_at = String(q("runAt").value || "").trim();
    } else if (mode === "daily") {
      schedule.time_of_day = String(q("timeOfDay").value || "").trim();
    } else {
      schedule.every_minutes = parseInt(String(q("everyMinutes").value || "30"), 10) || 30;
      const startAtUtc = String(q("startAtUtc").value || "").trim();
      if (startAtUtc) schedule.start_at_utc = startAtUtc;
    }

    return Object.assign(base, {
      job_id: String(q("jobId").value || "").trim(),
      enabled: String(q("enabled").value || "true") === "true",
      role: String(q("role").value || "generalist").trim(),
      binding: Object.assign({}, existingBinding, {
        type: String(q("bindingType").value || "task_id").trim(),
        id: String(q("bindingId").value || "").trim(),
      }),
      schedule: schedule,
      prompt: String(q("prompt").value || ""),
      context_refs: String(q("contextRefs").value || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean),
      required_context_bundle: String(q("requiredContextBundle").value || "").trim(),
      additional_context_refs: String(q("additionalContextRefs").value || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean),
      forbidden_context_refs: String(q("forbiddenContextRefs").value || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean),
      runtime: Object.assign({}, existingRuntime, {
        agent_tech: String(q("runtimeAgentTech").value || "").trim(),
        model: String(q("runtimeModel").value || "").trim(),
        reasoning_effort: String(q("runtimeReasoningEffort").value || "").trim(),
        fallback_models: String(q("runtimeFallbackModels").value || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean),
        fallback_agent_techs: String(q("runtimeFallbackAgentTechs").value || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean),
      }),
      expected_output: Object.assign({}, existingExpected, {
        kind: String(q("outKind").value || "report").trim(),
        format: String(q("outFormat").value || "markdown").trim(),
      }),
      policy: Object.assign({}, existingPolicy, {
        max_runtime_seconds: parseInt(String(q("maxRuntime").value || "1800"), 10) || 1800,
      }),
    });
  }

  function renderEvents(events) {
    const root = q("eventLog");
    if (!root) return;
    if (!Array.isArray(events) || !events.length) {
      root.textContent = "No execution events yet.";
      return;
    }
    root.textContent = events.map(function (row) {
      return [
        row.utc || "",
        row.job_id || "",
        row.run_id || "",
        row.event || "",
        row.status || "",
      ].join(" | ");
    }).join("\n");
  }

  function serviceLine(label, row, timeKey) {
    const safeRow = row || {};
    const running = Boolean(safeRow.running);
    return [
      label + "=" + (running ? "running" : "stopped"),
      "interval_s=" + String(safeRow.interval_s || "-"),
      timeKey + "=" + String(safeRow[timeKey] || "-"),
      "last_error=" + String(safeRow.last_error || "-"),
    ].join(" | ");
  }

  function schedulerV2Lines(bundle) {
    const safe = bundle || {};
    if (!safe || typeof safe !== "object" || !Object.keys(safe).length) return [];
    const ownership = safe.ownership || {};
    const projection = (((safe.shadow_payload || {}).evaluation || {}).projection || {});
    const progress = projection.family_progress || {};
    const lines = [
      [
        "scheduler_v2",
        "status=" + String(safe.status || "-"),
        "mode=" + String(ownership.mode || "-"),
        "owner=" + String(ownership.canonical_owner || "-"),
        "projection_equivalent=" + String(Boolean(safe.projection_equivalent)),
      ].join(" | "),
    ];
    if (projection && Object.keys(projection).length) {
      lines.push(
        [
          "v2_family",
          "parent=" + String(projection.family_parent_uid || "-"),
          "status=" + String(projection.family_status || "-"),
          "stage=" + String(projection.current_stage || "-"),
          "progress=" + String(progress.completed_children || 0) + "/" + String(progress.total_children || 0),
          "active=" + String(progress.active_children || 0),
          "blocked=" + String(progress.blocked_children || 0),
          "failed=" + String(progress.failed_children || 0),
        ].join(" | ")
      );
      const activeJob = projection.active_job || null;
      if (activeJob && activeJob.job_id) {
        lines.push(
          [
            "v2_active",
            "job_id=" + String(activeJob.job_id || "-"),
            "child_uid=" + String(activeJob.child_uid || "-"),
            "reason=" + String(activeJob.status_reason || "-"),
          ].join(" | ")
        );
      }
      const nextJobs = Array.isArray(projection.next_jobs) ? projection.next_jobs : [];
      if (nextJobs.length) {
        const head = nextJobs[0] || {};
        lines.push(
          [
            "v2_next",
            "job_id=" + String(head.job_id || "-"),
            "child_uid=" + String(head.child_uid || "-"),
            "reason=" + String(head.status_reason || "-"),
          ].join(" | ")
        );
      }
    }
    return lines;
  }

  function observationLines(observation) {
    const safe = observation || {};
    if (String(safe.status || "") !== "degraded") return [];
    const reasons = Array.isArray(safe.reasons) ? safe.reasons.filter(Boolean) : [];
    const parts = [
      "observation",
      "status=degraded",
    ];
    if (reasons.length) parts.push("reasons=" + reasons.join(","));
    if (safe.summary) parts.push(String(safe.summary));
    return [parts.join(" | ")];
  }

  function renderScheduler() {
    const root = q("schedulerStatus");
    if (!root) return;
    root.textContent = [
      serviceLine("scheduler", State.scheduler || {}, "last_tick_utc"),
      serviceLine("watchdog", State.watchdog || {}, "last_receipt_utc"),
      serviceLine("healer", State.healer || {}, "last_receipt_utc"),
    ].concat(observationLines(State.observationIntegrity)).concat(schedulerV2Lines(State.schedulerV2)).join("\n");
  }

  async function refreshJobs() {
    const payload = await apiGet("/api/schedules/jobs");
    State.jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
    State.scheduler = payload.scheduler || null;
    State.watchdog = payload.watchdog || null;
    State.healer = payload.healer || null;
    State.observationIntegrity = payload.observation_integrity || null;
    State.schedulerV2 = payload.scheduler_v2 || null;

    const selectedExists = State.jobs.some(function (row) {
      return String(row.job_id || "") === String(State.selectedJobId || "");
    });
    if (!selectedExists) {
      State.selectedJobId = State.jobs.length ? String(State.jobs[0].job_id || "") : "";
    }

    renderJobs();
    renderEvents(payload.events || []);
    renderScheduler();
    if (State.selectedJobId) {
      await loadJob(State.selectedJobId);
    } else {
      fillForm();
    }
  }

  async function loadJob(jobId) {
    const requestJobId = String(jobId || "").trim();
    const payload = await apiGet("/api/schedules/job?job_id=" + encodeURIComponent(requestJobId));
    const job = payload.job || {};
    job.prompt = String(job.prompt || job.prompt_preview || "");
    const idx = State.jobs.findIndex(function (row) { return String(row.job_id || "") === String(job.job_id || requestJobId || ""); });
    if (idx >= 0) State.jobs[idx] = job;
    else State.jobs.push(job);
    if (String(State.selectedJobId || "") !== requestJobId) {
      return job;
    }
    State.selectedJobId = String(job.job_id || requestJobId || "");
    fillForm();
    renderJobs();
    return job;
  }

  async function onSave(ev) {
    ev.preventDefault();
    const payload = await apiPost("/api/schedules/job/save", formPayload());
    State.selectedJobId = String(((payload || {}).job || {}).job_id || "");
    await refreshJobs();
  }

  async function onRunNow() {
    const jobId = String(q("jobId").value || "").trim();
    if (!jobId) return;
    await apiPost("/api/schedules/run_now", { job_id: jobId });
    await refreshJobs();
  }

  async function onToggle() {
    const jobId = String(q("jobId").value || "").trim();
    if (!jobId) return;
    const row = selectedJob();
    const enabled = !(row && row.enabled);
    await apiPost("/api/schedules/job/toggle", { job_id: jobId, enabled: enabled });
    await refreshJobs();
  }

  async function onTick() {
    await apiPost("/api/schedules/tick", { limit: 10 });
    await refreshJobs();
  }

  function onNew() {
    State.selectedJobId = "";
    q("jobForm").reset();
    q("role").value = "generalist";
    q("bindingType").value = "task_id";
    q("mode").value = "once";
    q("timezone").value = "UTC";
    q("enabled").value = "true";
    q("outKind").value = "report";
    q("outFormat").value = "markdown";
    q("maxRuntime").value = "1800";
    q("everyMinutes").value = "30";
    q("startAtUtc").value = "";
    syncMode();
    renderJobs();
  }

  function init() {
    q("mode").addEventListener("change", syncMode);
    q("jobForm").addEventListener("submit", function (ev) {
      onSave(ev).catch(function (err) { window.alert(String(err)); });
    });
    q("btnRefresh").addEventListener("click", function () { refreshJobs().catch(function (err) { window.alert(String(err)); }); });
    q("btnTick").addEventListener("click", function () { onTick().catch(function (err) { window.alert(String(err)); }); });
    q("btnRunNow").addEventListener("click", function () { onRunNow().catch(function (err) { window.alert(String(err)); }); });
    q("btnToggle").addEventListener("click", function () { onToggle().catch(function (err) { window.alert(String(err)); }); });
    q("btnNew").addEventListener("click", onNew);
    refreshJobs().catch(function (err) {
      q("jobList").textContent = String(err);
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
