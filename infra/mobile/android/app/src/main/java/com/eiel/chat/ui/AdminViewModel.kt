package com.eiel.chat.ui

import android.app.Application
import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.eiel.chat.data.AdminApi
import com.eiel.chat.data.AdminConfig
import com.eiel.chat.data.AdminJob
import com.eiel.chat.data.ArchiveItem
import com.eiel.chat.data.BuilderHarness
import com.eiel.chat.data.BuilderHarnessesPayload
import com.eiel.chat.data.BuilderJobStatus
import com.eiel.chat.data.BuilderLoadedHarnessPayload
import com.eiel.chat.data.BuilderConfig
import com.eiel.chat.data.BuilderGenerateHarnessResult
import com.eiel.chat.data.BuilderGenerateManifestResult
import com.eiel.chat.data.BuilderRegistry
import com.eiel.chat.data.BuilderRecentJob
import com.eiel.chat.data.BuilderTemplate
import com.eiel.chat.data.BuilderTemplatePayload
import com.eiel.chat.data.CommitJournalEntry
import com.eiel.chat.data.DownloadPayload
import com.eiel.chat.data.EpiAvPayload
import com.eiel.chat.data.FsEntry
import com.eiel.chat.data.GitStatus
import com.eiel.chat.data.ManifestJob
import com.eiel.chat.data.OperatorSnapshot
import com.eiel.chat.data.PlaneLogSnapshot
import com.eiel.chat.data.PlaneHistoryEntry
import com.eiel.chat.data.RestartBlocker
import com.eiel.chat.data.RestartServerResult
import com.eiel.chat.data.RunSummary
import com.eiel.chat.data.ScheduledJobDetail
import com.eiel.chat.data.ScheduledJobSummary
import com.eiel.chat.data.SchedulerHealthPayload
import com.eiel.chat.data.SchedulerServiceStatus
import com.eiel.chat.data.SchedulerMonitoringServiceStatus
import com.eiel.chat.data.ServerConfig
import com.eiel.chat.data.SystemProcess
import com.eiel.chat.data.SystemRun
import com.eiel.chat.util.AppLogger
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.net.URLEncoder
import java.security.MessageDigest
import java.util.UUID

private const val DEFAULT_TERMINAL_TIMEOUT_SEC = "7000"
private const val ADMIN_CONSOLE_MAX_CHARS = 64_000
private val LedgerItemPisPattern = Regex("^PIS-[0-9]{8}-[0-9]{4,6}Z-[A-Z0-9]+$", RegexOption.IGNORE_CASE)

internal enum class AdminUserToolRisk(
  val label: String,
  val requiresConfirmation: Boolean,
) {
  READ_ONLY(label = "read-only", requiresConfirmation = false),
  SAFE_MUTATION(label = "safe mutation", requiresConfirmation = true),
  DISRUPTIVE(label = "disruptive", requiresConfirmation = true),
}

internal data class AdminUserToolPreset(
  val id: String,
  val label: String,
  val description: String,
  val defaultCmd: String,
  val defaultTimeoutSec: String = DEFAULT_TERMINAL_TIMEOUT_SEC,
  val risk: AdminUserToolRisk = AdminUserToolRisk.READ_ONLY,
)

internal val AdminUserToolPresets: List<AdminUserToolPreset> = listOf(
  AdminUserToolPreset("find", "find", "Search the repo with a safe default pattern.", "bash infra/user/find scheduler .", "120"),
  AdminUserToolPreset("git-commit-tag", "git-commit-tag", "Show commit/tag helper usage.", "bash infra/user/git-commit-tag --help", "120"),
  AdminUserToolPreset("git-review", "git-review", "Show git review helper usage.", "bash infra/user/git-review --help", "120"),
  AdminUserToolPreset("next-jobs", "next-jobs", "List the next scheduled jobs.", "bash infra/user/next-jobs --limit 12", "120"),
  AdminUserToolPreset("schedule-job", "schedule-job", "List scheduled jobs through the scheduler CLI.", "bash infra/user/schedule-job list", "120"),
  AdminUserToolPreset("scheduler", "scheduler", "Preview the interactive scheduler helper menu and exit.", "printf 'q\\n' | bash infra/user/scheduler", "120"),
  AdminUserToolPreset("scheduler-change-job-model", "scheduler-change-job-model", "Show usage for model-changing helper.", "bash infra/user/scheduler-change-job-model --help", "120"),
  AdminUserToolPreset("scheduler-disable-all", "scheduler-disable-all", "Dry-run disabling all jobs.", "bash infra/user/scheduler-disable-all --dry-run", "120"),
  AdminUserToolPreset("scheduler-enable-all", "scheduler-enable-all", "Dry-run enabling all jobs.", "bash infra/user/scheduler-enable-all --dry-run", "120"),
  AdminUserToolPreset("scheduler-enable-disable-jobs", "scheduler-enable-disable-jobs", "Show usage for selective enable/disable helper.", "bash infra/user/scheduler-enable-disable-jobs --help", "120"),
  AdminUserToolPreset("scheduler-health", "scheduler-health", "Run the scheduler health check.", "bash infra/user/scheduler-health", "120"),
  AdminUserToolPreset("scheduler-pause", "scheduler-pause", "Pause the scheduler pipeline and wait for active work to drain.", "bash infra/user/scheduler-pause", "1800", AdminUserToolRisk.SAFE_MUTATION),
  AdminUserToolPreset("scheduler-postpone-active-job", "scheduler-postpone-active-job", "Show postpone-active-job helper usage.", "bash infra/user/scheduler-postpone-active-job --help", "120"),
  AdminUserToolPreset("scheduler-recover", "scheduler-recover", "Run recovery diagnosis only and abort at apply prompt.", "printf 'n\\n' | bash infra/user/scheduler-recover", "600"),
  AdminUserToolPreset("scheduler-resume", "scheduler-resume", "Resume the scheduler pipeline from the saved pause manifest.", "bash infra/user/scheduler-resume", "300", AdminUserToolRisk.SAFE_MUTATION),
  AdminUserToolPreset("scheduler-watch", "scheduler-watch", "Watch the live scheduler dashboard.", "bash infra/user/scheduler-watch 15", "21600"),
  AdminUserToolPreset("server", "server", "Preview the server helper menu and exit.", "printf 'q\\n' | bash infra/user/server", "120"),
  AdminUserToolPreset("server-restart", "server-restart", "Restart the server with default cooldown and drain handling.", "bash infra/user/server-restart", "7200", AdminUserToolRisk.DISRUPTIVE),
  AdminUserToolPreset("server-start", "server-start", "Start the WebUI server stack.", "bash infra/user/server-start", "600", AdminUserToolRisk.SAFE_MUTATION),
  AdminUserToolPreset("server-status", "server-status", "Show WebUI and scheduler status.", "bash infra/user/server-status", "120"),
  AdminUserToolPreset("server-stop", "server-stop", "Stop the WebUI server after draining active work.", "bash infra/user/server-stop", "7200", AdminUserToolRisk.DISRUPTIVE),
  AdminUserToolPreset("venv-activate", "venv-activate", "Print the shell command to activate the project venv.", "bash infra/user/venv-activate", "120"),
)

internal fun adminUserToolPreset(id: String): AdminUserToolPreset =
  AdminUserToolPresets.firstOrNull { it.id == id } ?: AdminUserToolPresets.first()

enum class AdminRefreshMode(
  val intervalMs: Long,
  val label: String,
) {
  FAST(2_000L, "fast"),
  NORMAL(4_000L, "normal"),
  BATTERY(8_000L, "battery");

  companion object {
    fun fromLabel(raw: String?): AdminRefreshMode {
      return entries.firstOrNull { it.label == raw } ?: NORMAL
    }
  }
}

data class DownloadedFile(
  val id: String = UUID.randomUUID().toString(),
  val filename: String,
  val mimeType: String,
  val absolutePath: String,
)

data class AdminUiState(
  val adminToken: String = "",
  val healthStatus: String = "unknown",
  val repoRoot: String = "?",
  val archivesDir: String = "",
  val refreshMode: AdminRefreshMode = AdminRefreshMode.NORMAL,
  val pauseWhenHidden: Boolean = true,
  val statusMessage: String = "",
  val restartBlockers: List<RestartBlocker> = emptyList(),
  val operatorSnapshot: OperatorSnapshot? = null,
  val operatorSnapshotLoading: Boolean = false,
  val operatorSnapshotError: String = "",
  val planePrompt: String = "",
  val planePlanMode: Boolean = false,
  val planeHistory: List<PlaneHistoryEntry> = emptyList(),
  val planeHistoryLoading: Boolean = false,
  val planeHistoryError: String = "",
  val planeJobId: String = "",
  val planeRunning: Boolean = false,
  val planeOutput: String = "",
  val planeOutputTitle: String = "the plane",
  val planeLog: PlaneLogSnapshot? = null,
  val planeLogLoading: Boolean = false,
  val planeLogError: String = "",
  val appUpdateBuilding: Boolean = false,
  val appUpdateDownloading: Boolean = false,
  val appUpdateJobId: String = "",
  val appUpdateChannel: String = "",
  val appUpdateRemoteSha256: String = "",
  val appUpdateRemoteBuiltAt: String = "",
  val appUpdateRemoteApkName: String = "",
  val appUpdateMessage: String = "",
  val appUpdatePendingInstallPath: String = "",
  val cmdText: String = "",
  val cmdCwd: String = "",
  val cmdTimeoutSec: String = DEFAULT_TERMINAL_TIMEOUT_SEC,
  val selectedUserToolId: String = AdminUserToolPresets.first().id,
  val cmdJobId: String = "",
  val cmdRunning: Boolean = false,
  val terminalOutput: String = "",
  val terminalOutputTitle: String = "console",
  val gitBranch: String = "?",
  val gitHead: String = "?",
  val gitDescribe: String = "?",
  val gitStatusPorcelain: String = "",
  val gitBaseline: String = "",
  val gitDoCommit: Boolean = true,
  val gitDoTag: Boolean = true,
  val gitUseJournalMessage: Boolean = true,
  val gitMessage: String = "",
  val gitJournalStatus: String = "Journal: unknown",
  val gitJournalPreviewSubject: String = "",
  val gitJournalEntries: List<CommitJournalEntry> = emptyList(),
  val gitJobId: String = "",
  val gitRunning: Boolean = false,
  val archives: List<ArchiveItem> = emptyList(),
  val archivesMessage: String = "",
  val patchApplyMessage: String = "",
  val patchApplying: Boolean = false,
  val patchApplyOutcome: String = "",
  val patchApplyRequestId: String = "",
  val runs: List<SystemRun> = emptyList(),
  val runSummaries: List<RunSummary> = emptyList(),
  val runsMessage: String = "",
  val selectedRunKey: String = "",
  val selectedRunStdoutText: String = "",
  val selectedRunStdoutLoading: Boolean = false,
  val selectedRunEpiAv: EpiAvPayload? = null,
  val selectedRunEpiAvLoading: Boolean = false,
  val selectedRunEpiAvError: String = "",
  val archiveDebugOnly: Boolean = false,
  val manifests: List<ManifestJob> = emptyList(),
  val manifestsMessage: String = "",
  val processes: List<SystemProcess> = emptyList(),
  val processesIncludeAll: Boolean = false,
  val processesMessage: String = "",
  val builderHarnesses: List<BuilderHarness> = emptyList(),
  val builderConfig: BuilderConfig? = null,
  val builderRegistries: List<BuilderRegistry> = emptyList(),
  val builderTemplates: List<BuilderTemplate> = emptyList(),
  val builderRecentJobs: List<BuilderRecentJob> = emptyList(),
  val builderActiveJobId: String = "",
  val builderSelectedCmd: String = "",
  val builderSelectedName: String = "",
  val builderSelectedRegistryPath: String = "",
  val builderSelectedTemplateName: String = "",
  val builderTemplateDraftName: String = "",
  val builderStatusMessage: String = "",
  val builderJobStatus: BuilderJobStatus? = null,
  val builderLog: String = "",
  val builderHarnessNameInput: String = "",
  val builderHarnessDescriptionInput: String = "",
  val builderHarnessQuestionInput: String = "",
  val builderHarnessRunsRootInput: String = "",
  val builderHarnessSeedsInput: String = "1",
  val builderHarnessCaseValuesInput: String = "default",
  val builderHarnessDefaultKnobsInput: String = "",
  val builderHarnessArtifactsInput: String = "",
  val builderHarnessGenerationsInput: String = "",
  val builderHarnessStepsInput: String = "",
  val builderHarnessPopInput: String = "",
  val builderHarnessGridInput: String = "",
  val builderHarnessStrideInput: String = "",
  val builderHarnessCkptEveryInput: String = "",
  val builderHarnessCkptKeepInput: String = "",
  val builderHarnessPerf: Boolean = false,
  val builderHarnessProfiler: Boolean = false,
  val builderHarnessResult: String = "",
  val builderHarnessRegistryPreview: String = "",
  val builderHarnessScriptPreview: String = "",
  val builderManifestNameInput: String = "",
  val builderManifestOutputRootInput: String = "",
  val builderManifestGitRefInput: String = "",
  val builderManifestManualPathsInput: String = "",
  val builderManifestIncludeSelectedRegistry: Boolean = true,
  val builderManifestStrict: Boolean = false,
  val builderManifestResult: String = "",
  val builderManifestPreview: String = "",
  val builderManifestStartCmd: String = "",
  val fsPath: String = "",
  val fsIncludeHidden: Boolean = true,
  val fsEntries: List<FsEntry> = emptyList(),
  val fsSelections: Set<String> = emptySet(),
  val fsPreviewPath: String = "",
  val fsPreviewText: String = "",
  val fsPreviewTruncated: Boolean = false,
  val fsPreviewLoading: Boolean = false,
  val fsPreviewError: String = "",
  val zipName: String = "",
  val replaceNote: String = "",
  val downloads: List<DownloadedFile> = emptyList(),
  val localDownloadsDir: String = "",
  val pendingDownloadId: String = "",
  val scheduledJobs: List<ScheduledJobSummary> = emptyList(),
  val scheduledJobsLoading: Boolean = false,
  val scheduledJobsMessage: String = "",
  val scheduledJobsFetchedUtc: String = "",
  val scheduledScheduler: SchedulerServiceStatus = SchedulerServiceStatus(
    running = false,
    intervalSeconds = 0,
    startedUtc = "",
    lastTickUtc = "",
    lastError = "",
    repoRoot = "",
  ),
  val scheduledWatchdog: SchedulerMonitoringServiceStatus? = null,
  val scheduledHealer: SchedulerMonitoringServiceStatus? = null,
  val scheduledHealthLoading: Boolean = false,
  val scheduledHealthMessage: String = "",
  val scheduledHealthFetchedUtc: String = "",
  val scheduledHealth: SchedulerHealthPayload? = null,
  val selectedScheduledJobId: String = "",
  val scheduledJobDetail: ScheduledJobDetail? = null,
  val scheduledJobDetailLoading: Boolean = false,
  val scheduledJobDetailError: String = "",
  val scheduledJobActionInFlight: Boolean = false,
  val scheduledJobActionLabel: String = "",
  val selectedPipelineScheduledJobId: String = "",
  val pipelineScheduledJobDetail: ScheduledJobDetail? = null,
  val pipelineScheduledJobDetailLoading: Boolean = false,
  val pipelineScheduledJobDetailError: String = "",
)

class AdminViewModel(
  application: Application,
) : AndroidViewModel(application) {
  private val api = AdminApi(serverBaseUrlProvider = { ServerConfig.resolveBaseUrl(application) })
  private val localDownloadsDir by lazy { resolveLocalDownloadsDir(application) }
  private val prefs by lazy {
    application.getSharedPreferences("admin_ui_state", Context.MODE_PRIVATE)
  }
  private val _uiState = MutableStateFlow(AdminUiState(localDownloadsDir = localDownloadsDir.absolutePath))
  val uiState: StateFlow<AdminUiState> = _uiState.asStateFlow()

  private var autoRefreshJob: Job? = null
  private var commandPollJob: Job? = null
  private var planePollJob: Job? = null
  private var mobileDeployPollJob: Job? = null
  private var gitPollJob: Job? = null

  init {
    restorePrefs()
    refreshHealth()
    refreshOperatorSnapshot()
    refreshPlaneHistory()
    loadConfig()
    refreshArchives()
    refreshScheduledJobs()
    startAutoRefresh()
  }

  fun onAdminTokenChanged(value: String) {
    _uiState.update { it.copy(adminToken = value) }
    prefs.edit().putString("admin_token", value).apply()
  }

  fun onRefreshModeChanged(mode: AdminRefreshMode) {
    _uiState.update { it.copy(refreshMode = mode) }
    prefs.edit().putString("refresh_mode", mode.label).apply()
    startAutoRefresh()
  }

  fun onArchiveDebugOnlyChanged(enabled: Boolean) {
    _uiState.update { it.copy(archiveDebugOnly = enabled) }
  }

  fun onProcessesIncludeAllChanged(enabled: Boolean) {
    _uiState.update { it.copy(processesIncludeAll = enabled) }
  }

  fun onCmdTextChanged(value: String) {
    _uiState.update { it.copy(cmdText = value) }
  }

  fun onSelectedUserToolChanged(value: String) {
    val preset = adminUserToolPreset(value)
    _uiState.update { it.copy(selectedUserToolId = preset.id) }
  }

  fun onCmdCwdChanged(value: String) {
    _uiState.update { it.copy(cmdCwd = value) }
  }

  fun onCmdTimeoutChanged(value: String) {
    _uiState.update { it.copy(cmdTimeoutSec = value) }
  }

  fun onPlanePromptChanged(value: String) {
    _uiState.update { it.copy(planePrompt = value) }
  }

  fun onPlanePlanModeChanged(enabled: Boolean) {
    _uiState.update { it.copy(planePlanMode = enabled) }
  }

  fun clearCommandAndTerminal() {
    _uiState.update {
      it.copy(
        cmdText = "",
        terminalOutput = "",
        terminalOutputTitle = "console",
      )
    }
  }

  fun installLatestAppUpdate() {
    val state = _uiState.value
    if (state.appUpdateDownloading) return
    val channel = state.appUpdateChannel.ifBlank {
      appUpdateChannelForPackageName(getApplication<Application>().packageName)
    }
    launchAdmin("install app update") {
      try {
        _uiState.update {
          it.copy(
            appUpdateDownloading = true,
            appUpdateChannel = channel,
            appUpdateMessage = "Downloading latest $channel APK...",
          )
        }
        val payload = withContext(Dispatchers.IO) { api.downloadMobileAppUpdate(channel, adminToken = token()) }
        val file = saveDownload(payload)
        val downloadedApk = File(file.absolutePath)
        val actualSha = withContext(Dispatchers.IO) { sha256Hex(downloadedApk) }
        val expectedSha = state.appUpdateRemoteSha256.ifBlank {
          withContext(Dispatchers.IO) { api.mobileAppUpdateManifest(channel, adminToken = token()).apkSha256 }
        }
        if (expectedSha.isNotBlank() && !actualSha.equals(expectedSha, ignoreCase = true)) {
          downloadedApk.delete()
          throw IllegalStateException("Downloaded APK checksum mismatch")
        }
        _uiState.update {
          it.copy(
            appUpdateDownloading = false,
            appUpdateRemoteSha256 = actualSha,
            appUpdatePendingInstallPath = file.absolutePath,
            appUpdateMessage = "Downloaded ${file.filename}. Launching installer...",
            downloads = listOf(
              DownloadedFile(
                filename = file.filename,
                mimeType = payload.mimeType,
                absolutePath = file.absolutePath,
              ),
            ) + it.downloads.filterNot { item -> item.absolutePath == file.absolutePath }.take(7),
          )
        }
      } catch (t: Throwable) {
        _uiState.update {
          it.copy(
            appUpdateDownloading = false,
            appUpdateMessage = t.message ?: "App update download failed",
          )
        }
        throw t
      }
    }
  }

  fun runPrimaryDeployAction() {
    val state = _uiState.value
    if (state.appUpdateBuilding || state.appUpdateDownloading) return
    runMobileDeploy()
  }

  fun runCommitFromJournal() {
    if (_uiState.value.gitRunning) return
    launchAdmin("commit from journal") {
      clearConsoleFor("commit")
      val job = withContext(Dispatchers.IO) { api.gitRun(token()) }
      applyGitJob(job)
      pollGitJob(job.jobId)
    }
  }

  fun onAppUpdateInstallHandled() {
    _uiState.update { it.copy(appUpdatePendingInstallPath = "") }
  }

  fun onGitBaselineChanged(value: String) {
    _uiState.update { it.copy(gitBaseline = value) }
  }

  fun onGitMessageChanged(value: String) {
    _uiState.update { it.copy(gitMessage = value) }
  }

  fun onGitUseJournalMessageChanged(enabled: Boolean) {
    _uiState.update { it.copy(gitUseJournalMessage = enabled) }
    prefs.edit().putBoolean("git_use_journal_message", enabled).apply()
  }

  fun onGitDoCommitChanged(enabled: Boolean) {
    _uiState.update { it.copy(gitDoCommit = enabled) }
  }

  fun onGitDoTagChanged(enabled: Boolean) {
    _uiState.update { it.copy(gitDoTag = enabled) }
  }

  fun onBuilderSelectedRegistryChanged(path: String) {
    _uiState.update { it.copy(builderSelectedRegistryPath = path) }
    prefs.edit().putString("builder_selected_registry_path", path).apply()
  }

  fun onBuilderSelectedTemplateChanged(name: String) {
    _uiState.update { it.copy(builderSelectedTemplateName = name) }
  }

  fun onBuilderTemplateDraftNameChanged(value: String) {
    _uiState.update { it.copy(builderTemplateDraftName = value) }
  }

  fun onBuilderHarnessNameChanged(value: String) = updateBuilder { it.copy(builderHarnessNameInput = value) }
  fun onBuilderHarnessDescriptionChanged(value: String) = updateBuilder { it.copy(builderHarnessDescriptionInput = value) }
  fun onBuilderHarnessQuestionChanged(value: String) = updateBuilder { it.copy(builderHarnessQuestionInput = value) }
  fun onBuilderHarnessRunsRootChanged(value: String) = updateBuilder { it.copy(builderHarnessRunsRootInput = value) }
  fun onBuilderHarnessSeedsChanged(value: String) = updateBuilder { it.copy(builderHarnessSeedsInput = value) }
  fun onBuilderHarnessCaseValuesChanged(value: String) = updateBuilder { it.copy(builderHarnessCaseValuesInput = value) }
  fun onBuilderHarnessDefaultKnobsChanged(value: String) = updateBuilder { it.copy(builderHarnessDefaultKnobsInput = value) }
  fun onBuilderHarnessArtifactsChanged(value: String) = updateBuilder { it.copy(builderHarnessArtifactsInput = value) }
  fun onBuilderHarnessGenerationsChanged(value: String) = updateBuilder { it.copy(builderHarnessGenerationsInput = value) }
  fun onBuilderHarnessStepsChanged(value: String) = updateBuilder { it.copy(builderHarnessStepsInput = value) }
  fun onBuilderHarnessPopChanged(value: String) = updateBuilder { it.copy(builderHarnessPopInput = value) }
  fun onBuilderHarnessGridChanged(value: String) = updateBuilder { it.copy(builderHarnessGridInput = value) }
  fun onBuilderHarnessStrideChanged(value: String) = updateBuilder { it.copy(builderHarnessStrideInput = value) }
  fun onBuilderHarnessCkptEveryChanged(value: String) = updateBuilder { it.copy(builderHarnessCkptEveryInput = value) }
  fun onBuilderHarnessCkptKeepChanged(value: String) = updateBuilder { it.copy(builderHarnessCkptKeepInput = value) }
  fun onBuilderHarnessPerfChanged(enabled: Boolean) = updateBuilder { it.copy(builderHarnessPerf = enabled) }
  fun onBuilderHarnessProfilerChanged(enabled: Boolean) = updateBuilder { it.copy(builderHarnessProfiler = enabled) }
  fun onBuilderManifestNameChanged(value: String) = updateBuilder { it.copy(builderManifestNameInput = value) }
  fun onBuilderManifestOutputRootChanged(value: String) = updateBuilder { it.copy(builderManifestOutputRootInput = value) }
  fun onBuilderManifestGitRefChanged(value: String) = updateBuilder { it.copy(builderManifestGitRefInput = value) }
  fun onBuilderManifestManualPathsChanged(value: String) = updateBuilder { it.copy(builderManifestManualPathsInput = value) }
  fun onBuilderManifestIncludeSelectedRegistryChanged(enabled: Boolean) = updateBuilder { it.copy(builderManifestIncludeSelectedRegistry = enabled) }
  fun onBuilderManifestStrictChanged(enabled: Boolean) = updateBuilder { it.copy(builderManifestStrict = enabled) }

  fun onFsPathChanged(value: String) {
    _uiState.update { it.copy(fsPath = value) }
  }

  fun onFsIncludeHiddenChanged(enabled: Boolean) {
    _uiState.update { it.copy(fsIncludeHidden = enabled) }
    prefs.edit().putBoolean("fs_hidden", enabled).apply()
  }

  fun onZipNameChanged(value: String) {
    _uiState.update { it.copy(zipName = value) }
  }

  fun refreshAll() {
    refreshHealth()
    refreshOperatorSnapshot()
    refreshPlaneHistory()
    refreshPlaneLog()
    loadConfig()
    refreshGit()
    refreshArchives()
    refreshProcesses()
    refreshFsList()
    refreshScheduledJobs()
  }

  fun refreshHealth() {
    launchAdmin("refresh health") {
      val info = withContext(Dispatchers.IO) { api.health() }
      _uiState.update { it.copy(healthStatus = info.status) }
    }
  }

  fun refreshOperatorSnapshot() {
    if (_uiState.value.operatorSnapshotLoading) return
    _uiState.update {
      it.copy(
        operatorSnapshotLoading = true,
        operatorSnapshotError = "",
      )
    }
    viewModelScope.launch {
      runCatching { withContext(Dispatchers.IO) { api.labAssistantSnapshot(token()) } }
        .onSuccess { snapshot ->
          _uiState.update {
            it.copy(
              operatorSnapshot = snapshot,
              operatorSnapshotLoading = false,
              operatorSnapshotError = "",
            )
          }
        }
        .onFailure { error ->
          val msg = error.message ?: "Unable to load operator snapshot"
          AppLogger.w("admin failure refresh operator snapshot: $msg")
          _uiState.update {
            it.copy(
              operatorSnapshot = null,
              operatorSnapshotLoading = false,
              operatorSnapshotError = msg,
            )
          }
        }
    }
  }

  fun refreshPlaneLog() {
    if (_uiState.value.planeLogLoading) return
    _uiState.update {
      it.copy(
        planeLogLoading = true,
        planeLogError = "",
      )
    }
    viewModelScope.launch {
      runCatching { withContext(Dispatchers.IO) { api.labAssistantWebOperations(token()) } }
        .onSuccess { snapshot ->
          _uiState.update {
            it.copy(
              planeLog = snapshot,
              planeLogLoading = false,
              planeLogError = "",
            )
          }
        }
        .onFailure { error ->
          val msg = error.message ?: "Unable to load plane log"
          AppLogger.w("admin failure refresh plane log: $msg")
          _uiState.update {
            it.copy(
              planeLog = null,
              planeLogLoading = false,
              planeLogError = msg,
            )
          }
        }
    }
  }

  fun refreshPlaneHistory(limit: Int = 100) {
    if (_uiState.value.planeHistoryLoading) return
    if (token().isBlank()) {
      _uiState.update {
        it.copy(
          planeHistory = emptyList(),
          planeHistoryLoading = false,
          planeHistoryError = "",
        )
      }
      return
    }
    _uiState.update {
      it.copy(
        planeHistoryLoading = true,
        planeHistoryError = "",
      )
    }
    viewModelScope.launch {
      runCatching { withContext(Dispatchers.IO) { api.planeHistory(token(), limit = limit) } }
        .onSuccess { history ->
          _uiState.update {
            it.copy(
              planeHistory = history,
              planeHistoryLoading = false,
              planeHistoryError = "",
            )
          }
        }
        .onFailure { error ->
          val msg = error.message ?: "Unable to load plane history"
          AppLogger.w("admin failure refresh plane history: $msg")
          _uiState.update {
            it.copy(
              planeHistoryLoading = false,
              planeHistoryError = msg,
            )
          }
        }
    }
  }

  fun loadConfig() {
    launchAdmin("load config") {
      val cfg = withContext(Dispatchers.IO) { api.config(token()) }
      applyConfig(cfg)
    }
  }

  fun runCommand() {
    val s = _uiState.value
    val cmd = s.cmdText.trim()
    if (cmd.isBlank()) return
    runCommandJob(
      source = "command",
      cmd = cmd,
      cwd = s.cmdCwd.trim(),
      timeoutSec = s.cmdTimeoutSec,
    )
  }

  fun runPlanePrompt(promptOverride: String? = null) {
    val state = _uiState.value
    val prompt = (promptOverride ?: state.planePrompt).trim()
    if (prompt.isBlank()) return
    launchAdmin("run the plane") {
      val job = withContext(Dispatchers.IO) {
        api.planeRun(
          adminToken = token(),
          prompt = prompt,
          plan = _uiState.value.planePlanMode,
          verbose = false,
        )
      }
      _uiState.update { it.copy(planePrompt = prompt) }
      applyPlaneJob(job)
      refreshPlaneHistory()
      pollPlaneJob(job.jobId)
    }
  }

  fun interruptPlanePrompt() {
    val jid = _uiState.value.planeJobId.trim()
    if (jid.isBlank()) return
    launchAdmin("interrupt the plane") {
      withContext(Dispatchers.IO) { api.commandKill(token(), jid) }
      appendPlaneConsoleNote("[ctrl-c requested]")
    }
  }

  fun usePlaneHistoryPrompt(prompt: String, runNow: Boolean = false) {
    _uiState.update { it.copy(planePrompt = prompt) }
    if (runNow) {
      runPlanePrompt(promptOverride = prompt)
    }
  }

  fun loadSelectedUserTool() {
    val state = _uiState.value
    val preset = adminUserToolPreset(state.selectedUserToolId)
    _uiState.update {
      it.copy(
        cmdText = preset.defaultCmd,
        cmdCwd = resolveToolDefaultCwd(it),
        cmdTimeoutSec = preset.defaultTimeoutSec,
      )
    }
  }

  fun runSelectedUserTool() {
    val state = _uiState.value
    val preset = adminUserToolPreset(state.selectedUserToolId)
    val cwd = resolveToolDefaultCwd(state)
    _uiState.update {
      it.copy(
        cmdText = preset.defaultCmd,
        cmdCwd = cwd,
        cmdTimeoutSec = preset.defaultTimeoutSec,
      )
    }
    runCommandJob(
      source = "tool:${preset.label}",
      cmd = preset.defaultCmd,
      cwd = cwd,
      timeoutSec = preset.defaultTimeoutSec,
    )
  }

  fun killCommand() {
    val jid = _uiState.value.cmdJobId.trim()
    if (jid.isBlank()) return
    launchAdmin("kill command") {
      withContext(Dispatchers.IO) { api.commandKill(token(), jid) }
      appendConsoleNote("command", "[kill requested]")
    }
  }

  fun clearCommandOutput() {
    _uiState.update { it.copy(terminalOutput = "", terminalOutputTitle = "console") }
  }

  fun runGitStatus() {
    launchAdmin("git status") {
      clearConsoleFor("git status")
      val timeout = _uiState.value.cmdTimeoutSec.toDoubleOrNull()?.coerceIn(1.0, 21_600.0) ?: DEFAULT_TERMINAL_TIMEOUT_SEC.toDouble()
      val job = withContext(Dispatchers.IO) {
        api.commandRun(
          adminToken = token(),
          cmd = "git status",
          cwd = _uiState.value.cmdCwd.trim(),
          timeoutSec = timeout,
        )
      }
      applyCommandJob(job)
      pollCommandJob(job.jobId)
    }
  }

  fun interruptTerminal() {
    val state = _uiState.value
    val activeJobId = when {
      state.cmdRunning -> state.cmdJobId.trim()
      state.gitRunning -> state.gitJobId.trim()
      state.appUpdateBuilding -> state.appUpdateJobId.trim()
      else -> ""
    }
    if (activeJobId.isBlank()) return
    launchAdmin("interrupt terminal") {
      withContext(Dispatchers.IO) { api.commandKill(token(), activeJobId) }
      appendConsoleNote("terminal", "[ctrl-c requested]")
    }
  }

  fun runMobileDeploy() {
    val channel = appUpdateChannelForPackageName(getApplication<Application>().packageName)
    launchAdmin("deploy mobile app") {
      clearConsoleFor("deploy")
      _uiState.update {
        it.copy(
          appUpdateBuilding = true,
          appUpdateChannel = channel,
          appUpdateMessage = "Building fresh $channel APK...",
          appUpdatePendingInstallPath = "",
        )
      }
      val job = withContext(Dispatchers.IO) { api.buildMobileAppUpdate(channel, adminToken = token()) }
      applyMobileDeployJob(job)
      _uiState.update { it.copy(statusMessage = "Mobile deploy started") }
      pollMobileDeployJob(job.jobId, channel)
    }
  }

  fun refreshGit() {
    launchAdmin("refresh git") {
      val status = withContext(Dispatchers.IO) { api.gitStatus(token()) }
      applyGitStatus(status)
    }
  }

  fun runGit() {
    val s = _uiState.value
    if (s.gitRunning) return
    launchAdmin("run git") {
      clearConsoleFor("git")
      val job = withContext(Dispatchers.IO) { api.gitRun(token()) }
      applyGitJob(job)
      pollGitJob(job.jobId)
    }
  }

  fun killGitJob() {
    val jid = _uiState.value.gitJobId.trim()
    if (jid.isBlank()) return
    launchAdmin("kill git job") {
      withContext(Dispatchers.IO) { api.commandKill(token(), jid) }
      appendConsoleNote("git", "[kill requested]")
    }
  }

  fun refreshArchives() {
    launchAdmin("refresh archives") {
      val items = withContext(Dispatchers.IO) { api.archives(token()) }
      _uiState.update { it.copy(archives = items, archivesMessage = "") }
    }
  }

  fun refreshScheduledJobs() {
    if (_uiState.value.scheduledJobsLoading) return
    _uiState.update {
      it.copy(
        scheduledJobsLoading = true,
        scheduledJobsMessage = if (it.scheduledJobs.isEmpty()) "Loading scheduled jobs..." else it.scheduledJobsMessage,
      )
    }
    viewModelScope.launch {
      runCatching {
        withContext(Dispatchers.IO) { api.scheduledJobs(adminToken = token()) }
      }.onSuccess { payload ->
        _uiState.update { state ->
          state.copy(
            scheduledJobs = payload.jobs,
            scheduledJobsFetchedUtc = payload.fetchedUtc,
            scheduledScheduler = payload.scheduler,
            scheduledWatchdog = payload.watchdog,
            scheduledHealer = payload.healer,
            scheduledJobsLoading = false,
            scheduledJobsMessage = "",
          )
        }
        refreshSchedulerHealth()
        val selectedJobId = _uiState.value.selectedScheduledJobId
        if (selectedJobId.isNotBlank()) {
          refreshScheduledJobDetail(selectedJobId)
        }
        val selectedPipelineJobId = _uiState.value.selectedPipelineScheduledJobId
        if (selectedPipelineJobId.isNotBlank()) {
          refreshPipelineScheduledJobDetail(selectedPipelineJobId)
        }
      }.onFailure { error ->
        val msg = error.message ?: "Unable to load scheduled jobs"
        AppLogger.w("admin failure refresh scheduled jobs: $msg")
        _uiState.update {
          it.copy(
            scheduledJobsLoading = false,
            scheduledJobsMessage = msg,
            statusMessage = msg,
          )
        }
      }
    }
  }

  fun refreshSchedulerHealth() {
    if (_uiState.value.scheduledHealthLoading) return
    _uiState.update {
      it.copy(
        scheduledHealthLoading = true,
        scheduledHealthMessage = if (it.scheduledHealth == null) {
          "Loading scheduler health..."
        } else {
          it.scheduledHealthMessage
        },
      )
    }
    viewModelScope.launch {
      runCatching { withContext(Dispatchers.IO) { api.scheduledHealth(adminToken = token()) } }
        .onSuccess { payload ->
          _uiState.update {
            it.copy(
              scheduledHealthLoading = false,
              scheduledHealthMessage = "",
              scheduledHealthFetchedUtc = payload.fetchedUtc,
              scheduledHealth = payload,
            )
          }
        }
        .onFailure { error ->
          val msg = error.message ?: "Unable to load scheduler health"
          AppLogger.w("admin failure refresh scheduler health: $msg")
          _uiState.update {
            it.copy(
              scheduledHealthLoading = false,
              scheduledHealthMessage = msg,
              statusMessage = msg,
            )
          }
        }
    }
  }

  fun openScheduledJob(jobId: String) {
    val safeJobId = jobId.trim()
    if (safeJobId.isBlank()) return
    _uiState.update {
      it.copy(
        selectedPipelineScheduledJobId = "",
        pipelineScheduledJobDetail = null,
        pipelineScheduledJobDetailLoading = false,
        pipelineScheduledJobDetailError = "",
        selectedScheduledJobId = safeJobId,
        scheduledJobDetail = if (it.scheduledJobDetail?.job?.jobId == safeJobId) it.scheduledJobDetail else null,
        scheduledJobDetailError = "",
      )
    }
    refreshScheduledJobDetail(safeJobId)
  }

  fun openPipelineScheduledJobPis(
    jobId: String,
    onResolved: (String) -> Unit,
    onError: (String) -> Unit,
  ) {
    val safeJobId = jobId.trim()
    if (safeJobId.isBlank()) {
      return
    }
    val job = _uiState.value.scheduledJobs.firstOrNull { it.jobId == safeJobId }
    if (job == null) {
      onError("No scheduled job found for $safeJobId.")
      return
    }

    val directPath = boundPisPath(job)
    if (directPath.isNotBlank()) {
      onResolved(directPath)
      return
    }

    val uid = job.binding.id.trim()
    val boundUid = boundPisUid(job)
    val effectiveUid = if (boundUid.isNotBlank() && LedgerItemPisPattern.matches(boundUid)) {
      boundUid
    } else if (LedgerItemPisPattern.matches(uid)) {
      uid
    } else {
      ""
    }

    if (effectiveUid.isBlank()) {
      onError("No bound PIS file available for job ${safeJobId}.")
      return
    }

    viewModelScope.launch {
      runCatching { withContext(Dispatchers.IO) { api.ledgerItem(effectiveUid) } }
        .onSuccess { item ->
          val dir = item.dir.trim()
          if (dir.isBlank()) {
            resolvePipelineJobPisFromLedgerDirectory(effectiveUid, onResolved, onError, safeJobId)
          } else {
            onResolved("${dir}/pis.md")
          }
        }
        .onFailure { _ ->
          resolvePipelineJobPisFromLedgerDirectory(effectiveUid, onResolved, onError, safeJobId)
        }
    }
  }

  private fun resolvePipelineJobPisFromLedgerDirectory(
    uid: String,
    onResolved: (String) -> Unit,
    onError: (String) -> Unit,
    safeJobId: String,
  ) {
    viewModelScope.launch {
      runCatching {
        val (_, entries) = withContext(Dispatchers.IO) { api.fsList(token(), "governance/ledger/items", true) }
        entries.firstOrNull { it.isDir && it.name.startsWith("${uid}__") }?.path
      }.onSuccess { dirPath ->
        if (dirPath.isNullOrBlank()) {
          onError("No bound PIS file available for job ${safeJobId}.")
        } else {
          onResolved("$dirPath/pis.md")
        }
      }.onFailure { error ->
        onError(error.message ?: "Unable to load PIS for job ${safeJobId}.")
      }
    }
  }

  fun closeScheduledJobDetail() {
    _uiState.update {
      it.copy(
        selectedScheduledJobId = "",
        scheduledJobDetail = null,
        scheduledJobDetailLoading = false,
        scheduledJobDetailError = "",
        // Keep pipeline selection cleared when leaving any schedule/job detail surface.
        selectedPipelineScheduledJobId = "",
        pipelineScheduledJobDetail = null,
        pipelineScheduledJobDetailLoading = false,
        pipelineScheduledJobDetailError = "",
      )
    }
  }

  fun openPipelineScheduledJob(jobId: String) {
    val safeJobId = jobId.trim()
    if (safeJobId.isBlank()) return
    _uiState.update {
      it.copy(
        selectedScheduledJobId = "",
        scheduledJobDetail = null,
        scheduledJobDetailLoading = false,
        scheduledJobDetailError = "",
        selectedPipelineScheduledJobId = safeJobId,
        pipelineScheduledJobDetail = if (it.pipelineScheduledJobDetail?.job?.jobId == safeJobId) it.pipelineScheduledJobDetail else null,
        pipelineScheduledJobDetailError = "",
      )
    }
    refreshPipelineScheduledJobDetail(safeJobId)
  }

  fun closePipelineScheduledJobDetail() {
    _uiState.update {
      it.copy(
        selectedPipelineScheduledJobId = "",
        pipelineScheduledJobDetail = null,
        pipelineScheduledJobDetailLoading = false,
        pipelineScheduledJobDetailError = "",
        // Keep schedules selection cleared when leaving pipeline detail.
        selectedScheduledJobId = "",
        scheduledJobDetail = null,
        scheduledJobDetailLoading = false,
        scheduledJobDetailError = "",
      )
    }
  }

  fun refreshPipelineScheduledJobDetail(jobId: String = _uiState.value.selectedPipelineScheduledJobId) {
    val safeJobId = jobId.trim()
    if (safeJobId.isBlank()) return
    if (_uiState.value.pipelineScheduledJobDetailLoading && _uiState.value.selectedPipelineScheduledJobId == safeJobId) return
    _uiState.update {
      it.copy(
        selectedPipelineScheduledJobId = safeJobId,
        pipelineScheduledJobDetailLoading = true,
        pipelineScheduledJobDetailError = "",
      )
    }
    viewModelScope.launch {
      runCatching {
        withContext(Dispatchers.IO) { api.scheduledJobDetail(safeJobId, adminToken = token()) }
      }.onSuccess { detail ->
        _uiState.update {
          it.copy(
            selectedPipelineScheduledJobId = safeJobId,
            pipelineScheduledJobDetail = detail,
            pipelineScheduledJobDetailLoading = false,
            pipelineScheduledJobDetailError = "",
          )
        }
      }.onFailure { error ->
        val msg = error.message ?: "Unable to load scheduled job detail"
        AppLogger.w("admin failure refresh pipeline scheduled job detail: $msg")
        _uiState.update {
          it.copy(
            selectedPipelineScheduledJobId = safeJobId,
            pipelineScheduledJobDetailLoading = false,
            pipelineScheduledJobDetailError = msg,
            statusMessage = msg,
          )
        }
      }
    }
  }

  fun refreshScheduledJobDetail(jobId: String = _uiState.value.selectedScheduledJobId) {
    val safeJobId = jobId.trim()
    if (safeJobId.isBlank()) return
    if (_uiState.value.scheduledJobDetailLoading && _uiState.value.selectedScheduledJobId == safeJobId) return
    _uiState.update {
      it.copy(
        selectedScheduledJobId = safeJobId,
        scheduledJobDetailLoading = true,
        scheduledJobDetailError = "",
      )
    }
    viewModelScope.launch {
      runCatching {
        withContext(Dispatchers.IO) { api.scheduledJobDetail(safeJobId, adminToken = token()) }
      }.onSuccess { detail ->
        _uiState.update {
          it.copy(
            selectedScheduledJobId = safeJobId,
            scheduledJobDetail = detail,
            scheduledJobDetailLoading = false,
            scheduledJobDetailError = "",
          )
        }
      }.onFailure { error ->
        val msg = error.message ?: "Unable to load scheduled job detail"
        AppLogger.w("admin failure refresh scheduled job detail: $msg")
        _uiState.update {
          it.copy(
            selectedScheduledJobId = safeJobId,
            scheduledJobDetailLoading = false,
            scheduledJobDetailError = msg,
            statusMessage = msg,
          )
        }
      }
    }
  }

  fun updateScheduledJobRunProfile(
    jobId: String,
    model: String,
    maxIterations: Int?,
    runNow: Boolean,
  ) {
    val safeJobId = jobId.trim()
    val safeModel = model.trim()
    if (safeJobId.isBlank()) return
    val iterations = maxIterations?.coerceIn(1, 10)
    launchAdmin("update scheduled job run profile") {
      _uiState.update { it.copy(scheduledJobActionInFlight = true, scheduledJobActionLabel = if (runNow) "Saving + running..." else "Saving...") }
      try {
        withContext(Dispatchers.IO) {
          api.updateScheduledJobRunProfile(
            jobId = safeJobId,
            model = safeModel,
            maxIterations = iterations,
            adminToken = token(),
          )
        }
        var runNowResult = ""
        if (runNow) {
          val result = withContext(Dispatchers.IO) { api.runScheduledJobNow(safeJobId, adminToken = token()) }
          runNowResult = buildString {
            if (result.ok && result.queued) {
              append("Saved")
              append(" • run queued")
              if (result.reason.isNotBlank()) append(" • ${result.reason}")
              if (result.runId.isNotBlank()) append(" • request ${result.runId}")
            } else {
              append("Saved")
              append(" • run not queued")
              if (result.reason.isNotBlank()) append(" • ${result.reason}")
            }
          }
        }
        refreshScheduledJobs()
        refreshScheduledJobDetail(safeJobId)
        _uiState.update {
          it.copy(
            statusMessage = if (runNow) runNowResult.ifBlank { "Scheduled job updated and started." } else "Scheduled job updated.",
          )
        }
      } finally {
        _uiState.update { it.copy(scheduledJobActionInFlight = false, scheduledJobActionLabel = "") }
      }
    }
  }

  fun runScheduledJobNow(jobId: String) {
    val safeJobId = jobId.trim()
    if (safeJobId.isBlank()) return
    launchAdmin("run scheduled job now") {
      _uiState.update { it.copy(scheduledJobActionInFlight = true, scheduledJobActionLabel = "Running...") }
      try {
        val result = withContext(Dispatchers.IO) { api.runScheduledJobNow(safeJobId, adminToken = token()) }
        refreshScheduledJobs()
        refreshScheduledJobDetail(safeJobId)
        _uiState.update {
          it.copy(
            statusMessage = buildString {
              if (result.ok && result.queued) {
                append("Run queued")
              } else {
                append("Run not queued")
              }
              if (result.reason.isNotBlank()) append(" • ${result.reason}")
              if (result.runId.isNotBlank()) append(" • request ${result.runId}")
            },
          )
        }
      } finally {
        _uiState.update { it.copy(scheduledJobActionInFlight = false, scheduledJobActionLabel = "") }
      }
    }
  }

  fun refreshRuns() {
    launchAdmin("refresh runs") {
      val currentSelectedKey = _uiState.value.selectedRunKey
      val runs = withContext(Dispatchers.IO) { api.systemRuns(token()) }
      val summaries = withContext(Dispatchers.IO) { api.runsIndex(token()) }
      val selectedKey = chooseSelectedRunKey(
        currentKey = currentSelectedKey,
        summaries = summaries,
        activeRuns = runs,
      )
      _uiState.update {
        it.copy(
          runs = runs,
          runSummaries = summaries,
          runsMessage = "active=${runs.size}",
          selectedRunKey = selectedKey,
          selectedRunStdoutLoading = selectedKey.isNotBlank(),
          selectedRunStdoutText = if (selectedKey == currentSelectedKey) it.selectedRunStdoutText else "",
          selectedRunEpiAv = if (selectedKey == currentSelectedKey) it.selectedRunEpiAv else null,
          selectedRunEpiAvLoading = selectedKey.isNotBlank(),
          selectedRunEpiAvError = if (selectedKey == currentSelectedKey) it.selectedRunEpiAvError else "",
        )
      }
      if (selectedKey.isNotBlank()) {
        refreshSelectedRunStdout(selectedKey)
        refreshSelectedRunEpiAv(selectedKey)
      } else {
        _uiState.update {
          it.copy(
            selectedRunStdoutText = "",
            selectedRunStdoutLoading = false,
            selectedRunEpiAv = null,
            selectedRunEpiAvLoading = false,
            selectedRunEpiAvError = "",
          )
        }
      }
    }
  }

  fun selectRun(runKey: String) {
    val safeRunKey = runKey.trim()
    _uiState.update {
      it.copy(
        selectedRunKey = safeRunKey,
        selectedRunStdoutText = "",
        selectedRunStdoutLoading = safeRunKey.isNotBlank(),
        selectedRunEpiAv = null,
        selectedRunEpiAvLoading = safeRunKey.isNotBlank(),
        selectedRunEpiAvError = "",
      )
    }
    prefs.edit().putString("selected_run_key", safeRunKey).apply()
    if (safeRunKey.isNotBlank()) {
      refreshSelectedRunStdout(safeRunKey)
      refreshSelectedRunEpiAv(safeRunKey)
    }
  }

  fun refreshSelectedRunStdout(runKey: String = _uiState.value.selectedRunKey) {
    val safeRunKey = runKey.trim()
    if (safeRunKey.isBlank()) {
      _uiState.update { it.copy(selectedRunStdoutText = "", selectedRunStdoutLoading = false) }
      return
    }
    launchAdmin("refresh selected run stdout") {
      _uiState.update { it.copy(selectedRunStdoutLoading = true) }
      val text = withContext(Dispatchers.IO) { api.runFileTail(token(), safeRunKey, which = "stdout") }
      _uiState.update {
        it.copy(
          selectedRunKey = safeRunKey,
          selectedRunStdoutText = text,
          selectedRunStdoutLoading = false,
        )
      }
    }
  }

  fun refreshSelectedRunEpiAv(runKey: String = _uiState.value.selectedRunKey) {
    val safeRunKey = runKey.trim()
    if (safeRunKey.isBlank()) {
      _uiState.update {
        it.copy(
          selectedRunEpiAv = null,
          selectedRunEpiAvLoading = false,
          selectedRunEpiAvError = "",
        )
      }
      return
    }
    launchAdmin("refresh selected run epiav") {
      _uiState.update {
        it.copy(
          selectedRunEpiAvLoading = true,
          selectedRunEpiAvError = "",
        )
      }
      runCatching {
        withContext(Dispatchers.IO) { api.runEpiAv(token(), safeRunKey) }
      }.onSuccess { payload ->
        _uiState.update {
          it.copy(
            selectedRunKey = safeRunKey,
            selectedRunEpiAv = payload,
            selectedRunEpiAvLoading = false,
            selectedRunEpiAvError = "",
          )
        }
      }.onFailure { error ->
        _uiState.update {
          it.copy(
            selectedRunEpiAvLoading = false,
            selectedRunEpiAvError = error.message ?: "Unable to load EPI/AV",
          )
        }
        throw error
      }
    }
  }

  fun createArchive() {
    val debugOnly = _uiState.value.archiveDebugOnly
    launchAdmin("create archive") {
      val payload = withContext(Dispatchers.IO) { api.createArchive(token(), debugOnly) }
      val file = saveArchiveDownload(payload)
      _uiState.update {
        it.copy(
          downloads = listOf(file) + it.downloads.filterNot { item -> item.absolutePath == file.absolutePath }.take(7),
          archivesMessage = "Saved ${file.filename} to ${file.absolutePath}",
          pendingDownloadId = file.id,
        )
      }
      refreshArchives()
    }
  }

  fun downloadArchive(name: String) {
    if (name.isBlank()) return
    launchAdmin("download archive") {
      val payload = withContext(Dispatchers.IO) { api.downloadArchive(token(), name) }
      val file = saveArchiveDownload(payload)
      _uiState.update {
        it.copy(
          downloads = listOf(file) + it.downloads.filterNot { item -> item.absolutePath == file.absolutePath }.take(7),
          archivesMessage = "Saved ${file.filename} to ${file.absolutePath}",
          pendingDownloadId = file.id,
        )
      }
    }
  }

  fun applyPatchUpload(filename: String, bytes: ByteArray) {
    if (bytes.isEmpty()) return
    val requestId = UUID.randomUUID().toString()
    _uiState.update {
      it.copy(
        patchApplying = true,
        patchApplyMessage = "Applying ${filename.ifBlank { "patch" }}...",
        patchApplyOutcome = "",
        patchApplyRequestId = requestId,
      )
    }
    launchAdmin("apply patch upload") {
      runCatching {
        withContext(Dispatchers.IO) {
          api.patchApplyUpload(
            adminToken = token(),
            filename = filename.ifBlank { "mobile_patch.diff" },
            bytes = bytes,
            allowDirty = false,
            forceOverwrite = false,
            verifySyntax = true,
            allowBinary = false,
          )
        }
      }.onSuccess { summary ->
        _uiState.update {
          it.copy(
            patchApplying = false,
            patchApplyMessage = summary.ifBlank { "Patch applied" },
            statusMessage = "Patch applied",
            patchApplyOutcome = "success",
          )
        }
      }.onFailure { error ->
        val msg = error.message ?: "Patch apply failed"
        AppLogger.w("admin failure apply patch upload: $msg")
        _uiState.update {
          it.copy(
            patchApplying = false,
            patchApplyMessage = msg,
            statusMessage = msg,
            patchApplyOutcome = "failed",
          )
        }
      }
    }
  }

  fun applySharedPatch(filename: String, bytes: ByteArray, text: String) {
    val safeName = filename.ifBlank { "shared_patch.diff" }
    val safeText = text.trim()
    if (bytes.isNotEmpty()) {
      applyPatchUpload(safeName, bytes)
      return
    }
    if (safeText.isBlank()) {
      val requestId = UUID.randomUUID().toString()
      _uiState.update {
        it.copy(
          patchApplyMessage = "Shared patch was empty.",
          statusMessage = "Shared patch was empty.",
          patchApplyOutcome = "failed",
          patchApplyRequestId = requestId,
        )
      }
      return
    }
    val requestId = UUID.randomUUID().toString()
    _uiState.update {
      it.copy(
        patchApplying = true,
        patchApplyMessage = "Applying ${safeName}...",
        patchApplyOutcome = "",
        patchApplyRequestId = requestId,
      )
    }
    launchAdmin("apply shared patch") {
      runCatching {
        withContext(Dispatchers.IO) {
          api.patchApplyText(
            adminToken = token(),
            text = safeText,
            allowDirty = false,
            forceOverwrite = false,
            verifySyntax = true,
            permitDeletions = false,
            allowBinary = false,
          )
        }
      }.onSuccess { summary ->
        _uiState.update {
          it.copy(
            patchApplying = false,
            patchApplyMessage = summary.ifBlank { "Patch applied" },
            statusMessage = "Patch applied",
            patchApplyOutcome = "success",
          )
        }
      }.onFailure { error ->
        val msg = error.message ?: "Patch apply failed"
        AppLogger.w("admin failure apply shared patch: $msg")
        _uiState.update {
          it.copy(
            patchApplying = false,
            patchApplyMessage = msg,
            statusMessage = msg,
            patchApplyOutcome = "failed",
          )
        }
      }
    }
  }

  fun refreshManifests() {
    launchAdmin("refresh manifests") {
      val jobs = withContext(Dispatchers.IO) { api.manifests(token()) }
      _uiState.update { it.copy(manifests = jobs, manifestsMessage = "") }
    }
  }

  fun refreshProcesses() {
    val includeAll = _uiState.value.processesIncludeAll
    launchAdmin("refresh processes") {
      val rows = withContext(Dispatchers.IO) { api.systemProcesses(token(), includeAll) }
      _uiState.update {
        it.copy(
          processes = rows,
          processesMessage = if (includeAll) "showing ${rows.size} host processes" else "showing ${rows.size} tracked processes",
        )
      }
    }
  }

  fun signalProcess(pid: Int, hard: Boolean) {
    if (pid <= 0) return
    launchAdmin(if (hard) "kill process" else "terminate process") {
      val note = withContext(Dispatchers.IO) { api.signalProcess(token(), pid, hard) }
      _uiState.update { it.copy(processesMessage = note, runsMessage = note) }
      refreshProcesses()
      refreshRuns()
    }
  }

  fun refreshBuilder() {
    launchAdmin("refresh builder") {
      val config = withContext(Dispatchers.IO) { api.builderConfig(token()) }
      val registries = withContext(Dispatchers.IO) { api.builderRegistries(token()) }
      val templates = withContext(Dispatchers.IO) { api.builderTemplates(token()) }
      val payload = withContext(Dispatchers.IO) { api.builderHarnesses(token()) }
      applyBuilderPayload(
        payload = payload,
        config = config,
        registries = registries,
        templates = templates,
      )
      val activeJobId = payload.activeJobId.ifBlank { _uiState.value.builderActiveJobId }
      if (activeJobId.isNotBlank()) {
        refreshBuilderJob(activeJobId)
      }
    }
  }

  fun loadSelectedBuilderRegistry() {
    val path = _uiState.value.builderSelectedRegistryPath.trim()
    if (path.isBlank()) return
    launchAdmin("load builder registry") {
      val result = withContext(Dispatchers.IO) { api.builderRegistryLoad(token(), path) }
      _uiState.update { state ->
        state.copy(
          builderSelectedRegistryPath = result.sourcePath.ifBlank { path },
          builderHarnessNameInput = result.harnessPayload?.testId ?: state.builderHarnessNameInput,
          builderHarnessDescriptionInput = result.harnessPayload?.description ?: state.builderHarnessDescriptionInput,
          builderHarnessQuestionInput = result.harnessPayload?.intentQuestion ?: state.builderHarnessQuestionInput,
          builderHarnessRunsRootInput = result.harnessPayload?.runsRoot?.ifBlank { state.builderHarnessRunsRootInput } ?: state.builderHarnessRunsRootInput,
          builderHarnessSeedsInput = result.harnessPayload?.seeds?.joinToString(",") ?: state.builderHarnessSeedsInput,
          builderHarnessCaseValuesInput = result.harnessPayload?.caseValues?.joinToString("\n") ?: state.builderHarnessCaseValuesInput,
          builderHarnessDefaultKnobsInput = result.harnessPayload?.defaultKnobs?.joinToString("\n") ?: state.builderHarnessDefaultKnobsInput,
          builderHarnessArtifactsInput = result.harnessPayload?.expectedArtifacts?.joinToString("\n") ?: state.builderHarnessArtifactsInput,
          builderHarnessGenerationsInput = result.harnessPayload?.generations?.toString().orEmpty(),
          builderHarnessStepsInput = result.harnessPayload?.steps?.toString().orEmpty(),
          builderHarnessPopInput = result.harnessPayload?.pop?.toString().orEmpty(),
          builderHarnessGridInput = result.harnessPayload?.gridSize?.toString().orEmpty(),
          builderHarnessStrideInput = result.harnessPayload?.stride?.toString().orEmpty(),
          builderHarnessCkptEveryInput = result.harnessPayload?.checkpointPeriodGen?.toString().orEmpty(),
          builderHarnessCkptKeepInput = result.harnessPayload?.checkpointRotate?.toString().orEmpty(),
          builderHarnessPerf = result.harnessPayload?.perf ?: state.builderHarnessPerf,
          builderHarnessProfiler = result.harnessPayload?.enableProfiler ?: state.builderHarnessProfiler,
          builderSelectedCmd = result.runCommandManaged.ifBlank { state.builderSelectedCmd },
          builderSelectedName = result.harnessPayload?.testId?.ifBlank { state.builderSelectedName } ?: state.builderSelectedName,
          builderStatusMessage = if (result.harnessExists) "Loaded ${result.sourcePath}" else "Loaded ${result.sourcePath}. Generate harness to create script.",
        )
      }
      prefs.edit().putString("builder_selected_registry_path", result.sourcePath.ifBlank { path }).apply()
    }
  }

  fun saveBuilderTemplate() {
    val state = _uiState.value
    val name = state.builderTemplateDraftName.trim()
    if (name.isBlank()) return
    launchAdmin("save builder template") {
      val saved = withContext(Dispatchers.IO) {
        api.builderSaveTemplate(
          adminToken = token(),
          name = name,
          harness = currentBuilderHarnessPayload(),
          manifestName = state.builderManifestNameInput.trim(),
          manifestOutputRoot = state.builderManifestOutputRootInput.trim(),
          manifestStrict = state.builderManifestStrict,
          manifestGitRefOverride = state.builderManifestGitRefInput.trim(),
          manifestManual = state.builderManifestManualPathsInput,
        )
      }
      _uiState.update {
        it.copy(
          builderSelectedTemplateName = saved.name,
          builderStatusMessage = "Saved template ${saved.name}",
        )
      }
      refreshBuilder()
    }
  }

  fun loadSelectedBuilderTemplate() {
    val name = _uiState.value.builderSelectedTemplateName.trim()
    if (name.isBlank()) return
    launchAdmin("load builder template") {
      val template = withContext(Dispatchers.IO) { api.builderLoadTemplate(token(), name) }
      applyBuilderTemplate(template)
      _uiState.update { it.copy(builderStatusMessage = "Loaded template ${template.templateName}") }
    }
  }

  fun generateBuilderHarness() {
    val harness = currentBuilderHarnessPayload() ?: return
    launchAdmin("generate builder harness") {
      val result = withContext(Dispatchers.IO) { api.builderGenerateHarness(token(), harness) }
      applyBuilderHarnessResult(result)
      refreshBuilder()
    }
  }

  fun generateBuilderManifest() {
    val state = _uiState.value
    val registryPaths = buildManifestRegistryPaths(state)
    if (registryPaths.isEmpty()) return
    launchAdmin("generate builder manifest") {
      val result = withContext(Dispatchers.IO) {
        api.builderGenerateManifest(
          adminToken = token(),
          testName = state.builderManifestNameInput.trim().ifBlank { state.builderHarnessNameInput.trim().ifBlank { "manifest" } },
          registryPaths = registryPaths,
          outputRoot = state.builderManifestOutputRootInput.trim(),
          strict = state.builderManifestStrict,
          gitRefOverride = state.builderManifestGitRefInput.trim(),
        )
      }
      applyBuilderManifestResult(result)
    }
  }

  fun selectBuilderHarness(name: String, cmd: String) {
    _uiState.update { it.copy(builderSelectedName = name, builderSelectedCmd = cmd) }
    prefs.edit()
      .putString("builder_selected_name", name)
      .putString("builder_selected_cmd", cmd)
      .apply()
  }

  fun launchSelectedBuilderHarness() {
    val s = _uiState.value
    val cmd = s.builderSelectedCmd.trim()
    if (cmd.isBlank()) return
    launchBuilderCommand(cmd)
  }

  fun launchGeneratedManifest() {
    val cmd = _uiState.value.builderManifestStartCmd.trim()
    if (cmd.isBlank()) return
    launchBuilderCommand(cmd)
  }

  private fun launchBuilderCommand(cmd: String) {
    launchAdmin("launch builder command") {
      val result = withContext(Dispatchers.IO) { api.builderLaunch(token(), cmd) }
      _uiState.update {
        it.copy(
          builderStatusMessage = when {
            result.error.isNotBlank() -> result.error
            result.jobId.isNotBlank() -> "launch ${result.status.ifBlank { result.lifecycle }}: ${result.jobId}"
            else -> result.status.ifBlank { "launch requested" }
          },
          builderActiveJobId = result.jobId.ifBlank { it.builderActiveJobId },
        )
      }
      val nextJobId = result.jobId.ifBlank { _uiState.value.builderActiveJobId }
      if (nextJobId.isNotBlank()) {
        pollBuilderJob(nextJobId)
      } else {
        refreshBuilder()
      }
    }
  }

  fun openBuilderJob(jobId: String) {
    if (jobId.isBlank()) return
    _uiState.update { it.copy(builderActiveJobId = jobId) }
    refreshBuilderJob(jobId)
  }

  fun refreshBuilderJob(jobId: String = _uiState.value.builderActiveJobId) {
    val safeJobId = jobId.trim()
    if (safeJobId.isBlank()) return
    launchAdmin("refresh builder job") {
      val status = withContext(Dispatchers.IO) { api.builderJobStatus(token(), safeJobId) }
      val log = withContext(Dispatchers.IO) { api.builderJobLog(token(), safeJobId) }
      _uiState.update {
        it.copy(
          builderActiveJobId = safeJobId,
          builderJobStatus = status,
          builderLog = log.ifBlank { status.logTail },
          builderStatusMessage = when {
            status.failureDetail.isNotBlank() -> status.failureDetail
            status.failureCode.isNotBlank() -> status.failureCode
            else -> status.lifecycle.ifBlank { status.state }
          },
        )
      }
    }
  }

  fun controlManifest(jobId: String, action: String, mode: String = "graceful") {
    if (jobId.isBlank()) return
    launchAdmin("manifest $action") {
      val jobs = withContext(Dispatchers.IO) { api.manifestControl(token(), jobId, action, mode) }
      _uiState.update { it.copy(manifests = jobs, manifestsMessage = "$action requested for $jobId") }
    }
  }

  fun refreshFsList() {
    val s = _uiState.value
    launchAdmin("refresh fs list") {
      val (dir, entries) = withContext(Dispatchers.IO) { api.fsList(token(), s.fsPath.trim(), includeHidden = true) }
      _uiState.update { it.copy(fsPath = dir, fsEntries = entries, fsIncludeHidden = true) }
    }
  }

  fun fsUp() {
    val current = _uiState.value.fsPath.trim().trimEnd('/')
    val next = current.substringBeforeLast('/', missingDelimiterValue = "")
    _uiState.update { it.copy(fsPath = next) }
    refreshFsList()
  }

  fun openFsPath(path: String) {
    _uiState.update { it.copy(fsPath = path) }
    refreshFsList()
  }

  fun toggleFsSelection(path: String, selected: Boolean) {
    _uiState.update { state ->
      val next = state.fsSelections.toMutableSet()
      if (selected) next += path else next -= path
      state.copy(fsSelections = next)
    }
  }

  fun clearFsSelections() {
    _uiState.update { it.copy(fsSelections = emptySet()) }
  }

  fun viewFsFile(path: String) {
    if (path.isBlank()) return
    _uiState.update {
      it.copy(
        fsPreviewPath = path,
        fsPreviewText = "",
        fsPreviewTruncated = false,
        fsPreviewLoading = true,
        fsPreviewError = "",
      )
    }
    viewModelScope.launch {
      try {
        val result = withContext(Dispatchers.IO) { api.fsRead(token(), path) }
        _uiState.update {
          it.copy(
            fsPreviewPath = result.path,
            fsPreviewText = result.text,
            fsPreviewTruncated = result.truncated,
            fsPreviewLoading = false,
            fsPreviewError = "",
            replaceNote = if (result.path.isNotBlank()) "Selected: ${result.path}" else "",
          )
        }
      } catch (t: Throwable) {
        val msg = t.message ?: "view fs file"
        AppLogger.w("admin failure view fs file: $msg")
        _uiState.update {
          it.copy(
            fsPreviewLoading = false,
            fsPreviewError = msg,
            statusMessage = msg,
          )
        }
      }
    }
  }

  fun downloadFsFile(path: String) {
    if (path.isBlank()) return
    launchAdmin("download fs file") {
      val payload = withContext(Dispatchers.IO) { api.fsDownload(token(), path) }
      val file = saveDownload(payload)
      _uiState.update {
        it.copy(
          downloads = listOf(file) + it.downloads.filterNot { item -> item.absolutePath == file.absolutePath }.take(7),
          replaceNote = "Saved ${file.filename}",
          pendingDownloadId = file.id,
        )
      }
    }
  }

  fun zipSelections() {
    val s = _uiState.value
    val selections = s.fsSelections.toList().sorted()
    if (selections.isEmpty()) return
    launchAdmin("zip selections") {
      val payload = withContext(Dispatchers.IO) {
        api.fsZip(
          adminToken = token(),
          paths = selections,
          name = defaultRepoSelectionZipName(),
          includeHidden = true,
        )
      }
      val file = saveDownload(payload)
      _uiState.update {
        it.copy(
          downloads = listOf(file) + it.downloads.filterNot { item -> item.absolutePath == file.absolutePath }.take(7),
          replaceNote = "Saved ${file.filename}",
          pendingDownloadId = file.id,
        )
      }
    }
  }

  fun pendingDownload(): DownloadedFile? {
    val state = _uiState.value
    val id = state.pendingDownloadId
    if (id.isBlank()) return null
    return state.downloads.firstOrNull { it.id == id }
  }

  fun onPendingDownloadHandled() {
    _uiState.update { it.copy(pendingDownloadId = "") }
  }

  fun replacePreviewedFile(filename: String, bytes: ByteArray) {
    val path = _uiState.value.fsPreviewPath.trim()
    if (path.isBlank()) return
    launchAdmin("replace previewed file") {
      val note = withContext(Dispatchers.IO) { api.fsReplace(token(), path, filename, bytes) }
      _uiState.update { it.copy(replaceNote = note) }
      viewFsFile(path)
      refreshFsList()
    }
  }

  fun restartServer() {
    launchAdmin("restart server") {
      val result = withContext(Dispatchers.IO) { api.restartServer(token()) }
      _uiState.update {
        it.copy(
          statusMessage = buildRestartStatusMessage(result),
          healthStatus = result.healthStatus,
          restartBlockers = result.blockers,
        )
      }
    }
  }

  private fun applyConfig(cfg: AdminConfig) {
    _uiState.update {
      it.copy(
        repoRoot = cfg.repoRoot.ifBlank { "?" },
        archivesDir = cfg.archivesDir,
      )
    }
  }

  private fun applyCommandJob(job: AdminJob) {
    applyCommandJob(job, source = "command")
  }

  private fun applyCommandJob(job: AdminJob, source: String) {
    _uiState.update {
      it.copy(
        cmdJobId = job.jobId,
        terminalOutput = formatConsoleOutput(source, renderJob(job)),
        terminalOutputTitle = if (job.running) "$source (live)" else source,
        cmdRunning = job.running,
      )
    }
  }

  private fun applyPlaneJob(job: AdminJob) {
    _uiState.update {
      it.copy(
        planeJobId = job.jobId,
        planeOutput = formatConsoleOutput("the plane", renderJob(job)),
        planeOutputTitle = if (job.running) "the plane (live)" else "the plane",
        planeRunning = job.running,
      )
    }
  }

  private fun applyMobileDeployJob(job: AdminJob) {
    _uiState.update {
      it.copy(
        appUpdateJobId = job.jobId,
        terminalOutput = formatConsoleOutput("deploy", renderJob(job)),
        terminalOutputTitle = if (job.running) "deploy (live)" else "deploy",
      )
    }
  }

  private fun applyGitStatus(status: GitStatus) {
    _uiState.update {
      it.copy(
        gitBranch = status.branch.ifBlank { "?" },
        gitHead = status.head.ifBlank { "?" },
        gitDescribe = status.describe.ifBlank { "?" },
        gitStatusPorcelain = status.statusPorcelain,
        gitJournalStatus = renderAdminGitJournalStatus(status),
        gitJournalPreviewSubject = status.commitJournalPreviewSubject,
        gitJournalEntries = status.commitJournalEntries,
        gitBaseline = if (it.gitBaseline.isBlank()) status.baselineSuggested else it.gitBaseline,
      )
    }
  }

  private fun applyGitJob(job: AdminJob) {
    _uiState.update {
      it.copy(
        gitJobId = job.jobId,
        terminalOutput = formatConsoleOutput("git", renderJob(job)),
        terminalOutputTitle = if (job.running) "git (live)" else "git",
        gitRunning = job.running,
      )
    }
  }

  private fun pollCommandJob(jobId: String, source: String = "command") {
    if (jobId.isBlank()) return
    commandPollJob?.cancel()
    commandPollJob = viewModelScope.launch {
      while (true) {
        delay(650L)
        val job = runCatching {
          withContext(Dispatchers.IO) { api.commandStatus(token(), jobId) }
        }.getOrElse {
          appendConsoleNote(source, "ERROR: ${it.message ?: "command status failed"}")
          return@launch
        }
        applyCommandJob(job, source = source)
        if (!job.running) return@launch
      }
    }
  }

  private fun pollPlaneJob(jobId: String) {
    if (jobId.isBlank()) return
    planePollJob?.cancel()
    planePollJob = viewModelScope.launch {
      while (true) {
        delay(650L)
        val job = runCatching {
          withContext(Dispatchers.IO) { api.commandStatus(token(), jobId) }
        }.getOrElse {
          appendPlaneConsoleNote("ERROR: ${it.message ?: "plane status failed"}")
          return@launch
        }
        applyPlaneJob(job)
        if (!job.running) {
          refreshPlaneHistory()
          return@launch
        }
      }
    }
  }

  private fun resolveToolDefaultCwd(state: AdminUiState): String {
    val current = state.cmdCwd.trim()
    if (current.isNotBlank()) return current
    val repo = state.repoRoot.trim()
    return if (repo.isNotBlank() && repo != "?") repo else ""
  }

  private fun runCommandJob(
    source: String,
    cmd: String,
    cwd: String,
    timeoutSec: String,
  ) {
    if (cmd.isBlank()) return
    launchAdmin("run $source") {
      clearConsoleFor(source)
      val timeout = timeoutSec.toDoubleOrNull()?.coerceIn(1.0, 21_600.0) ?: DEFAULT_TERMINAL_TIMEOUT_SEC.toDouble()
      val job = withContext(Dispatchers.IO) { api.commandRun(token(), cmd, cwd, timeout) }
      applyCommandJob(job, source = source)
      pollCommandJob(job.jobId, source = source)
    }
  }

  private fun pollMobileDeployJob(jobId: String, channel: String) {
    if (jobId.isBlank()) return
    mobileDeployPollJob?.cancel()
    mobileDeployPollJob = viewModelScope.launch {
      while (true) {
        delay(650L)
        val job = runCatching {
          withContext(Dispatchers.IO) { api.commandStatus(token(), jobId) }
        }.getOrElse {
          appendConsoleNote("deploy", "ERROR: ${it.message ?: "deploy status failed"}")
          return@launch
        }
        applyMobileDeployJob(job)
        if (job.running) continue
        _uiState.update { it.copy(appUpdateBuilding = false, appUpdateJobId = "") }
        if (job.returnCode == 0 && !job.killed) {
          runCatching { deployBuiltApp(channel) }
            .onFailure { error ->
              _uiState.update {
                it.copy(
                  appUpdateMessage = error.message ?: "Build finished but install prep failed",
                  statusMessage = error.message ?: "Build finished but install prep failed",
                )
              }
            }
        } else {
          val failure = job.error.ifBlank { "Build failed (rc=${job.returnCode ?: -1})" }
          _uiState.update {
            it.copy(
              appUpdateMessage = failure,
              statusMessage = failure,
            )
          }
        }
        return@launch
      }
    }
  }

  private suspend fun deployBuiltApp(channel: String) {
    val installedSha = withContext(Dispatchers.IO) { currentInstalledApkSha256(getApplication()) }
    val manifest = withContext(Dispatchers.IO) { api.mobileAppUpdateManifest(channel, adminToken = token()) }
    val currentPackage = getApplication<Application>().packageName
    val packageMatches = manifest.packageName == currentPackage
    _uiState.update {
      it.copy(
        appUpdateChannel = channel,
        appUpdateRemoteSha256 = manifest.apkSha256,
        appUpdateRemoteBuiltAt = manifest.builtAt,
        appUpdateRemoteApkName = manifest.apkName,
        appUpdateMessage = when {
          !packageMatches -> "Server APK package mismatch: ${manifest.packageName}"
          isAppUpdateAvailable(installedSha, manifest.apkSha256) -> "Fresh build ready. Downloading APK..."
          else -> "Fresh build finished. Downloading rebuilt APK..."
        },
      )
    }
    if (packageMatches) {
      installLatestAppUpdate()
    }
  }

  private fun pollGitJob(jobId: String) {
    if (jobId.isBlank()) return
    gitPollJob?.cancel()
    gitPollJob = viewModelScope.launch {
      while (true) {
        delay(650L)
        val job = runCatching {
          withContext(Dispatchers.IO) { api.commandStatus(token(), jobId) }
        }.getOrElse {
          appendConsoleNote("git", "ERROR: ${it.message ?: "git status failed"}")
          return@launch
        }
        applyGitJob(job)
        if (!job.running) {
          refreshGit()
          refreshArchives()
          return@launch
        }
      }
    }
  }

  private fun pollBuilderJob(jobId: String) {
    if (jobId.isBlank()) return
    viewModelScope.launch {
      while (true) {
        delay(1000L)
        val status = runCatching {
          withContext(Dispatchers.IO) { api.builderJobStatus(token(), jobId) }
        }.getOrElse { err ->
          _uiState.update { state ->
            state.copy(builderStatusMessage = state.builderStatusMessage.ifBlank { "builder status failed: ${err.message ?: "unknown error"}" })
          }
          return@launch
        }
        val log = runCatching {
          withContext(Dispatchers.IO) { api.builderJobLog(token(), jobId) }
        }.getOrDefault(status.logTail)
        _uiState.update {
          it.copy(
            builderActiveJobId = jobId,
            builderJobStatus = status,
            builderLog = log,
            builderStatusMessage = when {
              status.failureDetail.isNotBlank() -> status.failureDetail
              status.failureCode.isNotBlank() -> status.failureCode
              else -> status.lifecycle.ifBlank { status.state }
            },
          )
        }
        if (status.terminal) {
          refreshBuilder()
          refreshRuns()
          return@launch
        }
      }
    }
  }

  private fun clearConsoleFor(title: String) {
    _uiState.update { it.copy(terminalOutput = "", terminalOutputTitle = title) }
  }

  private fun appendPlaneConsoleNote(line: String) {
    _uiState.update {
      val next = buildString {
        if (it.planeOutput.isNotBlank()) append(it.planeOutput.trimEnd()).append('\n').append('\n')
        append(line)
      }
      it.copy(
        planeOutput = boundAdminConsoleOutput(next),
        planeOutputTitle = "the plane",
      )
    }
  }

  private fun appendConsoleNote(source: String, line: String) {
    _uiState.update {
      val next = buildString {
        if (it.terminalOutput.isNotBlank()) append(it.terminalOutput.trimEnd()).append('\n').append('\n')
        append('[').append(source).append("] ").append(line)
      }
      it.copy(
        terminalOutput = boundAdminConsoleOutput(next),
        terminalOutputTitle = source,
      )
    }
  }

  private fun normalizeBaseline(raw: String): String {
    val trimmed = raw.trim()
    if (trimmed.matches(Regex("\\d+"))) return "baseline-$trimmed"
    return trimmed
  }

  private fun applyBuilderPayload(
    payload: BuilderHarnessesPayload,
    config: BuilderConfig,
    registries: List<BuilderRegistry>,
    templates: List<BuilderTemplate>,
  ) {
    val current = _uiState.value
    val selectedHarness = payload.harnesses.firstOrNull { it.runCommandManaged == current.builderSelectedCmd }
      ?: payload.harnesses.firstOrNull()
    _uiState.update {
      it.copy(
        builderConfig = config,
        builderRegistries = registries,
        builderTemplates = templates,
        builderHarnesses = payload.harnesses,
        builderRecentJobs = payload.recentJobs,
        builderActiveJobId = payload.activeJobId.ifBlank { it.builderActiveJobId },
        builderSelectedCmd = current.builderSelectedCmd.takeIf { cmd -> payload.harnesses.any { harness -> harness.runCommandManaged == cmd } }
          ?: selectedHarness?.runCommandManaged.orEmpty(),
        builderSelectedName = current.builderSelectedName.takeIf { name -> name.isNotBlank() }
          ?: selectedHarness?.displayId ?: selectedHarness?.name.orEmpty(),
        builderSelectedRegistryPath = current.builderSelectedRegistryPath.takeIf { selected -> registries.any { reg -> reg.path == selected } }
          ?: registries.firstOrNull()?.path.orEmpty(),
        builderSelectedTemplateName = current.builderSelectedTemplateName.takeIf { selected -> templates.any { tpl -> tpl.name == selected } }
          ?: templates.firstOrNull()?.name.orEmpty(),
        builderHarnessRunsRootInput = current.builderHarnessRunsRootInput.ifBlank { config.runsRootDefault },
        builderManifestOutputRootInput = current.builderManifestOutputRootInput.ifBlank { config.manifestOutDir },
      )
    }
  }

  private fun applyBuilderTemplate(template: BuilderTemplatePayload) {
    _uiState.update { state ->
      state.copy(
        builderSelectedTemplateName = template.templateName,
        builderTemplateDraftName = template.templateName,
        builderHarnessNameInput = template.harness?.testId ?: state.builderHarnessNameInput,
        builderHarnessDescriptionInput = template.harness?.description ?: state.builderHarnessDescriptionInput,
        builderHarnessQuestionInput = template.harness?.intentQuestion ?: state.builderHarnessQuestionInput,
        builderHarnessRunsRootInput = template.harness?.runsRoot?.ifBlank { state.builderHarnessRunsRootInput } ?: state.builderHarnessRunsRootInput,
        builderHarnessSeedsInput = template.harness?.seeds?.joinToString(",") ?: state.builderHarnessSeedsInput,
        builderHarnessCaseValuesInput = template.harness?.caseValues?.joinToString("\n") ?: state.builderHarnessCaseValuesInput,
        builderHarnessDefaultKnobsInput = template.harness?.defaultKnobs?.joinToString("\n") ?: state.builderHarnessDefaultKnobsInput,
        builderHarnessArtifactsInput = template.harness?.expectedArtifacts?.joinToString("\n") ?: state.builderHarnessArtifactsInput,
        builderHarnessGenerationsInput = template.harness?.generations?.toString().orEmpty(),
        builderHarnessStepsInput = template.harness?.steps?.toString().orEmpty(),
        builderHarnessPopInput = template.harness?.pop?.toString().orEmpty(),
        builderHarnessGridInput = template.harness?.gridSize?.toString().orEmpty(),
        builderHarnessStrideInput = template.harness?.stride?.toString().orEmpty(),
        builderHarnessCkptEveryInput = template.harness?.checkpointPeriodGen?.toString().orEmpty(),
        builderHarnessCkptKeepInput = template.harness?.checkpointRotate?.toString().orEmpty(),
        builderHarnessPerf = template.harness?.perf ?: state.builderHarnessPerf,
        builderHarnessProfiler = template.harness?.enableProfiler ?: state.builderHarnessProfiler,
        builderManifestNameInput = template.manifestName.ifBlank { state.builderManifestNameInput },
        builderManifestOutputRootInput = template.manifestOutputRoot.ifBlank { state.builderManifestOutputRootInput },
        builderManifestStrict = template.manifestStrict,
        builderManifestGitRefInput = template.manifestGitRefOverride,
        builderManifestManualPathsInput = template.manifestManual,
      )
    }
  }

  private fun applyBuilderHarnessResult(result: BuilderGenerateHarnessResult) {
    _uiState.update {
      it.copy(
        builderSelectedName = result.name.ifBlank { it.builderSelectedName },
        builderSelectedCmd = result.runCommandManaged.ifBlank { it.builderSelectedCmd },
        builderStatusMessage = when {
          result.issues.isNotEmpty() -> result.issues.joinToString("\n")
          result.ok -> "Generated ${result.name}"
          else -> "Harness generation returned issues"
        },
        builderHarnessResult = buildString {
          append(if (result.ok) "OK" else "ISSUES")
          if (result.name.isNotBlank()) append(" · ").append(result.name)
          if (result.registryPath.isNotBlank()) append('\n').append("registry: ").append(result.registryPath)
          if (result.harnessPath.isNotBlank()) append('\n').append("harness: ").append(result.harnessPath)
          if (result.runCommandManaged.isNotBlank()) append('\n').append("start: ").append(result.runCommandManaged)
          if (result.issues.isNotEmpty()) append('\n').append(result.issues.joinToString("\n"))
        }.trim(),
        builderHarnessRegistryPreview = result.registryPreview,
        builderHarnessScriptPreview = result.harnessPreview,
      )
    }
  }

  private fun applyBuilderManifestResult(result: BuilderGenerateManifestResult) {
    _uiState.update {
      it.copy(
        builderStatusMessage = when {
          result.issues.isNotEmpty() -> result.issues.joinToString("\n")
          result.ok -> "Generated manifest ${result.manifestPath}"
          else -> "Manifest generation returned issues"
        },
        builderManifestResult = buildString {
          append(if (result.ok) "OK" else "ISSUES")
          append(" · jobs=").append(result.jobCount)
          if (result.manifestPath.isNotBlank()) append('\n').append("manifest: ").append(result.manifestPath)
          if (result.gitRef.isNotBlank()) append('\n').append("git_ref: ").append(result.gitRef)
          if (result.recommendedStartCmd.isNotBlank()) append('\n').append("start: ").append(result.recommendedStartCmd)
          if (result.issues.isNotEmpty()) append('\n').append(result.issues.joinToString("\n"))
        }.trim(),
        builderManifestPreview = result.manifestPreview,
        builderManifestStartCmd = result.recommendedStartCmd,
      )
    }
  }

  private fun currentBuilderHarnessPayload(): BuilderLoadedHarnessPayload? {
    val state = _uiState.value
    val testId = state.builderHarnessNameInput.trim()
    if (testId.isBlank()) {
      _uiState.update { it.copy(builderStatusMessage = "Harness name is required") }
      return null
    }
    val seeds = state.builderHarnessSeedsInput
      .split(Regex("[,\\s]+"))
      .mapNotNull { it.trim().toIntOrNull() }
      .distinct()
    if (seeds.isEmpty()) {
      _uiState.update { it.copy(builderStatusMessage = "At least one seed is required") }
      return null
    }
    val caseValues = state.builderHarnessCaseValuesInput
      .lines()
      .map { it.trim() }
      .filter { it.isNotBlank() }
      .ifEmpty { listOf("default") }
    return BuilderLoadedHarnessPayload(
      testId = testId,
      description = state.builderHarnessDescriptionInput.trim(),
      intentQuestion = state.builderHarnessQuestionInput.trim(),
      runsRoot = state.builderHarnessRunsRootInput.trim().ifBlank { state.builderConfig?.runsRootDefault.orEmpty() },
      seeds = seeds,
      caseValues = caseValues,
      defaultKnobs = state.builderHarnessDefaultKnobsInput.lines().map { it.trim() }.filter { it.isNotBlank() },
      expectedArtifacts = state.builderHarnessArtifactsInput.lines().map { it.trim() }.filter { it.isNotBlank() },
      perf = state.builderHarnessPerf,
      enableProfiler = state.builderHarnessProfiler,
      generations = state.builderHarnessGenerationsInput.trim().toIntOrNull(),
      steps = state.builderHarnessStepsInput.trim().toIntOrNull(),
      pop = state.builderHarnessPopInput.trim().toIntOrNull(),
      gridSize = state.builderHarnessGridInput.trim().toIntOrNull(),
      stride = state.builderHarnessStrideInput.trim().toIntOrNull(),
      checkpointPeriodGen = state.builderHarnessCkptEveryInput.trim().toIntOrNull(),
      checkpointRotate = state.builderHarnessCkptKeepInput.trim().toIntOrNull(),
    )
  }

  private fun buildManifestRegistryPaths(state: AdminUiState): List<String> {
    val paths = linkedSetOf<String>()
    if (state.builderManifestIncludeSelectedRegistry) {
      state.builderSelectedRegistryPath.trim().takeIf { it.isNotBlank() }?.let { paths += it }
    }
    state.builderManifestManualPathsInput.lines()
      .map { it.trim() }
      .filter { it.isNotBlank() }
      .forEach { paths += it }
    if (paths.isEmpty()) {
      _uiState.update { it.copy(builderStatusMessage = "Select or enter at least one registry path") }
    }
    return paths.toList()
  }

  private fun updateBuilder(transform: (AdminUiState) -> AdminUiState) {
    _uiState.update(transform)
  }

  private fun chooseSelectedRunKey(
    currentKey: String,
    summaries: List<RunSummary>,
    activeRuns: List<SystemRun>,
  ): String {
    if (currentKey.isNotBlank() && summaries.any { it.runKey == currentKey }) return currentKey
    val activeKeys = activeRuns.map { it.runKey }.toSet()
    return summaries.sortedWith(
      compareByDescending<RunSummary> { it.runKey in activeKeys }
        .thenByDescending { runStatusPriority(it.status) }
        .thenBy { it.displayId.ifBlank { it.runKey } },
    ).firstOrNull()?.runKey.orEmpty()
  }

  private fun runStatusPriority(status: String): Int {
    return when (status.trim().uppercase()) {
      "RUNNING" -> 4
      "STARTING" -> 3
      "HUNG" -> 2
      "STOPPING" -> 1
      else -> 0
    }
  }

  private fun renderJob(job: AdminJob): String {
    return buildString {
      append("kind: ").append(job.kind).append('\n')
      append("cmd:  ").append(job.cmd).append('\n')
      append("cwd:  ").append(job.cwdRel).append('\n')
      append("running=").append(job.running).append(" killed=").append(job.killed).append(" rc=").append(job.returnCode).append('\n')
      append("started: ").append(job.utcStarted).append(" finished: ").append(job.utcFinished).append('\n')
      append("duration_sec: ").append(job.durationSec).append('\n')
      if (job.error.isNotBlank()) append("error: ").append(job.error).append('\n')
      append('\n').append("--- stdout ---").append('\n')
      append(job.stdout)
      append('\n').append('\n').append("--- stderr ---").append('\n')
      append(job.stderr)
    }.trim()
  }

  private fun saveDownload(payload: DownloadPayload): DownloadedFile {
    val dir = localDownloadsDir.apply { mkdirs() }
    val filename = payload.filename.replace(Regex("[^A-Za-z0-9._-]"), "_")
    val file = File(dir, filename)
    copyDownloadPayloadTo(file, payload)
    return DownloadedFile(
      filename = filename,
      mimeType = payload.mimeType,
      absolutePath = file.absolutePath,
    )
  }

  private fun saveArchiveDownload(payload: DownloadPayload): DownloadedFile {
    val application = getApplication<Application>()
    val filename = payload.filename.replace(Regex("[^A-Za-z0-9._-]"), "_")
    val relativeLabel = "Download/plane/$filename"
    val resolver = application.contentResolver
    val collection = MediaStore.Downloads.EXTERNAL_CONTENT_URI
    val values = ContentValues().apply {
      put(MediaStore.MediaColumns.DISPLAY_NAME, filename)
      put(MediaStore.MediaColumns.MIME_TYPE, payload.mimeType)
      put(MediaStore.MediaColumns.RELATIVE_PATH, "Download/plane")
      put(MediaStore.MediaColumns.IS_PENDING, 1)
    }
    val uri = resolver.insert(collection, values)
      ?: throw IllegalStateException("Could not create download entry for $filename")
    try {
      resolver.openOutputStream(uri)?.use { out ->
        writeDownloadPayload(out, payload)
      } ?: throw IllegalStateException("Could not open download destination for $filename")
      values.clear()
      values.put(MediaStore.MediaColumns.IS_PENDING, 0)
      resolver.update(uri, values, null, null)
      return DownloadedFile(
        filename = filename,
        mimeType = payload.mimeType,
        absolutePath = relativeLabel,
      )
    } catch (error: Throwable) {
      runCatching { resolver.delete(uri, null, null) }
      throw error
    }
  }

  private fun copyDownloadPayloadTo(target: File, payload: DownloadPayload) {
    try {
      val tempPath = payload.tempFilePath.trim()
      if (tempPath.isNotBlank()) {
        File(tempPath).inputStream().use { input ->
          target.outputStream().use { output ->
            input.copyTo(output)
          }
        }
      } else {
        target.writeBytes(payload.bytes ?: ByteArray(0))
      }
    } finally {
      cleanupDownloadPayload(payload)
    }
  }

  private fun writeDownloadPayload(out: java.io.OutputStream, payload: DownloadPayload) {
    try {
      val tempPath = payload.tempFilePath.trim()
      if (tempPath.isNotBlank()) {
        File(tempPath).inputStream().use { input -> input.copyTo(out) }
      } else {
        out.write(payload.bytes ?: ByteArray(0))
      }
    } finally {
      cleanupDownloadPayload(payload)
    }
  }

  private fun cleanupDownloadPayload(payload: DownloadPayload) {
    val tempPath = payload.tempFilePath.trim()
    if (tempPath.isNotBlank()) {
      runCatching { File(tempPath).delete() }
    }
  }

  private fun resolveLocalDownloadsDir(application: Application): File {
    val external = application.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
    val baseDir = external ?: File(application.filesDir, "downloads")
    return File(baseDir, "plane_downloads").apply { mkdirs() }
  }

  private fun token(): String = _uiState.value.adminToken.trim()

  private fun currentInstalledApkSha256(application: Application): String {
    val source = application.applicationInfo.sourceDir.orEmpty()
    if (source.isBlank()) return ""
    val file = File(source)
    if (!file.exists() || !file.isFile) return ""
    return sha256Hex(file)
  }

  private fun sha256Hex(file: File): String {
    val digest = MessageDigest.getInstance("SHA-256")
    file.inputStream().use { input ->
      val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
      while (true) {
        val read = input.read(buffer)
        if (read <= 0) break
        digest.update(buffer, 0, read)
      }
    }
    return digest.digest().joinToString(separator = "") { byte -> "%02x".format(byte) }
  }

  private fun launchAdmin(label: String, block: suspend () -> Unit) {
    viewModelScope.launch {
      try {
        block()
      } catch (t: Throwable) {
        val msg = t.message ?: label
        AppLogger.w("admin failure $label: $msg")
        _uiState.update { it.copy(statusMessage = msg) }
      }
    }
  }

  private fun buildRestartStatusMessage(result: RestartServerResult): String {
    return renderRestartStatusMessage(result)
  }

  private fun restorePrefs() {
    val mode = AdminRefreshMode.fromLabel(prefs.getString("refresh_mode", AdminRefreshMode.NORMAL.label))
    _uiState.value = _uiState.value.copy(
      adminToken = prefs.getString("admin_token", "").orEmpty(),
      refreshMode = mode,
      gitUseJournalMessage = prefs.getBoolean("git_use_journal_message", true),
      fsIncludeHidden = true,
      builderSelectedName = prefs.getString("builder_selected_name", "").orEmpty(),
      builderSelectedCmd = prefs.getString("builder_selected_cmd", "").orEmpty(),
      builderSelectedRegistryPath = prefs.getString("builder_selected_registry_path", "").orEmpty(),
      selectedRunKey = prefs.getString("selected_run_key", "").orEmpty(),
    )
  }

  private fun defaultRepoSelectionZipName(): String {
    val stamp = java.time.LocalDateTime.now()
      .format(java.time.format.DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss"))
    return "repo_selection_$stamp.zip"
  }

  private fun startAutoRefresh() {
    autoRefreshJob?.cancel()
    autoRefreshJob = viewModelScope.launch {
      var tick = 0
      while (true) {
        delay(_uiState.value.refreshMode.intervalMs)
        tick += 1
        refreshHealth()
        refreshOperatorSnapshot()
        refreshPlaneLog()
        refreshScheduledJobs()
        if (tick % 3 == 0) refreshArchives()
      }
    }
  }

  override fun onCleared() {
    super.onCleared()
    autoRefreshJob?.cancel()
    planePollJob?.cancel()
  }
}

internal fun renderRestartStatusMessage(result: RestartServerResult): String {
  if (result.blocked) {
    val staleNote = if (result.staleSchedulerJobLockCount > 0) {
      " Ignored ${result.staleSchedulerJobLockCount} stale scheduler claim(s)."
    } else {
      ""
    }
    val baseMessage = result.message.ifBlank {
      result.blockers
        .firstNotNullOfOrNull { blocker -> blocker.summary.takeIf(String::isNotBlank) }
        ?: "Restart blocked."
    }
    return (baseMessage + staleNote).trim()
  }
  return result.message.ifBlank { "Restart requested." }
}

internal fun boundAdminConsoleOutput(
  text: String,
  maxChars: Int = ADMIN_CONSOLE_MAX_CHARS,
): String {
  if (text.length <= maxChars) return text
  return text.takeLast(maxChars)
}

private fun formatConsoleOutput(source: String, body: String): String {
  val normalizedBody = body.trim()
  if (normalizedBody.isBlank()) return "[$source] waiting for output..."
  return boundAdminConsoleOutput("[$source]\n$normalizedBody")
}

internal fun appUpdateChannelForPackageName(packageName: String): String {
  return if (packageName.trim().endsWith(".debug")) "debug" else "release"
}

internal fun isAppUpdateAvailable(
  installedSha256: String,
  remoteSha256: String,
): Boolean {
  val installed = installedSha256.trim()
  val remote = remoteSha256.trim()
  if (remote.isBlank()) return false
  if (installed.isBlank()) return true
  return !installed.equals(remote, ignoreCase = true)
}

internal fun resolveAdminGitMessage(
  sharedInput: String,
  fallbackGitMessage: String,
): String {
  val shared = sharedInput.trim()
  if (shared.isNotBlank()) return shared
  return fallbackGitMessage.trim()
}

internal fun renderAdminGitJournalStatus(status: GitStatus): String {
  if (status.commitJournalPendingCount <= 0) return "Journal: empty"
  if (status.commitJournalInvalidCount > 0) {
    return "Journal: ${status.commitJournalPendingCount} pending, ${status.commitJournalInvalidCount} invalid"
  }
  if (status.commitJournalReady && !status.commitJournalCoverageOk && status.commitJournalUncoveredCount > 0) {
    return "Journal: ready + ${status.commitJournalUncoveredCount} reconstructed paths"
  }
  if (status.commitJournalReady) return "Journal: ready (${status.commitJournalPendingCount} pending)"
  if (!status.commitJournalCoverageOk && status.commitJournalUncoveredCount > 0) {
    return "Journal: ${status.commitJournalPendingCount} pending, ${status.commitJournalUncoveredCount} uncovered paths"
  }
  if (status.commitJournalError.isNotBlank()) return "Journal: ${status.commitJournalError}"
  return "Journal: ${status.commitJournalPendingCount} pending"
}

internal fun buildMobileAppUpdateBrowserUrl(
  baseUrl: String,
  channel: String,
): String {
  val safeBaseUrl = baseUrl.trim().trimEnd('/')
  val safeChannel = channel.trim().ifBlank { "debug" }
  if (safeBaseUrl.isBlank()) return ""
  val query = buildList {
    add("channel=${urlEncodeUtf8(safeChannel)}")
    add("auto=1")
  }.joinToString("&")
  return "$safeBaseUrl/admin/mobile/update?$query"
}

private fun urlEncodeUtf8(value: String): String = URLEncoder.encode(value, Charsets.UTF_8.name())
