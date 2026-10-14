package com.eiel.chat.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class SchedulerRouteTest {
  @get:Rule
  val composeRule = createComposeRule()

  @Test
  fun schedulerStatusScreenRendersHealthAttentionRunningUpcomingAndQuickActions() {
    val projection = SchedulerStatusProjection(
      serviceHealth = SchedulerServiceHealthAxis.HEALTHY,
      serviceHealthMessage = "scheduler service healthy",
      freshness = SchedulerFreshnessAxis.FRESH,
      freshnessAgeLabel = "fresh (20s ago)",
      freshnessAgeSec = 20L,
      attentionItems = listOf(
        SchedulerAttentionItem(
          jobId = "job-error",
          title = "job-error",
          reason = "last status error",
          risk = SchedulerProjectedAttentionRisk.HIGH,
          launchedAtLabel = "overdue by 3m",
        ),
      ),
      runningItems = listOf(
        SchedulerRunningItem(
          jobId = "job-running",
          title = "job-running",
          startedLabel = "running for 2m",
          status = "active",
          startedUtc = "2026-03-27T09:58:00Z",
        ),
      ),
      upcomingItems = listOf(
        SchedulerUpcomingItem(
          jobId = "job-upcoming",
          title = "job-upcoming",
          dueLabel = "due in 5m",
          reason = "ready",
          dueUtc = "2026-03-27T10:05:00Z",
        ),
      ),
    )

    composeRule.setContent {
      MaterialTheme {
        SchedulerStatusScreen(
          projection = projection,
          loading = false,
          hasError = "",
          onOpenSchedules = {},
          onOpenPipelines = {},
          onRefresh = {},
        )
      }
    }

    assertNodeExists(composeRule.onNodeWithText("Scheduler service"))
    assertNodeExists(composeRule.onNodeWithText("Service health: healthy"))
    assertNodeExists(composeRule.onNodeWithText("Attention items"))
    assertNodeExists(composeRule.onNodeWithText("job-error"))
    assertNodeExists(composeRule.onNodeWithText("Running now"))
    assertNodeExists(composeRule.onNodeWithText("job-running"))
    assertNodeExists(composeRule.onNodeWithText("Next launches"))
    assertNodeExists(composeRule.onNodeWithText("job-upcoming"))
    assertNodeExists(composeRule.onNodeWithText("Refresh"))
    assertNodeExists(composeRule.onNodeWithText("Open Schedules"))
    assertNodeExists(composeRule.onNodeWithText("Open Pipelines"))
  }

  private fun assertNodeExists(interaction: SemanticsNodeInteraction): SemanticsNodeInteraction {
    interaction.fetchSemanticsNode()
    return interaction
  }
}
