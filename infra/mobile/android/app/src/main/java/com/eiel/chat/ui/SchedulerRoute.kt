package com.eiel.chat.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import java.time.Instant
import androidx.lifecycle.viewmodel.compose.viewModel

@Composable
fun SchedulerRoute(
  vm: AdminViewModel = viewModel(),
  onOpenSchedules: () -> Unit,
  onOpenPipelines: () -> Unit,
  clock: () -> Instant = { Instant.now() },
) {
  val state by vm.uiState.collectAsStateWithLifecycle()

  LaunchedEffect(Unit) {
    vm.refreshScheduledJobs()
    vm.refreshSchedulerHealth()
  }

  val projection = deriveSchedulerStatusProjection(
    jobs = state.scheduledJobs,
    scheduler = state.scheduledScheduler,
    watchdog = state.scheduledWatchdog,
    healer = state.scheduledHealer,
    health = state.scheduledHealth,
    jobsFetchedUtc = state.scheduledJobsFetchedUtc,
    healthFetchedUtc = state.scheduledHealthFetchedUtc,
    now = clock(),
  )

  val loading = state.scheduledJobsLoading || state.scheduledHealthLoading
  val hasError = state.scheduledJobsMessage.ifBlank { state.scheduledHealthMessage }.ifBlank { "" }

  SchedulerStatusScreen(
    projection = projection,
    loading = loading,
    hasError = hasError,
    onOpenSchedules = onOpenSchedules,
    onOpenPipelines = onOpenPipelines,
    onRefresh = {
      vm.refreshScheduledJobs()
      vm.refreshSchedulerHealth()
    },
  )
}

@Composable
internal fun SchedulerStatusScreen(
  projection: SchedulerStatusProjection,
  loading: Boolean,
  hasError: String,
  onOpenSchedules: () -> Unit,
  onOpenPipelines: () -> Unit,
  onRefresh: () -> Unit,
) {
  Column(
    modifier = Modifier
      .fillMaxSize()
      .verticalScroll(rememberScrollState())
      .run {
        if (loading) padding(bottom = 2.dp) else this
      },
  ) {
    SchedulerSectionHeader(title = "Scheduler service")
    SchedulerHealthBanner(
      serviceHealth = projection.serviceHealth,
      serviceHealthMessage = projection.serviceHealthMessage,
      freshness = projection.freshness,
      freshnessLabel = projection.freshnessAgeLabel,
    )
    if (hasError.isNotBlank()) {
      Text(
        text = hasError,
        color = MaterialTheme.colorScheme.error,
        modifier = Modifier.padding(start = 12.dp, end = 12.dp, top = 4.dp),
      )
    }

    SchedulerItemSection(title = "Attention items") {
      SchedulerStatusList(
        items = projection.attentionItems,
        emptyText = if (loading) "Loading attention items..." else "No attention items",
        render = { item ->
          Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
              text = item.risk.title,
              style = MaterialTheme.typography.labelSmall,
              color = if (item.risk == SchedulerProjectedAttentionRisk.HIGH) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
              modifier = Modifier
                .weight(0.28f),
            )
            Column(modifier = Modifier.weight(1f)) {
              Text(item.title, style = MaterialTheme.typography.titleSmall)
              Text(item.reason, style = MaterialTheme.typography.bodySmall)
              Text(item.launchedAtLabel, style = MaterialTheme.typography.bodySmall)
            }
          }
        },
      )
    }

    SchedulerItemSection(title = "Running now") {
      SchedulerStatusList(
        items = projection.runningItems,
        emptyText = if (loading) "Loading running jobs..." else "No running jobs",
        render = { item ->
          Column {
            Text(item.title, style = MaterialTheme.typography.titleSmall)
            Text(item.startedLabel, style = MaterialTheme.typography.bodySmall)
            Text(item.status, style = MaterialTheme.typography.bodySmall)
          }
        },
      )
    }

    SchedulerItemSection(title = "Next launches") {
      SchedulerStatusList(
        items = projection.upcomingItems,
        emptyText = if (loading) "Loading upcoming jobs..." else "No upcoming launches",
        render = { item ->
          Column {
            Text(item.title, style = MaterialTheme.typography.titleSmall)
            Text(item.dueLabel, style = MaterialTheme.typography.bodySmall)
            Text(item.reason, style = MaterialTheme.typography.bodySmall)
          }
        },
      )
    }

    SchedulerQuickActions(
      onOpenSchedules = onOpenSchedules,
      onOpenPipelines = onOpenPipelines,
      onRefresh = onRefresh,
    )
  }
}

@Composable
private fun SchedulerSectionHeader(title: String) {
  Card(
    modifier = Modifier.fillMaxWidth(),
    shape = RoundedCornerShape(12.dp),
    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
    elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
  ) {
    Text(
      text = title,
      style = MaterialTheme.typography.titleMedium,
      color = MaterialTheme.colorScheme.onPrimaryContainer,
      modifier = Modifier.padding(12.dp),
    )
  }
}

@Composable
private fun SchedulerHealthBanner(
  serviceHealth: SchedulerServiceHealthAxis,
  serviceHealthMessage: String,
  freshness: SchedulerFreshnessAxis,
  freshnessLabel: String,
) {
  val background = when (serviceHealth) {
    SchedulerServiceHealthAxis.HEALTHY -> MaterialTheme.colorScheme.primary.copy(alpha = 0.08f)
    SchedulerServiceHealthAxis.DEGRADED -> MaterialTheme.colorScheme.tertiary.copy(alpha = 0.08f)
    SchedulerServiceHealthAxis.CRITICAL -> MaterialTheme.colorScheme.error.copy(alpha = 0.10f)
    SchedulerServiceHealthAxis.UNKNOWN -> MaterialTheme.colorScheme.surfaceVariant
  }

  Card(
    modifier = Modifier
      .fillMaxWidth(),
    shape = RoundedCornerShape(12.dp),
    colors = CardDefaults.cardColors(containerColor = background),
    elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
  ) {
    Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
      Text(
        text = "Service health: ${serviceHealth.label}",
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.SemiBold,
      )
      Text(serviceHealthMessage, style = MaterialTheme.typography.bodySmall)
      Text("Data freshness: ${freshness.label} ($freshnessLabel)", style = MaterialTheme.typography.bodySmall)
    }
  }
}

@Composable
private fun SchedulerItemSection(
  title: String,
  content: @Composable () -> Unit,
) {
  Card(
    modifier = Modifier.fillMaxWidth(),
    shape = RoundedCornerShape(12.dp),
    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
  ) {
    Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
      Text(title, style = MaterialTheme.typography.titleSmall)
      content()
    }
  }
}

@Composable
private fun <T> SchedulerStatusList(
  items: List<T>,
  emptyText: String,
  render: @Composable (T) -> Unit,
) {
  if (items.isEmpty()) {
    Text(text = emptyText, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    return
  }
  Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
    items.forEach { item ->
      Card(
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
      ) {
        Column(modifier = Modifier.padding(10.dp)) {
          render(item)
        }
      }
    }
  }
}

@Composable
private fun SchedulerQuickActions(
  onOpenSchedules: () -> Unit,
  onOpenPipelines: () -> Unit,
  onRefresh: () -> Unit,
) {
  Row(
    modifier = Modifier.fillMaxWidth(),
    horizontalArrangement = Arrangement.spacedBy(8.dp),
  ) {
    TextButton(onClick = onRefresh) { Text("Refresh") }
    TextButton(onClick = onOpenSchedules) { Text("Open Schedules") }
    TextButton(onClick = onOpenPipelines) { Text("Open Pipelines") }
  }
}
