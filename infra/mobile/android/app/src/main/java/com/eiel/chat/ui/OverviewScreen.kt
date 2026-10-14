package com.eiel.chat.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.eiel.chat.data.OperatorSnapshot
import com.eiel.chat.data.OperatorSnapshotCommandCenter
import com.eiel.chat.data.OperatorSnapshotCommandCenterItem
import com.eiel.chat.data.OperatorSnapshotNotification
import com.eiel.chat.data.OperatorSnapshotSectionItem
import com.eiel.chat.data.OperatorSnapshotTask
import com.eiel.chat.data.PlaneHistoryEntry
import com.eiel.chat.domain.AgentChatState
import com.eiel.chat.domain.AgentTab
import com.eiel.chat.domain.MessageRole

private const val FRONT_DOOR_NEEDS_ATTENTION_LIMIT = 5
private val FRONT_DOOR_AGENT_TABS = listOf(AgentTab.PLANE)
private val HOME_PLANE_VERB_CHIPS = listOf(
  "commit",
  "archive weekly",
  "server status",
  "server restart",
  "scheduler watch",
  "help archive",
  "what are the next intent sheets to implement?",
)

internal data class OperatorFrontDoorModel(
  val headline: String,
  val state: String,
  val freshnessLabel: String,
  val topNotification: OperatorSnapshotNotification? = null,
  val groupedFailureSummary: String = "",
  val needsAttention: List<OperatorFrontDoorItem> = emptyList(),
  val running: List<OperatorFrontDoorItem> = emptyList(),
  val scheduled: List<OperatorFrontDoorItem> = emptyList(),
  val completedCount: Int = 0,
  val workItemsByTaskId: Map<String, com.eiel.chat.data.OperatorSnapshotWorkItem> = emptyMap(),
)

internal data class OperatorFrontDoorItem(
  val taskId: String,
  val title: String,
  val stateLabel: String,
  val reason: String,
  val nextAction: String,
  val stageLabel: String = "",
  val executionLabel: String = "",
  val metadata: List<String> = emptyList(),
  val recommended: Boolean = false,
  val riskLevel: String = "",
  val consequence: String = "",
)

internal data class FrontDoorNotificationAction(
  val label: String,
  val onClick: () -> Unit,
)

private enum class FrontDoorBucket {
  ATTENTION,
  RUNNING,
  SCHEDULED,
}

@Composable
fun OverviewRoute(
  chatVm: ChatViewModel,
  adminVm: AdminViewModel,
  onOpenChat: () -> Unit,
  onOpenPlaneLog: () -> Unit,
  onOpenBacklog: () -> Unit,
  onOpenAdmin: () -> Unit,
) {
  val chatState by chatVm.uiState.collectAsStateWithLifecycle()
  val adminState by adminVm.uiState.collectAsStateWithLifecycle()
  val lifecycleOwner = LocalLifecycleOwner.current
  LaunchedEffect(Unit) {
    adminVm.refreshHealth()
    adminVm.refreshOperatorSnapshot()
    adminVm.loadConfig()
  }
  DisposableEffect(lifecycleOwner, adminVm) {
    val observer = LifecycleEventObserver { _, event ->
      if (event == Lifecycle.Event.ON_RESUME) {
        adminVm.refreshHealth()
        adminVm.refreshOperatorSnapshot()
      }
    }
    lifecycleOwner.lifecycle.addObserver(observer)
    onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
  }
  OverviewScreen(
    chatState = chatState,
    adminState = adminState,
    onOpenChat = {
      chatVm.onTabSelected(AgentTab.default)
      onOpenChat()
    },
    onOpenPlaneLog = onOpenPlaneLog,
    onOpenBacklog = onOpenBacklog,
    onOpenAdmin = onOpenAdmin,
    onRefreshServer = adminVm::refreshAll,
    onRestartServer = adminVm::restartServer,
    onRefreshSnapshot = adminVm::refreshOperatorSnapshot,
    onPlanePromptChanged = adminVm::onPlanePromptChanged,
    onPlanePlanModeChanged = adminVm::onPlanePlanModeChanged,
    onRunPlanePrompt = { adminVm.runPlanePrompt() },
    onCancelPlanePrompt = adminVm::interruptPlanePrompt,
    onRefreshPlaneHistory = { adminVm.refreshPlaneHistory() },
    onUsePlaneHistoryPrompt = { prompt, runNow -> adminVm.usePlaneHistoryPrompt(prompt, runNow) },
    onOpenAgent = { tab ->
      chatVm.onTabSelected(tab)
      onOpenChat()
    },
  )
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun OverviewScreen(
  chatState: com.eiel.chat.domain.ChatUiState,
  adminState: AdminUiState,
  onOpenChat: () -> Unit,
  onOpenPlaneLog: () -> Unit,
  onOpenBacklog: () -> Unit,
  onOpenAdmin: () -> Unit,
  onRefreshServer: () -> Unit,
  onRestartServer: () -> Unit,
  onRefreshSnapshot: () -> Unit,
  onPlanePromptChanged: (String) -> Unit,
  onPlanePlanModeChanged: (Boolean) -> Unit,
  onRunPlanePrompt: () -> Unit,
  onCancelPlanePrompt: () -> Unit,
  onRefreshPlaneHistory: () -> Unit,
  onUsePlaneHistoryPrompt: (String, Boolean) -> Unit,
  onOpenAgent: (AgentTab) -> Unit,
) {
  val colorScheme = MaterialTheme.colorScheme
  val frontDoor = buildOperatorFrontDoorModel(adminState.operatorSnapshot)
  var selectedTaskId by remember(frontDoor) { mutableStateOf<String?>(null) }
  var showPlaneHistory by remember { mutableStateOf(false) }
  val selectedWorkItem = frontDoor?.workItemsByTaskId?.get(selectedTaskId)
  LazyColumn(
    modifier = Modifier.fillMaxSize(),
    contentPadding = compactScreenContentPadding(),
    verticalArrangement = Arrangement.spacedBy(CompactSectionGap),
  ) {
    item {
      MinimalServerPane(
        healthStatus = adminState.healthStatus,
        statusMessage = adminState.statusMessage,
        restartBlockers = adminState.restartBlockers,
        onRefresh = onRefreshServer,
        onRestart = onRestartServer,
      )
    }

    item {
      Card(modifier = Modifier.fillMaxWidth()) {
        SelectionContainer {
          Column(
            modifier = Modifier
              .fillMaxWidth()
              .padding(CompactCardPadding),
            verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
          ) {
            Text("Home", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Text(
              frontDoor?.headline ?: "Use the plane to start work or inspect what needs attention.",
              style = MaterialTheme.typography.bodyMedium,
            )
            FlowRow(
              modifier = Modifier.fillMaxWidth(),
              horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
              verticalArrangement = Arrangement.spacedBy(CompactInlineGap),
            ) {
              SmallButton("Start work", onOpenChat)
              SmallButton("Plane log", onOpenPlaneLog)
              SmallButton("The plane", onOpenBacklog)
              SmallButton("Admin", onOpenAdmin)
            }
            PlaneFrontDoorCard(
              prompt = adminState.planePrompt,
              planMode = adminState.planePlanMode,
              running = adminState.planeRunning,
              output = adminState.planeOutput,
              outputTitle = adminState.planeOutputTitle,
              historyCount = adminState.planeHistory.size,
              historyLoading = adminState.planeHistoryLoading,
              historyError = adminState.planeHistoryError,
              onPromptChanged = onPlanePromptChanged,
              onPlanModeChanged = onPlanePlanModeChanged,
              onRun = onRunPlanePrompt,
              onCancel = onCancelPlanePrompt,
              onOpenHistory = {
                onRefreshPlaneHistory()
                showPlaneHistory = true
              },
            )
            OverviewMeta("Snapshot", frontDoor?.freshnessLabel ?: snapshotLoadingLabel(adminState))
            if (frontDoor?.topNotification != null) {
              SnapshotNotificationBanner(
                notification = frontDoor.topNotification,
                actions = buildFrontDoorNotificationActions(
                  notification = frontDoor.topNotification,
                  onRefreshSnapshot = onRefreshSnapshot,
                  onOpenAdmin = onOpenAdmin,
                ),
              )
            }
            if (!frontDoor?.groupedFailureSummary.isNullOrBlank()) {
              Text(
                frontDoor?.groupedFailureSummary.orEmpty(),
                style = ChatLabelTextStyle,
                color = colorScheme.onSurfaceVariant,
              )
            } else if (adminState.operatorSnapshotError.isNotBlank()) {
              Text(adminState.operatorSnapshotError, style = ChatLabelTextStyle, color = colorScheme.error)
            } else if (adminState.statusMessage.isNotBlank()) {
              Text(adminState.statusMessage, style = ChatLabelTextStyle, color = colorScheme.primary)
            }
          }
        }
      }
    }

    item {
      FrontDoorSection(
        title = "Needs Attention",
        emptyLabel = "No blocked work right now.",
        items = frontDoor?.needsAttention.orEmpty(),
        accent = MaterialTheme.colorScheme.error,
        onOpenDetail = { selectedTaskId = it.taskId },
      )
    }

    item {
      FrontDoorSection(
        title = "Running",
        emptyLabel = "No active work visible in the current snapshot.",
        items = frontDoor?.running.orEmpty(),
        accent = MaterialTheme.colorScheme.primary,
        onOpenDetail = { selectedTaskId = it.taskId },
      )
    }

    item {
      FrontDoorSection(
        title = "Scheduled",
        emptyLabel = "No scheduled follow-up is visible in the current snapshot.",
        items = frontDoor?.scheduled.orEmpty(),
        accent = MaterialTheme.colorScheme.tertiary,
        onOpenDetail = { selectedTaskId = it.taskId },
      )
    }

    item {
      Card(modifier = Modifier.fillMaxWidth()) {
        Column(
          modifier = Modifier
            .fillMaxWidth()
            .padding(CompactCardPadding),
          verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
        ) {
          Text("Completed", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
          Text(
            if (frontDoor == null) "Completed work will appear here once snapshot data loads."
            else "${frontDoor.completedCount} completed task${if (frontDoor.completedCount == 1) "" else "s"} in snapshot history.",
            style = ChatLabelTextStyle,
            color = colorScheme.onSurfaceVariant,
          )
        }
      }
    }

    item {
      Text(
        "Agent Threads",
        style = MaterialTheme.typography.titleMedium,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier.padding(horizontal = 2.dp),
      )
    }

    items(items = FRONT_DOOR_AGENT_TABS, key = { tab -> tab.key }) { tab ->
      val agent = chatState.agentStates[tab] ?: AgentChatState(tab = tab)
      AgentOverviewCard(
        tab = tab,
        state = agent,
        selected = chatState.selectedTab == tab,
        onOpen = { onOpenAgent(tab) },
      )
    }
  }
  if (selectedWorkItem != null) {
    OperatorTaskDetailSheet(
      workItem = selectedWorkItem,
      onDismiss = { selectedTaskId = null },
    )
  }
  if (showPlaneHistory) {
    PlaneHistorySheet(
      entries = adminState.planeHistory,
      loading = adminState.planeHistoryLoading,
      error = adminState.planeHistoryError,
      onDismiss = { showPlaneHistory = false },
      onUse = { prompt ->
        onUsePlaneHistoryPrompt(prompt, false)
        showPlaneHistory = false
      },
      onRun = { prompt ->
        onUsePlaneHistoryPrompt(prompt, true)
        showPlaneHistory = false
      },
    )
  }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PlaneFrontDoorCard(
  prompt: String,
  planMode: Boolean,
  running: Boolean,
  output: String,
  outputTitle: String,
  historyCount: Int,
  historyLoading: Boolean,
  historyError: String,
  onPromptChanged: (String) -> Unit,
  onPlanModeChanged: (Boolean) -> Unit,
  onRun: () -> Unit,
  onCancel: () -> Unit,
  onOpenHistory: () -> Unit,
) {
  val colorScheme = MaterialTheme.colorScheme
  Column(
    modifier = Modifier
      .fillMaxWidth()
      .adminFieldSurface()
      .padding(CompactCardPadding),
    verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
  ) {
    Text("Ask the plane", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
    Text(
      "Type the same prompts you use in lab and labsh. The home page runs the real front door and shows the live terminal output below.",
      style = ChatLabelTextStyle,
      color = colorScheme.onSurfaceVariant,
    )
    AdminTextField(
      value = prompt,
      onValueChange = onPromptChanged,
      label = "Command",
      modifier = Modifier.fillMaxWidth(),
      singleLine = false,
      minLines = 2,
    )
    FlowRow(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
      verticalArrangement = Arrangement.spacedBy(CompactInlineGap),
    ) {
      HOME_PLANE_VERB_CHIPS.forEach { chipPrompt ->
        Chip(
          text = chipPrompt,
          selected = prompt.trim().equals(chipPrompt, ignoreCase = true),
          onClick = { onPromptChanged(chipPrompt) },
        )
      }
    }
    FlowRow(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
      verticalArrangement = Arrangement.spacedBy(CompactInlineGap),
    ) {
      Chip(
        text = if (planMode) "Plan only" else "Execute",
        selected = planMode,
        onClick = { onPlanModeChanged(!planMode) },
      )
      SmallButton("Recent${if (historyCount > 0) " ($historyCount)" else ""}", onOpenHistory)
      SmallButton("Run", onRun, enabled = prompt.isNotBlank() && !running)
      if (running) {
        SmallDangerButton("Cancel", onCancel)
      }
    }
    when {
      historyLoading -> Text("Loading recent prompts...", style = ChatLabelTextStyle, color = colorScheme.onSurfaceVariant)
      historyError.isNotBlank() -> Text(historyError, style = ChatLabelTextStyle, color = colorScheme.error)
    }
    if (output.isNotBlank() || running) {
      MonospaceBlock(
        text = trimAdminOutputForDisplay(output.ifBlank { "[the plane] waiting for output..." }),
        title = outputTitle.ifBlank { "the plane" },
        minHeight = 180.dp,
        maxHeight = 320.dp,
      )
    }
  }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun PlaneHistorySheet(
  entries: List<PlaneHistoryEntry>,
  loading: Boolean,
  error: String,
  onDismiss: () -> Unit,
  onUse: (String) -> Unit,
  onRun: (String) -> Unit,
) {
  val colorScheme = MaterialTheme.colorScheme
  ModalBottomSheet(onDismissRequest = onDismiss) {
    Column(
      modifier = Modifier
        .fillMaxWidth()
        .padding(CompactCardPadding),
      verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
    ) {
      Text("Recent prompts", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
      Text(
        "Pick one of the last prompts sent through the plane.",
        style = ChatLabelTextStyle,
        color = colorScheme.onSurfaceVariant,
      )
      when {
        loading -> Text("Loading...", style = ChatLabelTextStyle, color = colorScheme.onSurfaceVariant)
        error.isNotBlank() -> Text(error, style = ChatLabelTextStyle, color = colorScheme.error)
        entries.isEmpty() -> Text("No recent prompts yet.", style = ChatLabelTextStyle, color = colorScheme.onSurfaceVariant)
        else -> LazyColumn(
          modifier = Modifier.fillMaxWidth(),
          verticalArrangement = Arrangement.spacedBy(CompactInlineGap),
        ) {
          items(items = entries, key = { "${it.sessionId}:${it.utc}:${it.prompt}" }) { entry ->
            Card(modifier = Modifier.fillMaxWidth()) {
              Column(
                modifier = Modifier
                  .fillMaxWidth()
                  .clickable { onUse(entry.prompt) }
                  .padding(CompactCardPadding),
                verticalArrangement = Arrangement.spacedBy(CompactTightGap),
              ) {
                Text(entry.prompt, style = ChatLabelTextStyle, fontWeight = FontWeight.SemiBold)
                val meta = listOfNotNull(
                  entry.utc.takeIf { it.isNotBlank() },
                  entry.authorityScope.takeIf { it.isNotBlank() },
                ).joinToString(" · ")
                if (meta.isNotBlank()) {
                  Text(meta, style = ChatLabelTextStyle, color = colorScheme.onSurfaceVariant)
                }
                FlowRow(
                  modifier = Modifier.fillMaxWidth(),
                  horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
                  verticalArrangement = Arrangement.spacedBy(CompactInlineGap),
                ) {
                  SmallButton("Use", onClick = { onUse(entry.prompt) })
                  SmallButton("Run", onClick = { onRun(entry.prompt) })
                }
              }
            }
          }
        }
      }
    }
  }
}

internal fun buildFrontDoorNotificationActionLabels(notification: OperatorSnapshotNotification?): List<String> {
  val kind = notification?.kind?.trim()?.lowercase().orEmpty()
  return when (kind) {
    "stale_data", "stale_task_register_with_open_work" -> listOf("Refresh snapshot", "Admin")
    else -> emptyList()
  }
}

private fun executiveTopNotification(notification: OperatorSnapshotNotification?): OperatorSnapshotNotification? {
  val kind = notification?.kind?.trim()?.lowercase().orEmpty()
  return when (kind) {
    "stale_data", "stale_task_register_with_open_work", "truth_conflict" -> null
    else -> notification
  }
}

private fun shouldSuppressExecutiveTaskTruth(snapshot: OperatorSnapshot): Boolean {
  val state = snapshot.summary.statusLanguage.state.trim().lowercase()
  val notificationKind = snapshot.summary.notifications.firstOrNull()?.kind?.trim()?.lowercase().orEmpty()
  if (state in setOf("stale", "conflicted", "degraded", "unreachable", "reconnecting")) {
    return true
  }
  if (snapshot.freshness.stale || snapshot.freshness.confidence.trim().equals("STALE", ignoreCase = true)) {
    return true
  }
  return notificationKind in setOf("stale_data", "stale_task_register_with_open_work", "truth_conflict", "unreachable_server", "resumed_recovery")
}

private fun buildFrontDoorNotificationActions(
  notification: OperatorSnapshotNotification,
  onRefreshSnapshot: () -> Unit,
  onOpenAdmin: () -> Unit,
): List<FrontDoorNotificationAction> {
  return buildFrontDoorNotificationActionLabels(notification).mapNotNull { label ->
    when (label) {
      "Refresh snapshot" -> FrontDoorNotificationAction(label = label, onClick = onRefreshSnapshot)
      "Admin" -> FrontDoorNotificationAction(label = label, onClick = onOpenAdmin)
      else -> null
    }
  }
}

internal fun buildOperatorFrontDoorModel(snapshot: OperatorSnapshot?): OperatorFrontDoorModel? {
  if (snapshot == null) return null
  val suppressExecutiveTaskTruth = shouldSuppressExecutiveTaskTruth(snapshot)
  val tasksById = snapshot.activeTasks.associateBy { it.taskId }
  val workItemsByTaskId = snapshot.workItems.associateBy { it.taskId }
  val commandCenter = snapshot.commandCenter
  if (
    commandCenter.headline.isNotBlank() ||
    commandCenter.needsAttention.isNotEmpty() ||
    commandCenter.running.isNotEmpty() ||
    commandCenter.scheduled.isNotEmpty() ||
    commandCenter.completedCount > 0
  ) {
    val commandCenterNeedsAttention = mutableListOf<OperatorFrontDoorItem>()
    val commandCenterRunning = mutableListOf<OperatorFrontDoorItem>()
    val commandCenterScheduled = mutableListOf<OperatorFrontDoorItem>()
    val seenTaskIds = linkedSetOf<String>()

    fun appendItem(item: OperatorSnapshotCommandCenterItem) {
      val task = tasksById[item.taskId]
      val workItem = workItemsByTaskId[item.taskId]
      if (tasksById.isNotEmpty() && item.taskId.isNotBlank() && task == null) return
      val sanitized = OperatorFrontDoorItem(
        taskId = item.taskId,
        title = item.title,
        stateLabel = if (task != null) taskFrontDoorStateLabel(task, null) else frontDoorStateLabel(item.state),
        reason = item.reason.ifBlank { task?.waitingReason.orEmpty() }.ifBlank { "No reason recorded." },
        nextAction = item.nextAction.ifBlank {
          task?.nextDecisionSummary.orEmpty().ifBlank { task?.nextExpectedAction.orEmpty() }
        }.ifBlank { "No next action recorded." },
        stageLabel = item.stageLabel.ifBlank { buildFrontDoorStageLabel(task) },
        executionLabel = item.executionSurface.ifBlank { buildFrontDoorExecutionLabel(task) },
        metadata = if (item.metadata.isNotEmpty()) item.metadata else buildFrontDoorMetadata(workItem),
        recommended = item.recommended || workItem?.recommendedActionPrompt?.isNotBlank() == true,
        riskLevel = item.riskLevel.ifBlank { workItem?.recommendedActionRisk.orEmpty() },
        consequence = item.consequence.ifBlank { workItem?.recommendedActionConsequence.orEmpty() },
      )
      val dedupeKey = sanitized.taskId.ifBlank { sanitized.title }
      if (dedupeKey.isNotBlank() && !seenTaskIds.add(dedupeKey)) return
      when (frontDoorBucket(sanitized.stateLabel)) {
        FrontDoorBucket.ATTENTION -> commandCenterNeedsAttention += sanitized
        FrontDoorBucket.RUNNING -> commandCenterRunning += sanitized
        FrontDoorBucket.SCHEDULED -> commandCenterScheduled += sanitized
      }
    }

    commandCenter.needsAttention.forEach(::appendItem)
    commandCenter.running.forEach(::appendItem)
    commandCenter.scheduled.forEach(::appendItem)
    val groupedFailureSummary = if (suppressExecutiveTaskTruth) {
      ""
    } else {
      buildGroupedFailureSummary(commandCenterNeedsAttention + commandCenterRunning + commandCenterScheduled)
    }
    return OperatorFrontDoorModel(
      headline = commandCenter.headline.ifBlank {
        snapshot.summary.statusLanguage.headline.ifBlank {
          commandCenter.intakePrompt.ifBlank {
            humanizeStatus(snapshot.operatorState).ifBlank { "Operator snapshot" }
          }
        }
      },
      state = snapshot.summary.statusLanguage.state.ifBlank { snapshot.operatorState },
      freshnessLabel = buildSnapshotFreshnessLabel(
        snapshot.copy(
          freshness = if (
            commandCenter.snapshotMeta.generatedAt.isNotBlank() ||
            commandCenter.snapshotMeta.confidence.isNotBlank() ||
            commandCenter.snapshotMeta.freshnessSeconds != null
          ) commandCenter.snapshotMeta else snapshot.freshness,
        ),
      ),
      topNotification = executiveTopNotification(snapshot.summary.notifications.firstOrNull()),
      groupedFailureSummary = groupedFailureSummary,
      needsAttention = if (suppressExecutiveTaskTruth) emptyList() else commandCenterNeedsAttention,
      running = if (suppressExecutiveTaskTruth) emptyList() else commandCenterRunning,
      scheduled = if (suppressExecutiveTaskTruth) emptyList() else commandCenterScheduled,
      completedCount = commandCenter.completedCount,
      workItemsByTaskId = workItemsByTaskId,
    )
  }
  val activeTasksById = tasksById
  val sectionItemsByTask = snapshot.summary.sections
    .flatMap { it.items }
    .groupBy { it.taskId }

  fun nextActionForTask(taskId: String): OperatorSnapshotSectionItem? =
    snapshot.summary.sections
      .firstOrNull { it.sectionId == "next_actions" }
      ?.items
      ?.firstOrNull { it.taskId == taskId }

  fun mapTask(task: OperatorSnapshotTask?, sectionItem: OperatorSnapshotSectionItem?): OperatorFrontDoorItem? {
    val taskId = task?.taskId.orEmpty().ifBlank { sectionItem?.taskId.orEmpty() }
    val title = task?.title.orEmpty().ifBlank { sectionItem?.title.orEmpty() }
    val workItem = workItemsByTaskId[taskId]
    if (title.isBlank()) return null
    val nextActionItem = nextActionForTask(taskId)
    val nextAction = task?.nextDecisionSummary
      .orEmpty()
      .ifBlank { task?.nextExpectedAction.orEmpty() }
      .ifBlank { nextActionItem?.action.orEmpty() }
      .ifBlank { sectionItem?.action.orEmpty() }
    return OperatorFrontDoorItem(
      taskId = taskId,
      title = title,
      stateLabel = taskFrontDoorStateLabel(task, sectionItem),
      reason = task?.waitingReason.orEmpty().ifBlank { sectionItem?.summary.orEmpty() }.ifBlank { "No reason recorded." },
      nextAction = nextAction.ifBlank { "No next action recorded." },
      stageLabel = buildFrontDoorStageLabel(task),
      executionLabel = buildFrontDoorExecutionLabel(task),
      metadata = buildFrontDoorMetadata(workItem),
      recommended = workItem?.recommendedActionPrompt?.isNotBlank() == true,
      riskLevel = workItem?.recommendedActionRisk.orEmpty(),
      consequence = workItem?.recommendedActionConsequence.orEmpty(),
    )
  }

  val sectionFallbackItems = sectionItemsByTask.values
    .flatten()
    .filter { it.taskId.isNotBlank() && !activeTasksById.containsKey(it.taskId) }

  val taskItems = snapshot.activeTasks.mapNotNull { task ->
    mapTask(task, sectionItemsByTask[task.taskId]?.firstOrNull())
  } + sectionFallbackItems.mapNotNull { item ->
    mapTask(activeTasksById[item.taskId], item)
  }

  val needsAttention = taskItems
    .filter { frontDoorBucket(it.stateLabel) == FrontDoorBucket.ATTENTION }
    .take(FRONT_DOOR_NEEDS_ATTENTION_LIMIT)

  val running = taskItems
    .filter { frontDoorBucket(it.stateLabel) == FrontDoorBucket.RUNNING }

  val scheduled = taskItems
    .filter { frontDoorBucket(it.stateLabel) == FrontDoorBucket.SCHEDULED }

  val freshnessLabel = buildSnapshotFreshnessLabel(snapshot)
  val groupedFailureSummary = if (suppressExecutiveTaskTruth) "" else buildGroupedFailureSummary(taskItems)
  return OperatorFrontDoorModel(
    headline = snapshot.summary.statusLanguage.headline.ifBlank {
      humanizeStatus(snapshot.operatorState).ifBlank { "Operator snapshot" }
    },
    state = snapshot.summary.statusLanguage.state.ifBlank { snapshot.operatorState },
    freshnessLabel = freshnessLabel,
    topNotification = executiveTopNotification(snapshot.summary.notifications.firstOrNull()),
    groupedFailureSummary = groupedFailureSummary,
    needsAttention = if (suppressExecutiveTaskTruth) emptyList() else needsAttention,
    running = if (suppressExecutiveTaskTruth) emptyList() else running,
    scheduled = if (suppressExecutiveTaskTruth) emptyList() else scheduled,
    completedCount = snapshot.completedTasks.size,
    workItemsByTaskId = workItemsByTaskId,
  )
}

internal fun buildSnapshotFreshnessLabel(snapshot: OperatorSnapshot): String {
  val confidence = snapshot.freshness.confidence.ifBlank { "UNKNOWN" }
  val label = when (confidence.uppercase()) {
    "HIGH" -> "Fresh"
    "DEGRADED" -> "Degraded"
    "STALE" -> "Stale"
    "UNKNOWN" -> "Unknown"
    else -> humanizeStatus(confidence)
  }
  return when {
    snapshot.freshness.stale || confidence.uppercase() == "STALE" -> "Stale"
    snapshot.freshness.freshnessSeconds != null -> "$label · ${snapshot.freshness.freshnessSeconds}s old"
    else -> label
  }
}

private fun snapshotLoadingLabel(state: AdminUiState): String {
  return when {
    state.operatorSnapshotLoading -> "Loading..."
    state.operatorSnapshotError.isNotBlank() -> "Unavailable"
    else -> "Waiting"
  }
}

private fun humanizeStatus(raw: String): String {
  val normalized = raw.trim()
  if (normalized.isBlank()) return ""
  return normalized.split('_', '-', ' ')
    .filter { it.isNotBlank() }
    .joinToString(" ") { token ->
      token.lowercase().replaceFirstChar { ch -> if (ch.isLowerCase()) ch.titlecase() else ch.toString() }
    }
}

private fun frontDoorStateLabel(raw: String): String {
  return when (raw.trim().lowercase()) {
    "failed" -> "FAILED"
    "blocked", "evidence_missing", "ambiguous_artifact", "interrupted", "interrupted_recovering", "host_down_unreachable", "unreachable" -> "BLOCKED"
    "needs_input", "waiting_on_operator" -> "NEEDS_INPUT"
    "stalled", "stale" -> "STALLED"
    "degraded", "conflicted", "partial_restore_degraded" -> "DEGRADED"
    "retrying" -> "RETRYING"
    "active", "running", "waiting_on_job", "waiting_on_verification" -> "RUNNING"
    "scheduled" -> "SCHEDULED"
    "waiting" -> "WAITING"
    "completed", "done" -> "COMPLETED"
    else -> humanizeStatus(raw).uppercase()
  }
}

private fun taskFrontDoorStateLabel(
  task: OperatorSnapshotTask?,
  sectionItem: OperatorSnapshotSectionItem?,
): String {
  if (task == null) {
    return frontDoorStateLabel(sectionItem?.status.orEmpty())
  }
  val taskStatusLabel = frontDoorStateLabel(task.status.ifBlank { sectionItem?.status.orEmpty() })
  if (taskStatusLabel in listOf("FAILED", "BLOCKED", "NEEDS_INPUT", "COMPLETED")) {
    return taskStatusLabel
  }
  return when (task.currentRunState.trim().lowercase()) {
    "active", "running" -> "RUNNING"
    "requested", "waiting" -> "WAITING"
    "retrying" -> "RETRYING"
    "failed", "blocked", "conflict" -> "BLOCKED"
    "stale" -> "STALLED"
    else -> when (task.status.trim().lowercase()) {
      "waiting_on_job", "waiting_on_verification" -> if (task.currentRunId.isBlank()) "WAITING" else "RUNNING"
      else -> taskStatusLabel
    }
  }
}

private fun frontDoorBucket(stateLabel: String): FrontDoorBucket {
  return when (stateLabel) {
    "SCHEDULED", "WAITING", "RETRYING" -> FrontDoorBucket.SCHEDULED
    "RUNNING" -> FrontDoorBucket.RUNNING
    else -> FrontDoorBucket.ATTENTION
  }
}

private fun buildFrontDoorMetadata(
  workItem: com.eiel.chat.data.OperatorSnapshotWorkItem?,
): List<String> {
  if (workItem == null) return emptyList()
  val items = mutableListOf<String>()
  if (workItem.confidenceLabel.isNotBlank()) items += "Confidence ${workItem.confidenceLabel}"
  if (workItem.timingLabel.isNotBlank()) items += workItem.timingLabel
  if (workItem.retryLabel.isNotBlank()) items += workItem.retryLabel
  if (workItem.artifacts.isNotEmpty()) items += "${workItem.artifacts.size} artifact${if (workItem.artifacts.size == 1) "" else "s"}"
  return items
}

private fun buildGroupedFailureSummary(items: List<OperatorFrontDoorItem>): String {
  val repeated = items
    .map { it.reason.trim() }
    .filter { it.isNotBlank() && it != "No reason recorded." }
    .groupingBy { it }
    .eachCount()
    .entries
    .filter { it.value >= 2 }
    .maxByOrNull { it.value }
    ?: return ""
  return "${repeated.value} tasks share the same reason: ${repeated.key}"
}

private fun buildFrontDoorStageLabel(task: OperatorSnapshotTask?): String {
  if (task == null) return ""
  val progressSummary = task.runCheckpoint.progressSummary.trim()
  val progressPercent = task.runCheckpoint.progressPercent
  if (progressSummary.isNotBlank() && progressPercent != null) {
    return "$progressSummary ($progressPercent%)"
  }
  if (progressSummary.isNotBlank()) return progressSummary
  return humanizeStatus(task.currentRunState)
}

private fun buildFrontDoorExecutionLabel(task: OperatorSnapshotTask?): String {
  if (task == null || !task.delegated) return ""
  val lifecycle = when (taskFrontDoorStateLabel(task, null)) {
    "SCHEDULED", "WAITING", "RETRYING" -> "Scheduled"
    "RUNNING" -> "Running"
    else -> "Attention"
  }
  return "$lifecycle (Pipeline)"
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun OperatorTaskDetailSheet(
  workItem: com.eiel.chat.data.OperatorSnapshotWorkItem,
  onDismiss: () -> Unit,
) {
  ModalBottomSheet(onDismissRequest = onDismiss) {
    SelectionContainer {
      Column(
        modifier = Modifier
          .fillMaxWidth()
          .padding(horizontal = 20.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
      ) {
        Text(workItem.stableTitle, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        if (workItem.state.isNotBlank()) {
          Text(frontDoorStateLabel(workItem.state), style = ChatLabelTextStyle, color = frontDoorAccent(frontDoorStateLabel(workItem.state)))
        }
        DetailBlock("What", workItem.reason.ifBlank { "No reason recorded." })
        DetailBlock(
          "Action",
          workItem.recommendedActionPrompt.ifBlank { workItem.nextActionPrompt }.ifBlank { "No next action recorded." },
          emphasis = true,
        )
        val detailMetadata = listOfNotNull(
          workItem.confidenceLabel.takeIf { it.isNotBlank() }?.let { "Confidence $it" },
          workItem.timingLabel.takeIf { it.isNotBlank() },
          workItem.retryLabel.takeIf { it.isNotBlank() },
          workItem.executionSurface.takeIf { it.isNotBlank() },
        )
        if (detailMetadata.isNotEmpty()) {
          DetailBlock("Metadata", detailMetadata.joinToString(" · "))
        }
        if (workItem.recommendedActionRisk.isNotBlank() || workItem.recommendedActionConsequence.isNotBlank()) {
          val riskBits = buildList {
            if (workItem.recommendedActionRisk.isNotBlank()) add("Risk ${humanizeStatus(workItem.recommendedActionRisk)}")
            if (workItem.recommendedActionConsequence.isNotBlank()) add(workItem.recommendedActionConsequence)
          }
          DetailBlock("Why / Risk", riskBits.joinToString(" — "))
        }
        if (workItem.artifacts.isNotEmpty()) {
          DetailBlock("Artifacts", workItem.artifacts.joinToString("\n"))
        }
        Box(modifier = Modifier.padding(bottom = 16.dp))
      }
    }
  }
}

@Composable
private fun DetailBlock(
  title: String,
  body: String,
  emphasis: Boolean = false,
) {
  Column(
    modifier = Modifier
      .fillMaxWidth()
      .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f), RoundedCornerShape(16.dp))
      .padding(12.dp),
    verticalArrangement = Arrangement.spacedBy(4.dp),
  ) {
    Text(title, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Text(
      body,
      style = if (emphasis) MaterialTheme.typography.bodyMedium else ChatLabelTextStyle,
      fontWeight = if (emphasis) FontWeight.Medium else FontWeight.Normal,
    )
  }
}

@Composable
private fun FrontDoorSection(
  title: String,
  emptyLabel: String,
  items: List<OperatorFrontDoorItem>,
  accent: Color,
  onOpenDetail: (OperatorFrontDoorItem) -> Unit,
) {
  Card(modifier = Modifier.fillMaxWidth()) {
    Column(
      modifier = Modifier
        .fillMaxWidth()
        .padding(CompactCardPadding),
      verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
    ) {
      Text(title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
      if (items.isEmpty()) {
        Text(emptyLabel, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
      } else {
        items.forEach { item ->
          FrontDoorTaskCard(item = item, accent = accent, onOpenDetail = { onOpenDetail(item) })
        }
      }
    }
  }
}

@Composable
private fun FrontDoorTaskCard(
  item: OperatorFrontDoorItem,
  accent: Color,
  onOpenDetail: () -> Unit,
) {
  val stateAccent = frontDoorAccent(item.stateLabel)
  SelectionContainer {
    Column(
      modifier = Modifier
        .fillMaxWidth()
        .border(1.dp, stateAccent.copy(alpha = 0.35f), RoundedCornerShape(16.dp))
        .clickable(onClick = onOpenDetail)
        .padding(12.dp),
      verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
      Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
      ) {
        Text(
          item.title,
          style = MaterialTheme.typography.titleSmall,
          fontWeight = FontWeight.SemiBold,
          modifier = Modifier.weight(1f, fill = false),
        )
        Text(item.stateLabel, style = ChatLabelTextStyle, color = stateAccent, fontWeight = FontWeight.SemiBold)
      }
      Text(
        item.reason,
        style = MaterialTheme.typography.bodyMedium,
        maxLines = 2,
        overflow = TextOverflow.Ellipsis,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
      )
      Text(
        item.nextAction,
        style = MaterialTheme.typography.bodyMedium,
        fontWeight = if (item.recommended) FontWeight.Medium else FontWeight.Normal,
      )
      if (item.stageLabel.isNotBlank()) {
        Text("Stage: ${item.stageLabel}", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
      }
      if (item.executionLabel.isNotBlank()) {
        Text(item.executionLabel, style = ChatLabelTextStyle, color = stateAccent, fontWeight = FontWeight.SemiBold)
      }
      if (item.metadata.isNotEmpty()) {
        Text(
          item.metadata.joinToString(" · "),
          style = ChatLabelTextStyle,
          color = MaterialTheme.colorScheme.onSurfaceVariant,
          maxLines = 2,
          overflow = TextOverflow.Ellipsis,
        )
      }
    }
  }
}

@Composable
private fun frontDoorAccent(stateLabel: String): Color {
  val colorScheme = MaterialTheme.colorScheme
  return when (stateLabel) {
    "FAILED" -> colorScheme.error
    "BLOCKED", "DEGRADED", "STALLED", "NEEDS_INPUT", "RETRYING" -> Color(0xFFD99124)
    "RUNNING" -> Color(0xFF3178C6)
    "COMPLETED" -> Color(0xFF2E8B57)
    else -> colorScheme.onSurfaceVariant
  }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SnapshotNotificationBanner(
  notification: OperatorSnapshotNotification,
  actions: List<FrontDoorNotificationAction> = emptyList(),
) {
  val colorScheme = MaterialTheme.colorScheme
  val tint = when (notification.severity) {
    "danger" -> colorScheme.error
    "warning" -> colorScheme.tertiary
    else -> colorScheme.primary
  }
  Column(
    modifier = Modifier
      .fillMaxWidth()
      .background(tint.copy(alpha = 0.10f), RoundedCornerShape(16.dp))
      .border(1.dp, tint.copy(alpha = 0.25f), RoundedCornerShape(16.dp))
      .padding(10.dp),
    verticalArrangement = Arrangement.spacedBy(4.dp),
  ) {
    Text(notification.headline, style = ChatLabelTextStyle, fontWeight = FontWeight.SemiBold, color = tint)
    Text(notification.message, style = ChatLabelTextStyle, color = colorScheme.onSurfaceVariant)
    if (actions.isNotEmpty()) {
      FlowRow(
        horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
        verticalArrangement = Arrangement.spacedBy(CompactInlineGap),
      ) {
        actions.forEach { action ->
          SmallButton(action.label, action.onClick)
        }
      }
    }
  }
}

@Composable
private fun AgentOverviewCard(
  tab: AgentTab,
  state: AgentChatState,
  selected: Boolean,
  onOpen: () -> Unit,
) {
  val colorScheme = MaterialTheme.colorScheme
  val accent = when (tab) {
    AgentTab.PLANE -> Color(0xFF4F8CFF)
    AgentTab.ALL -> Color(0xFFFFB166)
    AgentTab.ALL_2 -> Color(0xFFF48C6C)
    AgentTab.ALL_3 -> Color(0xFFE06C75)
    AgentTab.ORCHESTRATOR -> Color(0xFF66A3FF)
    AgentTab.BUILDER -> Color(0xFF66A3FF)
    AgentTab.VERIFIER -> Color(0xFF7BF1A8)
  }
  val lastAssistant = state.messages.lastOrNull { it.role == MessageRole.ASSISTANT && it.content.isNotBlank() }?.content.orEmpty()
  val lastUser = state.messages.lastOrNull { it.role == MessageRole.USER && it.content.isNotBlank() }?.content.orEmpty()
  val unread = state.hasUnread
  val busyLabel = busyStatusLabel(state)

  Card(
    modifier = Modifier
      .fillMaxWidth()
      .border(
        width = 1.dp,
        color = when {
          selected -> accent
          unread -> accent.copy(alpha = 0.85f)
          else -> colorScheme.outline.copy(alpha = 0.7f)
        },
        shape = RoundedCornerShape(18.dp),
      ),
  ) {
    Column(
      modifier = Modifier
        .fillMaxWidth()
        .padding(12.dp),
      verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
      Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
      ) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
          Box(
            modifier = Modifier
              .background(accent.copy(alpha = 0.18f), RoundedCornerShape(999.dp))
              .padding(horizontal = 8.dp, vertical = 4.dp),
          ) {
            Text(tab.label, style = ChatLabelTextStyle, color = accent, fontWeight = FontWeight.SemiBold)
          }
          if (unread) {
            Text("Unread", style = ChatLabelTextStyle, color = accent, fontWeight = FontWeight.SemiBold)
          }
          if (busyLabel != null) {
            Text(busyLabel, style = ChatLabelTextStyle, color = colorScheme.primary, fontWeight = FontWeight.SemiBold)
          }
        }
        SmallButton("Open", onOpen)
      }

      Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        MiniMetric("Messages", state.messages.size.toString())
        MiniMetric("Thread", state.threadId)
        MiniMetric("Failed", if (state.lastError.isNullOrBlank()) "0" else "1")
      }

      if (lastUser.isNotBlank()) {
        PreviewBlock(
          title = "Last you said",
          text = lastUser,
        )
      }
      if (lastAssistant.isNotBlank()) {
        PreviewBlock(
          title = "Last reply",
          text = lastAssistant,
        )
      }
      if (state.lastError != null) {
        Text(
          state.lastError,
          style = ChatLabelTextStyle,
          color = colorScheme.error,
        )
      }
    }
  }
}

@Composable
private fun MiniMetric(
  label: String,
  value: String,
) {
  val colorScheme = MaterialTheme.colorScheme
  Box(
    modifier = Modifier
      .background(colorScheme.surfaceVariant, RoundedCornerShape(14.dp))
      .padding(horizontal = 8.dp, vertical = 6.dp),
  ) {
    Column {
      Text(label, style = ChatLabelTextStyle, color = colorScheme.onSurfaceVariant)
      Text(value, style = ChatLabelTextStyle, fontWeight = FontWeight.SemiBold)
    }
  }
}

@Composable
private fun PreviewBlock(
  title: String,
  text: String,
) {
  val colorScheme = MaterialTheme.colorScheme
  Column(
    modifier = Modifier
      .fillMaxWidth()
      .adminFieldSurface()
      .padding(10.dp),
    verticalArrangement = Arrangement.spacedBy(4.dp),
  ) {
    Text(title, style = ChatLabelTextStyle, color = colorScheme.onSurfaceVariant)
    Text(
      text = text,
      style = ChatLabelTextStyle,
      maxLines = 4,
      overflow = TextOverflow.Ellipsis,
    )
  }
}

@Composable
private fun OverviewMeta(
  label: String,
  value: String,
) {
  Column(
    modifier = Modifier.fillMaxWidth(),
    verticalArrangement = Arrangement.spacedBy(2.dp),
  ) {
    Text(label, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Text(
      value,
      style = ChatLabelTextStyle,
      fontWeight = FontWeight.SemiBold,
      maxLines = 2,
      overflow = TextOverflow.Ellipsis,
    )
  }
}
