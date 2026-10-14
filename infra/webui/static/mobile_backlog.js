(function () {
  "use strict";

  function el(id) { return document.getElementById(id); }

  const state = {
    items: [],
    selectedUids: [],
    topicFilter: "",
    groupMode: "status",
    topicCatalog: [],
    groups: [],
    scheduledBindings: {},
    latestCampaign: null,
    queueState: null,
    queuePreview: [],
    contractCheck: null,
    artifacts: null,
    runTriage: null,
    economics: null,
    archiveSnapshots: null,
    chronicle: null,
    shell: null,
  };

  function tokenValue() {
    const node = el("adminToken");
    return node ? String(node.value || "").trim() : "";
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

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  async function apiJson(url, opts) {
    const resp = await fetch(url, Object.assign({
      cache: "no-store",
      headers: Object.assign({ "Accept": "application/json" }, tokenHeaders(), (opts && opts.headers) ? opts.headers : {}),
    }, opts || {}));
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(String(data && data.error ? data.error : ("HTTP " + resp.status)));
    }
    return data;
  }

  function deriveSlug(meta) {
    const slug = String((meta && meta.slug) || "").trim();
    if (slug) return slug;
    const title = String((meta && meta.title) || "").trim().toLowerCase();
    if (!title) return "";
    return title.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
  }

  function itemMeta(item) {
    const nestedMeta = item && typeof item.meta === "object" ? item.meta : null;
    return nestedMeta || (item && typeof item === "object" ? item : {});
  }

  function itemStatus(item) {
    return String(itemMeta(item).status || "").trim().toLowerCase();
  }

  function itemKind(item) {
    const meta = itemMeta(item);
    const kind = String((item && item.backlog_kind) || meta.backlog_kind || "").trim().toLowerCase();
    if (kind) return kind;
    const uid = String((item && item.uid) || meta.uid || "").trim().toUpperCase();
    return uid.startsWith("IDEA-") ? "idea_note" : "pis";
  }

  function statusLabel(status) {
    const st = String(status || "").trim().toLowerCase();
    if (st === "draft") return "Draft";
    if (st === "ready" || st.startsWith("ready_")) return "Ready";
    return st || "Unknown";
  }

  function statusRank(status) {
    const st = String(status || "").trim().toLowerCase();
    if (st === "draft") return 0;
    if (st === "ready" || st.startsWith("ready_")) return 1;
    return 2;
  }

  function sortItems(items) {
    return items.slice().sort((a, b) => {
      const metaA = itemMeta(a);
      const metaB = itemMeta(b);
      const ra = statusRank(itemStatus(a));
      const rb = statusRank(itemStatus(b));
      if (ra !== rb) return ra - rb;
      const ua = Date.parse(String(metaA.updated_utc || metaA.created_utc || "")) || 0;
      const ub = Date.parse(String(metaB.updated_utc || metaB.created_utc || "")) || 0;
      if (ua !== ub) return ub - ua;
      return String(b && b.uid || "").localeCompare(String(a && a.uid || ""));
    });
  }

  function optionLabel(item) {
    const meta = itemMeta(item);
    const status = statusLabel(itemStatus(item));
    const uid = String(item && item.uid || "").trim() || "ITEM-?";
    const slug = deriveSlug(meta);
    const title = String(meta.title || "").trim();
    const kind = itemKind(item) === "idea_note" ? "Idea" : "PIS";
    const descriptor = [slug, title].filter((part, idx, arr) => part && arr.indexOf(part) === idx).join(" / ") || "(untitled)";
    return `${kind} · ${status} · ${uid} · ${descriptor}`;
  }

  function setLoadState(text, kind) {
    const badge = el("backlogLoadState");
    if (!badge) return;
    badge.textContent = String(text || "");
    badge.className = "badge " + (kind === "ok" ? "badge-ok" : kind === "bad" ? "badge-danger" : "badge-muted");
  }

  function setStatusBadge(status) {
    const badge = el("pisStatusBadge");
    if (!badge) return;
    const st = String(status || "").trim().toLowerCase();
    let cls = "status-other";
    if (st === "draft") cls = "status-draft";
    if (st === "ready" || st.startsWith("ready_")) cls = "status-ready";
    badge.className = "status-badge " + cls;
    badge.textContent = st || "status unknown";
  }

  function setText(id, value) {
    const node = el(id);
    if (node) node.textContent = String(value || "");
  }

  function currentItemsByUid() {
    const out = {};
    state.items.forEach((item) => {
      const uid = String(item && item.uid || "").trim();
      if (uid) out[uid] = item;
    });
    return out;
  }

  function selectedItems() {
    const map = currentItemsByUid();
    return state.selectedUids.map((uid) => map[uid]).filter(Boolean);
  }

  function renderSelect() {
    const select = el("pisSelect");
    if (!select) return;
    if (!state.items.length) {
      select.innerHTML = "<option value=\"\">No open backlog items</option>";
      return;
    }
    select.innerHTML = state.items.map((item) => {
      const uid = String(item.uid || "");
      const selected = state.selectedUids.indexOf(uid) >= 0 ? " selected" : "";
      return `<option value="${escapeHtml(uid)}"${selected}>${escapeHtml(optionLabel(item))}</option>`;
    }).join("");
  }

  function renderTopicFilter() {
    const select = el("topicFilterSelect");
    if (!select) return;
    const rows = Array.isArray(state.topicCatalog) ? state.topicCatalog : [];
    const options = ['<option value="">All topics</option>'];
    rows.forEach((row) => {
      const tag = String((row && row.tag) || "").trim();
      const count = Number((row && row.count) || 0);
      if (!tag) return;
      options.push(`<option value="${escapeHtml(tag)}">${escapeHtml(tag)} (${count})</option>`);
    });
    select.innerHTML = options.join("");
    select.value = String(state.topicFilter || "");
    setText("topicFilterHint", state.topicFilter ? ("Topic filter: " + state.topicFilter) : "Topic filter off.");
  }

  function renderGroupSummary() {
    const rows = Array.isArray(state.groups) ? state.groups : [];
    if (!rows.length) {
      setText("groupSummary", "-");
      return;
    }
    setText("groupSummary", rows.slice(0, 4).map((row) => {
      const key = String((row && row.key) || "unknown");
      const count = Number((row && row.count) || 0);
      return `${key}(${count})`;
    }).join(" · "));
  }

  function renderSelectionSummary() {
    const count = state.selectedUids.length;
    if (!count) {
      setText("selectionSummary", "No backlog item selected.");
      return;
    }
    const primary = state.selectedUids[0];
    setText("selectionSummary", count === 1 ? (`1 selected: ${primary}`) : (`${count} selected, primary detail: ${primary}`));
  }

  function renderCampaignSummary() {
    const campaign = state.latestCampaign || {};
    const queueState = state.queueState || {};
    const contractCheck = state.contractCheck || {};
    setText("campaignId", String(campaign.campaign_id || "-"));
    setText("campaignOutcome", String(campaign.launch_outcome || "-"));
    setText("queueSummary", [
      String(queueState.status || "idle"),
      queueState.current_uid ? ("current " + String(queueState.current_uid)) : "",
      Array.isArray(state.queuePreview) && state.queuePreview.length ? (`preview ${state.queuePreview.length}`) : "",
    ].filter(Boolean).join(" · ") || "-");
    setText("contractSummary", contractCheck.ok ? "ok" : "missing entities");
  }

  function renderExtendedSummary() {
    const artifacts = state.artifacts || {};
    const continuation = state.continuation || {};
    const runTriage = state.runTriage || {};
    const economics = state.economics || {};
    const archiveSnapshots = state.archiveSnapshots || {};
    const latestHandoff = artifacts.latest_handoff || {};
    const latestFailed = artifacts.latest_failed_action_log || {};
    setText("artifactSummary", [
      String(artifacts.status || "unknown"),
      continuation.status ? ("continuation " + String(continuation.status)) : "",
      continuation.authoritative_source ? ("source " + String(continuation.authoritative_source)) : "",
      latestHandoff.name ? ("handoff " + String(latestHandoff.name)) : "",
      latestFailed.name ? ("failed log " + String(latestFailed.name)) : "",
    ].filter(Boolean).join(" · ") || "-");
    setText("runTriageSummary", [
      String(runTriage.status || "unknown"),
      runTriage.queue && runTriage.queue.status ? ("queue " + String(runTriage.queue.status)) : "",
      runTriage.failure_case && runTriage.failure_case.failure_class ? String(runTriage.failure_case.failure_class) : "",
    ].filter(Boolean).join(" · ") || "-");
    setText("economicsSummary", [
      String(economics.status || "unknown"),
      economics.token_usage ? ("tokens " + String((economics.token_usage.total_tokens || 0))) : "",
      economics.waste_warning ? String(economics.waste_warning) : "",
    ].filter(Boolean).join(" · ") || "-");
    setText("archiveSummary", [
      String(archiveSnapshots.status || "unknown"),
      archiveSnapshots.latest_repo_snapshot && archiveSnapshots.latest_repo_snapshot.snapshot_id ? String(archiveSnapshots.latest_repo_snapshot.snapshot_id) : "",
      Array.isArray(archiveSnapshots.run_archives) ? (`run archives ${archiveSnapshots.run_archives.length}`) : "",
    ].filter(Boolean).join(" · ") || "-");
    setText(
      "artifactPreview",
      String(
        (continuation && continuation.summary) ||
        (latestHandoff && latestHandoff.preview_text) ||
        (latestFailed && latestFailed.preview_text) ||
        "No artifact preview available."
      )
    );
  }

  function renderShellSummary() {
    const shell = state.shell || {};
    const continuation = state.continuation || {};
    const executor = shell.executor_identity || {};
    const warnings = Array.isArray(shell.warnings) ? shell.warnings : [];
    const fallbacks = Array.isArray(shell.fallback_actions) ? shell.fallback_actions : [];
    setText("shellSummary", String(shell.status || "-"));
    setText("executorSummary", [
      executor.campaign_id ? ("campaign " + String(executor.campaign_id)) : "",
      executor.queue_run_id ? ("queue " + String(executor.queue_run_id)) : "",
      executor.workflow_id ? ("workflow " + String(executor.workflow_id)) : "",
    ].filter(Boolean).join(" · ") || "-");
    setText("fallbackActions", fallbacks.join(" · ") || "-");
    setText("shellWarnings", [
      warnings.join(" · "),
      continuation.summary ? String(continuation.summary) : "",
    ].filter(Boolean).join(" · ") || "-");
  }

  function renderChronicle() {
    const chronicle = state.chronicle || {};
    const entries = Array.isArray(chronicle.entries) ? chronicle.entries : [];
    if (!entries.length) {
      setText("chroniclePreview", "No chronicle entries available.");
      return;
    }
    setText("chroniclePreview", entries.slice(0, 12).map((row) => {
      return [
        String(row.utc || "-"),
        String(row.kind || "entry"),
        String(row.summary || ""),
        String(row.source || ""),
      ].filter(Boolean).join(" | ");
    }).join("\n"));
  }

  function renderEmpty() {
    setText("pisTitle", "No backlog item selected");
    setText("pisUid", "Select one or more items above.");
    setText("pisSlug", "-");
    setText("pisRelease", "-");
    setText("pisUpdated", "-");
    setText("pisCreated", "-");
    setText("pisTags", "-");
    setText("pisChangesets", "-");
    setText("ideaWhyNow", "-");
    setText("ideaResume", "-");
    setText("pisNextStatus", "Next: -");
    setStatusBadge("");
    setText("pisMarkdown", "No open backlog items found.");
    toggleIdeaActions(null, null);
  }

  function toggleIdeaActions(item, detail) {
    const card = el("ideaActionCard");
    if (!card) return;
    const isIdea = item && itemKind(item) === "idea_note";
    card.hidden = !isIdea;
    if (!isIdea) {
      return;
    }
    const meta = detail && detail.meta ? detail.meta : itemMeta(item);
    const blockers = Array.isArray(meta.resume_blockers) ? meta.resume_blockers.join(", ") : "";
    const whyNowInput = el("ideaWhyNowInput");
    const priorityInput = el("ideaPriorityInput");
    const resumeContextInput = el("ideaResumeContextInput");
    const blockersInput = el("ideaResumeBlockersInput");
    if (whyNowInput) whyNowInput.value = String(meta.why_now || "");
    if (priorityInput) priorityInput.value = String(meta.priority_signal || "");
    if (resumeContextInput) resumeContextInput.value = String(meta.resume_context || "");
    if (blockersInput) blockersInput.value = blockers;
  }

  async function fetchPrimaryDetail(primaryUid) {
    const url = primaryUid.startsWith("IDEA-")
      ? ("/api/idea_notes/" + encodeURIComponent(primaryUid))
      : ("/api/ledger/item/" + encodeURIComponent(primaryUid));
    return await apiJson(url + tokenQuery(true));
  }

  async function renderPrimaryDetail() {
    const primaryUid = state.selectedUids.length ? state.selectedUids[0] : "";
    if (!primaryUid) {
      renderEmpty();
      return;
    }
    const map = currentItemsByUid();
    const item = map[primaryUid];
    if (!item) {
      renderEmpty();
      return;
    }
    const detail = await fetchPrimaryDetail(primaryUid);
    const meta = itemMeta(item);
    const slug = deriveSlug(meta);
    const tags = Array.isArray(meta.tags) ? meta.tags.join(", ") : "";
    const topicTags = Array.isArray(meta.topic_tags) ? meta.topic_tags.join(", ") : "";
    const changesets = Array.isArray(meta.changesets) ? meta.changesets.join(", ") : "";
    const scheduled = Array.isArray(state.scheduledBindings[String(item.uid || "")]) ? state.scheduledBindings[String(item.uid || "")] : [];
    const detailMeta = detail && detail.meta && typeof detail.meta === "object" ? detail.meta : meta;
    const isIdea = itemKind(item) === "idea_note";
    setText("pisTitle", String(detailMeta.title || meta.title || item.uid || (isIdea ? "Untitled Idea" : "Untitled PIS")));
    setText("pisUid", String(item.uid || ""));
    setText("pisSlug", slug || "-");
    setText("pisRelease", String(detailMeta.release_state || meta.release_state || (isIdea ? "pre_intent" : "draft")));
    setText("pisUpdated", String(detailMeta.updated_utc || meta.updated_utc || "-"));
    setText("pisCreated", String(detailMeta.created_utc || meta.created_utc || "-"));
    setText("pisTags", topicTags || tags || (Array.isArray(detailMeta.tags) ? detailMeta.tags.join(", ") : "") || "-");
    setText("pisChangesets", changesets || "-");
    setText("ideaWhyNow", [
      String(detailMeta.why_now || "").trim(),
      String(detailMeta.priority_signal || "").trim() ? ("priority " + String(detailMeta.priority_signal || "").trim()) : "",
    ].filter(Boolean).join(" · ") || "-");
    setText("ideaResume", [
      String(detailMeta.resume_context || "").trim(),
      Array.isArray(detailMeta.resume_blockers) && detailMeta.resume_blockers.length ? ("blockers " + detailMeta.resume_blockers.join(", ")) : "",
    ].filter(Boolean).join(" · ") || "-");
    setText(
      "pisMarkdown",
      String((isIdea ? detail.idea_markdown : detail.pis_markdown) || "").trim() ||
      String(meta.idea_excerpt || "").trim() ||
      (isIdea ? "(empty idea note markdown)" : "(empty PIS markdown)")
    );
    setStatusBadge(itemStatus(item));
    toggleIdeaActions(item, detail);
    if (scheduled.length) {
      setText("groupSummary", scheduled.slice(0, 3).map((row) => String((row && row.job_id) || "")).filter(Boolean).join(", "));
    }
    if (!isIdea) {
      try {
        const workflow = await apiJson("/api/ledger/workflow/status?uid=" + encodeURIComponent(primaryUid) + tokenQuery(true));
        const next = workflow && workflow.next_status ? String(workflow.next_status) : "-";
        setText("pisNextStatus", "Next: " + next);
      } catch (_err) {
        setText("pisNextStatus", "Next: -");
      }
    } else {
      const next = detailMeta.status === "parked" ? "active" : detailMeta.status === "discarded" ? "-" : "parked/discarded";
      setText("pisNextStatus", "Next: " + next);
    }
  }

  async function loadCockpit() {
    setLoadState("loading", "muted");
    const params = new URLSearchParams();
    params.set("limit", "500");
    if (state.topicFilter) params.set("topic", state.topicFilter);
    params.set("group", state.groupMode || "status");
    state.selectedUids.forEach((uid) => params.append("selected_uid", uid));
    const payload = await apiJson("/api/mobile/cockpit?" + params.toString() + tokenQuery(true));
    const pis = payload && payload.pis && typeof payload.pis === "object" ? payload.pis : {};
    const items = sortItems(Array.isArray(pis.items) ? pis.items : []);
    const available = new Set(items.map((item) => String(item.uid || "")).filter(Boolean));
    state.items = items;
    state.topicCatalog = Array.isArray(pis.topic_catalog) ? pis.topic_catalog : [];
    state.groups = Array.isArray(pis.groups) ? pis.groups : [];
    state.scheduledBindings = pis.scheduled_bindings && typeof pis.scheduled_bindings === "object" ? pis.scheduled_bindings : {};
    state.latestCampaign = payload && payload.campaigns ? payload.campaigns.latest || null : null;
    state.queueState = payload && payload.execution_queue ? payload.execution_queue.state || null : null;
    state.queuePreview = payload && payload.execution_queue ? payload.execution_queue.queue_preview || [] : [];
    state.contractCheck = payload && payload.contracts ? payload.contracts.contract_check || null : null;
    state.artifacts = payload ? payload.artifacts || null : null;
    state.runTriage = payload ? payload.run_triage || null : null;
    state.economics = payload ? payload.economics || null : null;
    state.archiveSnapshots = payload ? payload.archive_snapshots || null : null;
    state.chronicle = payload ? payload.chronicle || null : null;
    state.shell = payload ? payload.shell || null : null;
    state.selectedUids = state.selectedUids.filter((uid) => available.has(uid));
    if (!state.selectedUids.length && items.length) {
      state.selectedUids = [String(items[0].uid || "")];
    }
    renderTopicFilter();
    renderGroupSummary();
    renderSelectionSummary();
    renderCampaignSummary();
    renderShellSummary();
    renderExtendedSummary();
    renderChronicle();
    renderSelect();
    setLoadState(items.length ? `${items.length} items` : "empty", "ok");
    await renderPrimaryDetail();
  }

  function readSelectionFromDom() {
    const select = el("pisSelect");
    if (!select) return [];
    return Array.from(select.selectedOptions || []).map((node) => String(node.value || "").trim()).filter(Boolean);
  }

  async function launchCampaign() {
    const selectedPisUids = selectedItems().filter((item) => itemKind(item) !== "idea_note").map((item) => String(item.uid || ""));
    if (!selectedPisUids.length) {
      setLoadState("select a PIS", "bad");
      return;
    }
    setLoadState("launching", "muted");
    const dependencyNode = el("dependencyModeSelect");
    const payload = await apiJson("/api/mobile/cockpit/campaigns/launch" + tokenQuery(false), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        selected_uids: selectedPisUids,
        dependency_mode: dependencyNode ? String(dependencyNode.value || "selected_order") : "selected_order",
        phase_label: "WF-01",
        commit_each_success: true,
      }),
    });
    state.latestCampaign = payload.campaign || null;
    renderCampaignSummary();
    await loadCockpit();
  }

  async function exportHandoff() {
    if (!state.selectedUids.length) {
      setLoadState("select a PIS", "bad");
      return;
    }
    const primaryUid = state.selectedUids[0];
    setLoadState("exporting handoff", "muted");
    await apiJson("/api/mobile/cockpit/artifacts/handoff" + tokenQuery(false), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ledger_uid: primaryUid,
        role: "builder",
        title: "Mobile cockpit handoff",
        selection: { mode: "last_n", n: 12 },
      }),
    });
    await loadCockpit();
  }

  async function createArchiveSnapshot() {
    setLoadState("archiving", "muted");
    await apiJson("/api/mobile/cockpit/archive/create" + tokenQuery(false), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ debug: false }),
    });
    await loadCockpit();
  }

  async function performIdeaAction(action) {
    const primaryUid = state.selectedUids.length ? state.selectedUids[0] : "";
    if (!primaryUid || !primaryUid.startsWith("IDEA-")) {
      setLoadState("select an idea", "bad");
      return;
    }
    setLoadState(action, "muted");
    const blockersRaw = String((el("ideaResumeBlockersInput") && el("ideaResumeBlockersInput").value) || "");
    const payload = {
      action: action,
      note: String((el("ideaActionNoteInput") && el("ideaActionNoteInput").value) || "").trim(),
      why_now: String((el("ideaWhyNowInput") && el("ideaWhyNowInput").value) || "").trim(),
      priority_signal: String((el("ideaPriorityInput") && el("ideaPriorityInput").value) || "").trim(),
      resume_context: String((el("ideaResumeContextInput") && el("ideaResumeContextInput").value) || "").trim(),
      resume_blockers: blockersRaw
        ? blockersRaw.split(",").map((part) => String(part || "").trim()).filter(Boolean)
        : [],
    };
    await apiJson("/api/idea_notes/" + encodeURIComponent(primaryUid) + "/action" + tokenQuery(false), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await loadCockpit();
    setLoadState("idea updated", "ok");
  }

  function wire() {
    const select = el("pisSelect");
    if (select) {
      select.addEventListener("change", async () => {
        state.selectedUids = readSelectionFromDom();
        renderSelectionSummary();
        try {
          await renderPrimaryDetail();
        } catch (err) {
          setLoadState("error", "bad");
          setText("pisMarkdown", "ERROR: " + String(err));
        }
      });
    }
    const refresh = el("btnBacklogRefresh");
    if (refresh) {
      refresh.addEventListener("click", async () => {
        try {
          await loadCockpit();
        } catch (err) {
          setLoadState("error", "bad");
          setText("pisMarkdown", "ERROR: " + String(err));
        }
      });
    }
    const topicFilterSelect = el("topicFilterSelect");
    if (topicFilterSelect) {
      topicFilterSelect.addEventListener("change", async () => {
        state.topicFilter = String(topicFilterSelect.value || "").trim();
        try {
          await loadCockpit();
        } catch (err) {
          setLoadState("error", "bad");
          setText("pisMarkdown", "ERROR: " + String(err));
        }
      });
    }
    const groupModeSelect = el("groupModeSelect");
    if (groupModeSelect) {
      groupModeSelect.value = state.groupMode;
      groupModeSelect.addEventListener("change", async () => {
        state.groupMode = String(groupModeSelect.value || "status").trim() || "status";
        try {
          await loadCockpit();
        } catch (err) {
          setLoadState("error", "bad");
          setText("pisMarkdown", "ERROR: " + String(err));
        }
      });
    }
    const launchButton = el("btnLaunchCampaign");
    if (launchButton) {
      launchButton.addEventListener("click", async () => {
        try {
          await launchCampaign();
        } catch (err) {
          setLoadState("launch failed", "bad");
          setText("pisMarkdown", "ERROR: " + String(err));
        }
      });
    }
    const handoffButton = el("btnExportHandoff");
    if (handoffButton) {
      handoffButton.addEventListener("click", async () => {
        try {
          await exportHandoff();
        } catch (err) {
          setLoadState("handoff failed", "bad");
          setText("artifactPreview", "ERROR: " + String(err));
        }
      });
    }
    const archiveButton = el("btnCreateArchive");
    if (archiveButton) {
      archiveButton.addEventListener("click", async () => {
        try {
          await createArchiveSnapshot();
        } catch (err) {
          setLoadState("archive failed", "bad");
          setText("artifactPreview", "ERROR: " + String(err));
        }
      });
    }
    const resumeButton = el("btnIdeaResume");
    if (resumeButton) {
      resumeButton.addEventListener("click", async () => {
        try {
          await performIdeaAction("resume");
        } catch (err) {
          setLoadState("resume failed", "bad");
          setText("pisMarkdown", "ERROR: " + String(err));
        }
      });
    }
    const parkButton = el("btnIdeaPark");
    if (parkButton) {
      parkButton.addEventListener("click", async () => {
        try {
          await performIdeaAction("park");
        } catch (err) {
          setLoadState("park failed", "bad");
          setText("pisMarkdown", "ERROR: " + String(err));
        }
      });
    }
    const discardButton = el("btnIdeaDiscard");
    if (discardButton) {
      discardButton.addEventListener("click", async () => {
        try {
          await performIdeaAction("discard");
        } catch (err) {
          setLoadState("discard failed", "bad");
          setText("pisMarkdown", "ERROR: " + String(err));
        }
      });
    }
  }

  async function boot() {
    wire();
    try {
      await loadCockpit();
    } catch (err) {
      setLoadState("error", "bad");
      setText("pisMarkdown", "ERROR: " + String(err));
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
