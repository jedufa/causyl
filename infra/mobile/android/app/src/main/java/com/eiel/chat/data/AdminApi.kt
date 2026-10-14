package com.eiel.chat.data

import com.eiel.chat.BuildConfig
import com.eiel.chat.domain.WorkflowEvent
import com.eiel.chat.domain.WorkflowStartRequest
import com.eiel.chat.domain.WorkflowStartResponse
import com.eiel.chat.domain.WorkflowStatus
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

data class HealthInfo(
  val status: String,
)

data class RestartBlocker(
  val kind: String,
  val count: Int,
  val summary: String,
)

data class RestartServerResult(
  val accepted: Boolean,
  val blocked: Boolean,
  val message: String,
  val healthStatus: String,
  val blockers: List<RestartBlocker> = emptyList(),
  val activeSchedulerJobLockCount: Int = 0,
  val staleSchedulerJobLockCount: Int = 0,
  val activeAdminCommandJobCount: Int = 0,
  val forceRequested: Boolean = false,
)

data class AdminConfig(
  val repoRoot: String,
  val archivesDir: String,
)

data class GitStatus(
  val branch: String,
  val head: String,
  val describe: String,
  val statusPorcelain: String,
  val baselineSuggested: String,
  val commitJournalPendingCount: Int,
  val commitJournalInvalidCount: Int,
  val commitJournalCoverageOk: Boolean,
  val commitJournalReady: Boolean,
  val commitJournalUncoveredCount: Int,
  val commitJournalError: String,
  val commitJournalPreviewSubject: String,
  val commitJournalEntries: List<CommitJournalEntry> = emptyList(),
  val stderr: String,
)

data class CommitJournalEntry(
  val entryId: String,
  val createdUtc: String,
  val title: String,
  val outcome: String,
  val scopeHint: String,
  val authorRole: String,
  val authorSource: String,
  val bindingType: String,
  val bindingId: String,
  val summaryBullets: List<String> = emptyList(),
  val files: List<String> = emptyList(),
  val validation: List<String> = emptyList(),
  val notes: List<String> = emptyList(),
)

data class AdminJob(
  val jobId: String,
  val kind: String,
  val cmd: String,
  val cwdRel: String,
  val running: Boolean,
  val killed: Boolean,
  val returnCode: Int?,
  val utcStarted: String,
  val utcFinished: String,
  val durationSec: Double,
  val stdout: String,
  val stderr: String,
  val error: String,
)

data class PlaneHistoryEntry(
  val sessionId: String,
  val utc: String,
  val prompt: String,
  val authorityScope: String,
)

data class ArchiveItem(
  val name: String,
  val bytes: Long,
  val mtime: Double,
)

data class ManifestJob(
  val jobId: String,
  val state: String,
  val pid: Int?,
  val reportDir: String,
  val manifest: String,
)

data class SystemRun(
  val runKey: String,
  val displayId: String,
  val displaySlug: String,
  val displaySuffix: String,
  val runDir: String,
  val state: String,
  val pid: Int?,
  val gen: Int?,
  val heartbeatAgeSec: Double?,
  val source: String,
  val startedAtUtc: String,
)

data class RunSummary(
  val runKey: String,
  val displayId: String,
  val displaySlug: String,
  val displaySuffix: String,
  val liveState: String,
  val status: String,
  val determinism: String,
  val strictMode: Boolean,
  val currentGeneration: Int?,
  val targetGeneration: Int?,
  val epi: Double?,
  val av: Double?,
  val lastVerifiedGeneration: Int?,
  val parentRunKey: String,
  val notesExcerpt: String,
)

data class EpiAvPoint(
  val generation: Int,
  val epi: Double?,
  val av: Double?,
)

data class EpiAvPayload(
  val source: String,
  val provisional: Boolean,
  val hasEpi: Boolean,
  val hasAv: Boolean,
  val points: List<EpiAvPoint>,
)

data class SystemProcess(
  val pid: Int,
  val ppid: Int,
  val pgid: Int,
  val stat: String,
  val elapsed: String,
  val pcpu: Double,
  val rssKb: Int,
  val threads: Int,
  val args: String,
  val matchesSimulation: Boolean,
  val processKind: String,
  val displayId: String,
  val displaySlug: String,
  val displaySuffix: String,
  val launchTargetModule: String,
  val jobId: String,
  val reportDir: String,
)

data class BuilderHarness(
  val name: String,
  val module: String,
  val displayId: String,
  val displaySlug: String,
  val displaySuffix: String,
  val harnessPath: String,
  val registryPath: String,
  val registryName: String,
  val description: String,
  val seedDefault: Int,
  val mtimeUtc: String,
  val sizeBytes: Int,
  val runCommandManaged: String,
)

data class BuilderRecentJob(
  val jobId: String,
  val displayId: String,
  val displaySlug: String,
  val displaySuffix: String,
  val state: String,
  val pid: Int?,
  val pgid: Int?,
  val reportDir: String,
  val launchTargetModule: String,
  val selectedTarget: String,
  val requestId: String,
  val updatedAtUtc: String,
)

data class BuilderHarnessesPayload(
  val harnesses: List<BuilderHarness>,
  val recentJobs: List<BuilderRecentJob>,
  val activeJobId: String,
)

data class BuilderLaunchResult(
  val status: String,
  val jobId: String,
  val lifecycle: String,
  val error: String,
)

data class BuilderJobStatus(
  val jobId: String,
  val displayId: String,
  val displaySlug: String,
  val displaySuffix: String,
  val lifecycle: String,
  val terminal: Boolean,
  val state: String,
  val pid: Int?,
  val pidAlive: Boolean,
  val exitCode: Int?,
  val failureCode: String,
  val failureDetail: String,
  val reportDir: String,
  val runPath: String,
  val logPath: String,
  val logTail: String,
  val launchTargetModule: String,
  val selectedTarget: String,
)

data class BuilderEntrypoint(
  val module: String,
  val label: String,
)

data class BuilderConfig(
  val gitHead: String,
  val runsRootDefault: String,
  val manifestOutDir: String,
  val entrypoints: List<BuilderEntrypoint>,
  val knobKeys: List<String>,
)

data class BuilderRegistry(
  val path: String,
  val uid: String,
  val displayId: String,
  val harnessExists: Boolean,
  val harnessPath: String,
  val mtimeUtc: String,
  val sizeBytes: Int,
)

data class BuilderTemplate(
  val name: String,
  val path: String,
  val mtimeUtc: String,
  val sizeBytes: Int,
)

data class BuilderLoadedHarnessPayload(
  val testId: String,
  val description: String,
  val intentQuestion: String,
  val runsRoot: String,
  val seeds: List<Int>,
  val caseValues: List<String>,
  val defaultKnobs: List<String>,
  val expectedArtifacts: List<String>,
  val perf: Boolean,
  val enableProfiler: Boolean,
  val generations: Int?,
  val steps: Int?,
  val pop: Int?,
  val gridSize: Int?,
  val stride: Int?,
  val checkpointPeriodGen: Int?,
  val checkpointRotate: Int?,
)

data class BuilderRegistryLoadResult(
  val sourcePath: String,
  val harnessExists: Boolean,
  val harnessPath: String,
  val runCommandManaged: String,
  val harnessPayload: BuilderLoadedHarnessPayload?,
)

data class BuilderTemplatePayload(
  val templateName: String,
  val harness: BuilderLoadedHarnessPayload?,
  val manifestName: String,
  val manifestOutputRoot: String,
  val manifestStrict: Boolean,
  val manifestGitRefOverride: String,
  val manifestManual: String,
)

data class BuilderGenerateHarnessResult(
  val ok: Boolean,
  val name: String,
  val uid: String,
  val registryPath: String,
  val harnessPath: String,
  val runCommandManaged: String,
  val issues: List<String>,
  val registryPreview: String,
  val harnessPreview: String,
)

data class BuilderGenerateManifestResult(
  val ok: Boolean,
  val manifestPath: String,
  val gitRef: String,
  val recommendedStartCmd: String,
  val jobCount: Int,
  val issues: List<String>,
  val manifestPreview: String,
)

data class LedgerJob(
  val jobId: String,
  val actionId: String,
  val event: String,
  val status: String,
  val exitCode: Int?,
)

data class LedgerItemSummary(
  val uid: String,
  val slug: String = "",
  val title: String,
  val status: String,
  val releaseState: String,
  val updatedUtc: String,
  val executionPriority: Int? = null,
)

data class LedgerArtifact(
  val relpath: String,
  val bytes: Long,
)

data class LedgerItemMeta(
  val uid: String,
  val slug: String,
  val title: String,
  val status: String,
  val releaseState: String,
  val createdUtc: String,
  val updatedUtc: String,
  val executionPriority: Int? = null,
  val tags: List<String>,
  val changesets: List<String> = emptyList(),
  val supersedesUid: String,
  val regressionOfUid: String,
)

data class LedgerItemDetail(
  val uid: String,
  val dir: String,
  val meta: LedgerItemMeta,
  val pisMarkdown: String,
  val notes: String,
  val artifacts: List<LedgerArtifact>,
)

data class MobileCockpitContracts(
  val schemaVersion: String,
  val contractOk: Boolean,
  val sharedStatusLanguage: List<String> = emptyList(),
  val sharedActionLanguage: List<String> = emptyList(),
  val entityOwners: Map<String, String> = emptyMap(),
)

data class MobileCockpitSelection(
  val selectedUids: List<String> = emptyList(),
  val primaryUid: String = "",
  val selectedCount: Int = 0,
)

data class MobileCockpitPisCounts(
  val draft: Int = 0,
  val ready: Int = 0,
  val total: Int = 0,
)

data class MobileCockpitCampaign(
  val campaignId: String,
  val launchOutcome: String,
  val queueRunId: String = "",
  val workflowId: String = "",
  val createdUtc: String = "",
)

data class MobileCockpitCampaigns(
  val latest: MobileCockpitCampaign? = null,
)

data class MobileCockpitExecutionQueue(
  val status: String = "",
  val currentUid: String = "",
  val runId: String = "",
  val totalCount: Int = 0,
  val workerAlive: Boolean = false,
)

data class MobileCockpitArtifactHandoff(
  val name: String,
  val previewText: String,
  val available: Boolean = false,
  val artifactPath: String = "",
)

data class MobileCockpitArtifacts(
  val status: String = "",
  val latestHandoff: MobileCockpitArtifactHandoff? = null,
)

data class MobileCockpitContinuationMemoryRecord(
  val summary: String = "",
  val status: String = "",
  val reason: String = "",
  val expiresAtUtc: String = "",
)

data class MobileCockpitContinuation(
  val status: String = "",
  val reason: String = "",
  val summary: String = "",
  val handoffStatus: String = "",
  val authoritativeSource: String = "",
  val packetStatus: String = "",
  val packetReason: String = "",
  val packetId: String = "",
  val packetRevision: Int = 0,
  val packetCreatedUtc: String = "",
  val memoryStatus: String = "",
  val memoryReason: String = "",
  val activeMemoryCount: Int = 0,
  val expiredMemoryCount: Int = 0,
  val withdrawnMemoryCount: Int = 0,
  val contradictedMemoryCount: Int = 0,
  val failedPaths: List<String> = emptyList(),
  val openQuestions: List<String> = emptyList(),
  val nextSteps: List<String> = emptyList(),
  val visibleCaveats: List<String> = emptyList(),
  val memoryRecords: List<MobileCockpitContinuationMemoryRecord> = emptyList(),
)

data class MobileCockpitRunTriage(
  val status: String = "",
  val summary: String = "",
  val failureClass: String = "",
  val actions: List<String> = emptyList(),
  val workflowId: String = "",
)

data class MobileCockpitEconomics(
  val status: String = "",
  val totalTokens: Int = 0,
  val wasteWarning: String = "",
)

data class MobileCockpitArchiveSnapshots(
  val status: String = "",
  val latestRepoSnapshotId: String = "",
  val runArchiveCount: Int = 0,
)

data class MobileCockpitChronicleEntry(
  val entryId: String,
  val kind: String,
  val summary: String,
  val utc: String = "",
  val source: String = "",
)

data class MobileCockpitChronicle(
  val status: String = "",
  val entries: List<MobileCockpitChronicleEntry> = emptyList(),
)

data class MobileCockpitShell(
  val status: String = "",
  val warnings: List<String> = emptyList(),
  val fallbackActions: List<String> = emptyList(),
  val campaignId: String = "",
  val workflowId: String = "",
  val queueRunId: String = "",
)

data class MobileCockpitPayload(
  val schemaVersion: String,
  val utc: String,
  val contracts: MobileCockpitContracts = MobileCockpitContracts("", false),
  val selection: MobileCockpitSelection = MobileCockpitSelection(),
  val pisItems: List<LedgerItemSummary> = emptyList(),
  val pisCounts: MobileCockpitPisCounts = MobileCockpitPisCounts(),
  val campaigns: MobileCockpitCampaigns = MobileCockpitCampaigns(),
  val executionQueue: MobileCockpitExecutionQueue = MobileCockpitExecutionQueue(),
  val artifacts: MobileCockpitArtifacts = MobileCockpitArtifacts(),
  val continuation: MobileCockpitContinuation = MobileCockpitContinuation(),
  val runTriage: MobileCockpitRunTriage = MobileCockpitRunTriage(),
  val economics: MobileCockpitEconomics = MobileCockpitEconomics(),
  val archiveSnapshots: MobileCockpitArchiveSnapshots = MobileCockpitArchiveSnapshots(),
  val chronicle: MobileCockpitChronicle = MobileCockpitChronicle(),
  val shell: MobileCockpitShell = MobileCockpitShell(),
  val rawContracts: String = "",
  val rawCampaigns: String = "",
  val rawExecutionQueue: String = "",
  val rawArtifacts: String = "",
  val rawContinuation: String = "",
  val rawRunTriage: String = "",
  val rawEconomics: String = "",
  val rawArchiveSnapshots: String = "",
  val rawChronicle: String = "",
  val rawShell: String = "",
)

data class MobileCockpitCampaignLaunchResult(
  val schemaVersion: String,
  val utc: String,
  val campaign: MobileCockpitCampaign,
)

data class MobileCockpitArtifactHandoffResult(
  val schemaVersion: String,
  val utc: String,
  val ok: Boolean,
  val artifactPath: String,
  val error: String = "",
)

data class MobileCockpitArchiveCreateResult(
  val schemaVersion: String,
  val utc: String,
  val ok: Boolean,
  val snapshotId: String,
  val downloadUrl: String,
  val error: String = "",
)

data class LedgerWorkflowStatus(
  val nextStatus: String,
)

data class LedgerCreateResult(
  val ok: Boolean,
  val uid: String,
  val slug: String,
  val dir: String,
  val createdUtc: String,
  val existing: Boolean = false,
)

data class OperatorSnapshot(
  val operatorState: String,
  val activeTasks: List<OperatorSnapshotTask> = emptyList(),
  val completedTasks: List<OperatorSnapshotTask> = emptyList(),
  val freshness: OperatorSnapshotFreshness = OperatorSnapshotFreshness(),
  val summary: OperatorSnapshotSummary = OperatorSnapshotSummary(),
  val commandCenter: OperatorSnapshotCommandCenter = OperatorSnapshotCommandCenter(),
  val workItems: List<OperatorSnapshotWorkItem> = emptyList(),
)

data class OperatorSnapshotTask(
  val taskId: String,
  val title: String,
  val status: String,
  val waitingReason: String,
  val nextExpectedAction: String,
  val nextDecisionSummary: String,
  val evidenceBackedCompletion: Boolean,
  val delegated: Boolean = false,
  val currentRunId: String = "",
  val currentRunState: String = "",
  val currentRunAttentionState: String = "",
  val runCheckpoint: OperatorSnapshotRunCheckpoint = OperatorSnapshotRunCheckpoint(),
)

data class OperatorSnapshotRunCheckpoint(
  val checkpointStatus: String = "",
  val progressPercent: Int? = null,
  val progressSummary: String = "",
)

data class OperatorSnapshotFreshness(
  val generatedAt: String = "",
  val freshnessSeconds: Int? = null,
  val confidence: String = "",
  val stale: Boolean = false,
  val staleSources: List<String> = emptyList(),
)

data class OperatorSnapshotSummary(
  val statusLanguage: OperatorSnapshotStatusLanguage = OperatorSnapshotStatusLanguage(),
  val sections: List<OperatorSnapshotSection> = emptyList(),
  val notifications: List<OperatorSnapshotNotification> = emptyList(),
)

data class OperatorSnapshotStatusLanguage(
  val state: String = "",
  val headline: String = "",
  val tone: String = "",
  val freshness: String = "",
  val successClaimAllowed: Boolean = false,
)

data class OperatorSnapshotSection(
  val sectionId: String,
  val title: String,
  val count: Int,
  val items: List<OperatorSnapshotSectionItem> = emptyList(),
)

data class OperatorSnapshotSectionItem(
  val taskId: String,
  val title: String,
  val status: String,
  val summary: String,
  val owner: String,
  val action: String,
  val evidenceRef: String,
)

data class OperatorSnapshotNotification(
  val kind: String,
  val severity: String,
  val headline: String,
  val message: String,
  val completionClaimAllowed: Boolean,
)

data class OperatorSnapshotCommandCenter(
  val headline: String = "",
  val snapshotMeta: OperatorSnapshotFreshness = OperatorSnapshotFreshness(),
  val intakePrompt: String = "",
  val needsAttention: List<OperatorSnapshotCommandCenterItem> = emptyList(),
  val running: List<OperatorSnapshotCommandCenterItem> = emptyList(),
  val scheduled: List<OperatorSnapshotCommandCenterItem> = emptyList(),
  val completedCount: Int = 0,
)

data class OperatorSnapshotCommandCenterItem(
  val taskId: String = "",
  val title: String = "",
  val state: String = "",
  val reason: String = "",
  val nextAction: String = "",
  val stageLabel: String = "",
  val executionSurface: String = "",
  val metadata: List<String> = emptyList(),
  val confidenceLabel: String = "",
  val recommended: Boolean = false,
  val riskLevel: String = "",
  val consequence: String = "",
  val artifactCount: Int = 0,
)

data class OperatorSnapshotWorkItem(
  val taskId: String = "",
  val stableTitle: String = "",
  val state: String = "",
  val reason: String = "",
  val executionSurface: String = "",
  val nextActionPrompt: String = "",
  val nextActionType: String = "",
  val nextActionBlocking: Boolean = false,
  val recommendedActionPrompt: String = "",
  val recommendedActionType: String = "",
  val recommendedActionRisk: String = "",
  val recommendedActionConsequence: String = "",
  val confidenceLabel: String = "",
  val confidenceWarn: Boolean = false,
  val timingLabel: String = "",
  val retryLabel: String = "",
  val displayDepth: Int = 0,
  val artifacts: List<String> = emptyList(),
)

data class PlaneLogSnapshot(
  val fetchedUtc: String = "",
  val dashboard: PlaneLogDashboard = PlaneLogDashboard(),
  val triage: PlaneLogTriage = PlaneLogTriage(),
  val watchdog: PlaneLogWatchdog = PlaneLogWatchdog(),
  val recentControls: List<PlaneLogControlEvent> = emptyList(),
)

data class PlaneLogDashboard(
  val operatorState: String = "",
  val headline: String = "",
  val activeTaskCount: Int = 0,
  val openLoopCount: Int = 0,
  val queueStatus: String = "",
  val queueStatusDetail: String = "",
  val queueActive: Boolean = false,
  val workflowCount: Int = 0,
  val schedulerRunning: Boolean = false,
  val schedulerLastTickUtc: String = "",
  val scheduledJobCount: Int = 0,
  val backendState: String = "",
)

data class PlaneLogTriage(
  val state: String = "",
  val headline: String = "",
  val conflictCount: Int = 0,
  val gapCount: Int = 0,
  val nextSteps: List<String> = emptyList(),
)

data class PlaneLogWatchdog(
  val auditTimerActive: Boolean = false,
  val selfhealTimerActive: Boolean = false,
  val auditTimerState: String = "",
  val selfhealTimerState: String = "",
  val auditLatestUtc: String = "",
  val selfhealLatestUtc: String = "",
  val auditLatestHealth: String = "",
  val selfhealLatestHealth: String = "",
)

data class PlaneLogControlEvent(
  val controlRequestId: String = "",
  val actionId: String = "",
  val requestedUtc: String = "",
  val writer: String = "",
  val confirmationState: String = "",
  val confirmationSummary: String = "",
  val durableSignal: String = "",
  val invocationOk: Boolean? = null,
)

data class OrchestrationWorkflowSummary(
  val workflowId: String,
  val phaseLabel: String,
  val status: String,
  val updatedUtc: String,
  val createdUtc: String,
  val bindingType: String,
  val bindingId: String,
  val turn: String,
)

data class WorkflowConversationItem(
  val id: String,
  val utc: String,
  val actor: String,
  val source: String,
  val target: String,
  val kind: String,
  val title: String,
  val text: String,
  val origin: String,
)

data class LedgerStatusUpdateResult(
  val ok: Boolean,
  val uid: String,
  val status: String,
  val updatedUtc: String,
  val blocked: Boolean,
  val reason: String,
)

data class LedgerPriorityUpdateResult(
  val ok: Boolean,
  val uid: String,
  val executionPriority: Int?,
  val updatedUtc: String,
)

data class LedgerLog(
  val logPath: String,
  val lines: List<String>,
)

data class PatchInspectResult(
  val summary: String,
)

data class FsEntry(
  val name: String,
  val path: String,
  val isDir: Boolean,
  val bytes: Long,
  val mtime: Double,
)

data class FsReadResult(
  val path: String,
  val text: String,
  val truncated: Boolean,
)

data class DownloadPayload(
  val filename: String,
  val mimeType: String,
  val bytes: ByteArray? = null,
  val tempFilePath: String = "",
)

data class MobileAppUpdateManifest(
  val channel: String,
  val packageName: String,
  val versionName: String,
  val versionCode: Int,
  val apkName: String,
  val apkSizeBytes: Long,
  val apkSha256: String,
  val builtAt: String,
  val sourcePath: String,
  val downloadPath: String,
)

class AdminApi(
  private val client: OkHttpClient = OkHttpClient.Builder()
    .retryOnConnectionFailure(true)
    .connectTimeout(20, TimeUnit.SECONDS)
    .writeTimeout(90, TimeUnit.SECONDS)
    .readTimeout(90, TimeUnit.SECONDS)
    .callTimeout(0, TimeUnit.MILLISECONDS)
    .build(),
  serverBaseUrl: String = BuildConfig.SERVER_BASE_URL,
  private val serverBaseUrlProvider: (() -> String)? = null,
  private val authToken: String = BuildConfig.AUTH_TOKEN,
  private val workflowTimeoutS: Int = BuildConfig.WORKFLOW_TIMEOUT_S,
  private val workflowTimeoutProvider: (() -> Int)? = null,
) {
  private val fallbackBaseUrl = ServerConfig.normalizeBaseUrl(serverBaseUrl)
  private val workflowRunClient: OkHttpClient = client.newBuilder()
    .readTimeout(0, TimeUnit.MILLISECONDS)
    .callTimeout(0, TimeUnit.MILLISECONDS)
    .build()

  private fun baseUrl(): String = ServerConfig.normalizeBaseUrl(serverBaseUrlProvider?.invoke() ?: fallbackBaseUrl)
  private fun currentWorkflowTimeoutS(): Int =
    (workflowTimeoutProvider?.invoke() ?: workflowTimeoutS).coerceIn(30, 6 * 60 * 60)

  suspend fun health(): HealthInfo {
    val obj = getJson("/api/health", adminToken = "")
    return HealthInfo(status = obj.optString("status", "unknown"))
  }

  suspend fun config(adminToken: String): AdminConfig {
    val obj = getJson("/api/admin/config", adminToken)
    return AdminConfig(
      repoRoot = obj.optString("repo_root", ""),
      archivesDir = obj.optString("archives_dir", ""),
    )
  }

  suspend fun gitStatus(adminToken: String): GitStatus {
    val obj = getJson("/api/admin/git/status", adminToken)
    val journal = obj.optJSONObject("commit_journal")
    return GitStatus(
      branch = obj.optString("branch", ""),
      head = obj.optString("head", ""),
      describe = obj.optString("describe", ""),
      statusPorcelain = obj.optString("status_porcelain", ""),
      baselineSuggested = obj.optString("baseline_tag_suggested", ""),
      commitJournalPendingCount = journal?.optInt("pending_count", 0) ?: 0,
      commitJournalInvalidCount = journal?.optInt("invalid_count", 0) ?: 0,
      commitJournalCoverageOk = journal?.optBoolean("coverage_ok", false) ?: false,
      commitJournalReady = journal?.optBoolean("ready", false) ?: false,
      commitJournalUncoveredCount = journal?.optJSONArray("uncovered_paths")?.length() ?: 0,
      commitJournalError = journal?.optString("error", "") ?: "",
      commitJournalPreviewSubject = journal?.optString("preview_subject", "") ?: "",
      commitJournalEntries = parseCommitJournalEntries(journal?.optJSONArray("entries")),
      stderr = obj.optString("stderr", ""),
    )
  }

  suspend fun gitRun(adminToken: String): AdminJob {
    val obj = postJson("/api/admin/git/run", adminToken, JSONObject())
    return parseJob(obj.optJSONObject("job"))
  }

  suspend fun commandRun(
    adminToken: String,
    cmd: String,
    cwd: String,
    timeoutSec: Double,
  ): AdminJob {
    val payload = JSONObject()
      .put("cmd", cmd)
      .put("cwd", cwd)
      .put("timeout_sec", timeoutSec)
    val obj = postJson("/api/admin/cmd/run", adminToken, payload)
    return parseJob(obj.optJSONObject("job"))
  }

  suspend fun commandStatus(adminToken: String, jobId: String): AdminJob {
    val obj = getJson("/api/admin/cmd/status?job_id=${encode(jobId)}", adminToken)
    return parseJob(obj.optJSONObject("job"))
  }

  suspend fun commandKill(adminToken: String, jobId: String): AdminJob? {
    val payload = JSONObject().put("job_id", jobId)
    val obj = postJson("/api/admin/cmd/kill", adminToken, payload)
    return obj.optJSONObject("job")?.let { parseJob(it) }
  }

  suspend fun planeRun(
    adminToken: String,
    prompt: String,
    plan: Boolean = false,
    verbose: Boolean = false,
    patchPath: String = "",
    zipPath: String = "",
    reportFile: String = "",
    snapshotFile: String = "",
    receiptsFile: String = "",
    watchdogFile: String = "",
    iterationsFile: String = "",
    fallbackEventsFile: String = "",
    targetPisUid: String = "",
    targetUid: String = "",
    jobId: String = "",
    taskId: String = "",
    currentStatus: String = "",
    expectedStatus: String = "",
    verifierVerdict: String = "",
    validationCommands: List<String> = emptyList(),
    allowFallback: Boolean = false,
    noFallback: Boolean = false,
    allowRecovery: Boolean = false,
  ): AdminJob {
    val payload = JSONObject()
      .put("prompt", prompt)
      .put("plan", plan)
      .put("verbose", verbose)
      .put("patch_path", patchPath)
      .put("zip_path", zipPath)
      .put("report_file", reportFile)
      .put("snapshot_file", snapshotFile)
      .put("receipts_file", receiptsFile)
      .put("watchdog_file", watchdogFile)
      .put("iterations_file", iterationsFile)
      .put("fallback_events_file", fallbackEventsFile)
      .put("target_pis_uid", targetPisUid)
      .put("target_uid", targetUid)
      .put("job_id", jobId)
      .put("task_id", taskId)
      .put("current_status", currentStatus)
      .put("expected_status", expectedStatus)
      .put("verifier_verdict", verifierVerdict)
      .put("validation_commands", JSONArray(validationCommands))
      .put("allow_fallback", allowFallback)
      .put("no_fallback", noFallback)
      .put("allow_recovery", allowRecovery)
    val obj = postJson("/api/mobile/plane/run", adminToken, payload)
    return parseJob(obj.optJSONObject("job"))
  }

  suspend fun planeHistory(adminToken: String, limit: Int = 100): List<PlaneHistoryEntry> {
    val obj = getJson("/api/mobile/plane/history?limit=${limit.coerceIn(1, 500)}", adminToken)
    return parsePlaneHistoryEntries(obj.optJSONArray("entries"))
  }

  suspend fun archives(adminToken: String): List<ArchiveItem> {
    val obj = getJson("/api/admin/archive/list", adminToken)
    return parseArchiveItems(obj.optJSONArray("archives"))
  }

  suspend fun createArchive(adminToken: String, debugOnly: Boolean): DownloadPayload {
    return downloadGet("/api/admin/archive/create?debug=${if (debugOnly) "1" else "0"}", adminToken)
  }

  suspend fun downloadArchive(adminToken: String, name: String): DownloadPayload {
    return downloadGet("/api/admin/archive/download/${encodePath(name)}", adminToken)
  }

  suspend fun manifests(adminToken: String): List<ManifestJob> {
    val obj = getJson("/api/admin/manifests", adminToken)
    return parseManifestJobs(obj.optJSONArray("jobs"))
  }

  suspend fun systemRuns(adminToken: String): List<SystemRun> {
    val obj = getJson("/api/system/runs", adminToken)
    return parseSystemRuns(obj.optJSONArray("active"))
  }

  suspend fun runsIndex(adminToken: String): List<RunSummary> {
    val obj = getJson("/api/runs", adminToken)
    return parseRunSummaries(obj.optJSONArray("runs"))
  }

  suspend fun scheduledJobs(adminToken: String = ""): ScheduledJobsPayload {
    val obj = getJson("/api/schedules/jobs", adminToken = adminToken)
    return ScheduledJobsPayload(
      fetchedUtc = obj.optString("utc", ""),
      jobs = parseScheduledJobSummaries(obj.optJSONArray("jobs")),
      scheduler = parseSchedulerServiceStatus(obj.optJSONObject("scheduler")),
      watchdog = parseSchedulerMonitoringServiceStatus(obj.optJSONObject("watchdog")),
      healer = parseSchedulerMonitoringServiceStatus(obj.optJSONObject("healer")),
    )
  }

  private fun parsePlaneHistoryEntries(arr: JSONArray?): List<PlaneHistoryEntry> {
    if (arr == null) return emptyList()
    return buildList(arr.length()) {
      for (i in 0 until arr.length()) {
        val obj = arr.optJSONObject(i) ?: continue
        add(
          PlaneHistoryEntry(
            sessionId = obj.optString("session_id", ""),
            utc = obj.optString("utc", ""),
            prompt = obj.optString("prompt", ""),
            authorityScope = obj.optString("authority_scope", ""),
          )
        )
      }
    }
  }

  suspend fun scheduledHealth(adminToken: String = ""): SchedulerHealthPayload {
    val obj = getJson("/api/schedules/health", adminToken = adminToken)
    return SchedulerHealthPayload(
      fetchedUtc = obj.optString("utc", ""),
      scheduler = parseSchedulerServiceStatus(obj.optJSONObject("scheduler")),
      watchdog = parseSchedulerMonitoringServiceStatus(obj.optJSONObject("watchdog")),
      healer = parseSchedulerMonitoringServiceStatus(obj.optJSONObject("healer")),
    )
  }

  suspend fun scheduledJobDetail(jobId: String, adminToken: String = ""): ScheduledJobDetail {
    val obj = getJson("/api/schedules/job?job_id=${encode(jobId)}", adminToken = adminToken)
    return ScheduledJobDetail(
      fetchedUtc = obj.optString("utc", ""),
      job = parseScheduledJobSummary(obj.optJSONObject("job")),
      receipts = parseScheduledJobReceipts(obj.optJSONArray("receipts")),
    )
  }

  suspend fun updateScheduledJobRunProfile(
    jobId: String,
    model: String,
    maxIterations: Int?,
    adminToken: String = "",
  ): ScheduledJobSummary {
    val payload = JSONObject().put("job_id", jobId).put("model", model)
    if (maxIterations != null) payload.put("max_iterations", maxIterations)
    val obj = postJson("/api/schedules/job/update_run_profile", adminToken = adminToken, payload = payload)
    return parseScheduledJobSummary(obj.optJSONObject("job"))
  }

  suspend fun runScheduledJobNow(jobId: String, adminToken: String = ""): ScheduledJobRunNowResult {
    val payload = JSONObject().put("job_id", jobId)
    val obj = postJson("/api/schedules/run_now", adminToken = adminToken, payload = payload)
    return ScheduledJobRunNowResult(
      ok = obj.optBoolean("ok", false),
      jobId = obj.optString("job_id", jobId),
      runId = obj.optString("run_id", ""),
      status = obj.optString("status", if (obj.optBoolean("queued", false)) "queued" else ""),
      queued = obj.optBoolean("queued", false),
      reason = obj.optString("reason", ""),
      requestedUtc = obj.optString("requested_utc", ""),
    )
  }

  suspend fun runFileTail(
    adminToken: String,
    runKey: String,
    which: String = "stdout",
    maxBytes: Int = 65536,
  ): String {
    val obj = getJson(
      "/api/run/file_tail?key=${encode(runKey)}&which=${encode(which)}&max_bytes=${maxBytes.coerceIn(1024, 524288)}",
      adminToken,
    )
    return obj.optString("text", "")
  }

  suspend fun runEpiAv(
    adminToken: String,
    runKey: String,
    afterGen: Int = -1,
    limit: Int = 240,
  ): EpiAvPayload {
    val obj = getJson(
      "/api/run/${encodePath(runKey)}/epiav?after_gen=$afterGen&limit=${limit.coerceIn(20, 1000)}",
      adminToken,
    )
    return EpiAvPayload(
      source = obj.optString("source", ""),
      provisional = obj.optBoolean("provisional", false),
      hasEpi = obj.optBoolean("has_epi", false),
      hasAv = obj.optBoolean("has_av", false),
      points = parseEpiAvPoints(obj.optJSONArray("points")),
    )
  }

  suspend fun systemProcesses(adminToken: String, includeAll: Boolean): List<SystemProcess> {
    val obj = getJson("/api/system/processes?include_all=${if (includeAll) "1" else "0"}", adminToken)
    return parseSystemProcesses(obj.optJSONArray("processes"))
  }

  suspend fun signalProcess(adminToken: String, pid: Int, hard: Boolean): String {
    val payload = JSONObject().put("pid", pid)
    val obj = postJson(
      if (hard) "/api/system/processes/kill" else "/api/system/processes/terminate",
      adminToken,
      payload,
    )
    val method = obj.optString("method", "")
    val signal = obj.optString("signal", if (hard) "SIGKILL" else "SIGTERM")
    return "Sent $signal to pid=$pid${if (method.isNotBlank()) " via $method" else ""}"
  }

  suspend fun builderHarnesses(adminToken: String): BuilderHarnessesPayload {
    val obj = getJson("/api/builder/harnesses", adminToken)
    return BuilderHarnessesPayload(
      harnesses = parseBuilderHarnesses(obj.optJSONArray("harnesses")),
      recentJobs = parseBuilderRecentJobs(obj.optJSONArray("recent_jobs")),
      activeJobId = obj.optString("active_job_id", ""),
    )
  }

  suspend fun builderConfig(adminToken: String): BuilderConfig {
    val obj = getJson("/api/builder/config", adminToken)
    return BuilderConfig(
      gitHead = obj.optString("git_head", ""),
      runsRootDefault = obj.optJSONObject("builder")?.optString("runs_root_default", "").orEmpty(),
      manifestOutDir = obj.optJSONObject("builder")?.optString("manifest_out_dir", "").orEmpty(),
      entrypoints = parseBuilderEntrypoints(obj.optJSONArray("entrypoints")),
      knobKeys = parseStringList(obj.optJSONArray("knob_keys")),
    )
  }

  suspend fun builderRegistries(adminToken: String): List<BuilderRegistry> {
    val obj = getJson("/api/builder/registries", adminToken)
    return parseBuilderRegistries(obj.optJSONArray("registries"))
  }

  suspend fun builderTemplates(adminToken: String): List<BuilderTemplate> {
    val obj = getJson("/api/builder/templates", adminToken)
    return parseBuilderTemplates(obj.optJSONArray("templates"))
  }

  suspend fun builderRegistryLoad(adminToken: String, path: String): BuilderRegistryLoadResult {
    val payload = JSONObject().put("path", path)
    val obj = postJson("/api/builder/registry/load", adminToken, payload)
    return BuilderRegistryLoadResult(
      sourcePath = obj.optString("source_path", ""),
      harnessExists = obj.optBoolean("harness_exists", false),
      harnessPath = obj.optString("harness_path", ""),
      runCommandManaged = obj.optString("run_command_managed", obj.optString("run_command", "")),
      harnessPayload = obj.optJSONObject("harness_payload")?.let(::parseBuilderLoadedHarnessPayload),
    )
  }

  suspend fun builderSaveTemplate(
    adminToken: String,
    name: String,
    harness: BuilderLoadedHarnessPayload?,
    manifestName: String,
    manifestOutputRoot: String,
    manifestStrict: Boolean,
    manifestGitRefOverride: String,
    manifestManual: String,
  ): BuilderTemplate {
    val payload = JSONObject()
      .put("name", name)
      .put("schema_version", "run_builder_template_v1")
      .put("payload", JSONObject().apply {
        if (harness != null) put("harness", buildBuilderHarnessTemplateJson(harness))
        put("manifest", JSONObject().apply {
          put("name", manifestName)
          put("output_root", manifestOutputRoot)
          put("strict", manifestStrict)
          put("git_ref_override", manifestGitRefOverride)
          put("manual", manifestManual)
        })
      })
    val obj = postJson("/api/builder/templates/save", adminToken, payload)
    return BuilderTemplate(
      name = obj.optString("name", ""),
      path = obj.optString("path", ""),
      mtimeUtc = obj.optString("utc", ""),
      sizeBytes = 0,
    )
  }

  suspend fun builderLoadTemplate(adminToken: String, name: String): BuilderTemplatePayload {
    val payload = JSONObject().put("name", name)
    val obj = postJson("/api/builder/templates/load", adminToken, payload)
    val doc = obj.optJSONObject("payload")
    val harness = doc?.optJSONObject("harness")?.let(::parseBuilderLoadedHarnessPayload)
    val manifest = doc?.optJSONObject("manifest")
    return BuilderTemplatePayload(
      templateName = obj.optString("name", name),
      harness = harness,
      manifestName = manifest?.optString("name", "").orEmpty(),
      manifestOutputRoot = manifest?.optString("output_root", "").orEmpty(),
      manifestStrict = manifest?.optBoolean("strict", false) ?: false,
      manifestGitRefOverride = manifest?.optString("git_ref_override", "").orEmpty(),
      manifestManual = manifest?.optString("manual", "").orEmpty(),
    )
  }

  suspend fun builderGenerateHarness(
    adminToken: String,
    harness: BuilderLoadedHarnessPayload,
  ): BuilderGenerateHarnessResult {
    val obj = postJson("/api/builder/generate_harness", adminToken, buildBuilderHarnessTemplateJson(harness))
    return BuilderGenerateHarnessResult(
      ok = obj.optBoolean("ok", false),
      name = obj.optString("name", ""),
      uid = obj.optString("uid", ""),
      registryPath = obj.optString("registry_path", ""),
      harnessPath = obj.optString("harness_path", ""),
      runCommandManaged = obj.optString("run_command_managed", obj.optString("run_command", "")),
      issues = parseStringList(obj.optJSONArray("issues")),
      registryPreview = obj.optString("registry_preview", ""),
      harnessPreview = obj.optString("harness_preview", ""),
    )
  }

  suspend fun builderGenerateManifest(
    adminToken: String,
    testName: String,
    registryPaths: List<String>,
    outputRoot: String,
    strict: Boolean,
    gitRefOverride: String,
  ): BuilderGenerateManifestResult {
    val payload = JSONObject()
      .put("testname", testName)
      .put("registry_paths", JSONArray(registryPaths))
      .put("output_root", outputRoot)
      .put("strict", strict)
      .put("git_ref_override", gitRefOverride)
    val obj = postJson("/api/builder/generate_manifest", adminToken, payload)
    return BuilderGenerateManifestResult(
      ok = obj.optBoolean("ok", false),
      manifestPath = obj.optString("manifest_path", ""),
      gitRef = obj.optString("git_ref", ""),
      recommendedStartCmd = obj.optString("recommended_start_cmd", ""),
      jobCount = obj.optInt("job_count", 0),
      issues = parseStringList(obj.optJSONArray("issues")),
      manifestPreview = obj.optString("manifest_preview", ""),
    )
  }

  suspend fun builderLaunch(adminToken: String, cmd: String): BuilderLaunchResult {
    val payload = JSONObject().put("cmd", cmd)
    val obj = postJson("/api/builder/launch", adminToken, payload)
    return BuilderLaunchResult(
      status = obj.optString("status", ""),
      jobId = obj.optString("job_id", ""),
      lifecycle = obj.optString("lifecycle", ""),
      error = obj.optString("error", ""),
    )
  }

  suspend fun builderJobStatus(adminToken: String, jobId: String): BuilderJobStatus {
    val obj = getJson("/api/builder/job_status?job_id=${encode(jobId)}", adminToken)
    return parseBuilderJobStatus(obj)
  }

  suspend fun builderJobLog(adminToken: String, jobId: String, maxBytes: Int = 65536): String {
    val obj = getJson(
      "/api/builder/job_log?job_id=${encode(jobId)}&max_bytes=${maxBytes.coerceIn(1024, 524288)}",
      adminToken,
    )
    return obj.optString("text", "")
  }

  suspend fun manifestControl(adminToken: String, jobId: String, action: String, mode: String = "graceful"): List<ManifestJob> {
    val payload = JSONObject().put("job_id", jobId)
    if (action == "stop") payload.put("mode", mode)
    postJson("/api/admin/manifests/$action", adminToken, payload)
    return manifests(adminToken)
  }

  suspend fun ledgerStatus(adminToken: String, uid: String, limit: Int): List<LedgerJob> {
    val obj = getJson("/api/ledger/action/status?uid=${encode(uid)}&limit=$limit", adminToken)
    return parseLedgerJobs(obj.optJSONArray("jobs"))
  }

  suspend fun mobileBacklog(limit: Int = 250): List<LedgerItemSummary> {
    val obj = getJson("/api/mobile/backlog?limit=${limit.coerceIn(1, 500)}", adminToken = "")
    return parseLedgerItemSummaries(obj.optJSONArray("items"))
  }

  suspend fun mobileCockpit(
    limit: Int = 250,
    selectedUids: List<String> = emptyList(),
    topic: String = "",
    groupMode: String = "status",
  ): MobileCockpitPayload {
    val selectedQuery = selectedUids
      .map { it.trim() }
      .filter { it.isNotBlank() }
      .joinToString("") { "&selected_uid=${encode(it)}" }
    val obj = getJson(
      "/api/mobile/cockpit?limit=${limit.coerceIn(1, 500)}&topic=${encode(topic)}&group=${encode(groupMode)}$selectedQuery",
      adminToken = "",
    )
    return parseMobileCockpitPayload(obj)
  }

  suspend fun mobileCockpitLaunchCampaign(
    selectedUids: List<String>,
    dependencyMode: String = "selected_order",
    phaseLabel: String = "WF-01",
    maxIterations: Int? = null,
    commitEachSuccess: Boolean = true,
  ): MobileCockpitCampaignLaunchResult {
    val payload = JSONObject()
      .put("selected_uids", JSONArray(selectedUids))
      .put("dependency_mode", dependencyMode)
      .put("phase_label", phaseLabel)
      .put("commit_each_success", commitEachSuccess)
    maxIterations?.let { payload.put("max_iterations", it) }
    val obj = postJson("/api/mobile/cockpit/campaigns/launch", adminToken = "", payload = payload)
    return MobileCockpitCampaignLaunchResult(
      schemaVersion = obj.optString("schema_version", ""),
      utc = obj.optString("utc", ""),
      campaign = parseMobileCockpitCampaign(obj.optJSONObject("campaign"))
        ?: MobileCockpitCampaign(campaignId = "", launchOutcome = ""),
    )
  }

  suspend fun mobileCockpitExportHandoff(
    ledgerUid: String,
    role: String = "builder",
  ): MobileCockpitArtifactHandoffResult {
    val payload = JSONObject()
      .put("ledger_uid", ledgerUid)
      .put("role", role)
    val obj = postJson("/api/mobile/cockpit/artifacts/handoff", adminToken = "", payload = payload)
    return MobileCockpitArtifactHandoffResult(
      schemaVersion = obj.optString("schema_version", ""),
      utc = obj.optString("utc", ""),
      ok = obj.optBoolean("ok", false),
      artifactPath = obj.optString("artifact_path", ""),
      error = obj.optString("error", ""),
    )
  }

  suspend fun mobileCockpitCreateArchive(
    adminToken: String,
    debug: Boolean = false,
  ): MobileCockpitArchiveCreateResult {
    val payload = JSONObject().put("debug", debug)
    val obj = postJson("/api/mobile/cockpit/archive/create", adminToken = adminToken, payload = payload)
    return MobileCockpitArchiveCreateResult(
      schemaVersion = obj.optString("schema_version", ""),
      utc = obj.optString("utc", ""),
      ok = obj.optBoolean("ok", true),
      snapshotId = obj.optString("snapshot_id", ""),
      downloadUrl = obj.optString("download_url", ""),
      error = obj.optString("error", ""),
    )
  }

  suspend fun labAssistantSnapshot(adminToken: String): OperatorSnapshot {
    val obj = getJson("/api/admin/lab_assistant/snapshot", adminToken)
    return parseOperatorSnapshot(obj)
  }

  suspend fun labAssistantWebOperations(adminToken: String, controlLimit: Int = 30): PlaneLogSnapshot {
    val obj = getJson(
      "/api/admin/lab_assistant/web_operations?control_limit=${controlLimit.coerceIn(10, 100)}",
      adminToken,
    )
    return parsePlaneLogSnapshot(obj)
  }

  suspend fun ledgerItems(limit: Int = 200): List<LedgerItemSummary> {
    val body = getResponseBody("/api/ledger/items?limit=${limit.coerceIn(1, 500)}", adminToken = "")
    return parseLedgerItemSummaries(parseLedgerItemsArray(body))
  }

  suspend fun ledgerItem(uid: String): LedgerItemDetail {
    val obj = getJson("/api/ledger/item/${encodePath(uid)}", adminToken = "")
    val metaObj = obj.optJSONObject("meta") ?: JSONObject()
    val meta = LedgerItemMeta(
      uid = metaObj.optString("uid", uid),
      slug = metaObj.optString("slug", ""),
      title = metaObj.optString("title", ""),
      status = metaObj.optString("status", ""),
      releaseState = metaObj.optString("release_state", "draft"),
      createdUtc = metaObj.optString("created_utc", ""),
      updatedUtc = metaObj.optString("updated_utc", ""),
      executionPriority = metaObj.takeIntOrNull("execution_priority"),
      tags = parseStringList(metaObj.optJSONArray("tags")),
      changesets = parseStringList(metaObj.optJSONArray("changesets")),
      supersedesUid = metaObj.optString("supersedes_uid", ""),
      regressionOfUid = metaObj.optString("regression_of_uid", ""),
    )
    return LedgerItemDetail(
      uid = obj.optString("uid", uid),
      dir = obj.optString("dir", ""),
      meta = meta,
      pisMarkdown = obj.optString("pis_markdown", ""),
      notes = obj.optString("notes", ""),
      artifacts = parseLedgerArtifacts(obj.optJSONArray("artifacts")),
    )
  }

  suspend fun ledgerWorkflowStatus(uid: String, adminToken: String = ""): LedgerWorkflowStatus {
    val obj = getJson("/api/ledger/workflow/status?uid=${encode(uid)}", adminToken = adminToken)
    return LedgerWorkflowStatus(
      nextStatus = obj.optString("next_status", ""),
    )
  }

  suspend fun listOrchestrations(
    bindingType: String,
    bindingId: String,
    limit: Int = 20,
  ): List<OrchestrationWorkflowSummary> {
    val obj = getJson(
      "/api/agents/orchestration/list?binding_type=${encode(bindingType)}&binding_id=${encode(bindingId)}&limit=${limit.coerceIn(1, 200)}",
      adminToken = "",
    )
    return parseOrchestrationWorkflowSummaries(obj.optJSONArray("items"))
  }

  suspend fun orchestrationStatus(
    workflowId: String,
    eventsLimit: Int = 120,
  ): WorkflowStatus {
    val obj = getJson(
      "/api/agents/orchestration/status?workflow_id=${encode(workflowId)}&events_limit=${eventsLimit.coerceIn(1, 500)}",
      adminToken = "",
    )
    val state = obj.optJSONObject("state") ?: JSONObject()
    return WorkflowStatus(
      workflowId = obj.optString("workflow_id", workflowId),
      status = state.optString("status", ""),
      statusDetail = state.optString("status_detail", ""),
      turn = state.optString("turn", ""),
      activeRole = state.optString("active_role", ""),
      activeWaitReason = state.optString("active_wait_reason", ""),
      activeProgressNote = state.optString("active_progress_note", ""),
      lastMeaningfulProgressUtc = state.optString("last_meaningful_progress_utc", ""),
      lastMeaningfulProgressEvent = state.optString("last_meaningful_progress_event", ""),
      stuckReason = state.optString("stuck_reason", ""),
      iteration = state.optInt("iteration", 1),
      maxIterations = state.optInt("max_iterations", 4),
      phaseLabel = state.optString("phase_label", ""),
      objective = state.optString("objective", ""),
      verifierVerdict = state.optString("verifier_verdict", ""),
      lastError = state.optString("last_error", ""),
      intentValidationReport = state.optString("intent_validation_report", ""),
      events = parseWorkflowEvents(obj.optJSONArray("events")),
    )
  }

  suspend fun orchestrationConversation(
    workflowId: String,
    eventsLimit: Int = 160,
  ): List<WorkflowConversationItem> {
    val obj = getJson(
      "/api/agents/orchestration/conversation?workflow_id=${encode(workflowId)}&events_limit=${eventsLimit.coerceIn(1, 500)}",
      adminToken = "",
    )
    return parseWorkflowConversationItems(obj.optJSONArray("items"))
  }

  suspend fun startWorkflow(request: WorkflowStartRequest): WorkflowStartResponse {
    val options = JSONObject().put("execution_directive", request.executionDirective)
    request.maxIterations?.let { options.put("max_iterations", it) }
    val payload = JSONObject()
      .put("binding", JSONObject().put("type", request.bindingType).put("id", request.bindingId))
      .put("phase_label", request.phaseLabel)
      .put("objective", request.objective)
      .put("timeout_s", currentWorkflowTimeoutS())
      .put("options", options)
    val obj = postJson("/api/agents/orchestration/start", adminToken = "", payload = payload)
    val state = obj.optJSONObject("state") ?: JSONObject()
    return WorkflowStartResponse(
      workflowId = obj.optString("workflow_id", "").ifBlank {
        throw IllegalStateException("workflow start failed: missing workflow_id")
      },
      phaseLabel = state.optString("phase_label", ""),
      objective = state.optString("objective", ""),
      maxIterations = state.optInt("max_iterations", 0),
    )
  }

  suspend fun runWorkflow(
    workflowId: String,
    maxSteps: Int = 40,
  ) {
    val payload = JSONObject()
      .put("workflow_id", workflowId)
      .put("max_steps", maxSteps.coerceIn(1, 200))
      .put("timeout_s", currentWorkflowTimeoutS())
    postJson("/api/agents/orchestration/run", adminToken = "", payload = payload, callClient = workflowRunClient)
  }

  suspend fun cancelWorkflow(
    workflowId: String,
    reason: String = "",
  ) {
    val payload = JSONObject()
      .put("workflow_id", workflowId)
      .put("reason", reason)
    postJson("/api/agents/orchestration/cancel", adminToken = "", payload = payload)
  }

  suspend fun ledgerUpdateStatus(
    uid: String,
    status: String,
    adminToken: String,
    note: String = "",
  ): LedgerStatusUpdateResult {
    val payload = JSONObject()
      .put("status", status)
      .put("note", note)
    val obj = postJson("/api/ledger/item/${encodePath(uid)}/status", adminToken, payload)
    val nestedResult = obj.optJSONObject("result")
    return LedgerStatusUpdateResult(
      ok = obj.optBoolean("ok", nestedResult?.optBoolean("ok", true) ?: true),
      uid = obj.optString("uid", uid),
      status = obj.optString("status", nestedResult?.optString("status", status).orEmpty()),
      updatedUtc = obj.optString("updated_utc", nestedResult?.optString("updated_utc", "").orEmpty()),
      blocked = obj.optBoolean("blocked", false),
      reason = obj.optString("reason", ""),
    )
  }

  suspend fun ledgerCreateItem(
    adminToken: String,
    title: String,
    pisMarkdown: String,
    tags: List<String> = emptyList(),
  ): LedgerCreateResult {
    val payload = JSONObject()
      .put("title", title)
      .put("pis_markdown", pisMarkdown)
      .put("tags", JSONArray(tags))
    val obj = postJson("/api/ledger/item/create", adminToken, payload)
    return LedgerCreateResult(
      ok = obj.optBoolean("ok", false),
      uid = obj.optString("uid", ""),
      slug = obj.optString("slug", ""),
      dir = obj.optString("dir", ""),
      createdUtc = obj.optString("created_utc", ""),
      existing = obj.optBoolean("existing", false),
    )
  }

  suspend fun ideaNoteCreate(
    adminToken: String,
    title: String,
    ideaMarkdown: String,
    tags: List<String> = emptyList(),
    projectId: String = "causyl",
    workstreamType: String = "shared_capability",
    scopeBoundary: String = "cross-project",
  ): LedgerCreateResult {
    val payload = JSONObject()
      .put("title", title)
      .put("idea_markdown", ideaMarkdown)
      .put("tags", JSONArray(tags))
      .put("project_id", projectId)
      .put("workstream_type", workstreamType)
      .put("scope_boundary", scopeBoundary)
    val obj = postJson("/api/idea_notes/create", adminToken, payload)
    return LedgerCreateResult(
      ok = obj.optBoolean("ok", false),
      uid = obj.optString("uid", ""),
      slug = obj.optString("slug", ""),
      dir = obj.optString("dir", ""),
      createdUtc = obj.optString("created_utc", ""),
      existing = obj.optBoolean("existing", false),
    )
  }

  suspend fun mobileLedgerUpdateStatus(
    uid: String,
    status: String,
    note: String = "",
  ): LedgerStatusUpdateResult {
    val payload = JSONObject()
      .put("uid", uid)
      .put("status", status)
      .put("note", note)
    val obj = postJson("/api/mobile/backlog/status", adminToken = "", payload = payload)
    return LedgerStatusUpdateResult(
      ok = obj.optBoolean("ok", false),
      uid = obj.optString("uid", uid),
      status = obj.optString("status", status),
      updatedUtc = obj.optString("updated_utc", ""),
      blocked = obj.optBoolean("blocked", false),
      reason = obj.optString("reason", ""),
    )
  }

  suspend fun mobileBacklogPrioritize(uid: String): LedgerPriorityUpdateResult {
    val payload = JSONObject().put("uid", uid)
    val obj = postJson("/api/mobile/backlog/prioritize", adminToken = "", payload = payload)
    return LedgerPriorityUpdateResult(
      ok = obj.optBoolean("ok", false),
      uid = obj.optString("uid", uid),
      executionPriority = obj.takeIntOrNull("execution_priority"),
      updatedUtc = obj.optString("updated_utc", ""),
    )
  }

  suspend fun ledgerLog(adminToken: String, uid: String, jobId: String, lines: Int): LedgerLog {
    val obj = getJson(
      "/api/ledger/action/log?uid=${encode(uid)}&job_id=${encode(jobId)}&lines=$lines",
      adminToken,
    )
    val rows = mutableListOf<String>()
    val arr = obj.optJSONArray("lines") ?: JSONArray()
    for (i in 0 until arr.length()) {
      rows += arr.optString(i, "")
    }
    return LedgerLog(
      logPath = obj.optString("log_path", ""),
      lines = rows,
    )
  }

  suspend fun patchApplyText(
    adminToken: String,
    text: String,
    allowDirty: Boolean,
    forceOverwrite: Boolean,
    verifySyntax: Boolean,
    permitDeletions: Boolean,
    allowBinary: Boolean,
  ): String {
    val payload = JSONObject()
      .put("mode", "diff")
      .put("text", text)
      .put("allow_dirty", allowDirty)
      .put("force_overwrite", forceOverwrite)
      .put("verify_syntax", verifySyntax)
      .put("permit_deletions", permitDeletions)
      .put("allow_binary", allowBinary)
    val obj = postJson("/api/admin/patch/apply", adminToken, payload)
    return buildPatchResult(obj)
  }

  suspend fun patchInspectUpload(adminToken: String, filename: String, bytes: ByteArray): PatchInspectResult {
    val obj = uploadBytes("/api/admin/patch/inspect", adminToken, filename, bytes)
    return PatchInspectResult(summary = buildPatchInspectSummary(obj))
  }

  suspend fun patchApplyUpload(
    adminToken: String,
    filename: String,
    bytes: ByteArray,
    allowDirty: Boolean,
    forceOverwrite: Boolean,
    verifySyntax: Boolean,
    allowBinary: Boolean,
  ): String {
    val qs = buildList {
      add("allow_dirty=${if (allowDirty) "1" else "0"}")
      add("force_overwrite=${if (forceOverwrite) "1" else "0"}")
      add("verify_syntax=${if (verifySyntax) "1" else "0"}")
      add("allow_binary=${if (allowBinary) "1" else "0"}")
    }.joinToString("&")
    val obj = uploadBytes("/api/admin/patch/apply?$qs", adminToken, filename, bytes)
    return buildPatchResult(obj)
  }

  suspend fun fsList(adminToken: String, path: String, includeHidden: Boolean): Pair<String, List<FsEntry>> {
    val obj = getJson(
      "/api/admin/fs/list?path=${encode(path)}&include_hidden=${if (includeHidden) "1" else "0"}",
      adminToken,
    )
    return obj.optString("dir", "") to parseFsEntries(obj.optJSONArray("entries"))
  }

  suspend fun fsRead(adminToken: String, path: String): FsReadResult {
    val obj = getJson("/api/admin/fs/read?path=${encode(path)}", adminToken)
    return FsReadResult(
      path = obj.optString("path", path),
      text = obj.optString("text", ""),
      truncated = obj.optBoolean("truncated", false),
    )
  }

  suspend fun fsDownload(adminToken: String, path: String): DownloadPayload {
    return downloadGet("/api/admin/fs/download?path=${encode(path)}", adminToken)
  }

  suspend fun fsReplace(adminToken: String, path: String, filename: String, bytes: ByteArray): String {
    val obj = uploadBytes("/api/admin/fs/replace?path=${encode(path)}", adminToken, filename, bytes)
    return "Replaced ${obj.optString("path", path)} (${obj.optInt("bytes", bytes.size)} bytes)"
  }

  suspend fun fsZip(adminToken: String, paths: List<String>, name: String, includeHidden: Boolean): DownloadPayload {
    val payload = JSONObject()
      .put("paths", JSONArray(paths))
      .put("name", name)
      .put("include_hidden", includeHidden)
    return downloadPost("/api/admin/fs/zip", adminToken, payload)
  }

  suspend fun restartServer(adminToken: String, force: Boolean = false): RestartServerResult {
    val payload = JSONObject().put("force", force)
    val req = Request.Builder()
      .url("${baseUrl()}/api/admin/server/restart")
      .post(payload.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
      .applyHeaders(adminToken)
      .build()
    return client.newCall(req).execute().use { response ->
      val body = response.body?.string().orEmpty()
      if (!response.isSuccessful && response.code != 409) {
        throw IllegalStateException(parseError(body, response.code))
      }
      parseRestartServerResult(JSONObject(body.ifBlank { "{}" }), response.code)
    }
  }

  suspend fun mobileAppUpdateManifest(channel: String, adminToken: String = ""): MobileAppUpdateManifest {
    val obj = getJson("/api/admin/mobile_apk/manifest?channel=${encode(channel)}", adminToken = adminToken)
    return MobileAppUpdateManifest(
      channel = obj.optString("channel", ""),
      packageName = obj.optString("package_name", ""),
      versionName = obj.optString("version_name", ""),
      versionCode = obj.optInt("version_code", 0),
      apkName = obj.optString("apk_name", ""),
      apkSizeBytes = obj.optLong("apk_size_bytes", 0L),
      apkSha256 = obj.optString("apk_sha256", ""),
      builtAt = obj.optString("built_at", ""),
      sourcePath = obj.optString("source_path", ""),
      downloadPath = obj.optString("download_path", ""),
    )
  }

  suspend fun buildMobileAppUpdate(channel: String, adminToken: String = ""): AdminJob {
    val payload = JSONObject().put("channel", channel)
    val obj = postJson("/api/admin/mobile_apk/build", adminToken = adminToken, payload = payload)
    return parseJob(obj.optJSONObject("job"))
  }

  suspend fun downloadMobileAppUpdate(channel: String, adminToken: String = ""): DownloadPayload {
    return downloadGet("/api/admin/mobile_apk/download?channel=${encode(channel)}", adminToken = adminToken)
  }

  private fun parseLedgerItemsArray(body: String): JSONArray {
    val trimmed = body.trim()
    if (trimmed.isBlank()) return JSONArray()
    return try {
      when (trimmed.first()) {
        '[' -> JSONArray(trimmed)
        '{' -> {
          val obj = JSONObject(trimmed)
          jsonArrayOrNull(obj, "items")
            ?: jsonArrayOrNull(obj, "entries")
            ?: jsonArrayOrNull(jsonObjectOrNull(obj, "data"), "items")
            ?: JSONArray()
        }
        else -> throw IllegalStateException("ledger_items failed: unsupported payload")
      }
    } catch (error: IllegalStateException) {
      throw error
    } catch (error: Throwable) {
      throw IllegalStateException("ledger_items failed: malformed payload", error)
    }
  }

  private fun parseLedgerItemSummaries(arr: JSONArray?): List<LedgerItemSummary> {
    val safeArr = arr ?: return emptyList()
    val out = mutableListOf<LedgerItemSummary>()
    for (i in 0 until safeArr.length()) {
      val row = jsonObjectOrNull(safeArr, i) ?: continue
      val meta = jsonObjectOrNull(row, "meta")
      out += LedgerItemSummary(
        uid = row.optString("uid", meta?.optString("uid", "").orEmpty()),
        slug = row.optString("slug", meta?.optString("slug", "").orEmpty()),
        title = row.optString("title", meta?.optString("title", "").orEmpty()),
        status = row.optString("status", meta?.optString("status", "").orEmpty()),
        releaseState = row.optString("release_state", meta?.optString("release_state", "").orEmpty()),
        updatedUtc = row.optString("updated_utc", meta?.optString("updated_utc", "").orEmpty()),
        executionPriority = row.takeIntOrNull("execution_priority") ?: meta?.takeIntOrNull("execution_priority"),
      )
    }
    return out
  }

  private fun parseMobileCockpitPayload(obj: JSONObject): MobileCockpitPayload {
    val selection = jsonObjectOrNull(obj, "selection")
    val pis = jsonObjectOrNull(obj, "pis")
    val contracts = jsonObjectOrNull(obj, "contracts")
    val queue = jsonObjectOrNull(obj, "execution_queue")
    val queueState = jsonObjectOrNull(queue, "state")
    val queueBatch = jsonObjectOrNull(queue, "batch_report")
    val shell = jsonObjectOrNull(obj, "shell")
    val executor = jsonObjectOrNull(shell, "executor_identity")
    val archiveSnapshots = jsonObjectOrNull(obj, "archive_snapshots")
    val latestRepoSnapshot = jsonObjectOrNull(archiveSnapshots, "latest_repo_snapshot")
    val pisCounts = jsonObjectOrNull(pis, "counts")
    val economics = jsonObjectOrNull(obj, "economics")
    val tokenUsage = jsonObjectOrNull(economics, "token_usage")
    val campaigns = jsonObjectOrNull(obj, "campaigns")
    val chronicle = jsonObjectOrNull(obj, "chronicle")
    return MobileCockpitPayload(
      schemaVersion = obj.optString("schema_version", ""),
      utc = obj.optString("utc", ""),
      contracts = parseMobileCockpitContracts(contracts),
      selection = MobileCockpitSelection(
        selectedUids = parseStringList(jsonArrayOrNull(selection, "selected_uids")),
        primaryUid = selection?.optString("primary_uid", "").orEmpty(),
        selectedCount = selection?.optInt("selected_count", 0) ?: 0,
      ),
      pisItems = parseLedgerItemSummaries(jsonArrayOrNull(pis, "items")),
      pisCounts = MobileCockpitPisCounts(
        draft = pisCounts?.optInt("draft", 0) ?: 0,
        ready = pisCounts?.optInt("ready", 0) ?: 0,
        total = pisCounts?.optInt("total", 0) ?: 0,
      ),
      campaigns = MobileCockpitCampaigns(
        latest = parseMobileCockpitCampaign(jsonObjectOrNull(campaigns, "latest")),
      ),
      executionQueue = MobileCockpitExecutionQueue(
        status = queueState?.optString("status", "").orEmpty(),
        currentUid = queueState?.optString("current_uid", "").orEmpty(),
        runId = queueState?.optString("run_id", queueBatch?.optString("run_id", "").orEmpty()).orEmpty(),
        totalCount = queueBatch?.optInt("total_count", 0) ?: 0,
        workerAlive = queue?.optBoolean("worker_alive", false) ?: false,
      ),
      artifacts = parseMobileCockpitArtifacts(jsonObjectOrNull(obj, "artifacts")),
      continuation = parseMobileCockpitContinuation(jsonObjectOrNull(obj, "continuation")),
      runTriage = parseMobileCockpitRunTriage(jsonObjectOrNull(obj, "run_triage")),
      economics = MobileCockpitEconomics(
        status = economics?.optString("status", "").orEmpty(),
        totalTokens = tokenUsage?.optInt("total_tokens", 0) ?: 0,
        wasteWarning = economics?.optString("waste_warning", "").orEmpty(),
      ),
      archiveSnapshots = MobileCockpitArchiveSnapshots(
        status = archiveSnapshots?.optString("status", "").orEmpty(),
        latestRepoSnapshotId = latestRepoSnapshot?.optString("snapshot_id", "").orEmpty(),
        runArchiveCount = jsonArrayOrNull(archiveSnapshots, "run_archives")?.length() ?: 0,
      ),
      chronicle = MobileCockpitChronicle(
        status = chronicle?.optString("status", "").orEmpty(),
        entries = parseMobileCockpitChronicleEntries(jsonArrayOrNull(chronicle, "entries")),
      ),
      shell = MobileCockpitShell(
        status = shell?.optString("status", "").orEmpty(),
        warnings = parseStringList(jsonArrayOrNull(shell, "warnings")),
        fallbackActions = parseStringList(jsonArrayOrNull(shell, "fallback_actions")),
        campaignId = executor?.optString("campaign_id", "").orEmpty(),
        workflowId = executor?.optString("workflow_id", "").orEmpty(),
        queueRunId = executor?.optString("queue_run_id", "").orEmpty(),
      ),
      rawContracts = prettyJson(contracts),
      rawCampaigns = prettyJson(campaigns),
      rawExecutionQueue = prettyJson(queue),
      rawArtifacts = prettyJson(jsonObjectOrNull(obj, "artifacts")),
      rawContinuation = prettyJson(jsonObjectOrNull(obj, "continuation")),
      rawRunTriage = prettyJson(jsonObjectOrNull(obj, "run_triage")),
      rawEconomics = prettyJson(economics),
      rawArchiveSnapshots = prettyJson(archiveSnapshots),
      rawChronicle = prettyJson(chronicle),
      rawShell = prettyJson(shell),
    )
  }

  private fun parseRestartBlockers(arr: JSONArray?): List<RestartBlocker> {
    val out = mutableListOf<RestartBlocker>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      out += RestartBlocker(
        kind = o.optString("kind", ""),
        count = o.optInt("count", 0),
        summary = o.optString("summary", ""),
      )
    }
    return out
  }

  private fun parseRestartServerResult(obj: JSONObject, code: Int): RestartServerResult {
    val scheduler = jsonObjectOrNull(obj, "scheduler")
    val adminCommands = jsonObjectOrNull(obj, "admin_commands")
    val blockers = parseRestartBlockers(jsonArrayOrNull(obj, "blockers"))
    val blocked = code == 409 || !obj.optBoolean("safe_to_restart", true)
    return RestartServerResult(
      accepted = !blocked,
      blocked = blocked,
      message = obj.optString("message", if (blocked) "Restart blocked." else "Restart requested."),
      healthStatus = if (blocked) "blocked" else obj.optString("status", "restarting"),
      blockers = blockers,
      activeSchedulerJobLockCount = scheduler?.optInt("active_job_lock_count", 0) ?: 0,
      staleSchedulerJobLockCount = scheduler?.optInt("stale_job_lock_count", 0) ?: 0,
      activeAdminCommandJobCount = adminCommands?.optInt("active_job_count", 0) ?: 0,
      forceRequested = obj.optBoolean("force_requested", false),
    )
  }

  private fun parseMobileCockpitContracts(obj: JSONObject?): MobileCockpitContracts {
    val row = obj ?: JSONObject()
    val entityOwners = linkedMapOf<String, String>()
    val entityContracts = jsonObjectOrNull(row, "entity_contracts")
    entityContracts?.keys()?.forEach { key ->
      entityOwners[key] = jsonObjectOrNull(entityContracts, key)?.optString("owner", "").orEmpty()
    }
    return MobileCockpitContracts(
      schemaVersion = row.optString("schema_version", ""),
      contractOk = jsonObjectOrNull(row, "contract_check")?.optBoolean("ok", false) ?: false,
      sharedStatusLanguage = parseStringList(jsonArrayOrNull(row, "shared_status_language")),
      sharedActionLanguage = parseStringList(jsonArrayOrNull(row, "shared_action_language")),
      entityOwners = entityOwners,
    )
  }

  private fun parseMobileCockpitCampaign(obj: JSONObject?): MobileCockpitCampaign? {
    val row = obj ?: return null
    if (!row.has("campaign_id") && !row.has("launch_outcome")) return null
    return MobileCockpitCampaign(
      campaignId = row.optString("campaign_id", ""),
      launchOutcome = row.optString("launch_outcome", ""),
      queueRunId = row.optString("queue_run_id", ""),
      workflowId = row.optString("workflow_id", ""),
      createdUtc = row.optString("created_utc", ""),
    )
  }

  private fun parseMobileCockpitArtifacts(obj: JSONObject?): MobileCockpitArtifacts {
    val row = obj ?: JSONObject()
    val latestHandoff = jsonObjectOrNull(row, "latest_handoff")
    return MobileCockpitArtifacts(
      status = row.optString("status", ""),
      latestHandoff = latestHandoff?.let {
        MobileCockpitArtifactHandoff(
          name = it.optString("name", ""),
          previewText = it.optString("preview_text", ""),
          available = it.optBoolean("available", false),
          artifactPath = it.optString("artifact_path", ""),
        )
      },
    )
  }

  private fun parseMobileCockpitContinuation(obj: JSONObject?): MobileCockpitContinuation {
    val row = obj ?: JSONObject()
    val memoryCounts = jsonObjectOrNull(row, "memory_counts")
    val memoryRecords = jsonArrayOrNull(row, "memory_records")
    return MobileCockpitContinuation(
      status = row.optString("status", ""),
      reason = row.optString("reason", ""),
      summary = row.optString("summary", ""),
      handoffStatus = row.optString("handoff_status", ""),
      authoritativeSource = row.optString("authoritative_source", ""),
      packetStatus = row.optString("packet_status", ""),
      packetReason = row.optString("packet_reason", ""),
      packetId = row.optString("packet_id", ""),
      packetRevision = row.optInt("packet_revision", 0),
      packetCreatedUtc = row.optString("packet_created_utc", ""),
      memoryStatus = row.optString("memory_status", ""),
      memoryReason = row.optString("memory_reason", ""),
      activeMemoryCount = memoryCounts?.optInt("active", 0) ?: 0,
      expiredMemoryCount = memoryCounts?.optInt("expired", 0) ?: 0,
      withdrawnMemoryCount = memoryCounts?.optInt("withdrawn", 0) ?: 0,
      contradictedMemoryCount = memoryCounts?.optInt("contradicted", 0) ?: 0,
      failedPaths = parseStringList(jsonArrayOrNull(row, "failed_paths")),
      openQuestions = parseStringList(jsonArrayOrNull(row, "open_questions")),
      nextSteps = parseStringList(jsonArrayOrNull(row, "next_steps")),
      visibleCaveats = parseStringList(jsonArrayOrNull(row, "visible_caveats")),
      memoryRecords = buildList {
        if (memoryRecords != null) {
          for (i in 0 until memoryRecords.length()) {
            val item = jsonObjectOrNull(memoryRecords, i) ?: continue
            add(
              MobileCockpitContinuationMemoryRecord(
                summary = item.optString("summary", ""),
                status = item.optString("status", ""),
                reason = item.optString("reason", ""),
                expiresAtUtc = item.optString("expires_at_utc", ""),
              ),
            )
          }
        }
      },
    )
  }

  private fun parseMobileCockpitRunTriage(obj: JSONObject?): MobileCockpitRunTriage {
    val row = obj ?: JSONObject()
    val failureCase = jsonObjectOrNull(row, "failure_case")
    val workflow = jsonObjectOrNull(row, "workflow")
    return MobileCockpitRunTriage(
      status = row.optString("status", ""),
      summary = row.optString("summary", ""),
      failureClass = failureCase?.optString("failure_class", "").orEmpty(),
      actions = parseStringList(jsonArrayOrNull(failureCase, "actions")),
      workflowId = workflow?.optString("workflow_id", "").orEmpty(),
    )
  }

  private fun parseMobileCockpitChronicleEntries(arr: JSONArray?): List<MobileCockpitChronicleEntry> {
    if (arr == null) return emptyList()
    val out = mutableListOf<MobileCockpitChronicleEntry>()
    for (i in 0 until arr.length()) {
      val row = jsonObjectOrNull(arr, i) ?: continue
      out += MobileCockpitChronicleEntry(
        entryId = row.optString("entry_id", ""),
        kind = row.optString("kind", ""),
        summary = row.optString("summary", ""),
        utc = row.optString("utc", ""),
        source = row.optString("source", ""),
      )
    }
    return out
  }

  private fun jsonObjectOrNull(obj: JSONObject?, key: String): JSONObject? {
    if (obj == null || !obj.has(key) || obj.isNull(key)) return null
    return runCatching { obj.getJSONObject(key) }.getOrNull()
  }

  private fun jsonArrayOrNull(obj: JSONObject?, key: String): JSONArray? {
    if (obj == null || !obj.has(key) || obj.isNull(key)) return null
    return runCatching { obj.getJSONArray(key) }.getOrNull()
  }

  private fun jsonObjectOrNull(arr: JSONArray?, index: Int): JSONObject? {
    if (arr == null || index < 0 || index >= arr.length()) return null
    return runCatching { arr.getJSONObject(index) }.getOrNull()
  }

  private fun prettyJson(obj: JSONObject?): String {
    return obj?.toString(2).orEmpty()
  }

  private fun parseOperatorSnapshot(obj: JSONObject): OperatorSnapshot {
    return OperatorSnapshot(
      operatorState = obj.optString("operator_state", ""),
      activeTasks = parseOperatorSnapshotTasks(obj.optJSONArray("active_tasks")),
      completedTasks = parseOperatorSnapshotTasks(obj.optJSONArray("completed_tasks")),
      freshness = parseOperatorSnapshotFreshness(obj.optJSONObject("freshness")),
      summary = parseOperatorSnapshotSummary(obj.optJSONObject("operator_summary")),
      commandCenter = parseOperatorSnapshotCommandCenter(obj.optJSONObject("command_center")),
      workItems = parseOperatorSnapshotWorkItems(obj.optJSONArray("work_items")),
    )
  }

  private fun parsePlaneLogSnapshot(obj: JSONObject): PlaneLogSnapshot {
    val runtimeControls = obj.optJSONObject("runtime_controls")
    return PlaneLogSnapshot(
      fetchedUtc = obj.optString("utc", ""),
      dashboard = parsePlaneLogDashboard(obj.optJSONObject("dashboard")),
      triage = parsePlaneLogTriage(obj.optJSONObject("error_triage")),
      watchdog = parsePlaneLogWatchdog(obj.optJSONObject("watchdog")),
      recentControls = parsePlaneLogControlEvents(runtimeControls?.optJSONArray("recent_controls")),
    )
  }

  private fun parsePlaneLogDashboard(obj: JSONObject?): PlaneLogDashboard {
    val row = obj ?: JSONObject()
    val statusLanguage = row.optJSONObject("status_language")
    return PlaneLogDashboard(
      operatorState = row.optString("operator_state", ""),
      headline = statusLanguage?.optString("headline", "").orEmpty(),
      activeTaskCount = row.optInt("active_task_count", 0),
      openLoopCount = row.optInt("open_loop_count", 0),
      queueStatus = row.optString("queue_status", ""),
      queueStatusDetail = row.optString("queue_status_detail", ""),
      queueActive = row.optBoolean("queue_active", false),
      workflowCount = row.optInt("workflow_count", 0),
      schedulerRunning = row.optBoolean("scheduler_running", false),
      schedulerLastTickUtc = row.optString("scheduler_last_tick_utc", ""),
      scheduledJobCount = row.optInt("scheduled_job_count", 0),
      backendState = row.optJSONObject("backend_projection")?.optString("state", "").orEmpty(),
    )
  }

  private fun parsePlaneLogControlEvents(arr: JSONArray?): List<PlaneLogControlEvent> {
    if (arr == null) return emptyList()
    val items = mutableListOf<PlaneLogControlEvent>()
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      val confirmation = row.optJSONObject("confirmation")
      val invocation = row.optJSONObject("invocation")
      items += PlaneLogControlEvent(
        controlRequestId = row.optString("control_request_id", ""),
        actionId = row.optString("action_id", ""),
        requestedUtc = row.optString("requested_utc", ""),
        writer = row.optString("writer", ""),
        confirmationState = confirmation?.optString("state", "").orEmpty(),
        confirmationSummary = confirmation?.optString("summary", "").orEmpty(),
        durableSignal = confirmation?.optString("durable_signal", "").orEmpty(),
        invocationOk = invocation?.optBoolean("ok"),
      )
    }
    return items
  }

  private fun parsePlaneLogTriage(obj: JSONObject?): PlaneLogTriage {
    val row = obj ?: JSONObject()
    return PlaneLogTriage(
      state = row.optString("state", ""),
      headline = row.optString("headline", ""),
      conflictCount = row.optInt("conflict_count", 0),
      gapCount = row.optInt("gap_count", 0),
      nextSteps = parsePlaneLogNextSteps(row.optJSONArray("next_steps")),
    )
  }

  private fun parsePlaneLogWatchdog(obj: JSONObject?): PlaneLogWatchdog {
    val row = obj ?: JSONObject()
    val timerStatus = row.optJSONObject("timer_status")
    val units = timerStatus?.optJSONObject("units")
    val auditTimer = units?.optJSONObject("audit_timer")
    val selfhealTimer = units?.optJSONObject("selfheal_timer")
    val latest = row.optJSONObject("latest")
    val auditLatest = latest?.optJSONObject("audit")
    val selfhealLatest = latest?.optJSONObject("selfheal")
    return PlaneLogWatchdog(
      auditTimerActive = auditTimer?.optString("active_state", "").equals("active", ignoreCase = true),
      selfhealTimerActive = selfhealTimer?.optString("active_state", "").equals("active", ignoreCase = true),
      auditTimerState = buildList {
        auditTimer?.optString("active_state", "")?.takeIf { it.isNotBlank() }?.let { add(it) }
        auditTimer?.optString("unit_file_state", "")?.takeIf { it.isNotBlank() }?.let { add(it) }
      }.joinToString(" / "),
      selfhealTimerState = buildList {
        selfhealTimer?.optString("active_state", "")?.takeIf { it.isNotBlank() }?.let { add(it) }
        selfhealTimer?.optString("unit_file_state", "")?.takeIf { it.isNotBlank() }?.let { add(it) }
      }.joinToString(" / "),
      auditLatestUtc = auditLatest?.optString("utc", "").orEmpty(),
      selfhealLatestUtc = selfhealLatest?.optString("utc", "").orEmpty(),
      auditLatestHealth = auditLatest?.optJSONObject("health")?.optString("health", "").orEmpty(),
      selfhealLatestHealth = selfhealLatest?.optJSONObject("health")?.optString("health", "").orEmpty(),
    )
  }

  private fun parsePlaneLogNextSteps(arr: JSONArray?): List<String> {
    if (arr == null) return emptyList()
    val items = mutableListOf<String>()
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      val summary = row.optString("summary", "").trim()
      if (summary.isNotEmpty()) items += summary
    }
    return items
  }

  private fun parseOperatorSnapshotTasks(arr: JSONArray?): List<OperatorSnapshotTask> {
    if (arr == null) return emptyList()
    val items = mutableListOf<OperatorSnapshotTask>()
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      val nextDecision = row.optJSONObject("next_decision")
      items += OperatorSnapshotTask(
        taskId = row.optString("task_id", ""),
        title = row.optString("title", ""),
        status = row.optString("status", ""),
        waitingReason = row.optString("waiting_reason", ""),
        nextExpectedAction = row.optString("next_expected_action", ""),
        nextDecisionSummary = nextDecision?.optString("summary", "").orEmpty()
          .ifBlank { nextDecision?.optString("kind", "").orEmpty() },
        evidenceBackedCompletion = row.optBoolean("evidence_backed_completion", false),
        delegated = row.optBoolean("delegated", false),
        currentRunId = row.optString("current_run_id", ""),
        currentRunState = row.optString("current_run_state", ""),
        currentRunAttentionState = row.optString("current_run_attention_state", ""),
        runCheckpoint = parseOperatorSnapshotRunCheckpoint(row.optJSONObject("run_checkpoint")),
      )
    }
    return items
  }

  private fun parseOperatorSnapshotRunCheckpoint(obj: JSONObject?): OperatorSnapshotRunCheckpoint {
    val row = obj ?: JSONObject()
    return OperatorSnapshotRunCheckpoint(
      checkpointStatus = row.optString("checkpoint_status", ""),
      progressPercent = row.takeIntOrNull("progress_percent"),
      progressSummary = row.optString("progress_summary", ""),
    )
  }

  private fun parseOperatorSnapshotFreshness(obj: JSONObject?): OperatorSnapshotFreshness {
    val row = obj ?: JSONObject()
    return OperatorSnapshotFreshness(
      generatedAt = row.optString("generated_at", ""),
      freshnessSeconds = row.takeIntOrNull("freshness_seconds"),
      confidence = row.optString("confidence", ""),
      stale = row.optBoolean("stale", false),
      staleSources = parseStringList(row.optJSONArray("stale_sources")),
    )
  }

  private fun parseOperatorSnapshotSummary(obj: JSONObject?): OperatorSnapshotSummary {
    val row = obj ?: JSONObject()
    return OperatorSnapshotSummary(
      statusLanguage = parseOperatorSnapshotStatusLanguage(row.optJSONObject("status_language")),
      sections = parseOperatorSnapshotSections(row.optJSONArray("sections")),
      notifications = parseOperatorSnapshotNotifications(row.optJSONArray("notifications")),
    )
  }

  private fun parseOperatorSnapshotCommandCenter(obj: JSONObject?): OperatorSnapshotCommandCenter {
    val row = obj ?: JSONObject()
    val snapshotMeta = row.optJSONObject("snapshot_meta")
    return OperatorSnapshotCommandCenter(
      headline = row.optString("headline", ""),
      snapshotMeta = parseOperatorSnapshotFreshness(snapshotMeta),
      intakePrompt = row.optJSONObject("intake")?.optString("prompt", "").orEmpty(),
      needsAttention = parseOperatorSnapshotCommandCenterItems(
        row.optJSONObject("needs_attention")?.optJSONArray("items"),
      ),
      running = parseOperatorSnapshotCommandCenterItems(
        row.optJSONObject("running")?.optJSONArray("items"),
      ),
      scheduled = parseOperatorSnapshotCommandCenterItems(
        row.optJSONObject("scheduled")?.optJSONArray("items"),
      ),
      completedCount = row.optJSONObject("completed")?.optInt("count", 0) ?: 0,
    )
  }

  private fun parseOperatorSnapshotCommandCenterItems(arr: JSONArray?): List<OperatorSnapshotCommandCenterItem> {
    if (arr == null) return emptyList()
    val items = mutableListOf<OperatorSnapshotCommandCenterItem>()
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      val stage = row.optJSONObject("stage")
      val stageLabel = stage?.optString("label", "").orEmpty()
      items += OperatorSnapshotCommandCenterItem(
        taskId = row.optString("task_id", ""),
        title = row.optString("title", ""),
        state = row.optString("state", ""),
        reason = row.optString("reason", ""),
        nextAction = row.optString("next_action", ""),
        stageLabel = stageLabel,
        executionSurface = row.optString("execution_surface", ""),
        metadata = parseStringList(row.optJSONArray("metadata")),
        confidenceLabel = row.optJSONObject("confidence")?.optString("label", "").orEmpty(),
        recommended = row.optJSONObject("recommended_action")?.optBoolean("recommended", false) ?: false,
        riskLevel = row.optJSONObject("recommended_action")?.optString("risk_level", "").orEmpty(),
        consequence = row.optJSONObject("recommended_action")?.optString("consequence", "").orEmpty(),
        artifactCount = row.optJSONObject("artifact_preview")?.optInt("count", 0) ?: 0,
      )
    }
    return items
  }

  private fun parseOperatorSnapshotWorkItems(arr: JSONArray?): List<OperatorSnapshotWorkItem> {
    if (arr == null) return emptyList()
    val items = mutableListOf<OperatorSnapshotWorkItem>()
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      val nextAction = row.optJSONObject("next_action")
      val recommendedAction = row.optJSONObject("recommended_action")
      val confidence = row.optJSONObject("confidence")
      val timing = row.optJSONObject("timing")
      val retry = row.optJSONObject("retry")
      val details = row.optJSONObject("details")
      items += OperatorSnapshotWorkItem(
        taskId = row.optString("task_id", ""),
        stableTitle = row.optString("stable_title", ""),
        state = row.optString("state", ""),
        reason = row.optString("reason", ""),
        executionSurface = row.optString("execution_surface", ""),
        nextActionPrompt = nextAction?.optString("prompt", "").orEmpty(),
        nextActionType = nextAction?.optString("type", "").orEmpty(),
        nextActionBlocking = nextAction?.optBoolean("blocking", false) ?: false,
        recommendedActionPrompt = recommendedAction?.optString("prompt", "").orEmpty(),
        recommendedActionType = recommendedAction?.optString("type", "").orEmpty(),
        recommendedActionRisk = recommendedAction?.optString("risk_level", "").orEmpty(),
        recommendedActionConsequence = recommendedAction?.optString("consequence", "").orEmpty(),
        confidenceLabel = confidence?.optString("label", "").orEmpty(),
        confidenceWarn = confidence?.optBoolean("warn", false) ?: false,
        timingLabel = timing?.optString("interpreted_label", "").orEmpty(),
        retryLabel = retry?.optString("label", "").orEmpty(),
        displayDepth = row.optInt("display_depth", 0),
        artifacts = parseStringList(details?.optJSONArray("artifacts")),
      )
    }
    return items
  }

  private fun parseOperatorSnapshotStatusLanguage(obj: JSONObject?): OperatorSnapshotStatusLanguage {
    val row = obj ?: JSONObject()
    return OperatorSnapshotStatusLanguage(
      state = row.optString("state", ""),
      headline = row.optString("headline", ""),
      tone = row.optString("tone", ""),
      freshness = row.optString("freshness", ""),
      successClaimAllowed = row.optBoolean("success_claim_allowed", false),
    )
  }

  private fun parseOperatorSnapshotSections(arr: JSONArray?): List<OperatorSnapshotSection> {
    if (arr == null) return emptyList()
    val items = mutableListOf<OperatorSnapshotSection>()
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      items += OperatorSnapshotSection(
        sectionId = row.optString("section_id", ""),
        title = row.optString("title", ""),
        count = row.optInt("count", 0),
        items = parseOperatorSnapshotSectionItems(row.optJSONArray("items")),
      )
    }
    return items
  }

  private fun parseOperatorSnapshotSectionItems(arr: JSONArray?): List<OperatorSnapshotSectionItem> {
    if (arr == null) return emptyList()
    val items = mutableListOf<OperatorSnapshotSectionItem>()
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      items += OperatorSnapshotSectionItem(
        taskId = row.optString("task_id", ""),
        title = row.optString("title", ""),
        status = row.optString("status", ""),
        summary = row.optString("summary", ""),
        owner = row.optString("owner", ""),
        action = row.optString("action", ""),
        evidenceRef = row.optString("evidence_ref", ""),
      )
    }
    return items
  }

  private fun parseOperatorSnapshotNotifications(arr: JSONArray?): List<OperatorSnapshotNotification> {
    if (arr == null) return emptyList()
    val items = mutableListOf<OperatorSnapshotNotification>()
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      items += OperatorSnapshotNotification(
        kind = row.optString("kind", ""),
        severity = row.optString("severity", ""),
        headline = row.optString("headline", ""),
        message = row.optString("message", ""),
        completionClaimAllowed = row.optBoolean("completion_claim_allowed", false),
      )
    }
    return items
  }

  private suspend fun getResponseBody(path: String, adminToken: String): String {
    val req = Request.Builder()
      .url("${baseUrl()}$path")
      .get()
      .applyHeaders(adminToken)
      .build()
    return client.newCall(req).execute().use { response ->
      val body = response.body?.string().orEmpty()
      if (!response.isSuccessful) {
        throw IllegalStateException(parseError(body, response.code))
      }
      body
    }
  }

  private suspend fun getJson(path: String, adminToken: String): JSONObject {
    return JSONObject(getResponseBody(path, adminToken).ifBlank { "{}" })
  }

  private suspend fun postJson(
    path: String,
    adminToken: String,
    payload: JSONObject,
    callClient: OkHttpClient = client,
  ): JSONObject {
    val req = Request.Builder()
      .url("${baseUrl()}$path")
      .post(payload.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
      .applyHeaders(adminToken)
      .build()
    return callClient.newCall(req).execute().use { response ->
      val body = response.body?.string().orEmpty()
      if (!response.isSuccessful) {
        throw IllegalStateException(parseError(body, response.code))
      }
      JSONObject(body.ifBlank { "{}" })
    }
  }

  private suspend fun uploadBytes(path: String, adminToken: String, filename: String, bytes: ByteArray): JSONObject {
    val req = Request.Builder()
      .url("${baseUrl()}$path")
      .post(bytes.toRequestBody("application/octet-stream".toMediaType()))
      .applyHeaders(adminToken)
      .header("X-Filename", filename)
      .build()
    return client.newCall(req).execute().use { response ->
      val body = response.body?.string().orEmpty()
      if (!response.isSuccessful) {
        throw IllegalStateException(parseError(body, response.code))
      }
      JSONObject(body.ifBlank { "{}" })
    }
  }

  private suspend fun downloadGet(path: String, adminToken: String): DownloadPayload {
    val req = Request.Builder()
      .url("${baseUrl()}$path")
      .get()
      .applyHeaders(adminToken)
      .build()
    return executeDownload(req)
  }

  private suspend fun downloadPost(path: String, adminToken: String, payload: JSONObject): DownloadPayload {
    val req = Request.Builder()
      .url("${baseUrl()}$path")
      .post(payload.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
      .applyHeaders(adminToken)
      .build()
    return executeDownload(req)
  }

  private fun executeDownload(req: Request): DownloadPayload {
    return client.newCall(req).execute().use { response ->
      val body = response.body
      if (!response.isSuccessful) {
        val text = body?.string().orEmpty()
        throw IllegalStateException(parseError(text, response.code))
      }
      val filename = parseDownloadFilename(
        response.header("Content-Disposition"),
        req.url.encodedPath.substringAfterLast('/').ifBlank { "download.bin" },
      )
        ?: "download.bin"
      val mime = response.header("Content-Type") ?: "application/octet-stream"
      val tempFile = kotlin.io.path.createTempFile(prefix = "plane_download_", suffix = ".bin").toFile()
      try {
        body?.byteStream()?.use { input ->
          tempFile.outputStream().use { output ->
            input.copyTo(output)
          }
        } ?: tempFile.writeBytes(ByteArray(0))
      } catch (error: Throwable) {
        tempFile.delete()
        throw error
      }
      DownloadPayload(
        filename = filename,
        mimeType = mime,
        tempFilePath = tempFile.absolutePath,
      )
    }
  }

  private fun parseDownloadFilename(contentDisposition: String?, fallback: String): String? {
    val header = contentDisposition?.trim().orEmpty()
    if (header.isBlank()) return fallback
    Regex("""filename\*=UTF-8''([^;]+)""", RegexOption.IGNORE_CASE)
      .find(header)
      ?.groupValues
      ?.getOrNull(1)
      ?.let { encoded ->
        return runCatching { java.net.URLDecoder.decode(encoded, "UTF-8") }.getOrDefault(encoded)
      }
    Regex("""filename="?([^\";]+)"?""", RegexOption.IGNORE_CASE)
      .find(header)
      ?.groupValues
      ?.getOrNull(1)
      ?.trim()
      ?.takeIf { it.isNotBlank() }
      ?.let { return it }
    return fallback
  }

  private fun Request.Builder.applyHeaders(adminToken: String): Request.Builder {
    header("Accept", "application/json")
    if (authToken.isNotBlank()) header("Authorization", "Bearer $authToken")
    if (adminToken.isNotBlank()) header("X-Admin-Token", adminToken)
    return this
  }

  private fun parseArchiveItems(arr: JSONArray?): List<ArchiveItem> {
    val out = mutableListOf<ArchiveItem>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      out += ArchiveItem(
        name = o.optString("name", ""),
        bytes = o.optLong("bytes", 0L),
        mtime = o.optDouble("mtime", 0.0),
      )
    }
    return out
  }

  private fun parseCommitJournalEntries(arr: JSONArray?): List<CommitJournalEntry> {
    if (arr == null) return emptyList()
    return buildList {
      for (i in 0 until arr.length()) {
        val obj = arr.optJSONObject(i) ?: continue
        val author = obj.optJSONObject("author")
        val binding = author?.optJSONObject("binding")
        add(
          CommitJournalEntry(
            entryId = obj.optString("entry_id", ""),
            createdUtc = obj.optString("created_utc", ""),
            title = obj.optString("title", ""),
            outcome = obj.optString("outcome", ""),
            scopeHint = obj.optString("scope_hint", ""),
            authorRole = author?.optString("role", "") ?: "",
            authorSource = author?.optString("source", "") ?: "",
            bindingType = binding?.optString("type", "") ?: "",
            bindingId = binding?.optString("id", "") ?: "",
            summaryBullets = parseStringList(obj.optJSONArray("summary_bullets")),
            files = parseStringList(obj.optJSONArray("files")),
            validation = parseStringList(obj.optJSONArray("validation")),
            notes = parseStringList(obj.optJSONArray("notes")),
          ),
        )
      }
    }
  }

  private fun parseManifestJobs(arr: JSONArray?): List<ManifestJob> {
    val out = mutableListOf<ManifestJob>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      out += ManifestJob(
        jobId = o.optString("job_id", ""),
        state = o.optString("state", ""),
        pid = o.optInt("pid").takeIf { it > 0 },
        reportDir = o.optString("report_dir", ""),
        manifest = o.optString("manifest", ""),
      )
    }
    return out
  }

  private fun parseSystemRuns(arr: JSONArray?): List<SystemRun> {
    val out = mutableListOf<SystemRun>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      out += SystemRun(
        runKey = o.optString("run_key", ""),
        displayId = o.optString("display_id", ""),
        displaySlug = o.optString("display_slug", ""),
        displaySuffix = o.optString("display_suffix", ""),
        runDir = o.optString("run_dir", ""),
        state = o.optString("state", ""),
        pid = if (o.has("pid") && !o.isNull("pid")) o.optInt("pid") else null,
        gen = if (o.has("gen") && !o.isNull("gen")) o.optInt("gen") else null,
        heartbeatAgeSec = if (o.has("heartbeat_age_s") && !o.isNull("heartbeat_age_s")) o.optDouble("heartbeat_age_s") else null,
        source = o.optString("source", ""),
        startedAtUtc = o.optString("started_at_utc", ""),
      )
    }
    return out
  }

  private fun parseSystemProcesses(arr: JSONArray?): List<SystemProcess> {
    val out = mutableListOf<SystemProcess>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      out += SystemProcess(
        pid = o.optInt("pid", 0),
        ppid = o.optInt("ppid", 0),
        pgid = o.optInt("pgid", 0),
        stat = o.optString("stat", ""),
        elapsed = o.optString("elapsed", ""),
        pcpu = o.optDouble("pcpu", 0.0),
        rssKb = o.optInt("rss_kb", 0),
        threads = o.optInt("threads", 0),
        args = o.optString("args", ""),
        matchesSimulation = o.optBoolean("matches_simulation", false),
        processKind = o.optString("process_kind", ""),
        displayId = o.optString("display_id", ""),
        displaySlug = o.optString("display_slug", ""),
        displaySuffix = o.optString("display_suffix", ""),
        launchTargetModule = o.optString("launch_target_module", ""),
        jobId = o.optString("job_id", ""),
        reportDir = o.optString("report_dir", ""),
      )
    }
    return out
  }

  private fun parseRunSummaries(arr: JSONArray?): List<RunSummary> {
    val out = mutableListOf<RunSummary>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      out += RunSummary(
        runKey = o.optString("run_key", ""),
        displayId = o.optString("display_id", ""),
        displaySlug = o.optString("display_slug", ""),
        displaySuffix = o.optString("display_suffix", ""),
        liveState = o.optString("live_state", ""),
        status = o.optString("status", ""),
        determinism = o.optString("determinism", ""),
        strictMode = o.optBoolean("strict_mode", false),
        currentGeneration = o.takeIntOrNull("current_generation"),
        targetGeneration = o.takeIntOrNull("target_generation"),
        epi = o.takeDoubleOrNull("epi"),
        av = o.takeDoubleOrNull("av"),
        lastVerifiedGeneration = o.takeIntOrNull("last_verified_generation"),
        parentRunKey = o.optString("parent_run_key", ""),
        notesExcerpt = o.optString("notes_excerpt", ""),
      )
    }
    return out
  }

  private fun parseScheduledJobSummaries(arr: JSONArray?): List<ScheduledJobSummary> {
    val out = mutableListOf<ScheduledJobSummary>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      out += parseScheduledJobSummary(row)
    }
    return out
  }

  private fun parseScheduledJobSummary(obj: JSONObject?): ScheduledJobSummary {
    val row = obj ?: JSONObject()
    return ScheduledJobSummary(
      jobId = row.optString("job_id", ""),
      jobKind = row.optString("job_kind", ""),
      enabled = row.optBoolean("enabled", false),
      role = row.optString("role", ""),
      runtimeRole = row.optString("runtime_role", ""),
      displayTitle = row.optString("display_title", ""),
      binding = parseScheduledJobBinding(row.optJSONObject("binding")),
      dependsOn = parseStringList(row.optJSONArray("depends_on")),
      schedule = parseScheduledJobSchedule(row.optJSONObject("schedule")),
      runtime = parseScheduledJobRuntime(row.optJSONObject("runtime")),
      loop = parseScheduledJobLoop(row.optJSONObject("loop")),
      prompt = row.optString("prompt", ""),
      promptPreview = row.optString("prompt_preview", ""),
      contextRefs = parseStringList(row.optJSONArray("context_refs")),
      createdUtc = row.optString("created_utc", ""),
      updatedUtc = row.optString("updated_utc", ""),
      state = parseScheduledJobState(row.optJSONObject("state")),
      due = parseScheduledJobDue(row.optJSONObject("due")),
      schedulerProjectionV1 = parseScheduledJobProjectionV1(row.optJSONObject("scheduler_projection_v1")),
      schedulerStatus = row.optString("scheduler_status", ""),
      schedulerStatusReason = row.optString("scheduler_status_reason", ""),
      schedulerTruthDegraded = row.optBoolean("scheduler_truth_degraded", false),
      schedulerStatusSource = row.optString("scheduler_status_source", ""),
    )
  }

  private fun parseScheduledJobProjectionV1(obj: JSONObject?): ScheduledJobProjectionV1? {
    val row = obj ?: return null
    return ScheduledJobProjectionV1(
      schemaVersion = row.optString("schema_version", ""),
      projectedStatus = row.optString("projected_status", ""),
      projectionReason = row.optString("projection_reason", ""),
      canonicalState = row.optString("canonical_state", ""),
      dueReason = row.optString("due_reason", ""),
      dueEligible = row.optBoolean("due_eligible", false),
      nextCandidateBucket = row.optString("next_candidate_bucket", ""),
      nextDueUtc = row.optString("next_due_utc", ""),
      durableConflict = row.optBoolean("durable_conflict", false),
      ambiguityReasons = parseStringList(row.optJSONArray("ambiguity_reasons")),
    )
  }

  private fun parseScheduledJobBinding(obj: JSONObject?): ScheduledJobBinding {
    val row = obj ?: JSONObject()
    return ScheduledJobBinding(
      type = row.optString("type", ""),
      id = row.optString("id", ""),
    )
  }

  private fun parseScheduledJobSchedule(obj: JSONObject?): ScheduledJobSchedule {
    val row = obj ?: JSONObject()
    return ScheduledJobSchedule(
      mode = row.optString("mode", ""),
      runAtUtc = row.optString("run_at", ""),
      timeOfDay = row.optString("time_of_day", ""),
      timezone = row.optString("timezone", ""),
    )
  }

  private fun parseScheduledJobDue(obj: JSONObject?): ScheduledJobDue {
    val row = obj ?: JSONObject()
    return ScheduledJobDue(
      eligible = row.optBoolean("eligible", false),
      reason = row.optString("reason", ""),
      slotKey = row.optString("slot_key", ""),
      dueUtc = row.optString("due_utc", ""),
    )
  }

  private fun parseScheduledJobState(obj: JSONObject?): ScheduledJobState {
    val row = obj ?: JSONObject()
    return ScheduledJobState(
      activeRunId = row.optString("active_run_id", ""),
      activeStartedUtc = row.optString("active_started_utc", ""),
      lastRunId = row.optString("last_run_id", ""),
      lastStartedUtc = row.optString("last_started_utc", ""),
      lastFinishedUtc = row.optString("last_finished_utc", ""),
      lastStatus = row.optString("last_status", ""),
      lastTrigger = row.optString("last_trigger", ""),
      lastReceiptPath = row.optString("last_receipt_path", ""),
      lastReportPath = row.optString("last_report_path", ""),
      lastSlotKey = row.optString("last_slot_key", ""),
      updatedUtc = row.optString("updated_utc", ""),
      activeClaimPresent = row.optBoolean("active_claim_present", false),
      activeClaimStale = row.optBoolean("active_claim_stale", false),
      activeClaimReason = row.optString("active_claim_reason", ""),
      activeClaimHeartbeatUtc = row.optString("active_claim_heartbeat_utc", ""),
      activeClaimAgeSec = row.takeDoubleOrNull("active_claim_age_s"),
    )
  }

  private fun parseScheduledJobRuntime(obj: JSONObject?): ScheduledJobRuntime {
    val row = obj ?: JSONObject()
    return ScheduledJobRuntime(
      agentTech = row.optString("agent_tech", ""),
      model = row.optString("model", ""),
    )
  }

  private fun parseScheduledJobLoop(obj: JSONObject?): ScheduledJobLoop {
    val row = obj ?: JSONObject()
    return ScheduledJobLoop(
      maxIterations = row.optInt("max_iterations", 0),
    )
  }

  private fun parseScheduledJobReceipts(arr: JSONArray?): List<ScheduledJobReceipt> {
    val out = mutableListOf<ScheduledJobReceipt>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      out += ScheduledJobReceipt(
        runId = row.optString("run_id", ""),
        status = row.optString("status", ""),
        startedUtc = row.optString("started_utc", ""),
        finishedUtc = row.optString("finished_utc", ""),
        trigger = row.optString("trigger", ""),
        slotKey = row.optString("slot_key", ""),
        specArchive = row.optString("spec_archive", ""),
        error = row.optString("error", ""),
      )
    }
    return out
  }

  private fun parseSchedulerServiceStatus(obj: JSONObject?): SchedulerServiceStatus {
    val row = obj ?: JSONObject()
    return SchedulerServiceStatus(
      running = row.optBoolean("running", false),
      intervalSeconds = row.optInt("interval_s", 0),
      startedUtc = row.optString("started_utc", ""),
      lastTickUtc = row.optString("last_tick_utc", ""),
      lastError = row.optString("last_error", ""),
      repoRoot = row.optString("repo_root", ""),
      paused = row.optBoolean("paused", false),
    )
  }

  private fun parseSchedulerMonitoringServiceStatus(obj: JSONObject?): SchedulerMonitoringServiceStatus {
    val row = obj ?: JSONObject()
    return SchedulerMonitoringServiceStatus(
      running = row.optBoolean("running", false),
      intervalSeconds = row.optInt("interval_s", 0),
      startedUtc = row.optString("started_utc", ""),
      lastReceiptUtc = row.optString("last_receipt_utc", ""),
      lastError = row.optString("last_error", ""),
      repoRoot = row.optString("repo_root", ""),
      latestReceipt = parseSchedulerMonitoringReceipt(row.optJSONObject("latest_receipt")),
    )
  }

  private fun parseSchedulerMonitoringReceipt(obj: JSONObject?): SchedulerMonitoringReceipt {
    val row = obj ?: JSONObject()
    return SchedulerMonitoringReceipt(
      schemaVersion = row.optString("schema_version", "scheduler-monitoring-v1"),
      utc = row.optString("utc", ""),
      findingsCount = row.optInt("findings_count", 0),
      error = row.optString("error", ""),
      schedulerHealth = row.optString("scheduler_health", ""),
      schedulerHealthReason = row.optString("scheduler_health_reason", ""),
    )
  }

  private fun parseEpiAvPoints(arr: JSONArray?): List<EpiAvPoint> {
    val out = mutableListOf<EpiAvPoint>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      val generation = o.takeIntOrNull("generation") ?: continue
      out += EpiAvPoint(
        generation = generation,
        epi = o.takeDoubleOrNull("epi"),
        av = o.takeDoubleOrNull("av"),
      )
    }
    return out
  }

  private fun parseBuilderHarnesses(arr: JSONArray?): List<BuilderHarness> {
    val out = mutableListOf<BuilderHarness>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      out += BuilderHarness(
        name = o.optString("name", ""),
        module = o.optString("module", ""),
        displayId = o.optString("display_id", ""),
        displaySlug = o.optString("display_slug", ""),
        displaySuffix = o.optString("display_suffix", ""),
        harnessPath = o.optString("harness_path", ""),
        registryPath = o.optString("registry_path", ""),
        registryName = o.optString("registry_name", ""),
        description = o.optString("description", ""),
        seedDefault = o.optInt("seed_default", 1),
        mtimeUtc = o.optString("mtime_utc", ""),
        sizeBytes = o.optInt("size_bytes", 0),
        runCommandManaged = o.optString("run_command_managed", ""),
      )
    }
    return out
  }

  private fun parseBuilderEntrypoints(arr: JSONArray?): List<BuilderEntrypoint> {
    val out = mutableListOf<BuilderEntrypoint>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      out += BuilderEntrypoint(
        module = o.optString("module", ""),
        label = o.optString("label", o.optString("module", "")),
      )
    }
    return out
  }

  private fun parseBuilderRegistries(arr: JSONArray?): List<BuilderRegistry> {
    val out = mutableListOf<BuilderRegistry>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      out += BuilderRegistry(
        path = o.optString("path", ""),
        uid = o.optString("uid", ""),
        displayId = o.optString("display_id", o.optString("path", "")),
        harnessExists = o.optBoolean("harness_exists", false),
        harnessPath = o.optString("harness_path", ""),
        mtimeUtc = o.optString("mtime_utc", ""),
        sizeBytes = o.optInt("size_bytes", 0),
      )
    }
    return out
  }

  private fun parseBuilderTemplates(arr: JSONArray?): List<BuilderTemplate> {
    val out = mutableListOf<BuilderTemplate>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      out += BuilderTemplate(
        name = o.optString("name", ""),
        path = o.optString("path", ""),
        mtimeUtc = o.optString("mtime_utc", ""),
        sizeBytes = o.optInt("size_bytes", 0),
      )
    }
    return out
  }

  private fun parseBuilderRecentJobs(arr: JSONArray?): List<BuilderRecentJob> {
    val out = mutableListOf<BuilderRecentJob>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      out += BuilderRecentJob(
        jobId = o.optString("job_id", ""),
        displayId = o.optString("display_id", ""),
        displaySlug = o.optString("display_slug", ""),
        displaySuffix = o.optString("display_suffix", ""),
        state = o.optString("state", ""),
        pid = if (o.has("pid") && !o.isNull("pid")) o.optInt("pid") else null,
        pgid = if (o.has("pgid") && !o.isNull("pgid")) o.optInt("pgid") else null,
        reportDir = o.optString("report_dir", ""),
        launchTargetModule = o.optString("launch_target_module", ""),
        selectedTarget = o.optString("selected_target", ""),
        requestId = o.optString("request_id", ""),
        updatedAtUtc = o.optString("updated_at_utc", ""),
      )
    }
    return out
  }

  private fun parseBuilderJobStatus(obj: JSONObject): BuilderJobStatus {
    return BuilderJobStatus(
      jobId = obj.optString("job_id", ""),
      displayId = obj.optString("display_id", ""),
      displaySlug = obj.optString("display_slug", ""),
      displaySuffix = obj.optString("display_suffix", ""),
      lifecycle = obj.optString("lifecycle", ""),
      terminal = obj.optBoolean("terminal", false),
      state = obj.optString("state", ""),
      pid = if (obj.has("pid") && !obj.isNull("pid")) obj.optInt("pid") else null,
      pidAlive = obj.optBoolean("pid_alive", false),
      exitCode = if (obj.has("exit_code") && !obj.isNull("exit_code")) obj.optInt("exit_code") else null,
      failureCode = obj.optString("failure_code", ""),
      failureDetail = obj.optString("failure_detail", ""),
      reportDir = obj.optString("report_dir", ""),
      runPath = obj.optString("run_path", ""),
      logPath = obj.optString("log_path", ""),
      logTail = obj.optString("log_tail", ""),
      launchTargetModule = obj.optString("launch_target_module", ""),
      selectedTarget = obj.optString("selected_target", ""),
    )
  }

  private fun parseBuilderLoadedHarnessPayload(obj: JSONObject): BuilderLoadedHarnessPayload {
    val cliDefaults = obj.optJSONObject("cli_defaults")
    val axes = obj.optJSONArray("axes")
    val caseValues = mutableListOf<String>()
    if (axes != null) {
      for (i in 0 until axes.length()) {
        val axis = axes.optJSONObject(i) ?: continue
        val values = parseStringList(axis.optJSONArray("values"))
        if (axis.optString("name", "") == "case" || caseValues.isEmpty()) {
          caseValues.clear()
          caseValues += values
          if (axis.optString("name", "") == "case") break
        }
      }
    }
    return BuilderLoadedHarnessPayload(
      testId = obj.optString("test_id", ""),
      description = obj.optString("description", ""),
      intentQuestion = obj.optString("intent_question", ""),
      runsRoot = obj.optString("runs_root", ""),
      seeds = parseIntList(obj.optJSONArray("seeds")),
      caseValues = caseValues,
      defaultKnobs = parseStringList(obj.optJSONArray("default_knobs")),
      expectedArtifacts = parseStringList(obj.optJSONArray("expected_artifacts")),
      perf = obj.optBoolean("perf", false),
      enableProfiler = obj.optBoolean("enable_profiler", false),
      generations = cliDefaults?.takeIntOrNull("generations"),
      steps = cliDefaults?.takeIntOrNull("steps"),
      pop = cliDefaults?.takeIntOrNull("pop"),
      gridSize = cliDefaults?.takeIntOrNull("grid_size"),
      stride = cliDefaults?.takeIntOrNull("policy_tick_stride"),
      checkpointPeriodGen = cliDefaults?.takeIntOrNull("checkpoint_period_gen"),
      checkpointRotate = cliDefaults?.takeIntOrNull("checkpoint_rotate"),
    )
  }

  private fun buildBuilderHarnessTemplateJson(harness: BuilderLoadedHarnessPayload): JSONObject {
    val cliDefaults = JSONObject()
    harness.generations?.let { cliDefaults.put("generations", it) }
    harness.steps?.let { cliDefaults.put("steps", it) }
    harness.pop?.let { cliDefaults.put("pop", it) }
    harness.gridSize?.let { cliDefaults.put("grid_size", it) }
    harness.stride?.let { cliDefaults.put("policy_tick_stride", it) }
    harness.checkpointPeriodGen?.let { cliDefaults.put("checkpoint_period_gen", it) }
    harness.checkpointRotate?.let { cliDefaults.put("checkpoint_rotate", it) }
    return JSONObject()
      .put("test_id", harness.testId)
      .put("description", harness.description)
      .put("intent_question", harness.intentQuestion)
      .put("runs_root", harness.runsRoot)
      .put("seeds", JSONArray(harness.seeds))
      .put("axes", JSONArray().put(JSONObject().put("name", "case").put("values", JSONArray(harness.caseValues))))
      .put("default_knobs", JSONArray(harness.defaultKnobs))
      .put("expected_artifacts", JSONArray(harness.expectedArtifacts))
      .put("perf", harness.perf)
      .put("enable_profiler", harness.enableProfiler)
      .put("cli_defaults", cliDefaults)
  }

  private fun parseStringList(arr: JSONArray?): List<String> {
    val out = mutableListOf<String>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val value = arr.optString(i, "").trim()
      if (value.isNotBlank()) out += value
    }
    return out
  }

  private fun parseIntList(arr: JSONArray?): List<Int> {
    val out = mutableListOf<Int>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val value = arr.optInt(i, Int.MIN_VALUE)
      if (value != Int.MIN_VALUE) out += value
    }
    return out
  }

  private fun parseLedgerJobs(arr: JSONArray?): List<LedgerJob> {
    val out = mutableListOf<LedgerJob>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      out += LedgerJob(
        jobId = o.optString("job_id", ""),
        actionId = o.optString("action_id", ""),
        event = o.optString("event", ""),
        status = o.optString("status", ""),
        exitCode = if (o.has("exit_code")) o.optInt("exit_code") else null,
      )
    }
    return out
  }

  private fun parseLedgerArtifacts(arr: JSONArray?): List<LedgerArtifact> {
    val out = mutableListOf<LedgerArtifact>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      out += LedgerArtifact(
        relpath = row.optString("relpath", ""),
        bytes = row.optLong("bytes", 0L),
      )
    }
    return out
  }

  private fun parseOrchestrationWorkflowSummaries(arr: JSONArray?): List<OrchestrationWorkflowSummary> {
    val out = mutableListOf<OrchestrationWorkflowSummary>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      val binding = row.optJSONObject("binding") ?: JSONObject()
      out += OrchestrationWorkflowSummary(
        workflowId = row.optString("workflow_id", ""),
        phaseLabel = row.optString("phase_label", ""),
        status = row.optString("status", ""),
        updatedUtc = row.optString("updated_utc", ""),
        createdUtc = row.optString("created_utc", ""),
        bindingType = binding.optString("type", ""),
        bindingId = binding.optString("id", ""),
        turn = row.optString("turn", ""),
      )
    }
    return out
  }

  private fun parseWorkflowEvents(arr: JSONArray?): List<WorkflowEvent> {
    val out = mutableListOf<WorkflowEvent>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      val payload = row.optJSONObject("payload") ?: JSONObject()
      val payloadMap = linkedMapOf<String, String>()
      payload.keys().forEach { key ->
        payloadMap[key] = payload.opt(key)?.toString().orEmpty()
      }
      out += WorkflowEvent(
        utc = row.optString("utc", ""),
        event = row.optString("event", ""),
        payload = payloadMap,
      )
    }
    return out
  }

  private fun parseWorkflowConversationItems(arr: JSONArray?): List<WorkflowConversationItem> {
    val out = mutableListOf<WorkflowConversationItem>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val row = arr.optJSONObject(i) ?: continue
      out += WorkflowConversationItem(
        id = row.optString("id", ""),
        utc = row.optString("utc", ""),
        actor = row.optString("actor", ""),
        source = row.optString("source", ""),
        target = row.optString("target", ""),
        kind = row.optString("kind", ""),
        title = row.optString("title", ""),
        text = row.optString("text", ""),
        origin = row.optString("origin", ""),
      )
    }
    return out
  }

  private fun parseFsEntries(arr: JSONArray?): List<FsEntry> {
    val out = mutableListOf<FsEntry>()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
      val o = arr.optJSONObject(i) ?: continue
      out += FsEntry(
        name = o.optString("name", ""),
        path = o.optString("path", ""),
        isDir = o.optBoolean("is_dir", false),
        bytes = o.optLong("bytes", 0L),
        mtime = o.optDouble("mtime", 0.0),
      )
    }
    return out
  }

  private fun parseJob(obj: JSONObject?): AdminJob {
    val job = obj ?: JSONObject()
    return AdminJob(
      jobId = job.optString("job_id", ""),
      kind = job.optString("kind", ""),
      cmd = job.optString("cmd", ""),
      cwdRel = job.optString("cwd_rel", ""),
      running = job.optBoolean("running", false),
      killed = job.optBoolean("killed", false),
      returnCode = if (job.has("returncode") && !job.isNull("returncode")) job.optInt("returncode") else null,
      utcStarted = job.optString("utc_started", ""),
      utcFinished = job.optString("utc_finished", ""),
      durationSec = job.optDouble("duration_sec", 0.0),
      stdout = job.optString("stdout", ""),
      stderr = job.optString("stderr", ""),
      error = job.optString("error", ""),
    )
  }

  private fun buildPatchResult(obj: JSONObject): String {
    val sb = StringBuilder()
    sb.append("rc=").append(obj.optInt("returncode", 0)).append('\n')
    val stdout = obj.optString("stdout", "")
    val stderr = obj.optString("stderr", "")
    if (stdout.isNotBlank()) {
      sb.append(stdout.trimEnd()).append('\n')
    }
    if (stderr.isNotBlank()) {
      sb.append(stderr.trimEnd()).append('\n')
    }
    return sb.toString().trim()
  }

  private fun buildPatchInspectSummary(obj: JSONObject): String {
    return when (obj.optString("kind", "")) {
      "zip" -> {
        val lines = mutableListOf<String>()
        lines += "zip entries=${obj.optInt("entry_count", 0)}"
        val arr = obj.optJSONArray("entries") ?: JSONArray()
        val max = minOf(arr.length(), 200)
        for (i in 0 until max) {
          val o = arr.optJSONObject(i) ?: continue
          val path = o.optString("path", "")
          val bytes = o.optLong("bytes", 0L)
          val overwrite = if (o.optBoolean("will_overwrite", false)) " OVERWRITE" else ""
          val skip = o.optString("skip_reason", "").takeIf { it.isNotBlank() }?.let { " SKIP($it)" }.orEmpty()
          lines += "- $path $bytes$overwrite$skip"
        }
        if (arr.length() > max) lines += "... truncated preview"
        lines.joinToString("\n")
      }
      "diff" -> {
        val head = obj.optString("preview_head", "")
        "diff chars=${obj.optInt("chars", 0)} lines=${obj.optInt("lines", 0)}\n$head".trim()
      }
      else -> obj.toString(2)
    }
  }

  private fun parseError(body: String, code: Int): String {
    return runCatching {
      val obj = JSONObject(body)
      val message = obj.optString("message", "").trim()
      val error = obj.optString("error", "").trim()
      val blockers = obj.optJSONArray("blockers")
      val blockerSummary = buildString {
        if (blockers != null) {
          for (i in 0 until blockers.length()) {
            val row = blockers.optJSONObject(i) ?: continue
            val summary = row.optString("summary", "").trim().ifBlank { row.optString("kind", "").trim() }
            if (summary.isBlank()) continue
            if (isNotEmpty()) append("; ")
            append(summary)
          }
        }
      }
      listOf(message, error)
        .firstOrNull { it.isNotBlank() }
        ?.let { base -> if (blockerSummary.isNotBlank()) "$base: $blockerSummary" else base }
    }.getOrNull() ?: "HTTP $code"
  }

  private fun encode(value: String): String {
    return java.net.URLEncoder.encode(value, "UTF-8")
  }

  private fun encodePath(value: String): String {
    return value.split("/").joinToString("/") { encode(it) }
  }

  private fun JSONObject.takeIntOrNull(key: String): Int? {
    return if (has(key) && !isNull(key)) optInt(key) else null
  }

  private fun JSONObject.takeDoubleOrNull(key: String): Double? {
    return if (has(key) && !isNull(key)) optDouble(key) else null
  }
}
