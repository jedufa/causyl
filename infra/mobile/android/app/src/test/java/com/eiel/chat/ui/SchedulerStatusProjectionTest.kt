package com.eiel.chat.ui

import com.eiel.chat.data.SchedulerHealthPayload
import com.eiel.chat.data.SchedulerMonitoringReceipt
import com.eiel.chat.data.SchedulerMonitoringServiceStatus
import com.eiel.chat.data.SchedulerServiceStatus
import com.eiel.chat.data.ScheduledJobBinding
import com.eiel.chat.data.ScheduledJobDue
import com.eiel.chat.data.ScheduledJobLoop
import com.eiel.chat.data.ScheduledJobProjectionV1
import com.eiel.chat.data.ScheduledJobRuntime
import com.eiel.chat.data.ScheduledJobSchedule
import com.eiel.chat.data.ScheduledJobState
import com.eiel.chat.data.ScheduledJobSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.time.Instant

class SchedulerStatusProjectionTest {
  @Test
  fun deriveSchedulerStatusProjectionSeparatesServiceAndFreshnessAxes() {
    val now = Instant.parse("2026-03-27T10:00:00Z")
    val projection = deriveSchedulerStatusProjection(
      jobs = listOf(
        scheduledJob("job-running", dueUtc = "2026-03-27T11:00:00Z", active = true),
      ),
      scheduler = scheduler(running = true),
      watchdog = null,
      healer = null,
      health = null,
      jobsFetchedUtc = now.minusSeconds(20).toString(),
      healthFetchedUtc = "",
      now = now,
    )

    assertEquals(SchedulerServiceHealthAxis.HEALTHY, projection.serviceHealth)
    assertEquals(SchedulerFreshnessAxis.FRESH, projection.freshness)
    assertEquals(true, projection.freshnessAgeLabel.contains("20"))
  }

  @Test
  fun deriveSchedulerStatusProjectionRanksAttentionAndCaps() {
    val now = Instant.parse("2026-03-27T10:00:00Z")
    val jobs = listOf(
      scheduledJob("job-ok", dueUtc = "2026-03-27T11:00:00Z", enabled = true),
      scheduledJob("job-error", lastStatus = "error", enabled = true),
      scheduledJob("job-block", dueReason = "dependency_not_ok:db"),
      scheduledJob("job-stale", lastStatus = "running", claimStale = true),
      scheduledJob("job-disabled", enabled = false),
      scheduledJob("job-block2", dueReason = "dependency_not_ok:api"),
    )
    val projection = deriveSchedulerStatusProjection(
      jobs = jobs,
      scheduler = scheduler(running = true),
      watchdog = null,
      healer = null,
      health = null,
      jobsFetchedUtc = now.minusSeconds(20).toString(),
      healthFetchedUtc = "",
      now = now,
      maxAttention = 3,
    )

    assertEquals(3, projection.attentionItems.size)
    assertEquals("high", projection.attentionItems.first().risk.title)
    assertEquals("job-error", projection.attentionItems[0].jobId)
    assertEquals("job-stale", projection.attentionItems[1].jobId)
  }

  @Test
  fun deriveSchedulerStatusProjectionDedupesJobIdsAcrossUpcomingAndAttention() {
    val now = Instant.parse("2026-03-27T10:00:00Z")
    val jobs = listOf(
      scheduledJob("duplicate", dueUtc = "2026-03-27T10:01:00Z", dueReason = "due", dueEligible = true),
      scheduledJob("duplicate", dueUtc = "2026-03-27T10:02:00Z", dueReason = "due", dueEligible = true),
      scheduledJob("other", dueUtc = "2026-03-27T10:03:00Z", dueReason = "due", dueEligible = true),
    )
    val projection = deriveSchedulerStatusProjection(
      jobs = jobs,
      scheduler = scheduler(running = true),
      watchdog = null,
      healer = null,
      health = null,
      jobsFetchedUtc = now.minusSeconds(20).toString(),
      healthFetchedUtc = "",
      now = now,
    )

    assertEquals(2, projection.upcomingItems.size)
    val seen = projection.upcomingItems.map { it.jobId }.toSet()
    assertEquals(setOf("duplicate", "other"), seen)
  }

  @Test
  fun deriveSchedulerStatusProjectionDerivesRunningAndUpcomingFromJobs() {
    val now = Instant.parse("2026-03-27T10:00:00Z")
    val projection = deriveSchedulerStatusProjection(
      jobs = listOf(
        scheduledJob(
          "running-now",
          enabled = true,
          active = true,
          activeStartedUtc = now.minusSeconds(120).toString(),
          lastStatus = "running",
        ),
        scheduledJob(
          "future-run",
          enabled = true,
          dueUtc = now.plusSeconds(120).toString(),
          dueEligible = true,
        ),
      ),
      scheduler = scheduler(running = true),
      watchdog = null,
      healer = null,
      health = null,
      jobsFetchedUtc = now.minusSeconds(20).toString(),
      healthFetchedUtc = "",
      now = now,
    )

    assertEquals(1, projection.runningItems.size)
    assertEquals("running-now", projection.runningItems.first().jobId)
    assertTrue(projection.runningItems.first().startedLabel.contains("running for"))
    assertEquals(1, projection.upcomingItems.size)
    assertEquals("future-run", projection.upcomingItems.first().jobId)
  }

  @Test
  fun deriveSchedulerStatusProjectionOrdersUpcomingByUrgencyAndOverdue() {
    val now = Instant.parse("2026-03-27T10:00:00Z")
    val projection = deriveSchedulerStatusProjection(
      jobs = listOf(
        scheduledJob("future", dueUtc = now.plusSeconds(120).toString(), dueEligible = true, dueReason = "ready"),
        scheduledJob("older-overdue", dueUtc = now.minusSeconds(900).toString(), dueEligible = true, dueReason = "ready"),
        scheduledJob("newer-overdue", dueUtc = now.minusSeconds(120).toString(), dueEligible = true, dueReason = "ready"),
      ),
      scheduler = scheduler(running = true),
      watchdog = null,
      healer = null,
      health = null,
      jobsFetchedUtc = now.toString(),
      healthFetchedUtc = "",
      now = now,
    )

    assertEquals("older-overdue", projection.upcomingItems[0].jobId)
    assertEquals("newer-overdue", projection.upcomingItems[1].jobId)
    assertEquals("future", projection.upcomingItems[2].jobId)
  }

  @Test
  fun deriveSchedulerStatusProjectionHandlesMalformedTimestamps() {
    val now = Instant.parse("2026-03-27T10:00:00Z")
    val projection = deriveSchedulerStatusProjection(
      jobs = listOf(
        scheduledJob(
          "bad-times",
          active = true,
          lastStatus = "running",
          activeStartedUtc = "not-a-time",
          dueUtc = "also-bad",
          dueEligible = true,
          dueReason = "ready",
        ),
        scheduledJob(
          "upcoming-bad-time",
          active = false,
          dueUtc = "also-bad",
          dueEligible = true,
          dueReason = "ready",
        ),
      ),
      scheduler = scheduler(running = true),
      watchdog = null,
      healer = null,
      health = null,
      jobsFetchedUtc = "not-a-time",
      healthFetchedUtc = "definitely-bad",
      now = now,
    )

    assertEquals(1, projection.runningItems.size)
    assertEquals("start unknown", projection.runningItems.first().startedLabel)
    assertEquals(1, projection.upcomingItems.size)
    assertEquals("due: unknown", projection.upcomingItems.first().dueLabel)
    assertEquals(SchedulerFreshnessAxis.UNKNOWN, projection.freshness)
  }

  @Test
  fun deriveSchedulerStatusProjectionPreservesSchedulerTruthOverJobSignals() {
    val now = Instant.parse("2026-03-27T10:00:00Z")
    val projection = deriveSchedulerStatusProjection(
      jobs = listOf(
        scheduledJob(
          "job-error",
          enabled = true,
          lastStatus = "error",
          dueReason = "overdue",
          dueEligible = false,
          active = true,
        ),
      ),
      scheduler = scheduler(running = false),
      watchdog = monitoringService(running = false),
      healer = monitoringService(running = false),
      health = null,
      jobsFetchedUtc = now.minusSeconds(20).toString(),
      healthFetchedUtc = "",
      now = now,
    )

    assertEquals(SchedulerServiceHealthAxis.CRITICAL, projection.serviceHealth)
    assertEquals(1, projection.attentionItems.size)
  }

  @Test
  fun deriveSchedulerStatusProjectionPerformanceSmokeForLargishJobSets() {
    val now = Instant.parse("2026-03-27T10:00:00Z")
    val jobs = (0 until 2_000).map { index ->
      scheduledJob(
        jobId = "job-$index",
        enabled = index % 3 != 0,
        active = index % 10 == 0,
        activeStartedUtc = if (index % 10 == 0) now.minusSeconds((index * 11L) % 900L).toString() else "",
        dueUtc = if (index % 2 == 0) now.plusSeconds((index % 200L) + 10L).toString() else now.minusSeconds((index % 120L) + 10L).toString(),
        dueEligible = index % 2 == 0,
        dueReason = if (index % 3 == 0) "dependency_not_ok:chain" else "due",
      )
    }

    val startMs = System.currentTimeMillis()
    val projection = deriveSchedulerStatusProjection(
      jobs = jobs,
      scheduler = scheduler(running = true),
      watchdog = null,
      healer = null,
      health = null,
      jobsFetchedUtc = now.minusSeconds(20).toString(),
      healthFetchedUtc = "",
      now = now,
    )
    val elapsedMs = System.currentTimeMillis() - startMs

    assertTrue(projection.attentionItems.size <= 5)
    assertTrue(projection.runningItems.size <= 5)
    assertTrue(projection.upcomingItems.size <= 5)
    assertTrue(elapsedMs < 1500)
  }

  @Test
  fun schedulerRouteShouldNotReusePipelineGroupingHelpers() {
    val possibleFiles = listOf(
      "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/SchedulerRoute.kt",
      "app/src/main/java/com/eiel/chat/ui/SchedulerRoute.kt",
      "src/main/java/com/eiel/chat/ui/SchedulerRoute.kt",
      "../app/src/main/java/com/eiel/chat/ui/SchedulerRoute.kt",
    )
    val routeFile = possibleFiles.map(::File).firstOrNull { it.exists() } ?: throw AssertionError(
      "SchedulerRoute source path not found",
    )
    val routeSource = routeFile.readText()
    assertTrue(!routeSource.contains("buildScheduledJobBranchSections"))
    assertTrue(!routeSource.contains("buildDependencySections"))
  }

  @Test
  fun appShellAddsSchedulerTabAndRoutings() {
    val possibleFiles = listOf(
      "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AppShell.kt",
      "app/src/main/java/com/eiel/chat/ui/AppShell.kt",
      "src/main/java/com/eiel/chat/ui/AppShell.kt",
      "../app/src/main/java/com/eiel/chat/ui/AppShell.kt",
    )
    val appShellFile = possibleFiles.map(::File).firstOrNull { it.exists() } ?: throw AssertionError(
      "AppShell source path not found",
    )
    val shellSource = appShellFile.readText()
    assertTrue(shellSource.contains("RootView.SCHEDULER"))
    assertTrue(shellSource.contains("SchedulerRoute"))
  }

  @Test
  fun deriveSchedulerStatusProjectionUsesHealthFreshnessTimestampWhenAvailable() {
    val now = Instant.parse("2026-03-27T10:00:00Z")
    val projection = deriveSchedulerStatusProjection(
      jobs = listOf(scheduledJob("job-id")),
      scheduler = scheduler(running = true),
      watchdog = monitoringService(running = true),
      healer = monitoringService(running = true),
      health = SchedulerHealthPayload(
        fetchedUtc = now.minusSeconds(20).toString(),
        scheduler = scheduler(running = true),
        watchdog = monitoringService(running = true),
        healer = monitoringService(running = true),
      ),
      jobsFetchedUtc = now.minusSeconds(1_800).toString(),
      healthFetchedUtc = now.minusSeconds(20).toString(),
      now = now,
    )

    assertEquals(SchedulerFreshnessAxis.FRESH, projection.freshness)
    assertEquals(20L, projection.freshnessAgeSec)
  }

  @Test
  fun deriveSchedulerStatusProjectionKeepsCompletedOnceOutOfUpcomingWhenWeakDueFieldsDisagree() {
    val now = Instant.parse("2026-03-27T10:00:00Z")
    val projection = deriveSchedulerStatusProjection(
      jobs = listOf(
        scheduledJob(
          "once-done",
          dueUtc = now.plusSeconds(120).toString(),
          dueEligible = true,
          dueReason = "due",
          lastStatus = "ok",
          sharedProjection = ScheduledJobProjectionV1(
            schemaVersion = "scheduler_projection_row_v1",
            projectedStatus = "completed_once",
            projectionReason = "already_executed",
            dueReason = "due",
            dueEligible = true,
            nextCandidateBucket = "none",
            nextDueUtc = now.plusSeconds(120).toString(),
          ),
        ),
      ),
      scheduler = scheduler(running = true),
      watchdog = null,
      healer = null,
      health = null,
      jobsFetchedUtc = now.toString(),
      healthFetchedUtc = "",
      now = now,
    )

    assertTrue(projection.upcomingItems.isEmpty())
    assertTrue(projection.attentionItems.isEmpty())
  }

  @Test
  fun deriveSchedulerStatusProjectionPrefersRunNowQueuedJobAheadOfFutureIdleWork() {
    val now = Instant.parse("2026-03-27T10:00:00Z")
    val projection = deriveSchedulerStatusProjection(
      jobs = listOf(
        scheduledJob(
          "future-idle",
          dueUtc = now.plusSeconds(600).toString(),
          dueEligible = false,
          dueReason = "not_due_yet",
          sharedProjection = ScheduledJobProjectionV1(
            schemaVersion = "scheduler_projection_row_v1",
            projectedStatus = "idle",
            projectionReason = "not_due_yet",
            dueReason = "not_due_yet",
            dueEligible = false,
            nextCandidateBucket = "scheduled_future",
            nextDueUtc = now.plusSeconds(600).toString(),
          ),
        ),
        scheduledJob(
          "run-now",
          dueUtc = now.plusSeconds(60).toString(),
          dueEligible = true,
          dueReason = "run_now_requested",
          sharedProjection = ScheduledJobProjectionV1(
            schemaVersion = "scheduler_projection_row_v1",
            projectedStatus = "queued",
            projectionReason = "run_now_requested",
            dueReason = "run_now_requested",
            dueEligible = true,
            nextCandidateBucket = "runnable_now",
            nextDueUtc = now.plusSeconds(60).toString(),
          ),
        ),
      ),
      scheduler = scheduler(running = true),
      watchdog = null,
      healer = null,
      health = null,
      jobsFetchedUtc = now.toString(),
      healthFetchedUtc = "",
      now = now,
    )

    assertEquals(listOf("run-now", "future-idle"), projection.upcomingItems.map { it.jobId })
    assertEquals("run-now request queued", projection.upcomingItems.first().reason)
  }

  @Test
  fun deriveSchedulerStatusProjectionUsesSharedBlockedTruthInsteadOfUpcomingFallback() {
    val now = Instant.parse("2026-03-27T10:00:00Z")
    val projection = deriveSchedulerStatusProjection(
      jobs = listOf(
        scheduledJob(
          "blocked-job",
          dueUtc = now.plusSeconds(120).toString(),
          dueEligible = true,
          dueReason = "due",
          sharedProjection = ScheduledJobProjectionV1(
            schemaVersion = "scheduler_projection_row_v1",
            projectedStatus = "blocked",
            projectionReason = "dependency_pending:prep_001",
            dueReason = "due",
            dueEligible = true,
            nextCandidateBucket = "none",
            nextDueUtc = now.plusSeconds(120).toString(),
          ),
        ),
      ),
      scheduler = scheduler(running = true),
      watchdog = null,
      healer = null,
      health = null,
      jobsFetchedUtc = now.toString(),
      healthFetchedUtc = "",
      now = now,
    )

    assertTrue(projection.upcomingItems.isEmpty())
    assertEquals(listOf("blocked-job"), projection.attentionItems.map { it.jobId })
    assertTrue(projection.attentionItems.single().reason.contains("prep_001"))
  }

  @Test
  fun deriveSchedulerStatusProjectionUsesSharedTruthForFrontDoorWhatsNextOrderingAndClassification() {
    val now = Instant.parse("2026-03-27T10:00:00Z")
    val projection = deriveSchedulerStatusProjection(
      jobs = listOf(
        scheduledJob(
          "blocked-drift",
          dueUtc = now.plusSeconds(30).toString(),
          dueEligible = true,
          dueReason = "due",
          sharedProjection = ScheduledJobProjectionV1(
            schemaVersion = "scheduler_projection_row_v1",
            projectedStatus = "blocked",
            projectionReason = "dependency_pending:prep_001",
            dueReason = "due",
            dueEligible = true,
            nextCandidateBucket = "runnable_now",
            nextDueUtc = now.plusSeconds(30).toString(),
          ),
        ),
        scheduledJob(
          "run-now",
          dueUtc = now.plusSeconds(300).toString(),
          dueEligible = true,
          dueReason = "run_now_requested",
          sharedProjection = ScheduledJobProjectionV1(
            schemaVersion = "scheduler_projection_row_v1",
            projectedStatus = "queued",
            projectionReason = "run_now_requested",
            dueReason = "run_now_requested",
            dueEligible = true,
            nextCandidateBucket = "runnable_now",
            nextDueUtc = now.plusSeconds(300).toString(),
          ),
        ),
        scheduledJob(
          "future-idle",
          dueUtc = now.plusSeconds(120).toString(),
          dueEligible = false,
          dueReason = "not_due_yet",
          sharedProjection = ScheduledJobProjectionV1(
            schemaVersion = "scheduler_projection_row_v1",
            projectedStatus = "idle",
            projectionReason = "not_due_yet",
            dueReason = "not_due_yet",
            dueEligible = false,
            nextCandidateBucket = "scheduled_future",
            nextDueUtc = now.plusSeconds(120).toString(),
          ),
        ),
      ),
      scheduler = scheduler(running = true),
      watchdog = null,
      healer = null,
      health = null,
      jobsFetchedUtc = now.toString(),
      healthFetchedUtc = "",
      now = now,
    )

    assertEquals(listOf("run-now", "future-idle"), projection.upcomingItems.map { it.jobId })
    assertEquals(listOf("blocked-drift"), projection.attentionItems.map { it.jobId })
  }

  private fun scheduler(
    running: Boolean,
    paused: Boolean = false,
  ): SchedulerServiceStatus = SchedulerServiceStatus(
    running = running,
    intervalSeconds = 30,
    startedUtc = "2026-03-27T00:00:00Z",
    lastTickUtc = "2026-03-27T00:01:00Z",
    lastError = "",
    repoRoot = "",
    paused = paused,
  )

  private fun monitoringService(running: Boolean): SchedulerMonitoringServiceStatus = SchedulerMonitoringServiceStatus(
    running = running,
    intervalSeconds = 30,
    startedUtc = "2026-03-27T00:00:00Z",
    lastReceiptUtc = "2026-03-27T00:01:00Z",
    lastError = "",
    repoRoot = "",
    latestReceipt = SchedulerMonitoringReceipt(
      schemaVersion = "scheduler-monitoring-v1",
      utc = "2026-03-27T00:01:00Z",
      findingsCount = 0,
      error = "",
      schedulerHealth = "healthy",
      schedulerHealthReason = "",
    ),
  )

  private fun scheduledJob(
    jobId: String,
    enabled: Boolean = true,
    active: Boolean = false,
    activeStartedUtc: String = "",
    dueUtc: String = "",
    dueEligible: Boolean = false,
    dueReason: String = "",
    lastStatus: String = "",
    claimStale: Boolean = false,
    sharedProjection: ScheduledJobProjectionV1? = null,
  ) = ScheduledJobSummary(
    jobId = jobId,
    jobKind = "builder",
    enabled = enabled,
    role = "role",
    runtimeRole = "runtime_role",
    displayTitle = jobId,
    binding = ScheduledJobBinding(type = "ledger_uid", id = "PIS-20260327-081404Z-2DFA"),
    dependsOn = emptyList(),
    schedule = ScheduledJobSchedule(
      mode = "interval",
      runAtUtc = "",
      timeOfDay = "",
      timezone = "UTC",
    ),
    runtime = ScheduledJobRuntime(
      agentTech = "llm",
      model = "x",
    ),
    loop = ScheduledJobLoop(maxIterations = 1),
    prompt = "prompt",
    promptPreview = "prompt",
    contextRefs = emptyList(),
    createdUtc = "2026-03-01T00:00:00Z",
    updatedUtc = "2026-03-01T00:00:00Z",
    state = ScheduledJobState(
      activeRunId = if (active) jobId else "",
      activeStartedUtc = if (active) activeStartedUtc else "",
      lastRunId = "",
      lastStartedUtc = "",
      lastFinishedUtc = "",
      lastStatus = lastStatus,
      lastTrigger = "",
      lastReceiptPath = "",
      lastReportPath = "",
      lastSlotKey = "",
      updatedUtc = "",
      activeClaimPresent = active,
      activeClaimStale = claimStale,
      activeClaimReason = if (claimStale) "timeout" else "",
      activeClaimHeartbeatUtc = "",
      activeClaimAgeSec = if (claimStale) 12.0 else null,
    ),
    due = ScheduledJobDue(
      eligible = dueEligible,
      reason = dueReason,
      slotKey = "",
      dueUtc = dueUtc,
    ),
    schedulerProjectionV1 = sharedProjection,
  )
}
