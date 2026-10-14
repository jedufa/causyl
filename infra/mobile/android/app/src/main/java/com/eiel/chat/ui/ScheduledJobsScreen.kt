package com.eiel.chat.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.eiel.chat.data.ScheduledJobDetail
import com.eiel.chat.data.ScheduledJobSummary
import com.eiel.chat.data.SchedulerServiceStatus
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

internal enum class ScheduledJobSurfaceStatus(
  val label: String,
  val sortRank: Int,
) {
  RUNNING("Active", 0),
  DEGRADED("Degraded", 1),
  FAILED("Failed", 2),
  BLOCKED("Blocked", 3),
  WAITING("Dependency wait", 4),
  PENDING("Pending", 5),
  SUCCEEDED("Succeeded", 6),
  UNKNOWN("Unknown", 7),
}

internal data class ScheduledJobProjection(
  val status: ScheduledJobSurfaceStatus,
  val reason: String,
  val detail: String,
  val heartbeat: String,
  val startUtc: String,
  val finishUtc: String,
  val elapsed: String,
  val boundPisUid: String,
  val boundPisPath: String,
  val boundPisTitle: String,
  val receiptPath: String,
  val reportPath: String,
)

internal data class ScheduledJobBranchSection(
  val key: String,
  val title: String,
  val subtitle: String,
  val jobs: List<ScheduledJobSummary>,
  val indentByJobId: Map<String, Int>,
)

internal data class VerificationWorkflowRunCard(
  val key: String,
  val title: String,
  val subtitle: String,
  val summary: String,
  val overallStatus: ScheduledJobSurfaceStatus,
  val phases: List<VerificationWorkflowRunPhase>,
)

internal data class VerificationWorkflowRunPhase(
  val jobId: String,
  val title: String,
  val status: ScheduledJobSurfaceStatus,
  val statusDetail: String,
)

private data class ScheduledJobEditTarget(
  val jobId: String,
  val title: String,
  val model: String,
  val maxIterations: Int?,
  val sourceLabel: String,
)

private enum class ScheduledJobFilter(
  val label: String,
) {
  ALL("All"),
  RUNNING("Active"),
  FAILED("Failed"),
  BLOCKED("Blocked"),
  PENDING("Pending"),
  SUCCEEDED("OK");

  companion object {
    fun fromName(raw: String): ScheduledJobFilter {
      return entries.firstOrNull { it.name == raw } ?: ALL
    }
  }
}

private val ViennaZoneId: ZoneId = ZoneId.of("Europe/Vienna")
private val ViennaDateTimeFormatter: DateTimeFormatter =
  DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm z", Locale.US).withZone(ViennaZoneId)

@Composable
fun SchedulesRoute(vm: AdminViewModel = viewModel()) {
  val state by vm.uiState.collectAsStateWithLifecycle()
  var activeRepoFilePath by remember { mutableStateOf<String?>(null) }

  LaunchedEffect(Unit) {
    vm.refreshScheduledJobs()
  }

  val openFileReference: (String) -> Unit = { path ->
    val safePath = normalizeScheduledJobFileReference(path)
    if (safePath.isNotBlank()) {
      activeRepoFilePath = safePath
      vm.viewFsFile(safePath)
    }
  }

  ScheduledJobsScreen(
    state = state,
    onRefresh = vm::refreshScheduledJobs,
    onOpenJob = vm::openScheduledJob,
    onCloseJob = vm::closeScheduledJobDetail,
    onRefreshSelectedJob = { vm.refreshScheduledJobDetail() },
    onUpdateJobRunProfile = vm::updateScheduledJobRunProfile,
    onRunJobNow = vm::runScheduledJobNow,
    onOpenFileReference = openFileReference,
  )

  activeRepoFilePath?.let { requestedPath ->
    val viewerPath = if (state.fsPreviewPath.isNotBlank()) state.fsPreviewPath else requestedPath
    val viewerLoading =
      state.fsPreviewLoading ||
        (state.fsPreviewPath != requestedPath && state.fsPreviewText.isBlank() && state.fsPreviewError.isBlank())
    val viewerError =
      if (state.fsPreviewPath == requestedPath || state.fsPreviewError.isNotBlank()) state.fsPreviewError else ""
    RepoFileViewerDialog(
      path = viewerPath,
      text = state.fsPreviewText,
      truncated = state.fsPreviewTruncated,
      loading = viewerLoading,
      error = viewerError,
      onDismiss = { activeRepoFilePath = null },
      onOpenFileReference = openFileReference,
    )
  }
}

@Composable
fun PipelinesRoute(vm: AdminViewModel = viewModel()) {
  val state by vm.uiState.collectAsStateWithLifecycle()
  var activeRepoFilePath by remember { mutableStateOf<String?>(null) }
  var pipelineOpenError by remember { mutableStateOf<String?>(null) }

  fun openPipelinePis(jobId: String) {
    val safeJobId = jobId.trim()
    if (safeJobId.isBlank()) {
      pipelineOpenError = null
      activeRepoFilePath = null
      return
    }
      pipelineOpenError = null
      activeRepoFilePath = null
      vm.openPipelineScheduledJobPis(
        jobId = safeJobId,
        onResolved = { path ->
          val normalizedPath = path.trim()
          if (normalizedPath.isBlank()) {
            pipelineOpenError = "No bound PIS path available for job $safeJobId."
            return@openPipelineScheduledJobPis
          }
          val safePath = normalizeScheduledJobFileReference(normalizedPath)
          activeRepoFilePath = safePath
          vm.viewFsFile(safePath)
        },
      onError = { message ->
        pipelineOpenError = if (message.isBlank()) "No bound PIS file available for job $safeJobId." else message
      },
    )
  }

  LaunchedEffect(Unit) {
    vm.refreshScheduledJobs()
  }

  Surface(
    modifier = Modifier.fillMaxSize(),
    color = MaterialTheme.colorScheme.background,
  ) {
    val nowUtc = state.scheduledJobsFetchedUtc.ifBlank { Instant.now().toString() }
    LazyColumn(
      modifier = Modifier.fillMaxSize(),
      contentPadding = compactScreenContentPadding(),
      verticalArrangement = Arrangement.spacedBy(CompactSectionGap),
    ) {
      item {
        VerificationWorkflowRunCards(
          jobs = state.scheduledJobs,
          nowUtc = nowUtc,
          onJobSelected = { jobId -> vm.openPipelineScheduledJob(jobId) },
        )
      }
      item {
        ScheduledPipelineBoardCard(
          jobs = state.scheduledJobs,
          nowUtc = nowUtc,
          onJobSelected = { jobId -> openPipelinePis(jobId) },
          defaultExpanded = true,
        )
      }
      item {
        SmallButton("Refresh jobs", onClick = vm::refreshScheduledJobs)
      }
    }
  }

  if (activeRepoFilePath != null || pipelineOpenError != null) {
    val requestedPath = activeRepoFilePath.orEmpty()
    val viewerPath = if (state.fsPreviewPath.isNotBlank()) state.fsPreviewPath else requestedPath
    val viewerLoading =
      if (activeRepoFilePath == null) {
        false
      } else {
        state.fsPreviewLoading ||
          (state.fsPreviewPath != requestedPath && state.fsPreviewText.isBlank() && state.fsPreviewError.isBlank())
      }
    val viewerError = when {
      pipelineOpenError != null -> pipelineOpenError.orEmpty()
      state.fsPreviewPath == requestedPath || state.fsPreviewError.isNotBlank() -> state.fsPreviewError
      else -> ""
    }
    RepoFileViewerDialog(
      path = viewerPath,
      text = if (pipelineOpenError == null) state.fsPreviewText else "",
      truncated = if (pipelineOpenError == null) state.fsPreviewTruncated else false,
      loading = viewerLoading,
      error = viewerError,
      onDismiss = {
        activeRepoFilePath = null
        pipelineOpenError = null
      },
      onOpenFileReference = vm::viewFsFile,
    )
  }
}

internal fun normalizeScheduledJobFileReference(path: String): String {
  return normalizeRepoFsReference(path)
}

@Composable
private fun ScheduledJobsScreen(
  state: AdminUiState,
  onRefresh: () -> Unit,
  onOpenJob: (String) -> Unit,
  onCloseJob: () -> Unit,
  onRefreshSelectedJob: () -> Unit,
  onUpdateJobRunProfile: (String, String, Int?, Boolean) -> Unit,
  onRunJobNow: (String) -> Unit,
  onOpenFileReference: (String) -> Unit,
) {
  val nowUtc = state.scheduledJobsFetchedUtc.ifBlank { Instant.now().toString() }
  var selectedFilterName by rememberSaveable { mutableStateOf(ScheduledJobFilter.ALL.name) }
  var editTarget by remember { mutableStateOf<ScheduledJobEditTarget?>(null) }
  val selectedFilter = remember(selectedFilterName) { ScheduledJobFilter.fromName(selectedFilterName) }
  val sortedJobs = remember(state.scheduledJobs, nowUtc) { sortScheduledJobsForMobile(state.scheduledJobs, nowUtc) }
  val filteredJobs = remember(sortedJobs, selectedFilter, nowUtc) {
    sortedJobs.filter { job ->
      val status = projectScheduledJob(job, nowUtc).status
      matchesScheduledJobFilter(status = status, filter = selectedFilter)
    }
  }
  val selectedDetail = state.scheduledJobDetail
  val listState = rememberLazyListState()
  BackHandler(enabled = state.selectedScheduledJobId.isNotBlank()) { onCloseJob() }

  Surface(
    modifier = Modifier.fillMaxSize(),
    color = MaterialTheme.colorScheme.background,
  ) {
    if (state.selectedScheduledJobId.isBlank()) {
      ScheduledJobsListScreen(
        jobs = filteredJobs,
        allJobs = sortedJobs,
        selectedFilter = selectedFilter,
        scheduler = state.scheduledScheduler,
        message = state.scheduledJobsMessage,
        nowUtc = nowUtc,
        listState = listState,
        onRefresh = onRefresh,
        onOpenJob = onOpenJob,
        onRescheduleJob = { job -> editTarget = editTargetFor(job) },
        onFilterSelected = { selectedFilterName = it.name },
      )
    } else {
      ScheduledJobDetailScreen(
        detail = selectedDetail,
        jobs = sortedJobs,
        loading = state.scheduledJobDetailLoading,
        error = state.scheduledJobDetailError,
        fetchedUtc = state.scheduledJobsFetchedUtc,
        onBack = onCloseJob,
        onRefresh = onRefreshSelectedJob,
        onEditJob = { job -> editTarget = editTargetFor(job) },
        onRunNow = onRunJobNow,
        actionInFlight = state.scheduledJobActionInFlight,
        actionLabel = state.scheduledJobActionLabel,
        onRescheduleReceipt = { job, _ -> editTarget = editTargetFor(job) },
        onOpenFileReference = onOpenFileReference,
      )
    }
  }

  editTarget?.let { target ->
    ScheduledJobEditDialog(
      target = target,
      inFlight = state.scheduledJobActionInFlight,
      actionLabel = state.scheduledJobActionLabel,
      onDismiss = { if (!state.scheduledJobActionInFlight) editTarget = null },
      onSave = { model, maxIterations, runNow ->
        onUpdateJobRunProfile(target.jobId, model, maxIterations, runNow)
        editTarget = null
      },
    )
  }
}

@Composable
private fun ScheduledJobsListScreen(
  jobs: List<ScheduledJobSummary>,
  allJobs: List<ScheduledJobSummary>,
  selectedFilter: ScheduledJobFilter,
  scheduler: SchedulerServiceStatus,
  message: String,
  nowUtc: String,
  listState: androidx.compose.foundation.lazy.LazyListState,
  onRefresh: () -> Unit,
  onOpenJob: (String) -> Unit,
  onRescheduleJob: (ScheduledJobSummary) -> Unit,
  onFilterSelected: (ScheduledJobFilter) -> Unit,
) {
  val projections = remember(jobs, nowUtc) { jobs.associate { it.jobId to projectScheduledJob(it, nowUtc) } }
  val counts = remember(allJobs, nowUtc) { scheduledJobStatusCounts(allJobs, nowUtc) }
  val branchSections = remember(jobs, allJobs, nowUtc) {
    buildScheduledJobBranchSections(displayJobs = jobs, graphJobs = allJobs, nowUtc = nowUtc)
  }

  LazyColumn(
    state = listState,
    modifier = Modifier.fillMaxSize(),
    contentPadding = compactScreenContentPadding(),
    verticalArrangement = Arrangement.spacedBy(CompactSectionGap),
  ) {
    item(key = "scheduler") {
      AdminCard(title = "Scheduler") {
        Row(
          modifier = Modifier.fillMaxWidth(),
          horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
          verticalAlignment = Alignment.CenterVertically,
        ) {
          SchedulerStatusLight(running = scheduler.running)
          ScheduleStatusChip(
            text = if (scheduler.running) "Running" else "Stopped",
            accent = if (scheduler.running) Color(0xFF2E9D54) else MaterialTheme.colorScheme.error,
            highlight = scheduler.running,
          )
          Spacer(modifier = Modifier.weight(1f))
          SmallButton("Refresh", onRefresh)
        }
        if (scheduler.lastError.isNotBlank()) {
          Text(
            text = "Scheduler error: ${scheduler.lastError}",
            style = ChatLabelTextStyle,
            color = MaterialTheme.colorScheme.error,
          )
        }
        if (message.isNotBlank()) {
          Text(
            text = message,
            style = ChatLabelTextStyle,
            color = MaterialTheme.colorScheme.error,
          )
        }
      }
    }

    item(key = "glance") {
      AdminCard(title = "At a glance") {
        Text(
          text = "Tap a status to filter. Active branches stay on top, and each branch keeps its own next jobs in order. All times are Vienna time.",
          style = ChatLabelTextStyle,
          color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap), modifier = Modifier.fillMaxWidth()) {
          SummaryFact(
            label = "Active",
            value = counts[ScheduledJobSurfaceStatus.RUNNING] ?: 0,
            accent = Color(0xFF1F8EFA),
            selected = selectedFilter == ScheduledJobFilter.RUNNING,
            modifier = Modifier.weight(1f),
            onClick = { onFilterSelected(if (selectedFilter == ScheduledJobFilter.RUNNING) ScheduledJobFilter.ALL else ScheduledJobFilter.RUNNING) },
          )
          SummaryFact(
            label = "Failed",
            value = (counts[ScheduledJobSurfaceStatus.FAILED] ?: 0) + (counts[ScheduledJobSurfaceStatus.DEGRADED] ?: 0),
            accent = Color(0xFFD1495B),
            selected = selectedFilter == ScheduledJobFilter.FAILED,
            modifier = Modifier.weight(1f),
            onClick = { onFilterSelected(if (selectedFilter == ScheduledJobFilter.FAILED) ScheduledJobFilter.ALL else ScheduledJobFilter.FAILED) },
          )
          SummaryFact(
            label = "Pending",
            value = (counts[ScheduledJobSurfaceStatus.PENDING] ?: 0) + (counts[ScheduledJobSurfaceStatus.WAITING] ?: 0),
            accent = Color(0xFF4D6FBD),
            selected = selectedFilter == ScheduledJobFilter.PENDING,
            modifier = Modifier.weight(1f),
            onClick = { onFilterSelected(if (selectedFilter == ScheduledJobFilter.PENDING) ScheduledJobFilter.ALL else ScheduledJobFilter.PENDING) },
          )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap), modifier = Modifier.fillMaxWidth()) {
          SummaryFact(
            label = "Blocked",
            value = counts[ScheduledJobSurfaceStatus.BLOCKED] ?: 0,
            accent = Color(0xFFC97A00),
            selected = selectedFilter == ScheduledJobFilter.BLOCKED,
            modifier = Modifier.weight(1f),
            onClick = { onFilterSelected(if (selectedFilter == ScheduledJobFilter.BLOCKED) ScheduledJobFilter.ALL else ScheduledJobFilter.BLOCKED) },
          )
          SummaryFact(
            label = "OK",
            value = counts[ScheduledJobSurfaceStatus.SUCCEEDED] ?: 0,
            accent = Color(0xFF2E9D54),
            selected = selectedFilter == ScheduledJobFilter.SUCCEEDED,
            modifier = Modifier.weight(1f),
            onClick = { onFilterSelected(if (selectedFilter == ScheduledJobFilter.SUCCEEDED) ScheduledJobFilter.ALL else ScheduledJobFilter.SUCCEEDED) },
          )
          SummaryFact(
            label = "All",
            value = allJobs.size,
            accent = MaterialTheme.colorScheme.primary,
            selected = selectedFilter == ScheduledJobFilter.ALL,
            modifier = Modifier.weight(1f),
            onClick = { onFilterSelected(ScheduledJobFilter.ALL) },
          )
        }
      }
    }

    if (jobs.isEmpty()) {
      item(key = "empty") {
        AdminCard(title = "Scheduled jobs") {
          Text(
            text = when {
              message.isNotBlank() -> message
              selectedFilter == ScheduledJobFilter.ALL -> "No scheduled jobs are available."
              else -> "No ${selectedFilter.label.lowercase(Locale.US)} jobs right now."
            },
            style = ChatBodyTextStyle,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
          )
        }
      }
    } else {
      item(key = "scheduled-title") {
        Text(
          text = "Scheduled jobs",
          style = MaterialTheme.typography.titleMedium,
          fontWeight = FontWeight.SemiBold,
          color = MaterialTheme.colorScheme.onSurface,
        )
      }
      items(
        items = branchSections,
        key = { section -> section.key },
      ) { section ->
        ScheduledJobBranchSectionCard(
          section = section,
          projections = projections,
          nowUtc = nowUtc,
          onOpenJob = onOpenJob,
          onRescheduleJob = onRescheduleJob,
        )
      }
    }
  }
}

@Composable
private fun ScheduledJobDetailScreen(
  detail: ScheduledJobDetail?,
  jobs: List<ScheduledJobSummary>,
  loading: Boolean,
  error: String,
  fetchedUtc: String,
  onBack: () -> Unit,
  onRefresh: () -> Unit,
  onEditJob: (ScheduledJobSummary) -> Unit,
  onRunNow: (String) -> Unit,
  actionInFlight: Boolean,
  actionLabel: String,
  onRescheduleReceipt: (ScheduledJobSummary, com.eiel.chat.data.ScheduledJobReceipt) -> Unit,
  onOpenFileReference: (String) -> Unit,
) {
  val current = detail?.job
  val nowUtc = detail?.fetchedUtc?.ifBlank { fetchedUtc }.orEmpty().ifBlank { Instant.now().toString() }
  val projection = current?.let { projectScheduledJob(it, nowUtc) }
  val dependencyRows = remember(jobs, current?.dependsOn) {
    current?.dependsOn.orEmpty().map { depId ->
      depId to jobs.firstOrNull { it.jobId == depId }
    }
  }

  LazyColumn(
    modifier = Modifier.fillMaxSize(),
    contentPadding = compactScreenContentPadding(),
    verticalArrangement = Arrangement.spacedBy(CompactSectionGap),
  ) {
    item {
      AdminCard(title = "Job detail") {
        Row(
          modifier = Modifier.fillMaxWidth(),
          horizontalArrangement = Arrangement.SpaceBetween,
          verticalAlignment = Alignment.CenterVertically,
        ) {
          Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap), verticalAlignment = Alignment.CenterVertically) {
            SmallButton("Back", onBack)
            if (projection != null) {
              ScheduleStatusChip(
                text = projection.status.label,
                accent = statusAccentColor(projection.status),
                highlight = projection.status == ScheduledJobSurfaceStatus.RUNNING,
              )
            }
          }
          SmallButton(if (loading) "Refreshing..." else "Refresh", onRefresh, enabled = !loading)
        }
        Text(
          text = current?.let(::displayJobTitle) ?: "Loading scheduled job...",
          style = MaterialTheme.typography.titleMedium,
          fontWeight = FontWeight.SemiBold,
        )
        if (current != null) {
          Text(
            text = current.jobId,
            style = ChatLabelTextStyle,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
          )
        }
        if (!projection?.reason.isNullOrBlank()) {
          Text(
            text = projection?.reason.orEmpty(),
            style = ChatBodyTextStyle,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
          )
        }
        if (error.isNotBlank()) {
          Text(
            text = error,
            style = ChatLabelTextStyle,
            color = MaterialTheme.colorScheme.error,
          )
        }
        if (current != null) {
          Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
            SmallButton(
              "Tune run",
              onClick = { onEditJob(current) },
              enabled = !actionInFlight,
            )
            SmallButton(
              if (actionInFlight && actionLabel.isNotBlank()) actionLabel else "Run now",
              onClick = { onRunNow(current.jobId) },
              enabled = !actionInFlight,
            )
          }
        }
      }
    }

    if (current != null && projection != null) {
      item {
        DetailFactsCard(title = "Truth") {
          DetailFactRow("PIS title", displayJobTitle(current))
          DetailFactRow("Binding", bindingLabel(current))
          DetailFactRow("Model", current.runtime.model.ifBlank { "default" })
          if (current.jobKind != "single_turn") {
            DetailFactRow("Max iterations", current.loop.maxIterations.takeIf { it > 0 }?.toString() ?: "default")
          }
          DetailFactRow("Current", projection.status.label)
          DetailFactRow("Execution heartbeat", projection.heartbeat)
          DetailFactRow("Due", humanizeScheduledDueReason(current.due.reason))
          DetailFactRow("Last status", current.state.lastStatus.ifBlank { "none yet" })
          DetailFactRow("Updated", formatViennaTime(current.state.updatedUtc))
        }
      }

      item {
        DetailFactsCard(title = "Timing") {
          DetailFactRow("Start", formatViennaTime(projection.startUtc).ifBlank { "Unavailable" })
          DetailFactRow(
            "Finish",
            formatViennaTime(projection.finishUtc).ifBlank {
              if (projection.status == ScheduledJobSurfaceStatus.RUNNING || projection.status == ScheduledJobSurfaceStatus.DEGRADED) {
                "In progress"
              } else {
                "Unavailable"
              }
            },
          )
          DetailFactRow("Elapsed", projection.elapsed.ifBlank { "Unavailable" })
          DetailFactRow("Trigger", current.state.lastTrigger.ifBlank { "Not run yet" })
        }
      }

      item {
        DetailFactsCard(title = "Bound PIS") {
          DetailFactRow("UID", projection.boundPisUid.ifBlank { bindingLabel(current) })
          if (projection.boundPisPath.isNotBlank()) {
            Text(
              text = projection.boundPisTitle.ifBlank { displayJobTitle(current) },
              style = ChatBodyTextStyle,
              color = MaterialTheme.colorScheme.primary,
              fontWeight = FontWeight.SemiBold,
              modifier = Modifier.clickable { onOpenFileReference(projection.boundPisPath) },
            )
          }
          if (projection.boundPisPath.isNotBlank()) {
            SmallButton("Read PIS", onClick = { onOpenFileReference(projection.boundPisPath) })
          }
        }
      }

      item {
        DetailFactsCard(title = "Dependencies") {
          if (dependencyRows.isEmpty()) {
            Text("No dependencies", style = ChatBodyTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
          } else {
            Column(verticalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
              dependencyRows.forEach { (depId, depJob) ->
                val depProjection = depJob?.let { projectScheduledJob(it, nowUtc) }
                DetailFactRow(
                  depId,
                  depProjection?.status?.label ?: "Missing",
                )
              }
            }
          }
        }
      }

      item {
        DetailFactsCard(title = "Evidence") {
          DetailFactRow("Receipt path", projection.receiptPath.ifBlank { "Unavailable" })
          DetailFactRow("Report path", projection.reportPath.ifBlank { "Unavailable" })
          Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
            SmallButton("Open receipt", onClick = { onOpenFileReference(projection.receiptPath) }, enabled = projection.receiptPath.isNotBlank())
            SmallButton("Open report", onClick = { onOpenFileReference(projection.reportPath) }, enabled = projection.reportPath.isNotBlank())
          }
        }
      }

      item {
        DetailFactsCard(title = "Recent receipts") {
          if (detail.receipts.isEmpty()) {
            Text("No receipts yet", style = ChatBodyTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
          } else {
            Column(verticalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
              detail.receipts.take(4).forEach { receipt ->
                val canReschedule = receipt.status.trim().lowercase(Locale.US) != "ok"
                Card(
                  colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                  modifier = Modifier
                    .fillMaxWidth()
                    .clickable(enabled = canReschedule && !actionInFlight) { onRescheduleReceipt(current, receipt) },
                ) {
                  Column(
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 10.dp),
                    verticalArrangement = Arrangement.spacedBy(CompactTightGap),
                  ) {
                    val line = buildString {
                      append(receipt.runId.ifBlank { "receipt" })
                      append(" • ")
                      append(receipt.status.ifBlank { "unknown" })
                      val finished = formatViennaTime(receipt.finishedUtc)
                      if (finished.isNotBlank()) {
                        append(" • ")
                        append(finished)
                      }
                    }
                    Text(line, style = ChatBodyTextStyle, color = MaterialTheme.colorScheme.onSurface)
                    if (receipt.error.isNotBlank()) {
                      Text(receipt.error, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.error)
                    }
                    if (canReschedule) {
                      Text(
                        text = if (actionInFlight) actionLabel.ifBlank { "Working..." } else "Tap to reschedule this failed run",
                        style = ChatLabelTextStyle,
                        color = MaterialTheme.colorScheme.primary,
                      )
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}

@Composable
private fun ScheduledJobBranchSectionCard(
  section: ScheduledJobBranchSection,
  projections: Map<String, ScheduledJobProjection>,
  nowUtc: String,
  onOpenJob: (String) -> Unit,
  onRescheduleJob: (ScheduledJobSummary) -> Unit,
) {
  Surface(
    shape = RoundedCornerShape(18.dp),
    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.34f),
  ) {
    Column(
      modifier = Modifier
        .fillMaxWidth()
        .padding(horizontal = 12.dp, vertical = 12.dp),
      verticalArrangement = Arrangement.spacedBy(CompactInlineGap),
    ) {
      Text(
        text = section.title,
        style = ChatBodyTextStyle,
        fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.onSurface,
      )
      if (section.subtitle.isNotBlank()) {
        Text(
          text = section.subtitle,
          style = ChatLabelTextStyle,
          color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
      }
      Column(verticalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
        section.jobs.forEach { job ->
          ScheduledJobRow(
            job = job,
            projection = projections.getValue(job.jobId),
            indentLevel = section.indentByJobId[job.jobId] ?: 0,
            nowUtc = nowUtc,
            onClick = { onOpenJob(job.jobId) },
            onReschedule = { onRescheduleJob(job) },
          )
        }
      }
    }
  }
}

@Composable
private fun ScheduledPipelineBoardCard(
  jobs: List<ScheduledJobSummary>,
  nowUtc: String,
  onJobSelected: (String) -> Unit,
  defaultExpanded: Boolean = false,
) {
  var expanded by rememberSaveable { mutableStateOf(defaultExpanded) }
  val board = remember(jobs, nowUtc) { buildPipelineBoard(jobs, nowUtc) }
  CollapsibleAdminCard(
    title = "Pipeline lanes",
    expanded = expanded,
    onToggle = { expanded = !expanded },
  ) {
    if (!expanded) {
      if (board.totalItems == 0) {
        Text(
          text = "No scheduled jobs are available.",
          style = ChatBodyTextStyle,
          color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        return@CollapsibleAdminCard
      }
      Text(
        text = "${board.lanes.size} lanes • ${board.totalItems} items",
        style = ChatBodyTextStyle,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
      )
      return@CollapsibleAdminCard
    }
    if (board.totalItems == 0) {
      Text(
        text = "No scheduled jobs are available.",
        style = ChatBodyTextStyle,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
      )
      return@CollapsibleAdminCard
    }
    val scroll = rememberScrollState()
    val maxHeight = pipelineBoardMaxHeight(board)
    Row(
      modifier = Modifier
        .fillMaxWidth()
        .heightIn(min = PipelineBoardMinHeight, max = maxHeight)
        .verticalScroll(scroll),
      horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
      board.lanes.forEachIndexed { index, lane ->
        PipelineLaneColumn(
          modifier = Modifier.weight(1f),
          title = "Pipe ${index + 1}",
          items = lane.items,
          onJobSelected = onJobSelected,
        )
      }
    }
  }
}

@Composable
private fun VerificationWorkflowRunCards(
  jobs: List<ScheduledJobSummary>,
  nowUtc: String,
  onJobSelected: (String) -> Unit,
) {
  val runCards = remember(jobs, nowUtc) { buildVerificationWorkflowRunCards(jobs, nowUtc) }
  CollapsibleAdminCard(
    title = "Verification workflow runs",
    expanded = true,
    onToggle = {},
  ) {
    if (runCards.isEmpty()) {
      Text(
        text = "No verification workflow runs are available.",
        style = ChatBodyTextStyle,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
      )
      return@CollapsibleAdminCard
    }
    Column(verticalArrangement = Arrangement.spacedBy(CompactSectionGap)) {
      runCards.forEach { card ->
        VerificationWorkflowRunCardView(
          card = card,
          onJobSelected = onJobSelected,
        )
      }
    }
  }
}

@Composable
private fun VerificationWorkflowRunCardView(
  card: VerificationWorkflowRunCard,
  onJobSelected: (String) -> Unit,
) {
  val accent = statusAccentColor(card.overallStatus)
  Card(
    modifier = Modifier.fillMaxWidth(),
    colors = CardDefaults.cardColors(
      containerColor = when (card.overallStatus) {
        ScheduledJobSurfaceStatus.RUNNING,
        ScheduledJobSurfaceStatus.DEGRADED -> accent.copy(alpha = 0.10f)
        ScheduledJobSurfaceStatus.FAILED,
        ScheduledJobSurfaceStatus.BLOCKED,
        ScheduledJobSurfaceStatus.WAITING -> accent.copy(alpha = 0.08f)
        else -> MaterialTheme.colorScheme.surface
      },
    ),
    border = BorderStroke(1.dp, accent.copy(alpha = 0.35f)),
    shape = RoundedCornerShape(16.dp),
  ) {
    Column(
      modifier = Modifier.padding(12.dp),
      verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
    ) {
      Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
        verticalAlignment = Alignment.CenterVertically,
      ) {
        Text(
          text = card.title,
          style = MaterialTheme.typography.titleSmall,
          fontWeight = FontWeight.SemiBold,
          modifier = Modifier.weight(1f),
        )
        ScheduleStatusChip(
          text = card.overallStatus.label,
          accent = accent,
          highlight = card.overallStatus == ScheduledJobSurfaceStatus.RUNNING || card.overallStatus == ScheduledJobSurfaceStatus.DEGRADED,
        )
      }
      if (card.subtitle.isNotBlank()) {
        Text(
          text = card.subtitle,
          style = ChatLabelTextStyle,
          color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
      }
      Text(
        text = card.summary,
        style = ChatBodyTextStyle,
        color = MaterialTheme.colorScheme.onSurface,
      )
      Column(verticalArrangement = Arrangement.spacedBy(CompactTightGap)) {
        card.phases.forEach { phase ->
          VerificationWorkflowPhaseRow(
            phase = phase,
            onClick = { onJobSelected(phase.jobId) },
          )
        }
      }
    }
  }
}

@Composable
private fun VerificationWorkflowPhaseRow(
  phase: VerificationWorkflowRunPhase,
  onClick: () -> Unit,
) {
  val accent = statusAccentColor(phase.status)
  Row(
    modifier = Modifier
      .fillMaxWidth()
      .clip(RoundedCornerShape(12.dp))
      .clickable(onClick = onClick)
      .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.72f))
      .padding(horizontal = 10.dp, vertical = 8.dp),
    horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
    verticalAlignment = Alignment.CenterVertically,
  ) {
    Box(
      modifier = Modifier
        .size(width = 6.dp, height = 34.dp)
        .background(accent, RoundedCornerShape(999.dp)),
    )
    Column(
      modifier = Modifier.weight(1f),
      verticalArrangement = Arrangement.spacedBy(CompactTightGap),
    ) {
      Text(
        text = phase.title,
        style = ChatBodyTextStyle,
        fontWeight = FontWeight.SemiBold,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
      )
      Text(
        text = phase.statusDetail,
        style = ChatLabelTextStyle,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        maxLines = 2,
        overflow = TextOverflow.Ellipsis,
      )
    }
    ScheduleStatusChip(
      text = phase.status.label,
      accent = accent,
      highlight = phase.status == ScheduledJobSurfaceStatus.RUNNING || phase.status == ScheduledJobSurfaceStatus.DEGRADED,
    )
  }
}

@Composable
private fun PipelineLaneColumn(
  modifier: Modifier = Modifier,
  title: String,
  items: List<PipelineLaneItem>,
  onJobSelected: (String) -> Unit,
) {
  var expandedWaitingGroups by remember { mutableStateOf(setOf<String>()) }
  val neutralLaneColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.82f)
  Column(
    modifier = modifier
      .fillMaxHeight()
      .background(neutralLaneColor, RoundedCornerShape(14.dp))
      .border(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.6f), RoundedCornerShape(14.dp))
      .padding(8.dp),
    verticalArrangement = Arrangement.spacedBy(PipelineBoardItemGap),
  ) {
    Text(
      text = title,
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.9f),
      fontWeight = FontWeight.SemiBold,
    )
    items.forEach { item ->
      when (item) {
        is PipelineLaneItem.TaskCard -> PipelineTaskCard(item, onJobSelected)
        is PipelineLaneItem.WaitCard -> PipelineWaitCard(item)
        is PipelineLaneItem.CollapsedWaitGroup -> {
          val expanded = expandedWaitingGroups.contains(item.groupId)
          PipelineCollapsedWaitGroupCard(
            item = item,
            expanded = expanded,
            onToggle = {
              expandedWaitingGroups = if (expanded) {
                expandedWaitingGroups - item.groupId
              } else {
                expandedWaitingGroups + item.groupId
              }
            },
            onJobSelected = onJobSelected,
          )
        }
      }
    }
  }
}

@Composable
private fun PipelineTaskCard(
  item: PipelineLaneItem.TaskCard,
  onJobSelected: (String) -> Unit,
) {
  val projection = item.projection
  val status = projection.status
  val accent = statusAccentColor(status)
  val isActive = status == ScheduledJobSurfaceStatus.RUNNING || status == ScheduledJobSurfaceStatus.DEGRADED
  val isDependencyWait = status == ScheduledJobSurfaceStatus.WAITING
  val isBlocked = status == ScheduledJobSurfaceStatus.BLOCKED
  val isScheduledWait = status == ScheduledJobSurfaceStatus.PENDING
  val isFailed = status == ScheduledJobSurfaceStatus.FAILED
  val isDone = status == ScheduledJobSurfaceStatus.SUCCEEDED
  val isScheduledByClock = isScheduledWait && shouldTreatAsTimeScheduled(projection)
  val cardColor = when {
    isActive -> accent.copy(alpha = 0.18f)
    isDependencyWait -> MaterialTheme.colorScheme.surface.copy(alpha = 0.28f)
    isBlocked -> MaterialTheme.colorScheme.surface.copy(alpha = 0.24f)
    isScheduledWait -> MaterialTheme.colorScheme.surface.copy(alpha = 0.40f)
    isFailed -> MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.16f)
    isDone -> MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.26f)
    else -> MaterialTheme.colorScheme.surface.copy(alpha = 0.86f)
  }
  val borderColor = when {
    isActive -> accent.copy(alpha = 0.68f)
    isDependencyWait -> MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.56f)
    isBlocked -> MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.52f)
    isScheduledWait -> MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.58f)
    isFailed -> MaterialTheme.colorScheme.error.copy(alpha = 0.44f)
    isDone -> MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.38f)
    else -> MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.48f)
  }
  val markerColor = when {
    isActive -> accent
    isDependencyWait || isBlocked -> MaterialTheme.colorScheme.onSurfaceVariant
    isDone -> accent.copy(alpha = 0.64f)
    isFailed -> MaterialTheme.colorScheme.error
    else -> MaterialTheme.colorScheme.onSurfaceVariant
  }
  val markerHeight = if (isActive) 54.dp else 50.dp
  val markerAlpha = if (isActive) activePulseAlpha() else 1f
  Card(
    modifier = Modifier
      .fillMaxWidth()
      .height(PipelineBoardItemHeight)
      .clickable { onJobSelected(item.job.jobId) },
    colors = CardDefaults.cardColors(containerColor = cardColor),
    border = BorderStroke(1.dp, borderColor),
    shape = RoundedCornerShape(12.dp),
  ) {
    Column(
      modifier = Modifier.padding(horizontal = 10.dp, vertical = 10.dp),
      verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
      Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        if (isDependencyWait || isBlocked) {
          Text("🔒", fontSize = ChatLabelTextStyle.fontSize)
        } else if (isScheduledByClock) {
          Text("⏰", fontSize = ChatLabelTextStyle.fontSize)
        }
        Text(
          text = displayJobTitle(item.job),
          style = ChatBodyTextStyle,
          fontWeight = if (isActive) FontWeight.Bold else FontWeight.SemiBold,
          modifier = Modifier.weight(1f),
          maxLines = 1,
          overflow = TextOverflow.Ellipsis,
        )
        ScheduleStatusChip(
          text = projection.status.label,
          accent = accent,
          highlight = isActive,
        )
      }
      if (projection.reason.isNotBlank()) {
        Text(
          text = projectionReasonSummary(projection),
          style = ChatBodyTextStyle,
          color = if (isDone || isFailed) MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.72f) else MaterialTheme.colorScheme.onSurfaceVariant,
          maxLines = if (isDone || isFailed) 1 else 2,
          overflow = TextOverflow.Ellipsis,
        )
      }
      Text(
        text = buildSecondaryJobLine(item.job, projection),
        style = ChatLabelTextStyle,
        color = if (isDone || isFailed) MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.58f) else MaterialTheme.colorScheme.onSurfaceVariant,
        maxLines = if (isDone) 1 else 2,
        overflow = TextOverflow.Ellipsis,
      )
    }
    if (isActive) {
      Box(
        modifier = Modifier
          .fillMaxWidth()
          .height(2.dp)
          .clip(RoundedCornerShape(topStart = 12.dp, topEnd = 12.dp, bottomEnd = 0.dp, bottomStart = 0.dp))
          .background(markerColor),
      )
    }
    Box(
      modifier = Modifier
        .height(markerHeight)
        .width(5.dp)
        .background(markerColor.copy(alpha = 0.95f), RoundedCornerShape(999.dp))
        .alpha(markerAlpha),
    )
  }
}

private fun projectionReasonSummary(projection: ScheduledJobProjection): String {
  if (projection.status != ScheduledJobSurfaceStatus.WAITING) return projection.reason
  val dependency = projection.reason.removePrefix("Waiting on ").trim()
  return if (dependency.isBlank()) "Waiting for dependency" else "Waiting for $dependency"
}

@Composable
private fun PipelineWaitCard(
  item: PipelineLaneItem.WaitCard,
) {
  Card(
    modifier = Modifier
      .fillMaxWidth()
      .height(PipelineBoardItemHeight),
    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.64f)),
    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f)),
    shape = RoundedCornerShape(10.dp),
  ) {
    Column(
      modifier = Modifier.padding(horizontal = 10.dp, vertical = 10.dp),
      verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
      Text("⏰ Wait until ${item.until}", style = ChatBodyTextStyle, color = MaterialTheme.colorScheme.onSurface, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
      Text(item.reason, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
  }
}

@Composable
private fun PipelineCollapsedWaitGroupCard(
  item: PipelineLaneItem.CollapsedWaitGroup,
  expanded: Boolean,
  onToggle: () -> Unit,
  onJobSelected: (String) -> Unit,
) {
  val waitReason = item.reason.ifBlank { "dependency" }
  val waitSubject = waitReason.ifBlank { "dependency" }
  val collapsedText = "🔒 ${item.count} tasks waiting for $waitSubject"
  Card(
    modifier = Modifier
      .fillMaxWidth()
      .height(PipelineBoardItemHeight)
      .clickable { onToggle() },
    colors = CardDefaults.cardColors(
      containerColor = if (expanded) MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.24f) else MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.25f),
    ),
    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.45f)),
    shape = RoundedCornerShape(10.dp),
  ) {
    Column(
      modifier = Modifier.padding(horizontal = 10.dp, vertical = 10.dp),
      verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
      Text(collapsedText, style = ChatBodyTextStyle, color = MaterialTheme.colorScheme.onSurface)
      Text(
        text = if (expanded) "tap to collapse" else "tap to expand",
        style = ChatLabelTextStyle,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
      )
    }
  }
  if (expanded) {
    Column(
      modifier = Modifier
        .fillMaxWidth()
        .padding(top = PipelineBoardItemGap),
      verticalArrangement = Arrangement.spacedBy(PipelineBoardItemGap),
    ) {
      item.jobs.forEach { job -> PipelineTaskCard(item = job, onJobSelected = onJobSelected) }
    }
  }
}

@Composable
private fun ScheduledJobRow(
  job: ScheduledJobSummary,
  projection: ScheduledJobProjection,
  indentLevel: Int,
  nowUtc: String,
  onClick: () -> Unit,
  onReschedule: () -> Unit,
) {
  val accent = statusAccentColor(projection.status)
  val effectiveIndent = indentLevel.coerceIn(0, 2)
  Row(
    modifier = Modifier
      .fillMaxWidth()
      .padding(start = (effectiveIndent * 6).dp),
    horizontalArrangement = Arrangement.spacedBy(8.dp),
    verticalAlignment = Alignment.Top,
  ) {
    if (effectiveIndent > 0) {
      Column(
        modifier = Modifier.padding(top = 10.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
      ) {
        repeat(effectiveIndent) {
          Box(
            modifier = Modifier
              .size(width = 2.dp, height = 18.dp)
              .background(
                MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.68f),
                RoundedCornerShape(999.dp),
              ),
          )
        }
      }
    }
    Card(
      modifier = Modifier
        .weight(1f)
        .clickable(onClick = onClick),
      colors = CardDefaults.cardColors(
        containerColor = when (projection.status) {
          ScheduledJobSurfaceStatus.RUNNING -> accent.copy(alpha = 0.11f)
          ScheduledJobSurfaceStatus.DEGRADED -> accent.copy(alpha = 0.11f)
          ScheduledJobSurfaceStatus.FAILED -> accent.copy(alpha = 0.08f)
          ScheduledJobSurfaceStatus.BLOCKED, ScheduledJobSurfaceStatus.WAITING -> accent.copy(alpha = 0.08f)
          else -> MaterialTheme.colorScheme.surface
        },
      ),
    ) {
      Row(
        modifier = Modifier
          .fillMaxWidth()
          .padding(horizontal = 10.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.Top,
      ) {
        Box(
          modifier = Modifier
            .size(width = 6.dp, height = 54.dp)
            .background(accent, RoundedCornerShape(999.dp))
            .alpha(if (projection.status == ScheduledJobSurfaceStatus.RUNNING || projection.status == ScheduledJobSurfaceStatus.DEGRADED) activePulseAlpha() else 1f),
        )
        Column(
          modifier = Modifier.weight(1f),
          verticalArrangement = Arrangement.spacedBy(CompactTightGap),
        ) {
          Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
            verticalAlignment = Alignment.CenterVertically,
          ) {
            Text(
              text = displayJobTitle(job),
              style = ChatBodyTextStyle,
              fontWeight = FontWeight.SemiBold,
              modifier = Modifier.weight(1f),
              maxLines = 1,
              overflow = TextOverflow.Ellipsis,
            )
            ScheduleStatusChip(
              text = projection.status.label,
              accent = accent,
              highlight = projection.status == ScheduledJobSurfaceStatus.RUNNING || projection.status == ScheduledJobSurfaceStatus.DEGRADED,
            )
          }
          Text(
            text = job.jobId,
            style = ChatLabelTextStyle,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
          )
          Text(
            text = projection.detail.ifBlank { scheduledJobTimelineText(job = job, projection = projection, nowUtc = nowUtc) },
            style = ChatBodyTextStyle,
            color = MaterialTheme.colorScheme.onSurface,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
          )
          Text(
            text = buildSecondaryJobLine(job = job, projection = projection),
            style = ChatLabelTextStyle,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
          )
          if (projection.status == ScheduledJobSurfaceStatus.FAILED || projection.status == ScheduledJobSurfaceStatus.DEGRADED) {
            Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
              SmallButton("Reschedule", onReschedule)
            }
          }
        }
      }
    }
  }
}

@Composable
private fun ScheduleStatusChip(
  text: String,
  accent: Color,
  highlight: Boolean,
) {
  val pulse = if (highlight) activePulseAlpha() else 1f
  Box(
    modifier = Modifier
      .background(accent.copy(alpha = 0.14f), RoundedCornerShape(999.dp))
      .border(1.dp, accent.copy(alpha = 0.45f), RoundedCornerShape(999.dp))
      .padding(horizontal = 9.dp, vertical = 5.dp),
  ) {
    Text(
      text = text,
      style = ChatLabelTextStyle,
      color = accent,
      fontWeight = FontWeight.SemiBold,
      modifier = Modifier.alpha(pulse),
    )
  }
}

internal data class PipelineLane(
  val items: List<PipelineLaneItem>,
)

internal sealed interface PipelineLaneItem {
  data class TaskCard(val job: ScheduledJobSummary, val projection: ScheduledJobProjection) : PipelineLaneItem
  data class WaitCard(val until: String, val reason: String) : PipelineLaneItem
  data class CollapsedWaitGroup(
    val count: Int,
    val reason: String,
    val jobs: List<PipelineLaneItem.TaskCard>,
    val groupId: String,
  ) : PipelineLaneItem
}

internal data class PipelineBoard(
  val lanes: List<PipelineLane>,
  val totalItems: Int,
)

internal fun buildPipelineBoard(
  jobs: List<ScheduledJobSummary>,
  nowUtc: String,
): PipelineBoard {
  val lanes = List(PIPELINE_LANE_COUNT) { mutableListOf<PipelineLaneItem>() }
  if (jobs.isEmpty()) {
    return PipelineBoard(lanes = lanes.map { PipelineLane(it) }, totalItems = 0)
  }
  val now = parseInstantOrNull(nowUtc)
  val sorted = sortScheduledJobsForMobile(jobs, nowUtc)
  sorted.forEachIndexed { idx, job ->
    val laneIndex = (idx % PIPELINE_LANE_COUNT)
    val projection = projectScheduledJob(job, nowUtc)
    if (shouldInsertWaitMarker(job = job, nowUtc = now)) {
      val waitText = formatViennaTime(job.due.dueUtc.ifBlank { job.schedule.runAtUtc }).ifBlank { "scheduled" }
      lanes[laneIndex] += PipelineLaneItem.WaitCard(until = waitText, reason = humanizeScheduledDueReason(job.due.reason))
    }
    lanes[laneIndex] += PipelineLaneItem.TaskCard(job = job, projection = projection)
  }
  val collapsed = lanes.mapIndexed { index, items ->
    PipelineLane(collapseDependencyWaitQueues(items, laneIndex = index))
  }
  return PipelineBoard(
    lanes = collapsed,
    totalItems = collapsed.sumOf { it.items.size },
  )
}

internal fun buildVerificationWorkflowRunCards(
  jobs: List<ScheduledJobSummary>,
  nowUtc: String,
): List<VerificationWorkflowRunCard> {
  val sections = buildScheduledJobBranchSections(
    displayJobs = sortScheduledJobsForMobile(jobs, nowUtc),
    graphJobs = jobs,
    nowUtc = nowUtc,
  )
  if (sections.isEmpty()) return emptyList()
  return sections.map { section ->
    val phases = section.jobs.map { job ->
      val projection = projectScheduledJob(job, nowUtc)
      VerificationWorkflowRunPhase(
        jobId = job.jobId,
        title = displayJobTitle(job),
        status = projection.status,
        statusDetail = when (projection.status) {
          ScheduledJobSurfaceStatus.DEGRADED -> projection.heartbeat.ifBlank { projection.reason }
          ScheduledJobSurfaceStatus.RUNNING -> projection.reason
          ScheduledJobSurfaceStatus.BLOCKED,
          ScheduledJobSurfaceStatus.WAITING,
          ScheduledJobSurfaceStatus.FAILED,
          ScheduledJobSurfaceStatus.PENDING,
          ScheduledJobSurfaceStatus.SUCCEEDED,
          ScheduledJobSurfaceStatus.UNKNOWN -> projection.reason
        },
      )
    }
    VerificationWorkflowRunCard(
      key = section.key,
      title = section.title,
      subtitle = section.subtitle,
      summary = buildVerificationWorkflowRunSummary(phases),
      overallStatus = summarizeVerificationWorkflowRunStatus(phases.map { it.status }),
      phases = phases,
    )
  }
}

internal fun summarizeVerificationWorkflowRunStatus(
  statuses: List<ScheduledJobSurfaceStatus>,
): ScheduledJobSurfaceStatus {
  if (statuses.isEmpty()) return ScheduledJobSurfaceStatus.UNKNOWN
  return when {
    statuses.any { it == ScheduledJobSurfaceStatus.DEGRADED } -> ScheduledJobSurfaceStatus.DEGRADED
    statuses.any { it == ScheduledJobSurfaceStatus.FAILED } -> ScheduledJobSurfaceStatus.FAILED
    statuses.any { it == ScheduledJobSurfaceStatus.BLOCKED } -> ScheduledJobSurfaceStatus.BLOCKED
    statuses.any { it == ScheduledJobSurfaceStatus.RUNNING } -> ScheduledJobSurfaceStatus.RUNNING
    statuses.any { it == ScheduledJobSurfaceStatus.WAITING } -> ScheduledJobSurfaceStatus.WAITING
    statuses.any { it == ScheduledJobSurfaceStatus.PENDING } -> ScheduledJobSurfaceStatus.PENDING
    statuses.all { it == ScheduledJobSurfaceStatus.SUCCEEDED } -> ScheduledJobSurfaceStatus.SUCCEEDED
    else -> ScheduledJobSurfaceStatus.UNKNOWN
  }
}

internal fun buildVerificationWorkflowRunSummary(
  phases: List<VerificationWorkflowRunPhase>,
): String {
  if (phases.isEmpty()) return "No visible phases."
  val counts = phases.groupingBy { it.status }.eachCount()
  val parts = mutableListOf<String>()
  counts[ScheduledJobSurfaceStatus.DEGRADED]?.takeIf { it > 0 }?.let { count ->
    parts += "$count degraded"
  }
  counts[ScheduledJobSurfaceStatus.FAILED]?.takeIf { it > 0 }?.let { count ->
    parts += "$count failed"
  }
  counts[ScheduledJobSurfaceStatus.BLOCKED]?.takeIf { it > 0 }?.let { count ->
    parts += "$count blocked"
  }
  counts[ScheduledJobSurfaceStatus.RUNNING]?.takeIf { it > 0 }?.let { count ->
    parts += "$count active"
  }
  counts[ScheduledJobSurfaceStatus.WAITING]?.takeIf { it > 0 }?.let { count ->
    parts += "$count waiting on dependency"
  }
  counts[ScheduledJobSurfaceStatus.PENDING]?.takeIf { it > 0 }?.let { count ->
    parts += "$count pending"
  }
  counts[ScheduledJobSurfaceStatus.SUCCEEDED]?.takeIf { it > 0 }?.let { count ->
    parts += "$count succeeded"
  }
  counts[ScheduledJobSurfaceStatus.UNKNOWN]?.takeIf { it > 0 }?.let { count ->
    parts += "$count unknown"
  }
  return if (parts.isEmpty()) {
    "${phases.size} phase${if (phases.size == 1) "" else "s"}"
  } else {
    parts.joinToString(" · ")
  }
}

private fun collapseDependencyWaitQueues(
  items: List<PipelineLaneItem>,
  laneIndex: Int,
): List<PipelineLaneItem> {
  if (items.isEmpty()) return emptyList()
  val collapsed = mutableListOf<PipelineLaneItem>()
  var index = 0
  while (index < items.size) {
    val current = items[index]
    val groupKey = current.dependencyWaitQueueReasonKey()
    if (groupKey == null) {
      collapsed += current
      index++
      continue
    }
    val groupedTasks = mutableListOf<PipelineLaneItem.TaskCard>()
    var next = index
    while (next < items.size) {
      val candidate = items[next]
      val candidateKey = candidate.dependencyWaitQueueReasonKey()
      if (candidateKey == null || candidateKey != groupKey) break
      if (candidate is PipelineLaneItem.TaskCard) {
        groupedTasks += candidate
      }
      next++
    }
    if (groupedTasks.size >= PIPELINE_COLLAPSED_WAIT_GROUP_THRESHOLD) {
      collapsed += PipelineLaneItem.CollapsedWaitGroup(
        count = groupedTasks.size,
        reason = groupKey,
        jobs = groupedTasks,
        groupId = "lane-${laneIndex}-${groupKey}-${index}",
      )
    } else {
      collapsed += groupedTasks
    }
    index = next
  }
  return collapsed
}

private fun PipelineLaneItem.dependencyWaitQueueReasonKey(): String? {
  val itemTask = this as? PipelineLaneItem.TaskCard ?: return null
  if (itemTask.projection.status != ScheduledJobSurfaceStatus.WAITING) return null
  val rawReason = itemTask.projection.reason.trim()
  if (rawReason.isBlank()) return "dependency"
  return if (rawReason.startsWith("Waiting on ", ignoreCase = true)) {
    rawReason.removePrefix("Waiting on ").trim().ifBlank { "dependency" }
  } else {
    rawReason
  }
}

private fun shouldInsertWaitMarker(
  job: ScheduledJobSummary,
  nowUtc: Instant?,
): Boolean {
  if (!job.enabled) return false
  if (job.state.activeRunId.isNotBlank() || job.state.lastStatus.equals("running", ignoreCase = true)) return false
  val dueReason = job.due.reason.lowercase(Locale.US)
  if (dueReason == "not_due_yet") return true
  if (dueReason != "due") return false
  val due = parseInstantOrNull(job.due.dueUtc) ?: parseInstantOrNull(job.schedule.runAtUtc)
  return due?.isAfter(nowUtc ?: Instant.now()) == true
}

private fun shouldTreatAsTimeScheduled(
  projection: ScheduledJobProjection,
): Boolean {
  val reason = projection.reason.lowercase(Locale.US)
  return projection.status == ScheduledJobSurfaceStatus.PENDING && (
    reason.contains("waiting for scheduled time") ||
      reason.contains("ready to run") ||
      reason.contains("pending scheduling")
  )
}

internal const val PIPELINE_LANE_COUNT = 3
private const val PIPELINE_BOARD_VISIBLE_ITEMS = 5
private const val PIPELINE_COLLAPSED_WAIT_GROUP_THRESHOLD = 3
private val PipelineBoardItemHeight = 104.dp
private val PipelineBoardItemGap = 12.dp
private val PipelineBoardLanePadding = 10.dp
private val PipelineBoardLaneTitleHeight = 22.dp
private val PipelineBoardTitleToItemsGap = 8.dp
private val PipelineBoardLaneChromeHeight =
  PipelineBoardLanePadding * 2 + PipelineBoardLaneTitleHeight + PipelineBoardTitleToItemsGap
private val PipelineBoardMinHeight = pipelineBoardHeightForItems(1)

internal fun pipelineBoardVisibleRows(board: PipelineBoard): Int {
  val maxItems = board.lanes.maxOfOrNull { it.items.size } ?: 0
  if (maxItems <= 0) return 0
  return maxItems.coerceAtLeast(1).coerceAtMost(PIPELINE_BOARD_VISIBLE_ITEMS)
}

internal fun pipelineBoardHeightForItems(itemsCount: Int): Dp {
  val clampedItems = itemsCount.coerceIn(1, PIPELINE_BOARD_VISIBLE_ITEMS)
  return PipelineBoardLaneChromeHeight +
    PipelineBoardItemHeight * clampedItems +
    PipelineBoardItemGap * (clampedItems - 1)
}

private fun pipelineBoardMaxHeight(board: PipelineBoard): Dp {
  return pipelineBoardHeightForItems(pipelineBoardVisibleRows(board))
}

@Composable
private fun SchedulerStatusLight(running: Boolean) {
  Box(
    modifier = Modifier
      .size(10.dp)
      .background(
        if (running) Color(0xFF27AE60) else Color(0xFFD1495B),
        RoundedCornerShape(999.dp),
      ),
  )
}

@Composable
private fun SummaryFact(
  label: String,
  value: Int,
  accent: Color,
  selected: Boolean,
  modifier: Modifier = Modifier,
  onClick: () -> Unit,
) {
  Box(
    modifier = modifier
      .background(
        if (selected) accent.copy(alpha = 0.16f) else MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
        RoundedCornerShape(14.dp),
      )
      .border(
        width = 1.dp,
        color = if (selected) accent.copy(alpha = 0.55f) else Color.Transparent,
        shape = RoundedCornerShape(14.dp),
      )
      .clickable(onClick = onClick)
      .padding(horizontal = 10.dp, vertical = 8.dp),
  ) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
      Text(label, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
      Text(value.toString(), style = ChatBodyTextStyle, fontWeight = FontWeight.SemiBold, color = accent)
    }
  }
}

@Composable
private fun DetailFactsCard(
  title: String,
  content: @Composable ColumnScope.() -> Unit,
) {
  AdminCard(title = title, content = content)
}

@Composable
private fun DetailFactRow(
  label: String,
  value: String,
) {
  Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
    Text(label, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Text(value, style = ChatBodyTextStyle, color = MaterialTheme.colorScheme.onSurface)
  }
  Spacer(modifier = Modifier.height(CompactTightGap))
}

@Composable
private fun activePulseAlpha(): Float {
  val transition = rememberInfiniteTransition(label = "schedule-active")
  val alpha by transition.animateFloat(
    initialValue = 0.45f,
    targetValue = 1f,
    animationSpec = infiniteRepeatable(
      animation = tween(durationMillis = 1250, easing = FastOutSlowInEasing),
      repeatMode = RepeatMode.Reverse,
    ),
    label = "schedule-active-alpha",
  )
  return alpha
}

internal fun projectScheduledJob(
  job: ScheduledJobSummary,
  nowUtc: String,
): ScheduledJobProjection {
  val state = job.state
  val due = job.due
  val isRunning = state.activeRunId.isNotBlank() || state.lastStatus.equals("running", ignoreCase = true)
  val degradedRun = isRunning && state.activeClaimStale
  val startUtc = when {
    isRunning -> state.activeStartedUtc.ifBlank { state.lastStartedUtc }
    else -> state.lastStartedUtc
  }
  val finishUtc = if (isRunning) "" else state.lastFinishedUtc
  val elapsed = when {
    startUtc.isBlank() -> ""
    isRunning -> formatElapsedBetween(startUtc = startUtc, endUtc = nowUtc)
    else -> formatElapsedBetween(startUtc = startUtc, endUtc = finishUtc)
  }
  val heartbeat = when {
    !isRunning -> "Not active"
    state.activeClaimStale -> {
      val age = formatAgeSeconds(state.activeClaimAgeSec)
      val reason = heartbeatReason(state.activeClaimReason)
      if (age.isNotBlank()) "Stale ($age)${if (reason.isNotBlank()) " • $reason" else ""}" else "Stale${if (reason.isNotBlank()) " • $reason" else ""}"
    }
    state.activeClaimPresent -> {
      val age = formatAgeSeconds(state.activeClaimAgeSec)
      val beatAt = formatViennaTime(state.activeClaimHeartbeatUtc)
      when {
        age.isNotBlank() && beatAt.isNotBlank() -> "Fresh ($age) • $beatAt"
        age.isNotBlank() -> "Fresh ($age)"
        beatAt.isNotBlank() -> "Fresh • $beatAt"
        else -> "Fresh"
      }
    }
    else -> "Unknown"
  }
  val status = when {
    degradedRun -> ScheduledJobSurfaceStatus.DEGRADED
    isRunning -> ScheduledJobSurfaceStatus.RUNNING
    state.lastStatus.equals("error", ignoreCase = true) -> ScheduledJobSurfaceStatus.FAILED
    !job.enabled -> ScheduledJobSurfaceStatus.BLOCKED
    due.reason.startsWith("dependency_not_ok:", ignoreCase = true) -> ScheduledJobSurfaceStatus.WAITING
    state.lastStatus.equals("ok", ignoreCase = true) -> ScheduledJobSurfaceStatus.SUCCEEDED
    due.eligible -> ScheduledJobSurfaceStatus.PENDING
    due.reason.equals("not_due_yet", ignoreCase = true) || due.reason.isBlank() -> ScheduledJobSurfaceStatus.PENDING
    else -> ScheduledJobSurfaceStatus.UNKNOWN
  }
  val reason = when (status) {
    ScheduledJobSurfaceStatus.DEGRADED -> "Active run heartbeat is stale${if (elapsed.isNotBlank()) " • $elapsed elapsed" else ""}"
    ScheduledJobSurfaceStatus.RUNNING -> "Running now${if (elapsed.isNotBlank()) " • $elapsed elapsed" else ""}"
    ScheduledJobSurfaceStatus.FAILED -> "Last run failed${if (formatIsoUtcForAdmin(finishUtc).isNotBlank()) " • ${formatIsoUtcForAdmin(finishUtc)}" else ""}"
    ScheduledJobSurfaceStatus.BLOCKED -> "Disabled by spec"
    ScheduledJobSurfaceStatus.WAITING -> humanizeScheduledDueReason(due.reason)
    ScheduledJobSurfaceStatus.SUCCEEDED -> "Last run succeeded${if (elapsed.isNotBlank()) " • $elapsed" else ""}"
    ScheduledJobSurfaceStatus.PENDING -> humanizeScheduledDueReason(due.reason)
    ScheduledJobSurfaceStatus.UNKNOWN -> if (due.reason.isNotBlank()) humanizeScheduledDueReason(due.reason) else "Status unavailable"
  }
  return ScheduledJobProjection(
    status = status,
    reason = reason,
    detail = scheduledJobTimelineText(job = job, projectionStatus = status, startUtc = startUtc, finishUtc = finishUtc, elapsed = elapsed),
    heartbeat = heartbeat,
    startUtc = startUtc,
    finishUtc = finishUtc,
    elapsed = elapsed,
    boundPisUid = boundPisUid(job),
    boundPisPath = boundPisPath(job),
    boundPisTitle = boundPisTitle(job),
    receiptPath = state.lastReceiptPath,
    reportPath = state.lastReportPath,
  )
}

internal fun sortScheduledJobsForMobile(
  jobs: List<ScheduledJobSummary>,
  nowUtc: String,
): List<ScheduledJobSummary> {
  return jobs.sortedWith(
    compareBy<ScheduledJobSummary> { scheduleSortBucket(projectScheduledJob(it, nowUtc).status) }
      .thenBy { scheduleDueSortKey(it) }
      .thenByDescending { parseInstantOrNull(it.state.activeStartedUtc)?.toEpochMilli() ?: Long.MIN_VALUE }
      .thenByDescending { parseInstantOrNull(it.state.lastFinishedUtc)?.toEpochMilli() ?: Long.MIN_VALUE }
      .thenBy { it.jobId },
  )
}

internal fun buildScheduledJobBranchSections(
  displayJobs: List<ScheduledJobSummary>,
  graphJobs: List<ScheduledJobSummary>,
  nowUtc: String,
): List<ScheduledJobBranchSection> {
  if (displayJobs.isEmpty()) return emptyList()
  val graphById = graphJobs.associateBy { it.jobId }
  val laneCache = mutableMapOf<String, Pair<String, String>>()
  val laneGroups = LinkedHashMap<String, MutableList<ScheduledJobSummary>>()

  displayJobs.forEach { job ->
    val (laneKey, _) = laneForScheduledJob(job = job, jobsById = graphById, laneCache = laneCache)
    laneGroups.getOrPut(laneKey) { mutableListOf() } += job
  }

  return laneGroups.entries
    .map { (laneKey, laneJobs) ->
      val firstJob = laneJobs.first()
      val laneTitle = laneForScheduledJob(job = firstJob, jobsById = graphById, laneCache = laneCache).second
      val orderedJobs = sortScheduledJobsWithinBranch(laneJobs, graphById, nowUtc)
      ScheduledJobBranchSection(
        key = laneKey,
        title = laneTitle,
        subtitle = buildScheduledJobBranchSubtitle(orderedJobs, nowUtc),
        jobs = orderedJobs,
        indentByJobId = buildScheduledJobBranchIndentDepths(orderedJobs, graphById, nowUtc),
      )
    }
    .sortedWith(
      compareBy<ScheduledJobBranchSection> { scheduledJobBranchSortBucket(it.jobs, nowUtc) }
        .thenBy { scheduledJobBranchDueSortKey(it.jobs) }
        .thenBy { it.title.lowercase(Locale.US) }
        .thenBy { it.key },
    )
}

internal fun scheduledJobStatusCounts(
  jobs: List<ScheduledJobSummary>,
  nowUtc: String,
): Map<ScheduledJobSurfaceStatus, Int> {
  val counts = LinkedHashMap<ScheduledJobSurfaceStatus, Int>()
  ScheduledJobSurfaceStatus.entries.forEach { counts[it] = 0 }
  jobs.forEach { job ->
    val status = projectScheduledJob(job, nowUtc).status
    counts[status] = (counts[status] ?: 0) + 1
  }
  return counts
}

internal fun formatElapsedBetween(
  startUtc: String,
  endUtc: String,
): String {
  val start = parseInstantOrNull(startUtc) ?: return ""
  val end = parseInstantOrNull(endUtc) ?: return ""
  val seconds = Duration.between(start, end).seconds.coerceAtLeast(0L)
  val hours = seconds / 3600
  val minutes = (seconds % 3600) / 60
  val secs = seconds % 60
  return when {
    hours > 0 -> "${hours}h ${minutes}m"
    minutes > 0 -> "${minutes}m ${secs}s"
    else -> "${secs}s"
  }
}

private fun formatAgeSeconds(seconds: Double?): String {
  val raw = seconds ?: return ""
  val total = raw.toLong().coerceAtLeast(0L)
  val hours = total / 3600
  val minutes = (total % 3600) / 60
  val secs = total % 60
  return when {
    hours > 0 -> "${hours}h ${minutes}m ago"
    minutes > 0 -> "${minutes}m ${secs}s ago"
    else -> "${secs}s ago"
  }
}

private fun heartbeatReason(raw: String): String {
  return when (raw.trim().lowercase(Locale.US)) {
    "missing" -> "missing lock claim"
    "run_id_mismatch" -> "run id mismatch"
    "missing_probe_utc" -> "missing heartbeat timestamp"
    "invalid_probe_utc" -> "invalid heartbeat timestamp"
    "stale_heartbeat" -> "no recent heartbeat"
    "fresh" -> ""
    else -> raw.trim().replace('_', ' ')
  }
}

internal fun humanizeScheduledDueReason(raw: String): String {
  val reason = raw.trim()
  return when {
    reason.isBlank() -> "Pending scheduling"
    reason.equals("due", ignoreCase = true) -> "Ready to run"
    reason.equals("not_due_yet", ignoreCase = true) -> "Waiting for scheduled time"
    reason.equals("already_executed", ignoreCase = true) -> "Current slot already executed"
    reason.equals("disabled", ignoreCase = true) -> "Disabled"
    reason.startsWith("dependency_not_ok:", ignoreCase = true) -> "Waiting on ${reason.substringAfter(':')}"
    reason.equals("active_run", ignoreCase = true) -> "Already running"
    else -> reason.replace('_', ' ')
  }
}

internal fun bindingLabel(job: ScheduledJobSummary): String {
  val binding = job.binding
  return when {
    binding.type.isBlank() && binding.id.isBlank() -> job.role.ifBlank { job.jobKind }
    binding.type.isBlank() -> binding.id
    else -> "${binding.type}:${binding.id}"
  }
}

internal fun boundPisUid(job: ScheduledJobSummary): String {
  return if (job.binding.type == "ledger_uid") job.binding.id else ""
}

private val LedgerItemPisPattern =
  Regex("PIS-[0-9]{8}-[0-9]{4,6}Z-[A-Z0-9]+", RegexOption.IGNORE_CASE)

private fun extractLedgerItemDirectoryFromContextRef(ref: String): String {
  val normalized = ref.replace('\\', '/').trim().trimEnd('/')
  val prefix = "governance/ledger/items/"
  if (prefix !in normalized) return ""
  val markerIndex = normalized.indexOf(prefix)
  if (markerIndex < 0) return ""
  val afterPrefix = normalized.substring(markerIndex + prefix.length)
  val firstSegment = afterPrefix.substringBefore('/').trim()
  if (firstSegment.isBlank()) return ""
  val uidPart = firstSegment.substringBefore("__").trim()
  if (!LedgerItemPisPattern.matches(uidPart)) return ""
  return normalized.substring(0, markerIndex + prefix.length) + firstSegment
}

internal fun boundPisPath(job: ScheduledJobSummary): String {
  val targetId = boundPisUid(job).ifBlank { job.binding.id }
  if (job.contextRefs.isEmpty()) return ""

  val normalizedRefs = job.contextRefs
    .map { it.trim().replace('\\', '/').trim() }
    .filter { it.isNotBlank() }

  val pisRefs = normalizedRefs.filter { ref -> ref.endsWith("/pis.md", ignoreCase = true) }
  if (targetId.isNotBlank()) {
    pisRefs.firstOrNull { ref -> ref.contains(targetId, ignoreCase = true) }?.let { return it }
  }
  pisRefs.firstOrNull()?.let { return it }

  val ledgerItemRefs = normalizedRefs
    .map(::extractLedgerItemDirectoryFromContextRef)
    .filter { it.isNotBlank() }

  if (ledgerItemRefs.isNotEmpty()) {
    if (targetId.isNotBlank()) {
      ledgerItemRefs.firstOrNull { ref -> ref.contains(targetId, ignoreCase = true) }?.let { dir ->
        return "${dir.trimEnd('/')}/pis.md"
      }
    }
    return "${ledgerItemRefs.first().trimEnd('/')}/pis.md"
  }

  return ""
}

internal fun boundPisTitle(job: ScheduledJobSummary): String {
  val path = boundPisPath(job)
  if (path.isBlank()) return ""
  val slug = path.substringAfter("__", "").substringBefore("/pis.md").trim()
  if (slug.isBlank()) return ""
  return slug
    .split('-', '_')
    .filter { it.isNotBlank() }
    .joinToString(" ") { part ->
      part.replaceFirstChar { ch -> if (ch.isLowerCase()) ch.titlecase(Locale.US) else ch.toString() }
    }
}

internal fun displayJobTitle(job: ScheduledJobSummary): String {
  return job.displayTitle.trim()
    .ifBlank { boundPisTitle(job) }
    .ifBlank { job.promptPreview.trim().substringBefore('\n').ifBlank { job.prompt.trim().substringBefore('\n') } }
    .ifBlank { job.jobId }
}

private fun buildSecondaryJobLine(
  job: ScheduledJobSummary,
  projection: ScheduledJobProjection,
): String {
  val parts = mutableListOf<String>()
  if (job.runtime.model.isNotBlank()) parts += job.runtime.model
  if (job.jobKind != "single_turn" && job.loop.maxIterations > 0) parts += "${job.loop.maxIterations} iter"
  if (job.dependsOn.isNotEmpty()) parts += "${job.dependsOn.size} dep"
  if (job.schedule.mode == "daily" && job.schedule.timeOfDay.isNotBlank()) {
    parts += "daily ${job.schedule.timeOfDay} ${job.schedule.timezone}".trim()
  } else if (job.schedule.runAtUtc.isNotBlank()) {
    parts += formatViennaTime(job.schedule.runAtUtc)
  }
  if (projection.boundPisUid.isNotBlank()) parts += projection.boundPisUid
  return parts.joinToString(" • ")
}

private fun editTargetFor(job: ScheduledJobSummary): ScheduledJobEditTarget {
  val iterations = if (job.jobKind == "single_turn") null else job.loop.maxIterations.takeIf { it > 0 } ?: 3
  return ScheduledJobEditTarget(
    jobId = job.jobId,
    title = displayJobTitle(job),
    model = job.runtime.model.ifBlank { "gpt-5.4" },
    maxIterations = iterations,
    sourceLabel = job.state.lastStatus.ifBlank { "job" },
  )
}

@Composable
private fun ScheduledJobEditDialog(
  target: ScheduledJobEditTarget,
  inFlight: Boolean,
  actionLabel: String,
  onDismiss: () -> Unit,
  onSave: (String, Int?, Boolean) -> Unit,
) {
  var model by remember(target.jobId) { mutableStateOf(target.model) }
  var iterationsText by remember(target.jobId) { mutableStateOf(target.maxIterations?.toString().orEmpty()) }
  val parsedIterations = iterationsText.trim().toIntOrNull()
  val modelOk = model.trim().isNotBlank()
  val iterationsOk = target.maxIterations == null || (parsedIterations != null && parsedIterations in 1..10)
  val confirmEnabled = !inFlight && modelOk && iterationsOk

  AlertDialog(
    onDismissRequest = onDismiss,
    title = { Text("Run settings") },
    text = {
      Column(verticalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
        Text(
          text = target.title,
          style = ChatBodyTextStyle,
          color = MaterialTheme.colorScheme.onSurface,
        )
        Text(
          text = target.jobId,
          style = ChatLabelTextStyle,
          color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedTextField(
          value = model,
          onValueChange = { model = it },
          modifier = Modifier.fillMaxWidth(),
          singleLine = true,
          label = { Text("Model") },
        )
        if (target.maxIterations != null) {
          OutlinedTextField(
            value = iterationsText,
            onValueChange = { iterationsText = it.filter { ch -> ch.isDigit() } },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            label = { Text("Max iterations") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            supportingText = { Text("1 to 10") },
          )
        }
        if (inFlight && actionLabel.isNotBlank()) {
          Text(
            text = actionLabel,
            style = ChatLabelTextStyle,
            color = MaterialTheme.colorScheme.primary,
          )
        }
      }
    },
    confirmButton = {
      TextButton(
        onClick = { onSave(model.trim(), parsedIterations, true) },
        enabled = confirmEnabled,
      ) {
        Text("Save + run")
      }
    },
    dismissButton = {
      Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
        TextButton(
          onClick = { onSave(model.trim(), parsedIterations, false) },
          enabled = confirmEnabled,
        ) {
          Text("Save")
        }
        TextButton(onClick = onDismiss, enabled = !inFlight) {
          Text("Cancel")
        }
      }
    },
  )
}

private fun scheduledJobTimelineText(
  job: ScheduledJobSummary,
  projection: ScheduledJobProjection,
  nowUtc: String,
): String = scheduledJobTimelineText(
  job = job,
  projectionStatus = projection.status,
  startUtc = projection.startUtc,
  finishUtc = projection.finishUtc,
  elapsed = projection.elapsed.ifBlank { formatElapsedBetween(projection.startUtc, nowUtc) },
)

private fun scheduledJobTimelineText(
  job: ScheduledJobSummary,
  projectionStatus: ScheduledJobSurfaceStatus,
  startUtc: String,
  finishUtc: String,
  elapsed: String,
): String {
  val parts = mutableListOf<String>()
  val start = formatViennaTime(startUtc)
  val finish = formatViennaTime(finishUtc)
  when (projectionStatus) {
    ScheduledJobSurfaceStatus.RUNNING, ScheduledJobSurfaceStatus.DEGRADED -> {
      if (start.isNotBlank()) parts += "Started $start"
      if (elapsed.isNotBlank()) parts += elapsed
    }
    ScheduledJobSurfaceStatus.SUCCEEDED, ScheduledJobSurfaceStatus.FAILED -> {
      if (start.isNotBlank()) parts += "Start $start"
      if (finish.isNotBlank()) parts += "Finish $finish"
      if (elapsed.isNotBlank()) parts += elapsed
    }
    else -> {
      val due = formatViennaTime(job.due.dueUtc.ifBlank { job.schedule.runAtUtc })
      if (due.isNotBlank()) parts += "Due $due"
      if (elapsed.isNotBlank()) parts += elapsed
    }
  }
  return parts.joinToString(" • ")
}

private fun statusAccentColor(status: ScheduledJobSurfaceStatus): Color {
  return when (status) {
    ScheduledJobSurfaceStatus.RUNNING -> Color(0xFF1F8EFA)
    ScheduledJobSurfaceStatus.DEGRADED -> Color(0xFFB96A00)
    ScheduledJobSurfaceStatus.FAILED -> Color(0xFFD1495B)
    ScheduledJobSurfaceStatus.BLOCKED -> Color(0xFF9C5F00)
    ScheduledJobSurfaceStatus.WAITING -> Color(0xFFC97A00)
    ScheduledJobSurfaceStatus.PENDING -> Color(0xFF5A6B7A)
    ScheduledJobSurfaceStatus.SUCCEEDED -> Color(0xFF2E9D54)
    ScheduledJobSurfaceStatus.UNKNOWN -> Color(0xFF7A6C5D)
  }
}

private fun parseInstantOrNull(raw: String): Instant? {
  val text = raw.trim()
  if (text.isBlank()) return null
  return runCatching { Instant.parse(text) }.getOrNull()
}

private fun matchesScheduledJobFilter(
  status: ScheduledJobSurfaceStatus,
  filter: ScheduledJobFilter,
): Boolean {
  return when (filter) {
    ScheduledJobFilter.ALL -> true
    ScheduledJobFilter.RUNNING -> status == ScheduledJobSurfaceStatus.RUNNING
    ScheduledJobFilter.FAILED -> status == ScheduledJobSurfaceStatus.FAILED || status == ScheduledJobSurfaceStatus.DEGRADED
    ScheduledJobFilter.BLOCKED -> status == ScheduledJobSurfaceStatus.BLOCKED
    ScheduledJobFilter.PENDING -> status == ScheduledJobSurfaceStatus.PENDING || status == ScheduledJobSurfaceStatus.WAITING
    ScheduledJobFilter.SUCCEEDED -> status == ScheduledJobSurfaceStatus.SUCCEEDED
  }
}

private fun laneForScheduledJob(
  job: ScheduledJobSummary,
  jobsById: Map<String, ScheduledJobSummary>,
  laneCache: MutableMap<String, Pair<String, String>>,
): Pair<String, String> {
  return laneCache.getOrPut(job.jobId) {
    val rootIds = findScheduledJobRootIds(job, jobsById, mutableSetOf()).sorted()
    val laneKey = rootIds.joinToString("|").ifBlank { job.jobId }
    val laneTitle = when {
      rootIds.isEmpty() -> displayJobTitle(job)
      rootIds.size == 1 -> displayJobTitle(jobsById[rootIds.first()] ?: job)
      else -> rootIds.joinToString(" + ") { rootId ->
        displayJobTitle(jobsById[rootId] ?: job.copy(jobId = rootId))
      }
    }
    laneKey to laneTitle
  }
}

private fun findScheduledJobRootIds(
  job: ScheduledJobSummary,
  jobsById: Map<String, ScheduledJobSummary>,
  visiting: MutableSet<String>,
): Set<String> {
  if (!visiting.add(job.jobId)) return setOf(job.jobId)
  if (job.dependsOn.isEmpty()) return setOf(job.jobId)
  val rootIds = linkedSetOf<String>()
  job.dependsOn.forEach { depId ->
    val depJob = jobsById[depId]
    if (depJob == null) {
      rootIds += depId
    } else {
      rootIds += findScheduledJobRootIds(depJob, jobsById, visiting)
    }
  }
  visiting.remove(job.jobId)
  return if (rootIds.isEmpty()) setOf(job.jobId) else rootIds
}

private fun sortScheduledJobsWithinBranch(
  jobs: List<ScheduledJobSummary>,
  jobsById: Map<String, ScheduledJobSummary>,
  nowUtc: String,
): List<ScheduledJobSummary> {
  val displayIds = jobs.map { it.jobId }.toSet()
  val projections = jobs.associate { it.jobId to projectScheduledJob(it, nowUtc) }
  val statuses = projections.mapValues { it.value.status }
  val activeJobs = jobs
    .filter { statuses[it.jobId] == ScheduledJobSurfaceStatus.RUNNING }
    .sortedWith(defaultScheduledJobComparator(nowUtc))
  if (activeJobs.isEmpty()) {
    val depthCache = mutableMapOf<String, Int>()
    return jobs.sortedWith(
      compareBy<ScheduledJobSummary> { scheduleSortBucket(projectScheduledJob(it, nowUtc).status) }
        .thenBy { scheduledJobDepth(it, jobsById, depthCache, mutableSetOf()) }
        .thenBy { scheduleDueSortKey(it) }
        .thenByDescending { parseInstantOrNull(it.state.activeStartedUtc)?.toEpochMilli() ?: Long.MIN_VALUE }
        .thenByDescending { parseInstantOrNull(it.state.lastFinishedUtc)?.toEpochMilli() ?: Long.MIN_VALUE }
        .thenBy { it.jobId },
    )
  }

  val activeOrder = activeJobs.mapIndexed { index, job -> job.jobId to index }.toMap()
  val lineageCache = mutableMapOf<String, ScheduledJobActiveLineage?>()
  val depthCache = mutableMapOf<String, Int>()
  return jobs.sortedWith(
    compareBy<ScheduledJobSummary> {
      val lineage = scheduledJobDisplayedActiveLineage(
        job = it,
        displayIds = displayIds,
        jobsById = jobsById,
        statuses = statuses,
        cache = lineageCache,
        visiting = mutableSetOf(),
      )
      if (lineage == null) 1 else 0
    }
      .thenBy {
        val lineage = scheduledJobDisplayedActiveLineage(
          job = it,
          displayIds = displayIds,
          jobsById = jobsById,
          statuses = statuses,
          cache = lineageCache,
          visiting = mutableSetOf(),
        )
        lineage?.activeJobId?.let { activeOrder[it] } ?: Int.MAX_VALUE
      }
      .thenBy {
        val lineage = scheduledJobDisplayedActiveLineage(
          job = it,
          displayIds = displayIds,
          jobsById = jobsById,
          statuses = statuses,
          cache = lineageCache,
          visiting = mutableSetOf(),
        )
        if (lineage != null && lineage.depth == 0) 0 else 1
      }
      .thenBy {
        val lineage = scheduledJobDisplayedActiveLineage(
          job = it,
          displayIds = displayIds,
          jobsById = jobsById,
          statuses = statuses,
          cache = lineageCache,
          visiting = mutableSetOf(),
        )
        lineage?.depth ?: Int.MAX_VALUE
      }
      .thenBy { scheduleSortBucket(statuses.getValue(it.jobId)) }
      .thenBy { scheduledJobDepth(it, jobsById, depthCache, mutableSetOf()) }
      .thenBy { scheduleDueSortKey(it) }
      .thenByDescending { parseInstantOrNull(it.state.activeStartedUtc)?.toEpochMilli() ?: Long.MIN_VALUE }
      .thenByDescending { parseInstantOrNull(it.state.lastFinishedUtc)?.toEpochMilli() ?: Long.MIN_VALUE }
      .thenBy { it.jobId },
  )
}

internal fun buildScheduledJobBranchIndentDepths(
  jobs: List<ScheduledJobSummary>,
  jobsById: Map<String, ScheduledJobSummary>,
  nowUtc: String,
): Map<String, Int> {
  if (jobs.isEmpty()) return emptyMap()
  val branchIds = jobs.map { it.jobId }.toSet()
  val statuses = jobs.associate { it.jobId to projectScheduledJob(it, nowUtc).status }
  val lineageCache = mutableMapOf<String, ScheduledJobActiveLineage?>()
  return jobs.associate { job ->
    val lineage = scheduledJobDisplayedActiveLineage(
      job = job,
      displayIds = branchIds,
      jobsById = jobsById,
      statuses = statuses,
      cache = lineageCache,
      visiting = mutableSetOf(),
    )
    job.jobId to (lineage?.depth?.coerceIn(0, 2) ?: 0)
  }
}

private fun defaultScheduledJobComparator(nowUtc: String): Comparator<ScheduledJobSummary> {
  return compareBy<ScheduledJobSummary> { scheduleSortBucket(projectScheduledJob(it, nowUtc).status) }
    .thenBy { scheduleDueSortKey(it) }
    .thenByDescending { parseInstantOrNull(it.state.activeStartedUtc)?.toEpochMilli() ?: Long.MIN_VALUE }
    .thenByDescending { parseInstantOrNull(it.state.lastFinishedUtc)?.toEpochMilli() ?: Long.MIN_VALUE }
    .thenBy { it.jobId }
}

private fun scheduledJobDepth(
  job: ScheduledJobSummary,
  jobsById: Map<String, ScheduledJobSummary>,
  depthCache: MutableMap<String, Int>,
  visiting: MutableSet<String>,
): Int {
  depthCache[job.jobId]?.let { return it }
  if (!visiting.add(job.jobId)) return 0
  val depth = if (job.dependsOn.isEmpty()) {
    0
  } else {
    1 + job.dependsOn.maxOf { depId ->
      jobsById[depId]?.let { scheduledJobDepth(it, jobsById, depthCache, visiting) } ?: 0
    }
  }
  visiting.remove(job.jobId)
  depthCache[job.jobId] = depth
  return depth
}

private data class ScheduledJobActiveLineage(
  val activeJobId: String,
  val depth: Int,
)

private fun scheduledJobDisplayedActiveLineage(
  job: ScheduledJobSummary,
  displayIds: Set<String>,
  jobsById: Map<String, ScheduledJobSummary>,
  statuses: Map<String, ScheduledJobSurfaceStatus>,
  cache: MutableMap<String, ScheduledJobActiveLineage?>,
  visiting: MutableSet<String>,
): ScheduledJobActiveLineage? {
  cache[job.jobId]?.let { return it }
  if (!visiting.add(job.jobId)) return null
  val status = statuses[job.jobId] ?: ScheduledJobSurfaceStatus.UNKNOWN
  if (status == ScheduledJobSurfaceStatus.RUNNING) {
    visiting.remove(job.jobId)
    return ScheduledJobActiveLineage(activeJobId = job.jobId, depth = 0).also { cache[job.jobId] = it }
  }
  if (status == ScheduledJobSurfaceStatus.DEGRADED) {
    visiting.remove(job.jobId)
    return ScheduledJobActiveLineage(activeJobId = job.jobId, depth = 0).also { cache[job.jobId] = it }
  }
  if (status == ScheduledJobSurfaceStatus.SUCCEEDED || status == ScheduledJobSurfaceStatus.FAILED) {
    visiting.remove(job.jobId)
    cache[job.jobId] = null
    return null
  }
  val best = job.dependsOn
    .mapNotNull { depId ->
      val depJob = jobsById[depId] ?: return@mapNotNull null
      if (depJob.jobId !in displayIds) return@mapNotNull null
      val lineage = scheduledJobDisplayedActiveLineage(
        job = depJob,
        displayIds = displayIds,
        jobsById = jobsById,
        statuses = statuses,
        cache = cache,
        visiting = visiting,
      ) ?: return@mapNotNull null
      ScheduledJobActiveLineage(activeJobId = lineage.activeJobId, depth = lineage.depth + 1)
    }
    .minWithOrNull(compareBy<ScheduledJobActiveLineage> { it.depth }.thenBy { it.activeJobId })
  visiting.remove(job.jobId)
  cache[job.jobId] = best
  return best
}

private fun buildScheduledJobBranchSubtitle(
  jobs: List<ScheduledJobSummary>,
  nowUtc: String,
): String {
  if (jobs.isEmpty()) return ""
  val statuses = jobs.map { projectScheduledJob(it, nowUtc).status }
  val activeCount = statuses.count { it == ScheduledJobSurfaceStatus.RUNNING }
  val pendingCount = statuses.count { it == ScheduledJobSurfaceStatus.PENDING || it == ScheduledJobSurfaceStatus.WAITING }
  val failedCount = statuses.count { it == ScheduledJobSurfaceStatus.FAILED }
  val degradedCount = statuses.count { it == ScheduledJobSurfaceStatus.DEGRADED }
  val nextDue = jobs
    .mapNotNull { parseInstantOrNull(it.due.dueUtc.ifBlank { it.schedule.runAtUtc }) }
    .minOrNull()
  val parts = mutableListOf<String>()
  if (activeCount > 0) parts += "$activeCount active"
  if (pendingCount > 0) parts += "$pendingCount queued"
  if (degradedCount > 0) parts += "$degradedCount degraded"
  if (failedCount > 0) parts += "$failedCount failed"
  if (nextDue != null) parts += "next ${formatViennaTime(nextDue.toString())}"
  if (parts.isEmpty()) parts += "${jobs.size} job${if (jobs.size == 1) "" else "s"}"
  return parts.joinToString(" • ")
}

private fun scheduledJobBranchSortBucket(
  jobs: List<ScheduledJobSummary>,
  nowUtc: String,
): Int {
  val statuses = jobs.map { projectScheduledJob(it, nowUtc).status }
  return when {
    statuses.any { it == ScheduledJobSurfaceStatus.RUNNING } -> 0
    statuses.any { it == ScheduledJobSurfaceStatus.PENDING || it == ScheduledJobSurfaceStatus.WAITING } -> 1
    statuses.any { it == ScheduledJobSurfaceStatus.DEGRADED || it == ScheduledJobSurfaceStatus.FAILED } -> 2
    statuses.any { it == ScheduledJobSurfaceStatus.BLOCKED } -> 3
    statuses.any { it == ScheduledJobSurfaceStatus.SUCCEEDED } -> 4
    else -> 5
  }
}

private fun scheduledJobBranchDueSortKey(jobs: List<ScheduledJobSummary>): Long {
  return jobs.minOfOrNull(::scheduleDueSortKey) ?: Long.MAX_VALUE
}

private fun scheduleSortBucket(status: ScheduledJobSurfaceStatus): Int {
  return when (status) {
    ScheduledJobSurfaceStatus.RUNNING -> 0
    ScheduledJobSurfaceStatus.PENDING, ScheduledJobSurfaceStatus.WAITING -> 1
    ScheduledJobSurfaceStatus.DEGRADED, ScheduledJobSurfaceStatus.FAILED -> 2
    ScheduledJobSurfaceStatus.BLOCKED -> 3
    ScheduledJobSurfaceStatus.SUCCEEDED -> 4
    ScheduledJobSurfaceStatus.UNKNOWN -> 5
  }
}

private fun scheduleDueSortKey(job: ScheduledJobSummary): Long {
  val due = parseInstantOrNull(job.due.dueUtc)?.toEpochMilli()
  val runAt = parseInstantOrNull(job.schedule.runAtUtc)?.toEpochMilli()
  return due ?: runAt ?: Long.MAX_VALUE
}

private fun formatViennaTime(utc: String): String {
  val parsed = parseInstantOrNull(utc) ?: return ""
  return ViennaDateTimeFormatter.format(parsed)
}
