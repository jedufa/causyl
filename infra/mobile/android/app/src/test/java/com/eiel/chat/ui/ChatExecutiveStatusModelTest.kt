package com.eiel.chat.ui

import com.eiel.chat.domain.AgentChatState
import com.eiel.chat.domain.AgentTab
import com.eiel.chat.domain.ChatMessage
import com.eiel.chat.domain.MessageRole
import com.eiel.chat.domain.MessageStatus
import com.eiel.chat.domain.StreamPhase
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class ChatExecutiveStatusModelTest {
  @Test
  fun mapsOfflineTruthToExecutiveStatusCard() {
    val card = executiveStatusCardModel(
      state = AgentChatState(tab = AgentTab.ALL),
      serverHealthyKnown = true,
      serverHealthy = false,
    )

    assertNotNull(card)
    assertEquals("Offline from server", card?.headline)
  }

  @Test
  fun mapsReconnectProgressToReattachStatusCard() {
    val state = AgentChatState(
      tab = AgentTab.ALL,
      isStreaming = true,
      isSubmitting = true,
      streamingProgress = "Resuming reply...",
      streamPhase = null,
    )

    val card = executiveStatusCardModel(
      state = state,
      serverHealthyKnown = true,
      serverHealthy = true,
    )

    assertNotNull(card)
    assertEquals("Reattaching to running turn", card?.headline)
  }

  @Test
  fun mapsInterruptedTerminalToRetryableCard() {
    val interrupted = ChatMessage(
      threadId = AgentTab.ALL.threadId,
      role = MessageRole.ASSISTANT,
      content = "partial",
      status = MessageStatus.INTERRUPTED,
      createdAtMs = 1L,
      updatedAtMs = 1L,
    )
    val state = AgentChatState(
      tab = AgentTab.ALL,
      messages = listOf(interrupted),
      lastFailedPrompt = "retry prompt",
    )

    val card = executiveStatusCardModel(
      state = state,
      serverHealthyKnown = true,
      serverHealthy = true,
    )

    assertNotNull(card)
    assertEquals("Last turn interrupted", card?.headline)
    assertEquals(ExecutiveStatusAction.RETRY_LAST_FAILED, card?.action)
  }

  @Test
  fun yieldsNoExecutiveCardForHealthyIdleState() {
    val state = AgentChatState(tab = AgentTab.ALL)

    val card = executiveStatusCardModel(
      state = state,
      serverHealthyKnown = true,
      serverHealthy = true,
    )

    assertNull(card)
  }

  @Test
  fun hostVerificationBusyCardDoesNotPretendCompletion() {
    val state = AgentChatState(
      tab = AgentTab.ALL,
      isStreaming = true,
      isSubmitting = true,
      streamPhase = StreamPhase.HOST_VERIFICATION_WAIT,
    )

    val card = executiveStatusCardModel(
      state = state,
      serverHealthyKnown = true,
      serverHealthy = true,
    )

    assertNotNull(card)
    assertEquals("Waiting on verification runner", card?.headline)
  }
}
