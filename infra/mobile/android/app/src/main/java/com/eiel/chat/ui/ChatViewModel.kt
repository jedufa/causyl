package com.eiel.chat.ui

import android.app.Application
import android.content.Context
import android.content.SharedPreferences
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.eiel.chat.BuildConfig
import com.eiel.chat.data.AdminApi
import com.eiel.chat.data.ChatRemoteCancelConflictException
import com.eiel.chat.data.ChatRemoteCancelFailedException
import com.eiel.chat.data.ChatRemoteCancelPendingException
import com.eiel.chat.data.ChatRepository
import com.eiel.chat.data.HttpMobileRuntimePolicySource
import com.eiel.chat.data.HttpChatApi
import com.eiel.chat.data.LedgerItemSummary
import com.eiel.chat.data.MobileRuntimePolicy
import com.eiel.chat.data.MobileRuntimePolicySource
import com.eiel.chat.data.ServerHealthProbe
import com.eiel.chat.data.ServerConfig
import com.eiel.chat.domain.AgentChatState
import com.eiel.chat.domain.AgentTab
import com.eiel.chat.domain.AGENT_PROMPT_QUEUE_MAX_DEPTH
import com.eiel.chat.domain.CapDialogState
import com.eiel.chat.domain.ChatAttachedDocument
import com.eiel.chat.domain.ChatMessage
import com.eiel.chat.domain.ChatReducer
import com.eiel.chat.domain.ChatUiState
import com.eiel.chat.domain.DIRECT_CHAT_STOP_RACE_LABEL
import com.eiel.chat.domain.MessageRole
import com.eiel.chat.domain.MessageStatus
import com.eiel.chat.domain.PisOption
import com.eiel.chat.domain.SessionHistoryMessage
import com.eiel.chat.domain.SessionSnapshot
import com.eiel.chat.domain.StreamEvent
import com.eiel.chat.domain.WorkflowEvent
import com.eiel.chat.domain.WorkflowStartRequest
import com.eiel.chat.domain.WorkflowStatus
import com.eiel.chat.domain.buildPromptWithAttachedDocuments
import com.eiel.chat.domain.parsePersistedMessageStatus
import com.eiel.chat.util.AppLogger
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext
import org.json.JSONObject

private val PENDING_WORKFLOW_MESSAGE_STATUSES = setOf(MessageStatus.SENDING, MessageStatus.STREAMING)
private const val RUNTIME_POLICY_REFRESH_INTERVAL_MS = 60_000L
private const val HEARTBEAT_INTERVAL_MS = 5_000L
private const val HEARTBEAT_FAILURE_THRESHOLD = 3
private const val HEARTBEAT_FAILURE_GRACE_MS = 60_000L
private const val RECENT_SERVER_CONTACT_GRACE_MS = 2 * 60_000L
private const val CHAT_UI_STATE_SCHEMA_VERSION = 2
private val CHAT_HEADER_TITLE_REGEX =
  Regex("^[A-Za-z0-9][A-Za-z0-9'-]*(?: [A-Za-z0-9][A-Za-z0-9'-]*){2,3}$")

class ChatViewModel(
  application: Application,
  private val runtimePolicySource: MobileRuntimePolicySource = HttpMobileRuntimePolicySource(application),
  private val repository: ChatRepository = ChatRepository(
    HttpChatApi(
      serverBaseUrlProvider = { ServerConfig.resolveBaseUrl(application) },
      chatTimeoutProvider = { runtimePolicySource.current().chatTurnTimeoutS },
      workflowTimeoutProvider = { runtimePolicySource.current().workflowTimeoutS },
      workingBaseUrlRecorder = { ServerConfig.saveBaseUrl(application, it) },
    ),
  ),
  private val sharedPrefs: SharedPreferences? = null,
  private val networkChecker: (() -> Boolean)? = null,
  private val ledgerItemsSource: LedgerItemsSource = AdminLedgerItemsSource(application),
  private val startBackgroundWork: Boolean = true,
  private val enableHeartbeatPolling: Boolean = true,
  private val sessionRecoveryPollCycleLimit: Int? = null,
  private val enableWorkflowStatusPolling: Boolean = true,
  private val currentTimeMsProvider: () -> Long = { System.currentTimeMillis() },
) : AndroidViewModel(application) {
  private data class PendingTurnRequest(
    val prompt: String,
    val requestId: String,
    val idempotencyKey: String,
    val turnId: String = "",
    val acceptedByServer: Boolean = false,
  )

  private val _uiState = MutableStateFlow(ChatUiState())
  val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

  private val streamJobs = mutableMapOf<AgentTab, Job>()
  private val sessionRecoveryJobs = mutableMapOf<AgentTab, Job>()
  private val preflightJobs = mutableMapOf<AgentTab, Job>()
  private val workflowRunJobs = mutableMapOf<AgentTab, Job>()
  private val workflowPollJobs = mutableMapOf<String, Job>()
  private val workflowResumeJobs = mutableMapOf<String, Job>()
  private val workflowRuntimeRecords = mutableMapOf<String, WorkflowRuntimeRecord>()
  private val restoredActiveWorkflowIds = mutableSetOf<String>()
  private val pendingWorkflowLaunches = mutableMapOf<AgentTab, PendingWorkflowLaunch>()
  private var heartbeatJob: Job? = null
  private val pendingPrompts = mutableMapOf<AgentTab, String>()
  private val queuedPromptTransportPrompts = mutableMapOf<AgentTab, MutableList<String>>()
  private val failedPromptTransportPrompts = mutableMapOf<AgentTab, String>()
  private val pendingPromptQueuedTabs = mutableSetOf<AgentTab>()
  private val inFlightPrompts = mutableMapOf<AgentTab, String>()
  private val pendingTurnRequests = mutableMapOf<AgentTab, PendingTurnRequest>()
  private val seenWorkflowEventKeys = mutableMapOf<AgentTab, MutableSet<String>>()
  private val emittedWorkflowReports = mutableSetOf<String>()
  private val restoredWorkflowResumeAttempts = mutableSetOf<String>()
  private val prefs by lazy {
    sharedPrefs ?: getApplication<Application>().getSharedPreferences("chat_ui_state", Context.MODE_PRIVATE)
  }
  @Volatile
  private var currentRuntimePolicy: MobileRuntimePolicy = runtimePolicySource.current()
  private var lastRuntimePolicyRefreshAtMs: Long = currentRuntimePolicy.fetchedAtMs
  private var consecutiveHeartbeatFailures: Int = 0
  @Volatile
  private var lastSuccessfulServerContactAtMs: Long = 0L
  private val legacyDraftKey = "draft"
  private val legacyPendingKey = "pending_prompt"
  private val legacyFailedKey = "last_failed_prompt"
  private val legacyHistoryKey = "history_cache_json"
  private val prefsSchemaVersionKey = "chat_ui_state_schema_version"

  init {
    maybeResetLegacyMobileState()
    restoreWorkflowPersistence()
    restorePersistedState()
    loadCachedHistory()
    seedWorkflowRuntimeRecordsFromCachedMessages()
    reconcileRestoredPendingWorkflowLaunches()
    rebuildWorkflowEventCaches()
    refreshPisOptions()
    if (startBackgroundWork) {
      if (enableWorkflowStatusPolling) {
        activeWorkflowIds().forEach(::ensureWorkflowPolling)
        restoredActiveWorkflowIds.forEach(::ensureRestoredWorkflowResume)
      }
      val hasAnyLocalDirectHistory = AgentTab.entries.any { tab ->
        agentState(tab).messages.any { message -> message.role != MessageRole.WORKFLOW }
      }
      AgentTab.entries.forEach { tab ->
        if (shouldStartStartupHydration(tab, hasAnyLocalDirectHistory = hasAnyLocalDirectHistory)) {
          viewModelScope.launch {
            hydrateSessionSnapshotWithRetry(tab, startupOnly = true)
          }
        }
      }
      if (enableHeartbeatPolling) {
        heartbeatJob = viewModelScope.launch {
          var lastStatus: Boolean? = null
          while (isActive) {
            val ok = runCatching { repository.isServerHealthy() }.getOrDefault(false)
            if (ok) {
              noteServerReachable()
              refreshRuntimePolicyIfStale()
              consecutiveHeartbeatFailures = 0
              _uiState.update { it.copy(serverHealthy = true, serverHealthyKnown = true) }
            } else {
              consecutiveHeartbeatFailures += 1
              if (shouldMarkServerUnhealthy(nowMs = currentTimeMsProvider())) {
                _uiState.update { it.copy(serverHealthy = false, serverHealthyKnown = true) }
              }
            }
            val effectiveStatus = _uiState.value.serverHealthy
            if (lastStatus == null || lastStatus != effectiveStatus) {
              AppLogger.i("heartbeat serverHealthy=$effectiveStatus probeOk=$ok failures=$consecutiveHeartbeatFailures")
              lastStatus = effectiveStatus
            }
            delay(HEARTBEAT_INTERVAL_MS)
          }
        }
      }
      viewModelScope.launch {
        refreshRuntimePolicyIfStale(force = true)
      }
    }
  }

  private fun currentChatIdleTimeoutS(): Int = currentRuntimePolicy.chatIdleTimeoutS

  private fun persistPendingTurnRequest(tab: AgentTab, request: PendingTurnRequest?) {
    if (request == null) {
      prefs.edit().remove(prefsPendingTurnRequestKey(tab)).apply()
      return
    }
    val payload = JSONObject()
      .put("prompt", request.prompt)
      .put("request_id", request.requestId)
      .put("idempotency_key", request.idempotencyKey)
      .put("turn_id", request.turnId)
      .put("accepted_by_server", request.acceptedByServer)
      .toString()
    prefs.edit().putString(prefsPendingTurnRequestKey(tab), payload).apply()
  }

  private fun restorePendingTurnRequest(tab: AgentTab): PendingTurnRequest? {
    val raw = prefs.getString(prefsPendingTurnRequestKey(tab), null)?.trim().orEmpty()
    if (raw.isBlank()) return null
    return runCatching {
      val obj = JSONObject(raw)
      val prompt = obj.optString("prompt").trim()
      val requestId = obj.optString("request_id").trim()
      val idempotencyKey = obj.optString("idempotency_key").trim().ifBlank { requestId }
      val turnId = obj.optString("turn_id").trim()
      val acceptedByServer = obj.optBoolean("accepted_by_server", false)
      if (prompt.isBlank() || requestId.isBlank() || idempotencyKey.isBlank()) {
        null
      } else {
        PendingTurnRequest(
          prompt = prompt,
          requestId = requestId,
          idempotencyKey = idempotencyKey,
          turnId = turnId,
          acceptedByServer = acceptedByServer,
        )
      }
    }.getOrNull()
  }

  private fun currentChatIdleTimeoutMs(): Long {
    return (currentChatIdleTimeoutS().toLong() * 1000L).coerceAtLeast(30_000L)
  }

  private fun noteServerReachable(nowMs: Long = currentTimeMsProvider()) {
    lastSuccessfulServerContactAtMs = nowMs
  }

  private fun hasRecentSuccessfulServerContact(nowMs: Long = currentTimeMsProvider()): Boolean {
    val last = lastSuccessfulServerContactAtMs
    return last > 0L && nowMs - last <= RECENT_SERVER_CONTACT_GRACE_MS
  }

  private fun shouldMarkServerUnhealthy(nowMs: Long = currentTimeMsProvider()): Boolean {
    if (consecutiveHeartbeatFailures < HEARTBEAT_FAILURE_THRESHOLD) {
      return false
    }
    val last = lastSuccessfulServerContactAtMs
    return last <= 0L || nowMs - last > HEARTBEAT_FAILURE_GRACE_MS
  }

  private fun suggestedSessionCap(stats: com.eiel.chat.domain.SessionStats): Int {
    return maxOf(
      stats.turns + 500,
      stats.maxTurns + 500,
      (stats.maxTurns * 3) / 2,
    )
  }

  private suspend fun refreshRuntimePolicyIfStale(force: Boolean = false) {
    val nowMs = currentTimeMsProvider()
    if (!force && nowMs - lastRuntimePolicyRefreshAtMs < RUNTIME_POLICY_REFRESH_INTERVAL_MS) {
      return
    }
    lastRuntimePolicyRefreshAtMs = nowMs
    val refreshed = runCatching { runtimePolicySource.refresh() }.getOrNull() ?: return
    currentRuntimePolicy = refreshed
    lastRuntimePolicyRefreshAtMs = maxOf(nowMs, refreshed.fetchedAtMs)
  }

  fun onTabSelected(tab: AgentTab) {
    _uiState.update { state ->
      val current = state.agentStates[tab] ?: AgentChatState(tab = tab)
      state.copy(
        selectedTab = tab,
        agentStates = state.agentStates + (tab to ChatReducer.applySelected(current)),
      )
    }
    prefs.edit()
      .putString(prefsSelectedTabKey(), if (tab.visibleInChatTabs) tab.key else AgentTab.default.key)
      .putBoolean(prefsUnreadKey(tab), false)
      .apply()
  }

  fun onDraftChanged(draft: String) {
    val tab = _uiState.value.selectedTab
    updateAgentState(tab) { ChatReducer.applyDraftChanged(it, draft) }
    prefs.edit().putString(prefsDraftKey(tab), draft).apply()
  }

  fun onSelectedPisChanged(pisId: String) {
    val tab = _uiState.value.selectedTab
    updateAgentState(tab) { ChatReducer.applySelectedPisChanged(it, pisId) }
    prefs.edit().putString(prefsPisKey(tab), pisId).apply()
  }

  fun onWorkflowCommentaryChanged(commentary: String) {
    val tab = _uiState.value.selectedTab
    updateAgentState(tab) { ChatReducer.applyWorkflowCommentaryChanged(it, commentary) }
    prefs.edit().putString(prefsWorkflowCommentaryKey(tab), commentary).apply()
  }

  fun addAttachedDocument(filename: String, mimeType: String, text: String, truncated: Boolean) {
    val safeText = text.trim()
    if (safeText.isBlank()) return
    val tab = _uiState.value.selectedTab
    updateAgentState(tab) { state ->
      state.copy(
        attachedDocuments = state.attachedDocuments + ChatAttachedDocument(
          filename = filename.ifBlank { "shared_document.txt" },
          mimeType = mimeType,
          text = safeText,
          truncated = truncated,
        ),
        lastError = null,
      )
    }
  }

  fun removeAttachedDocument(documentId: String) {
    val safeId = documentId.trim()
    if (safeId.isBlank()) return
    val tab = _uiState.value.selectedTab
    updateAgentState(tab) { state ->
      state.copy(attachedDocuments = state.attachedDocuments.filterNot { it.id == safeId })
    }
  }

  fun onSendClicked() {
    val tab = _uiState.value.selectedTab
    val before = agentState(tab)
    val prompt = before.draft.trim()
    if (prompt.isBlank()) return
    if (before.isStreaming || before.isSubmitting) {
      enqueuePrompt(tab, prompt)
      return
    }
    dispatchPromptWithPreflight(tab, prompt, fromQueue = false)
  }

  fun onQueueTailPopClicked() {
    val tab = _uiState.value.selectedTab
    val before = agentState(tab)
    if (before.queuedPrompts.isEmpty()) return
    val nextQueue = before.queuedPrompts.dropLast(1)
    queuedPromptTransportPrompts[tab] = queuedTransportPrompts(tab).dropLast(1).toMutableList()
    updateAgentState(tab) { it.copy(queuedPrompts = nextQueue) }
    persistQueuedPrompts(tab, nextQueue, queuedTransportPrompts(tab))
  }

  private fun enqueuePrompt(tab: AgentTab, prompt: String) {
    val before = agentState(tab)
    if (prompt.isBlank()) return
    if (before.queuedPrompts.size >= AGENT_PROMPT_QUEUE_MAX_DEPTH) {
      updateAgentState(tab) {
        it.copy(lastError = "Queue full (${AGENT_PROMPT_QUEUE_MAX_DEPTH}). Stop or long-press to remove the newest queued item.")
      }
      return
    }
    val nextTransportQueue = queuedTransportPrompts(tab).toMutableList().apply {
      add(buildPromptWithAttachedDocuments(prompt, before.attachedDocuments))
    }.takeLast(AGENT_PROMPT_QUEUE_MAX_DEPTH).toMutableList()
    queuedPromptTransportPrompts[tab] = nextTransportQueue
    val nextQueue = before.queuedPrompts + prompt
    updateAgentState(tab) {
      it.copy(
        draft = "",
        queuedPrompts = nextQueue,
        lastError = null,
      )
    }
    prefs.edit().remove(prefsDraftKey(tab)).apply()
    persistQueuedPrompts(tab, nextQueue, nextTransportQueue)
  }

  private fun dispatchPromptWithPreflight(tab: AgentTab, prompt: String, fromQueue: Boolean) {
    beginPreflight(tab, prompt)
    saveHistoryCacheFromUiState(tab)
    val recentlyConfirmedReachable = hasRecentSuccessfulServerContact() || _uiState.value.serverHealthy
    if (!isNetworkAvailable() && !recentlyConfirmedReachable) {
      updateAgentState(tab) {
        it.copy(
          lastError = "No network connection. Disable airplane mode or reconnect Wi-Fi.",
          lastFailedPrompt = prompt,
        )
      }
      restorePreflightPrompt(tab, prompt)
      prefs.edit().putString(prefsFailedKey(tab), prompt).apply()
      finishPreflight(tab)
      AppLogger.w("send blocked: no network tab=${tab.key}")
      return
    }
    if (!isNetworkAvailable() && recentlyConfirmedReachable) {
      AppLogger.w("send continuing despite local network check because recent server contact exists tab=${tab.key}")
    }
    preflightJobs.remove(tab)?.cancel()
    preflightJobs[tab] = viewModelScope.launch {
      val healthy = runCatching { repository.isServerHealthy() }.getOrElse { error ->
        if (error is CancellationException) throw error
        true
      }
      var serverHealthWarning: String? = null
      if (!healthy) {
        val probe = runCatching { repository.serverHealthProbe() }.getOrElse { error ->
          if (error is CancellationException) throw error
          ServerHealthProbe(
            isHealthy = false,
            baseUrl = ServerConfig.resolveBaseUrl(getApplication()),
            detail = error.message ?: error::class.java.simpleName,
          )
        }
        serverHealthWarning = formatServerUnavailableMessage(probe)
        AppLogger.w("send preflight health probe unhealthy; continuing send tab=${tab.key}: $serverHealthWarning")
      }
      try {
        refreshRuntimePolicyIfStale(force = true)
        val stats = repository.getSessionStats(agentState(tab).threadId)
        noteServerReachable()
        consecutiveHeartbeatFailures = 0
        _uiState.update { it.copy(serverHealthy = true, serverHealthyKnown = true) }
        if (stats.atCap) {
          pendingPrompts[tab] = prompt
          if (fromQueue) pendingPromptQueuedTabs += tab else pendingPromptQueuedTabs.remove(tab)
          updateAgentState(tab) {
            it.copy(
              capDialog = CapDialogState(
                stats = stats,
                suggestedNewCap = suggestedSessionCap(stats),
              ),
            )
          }
          restorePreflightPrompt(tab, prompt)
          finishPreflight(tab)
          return@launch
        }
      } catch (_: Throwable) {
        // Best-effort: if stats endpoint is unavailable, proceed with send.
      }
      if (fromQueue) {
        consumeQueuedPromptHead(tab, prompt)
      } else {
        pendingPromptQueuedTabs.remove(tab)
      }
      finishPreflight(tab)
      if (!serverHealthWarning.isNullOrBlank()) {
        updateAgentState(tab) { it.copy(lastError = serverHealthWarning) }
      }
      sendPrompt(tab, prompt)
    }.also { job ->
      job.invokeOnCompletion {
        if (preflightJobs[tab] === job) {
          preflightJobs.remove(tab)
        }
        if (!agentState(tab).isStreaming) {
          finishPreflight(tab)
        }
      }
    }
  }

  fun onLaunchWorkflowClicked() {
    val tab = _uiState.value.selectedTab
    if (tab != AgentTab.ORCHESTRATOR) return
    val before = agentState(tab)
    val pisId = before.selectedPisId.trim()
    val launchDirective = parseWorkflowLaunchDirective(before.draft)
    val commentary = launchDirective.instruction.takeIf { it.isNotBlank() }?.let { "SPECIAL INSTRUCTION: $it" }.orEmpty()
    if (pisId.isBlank()) {
      updateAgentState(tab) { it.copy(lastError = "Select a PIS before starting WF-01.") }
      return
    }
    if (before.activeWorkflowId != null || before.isLaunchingWorkflow) {
      updateAgentState(tab) { it.copy(lastError = "WF-01 is already running for this Orchestrator tab.") }
      return
    }
    if (!isNetworkAvailable()) {
      updateAgentState(tab) { it.copy(lastError = "No network connection. Disable airplane mode or reconnect Wi-Fi.") }
      return
    }

    val objective = buildWorkflowObjective(pisId = pisId)
    val requestSummary = buildWorkflowRequestSummary(
      pisId = pisId,
      commentary = commentary,
      maxIterations = launchDirective.maxIterations,
    )
    val nowMs = System.currentTimeMillis()
    val launchMessageEventKey = buildPendingWorkflowLaunchEventKey(tab = tab, pisId = pisId, createdAtMs = nowMs)
    updateAgentState(tab) {
      ChatReducer.applyWorkflowLaunchStarted(
        state = it,
        requestSummary = requestSummary,
        workflowLabel = "WF-01",
        messageEventKey = launchMessageEventKey,
        nowMs = nowMs,
      )
    }
    saveHistoryCacheFromUiState(tab)
    persistPendingWorkflowLaunch(
      PendingWorkflowLaunch(
        tab = tab,
        pisId = pisId,
        commentary = commentary,
        maxIterations = launchDirective.maxIterations,
        requestSummary = requestSummary,
        createdAtMs = nowMs,
        launchMessageEventKey = launchMessageEventKey,
      ),
    )
    prefs.edit()
      .putString(prefsWorkflowCommentaryKey(tab), commentary)
      .putString(prefsDraftKey(tab), "")
      .apply()

    workflowRunJobs.remove(tab)?.cancel()
    viewModelScope.launch {
      try {
        val started = repository.startWorkflow(
          WorkflowStartRequest(
            bindingType = "ledger_uid",
            bindingId = pisId,
            phaseLabel = "WF-01",
            objective = objective,
            maxIterations = launchDirective.maxIterations,
            executionDirective = commentary,
          ),
        )
        val nowMs = System.currentTimeMillis()
        updateAgentState(tab) {
          ChatReducer.applyWorkflowStarted(
            state = it,
            workflowId = started.workflowId,
            requestSummary = buildWorkflowRequestSummary(
              pisId = pisId,
              commentary = commentary,
              maxIterations = started.maxIterations.takeIf { value -> value > 0 } ?: launchDirective.maxIterations,
              objective = started.objective.takeIf { value -> value.isNotBlank() },
            ),
            pendingMessageEventKey = launchMessageEventKey,
            nowMs = nowMs,
          )
        }
        saveHistoryCacheFromUiState(tab)
        clearPendingWorkflowLaunch(tab)
        upsertWorkflowRuntimeRecord(
          workflowId = started.workflowId,
          participantTabs = setOf(AgentTab.ORCHESTRATOR),
          status = "running",
          iteration = 0,
        )
        prefs.edit()
          .remove(prefsWorkflowCommentaryKey(tab))
          .putString(prefsActiveWorkflowKey(tab), started.workflowId)
          .apply()
        if (enableWorkflowStatusPolling) {
          ensureWorkflowPolling(started.workflowId)
        }
        workflowRunJobs[tab] = viewModelScope.launch {
          try {
            repository.runWorkflow(started.workflowId, maxSteps = 40)
          } catch (t: Throwable) {
            AppLogger.e("workflow run failure tab=${tab.key}", t)
          } finally {
            runCatching { processWorkflowStatus(repository.getWorkflowStatus(started.workflowId, eventsLimit = 120)) }
          }
        }
      } catch (t: Throwable) {
        AppLogger.e("workflow start failure tab=${tab.key}", t)
        updateAgentState(tab) {
          ChatReducer.applyWorkflowLaunchFailed(
            state = it,
            reason = t.message ?: "Workflow launch failed.",
            pendingMessageEventKey = launchMessageEventKey,
            nowMs = System.currentTimeMillis(),
          )
        }
        saveHistoryCacheFromUiState(tab)
        clearPendingWorkflowLaunch(tab)
      }
    }
  }

  fun onCapDialogDismissed() {
    val tab = _uiState.value.selectedTab
    pendingPrompts.remove(tab)
    pendingPromptQueuedTabs.remove(tab)
    updateAgentState(tab) { it.copy(capDialog = null) }
  }

  fun onCapContinueClicked() {
    val tab = _uiState.value.selectedTab
    val prompt = pendingPrompts[tab] ?: agentState(tab).draft.trim()
    if (prompt.isBlank()) {
      updateAgentState(tab) { it.copy(capDialog = null) }
      return
    }
    if (pendingPromptQueuedTabs.remove(tab)) {
      consumeQueuedPromptHead(tab, prompt)
    }
    finishPreflight(tab)
    updateAgentState(tab) { it.copy(capDialog = null) }
    sendPrompt(tab, prompt)
  }

  fun onRetryLastFailedClicked() {
    val tab = _uiState.value.selectedTab
    val current = agentState(tab)
    val prompt = current.lastFailedPrompt?.trim().orEmpty()
    if (prompt.isBlank()) return
    val queuedHead = current.queuedPrompts.firstOrNull()?.trim().orEmpty()
    if (!current.isStreaming && !current.isSubmitting && queuedHead == prompt) {
      updateAgentState(tab) { it.copy(lastFailedPrompt = null, lastError = null) }
      prefs.edit().remove(prefsFailedKey(tab)).apply()
      dispatchPromptWithPreflight(tab, prompt, fromQueue = true)
      return
    }
    updateAgentState(tab) { it.copy(draft = prompt, lastFailedPrompt = null, lastError = null) }
    prefs.edit().putString(prefsDraftKey(tab), prompt).remove(prefsFailedKey(tab)).apply()
    val effectivePrompt = failedPromptTransportPrompts.remove(tab)
    sendPrompt(tab, prompt, overrideEffectivePrompt = effectivePrompt)
  }

  fun onCapIncreaseClicked() {
    val tab = _uiState.value.selectedTab
    val state = agentState(tab)
    val dialog = state.capDialog ?: return
    val prompt = pendingPrompts[tab] ?: state.draft.trim()
    viewModelScope.launch {
      val updatedStats = try {
        repository.setSessionCap(state.threadId, dialog.suggestedNewCap)
      } catch (error: Throwable) {
        updateAgentState(tab) {
          it.copy(lastError = error.message ?: "Failed to increase memory cap.")
        }
        finishPreflight(tab)
        return@launch
      }
      if (updatedStats.atCap) {
        updateAgentState(tab) {
          it.copy(
            capDialog = CapDialogState(
              stats = updatedStats,
              suggestedNewCap = suggestedSessionCap(updatedStats),
            ),
            lastError = "Memory cap is still below the current turn count. Increase again or continue with cycling.",
          )
        }
        finishPreflight(tab)
        return@launch
      }
      if (pendingPromptQueuedTabs.remove(tab)) {
        consumeQueuedPromptHead(tab, prompt)
      }
      finishPreflight(tab)
      updateAgentState(tab) { it.copy(capDialog = null, lastError = null) }
      if (prompt.isNotBlank()) sendPrompt(tab, prompt)
    }
  }

  fun onStopClicked() {
    val tab = _uiState.value.selectedTab
    val stateBeforeStop = agentState(tab)
    val threadId = stateBeforeStop.threadId
    val preflightPrompt = currentPendingPreflightPrompt(tab)
    val hadActiveAssistantTurn = streamJobs[tab]?.isActive == true
    preflightJobs.remove(tab)?.cancel()
    streamJobs.remove(tab)?.cancel()
    sessionRecoveryJobs.remove(tab)?.cancel()
    updateAgentState(tab) { ChatReducer.applyStreamCancelled(it, nowMs = System.currentTimeMillis()) }
    saveHistoryCacheFromUiState(tab)
    inFlightPrompts.remove(tab)
    pendingPromptQueuedTabs.remove(tab)
    restorePreflightPrompt(tab, preflightPrompt)
    finishPreflight(tab)
    if (preflightPrompt.isNotBlank()) {
      updateAgentState(tab) { state ->
        if (state.draft == preflightPrompt) state else state.copy(draft = preflightPrompt)
      }
    }
    updateAgentState(tab) {
      it.copy(
        isSubmitting = false,
        isStreaming = false,
        streamingProgress = null,
        streamPhase = null,
        streamPhaseStartedAtMs = null,
      )
    }
    prefs.edit().remove(prefsPendingKey(tab)).apply()
    viewModelScope.launch {
      var followupError: String? = null
      try {
        repository.cancel(threadId)
      } catch (t: Throwable) {
        if (hadActiveAssistantTurn) {
          followupError = when (t) {
            is ChatRemoteCancelPendingException -> {
              t.message?.trim().orEmpty().ifBlank {
                "Local rendering stopped, but the server is still resolving the cancel request. The reply may still complete; refresh to confirm the final server state."
              }
            }
            is ChatRemoteCancelConflictException -> {
              t.message?.trim().orEmpty().ifBlank {
                "Local rendering stopped, but the server had no active turn to cancel. The reply may have already completed."
              }
            }
            is ChatRemoteCancelFailedException -> {
              t.message?.trim().orEmpty().ifBlank {
                "Local rendering stopped, but server cancellation may not have succeeded. Refresh to confirm the final server state."
              }
            }
            else -> {
              t.message?.trim().orEmpty().ifBlank {
                "Local rendering stopped, but server cancellation may not have succeeded. Refresh to confirm the final server state."
              }
            }
          }
        } else {
          AppLogger.w("remote stop ignored without active assistant turn tab=${tab.key}: ${t.message}")
        }
      }
      if (followupError != null) {
        updateAgentState(tab) {
          applyRemoteCancelUncertain(
            it,
            reason = followupError!!,
            nowMs = System.currentTimeMillis(),
          )
        }
        saveHistoryCacheFromUiState(tab)
      }
      if (hadActiveAssistantTurn) {
        beginSessionSnapshotRecovery(tab)
      }
    }
  }

  private fun sendPrompt(tab: AgentTab, prompt: String, overrideEffectivePrompt: String? = null) {
    if (prompt.isBlank()) return
    val queuedHead = agentState(tab).queuedPrompts.firstOrNull()?.trim().orEmpty()
    val queuedEffectivePrompt = queuedTransportPrompts(tab).firstOrNull()?.trim().orEmpty()
    val effectivePrompt = overrideEffectivePrompt?.trim()?.takeIf { it.isNotBlank() }
      ?: if (queuedHead.isNotBlank() && queuedHead == prompt.trim() && queuedEffectivePrompt.isNotBlank()) {
        queuedEffectivePrompt
      } else {
        buildPromptWithAttachedDocuments(prompt, agentState(tab).attachedDocuments)
      }
    sessionRecoveryJobs.remove(tab)?.cancel()
    pendingPrompts.remove(tab)
    pendingPromptQueuedTabs.remove(tab)
    inFlightPrompts[tab] = effectivePrompt
    prefs.edit().putString(prefsPendingKey(tab), prompt).remove(prefsFailedKey(tab)).apply()
    updateAgentState(tab) { ChatReducer.applySendStarted(it, prompt = prompt, nowMs = System.currentTimeMillis()) }
    prefs.edit().remove(prefsDraftKey(tab)).putBoolean(prefsUnreadKey(tab), false).apply()
    streamJobs.remove(tab)?.cancel()
    streamJobs[tab] = viewModelScope.launch streamLoop@{
      val existingRequest = pendingTurnRequests[tab]
      val pendingRequest = if (existingRequest != null && existingRequest.prompt == effectivePrompt) {
        existingRequest
      } else {
        val requestId = "mobile_send_${java.util.UUID.randomUUID()}"
        PendingTurnRequest(
          prompt = effectivePrompt,
          requestId = requestId,
          idempotencyKey = requestId,
        )
      }
      pendingTurnRequests[tab] = pendingRequest
      persistPendingTurnRequest(tab, pendingRequest)
      var lastStreamEventAtMs = currentTimeMsProvider()
      var recoveringExistingTurn = false
      val idleWatchdog = launch {
        while (true) {
          delay(5000L)
          val idleForMs = currentTimeMsProvider() - lastStreamEventAtMs
          if (idleForMs > currentChatIdleTimeoutMs()) {
            AppLogger.w("stream idle timeout after ${idleForMs}ms tab=${tab.key}")
            streamJobs[tab]?.cancel(CancellationException("stream idle timeout"))
            return@launch
          }
        }
      }
      try {
        var sawDone = false
        repository.stream(
          threadId = agentState(tab).threadId,
          prompt = effectivePrompt,
          agentKey = tab.key,
          requestId = pendingRequest.requestId,
          idempotencyKey = pendingRequest.idempotencyKey,
          resumeTurnId = pendingRequest.turnId,
        ).collect { ev ->
          lastStreamEventAtMs = currentTimeMsProvider()
          when (ev) {
            is StreamEvent.Start -> {
              val updatedRequest = pendingTurnRequests[tab]?.copy(
                turnId = ev.turnId,
                acceptedByServer = ev.turnId.isNotBlank(),
              ) ?: pendingRequest.copy(turnId = ev.turnId, acceptedByServer = ev.turnId.isNotBlank())
              pendingTurnRequests[tab] = updatedRequest
              persistPendingTurnRequest(tab, updatedRequest)
            }
            is StreamEvent.Token -> {
              updateAgentState(tab) {
                ChatReducer.applyStreamToken(it, token = ev.text, nowMs = System.currentTimeMillis())
              }
            }
            is StreamEvent.Progress -> {
              val updatedRequest = pendingTurnRequests[tab]?.copy(acceptedByServer = true) ?: pendingRequest.copy(acceptedByServer = true)
              pendingTurnRequests[tab] = updatedRequest
              persistPendingTurnRequest(tab, updatedRequest)
              updateAgentState(tab) {
                ChatReducer.applyStreamProgress(
                  it,
                  text = ev.text,
                  phase = ev.phase,
                  nowMs = System.currentTimeMillis(),
                )
              }
            }
            is StreamEvent.Title -> applyConversationTitle(tab, ev.text)
            is StreamEvent.PlaneRouted -> {
              if (ev.enabled) {
                updateAgentState(tab) {
                  ChatReducer.applyPlaneRouted(it, nowMs = System.currentTimeMillis())
                }
              }
            }
            is StreamEvent.Keepalive -> Unit
            is StreamEvent.Done -> {
              sawDone = true
              updateAgentState(tab) {
                ChatReducer.applyStreamDone(it, nowMs = System.currentTimeMillis())
              }
              if (_uiState.value.selectedTab != tab) {
                markUnread(tab)
              }
              saveHistoryCacheFromUiState(tab)
            }
            is StreamEvent.Error -> {
              if (isActiveTurnAlreadyRunningError(ev.reason)) {
                recoveringExistingTurn = true
                updateAgentState(tab) {
                  ChatReducer.applyRecoveringExistingTurn(it, nowMs = System.currentTimeMillis())
                }
                saveHistoryCacheFromUiState(tab)
                beginSessionSnapshotRecovery(tab)
              } else {
                failedPromptTransportPrompts[tab] = effectivePrompt
                updateAgentState(tab) {
                  ChatReducer.applyStreamError(
                    it,
                    reason = ev.reason,
                    nowMs = System.currentTimeMillis(),
                    acceptedByServer = pendingTurnRequests[tab]?.acceptedByServer == true,
                  )
                    .copy(lastFailedPrompt = prompt)
                }
                prefs.edit().putString(prefsFailedKey(tab), prompt).apply()
                if (_uiState.value.selectedTab != tab) {
                  markUnread(tab)
                }
                saveHistoryCacheFromUiState(tab)
              }
            }
          }
        }
        val hasFailed = agentState(tab).lastError != null
        if (!hasFailed && !recoveringExistingTurn) {
          if (sawDone) {
            runCatching { syncCanonicalSessionAfterTerminalStream(tab) }
          }
          pendingTurnRequests.remove(tab)
          persistPendingTurnRequest(tab, null)
          inFlightPrompts.remove(tab)
          failedPromptTransportPrompts.remove(tab)
          prefs.edit().remove(prefsPendingKey(tab)).remove(prefsFailedKey(tab)).apply()
          updateAgentState(tab) { it.copy(lastFailedPrompt = null) }
          maybeDrainQueuedPrompt(tab)
        }
      } catch (t: Throwable) {
        if (t is CancellationException) {
          if (!((t.message ?: "").contains("stream idle timeout", ignoreCase = true))) {
            return@streamLoop
          }
        }
        AppLogger.e("send/stream failure tab=${tab.key}", t)
        failedPromptTransportPrompts[tab] = effectivePrompt
        updateAgentState(tab) {
          ChatReducer.applyStreamError(
            it,
            reason = if (t is CancellationException) {
              "No response from server for ${currentChatIdleTimeoutS()}s."
            } else {
              t.message ?: "send/stream failure"
            },
            nowMs = System.currentTimeMillis(),
            acceptedByServer = pendingTurnRequests[tab]?.acceptedByServer == true,
          )
            .copy(lastFailedPrompt = prompt)
        }
        prefs.edit().putString(prefsFailedKey(tab), prompt).apply()
        if (_uiState.value.selectedTab != tab) {
          markUnread(tab)
        }
        saveHistoryCacheFromUiState(tab)
      } finally {
        idleWatchdog.cancel()
        if (recoveringExistingTurn) {
          return@streamLoop
        }
        val err = agentState(tab).lastError
        val failedTransportPrompt = inFlightPrompts[tab]?.takeIf { it.isNotBlank() }
        if (err != null && failedTransportPrompt != null) {
          failedPromptTransportPrompts[tab] = failedTransportPrompt
          updateAgentState(tab) { state ->
            val visiblePrompt = state.lastFailedPrompt?.takeIf { it.isNotBlank() } ?: prompt
            state.copy(lastFailedPrompt = visiblePrompt)
          }
          prefs.edit().putString(prefsFailedKey(tab), prompt).remove(prefsPendingKey(tab)).apply()
          saveHistoryCacheFromUiState(tab)
          beginSessionSnapshotRecovery(tab)
        }
      }
    }
  }

  private fun isActiveTurnAlreadyRunningError(reason: String?): Boolean {
    val normalized = reason?.trim()?.lowercase().orEmpty()
    return normalized.contains("another turn is already running for this tab")
  }

  private fun applyRemoteCancelUncertain(
    state: AgentChatState,
    reason: String,
    nowMs: Long,
  ): AgentChatState {
    val idx = state.messages.indexOfLast {
      it.role == MessageRole.ASSISTANT && it.status == MessageStatus.CANCELLED
    }
    if (idx < 0) {
      return state.copy(
        isStreaming = false,
        isSubmitting = false,
        streamingProgress = null,
        streamPhase = null,
        streamPhaseStartedAtMs = null,
        lastError = reason,
      )
    }
    val current = state.messages[idx]
    val updated = current.copy(
      status = if (current.content.isBlank()) MessageStatus.FAILED else MessageStatus.INTERRUPTED,
      updatedAtMs = nowMs,
    )
    val next = state.messages.toMutableList()
    next[idx] = updated
    return state.copy(
      messages = next,
      isStreaming = false,
      isSubmitting = false,
      streamingProgress = null,
      streamPhase = null,
      streamPhaseStartedAtMs = null,
      lastError = reason,
    )
  }

  private suspend fun syncCanonicalSessionAfterTerminalStream(tab: AgentTab) {
    // Terminal success does one unconditional canonical refresh so follow-up
    // artifacts can land even when recovery-only hydration would stay idle.
    hydrateSessionSnapshotOnce(tab)
  }

  private fun beginSessionSnapshotRecovery(tab: AgentTab) {
    val current = sessionRecoveryJobs[tab]
    if (current?.isActive == true) {
      return
    }
    sessionRecoveryJobs.remove(tab)?.cancel()
    sessionRecoveryJobs[tab] = viewModelScope.launch {
      hydrateSessionSnapshotWithRetry(tab)
    }.also { job ->
      job.invokeOnCompletion {
        if (sessionRecoveryJobs[tab] === job) {
          sessionRecoveryJobs.remove(tab)
        }
      }
    }
  }

  private suspend fun hydrateSessionSnapshotWithRetry(tab: AgentTab, startupOnly: Boolean = false) {
    repeat(30) { idx ->
      if (startupOnly && idx > 0 && !shouldContinueStartupHydration(tab)) {
        return
      }
      if (!shouldStartBackgroundHydration(tab)) {
        return
      }
      val restored = try {
        hydrateSessionSnapshotOnce(tab, startupOnly = startupOnly)
      } catch (t: Throwable) {
        if (isMalformedSessionSnapshotPayloadError(t)) {
          AppLogger.e("session snapshot malformed for recovery tab=${tab.key}", t)
          applySessionSnapshotRecoveryError(tab)
          return
        }
        throw t
      }
      if (restored) return
      delay(if (idx < 10) 1000L else 2000L)
    }
  }

  private suspend fun hydrateSessionSnapshotOnce(tab: AgentTab, startupOnly: Boolean = false): Boolean {
    val threadId = agentState(tab).threadId
    var snapshot = SessionSnapshot(history = emptyList())
    val backoffMs = listOf(0L, 400L, 900L, 1600L)
    for (waitMs in backoffMs) {
      if (startupOnly && waitMs > 0L && agentState(tab).messages.any { it.role != MessageRole.WORKFLOW }) {
        return true
      }
      if (waitMs > 0) {
        delay(waitMs)
        if (startupOnly && !shouldContinueStartupHydration(tab)) {
          return true
        }
      }
      snapshot = try {
        repository.getSessionSnapshot(threadId)
      } catch (t: Throwable) {
        if (isMalformedSessionSnapshotPayloadError(t)) {
          throw t
        }
        AppLogger.w("session snapshot fetch attempt failed tab=${tab.key}")
        SessionSnapshot(history = emptyList())
      }
      if (snapshot.history.isNotEmpty() || isCanonicalStreamActive(snapshot.streamState?.status)) break
    }
    val history = snapshot.history
    val shouldRecoverStream = isCanonicalStreamActive(snapshot.streamState?.status)
    if (shouldRecoverStream) {
      val existing = pendingTurnRequests[tab]
      if (existing != null) {
        val updatedRequest = existing.copy(
          turnId = snapshot.streamState?.turnId.orEmpty().ifBlank { existing.turnId },
          acceptedByServer = true,
        )
        pendingTurnRequests[tab] = updatedRequest
        persistPendingTurnRequest(tab, updatedRequest)
      }
    }
    if (history.isEmpty() && !shouldRecoverStream) return false
    val existingState = agentState(tab)
    val wasStreaming = existingState.isStreaming
    val existingMessages = existingState.messages
    var nowMs = System.currentTimeMillis() - (history.size * 5L)
    val loaded = history.map { row ->
      nowMs += 5L
      ChatMessage(
        threadId = threadId,
        role = row.role,
        content = row.content,
        status = row.status,
        createdAtMs = nowMs,
        updatedAtMs = nowMs,
        label = row.label,
        eventKey = row.eventKey,
        workflowId = row.workflowId,
        planeRouted = row.planeRouted,
      )
    }
    val preserveLocalDirectHistory = shouldPreferLocalDirectHistory(
      existing = existingMessages,
      loaded = loaded,
      shouldRecoverStream = shouldRecoverStream,
    )
    val merged = if (preserveLocalDirectHistory) {
      AppLogger.i("preserving local direct history during snapshot hydration tab=${tab.key}")
      mergeWorkflowSnapshotIntoExistingHistory(
        existing = existingMessages,
        loaded = loaded,
      )
    } else {
      mergeHydratedHistoryMessages(
        loaded = loaded,
        existing = existingMessages,
        shouldRecoverStream = shouldRecoverStream,
      )
    }
    val recovered = overlayRecoveredStreamState(
      threadId = threadId,
      baseMessages = merged,
      snapshot = snapshot,
      nowMs = System.currentTimeMillis(),
    )
    val resolvedInterruptedCompletion = !shouldRecoverStream &&
      canonicalSnapshotConfirmsInterruptedCompletion(existing = existingMessages, recovered = recovered)
    val resolvedFailedCompletion = !shouldRecoverStream &&
      canonicalSnapshotConfirmsFailedCompletion(existing = existingMessages, recovered = recovered)
    val resolvedStopRaceCompletion = !shouldRecoverStream &&
      canonicalSnapshotConfirmsStopRaceCompletion(existing = existingMessages, recovered = recovered)
    val shouldClearFailureState = shouldRecoverStream || resolvedInterruptedCompletion || resolvedFailedCompletion
    val persistedFailedPrompt = prefs.getString(prefsFailedKey(tab), null)?.trim()?.takeIf { it.isNotBlank() }
    updateAgentState(tab) {
      it.copy(
        messages = recovered,
        lastError = if (shouldClearFailureState) null else it.lastError,
        lastFailedPrompt = if (shouldClearFailureState) null else (it.lastFailedPrompt ?: persistedFailedPrompt),
        isStreaming = shouldRecoverStream,
        isSubmitting = shouldRecoverStream,
        streamPhase = null,
        streamPhaseStartedAtMs = null,
        streamingProgress = if (shouldRecoverStream && snapshot.streamState?.content.isNullOrBlank()) {
          "Resuming reply..."
        } else {
          null
        },
      )
    }
    seedWorkflowRuntimeRecordFromMessages(tab = tab, messages = recovered)
    AppLogger.i("session snapshot hydrated tab=${tab.key} messages=${recovered.size} recovering=$shouldRecoverStream")
    saveHistoryCacheFromUiState(tab)
    if (shouldRecoverStream) {
      ensureSessionRecoveryPolling(tab)
    } else {
      sessionRecoveryJobs.remove(tab)?.cancel()
      val shouldFinalizeRecoveredTurn = resolvedInterruptedCompletion ||
        resolvedFailedCompletion ||
        resolvedStopRaceCompletion ||
        (wasStreaming && agentState(tab).lastError == null)
      if (shouldFinalizeRecoveredTurn) {
        inFlightPrompts.remove(tab)
        pendingTurnRequests.remove(tab)
        persistPendingTurnRequest(tab, null)
        prefs.edit().remove(prefsPendingKey(tab)).remove(prefsFailedKey(tab)).apply()
        if (!startupOnly && _uiState.value.selectedTab != tab) {
          markUnread(tab)
        }
        maybeDrainQueuedPrompt(tab)
      }
    }
    return preserveLocalDirectHistory ||
      shouldRecoverStream ||
      resolvedInterruptedCompletion ||
      resolvedFailedCompletion ||
      resolvedStopRaceCompletion ||
      (existingMessages.isEmpty() && recovered.isNotEmpty())
  }

  private fun overlayRecoveredStreamState(
    threadId: String,
    baseMessages: List<ChatMessage>,
    snapshot: SessionSnapshot,
    nowMs: Long,
  ): List<ChatMessage> {
    val hasActiveStream = isCanonicalStreamActive(snapshot.streamState?.status)
    if (!hasActiveStream) {
      return baseMessages
    }
    val partial = snapshot.streamState?.content.orEmpty()
    val pendingAssistant = ChatMessage(
      threadId = threadId,
      role = MessageRole.ASSISTANT,
      content = partial,
      status = MessageStatus.STREAMING,
      createdAtMs = nowMs,
      updatedAtMs = nowMs,
    )
    val last = baseMessages.lastOrNull()
    return if (
      last?.role == MessageRole.ASSISTANT &&
      (last.status == MessageStatus.STREAMING || last.status == MessageStatus.INTERRUPTED)
    ) {
      baseMessages.dropLast(1) + pendingAssistant.copy(
        content = preferredRecoveredStreamContent(
          localContent = last.content,
          canonicalContent = partial,
        ),
        createdAtMs = last.createdAtMs,
      )
    } else {
      baseMessages + pendingAssistant
    }
  }

  private fun ensureSessionRecoveryPolling(tab: AgentTab) {
    val current = sessionRecoveryJobs[tab]
    if (current?.isActive == true) {
      return
    }
    sessionRecoveryJobs.remove(tab)?.cancel()
    sessionRecoveryJobs[tab] = viewModelScope.launch {
      var pollCycles = 0
      while (isActive) {
        sessionRecoveryPollCycleLimit?.let { limit ->
          if (pollCycles >= limit) {
            AppLogger.i("session recovery polling capped tab=${tab.key} limit=$limit")
            return@launch
          }
        }
        delay(1200L)
        pollCycles += 1
        val stillRecovering = runCatching { hydrateSessionSnapshotOnce(tab) }.getOrDefault(false)
        if (!stillRecovering) {
          return@launch
        }
      }
    }
  }

  private fun mergeHydratedHistoryMessages(
    loaded: List<ChatMessage>,
    existing: List<ChatMessage>,
    shouldRecoverStream: Boolean,
  ): List<ChatMessage> {
    val loadedNonWorkflow = loaded.filter { it.role != MessageRole.WORKFLOW }
    val retainedLocalNonWorkflow = buildList {
      if (shouldRecoverStream && loadedNonWorkflow.isEmpty()) {
        addAll(existing.filter { it.role != MessageRole.WORKFLOW })
      }
      addAll(unmatchedLocalDirectMessages(existing = existing, loaded = loaded))
    }.distinctBy { it.localMergeIdentityKey() }
    val loadedWorkflow = loaded.filter { it.role == MessageRole.WORKFLOW }
    if (loadedWorkflow.isEmpty()) {
      return loaded + retainedLocalNonWorkflow + existing.filter { it.role == MessageRole.WORKFLOW }
    }
    val retainedLocalWorkflow = existing.filter { message ->
      message.role == MessageRole.WORKFLOW &&
        message.status in PENDING_WORKFLOW_MESSAGE_STATUSES &&
        loadedWorkflow.none { hydrated -> hydrated.matchesWorkflowIdentity(message) }
    }
    return loaded + retainedLocalNonWorkflow + retainedLocalWorkflow
  }

  private fun mergeWorkflowSnapshotIntoExistingHistory(
    existing: List<ChatMessage>,
    loaded: List<ChatMessage>,
  ): List<ChatMessage> {
    val loadedWorkflow = loaded.filter { it.role == MessageRole.WORKFLOW }
    if (loadedWorkflow.isEmpty()) return existing
    val merged = existing.toMutableList()
    loadedWorkflow.forEach { hydrated ->
      val existingIndex = merged.indexOfFirst { current ->
        current.role == MessageRole.WORKFLOW && current.matchesWorkflowIdentity(hydrated)
      }
      if (existingIndex >= 0) {
        merged[existingIndex] = hydrated.copy(
          id = merged[existingIndex].id,
          createdAtMs = merged[existingIndex].createdAtMs,
        )
      } else {
        merged += hydrated
      }
    }
    return merged
  }

  private fun shouldPreferLocalDirectHistory(
    existing: List<ChatMessage>,
    loaded: List<ChatMessage>,
    shouldRecoverStream: Boolean,
  ): Boolean {
    if (shouldRecoverStream) return false
    val existingDirectIndices = existing.indices.filter { existing[it].role != MessageRole.WORKFLOW }
    if (existingDirectIndices.isEmpty()) return false
    val loadedDirectIndices = loaded.indices.filter { loaded[it].role != MessageRole.WORKFLOW }
    if (loadedDirectIndices.isEmpty()) return true
    if (loadedDirectIndices.size < existingDirectIndices.size) return true
    return existingDirectIndices.any { localIndex ->
      loadedDirectIndices.none { hydratedIndex ->
        directMessageMatches(
          candidateMessages = loaded,
          candidateIndex = hydratedIndex,
          localMessages = existing,
          localIndex = localIndex,
          ignoreStatus = true,
        )
      }
    }
  }

  private fun unmatchedLocalDirectMessages(
    existing: List<ChatMessage>,
    loaded: List<ChatMessage>,
  ): List<ChatMessage> {
    val loadedDirectIndices = loaded.indices.filter { loaded[it].role != MessageRole.WORKFLOW }
    return existing.indices
      .filter { existing[it].role != MessageRole.WORKFLOW }
      .filter { localIndex ->
        loadedDirectIndices.none { hydratedIndex ->
          directMessageMatches(
            candidateMessages = loaded,
            candidateIndex = hydratedIndex,
            localMessages = existing,
            localIndex = localIndex,
          )
        }
      }
      .map(existing::get)
  }

  private fun directMessageMatches(
    candidateMessages: List<ChatMessage>,
    candidateIndex: Int,
    localMessages: List<ChatMessage>,
    localIndex: Int,
    ignoreStatus: Boolean = false,
  ): Boolean {
    val candidate = candidateMessages.getOrNull(candidateIndex) ?: return false
    val local = localMessages.getOrNull(localIndex) ?: return false
    return candidate.confirmsInterruptedLocalMessage(
      other = local,
      candidateMessages = candidateMessages,
      candidateIndex = candidateIndex,
      localMessages = localMessages,
      localIndex = localIndex,
    ) || (
      candidate.role == local.role &&
        candidate.content == local.content &&
        (ignoreStatus || candidate.status == local.status) &&
        candidate.label == local.label &&
        candidate.eventKey == local.eventKey &&
        candidate.workflowId == local.workflowId
    )
  }

  private fun ChatMessage.confirmsInterruptedLocalMessage(
    other: ChatMessage,
    candidateMessages: List<ChatMessage>,
    candidateIndex: Int,
    localMessages: List<ChatMessage>,
    localIndex: Int,
  ): Boolean {
    val confirmsInterruptedTerminal = status == MessageStatus.COMPLETE ||
      (status == MessageStatus.INTERRUPTED && isDirectChatStopRaceLabel(label))
    return role == MessageRole.ASSISTANT &&
      other.role == MessageRole.ASSISTANT &&
      confirmsInterruptedTerminal &&
      other.status == MessageStatus.INTERRUPTED &&
      content.length >= other.content.length &&
      content.startsWith(other.content) &&
      hasCompatibleInterruptedCompletionLabel(other) &&
      eventKey == other.eventKey &&
      workflowId == other.workflowId &&
      previousDirectUserContent(candidateMessages, candidateIndex) == previousDirectUserContent(localMessages, localIndex)
  }

  private fun ChatMessage.hasCompatibleInterruptedCompletionLabel(other: ChatMessage): Boolean {
    if (label == other.label) return true
    return isDirectChatStopRaceLabel(label) && other.label.isNullOrBlank()
  }

  private fun canonicalSnapshotConfirmsInterruptedCompletion(
    existing: List<ChatMessage>,
    recovered: List<ChatMessage>,
  ): Boolean {
    val interruptedIndex = existing.indexOfLast {
      it.role == MessageRole.ASSISTANT && it.status == MessageStatus.INTERRUPTED
    }
    if (interruptedIndex < 0) return false
    val interruptedLocal = existing[interruptedIndex]
    return recovered.indices.any { hydratedIndex ->
      val candidate = recovered[hydratedIndex]
      !isDirectChatStopRaceLabel(candidate.label) && candidate.confirmsInterruptedLocalMessage(
        other = interruptedLocal,
        candidateMessages = recovered,
        candidateIndex = hydratedIndex,
        localMessages = existing,
        localIndex = interruptedIndex,
      )
    }
  }

  private fun canonicalSnapshotConfirmsFailedCompletion(
    existing: List<ChatMessage>,
    recovered: List<ChatMessage>,
  ): Boolean {
    val failedIndex = existing.indexOfLast {
      it.role == MessageRole.ASSISTANT && it.status == MessageStatus.FAILED
    }
    if (failedIndex < 0) return false
    val failedLocal = existing[failedIndex]
    val failedUser = previousDirectUserContent(existing, failedIndex).orEmpty()
    if (failedUser.isBlank()) return false
    return recovered.indices.any { hydratedIndex ->
      val candidate = recovered[hydratedIndex]
      candidate.role == MessageRole.ASSISTANT &&
        candidate.status == MessageStatus.COMPLETE &&
        previousDirectUserContent(recovered, hydratedIndex) == failedUser &&
        (
          failedLocal.eventKey.orEmpty().isBlank() ||
            candidate.eventKey == failedLocal.eventKey
        ) &&
        (
          failedLocal.workflowId.orEmpty().isBlank() ||
            candidate.workflowId == failedLocal.workflowId
        )
    }
  }

  private fun canonicalSnapshotConfirmsStopRaceCompletion(
    existing: List<ChatMessage>,
    recovered: List<ChatMessage>,
  ): Boolean {
    val interruptedIndex = existing.indexOfLast {
      it.role == MessageRole.ASSISTANT && it.status == MessageStatus.INTERRUPTED
    }
    if (interruptedIndex < 0) return false
    val interruptedLocal = existing[interruptedIndex]
    return recovered.indices.any { hydratedIndex ->
      val candidate = recovered[hydratedIndex]
      isDirectChatStopRaceLabel(candidate.label) && candidate.confirmsInterruptedLocalMessage(
        other = interruptedLocal,
        candidateMessages = recovered,
        candidateIndex = hydratedIndex,
        localMessages = existing,
        localIndex = interruptedIndex,
      )
    }
  }

  private fun previousDirectUserContent(messages: List<ChatMessage>, startIndexExclusive: Int): String? {
    for (idx in (startIndexExclusive - 1) downTo 0) {
      val msg = messages[idx]
      if (msg.role == MessageRole.WORKFLOW) continue
      if (msg.role == MessageRole.USER) return msg.content
    }
    return null
  }

  private fun preferredRecoveredStreamContent(localContent: String, canonicalContent: String): String {
    val local = localContent
    val canonical = canonicalContent
    if (local.isBlank()) return canonical
    if (canonical.isBlank()) return local
    if (canonical.startsWith(local)) return canonical
    if (local.startsWith(canonical)) return local
    return local
  }

  private fun isCanonicalStreamActive(status: String?): Boolean {
    return when (status?.trim()?.lowercase()) {
      "streaming", "running", "active", "submitted", "awaiting_resume", "interrupted", "interrupted_recovering" -> true
      else -> false
    }
  }

  private fun isMalformedSessionSnapshotPayloadError(error: Throwable): Boolean {
    val message = error.message?.trim()?.lowercase().orEmpty()
    return message.contains("session snapshot failed: malformed payload")
  }

  private fun applySessionSnapshotRecoveryError(tab: AgentTab) {
    updateAgentState(tab) {
      it.copy(
        isStreaming = false,
        isSubmitting = false,
        streamingProgress = null,
        streamPhase = null,
        streamPhaseStartedAtMs = null,
        lastError = "Session recovery failed: malformed session snapshot payload from the server.",
      )
    }
    saveHistoryCacheFromUiState(tab)
  }

  private fun isDirectChatStopRaceLabel(label: String?): Boolean {
    return label?.trim()?.equals(DIRECT_CHAT_STOP_RACE_LABEL, ignoreCase = true) == true
  }

  private fun ChatMessage.localMergeIdentityKey(): String {
    return listOf(
      role.name,
      status.name,
      content,
      label.orEmpty(),
      eventKey.orEmpty(),
      workflowId.orEmpty(),
    ).joinToString("|")
  }

  private fun ChatMessage.matchesWorkflowIdentity(other: ChatMessage): Boolean {
    val thisEventKey = eventKey?.trim().orEmpty()
    val otherEventKey = other.eventKey?.trim().orEmpty()
    if (thisEventKey.isNotBlank() && otherEventKey.isNotBlank()) {
      return thisEventKey == otherEventKey
    }
    return workflowId == other.workflowId &&
      label == other.label &&
      content == other.content &&
      status == other.status
  }

  private fun formatServerUnavailableMessage(probe: ServerHealthProbe): String {
    val baseUrl = probe.baseUrl.ifBlank { ServerConfig.resolveBaseUrl(getApplication()) }
    val reason = when {
      probe.detail.isNotBlank() -> probe.detail
      probe.httpCode != null -> "HTTP ${probe.httpCode}"
      probe.status.isNotBlank() -> "health status=${probe.status}"
      else -> "health probe failed"
    }
    return "Backend check failed at $baseUrl/api/health ($reason). Verify the mobile base URL and backend status."
  }

  override fun onCleared() {
    super.onCleared()
    preflightJobs.values.forEach { it.cancel() }
    streamJobs.values.forEach { it.cancel() }
    sessionRecoveryJobs.values.forEach { it.cancel() }
    workflowRunJobs.values.forEach { it.cancel() }
    workflowPollJobs.values.forEach { it.cancel() }
    workflowResumeJobs.values.forEach { it.cancel() }
    heartbeatJob?.cancel()
    AppLogger.i("ChatViewModel onCleared")
  }

  private fun restorePersistedState() {
    val selectedTab = AgentTab.visibleOrDefault(prefs.getString(prefsSelectedTabKey(), AgentTab.default.key))
    var state = ChatUiState(selectedTab = selectedTab)
    AgentTab.entries.forEach { tab ->
      val draft = (
        prefs.getString(prefsDraftKey(tab), null)
          ?: if (tab == AgentTab.ALL) prefs.getString(legacyDraftKey, "") else ""
        ).orEmpty()
      val pending = (
        prefs.getString(prefsPendingKey(tab), null)
          ?: if (tab == AgentTab.ALL) prefs.getString(legacyPendingKey, "") else ""
        ).orEmpty()
      val pendingRequest = restorePendingTurnRequest(tab)
      if (pendingRequest != null) {
        pendingTurnRequests[tab] = pendingRequest
      }
      val failed = (
        prefs.getString(prefsFailedKey(tab), null)
          ?: if (tab == AgentTab.ALL) prefs.getString(legacyFailedKey, "") else ""
        ).orEmpty()
      val unread = prefs.getBoolean(prefsUnreadKey(tab), false)
      val title = if (tab.supportsConversationTitle()) {
        normalizeConversationTitle(prefs.getString(prefsTitleKey(tab), ""))
      } else {
        ""
      }
      val queuedPrompts = parseJsonStringArray(prefs.getString(prefsQueueKey(tab), "[]").orEmpty())
        .map { it.trim() }
        .filter { it.isNotBlank() }
        .take(AGENT_PROMPT_QUEUE_MAX_DEPTH)
      val queuedTransportPrompts = parseJsonStringArray(prefs.getString(prefsQueueTransportKey(tab), "[]").orEmpty())
        .map { it.trim() }
        .filter { it.isNotBlank() }
        .take(AGENT_PROMPT_QUEUE_MAX_DEPTH)
      val selectedPis = prefs.getString(prefsPisKey(tab), "").orEmpty()
      val workflowCommentary = prefs.getString(prefsWorkflowCommentaryKey(tab), "").orEmpty()
      val activeWorkflowId = prefs.getString(prefsActiveWorkflowKey(tab), null)?.trim()?.ifBlank { null }
      val pendingLaunch = pendingWorkflowLaunches[tab]
      val current = state.agentStates[tab] ?: AgentChatState(tab = tab)
      val updated = current.copy(
        title = title,
        draft = draft,
        queuedPrompts = queuedPrompts,
        selectedPisId = selectedPis,
        workflowCommentary = workflowCommentary,
        activeWorkflowId = activeWorkflowId,
        isLaunchingWorkflow = pendingLaunch != null,
        lastFailedPrompt = when {
          failed.isNotBlank() -> failed
          pending.isNotBlank() -> pending
          pendingRequest?.prompt?.isNotBlank() == true -> pendingRequest.prompt
          else -> null
        },
        hasUnread = unread,
      )
      queuedPromptTransportPrompts[tab] = if (queuedTransportPrompts.size == queuedPrompts.size) {
        queuedTransportPrompts.toMutableList()
      } else {
        queuedPrompts.toMutableList()
      }
      state = state.copy(agentStates = state.agentStates + (tab to updated))
    }
    val selectedState = state.agentStates[selectedTab] ?: AgentChatState(tab = selectedTab)
    state = state.copy(agentStates = state.agentStates + (selectedTab to ChatReducer.applySelected(selectedState)))
    _uiState.value = state
    val editor = prefs.edit().putBoolean(prefsUnreadKey(selectedTab), false)
    AgentTab.entries.forEach { tab ->
      val stored = prefs.getString(prefsTitleKey(tab), null)
      if (stored != null && (!tab.supportsConversationTitle() || normalizeConversationTitle(stored).isBlank())) {
        editor.remove(prefsTitleKey(tab))
      }
    }
    editor.apply()
  }

  private fun isNetworkAvailable(): Boolean {
    networkChecker?.let { return it() }
    return try {
      val cm = getApplication<Application>()
        .getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        ?: return true
      val network = cm.activeNetwork ?: return false
      val caps = cm.getNetworkCapabilities(network) ?: return false
      caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    } catch (_: SecurityException) {
      true
    } catch (_: Throwable) {
      true
    }
  }

  private fun loadCachedHistory() {
    AgentTab.entries.forEach { tab ->
      if (agentState(tab).messages.isNotEmpty()) return@forEach
      val raw = (
        prefs.getString(prefsHistoryKey(tab), null)
          ?: if (tab == AgentTab.ALL) prefs.getString(legacyHistoryKey, "") else ""
        ).orEmpty()
      if (raw.isBlank()) return@forEach
      try {
        val rows = parseJsonObjectArray(raw).mapNotNull { row ->
          val roleRaw = row.stringOrEmpty("role").trim().lowercase()
          val content = row.stringOrEmpty("content")
          if (content.isBlank()) return@mapNotNull null
          val role = when (roleRaw) {
            "assistant" -> MessageRole.ASSISTANT
            "workflow" -> MessageRole.WORKFLOW
            else -> MessageRole.USER
          }
          val label = row.stringOrNull("label")
          SessionHistoryMessage(
            role = role,
            content = content,
            status = parsePersistedMessageStatus(
              rawStatus = row.stringOrEmpty("status"),
              label = label,
            ),
            label = label,
            workflowId = row.stringOrNull("workflow_id"),
            eventKey = row.stringOrNull("event_key"),
            planeRouted = row.booleanOrFalse("plane_routed"),
          )
        }
        if (rows.isEmpty()) return@forEach
        var nowMs = System.currentTimeMillis() - (rows.size * 5L)
        val loaded = rows.map { row ->
          nowMs += 5L
          ChatMessage(
            threadId = agentState(tab).threadId,
            role = row.role,
            content = row.content,
            status = row.status,
            createdAtMs = nowMs,
            updatedAtMs = nowMs,
            label = row.label,
            eventKey = row.eventKey,
            workflowId = row.workflowId,
            planeRouted = row.planeRouted,
          )
        }
        updateAgentState(tab) { it.copy(messages = loaded) }
        seedWorkflowRuntimeRecordFromMessages(tab = tab, messages = loaded)
        AppLogger.i("loaded cached history tab=${tab.key} messages=${loaded.size}")
      } catch (_: Throwable) {
        // ignore malformed cache
      }
    }
  }

  private fun saveHistoryCache(tab: AgentTab, rows: List<SessionHistoryMessage>) {
    try {
      val payload = jsonArray(
        rows.map { row ->
          val fields = mutableListOf(
            "role" to jsonString(
              when (row.role) {
                MessageRole.ASSISTANT -> "assistant"
                MessageRole.WORKFLOW -> "workflow"
                MessageRole.USER -> "user"
              },
            ),
            "content" to jsonString(row.content),
            "status" to jsonString(row.status.name.lowercase()),
          )
          row.label?.takeIf { it.isNotBlank() }?.let { fields += "label" to jsonString(it) }
          row.workflowId?.takeIf { it.isNotBlank() }?.let { fields += "workflow_id" to jsonString(it) }
          row.eventKey?.takeIf { it.isNotBlank() }?.let { fields += "event_key" to jsonString(it) }
          if (row.planeRouted) fields += "plane_routed" to "true"
          jsonObject(fields)
        },
      )
      prefs.edit().putString(prefsHistoryKey(tab), payload).apply()
    } catch (_: Throwable) {
      // ignore cache write failures
    }
  }

  private fun saveHistoryCacheFromUiState(tab: AgentTab) {
    val rows = agentState(tab).messages.mapNotNull { m ->
      if (m.content.isBlank()) return@mapNotNull null
      if (m.status == MessageStatus.STREAMING) return@mapNotNull null
      if (m.status == MessageStatus.SENDING && m.role == MessageRole.ASSISTANT) return@mapNotNull null
      SessionHistoryMessage(
        role = m.role,
        content = m.content,
        status = m.status,
        label = m.label,
        workflowId = m.workflowId,
        eventKey = m.eventKey,
        planeRouted = m.planeRouted,
      )
    }
    if (rows.isNotEmpty()) {
      saveHistoryCache(tab, rows)
      AppLogger.i("saved ui cache tab=${tab.key} messages=${rows.size}")
    } else {
      prefs.edit().remove(prefsHistoryKey(tab)).apply()
    }
  }

  private fun maybeDrainQueuedPrompt(tab: AgentTab) {
    val current = agentState(tab)
    if (current.isStreaming || current.isSubmitting) return
    if (current.lastFailedPrompt?.isNotBlank() == true) return
    val nextPrompt = current.queuedPrompts.firstOrNull()?.trim().orEmpty()
    if (nextPrompt.isBlank()) return
    dispatchPromptWithPreflight(tab, nextPrompt, fromQueue = true)
  }

  private fun consumeQueuedPromptHead(tab: AgentTab, prompt: String) {
    val queue = agentState(tab).queuedPrompts
    if (queue.isEmpty()) return
    val head = queue.firstOrNull()?.trim().orEmpty()
    if (head.isBlank()) return
    if (head != prompt.trim()) return
    val nextQueue = queue.drop(1)
    val nextTransportQueue = queuedTransportPrompts(tab).drop(1)
    queuedPromptTransportPrompts[tab] = nextTransportQueue.toMutableList()
    updateAgentState(tab) { it.copy(queuedPrompts = nextQueue) }
    persistQueuedPrompts(tab, nextQueue, nextTransportQueue)
  }

  private fun persistQueuedPrompts(tab: AgentTab, prompts: List<String>, transportPrompts: List<String>) {
    val payload = jsonArray(
      prompts
        .map { it.trim() }
        .filter { it.isNotBlank() }
        .take(AGENT_PROMPT_QUEUE_MAX_DEPTH)
        .map(::jsonString),
    )
    val transportPayload = jsonArray(
      transportPrompts
        .map { it.trim() }
        .filter { it.isNotBlank() }
        .take(AGENT_PROMPT_QUEUE_MAX_DEPTH)
        .map(::jsonString),
    )
    prefs.edit()
      .putString(prefsQueueKey(tab), payload)
      .putString(prefsQueueTransportKey(tab), transportPayload)
      .apply()
  }

  private fun queuedTransportPrompts(tab: AgentTab): List<String> {
    return queuedPromptTransportPrompts[tab]?.toList().orEmpty()
  }

  private fun beginPreflight(tab: AgentTab, prompt: String) {
    updateAgentState(tab) { ChatReducer.applyPreflightStarted(it, prompt = prompt, nowMs = System.currentTimeMillis()) }
  }

  private fun finishPreflight(tab: AgentTab) {
    updateAgentState(tab) {
      if (it.isStreaming || !it.isSubmitting) {
        it
      } else {
        it.copy(isSubmitting = false)
      }
    }
  }

  private fun restorePreflightPrompt(tab: AgentTab, prompt: String) {
    val trimmed = prompt.trim()
    if (trimmed.isBlank()) return
    updateAgentState(tab) { state ->
      val userIdx = state.messages.indexOfLast {
        it.role == MessageRole.USER &&
          it.status == MessageStatus.SENDING &&
          it.content == trimmed
      }
      if (userIdx < 0) {
        if (state.draft == trimmed) {
          state
        } else {
          state.copy(draft = trimmed)
        }
      } else {
        val next = state.messages.toMutableList()
        next.removeAt(userIdx)
        val assistantIdx = next.indexOfLast {
          it.role == MessageRole.ASSISTANT &&
            (it.status == MessageStatus.STREAMING || it.status == MessageStatus.CANCELLED) &&
            it.content.isBlank()
        }
        if (assistantIdx >= 0) {
          next.removeAt(assistantIdx)
        }
        state.copy(messages = next, draft = trimmed)
      }
    }
    saveHistoryCacheFromUiState(tab)
  }

  private fun currentPendingPreflightPrompt(tab: AgentTab): String {
    return agentState(tab).messages.lastOrNull {
      it.role == MessageRole.USER &&
        it.status == MessageStatus.SENDING &&
        it.content.isNotBlank()
    }?.content.orEmpty()
  }

  private fun shouldStartBackgroundHydration(tab: AgentTab): Boolean {
    val state = agentState(tab)
    if (state.lastFailedPrompt?.isNotBlank() == true) return true
    if (state.messages.any { it.role == MessageRole.ASSISTANT && it.status in setOf(MessageStatus.INTERRUPTED, MessageStatus.CANCELLED) }) {
      return true
    }
    val hasPendingPref = prefs.getString(prefsPendingKey(tab), null).orEmpty().isNotBlank()
    if (hasPendingPref) return true
    if ((state.activeWorkflowId != null || state.isLaunchingWorkflow) && state.messages.isEmpty()) return true
    return state.messages.isEmpty()
  }

  private fun shouldStartStartupHydration(tab: AgentTab, hasAnyLocalDirectHistory: Boolean): Boolean {
    val state = agentState(tab)
    if (state.lastFailedPrompt?.isNotBlank() == true) return true
    if (state.messages.any { it.role == MessageRole.ASSISTANT && it.status in setOf(MessageStatus.INTERRUPTED, MessageStatus.CANCELLED) }) {
      return true
    }
    val hasPendingPref = prefs.getString(prefsPendingKey(tab), null).orEmpty().isNotBlank()
    if (hasPendingPref) return true
    if ((state.activeWorkflowId != null || state.isLaunchingWorkflow) && state.messages.isEmpty()) return true
    if (state.messages.isNotEmpty()) return false
    return !hasAnyLocalDirectHistory && tab == _uiState.value.selectedTab
  }

  private fun shouldContinueStartupHydration(tab: AgentTab): Boolean {
    val state = agentState(tab)
    return state.isStreaming || state.messages.any {
      it.role == MessageRole.ASSISTANT &&
        it.status in setOf(MessageStatus.STREAMING, MessageStatus.INTERRUPTED, MessageStatus.CANCELLED)
    }
  }

  internal fun processWorkflowStatus(status: WorkflowStatus) {
    val participantTabs = mergedWorkflowParticipantTabs(status)
    upsertWorkflowRuntimeRecord(
      workflowId = status.workflowId,
      participantTabs = participantTabs,
      status = status.status,
      iteration = status.iteration,
    )
    val isTerminal = status.status.lowercase() in TERMINAL_WORKFLOW_STATUSES
    if (!isTerminal) {
      participantTabs.forEach { targetTab ->
        updateAgentState(targetTab) { state ->
          if (state.activeWorkflowId == status.workflowId) {
            state
          } else {
            state.copy(activeWorkflowId = status.workflowId)
          }
        }
        prefs.edit().putString(prefsActiveWorkflowKey(targetTab), status.workflowId).apply()
      }
    }
    status.events.forEach { event ->
      val eventKey = workflowEventKey(status.workflowId, event)
      val messageSpec = WorkflowTraceMapper.messageForEvent(status, event) ?: return@forEach
      messageSpec.tabs.forEach { targetTab ->
        appendWorkflowMessageIfMissing(
          tab = targetTab,
          eventKey = eventKey,
          workflowId = status.workflowId,
          label = messageSpec.label,
          content = messageSpec.content,
        )
      }
    }

    if (isTerminal) {
      val runtimeRecord = workflowRuntimeRecords[status.workflowId]
      val terminalTabs = runtimeRecord?.participantTabs.orEmpty().ifEmpty { participantTabs }
      val hasTerminalFailureEvent = status.status.equals("failed", ignoreCase = true) &&
        WorkflowTraceMapper.hasTerminalFailureEvent(status)
      AgentTab.entries.forEach { targetTab ->
        updateAgentState(targetTab) { state ->
          if (state.activeWorkflowId == status.workflowId) {
            ChatReducer.applyWorkflowFinished(state)
          } else {
            state
          }
        }
        if (agentState(targetTab).activeWorkflowId == null) {
          prefs.edit().remove(prefsActiveWorkflowKey(targetTab)).apply()
        }
      }
      if (status.intentValidationReport.isNotBlank()) {
        val reportKey = "report|${status.workflowId}"
        if (emittedWorkflowReports.add(reportKey)) {
          terminalTabs.forEach { targetTab ->
            appendWorkflowMessageIfMissing(
              tab = targetTab,
              eventKey = reportKey,
              workflowId = status.workflowId,
              label = "WF-01 Result",
              content = status.intentValidationReport,
            )
          }
        }
      } else if (status.lastError.isNotBlank() && !hasTerminalFailureEvent) {
        terminalTabs.forEach { targetTab ->
          appendWorkflowMessageIfMissing(
            tab = targetTab,
            eventKey = "final_error|${status.workflowId}",
            workflowId = status.workflowId,
            label = "WF-01",
            content = "Workflow ended with error: ${status.lastError}",
          )
        }
      }
      workflowRunJobs.entries.removeAll { !it.value.isActive }
      workflowPollJobs.remove(status.workflowId)?.cancel()
      restoredActiveWorkflowIds.remove(status.workflowId)
      persistWorkflowRuntimeRecords()
    }
  }

  fun onWorkflowCancelRequested(workflowId: String) {
    val wid = workflowId.trim()
    if (wid.isBlank()) return
    val selectedTab = _uiState.value.selectedTab
    AgentTab.entries
      .filter { agentState(it).activeWorkflowId == wid }
      .forEach { tab -> workflowRunJobs.remove(tab)?.cancel() }
    workflowResumeJobs.remove(wid)?.cancel()
    viewModelScope.launch {
      try {
        repository.cancelWorkflow(wid, reason = "cancelled from mobile")
        runCatching { repository.getWorkflowStatus(wid, eventsLimit = 120) }
          .onSuccess(::processWorkflowStatus)
          .onFailure { t ->
            if (isMissingWorkflowError(t) && shouldHandleMissingWorkflowAsRecoveryFailure(wid)) {
              handleRestoredWorkflowRecoveryFailure(
                workflowId = wid,
                reason = buildWorkflowRecoveryReason(t),
              )
            } else {
              AppLogger.w("workflow cancel status refresh failed workflow=$wid: ${t.message}")
            }
          }
      } catch (t: Throwable) {
        AppLogger.w("workflow cancel failed workflow=$wid: ${t.message}")
        updateAgentState(selectedTab) {
          it.copy(lastError = t.message?.trim().orEmpty().ifBlank { "Workflow cancel failed." })
        }
      }
    }
  }

  private fun ensureWorkflowPolling(workflowId: String) {
    val current = workflowPollJobs[workflowId]
    if (current?.isActive == true) {
      return
    }
    workflowPollJobs.remove(workflowId)?.cancel()
    workflowPollJobs[workflowId] = viewModelScope.launch {
      while (isActive) {
        try {
          val status = repository.getWorkflowStatus(workflowId, eventsLimit = 120)
          processWorkflowStatus(status)
          if (status.status.lowercase() in setOf("completed", "failed", "cancelled")) {
            return@launch
          }
        } catch (t: Throwable) {
          if (isMissingWorkflowError(t) && shouldHandleMissingWorkflowAsRecoveryFailure(workflowId)) {
            handleRestoredWorkflowRecoveryFailure(
              workflowId = workflowId,
              reason = buildWorkflowRecoveryReason(t),
            )
            return@launch
          }
          AppLogger.w("workflow status poll failure workflow=$workflowId: ${t.message}")
        }
        delay(1200L)
      }
    }
  }

  private fun ensureRestoredWorkflowResume(workflowId: String) {
    if (!restoredWorkflowResumeAttempts.add(workflowId)) {
      return
    }
    val current = workflowResumeJobs[workflowId]
    if (current?.isActive == true) {
      return
    }
    workflowResumeJobs.remove(workflowId)?.cancel()
    workflowResumeJobs[workflowId] = viewModelScope.launch {
      try {
        repository.runWorkflow(workflowId, maxSteps = 40)
      } catch (t: Throwable) {
        if (isMissingWorkflowError(t)) {
          handleRestoredWorkflowRecoveryFailure(
            workflowId = workflowId,
            reason = buildWorkflowRecoveryReason(t),
          )
          return@launch
        }
        AppLogger.w("workflow resume attempt failed workflow=$workflowId: ${t.message}")
      }

      runCatching { repository.getWorkflowStatus(workflowId, eventsLimit = 120) }
        .onSuccess(::processWorkflowStatus)
        .onFailure { t ->
          if (isMissingWorkflowError(t)) {
            handleRestoredWorkflowRecoveryFailure(
              workflowId = workflowId,
              reason = buildWorkflowRecoveryReason(t),
            )
          } else {
            AppLogger.w("workflow post-resume status refresh failed workflow=$workflowId: ${t.message}")
          }
        }
    }.also { job ->
      job.invokeOnCompletion {
        if (workflowResumeJobs[workflowId] === job) {
          workflowResumeJobs.remove(workflowId)
        }
      }
    }
  }

  private fun isMissingWorkflowError(error: Throwable): Boolean {
    val text = error.message.orEmpty().lowercase()
    return text.contains("http 404") || text.contains("workflow not found")
  }

  private fun shouldHandleMissingWorkflowAsRecoveryFailure(workflowId: String): Boolean {
    return workflowId in restoredActiveWorkflowIds
  }

  private fun buildWorkflowRecoveryReason(error: Throwable): String {
    val raw = error.message?.trim().orEmpty()
    return if (raw.isBlank()) {
      "The server no longer has this workflow."
    } else {
      raw
    }
  }

  private fun handleRestoredWorkflowRecoveryFailure(workflowId: String, reason: String) {
    val existing = workflowRuntimeRecords[workflowId]
    val affectedTabs = (
      existing?.participantTabs.orEmpty() +
        AgentTab.entries.filter { agentState(it).activeWorkflowId == workflowId }
      ).ifEmpty {
      setOf(AgentTab.ORCHESTRATOR)
    }

    workflowRuntimeRecords[workflowId] = WorkflowRuntimeRecord(
      workflowId = workflowId,
      participantTabs = affectedTabs,
      status = "failed",
      iteration = existing?.iteration ?: 0,
    )
    restoredActiveWorkflowIds.remove(workflowId)
    persistWorkflowRuntimeRecords()
    workflowPollJobs.remove(workflowId)?.cancel()
    workflowResumeJobs.remove(workflowId)

    affectedTabs.forEach { targetTab ->
      updateAgentState(targetTab) { state ->
        if (state.activeWorkflowId == workflowId) {
          ChatReducer.applyWorkflowFinished(state)
        } else {
          state
        }
      }
      if (agentState(targetTab).activeWorkflowId == null) {
        prefs.edit().remove(prefsActiveWorkflowKey(targetTab)).apply()
      }
      appendWorkflowMessageIfMissing(
        tab = targetTab,
        eventKey = "restore_recovery|$workflowId",
        workflowId = workflowId,
        label = "WF-01",
        content = "Workflow recovery failed for $workflowId: $reason\nCleared the stale local active state.",
      )
    }
  }

  private fun appendWorkflowMessageIfMissing(
    tab: AgentTab,
    eventKey: String,
    workflowId: String,
    label: String,
    content: String,
  ) {
    if (content.isBlank()) return
    val seen = seenWorkflowEventKeys.getOrPut(tab) { mutableSetOf() }
    if (!seen.add(eventKey)) return
    val nowMs = System.currentTimeMillis()
    updateAgentState(tab) { state ->
      if (state.messages.any { it.eventKey == eventKey }) return@updateAgentState state
      state.copy(
        messages = state.messages + ChatMessage(
          threadId = state.threadId,
          role = MessageRole.WORKFLOW,
          content = content,
          status = MessageStatus.COMPLETE,
          createdAtMs = nowMs,
          updatedAtMs = nowMs,
          label = label,
          eventKey = eventKey,
          workflowId = workflowId,
        ),
      )
    }
    seedWorkflowRuntimeRecordFromMessages(tab = tab, messages = agentState(tab).messages)
    saveHistoryCacheFromUiState(tab)
    if (_uiState.value.selectedTab != tab) {
      markUnread(tab)
    }
  }

  private fun workflowEventKey(workflowId: String, event: WorkflowEvent): String {
    val payloadKey = event.payload.entries
      .sortedBy { it.key }
      .joinToString("&") { "${it.key}=${it.value}" }
    return "${workflowId}|${event.utc}|${event.event}|${payloadKey}"
  }

  private fun workflowParticipantTabs(status: WorkflowStatus): Set<AgentTab> {
    val tabs = linkedSetOf(AgentTab.ORCHESTRATOR)
    status.events.forEach { event ->
      WorkflowTraceMapper.messageForEvent(status, event)?.tabs?.let(tabs::addAll)
    }
    when (status.turn.lowercase()) {
      "builder_run" -> tabs += AgentTab.BUILDER
      "verifier_run" -> {
        tabs += AgentTab.BUILDER
        tabs += AgentTab.VERIFIER
      }
    }
    return tabs
  }

  private fun buildWorkflowObjective(pisId: String): String {
    return "Implement and verify $pisId with WF-01."
  }

  private fun buildWorkflowRequestSummary(
    pisId: String,
    commentary: String,
    maxIterations: Int?,
    objective: String? = null,
  ): String {
    return buildString {
      append("WF-01 requested for ")
      append(pisId)
      maxIterations?.let {
        append("\nWorkflow config: max_iterations=")
        append(it)
        append(".")
      }
      objective?.takeIf { it.isNotBlank() }?.let {
        append("\n")
        append(it)
      }
      append("\nBuilder implements, Verifier audits, and Orchestrator tracks progress.")
      if (commentary.isNotBlank()) {
        append("\n")
        append(commentary)
      }
    }
  }

  fun refreshPisOptions() {
    viewModelScope.launch {
      _uiState.update { it.copy(pisOptionsLoading = true, pisOptionsError = null) }
      val result = runCatching { ledgerItemsSource.listLedgerItems(limit = 200) }
      if (result.isFailure) {
        val reason = result.exceptionOrNull()?.message?.trim().orEmpty().ifBlank { "PIS fetch failed" }
        _uiState.update { state ->
          state.copy(
            pisOptionsLoading = false,
            pisOptionsError = reason,
          )
        }
        return@launch
      }
      val items = result.getOrDefault(emptyList())
      val options = items
        .filter { it.uid.startsWith("PIS-") }
        .filter(::isOpenPisItem)
        .sortedWith(
          compareByDescending<LedgerItemSummary> { it.updatedUtc }
            .thenByDescending { it.uid },
        )
        .map { item ->
          PisOption(
            uid = item.uid,
            title = item.title,
            status = item.status,
            releaseState = item.releaseState,
            displayLabel = buildPisDisplayLabel(item.uid, item.title),
          )
        }
      _uiState.update { state ->
        val nextAgentStates = state.agentStates.mapValues { (_, agent) ->
          if (agent.selectedPisId.isBlank() || options.any { it.uid == agent.selectedPisId }) {
            agent
          } else {
            agent.copy(selectedPisId = "")
          }
        }
        state.copy(
          pisOptions = options,
          pisOptionsLoading = false,
          pisOptionsError = null,
          agentStates = nextAgentStates,
        )
      }
    }
  }

  private fun buildPisDisplayLabel(uid: String, title: String): String {
    val basis = title.trim().ifBlank { uid }
      .replace(Regex("\\s+"), " ")
      .replace('_', ' ')
      .trim()
    val short = if (basis.length <= 26) basis else basis.take(23).trimEnd() + "..."
    return "$short · ${uid.takeLast(4)}"
  }

  private fun markUnread(tab: AgentTab) {
    updateAgentState(tab) { ChatReducer.markUnread(it) }
    prefs.edit().putBoolean(prefsUnreadKey(tab), true).apply()
  }

  private fun agentState(tab: AgentTab): AgentChatState {
    return _uiState.value.agentStates[tab] ?: AgentChatState(tab = tab)
  }

  private fun updateAgentState(tab: AgentTab, transform: (AgentChatState) -> AgentChatState) {
    _uiState.update { state ->
      val current = state.agentStates[tab] ?: AgentChatState(tab = tab)
      state.copy(agentStates = state.agentStates + (tab to transform(current)))
    }
  }

  private fun prefsSelectedTabKey(): String = "selected_tab"
  private fun prefsTitleKey(tab: AgentTab): String = "conversation_title_${tab.key}"
  private fun prefsDraftKey(tab: AgentTab): String = "draft_${tab.key}"
  private fun prefsPendingKey(tab: AgentTab): String = "pending_prompt_${tab.key}"
  private fun prefsPendingTurnRequestKey(tab: AgentTab): String = "pending_turn_request_${tab.key}"
  private fun prefsFailedKey(tab: AgentTab): String = "last_failed_prompt_${tab.key}"
  private fun prefsHistoryKey(tab: AgentTab): String = "history_cache_json_${tab.key}"
  private fun prefsQueueKey(tab: AgentTab): String = "queued_prompts_${tab.key}"
  private fun prefsQueueTransportKey(tab: AgentTab): String = "queued_prompt_transports_${tab.key}"
  private fun prefsUnreadKey(tab: AgentTab): String = "has_unread_${tab.key}"
  private fun prefsPisKey(tab: AgentTab): String = "selected_pis_${tab.key}"
  private fun prefsWorkflowCommentaryKey(tab: AgentTab): String = "workflow_commentary_${tab.key}"
  private fun prefsActiveWorkflowKey(tab: AgentTab): String = "active_workflow_${tab.key}"
  private fun prefsWorkflowRuntimeRecordsKey(): String = "workflow_runtime_records"
  private fun prefsPendingWorkflowLaunchesKey(): String = "pending_workflow_launches"

  private fun maybeResetLegacyMobileState() {
    val schemaVersion = prefs.getInt(prefsSchemaVersionKey, 0)
    if (schemaVersion >= CHAT_UI_STATE_SCHEMA_VERSION) return
    val editor = prefs.edit()
    editor.remove(legacyDraftKey)
    editor.remove(legacyPendingKey)
    editor.remove(legacyFailedKey)
    editor.remove(legacyHistoryKey)
    val selectedTab = prefs.getString(prefsSelectedTabKey(), AgentTab.default.key)
    if (!AgentTab.fromKey(selectedTab).visibleInChatTabs) {
      editor.putString(prefsSelectedTabKey(), AgentTab.default.key)
    }
    editor.putInt(prefsSchemaVersionKey, CHAT_UI_STATE_SCHEMA_VERSION)
    editor.apply()
    AppLogger.i("chat ui state migrated for schema v$CHAT_UI_STATE_SCHEMA_VERSION")
  }

  private fun applyConversationTitle(tab: AgentTab, rawTitle: String) {
    if (!tab.supportsConversationTitle()) return
    val title = normalizeConversationTitle(rawTitle)
    if (title.isBlank()) return
    updateAgentState(tab) { state ->
      if (state.title.trim() == title) state else state.copy(title = title)
    }
    prefs.edit().putString(prefsTitleKey(tab), title).apply()
  }

  private fun normalizeConversationTitle(rawTitle: String?): String {
    val title = rawTitle
      ?.trim()
      ?.replace(Regex("\\s+"), " ")
      .orEmpty()
    if (title.isBlank()) return ""
    if (!CHAT_HEADER_TITLE_REGEX.matches(title)) return ""
    return title
  }

  private fun AgentTab.supportsConversationTitle(): Boolean {
    return this == AgentTab.ALL || this == AgentTab.ALL_2 || this == AgentTab.ALL_3
  }

  private fun restoreWorkflowPersistence() {
    restoredActiveWorkflowIds.clear()
    restoreWorkflowRuntimeRecords()
    restorePendingWorkflowLaunches()
    AgentTab.entries.forEach { tab ->
      val workflowId = prefs.getString(prefsActiveWorkflowKey(tab), null)?.trim()?.ifBlank { null } ?: return@forEach
      restoredActiveWorkflowIds += workflowId
      val existing = workflowRuntimeRecords[workflowId]
      workflowRuntimeRecords[workflowId] = WorkflowRuntimeRecord(
        workflowId = workflowId,
        participantTabs = (existing?.participantTabs ?: setOf(AgentTab.ORCHESTRATOR)) + tab,
        status = existing?.status ?: "running",
        iteration = existing?.iteration ?: 0,
      )
    }
    persistWorkflowRuntimeRecords()
  }

  private fun restoreWorkflowRuntimeRecords() {
    workflowRuntimeRecords.clear()
    val raw = prefs.getString(prefsWorkflowRuntimeRecordsKey(), "").orEmpty()
    if (raw.isBlank()) return
    runCatching {
      for (obj in parseJsonObjectArray(raw)) {
        val workflowId = obj.stringOrEmpty("workflow_id").trim()
        if (workflowId.isBlank()) continue
        val tabs = obj.stringList("participants").mapTo(mutableSetOf()) { AgentTab.fromKey(it) }
        workflowRuntimeRecords[workflowId] = WorkflowRuntimeRecord(
          workflowId = workflowId,
          participantTabs = tabs.ifEmpty { setOf(AgentTab.ORCHESTRATOR) },
          status = obj.stringOrEmpty("status").ifBlank { "running" },
          iteration = obj.intOrDefault("iteration", 0),
        )
      }
    }
  }

  private fun seedWorkflowRuntimeRecordsFromCachedMessages() {
    AgentTab.entries.forEach { tab ->
      seedWorkflowRuntimeRecordFromMessages(tab = tab, messages = agentState(tab).messages)
    }
  }

  private fun seedWorkflowRuntimeRecordFromMessages(tab: AgentTab, messages: List<ChatMessage>) {
    messages
      .asSequence()
      .filter { it.role == MessageRole.WORKFLOW }
      .mapNotNull { it.workflowId?.trim()?.ifBlank { null } }
      .distinct()
      .forEach { workflowId ->
        val existing = workflowRuntimeRecords[workflowId]
        upsertWorkflowRuntimeRecord(
          workflowId = workflowId,
          participantTabs = setOf(tab),
          status = existing?.status ?: "running",
          iteration = existing?.iteration ?: 0,
        )
      }
  }

  private fun restorePendingWorkflowLaunches() {
    pendingWorkflowLaunches.clear()
    val raw = prefs.getString(prefsPendingWorkflowLaunchesKey(), "").orEmpty()
    if (raw.isBlank()) return
    runCatching {
      for (obj in parseJsonObjectArray(raw)) {
        val tab = AgentTab.fromKey(obj.stringOrNull("tab"))
        val pisId = obj.stringOrNull("pis_id").orEmpty().ifBlank { "PIS-003" }
        val commentary = obj.stringOrEmpty("commentary")
        val maxIterations = obj.intOrNull("max_iterations")
        val createdAtMs = obj.longOrDefault("created_at_ms", 0L)
        pendingWorkflowLaunches[tab] = PendingWorkflowLaunch(
          tab = tab,
          pisId = pisId,
          commentary = commentary,
          maxIterations = maxIterations,
          // Prefer the persisted launch summary so restore can match the
          // exact bubble previously shown even if formatting logic changed.
          requestSummary = obj.stringOrNull("request_summary") ?: buildWorkflowRequestSummary(
            pisId = pisId,
            commentary = commentary,
            maxIterations = maxIterations,
          ),
          createdAtMs = createdAtMs,
          launchMessageEventKey = obj.stringOrNull("launch_message_event_key").orEmpty().ifBlank {
            buildPendingWorkflowLaunchEventKey(
              tab = tab,
              pisId = pisId,
              createdAtMs = createdAtMs,
            )
          },
        )
      }
    }
  }

  private fun persistWorkflowRuntimeRecords() {
    val payload = jsonArray(
      workflowRuntimeRecords.values.sortedBy { it.workflowId }.map { record ->
        jsonObject(
          listOf(
            "workflow_id" to jsonString(record.workflowId),
            "status" to jsonString(record.status),
            "iteration" to record.iteration.toString(),
            "participants" to jsonArray(record.participantTabs.sortedBy { it.key }.map { jsonString(it.key) }),
          ),
        )
      },
    )
    prefs.edit().putString(prefsWorkflowRuntimeRecordsKey(), payload).apply()
  }

  private fun persistPendingWorkflowLaunches() {
    val payload = jsonArray(
      pendingWorkflowLaunches.values.sortedBy { it.tab.key }.map { launch ->
        jsonObject(
          listOf(
            "tab" to jsonString(launch.tab.key),
            "pis_id" to jsonString(launch.pisId),
            "commentary" to jsonString(launch.commentary),
            "request_summary" to jsonString(launch.requestSummary),
            "created_at_ms" to launch.createdAtMs.toString(),
            "launch_message_event_key" to jsonString(launch.launchMessageEventKey),
          ) + listOfNotNull(
            launch.maxIterations?.let { "max_iterations" to it.toString() },
          ),
        )
      },
    )
    prefs.edit().putString(prefsPendingWorkflowLaunchesKey(), payload).apply()
  }

  private fun persistPendingWorkflowLaunch(launch: PendingWorkflowLaunch) {
    pendingWorkflowLaunches[launch.tab] = launch
    persistPendingWorkflowLaunches()
  }

  private fun parseWorkflowLaunchDirective(draft: String): WorkflowLaunchDirective {
    return com.eiel.chat.ui.parseWorkflowLaunchDirective(draft)
  }

  private fun clearPendingWorkflowLaunch(tab: AgentTab) {
    pendingWorkflowLaunches.remove(tab)
    persistPendingWorkflowLaunches()
  }

  private fun reconcileRestoredPendingWorkflowLaunches() {
    val pending = pendingWorkflowLaunches.toMap()
    pending.forEach { (tab, launch) ->
      if (agentState(tab).activeWorkflowId != null) {
        clearPendingWorkflowLaunch(tab)
        return@forEach
      }
      ensurePendingWorkflowLaunchMessageExists(launch)
      updateAgentState(tab) {
        ChatReducer.applyWorkflowLaunchFailed(
          state = it,
          reason = "Workflow launch was interrupted before the server acknowledged start.",
          pendingMessageEventKey = launch.launchMessageEventKey,
          nowMs = System.currentTimeMillis(),
        )
      }
      saveHistoryCacheFromUiState(tab)
      clearPendingWorkflowLaunch(tab)
    }
  }

  private fun ensurePendingWorkflowLaunchMessageExists(launch: PendingWorkflowLaunch) {
    val tab = launch.tab
    val existing = agentState(tab).messages.indexOfLast { message ->
      message.role == MessageRole.WORKFLOW &&
        (
          message.eventKey == launch.launchMessageEventKey ||
            (message.status == MessageStatus.SENDING && message.content == launch.requestSummary)
          )
    }
    if (existing >= 0) {
      updateAgentState(tab) { state ->
        val current = state.messages.getOrNull(existing) ?: return@updateAgentState state
        if (current.eventKey == launch.launchMessageEventKey) return@updateAgentState state
        val next = state.messages.toMutableList()
        next[existing] = current.copy(eventKey = launch.launchMessageEventKey)
        state.copy(messages = next)
      }
      saveHistoryCacheFromUiState(tab)
      return
    }
    val nowMs = if (launch.createdAtMs > 0L) launch.createdAtMs else System.currentTimeMillis()
    updateAgentState(tab) { state ->
      state.copy(
        messages = state.messages + ChatMessage(
          threadId = state.threadId,
          role = MessageRole.WORKFLOW,
          content = launch.requestSummary,
          status = MessageStatus.SENDING,
          createdAtMs = nowMs,
          updatedAtMs = nowMs,
          label = "WF-01",
          eventKey = launch.launchMessageEventKey,
        ),
      )
    }
    saveHistoryCacheFromUiState(tab)
  }

  private fun activeWorkflowIds(): Set<String> {
    return buildSet {
      addAll(
        AgentTab.entries
          .mapNotNull { tab -> agentState(tab).activeWorkflowId },
      )
    }
  }

  private fun mergedWorkflowParticipantTabs(status: WorkflowStatus): Set<AgentTab> {
    val runtimeParticipants = workflowRuntimeRecords[status.workflowId]?.participantTabs.orEmpty()
    val activeTabs = AgentTab.entries.filterTo(linkedSetOf()) { agentState(it).activeWorkflowId == status.workflowId }
    return (runtimeParticipants + activeTabs + workflowParticipantTabs(status)).ifEmpty {
      setOf(AgentTab.ORCHESTRATOR)
    }
  }

  private fun upsertWorkflowRuntimeRecord(
    workflowId: String,
    participantTabs: Set<AgentTab>,
    status: String,
    iteration: Int,
  ) {
    val existing = workflowRuntimeRecords[workflowId]
    workflowRuntimeRecords[workflowId] = WorkflowRuntimeRecord(
      workflowId = workflowId,
      participantTabs = (existing?.participantTabs.orEmpty() + participantTabs).ifEmpty {
        setOf(AgentTab.ORCHESTRATOR)
      },
      status = status,
      iteration = maxOf(existing?.iteration ?: 0, iteration),
    )
    persistWorkflowRuntimeRecords()
  }

  private fun buildPendingWorkflowLaunchEventKey(tab: AgentTab, pisId: String, createdAtMs: Long): String {
    return "launch|${tab.key}|${pisId.trim()}|$createdAtMs"
  }

  private fun rebuildWorkflowEventCaches() {
    seenWorkflowEventKeys.clear()
    emittedWorkflowReports.clear()
    AgentTab.entries.forEach { tab ->
      val eventKeys = agentState(tab).messages.mapNotNull { it.eventKey }.toMutableSet()
      if (eventKeys.isNotEmpty()) {
        seenWorkflowEventKeys[tab] = eventKeys
      }
      eventKeys
        .filter { it.startsWith("report|") }
        .forEach { emittedWorkflowReports += it }
    }
  }

  private data class WorkflowRuntimeRecord(
    val workflowId: String,
    val participantTabs: Set<AgentTab>,
    val status: String,
    val iteration: Int,
  )

  private data class PendingWorkflowLaunch(
    val tab: AgentTab,
    val pisId: String,
    val commentary: String,
    val maxIterations: Int?,
    val requestSummary: String,
    val createdAtMs: Long,
    val launchMessageEventKey: String,
  )

  companion object {
    fun factory(application: Application): ViewModelProvider.Factory = object : ViewModelProvider.Factory {
      @Suppress("UNCHECKED_CAST")
      override fun <T : ViewModel> create(modelClass: Class<T>): T {
        if (modelClass.isAssignableFrom(ChatViewModel::class.java)) {
          return ChatViewModel(application) as T
        }
        throw IllegalArgumentException("Unknown ViewModel class: ${modelClass.name}")
      }
    }

    val TERMINAL_WORKFLOW_STATUSES = setOf("completed", "failed", "cancelled")
  }
}

interface LedgerItemsSource {
  suspend fun listLedgerItems(limit: Int): List<LedgerItemSummary>
}

private class AdminLedgerItemsSource(
  application: Application,
) : LedgerItemsSource {
  private val api = AdminApi(serverBaseUrlProvider = { ServerConfig.resolveBaseUrl(application) })

  override suspend fun listLedgerItems(limit: Int): List<LedgerItemSummary> {
    return withContext(Dispatchers.IO) {
      api.ledgerItems(limit = limit)
    }
  }
}

private val CLOSED_PIS_STATUSES = setOf("done")

private fun isOpenPisItem(item: LedgerItemSummary): Boolean {
  val status = item.status.trim().lowercase()
  return status !in CLOSED_PIS_STATUSES
}

private fun jsonArray(elements: List<String>): String {
  return elements.joinToString(prefix = "[", postfix = "]")
}

private fun jsonObject(fields: List<Pair<String, String>>): String {
  return fields.joinToString(prefix = "{", postfix = "}") { (key, value) ->
    "${jsonString(key)}:$value"
  }
}

private fun jsonString(value: String): String {
  return buildString(value.length + 2) {
    append('"')
    value.forEach { ch ->
      when (ch) {
        '\\' -> append("\\\\")
        '"' -> append("\\\"")
        '\b' -> append("\\b")
        '\u000C' -> append("\\f")
        '\n' -> append("\\n")
        '\r' -> append("\\r")
        '\t' -> append("\\t")
        else -> {
          if (ch.code < 0x20) {
            append("\\u")
            append(ch.code.toString(16).padStart(4, '0'))
          } else {
            append(ch)
          }
        }
      }
    }
    append('"')
  }
}

private fun parseJsonObjectArray(raw: String): List<Map<String, Any?>> {
  val parsed = SimpleJsonParser(raw).parseValue()
  return (parsed as? List<*>)?.mapNotNull { it as? Map<String, Any?> }.orEmpty()
}

private fun parseJsonStringArray(raw: String): List<String> {
  val parsed = SimpleJsonParser(raw).parseValue()
  return (parsed as? List<*>)?.mapNotNull { (it as? String)?.trim()?.ifBlank { null } }.orEmpty()
}

private fun Map<String, Any?>.stringOrEmpty(key: String): String {
  return stringOrNull(key).orEmpty()
}

private fun Map<String, Any?>.stringOrNull(key: String): String? {
  return (this[key] as? String)?.trim()?.ifBlank { null }
}

private fun Map<String, Any?>.stringList(key: String): List<String> {
  return (this[key] as? List<*>)?.mapNotNull { (it as? String)?.trim()?.ifBlank { null } }.orEmpty()
}

private fun Map<String, Any?>.intOrDefault(key: String, default: Int): Int {
  return when (val value = this[key]) {
    is Number -> value.toInt()
    is String -> value.toIntOrNull() ?: default
    else -> default
  }
}

private fun Map<String, Any?>.booleanOrFalse(key: String): Boolean {
  return when (val value = this[key]) {
    is Boolean -> value
    is String -> value.equals("true", ignoreCase = true)
    is Number -> value.toInt() != 0
    else -> false
  }
}

private fun Map<String, Any?>.intOrNull(key: String): Int? {
  return when (val value = this[key]) {
    is Number -> value.toInt()
    is String -> value.toIntOrNull()
    else -> null
  }
}

private fun Map<String, Any?>.longOrDefault(key: String, default: Long): Long {
  return when (val value = this[key]) {
    is Number -> value.toLong()
    is String -> value.toLongOrNull() ?: default
    else -> default
  }
}

private class SimpleJsonParser(private val raw: String) {
  private var index: Int = 0

  fun parseValue(): Any? {
    skipWhitespace()
    return when (val ch = peek()) {
      '{' -> parseObject()
      '[' -> parseArray()
      '"' -> parseString()
      '-', in '0'..'9' -> parseNumber()
      't' -> parseLiteral("true", true)
      'f' -> parseLiteral("false", false)
      'n' -> parseLiteral("null", null)
      else -> throw IllegalArgumentException("Unexpected JSON token '$ch' at $index")
    }
  }

  private fun parseObject(): Map<String, Any?> {
    expect('{')
    skipWhitespace()
    if (peek() == '}') {
      index += 1
      return emptyMap()
    }
    val out = linkedMapOf<String, Any?>()
    while (true) {
      skipWhitespace()
      val key = parseString()
      skipWhitespace()
      expect(':')
      val value = parseValue()
      out[key] = value
      skipWhitespace()
      when (peek()) {
        ',' -> index += 1
        '}' -> {
          index += 1
          return out
        }
        else -> throw IllegalArgumentException("Expected ',' or '}' at $index")
      }
    }
  }

  private fun parseArray(): List<Any?> {
    expect('[')
    skipWhitespace()
    if (peek() == ']') {
      index += 1
      return emptyList()
    }
    val out = mutableListOf<Any?>()
    while (true) {
      out += parseValue()
      skipWhitespace()
      when (peek()) {
        ',' -> index += 1
        ']' -> {
          index += 1
          return out
        }
        else -> throw IllegalArgumentException("Expected ',' or ']' at $index")
      }
    }
  }

  private fun parseString(): String {
    expect('"')
    val out = StringBuilder()
    while (index < raw.length) {
      val ch = raw[index++]
      when (ch) {
        '"' -> return out.toString()
        '\\' -> {
          val escaped = raw.getOrNull(index++) ?: throw IllegalArgumentException("Unterminated escape sequence")
          out.append(
            when (escaped) {
              '"', '\\', '/' -> escaped
              'b' -> '\b'
              'f' -> '\u000C'
              'n' -> '\n'
              'r' -> '\r'
              't' -> '\t'
              'u' -> {
                val hex = raw.substring(index, (index + 4).coerceAtMost(raw.length))
                if (hex.length != 4) throw IllegalArgumentException("Invalid unicode escape at $index")
                index += 4
                hex.toInt(16).toChar()
              }
              else -> throw IllegalArgumentException("Invalid escape '\\$escaped' at $index")
            },
          )
        }
        else -> out.append(ch)
      }
    }
    throw IllegalArgumentException("Unterminated string")
  }

  private fun parseNumber(): Number {
    val start = index
    if (peek() == '-') index += 1
    while (peek() in '0'..'9') index += 1
    val isFractional = if (peek() == '.') {
      index += 1
      while (peek() in '0'..'9') index += 1
      true
    } else {
      false
    }
    if (peek() == 'e' || peek() == 'E') {
      index += 1
      if (peek() == '+' || peek() == '-') index += 1
      while (peek() in '0'..'9') index += 1
      return raw.substring(start, index).toDouble()
    }
    val token = raw.substring(start, index)
    return if (isFractional) token.toDouble() else token.toLong()
  }

  private fun parseLiteral(token: String, value: Any?): Any? {
    if (!raw.startsWith(token, index)) {
      throw IllegalArgumentException("Expected '$token' at $index")
    }
    index += token.length
    return value
  }

  private fun skipWhitespace() {
    while (index < raw.length && raw[index].isWhitespace()) {
      index += 1
    }
  }

  private fun expect(ch: Char) {
    skipWhitespace()
    if (peek() != ch) {
      throw IllegalArgumentException("Expected '$ch' at $index")
    }
    index += 1
  }

  private fun peek(): Char {
    return raw.getOrNull(index) ?: '\u0000'
  }
}
