package com.eiel.chat

import com.eiel.chat.domain.AgentChatState
import com.eiel.chat.domain.AgentTab
import com.eiel.chat.domain.ChatReducer
import com.eiel.chat.domain.MessageRole
import com.eiel.chat.domain.MessageStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HostVerificationSentinelTest {

  @Test
  fun sendStartedCreatesStreamingAssistantBubble() {
    val state = ChatReducer.applySendStarted(
      AgentChatState(tab = AgentTab.ALL, draft = "hello"),
      prompt = "hello",
      nowMs = 10L,
    )

    assertEquals(2, state.messages.size)
    assertEquals(MessageRole.USER, state.messages.first().role)
    assertEquals(MessageRole.ASSISTANT, state.messages.last().role)
    assertEquals(MessageStatus.STREAMING, state.messages.last().status)
    assertTrue(state.isStreaming)
    assertTrue(state.isSubmitting)
  }

  @Test
  fun workflowLaunchFailureMarksPendingBubbleFailed() {
    val started = ChatReducer.applyWorkflowLaunchStarted(
      state = AgentChatState(tab = AgentTab.ORCHESTRATOR),
      requestSummary = "WF-01 requested for PIS-006",
      workflowLabel = "WF-01",
      messageEventKey = "launch|o|PIS-006|100",
      nowMs = 100L,
    )

    val failed = ChatReducer.applyWorkflowLaunchFailed(
      state = started,
      reason = "HTTP 500",
      pendingMessageEventKey = "launch|o|PIS-006|100",
      nowMs = 140L,
    )

    assertEquals(1, failed.messages.size)
    assertEquals(MessageStatus.FAILED, failed.messages.single().status)
    assertFalse(failed.isLaunchingWorkflow)
    assertEquals("HTTP 500", failed.lastError)
  }
}
