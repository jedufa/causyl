package com.eiel.chat.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.eiel.chat.domain.ChatMessage
import com.eiel.chat.domain.AgentChatState
import com.eiel.chat.domain.AgentTab
import com.eiel.chat.domain.ChatUiState
import com.eiel.chat.domain.MessageRole
import com.eiel.chat.domain.MessageStatus
import com.eiel.chat.domain.StreamPhase
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class ChatScreenTest {
  @get:Rule
  val composeRule = createComposeRule()

  @Test
  fun speechStopOverlayShowsOnlyWhenVisibleAndStopsOnTap() {
    var stopCount = 0

    composeRule.setContent {
      MaterialTheme {
        SpeechStopOverlay(
          visible = false,
          onStop = { stopCount += 1 },
        )
      }
    }

    assertNodeDoesNotExist(composeRule.onNodeWithTag(SpeechStopButtonTestTag))

    composeRule.setContent {
      MaterialTheme {
        SpeechStopOverlay(
          visible = true,
          onStop = { stopCount += 1 },
        )
      }
    }

    assertNodeExists(composeRule.onNodeWithTag(SpeechStopButtonTestTag)).performClick()
    composeRule.runOnIdle {
      assertEquals(1, stopCount)
    }
  }

  @Test
  fun busyDraftShowsQueueActionInsteadOfStop() {
    val active = AgentChatState(
      tab = AgentTab.ALL,
      draft = "queue this next",
      isStreaming = true,
    )
    val state = ChatUiState(
      selectedTab = AgentTab.ALL,
      agentStates = AgentTab.entries.associateWith { tab ->
        if (tab == AgentTab.ALL) active else AgentChatState(tab = tab)
      },
    )

    composeRule.setContent {
      MaterialTheme {
        ChatScreen(
          state = state,
          onTabSelected = {},
          onDraftChanged = {},
          onSelectedPisChanged = {},
          onPisSelectorOpened = {},
          onWorkflowCommentaryChanged = {},
          onSendClicked = {},
          onLaunchWorkflowClicked = {},
          onWorkflowCancelRequested = {},
          onStopClicked = {},
          onQueueTailPopClicked = {},
          onRetryLastFailedClicked = {},
          onCapContinueClicked = {},
          onCapIncreaseClicked = {},
          onCapDialogDismissed = {},
        )
      }
    }

    assertNodeExists(composeRule.onNodeWithContentDescription("Queue message"))
    assertNodeDoesNotExist(composeRule.onNodeWithContentDescription("Stop response"))
  }

  @Test
  fun headerShowsStructuredShortTitleOnly() {
    val active = AgentChatState(
      tab = AgentTab.ALL_2,
      title = "MOBI",
    )
    val state = ChatUiState(
      selectedTab = AgentTab.ALL_2,
      agentStates = AgentTab.entries.associateWith { tab ->
        if (tab == AgentTab.ALL_2) active else AgentChatState(tab = tab)
      },
    )

    composeRule.setContent {
      MaterialTheme {
        ChatScreen(
          state = state,
          onTabSelected = {},
          onDraftChanged = {},
          onSelectedPisChanged = {},
          onPisSelectorOpened = {},
          onWorkflowCommentaryChanged = {},
          onSendClicked = {},
          onLaunchWorkflowClicked = {},
          onWorkflowCancelRequested = {},
          onStopClicked = {},
          onQueueTailPopClicked = {},
          onRetryLastFailedClicked = {},
          onCapContinueClicked = {},
          onCapIncreaseClicked = {},
          onCapDialogDismissed = {},
        )
      }
    }

    assertNodeExists(composeRule.onNodeWithText("MOBI"))
    assertNodeDoesNotExist(composeRule.onNodeWithText("Mobile updater browser fallback"))
  }

  @Test
  fun tabStripKeepsFixedAgentChipLabelsEvenWhenOtherTabsHaveTitles() {
    val state = ChatUiState(
      selectedTab = AgentTab.ALL,
      agentStates = AgentTab.entries.associateWith { tab ->
        when (tab) {
          AgentTab.ALL_2 -> AgentChatState(
            tab = tab,
            title = "CHEM",
          )
          else -> AgentChatState(tab = tab)
        }
      },
    )

    composeRule.setContent {
      MaterialTheme {
        ChatScreen(
          state = state,
          onTabSelected = {},
          onDraftChanged = {},
          onSelectedPisChanged = {},
          onPisSelectorOpened = {},
          onWorkflowCommentaryChanged = {},
          onSendClicked = {},
          onLaunchWorkflowClicked = {},
          onWorkflowCancelRequested = {},
          onStopClicked = {},
          onQueueTailPopClicked = {},
          onRetryLastFailedClicked = {},
          onCapContinueClicked = {},
          onCapIncreaseClicked = {},
          onCapDialogDismissed = {},
        )
      }
    }

    assertNodeExists(composeRule.onNodeWithText("A"))
    assertNodeExists(composeRule.onNodeWithText("A2"))
    assertNodeExists(composeRule.onNodeWithText("A3"))
    assertNodeExists(composeRule.onNodeWithText("O"))
    assertNodeExists(composeRule.onNodeWithText("B"))
    assertNodeExists(composeRule.onNodeWithText("V"))
    assertNodeDoesNotExist(composeRule.onNodeWithText("CHEM"))
  }

  @Test
  fun runnerWaitStatusStripShowsOnlyDuringHostVerificationPhase() {
    val waiting = AgentChatState(
      tab = AgentTab.ALL,
      isStreaming = true,
      streamPhase = StreamPhase.HOST_VERIFICATION_WAIT,
      streamPhaseStartedAtMs = System.currentTimeMillis() - 5_000L,
    )
    val thinking = AgentChatState(
      tab = AgentTab.ALL,
      isStreaming = true,
    )

    fun render(active: AgentChatState) {
      val state = ChatUiState(
        selectedTab = AgentTab.ALL,
        agentStates = AgentTab.entries.associateWith { tab ->
          if (tab == AgentTab.ALL) active else AgentChatState(tab = tab)
        },
      )
      composeRule.setContent {
        MaterialTheme {
          ChatScreen(
            state = state,
            onTabSelected = {},
            onDraftChanged = {},
            onSelectedPisChanged = {},
            onPisSelectorOpened = {},
            onWorkflowCommentaryChanged = {},
            onSendClicked = {},
            onLaunchWorkflowClicked = {},
            onWorkflowCancelRequested = {},
            onStopClicked = {},
            onQueueTailPopClicked = {},
            onRetryLastFailedClicked = {},
            onCapContinueClicked = {},
            onCapIncreaseClicked = {},
            onCapDialogDismissed = {},
          )
        }
      }
    }

    render(waiting)
    assertNodeExists(composeRule.onNodeWithTag(RunnerWaitStatusStripTestTag))

    render(thinking)
    assertNodeDoesNotExist(composeRule.onNodeWithTag(RunnerWaitStatusStripTestTag))
  }

  @Test
  fun executiveStatusCardShowsOfflineTruthBeforeChatList() {
    val state = ChatUiState(
      selectedTab = AgentTab.ALL,
      serverHealthyKnown = true,
      serverHealthy = false,
      agentStates = AgentTab.entries.associateWith { tab -> AgentChatState(tab = tab) },
    )

    composeRule.setContent {
      MaterialTheme {
        ChatScreen(
          state = state,
          onTabSelected = {},
          onDraftChanged = {},
          onSelectedPisChanged = {},
          onPisSelectorOpened = {},
          onWorkflowCommentaryChanged = {},
          onSendClicked = {},
          onLaunchWorkflowClicked = {},
          onWorkflowCancelRequested = {},
          onStopClicked = {},
          onQueueTailPopClicked = {},
          onRetryLastFailedClicked = {},
          onCapContinueClicked = {},
          onCapIncreaseClicked = {},
          onCapDialogDismissed = {},
        )
      }
    }

    assertNodeExists(composeRule.onNodeWithTag(ExecutiveStatusCardTestTag))
    assertNodeExists(composeRule.onNodeWithText("Offline from server"))
  }

  @Test
  fun executiveStatusCardSurfacesInterruptedTurnAndRetryAction() {
    var retryCount = 0
    val interruptedMessage = ChatMessage(
      threadId = AgentTab.ALL.threadId,
      role = MessageRole.ASSISTANT,
      content = "Partial answer",
      status = MessageStatus.INTERRUPTED,
      createdAtMs = 1L,
      updatedAtMs = 1L,
    )
    val active = AgentChatState(
      tab = AgentTab.ALL,
      messages = listOf(interruptedMessage),
      lastFailedPrompt = "retry me",
    )
    val state = ChatUiState(
      selectedTab = AgentTab.ALL,
      serverHealthyKnown = true,
      serverHealthy = true,
      agentStates = AgentTab.entries.associateWith { tab ->
        if (tab == AgentTab.ALL) active else AgentChatState(tab = tab)
      },
    )

    composeRule.setContent {
      MaterialTheme {
        ChatScreen(
          state = state,
          onTabSelected = {},
          onDraftChanged = {},
          onSelectedPisChanged = {},
          onPisSelectorOpened = {},
          onWorkflowCommentaryChanged = {},
          onSendClicked = {},
          onLaunchWorkflowClicked = {},
          onWorkflowCancelRequested = {},
          onStopClicked = {},
          onQueueTailPopClicked = {},
          onRetryLastFailedClicked = { retryCount += 1 },
          onCapContinueClicked = {},
          onCapIncreaseClicked = {},
          onCapDialogDismissed = {},
        )
      }
    }

    assertNodeExists(composeRule.onNodeWithTag(ExecutiveStatusCardTestTag))
    assertNodeExists(composeRule.onNodeWithText("Last turn interrupted"))
    assertNodeExists(composeRule.onNodeWithText("Retry")).performClick()
    composeRule.runOnIdle {
      assertEquals(1, retryCount)
    }
  }

  @Test
  fun stopQuickActionRequiresConfirmationBeforeDispatch() {
    var stopCount = 0
    val active = AgentChatState(
      tab = AgentTab.ALL,
      isStreaming = true,
    )
    val state = ChatUiState(
      selectedTab = AgentTab.ALL,
      serverHealthyKnown = true,
      serverHealthy = true,
      agentStates = AgentTab.entries.associateWith { tab ->
        if (tab == AgentTab.ALL) active else AgentChatState(tab = tab)
      },
    )

    composeRule.setContent {
      MaterialTheme {
        ChatScreen(
          state = state,
          onTabSelected = {},
          onDraftChanged = {},
          onSelectedPisChanged = {},
          onPisSelectorOpened = {},
          onWorkflowCommentaryChanged = {},
          onSendClicked = {},
          onLaunchWorkflowClicked = {},
          onWorkflowCancelRequested = {},
          onStopClicked = { stopCount += 1 },
          onQueueTailPopClicked = {},
          onRetryLastFailedClicked = {},
          onCapContinueClicked = {},
          onCapIncreaseClicked = {},
          onCapDialogDismissed = {},
        )
      }
    }

    assertNodeExists(composeRule.onNodeWithContentDescription("Stop response")).performClick()
    composeRule.runOnIdle {
      assertEquals(0, stopCount)
    }
    assertNodeExists(composeRule.onNodeWithText(StopTurnConfirmDialogTitle))
    assertNodeExists(composeRule.onNodeWithText("Stop")).performClick()
    composeRule.runOnIdle {
      assertEquals(1, stopCount)
    }
  }

  private fun assertNodeExists(interaction: SemanticsNodeInteraction): SemanticsNodeInteraction {
    interaction.fetchSemanticsNode()
    return interaction
  }

  private fun assertNodeDoesNotExist(interaction: SemanticsNodeInteraction) {
    assertTrue(runCatching { interaction.fetchSemanticsNode() }.isFailure)
  }
}
