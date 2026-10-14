package com.eiel.chat.ui

import com.eiel.chat.data.OperatorSnapshot
import com.eiel.chat.data.OperatorSnapshotCommandCenter
import com.eiel.chat.data.OperatorSnapshotCommandCenterItem
import com.eiel.chat.data.OperatorSnapshotFreshness
import com.eiel.chat.data.OperatorSnapshotNotification
import com.eiel.chat.data.OperatorSnapshotSection
import com.eiel.chat.data.OperatorSnapshotSectionItem
import com.eiel.chat.data.OperatorSnapshotStatusLanguage
import com.eiel.chat.data.OperatorSnapshotSummary
import com.eiel.chat.data.OperatorSnapshotTask
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OverviewFrontDoorModelTest {
  @Test
  fun frontDoorModelMapsBlockedAndRunningCardsFromCanonicalSnapshot() {
    val snapshot = OperatorSnapshot(
      operatorState = "blocked",
      activeTasks = listOf(
        OperatorSnapshotTask(
          taskId = "task-blocked",
          title = "Proof gap",
          status = "evidence_missing",
          waitingReason = "Verification proof missing",
          nextExpectedAction = "Collect focused proof",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
        ),
        OperatorSnapshotTask(
          taskId = "task-running",
          title = "Front door UI",
          status = "waiting_on_job",
          waitingReason = "Builder run active",
          nextExpectedAction = "Wait for builder output",
          nextDecisionSummary = "Review builder output",
          evidenceBackedCompletion = false,
          delegated = true,
          currentRunId = "run-123",
          currentRunState = "running",
          runCheckpoint = com.eiel.chat.data.OperatorSnapshotRunCheckpoint(
            checkpointStatus = "active",
            progressPercent = 60,
            progressSummary = "Verifying changes",
          ),
        ),
      ),
      completedTasks = listOf(
        OperatorSnapshotTask(
          taskId = "task-done",
          title = "Snapshot parser",
          status = "completed",
          waitingReason = "",
          nextExpectedAction = "",
          nextDecisionSummary = "",
          evidenceBackedCompletion = true,
        ),
      ),
      freshness = OperatorSnapshotFreshness(
        freshnessSeconds = 14,
        confidence = "HIGH",
        stale = false,
      ),
      summary = OperatorSnapshotSummary(
        statusLanguage = OperatorSnapshotStatusLanguage(
          state = "blocked",
          headline = "Blocked work requires action",
        ),
        sections = listOf(
          OperatorSnapshotSection(
            sectionId = "blocked_work",
            title = "Blocked Work",
            count = 1,
            items = listOf(
              OperatorSnapshotSectionItem(
                taskId = "task-blocked",
                title = "Proof gap",
                status = "evidence_missing",
                summary = "Verification proof missing",
                owner = "",
                action = "",
                evidenceRef = "",
              ),
            ),
          ),
          OperatorSnapshotSection(
            sectionId = "active_work",
            title = "Active Work",
            count = 1,
            items = listOf(
              OperatorSnapshotSectionItem(
                taskId = "task-running",
                title = "Front door UI",
                status = "waiting_on_job",
                summary = "Builder run active",
                owner = "",
                action = "",
                evidenceRef = "",
              ),
            ),
          ),
          OperatorSnapshotSection(
            sectionId = "next_actions",
            title = "Next Actions",
            count = 2,
            items = listOf(
              OperatorSnapshotSectionItem(
                taskId = "task-blocked",
                title = "Proof gap",
                status = "",
                summary = "",
                owner = "operator",
                action = "Collect focused proof",
                evidenceRef = "",
              ),
              OperatorSnapshotSectionItem(
                taskId = "task-running",
                title = "Front door UI",
                status = "",
                summary = "",
                owner = "builder",
                action = "Review builder output",
                evidenceRef = "",
              ),
            ),
          ),
        ),
        notifications = listOf(
          OperatorSnapshotNotification(
            kind = "blocked_or_failed_work",
            severity = "danger",
            headline = "Blocked work needs action",
            message = "One or more tasks are blocked.",
            completionClaimAllowed = false,
          ),
        ),
      ),
    )

    val model = buildOperatorFrontDoorModel(snapshot)

    requireNotNull(model)
    assertEquals("Blocked work requires action", model.headline)
    assertEquals("Fresh · 14s old", model.freshnessLabel)
    assertEquals(1, model.needsAttention.size)
    assertEquals("Collect focused proof", model.needsAttention.single().nextAction)
    assertEquals(1, model.running.size)
    assertEquals("RUNNING", model.running.single().stateLabel)
    assertEquals("Verifying changes (60%)", model.running.single().stageLabel)
    assertEquals("Running (Pipeline)", model.running.single().executionLabel)
    assertEquals("Review builder output", model.running.single().nextAction)
    assertEquals(1, model.completedCount)
    assertEquals("blocked_or_failed_work", model.topNotification?.kind)
  }

  @Test
  fun frontDoorModelCapsNeedsAttentionAtFiveItems() {
    val snapshot = OperatorSnapshot(
      operatorState = "blocked",
      activeTasks = (1..7).map { index ->
        OperatorSnapshotTask(
          taskId = "task-$index",
          title = "Blocked item $index",
          status = "failed",
          waitingReason = "Reason $index",
          nextExpectedAction = "Action $index",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
        )
      },
      summary = OperatorSnapshotSummary(
        sections = listOf(
          OperatorSnapshotSection(
            sectionId = "blocked_work",
            title = "Blocked Work",
            count = 7,
            items = (1..7).map { index ->
              OperatorSnapshotSectionItem(
                taskId = "task-$index",
                title = "Blocked item $index",
                status = "failed",
                summary = "Reason $index",
                owner = "",
                action = "Action $index",
                evidenceRef = "",
              )
            },
          ),
        ),
      ),
    )

    val model = buildOperatorFrontDoorModel(snapshot)

    requireNotNull(model)
    assertEquals(5, model.needsAttention.size)
    assertEquals("task-1", model.needsAttention.first().taskId)
    assertEquals("task-5", model.needsAttention.last().taskId)
  }

  @Test
  fun staleSnapshotFreshnessLabelDoesNotPretendPrecision() {
    val label = buildSnapshotFreshnessLabel(
      OperatorSnapshot(
        operatorState = "idle",
        freshness = OperatorSnapshotFreshness(
          freshnessSeconds = 120,
          confidence = "STALE",
          stale = true,
        ),
      ),
    )

    assertEquals("Stale", label)
    assertTrue(label != "Stale · 120s old")
  }

  @Test
  fun staleConfidenceWithoutExplicitBooleanStillCollapsesToPlainStale() {
    val label = buildSnapshotFreshnessLabel(
      OperatorSnapshot(
        operatorState = "idle",
        freshness = OperatorSnapshotFreshness(
          freshnessSeconds = 1232,
          confidence = "STALE",
          stale = false,
        ),
      ),
    )

    assertEquals("Stale", label)
  }

  @Test
  fun staleNotificationGetsActionableLabels() {
    val labels = buildFrontDoorNotificationActionLabels(
      OperatorSnapshotNotification(
        kind = "stale_data",
        severity = "warning",
        headline = "Projection is stale",
        message = "Treat completion as provisional.",
        completionClaimAllowed = false,
      ),
    )

    assertEquals(listOf("Refresh snapshot", "Admin"), labels)
  }

  @Test
  fun provisionalSnapshotSuppressesExecutiveTaskBucketsAndGroupedReasonSummary() {
    val snapshot = OperatorSnapshot(
      operatorState = "running",
      activeTasks = listOf(
        OperatorSnapshotTask(
          taskId = "task-1",
          title = "Ghost task A",
          status = "blocked",
          waitingReason = "Active responsibility has no durable delegation bindings after restart; continuity cannot be proven.",
          nextExpectedAction = "Inspect task state",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
        ),
        OperatorSnapshotTask(
          taskId = "task-2",
          title = "Ghost task B",
          status = "blocked",
          waitingReason = "Active responsibility has no durable delegation bindings after restart; continuity cannot be proven.",
          nextExpectedAction = "Inspect task state",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
        ),
      ),
      freshness = OperatorSnapshotFreshness(
        freshnessSeconds = 1232,
        confidence = "STALE",
        stale = false,
      ),
      summary = OperatorSnapshotSummary(
        statusLanguage = OperatorSnapshotStatusLanguage(
          state = "conflicted",
          headline = "Conflicting durable truth detected",
        ),
        notifications = listOf(
          OperatorSnapshotNotification(
            kind = "stale_data",
            severity = "warning",
            headline = "Projection is stale",
            message = "Freshness markers are out of date.",
            completionClaimAllowed = false,
          ),
        ),
      ),
    )

    val model = buildOperatorFrontDoorModel(snapshot)

    requireNotNull(model)
    assertEquals("Stale", model.freshnessLabel)
    assertEquals("", model.groupedFailureSummary)
    assertTrue(model.needsAttention.isEmpty())
    assertTrue(model.running.isEmpty())
    assertTrue(model.scheduled.isEmpty())
    assertEquals(null, model.topNotification)
  }

  @Test
  fun frontDoorModelSeparatesScheduledTasksFromRunningWork() {
    val snapshot = OperatorSnapshot(
      operatorState = "running",
      activeTasks = listOf(
        OperatorSnapshotTask(
          taskId = "task-scheduled",
          title = "Morning follow-up",
          status = "scheduled",
          waitingReason = "Queued for the next scheduler window",
          nextExpectedAction = "Wait for scheduled launch",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
          delegated = true,
        ),
        OperatorSnapshotTask(
          taskId = "task-waiting",
          title = "Operator input",
          status = "waiting",
          waitingReason = "Pending scheduled dependency",
          nextExpectedAction = "Wait for upstream result",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
          delegated = true,
        ),
      ),
    )

    val model = buildOperatorFrontDoorModel(snapshot)

    requireNotNull(model)
    assertTrue(model.running.isEmpty())
    assertEquals(listOf("task-scheduled", "task-waiting"), model.scheduled.map { it.taskId })
    assertEquals("SCHEDULED", model.scheduled.first().stateLabel)
    assertEquals("Scheduled (Pipeline)", model.scheduled.first().executionLabel)
    assertEquals("WAITING", model.scheduled.last().stateLabel)
  }

  @Test
  fun frontDoorModelTreatsWaitingOnJobWithoutCurrentRunAsScheduled() {
    val snapshot = OperatorSnapshot(
      operatorState = "running",
      activeTasks = listOf(
        OperatorSnapshotTask(
          taskId = "task-queued",
          title = "Queued follow-up",
          status = "waiting_on_job",
          waitingReason = "Queued but not yet active",
          nextExpectedAction = "Wait for launch",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
          delegated = true,
          currentRunId = "",
          currentRunState = "",
        ),
      ),
    )

    val model = buildOperatorFrontDoorModel(snapshot)

    requireNotNull(model)
    assertTrue(model.running.isEmpty())
    assertEquals(listOf("task-queued"), model.scheduled.map { it.taskId })
    assertEquals("WAITING", model.scheduled.single().stateLabel)
    assertEquals("Scheduled (Pipeline)", model.scheduled.single().executionLabel)
  }

  @Test
  fun frontDoorModelSanitizesCommandCenterCountsWithoutExpandingToAllTasks() {
    val snapshot = OperatorSnapshot(
      operatorState = "blocked",
      activeTasks = listOf(
        OperatorSnapshotTask(
          taskId = "task-proof-gap",
          title = "Proof gap",
          status = "evidence_missing",
          waitingReason = "Verification proof missing",
          nextExpectedAction = "Collect focused proof",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
        ),
        OperatorSnapshotTask(
          taskId = "task-queued",
          title = "Queued follow-up",
          status = "waiting_on_job",
          waitingReason = "Queued but not yet active",
          nextExpectedAction = "Wait for launch",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
          delegated = true,
          currentRunId = "",
          currentRunState = "",
        ),
      ),
      summary = OperatorSnapshotSummary(
        statusLanguage = OperatorSnapshotStatusLanguage(
          state = "blocked",
          headline = "Blocked work requires action",
        ),
        sections = listOf(
          OperatorSnapshotSection(
            sectionId = "blocked_work",
            title = "Blocked Work",
            count = 1,
            items = listOf(
              OperatorSnapshotSectionItem(
                taskId = "task-proof-gap",
                title = "Proof gap",
                status = "evidence_missing",
                summary = "Verification proof missing",
                owner = "",
                action = "Collect focused proof",
                evidenceRef = "",
              ),
            ),
          ),
        ),
      ),
      commandCenter = OperatorSnapshotCommandCenter(
        headline = "Stale command center",
        running = listOf(
          OperatorSnapshotCommandCenterItem(
            taskId = "task-queued",
            title = "Queued follow-up",
            state = "RUNNING",
            reason = "Stale running label",
            nextAction = "Wait",
          ),
        ),
        needsAttention = listOf(
          OperatorSnapshotCommandCenterItem(
            taskId = "task-proof-gap",
            title = "Proof gap",
            state = "BLOCKED",
            reason = "Verification proof missing",
            nextAction = "Collect focused proof",
          ),
          OperatorSnapshotCommandCenterItem(
            taskId = "ghost-blocked",
            title = "Ghost blocked",
            state = "BLOCKED",
            reason = "Stale",
            nextAction = "Inspect",
          ),
        ),
      ),
    )

    val model = buildOperatorFrontDoorModel(snapshot)

    requireNotNull(model)
    assertTrue(model.running.isEmpty())
    assertEquals(listOf("task-queued"), model.scheduled.map { it.taskId })
    assertEquals(listOf("task-proof-gap"), model.needsAttention.map { it.taskId })
  }

  @Test
  fun frontDoorModelPrefersCommandCenterProjectionWhenPresent() {
    val snapshot = OperatorSnapshot(
      operatorState = "blocked",
      freshness = OperatorSnapshotFreshness(
        freshnessSeconds = 30,
        confidence = "HIGH",
        stale = false,
      ),
      commandCenter = OperatorSnapshotCommandCenter(
        headline = "Blocked work requires action",
        snapshotMeta = OperatorSnapshotFreshness(
          freshnessSeconds = 9,
          confidence = "DEGRADED",
          stale = false,
        ),
        needsAttention = listOf(
          OperatorSnapshotCommandCenterItem(
            taskId = "task-command-center",
            title = "Proof gap",
            state = "BLOCKED",
            reason = "Verification proof missing",
            nextAction = "Collect focused proof",
            stageLabel = "Needs review",
            executionSurface = "Attention (Pipeline)",
          ),
        ),
        running = listOf(
          OperatorSnapshotCommandCenterItem(
            taskId = "task-running",
            title = "Builder task",
            state = "RUNNING",
            reason = "Builder run active",
            nextAction = "Wait for builder output",
            stageLabel = "Verifying changes (60%)",
            executionSurface = "Running (Pipeline)",
          ),
        ),
        completedCount = 4,
      ),
    )

    val model = buildOperatorFrontDoorModel(snapshot)

    requireNotNull(model)
    assertEquals("Blocked work requires action", model.headline)
    assertEquals("Degraded · 9s old", model.freshnessLabel)
    assertEquals(listOf("task-command-center"), model.needsAttention.map { it.taskId })
    assertEquals("Collect focused proof", model.needsAttention.single().nextAction)
    assertEquals(listOf("task-running"), model.running.map { it.taskId })
    assertEquals("Running (Pipeline)", model.running.single().executionLabel)
    assertEquals(4, model.completedCount)
  }

  @Test
  fun frontDoorModelBuildsGroupedFailureSummaryFromRepeatedReasons() {
    val snapshot = OperatorSnapshot(
      operatorState = "blocked",
      activeTasks = listOf(
        OperatorSnapshotTask(
          taskId = "task-a",
          title = "Blocked A",
          status = "failed",
          waitingReason = "Same root cause",
          nextExpectedAction = "Inspect logs",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
        ),
        OperatorSnapshotTask(
          taskId = "task-b",
          title = "Blocked B",
          status = "failed",
          waitingReason = "Same root cause",
          nextExpectedAction = "Inspect logs",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
        ),
      ),
    )

    val model = buildOperatorFrontDoorModel(snapshot)

    requireNotNull(model)
    assertEquals("2 tasks share the same reason: Same root cause", model.groupedFailureSummary)
  }

  @Test
  fun frontDoorModelIgnoresPlaceholderReasonsForGroupedFailureSummary() {
    val snapshot = OperatorSnapshot(
      operatorState = "blocked",
      activeTasks = listOf(
        OperatorSnapshotTask(
          taskId = "task-a",
          title = "Blocked A",
          status = "failed",
          waitingReason = "",
          nextExpectedAction = "Inspect logs",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
        ),
        OperatorSnapshotTask(
          taskId = "task-b",
          title = "Blocked B",
          status = "failed",
          waitingReason = "",
          nextExpectedAction = "Inspect logs",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
        ),
      ),
    )

    val model = buildOperatorFrontDoorModel(snapshot)

    requireNotNull(model)
    assertEquals("", model.groupedFailureSummary)
  }

  @Test
  fun frontDoorModelCarriesMetadataAndRecommendedActionFromWorkItems() {
    val snapshot = OperatorSnapshot(
      operatorState = "blocked",
      activeTasks = listOf(
        OperatorSnapshotTask(
          taskId = "task-proof-gap",
          title = "Proof gap",
          status = "evidence_missing",
          waitingReason = "Verification proof missing",
          nextExpectedAction = "Collect focused proof",
          nextDecisionSummary = "",
          evidenceBackedCompletion = false,
        ),
      ),
      workItems = listOf(
        com.eiel.chat.data.OperatorSnapshotWorkItem(
          taskId = "task-proof-gap",
          stableTitle = "Proof gap",
          state = "BLOCKED",
          reason = "Verification proof missing",
          recommendedActionPrompt = "Collect focused proof",
          recommendedActionRisk = "medium",
          recommendedActionConsequence = "A new attempt starts and the current failure state may be replaced.",
          confidenceLabel = "LOW",
          confidenceWarn = true,
          timingLabel = "Escalates in 5m",
          retryLabel = "Retry 1/3",
          artifacts = listOf("runtime/proof.json"),
        ),
      ),
    )

    val model = buildOperatorFrontDoorModel(snapshot)

    requireNotNull(model)
    val item = model.needsAttention.single()
    assertTrue(item.recommended)
    assertEquals("medium", item.riskLevel)
    assertEquals(
      listOf("Confidence LOW", "Escalates in 5m", "Retry 1/3", "1 artifact"),
      item.metadata,
    )
  }
}
