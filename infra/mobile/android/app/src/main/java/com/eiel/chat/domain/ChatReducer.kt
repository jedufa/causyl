package com.eiel.chat.domain

object ChatReducer {
  fun applyPreflightStarted(state: AgentChatState, prompt: String, nowMs: Long): AgentChatState {
    val trimmed = prompt.trim()
    if (trimmed.isEmpty() || state.isSubmitting || state.isStreaming) {
      return state
    }
    val user = ChatMessage(
      threadId = state.threadId,
      role = MessageRole.USER,
      content = trimmed,
      status = MessageStatus.SENDING,
      createdAtMs = nowMs,
      updatedAtMs = nowMs,
    )
    val assistant = ChatMessage(
      threadId = state.threadId,
      role = MessageRole.ASSISTANT,
      content = "",
      status = MessageStatus.STREAMING,
      createdAtMs = nowMs,
      updatedAtMs = nowMs,
    )
    return state.copy(
      messages = state.messages + listOf(user, assistant),
      draft = "",
      isSubmitting = true,
      lastError = null,
      hasUnread = false,
    )
  }

  fun applyDraftChanged(state: AgentChatState, draft: String): AgentChatState {
    return state.copy(draft = draft)
  }

  fun applySelectedPisChanged(state: AgentChatState, pisId: String): AgentChatState {
    return state.copy(selectedPisId = pisId)
  }

  fun applyWorkflowCommentaryChanged(state: AgentChatState, commentary: String): AgentChatState {
    return state.copy(workflowCommentary = commentary)
  }

  fun applySelected(state: AgentChatState): AgentChatState {
    if (!state.hasUnread) return state
    return state.copy(hasUnread = false)
  }

  fun applySendStarted(state: AgentChatState, prompt: String, nowMs: Long): AgentChatState {
    val trimmed = prompt.trim()
    if (trimmed.isEmpty() || state.isStreaming) {
      return state
    }
    val nextMessages = state.messages.toMutableList()
    val pendingUserIdx = nextMessages.indexOfLast {
      it.role == MessageRole.USER &&
        it.status == MessageStatus.SENDING &&
        it.content == trimmed
    }
    if (pendingUserIdx >= 0) {
      val current = nextMessages[pendingUserIdx]
      nextMessages[pendingUserIdx] = current.copy(
        status = MessageStatus.COMPLETE,
        updatedAtMs = nowMs,
      )
    } else {
      nextMessages += ChatMessage(
        threadId = state.threadId,
        role = MessageRole.USER,
        content = trimmed,
        status = MessageStatus.COMPLETE,
        createdAtMs = nowMs,
        updatedAtMs = nowMs,
      )
    }
    val hasPendingAssistant = nextMessages.any {
      it.role == MessageRole.ASSISTANT && it.status == MessageStatus.STREAMING
    }
    return state.copy(
      messages = if (hasPendingAssistant) nextMessages else {
        nextMessages + ChatMessage(
          threadId = state.threadId,
          role = MessageRole.ASSISTANT,
          content = "",
          status = MessageStatus.STREAMING,
          createdAtMs = nowMs,
          updatedAtMs = nowMs,
        )
      },
      draft = "",
      isSubmitting = true,
      isStreaming = true,
      streamingProgress = null,
      streamPhase = null,
      streamPhaseStartedAtMs = null,
      lastError = null,
      lastFailedPrompt = null,
      capDialog = null,
      hasUnread = false,
    )
  }

  fun applyPlaneRouted(state: AgentChatState, nowMs: Long): AgentChatState {
    if (!state.messages.any { (it.role == MessageRole.USER || it.role == MessageRole.ASSISTANT) && !it.planeRouted }) {
      return state
    }
    val userIdx = state.messages.indexOfLast { it.role == MessageRole.USER }
    val assistantIdx = state.messages.indexOfLast { it.role == MessageRole.ASSISTANT && it.status == MessageStatus.STREAMING }
    if (userIdx < 0 && assistantIdx < 0) return state
    val next = state.messages.toMutableList()
    if (userIdx >= 0) {
      next[userIdx] = next[userIdx].copy(planeRouted = true, updatedAtMs = nowMs)
    }
    if (assistantIdx >= 0) {
      next[assistantIdx] = next[assistantIdx].copy(planeRouted = true, updatedAtMs = nowMs)
    }
    return state.copy(messages = next)
  }

  fun applyStreamToken(state: AgentChatState, token: String, nowMs: Long): AgentChatState {
    if (!state.isStreaming) {
      return state
    }
    val idx = state.messages.indexOfLast { it.role == MessageRole.ASSISTANT && it.status == MessageStatus.STREAMING }
    if (idx < 0) {
      return state
    }
    val current = state.messages[idx]
    val updated = current.copy(
      content = current.content + token,
      label = null,
      updatedAtMs = nowMs,
    )
    val next = state.messages.toMutableList()
    next[idx] = updated
    return state.copy(
      messages = next,
      streamingProgress = null,
      streamPhase = null,
      streamPhaseStartedAtMs = null,
    )
  }

  fun applyStreamProgress(state: AgentChatState, text: String, phase: StreamPhase?, nowMs: Long): AgentChatState {
    if (!state.isStreaming) {
      return state
    }
    val progress = text.trim()
    if (progress.isEmpty()) {
      return state
    }
    val idx = state.messages.indexOfLast { it.role == MessageRole.ASSISTANT && it.status == MessageStatus.STREAMING }
    if (idx < 0) {
      return state
    }
    val updated = state.messages[idx].copy(label = null, updatedAtMs = nowMs)
    val next = state.messages.toMutableList()
    next[idx] = updated
    val startedAtMs = when {
      phase == null -> null
      phase == state.streamPhase && state.streamPhaseStartedAtMs != null -> state.streamPhaseStartedAtMs
      else -> nowMs
    }
    return state.copy(
      messages = next,
      streamingProgress = progress,
      streamPhase = phase,
      streamPhaseStartedAtMs = startedAtMs,
    )
  }

  fun applyStreamDone(state: AgentChatState, nowMs: Long): AgentChatState {
    val idx = state.messages.indexOfLast { it.role == MessageRole.ASSISTANT && it.status == MessageStatus.STREAMING }
    if (idx < 0) {
      return state.copy(
        isStreaming = false,
        isSubmitting = false,
        streamingProgress = null,
        streamPhase = null,
        streamPhaseStartedAtMs = null,
      )
    }
    val updated = state.messages[idx].copy(status = MessageStatus.COMPLETE, label = null, updatedAtMs = nowMs)
    val next = state.messages.toMutableList()
    next[idx] = updated
    return state.copy(
      messages = next,
      isStreaming = false,
      isSubmitting = false,
      streamingProgress = null,
      streamPhase = null,
      streamPhaseStartedAtMs = null,
    )
  }

  fun applyStreamError(state: AgentChatState, reason: String, nowMs: Long, acceptedByServer: Boolean = false): AgentChatState {
    val idx = state.messages.indexOfLast { it.role == MessageRole.ASSISTANT && it.status == MessageStatus.STREAMING }
    if (idx < 0) {
      return state.copy(
        isStreaming = false,
        isSubmitting = false,
        streamingProgress = null,
        streamPhase = null,
        streamPhaseStartedAtMs = null,
        lastError = reason,
      )
    }
    val current = state.messages[idx]
    val hadContent = current.content.isNotBlank()
    val hadAcceptedWork = acceptedByServer || state.streamingProgress?.isNotBlank() == true || state.streamPhase != null
    val updated = state.messages[idx].copy(
      status = if (hadContent || hadAcceptedWork) MessageStatus.INTERRUPTED else MessageStatus.FAILED,
      content = when {
        hadContent -> current.content
        hadAcceptedWork -> "Connection lost while the server was still working."
        else -> "Failed to fetch response."
      },
      label = null,
      updatedAtMs = nowMs,
    )
    val next = state.messages.toMutableList()
    next[idx] = updated
    return state.copy(
      messages = next,
      isStreaming = false,
      isSubmitting = false,
      streamingProgress = null,
      streamPhase = null,
      streamPhaseStartedAtMs = null,
      lastError = reason,
    )
  }

  fun applyRecoveringExistingTurn(state: AgentChatState, nowMs: Long): AgentChatState {
    val idx = state.messages.indexOfLast { it.role == MessageRole.ASSISTANT && it.status == MessageStatus.STREAMING }
    if (idx < 0) {
      return state.copy(
        isStreaming = true,
        isSubmitting = true,
        streamingProgress = "Reconnecting to running turn...",
        streamPhase = null,
        streamPhaseStartedAtMs = nowMs,
        lastError = null,
        lastFailedPrompt = null,
      )
    }
    val updated = state.messages[idx].copy(label = null, updatedAtMs = nowMs)
    val next = state.messages.toMutableList()
    next[idx] = updated
    return state.copy(
      messages = next,
      isStreaming = true,
      isSubmitting = true,
      streamingProgress = "Reconnecting to running turn...",
      streamPhase = null,
      streamPhaseStartedAtMs = nowMs,
      lastError = null,
      lastFailedPrompt = null,
    )
  }

  fun applyStreamCancelled(state: AgentChatState, nowMs: Long): AgentChatState {
    val idx = state.messages.indexOfLast { it.role == MessageRole.ASSISTANT && it.status == MessageStatus.STREAMING }
    if (idx < 0) {
      return state.copy(
        isStreaming = false,
        isSubmitting = false,
        streamingProgress = null,
        streamPhase = null,
        streamPhaseStartedAtMs = null,
      )
    }
    val updated = state.messages[idx].copy(status = MessageStatus.CANCELLED, updatedAtMs = nowMs)
    val next = state.messages.toMutableList()
    next[idx] = updated
    return state.copy(
      messages = next,
      isStreaming = false,
      isSubmitting = false,
      streamingProgress = null,
      streamPhase = null,
      streamPhaseStartedAtMs = null,
    )
  }

  fun markUnread(state: AgentChatState): AgentChatState {
    return if (state.hasUnread) state else state.copy(hasUnread = true)
  }

  fun applyWorkflowLaunchStarted(
    state: AgentChatState,
    requestSummary: String,
    workflowLabel: String,
    messageEventKey: String,
    nowMs: Long,
  ): AgentChatState {
    val workflowMessage = ChatMessage(
      threadId = state.threadId,
      role = MessageRole.WORKFLOW,
      content = requestSummary,
      status = MessageStatus.SENDING,
      createdAtMs = nowMs,
      updatedAtMs = nowMs,
      label = workflowLabel,
      eventKey = messageEventKey,
    )
    return state.copy(
      messages = state.messages + workflowMessage,
      isLaunchingWorkflow = true,
      lastError = null,
      draft = "",
    )
  }

  fun applyWorkflowStarted(
    state: AgentChatState,
    workflowId: String,
    requestSummary: String? = null,
    pendingMessageEventKey: String? = null,
    nowMs: Long,
  ): AgentChatState {
    val nextMessages = updatePendingWorkflowLaunchMessage(
      state = state,
      status = MessageStatus.COMPLETE,
      workflowId = workflowId,
      requestSummary = requestSummary,
      failureReason = null,
      pendingMessageEventKey = pendingMessageEventKey,
      nowMs = nowMs,
    )
    return state.copy(
      messages = nextMessages,
      activeWorkflowId = workflowId,
      isLaunchingWorkflow = false,
      lastError = null,
      workflowCommentary = "",
    )
  }

  fun applyWorkflowLaunchFailed(
    state: AgentChatState,
    reason: String,
    pendingMessageEventKey: String? = null,
    nowMs: Long,
  ): AgentChatState {
    val nextMessages = updatePendingWorkflowLaunchMessage(
      state = state,
      status = MessageStatus.FAILED,
      workflowId = null,
      requestSummary = null,
      failureReason = reason,
      pendingMessageEventKey = pendingMessageEventKey,
      nowMs = nowMs,
    )
    return state.copy(
      messages = nextMessages,
      isLaunchingWorkflow = false,
      activeWorkflowId = null,
      lastError = reason,
    )
  }

  fun applyWorkflowFinished(state: AgentChatState): AgentChatState {
    return state.copy(
      activeWorkflowId = null,
      isLaunchingWorkflow = false,
    )
  }

  private fun updatePendingWorkflowLaunchMessage(
    state: AgentChatState,
    status: MessageStatus,
    workflowId: String?,
    requestSummary: String?,
    failureReason: String?,
    pendingMessageEventKey: String?,
    nowMs: Long,
  ): List<ChatMessage> {
    val idx = state.messages.indexOfLast {
      it.role == MessageRole.WORKFLOW &&
        it.status == MessageStatus.SENDING &&
        (pendingMessageEventKey == null || it.eventKey == pendingMessageEventKey)
    }
    if (idx < 0) return state.messages
    val current = state.messages[idx]
    val nextContent = when {
      status == MessageStatus.FAILED && !failureReason.isNullOrBlank() -> {
        val failedHeader = current.content.lineSequence().firstOrNull()
          ?.replace(" requested for ", " failed to start for ")
          ?: "Workflow launch failed"
        val remainingLines = current.content.lineSequence().drop(1).toList()
        buildString {
          append(failedHeader)
          remainingLines.forEach { line ->
            append('\n')
            append(line)
          }
          val suffix = "Launch failed: $failureReason"
          if (!current.content.contains(suffix)) {
            append('\n')
            append(suffix)
          }
        }
      }
      !requestSummary.isNullOrBlank() -> requestSummary
      else -> current.content
    }
    val next = state.messages.toMutableList()
    next[idx] = current.copy(
      content = nextContent,
      status = status,
      updatedAtMs = nowMs,
      workflowId = workflowId ?: current.workflowId,
    )
    return next
  }
}
