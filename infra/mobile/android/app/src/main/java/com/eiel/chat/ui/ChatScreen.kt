package com.eiel.chat.ui

import android.app.Application
import android.content.Context
import android.net.Uri
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.sizeIn
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.foundation.layout.wrapContentHeight
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.Image
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.changedToUpIgnoreConsumed
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalViewConfiguration
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.hypot
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.ContentPaste
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.VolumeUp
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeoutOrNull
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.eiel.chat.R
import com.eiel.chat.domain.AgentChatState
import com.eiel.chat.domain.AgentTab
import com.eiel.chat.domain.AGENT_PROMPT_QUEUE_MAX_DEPTH
import com.eiel.chat.domain.ChatAttachedDocument
import com.eiel.chat.domain.ChatMessage
import com.eiel.chat.domain.ChatUiState
import com.eiel.chat.domain.MessageRole
import com.eiel.chat.domain.MessageStatus
import com.eiel.chat.domain.PisOption
import com.eiel.chat.domain.StreamPhase
import androidx.compose.runtime.DisposableEffect

private const val CHAT_ATTACHMENT_MAX_BYTES = 128 * 1024

internal val ChatBodyTextStyle = TextStyle(
  fontSize = 16.sp,
  lineHeight = 24.sp,
  fontWeight = FontWeight.Normal,
)

internal val ChatLabelTextStyle = TextStyle(
  fontSize = 12.sp,
  lineHeight = 16.sp,
  fontWeight = FontWeight.Medium,
)

internal const val BubbleCopyLongPressMs = 750L
internal const val BubbleSpeechLongPressMs = 3000L
internal const val BubbleSpeechLongPressMotionSlopMultiplier = 2.5f
internal const val SpeechStopButtonTestTag = "speech_stop_button"
internal const val RunnerWaitStatusStripTestTag = "runner_wait_status_strip"
internal const val ExecutiveStatusCardTestTag = "executive_status_card"
internal const val StopTurnConfirmDialogTitle = "Stop Current Reply?"
private const val HostVerificationRequestMarker = "HOST_VERIFICATION_REQUEST_JSON:"
private val HostVerificationTimeoutRegex = Regex("\"timeout_s\"\\s*:\\s*(\\d+)")
internal const val DefaultRunnerWaitTimeoutMs = 300_000L

internal enum class BubblePrimaryLongPressAction {
  COPY,
  SPEAK,
  STOP_SPEAKING,
}

internal enum class ComposerPrimaryAction {
  NONE,
  SEND,
  QUEUE,
  STOP,
}

internal enum class ExecutiveStatusAction {
  RETRY_LAST_FAILED,
}

internal enum class ChatTopSection {
  EXECUTIVE_STATUS,
  ERROR_BANNER,
  MESSAGE_LIST,
}

internal enum class ComposerAuxSection {
  RUNNER_WAIT,
  QUEUE_PREVIEW,
}

internal enum class StopQuickActionEvent {
  REQUEST_STOP,
  CONFIRM_STOP,
  DISMISS,
}

internal data class StopQuickActionTransition(
  val confirmationPending: Boolean,
  val shouldDispatchStop: Boolean,
)

internal data class ExecutiveStatusCardModel(
  val headline: String,
  val detail: String,
  val action: ExecutiveStatusAction? = null,
)

internal fun chatTopSectionOrder(
  hasExecutiveStatus: Boolean,
  hasErrorBanner: Boolean,
): List<ChatTopSection> {
  val sections = mutableListOf<ChatTopSection>()
  if (hasExecutiveStatus) sections += ChatTopSection.EXECUTIVE_STATUS
  if (hasErrorBanner) sections += ChatTopSection.ERROR_BANNER
  sections += ChatTopSection.MESSAGE_LIST
  return sections
}

internal fun composerAuxSectionOrder(
  showRunnerWait: Boolean,
  queueCount: Int,
): List<ComposerAuxSection> {
  val sections = mutableListOf<ComposerAuxSection>()
  if (showRunnerWait) sections += ComposerAuxSection.RUNNER_WAIT
  if (queueCount > 0) sections += ComposerAuxSection.QUEUE_PREVIEW
  return sections
}

internal fun reduceStopQuickAction(
  confirmationPending: Boolean,
  event: StopQuickActionEvent,
): StopQuickActionTransition {
  return when (event) {
    StopQuickActionEvent.REQUEST_STOP -> StopQuickActionTransition(
      confirmationPending = true,
      shouldDispatchStop = false,
    )
    StopQuickActionEvent.CONFIRM_STOP -> StopQuickActionTransition(
      confirmationPending = false,
      shouldDispatchStop = confirmationPending,
    )
    StopQuickActionEvent.DISMISS -> StopQuickActionTransition(
      confirmationPending = false,
      shouldDispatchStop = false,
    )
  }
}

/** Decide the primary long-press behavior for a chat bubble. */
internal fun bubblePrimaryLongPressAction(
  role: MessageRole,
  messageId: String,
  activeSpeechMessageId: String?,
): BubblePrimaryLongPressAction {
  if (role == MessageRole.USER) return BubblePrimaryLongPressAction.COPY
  if (activeSpeechMessageId == messageId) return BubblePrimaryLongPressAction.STOP_SPEAKING
  return BubblePrimaryLongPressAction.SPEAK
}

/** Return the hold duration required before the primary bubble action fires. */
internal fun bubblePrimaryLongPressTimeoutMs(role: MessageRole): Long {
  return if (role == MessageRole.USER) BubbleCopyLongPressMs else BubbleSpeechLongPressMs
}

/** Allow more finger drift for speech hold-to-activate than for copy/select holds. */
internal fun bubblePrimaryLongPressCancelDistancePx(role: MessageRole, touchSlopPx: Float): Float {
  val base = touchSlopPx.coerceAtLeast(0f)
  return if (role == MessageRole.USER) {
    base
  } else {
    base * BubbleSpeechLongPressMotionSlopMultiplier
  }
}

internal fun composerPrimaryAction(hasDraft: Boolean, isBusy: Boolean): ComposerPrimaryAction {
  return when {
    hasDraft && isBusy -> ComposerPrimaryAction.QUEUE
    hasDraft -> ComposerPrimaryAction.SEND
    isBusy -> ComposerPrimaryAction.STOP
    else -> ComposerPrimaryAction.NONE
  }
}

internal fun executiveStatusCardModel(
  state: AgentChatState,
  serverHealthyKnown: Boolean,
  serverHealthy: Boolean,
): ExecutiveStatusCardModel? {
  val isBusy = state.isStreaming || state.isSubmitting
  val hasRetryAction = !isBusy && !state.lastFailedPrompt.isNullOrBlank()
  if (serverHealthyKnown && !serverHealthy) {
    return ExecutiveStatusCardModel(
      headline = "Offline from server",
      detail = "Durable turn state may have changed on the server. Reconnect, then refresh.",
      action = if (hasRetryAction) ExecutiveStatusAction.RETRY_LAST_FAILED else null,
    )
  }
  if (isBusy && state.streamPhase == StreamPhase.HOST_VERIFICATION_WAIT) {
    return ExecutiveStatusCardModel(
      headline = "Waiting on verification runner",
      detail = "Turn completion is pending external runner evidence.",
    )
  }
  val progress = state.streamingProgress?.trim().orEmpty()
  if (isBusy && progress.isNotBlank()) {
    val normalized = progress.lowercase()
    if (
      normalized.contains("reconnect") ||
      normalized.contains("resum") ||
      normalized.contains("reattach")
    ) {
      return ExecutiveStatusCardModel(
        headline = "Reattaching to running turn",
        detail = "Reply stream is being restored from durable turn/session state.",
      )
    }
  }
  val latestAssistant = state.messages.lastOrNull { it.role == MessageRole.ASSISTANT }
  return when (latestAssistant?.status) {
    MessageStatus.INTERRUPTED -> ExecutiveStatusCardModel(
      headline = "Last turn interrupted",
      detail = "Partial output was preserved. Retry only if the next action is still needed.",
      action = if (hasRetryAction) ExecutiveStatusAction.RETRY_LAST_FAILED else null,
    )
    MessageStatus.FAILED -> ExecutiveStatusCardModel(
      headline = "Last turn failed",
      detail = "No durable completion was confirmed.",
      action = if (hasRetryAction) ExecutiveStatusAction.RETRY_LAST_FAILED else null,
    )
    MessageStatus.CANCELLED -> ExecutiveStatusCardModel(
      headline = "Turn cancellation requested",
      detail = "Treat this as pending until canonical session state confirms final outcome.",
    )
    else -> null
  }
}

/** Return the top-level busy label for a lane, distinguishing runner waits from generic streaming. */
internal fun busyStatusLabel(state: AgentChatState): String? {
  if (!(state.isStreaming || state.isSubmitting)) return null
  return if (state.streamPhase == StreamPhase.HOST_VERIFICATION_WAIT) {
    "Waiting on runner"
  } else {
    "Thinking"
  }
}

/** Format elapsed milliseconds as mm:ss for compact status UI. */
internal fun formatElapsedTimer(elapsedMs: Long): String {
  val totalSeconds = (elapsedMs / 1000L).coerceAtLeast(0L)
  val minutes = totalSeconds / 60L
  val seconds = totalSeconds % 60L
  return String.format("%02d:%02d", minutes, seconds)
}

/** Resolve the host-verification timeout from the request marker, or fall back to the mobile default. */
internal fun hostVerificationTimeoutMs(
  state: AgentChatState,
  fallbackMs: Long = DefaultRunnerWaitTimeoutMs,
): Long {
  val markerPayload = state.messages
    .asReversed()
    .firstOrNull { message ->
      message.role == MessageRole.ASSISTANT && message.content.contains(HostVerificationRequestMarker)
    }
    ?.content
    ?.substringAfter(HostVerificationRequestMarker, missingDelimiterValue = "")
    .orEmpty()
  val timeoutS = HostVerificationTimeoutRegex.find(markerPayload)?.groupValues?.getOrNull(1)?.toLongOrNull()
  return ((timeoutS ?: (fallbackMs / 1000L)).coerceIn(1L, 3600L)) * 1000L
}

/** Build the bottom-strip runner wait text. */
internal fun runnerWaitStatusText(
  remainingMs: Long,
  pulseMs: Long,
): String {
  val wave = ".".repeat(((pulseMs / 500L) % 4L).toInt())
  return "Waiting for runner$wave  ${formatElapsedTimer(remainingMs)}"
}

private data class WorkflowBubblePalette(
  val background: Color,
  val border: Color,
  val text: Color,
)

@Composable
fun ChatRoute(
  vm: ChatViewModel? = null,
  adminVm: AdminViewModel = viewModel(),
) {
  val application = LocalContext.current.applicationContext as Application
  val context = LocalContext.current
  val resolvedVm = vm ?: viewModel(factory = ChatViewModel.factory(application))
  val state by resolvedVm.uiState.collectAsStateWithLifecycle()
  val adminState by adminVm.uiState.collectAsStateWithLifecycle()
  val attachDocumentLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
    val safeUri = uri ?: return@rememberLauncherForActivityResult
    when (val outcome = readChatAttachment(context, safeUri)) {
      is ChatAttachmentLoadOutcome.Error -> {
        Toast.makeText(context, outcome.message, Toast.LENGTH_SHORT).show()
      }
      is ChatAttachmentLoadOutcome.Success -> {
        val attachment = outcome.result
        resolvedVm.addAttachedDocument(
          filename = attachment.filename,
          mimeType = attachment.mimeType,
          text = attachment.text,
          truncated = attachment.truncated,
        )
      }
    }
  }
  ChatScreen(
    state = state,
    fileViewerPath = adminState.fsPreviewPath,
    fileViewerText = adminState.fsPreviewText,
    fileViewerTruncated = adminState.fsPreviewTruncated,
    fileViewerLoading = adminState.fsPreviewLoading,
    fileViewerError = adminState.fsPreviewError,
    onTabSelected = resolvedVm::onTabSelected,
    onDraftChanged = resolvedVm::onDraftChanged,
    onSelectedPisChanged = resolvedVm::onSelectedPisChanged,
    onPisSelectorOpened = resolvedVm::refreshPisOptions,
    onWorkflowCommentaryChanged = resolvedVm::onWorkflowCommentaryChanged,
    onAddAttachedDocumentClicked = { attachDocumentLauncher.launch(arrayOf("*/*")) },
    onRemoveAttachedDocumentClicked = resolvedVm::removeAttachedDocument,
    onSendClicked = resolvedVm::onSendClicked,
    onLaunchWorkflowClicked = resolvedVm::onLaunchWorkflowClicked,
    onWorkflowCancelRequested = resolvedVm::onWorkflowCancelRequested,
    onStopClicked = resolvedVm::onStopClicked,
    onQueueTailPopClicked = resolvedVm::onQueueTailPopClicked,
    onRetryLastFailedClicked = resolvedVm::onRetryLastFailedClicked,
    onCapContinueClicked = resolvedVm::onCapContinueClicked,
    onCapIncreaseClicked = resolvedVm::onCapIncreaseClicked,
    onCapDialogDismissed = resolvedVm::onCapDialogDismissed,
    onOpenFileReference = adminVm::viewFsFile,
  )
}

@Composable
fun ChatScreen(
  state: ChatUiState,
  fileViewerPath: String = "",
  fileViewerText: String = "",
  fileViewerTruncated: Boolean = false,
  fileViewerLoading: Boolean = false,
  fileViewerError: String = "",
  onTabSelected: (AgentTab) -> Unit,
  onDraftChanged: (String) -> Unit,
  onSelectedPisChanged: (String) -> Unit,
  onPisSelectorOpened: () -> Unit,
  onWorkflowCommentaryChanged: (String) -> Unit,
  onAddAttachedDocumentClicked: () -> Unit,
  onRemoveAttachedDocumentClicked: (String) -> Unit,
  onSendClicked: () -> Unit,
  onLaunchWorkflowClicked: () -> Unit,
  onWorkflowCancelRequested: (String) -> Unit,
  onStopClicked: () -> Unit,
  onQueueTailPopClicked: () -> Unit,
  onRetryLastFailedClicked: () -> Unit,
  onCapContinueClicked: () -> Unit,
  onCapIncreaseClicked: () -> Unit,
  onCapDialogDismissed: () -> Unit,
  onOpenFileReference: (String) -> Unit = {},
) {
  val activeState = state.active
  val listState = rememberLazyListState()
  val density = LocalDensity.current
  val clipboardManager = LocalClipboardManager.current
  val context = LocalContext.current
  var composerHeightPx by remember { mutableIntStateOf(0) }
  var selectedBubbleForCopyDialog by remember { mutableStateOf<ChatMessage?>(null) }
  var selectedErrorForCopyDialog by remember { mutableStateOf<String?>(null) }
  var pendingWorkflowCancelId by remember { mutableStateOf<String?>(null) }
  var activeRepoFilePath by remember { mutableStateOf<String?>(null) }
  var lastCopyToastAtMs by remember { mutableLongStateOf(0L) }
  var copyToast by remember { mutableStateOf<Toast?>(null) }
  var activeSpeechMessageId by remember { mutableStateOf<String?>(null) }
  var pendingDirectStopConfirmation by remember { mutableStateOf(false) }
  val speechController = remember(context.applicationContext) { BubbleSpeechController(context.applicationContext) }

  val showCopyToast: (String) -> Unit = { message ->
    val now = System.currentTimeMillis()
    if (now - lastCopyToastAtMs >= 700L) {
      copyToast?.cancel()
      copyToast = Toast.makeText(context, message, Toast.LENGTH_SHORT)
      copyToast?.show()
      lastCopyToastAtMs = now
    }
  }

  DisposableEffect(speechController) {
    speechController.onSpeechStateChanged = { activeSpeechMessageId = it }
    speechController.onError = showCopyToast
    onDispose {
      speechController.shutdown()
    }
  }

  LaunchedEffect(activeState.tab) {
    speechController.stop()
  }

  LaunchedEffect(
    activeState.tab,
    activeState.messages.size,
    activeState.messages.lastOrNull()?.content?.length,
    activeState.messages.lastOrNull()?.status,
  ) {
    if (activeState.messages.isNotEmpty()) {
      listState.scrollToItem(activeState.messages.lastIndex)
    }
  }

  Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
    Box(modifier = Modifier.fillMaxSize()) {
      val composerOverlayHeight = with(density) { composerHeightPx.toDp() }.coerceAtLeast(120.dp)
      val listBottomPadding = composerOverlayHeight + 20.dp

      Column(modifier = Modifier.fillMaxSize()) {
        val selectedPis = state.pisOptions.firstOrNull { it.uid == activeState.selectedPisId }
        Row(
          modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 10.dp, vertical = 8.dp),
          verticalAlignment = Alignment.CenterVertically,
        ) {
          Text(
            text = activeState.displayTitle,
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.weight(1f),
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
          )
          if (state.selectedTab == AgentTab.ORCHESTRATOR) {
            PisSelector(
              selectedPisId = activeState.selectedPisId,
              options = state.pisOptions,
              loading = state.pisOptionsLoading,
              error = state.pisOptionsError,
              enabled = !activeState.isLaunchingWorkflow && activeState.activeWorkflowId == null,
              onSelectedPisChanged = onSelectedPisChanged,
              onOpened = onPisSelectorOpened,
            )
            Spacer(modifier = Modifier.size(8.dp))
          }
          Box(
            modifier = Modifier
              .size(8.dp)
              .background(
                color = when {
                  !state.serverHealthyKnown -> Color(0xFF9CA3AF)
                  state.serverHealthy -> Color(0xFF27AE60)
                  else -> Color(0xFFD64545)
                },
                shape = RoundedCornerShape(999.dp),
              ),
          )
        }

        if (state.selectedTab == AgentTab.ORCHESTRATOR && selectedPis != null) {
          SelectedPisDetails(
            option = selectedPis,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 2.dp),
          )
        }

        Row(
          modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 8.dp),
          horizontalArrangement = Arrangement.spacedBy(CompactInlineGap),
        ) {
          state.tabs.forEach { tab ->
            val tabState = state.agentStates[tab] ?: AgentChatState(tab = tab)
            AgentTabChip(
              tab = tab,
              selected = state.selectedTab == tab,
              hasUnread = tabState.hasUnread,
              onClick = { onTabSelected(tab) },
            )
          }
        }

        val executiveCard = executiveStatusCardModel(
          state = activeState,
          serverHealthyKnown = state.serverHealthyKnown,
          serverHealthy = state.serverHealthy,
        )
        val topSections = chatTopSectionOrder(
          hasExecutiveStatus = executiveCard != null,
          hasErrorBanner = activeState.lastError != null,
        )
        topSections.forEach { section ->
          when (section) {
            ChatTopSection.EXECUTIVE_STATUS -> {
              executiveCard?.let { statusCard ->
                ExecutiveStatusCard(
                  card = statusCard,
                  onAction = { action ->
                    when (action) {
                      ExecutiveStatusAction.RETRY_LAST_FAILED -> onRetryLastFailedClicked()
                    }
                  },
                  modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                )
              }
            }
            ChatTopSection.ERROR_BANNER -> {
              val errorText = activeState.lastError
              if (errorText != null) {
                Row(
                  modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 10.dp, vertical = 2.dp),
                  horizontalArrangement = Arrangement.SpaceBetween,
                  verticalAlignment = Alignment.CenterVertically,
                ) {
                  Text(
                    text = "Error: $errorText",
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier
                      .weight(1f)
                      .clickable { selectedErrorForCopyDialog = errorText },
                  )
                  TextButton(onClick = { selectedErrorForCopyDialog = errorText }) {
                    Text("View")
                  }
                  TextButton(
                    onClick = {
                      clipboardManager.setText(AnnotatedString(errorText))
                      showCopyToast("Error copied")
                    },
                  ) {
                    Text("Copy")
                  }
                  if (!activeState.isStreaming && !activeState.isSubmitting && !activeState.lastFailedPrompt.isNullOrBlank()) {
                    TextButton(onClick = onRetryLastFailedClicked) {
                      Text("Retry")
                    }
                  }
                }
              }
            }
            ChatTopSection.MESSAGE_LIST -> {
              LazyColumn(
                state = listState,
                modifier = Modifier
                  .weight(1f)
                  .fillMaxWidth()
                  .padding(horizontal = 8.dp),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(bottom = listBottomPadding),
                verticalArrangement = Arrangement.spacedBy(CompactFieldGap),
              ) {
                val activeStreamingAssistantId =
                  activeState.messages.lastOrNull {
                    it.role == MessageRole.ASSISTANT && it.status == MessageStatus.STREAMING
                  }?.id
                items(activeState.messages, key = { it.id }) { msg ->
                  MessageBubble(
                    msg = msg,
                    progressText = if (msg.id == activeStreamingAssistantId) activeState.streamingProgress else null,
                    isSpeaking = activeSpeechMessageId == msg.id,
                    onPrimaryLongPress = {
                      when (bubblePrimaryLongPressAction(msg.role, msg.id, activeSpeechMessageId)) {
                        BubblePrimaryLongPressAction.COPY -> {
                          clipboardManager.setText(AnnotatedString(msg.content))
                          showCopyToast("Bubble copied")
                        }
                        BubblePrimaryLongPressAction.SPEAK -> {
                          val speechText = markdownToSpeechText(msg.content)
                          if (speechText.isBlank()) {
                            showCopyToast("Nothing to speak")
                          } else {
                            speechController.speak(messageId = msg.id, text = speechText)
                          }
                        }
                        BubblePrimaryLongPressAction.STOP_SPEAKING -> {
                          speechController.stop()
                          showCopyToast("Speech stopped")
                        }
                      }
                    },
                    onCopyCodeBlock = { text ->
                      clipboardManager.setText(AnnotatedString(text))
                      showCopyToast("Code copied")
                    },
                    onSelectSpecificText = { selectedBubbleForCopyDialog = msg },
                    onOpenFileReference = { path ->
                      activeRepoFilePath = path
                      onOpenFileReference(path)
                    },
                  )
                }
              }
            }
          }
        }
      }

      Composer(
        state = activeState,
        onDraftChanged = onDraftChanged,
        onWorkflowCommentaryChanged = onWorkflowCommentaryChanged,
        onAddAttachedDocumentClicked = onAddAttachedDocumentClicked,
        onRemoveAttachedDocumentClicked = onRemoveAttachedDocumentClicked,
        onSendClicked = onSendClicked,
        onLaunchWorkflowClicked = onLaunchWorkflowClicked,
        onWorkflowCancelClicked = { workflowId -> pendingWorkflowCancelId = workflowId },
        onStopClicked = {
          pendingDirectStopConfirmation = reduceStopQuickAction(
            confirmationPending = pendingDirectStopConfirmation,
            event = StopQuickActionEvent.REQUEST_STOP,
          ).confirmationPending
        },
        onQueueTailPopClicked = onQueueTailPopClicked,
        modifier = Modifier
          .align(Alignment.BottomCenter)
          .onSizeChanged { composerHeightPx = it.height },
      )

      SpeechStopOverlay(
        visible = activeSpeechMessageId != null,
        onStop = {
          speechController.stop()
          showCopyToast("Speech stopped")
        },
        modifier = Modifier
          .align(Alignment.CenterEnd)
          .statusBarsPadding()
          .navigationBarsPadding()
          .padding(end = 12.dp),
      )

      activeRepoFilePath?.let { requestedPath ->
        val viewerPath = if (fileViewerPath.isNotBlank()) fileViewerPath else requestedPath
        val viewerLoading =
          fileViewerLoading ||
            (fileViewerPath != requestedPath && fileViewerText.isBlank() && fileViewerError.isBlank())
        val viewerError =
          if (fileViewerPath == requestedPath || fileViewerError.isNotBlank()) fileViewerError else ""
        RepoFileViewerDialog(
          path = viewerPath,
          text = fileViewerText,
          truncated = fileViewerTruncated,
          loading = viewerLoading,
          error = viewerError,
          onDismiss = { activeRepoFilePath = null },
          onOpenFileReference = { nextPath ->
            activeRepoFilePath = nextPath
            onOpenFileReference(nextPath)
          },
        )
      }

      val capDialog = activeState.capDialog
      if (capDialog != null) {
        val s = capDialog.stats
        AlertDialog(
          onDismissRequest = onCapDialogDismissed,
          title = { Text("Memory Cap Reached") },
          text = {
            Text(
              "Turns: ${s.turns}/${s.maxTurns}\n" +
                "Messages: ${s.messages}\n" +
                "Text: ${s.lines} lines, ${s.chars} chars\n" +
                "Storage: ${s.storageFileBytes} bytes\n\n" +
                "Increase to ${capDialog.suggestedNewCap} turns or continue with cycling?",
            )
          },
          confirmButton = {
            TextButton(onClick = onCapIncreaseClicked) {
              Text("Increase Cap")
            }
          },
          dismissButton = {
            TextButton(onClick = onCapContinueClicked) {
              Text("Continue Cycling")
            }
          },
        )
      }

      val bubble = selectedBubbleForCopyDialog
      if (bubble != null) {
        AlertDialog(
          onDismissRequest = { selectedBubbleForCopyDialog = null },
          title = { Text("Select Text") },
          text = {
            val scrollState = rememberScrollState()
            Box(
              modifier = Modifier
                .fillMaxWidth()
                .heightIn(max = 360.dp)
                .verticalScroll(scrollState),
            ) {
              androidx.compose.foundation.text.selection.SelectionContainer {
                Text(
                  text = bubble.content,
                  style = MaterialTheme.typography.bodyMedium,
                )
              }
            }
          },
          confirmButton = {
            TextButton(onClick = { selectedBubbleForCopyDialog = null }) {
              Text("Done")
            }
          },
          dismissButton = {
            TextButton(onClick = {
              clipboardManager.setText(AnnotatedString(bubble.content))
              showCopyToast("Bubble copied")
              selectedBubbleForCopyDialog = null
            }) {
              Text("Copy Full")
            }
          },
        )
      }

      val workflowCancelId = pendingWorkflowCancelId
      if (workflowCancelId != null) {
        AlertDialog(
          onDismissRequest = { pendingWorkflowCancelId = null },
          title = { Text("Cancel Workflow") },
          text = {
            Text(
              text = "Cancel workflow $workflowCancelId?",
              style = MaterialTheme.typography.bodyMedium,
            )
          },
          confirmButton = {
            TextButton(
              onClick = {
                onWorkflowCancelRequested(workflowCancelId)
                pendingWorkflowCancelId = null
              },
            ) {
              Text("Cancel")
            }
          },
          dismissButton = {
            TextButton(onClick = { pendingWorkflowCancelId = null }) {
              Text("Keep Running")
            }
          },
        )
      }

      if (pendingDirectStopConfirmation) {
        AlertDialog(
          onDismissRequest = {
            pendingDirectStopConfirmation = reduceStopQuickAction(
              confirmationPending = pendingDirectStopConfirmation,
              event = StopQuickActionEvent.DISMISS,
            ).confirmationPending
          },
          title = { Text(StopTurnConfirmDialogTitle) },
          text = {
            Text(
              text = "Stop local rendering and send cancel to the server? Final outcome is confirmed from durable session state.",
              style = MaterialTheme.typography.bodyMedium,
            )
          },
          confirmButton = {
            TextButton(
              onClick = {
                val transition = reduceStopQuickAction(
                  confirmationPending = pendingDirectStopConfirmation,
                  event = StopQuickActionEvent.CONFIRM_STOP,
                )
                if (transition.shouldDispatchStop) {
                  onStopClicked()
                }
                pendingDirectStopConfirmation = transition.confirmationPending
              },
            ) {
              Text("Stop")
            }
          },
          dismissButton = {
            TextButton(
              onClick = {
                pendingDirectStopConfirmation = reduceStopQuickAction(
                  confirmationPending = pendingDirectStopConfirmation,
                  event = StopQuickActionEvent.DISMISS,
                ).confirmationPending
              },
            ) {
              Text("Keep Running")
            }
          },
        )
      }

      val errorText = selectedErrorForCopyDialog
      if (errorText != null) {
        AlertDialog(
          onDismissRequest = { selectedErrorForCopyDialog = null },
          title = { Text("Error Details") },
          text = {
            val scrollState = rememberScrollState()
            Box(
              modifier = Modifier
                .fillMaxWidth()
                .heightIn(max = 360.dp)
                .verticalScroll(scrollState),
            ) {
              androidx.compose.foundation.text.selection.SelectionContainer {
                Text(
                  text = errorText,
                  style = MaterialTheme.typography.bodyMedium,
                  color = MaterialTheme.colorScheme.error,
                )
              }
            }
          },
          confirmButton = {
            TextButton(onClick = { selectedErrorForCopyDialog = null }) {
              Text("Done")
            }
          },
          dismissButton = {
            TextButton(
              onClick = {
                clipboardManager.setText(AnnotatedString(errorText))
                showCopyToast("Error copied")
                selectedErrorForCopyDialog = null
              },
            ) {
              Text("Copy Full")
            }
          },
        )
      }
    }
  }
}

@Composable
private fun ExecutiveStatusCard(
  card: ExecutiveStatusCardModel,
  onAction: (ExecutiveStatusAction) -> Unit,
  modifier: Modifier = Modifier,
) {
  val colorScheme = MaterialTheme.colorScheme
  Surface(
    shape = RoundedCornerShape(12.dp),
    color = colorScheme.surfaceVariant.copy(alpha = 0.36f),
    tonalElevation = 0.dp,
    modifier = modifier
      .fillMaxWidth()
      .testTag(ExecutiveStatusCardTestTag)
      .border(
        width = 1.dp,
        color = colorScheme.outline.copy(alpha = 0.26f),
        shape = RoundedCornerShape(12.dp),
      ),
  ) {
    Column(modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp)) {
      Text(
        text = card.headline,
        style = ChatBodyTextStyle.copy(fontSize = 15.sp, lineHeight = 22.sp),
        color = colorScheme.onSurface,
      )
      Text(
        text = card.detail,
        style = ChatLabelTextStyle,
        color = colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 3.dp),
      )
      if (card.action != null) {
        Row(
          modifier = Modifier
            .fillMaxWidth()
            .padding(top = 6.dp),
          horizontalArrangement = Arrangement.End,
        ) {
          TextButton(onClick = { onAction(card.action) }) {
            Text(
              text = when (card.action) {
                ExecutiveStatusAction.RETRY_LAST_FAILED -> "Retry"
              },
            )
          }
        }
      }
    }
  }
}

@Composable
internal fun SpeechStopOverlay(
  visible: Boolean,
  onStop: () -> Unit,
  modifier: Modifier = Modifier,
) {
  if (!visible) return
  SpeechStopButton(onClick = onStop, modifier = modifier)
}

@Composable
private fun RunnerWaitStatusStrip(
  startedAtMs: Long,
  timeoutMs: Long,
  modifier: Modifier = Modifier,
) {
  val colorScheme = MaterialTheme.colorScheme
  val elapsedMs by androidx.compose.runtime.produceState(
    initialValue = 0L,
    key1 = startedAtMs,
    key2 = timeoutMs,
  ) {
    while (true) {
      value = (System.currentTimeMillis() - startedAtMs).coerceAtLeast(0L)
      delay(1000L)
    }
  }
  val remainingMs = (timeoutMs - elapsedMs).coerceAtLeast(0L)
  val transition = rememberInfiniteTransition(label = "runner_wait_status")
  val shimmerShift by transition.animateFloat(
    initialValue = -900f,
    targetValue = 900f,
    animationSpec = infiniteRepeatable(
      animation = tween(durationMillis = 2200, easing = LinearEasing),
      repeatMode = RepeatMode.Restart,
    ),
    label = "runner_wait_status_shimmer",
  )
  val shimmerBrush = remember(shimmerShift, colorScheme.primary) {
    Brush.linearGradient(
      colors = listOf(
        colorScheme.primary.copy(alpha = 0.36f),
        colorScheme.primary.copy(alpha = 0.95f),
        colorScheme.primary.copy(alpha = 0.36f),
      ),
      start = Offset(shimmerShift - 320f, 0f),
      end = Offset(shimmerShift + 320f, 0f),
    )
  }
  Surface(
    shape = RoundedCornerShape(16.dp),
    color = colorScheme.surfaceVariant.copy(alpha = 0.3f),
    tonalElevation = 0.dp,
    modifier = modifier
      .testTag(RunnerWaitStatusStripTestTag)
      .fillMaxWidth()
      .border(
        width = 1.dp,
        color = colorScheme.outline.copy(alpha = 0.28f),
        shape = RoundedCornerShape(16.dp),
      ),
  ) {
    Row(
      modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
      horizontalArrangement = Arrangement.spacedBy(10.dp),
      verticalAlignment = Alignment.CenterVertically,
    ) {
      Icon(
        imageVector = Icons.Filled.Schedule,
        contentDescription = "Waiting for runner",
        tint = colorScheme.primary,
        modifier = Modifier.size(16.dp),
      )
      Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(
          text = "External verification",
          color = colorScheme.onSurfaceVariant,
          style = ChatLabelTextStyle,
        )
        Text(
          text = runnerWaitStatusText(remainingMs = remainingMs, pulseMs = elapsedMs),
          style = ChatBodyTextStyle.copy(fontSize = 14.sp, lineHeight = 20.sp, brush = shimmerBrush),
        )
      }
    }
  }
}

@Composable
private fun SpeechStopButton(
  onClick: () -> Unit,
  modifier: Modifier = Modifier,
) {
  val colorScheme = MaterialTheme.colorScheme
  Surface(
    onClick = onClick,
    shape = RoundedCornerShape(999.dp),
    color = colorScheme.error,
    contentColor = colorScheme.onError,
    tonalElevation = 8.dp,
    shadowElevation = 8.dp,
    modifier = modifier
      .testTag(SpeechStopButtonTestTag),
  ) {
    Icon(
      imageVector = Icons.Filled.Stop,
      contentDescription = "Stop speech",
      modifier = Modifier
        .sizeIn(minWidth = 48.dp, minHeight = 48.dp)
        .padding(horizontal = 12.dp, vertical = 12.dp),
    )
  }
}

@Composable
private fun SelectedPisDetails(
  option: PisOption,
  modifier: Modifier = Modifier,
) {
  val colorScheme = MaterialTheme.colorScheme
  val title = option.title.trim().ifBlank { "Untitled PIS" }
  val status = option.status.trim().ifBlank { "unknown" }.replace('_', ' ')
  val releaseState = option.releaseState.trim().ifBlank { "draft" }.replace('_', ' ')
  Surface(
    shape = RoundedCornerShape(12.dp),
    color = colorScheme.surfaceVariant.copy(alpha = 0.3f),
    tonalElevation = 0.dp,
    modifier = modifier
      .fillMaxWidth()
      .border(
        width = 1.dp,
        color = colorScheme.outline.copy(alpha = 0.22f),
        shape = RoundedCornerShape(12.dp),
      ),
  ) {
    Column(modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp)) {
      Text(
        text = title,
        style = ChatBodyTextStyle,
        color = colorScheme.onSurface,
        maxLines = 2,
        overflow = TextOverflow.Ellipsis,
      )
      Text(
        text = "${option.uid}  •  $status  •  release $releaseState",
        style = ChatLabelTextStyle,
        color = colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 4.dp),
      )
    }
  }
}

@Composable
private fun MessageBubble(
  msg: ChatMessage,
  progressText: String?,
  isSpeaking: Boolean,
  onPrimaryLongPress: () -> Unit,
  onCopyCodeBlock: (String) -> Unit,
  onSelectSpecificText: () -> Unit,
  onOpenFileReference: (String) -> Unit,
) {
  val viewConfig = LocalViewConfiguration.current
  val doubleTapTimeoutMs = viewConfig.doubleTapTimeoutMillis
  val primaryLongPressMs = bubblePrimaryLongPressTimeoutMs(msg.role)
  val primaryLongPressCancelDistancePx =
    bubblePrimaryLongPressCancelDistancePx(msg.role, viewConfig.touchSlop)
  val isUser = msg.role == MessageRole.USER
  val isWorkflow = msg.role == MessageRole.WORKFLOW
  val isDarkTheme = isSystemInDarkTheme()
  val isStreamingAssistant = (msg.role == MessageRole.ASSISTANT && msg.status == MessageStatus.STREAMING)
  val colorScheme = MaterialTheme.colorScheme
  val bubbleShape = RoundedCornerShape(20.dp)
  val bubbleWidth = when {
    isUser -> 0.88f
    isWorkflow -> 0.96f
    else -> 0.99f
  }
  val workflowPalette = remember(msg.label, isDarkTheme) {
    workflowPaletteFor(label = msg.label, isDarkTheme = isDarkTheme)
  }
  val bubbleBg = when {
    isUser -> colorScheme.primary
    isWorkflow -> workflowPalette.background
    isDarkTheme -> Color.Black
    else -> colorScheme.surface
  }
  val bubbleBorder = when {
    isUser -> Color.Transparent
    isWorkflow -> workflowPalette.border
    isDarkTheme -> Color.Black
    else -> colorScheme.outline.copy(alpha = 0.7f)
  }
  val bodyTextColor = when {
    isUser -> colorScheme.onPrimary
    isWorkflow -> workflowPalette.text
    else -> colorScheme.onSurface
  }
  val nowMs by androidx.compose.runtime.produceState(
    initialValue = System.currentTimeMillis(),
    key1 = msg.id,
    key2 = isStreamingAssistant,
  ) {
    value = System.currentTimeMillis()
    while (isStreamingAssistant) {
      delay(1000L)
      value = System.currentTimeMillis()
    }
  }
  Row(
    modifier = Modifier.fillMaxWidth(),
    horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
  ) {
    Box(
      modifier = Modifier
        .fillMaxWidth(bubbleWidth)
        .background(
          color = bubbleBg,
          shape = bubbleShape,
        )
        .border(width = 1.dp, color = bubbleBorder, shape = bubbleShape)
        .pointerInput(msg.id, doubleTapTimeoutMs, primaryLongPressMs, primaryLongPressCancelDistancePx) {
          awaitEachGesture {
            val firstDown = awaitFirstDown(requireUnconsumed = false)
            var releasedEarly = false
            var cancelledByDrag = false
            val longPressTimeout = withTimeoutOrNull(primaryLongPressMs) {
              while (true) {
                val event = awaitPointerEvent()
                val change = event.changes.firstOrNull { it.id == firstDown.id } ?: continue
                if (change.changedToUpIgnoreConsumed()) {
                  releasedEarly = true
                  return@withTimeoutOrNull false
                }
                val dx = change.position.x - firstDown.position.x
                val dy = change.position.y - firstDown.position.y
                if (hypot(dx.toDouble(), dy.toDouble()) > primaryLongPressCancelDistancePx.toDouble()) {
                  cancelledByDrag = true
                  return@withTimeoutOrNull false
                }
              }
            } == null

            if (cancelledByDrag) {
              waitForUpOrCancellation()
              return@awaitEachGesture
            }

            if (longPressTimeout) {
              onPrimaryLongPress()
              waitForUpOrCancellation()
              return@awaitEachGesture
            }

            if (!releasedEarly) return@awaitEachGesture

            val secondDown = withTimeoutOrNull(doubleTapTimeoutMs) {
              awaitFirstDown(requireUnconsumed = false)
            } ?: return@awaitEachGesture
            val secondUp = waitForUpOrCancellation() ?: return@awaitEachGesture
            val secondHeldMs = secondUp.uptimeMillis - secondDown.uptimeMillis
            if (secondHeldMs < BubbleCopyLongPressMs) {
              onSelectSpecificText()
            }
          }
        }
        .padding(CompactCardPadding),
    ) {
      Column {
        if (!msg.label.isNullOrBlank() || msg.planeRouted) {
          Row(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(bottom = 6.dp),
          ) {
            if (!msg.label.isNullOrBlank()) {
              Text(
                text = msg.label ?: "",
                color = bodyTextColor.copy(alpha = 0.82f),
                style = ChatLabelTextStyle,
              )
            }
            if (msg.planeRouted) {
              Surface(
                color = bodyTextColor.copy(alpha = if (isUser) 0.18f else 0.08f),
                contentColor = bodyTextColor.copy(alpha = 0.9f),
                shape = RoundedCornerShape(999.dp),
              ) {
                Text(
                  text = "Plane",
                  style = ChatLabelTextStyle,
                  modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
                )
              }
            }
          }
        }
        if (isSpeaking) {
          Row(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(bottom = 6.dp),
          ) {
            Icon(
              imageVector = Icons.Filled.VolumeUp,
              contentDescription = "Speaking",
              tint = bodyTextColor.copy(alpha = 0.9f),
              modifier = Modifier.size(14.dp),
            )
            Text(
              text = "Speaking",
              color = bodyTextColor.copy(alpha = 0.9f),
              style = ChatLabelTextStyle,
            )
          }
        }
        if (msg.content.isBlank() && msg.status == MessageStatus.STREAMING) {
          if (!progressText.isNullOrBlank()) {
            StreamingProgressIndicator(
              text = progressText,
              textColor = bodyTextColor,
              modifier = Modifier.padding(top = 2.dp),
            )
          } else {
            ThinkingPlaceholder(
              elapsedMs = (nowMs - msg.createdAtMs).coerceAtLeast(0L),
              topPadding = 0.dp,
            )
          }
        } else {
          if (isUser) {
            Text(
              text = msg.content,
              color = bodyTextColor,
              style = ChatBodyTextStyle,
              modifier = Modifier.wrapContentHeight(),
            )
          } else {
            MarkdownMessage(
              text = msg.content,
              textColor = bodyTextColor,
              onCopyCodeBlock = onCopyCodeBlock,
              modifier = Modifier.wrapContentHeight(),
              onOpenFileReference = onOpenFileReference,
            )
          }
        }
        if (
          msg.status == MessageStatus.FAILED ||
          msg.status == MessageStatus.CANCELLED ||
          msg.status == MessageStatus.INTERRUPTED
        ) {
          Text(
            text = msg.status.name.lowercase(),
            color = MaterialTheme.colorScheme.error,
            style = ChatLabelTextStyle,
          )
        }
      }
    }
  }
}

@Composable
private fun StreamingProgressIndicator(
  text: String,
  textColor: Color,
  modifier: Modifier = Modifier,
) {
  val transition = rememberInfiniteTransition(label = "streaming_progress")
  val shimmerShift by transition.animateFloat(
    initialValue = -1200f,
    targetValue = 1200f,
    animationSpec = infiniteRepeatable(
      animation = tween(durationMillis = 2200, easing = LinearEasing),
      repeatMode = RepeatMode.Restart,
    ),
    label = "streaming_progress_shimmer",
  )
  val shimmerBrush = remember(shimmerShift, textColor) {
    Brush.linearGradient(
      colors = listOf(
        textColor.copy(alpha = 0.34f),
        textColor.copy(alpha = 0.96f),
        textColor.copy(alpha = 0.34f),
      ),
      start = Offset(shimmerShift - 420f, 0f),
      end = Offset(shimmerShift + 420f, 0f),
    )
  }
  Column(modifier = modifier) {
    Text(
      text = "Working",
      style = ChatLabelTextStyle.copy(brush = shimmerBrush),
      modifier = Modifier.padding(bottom = 4.dp),
    )
    Text(
      text = text,
      style = ChatBodyTextStyle.copy(
        fontStyle = androidx.compose.ui.text.font.FontStyle.Italic,
        brush = shimmerBrush,
      ),
    )
  }
}

private fun workflowPaletteFor(label: String?, isDarkTheme: Boolean): WorkflowBubblePalette {
  val normalized = label.orEmpty().trim().uppercase()
  return when {
    normalized == "B" -> {
      if (isDarkTheme) {
        WorkflowBubblePalette(
          background = Color(0xFF11283A),
          border = Color(0xFF69B7FF),
          text = Color(0xFFE9F5FF),
        )
      } else {
        WorkflowBubblePalette(
          background = Color(0xFFE8F3FF),
          border = Color(0xFF2C78C4),
          text = Color(0xFF16314A),
        )
      }
    }
    normalized == "V" -> {
      if (isDarkTheme) {
        WorkflowBubblePalette(
          background = Color(0xFF35260D),
          border = Color(0xFFF0C15A),
          text = Color(0xFFFFF4D8),
        )
      } else {
        WorkflowBubblePalette(
          background = Color(0xFFFFF3D8),
          border = Color(0xFFC08A18),
          text = Color(0xFF4B3608),
        )
      }
    }
    normalized == "O" -> {
      if (isDarkTheme) {
        WorkflowBubblePalette(
          background = Color(0xFF123330),
          border = Color(0xFF53C4B6),
          text = Color(0xFFE2FAF6),
        )
      } else {
        WorkflowBubblePalette(
          background = Color(0xFFDFF5F2),
          border = Color(0xFF278579),
          text = Color(0xFF123A35),
        )
      }
    }
    else -> {
      if (isDarkTheme) {
        WorkflowBubblePalette(
          background = Color(0xFF1E3525),
          border = Color(0xFF74C78B),
          text = Color(0xFFEAF9EE),
        )
      } else {
        WorkflowBubblePalette(
          background = Color(0xFFE3F3E8),
          border = Color(0xFF4C8F60),
          text = Color(0xFF183222),
        )
      }
    }
  }
}

@Composable
private fun ThinkingPlaceholder(elapsedMs: Long, topPadding: Dp = 4.dp) {
  val colorScheme = MaterialTheme.colorScheme
  val secs = (elapsedMs / 1000L).toInt().coerceAtLeast(0)
  val mm = secs / 60
  val ss = secs % 60
  val dotCount = ((elapsedMs / 500L) % 4L).toInt()
  val dots = ".".repeat(dotCount)
  val elapsedText = String.format("%02d:%02d", mm, ss)

  Text(
    text = "Thinking$dots  $elapsedText",
    color = colorScheme.onSurfaceVariant,
    style = ChatBodyTextStyle.copy(fontSize = 14.sp, lineHeight = 20.sp),
    modifier = Modifier.padding(top = topPadding),
  )
}

@Composable
private fun PisSelector(
  selectedPisId: String,
  options: List<PisOption>,
  loading: Boolean,
  error: String?,
  enabled: Boolean,
  onSelectedPisChanged: (String) -> Unit,
  onOpened: () -> Unit,
) {
  var expanded by remember { mutableStateOf(false) }
  val colorScheme = MaterialTheme.colorScheme
  val selectedLabel = when {
    loading -> "Loading PIS..."
    !error.isNullOrBlank() && options.isEmpty() -> "PIS unavailable"
    else -> options.firstOrNull { it.uid == selectedPisId }?.displayLabel ?: "No PIS"
  }
  Box {
    Surface(
      shape = RoundedCornerShape(12.dp),
      color = colorScheme.surfaceVariant.copy(alpha = 0.22f),
      tonalElevation = 0.dp,
      modifier = Modifier.border(
        width = 1.dp,
        color = colorScheme.outline.copy(alpha = 0.35f),
        shape = RoundedCornerShape(12.dp),
      ),
    ) {
      TextButton(
        onClick = {
          onOpened()
          expanded = true
        },
        enabled = enabled,
        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 10.dp, vertical = 4.dp),
      ) {
        Text(
          text = selectedLabel,
          style = ChatLabelTextStyle,
        )
      }
    }
    DropdownMenu(
      expanded = expanded,
      onDismissRequest = { expanded = false },
    ) {
      if (loading) {
        DropdownMenuItem(
          text = { Text("Loading...") },
          onClick = {},
          enabled = false,
        )
      }
      if (!error.isNullOrBlank()) {
        DropdownMenuItem(
          text = { Text("Fetch failed: $error") },
          onClick = {},
          enabled = false,
        )
      }
      DropdownMenuItem(
        text = { Text("No PIS") },
        onClick = {
          onSelectedPisChanged("")
          expanded = false
        },
      )
      options.forEach { option ->
        DropdownMenuItem(
          text = { Text(option.displayLabel) },
          onClick = {
            onSelectedPisChanged(option.uid)
            expanded = false
          },
        )
      }
    }
  }
}

@Composable
private fun AgentTabChip(
  tab: AgentTab,
  selected: Boolean,
  hasUnread: Boolean,
  onClick: () -> Unit,
) {
  val colorScheme = MaterialTheme.colorScheme
  val bg = if (selected) colorScheme.primary.copy(alpha = 0.12f) else colorScheme.surface
  val border = when {
    selected -> colorScheme.primary
    hasUnread -> colorScheme.primary.copy(alpha = 0.9f)
    else -> colorScheme.outline.copy(alpha = 0.75f)
  }
  val textColor = if (selected) colorScheme.primary else colorScheme.onSurface
  Box(
    modifier = Modifier
      .wrapContentWidth()
      .background(bg, RoundedCornerShape(999.dp))
      .border(1.dp, border, RoundedCornerShape(999.dp))
      .clickable(onClick = onClick)
      .padding(horizontal = 10.dp, vertical = 6.dp),
    contentAlignment = Alignment.Center,
  ) {
    Row(
      horizontalArrangement = Arrangement.spacedBy(CompactTightGap),
      verticalAlignment = Alignment.CenterVertically,
    ) {
      Text(
        text = tab.label,
        color = textColor,
        style = ChatLabelTextStyle.copy(
          fontSize = 13.sp,
          fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Medium,
        ),
      )
      if (hasUnread && !selected) {
        Box(
          modifier = Modifier
            .size(6.dp)
            .background(colorScheme.primary, RoundedCornerShape(999.dp)),
        )
      }
    }
  }
}

@Composable
@OptIn(ExperimentalFoundationApi::class)
private fun Composer(
  state: AgentChatState,
  onDraftChanged: (String) -> Unit,
  onWorkflowCommentaryChanged: (String) -> Unit,
  onAddAttachedDocumentClicked: () -> Unit,
  onRemoveAttachedDocumentClicked: (String) -> Unit,
  onSendClicked: () -> Unit,
  onLaunchWorkflowClicked: () -> Unit,
  onWorkflowCancelClicked: (String) -> Unit,
  onStopClicked: () -> Unit,
  onQueueTailPopClicked: () -> Unit,
  modifier: Modifier = Modifier,
) {
  val colorScheme = MaterialTheme.colorScheme
  val queueCount = state.queuedPrompts.size
  val queueAtCap = queueCount >= AGENT_PROMPT_QUEUE_MAX_DEPTH
  val isBusy = state.isStreaming || state.isSubmitting
  val hasDraft = state.draft.trim().isNotEmpty()
  val primaryAction = remember(hasDraft, isBusy) { composerPrimaryAction(hasDraft = hasDraft, isBusy = isBusy) }
  val showWorkflowControls = state.tab == AgentTab.ORCHESTRATOR
  val canLaunchWorkflow = remember(
    state.selectedPisId,
    state.draft,
    state.isLaunchingWorkflow,
    state.activeWorkflowId,
    state.isStreaming,
    state.isSubmitting,
  ) {
    state.selectedPisId.trim().isNotEmpty() &&
      !state.isLaunchingWorkflow &&
      state.activeWorkflowId == null &&
      !state.isStreaming &&
      !state.isSubmitting
  }
  val composerShape = RoundedCornerShape(20.dp)
  val tattooPainter = painterResource(id = R.drawable.tattoo)
  val workflowTint = when {
    canLaunchWorkflow -> colorScheme.primary
    else -> colorScheme.onSurface.copy(alpha = 0.38f)
  }
  val sendTint = when {
    isBusy -> colorScheme.primary
    primaryAction == ComposerPrimaryAction.SEND || primaryAction == ComposerPrimaryAction.QUEUE -> colorScheme.primary
    else -> colorScheme.onSurface.copy(alpha = 0.38f)
  }
  val sendEnabled = remember(primaryAction) {
    primaryAction != ComposerPrimaryAction.NONE
  }
  val showRunnerWait = state.streamPhase == StreamPhase.HOST_VERIFICATION_WAIT && isBusy
  val auxSections = remember(showRunnerWait, queueCount) {
    composerAuxSectionOrder(showRunnerWait = showRunnerWait, queueCount = queueCount)
  }
  val sendDescription = remember(primaryAction) {
    when (primaryAction) {
      ComposerPrimaryAction.SEND -> "Send message"
      ComposerPrimaryAction.QUEUE -> "Queue message"
      ComposerPrimaryAction.STOP -> "Stop response"
      ComposerPrimaryAction.NONE -> "Send message"
    }
  }

  Column(
    modifier = modifier
      .fillMaxWidth()
      .navigationBarsPadding()
      .imePadding()
      .padding(8.dp),
  ) {
    if (state.attachedDocuments.isNotEmpty()) {
      Row(
        modifier = Modifier
          .fillMaxWidth()
          .horizontalScroll(rememberScrollState())
          .padding(horizontal = 4.dp, vertical = 2.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
      ) {
        state.attachedDocuments.forEach { document ->
          AttachedDocumentChip(
            document = document,
            onRemove = { onRemoveAttachedDocumentClicked(document.id) },
          )
        }
      }
    }
    auxSections.forEach { section ->
      when (section) {
        ComposerAuxSection.RUNNER_WAIT -> RunnerWaitStatusStrip(
          startedAtMs = state.streamPhaseStartedAtMs ?: System.currentTimeMillis(),
          timeoutMs = hostVerificationTimeoutMs(state),
          modifier = Modifier.padding(horizontal = 4.dp, vertical = 2.dp),
        )
        ComposerAuxSection.QUEUE_PREVIEW -> {
          Column(
            modifier = Modifier
              .fillMaxWidth()
              .padding(horizontal = 4.dp, vertical = 2.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
          ) {
            Text(
              text = "Queued $queueCount/$AGENT_PROMPT_QUEUE_MAX_DEPTH",
              color = colorScheme.onSurfaceVariant,
              style = ChatLabelTextStyle,
            )
            Row(
              modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
              horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
              state.queuedPrompts.forEachIndexed { index, prompt ->
                Surface(
                  shape = RoundedCornerShape(999.dp),
                  color = colorScheme.surfaceVariant.copy(alpha = 0.45f),
                  tonalElevation = 0.dp,
                ) {
                  Text(
                    text = "${index + 1}. ${prompt.trim()}",
                    color = colorScheme.onSurface,
                    style = ChatLabelTextStyle,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                  )
                }
              }
            }
          }
        }
      }
    }
    if (showWorkflowControls && (state.activeWorkflowId != null || state.isLaunchingWorkflow)) {
      Row(
        modifier = Modifier
          .fillMaxWidth()
          .padding(start = 4.dp, bottom = 6.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
      ) {
        Column(modifier = Modifier.weight(1f)) {
          Text(
            text = if (state.activeWorkflowId != null) {
              "WF-01 running on ${state.selectedPisId}"
            } else {
              "WF-01 is starting on ${state.selectedPisId}"
            },
            color = colorScheme.onSurfaceVariant,
            style = ChatLabelTextStyle,
          )
          state.activeWorkflowId?.let { workflowId ->
            Text(
              text = workflowId,
              color = colorScheme.onSurfaceVariant.copy(alpha = 0.8f),
              style = ChatLabelTextStyle,
            )
          }
        }
        if (state.activeWorkflowId != null) {
          IconButton(
            onClick = { onWorkflowCancelClicked(state.activeWorkflowId) },
            modifier = Modifier.size(36.dp),
          ) {
            Icon(
              imageVector = Icons.Filled.Stop,
              contentDescription = "Cancel workflow",
              tint = colorScheme.error,
            )
          }
        }
      }
    }
    OutlinedTextField(
      value = state.draft,
      onValueChange = onDraftChanged,
      textStyle = ChatBodyTextStyle,
        placeholder = {
          Text(
          "Message Codex",
          style = ChatBodyTextStyle,
          color = colorScheme.onSurfaceVariant,
        )
      },
      minLines = 1,
      maxLines = 10,
      shape = composerShape,
      colors = OutlinedTextFieldDefaults.colors(
        focusedContainerColor = colorScheme.surface,
        unfocusedContainerColor = colorScheme.surface,
        focusedTextColor = colorScheme.onSurface,
        unfocusedTextColor = colorScheme.onSurface,
        cursorColor = colorScheme.primary,
        focusedBorderColor = colorScheme.outline,
        unfocusedBorderColor = colorScheme.outline.copy(alpha = 0.8f),
      ),
      trailingIcon = {
        Row(
          horizontalArrangement = Arrangement.spacedBy(2.dp),
          verticalAlignment = Alignment.CenterVertically,
        ) {
          IconButton(
            onClick = onAddAttachedDocumentClicked,
            modifier = Modifier.size(30.dp),
          ) {
            Icon(
              imageVector = Icons.Filled.ContentPaste,
              contentDescription = "Add document",
              tint = colorScheme.onSurfaceVariant,
              modifier = Modifier.size(18.dp),
            )
          }
          if (showWorkflowControls && state.selectedPisId.trim().isNotEmpty()) {
            IconButton(
              onClick = onLaunchWorkflowClicked,
              enabled = canLaunchWorkflow,
              modifier = Modifier.size(36.dp),
            ) {
              Image(
                painter = tattooPainter,
                contentDescription = "Start workflow",
                modifier = Modifier.size(20.dp),
                colorFilter = androidx.compose.ui.graphics.ColorFilter.tint(workflowTint),
              )
            }
          }
          Box(
            modifier = Modifier
              .size(36.dp)
              .combinedClickable(
                enabled = sendEnabled || queueCount > 0,
                onClick = {
                  when (primaryAction) {
                    ComposerPrimaryAction.SEND,
                    ComposerPrimaryAction.QUEUE,
                    -> onSendClicked()
                    ComposerPrimaryAction.STOP -> onStopClicked()
                    ComposerPrimaryAction.NONE -> Unit
                  }
                },
                onLongClick = {
                  if (queueCount > 0) {
                    onQueueTailPopClicked()
                  }
                },
              ),
            contentAlignment = Alignment.Center,
          ) {
            Icon(
              imageVector = when (primaryAction) {
                ComposerPrimaryAction.STOP -> Icons.Filled.Stop
                ComposerPrimaryAction.SEND,
                ComposerPrimaryAction.QUEUE,
                ComposerPrimaryAction.NONE -> Icons.AutoMirrored.Filled.Send
              },
              contentDescription = sendDescription,
              tint = if (sendEnabled) sendTint else colorScheme.onSurface.copy(alpha = 0.38f),
            )
            if (primaryAction == ComposerPrimaryAction.QUEUE && !queueAtCap) {
              Icon(
                imageVector = Icons.Filled.Schedule,
                contentDescription = null,
                tint = colorScheme.primary,
                modifier = Modifier
                  .align(Alignment.TopEnd)
                  .size(12.dp)
                  .offset(x = 1.dp, y = (-1).dp),
              )
            }
            if (queueCount > 0) {
              Box(
                modifier = Modifier
                  .align(Alignment.BottomEnd)
                  .offset(x = 2.dp, y = 2.dp)
                  .background(colorScheme.primary, RoundedCornerShape(999.dp))
                  .padding(horizontal = 4.dp, vertical = 1.dp),
              ) {
                Text(
                  text = queueCount.toString(),
                  color = colorScheme.onPrimary,
                  style = ChatLabelTextStyle.copy(fontSize = 9.sp, lineHeight = 10.sp),
                )
              }
            }
          }
        }
      },
      modifier = Modifier.fillMaxWidth(),
    )
  }
}

@Composable
private fun AttachedDocumentChip(
  document: ChatAttachedDocument,
  onRemove: () -> Unit,
) {
  Surface(
    shape = RoundedCornerShape(999.dp),
    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
    tonalElevation = 0.dp,
  ) {
    Row(
      modifier = Modifier.padding(start = 10.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
      horizontalArrangement = Arrangement.spacedBy(4.dp),
      verticalAlignment = Alignment.CenterVertically,
    ) {
      Text(
        text = document.filename,
        color = MaterialTheme.colorScheme.onSurface,
        style = ChatLabelTextStyle,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = Modifier.widthIn(max = 160.dp),
      )
      IconButton(
        onClick = onRemove,
        modifier = Modifier.size(20.dp),
      ) {
        Text("x", style = ChatLabelTextStyle)
      }
    }
  }
}

private data class ChatAttachmentReadResult(
  val filename: String,
  val mimeType: String,
  val text: String,
  val truncated: Boolean,
)

private sealed interface ChatAttachmentLoadOutcome {
  data class Success(val result: ChatAttachmentReadResult) : ChatAttachmentLoadOutcome
  data class Error(val message: String) : ChatAttachmentLoadOutcome
}

private fun readChatAttachment(context: Context, uri: Uri): ChatAttachmentLoadOutcome {
  val filename = runCatching {
    context.contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)
      ?.use { cursor ->
        if (cursor.moveToFirst()) cursor.getString(0).orEmpty() else ""
      }
      .orEmpty()
  }.getOrElse { uri.lastPathSegment.orEmpty() }
  val mimeType = context.contentResolver.getType(uri).orEmpty()
  val byteRead = runCatching {
    context.contentResolver.openInputStream(uri)?.use { input ->
      val buffer = ByteArray(8 * 1024)
      val out = java.io.ByteArrayOutputStream()
      var total = 0
      var truncated = false
      while (true) {
        val read = input.read(buffer)
        if (read <= 0) break
        val room = CHAT_ATTACHMENT_MAX_BYTES - total
        if (room <= 0) {
          truncated = true
          break
        }
        val toWrite = minOf(read, room)
        out.write(buffer, 0, toWrite)
        total += toWrite
        if (toWrite < read) {
          truncated = true
          break
        }
      }
      Pair(out.toByteArray(), truncated)
    } ?: (byteArrayOf() to false)
  }.getOrDefault(byteArrayOf() to false)
  val bytes = byteRead.first
  if (bytes.isEmpty()) return ChatAttachmentLoadOutcome.Error("Selected document was empty.")
  val decoded = runCatching { bytes.toString(Charsets.UTF_8) }.getOrDefault("")
  if (decoded.isBlank() || decoded.contains('\u0000')) {
    return ChatAttachmentLoadOutcome.Error("Only text documents can be attached to chat right now.")
  }
  val normalized = decoded.trim()
  if (normalized.isBlank()) return ChatAttachmentLoadOutcome.Error("Selected document had no readable text.")
  val maxChars = 24_000
  return ChatAttachmentLoadOutcome.Success(
    ChatAttachmentReadResult(
      filename = filename.ifBlank { "shared_document.txt" },
      mimeType = mimeType,
      text = normalized.take(maxChars),
      truncated = normalized.length > maxChars || byteRead.second,
    ),
  )
}
