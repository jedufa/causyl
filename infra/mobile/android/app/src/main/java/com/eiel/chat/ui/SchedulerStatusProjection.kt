package com.eiel.chat.ui

import com.eiel.chat.data.SchedulerHealthPayload
import com.eiel.chat.data.SchedulerMonitoringServiceStatus
import com.eiel.chat.data.SchedulerServiceStatus
import com.eiel.chat.data.ScheduledJobProjectionV1
import com.eiel.chat.data.ScheduledJobSummary
import java.time.Duration
import java.time.Instant
import kotlin.math.max
import kotlin.math.min

enum class SchedulerServiceHealthAxis(
  val label: String,
) {
  HEALTHY("healthy"),
  DEGRADED("degraded"),
  CRITICAL("critical"),
  UNKNOWN("unknown"),
}

enum class SchedulerFreshnessAxis(
  val label: String,
) {
  FRESH("fresh"),
  STALE("stale"),
  UNKNOWN("unknown"),
}

enum class SchedulerProjectedAttentionRisk(
  val rank: Int,
  val title: String,
) {
  HIGH(0, "high"),
  MEDIUM(1, "medium"),
  LOW(2, "low"),
}

data class SchedulerAttentionItem(
  val jobId: String,
  val title: String,
  val reason: String,
  val risk: SchedulerProjectedAttentionRisk,
  val launchedAtLabel: String,
  val sortRank: Int = 0,
)

data class SchedulerRunningItem(
  val jobId: String,
  val title: String,
  val startedLabel: String,
  val status: String,
  val startedUtc: String,
)

data class SchedulerUpcomingItem(
  val jobId: String,
  val title: String,
  val dueLabel: String,
  val reason: String,
  val dueUtc: String,
)

private data class SchedulerUpcomingCandidate(
  val item: SchedulerUpcomingItem,
  val candidateBucket: String,
  val reasonCode: String,
)

data class SchedulerStatusProjection(
  val serviceHealth: SchedulerServiceHealthAxis,
  val serviceHealthMessage: String,
  val freshness: SchedulerFreshnessAxis,
  val freshnessAgeLabel: String,
  val freshnessAgeSec: Long?,
  val attentionItems: List<SchedulerAttentionItem>,
  val runningItems: List<SchedulerRunningItem>,
  val upcomingItems: List<SchedulerUpcomingItem>,
)

fun deriveSchedulerStatusProjection(
  jobs: List<ScheduledJobSummary>,
  scheduler: SchedulerServiceStatus,
  watchdog: SchedulerMonitoringServiceStatus?,
  healer: SchedulerMonitoringServiceStatus?,
  health: SchedulerHealthPayload?,
  jobsFetchedUtc: String,
  healthFetchedUtc: String,
  now: Instant,
  maxAttention: Int = 5,
  maxRunning: Int = 5,
  maxUpcoming: Int = 5,
): SchedulerStatusProjection {
  val schedulerSource = health?.scheduler ?: scheduler
  val watchdogSource = health?.watchdog ?: watchdog
  val healerSource = health?.healer ?: healer
  val service = deriveSchedulerServiceHealth(schedulerSource, watchdogSource, healerSource)
  val freshestUtc = maxParsedInstant(jobsFetchedUtc, healthFetchedUtc)
  val freshness = deriveFreshness(now, freshestUtc)

  val attentionRaw = mutableListOf<SchedulerAttentionItem>()
  val runningRaw = mutableListOf<SchedulerRunningItem>()
  val upcomingRaw = mutableListOf<SchedulerUpcomingCandidate>()

  jobs.forEach { job ->
    val projection = effectiveSchedulerProjection(job)
    val projectedStatus = projection.projectedStatus.lowercase()
    val projectedReason = projection.projectionReason.ifBlank { job.schedulerStatusReason.ifBlank { job.due.reason } }
    val dueUtc = projection.nextDueUtc.ifBlank { job.due.dueUtc }
    val dueInstant = parseTimestamp(dueUtc)
    val lastStatus = job.state.lastStatus.lowercase()
    val claimStale = job.state.activeClaimStale
    val isRunning = projectedStatus == "active"
    val isBlocked = projectedStatus == "blocked"
    val isFailed = projectedStatus == "failed"
    val isAmbiguous = projectedStatus == "ambiguous" || projection.durableConflict || job.schedulerTruthDegraded
    val needsAttention = isBlocked || isFailed || isAmbiguous || claimStale || !job.enabled
    val launchLabel = buildLaunchLabel(dueInstant, now)

    if (isRunning) {
      runningRaw += SchedulerRunningItem(
        jobId = job.jobId,
        title = job.displayTitle.ifBlank { job.jobId },
        startedLabel = elapsedLabel(parseTimestamp(job.state.activeStartedUtc), now),
        status = when {
          claimStale -> "active claim stale"
          isAmbiguous -> "active truth degraded"
          else -> "active"
        },
        startedUtc = job.state.activeStartedUtc,
      )
    }

    if (needsAttention) {
      val risk = when {
        isFailed || isAmbiguous || claimStale -> SchedulerProjectedAttentionRisk.HIGH
        isBlocked -> SchedulerProjectedAttentionRisk.HIGH
        !job.enabled -> SchedulerProjectedAttentionRisk.MEDIUM
        else -> SchedulerProjectedAttentionRisk.LOW
      }
      attentionRaw += SchedulerAttentionItem(
        jobId = job.jobId,
        title = job.displayTitle.ifBlank { job.jobId },
        reason = listOfNotNull(
          if (isFailed) "failed: ${projectedReason.ifBlank { lastStatus.ifBlank { "unknown" } }}" else null,
          if (claimStale) "active claim stale" else null,
          if (isBlocked) humanizeProjectionReason(projectedReason, fallbackPrefix = "blocked") else null,
          if (isAmbiguous) humanizeProjectionReason(projectedReason, fallbackPrefix = "truth degraded") else null,
          if (!job.enabled) "disabled" else null,
          if (!isFailed && !claimStale && !isBlocked && !isAmbiguous && projectedReason.isNotBlank()) {
            humanizeProjectionReason(projectedReason, fallbackPrefix = "")
          } else {
            null
          },
        ).joinToString(" · "),
        risk = risk,
        launchedAtLabel = launchLabel,
        sortRank = when {
          isFailed || claimStale || isAmbiguous -> 0
          isBlocked -> 1
          !job.enabled -> 2
          else -> 3
        },
      )
    }

    val isUpcomingCandidate = job.enabled &&
      projectedStatus in setOf("queued", "idle") &&
      projection.nextCandidateBucket in setOf("runnable_now", "scheduled_future")
    if (!isRunning && isUpcomingCandidate) {
      upcomingRaw += SchedulerUpcomingCandidate(
        item = SchedulerUpcomingItem(
          jobId = job.jobId,
          title = job.displayTitle.ifBlank { job.jobId },
          dueLabel = dueLabel(dueInstant, now),
          reason = humanizeUpcomingReason(projection),
          dueUtc = dueUtc,
        ),
        candidateBucket = projection.nextCandidateBucket,
        reasonCode = projection.dueReason.ifBlank { projection.projectionReason },
      )
    }
  }

  val attentionItems = rankAttention(attentionRaw).dedupeById().take(max(0, maxAttention))
  val runningItems = runningRaw
    .sortedByDescending { startedSortKey(it.startedUtc) }
    .dedupeById()
    .take(max(0, maxRunning))
  val upcomingItems = upcomingRaw
    .sortedWith(
      compareBy<SchedulerUpcomingCandidate>(
        { upcomingBucketPriority(it.candidateBucket) },
        { upcomingReasonPriority(it.reasonCode) },
        { upcomingSortKey(it.item.dueUtc) },
        { it.item.jobId },
      ),
    )
    .map { it.item }
    .dedupeById()
    .take(max(0, maxUpcoming))

  return SchedulerStatusProjection(
    serviceHealth = service.first,
    serviceHealthMessage = service.second,
    freshness = freshness.first,
    freshnessAgeLabel = freshness.second,
    freshnessAgeSec = freshness.third,
    attentionItems = attentionItems,
    runningItems = runningItems,
    upcomingItems = upcomingItems,
  )
}

private fun deriveSchedulerServiceHealth(
  scheduler: SchedulerServiceStatus,
  watchdog: SchedulerMonitoringServiceStatus?,
  healer: SchedulerMonitoringServiceStatus?,
): Pair<SchedulerServiceHealthAxis, String> {
  if (scheduler.paused) {
    return SchedulerServiceHealthAxis.DEGRADED to "scheduler paused"
  }
  if (!scheduler.running) {
    return SchedulerServiceHealthAxis.CRITICAL to "scheduler stopped"
  }
  if (watchdog != null && !watchdog.running) {
    return SchedulerServiceHealthAxis.DEGRADED to "watchdog not running"
  }
  if (healer != null && !healer.running) {
    return SchedulerServiceHealthAxis.DEGRADED to "healer not running"
  }
  if (scheduler.lastError.isNotBlank()) {
    return SchedulerServiceHealthAxis.DEGRADED to scheduler.lastError
  }
  return SchedulerServiceHealthAxis.HEALTHY to "scheduler service healthy"
}

private fun deriveFreshness(now: Instant, freshestUtc: Instant?): Triple<SchedulerFreshnessAxis, String, Long?> {
  if (freshestUtc == null) return Triple(SchedulerFreshnessAxis.UNKNOWN, "freshness unknown", null)
  val ageSec = Duration.between(freshestUtc, now).seconds.coerceAtLeast(0L)
  val label = when {
    ageSec <= 30L -> "fresh (${ageSec}s ago)"
    ageSec <= 300L -> "stale (${ageSec}s ago)"
    else -> "stale (${ageSec}s ago)"
  }
  val axis = if (ageSec <= 30L) SchedulerFreshnessAxis.FRESH else SchedulerFreshnessAxis.STALE
  return Triple(axis, label, ageSec)
}

private fun maxParsedInstant(vararg rawValues: String): Instant? {
  var best: Instant? = null
  for (raw in rawValues) {
    val value = parseTimestamp(raw)
    if (value != null && (best == null || value.isAfter(best))) {
      best = value
    }
  }
  return best
}

private fun rankAttention(items: List<SchedulerAttentionItem>): List<SchedulerAttentionItem> =
  items.sortedWith(compareBy({ it.risk.rank }, { it.sortRank }, { it.jobId }))

private fun effectiveSchedulerProjection(job: ScheduledJobSummary): ScheduledJobProjectionV1 {
  val existing = job.schedulerProjectionV1
  if (existing != null && existing.projectedStatus.isNotBlank()) return existing

  val lastStatus = job.state.lastStatus.lowercase()
  val dueReason = job.due.reason
  val projectedStatus = when {
    job.state.activeRunId.isNotBlank() && lastStatus == "running" -> "active"
    lastStatus in setOf("error", "failed", "timeout", "retry_exhausted") -> "failed"
    dueReason.contains("dependency", ignoreCase = true) || lastStatus == "blocked" -> "blocked"
    job.due.eligible -> "queued"
    !job.enabled -> "idle"
    else -> "idle"
  }
  val nextCandidateBucket = when {
    projectedStatus == "queued" -> "runnable_now"
    projectedStatus == "idle" && job.schedule.runAtUtc.isNotBlank() -> "scheduled_future"
    else -> "none"
  }
  return ScheduledJobProjectionV1(
    schemaVersion = "scheduler_projection_row_v1",
    projectedStatus = projectedStatus,
    projectionReason = dueReason.ifBlank { lastStatus },
    canonicalState = "",
    dueReason = dueReason,
    dueEligible = job.due.eligible,
    nextCandidateBucket = nextCandidateBucket,
    nextDueUtc = job.due.dueUtc.ifBlank { job.schedule.runAtUtc },
    durableConflict = false,
    ambiguityReasons = emptyList(),
  )
}

private fun buildLaunchLabel(dueInstant: Instant?, now: Instant): String {
  if (dueInstant == null) return "launch timing unknown"
  return if (dueInstant.isAfter(now)) {
    "launches soon in ${durationLabel(Duration.between(now, dueInstant))}"
  } else {
    "launch overdue by ${durationLabel(Duration.between(dueInstant, now))}"
  }
}

private fun dueLabel(dueInstant: Instant?, now: Instant): String {
  if (dueInstant == null) return "due: unknown"
  return if (dueInstant.isAfter(now)) {
    "due in ${durationLabel(Duration.between(now, dueInstant))}"
  } else {
    "overdue by ${durationLabel(Duration.between(dueInstant, now))}"
  }
}

private fun humanizeUpcomingReason(projection: ScheduledJobProjectionV1): String {
  return when (projection.dueReason.ifBlank { projection.projectionReason }) {
    "run_now_requested" -> "run-now request queued"
    "due" -> "ready"
    "due_failed_slot_reusable" -> "retry slot reusable"
    "retry_backoff" -> "retry backoff"
    "not_due_yet" -> "scheduled"
    else -> humanizeProjectionReason(
      projection.projectionReason.ifBlank { projection.dueReason },
      fallbackPrefix = "scheduled",
    )
  }
}

private fun humanizeProjectionReason(reason: String, fallbackPrefix: String): String {
  val cleaned = reason.trim()
  if (cleaned.isBlank()) return fallbackPrefix.ifBlank { "unknown" }
  return when {
    cleaned.startsWith("dependency_") && ":" in cleaned -> "dependency wait: ${cleaned.substringAfter(':')}"
    cleaned == "active_run_present" -> "active run present"
    cleaned == "completed_once" -> "completed once"
    cleaned == "run_now_requested" -> "run-now requested"
    cleaned == "not_due_yet" -> "not due yet"
    cleaned == "due_failed_slot_reusable" -> "retry slot reusable"
    else -> cleaned.replace('_', ' ')
  }
}

private fun upcomingBucketPriority(bucket: String): Int = when (bucket) {
  "runnable_now" -> 0
  "scheduled_future" -> 1
  else -> 2
}

private fun upcomingReasonPriority(reasonCode: String): Int = when (reasonCode) {
  "run_now_requested" -> 0
  "due" -> 1
  "due_failed_slot_reusable" -> 2
  "retry_backoff" -> 3
  "not_due_yet" -> 4
  else -> 5
}

private fun <T> dedupeById(items: List<T>, key: (T) -> String): List<T> {
  val seen = HashSet<String>()
  return items.filter {
    val id = key(it)
    if (seen.contains(id)) {
      false
    } else {
      seen.add(id)
      true
    }
  }
}

private fun <T> List<T>.dedupeById(): List<T> = dedupeById(this) { item ->
  when (item) {
    is SchedulerAttentionItem -> item.jobId
    is SchedulerRunningItem -> item.jobId
    is SchedulerUpcomingItem -> item.jobId
    else -> ""
  }
}

private fun parseTimestamp(raw: String): Instant? =
  runCatching { Instant.parse(raw) }.getOrNull()

private fun startedSortKey(raw: String): Long = parseTimestamp(raw)?.toEpochMilli() ?: Long.MIN_VALUE

private fun upcomingSortKey(raw: String): Long = parseTimestamp(raw)?.toEpochMilli() ?: Long.MAX_VALUE

private fun durationLabel(duration: Duration): String {
  val minutes = min(10_000L, max(0L, duration.abs().seconds))
  val value = if (minutes >= 3600) "${minutes / 3600}h" else if (minutes >= 60) "${minutes / 60}m" else "${minutes}s"
  return value
}

private fun elapsedLabel(startedAt: Instant?, now: Instant): String {
  if (startedAt == null) return "start unknown"
  val elapsed = Duration.between(startedAt, now).seconds.coerceAtLeast(0)
  return "running for ${durationLabel(Duration.ofSeconds(elapsed))}"
}
