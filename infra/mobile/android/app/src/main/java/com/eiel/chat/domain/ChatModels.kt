package com.eiel.chat.domain

import java.util.UUID

enum class MessageRole {
  USER,
  ASSISTANT,
  WORKFLOW,
}

enum class MessageStatus {
  SENDING,
  STREAMING,
  COMPLETE,
  INTERRUPTED,
  FAILED,
  CANCELLED,
}

enum class StreamPhase(val wireValue: String) {
  HOST_VERIFICATION_WAIT("host_verification_wait");

  companion object {
    fun fromWireValue(raw: String?): StreamPhase? {
      val normalized = raw?.trim()?.lowercase().orEmpty()
      return entries.firstOrNull { it.wireValue == normalized }
    }
  }
}

const val AGENT_PROMPT_QUEUE_MAX_DEPTH = 5
const val DEFAULT_AGENT_CHAT_TITLE = "Causyl Codex Chat"
const val DIRECT_CHAT_STOP_RACE_LABEL = "Stop raced"

fun parsePersistedMessageStatus(rawStatus: String?, label: String? = null): MessageStatus {
  val normalizedStatus = rawStatus?.trim()?.lowercase().orEmpty()
  val normalizedLabel = label?.trim()
  if (
    normalizedLabel.equals(DIRECT_CHAT_STOP_RACE_LABEL, ignoreCase = true) &&
    normalizedStatus in setOf("", "ok", "complete")
  ) {
    return MessageStatus.INTERRUPTED
  }
  return when (normalizedStatus) {
    "interrupted" -> MessageStatus.INTERRUPTED
    "failed" -> MessageStatus.FAILED
    "cancelled" -> MessageStatus.CANCELLED
    "sending" -> MessageStatus.SENDING
    "streaming" -> MessageStatus.STREAMING
    else -> MessageStatus.COMPLETE
  }
}

enum class AgentTab(
  val key: String,
  val label: String,
  val threadId: String,
  val headerTitle: String,
  val visibleInChatTabs: Boolean = true,
) {
  PLANE(key = "p", label = "Plane", threadId = "mpl", headerTitle = "PLANE", visibleInChatTabs = false),
  ALL(key = "a", label = "A", threadId = "mbl", headerTitle = "AGT"),
  ALL_2(key = "a2", label = "A2", threadId = "mb2", headerTitle = "AGT"),
  ALL_3(key = "a3", label = "A3", threadId = "mb3", headerTitle = "AGT"),
  ORCHESTRATOR(key = "o", label = "O", threadId = "mor", headerTitle = "ORCH"),
  BUILDER(key = "b", label = "B", threadId = "mbu", headerTitle = "BLD"),
  VERIFIER(key = "v", label = "V", threadId = "mvr", headerTitle = "VER");

  companion object {
    val default: AgentTab = ALL
    val visibleEntries: List<AgentTab> = entries.filter { it.visibleInChatTabs }

    fun fromKey(raw: String?): AgentTab {
      val norm = raw?.trim()?.lowercase().orEmpty()
      return entries.firstOrNull { it.key == norm } ?: default
    }

    fun visibleOrDefault(raw: String?): AgentTab {
      val resolved = fromKey(raw)
      return if (resolved.visibleInChatTabs) resolved else default
    }
  }
}

data class ChatMessage(
  val id: String = UUID.randomUUID().toString(),
  val threadId: String,
  val role: MessageRole,
  val content: String,
  val status: MessageStatus,
  val createdAtMs: Long,
  val updatedAtMs: Long,
  val label: String? = null,
  val eventKey: String? = null,
  val workflowId: String? = null,
  val planeRouted: Boolean = false,
)

data class ChatAttachedDocument(
  val id: String = UUID.randomUUID().toString(),
  val filename: String,
  val mimeType: String,
  val text: String,
  val truncated: Boolean = false,
)

data class AgentChatState(
  val tab: AgentTab,
  val messages: List<ChatMessage> = emptyList(),
  val title: String = "",
  val draft: String = "",
  val queuedPrompts: List<String> = emptyList(),
  val selectedPisId: String = "",
  val workflowCommentary: String = "",
  val attachedDocuments: List<ChatAttachedDocument> = emptyList(),
  val activeWorkflowId: String? = null,
  val isLaunchingWorkflow: Boolean = false,
  val isStreaming: Boolean = false,
  val isSubmitting: Boolean = false,
  val streamingProgress: String? = null,
  val streamPhase: StreamPhase? = null,
  val streamPhaseStartedAtMs: Long? = null,
  val lastError: String? = null,
  val lastFailedPrompt: String? = null,
  val capDialog: CapDialogState? = null,
  val hasUnread: Boolean = false,
) {
  val threadId: String
    get() = tab.threadId

  val displayTitle: String
    get() = title.trim().ifBlank { tab.headerTitle }
}

data class PisOption(
  val uid: String,
  val title: String,
  val status: String,
  val releaseState: String,
  val displayLabel: String,
)

internal fun buildPromptWithAttachedDocuments(
  prompt: String,
  documents: List<ChatAttachedDocument>,
): String {
  val trimmedPrompt = prompt.trim()
  if (documents.isEmpty()) return trimmedPrompt
  val attachmentBlock = buildString {
    appendLine("Attached documents for implicit context:")
    documents.forEachIndexed { index, document ->
      appendLine("[Document ${index + 1}] ${document.filename.ifBlank { "shared_document.txt" }}")
      if (document.mimeType.isNotBlank()) {
        appendLine("mime: ${document.mimeType}")
      }
      if (document.truncated) {
        appendLine("note: content was truncated for mobile send")
      }
      appendLine(document.text.trim())
      appendLine()
    }
    appendLine("Use these documents as implicit context for the user request below.")
  }.trim()
  return buildString {
    appendLine(attachmentBlock)
    appendLine()
    append(trimmedPrompt)
  }.trim()
}

data class ChatUiState(
  val selectedTab: AgentTab = AgentTab.default,
  val agentStates: Map<AgentTab, AgentChatState> = AgentTab.entries.associateWith { AgentChatState(tab = it) },
  val pisOptions: List<PisOption> = emptyList(),
  val pisOptionsLoading: Boolean = false,
  val pisOptionsError: String? = null,
  val serverHealthy: Boolean = false,
  val serverHealthyKnown: Boolean = false,
) {
  val tabs: List<AgentTab>
    get() = AgentTab.visibleEntries

  val active: AgentChatState
    get() = agentStates[selectedTab] ?: AgentChatState(tab = selectedTab)
}

data class SessionStats(
  val threadId: String,
  val maxTurns: Int,
  val turns: Int,
  val messages: Int,
  val chars: Int,
  val lines: Int,
  val atCap: Boolean,
  val willCycleNext: Boolean,
  val storagePath: String,
  val storageFileBytes: Int,
)

data class SessionHistoryMessage(
  val role: MessageRole,
  val content: String,
  val status: MessageStatus = MessageStatus.COMPLETE,
  val label: String? = null,
  val workflowId: String? = null,
  val eventKey: String? = null,
  val planeRouted: Boolean = false,
)

data class SessionStreamState(
  val status: String,
  val content: String,
  val startedUtc: String? = null,
  val updatedUtc: String? = null,
  val turnId: String? = null,
)

data class SessionSnapshot(
  val history: List<SessionHistoryMessage>,
  val streamState: SessionStreamState? = null,
)

data class CapDialogState(
  val stats: SessionStats,
  val suggestedNewCap: Int,
)

data class SendRequest(
  val threadId: String,
  val prompt: String,
  val agentKey: String,
) {
  var requestId: String = ""
  var idempotencyKey: String = ""
  var resumeTurnId: String = ""

  fun ensureTransportIdentity(): SendRequest {
    if (requestId.isBlank()) {
      requestId = "mobile_send_${UUID.randomUUID()}"
    }
    if (idempotencyKey.isBlank()) {
      idempotencyKey = requestId
    }
    return this
  }
}

data class WorkflowStartRequest(
  val bindingType: String,
  val bindingId: String,
  val phaseLabel: String,
  val objective: String,
  val maxIterations: Int?,
  val executionDirective: String,
)

data class WorkflowStartResponse(
  val workflowId: String,
  val phaseLabel: String = "",
  val objective: String = "",
  val maxIterations: Int = 0,
)

data class WorkflowEvent(
  val utc: String,
  val event: String,
  val payload: Map<String, String>,
)

data class WorkflowStatus(
  val workflowId: String,
  val status: String,
  val statusDetail: String = "",
  val turn: String,
  val activeRole: String = "",
  val activeWaitReason: String = "",
  val activeProgressNote: String = "",
  val lastMeaningfulProgressUtc: String = "",
  val lastMeaningfulProgressEvent: String = "",
  val stuckReason: String = "",
  val iteration: Int,
  val maxIterations: Int,
  val phaseLabel: String,
  val objective: String,
  val verifierVerdict: String,
  val lastError: String,
  val intentValidationReport: String,
  val events: List<WorkflowEvent>,
)

sealed class StreamEvent {
  data class Start(val turnId: String) : StreamEvent()
  data class Token(val text: String) : StreamEvent()
  data class Progress(val text: String, val phase: StreamPhase? = null) : StreamEvent()
  data class Title(val text: String) : StreamEvent()
  data class PlaneRouted(val enabled: Boolean) : StreamEvent()
  data object Keepalive : StreamEvent()
  data object Done : StreamEvent()
  data class Error(val reason: String) : StreamEvent()
}
