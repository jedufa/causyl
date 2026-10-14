package com.eiel.chat.ui

import android.app.Application
import android.content.Context
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Archive
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Hub
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Checkbox
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import com.eiel.chat.data.AdminApi
import com.eiel.chat.data.LedgerItemDetail
import com.eiel.chat.data.LedgerItemSummary
import com.eiel.chat.data.LedgerWorkflowStatus
import com.eiel.chat.data.MobileCockpitArtifactHandoffResult
import com.eiel.chat.data.MobileCockpitArchiveCreateResult
import com.eiel.chat.data.MobileCockpitContinuation
import com.eiel.chat.data.MobileCockpitPayload
import com.eiel.chat.data.MobileRuntimePolicyStore
import com.eiel.chat.data.OrchestrationWorkflowSummary
import com.eiel.chat.data.ServerConfig
import com.eiel.chat.data.WorkflowConversationItem
import com.eiel.chat.domain.WorkflowStartRequest
import com.eiel.chat.domain.WorkflowStatus
import com.eiel.chat.util.AppLogger
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private val MOBILE_BACKLOG_DEFAULT_HIDDEN_STATUSES = setOf("done")
private const val BACKLOG_EXECUTION_PANE_VIEWPORT_COUNT = 3
private val BACKLOG_EXECUTION_PANE_MAX_HEIGHT = 252.dp
private val MOBILE_BACKLOG_EDITABLE_STATUSES = listOf(
  "draft",
  "ready_for_implement",
  "in_progress",
  "ready_for_assure",
  "ready",
  "in_integration",
  "implemented",
  "verified",
  "done",
  "blocked",
  "abandoned",
  "deprecated",
  "reopened",
)
private val BACKLOG_WORKFLOW_TERMINAL_STATUSES = setOf("completed", "failed", "cancelled")
private const val BACKLOG_WORKFLOW_POLL_MS = 2500L
private const val BACKLOG_WORKFLOW_MAX_STEPS = 40
private val BACKLOG_ROLE_ORCHESTRATOR = Color(0xFF1D9E96)
private val BACKLOG_ROLE_BUILDER = Color(0xFF4078F2)
private val BACKLOG_ROLE_VERIFIER = Color(0xFFD99A22)
private val BACKLOG_TRACKER_SURFACE_TOP = Color(0xFFF7F3EA)
private val BACKLOG_TRACKER_SURFACE_BOTTOM = Color(0xFFE3D8C4)
private val BACKLOG_TRACKER_RAIL = Color(0xFF8F9C9A)
private val BACKLOG_STAGE_PENDING = Color(0xFFB8B6AC)

data class BacklogUiState(
  val allItems: List<LedgerItemSummary> = emptyList(),
  val items: List<LedgerItemSummary> = emptyList(),
  val hiddenStatuses: Set<String> = MOBILE_BACKLOG_DEFAULT_HIDDEN_STATUSES,
  val selectedUid: String = "",
  val stagedExecutionUids: Set<String> = emptySet(),
  val workflowInstructionsInput: String = "",
  val selectedItem: LedgerItemDetail? = null,
  val workflowStatus: LedgerWorkflowStatus? = null,
  val workflowHandle: OrchestrationWorkflowSummary? = null,
  val workflowRuntime: WorkflowStatus? = null,
  val workflowActivity: List<WorkflowConversationItem> = emptyList(),
  val cockpit: MobileCockpitPayload? = null,
  val loading: Boolean = false,
  val detailLoading: Boolean = false,
  val workflowLoading: Boolean = false,
  val workflowActionRunning: Boolean = false,
  val cockpitLoading: Boolean = false,
  val campaignLaunching: Boolean = false,
  val handoffExportRunning: Boolean = false,
  val archiveCreateRunning: Boolean = false,
  val statusUpdating: Boolean = false,
  val priorityUpdating: Boolean = false,
  val createRunning: Boolean = false,
  val createOutcome: String = "",
  val createRequestId: String = "",
  val message: String = "",
  val error: String = "",
)

enum class BacklogCreateMode(
  val label: String,
) {
  PIS("New PIS"),
  IDEA("New Idea"),
}

private enum class CockpitPagerPage(
  val label: String,
) {
  PIS("Active PISes"),
  RUNS("Active runs"),
  ARTIFACTS("Recent artifacts"),
}

private enum class CockpitDepthSection(
  val label: String,
) {
  SUMMARY("Summary"),
  EVIDENCE("Evidence"),
  LOGS("Logs"),
  ARTIFACT("Artifact"),
  CONFIG("Config"),
  ACTIONS("Actions"),
}

private data class CockpitDetailModel(
  val title: String,
  val subtitle: String,
  val summary: String,
  val evidence: String,
  val logs: String,
  val artifact: String,
  val config: String,
  val actions: String,
)

class BacklogViewModel(
  application: Application,
) : AndroidViewModel(application) {
  private val api = AdminApi(
    serverBaseUrlProvider = { ServerConfig.resolveBaseUrl(application) },
    workflowTimeoutProvider = { MobileRuntimePolicyStore.fromContext(application).read().workflowTimeoutS },
  )
  private val prefs by lazy {
    application.getSharedPreferences("admin_ui_state", Context.MODE_PRIVATE)
  }
  private val _uiState = MutableStateFlow(
    BacklogUiState(hiddenStatuses = loadStoredBacklogHiddenStatuses()),
  )
  val uiState: StateFlow<BacklogUiState> = _uiState.asStateFlow()

  private var workflowPollJob: Job? = null
  private var polledWorkflowId: String = ""
  private var pendingRunStartWorkflowId: String = ""

  private fun adminToken(): String = prefs.getString("admin_token", "").orEmpty()

  fun onWorkflowInstructionsChanged(value: String) {
    val uid = _uiState.value.selectedUid.trim()
    _uiState.update { it.copy(workflowInstructionsInput = value) }
    if (uid.isNotBlank()) {
      prefs.edit().putString(backlogWorkflowInstructionsPrefKey(uid), value).apply()
    }
  }

  fun refresh() {
    viewModelScope.launch {
      _uiState.update { it.copy(loading = true, cockpitLoading = true, error = "", message = "") }
      runCatching {
        withContext(Dispatchers.IO) {
          runCatching { api.mobileBacklog(limit = 250) }
            .getOrElse { api.ledgerItems(limit = 250) }
        }
        }
        .onSuccess { rawItems ->
          val allItems = rawItems.filter { it.uid.startsWith("PIS-") }
          val hiddenStatuses = normalizeBacklogHiddenStatuses(_uiState.value.hiddenStatuses)
          val items = filterMobileBacklogItems(allItems, hiddenStatuses)
          val storedStagedExecutionUids = uiState.value.stagedExecutionUids.ifEmpty { loadStoredExecutionSelections() }
          val stagedExecutionUids = sanitizeBacklogExecutionSelections(
            stagedUids = storedStagedExecutionUids,
            items = allItems,
          )
          if (stagedExecutionUids != storedStagedExecutionUids) {
            storeExecutionSelections(stagedExecutionUids)
          }
          val selectedUid = uiState.value.selectedUid
            .takeIf { uid -> items.any { it.uid == uid } }
            ?: items.firstOrNull { it.uid in stagedExecutionUids }?.uid
            ?: items.firstOrNull()?.uid.orEmpty()
          if (selectedUid.isBlank()) {
            stopWorkflowPolling()
          }
          _uiState.update { state ->
            val keepSelected = state.selectedUid == selectedUid
            state.copy(
              allItems = allItems,
              items = items,
              hiddenStatuses = hiddenStatuses,
              selectedUid = selectedUid,
              stagedExecutionUids = stagedExecutionUids,
              workflowInstructionsInput = if (keepSelected) state.workflowInstructionsInput else loadStoredWorkflowInstructions(selectedUid),
              selectedItem = if (keepSelected) state.selectedItem else null,
              workflowStatus = if (keepSelected) state.workflowStatus else null,
              workflowHandle = if (keepSelected) state.workflowHandle else null,
              workflowRuntime = if (keepSelected) state.workflowRuntime else null,
              workflowActivity = if (keepSelected) state.workflowActivity else emptyList(),
              loading = false,
              detailLoading = state.detailLoading && keepSelected,
              workflowLoading = state.workflowLoading && keepSelected,
              cockpitLoading = true,
              priorityUpdating = false,
              message = buildBacklogVisibilityMessage(
                visibleCount = items.size,
                totalCount = allItems.size,
                hiddenStatuses = hiddenStatuses,
              ),
              error = "",
            )
          }
          if (selectedUid.isNotBlank()) {
            loadItem(selectedUid)
          }
          refreshCockpitProjection(selectedUid = selectedUid, stagedExecutionUids = stagedExecutionUids)
        }
        .onFailure { error ->
          AppLogger.e("backlog refresh failed: ${error.javaClass.simpleName}: ${error.message ?: "(no message)"}", error)
          _uiState.update {
            it.copy(
              loading = false,
              cockpitLoading = false,
              error = error.message ?: error.javaClass.simpleName ?: "Ledger refresh failed",
              message = "",
            )
          }
        }
    }
  }

  fun select(uid: String) {
    val safeUid = uid.trim()
    if (safeUid.isBlank() || safeUid == _uiState.value.selectedUid) return
    stopWorkflowPolling()
    _uiState.update {
      it.copy(
        selectedUid = safeUid,
        workflowInstructionsInput = loadStoredWorkflowInstructions(safeUid),
        selectedItem = null,
        workflowStatus = null,
        workflowHandle = null,
        workflowRuntime = null,
        workflowActivity = emptyList(),
        detailLoading = true,
        workflowLoading = true,
        error = "",
      )
    }
    loadItem(safeUid)
    refreshCockpitProjection(selectedUid = safeUid, stagedExecutionUids = _uiState.value.stagedExecutionUids)
  }

  fun onExecutionStageChanged(uid: String, checked: Boolean) {
    val safeUid = uid.trim()
    if (safeUid.isBlank()) return
    val currentItems = _uiState.value.items
    val updatedSelections = sanitizeBacklogExecutionSelections(
      stagedUids = if (checked) _uiState.value.stagedExecutionUids + safeUid else _uiState.value.stagedExecutionUids - safeUid,
      items = _uiState.value.allItems.ifEmpty { currentItems },
    )
    storeExecutionSelections(updatedSelections)
    _uiState.update {
      it.copy(
        stagedExecutionUids = updatedSelections,
        error = "",
        message = if (checked) {
          "Staged $safeUid for WF-01."
        } else {
          "Removed $safeUid from the staged execution set."
        },
      )
    }
    if (checked && safeUid != _uiState.value.selectedUid) {
      select(safeUid)
    }
    refreshCockpitProjection(
      selectedUid = if (checked) safeUid else _uiState.value.selectedUid,
      stagedExecutionUids = updatedSelections,
    )
  }

  fun onStatusFilterVisibilityChanged(
    status: String,
    visible: Boolean,
  ) {
    val normalizedStatus = status.trim().lowercase()
    if (normalizedStatus.isBlank()) return
    val snapshot = _uiState.value
    val updatedHiddenStatuses = normalizeBacklogHiddenStatuses(
      if (visible) {
        snapshot.hiddenStatuses - normalizedStatus
      } else {
        snapshot.hiddenStatuses + normalizedStatus
      },
    )
    if (updatedHiddenStatuses == snapshot.hiddenStatuses) return
    storeBacklogHiddenStatuses(updatedHiddenStatuses)
    val visibleItems = filterMobileBacklogItems(snapshot.allItems, updatedHiddenStatuses)
    val selectedUid = snapshot.selectedUid
      .takeIf { uid -> visibleItems.any { it.uid == uid } }
      ?: visibleItems.firstOrNull { it.uid in snapshot.stagedExecutionUids }?.uid
      ?: visibleItems.firstOrNull()?.uid.orEmpty()
    if (selectedUid.isBlank()) {
      stopWorkflowPolling()
    }
    _uiState.update { state ->
      val keepSelected = state.selectedUid == selectedUid
      state.copy(
        items = visibleItems,
        hiddenStatuses = updatedHiddenStatuses,
        selectedUid = selectedUid,
        workflowInstructionsInput = if (keepSelected) state.workflowInstructionsInput else loadStoredWorkflowInstructions(selectedUid),
        selectedItem = if (keepSelected) state.selectedItem else null,
        workflowStatus = if (keepSelected) state.workflowStatus else null,
        workflowHandle = if (keepSelected) state.workflowHandle else null,
        workflowRuntime = if (keepSelected) state.workflowRuntime else null,
        workflowActivity = if (keepSelected) state.workflowActivity else emptyList(),
        detailLoading = state.detailLoading && keepSelected,
        workflowLoading = state.workflowLoading && keepSelected,
        message = buildBacklogVisibilityMessage(
          visibleCount = visibleItems.size,
          totalCount = state.allItems.size,
          hiddenStatuses = updatedHiddenStatuses,
        ),
        error = "",
      )
    }
    if (selectedUid.isNotBlank() && selectedUid != snapshot.selectedUid) {
      loadItem(selectedUid)
    }
    refreshCockpitProjection(selectedUid = selectedUid, stagedExecutionUids = _uiState.value.stagedExecutionUids)
  }

  fun prioritize(uid: String) {
    val safeUid = uid.trim()
    if (safeUid.isBlank()) return
    if (safeUid != _uiState.value.selectedUid) {
      select(safeUid)
    }
    viewModelScope.launch {
      _uiState.update {
        it.copy(
          priorityUpdating = true,
          error = "",
          message = "Updating execution queue...",
        )
      }
      runCatching {
        withContext(Dispatchers.IO) { api.mobileBacklogPrioritize(uid = safeUid) }
      }
        .onSuccess { result ->
          _uiState.update { state ->
            state.copy(
              items = applyBacklogPriorityUpdate(
                items = state.items,
                uid = safeUid,
                updatedUtc = result.updatedUtc,
                executionPriority = result.executionPriority,
              ),
              selectedUid = safeUid,
              selectedItem = state.selectedItem?.takeIf { it.uid == safeUid }?.let { detail ->
                detail.copy(
                  meta = detail.meta.copy(
                    updatedUtc = result.updatedUtc.ifBlank { detail.meta.updatedUtc },
                    executionPriority = result.executionPriority ?: detail.meta.executionPriority,
                  ),
                )
              } ?: state.selectedItem,
              priorityUpdating = false,
              error = "",
              message = "Moved $safeUid to the front of the execution queue.",
            )
          }
          if (_uiState.value.selectedItem?.uid != safeUid) {
            loadItem(safeUid)
          }
        }
        .onFailure { error ->
          AppLogger.e("backlog prioritize failed: ${error.javaClass.simpleName}: ${error.message ?: "(no message)"}", error)
          _uiState.update {
            it.copy(
              priorityUpdating = false,
              error = error.message ?: error.javaClass.simpleName ?: "Execution queue update failed",
              message = "",
            )
          }
        }
    }
  }

  fun updateStatus(status: String) {
    val targetStatus = status.trim().lowercase()
    val snapshot = _uiState.value
    val uid = snapshot.selectedUid.trim()
    val currentStatus = (
      snapshot.selectedItem?.meta?.status
        ?: snapshot.items.firstOrNull { it.uid == uid }?.status
      )
      .orEmpty()
      .trim()
      .lowercase()
    if (uid.isBlank() || targetStatus.isBlank() || targetStatus == currentStatus) return
    if (targetStatus !in editableBacklogStatusOptions(currentStatus)) return

    viewModelScope.launch {
      _uiState.update { it.copy(statusUpdating = true, error = "", message = "") }
      runCatching {
        withContext(Dispatchers.IO) {
          try {
            api.mobileLedgerUpdateStatus(
              uid = uid,
              status = targetStatus,
            )
          } catch (error: IllegalStateException) {
            val token = adminToken()
            if (token.isBlank() || !isMissingBacklogStatusRouteError(error.message)) {
              throw error
            }
            api.ledgerUpdateStatus(
              uid = uid,
              status = targetStatus,
              adminToken = token,
            )
          }
        }
      }
        .onSuccess { result ->
          if (!result.ok) {
            _uiState.update {
              it.copy(
                statusUpdating = false,
                error = result.reason.ifBlank { "Ledger status update failed" },
                message = "",
              )
            }
            return@onSuccess
          }
          _uiState.update { state ->
            val updatedUtc = result.updatedUtc.ifBlank {
              state.items.firstOrNull { it.uid == uid }?.updatedUtc
                ?: state.selectedItem?.meta?.updatedUtc
                ?: ""
            }
            val updatedSelectedItem = state.selectedItem?.let { selectedItem ->
              if (selectedItem.uid != uid) {
                selectedItem
              } else {
                selectedItem.copy(
                  meta = selectedItem.meta.copy(
                    status = result.status,
                    updatedUtc = updatedUtc,
                  ),
                )
              }
            }
            state.copy(
              items = applyBacklogStatusUpdate(state.items, uid, result.status, updatedUtc),
              selectedItem = updatedSelectedItem,
              statusUpdating = false,
              message = backlogStatusUpdateMessage(
                previousStatus = currentStatus,
                nextStatus = result.status,
                hiddenStatuses = state.hiddenStatuses,
              ),
              error = "",
            )
          }
          if (_uiState.value.selectedUid.trim() == uid) {
            loadItem(uid)
          }
        }
        .onFailure { error ->
          AppLogger.e("backlog status update failed uid=$uid: ${error.javaClass.simpleName}: ${error.message ?: "(no message)"}", error)
          _uiState.update {
            it.copy(
              statusUpdating = false,
              error = error.message ?: error.javaClass.simpleName ?: "Ledger status update failed",
              message = "",
            )
          }
        }
    }
  }

  fun createEntry(
    mode: BacklogCreateMode,
    title: String,
    detail: String,
  ) {
    val safeTitle = title.trim()
    val safeDetail = detail.trim()
    if (safeTitle.isBlank()) {
      _uiState.update {
        it.copy(
          error = if (mode == BacklogCreateMode.PIS) "PIS title is required." else "Idea title is required.",
          message = "",
        )
      }
      return
    }
    val token = adminToken()
    if (token.isBlank()) {
      _uiState.update {
        it.copy(
          error = "Admin token is required to create backlog items from mobile.",
          message = "",
        )
      }
      return
    }

    viewModelScope.launch {
      val requestId = java.util.UUID.randomUUID().toString()
      _uiState.update {
        it.copy(
          createRunning = true,
          createOutcome = "",
          createRequestId = requestId,
          error = "",
          message = if (mode == BacklogCreateMode.PIS) "Creating draft PIS..." else "Saving deferred idea...",
        )
      }
      runCatching {
        withContext(Dispatchers.IO) {
          when (mode) {
            BacklogCreateMode.PIS -> {
              val created = api.ledgerCreateItem(
                adminToken = token,
                title = safeTitle,
                pisMarkdown = buildMobileLedgerMarkdown(mode = mode, title = safeTitle, detail = safeDetail),
                tags = buildMobileLedgerTags(mode = mode),
              )
              if (!created.ok || created.uid.isBlank()) {
                throw IllegalStateException("ledger create failed")
              }
              created.uid
            }
            BacklogCreateMode.IDEA -> {
              val created = api.ideaNoteCreate(
                adminToken = token,
                title = safeTitle,
                ideaMarkdown = buildMobileLedgerMarkdown(mode = mode, title = safeTitle, detail = safeDetail),
                tags = buildMobileLedgerTags(mode = mode),
              )
              if (!created.ok || created.uid.isBlank()) {
                throw IllegalStateException("idea note create failed")
              }
              created.uid
            }
          }
        }
      }
        .onSuccess { uid ->
          _uiState.update {
            it.copy(
              createRunning = false,
              createOutcome = "success",
              selectedUid = if (mode == BacklogCreateMode.PIS) uid else it.selectedUid,
              message = if (mode == BacklogCreateMode.PIS) {
                "Created draft $uid."
              } else {
                "Saved idea note $uid."
              },
              error = "",
            )
          }
          refresh()
          if (mode == BacklogCreateMode.PIS) {
            select(uid)
          }
        }
        .onFailure { error ->
          AppLogger.e("backlog create failed: ${error.javaClass.simpleName}: ${error.message ?: "(no message)"}", error)
          _uiState.update {
            it.copy(
              createRunning = false,
              createOutcome = "failed",
              error = error.message ?: error.javaClass.simpleName ?: "Ledger create failed",
              message = "",
            )
          }
        }
    }
  }

  fun importSharedDocument(
    type: SharedDocumentType,
    filename: String,
    mimeType: String,
    text: String,
  ) {
    val token = adminToken()
    if (token.isBlank()) {
      _uiState.update {
        it.copy(
          error = "Admin token is required to import shared documents into the plane.",
          message = "",
        )
      }
      return
    }
    val importedText = text.trim()
    if (importedText.isBlank()) {
      _uiState.update {
        it.copy(
          error = "Shared document text was empty.",
          message = "",
        )
      }
      return
    }
    val title = sharedDocumentTitle(type, filename, importedText)
    val markdown = buildSharedImportMarkdown(
      type = type,
      title = title,
      text = importedText,
      filename = filename,
      mimeType = mimeType,
    )
    viewModelScope.launch {
      val requestId = java.util.UUID.randomUUID().toString()
      _uiState.update {
        it.copy(
          createRunning = true,
          createOutcome = "",
          createRequestId = requestId,
          error = "",
          message = "Importing ${type.label.lowercase()} into the plane...",
        )
      }
      runCatching {
        withContext(Dispatchers.IO) {
          val created = api.ledgerCreateItem(
            adminToken = token,
            title = title,
            pisMarkdown = markdown,
            tags = buildSharedImportTags(type),
          )
          if (!created.ok || created.uid.isBlank()) {
            throw IllegalStateException("shared import create failed")
          }
          created.uid
        }
      }
        .onSuccess { uid ->
          _uiState.update {
            it.copy(
              createRunning = false,
              createOutcome = "success",
              selectedUid = uid,
              message = "Imported ${type.label.lowercase()} as $uid.",
              error = "",
            )
          }
          refresh()
          select(uid)
        }
        .onFailure { error ->
          AppLogger.e("shared import failed: ${error.javaClass.simpleName}: ${error.message ?: "(no message)"}", error)
          _uiState.update {
            it.copy(
              createRunning = false,
              createOutcome = "failed",
              error = error.message ?: error.javaClass.simpleName ?: "Shared import failed",
              message = "",
            )
          }
        }
    }
  }

  fun refreshWorkflow() {
    val uid = _uiState.value.selectedUid.trim()
    if (uid.isBlank()) return
    refreshWorkflowState(uid, showLoading = true)
  }

  fun launchWorkflow() {
    val snapshot = _uiState.value
    val uid = snapshot.selectedUid.trim()
    if (uid.isBlank()) return
    val existingWorkflowId = snapshot.workflowHandle?.workflowId?.trim().orEmpty()
    val workflowStatus = snapshot.workflowRuntime?.status?.ifBlank {
      snapshot.workflowHandle?.status.orEmpty()
    } ?: snapshot.workflowHandle?.status.orEmpty()
    val canResumeExisting = existingWorkflowId.isNotBlank() &&
      !isWorkflowTerminalStatus(workflowStatus) &&
      !workflowHasProgress(snapshot.workflowRuntime)
    if (canResumeExisting) {
      _uiState.update {
        it.copy(
          workflowActionRunning = false,
          error = "",
          message = "WF-01 runner starting for $uid.",
        )
      }
      launchWorkflowRunRequest(uid = uid, workflowId = existingWorkflowId)
      return
    }
    if (!isWorkflowTerminalStatus(workflowStatus) && existingWorkflowId.isNotBlank()) {
      return
    }
    if (uid !in snapshot.stagedExecutionUids) {
      _uiState.update {
        it.copy(
          error = "",
          message = "Tick $uid in the execution list before launching WF-01.",
        )
      }
      return
    }
    viewModelScope.launch {
      _uiState.update { it.copy(workflowActionRunning = true, error = "", message = "") }
      runCatching {
        withContext(Dispatchers.IO) {
          api.startWorkflow(buildBacklogWorkflowStartRequest(uid, snapshot.workflowInstructionsInput))
        }
      }
        .onSuccess { started ->
          _uiState.update {
            it.copy(
              workflowActionRunning = false,
              message = "WF-01 created for $uid. Runner starting...",
              error = "",
            )
          }
          refreshWorkflowState(uid, showLoading = true, preferredWorkflowId = started.workflowId)
          launchWorkflowRunRequest(uid = uid, workflowId = started.workflowId)
        }
        .onFailure { error ->
          AppLogger.e("backlog workflow start failed uid=$uid: ${error.message}", error)
          _uiState.update {
            it.copy(
              workflowActionRunning = false,
              error = error.message ?: "Workflow launch failed",
              message = "",
            )
          }
        }
    }
  }

  fun cancelWorkflow() {
    val snapshot = _uiState.value
    val uid = snapshot.selectedUid.trim()
    val workflowId = snapshot.workflowHandle?.workflowId?.trim().orEmpty()
    if (uid.isBlank() || workflowId.isBlank()) return
    viewModelScope.launch {
      _uiState.update { it.copy(workflowActionRunning = true, error = "", message = "") }
      runCatching {
        withContext(Dispatchers.IO) {
          api.cancelWorkflow(workflowId, reason = "cancelled from backlog")
        }
      }
        .onSuccess {
          _uiState.update {
            it.copy(
              workflowActionRunning = false,
              message = "Workflow cancelled for $uid.",
              error = "",
            )
          }
          refreshWorkflowState(uid, showLoading = true, preferredWorkflowId = workflowId)
        }
        .onFailure { error ->
          AppLogger.e("backlog workflow cancel failed uid=$uid workflow=$workflowId: ${error.message}", error)
          _uiState.update {
            it.copy(
              workflowActionRunning = false,
              error = error.message ?: "Workflow cancel failed",
              message = "",
            )
          }
        }
    }
  }

  fun launchCampaign() {
    val snapshot = _uiState.value
    val selectedUids = buildCockpitActionSelection(snapshot.selectedUid, snapshot.stagedExecutionUids)
    if (selectedUids.isEmpty()) {
      _uiState.update {
        it.copy(
          error = "",
          message = "Select or stage at least one PIS before launching a campaign.",
        )
      }
      return
    }
    val launchDirective = parseWorkflowLaunchDirective(snapshot.workflowInstructionsInput)
    viewModelScope.launch {
      _uiState.update {
        it.copy(
          campaignLaunching = true,
          cockpitLoading = true,
          error = "",
          message = "Launching campaign from the plane...",
        )
      }
      runCatching {
        withContext(Dispatchers.IO) {
          api.mobileCockpitLaunchCampaign(
            selectedUids = selectedUids,
            dependencyMode = "selected_order",
            phaseLabel = "WF-01",
            maxIterations = launchDirective.maxIterations,
            commitEachSuccess = true,
          )
        }
      }
        .onSuccess { result ->
          _uiState.update {
            it.copy(
              campaignLaunching = false,
              error = "",
              message = buildCampaignLaunchMessage(result),
            )
          }
          refresh()
        }
        .onFailure { error ->
          AppLogger.e("mobile cockpit launch failed: ${error.javaClass.simpleName}: ${error.message ?: "(no message)"}", error)
          _uiState.update {
            it.copy(
              campaignLaunching = false,
              cockpitLoading = false,
              error = error.message ?: error.javaClass.simpleName ?: "Campaign launch failed",
              message = "",
            )
          }
        }
    }
  }

  fun exportHandoff() {
    val uid = _uiState.value.selectedUid.trim()
    if (uid.isBlank()) return
    viewModelScope.launch {
      _uiState.update {
        it.copy(
          handoffExportRunning = true,
          cockpitLoading = true,
          error = "",
          message = "Exporting handoff from the plane...",
        )
      }
      runCatching {
        withContext(Dispatchers.IO) {
          api.mobileCockpitExportHandoff(ledgerUid = uid, role = "builder")
        }
      }
        .onSuccess { result ->
          _uiState.update {
            it.copy(
              handoffExportRunning = false,
              error = if (result.ok) "" else result.error.ifBlank { "Handoff export failed" },
              message = buildHandoffExportMessage(result),
            )
          }
          refreshCockpitProjection(selectedUid = uid, stagedExecutionUids = _uiState.value.stagedExecutionUids)
        }
        .onFailure { error ->
          AppLogger.e("mobile cockpit handoff export failed uid=$uid: ${error.javaClass.simpleName}: ${error.message ?: "(no message)"}", error)
          _uiState.update {
            it.copy(
              handoffExportRunning = false,
              cockpitLoading = false,
              error = error.message ?: error.javaClass.simpleName ?: "Handoff export failed",
              message = "",
            )
          }
        }
    }
  }

  fun createArchiveSnapshot() {
    val token = adminToken()
    if (token.isBlank()) {
      _uiState.update {
        it.copy(
          error = "Admin token is required to create an archive snapshot from the plane.",
          message = "",
        )
      }
      return
    }
    viewModelScope.launch {
      _uiState.update {
        it.copy(
          archiveCreateRunning = true,
          cockpitLoading = true,
          error = "",
          message = "Creating archive snapshot...",
        )
      }
      runCatching {
        withContext(Dispatchers.IO) {
          api.mobileCockpitCreateArchive(adminToken = token, debug = false)
        }
      }
        .onSuccess { result ->
          _uiState.update {
            it.copy(
              archiveCreateRunning = false,
              error = if (result.ok) "" else result.error.ifBlank { "Archive snapshot failed" },
              message = buildArchiveCreateMessage(result),
            )
          }
          refreshCockpitProjection(selectedUid = _uiState.value.selectedUid, stagedExecutionUids = _uiState.value.stagedExecutionUids)
        }
        .onFailure { error ->
          AppLogger.e("mobile cockpit archive create failed: ${error.javaClass.simpleName}: ${error.message ?: "(no message)"}", error)
          _uiState.update {
            it.copy(
              archiveCreateRunning = false,
              cockpitLoading = false,
              error = error.message ?: error.javaClass.simpleName ?: "Archive snapshot failed",
              message = "",
            )
          }
        }
    }
  }

  private fun refreshCockpitProjection(
    selectedUid: String,
    stagedExecutionUids: Set<String>,
  ) {
    val selectedUids = buildCockpitActionSelection(selectedUid, stagedExecutionUids)
    viewModelScope.launch {
      _uiState.update { it.copy(cockpitLoading = true) }
      runCatching {
        withContext(Dispatchers.IO) {
          api.mobileCockpit(limit = 250, selectedUids = selectedUids)
        }
      }
        .onSuccess { payload ->
          if (selectedUid.isNotBlank() && _uiState.value.selectedUid.trim() != selectedUid.trim()) return@onSuccess
          _uiState.update {
            it.copy(
              cockpit = payload,
              cockpitLoading = false,
              loading = false,
            )
          }
        }
        .onFailure { error ->
          AppLogger.w("mobile cockpit projection refresh failed: ${error.javaClass.simpleName}: ${error.message ?: "(no message)"}")
          _uiState.update {
            it.copy(
              cockpitLoading = false,
              loading = false,
              error = if (it.error.isNotBlank()) it.error else error.message ?: error.javaClass.simpleName ?: "The plane refresh failed",
            )
          }
        }
    }
  }

  private fun loadItem(uid: String) {
    viewModelScope.launch {
      _uiState.update { it.copy(detailLoading = true, workflowLoading = true, error = "") }
      runCatching {
        withContext(Dispatchers.IO) {
          api.ledgerItem(uid)
        }
      }
        .onSuccess { item ->
          if (_uiState.value.selectedUid.trim() != uid) return@onSuccess
          _uiState.update {
            it.copy(
              selectedUid = uid,
              selectedItem = item,
              detailLoading = false,
              error = "",
            )
          }
          refreshWorkflowState(uid, showLoading = false)
        }
        .onFailure { error ->
          AppLogger.e("backlog item load failed uid=$uid: ${error.javaClass.simpleName}: ${error.message ?: "(no message)"}", error)
          _uiState.update {
            it.copy(
              detailLoading = false,
              workflowLoading = false,
              error = error.message ?: error.javaClass.simpleName ?: "PIS load failed",
            )
          }
        }
    }
  }

  private fun refreshWorkflowState(
    uid: String,
    showLoading: Boolean,
    preferredWorkflowId: String? = null,
  ) {
    viewModelScope.launch {
      if (showLoading) {
        _uiState.update { it.copy(workflowLoading = true, error = "") }
      }
      runCatching {
        withContext(Dispatchers.IO) {
          val canonicalStatus = runCatching { api.ledgerWorkflowStatus(uid, adminToken = adminToken()) }.getOrNull()
          val workflows = api.listOrchestrations(bindingType = "ledger_uid", bindingId = uid, limit = 24)
          val handle = selectWorkflowHandle(workflows, preferredWorkflowId)
          val runtime = handle?.workflowId?.takeIf { it.isNotBlank() }?.let { workflowId ->
            api.orchestrationStatus(workflowId, eventsLimit = 120)
          }
          val activity = handle?.workflowId?.takeIf { it.isNotBlank() }?.let { workflowId ->
            runCatching { api.orchestrationConversation(workflowId, eventsLimit = 160) }.getOrDefault(emptyList())
          }.orEmpty()
          WorkflowSnapshot(
            canonicalStatus = canonicalStatus,
            handle = handle,
            runtime = runtime,
            activity = activity,
          )
        }
      }
        .onSuccess { snapshot ->
          if (_uiState.value.selectedUid.trim() != uid) return@onSuccess
          if (snapshot.handle?.workflowId == pendingRunStartWorkflowId && workflowHasProgress(snapshot.runtime)) {
            pendingRunStartWorkflowId = ""
          }
          _uiState.update {
            it.copy(
              workflowStatus = snapshot.canonicalStatus,
              workflowHandle = snapshot.handle,
              workflowRuntime = snapshot.runtime,
              workflowActivity = snapshot.activity,
              workflowLoading = false,
              error = "",
            )
          }
          syncWorkflowPolling(uid, snapshot.handle, snapshot.runtime)
        }
        .onFailure { error ->
          if (_uiState.value.selectedUid.trim() != uid) return@onFailure
          AppLogger.e("backlog workflow refresh failed uid=$uid: ${error.javaClass.simpleName}: ${error.message ?: "(no message)"}", error)
          _uiState.update {
            it.copy(
              workflowLoading = false,
              error = error.message ?: error.javaClass.simpleName ?: "Workflow refresh failed",
            )
          }
          stopWorkflowPolling()
        }
    }
  }

  private fun syncWorkflowPolling(
    uid: String,
    handle: OrchestrationWorkflowSummary?,
    runtime: WorkflowStatus?,
  ) {
    val workflowId = runtime?.workflowId?.trim().orEmpty().ifBlank {
      handle?.workflowId?.trim().orEmpty()
    }
    val status = runtime?.status?.ifBlank {
      handle?.status.orEmpty()
    } ?: handle?.status.orEmpty()
    val shouldPoll = uid.isNotBlank() &&
      workflowId.isNotBlank() &&
      !isWorkflowTerminalStatus(status) &&
      (workflowHasProgress(runtime) || workflowId == pendingRunStartWorkflowId)
    if (!shouldPoll) {
      stopWorkflowPolling()
    } else {
      startWorkflowPolling(uid = uid, workflowId = workflowId)
    }
  }

  private fun startWorkflowPolling(uid: String, workflowId: String) {
    if (workflowId == polledWorkflowId && workflowPollJob?.isActive == true) return
    stopWorkflowPolling()
    polledWorkflowId = workflowId
    workflowPollJob = viewModelScope.launch {
      val currentJob = currentCoroutineContext()[Job]
      try {
        while (isActive) {
          delay(BACKLOG_WORKFLOW_POLL_MS)
          if (_uiState.value.selectedUid.trim() != uid) break
          runCatching {
            withContext(Dispatchers.IO) {
              val runtime = api.orchestrationStatus(workflowId, eventsLimit = 120)
              val activity = runCatching {
                api.orchestrationConversation(workflowId, eventsLimit = 160)
              }.getOrDefault(emptyList())
              runtime to activity
            }
          }
            .onSuccess { (runtime, activity) ->
              if (_uiState.value.selectedUid.trim() != uid) return@onSuccess
              _uiState.update { state ->
                if (state.selectedUid.trim() != uid) {
                  state
                } else {
                  state.copy(
                    workflowHandle = state.workflowHandle?.copy(
                      status = runtime.status.ifBlank { state.workflowHandle.status },
                      turn = runtime.turn.ifBlank { state.workflowHandle.turn },
                    ),
                    workflowRuntime = runtime,
                    workflowActivity = activity,
                    workflowLoading = false,
                  )
                }
              }
              if (isWorkflowTerminalStatus(runtime.status)) {
                pendingRunStartWorkflowId = ""
                refreshWorkflowState(uid, showLoading = false, preferredWorkflowId = workflowId)
                return@launch
              }
            }
            .onFailure { error ->
              AppLogger.w("backlog workflow poll failed uid=$uid workflow=$workflowId: ${error.message}")
              refreshWorkflowState(uid, showLoading = false, preferredWorkflowId = workflowId)
              return@launch
            }
        }
      } finally {
        if (polledWorkflowId == workflowId) {
          polledWorkflowId = ""
        }
        if (workflowPollJob == currentJob) {
          workflowPollJob = null
        }
      }
    }
  }

  private fun stopWorkflowPolling() {
    workflowPollJob?.cancel()
    workflowPollJob = null
    polledWorkflowId = ""
  }

  private fun launchWorkflowRunRequest(
    uid: String,
    workflowId: String,
  ) {
    pendingRunStartWorkflowId = workflowId
    startWorkflowPolling(uid = uid, workflowId = workflowId)
    viewModelScope.launch {
      runCatching {
        withContext(Dispatchers.IO) {
          api.runWorkflow(workflowId, maxSteps = BACKLOG_WORKFLOW_MAX_STEPS)
        }
      }
        .onFailure { error ->
          AppLogger.e("backlog workflow run failed uid=$uid workflow=$workflowId: ${error.message}", error)
          val current = _uiState.value
          val currentRuntime = current.workflowRuntime?.takeIf { it.workflowId == workflowId }
          val failedBeforeProgress = !workflowHasProgress(currentRuntime)
          if (failedBeforeProgress) {
            pendingRunStartWorkflowId = ""
            stopWorkflowPolling()
            if (current.selectedUid.trim() == uid) {
              _uiState.update {
                it.copy(
                  workflowActionRunning = false,
                  workflowLoading = false,
                  error = "WF-01 runner failed to start: ${error.message ?: "unknown error"}",
                  message = "",
                )
              }
            }
          } else {
            AppLogger.w("backlog workflow run request ended after progress workflow=$workflowId: ${error.message}")
          }
        }
      refreshWorkflowState(uid, showLoading = false, preferredWorkflowId = workflowId)
    }
  }

  override fun onCleared() {
    stopWorkflowPolling()
    super.onCleared()
  }

  private fun loadStoredWorkflowInstructions(uid: String): String {
    if (uid.isBlank()) return ""
    return prefs.getString(backlogWorkflowInstructionsPrefKey(uid), "").orEmpty()
  }

  private fun loadStoredExecutionSelections(): Set<String> {
    return prefs.getStringSet(BACKLOG_EXECUTION_SELECTIONS_PREF_KEY, emptySet())
      ?.map { it.trim() }
      ?.filter { it.isNotBlank() }
      ?.toSet()
      .orEmpty()
  }

  private fun storeExecutionSelections(uids: Set<String>) {
    prefs.edit().putStringSet(BACKLOG_EXECUTION_SELECTIONS_PREF_KEY, HashSet(uids)).apply()
  }

  private fun loadStoredBacklogHiddenStatuses(): Set<String> {
    return normalizeBacklogHiddenStatuses(
      prefs.getStringSet(BACKLOG_HIDDEN_STATUSES_PREF_KEY, MOBILE_BACKLOG_DEFAULT_HIDDEN_STATUSES)
        .orEmpty(),
    )
  }

  private fun storeBacklogHiddenStatuses(statuses: Set<String>) {
    prefs.edit().putStringSet(BACKLOG_HIDDEN_STATUSES_PREF_KEY, HashSet(statuses)).apply()
  }

  companion object {
    fun factory(application: Application): ViewModelProvider.Factory = object : ViewModelProvider.Factory {
      @Suppress("UNCHECKED_CAST")
      override fun <T : ViewModel> create(modelClass: Class<T>): T {
        if (modelClass.isAssignableFrom(BacklogViewModel::class.java)) {
          return BacklogViewModel(application) as T
        }
        throw IllegalArgumentException("Unknown ViewModel class: ${modelClass.name}")
      }
    }
  }
}

@Composable
fun BacklogRoute(vm: BacklogViewModel = viewModel(factory = BacklogViewModel.factory(LocalContext.current.applicationContext as Application))) {
  val state by vm.uiState.collectAsStateWithLifecycle()
  LaunchedEffect(Unit) {
    vm.refresh()
  }
  BacklogScreen(
    state = state,
    onRefresh = vm::refresh,
    onSelect = vm::select,
    onPrioritize = vm::prioritize,
    onExecutionStageChanged = vm::onExecutionStageChanged,
    onStatusFilterVisibilityChanged = vm::onStatusFilterVisibilityChanged,
    onStatusChange = vm::updateStatus,
    onCreateEntry = vm::createEntry,
    onWorkflowInstructionsChanged = vm::onWorkflowInstructionsChanged,
    onWorkflowRefresh = vm::refreshWorkflow,
    onWorkflowLaunch = vm::launchWorkflow,
    onWorkflowCancel = vm::cancelWorkflow,
    onCampaignLaunch = vm::launchCampaign,
    onExportHandoff = vm::exportHandoff,
    onCreateArchiveSnapshot = vm::createArchiveSnapshot,
  )
}

@Composable
@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
private fun BacklogScreen(
  state: BacklogUiState,
  onRefresh: () -> Unit,
  onSelect: (String) -> Unit,
  onPrioritize: (String) -> Unit,
  onExecutionStageChanged: (String, Boolean) -> Unit,
  onStatusFilterVisibilityChanged: (String, Boolean) -> Unit,
  onStatusChange: (String) -> Unit,
  onCreateEntry: (BacklogCreateMode, String, String) -> Unit,
  onWorkflowInstructionsChanged: (String) -> Unit,
  onWorkflowRefresh: () -> Unit,
  onWorkflowLaunch: () -> Unit,
  onWorkflowCancel: () -> Unit,
  onCampaignLaunch: () -> Unit,
  onExportHandoff: () -> Unit,
  onCreateArchiveSnapshot: () -> Unit,
) {
  var createDialogVisible by rememberSaveable { mutableStateOf(false) }
  var createMode by rememberSaveable { mutableStateOf(BacklogCreateMode.PIS.name) }
  var createTitle by rememberSaveable { mutableStateOf("") }
  var createDetail by rememberSaveable { mutableStateOf("") }
  var detailModel by remember { mutableStateOf<CockpitDetailModel?>(null) }
  var depthSection by rememberSaveable { mutableStateOf(CockpitDepthSection.SUMMARY.name) }
  val bottomSheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
  val haptics = LocalHapticFeedback.current
  val selectedSummary = state.items.firstOrNull { it.uid == state.selectedUid }
  val selectedItem = state.selectedItem
  val currentStatus = selectedItem?.meta?.status ?: selectedSummary?.status.orEmpty()
  val executionItems = buildExecutionPaneItems(items = state.items)
  val cockpit = state.cockpit
  val pagerState = rememberPagerState(pageCount = { CockpitPagerPage.entries.size })
  val statusFilterOptions = remember(state.allItems, state.hiddenStatuses) {
    buildBacklogStatusFilterOptions(
      items = state.allItems,
      hiddenStatuses = state.hiddenStatuses,
    )
  }
  LazyColumn(
    modifier = Modifier.fillMaxSize(),
    contentPadding = compactScreenContentPadding(),
    verticalArrangement = Arrangement.spacedBy(CompactSectionGap),
  ) {
    item {
      ExecutiveHeroCard(
        state = state,
        cockpit = cockpit,
        onPrimaryAction = {
          haptics.performHapticFeedback(HapticFeedbackType.LongPress)
          onCampaignLaunch()
        },
      )
    }

    item {
      ExecutiveSignalStrip(
        cockpit = cockpit,
        onOpenDetail = { model ->
          haptics.performHapticFeedback(HapticFeedbackType.LongPress)
          detailModel = model
          depthSection = CockpitDepthSection.SUMMARY.name
        },
      )
    }

    item {
      ExecutivePager(
        state = state,
        cockpit = cockpit,
        pagerState = pagerState,
        onOpenDetail = { model ->
          haptics.performHapticFeedback(HapticFeedbackType.LongPress)
          detailModel = model
          depthSection = CockpitDepthSection.SUMMARY.name
        },
      )
    }

    item {
      OperationalDepthHeader(
        onOpenSelectedPis = {
          detailModel = buildSelectedPisDetailModel(state)
          depthSection = CockpitDepthSection.SUMMARY.name
        },
        onOpenSystemTruth = {
          detailModel = buildSystemTruthDetailModel(state)
          depthSection = CockpitDepthSection.SUMMARY.name
        },
      )
    }

    item {
      BacklogExecutionPaneCard(
        items = executionItems,
        selectedUid = state.selectedUid,
        stagedExecutionUids = state.stagedExecutionUids,
        totalCount = state.items.size,
        loading = state.loading,
        updating = state.priorityUpdating,
        onSelect = onSelect,
        onStageChanged = onExecutionStageChanged,
        onPrioritize = onPrioritize,
      )
    }

    item {
      BacklogWorkflowCard(
        state = state,
        onInstructionsChanged = onWorkflowInstructionsChanged,
        onRefresh = onWorkflowRefresh,
        onLaunch = onWorkflowLaunch,
        onCancel = onWorkflowCancel,
      )
    }

    item {
      BacklogCampaignQueueCard(
        state = state,
        cockpit = cockpit,
        onLaunchCampaign = onCampaignLaunch,
      )
    }

    item {
      BacklogShellStatusCard(
        state = state,
        cockpit = cockpit,
        onExportHandoff = onExportHandoff,
        onCreateArchiveSnapshot = onCreateArchiveSnapshot,
      )
    }

    item {
      BacklogSignalsAndArchivesCard(
        state = state,
        cockpit = cockpit,
      )
    }

    item {
      BacklogChronicleCard(
        cockpit = cockpit,
        loading = state.cockpitLoading,
      )
    }

    item {
      AdminCard(title = "The plane") {
        Row(
          modifier = Modifier.fillMaxWidth(),
          horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
          verticalAlignment = Alignment.CenterVertically,
        ) {
          SmallButton("Refresh", onRefresh)
          SmallButton(
            "New PIS",
            onClick = {
              createMode = BacklogCreateMode.PIS.name
              createTitle = ""
              createDetail = ""
              createDialogVisible = true
            },
            enabled = !state.createRunning,
          )
          SmallButton(
            "Idea",
            onClick = {
              createMode = BacklogCreateMode.IDEA.name
              createTitle = ""
              createDetail = ""
              createDialogVisible = true
            },
            enabled = !state.createRunning,
          )
          if (state.loading) {
            Text("Loading...", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.primary)
          } else if (state.createRunning) {
            Text("Saving...", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.primary)
          } else if (state.message.isNotBlank()) {
            Text(state.message, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
          }
        }
        BacklogStatusFilter(
          options = statusFilterOptions,
          hiddenStatuses = state.hiddenStatuses,
          onStatusVisibilityChanged = onStatusFilterVisibilityChanged,
        )
        BacklogDropdown(
          items = state.items,
          selectedUid = state.selectedUid,
          onSelect = onSelect,
        )
        if (selectedSummary != null) {
          Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
            BacklogStatusDropdown(
              currentStatus = currentStatus,
              updating = state.statusUpdating,
              enabled = !state.detailLoading,
              onSelect = onStatusChange,
            )
            StatusPill(
              "Updated",
              (selectedItem?.meta?.updatedUtc ?: selectedSummary.updatedUtc).ifBlank { "-" },
              MaterialTheme.colorScheme.primary,
            )
          }
        }
        if (state.error.isNotBlank()) {
          Text(state.error, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.error)
        }
      }
    }

    if (selectedItem != null) item {
      AdminCard(title = "Selected PIS") {
        BacklogMarkdownCard(markdown = selectedItem.pisMarkdown)
      }
    }
  }

  detailModel?.let { model ->
    ModalBottomSheet(
      onDismissRequest = { detailModel = null },
      sheetState = bottomSheetState,
      containerColor = Color(0xFF111317),
      tonalElevation = 6.dp,
    ) {
      OperationalDepthSheet(
        model = model,
        selectedSection = runCatching { CockpitDepthSection.valueOf(depthSection) }.getOrDefault(CockpitDepthSection.SUMMARY),
        onSectionSelected = { depthSection = it.name },
        onDismiss = { detailModel = null },
      )
    }
  }

  if (createDialogVisible) {
    val mode = runCatching { BacklogCreateMode.valueOf(createMode) }.getOrDefault(BacklogCreateMode.PIS)
    AlertDialog(
      onDismissRequest = {
        if (!state.createRunning) {
          createDialogVisible = false
        }
      },
      title = { Text(mode.label) },
      text = {
        Column(verticalArrangement = Arrangement.spacedBy(CompactFieldGap)) {
          Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
            SmallButton(
              "PIS",
              onClick = { createMode = BacklogCreateMode.PIS.name },
              enabled = !state.createRunning && mode != BacklogCreateMode.PIS,
            )
            SmallButton(
              "Idea",
              onClick = { createMode = BacklogCreateMode.IDEA.name },
              enabled = !state.createRunning && mode != BacklogCreateMode.IDEA,
            )
          }
          AdminTextField(
            value = createTitle,
            onValueChange = { createTitle = it },
            label = if (mode == BacklogCreateMode.PIS) "Title" else "Idea title",
            modifier = Modifier.fillMaxWidth(),
            minLines = 1,
          )
          AdminTextField(
            value = createDetail,
            onValueChange = { createDetail = it },
            label = if (mode == BacklogCreateMode.PIS) "Problem / contract" else "Why it matters / next thought",
            modifier = Modifier.fillMaxWidth(),
            minLines = 5,
          )
          Text(
            if (mode == BacklogCreateMode.PIS) {
              "Creates a real draft PIS in the ledger."
            } else {
              "Creates a ledger item and moves it to deferred so it stays parked for later."
            },
            style = ChatLabelTextStyle,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
          )
        }
      },
      confirmButton = {
        TextButton(
          onClick = {
            onCreateEntry(mode, createTitle, createDetail)
            if (!state.createRunning) {
              createDialogVisible = false
            }
          },
          enabled = !state.createRunning && createTitle.trim().isNotBlank(),
        ) {
          Text(if (mode == BacklogCreateMode.PIS) "Create" else "Save")
        }
      },
      dismissButton = {
        TextButton(
          onClick = { createDialogVisible = false },
          enabled = !state.createRunning,
        ) {
          Text("Close")
        }
      },
    )
  }
}

@Composable
private fun BacklogStatusFilter(
  options: List<BacklogStatusFilterOption>,
  hiddenStatuses: Set<String>,
  onStatusVisibilityChanged: (String, Boolean) -> Unit,
) {
  var expanded by rememberSaveable { mutableStateOf(false) }
  val hiddenSummary = remember(hiddenStatuses) { buildBacklogStatusFilterSummary(hiddenStatuses) }
  Column(verticalArrangement = Arrangement.spacedBy(CompactTightGap)) {
    Text("Status filter", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
    androidx.compose.material3.Card(
      modifier = Modifier.fillMaxWidth(),
      shape = RoundedCornerShape(16.dp),
    ) {
      Column(
        modifier = Modifier
          .fillMaxWidth()
          .padding(CompactCardPadding),
        verticalArrangement = Arrangement.spacedBy(CompactTightGap),
      ) {
        Row(
          modifier = Modifier.fillMaxWidth(),
          horizontalArrangement = Arrangement.SpaceBetween,
          verticalAlignment = Alignment.CenterVertically,
        ) {
          Text(
            text = hiddenSummary,
            style = ChatLabelTextStyle,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.weight(1f),
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
          )
          SmallButton(if (expanded) "Close" else "Filter", onClick = { expanded = !expanded })
        }
        DropdownMenu(
          expanded = expanded,
          onDismissRequest = { expanded = false },
          modifier = Modifier.fillMaxWidth(0.94f),
        ) {
          if (options.isEmpty()) {
            DropdownMenuItem(
              text = { Text("No statuses found") },
              onClick = { expanded = false },
            )
          } else {
            DropdownMenuItem(
              text = { Text("Show all statuses") },
              onClick = {
                options.forEach { option ->
                  if (!option.visible) {
                    onStatusVisibilityChanged(option.status, true)
                  }
                }
              },
            )
            options.forEach { option ->
              DropdownMenuItem(
                text = {
                  Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                  ) {
                    Checkbox(
                      checked = option.visible,
                      onCheckedChange = { checked ->
                        onStatusVisibilityChanged(option.status, checked)
                      },
                    )
                    Text(
                      text = "${humanizeLedgerStatus(option.status)} (${option.count})",
                      maxLines = 1,
                      overflow = TextOverflow.Ellipsis,
                    )
                  }
                },
                onClick = {
                  onStatusVisibilityChanged(option.status, !option.visible)
                },
              )
            }
          }
        }
      }
    }
  }
}

@Composable
private fun ExecutiveHeroCard(
  state: BacklogUiState,
  cockpit: MobileCockpitPayload?,
  onPrimaryAction: () -> Unit,
) {
  val hero = buildExecutiveHero(cockpit = cockpit, state = state)
  val accent = hero.color
  val canAct = canLaunchExecutiveAction(state)
  AdminCard(title = "Executive surface") {
    Box(
      modifier = Modifier
        .fillMaxWidth()
        .background(
          brush = Brush.verticalGradient(
            colors = listOf(Color(0xFF171C22), Color(0xFF101419)),
          ),
          shape = RoundedCornerShape(22.dp),
        )
        .border(width = 1.dp, color = accent.copy(alpha = 0.4f), shape = RoundedCornerShape(22.dp))
        .padding(horizontal = 16.dp, vertical = 16.dp),
    ) {
      Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(hero.eyebrow, style = ChatLabelTextStyle, color = accent, fontWeight = FontWeight.SemiBold)
        Text(hero.title, style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.onSurface, fontWeight = FontWeight.SemiBold)
        Text(hero.reason, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap), verticalAlignment = Alignment.CenterVertically) {
          SmallButton(hero.cta, onPrimaryAction, enabled = canAct && !state.campaignLaunching)
          if (state.campaignLaunching) {
            Text("Applying...", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.primary)
          } else if (!canAct) {
            Text("Select a PIS first", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
          }
        }
      }
    }
  }
}

@Composable
private fun ExecutiveSignalStrip(
  cockpit: MobileCockpitPayload?,
  onOpenDetail: (CockpitDetailModel) -> Unit,
) {
  Row(
    modifier = Modifier
      .fillMaxWidth()
      .horizontalScroll(rememberScrollState()),
    horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
  ) {
    ExecutiveSignalCard(
      icon = { Icon(Icons.Filled.Hub, contentDescription = null, tint = MaterialTheme.colorScheme.primary) },
      title = "Campaigns",
      value = cockpit?.campaigns?.latest?.launchOutcome?.ifBlank { "Idle" } ?: "Idle",
      summary = cockpit?.campaigns?.latest?.campaignId?.ifBlank { "No active campaign" } ?: "No active campaign",
      color = MaterialTheme.colorScheme.primary,
      onClick = { onOpenDetail(buildCampaignDetailModel(cockpit)) },
    )
    ExecutiveSignalCard(
      icon = { Icon(Icons.Filled.Refresh, contentDescription = null, tint = executiveLivenessColor(cockpit)) },
      title = "Liveness",
      value = buildDerivedLiveness(cockpit),
      summary = buildExecutiveLivenessReason(cockpit),
      color = executiveLivenessColor(cockpit),
      onClick = { onOpenDetail(buildSystemTruthDetailModel(BacklogUiState(cockpit = cockpit))) },
    )
    ExecutiveSignalCard(
      icon = { Icon(Icons.Filled.Description, contentDescription = null, tint = MaterialTheme.colorScheme.tertiary) },
      title = "Economics",
      value = cockpit?.economics?.status?.ifBlank { "Unknown" } ?: "Unknown",
      summary = buildEconomicsSummary(cockpit?.economics?.totalTokens ?: 0, cockpit?.economics?.wasteWarning.orEmpty()),
      color = MaterialTheme.colorScheme.tertiary,
      onClick = { onOpenDetail(buildEconomicsDetailModel(cockpit)) },
    )
  }
}

@Composable
private fun ExecutiveSignalCard(
  icon: @Composable () -> Unit,
  title: String,
  value: String,
  summary: String,
  color: Color,
  onClick: () -> Unit,
) {
  Column(
    modifier = Modifier
      .width(176.dp)
      .background(Color(0xFF12161B), RoundedCornerShape(20.dp))
      .border(1.dp, color.copy(alpha = 0.34f), RoundedCornerShape(20.dp))
      .clickable(onClick = onClick)
      .padding(14.dp),
    verticalArrangement = Arrangement.spacedBy(8.dp),
  ) {
    icon()
    Text(title, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Text(value, style = MaterialTheme.typography.titleMedium, color = color, fontWeight = FontWeight.SemiBold)
    Text(summary, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurface, maxLines = 3, overflow = TextOverflow.Ellipsis)
  }
}

@Composable
private fun ExecutivePager(
  state: BacklogUiState,
  cockpit: MobileCockpitPayload?,
  pagerState: androidx.compose.foundation.pager.PagerState,
  onOpenDetail: (CockpitDetailModel) -> Unit,
) {
  Column(verticalArrangement = Arrangement.spacedBy(CompactTightGap)) {
    Text("Operational snapshots", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
    HorizontalPager(state = pagerState, beyondViewportPageCount = 1) { page ->
      when (CockpitPagerPage.entries[page]) {
        CockpitPagerPage.PIS -> ExecutivePagerCard(
          title = "Active PISes",
          lines = buildExecutivePisLines(state),
          icon = { Icon(Icons.Filled.Psychology, contentDescription = null, tint = MaterialTheme.colorScheme.primary) },
          onOpen = { onOpenDetail(buildSelectedPisDetailModel(state)) },
        )
        CockpitPagerPage.RUNS -> ExecutivePagerCard(
          title = "Active runs",
          lines = buildExecutiveRunLines(cockpit),
          icon = { Icon(Icons.Filled.PlayArrow, contentDescription = null, tint = executiveLivenessColor(cockpit)) },
          onOpen = { onOpenDetail(buildSystemTruthDetailModel(state)) },
        )
        CockpitPagerPage.ARTIFACTS -> ExecutivePagerCard(
          title = "Recent artifacts",
          lines = buildExecutiveArtifactLines(cockpit),
          icon = { Icon(Icons.Filled.Archive, contentDescription = null, tint = MaterialTheme.colorScheme.tertiary) },
          onOpen = { onOpenDetail(buildArtifactDetailModel(cockpit)) },
        )
      }
    }
  }
}

@Composable
private fun ExecutivePagerCard(
  title: String,
  lines: List<String>,
  icon: @Composable () -> Unit,
  onOpen: () -> Unit,
) {
  Column(
    modifier = Modifier
      .fillMaxWidth()
      .background(Color(0xFF12161B), RoundedCornerShape(22.dp))
      .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.24f), RoundedCornerShape(22.dp))
      .clickable(onClick = onOpen)
      .padding(horizontal = 16.dp, vertical = 14.dp),
    verticalArrangement = Arrangement.spacedBy(8.dp),
  ) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
      icon()
      Text(title, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onSurface, fontWeight = FontWeight.SemiBold)
    }
    lines.take(3).forEach { line ->
      Text(line, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
  }
}

@Composable
private fun OperationalDepthHeader(
  onOpenSelectedPis: () -> Unit,
  onOpenSystemTruth: () -> Unit,
) {
  AdminCard(title = "Operational depth") {
    Text(
      "Beautiful at first glance, brutally useful on dig-in.",
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
      AssistChip(onClick = onOpenSelectedPis, label = { Text("Selected PIS") }, leadingIcon = {
        Icon(Icons.Filled.Psychology, contentDescription = null)
      })
      AssistChip(onClick = onOpenSystemTruth, label = { Text("System truth") }, leadingIcon = {
        Icon(Icons.Filled.ContentCopy, contentDescription = null)
      })
    }
  }
}

@Composable
private fun OperationalDepthSheet(
  model: CockpitDetailModel,
  selectedSection: CockpitDepthSection,
  onSectionSelected: (CockpitDepthSection) -> Unit,
  onDismiss: () -> Unit,
) {
  Column(
    modifier = Modifier
      .fillMaxWidth()
      .padding(horizontal = 16.dp, vertical = 8.dp),
    verticalArrangement = Arrangement.spacedBy(12.dp),
  ) {
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
      Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(model.title, style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.onSurface, fontWeight = FontWeight.SemiBold)
        Text(model.subtitle, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
      }
      TextButton(onClick = onDismiss) { Text("Close") }
    }
    Row(
      modifier = Modifier
        .fillMaxWidth()
        .horizontalScroll(rememberScrollState()),
      horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
    ) {
      CockpitDepthSection.entries.forEach { section ->
        FilterChip(
          selected = section == selectedSection,
          onClick = { onSectionSelected(section) },
          label = { Text(section.label) },
        )
      }
    }
    HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.2f))
    when (selectedSection) {
      CockpitDepthSection.SUMMARY -> DepthTextBlock("Summary", model.summary)
      CockpitDepthSection.EVIDENCE -> DepthTextBlock("Evidence", model.evidence)
      CockpitDepthSection.LOGS -> DepthTextBlock("Logs", model.logs)
      CockpitDepthSection.ARTIFACT -> DepthTextBlock("Artifact", model.artifact)
      CockpitDepthSection.CONFIG -> DepthTextBlock("Config / payload", model.config)
      CockpitDepthSection.ACTIONS -> DepthTextBlock("Actions", model.actions)
    }
  }
}

@Composable
private fun DepthTextBlock(
  title: String,
  text: String,
) {
  Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
    Text(title, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
    SelectionContainer {
      Column(
        modifier = Modifier
          .fillMaxWidth()
          .background(Color(0xFF0D1014), RoundedCornerShape(16.dp))
          .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.16f), RoundedCornerShape(16.dp))
          .padding(14.dp),
      ) {
        Text(
          text.ifBlank { "[no data]" },
          style = ChatLabelTextStyle,
          color = MaterialTheme.colorScheme.onSurface,
        )
      }
    }
  }
}

@Composable
private fun BacklogExecutionPaneCard(
  items: List<LedgerItemSummary>,
  selectedUid: String,
  stagedExecutionUids: Set<String>,
  totalCount: Int,
  loading: Boolean,
  updating: Boolean,
  onSelect: (String) -> Unit,
  onStageChanged: (String, Boolean) -> Unit,
  onPrioritize: (String) -> Unit,
) {
  AdminCard(title = "Execution pane") {
    val visibleCount = minOf(totalCount, BACKLOG_EXECUTION_PANE_VIEWPORT_COUNT)
    val summary = buildExecutionPaneSummary(totalCount = totalCount, visibleCount = visibleCount)
    if (loading && items.isEmpty()) {
      Text("Loading queue...", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.primary)
    } else if (items.isEmpty()) {
      Text("No visible PIS items are queued.", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
    } else {
      Text(summary, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
      Text(
        buildExecutionStageSummary(stagedExecutionUids.size),
        style = ChatLabelTextStyle,
        color = MaterialTheme.colorScheme.primary,
      )
      if (updating) {
        Text("Updating queue...", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.primary)
      }
      Column(
        modifier = Modifier
          .fillMaxWidth()
          .heightIn(max = BACKLOG_EXECUTION_PANE_MAX_HEIGHT)
          .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(CompactTightGap),
      ) {
        items.forEachIndexed { index, item ->
          BacklogExecutionPaneRow(
            item = item,
            rank = index + 1,
            selected = item.uid == selectedUid,
            staged = item.uid in stagedExecutionUids,
            updating = updating,
            onOpen = { onSelect(item.uid) },
            onStageChanged = { checked -> onStageChanged(item.uid, checked) },
            onPrioritize = { onPrioritize(item.uid) },
          )
        }
      }
    }
  }
}

@Composable
private fun BacklogExecutionPaneRow(
  item: LedgerItemSummary,
  rank: Int,
  selected: Boolean,
  staged: Boolean,
  updating: Boolean,
  onOpen: () -> Unit,
  onStageChanged: (Boolean) -> Unit,
  onPrioritize: () -> Unit,
) {
  val accent = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline.copy(alpha = 0.3f)
  val descriptor = item.title.trim().ifBlank { item.slug.trim().ifBlank { item.uid } }
  val actionEnabled = !updating && rank != 1
  Column(
    modifier = Modifier
      .fillMaxWidth()
      .clickable(onClick = onOpen)
      .border(width = 1.dp, color = accent, shape = RoundedCornerShape(16.dp))
      .padding(horizontal = 12.dp, vertical = 10.dp),
    verticalArrangement = Arrangement.spacedBy(CompactTightGap),
  ) {
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.SpaceBetween,
      verticalAlignment = Alignment.CenterVertically,
    ) {
      Row(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
      ) {
        Checkbox(
          modifier = Modifier.size(20.dp),
          checked = staged,
          onCheckedChange = onStageChanged,
        )
        Box(
          modifier = Modifier
            .background(
              color = if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.14f) else MaterialTheme.colorScheme.surfaceVariant,
              shape = RoundedCornerShape(999.dp),
            )
            .padding(horizontal = 9.dp, vertical = 4.dp),
        ) {
          Text(
            text = rank.toString(),
            style = ChatLabelTextStyle,
            fontWeight = FontWeight.SemiBold,
            color = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
          )
        }
        Text(
          text = mobileBacklogStatusLabel(item.status),
          style = ChatLabelTextStyle,
          color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
      }
      SmallButton(
        "To top",
        onClick = onPrioritize,
        enabled = actionEnabled,
      )
    }
    Text(
      text = descriptor,
      style = ChatLabelTextStyle,
      fontWeight = FontWeight.SemiBold,
      color = MaterialTheme.colorScheme.onSurface,
      maxLines = 2,
      overflow = TextOverflow.Ellipsis,
    )
    if (staged) {
      Text(
        text = "Staged for WF-01",
        style = ChatLabelTextStyle,
        color = MaterialTheme.colorScheme.primary,
      )
    }
    Text(
      text = item.uid,
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurfaceVariant,
      maxLines = 1,
      overflow = TextOverflow.Ellipsis,
    )
  }
}

@Composable
private fun BacklogWorkflowCard(
  state: BacklogUiState,
  onInstructionsChanged: (String) -> Unit,
  onRefresh: () -> Unit,
  onLaunch: () -> Unit,
  onCancel: () -> Unit,
) {
  val selectedUid = state.selectedUid.ifBlank { state.selectedItem?.uid.orEmpty() }
  val runtime = state.workflowRuntime
  val workflowHandle = state.workflowHandle
  val workflowId = runtime?.workflowId?.ifBlank { workflowHandle?.workflowId.orEmpty() }
    ?: workflowHandle?.workflowId.orEmpty()
  val workflowStatus = runtime?.status?.ifBlank { workflowHandle?.status.orEmpty() }
    ?: workflowHandle?.status.orEmpty()
  val phaseLabel = runtime?.phaseLabel?.ifBlank { workflowHandle?.phaseLabel.orEmpty() }
    ?: workflowHandle?.phaseLabel.orEmpty()
  val canResumeExisting = workflowId.isNotBlank() &&
    !isWorkflowTerminalStatus(workflowStatus) &&
    !workflowHasProgress(runtime)
  val canCancel = workflowId.isNotBlank() && !isWorkflowTerminalStatus(workflowStatus)
  val selectedStaged = selectedUid.isNotBlank() && selectedUid in state.stagedExecutionUids
  val canLaunch = selectedUid.isNotBlank() &&
    !state.workflowActionRunning &&
    (canResumeExisting || (selectedStaged && !canCancel))
  val launchLabel = if (canResumeExisting) "Resume WF-01" else "Launch WF-01"
  val headline = buildWorkflowHeadline(workflowStatus = workflowStatus, phaseLabel = phaseLabel)
  val iterationValue = buildWorkflowIterationValue(runtime)
  val launchDirective = remember(state.workflowInstructionsInput) {
    parseWorkflowLaunchDirective(state.workflowInstructionsInput)
  }
  val activityModel = remember(
    state.selectedUid,
    state.workflowLoading,
    state.workflowHandle,
    state.workflowRuntime,
    state.workflowActivity,
  ) {
    buildBacklogWorkflowActivityModel(state)
  }

  AdminCard(title = "Workflow") {
    Text(
      text = buildWorkflowExecutionTargetLabel(
        selectedUid = selectedUid,
        stagedExecutionUids = state.stagedExecutionUids,
      ),
      style = ChatLabelTextStyle,
      fontWeight = FontWeight.SemiBold,
      color = MaterialTheme.colorScheme.primary,
    )
    AdminTextField(
      value = state.workflowInstructionsInput,
      onValueChange = onInstructionsChanged,
      label = "Additional instructions",
      modifier = Modifier.fillMaxWidth(),
      minLines = 3,
    )
    Text(
      text = if (launchDirective.maxIterations != null) {
        "Leading digit sets WF-01 iterations. Current value: ${launchDirective.maxIterations}. Remaining text is sent as execution guidance."
      } else {
        "Optional. Passed to WF-01 as extra execution guidance, same idea as the O tab. Prefix 1-$WORKFLOW_MAX_ITERATIONS_MAX to set iterations."
      },
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
      verticalAlignment = Alignment.CenterVertically,
    ) {
      SmallButton("Refresh", onRefresh, enabled = selectedUid.isNotBlank() && !state.workflowActionRunning)
      SmallButton(launchLabel, onLaunch, enabled = canLaunch)
      SmallButton("Cancel", onCancel, enabled = canCancel && !state.workflowActionRunning)
      when {
        state.workflowActionRunning -> Text("Applying...", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.primary)
        state.workflowLoading -> Text("Syncing...", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.primary)
        headline.isNotBlank() -> Text(headline, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
      }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
      StatusPill(
        label = "Workflow",
        value = workflowStatus.ifBlank { "Idle" },
        accent = backlogWorkflowStatusColor(
          workflowStatus = workflowStatus,
          primary = MaterialTheme.colorScheme.primary,
          secondary = MaterialTheme.colorScheme.onSurfaceVariant,
          error = MaterialTheme.colorScheme.error,
        ),
      )
      StatusPill(
        label = "Iteration",
        value = iterationValue,
        accent = MaterialTheme.colorScheme.tertiary,
      )
    }
    WorkflowStageTracker(runtime = runtime, workflowHandle = workflowHandle)
    BacklogWorkflowActivityPanel(model = activityModel)
  }
}

@Composable
private fun BacklogCampaignQueueCard(
  state: BacklogUiState,
  cockpit: MobileCockpitPayload?,
  onLaunchCampaign: () -> Unit,
) {
  val selectionSummary = buildCockpitSelectionSummary(state.selectedUid, state.stagedExecutionUids)
  val latestCampaign = cockpit?.campaigns?.latest
  val queue = cockpit?.executionQueue
  AdminCard(title = "Campaign and queue") {
    Text(selectionSummary, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
      verticalAlignment = Alignment.CenterVertically,
    ) {
      SmallButton(
        "Launch selected campaign",
        onLaunchCampaign,
        enabled = !state.campaignLaunching && buildCockpitActionSelection(state.selectedUid, state.stagedExecutionUids).isNotEmpty(),
      )
      when {
        state.campaignLaunching -> Text("Launching...", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.primary)
        state.cockpitLoading -> Text("Refreshing...", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.primary)
      }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
      StatusPill(
        label = "Campaign",
        value = latestCampaign?.launchOutcome?.ifBlank { "Idle" } ?: "Idle",
        accent = MaterialTheme.colorScheme.primary,
      )
      StatusPill(
        label = "Queue",
        value = queue?.status?.ifBlank { "Unknown" } ?: "Unknown",
        accent = MaterialTheme.colorScheme.tertiary,
      )
    }
    Text(
      text = buildCampaignQueueDetail(latestCampaign?.campaignId.orEmpty(), queue?.runId.orEmpty(), queue?.currentUid.orEmpty(), queue?.totalCount ?: 0),
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurface,
    )
  }
}

@Composable
private fun BacklogShellStatusCard(
  state: BacklogUiState,
  cockpit: MobileCockpitPayload?,
  onExportHandoff: () -> Unit,
  onCreateArchiveSnapshot: () -> Unit,
) {
  val shell = cockpit?.shell
  AdminCard(title = "Shell status") {
    Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
      StatusPill(
        label = "Trust",
        value = shell?.status?.ifBlank { "Unknown" } ?: "Unknown",
        accent = when (shell?.status?.trim()?.lowercase()) {
          "low_trust", "blocked", "failed" -> MaterialTheme.colorScheme.error
          else -> MaterialTheme.colorScheme.primary
        },
      )
      StatusPill(
        label = "Executor",
        value = buildExecutorSummary(shell?.campaignId.orEmpty(), shell?.workflowId.orEmpty(), shell?.queueRunId.orEmpty()),
        accent = MaterialTheme.colorScheme.tertiary,
      )
    }
    Text(
      text = buildShellWarningsSummary(shell?.warnings.orEmpty(), shell?.fallbackActions.orEmpty()),
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurface,
    )
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
      verticalAlignment = Alignment.CenterVertically,
    ) {
      SmallButton("Export handoff", onExportHandoff, enabled = state.selectedUid.isNotBlank() && !state.handoffExportRunning)
      SmallButton("Create archive snapshot", onCreateArchiveSnapshot, enabled = !state.archiveCreateRunning)
      if (state.handoffExportRunning || state.archiveCreateRunning) {
        Text("Applying...", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.primary)
      }
    }
  }
}

@Composable
private fun BacklogSignalsAndArchivesCard(
  state: BacklogUiState,
  cockpit: MobileCockpitPayload?,
) {
  val artifacts = cockpit?.artifacts
  val runTriage = cockpit?.runTriage
  val economics = cockpit?.economics
  val archives = cockpit?.archiveSnapshots
  AdminCard(title = "Signals and archives") {
    Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
      StatusPill("Artifacts", artifacts?.status?.ifBlank { "Unknown" } ?: "Unknown", MaterialTheme.colorScheme.primary)
      StatusPill("Run", runTriage?.status?.ifBlank { "Unknown" } ?: "Unknown", MaterialTheme.colorScheme.tertiary)
    }
    Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
      StatusPill("Economics", economics?.status?.ifBlank { "Unknown" } ?: "Unknown", MaterialTheme.colorScheme.primary)
      StatusPill("Archives", archives?.status?.ifBlank { "Unknown" } ?: "Unknown", MaterialTheme.colorScheme.tertiary)
    }
    Text(
      buildArtifactSummary(artifacts?.latestHandoff?.name.orEmpty(), artifacts?.latestHandoff?.previewText.orEmpty()),
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurface,
    )
    Text(
      buildRunTriageSummary(runTriage?.summary.orEmpty(), runTriage?.failureClass.orEmpty(), runTriage?.actions.orEmpty()),
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurface,
    )
    Text(
      buildEconomicsSummary(economics?.totalTokens ?: 0, economics?.wasteWarning.orEmpty()),
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurface,
    )
    Text(
      buildArchiveSummary(archives?.latestRepoSnapshotId.orEmpty(), archives?.runArchiveCount ?: 0),
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurface,
    )
  }
}

@Composable
private fun BacklogChronicleCard(
  cockpit: MobileCockpitPayload?,
  loading: Boolean,
) {
  val chronicle = cockpit?.chronicle
  AdminCard(title = "Chronicle") {
    when {
      loading -> Text("Refreshing chronicle...", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.primary)
      chronicle == null || chronicle.entries.isEmpty() ->
        Text("No chronicle entries yet.", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
      else -> chronicle.entries.take(8).forEach { entry ->
        Column(
          modifier = Modifier
            .fillMaxWidth()
            .border(
              width = 1.dp,
              color = MaterialTheme.colorScheme.outline.copy(alpha = 0.22f),
              shape = RoundedCornerShape(16.dp),
            )
            .padding(horizontal = 12.dp, vertical = 10.dp),
          verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
          Text(
            text = "${humanizeLedgerStatus(entry.kind)}${entry.utc.takeIf { it.isNotBlank() }?.let { "  $it" } ?: ""}",
            style = ChatLabelTextStyle,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.primary,
          )
          Text(entry.summary, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurface)
        }
      }
    }
  }
}

@Composable
private fun WorkflowStageTracker(
  runtime: WorkflowStatus?,
  workflowHandle: OrchestrationWorkflowSummary?,
) {
  val roleStates = remember(runtime, workflowHandle) { buildWorkflowRoleStates(runtime, workflowHandle) }
  Box(
    modifier = Modifier
      .fillMaxWidth()
      .background(
        brush = Brush.verticalGradient(
          colors = listOf(BACKLOG_TRACKER_SURFACE_TOP, BACKLOG_TRACKER_SURFACE_BOTTOM),
        ),
        shape = RoundedCornerShape(24.dp),
      )
      .border(
        width = 1.dp,
        color = MaterialTheme.colorScheme.outline.copy(alpha = 0.28f),
        shape = RoundedCornerShape(24.dp),
      )
      .padding(horizontal = 12.dp, vertical = 12.dp),
  ) {
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.spacedBy(10.dp),
      verticalAlignment = Alignment.Top,
    ) {
      roleStates.forEach { roleState ->
        WorkflowStageSegment(
          roleState = roleState,
          modifier = Modifier.weight(1f),
        )
      }
    }
  }
}

@Composable
private fun WorkflowStageSegment(
  roleState: BacklogWorkflowRoleState,
  modifier: Modifier = Modifier,
) {
  val stageColors = when {
    roleState.failed -> listOf(Color(0xFF7D1E1E), Color(0xFFC64747))
    roleState.active -> listOf(roleState.color.copy(alpha = 0.96f), roleState.color.copy(alpha = 0.58f))
    roleState.completed -> listOf(
      Color.White.copy(alpha = 0.96f),
      roleState.color.copy(alpha = 0.18f),
    )
    else -> listOf(BACKLOG_STAGE_PENDING.copy(alpha = 0.84f), BACKLOG_STAGE_PENDING.copy(alpha = 0.46f))
  }
  val borderColor = when {
    roleState.failed -> Color(0xFFD16262)
    roleState.active -> roleState.color.copy(alpha = 0.96f)
    roleState.completed -> roleState.color.copy(alpha = 0.42f)
    else -> BACKLOG_TRACKER_RAIL.copy(alpha = 0.36f)
  }
  val statusColor = when {
    roleState.failed -> Color(0xFFFFD2D2)
    roleState.active -> Color.White
    roleState.completed -> Color(0xFF2D3438)
    else -> Color(0xFF4E5558)
  }
  Box(
    modifier = modifier
      .background(
        brush = Brush.verticalGradient(stageColors),
        shape = RoundedCornerShape(20.dp),
      )
      .border(width = 1.dp, color = borderColor, shape = RoundedCornerShape(20.dp))
      .padding(horizontal = 10.dp, vertical = 10.dp),
  ) {
    Box(
      modifier = Modifier
        .matchParentSize()
        .background(
          brush = Brush.linearGradient(
            colors = listOf(
              Color.White.copy(alpha = if (roleState.active) 0.28f else 0.08f),
              Color.Transparent,
              Color.White.copy(alpha = if (roleState.active) 0.18f else 0.04f),
            ),
            start = Offset(0f, 0f),
            end = Offset(220f, 140f),
          ),
          shape = RoundedCornerShape(20.dp),
        ),
    )
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
      Text(
        text = roleState.label,
        style = ChatLabelTextStyle,
        color = statusColor.copy(alpha = 0.92f),
        fontWeight = FontWeight.SemiBold,
      )
      Text(
        text = roleState.title,
        style = ChatLabelTextStyle,
        color = statusColor,
        fontWeight = FontWeight.SemiBold,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
      )
      Text(
        text = roleState.statusText,
        style = ChatLabelTextStyle,
        color = statusColor.copy(alpha = 0.84f),
        maxLines = 2,
        overflow = TextOverflow.Ellipsis,
      )
    }
  }
}

@Composable
private fun BacklogWorkflowActivityPanel(
  model: BacklogWorkflowActivityModel,
) {
  Column(verticalArrangement = Arrangement.spacedBy(CompactFieldGap)) {
    androidx.compose.material3.Card(
      modifier = Modifier.fillMaxWidth(),
      shape = RoundedCornerShape(18.dp),
    ) {
      Column(
        modifier = Modifier
          .fillMaxWidth()
          .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
      ) {
        Text(
          text = "Workflow activity",
          style = ChatLabelTextStyle,
          color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (model.live) {
          WorkflowLiveStatusStrip(
            headline = model.headline,
            detail = model.detail,
          )
        } else {
          Column(
            modifier = Modifier
              .fillMaxWidth()
              .adminFieldSurface()
              .padding(horizontal = 14.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
          ) {
            Text(
              text = model.headline,
              style = ChatLabelTextStyle,
              fontWeight = FontWeight.SemiBold,
              color = MaterialTheme.colorScheme.primary,
            )
            Text(
              text = model.detail,
              style = ChatLabelTextStyle,
              color = MaterialTheme.colorScheme.onSurface,
            )
          }
        }
        model.entries.forEach { entry ->
          WorkflowActivityEntryCard(entry = entry)
        }
      }
    }
  }
}

@Composable
private fun WorkflowLiveStatusStrip(
  headline: String,
  detail: String,
) {
  val colorScheme = MaterialTheme.colorScheme
  val transition = rememberInfiniteTransition(label = "backlog_workflow_live")
  val shimmerShift by transition.animateFloat(
    initialValue = -720f,
    targetValue = 720f,
    animationSpec = infiniteRepeatable(
      animation = tween(durationMillis = 2100, easing = LinearEasing),
      repeatMode = RepeatMode.Restart,
    ),
    label = "backlog_workflow_live_shimmer",
  )
  val shimmerBrush = remember(shimmerShift, colorScheme.primary) {
    Brush.linearGradient(
      colors = listOf(
        colorScheme.primary.copy(alpha = 0.38f),
        colorScheme.primary.copy(alpha = 0.98f),
        colorScheme.primary.copy(alpha = 0.38f),
      ),
      start = Offset(shimmerShift - 280f, 0f),
      end = Offset(shimmerShift + 280f, 0f),
    )
  }
  val elapsedMs by produceState(initialValue = 0L, key1 = headline, key2 = detail) {
    while (true) {
      value += 1000L
      delay(1000L)
    }
  }
  val wave = ".".repeat(((elapsedMs / 500L) % 4L).toInt())
  Column(
    modifier = Modifier
      .fillMaxWidth()
      .adminFieldSurface()
      .padding(horizontal = 14.dp, vertical = 12.dp),
    verticalArrangement = Arrangement.spacedBy(4.dp),
  ) {
    Text(
      text = "$headline$wave",
      style = ChatLabelTextStyle.copy(brush = shimmerBrush),
      fontWeight = FontWeight.SemiBold,
    )
    Text(
      text = detail,
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurface,
    )
  }
}

@Composable
private fun WorkflowActivityEntryCard(
  entry: BacklogWorkflowEntry,
) {
  val accent = when (entry.tone) {
    BacklogWorkflowEntryTone.NEUTRAL -> MaterialTheme.colorScheme.outline.copy(alpha = 0.42f)
    BacklogWorkflowEntryTone.ACTIVE -> MaterialTheme.colorScheme.primary.copy(alpha = 0.42f)
    BacklogWorkflowEntryTone.WARNING -> MaterialTheme.colorScheme.error.copy(alpha = 0.42f)
  }
  val badgeBackground = when (entry.tone) {
    BacklogWorkflowEntryTone.NEUTRAL -> MaterialTheme.colorScheme.surfaceVariant
    BacklogWorkflowEntryTone.ACTIVE -> MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)
    BacklogWorkflowEntryTone.WARNING -> MaterialTheme.colorScheme.error.copy(alpha = 0.12f)
  }
  val badgeColor = when (entry.tone) {
    BacklogWorkflowEntryTone.NEUTRAL -> MaterialTheme.colorScheme.onSurfaceVariant
    BacklogWorkflowEntryTone.ACTIVE -> MaterialTheme.colorScheme.primary
    BacklogWorkflowEntryTone.WARNING -> MaterialTheme.colorScheme.error
  }
  Column(
    modifier = Modifier
      .fillMaxWidth()
      .border(width = 1.dp, color = accent, shape = RoundedCornerShape(16.dp))
      .padding(horizontal = 14.dp, vertical = 12.dp),
    verticalArrangement = Arrangement.spacedBy(8.dp),
  ) {
    Row(
      horizontalArrangement = Arrangement.spacedBy(8.dp),
      verticalAlignment = Alignment.CenterVertically,
    ) {
      Box(
        modifier = Modifier
          .background(color = badgeBackground, shape = RoundedCornerShape(999.dp))
          .padding(horizontal = 10.dp, vertical = 4.dp),
      ) {
        Text(
          text = entry.label,
          style = ChatLabelTextStyle,
          fontWeight = FontWeight.SemiBold,
          color = badgeColor,
        )
      }
      Text(
        text = entry.title,
        style = ChatLabelTextStyle,
        fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.onSurface,
      )
    }
    Text(
      text = entry.body,
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurface,
    )
  }
}

@Composable
private fun BacklogMarkdownCard(markdown: String) {
  val renderedMarkdown = markdown.ifBlank { "[no pis markdown]" }

  Column(verticalArrangement = Arrangement.spacedBy(CompactTightGap)) {
    androidx.compose.material3.Card(
      modifier = Modifier.fillMaxWidth(),
      shape = RoundedCornerShape(16.dp),
    ) {
      Column(
        modifier = Modifier
          .fillMaxWidth()
          .padding(CompactCardPadding),
        verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
      ) {
        Text(
          "Readable markdown view",
          style = ChatLabelTextStyle,
          color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Column(
          modifier = Modifier
            .fillMaxWidth()
            .adminFieldSurface()
            .padding(horizontal = 14.dp, vertical = 14.dp),
          verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
        ) {
          MarkdownMessage(
            text = renderedMarkdown,
            textColor = MaterialTheme.colorScheme.onSurface,
            onCopyCodeBlock = {},
          )
        }
      }
    }
  }
}

@Composable
private fun BacklogDropdown(
  items: List<LedgerItemSummary>,
  selectedUid: String,
  onSelect: (String) -> Unit,
) {
  var expanded by rememberSaveable { mutableStateOf(false) }
  val selectedLabel = remember(items, selectedUid) {
    items.firstOrNull { it.uid == selectedUid }?.let(::formatMobileBacklogOptionLabel) ?: "Select a PIS"
  }
  Column(verticalArrangement = Arrangement.spacedBy(CompactTightGap)) {
    Text("PIS selector", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
    androidx.compose.material3.Card(
      modifier = Modifier.fillMaxWidth(),
      shape = RoundedCornerShape(16.dp),
    ) {
      Column(modifier = Modifier.fillMaxWidth().padding(CompactCardPadding)) {
        Row(
          modifier = Modifier.fillMaxWidth(),
          horizontalArrangement = Arrangement.SpaceBetween,
          verticalAlignment = Alignment.CenterVertically,
        ) {
          Text(
            text = selectedLabel,
            style = ChatLabelTextStyle,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.weight(1f),
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
          )
          SmallButton(if (expanded) "Close" else "Choose", onClick = { expanded = !expanded })
        }
        DropdownMenu(
          expanded = expanded,
          onDismissRequest = { expanded = false },
          modifier = Modifier.fillMaxWidth(0.94f),
        ) {
          if (items.isEmpty()) {
            DropdownMenuItem(
                text = { Text("No visible items") },
              onClick = { expanded = false },
            )
          } else {
            items.forEach { item ->
              DropdownMenuItem(
                text = {
                  Text(
                    formatMobileBacklogOptionLabel(item),
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                  )
                },
                onClick = {
                  expanded = false
                  onSelect(item.uid)
                },
              )
            }
          }
        }
      }
    }
  }
}

@Composable
private fun RowScope.BacklogStatusDropdown(
  currentStatus: String,
  updating: Boolean,
  enabled: Boolean,
  onSelect: (String) -> Unit,
) {
  var expanded by rememberSaveable(currentStatus, updating) { mutableStateOf(false) }
  val options = remember(currentStatus) { editableBacklogStatusOptions(currentStatus) }
  Box(
    modifier = Modifier
      .weight(1f)
      .padding(top = 2.dp)
      .adminFieldSurface()
      .padding(horizontal = 10.dp, vertical = 8.dp),
  ) {
    Column(verticalArrangement = Arrangement.spacedBy(CompactTightGap)) {
      Text("Status", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
      Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
      ) {
        Text(
          if (updating) "Saving..." else humanizeLedgerStatus(currentStatus),
          style = ChatLabelTextStyle,
          fontWeight = FontWeight.SemiBold,
          color = MaterialTheme.colorScheme.primary,
          modifier = Modifier.weight(1f),
          maxLines = 2,
          overflow = TextOverflow.Ellipsis,
        )
        SmallButton(
          if (expanded) "Close" else "Change",
          enabled = enabled && !updating,
          onClick = { expanded = !expanded },
        )
      }
      DropdownMenu(
        expanded = expanded,
        onDismissRequest = { expanded = false },
        modifier = Modifier.fillMaxWidth(0.94f),
      ) {
        options.forEach { status ->
          DropdownMenuItem(
            text = { Text(humanizeLedgerStatus(status)) },
            onClick = {
              expanded = false
              onSelect(status)
            },
          )
        }
      }
    }
  }
}

@Composable
private fun RowScope.StatusPill(
  label: String,
  value: String,
  accent: Color,
) {
  Box(
    modifier = Modifier
      .weight(1f)
      .padding(top = 2.dp)
      .adminFieldSurface()
      .padding(horizontal = 10.dp, vertical = 8.dp),
  ) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
      Text(label, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
      Text(value, style = ChatLabelTextStyle, fontWeight = FontWeight.SemiBold, color = accent)
    }
  }
}

internal data class BacklogStatusFilterOption(
  val status: String,
  val count: Int,
  val visible: Boolean,
)

internal fun isMobileBacklogCandidate(
  item: LedgerItemSummary,
  hiddenStatuses: Set<String> = MOBILE_BACKLOG_DEFAULT_HIDDEN_STATUSES,
): Boolean {
  val status = item.status.trim().lowercase()
  return item.uid.startsWith("PIS-") &&
    status !in normalizeBacklogHiddenStatuses(hiddenStatuses)
}

internal fun filterMobileBacklogItems(
  items: List<LedgerItemSummary>,
  hiddenStatuses: Set<String>,
): List<LedgerItemSummary> {
  val normalizedHiddenStatuses = normalizeBacklogHiddenStatuses(hiddenStatuses)
  return items.filter { isMobileBacklogCandidate(it, normalizedHiddenStatuses) }
}

internal fun buildBacklogStatusFilterOptions(
  items: List<LedgerItemSummary>,
  hiddenStatuses: Set<String>,
): List<BacklogStatusFilterOption> {
  val normalizedHiddenStatuses = normalizeBacklogHiddenStatuses(hiddenStatuses)
  val counts = items
    .filter { it.uid.startsWith("PIS-") }
    .groupingBy { it.status.trim().lowercase() }
    .eachCount()
  val orderedStatuses = (counts.keys + normalizedHiddenStatuses)
    .filter { it.isNotBlank() }
    .distinct()
    .sortedWith(
      compareBy<String> { backlogStatusSortOrder(it) }
        .thenBy { humanizeLedgerStatus(it) },
    )
  return orderedStatuses.map { status ->
    BacklogStatusFilterOption(
      status = status,
      count = counts[status] ?: 0,
      visible = status !in normalizedHiddenStatuses,
    )
  }
}

internal fun buildBacklogStatusFilterSummary(hiddenStatuses: Set<String>): String {
  val normalizedHiddenStatuses = normalizeBacklogHiddenStatuses(hiddenStatuses)
  if (normalizedHiddenStatuses.isEmpty()) {
    return "All statuses visible"
  }
  return "Hidden: ${normalizedHiddenStatuses.map(::humanizeLedgerStatus).sorted().joinToString(", ")}"
}

internal fun buildBacklogVisibilityMessage(
  visibleCount: Int,
  totalCount: Int,
  hiddenStatuses: Set<String>,
): String {
  val normalizedHiddenStatuses = normalizeBacklogHiddenStatuses(hiddenStatuses)
  return when {
    totalCount <= 0 -> "No visible PIS items found."
    visibleCount <= 0 && normalizedHiddenStatuses.isNotEmpty() ->
      "No PIS items match the current status filter."
    normalizedHiddenStatuses.isEmpty() ->
      "$visibleCount visible PIS items"
    else ->
      "$visibleCount of $totalCount PIS items visible"
  }
}

internal fun formatMobileBacklogOptionLabel(item: LedgerItemSummary): String {
  val status = mobileBacklogStatusLabel(item.status)
  val descriptor = listOf(item.slug.trim(), item.title.trim())
    .filter { it.isNotBlank() }
    .distinct()
    .joinToString(" / ")
    .ifBlank { item.uid }
  return "$status | ${item.uid} | $descriptor"
}

internal fun buildExecutionPaneItems(
  items: List<LedgerItemSummary>,
): List<LedgerItemSummary> {
  return items
}

internal fun buildExecutionPaneSummary(
  totalCount: Int,
  visibleCount: Int,
): String {
  return when {
    totalCount <= 0 || visibleCount <= 0 -> "Nothing queued."
    totalCount <= visibleCount -> "Showing the full execution queue."
    else -> "Showing next $visibleCount of $totalCount queued PIS items."
  }
}

internal fun buildExecutionStageSummary(stagedCount: Int): String {
  return when (stagedCount) {
    0 -> "No PIS items staged for WF-01 yet."
    1 -> "1 PIS item staged for WF-01."
    else -> "$stagedCount PIS items staged for WF-01."
  }
}

internal fun buildCockpitActionSelection(
  selectedUid: String,
  stagedExecutionUids: Set<String>,
): List<String> {
  val staged = stagedExecutionUids.map { it.trim() }.filter { it.isNotBlank() }
  if (staged.isNotEmpty()) return staged
  val selected = selectedUid.trim()
  return if (selected.isBlank()) emptyList() else listOf(selected)
}

internal fun buildCockpitSelectionSummary(
  selectedUid: String,
  stagedExecutionUids: Set<String>,
): String {
  val selected = buildCockpitActionSelection(selectedUid, stagedExecutionUids)
  return when (selected.size) {
    0 -> "No PIS selected for campaign launch yet."
    1 -> "Campaign target: ${selected.first()}."
    else -> "Campaign targets: ${selected.size} staged PIS items."
  }
}

private fun buildCampaignLaunchMessage(result: com.eiel.chat.data.MobileCockpitCampaignLaunchResult): String {
  val campaign = result.campaign
  return when (campaign.launchOutcome.trim().lowercase()) {
    "started" -> "Campaign ${campaign.campaignId.ifBlank { "(pending id)" }} started."
    "rejected" -> "Campaign launch was rejected."
    else -> "Campaign status: ${campaign.launchOutcome.ifBlank { "unknown" }}."
  }
}

private fun buildHandoffExportMessage(result: MobileCockpitArtifactHandoffResult): String {
  return if (result.ok) {
    "Handoff exported: ${result.artifactPath.ifBlank { "artifact recorded" }}."
  } else {
    result.error.ifBlank { "Handoff export failed." }
  }
}

private fun buildArchiveCreateMessage(result: MobileCockpitArchiveCreateResult): String {
  return if (result.ok) {
    "Archive snapshot created: ${result.snapshotId.ifBlank { "snapshot ready" }}."
  } else {
    result.error.ifBlank { "Archive snapshot failed." }
  }
}

private fun buildCampaignQueueDetail(
  campaignId: String,
  runId: String,
  currentUid: String,
  totalCount: Int,
): String {
  val bits = buildList {
    campaignId.takeIf { it.isNotBlank() }?.let { add("Campaign $it") }
    runId.takeIf { it.isNotBlank() }?.let { add("Queue run $it") }
    currentUid.takeIf { it.isNotBlank() }?.let { add("Current $it") }
    if (totalCount > 0) add("$totalCount items in queue view")
  }
  return bits.ifEmpty { listOf("No active campaign or queue projection yet.") }.joinToString("  ")
}

private fun buildExecutorSummary(
  campaignId: String,
  workflowId: String,
  queueRunId: String,
): String {
  return listOf(campaignId, workflowId, queueRunId)
    .map { it.trim() }
    .firstOrNull { it.isNotBlank() }
    ?: "Unassigned"
}

private fun buildShellWarningsSummary(
  warnings: List<String>,
  fallbackActions: List<String>,
): String {
  val warningText = if (warnings.isEmpty()) {
    "No shell warnings."
  } else {
    "Warnings: ${warnings.joinToString(", ")}."
  }
  val fallbackText = if (fallbackActions.isEmpty()) {
    ""
  } else {
    " Fallbacks: ${fallbackActions.joinToString(", ")}."
  }
  return warningText + fallbackText
}

private fun buildArtifactSummary(
  handoffName: String,
  previewText: String,
): String {
  if (handoffName.isBlank()) return "No handoff artifact projected yet."
  val preview = compactWorkflowText(previewText, maxChars = 120)
  return if (preview.isBlank()) "Latest handoff: $handoffName." else "Latest handoff: $handoffName. $preview"
}

private fun buildContinuationSummary(continuation: MobileCockpitContinuation?): String {
  val row = continuation ?: return "No durable continuation state projected yet."
  val summary = row.summary.trim()
  if (summary.isNotBlank()) return summary
  val parts = buildList {
    row.status.takeIf { it.isNotBlank() }?.let { add("Continuation $it") }
    row.authoritativeSource.takeIf { it.isNotBlank() }?.let { add("source $it") }
    row.packetId.takeIf { it.isNotBlank() }?.let { add("packet ${it}:r${row.packetRevision}") }
    row.memoryStatus.takeIf { it.isNotBlank() && it != "none" }?.let { add("memory $it") }
    if (row.activeMemoryCount > 0) add("active memory ${row.activeMemoryCount}")
    if (row.withdrawnMemoryCount > 0) add("withdrawn memory ${row.withdrawnMemoryCount}")
    if (row.contradictedMemoryCount > 0) add("contradicted memory ${row.contradictedMemoryCount}")
  }
  return parts.joinToString(" · ").ifBlank { "No durable continuation state projected yet." }
}

private fun buildContinuationInspectionBlock(continuation: MobileCockpitContinuation?): String {
  val row = continuation ?: return ""
  val lines = mutableListOf<String>()
  lines += buildContinuationSummary(row)
  row.failedPaths.firstOrNull()?.takeIf { it.isNotBlank() }?.let { lines += "Failed path: $it" }
  row.openQuestions.firstOrNull()?.takeIf { it.isNotBlank() }?.let { lines += "Open question: $it" }
  row.nextSteps.firstOrNull()?.takeIf { it.isNotBlank() }?.let { lines += "Next: $it" }
  row.visibleCaveats.firstOrNull()?.takeIf { it.isNotBlank() }?.let { lines += "Caveat: $it" }
  if (row.memoryStatus.isNotBlank() && row.memoryStatus != "none") {
    lines += "Memory counts: active=${row.activeMemoryCount}, expired=${row.expiredMemoryCount}, withdrawn=${row.withdrawnMemoryCount}, contradicted=${row.contradictedMemoryCount}"
  }
  if (row.memoryRecords.isNotEmpty()) {
    lines += row.memoryRecords.joinToString("\n") { record ->
      listOf(
        record.summary.takeIf { it.isNotBlank() } ?: "derived memory",
        record.status.takeIf { it.isNotBlank() } ?: "unknown",
        record.reason.takeIf { it.isNotBlank() },
      ).filterNotNull().joinToString(" · ")
    }
  }
  return lines.filter { it.isNotBlank() }.joinToString("\n")
}

private fun buildRunTriageSummary(
  summary: String,
  failureClass: String,
  actions: List<String>,
): String {
  val base = summary.trim().ifBlank {
    if (failureClass.isNotBlank()) "Failure class: $failureClass." else "No run-triage detail projected."
  }
  val actionText = if (actions.isEmpty()) "" else " Actions: ${actions.joinToString(", ")}."
  return base + actionText
}

private fun buildEconomicsSummary(
  totalTokens: Int,
  wasteWarning: String,
): String {
  val warning = wasteWarning.trim()
  return if (warning.isBlank()) {
    "Economics projection: $totalTokens total tokens."
  } else {
    "Economics projection: $totalTokens total tokens. Warning: $warning."
  }
}

private fun buildArchiveSummary(
  snapshotId: String,
  runArchiveCount: Int,
): String {
  val bits = buildList {
    snapshotId.takeIf { it.isNotBlank() }?.let { add("Latest repo snapshot: $it") }
    add("$runArchiveCount run archives visible")
  }
  return bits.joinToString("  ")
}

private data class ExecutiveHeroModel(
  val eyebrow: String,
  val title: String,
  val reason: String,
  val cta: String,
  val color: Color,
)

private fun buildExecutiveHero(
  cockpit: MobileCockpitPayload?,
  state: BacklogUiState,
): ExecutiveHeroModel {
  val liveness = buildDerivedLiveness(cockpit)
  return when {
    liveness == "Blocked" -> ExecutiveHeroModel(
      eyebrow = "Next best action",
      title = "Unblock the active path",
      reason = buildExecutiveLivenessReason(cockpit),
      cta = "Launch selected campaign",
      color = Color(0xFFF0A83A),
    )
    cockpit?.economics?.wasteWarning?.isNotBlank() == true -> ExecutiveHeroModel(
      eyebrow = "Cost pressure",
      title = "Reduce uncertainty before more burn",
      reason = buildEconomicsSummary(cockpit.economics.totalTokens, cockpit.economics.wasteWarning),
      cta = "Launch selected campaign",
      color = Color(0xFF8CC7FF),
    )
    buildCockpitActionSelection(state.selectedUid, state.stagedExecutionUids).isNotEmpty() -> ExecutiveHeroModel(
      eyebrow = "Next best action",
      title = "Advance the selected work",
      reason = buildCockpitSelectionSummary(state.selectedUid, state.stagedExecutionUids),
      cta = "Launch selected campaign",
      color = Color(0xFF5B8DEF),
    )
    else -> ExecutiveHeroModel(
      eyebrow = "Ready state",
      title = "Select the next PIS to move",
      reason = "The executive layer is calm until you choose the next move.",
      cta = "Select a PIS",
      color = Color(0xFF5B8DEF),
    )
  }
}

internal fun buildDerivedLiveness(cockpit: MobileCockpitPayload?): String {
  val shell = cockpit?.shell?.status?.trim()?.lowercase().orEmpty()
  val run = cockpit?.runTriage?.status?.trim()?.lowercase().orEmpty()
  val blockedStates = setOf("failed", "blocked", "action_required")
  val degradedStates = setOf(
    "low_trust",
    "degraded",
    "stalled",
    "waiting_on_operator",
    "waiting",
    "waiting_for_evidence",
    "evidence_missing",
    "warn",
    "warning",
  )
  val healthyStates = setOf("healthy", "ok", "running", "queued", "completed", "ready")
  val unknownStates = setOf("", "unknown", "inactive", "idle")
  return when {
    shell in blockedStates || run in blockedStates -> "Blocked"
    shell in degradedStates || run in degradedStates -> "Degraded"
    shell in unknownStates && run in unknownStates -> "Unknown"
    shell in healthyStates && (run in healthyStates || run in unknownStates) -> "Healthy"
    run in healthyStates && shell in unknownStates -> "Healthy"
    shell.isNotBlank() || run.isNotBlank() -> "Degraded"
    else -> "Unknown"
  }
}

private fun executiveLivenessColor(cockpit: MobileCockpitPayload?): Color {
  return when (buildDerivedLiveness(cockpit)) {
    "Healthy" -> Color(0xFF3DBA74)
    "Degraded" -> Color(0xFFF0A83A)
    "Blocked" -> Color(0xFFE25B5B)
    else -> Color(0xFF828A95)
  }
}

private fun buildExecutiveLivenessReason(cockpit: MobileCockpitPayload?): String {
  val warnings = cockpit?.shell?.warnings.orEmpty()
  if (warnings.isNotEmpty()) return warnings.joinToString(", ")
  cockpit?.continuation?.summary?.trim()?.takeIf { it.isNotBlank() }?.let { return it }
  val summary = cockpit?.runTriage?.summary.orEmpty().trim()
  if (summary.isNotBlank()) return summary
  return "No contradictory liveness signals surfaced."
}

private fun buildExecutivePisLines(state: BacklogUiState): List<String> {
  return state.items.take(3).map {
    "${mobileBacklogStatusLabel(it.status)}  ${it.uid}  ${it.title.ifBlank { it.slug.ifBlank { "Untitled" } }}"
  }.ifEmpty { listOf("No visible PIS items on the plane.") }
}

private fun buildExecutiveRunLines(cockpit: MobileCockpitPayload?): List<String> {
  val out = mutableListOf<String>()
  cockpit?.campaigns?.latest?.campaignId?.takeIf { it.isNotBlank() }?.let { out += "Campaign $it" }
  cockpit?.executionQueue?.currentUid?.takeIf { it.isNotBlank() }?.let { out += "Current ${it}" }
  cockpit?.runTriage?.summary?.takeIf { it.isNotBlank() }?.let { out += compactWorkflowText(it, 90) }
  return out.ifEmpty { listOf("No active run trajectory projected.") }
}

private fun buildExecutiveArtifactLines(cockpit: MobileCockpitPayload?): List<String> {
  val out = mutableListOf<String>()
  cockpit?.continuation?.summary?.takeIf { it.isNotBlank() }?.let { out += compactWorkflowText(it, 96) }
  cockpit?.artifacts?.latestHandoff?.name?.takeIf { it.isNotBlank() }?.let { out += "Handoff $it" }
  cockpit?.archiveSnapshots?.latestRepoSnapshotId?.takeIf { it.isNotBlank() }?.let { out += "Archive $it" }
  cockpit?.chronicle?.entries?.firstOrNull()?.summary?.takeIf { it.isNotBlank() }?.let { out += compactWorkflowText(it, 96) }
  return out.ifEmpty { listOf("No recent artifacts projected.") }
}

private fun buildCampaignDetailModel(cockpit: MobileCockpitPayload?): CockpitDetailModel {
  val latest = cockpit?.campaigns?.latest
  return CockpitDetailModel(
    title = "Campaign",
    subtitle = latest?.campaignId?.ifBlank { "No campaign id" } ?: "No campaign id",
    summary = buildCampaignQueueDetail(latest?.campaignId.orEmpty(), cockpit?.executionQueue?.runId.orEmpty(), cockpit?.executionQueue?.currentUid.orEmpty(), cockpit?.executionQueue?.totalCount ?: 0),
    evidence = cockpit?.rawShell.orEmpty().ifBlank { buildShellWarningsSummary(cockpit?.shell?.warnings.orEmpty(), cockpit?.shell?.fallbackActions.orEmpty()) },
    logs = cockpit?.rawChronicle.orEmpty().ifBlank { cockpit?.chronicle?.entries?.joinToString("\n") { "${it.utc} ${it.kind}: ${it.summary}" }.orEmpty() },
    artifact = cockpit?.rawContinuation.orEmpty().ifBlank {
      listOf(
        buildContinuationInspectionBlock(cockpit?.continuation),
        buildArtifactSummary(cockpit?.artifacts?.latestHandoff?.name.orEmpty(), cockpit?.artifacts?.latestHandoff?.previewText.orEmpty()),
      ).filter { it.isNotBlank() }.joinToString("\n\n")
    },
    config = cockpit?.rawCampaigns.orEmpty().ifBlank { cockpit?.rawContracts.orEmpty() },
    actions = cockpit?.shell?.fallbackActions?.joinToString("\n").orEmpty().ifBlank { "Launch selected campaign\nRefresh projection\nInspect workflow" },
  )
}

private fun buildEconomicsDetailModel(cockpit: MobileCockpitPayload?): CockpitDetailModel {
  return CockpitDetailModel(
    title = "Economics",
    subtitle = cockpit?.economics?.status?.ifBlank { "Unknown" } ?: "Unknown",
    summary = buildEconomicsSummary(cockpit?.economics?.totalTokens ?: 0, cockpit?.economics?.wasteWarning.orEmpty()),
    evidence = cockpit?.rawShell.orEmpty().ifBlank { "Derived state: ${buildDerivedLiveness(cockpit)}\nWarnings: ${cockpit?.shell?.warnings.orEmpty().joinToString(", ")}" },
    logs = cockpit?.rawChronicle.orEmpty().ifBlank { cockpit?.chronicle?.entries?.joinToString("\n") { "${it.utc} ${it.summary}" }.orEmpty() },
    artifact = cockpit?.rawContinuation.orEmpty().ifBlank { buildContinuationInspectionBlock(cockpit?.continuation) },
    config = cockpit?.rawEconomics.orEmpty().ifBlank { "token_usage.total_tokens=${cockpit?.economics?.totalTokens ?: 0}\nwaste_warning=${cockpit?.economics?.wasteWarning.orEmpty()}" },
    actions = cockpit?.shell?.fallbackActions?.joinToString("\n").orEmpty().ifBlank { "Reduce uncertainty before more execution\nInspect artifact handoff\nCreate archive snapshot" },
  )
}

private fun buildArtifactDetailModel(cockpit: MobileCockpitPayload?): CockpitDetailModel {
  return CockpitDetailModel(
    title = "Artifacts",
    subtitle = cockpit?.artifacts?.status?.ifBlank { "Unknown" } ?: "Unknown",
    summary = buildContinuationSummary(cockpit?.continuation),
    evidence = cockpit?.rawContinuation.orEmpty().ifBlank { buildContinuationInspectionBlock(cockpit?.continuation) },
    logs = cockpit?.rawChronicle.orEmpty().ifBlank { cockpit?.chronicle?.entries?.joinToString("\n") { "${it.utc} ${it.kind}: ${it.summary}" }.orEmpty() },
    artifact = cockpit?.rawArtifacts.orEmpty().ifBlank {
      listOf(
        buildArtifactSummary(cockpit?.artifacts?.latestHandoff?.name.orEmpty(), cockpit?.artifacts?.latestHandoff?.previewText.orEmpty()),
        cockpit?.artifacts?.latestHandoff?.previewText.orEmpty(),
      ).filter { it.isNotBlank() }.joinToString("\n\n")
    },
    config = listOf(
      cockpit?.rawArtifacts.orEmpty(),
      cockpit?.rawContinuation.orEmpty(),
    ).filter { it.isNotBlank() }.joinToString("\n\n").ifBlank {
      "handoff=${cockpit?.artifacts?.latestHandoff?.name.orEmpty()}\ncontinuation_status=${cockpit?.continuation?.status.orEmpty()}\narchive=${cockpit?.archiveSnapshots?.latestRepoSnapshotId.orEmpty()}"
    },
    actions = cockpit?.shell?.fallbackActions?.joinToString("\n").orEmpty().ifBlank { "Export handoff\nCreate archive snapshot\nCopy artifact preview" },
  )
}

private fun buildSelectedPisDetailModel(state: BacklogUiState): CockpitDetailModel {
  val selected = state.selectedItem
  return CockpitDetailModel(
    title = selected?.meta?.title?.ifBlank { state.selectedUid.ifBlank { "Selected PIS" } } ?: "Selected PIS",
    subtitle = state.selectedUid.ifBlank { "No PIS selected" },
    summary = buildCockpitSelectionSummary(state.selectedUid, state.stagedExecutionUids),
    evidence = selected?.notes?.ifBlank { "No extra notes captured." } ?: "No extra notes captured.",
    logs = state.workflowActivity.joinToString("\n") { "${it.utc} ${it.actor}: ${compactWorkflowText(it.text, 180)}" },
    artifact = selected?.artifacts?.joinToString("\n") { "${it.relpath} (${it.bytes} bytes)" }.orEmpty(),
    config = buildSelectedPisRawConfig(selected, state),
    actions = editableBacklogStatusOptions(selected?.meta?.status.orEmpty()).joinToString("\n"),
  )
}

private fun buildSystemTruthDetailModel(state: BacklogUiState): CockpitDetailModel {
  val cockpit = state.cockpit
  return CockpitDetailModel(
    title = "System truth",
    subtitle = "Derived liveness: ${buildDerivedLiveness(cockpit)}",
    summary = buildExecutiveLivenessReason(cockpit),
    evidence = cockpit?.rawContinuation.orEmpty().ifBlank { buildContinuationInspectionBlock(cockpit?.continuation) },
    logs = cockpit?.rawChronicle.orEmpty().ifBlank { cockpit?.chronicle?.entries?.joinToString("\n") { "${it.utc} ${it.kind}: ${it.summary}" }.orEmpty() },
    artifact = cockpit?.rawRunTriage.orEmpty().ifBlank { buildRunTriageSummary(cockpit?.runTriage?.summary.orEmpty(), cockpit?.runTriage?.failureClass.orEmpty(), cockpit?.runTriage?.actions.orEmpty()) },
    config = buildSystemTruthConfig(cockpit),
    actions = cockpit?.shell?.fallbackActions?.joinToString("\n").orEmpty().ifBlank { buildShellWarningsSummary(cockpit?.shell?.warnings.orEmpty(), cockpit?.shell?.fallbackActions.orEmpty()) },
  )
}

private fun buildSelectedPisRawConfig(
  selected: com.eiel.chat.data.LedgerItemDetail?,
  state: BacklogUiState,
): String {
  if (selected == null) return state.selectedUid.ifBlank { "[no selected PIS]" }
  return buildString {
    appendLine("uid=${selected.uid}")
    appendLine("title=${selected.meta.title}")
    appendLine("status=${selected.meta.status}")
    appendLine("release_state=${selected.meta.releaseState}")
    appendLine("updated_utc=${selected.meta.updatedUtc}")
    appendLine("---")
    append(selected.pisMarkdown)
  }.trim()
}

private fun buildSystemTruthConfig(cockpit: MobileCockpitPayload?): String {
  return listOf(
    cockpit?.rawShell.orEmpty(),
    cockpit?.rawContinuation.orEmpty(),
    cockpit?.rawExecutionQueue.orEmpty(),
    cockpit?.rawContracts.orEmpty(),
  ).filter { it.isNotBlank() }.joinToString("\n\n").ifBlank {
    "shell.status=${cockpit?.shell?.status.orEmpty()}\ncontinuation.status=${cockpit?.continuation?.status.orEmpty()}\nqueue.status=${cockpit?.executionQueue?.status.orEmpty()}\nworkflow_id=${cockpit?.runTriage?.workflowId.orEmpty()}"
  }
}

internal fun canLaunchExecutiveAction(state: BacklogUiState): Boolean {
  return buildCockpitActionSelection(state.selectedUid, state.stagedExecutionUids).isNotEmpty()
}

internal fun humanizeLedgerStatus(raw: String): String {
  return raw.trim()
    .ifBlank { "-" }
    .replace('_', ' ')
    .replaceFirstChar { if (it.isLowerCase()) it.titlecase() else it.toString() }
}

internal fun normalizeBacklogHiddenStatuses(statuses: Set<String>): Set<String> {
  return statuses
    .map { it.trim().lowercase() }
    .filter { it.isNotBlank() }
    .toCollection(linkedSetOf())
}

internal fun editableBacklogStatusOptions(currentStatus: String): List<String> {
  val normalizedCurrent = currentStatus.trim().lowercase()
  return buildList {
    if (normalizedCurrent.isNotBlank() && normalizedCurrent !in MOBILE_BACKLOG_EDITABLE_STATUSES) {
      add(normalizedCurrent)
    }
    addAll(MOBILE_BACKLOG_EDITABLE_STATUSES)
  }.distinct()
}

internal fun applyBacklogStatusUpdate(
  items: List<LedgerItemSummary>,
  uid: String,
  status: String,
  updatedUtc: String,
): List<LedgerItemSummary> {
  return items.map { item ->
    if (item.uid != uid) {
      item
    } else {
      item.copy(
        status = status,
        updatedUtc = updatedUtc.ifBlank { item.updatedUtc },
      )
    }
  }
}

internal fun applyBacklogPriorityUpdate(
  items: List<LedgerItemSummary>,
  uid: String,
  updatedUtc: String,
  executionPriority: Int?,
): List<LedgerItemSummary> {
  val targetIndex = items.indexOfFirst { it.uid == uid }
  if (targetIndex < 0) {
    return items
  }
  val updatedItem = items[targetIndex].copy(
    updatedUtc = updatedUtc.ifBlank { items[targetIndex].updatedUtc },
    executionPriority = executionPriority ?: items[targetIndex].executionPriority,
  )
  return buildList(items.size) {
    add(updatedItem)
    items.forEachIndexed { index, item ->
      if (index != targetIndex) {
        add(item)
      }
    }
  }
}

internal fun sanitizeBacklogExecutionSelections(
  stagedUids: Set<String>,
  items: List<LedgerItemSummary>,
): Set<String> {
  val availableUids = items.mapTo(linkedSetOf()) { it.uid }
  return stagedUids
    .map { it.trim() }
    .filter { it.isNotBlank() && it in availableUids }
    .toCollection(linkedSetOf())
}

internal fun backlogStatusUpdateMessage(
  previousStatus: String,
  nextStatus: String,
  hiddenStatuses: Set<String> = MOBILE_BACKLOG_DEFAULT_HIDDEN_STATUSES,
): String {
  val base = "Status changed: ${humanizeLedgerStatus(previousStatus)} -> ${humanizeLedgerStatus(nextStatus)}."
  return if (isBacklogVisibleAfterStatusUpdate(nextStatus, hiddenStatuses)) {
    base
  } else {
    "$base Item stays visible until the next refresh."
  }
}

internal fun isBacklogVisibleAfterStatusUpdate(
  status: String,
  hiddenStatuses: Set<String> = MOBILE_BACKLOG_DEFAULT_HIDDEN_STATUSES,
): Boolean {
  return status.trim().lowercase() !in normalizeBacklogHiddenStatuses(hiddenStatuses)
}

internal fun isMissingBacklogStatusRouteError(message: String?): Boolean {
  return message.orEmpty().trim().equals("not found", ignoreCase = true)
}

private fun selectWorkflowHandle(
  items: List<OrchestrationWorkflowSummary>,
  preferredWorkflowId: String?,
): OrchestrationWorkflowSummary? {
  val preferredId = preferredWorkflowId?.trim().orEmpty()
  return items.firstOrNull { it.workflowId == preferredId }
    ?: items.firstOrNull { !isWorkflowTerminalStatus(it.status) }
    ?: items.firstOrNull()
}

private fun isWorkflowTerminalStatus(status: String): Boolean {
  return status.trim().lowercase() in BACKLOG_WORKFLOW_TERMINAL_STATUSES
}

private fun buildWorkflowObjective(pisId: String): String {
  return "Implement and verify $pisId with WF-01."
}

internal fun buildBacklogWorkflowStartRequest(
  uid: String,
  executionDirective: String,
): WorkflowStartRequest {
  val pisId = uid.trim()
  val launchDirective = parseWorkflowLaunchDirective(executionDirective)
  return WorkflowStartRequest(
    bindingType = "ledger_uid",
    bindingId = pisId,
    phaseLabel = "WF-01",
    objective = buildWorkflowObjective(pisId),
    maxIterations = launchDirective.maxIterations,
    executionDirective = launchDirective.instruction,
  )
}

private fun backlogWorkflowInstructionsPrefKey(uid: String): String {
  return "backlog_workflow_instructions_${uid.trim()}"
}

private const val BACKLOG_EXECUTION_SELECTIONS_PREF_KEY = "backlog_execution_staged_uids"
private const val BACKLOG_HIDDEN_STATUSES_PREF_KEY = "backlog_hidden_statuses"

internal fun buildWorkflowExecutionTargetLabel(
  selectedUid: String,
  stagedExecutionUids: Set<String>,
): String {
  val safeSelectedUid = selectedUid.trim()
  return when {
    safeSelectedUid.isBlank() && stagedExecutionUids.isEmpty() -> "Tick a PIS in the execution list to stage WF-01."
    safeSelectedUid.isBlank() -> "${stagedExecutionUids.size} PIS items staged. Open one to run WF-01."
    safeSelectedUid !in stagedExecutionUids -> "Tick this PIS in the execution list to run WF-01."
    stagedExecutionUids.size <= 1 -> "WF-01 target: $safeSelectedUid"
    else -> "WF-01 target: $safeSelectedUid · ${stagedExecutionUids.size} staged"
  }
}

private fun buildWorkflowHeadline(
  workflowStatus: String,
  phaseLabel: String,
): String {
  val statusLabel = workflowStatus.trim().ifBlank { "idle" }
  return listOf(phaseLabel.trim().ifBlank { "WF-01" }, humanizeLedgerStatus(statusLabel)).joinToString(" ")
}

private fun buildMobileLedgerMarkdown(
  mode: BacklogCreateMode,
  title: String,
  detail: String,
): String {
  val safeTitle = title.trim()
  val safeDetail = detail.trim()
  return when (mode) {
    BacklogCreateMode.PIS -> buildString {
      appendLine("# $safeTitle")
      appendLine()
      appendLine("## Problem")
      appendLine()
      appendLine(if (safeDetail.isBlank()) "Fill in the contract, failure mode, and acceptance bar." else safeDetail)
      appendLine()
      appendLine("## Acceptance")
      appendLine()
      appendLine("- Define the durable truth boundary.")
      appendLine("- Define the fail-closed behavior.")
      appendLine("- Define the narrow validation needed to close it.")
    }
    BacklogCreateMode.IDEA -> buildString {
      appendLine("# $safeTitle")
      appendLine()
      appendLine("## Idea")
      appendLine()
      appendLine(if (safeDetail.isBlank()) "Captured from mobile for later decomposition." else safeDetail)
      appendLine()
      appendLine("## Next cut")
      appendLine()
      appendLine("- Decide whether this should become a real PIS.")
      appendLine("- If yes, define the contract and acceptance bar.")
    }
  }
}

private fun buildMobileLedgerTags(mode: BacklogCreateMode): List<String> {
  return when (mode) {
    BacklogCreateMode.PIS -> listOf("mobile", "pis")
    BacklogCreateMode.IDEA -> listOf("mobile", "idea")
  }
}

private fun buildSharedImportTags(type: SharedDocumentType): List<String> {
  return listOf(
    "mobile",
    "shared",
    when (type) {
      SharedDocumentType.PIS -> "pis"
      SharedDocumentType.PATCH -> "patch"
      SharedDocumentType.AUDIT_REPORT -> "audit-report"
      SharedDocumentType.PATCH_REPORT -> "patch-report"
    },
  )
}

private fun buildWorkflowIterationValue(runtime: WorkflowStatus?): String {
  val maxIterations = runtime?.maxIterations ?: 0
  if (maxIterations <= 0) return "-"
  val iteration = (runtime?.iteration ?: 1).coerceAtLeast(1)
  return "${iteration.coerceAtMost(maxIterations)}/$maxIterations"
}

internal fun buildBacklogWorkflowActivityModel(state: BacklogUiState): BacklogWorkflowActivityModel {
  val runtime = state.workflowRuntime
  val workflowHandle = state.workflowHandle
  if (state.workflowLoading && runtime == null && workflowHandle == null) {
    return BacklogWorkflowActivityModel(
      headline = "Syncing workflow",
      detail = "Loading the latest workflow state for ${state.selectedUid.ifBlank { "the selected PIS" }}.",
      live = true,
      entries = emptyList(),
    )
  }
  if (runtime == null && workflowHandle == null) {
    return BacklogWorkflowActivityModel(
      headline = "No workflow yet",
      detail = "Launch WF-01 here or from the O tab to start a ledger-bound run for ${state.selectedUid.ifBlank { "this PIS" }}.",
      live = false,
      entries = emptyList(),
    )
  }
  if (!isWorkflowTerminalStatus(workflowHandle?.status.orEmpty()) && !workflowHasProgress(runtime)) {
    return BacklogWorkflowActivityModel(
      headline = "Runner waiting",
      detail = "The workflow exists for ${state.selectedUid.ifBlank { "this PIS" }}, but the runner has not started yet. Tap Resume WF-01 to continue.",
      live = false,
      entries = emptyList(),
    )
  }

  val status = (
    runtime?.status?.ifBlank { workflowHandle?.status.orEmpty() }
      ?: workflowHandle?.status.orEmpty()
    ).ifBlank { "idle" }
  val turn = (
    runtime?.turn?.ifBlank { workflowHandle?.turn.orEmpty() }
      ?: workflowHandle?.turn.orEmpty()
    ).ifBlank { "-" }
  val headline = buildWorkflowActivityHeadline(status = status, turn = turn, statusDetail = runtime?.statusDetail.orEmpty())
  val detail = buildWorkflowActivityDetail(runtime = runtime, workflowHandle = workflowHandle)
  val entries = buildWorkflowActivityEntries(state, turn)
  return BacklogWorkflowActivityModel(
    headline = headline,
    detail = detail,
    live = !isWorkflowTerminalStatus(status),
    entries = entries,
  )
}

private fun buildWorkflowActivityHeadline(
  status: String,
  turn: String,
  statusDetail: String = "",
): String {
  return when {
    status.equals("failed", ignoreCase = true) -> "Workflow blocked"
    status.equals("completed", ignoreCase = true) -> "Workflow completed"
    status.equals("cancelled", ignoreCase = true) -> "Workflow cancelled"
    statusDetail.equals("stuck_escalating", ignoreCase = true) -> "Escalating stuck role"
    statusDetail.equals("running_waiting_host_verification", ignoreCase = true) -> "Waiting on host proof"
    statusDetail.equals("blocked_environment", ignoreCase = true) -> "Environment blocked"
    statusDetail.equals("blocked_validation_loop", ignoreCase = true) -> "Validation loop detected"
    turn == "builder_run" -> "Builder working"
    turn == "verifier_run" -> "Verifier reviewing"
    turn == "se_tasking" -> "Orchestrator briefing"
    turn == "se_decide" -> "Orchestrator deciding"
    turn == "se_unblock" -> "Orchestrator recovering"
    turn == "intent_validation" -> "Orchestrator closing"
    status.equals("running", ignoreCase = true) -> "Workflow running"
    else -> "Workflow status"
  }
}

private fun buildWorkflowActivityDetail(
  runtime: WorkflowStatus?,
  workflowHandle: OrchestrationWorkflowSummary?,
): String {
  val workflowStatus = runtime?.status?.ifBlank { workflowHandle?.status.orEmpty() }
    ?: workflowHandle?.status.orEmpty()
  val turn = runtime?.turn?.ifBlank { workflowHandle?.turn.orEmpty() }
    ?: workflowHandle?.turn.orEmpty()
  val maxIterations = runtime?.maxIterations ?: 0
  val iterationSuffix = if (maxIterations > 0) {
    "Iteration ${(runtime?.iteration ?: 1).coerceAtLeast(1).coerceAtMost(maxIterations)} of $maxIterations."
  } else {
    ""
  }
  val phaseLabel = runtime?.phaseLabel?.ifBlank { workflowHandle?.phaseLabel.orEmpty() }
    ?: workflowHandle?.phaseLabel.orEmpty()
  val detail = runtime?.statusDetail.orEmpty()
  val progressNote = runtime?.activeProgressNote?.takeIf { it.isNotBlank() }
  val waitReason = runtime?.activeWaitReason?.takeIf { it.isNotBlank() }
  val stuckReason = runtime?.stuckReason?.takeIf { it.isNotBlank() }
  return when {
    workflowStatus.equals("failed", ignoreCase = true) ->
      runtime?.lastError?.takeIf { it.isNotBlank() }?.let(::compactWorkflowText)
        ?: "The workflow reported a failure and needs attention."
    detail.equals("running_waiting_host_verification", ignoreCase = true) ->
      listOf(iterationSuffix, waitReason ?: progressNote ?: "Waiting for host verification to finish.")
        .filter { it.isNotBlank() }
        .joinToString(" ")
    detail.equals("stuck_escalating", ignoreCase = true) ->
      listOf(iterationSuffix, stuckReason ?: progressNote ?: "The workflow detected a stuck role and is escalating.")
        .filter { it.isNotBlank() }
        .joinToString(" ")
    detail.equals("blocked_environment", ignoreCase = true) ->
      listOf(iterationSuffix, stuckReason ?: waitReason ?: "The workflow is blocked by the current environment.")
        .filter { it.isNotBlank() }
        .joinToString(" ")
    detail.equals("blocked_validation_loop", ignoreCase = true) ->
      listOf(iterationSuffix, stuckReason ?: progressNote ?: "The workflow detected a repeated validation loop.")
        .filter { it.isNotBlank() }
        .joinToString(" ")
    workflowStatus.equals("completed", ignoreCase = true) ->
      listOf(iterationSuffix, "$phaseLabel finished successfully.")
        .filter { it.isNotBlank() }
        .joinToString(" ")
        .ifBlank { "The workflow finished successfully." }
    turn == "builder_run" ->
      listOf(iterationSuffix, progressNote ?: "Builder is working on the current implementation step.")
        .filter { it.isNotBlank() }
        .joinToString(" ")
    turn == "verifier_run" ->
      listOf(iterationSuffix, progressNote ?: "Verifier is checking the builder result.")
        .filter { it.isNotBlank() }
        .joinToString(" ")
    turn == "se_tasking" ->
      listOf(iterationSuffix, progressNote ?: "Orchestrator is shaping the next brief.")
        .filter { it.isNotBlank() }
        .joinToString(" ")
    turn == "se_decide" ->
      listOf(iterationSuffix, progressNote ?: "Orchestrator is deciding whether to continue, revise, or stop.")
        .filter { it.isNotBlank() }
        .joinToString(" ")
    turn == "se_unblock" ->
      listOf(iterationSuffix, progressNote ?: "Orchestrator is recovering a stuck role.")
        .filter { it.isNotBlank() }
        .joinToString(" ")
    turn == "intent_validation" ->
      progressNote ?: "Orchestrator is wrapping the workflow and final validation report."
    else ->
      listOf(iterationSuffix, "Status: ${humanizeLedgerStatus(workflowStatus)}.")
        .filter { it.isNotBlank() }
        .joinToString(" ")
        .ifBlank { "Waiting for the next workflow update." }
  }
}

private fun buildWorkflowActivityEntries(
  state: BacklogUiState,
  activeTurn: String,
): List<BacklogWorkflowEntry> {
  val cutoffUtc = workflowStartCutoffUtc(state)
  val fromConversation = state.workflowActivity
    .filter { item ->
      item.text.isNotBlank() &&
        (cutoffUtc.isBlank() || (item.utc.isNotBlank() && item.utc >= cutoffUtc)) &&
        item.actor.lowercase() != "user"
    }
    .takeLast(4)
    .map { item ->
      val label = workflowActorTag(item.actor)
      BacklogWorkflowEntry(
        label = label,
        title = workflowEntryTitle(label = label, tone = workflowEntryTone(label, activeTurn)),
        body = compactWorkflowText(item.text),
        tone = workflowEntryTone(label, activeTurn),
      )
    }
    .distinct()
  if (fromConversation.isNotEmpty()) return fromConversation

  val runtime = state.workflowRuntime ?: return emptyList()
  val eventEntries = runtime.events
    .mapNotNull { event -> WorkflowTraceMapper.messageForEvent(runtime, event) }
    .takeLast(4)
    .map { message ->
      BacklogWorkflowEntry(
        label = message.label,
        title = workflowEntryTitle(label = message.label, tone = workflowEntryTone(message.label, activeTurn)),
        body = compactWorkflowText(message.content),
        tone = workflowEntryTone(message.label, activeTurn),
      )
    }
    .distinct()
  if (runtime.lastError.isBlank()) return eventEntries
  return eventEntries + BacklogWorkflowEntry(
    label = "WF",
    title = "Attention needed",
    body = compactWorkflowText(runtime.lastError),
    tone = BacklogWorkflowEntryTone.WARNING,
  )
}

private fun workflowEntryTone(
  label: String,
  activeTurn: String,
): BacklogWorkflowEntryTone {
  if (label == "WF") return BacklogWorkflowEntryTone.WARNING
  val activeLabel = when (activeTurn) {
    "builder_run" -> "B"
    "verifier_run" -> "V"
    "se_tasking", "se_decide", "se_unblock", "intent_validation" -> "O"
    else -> ""
  }
  return if (label == activeLabel) BacklogWorkflowEntryTone.ACTIVE else BacklogWorkflowEntryTone.NEUTRAL
}

private fun workflowEntryTitle(
  label: String,
  tone: BacklogWorkflowEntryTone,
): String {
  return when (label) {
    "O" -> if (tone == BacklogWorkflowEntryTone.ACTIVE) "Orchestrator live" else "Orchestrator update"
    "B" -> if (tone == BacklogWorkflowEntryTone.ACTIVE) "Builder live" else "Builder update"
    "V" -> if (tone == BacklogWorkflowEntryTone.ACTIVE) "Verifier live" else "Verifier update"
    "WF", "WF-01" -> "Workflow status"
    else -> "Workflow update"
  }
}

private fun compactWorkflowText(text: String, maxChars: Int = 180): String {
  val compact = text
    .lineSequence()
    .map { it.trim() }
    .filter { it.isNotBlank() }
    .joinToString(" ")
    .replace(Regex("\\s+"), " ")
  if (compact.length <= maxChars) return compact
  return compact.take(maxChars - 3).trimEnd() + "..."
}

private fun workflowActorTag(actor: String): String {
  return when (actor.trim().lowercase()) {
    "generalist", "orchestrator" -> "O"
    "builder" -> "B"
    "assurer", "verifier" -> "V"
    else -> "WF"
  }
}

private fun backlogWorkflowStatusColor(
  workflowStatus: String,
  primary: Color,
  secondary: Color,
  error: Color,
): Color {
  return when (workflowStatus.trim().lowercase()) {
    "completed" -> BACKLOG_ROLE_ORCHESTRATOR
    "failed" -> error
    "cancelled" -> secondary
    "running", "idle" -> primary
    else -> primary
  }
}

internal fun buildWorkflowRoleStates(
  runtime: WorkflowStatus?,
  workflowHandle: OrchestrationWorkflowSummary?,
): List<BacklogWorkflowRoleState> {
  val status = runtime?.status?.ifBlank { workflowHandle?.status.orEmpty() }
    ?: workflowHandle?.status.orEmpty()
  val terminal = isWorkflowTerminalStatus(status)
  val turn = (
    runtime?.turn?.ifBlank { workflowHandle?.turn.orEmpty() }
      ?: workflowHandle?.turn.orEmpty()
    ).trim().lowercase()
  val detail = runtime?.statusDetail.orEmpty().trim().lowercase()
  val events = runtime?.events.orEmpty()
  val activeRole = runtime?.activeRole?.trim()?.uppercase()?.takeIf { it.isNotBlank() } ?: when (turn) {
    "builder_run" -> "B"
    "verifier_run" -> "V"
    "se_tasking", "se_decide", "se_unblock", "intent_validation" -> "O"
    else -> ""
  }
  val builderDone = events.any { it.event == "builder_run_done" }
  val verifierDone = events.any { it.event == "verifier_run_done" }
  val orchestratorDone = events.any { event ->
    event.event in setOf("se_tasking_done", "se_decision_done", "intent_validation_done")
  }
  return listOf(
    BacklogWorkflowRoleState(
      label = "O",
      title = "Orchestrator",
      color = BACKLOG_ROLE_ORCHESTRATOR,
      active = !terminal && activeRole == "O",
      completed = orchestratorDone,
      failed = status.trim().lowercase() == "failed" && activeRole == "O",
      statusText = workflowRoleStatusText(
        label = "O",
        active = !terminal && activeRole == "O",
        completed = orchestratorDone,
        failed = status.trim().lowercase() == "failed" && activeRole == "O",
        detail = detail,
      ),
    ),
    BacklogWorkflowRoleState(
      label = "B",
      title = "Builder",
      color = BACKLOG_ROLE_BUILDER,
      active = !terminal && activeRole == "B",
      completed = builderDone,
      failed = status.trim().lowercase() == "failed" && activeRole == "B",
      statusText = workflowRoleStatusText(
        label = "B",
        active = !terminal && activeRole == "B",
        completed = builderDone,
        failed = status.trim().lowercase() == "failed" && activeRole == "B",
        detail = detail,
      ),
    ),
    BacklogWorkflowRoleState(
      label = "V",
      title = "Verifier",
      color = BACKLOG_ROLE_VERIFIER,
      active = !terminal && activeRole == "V",
      completed = verifierDone,
      failed = status.trim().lowercase() == "failed" && activeRole == "V",
      statusText = workflowRoleStatusText(
        label = "V",
        active = !terminal && activeRole == "V",
        completed = verifierDone,
        failed = status.trim().lowercase() == "failed" && activeRole == "V",
        detail = detail,
      ),
    ),
  )
}

private fun workflowRoleStatusText(
  label: String,
  active: Boolean,
  completed: Boolean,
  failed: Boolean,
  detail: String = "",
): String {
  return when {
    failed -> "Needs attention"
    active && detail == "running_waiting_host_verification" -> "Waiting"
    active && detail == "stuck_escalating" -> "Escalating"
    active && detail == "blocked_environment" -> "Blocked"
    active && detail == "blocked_validation_loop" -> "Looping"
    active -> "Active"
    completed -> "Done"
    label == "O" -> "Queued"
    else -> "Waiting"
  }
}

private fun mobileBacklogStatusRank(status: String): Int {
  return when (status.trim().lowercase()) {
    "draft" -> 0
    "ready", "ready_for_implement", "ready_to_implement" -> 1
    else -> 2
  }
}

private fun backlogStatusSortOrder(status: String): Int {
  val normalizedStatus = status.trim().lowercase()
  return when {
    normalizedStatus == "done" -> 10_000
    normalizedStatus == "deprecated" -> 9_000
    normalizedStatus == "ready_to_implement" -> MOBILE_BACKLOG_EDITABLE_STATUSES.indexOf("ready_for_implement")
    else -> MOBILE_BACKLOG_EDITABLE_STATUSES.indexOf(normalizedStatus)
      .takeIf { it >= 0 }
      ?: 8_000
  }
}

private fun mobileBacklogStatusLabel(status: String): String {
  return when (status.trim().lowercase()) {
    "draft" -> "Draft"
    "ready", "ready_for_implement", "ready_to_implement" -> "Ready"
    else -> humanizeLedgerStatus(status)
  }
}

private fun workflowHasProgress(runtime: WorkflowStatus?): Boolean {
  if (runtime == null) return false
  if (runtime.status.trim().lowercase() != "idle") return true
  return runtime.events.any { it.event != "workflow_started" }
}

private fun workflowStartCutoffUtc(state: BacklogUiState): String {
  val fromHandle = state.workflowHandle?.createdUtc.orEmpty().trim()
  if (fromHandle.isNotBlank()) return fromHandle
  return state.workflowRuntime?.events
    ?.firstOrNull { it.event == "workflow_started" }
    ?.utc
    .orEmpty()
    .trim()
}

private data class WorkflowSnapshot(
  val canonicalStatus: LedgerWorkflowStatus?,
  val handle: OrchestrationWorkflowSummary?,
  val runtime: WorkflowStatus?,
  val activity: List<WorkflowConversationItem>,
)

internal data class BacklogWorkflowActivityModel(
  val headline: String,
  val detail: String,
  val live: Boolean,
  val entries: List<BacklogWorkflowEntry>,
)

internal data class BacklogWorkflowEntry(
  val label: String,
  val title: String,
  val body: String,
  val tone: BacklogWorkflowEntryTone,
)

internal enum class BacklogWorkflowEntryTone {
  NEUTRAL,
  ACTIVE,
  WARNING,
}

internal data class BacklogWorkflowRoleState(
  val label: String,
  val title: String,
  val color: Color,
  val active: Boolean,
  val completed: Boolean,
  val failed: Boolean,
  val statusText: String,
)
