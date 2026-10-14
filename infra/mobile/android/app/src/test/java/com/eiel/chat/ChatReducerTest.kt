package com.eiel.chat

import com.eiel.chat.domain.ChatReducer
import com.eiel.chat.domain.AgentChatState
import com.eiel.chat.domain.AgentTab
import com.eiel.chat.domain.MessageRole
import com.eiel.chat.domain.MessageStatus
import com.eiel.chat.domain.StreamPhase
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatReducerTest {

  @Test
  fun sendStartedAppendsUserAndAssistantStreamingRows() {
    val s0 = AgentChatState(tab = AgentTab.ALL, draft = "hello")
    val s1 = ChatReducer.applySendStarted(s0, prompt = "hello", nowMs = 10L)

    assertEquals(2, s1.messages.size)
    assertEquals(MessageRole.USER, s1.messages[0].role)
    assertEquals(MessageRole.ASSISTANT, s1.messages[1].role)
    assertEquals(MessageStatus.STREAMING, s1.messages[1].status)
    assertTrue(s1.isStreaming)
    assertTrue(s1.isSubmitting)
    assertEquals("", s1.draft)
  }

  @Test
  fun streamTokenAndDoneUpdateAssistantRowDeterministically() {
    val s0 = AgentChatState(tab = AgentTab.ALL, draft = "hello")
    val s1 = ChatReducer.applySendStarted(s0, prompt = "hello", nowMs = 10L)
    val s2 = ChatReducer.applyStreamToken(s1, "A", nowMs = 11L)
    val s3 = ChatReducer.applyStreamToken(s2, "B", nowMs = 12L)
    val s4 = ChatReducer.applyStreamDone(s3, nowMs = 13L)

    val assistant = s4.messages.last()
    assertEquals("AB", assistant.content)
    assertEquals(MessageStatus.COMPLETE, assistant.status)
    assertFalse(s4.isStreaming)
    assertFalse(s4.isSubmitting)
  }

  @Test
  fun streamProgressIsTemporaryAndFirstTokenReplacesIt() {
    val s0 = AgentChatState(tab = AgentTab.ALL, draft = "hello")
    val s1 = ChatReducer.applySendStarted(s0, prompt = "hello", nowMs = 10L)
    val s2 = ChatReducer.applyStreamProgress(s1, "Checking the code path", phase = null, nowMs = 11L)
    val s3 = ChatReducer.applyStreamToken(s2, "Final", nowMs = 12L)
    val s4 = ChatReducer.applyStreamToken(s3, " answer", nowMs = 13L)
    val s5 = ChatReducer.applyStreamDone(s4, nowMs = 14L)

    val assistant = s5.messages.last()
    assertEquals("Final answer", assistant.content)
    assertNull(assistant.label)
    assertEquals(MessageStatus.COMPLETE, assistant.status)
  }

  @Test
  fun streamErrorAfterProgressStillFailsBubble() {
    val s0 = AgentChatState(tab = AgentTab.ALL, draft = "hello")
    val s1 = ChatReducer.applySendStarted(s0, prompt = "hello", nowMs = 10L)
    val s2 = ChatReducer.applyStreamProgress(s1, "Checking the code path", phase = null, nowMs = 11L)
    val s3 = ChatReducer.applyStreamError(s2, reason = "timeout", nowMs = 12L)

    val assistant = s3.messages.last()
    assertEquals(MessageStatus.FAILED, assistant.status)
    assertEquals("Failed to fetch response.", assistant.content)
    assertEquals("timeout", s3.lastError)
  }

  @Test
  fun hostVerificationProgressTracksPhaseUntilTerminalEvent() {
    val s0 = AgentChatState(tab = AgentTab.ALL, draft = "hello")
    val s1 = ChatReducer.applySendStarted(s0, prompt = "hello", nowMs = 10L)
    val s2 = ChatReducer.applyStreamToken(s1, "Answer ready.", nowMs = 20L)
    val s3 = ChatReducer.applyStreamProgress(
      s2,
      text = "Running host verification...",
      phase = StreamPhase.HOST_VERIFICATION_WAIT,
      nowMs = 30L,
    )
    val s4 = ChatReducer.applyStreamDone(s3, nowMs = 40L)

    assertEquals(StreamPhase.HOST_VERIFICATION_WAIT, s3.streamPhase)
    assertEquals(30L, s3.streamPhaseStartedAtMs)
    assertEquals("Running host verification...", s3.streamingProgress)
    assertNull(s4.streamPhase)
    assertNull(s4.streamPhaseStartedAtMs)
    assertNull(s4.streamingProgress)
  }

  @Test
  fun recoveringExistingTurnKeepsStreamingStateAlive() {
    val s0 = AgentChatState(tab = AgentTab.ALL, draft = "hello")
    val s1 = ChatReducer.applySendStarted(s0, prompt = "hello", nowMs = 10L)
    val s2 = ChatReducer.applyRecoveringExistingTurn(s1, nowMs = 20L)

    val assistant = s2.messages.last()
    assertEquals(MessageRole.ASSISTANT, assistant.role)
    assertEquals(MessageStatus.STREAMING, assistant.status)
    assertTrue(s2.isStreaming)
    assertTrue(s2.isSubmitting)
    assertEquals("Reconnecting to running turn...", s2.streamingProgress)
    assertEquals(20L, s2.streamPhaseStartedAtMs)
    assertNull(s2.lastError)
  }

  @Test
  fun planeRoutedMarksCurrentUserAndStreamingAssistantBubbles() {
    val s0 = AgentChatState(tab = AgentTab.ALL, draft = "hello")
    val s1 = ChatReducer.applySendStarted(s0, prompt = "hello", nowMs = 10L)
    val s2 = ChatReducer.applyPlaneRouted(s1, nowMs = 11L)

    assertTrue(s2.messages[0].planeRouted)
    assertTrue(s2.messages[1].planeRouted)
  }

  @Test
  fun repeatedHostVerificationProgressKeepsOriginalPhaseStartTime() {
    val s0 = AgentChatState(tab = AgentTab.ALL, draft = "hello")
    val s1 = ChatReducer.applySendStarted(s0, prompt = "hello", nowMs = 10L)
    val s2 = ChatReducer.applyStreamToken(s1, "Answer ready.", nowMs = 20L)
    val s3 = ChatReducer.applyStreamProgress(
      s2,
      text = "Running host verification...",
      phase = StreamPhase.HOST_VERIFICATION_WAIT,
      nowMs = 30L,
    )
    val s4 = ChatReducer.applyStreamProgress(
      s3,
      text = "Running host verification...",
      phase = StreamPhase.HOST_VERIFICATION_WAIT,
      nowMs = 45L,
    )

    assertEquals(StreamPhase.HOST_VERIFICATION_WAIT, s4.streamPhase)
    assertEquals(30L, s4.streamPhaseStartedAtMs)
    assertEquals("Running host verification...", s4.streamingProgress)
  }

  @Test
  fun markUnreadAndSelectedToggleUnreadState() {
    val s0 = AgentChatState(tab = AgentTab.BUILDER)
    val s1 = ChatReducer.markUnread(s0)
    val s2 = ChatReducer.applySelected(s1)

    assertTrue(s1.hasUnread)
    assertFalse(s2.hasUnread)
  }

  @Test
  fun workflowLaunchLifecycleUsesWorkflowBubbleAndClearsCommentaryOnStart() {
    val s0 = AgentChatState(
      tab = AgentTab.ORCHESTRATOR,
      workflowCommentary = "Check edge cases",
    )
    val s1 = ChatReducer.applyWorkflowLaunchStarted(
      state = s0,
      requestSummary = "WF-01 requested for PIS-006\nAdditional request: Check edge cases",
      workflowLabel = "WF-01",
      messageEventKey = "launch|o|PIS-006|100",
      nowMs = 100L,
    )
    val s2 = ChatReducer.applyWorkflowStarted(
      state = s1,
      workflowId = "wf-123",
      pendingMessageEventKey = "launch|o|PIS-006|100",
      nowMs = 120L,
    )
    val s3 = ChatReducer.applyWorkflowFinished(s2)

    assertEquals(1, s1.messages.size)
    assertEquals(MessageRole.WORKFLOW, s1.messages.single().role)
    assertEquals("WF-01", s1.messages.single().label)
    assertEquals(MessageStatus.SENDING, s1.messages.single().status)
    assertTrue(s1.messages.single().content.contains("PIS-006"))
    assertTrue(s1.messages.single().content.contains("Additional request"))
    assertTrue(s1.isLaunchingWorkflow)

    assertEquals("wf-123", s2.activeWorkflowId)
    assertFalse(s2.isLaunchingWorkflow)
    assertEquals("", s2.workflowCommentary)
    assertEquals(MessageStatus.COMPLETE, s2.messages.single().status)
    assertEquals("wf-123", s2.messages.single().workflowId)
    assertEquals(120L, s2.messages.single().updatedAtMs)

    assertNull(s3.activeWorkflowId)
    assertFalse(s3.isLaunchingWorkflow)
  }

  @Test
  fun workflowLaunchFailureMarksPendingBubbleFailed() {
    val started = ChatReducer.applyWorkflowLaunchStarted(
      state = AgentChatState(tab = AgentTab.ORCHESTRATOR),
      requestSummary = "WF-01 requested for PIS-006\nAdditional request: Check edge cases",
      workflowLabel = "WF-01",
      messageEventKey = "launch|o|PIS-006|100",
      nowMs = 100L,
    )

    val failed = ChatReducer.applyWorkflowLaunchFailed(
      state = started,
      reason = "HTTP 500",
      pendingMessageEventKey = "launch|o|PIS-006|100",
      nowMs = 150L,
    )

    assertEquals(1, failed.messages.size)
    assertEquals(MessageStatus.FAILED, failed.messages.single().status)
    assertTrue(failed.messages.single().content.contains("failed to start"))
    assertTrue(failed.messages.single().content.contains("Additional request"))
    assertTrue(failed.messages.single().content.contains("Launch failed: HTTP 500"))
    assertEquals(150L, failed.messages.single().updatedAtMs)
    assertEquals("HTTP 500", failed.lastError)
  }
}
