package com.eiel.chat.ui

import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import android.widget.Toast
import com.eiel.chat.BuildConfig
import com.eiel.chat.data.ServerConfig
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Divider
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.focus.onFocusEvent
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import com.eiel.chat.data.RestartBlocker
import com.eiel.chat.data.CommitJournalEntry
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch

internal val CompactScreenPadding = 6.dp
internal val CompactSectionGap = 6.dp
internal val CompactCardPadding = 8.dp
internal val CompactInlineGap = 4.dp
internal val CompactFieldGap = 4.dp
internal val CompactTightGap = 2.dp
internal val CompactContentRadius = 12.dp
internal val CompactPaneBottomPeekPadding = 20.dp
internal val CompactRepoRowGap = 2.dp
internal val CompactRepoRowIndent = 28.dp
internal val TerminalBlockMinHeight = 210.dp
internal val TerminalBlockMaxHeight = 380.dp
internal val CommitJournalViewerMinHeight = 380.dp
internal val CommitJournalViewerMaxHeight = 560.dp
internal const val AdminOutputPreviewChars = 32_000
internal val AdminMonoTextStyle = TextStyle(
  fontSize = 12.sp,
  lineHeight = 15.sp,
  fontWeight = FontWeight.Normal,
  fontFamily = FontFamily.Monospace,
)
internal val CommitJournalMonoTextStyle = AdminMonoTextStyle.copy(
  fontSize = 13.sp,
  lineHeight = 18.sp,
)

internal fun trimAdminOutputForDisplay(
  text: String,
  maxChars: Int = AdminOutputPreviewChars,
): String {
  if (text.length <= maxChars) return text
  return buildString {
    append("[showing last ")
    append(maxChars)
    append(" of ")
    append(text.length)
    append(" chars; use Copy all for the full transcript]")
    append('\n')
    append(text.takeLast(maxChars))
  }
}

@Composable
internal fun compactScreenContentPadding(): PaddingValues {
  val navigationBottomPadding = WindowInsets.navigationBars.asPaddingValues().calculateBottomPadding()
  return PaddingValues(
    start = CompactScreenPadding,
    top = CompactScreenPadding,
    end = CompactScreenPadding,
    bottom = compactScreenBottomPadding(navigationBottomPadding),
  )
}

internal fun compactScreenBottomPadding(navigationBottomPadding: Dp): Dp =
  CompactScreenPadding + navigationBottomPadding + CompactPaneBottomPeekPadding

@Composable
fun AdminRoute(vm: AdminViewModel = viewModel()) {
  val context = LocalContext.current
  val lifecycleOwner = LocalLifecycleOwner.current
  val state by vm.uiState.collectAsStateWithLifecycle()
  val patchPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
    if (uri == null) return@rememberLauncherForActivityResult
    runCatching {
      val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
        ?: throw IllegalStateException("Could not read selected patch")
      val filename = queryDisplayName(context, uri).ifBlank { "mobile_patch.diff" }
      vm.applyPatchUpload(filename, bytes)
    }.onFailure { error ->
      Toast.makeText(context, error.message ?: "Could not read selected patch", Toast.LENGTH_SHORT).show()
    }
  }
  var batteryOptimizationIgnored by remember {
    mutableStateOf(isIgnoringBatteryOptimizations(context))
  }

  fun handlePendingInstall(path: String) {
    when (launchApkInstaller(context, path)) {
      ApkInstallerLaunchResult.STARTED_INSTALLER -> vm.onAppUpdateInstallHandled()
      ApkInstallerLaunchResult.AWAITING_INSTALL_PERMISSION -> {
        Toast.makeText(
          context,
          "Allow installs from this app, then return here to continue.",
          Toast.LENGTH_LONG,
        ).show()
      }
      ApkInstallerLaunchResult.MISSING_FILE -> {
        Toast.makeText(context, "APK file not found", Toast.LENGTH_SHORT).show()
        vm.onAppUpdateInstallHandled()
      }
      ApkInstallerLaunchResult.FAILED -> {
        Toast.makeText(context, "Could not open the Android installer", Toast.LENGTH_SHORT).show()
        vm.onAppUpdateInstallHandled()
      }
    }
  }

  fun refreshBatteryOptimizationStatus() {
    batteryOptimizationIgnored = isIgnoringBatteryOptimizations(context)
  }

  fun requestBatteryOptimizationExemption() {
    when (launchBatteryOptimizationSettings(context)) {
      BatteryOptimizationLaunchResult.ALREADY_IGNORING -> {
        refreshBatteryOptimizationStatus()
        Toast.makeText(
          context,
          "Battery optimization is already disabled for this app.",
          Toast.LENGTH_SHORT,
        ).show()
      }
      BatteryOptimizationLaunchResult.OPENED_REQUEST -> {
        Toast.makeText(
          context,
          "Allow unrestricted battery use for stable long-running chats.",
          Toast.LENGTH_LONG,
        ).show()
      }
      BatteryOptimizationLaunchResult.OPENED_SETTINGS -> {
        Toast.makeText(
          context,
          "Open battery settings and mark this app unrestricted.",
          Toast.LENGTH_LONG,
        ).show()
      }
      BatteryOptimizationLaunchResult.NOT_SUPPORTED -> {
        Toast.makeText(
          context,
          "This Android version does not use battery optimization exemptions.",
          Toast.LENGTH_SHORT,
        ).show()
      }
      BatteryOptimizationLaunchResult.FAILED -> {
        Toast.makeText(
          context,
          "Could not open Android battery settings.",
          Toast.LENGTH_SHORT,
        ).show()
      }
    }
  }

  LaunchedEffect(Unit) {
    refreshBatteryOptimizationStatus()
    vm.refreshHealth()
    vm.loadConfig()
    vm.refreshGit()
  }
  LaunchedEffect(state.appUpdatePendingInstallPath) {
    val path = state.appUpdatePendingInstallPath
    if (path.isNotBlank()) {
      handlePendingInstall(path)
    }
  }
  DisposableEffect(lifecycleOwner, state.appUpdatePendingInstallPath) {
    val observer = LifecycleEventObserver { _, event ->
      val path = state.appUpdatePendingInstallPath
      if (
        event == Lifecycle.Event.ON_RESUME &&
        path.isNotBlank() &&
        canInstallLocalApk(context)
      ) {
        handlePendingInstall(path)
      }
      if (event == Lifecycle.Event.ON_RESUME) {
        refreshBatteryOptimizationStatus()
      }
    }
    lifecycleOwner.lifecycle.addObserver(observer)
    onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
  }
  AdminScreen(
    state = state,
    vm = vm,
    onPickPatch = { patchPicker.launch(arrayOf("*/*")) },
  )
}

@Composable
fun AdminScreen(
  state: AdminUiState,
  vm: AdminViewModel,
  onPickPatch: () -> Unit,
) {
  val context = LocalContext.current
  val clipboard = LocalClipboardManager.current
  var restartDialogVisible by remember { mutableStateOf(false) }
  var confirmUserToolRunId by remember { mutableStateOf<String?>(null) }
  val appUpdateBrowserUrl = remember(state.appUpdateChannel) {
    buildMobileAppUpdateBrowserUrl(
      baseUrl = ServerConfig.resolveBaseUrl(context),
      channel = state.appUpdateChannel.ifBlank { appUpdateChannelForPackageName(context.packageName) },
    )
  }

  fun copyText(text: String, message: String) {
    clipboard.setText(AnnotatedString(text))
    Toast.makeText(context, message, Toast.LENGTH_SHORT).show()
  }

  Surface(
    modifier = Modifier
      .fillMaxSize(),
    color = MaterialTheme.colorScheme.background,
  ) {
    LazyColumn(
      modifier = Modifier.fillMaxSize(),
      contentPadding = compactScreenContentPadding(),
      verticalArrangement = Arrangement.spacedBy(CompactSectionGap),
    ) {
      item {
        MinimalServerPane(
          healthStatus = state.healthStatus,
          statusMessage = state.statusMessage,
          restartBlockers = state.restartBlockers,
          onRefresh = vm::refreshAll,
          onRestart = { restartDialogVisible = true },
        )
      }

      item {
        ArchivesCard(
          state = state,
          onToggleDebug = vm::onArchiveDebugOnlyChanged,
          onRefresh = vm::refreshArchives,
          onCreate = vm::createArchive,
          onDownload = vm::downloadArchive,
          onPickPatch = onPickPatch,
        )
      }

      if (state.downloads.isNotEmpty()) {
        item {
          DownloadsCard(
            downloads = state.downloads,
          )
        }
      }

      item {
        AlwaysVisibleTerminalCard(
          state = state,
          onCommit = vm::runCommitFromJournal,
          onSendCommand = vm::runCommand,
          onOpenDeploy = { openUrlInBrowser(context, appUpdateBrowserUrl) },
          onCommandChanged = vm::onCmdTextChanged,
          onUserToolSelected = vm::onSelectedUserToolChanged,
          onLoadUserTool = vm::loadSelectedUserTool,
          onRunUserTool = {
            val preset = adminUserToolPreset(state.selectedUserToolId)
            if (preset.risk.requiresConfirmation) {
              confirmUserToolRunId = preset.id
            } else {
              vm.runSelectedUserTool()
            }
          },
          onClear = vm::clearCommandAndTerminal,
          onCopyOutput = { copyText(state.terminalOutput, "Terminal output copied") },
          onGitStatus = vm::runGitStatus,
          onCtrlC = vm::interruptTerminal,
        )
      }

      item {
        MinimalGitPane(state = state)
      }
    }
  }

  if (restartDialogVisible) {
    AlertDialog(
      onDismissRequest = { restartDialogVisible = false },
      title = { Text("Restart WebUI") },
      text = { Text("Restart the backend web server now? Only do this when the plane is idle. Active tasks, open loops, or live scheduler claims will block the restart and keep the current work intact.") },
      confirmButton = {
        TextButton(
          onClick = {
            restartDialogVisible = false
            vm.restartServer()
          },
        ) {
          Text("Restart")
        }
      },
      dismissButton = {
        TextButton(onClick = { restartDialogVisible = false }) {
          Text("Cancel")
        }
      },
    )
  }

  val pendingTool = confirmUserToolRunId?.let(::adminUserToolPreset)
  if (pendingTool != null) {
    AlertDialog(
      onDismissRequest = { confirmUserToolRunId = null },
      title = { Text("Run ${pendingTool.label}?") },
      text = {
        Text(
          "Risk: ${pendingTool.risk.label}. This will run:\n\n${pendingTool.defaultCmd}",
        )
      },
      confirmButton = {
        TextButton(
          onClick = {
            confirmUserToolRunId = null
            vm.runSelectedUserTool()
          },
        ) {
          Text("Run")
        }
      },
      dismissButton = {
        TextButton(onClick = { confirmUserToolRunId = null }) {
          Text("Cancel")
        }
      },
    )
  }
}

@Composable
private fun MinimalAdminActionsCard(
  state: AdminUiState,
  onCommit: () -> Unit,
  onSendCommand: () -> Unit,
  onOpenDeploy: () -> Unit,
  onCommandChanged: (String) -> Unit,
) {
  AdminCard(title = "Admin") {
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
    ) {
      Button(
        onClick = onCommit,
        enabled = !state.gitRunning,
        modifier = Modifier.weight(1f),
      ) {
        Text(if (state.gitRunning) "Commit..." else "Commit")
      }
      Button(
        onClick = onSendCommand,
        enabled = state.cmdText.isNotBlank() && !state.cmdRunning,
        modifier = Modifier.weight(1f),
      ) {
        Text(if (state.cmdRunning) "Send CMD..." else "Send CMD")
      }
      Button(
        onClick = onOpenDeploy,
        modifier = Modifier.weight(1f),
      ) {
        Text("Deploy")
      }
    }
    AdminTextField(
      value = state.cmdText,
      onValueChange = onCommandChanged,
      modifier = Modifier.fillMaxWidth(),
      label = "Command",
      minLines = 3,
    )
    Text(
      "Command timeout: ${state.cmdTimeoutSec.ifBlank { "7000" }} sec",
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
  }
}

@Composable
private fun AlwaysVisibleTerminalCard(
  state: AdminUiState,
  onCommit: () -> Unit,
  onSendCommand: () -> Unit,
  onOpenDeploy: () -> Unit,
  onCommandChanged: (String) -> Unit,
  onUserToolSelected: (String) -> Unit,
  onLoadUserTool: () -> Unit,
  onRunUserTool: () -> Unit,
  onClear: () -> Unit,
  onCopyOutput: () -> Unit,
  onGitStatus: () -> Unit,
  onCtrlC: () -> Unit,
) {
  var toolMenuExpanded by rememberSaveable { mutableStateOf(false) }
  val selectedTool = remember(state.selectedUserToolId) { adminUserToolPreset(state.selectedUserToolId) }
  AdminCard(title = "Terminal") {
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
    ) {
      Button(
        onClick = onCommit,
        enabled = !state.gitRunning,
        modifier = Modifier.weight(1f),
      ) {
        Text(if (state.gitRunning) "Commit..." else "Commit")
      }
      Button(
        onClick = onSendCommand,
        enabled = state.cmdText.isNotBlank() && !state.cmdRunning,
        modifier = Modifier.weight(1f),
      ) {
        Text(if (state.cmdRunning) "Send CMD..." else "Send CMD")
      }
      Button(
        onClick = onOpenDeploy,
        modifier = Modifier.weight(1f),
      ) {
        Text("Deploy")
      }
    }
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
    ) {
      SmallButton("Clear", onClear)
      SmallButton("Copy", onCopyOutput)
      SmallButton("Git Status", onGitStatus)
      SmallDangerButton("CTRL-C", onCtrlC)
    }
    Column(verticalArrangement = Arrangement.spacedBy(CompactTightGap)) {
      Text("Operator tools", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
      Box {
        SmallButton("${selectedTool.label} ▾", onClick = { toolMenuExpanded = true })
        DropdownMenu(
          expanded = toolMenuExpanded,
          onDismissRequest = { toolMenuExpanded = false },
        ) {
          AdminUserToolPresets.forEach { preset ->
            DropdownMenuItem(
              text = { Text(preset.label) },
              onClick = {
                toolMenuExpanded = false
                onUserToolSelected(preset.id)
              },
            )
          }
        }
      }
      Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
      ) {
        SmallButton("Load default", onLoadUserTool)
        Button(
          onClick = onRunUserTool,
          enabled = !state.cmdRunning,
        ) {
          Text(if (state.cmdRunning) "Running..." else "Run tool")
        }
      }
      Text(
        "${selectedTool.description} Default timeout ${selectedTool.defaultTimeoutSec}s. Risk: ${selectedTool.risk.label}.",
        style = ChatLabelTextStyle,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
      )
      MonospaceBlock(
        text = selectedTool.defaultCmd,
        title = "default command",
        minHeight = 48.dp,
        maxHeight = 120.dp,
      )
    }
    AdminTextField(
      value = state.cmdText,
      onValueChange = onCommandChanged,
      modifier = Modifier.fillMaxWidth(),
      label = "Command",
      minLines = 3,
    )
    Text(
      "Command timeout: ${state.cmdTimeoutSec.ifBlank { "7000" }} sec",
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    MonospaceBlock(
      text = if (state.terminalOutput.isBlank()) "[terminal idle]" else trimAdminOutputForDisplay(state.terminalOutput),
      title = state.terminalOutputTitle,
      minHeight = 260.dp,
      maxHeight = 420.dp,
    )
  }
}

@Composable
private fun TerminalActionsCard(
  @Suppress("UNUSED_PARAMETER") state: AdminUiState,
  onClear: () -> Unit,
  onCopyCommand: () -> Unit,
  onGitStatus: () -> Unit,
  onCtrlC: () -> Unit,
) {
  AdminCard(title = "Terminal Pane") {
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
    ) {
      SmallButton("Clear", onClear)
      SmallButton("Copy", onCopyCommand)
      SmallButton("Git Status", onGitStatus)
      SmallDangerButton("CTRL-C", onCtrlC)
    }
  }
}

@Composable
private fun MinimalGitPane(state: AdminUiState) {
  AdminCard(title = "Git") {
    Text("Branch / worktree: ${state.gitBranch.ifBlank { "-" }}", style = ChatLabelTextStyle)
    Text("Head id: ${state.gitHead.ifBlank { "-" }}", style = ChatLabelTextStyle)
    Text("Current baseline: ${state.gitBaseline.ifBlank { "-" }}", style = ChatLabelTextStyle)
    Text(state.gitJournalStatus, style = ChatLabelTextStyle)
    if (state.gitJournalPreviewSubject.isNotBlank()) {
      Text("Preview: ${state.gitJournalPreviewSubject}", style = ChatLabelTextStyle)
    }
    if (state.gitJournalEntries.isNotEmpty()) {
      Text(
        formatCommitJournalViewerSummary(state.gitJournalEntries),
        style = ChatLabelTextStyle,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
      )
    }
    Divider()
    CommitJournalViewer(entries = state.gitJournalEntries)
  }
}

@Composable
private fun CommitJournalViewer(entries: List<CommitJournalEntry>) {
  if (entries.isEmpty()) {
    Text(
      "No pending journal entries yet. New code-changing turns should append here before commit.",
      style = ChatLabelTextStyle,
    )
    return
  }
  var selectedEntryId by rememberSaveable { mutableStateOf("") }
  val entryIds = remember(entries) { entries.map { it.entryId } }
  val pagerState = rememberPagerState(
    initialPage = resolveJournalViewerIndex(entryIds, selectedEntryId),
    pageCount = { entries.size },
  )
  val scope = rememberCoroutineScope()

  LaunchedEffect(entryIds) {
    if (entries.isEmpty()) {
      selectedEntryId = ""
      return@LaunchedEffect
    }
    val targetPage = resolveJournalViewerIndex(entryIds, selectedEntryId)
    if (pagerState.currentPage != targetPage) {
      pagerState.scrollToPage(targetPage)
    }
    selectedEntryId = entries[targetPage].entryId
  }
  LaunchedEffect(pagerState.currentPage, entryIds) {
    entries.getOrNull(pagerState.currentPage)?.let { selectedEntryId = it.entryId }
  }

  val currentPage = normalizeJournalViewerIndex(pagerState.currentPage, entries.size)
  val currentEntry = entries[currentPage]

  Column(verticalArrangement = Arrangement.spacedBy(CompactFieldGap)) {
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.SpaceBetween,
      verticalAlignment = Alignment.CenterVertically,
    ) {
      Text(
        "Journal ${currentPage + 1}/${entries.size}",
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.SemiBold,
      )
      CommitJournalOutcomeChip(currentEntry.outcome)
    }
    Text(
      "Swipe left or right to move through entries. The viewer opens on the newest fragment.",
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Row(
      modifier = Modifier
        .fillMaxWidth()
        .horizontalScroll(rememberScrollState()),
      horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
    ) {
      commitJournalEntryFacts(currentEntry).forEach { fact ->
        JournalMetaChip(
          text = fact,
          tint = MaterialTheme.colorScheme.surfaceVariant,
          contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
        )
      }
    }
    HorizontalPager(
      state = pagerState,
      modifier = Modifier
        .fillMaxWidth()
        .heightIn(min = CommitJournalViewerMinHeight, max = CommitJournalViewerMaxHeight),
      pageSpacing = 10.dp,
    ) { page ->
      CommitJournalEntryCard(entry = entries[page])
    }
    JournalPagerDots(
      currentPage = currentPage,
      total = entries.size,
    )
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.SpaceBetween,
      verticalAlignment = Alignment.CenterVertically,
    ) {
      SmallButton(
        text = "Prev",
        onClick = {
          scope.launch {
            pagerState.animateScrollToPage(stepJournalViewerIndex(currentPage, -1, entries.size))
          }
        },
        enabled = currentPage > 0,
      )
      SmallButton(
        text = "Next",
        onClick = {
          scope.launch {
            pagerState.animateScrollToPage(stepJournalViewerIndex(currentPage, 1, entries.size))
          }
        },
        enabled = currentPage < entries.lastIndex,
      )
    }
  }
}

@Composable
private fun CommitJournalEntryCard(entry: CommitJournalEntry) {
  val scrollState = rememberScrollState()
  Card(
    modifier = Modifier
      .fillMaxWidth()
      .heightIn(min = CommitJournalViewerMinHeight, max = CommitJournalViewerMaxHeight),
  ) {
    Column(
      modifier = Modifier
        .fillMaxWidth()
        .verticalScroll(scrollState)
        .padding(12.dp),
      verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
    ) {
      Text(
        entry.title.ifBlank { "Untitled journal entry" },
        style = MaterialTheme.typography.titleLarge,
        fontWeight = FontWeight.SemiBold,
      )
      Text(
        formatCommitJournalMeta(entry),
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
      )
      if (entry.scopeHint.isNotBlank()) {
        JournalMetaChip(
          text = entry.scopeHint,
          tint = MaterialTheme.colorScheme.primary.copy(alpha = 0.12f),
          contentColor = MaterialTheme.colorScheme.primary,
        )
      }
      CommitJournalSectionCard(
        title = "Summary",
        lines = entry.summaryBullets,
      )
      CommitJournalSectionCard(
        title = "Files",
        lines = entry.files,
        monospace = true,
      )
      CommitJournalSectionCard(
        title = "Validation",
        lines = entry.validation,
        monospace = true,
      )
      CommitJournalSectionCard(
        title = "Notes",
        lines = entry.notes,
      )
    }
  }
}

@Composable
private fun CommitJournalSectionCard(
  title: String,
  lines: List<String>,
  monospace: Boolean = false,
) {
  if (lines.isEmpty()) return
  Card(
    modifier = Modifier.fillMaxWidth(),
  ) {
    Column(
      modifier = Modifier
        .fillMaxWidth()
        .padding(10.dp),
      verticalArrangement = Arrangement.spacedBy(CompactTightGap),
    ) {
      Text(
        title,
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.SemiBold,
      )
      lines.forEach { line ->
        Text(
          text = "\u2022 $line",
          style = if (monospace) CommitJournalMonoTextStyle else MaterialTheme.typography.bodyMedium,
        )
      }
    }
  }
}

internal fun initialJournalViewerIndex(count: Int): Int = if (count <= 0) 0 else count - 1

internal fun normalizeJournalViewerIndex(index: Int, count: Int): Int {
  if (count <= 0) return 0
  return index.coerceIn(0, count - 1)
}

internal fun stepJournalViewerIndex(current: Int, delta: Int, count: Int): Int =
  normalizeJournalViewerIndex(current + delta, count)

internal fun resolveJournalViewerIndex(entryIds: List<String>, selectedEntryId: String): Int {
  if (entryIds.isEmpty()) return 0
  val existingIndex = entryIds.indexOf(selectedEntryId)
  return if (existingIndex >= 0) existingIndex else initialJournalViewerIndex(entryIds.size)
}

internal fun formatCommitJournalMeta(entry: CommitJournalEntry): String {
  val parts = mutableListOf<String>()
  if (entry.authorRole.isNotBlank()) parts += entry.authorRole
  if (entry.bindingId.isNotBlank()) parts += entry.bindingId
  if (entry.createdUtc.isNotBlank()) parts += formatIsoUtcForAdmin(entry.createdUtc)
  return parts.joinToString(" \u00b7 ")
}

internal fun commitJournalEntryFacts(entry: CommitJournalEntry): List<String> {
  val facts = mutableListOf<String>()
  if (entry.summaryBullets.isNotEmpty()) facts += pluralizeJournalFact(entry.summaryBullets.size, "summary", "summaries")
  if (entry.files.isNotEmpty()) facts += pluralizeJournalFact(entry.files.size, "file")
  if (entry.validation.isNotEmpty()) facts += pluralizeJournalFact(entry.validation.size, "check")
  if (entry.notes.isNotEmpty()) facts += pluralizeJournalFact(entry.notes.size, "note")
  return facts
}

internal fun formatCommitJournalViewerSummary(entries: List<CommitJournalEntry>): String {
  if (entries.isEmpty()) return ""
  val applied = entries.count { it.outcome == "applied" }
  val partial = entries.count { it.outcome == "partial" }
  val noteOnly = entries.count { it.outcome == "note_only" }
  val parts = mutableListOf("${entries.size} pending")
  if (applied > 0) parts += pluralizeJournalFact(applied, "applied")
  if (partial > 0) parts += pluralizeJournalFact(partial, "partial")
  if (noteOnly > 0) parts += pluralizeJournalFact(noteOnly, "note")
  return parts.joinToString(" \u00b7 ")
}

internal fun pluralizeJournalFact(count: Int, singular: String, plural: String = "${singular}s"): String =
  if (count == 1) "1 $singular" else "$count $plural"

internal fun formatIsoUtcForAdmin(utc: String): String {
  val trimmed = utc.trim()
  if (trimmed.isBlank()) return ""
  return runCatching {
    val parsed = java.time.Instant.parse(trimmed)
    SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(Date.from(parsed))
  }.getOrElse { trimmed }
}

@Composable
private fun CommitJournalOutcomeChip(outcome: String) {
  val normalized = outcome.trim().ifBlank { "pending" }
  val tint = when (normalized) {
    "applied" -> MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)
    "partial" -> MaterialTheme.colorScheme.tertiary.copy(alpha = 0.16f)
    "note_only" -> MaterialTheme.colorScheme.secondary.copy(alpha = 0.16f)
    else -> MaterialTheme.colorScheme.surfaceVariant
  }
  val contentColor = when (normalized) {
    "applied" -> MaterialTheme.colorScheme.primary
    "partial" -> MaterialTheme.colorScheme.tertiary
    "note_only" -> MaterialTheme.colorScheme.secondary
    else -> MaterialTheme.colorScheme.onSurfaceVariant
  }
  JournalMetaChip(
    text = normalized.replace('_', ' '),
    tint = tint,
    contentColor = contentColor,
  )
}

@Composable
private fun JournalMetaChip(
  text: String,
  tint: Color,
  contentColor: Color,
) {
  Box(
    modifier = Modifier
      .background(tint, RoundedCornerShape(999.dp))
      .padding(horizontal = 10.dp, vertical = 6.dp),
  ) {
    Text(
      text = text,
      style = ChatLabelTextStyle,
      color = contentColor,
    )
  }
}

@Composable
private fun JournalPagerDots(
  currentPage: Int,
  total: Int,
) {
  if (total <= 1) return
  Row(
    modifier = Modifier.fillMaxWidth(),
    horizontalArrangement = Arrangement.Center,
  ) {
    val start = when {
      total <= 7 -> 0
      currentPage <= 3 -> 0
      currentPage >= total - 4 -> total - 7
      else -> currentPage - 3
    }
    val endExclusive = minOf(total, start + 7)
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
      for (page in start until endExclusive) {
        Box(
          modifier = Modifier
            .size(width = if (page == currentPage) 20.dp else 8.dp, height = 8.dp)
            .background(
              color = if (page == currentPage) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline.copy(alpha = 0.35f),
              shape = RoundedCornerShape(999.dp),
            ),
        )
      }
    }
  }
}

@Composable
internal fun MinimalServerPane(
  healthStatus: String,
  statusMessage: String,
  restartBlockers: List<RestartBlocker>,
  onRefresh: () -> Unit,
  onRestart: () -> Unit,
) {
  Card(modifier = Modifier.fillMaxWidth()) {
    Column(
      modifier = Modifier
        .fillMaxWidth()
        .padding(horizontal = CompactCardPadding, vertical = 6.dp),
      verticalArrangement = Arrangement.spacedBy(CompactTightGap),
    ) {
      Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
      ) {
        Row(
          horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
          verticalAlignment = Alignment.CenterVertically,
        ) {
          Text("Server", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
          Box(
            modifier = Modifier
              .size(10.dp)
              .background(healthColor(healthStatus), RoundedCornerShape(999.dp)),
          )
          Text(healthStatus, style = ChatLabelTextStyle)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
          SmallButton("Refresh", onRefresh)
          SmallDangerButton("Restart", onRestart)
        }
      }
      if (statusMessage.isNotBlank()) {
        Text(
          statusMessage,
          color = MaterialTheme.colorScheme.primary,
          style = ChatLabelTextStyle,
        )
      }
      restartBlockers.forEach { blocker ->
        Text(
          "\u2022 ${blocker.summary.ifBlank { blocker.kind }}",
          color = MaterialTheme.colorScheme.error,
          style = ChatLabelTextStyle,
        )
      }
    }
  }
}

@Composable
private fun LegacyMinimalServerPane(
  healthStatus: String,
  statusMessage: String,
  onRefresh: () -> Unit,
  onRestart: () -> Unit,
) {
  AdminCard(title = "Server") {
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.SpaceBetween,
      verticalAlignment = Alignment.CenterVertically,
    ) {
      Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap), verticalAlignment = Alignment.CenterVertically) {
        Box(
          modifier = Modifier
            .size(10.dp)
            .background(healthColor(healthStatus), RoundedCornerShape(999.dp)),
        )
        Text(healthStatus, style = ChatLabelTextStyle)
      }
      Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
        SmallButton("Refresh", onRefresh)
        SmallDangerButton("Restart", onRestart)
      }
    }
    if (statusMessage.isNotBlank()) {
      Text(statusMessage, color = MaterialTheme.colorScheme.primary, style = ChatLabelTextStyle)
    }
  }
}

@Composable
internal fun HeaderCard(
  healthStatus: String,
  statusMessage: String,
  appUpdateBuilding: Boolean,
  appUpdateDownloading: Boolean,
  appUpdateChannel: String,
  appUpdateRemoteBuiltAt: String,
  appUpdateRemoteApkName: String,
  appUpdateMessage: String,
  appUpdateBrowserUrl: String,
  batteryOptimizationIgnored: Boolean,
  refreshMode: AdminRefreshMode,
  repoRoot: String,
  onRefresh: () -> Unit,
  onLoadConfig: () -> Unit,
  onRefreshModeChanged: (AdminRefreshMode) -> Unit,
  onOpenBrowserLink: () -> Unit,
  onCopyBrowserLink: () -> Unit,
  onRequestBatteryOptimizationExemption: () -> Unit,
  onRestart: () -> Unit,
) {
  AdminCard(title = "Status") {
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.SpaceBetween,
      verticalAlignment = Alignment.CenterVertically,
    ) {
      Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap), verticalAlignment = Alignment.CenterVertically) {
        Box(
          modifier = Modifier
            .size(10.dp)
            .background(healthColor(healthStatus), RoundedCornerShape(999.dp)),
        )
        Text(healthStatus)
      }
      Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
        SmallButton("Refresh", onRefresh)
        SmallButton("Load config", onLoadConfig)
        SmallDangerButton("Restart", onRestart)
      }
    }
    Spacer(modifier = Modifier.height(CompactFieldGap))
    Text("Repo: $repoRoot", style = ChatLabelTextStyle, maxLines = 1)
    if (statusMessage.isNotBlank()) {
      Text(statusMessage, color = MaterialTheme.colorScheme.primary, style = ChatLabelTextStyle)
    }
    Divider()
    Text("App deploy", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
    Text(
      buildString {
        append("Current ")
        append(BuildConfig.VERSION_NAME)
        if (appUpdateChannel.isNotBlank()) append(" · channel ").append(appUpdateChannel)
        if (appUpdateRemoteApkName.isNotBlank()) append(" · latest ").append(appUpdateRemoteApkName)
        if (appUpdateBuilding) append(" · building fresh apk")
        else if (appUpdateDownloading) append(" · downloading fresh apk")
        else if (appUpdateRemoteBuiltAt.isNotBlank()) append(" · published")
      },
      style = ChatLabelTextStyle,
      maxLines = 2,
      overflow = TextOverflow.Ellipsis,
    )
    if (appUpdateRemoteBuiltAt.isNotBlank()) {
      Text("Latest build: $appUpdateRemoteBuiltAt", style = ChatLabelTextStyle, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
    if (appUpdateMessage.isNotBlank()) {
      Text(appUpdateMessage, color = MaterialTheme.colorScheme.primary, style = ChatLabelTextStyle)
    }
    Text("Deploy always builds the newest APK on the server first, then downloads it here or through the browser link.", style = ChatLabelTextStyle)
    if (appUpdateBrowserUrl.isNotBlank()) {
      Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
        SmallButton("Open deploy link", onOpenBrowserLink)
        SmallButton("Copy deploy link", onCopyBrowserLink)
      }
      Text(appUpdateBrowserUrl, style = ChatLabelTextStyle, maxLines = 3, overflow = TextOverflow.Ellipsis)
    }
    Divider()
    Text("Background reliability", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
    Text(
      if (batteryOptimizationIgnored) {
        "Battery optimization is disabled for this app."
      } else {
        "Battery optimization is still enabled. Android may suspend long chat streams when the screen turns off."
      },
      style = ChatLabelTextStyle,
      color = if (batteryOptimizationIgnored) {
        MaterialTheme.colorScheme.primary
      } else {
        MaterialTheme.colorScheme.error
      },
    )
    Text(
      "Use the button below after each reinstall if Android resets the unrestricted background setting.",
      style = ChatLabelTextStyle,
    )
    Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
      SmallButton(
        if (batteryOptimizationIgnored) "Battery ok" else "Disable optimization",
        onClick = onRequestBatteryOptimizationExemption,
      )
    }
    Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
      AdminRefreshMode.entries.forEach { mode: AdminRefreshMode ->
        Chip(
          text = mode.label,
          selected = mode == refreshMode,
          onClick = { onRefreshModeChanged(mode) },
        )
      }
    }
  }
}

@Composable
internal fun PrimaryAdminConsoleCard(
  state: AdminUiState,
  onDeploy: () -> Unit,
  onCmdChanged: (String) -> Unit,
  onRunGit: () -> Unit,
  onRunCommand: () -> Unit,
) {
  Card(modifier = Modifier.fillMaxWidth()) {
    Column(
      modifier = Modifier
        .fillMaxWidth()
        .padding(CompactCardPadding),
      verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
    ) {
      Button(
        onClick = onDeploy,
        enabled = isPrimaryDeployEnabled(state),
        modifier = Modifier.fillMaxWidth(),
      ) {
        Text(primaryDeployButtonLabel(state))
      }
      Text(
        "Deploy builds the newest APK on the server, then downloads or installs it here.",
        style = ChatLabelTextStyle,
      )
      CommandComposerContent(
        state = state,
        onCmdChanged = onCmdChanged,
        onRunGit = onRunGit,
        onRunCommand = onRunCommand,
      )
    }
  }
}

@Composable
internal fun CommandComposerCard(
  state: AdminUiState,
  onCmdChanged: (String) -> Unit,
  onRunGit: () -> Unit,
  onRunCommand: () -> Unit,
) {
  AdminCard(title = "Quick actions") {
    CommandComposerContent(
      state = state,
      onCmdChanged = onCmdChanged,
      onRunGit = onRunGit,
      onRunCommand = onRunCommand,
    )
  }
}

@Composable
internal fun CommandComposerContent(
  state: AdminUiState,
  onCmdChanged: (String) -> Unit,
  onRunGit: () -> Unit,
  onRunCommand: () -> Unit,
) {
  val canRunGit = !state.gitRunning
  val canRunCommand = state.cmdText.isNotBlank() && !state.cmdRunning
  AdminTextField(
    value = state.cmdText,
    onValueChange = onCmdChanged,
    modifier = Modifier.fillMaxWidth(),
    label = "Command or git message",
    minLines = 2,
  )
  Row(
    modifier = Modifier.fillMaxWidth(),
    horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
    verticalAlignment = Alignment.CenterVertically,
  ) {
    Button(
      onClick = onRunGit,
      enabled = canRunGit,
      modifier = Modifier.weight(1f),
    ) {
      Text(if (state.gitRunning) "Git..." else "Git")
    }
    Button(
      onClick = onRunCommand,
      enabled = canRunCommand,
      modifier = Modifier.weight(1f),
    ) {
      Text(if (state.cmdRunning) "Terminal..." else "Terminal")
    }
  }
  Text(
    "Git now runs the unified journal-backed commit+tag script with an auto-assigned baseline tag. Terminal still uses this text as the command. Deploy fresh is separate and only builds/downloads the APK.",
    style = ChatLabelTextStyle,
  )
}

@Composable
internal fun TerminalCard(
  state: AdminUiState,
  onKillCommand: () -> Unit,
  onKillGit: () -> Unit,
  onClear: () -> Unit,
  onCopy: (String) -> Unit,
) {
  AdminCard(title = "Terminal") {
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.SpaceBetween,
      verticalAlignment = Alignment.CenterVertically,
    ) {
      Text(state.terminalOutputTitle, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
      Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
        if (state.cmdRunning) {
          SmallDangerButton("Kill cmd", onKillCommand)
        }
        if (state.gitRunning) {
          SmallDangerButton("Kill git", onKillGit)
        }
        SmallButton("Clear", onClear)
        SmallButton("Copy all", onClick = { onCopy(state.terminalOutput) })
      }
    }
    val output = state.terminalOutput
    if (output.isBlank()) {
      Text("Run deploy, git, or command actions to stream output here.", style = ChatLabelTextStyle)
    } else {
      MonospaceBlock(
        text = trimAdminOutputForDisplay(output),
        title = state.terminalOutputTitle,
      )
    }
  }
}

@Composable
internal fun AdminConfigurationCard(
  state: AdminUiState,
  expanded: Boolean,
  onToggleExpanded: () -> Unit,
  onAdminTokenChanged: (String) -> Unit,
  onCwdChanged: (String) -> Unit,
  onTimeoutChanged: (String) -> Unit,
) {
  CollapsibleAdminCard(
    title = "Advanced settings",
    expanded = expanded,
    onToggle = onToggleExpanded,
  ) {
    Text("Command settings", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
    Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
      AdminTextField(
        value = state.cmdCwd,
        onValueChange = onCwdChanged,
        modifier = Modifier.weight(1f),
        singleLine = true,
        label = "cwd",
      )
      AdminTextField(
        value = state.cmdTimeoutSec,
        onValueChange = onTimeoutChanged,
        modifier = Modifier.weight(1f),
        singleLine = true,
        label = "timeout sec",
      )
    }
    Text(if (state.cmdRunning) "command running" else "command idle", style = ChatLabelTextStyle)

    Divider()
    Text("Server access", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
    AdminTextField(
      value = state.adminToken,
      onValueChange = onAdminTokenChanged,
      modifier = Modifier.fillMaxWidth(),
      singleLine = true,
      label = "Admin token",
    )
  }
}

@Composable
internal fun GitConfigurationCard(
  state: AdminUiState,
  onGitBaselineChanged: (String) -> Unit,
  onGitDoCommitChanged: (Boolean) -> Unit,
  onGitDoTagChanged: (Boolean) -> Unit,
  onGitUseJournalMessageChanged: (Boolean) -> Unit,
  onRefreshGit: () -> Unit,
) {
  AdminCard(title = "Git settings") {
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.SpaceBetween,
      verticalAlignment = Alignment.CenterVertically,
    ) {
      Column(verticalArrangement = Arrangement.spacedBy(CompactTightGap)) {
        Text(if (state.gitRunning) "running" else state.gitBranch, style = ChatLabelTextStyle)
      }
      Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
        SmallButton("Refresh", onRefreshGit)
      }
    }
    Text("HEAD ${state.gitHead} · ${state.gitDescribe}", style = ChatLabelTextStyle, maxLines = 1, overflow = TextOverflow.Ellipsis)
    Text("Next tag: ${state.gitBaseline.ifBlank { "-" }}", style = ChatLabelTextStyle)
    Text(state.gitJournalStatus, style = ChatLabelTextStyle)
    if (state.gitJournalPreviewSubject.isNotBlank()) {
      Text("Preview subject: ${state.gitJournalPreviewSubject}", style = ChatLabelTextStyle)
    }
    Text(
      "Actor-facing git runs now go through tools/git/git-commit-tag.sh. The baseline tag is auto-assigned and the message is composed from the journal. Deploy fresh does not commit or tag anything.",
      style = ChatLabelTextStyle,
    )
    if (state.gitStatusPorcelain.isNotBlank()) {
      Text(
        "Status: ${state.gitStatusPorcelain.lineSequence().firstOrNull().orEmpty()}",
        style = ChatLabelTextStyle,
        maxLines = 2,
        overflow = TextOverflow.Ellipsis,
      )
    }
  }
}

internal fun primaryDeployButtonLabel(state: AdminUiState): String =
  when {
    state.appUpdateBuilding -> "Deploying..."
    state.appUpdateDownloading -> "Downloading..."
    else -> "Deploy fresh"
  }

internal fun isPrimaryDeployEnabled(state: AdminUiState): Boolean =
  !state.appUpdateBuilding && !state.appUpdateDownloading

@Composable
internal fun ArchivesCard(
  state: AdminUiState,
  onToggleDebug: (Boolean) -> Unit,
  onRefresh: () -> Unit,
  onCreate: () -> Unit,
  onDownload: (String) -> Unit,
  onPickPatch: () -> Unit,
) {
  AdminCard(title = "Archives") {
    Text(
      "Create or save a repository archive through the plane-backed archive tool.",
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    if (state.localDownloadsDir.isNotBlank()) {
      Text(
        "Downloads save locally to ${state.localDownloadsDir}",
        style = ChatLabelTextStyle,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
      )
    }
    ToggleRow("Debug-only snapshot", state.archiveDebugOnly, onToggleDebug)
    Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
      SmallButton("Refresh", onRefresh)
      SmallButton("Create + Save", onCreate)
      SmallButton(if (state.patchApplying) "Applying..." else "Pick Patch", onPickPatch, enabled = !state.patchApplying)
    }
    if (state.archivesMessage.isNotBlank()) {
      Text(state.archivesMessage, color = MaterialTheme.colorScheme.primary, style = ChatLabelTextStyle)
    }
    if (state.patchApplyMessage.isNotBlank()) {
      Text(state.patchApplyMessage, color = MaterialTheme.colorScheme.primary, style = ChatLabelTextStyle)
    }
    if (state.archives.isEmpty()) {
      Text("No archives found.", style = ChatLabelTextStyle)
    } else {
      val latestArchive = state.archives.first()
      Text(
        "Showing the most recent archive only.",
        style = ChatLabelTextStyle,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
      )
      ItemRow(
        title = latestArchive.name,
        subtitle = "${formatBytes(latestArchive.bytes)} · ${formatTime(latestArchive.mtime)}",
        actionLabel = "Download",
        onAction = { onDownload(latestArchive.name) },
      )
    }
  }
}

@Composable
internal fun ManifestsCard(
  state: AdminUiState,
  onRefresh: () -> Unit,
  onStop: (String) -> Unit,
  onForce: (String) -> Unit,
  onPause: (String) -> Unit,
  onResume: (String) -> Unit,
) {
  AdminCard(title = "Manifests") {
    Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
      SmallButton("Refresh", onRefresh)
    }
    if (state.manifestsMessage.isNotBlank()) {
      Text(state.manifestsMessage, color = MaterialTheme.colorScheme.primary, style = ChatLabelTextStyle)
    }
    if (state.manifests.isEmpty()) {
      Text("No durable manifest jobs found.", style = ChatLabelTextStyle)
    } else {
      Column(verticalArrangement = Arrangement.spacedBy(CompactSectionGap)) {
        state.manifests.forEach { job ->
          Card {
            Column(modifier = Modifier.padding(CompactCardPadding), verticalArrangement = Arrangement.spacedBy(CompactTightGap)) {
              Text(job.jobId, fontWeight = FontWeight.SemiBold, style = ChatLabelTextStyle)
              Text("state=${job.state} pid=${job.pid ?: "-"}", style = ChatLabelTextStyle)
              if (job.reportDir.isNotBlank()) Text("report_dir=${job.reportDir}", style = ChatLabelTextStyle)
              if (job.manifest.isNotBlank()) Text("manifest=${job.manifest}", style = ChatLabelTextStyle)
              Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
                SmallButton("Pause", onClick = { onPause(job.jobId) })
                SmallButton("Resume", onClick = { onResume(job.jobId) })
                SmallButton("Stop", onClick = { onStop(job.jobId) })
                SmallDangerButton("Force") { onForce(job.jobId) }
              }
            }
          }
        }
      }
    }
  }
}

@Composable
internal fun RepoBrowserCard(
  state: AdminUiState,
  onRefresh: () -> Unit,
  onUp: () -> Unit,
  onOpenDir: (String) -> Unit,
  onViewFile: (String) -> Unit,
  onDownloadFile: (String) -> Unit,
  onSelectionChanged: (String, Boolean) -> Unit,
  onZip: () -> Unit,
  onClearSelection: () -> Unit,
) {
  AdminCard(title = "Repo Browser") {
    Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
      SmallButton("Up", onUp)
      SmallButton("List", onRefresh)
    }
    Text(state.fsPath.ifBlank { "/" }, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap)) {
      SmallButton("Zip selected", onZip)
      SmallButton("Clear selection", onClearSelection)
    }
    Text("Selected: ${state.fsSelections.size}", style = ChatLabelTextStyle)
    if (state.replaceNote.isNotBlank()) {
      Text(state.replaceNote, color = MaterialTheme.colorScheme.primary, style = ChatLabelTextStyle)
    }
    if (state.fsEntries.isEmpty()) {
      Text("No entries.", style = ChatLabelTextStyle)
    } else {
      Column(verticalArrangement = Arrangement.spacedBy(CompactRepoRowGap)) {
        state.fsEntries.forEachIndexed { index, entry ->
          Column(verticalArrangement = Arrangement.spacedBy(CompactRepoRowGap)) {
            Row(
              modifier = Modifier.fillMaxWidth(),
              verticalAlignment = Alignment.CenterVertically,
              horizontalArrangement = Arrangement.spacedBy(CompactRepoRowGap),
            ) {
              Checkbox(
                modifier = Modifier.size(20.dp),
                checked = state.fsSelections.contains(entry.path),
                onCheckedChange = { checked: Boolean -> onSelectionChanged(entry.path, checked) },
              )
              Text(
                text = (if (entry.isDir) "DIR " else "FILE ") + entry.name,
                modifier = Modifier.weight(1f),
                fontWeight = FontWeight.SemiBold,
                style = ChatLabelTextStyle,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
              )
              Row(horizontalArrangement = Arrangement.spacedBy(CompactRepoRowGap)) {
                if (entry.isDir) {
                  SmallButton("Open", onClick = { onOpenDir(entry.path) })
                } else {
                  SmallButton("View", onClick = { onViewFile(entry.path) })
                  SmallButton("Save", onClick = { onDownloadFile(entry.path) })
                }
              }
            }
            Text(
              text = "${formatBytes(entry.bytes)} · ${if (entry.isDir) "directory" else formatTime(entry.mtime)}",
              modifier = Modifier.padding(start = CompactRepoRowIndent),
              style = ChatLabelTextStyle,
              maxLines = 1,
              overflow = TextOverflow.Ellipsis,
            )
            if (index < state.fsEntries.lastIndex) {
              Divider()
            }
          }
        }
      }
    }
  }
}

@Composable
internal fun DownloadsCard(
  downloads: List<DownloadedFile>,
) {
  AdminCard(title = "Recent Downloads") {
    Text(
      "Files are stored locally on this device at the paths shown below.",
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Column(verticalArrangement = Arrangement.spacedBy(CompactFieldGap)) {
      downloads.forEach { item ->
        Card {
          Column(
            modifier = Modifier
              .fillMaxWidth()
              .padding(CompactCardPadding),
            verticalArrangement = Arrangement.spacedBy(CompactTightGap),
          ) {
            Text(item.filename, style = ChatLabelTextStyle, fontWeight = FontWeight.SemiBold)
            Text(item.absolutePath, style = ChatLabelTextStyle)
          }
        }
      }
    }
  }
}

@Composable
internal fun AdminCard(
  title: String,
  modifier: Modifier = Modifier,
  content: @Composable ColumnScope.() -> Unit,
) {
  Card(modifier = modifier.fillMaxWidth()) {
    Column(
      modifier = Modifier
        .fillMaxWidth()
        .padding(CompactCardPadding),
      verticalArrangement = Arrangement.spacedBy(CompactTightGap),
      content = {
        Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        Spacer(modifier = Modifier.height(CompactTightGap))
        content()
      },
    )
  }
}

@Composable
internal fun CollapsibleAdminCard(
  title: String,
  expanded: Boolean,
  onToggle: () -> Unit,
  modifier: Modifier = Modifier,
  content: @Composable ColumnScope.() -> Unit,
) {
  Card(modifier = modifier.fillMaxWidth()) {
    Column(
      modifier = Modifier
        .fillMaxWidth()
        .padding(CompactCardPadding),
      verticalArrangement = Arrangement.spacedBy(CompactTightGap),
    ) {
      Row(
        modifier = Modifier
          .fillMaxWidth()
          .clickable(onClick = onToggle),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
      ) {
        Row(horizontalArrangement = Arrangement.spacedBy(CompactInlineGap), verticalAlignment = Alignment.CenterVertically) {
          Text(if (expanded) "▾" else "▸", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.primary)
          Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        }
        SmallButton(if (expanded) "Hide" else "Show", onToggle)
      }
      if (expanded) {
        Spacer(modifier = Modifier.height(CompactTightGap))
        content()
      }
    }
  }
}

@Composable
internal fun ToggleRow(
  label: String,
  checked: Boolean,
  onCheckedChange: (Boolean) -> Unit,
) {
  Row(
    modifier = Modifier
      .fillMaxWidth()
      .clickable { onCheckedChange(!checked) },
    verticalAlignment = Alignment.CenterVertically,
  ) {
    Checkbox(checked = checked, onCheckedChange = onCheckedChange)
    Text(label, style = ChatLabelTextStyle)
  }
}

@Composable
internal fun SmallButton(
  text: String,
  onClick: () -> Unit,
  enabled: Boolean = true,
) {
  TextButton(
    onClick = onClick,
    enabled = enabled,
    contentPadding = PaddingValues(horizontal = 4.dp, vertical = 0.dp),
  ) {
    Text(text, style = ChatLabelTextStyle)
  }
}

@Composable
internal fun SmallDangerButton(
  text: String,
  onClick: () -> Unit,
) {
  TextButton(
    onClick = onClick,
    contentPadding = PaddingValues(horizontal = 4.dp, vertical = 0.dp),
  ) {
    Text(text, style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.error)
  }
}

@Composable
internal fun Chip(
  text: String,
  selected: Boolean,
  onClick: () -> Unit,
  enabled: Boolean = true,
) {
  val borderColor = when {
    !enabled -> MaterialTheme.colorScheme.outline.copy(alpha = 0.35f)
    selected -> MaterialTheme.colorScheme.primary
    else -> MaterialTheme.colorScheme.outline.copy(alpha = 0.7f)
  }
  val backgroundColor = when {
    !enabled -> MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.42f)
    selected -> MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)
    else -> MaterialTheme.colorScheme.surface
  }
  val textColor = when {
    !enabled -> MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.72f)
    selected -> MaterialTheme.colorScheme.primary
    else -> MaterialTheme.colorScheme.onSurface
  }
  Box(
    modifier = Modifier
      .background(backgroundColor, RoundedCornerShape(999.dp))
      .border(
        width = 1.dp,
        color = borderColor,
        shape = RoundedCornerShape(999.dp),
      )
      .clickable(enabled = enabled, onClick = onClick)
      .padding(horizontal = 8.dp, vertical = 4.dp),
  ) {
    Text(text, style = ChatLabelTextStyle, color = textColor)
  }
}

@Composable
internal fun ItemRow(
  title: String,
  subtitle: String,
  actionLabel: String,
  onAction: () -> Unit,
) {
  Card {
    Row(
      modifier = Modifier
        .fillMaxWidth()
        .padding(CompactCardPadding),
      horizontalArrangement = Arrangement.SpaceBetween,
      verticalAlignment = Alignment.CenterVertically,
    ) {
      Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(CompactTightGap)) {
        Text(title, style = ChatLabelTextStyle, fontWeight = FontWeight.SemiBold)
        Text(subtitle, style = ChatLabelTextStyle)
      }
      SmallButton(actionLabel, onAction)
    }
  }
}

@Composable
@OptIn(ExperimentalFoundationApi::class)
internal fun AdminTextField(
  value: String,
  onValueChange: (String) -> Unit,
  label: String,
  modifier: Modifier = Modifier,
  singleLine: Boolean = false,
  minLines: Int = 1,
  enabled: Boolean = true,
) {
  val bringIntoViewRequester = remember { BringIntoViewRequester() }
  val scope = rememberCoroutineScope()
  OutlinedTextField(
    value = value,
    onValueChange = onValueChange,
    modifier = modifier
      .bringIntoViewRequester(bringIntoViewRequester)
      .onFocusEvent { focusState ->
        if (focusState.isFocused) {
          scope.launch {
            delay(250)
            bringIntoViewRequester.bringIntoView()
          }
        }
      }
      .adminFieldSurface(),
    enabled = enabled,
    singleLine = singleLine,
    minLines = minLines,
    label = { Text(label) },
    textStyle = ChatLabelTextStyle,
    colors = OutlinedTextFieldDefaults.colors(
      focusedContainerColor = Color.Transparent,
      unfocusedContainerColor = Color.Transparent,
      disabledContainerColor = Color.Transparent,
      errorContainerColor = Color.Transparent,
      focusedBorderColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.82f),
      unfocusedBorderColor = MaterialTheme.colorScheme.outline.copy(alpha = 0.78f),
      focusedLabelColor = MaterialTheme.colorScheme.primary,
      unfocusedLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
      cursorColor = MaterialTheme.colorScheme.primary,
    ),
  )
}

internal fun Modifier.adminFieldSurface(): Modifier = composed {
  val colorScheme = MaterialTheme.colorScheme
  this
    .background(
      brush = Brush.verticalGradient(
        colors = listOf(
          colorScheme.surfaceVariant.copy(alpha = 0.96f),
          colorScheme.secondaryContainer.copy(alpha = 0.34f),
          colorScheme.primary.copy(alpha = 0.10f),
        ),
      ),
      shape = RoundedCornerShape(16.dp),
    )
    .border(
      width = 1.dp,
      color = colorScheme.outline.copy(alpha = 0.82f),
      shape = RoundedCornerShape(16.dp),
    )
}

@Composable
internal fun MonospaceBlock(
  text: String,
  title: String = "terminal",
  minHeight: Dp = TerminalBlockMinHeight,
  maxHeight: Dp = TerminalBlockMaxHeight,
) {
  val scrollState = rememberScrollState()
  var followBottom by rememberSaveable { mutableStateOf(true) }
  LaunchedEffect(text) {
    if (followBottom) {
      scrollState.scrollTo(scrollState.maxValue)
    }
  }
  LaunchedEffect(scrollState) {
    snapshotFlow { scrollState.value to scrollState.maxValue }.collect { (value, maxValue) ->
      val nearBottom = maxValue <= 0 || value >= maxValue - 24
      if (nearBottom != followBottom) {
        followBottom = nearBottom
      }
    }
  }
  Column(
    modifier = Modifier
      .fillMaxWidth()
      .heightIn(min = minHeight, max = maxHeight)
      .background(Color(0xFF050706), RoundedCornerShape(16.dp))
      .border(
        width = 1.dp,
        color = Color(0xFF284F37),
        shape = RoundedCornerShape(16.dp),
      )
      .padding(0.dp),
    verticalArrangement = Arrangement.spacedBy(0.dp),
  ) {
    Row(
      modifier = Modifier
        .fillMaxWidth()
        .background(Color(0xFF0D1411))
        .padding(horizontal = 10.dp, vertical = 6.dp),
      verticalAlignment = Alignment.CenterVertically,
    ) {
      listOf(Color(0xFFFF5F56), Color(0xFFFFBD2E), Color(0xFF27C93F)).forEach { dot ->
        Box(
          modifier = Modifier
            .padding(end = 6.dp)
            .size(8.dp)
            .background(dot, RoundedCornerShape(999.dp)),
        )
      }
      Text(
        text = title,
        style = ChatLabelTextStyle,
        color = Color(0xFF8FB39B),
        modifier = Modifier.padding(start = 4.dp),
      )
    }
    Box(
      modifier = Modifier
        .fillMaxWidth()
        .weight(1f)
        .verticalScroll(scrollState)
        .padding(horizontal = 10.dp, vertical = 10.dp),
    ) {
      Text(
        text = text,
        style = AdminMonoTextStyle.copy(fontSize = 13.sp, lineHeight = 17.sp),
        color = Color(0xFFD8F4DD),
      )
    }
  }
}

internal fun healthColor(status: String) = when (status.trim().lowercase(Locale.ROOT)) {
  "ok" -> androidx.compose.ui.graphics.Color(0xFF27AE60)
  "restarting" -> androidx.compose.ui.graphics.Color(0xFFE67E22)
  else -> androidx.compose.ui.graphics.Color(0xFFD64545)
}

internal fun formatBytes(value: Long): String {
  var v = value.toDouble().coerceAtLeast(0.0)
  val units = listOf("B", "KB", "MB", "GB")
  var idx = 0
  while (v >= 1024.0 && idx < units.lastIndex) {
    v /= 1024.0
    idx += 1
  }
  return if (idx == 0) "${v.toLong()} ${units[idx]}" else String.format(Locale.US, "%.1f %s", v, units[idx])
}

internal fun formatTime(epochSeconds: Double): String {
  if (epochSeconds <= 0.0) return "-"
  val date = Date((epochSeconds * 1000.0).toLong())
  return SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(date)
}

internal fun openUrlInBrowser(context: android.content.Context, url: String) {
  val safeUrl = url.trim()
  if (safeUrl.isBlank()) {
    Toast.makeText(context, "Update link unavailable", Toast.LENGTH_SHORT).show()
    return
  }
  val intent = Intent(Intent.ACTION_VIEW, Uri.parse(safeUrl)).apply {
    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
  }
  context.startActivity(intent)
}

internal fun queryDisplayName(context: android.content.Context, uri: Uri): String {
  return runCatching {
    context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
      val idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
      if (idx >= 0 && cursor.moveToFirst()) {
        return@runCatching cursor.getString(idx).orEmpty()
      }
    }
    ""
  }.getOrDefault("")
}
