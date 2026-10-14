package com.eiel.chat.data

data class ScheduledJobBinding(
  val type: String,
  val id: String,
)

data class ScheduledJobSchedule(
  val mode: String,
  val runAtUtc: String,
  val timeOfDay: String,
  val timezone: String,
)

data class ScheduledJobDue(
  val eligible: Boolean,
  val reason: String,
  val slotKey: String,
  val dueUtc: String,
)

data class ScheduledJobState(
  val activeRunId: String,
  val activeStartedUtc: String,
  val lastRunId: String,
  val lastStartedUtc: String,
  val lastFinishedUtc: String,
  val lastStatus: String,
  val lastTrigger: String,
  val lastReceiptPath: String,
  val lastReportPath: String,
  val lastSlotKey: String,
  val updatedUtc: String,
  val activeClaimPresent: Boolean = false,
  val activeClaimStale: Boolean = false,
  val activeClaimReason: String = "",
  val activeClaimHeartbeatUtc: String = "",
  val activeClaimAgeSec: Double? = null,
)

data class ScheduledJobRuntime(
  val agentTech: String,
  val model: String,
)

data class ScheduledJobLoop(
  val maxIterations: Int,
)

data class ScheduledJobProjectionV1(
  val schemaVersion: String = "",
  val projectedStatus: String = "",
  val projectionReason: String = "",
  val canonicalState: String = "",
  val dueReason: String = "",
  val dueEligible: Boolean = false,
  val nextCandidateBucket: String = "",
  val nextDueUtc: String = "",
  val durableConflict: Boolean = false,
  val ambiguityReasons: List<String> = emptyList(),
)

data class ScheduledJobSummary(
  val jobId: String,
  val jobKind: String,
  val enabled: Boolean,
  val role: String,
  val runtimeRole: String,
  val displayTitle: String,
  val binding: ScheduledJobBinding,
  val dependsOn: List<String>,
  val schedule: ScheduledJobSchedule,
  val runtime: ScheduledJobRuntime,
  val loop: ScheduledJobLoop,
  val prompt: String,
  val promptPreview: String,
  val contextRefs: List<String>,
  val createdUtc: String,
  val updatedUtc: String,
  val state: ScheduledJobState,
  val due: ScheduledJobDue,
  val schedulerProjectionV1: ScheduledJobProjectionV1? = null,
  val schedulerStatus: String = "",
  val schedulerStatusReason: String = "",
  val schedulerTruthDegraded: Boolean = false,
  val schedulerStatusSource: String = "",
)

data class ScheduledJobReceipt(
  val runId: String,
  val status: String,
  val startedUtc: String,
  val finishedUtc: String,
  val trigger: String,
  val slotKey: String,
  val specArchive: String,
  val error: String,
)

data class SchedulerServiceStatus(
  val running: Boolean,
  val intervalSeconds: Int,
  val startedUtc: String,
  val lastTickUtc: String,
  val lastError: String,
  val repoRoot: String,
  val paused: Boolean = false,
)

data class SchedulerMonitoringReceipt(
  val schemaVersion: String,
  val utc: String,
  val findingsCount: Int,
  val error: String,
  val schedulerHealth: String,
  val schedulerHealthReason: String,
)

data class SchedulerMonitoringServiceStatus(
  val running: Boolean,
  val intervalSeconds: Int,
  val startedUtc: String,
  val lastReceiptUtc: String,
  val lastError: String,
  val repoRoot: String,
  val latestReceipt: SchedulerMonitoringReceipt,
)

data class ScheduledJobsPayload(
  val fetchedUtc: String,
  val jobs: List<ScheduledJobSummary>,
  val scheduler: SchedulerServiceStatus,
  val watchdog: SchedulerMonitoringServiceStatus,
  val healer: SchedulerMonitoringServiceStatus,
)

data class SchedulerHealthPayload(
  val fetchedUtc: String,
  val scheduler: SchedulerServiceStatus,
  val watchdog: SchedulerMonitoringServiceStatus,
  val healer: SchedulerMonitoringServiceStatus,
)

data class ScheduledJobDetail(
  val fetchedUtc: String,
  val job: ScheduledJobSummary,
  val receipts: List<ScheduledJobReceipt>,
)

data class ScheduledJobRunNowResult(
  val ok: Boolean,
  val jobId: String,
  val runId: String,
  val status: String,
  val queued: Boolean,
  val reason: String,
  val requestedUtc: String,
)
