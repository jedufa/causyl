package com.eiel.chat.ui

import android.app.Application
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.NavigationDrawerItemDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.eiel.chat.data.ServerConfig
import kotlinx.coroutines.launch

private enum class RootView(
  val label: String,
) {
  HOME("Home"),
  CHAT("Chat"),
  PLANE_LOG("Plane Log"),
  BACKLOG("The plane"),
  PIPELINES("Job Pipelines"),
  SCHEDULES("Job Monitor"),
  SCHEDULER("Scheduler"),
  ADMIN("Admin"),
}

@Composable
fun AppShell(
  chatVm: ChatViewModel? = null,
  adminVm: AdminViewModel = viewModel(),
  sharedDocument: SharedDocumentPayload? = null,
  onSharedDocumentConsumed: () -> Unit = {},
  initialRouteName: String = RootView.HOME.name,
) {
  val application = LocalContext.current.applicationContext as Application
  val context = LocalContext.current
  val resolvedChatVm = chatVm ?: viewModel(factory = ChatViewModel.factory(application))
  val backlogVm: BacklogViewModel = viewModel(factory = BacklogViewModel.factory(application))
  val adminState by adminVm.uiState.collectAsStateWithLifecycle()
  val backlogState by backlogVm.uiState.collectAsStateWithLifecycle()
  var selected by rememberSaveable { mutableStateOf(initialRouteName) }
  var serverDialogVisible by rememberSaveable { mutableStateOf(false) }
  var serverUrlDraft by rememberSaveable { mutableStateOf(ServerConfig.resolveBaseUrl(context)) }
  var sharedDocType by rememberSaveable(sharedDocument?.requestKey) {
    mutableStateOf(sharedDocument?.let(::inferSharedDocumentType)?.name ?: SharedDocumentType.PIS.name)
  }
  var sharedProcessInFlight by rememberSaveable(sharedDocument?.requestKey) { mutableStateOf(false) }
  var sharedProcessObservedRunning by rememberSaveable(sharedDocument?.requestKey) { mutableStateOf(false) }
  var sharedProcessError by rememberSaveable(sharedDocument?.requestKey) { mutableStateOf("") }
  var sharedTrackedRequestId by rememberSaveable(sharedDocument?.requestKey) { mutableStateOf("") }
  val current = runCatching { RootView.valueOf(selected) }.getOrDefault(RootView.CHAT)
  val drawerState = androidx.compose.material3.rememberDrawerState(initialValue = androidx.compose.material3.DrawerValue.Closed)
  val scope = rememberCoroutineScope()
  val colorScheme = MaterialTheme.colorScheme

  ModalNavigationDrawer(
    drawerState = drawerState,
    drawerContent = {
      ModalDrawerSheet(
        modifier = Modifier.navigationBarsPadding(),
        drawerContainerColor = colorScheme.surface,
      ) {
        SelectionContainer {
          Column(
            modifier = Modifier
              .fillMaxSize()
              .padding(horizontal = 8.dp, vertical = 10.dp),
          ) {
            Text("Causyl", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
            RootView.entries.forEach { item ->
              NavigationDrawerItem(
                label = { Text(item.label) },
                selected = item == current,
                onClick = {
                  selected = item.name
                  scope.launch { drawerState.close() }
                },
                icon = {
                  Box(
                    modifier = Modifier
                      .size(10.dp)
                      .background(
                        if (item == current) colorScheme.primary else colorScheme.outline.copy(alpha = 0.8f),
                        shape = androidx.compose.foundation.shape.RoundedCornerShape(999.dp),
                      ),
                  )
                },
                modifier = Modifier
                  .padding(bottom = 2.dp)
                  .testTag("app-shell-nav-item-${item.name.lowercase()}"),
                colors = NavigationDrawerItemDefaults.colors(
                  selectedContainerColor = colorScheme.primary.copy(alpha = 0.10f),
                  selectedTextColor = colorScheme.primary,
                  selectedIconColor = colorScheme.primary,
                ),
              )
            }
            Spacer(modifier = Modifier.weight(1f))
          }
        }
      }
    },
  ) {
    Surface(color = colorScheme.background, modifier = Modifier.fillMaxSize()) {
      SelectionContainer {
        Column(
          modifier = Modifier
            .fillMaxSize()
            .statusBarsPadding(),
        ) {
          Row(
            modifier = Modifier
              .fillMaxWidth()
              .padding(horizontal = 6.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
          ) {
            TextButton(
              onClick = { scope.launch { drawerState.open() } },
              contentPadding = PaddingValues(horizontal = 6.dp, vertical = 4.dp),
              modifier = Modifier.testTag("app-shell-menu-button"),
            ) {
              Icon(Icons.Filled.Menu, contentDescription = "Open menu")
              Text("Menu", modifier = Modifier.padding(start = 4.dp))
            }
            TextButton(
              onClick = {
                serverUrlDraft = ServerConfig.resolveBaseUrl(context)
                serverDialogVisible = true
              },
              contentPadding = PaddingValues(horizontal = 6.dp, vertical = 4.dp),
            ) {
              Text("Server")
            }
            Spacer(modifier = Modifier.weight(1f))
            Text(current.label, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
          }

          Box(modifier = Modifier.fillMaxSize()) {
            when (current) {
              RootView.HOME -> OverviewRoute(
                chatVm = resolvedChatVm,
                adminVm = adminVm,
                onOpenChat = { selected = RootView.CHAT.name },
                onOpenPlaneLog = { selected = RootView.PLANE_LOG.name },
                onOpenBacklog = { selected = RootView.BACKLOG.name },
                onOpenAdmin = { selected = RootView.ADMIN.name },
              )
              RootView.CHAT -> ChatRoute(vm = resolvedChatVm, adminVm = adminVm)
              RootView.PLANE_LOG -> PlaneLogRoute(vm = adminVm, onOpenAdmin = { selected = RootView.ADMIN.name })
              RootView.BACKLOG -> BacklogRoute(vm = backlogVm)
              RootView.PIPELINES -> PipelinesRoute(vm = adminVm)
              RootView.SCHEDULES -> SchedulesRoute(vm = adminVm)
              RootView.SCHEDULER -> SchedulerRoute(
                vm = adminVm,
                onOpenSchedules = { selected = RootView.SCHEDULES.name },
                onOpenPipelines = { selected = RootView.PIPELINES.name },
              )
              RootView.ADMIN -> AdminRoute(vm = adminVm)
            }
          }
        }
      }
    }
  }

  if (serverDialogVisible) {
    AlertDialog(
      onDismissRequest = { serverDialogVisible = false },
      title = { SelectionContainer { Text("Server URL") } },
      text = {
        SelectionContainer {
          OutlinedTextField(
            value = serverUrlDraft,
            onValueChange = { serverUrlDraft = it },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            label = { Text("Base URL") },
            supportingText = { Text("Used by chat, ledger, and admin fetches.") },
          )
        }
      },
      confirmButton = {
        TextButton(
          onClick = {
            ServerConfig.saveBaseUrl(context, serverUrlDraft)
            serverUrlDraft = ServerConfig.resolveBaseUrl(context)
            resolvedChatVm.refreshPisOptions()
            adminVm.refreshHealth()
            serverDialogVisible = false
          },
        ) {
          SelectionContainer { Text("Apply") }
        }
      },
      dismissButton = {
        Row {
          TextButton(
            onClick = {
              ServerConfig.resetBaseUrl(context)
              serverUrlDraft = ServerConfig.resolveBaseUrl(context)
            },
          ) {
            SelectionContainer { Text("Default") }
          }
          TextButton(onClick = { serverDialogVisible = false }) {
            SelectionContainer { Text("Close") }
          }
        }
      },
    )
  }

  LaunchedEffect(sharedDocument?.requestKey, sharedProcessInFlight, sharedDocType, sharedTrackedRequestId, adminState.patchApplying, adminState.patchApplyOutcome, adminState.patchApplyRequestId, adminState.patchApplyMessage, backlogState.createRunning, backlogState.createOutcome, backlogState.createRequestId, backlogState.error) {
    val doc = sharedDocument ?: return@LaunchedEffect
    if (!sharedProcessInFlight) return@LaunchedEffect
    val selectedType = runCatching { SharedDocumentType.valueOf(sharedDocType) }.getOrDefault(inferSharedDocumentType(doc))
    val running = if (selectedType == SharedDocumentType.PATCH) {
      adminState.patchApplying && adminState.patchApplyRequestId == sharedTrackedRequestId
    } else {
      backlogState.createRunning && backlogState.createRequestId == sharedTrackedRequestId
    }
    if (running) {
      sharedProcessObservedRunning = true
      return@LaunchedEffect
    }
    if (!sharedProcessObservedRunning) return@LaunchedEffect
    if (selectedType == SharedDocumentType.PATCH) {
      if (adminState.patchApplyRequestId != sharedTrackedRequestId) return@LaunchedEffect
      if (adminState.patchApplyOutcome == "success") {
        sharedProcessInFlight = false
        sharedProcessObservedRunning = false
        sharedTrackedRequestId = ""
        sharedProcessError = ""
        onSharedDocumentConsumed()
      } else {
        sharedProcessInFlight = false
        sharedProcessObservedRunning = false
        sharedTrackedRequestId = ""
        sharedProcessError = adminState.patchApplyMessage.ifBlank { adminState.statusMessage.ifBlank { "Patch apply failed." } }
      }
    } else {
      if (backlogState.createRequestId != sharedTrackedRequestId) return@LaunchedEffect
      if (backlogState.createOutcome == "success") {
        sharedProcessInFlight = false
        sharedProcessObservedRunning = false
        sharedTrackedRequestId = ""
        sharedProcessError = ""
        onSharedDocumentConsumed()
      } else if (backlogState.createOutcome == "failed") {
        sharedProcessInFlight = false
        sharedProcessObservedRunning = false
        sharedTrackedRequestId = ""
        sharedProcessError = backlogState.error
      }
    }
  }

  if (sharedDocument != null) {
    val selectedType = runCatching { SharedDocumentType.valueOf(sharedDocType) }.getOrDefault(inferSharedDocumentType(sharedDocument))
    AlertDialog(
      onDismissRequest = {
        if (!sharedProcessInFlight) {
          sharedProcessError = ""
          onSharedDocumentConsumed()
        }
      },
      title = { SelectionContainer { Text("Import shared document") } },
      text = {
        SelectionContainer {
          Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
              "Choose how the plane should process ${sharedDocument.filename.ifBlank { "this document" }}.",
              style = MaterialTheme.typography.bodyMedium,
            )
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
              SharedDocumentType.entries.forEach { type ->
                FilterChip(
                  selected = type == selectedType,
                  onClick = { sharedDocType = type.name },
                  label = { Text(type.label) },
                )
              }
            }
            Text(
              sharedDocument.text.take(280).ifBlank { "Binary or non-text document shared." },
              style = MaterialTheme.typography.bodySmall,
            )
            if (sharedDocument.truncated) {
              Text(
                "This document exceeds the mobile intake limit and will not be processed until you share a smaller file.",
                style = MaterialTheme.typography.bodySmall,
                color = colorScheme.error,
              )
            }
            if (sharedProcessError.isNotBlank()) {
              Text(
                sharedProcessError,
                style = MaterialTheme.typography.bodySmall,
                color = colorScheme.error,
              )
            }
          }
        }
      },
      confirmButton = {
        TextButton(
          onClick = {
            if (sharedDocument.truncated) {
              sharedProcessError = "Shared document is too large for mobile intake."
              return@TextButton
            }
            sharedProcessError = ""
            sharedProcessInFlight = true
            sharedProcessObservedRunning = false
            when (selectedType) {
              SharedDocumentType.PATCH -> {
                selected = RootView.ADMIN.name
                adminVm.applySharedPatch(sharedDocument.filename, sharedDocument.bytes, sharedDocument.text)
                sharedTrackedRequestId = adminVm.uiState.value.patchApplyRequestId
              }
              SharedDocumentType.PIS,
              SharedDocumentType.AUDIT_REPORT,
              SharedDocumentType.PATCH_REPORT,
              -> {
                selected = RootView.BACKLOG.name
                backlogVm.importSharedDocument(
                  type = selectedType,
                  filename = sharedDocument.filename,
                  mimeType = sharedDocument.mimeType,
                  text = sharedDocument.text,
                )
                sharedTrackedRequestId = backlogVm.uiState.value.createRequestId
              }
            }
          },
          enabled = !sharedProcessInFlight,
        ) {
          SelectionContainer { Text(if (sharedProcessInFlight) "Processing..." else "Process") }
        }
      },
      dismissButton = {
        TextButton(
          onClick = {
            if (!sharedProcessInFlight) {
              sharedProcessError = ""
              onSharedDocumentConsumed()
            }
          },
          enabled = !sharedProcessInFlight,
        ) {
          SelectionContainer { Text("Cancel") }
        }
      },
    )
  }
}
