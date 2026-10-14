package com.eiel.chat.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.eiel.chat.data.PlaneLogControlEvent
import com.eiel.chat.data.PlaneLogSnapshot
import com.eiel.chat.data.PlaneLogWatchdog
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight

@Composable
fun PlaneLogRoute(
  vm: AdminViewModel = viewModel(),
  onOpenAdmin: () -> Unit,
) {
  val state by vm.uiState.collectAsStateWithLifecycle()

  LaunchedEffect(Unit) {
    vm.refreshPlaneLog()
  }

  PlaneLogScreen(
    snapshot = state.planeLog,
    loading = state.planeLogLoading,
    error = state.planeLogError,
    onRefresh = vm::refreshPlaneLog,
    onOpenAdmin = onOpenAdmin,
  )
}

@Composable
private fun PlaneLogScreen(
  snapshot: PlaneLogSnapshot?,
  loading: Boolean,
  error: String,
  onRefresh: () -> Unit,
  onOpenAdmin: () -> Unit,
) {
  LazyColumn(
    modifier = Modifier.fillMaxSize(),
    contentPadding = compactScreenContentPadding(),
    verticalArrangement = Arrangement.spacedBy(CompactSectionGap),
  ) {
    item {
      Card(modifier = Modifier.fillMaxWidth()) {
        SelectionContainer {
          Column(
            modifier = Modifier
              .fillMaxWidth()
              .padding(CompactCardPadding),
            verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
          ) {
            Text("Plane Log", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Text(
              snapshot?.dashboard?.headline?.ifBlank { "Recent plane transactions and runtime confirmations." }
                ?: "Recent plane transactions and runtime confirmations.",
              style = MaterialTheme.typography.bodyMedium,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
              SmallButton(if (loading) "Refreshing..." else "Refresh", onRefresh, enabled = !loading)
              SmallButton("Admin", onOpenAdmin)
            }
            if (error.isNotBlank()) {
              Text(error, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.error)
            } else if (snapshot != null) {
              Text(buildPlaneLogSummary(snapshot), style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
          }
        }
      }
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
            Text("Current State", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
            if (snapshot == null && loading) {
              Text("Loading plane status...", style = MaterialTheme.typography.bodyMedium)
            } else if (snapshot != null) {
              val triage = snapshot.triage
              if (triage.headline.isNotBlank()) {
                Text(triage.headline, style = MaterialTheme.typography.bodyMedium)
              }
              val counts = buildList {
                if (triage.state.isNotBlank()) add("state=${triage.state}")
                add("conflicts=${triage.conflictCount}")
                add("gaps=${triage.gapCount}")
              }
              Text(counts.joinToString("  "), style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
              if (triage.nextSteps.isNotEmpty()) {
                Text("Next steps", style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold)
                triage.nextSteps.take(3).forEach { step ->
                  Text("• $step", style = MaterialTheme.typography.bodyMedium)
                }
              }
              if (snapshot.recentControls.isEmpty()) {
                Text(
                  "No recent plane control transactions recorded.",
                  style = MaterialTheme.typography.bodyMedium,
                  color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
              }
            }
          }
        }
      }
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
            Text("Watchdogs", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
            if (snapshot == null && loading) {
              Text("Loading watchdog status...", style = MaterialTheme.typography.bodyMedium)
            } else if (snapshot != null) {
              val watchdog = snapshot.watchdog
              Text(buildWatchdogSummary(watchdog), style = MaterialTheme.typography.bodyMedium)
              val latestBits = buildList {
                if (watchdog.auditLatestUtc.isNotBlank()) {
                  add("audit=${watchdog.auditLatestHealth.ifBlank { "unknown" }} @ ${watchdog.auditLatestUtc}")
                }
                if (watchdog.selfhealLatestUtc.isNotBlank()) {
                  add("selfheal=${watchdog.selfhealLatestHealth.ifBlank { "unknown" }} @ ${watchdog.selfhealLatestUtc}")
                }
              }
              if (latestBits.isNotEmpty()) {
                Text(latestBits.joinToString("  "), style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
              }
            }
          }
        }
      }
    }

    if (!snapshot?.recentControls.isNullOrEmpty()) {
      item {
        Card(modifier = Modifier.fillMaxWidth()) {
          SelectionContainer {
            Column(
              modifier = Modifier
                .fillMaxWidth()
                .padding(CompactCardPadding),
              verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
            ) {
              Text("Recent Transactions", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
              Text(
                "Latest confirmed or pending actions recorded by the plane.",
                style = MaterialTheme.typography.bodyMedium,
              )
            }
          }
        }
      }
    }

    items(snapshot?.recentControls.orEmpty()) { event ->
      PlaneLogEventCard(event = event)
    }
  }
}

@Composable
private fun PlaneLogEventCard(event: PlaneLogControlEvent) {
  val colorScheme = MaterialTheme.colorScheme
  val accent = when (event.confirmationState.lowercase()) {
    "confirmed" -> colorScheme.primary
    "pending" -> colorScheme.tertiary
    "failed" -> colorScheme.error
    else -> colorScheme.outline
  }
  Card(modifier = Modifier.fillMaxWidth()) {
    SelectionContainer {
      Column(
        modifier = Modifier
          .fillMaxWidth()
          .padding(CompactCardPadding),
        verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
      ) {
        Text(event.actionId.ifBlank { "control" }, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
        Text(
          "${event.confirmationState.ifBlank { "unknown" }} · ${event.requestedUtc.ifBlank { "time unknown" }}",
          style = ChatLabelTextStyle,
          color = accent,
        )
        if (event.confirmationSummary.isNotBlank()) {
          Text(event.confirmationSummary, style = MaterialTheme.typography.bodyMedium)
        }
        val detailParts = buildList {
          if (event.writer.isNotBlank()) add("writer=${event.writer}")
          if (event.durableSignal.isNotBlank()) add("signal=${event.durableSignal}")
          event.invocationOk?.let { add("invocation_ok=$it") }
          if (event.controlRequestId.isNotBlank()) add("id=${event.controlRequestId}")
        }
        if (detailParts.isNotEmpty()) {
          Text(detailParts.joinToString("  "), style = ChatLabelTextStyle, color = colorScheme.onSurfaceVariant)
        }
      }
    }
  }
}

private fun buildPlaneLogSummary(snapshot: PlaneLogSnapshot): String {
  val dashboard = snapshot.dashboard
  val bits = buildList {
    if (dashboard.operatorState.isNotBlank()) add("state=${dashboard.operatorState}")
    add("tasks=${dashboard.activeTaskCount}")
    add("loops=${dashboard.openLoopCount}")
    if (dashboard.queueStatus.isNotBlank()) add("queue=${dashboard.queueStatus}")
    add("workflows=${dashboard.workflowCount}")
    add("scheduled=${dashboard.scheduledJobCount}")
    if (dashboard.schedulerLastTickUtc.isNotBlank()) add("last_tick=${dashboard.schedulerLastTickUtc}")
    if (dashboard.backendState.isNotBlank()) add("backend=${dashboard.backendState}")
  }
  return bits.joinToString("  ")
}

private fun buildWatchdogSummary(watchdog: PlaneLogWatchdog): String {
  val bits = buildList {
    add("audit=${if (watchdog.auditTimerActive) "on" else "off"}")
    if (watchdog.auditTimerState.isNotBlank()) add("audit_state=${watchdog.auditTimerState}")
    add("selfheal=${if (watchdog.selfhealTimerActive) "on" else "off"}")
    if (watchdog.selfhealTimerState.isNotBlank()) add("selfheal_state=${watchdog.selfhealTimerState}")
  }
  return bits.joinToString("  ")
}
