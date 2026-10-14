package com.eiel.chat.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatExecutiveLayoutModelTest {
  @Test
  fun chatTopSectionOrderKeepsExecutiveStatusBeforeErrorsAndMessages() {
    val sections = chatTopSectionOrder(
      hasExecutiveStatus = true,
      hasErrorBanner = true,
    )

    assertEquals(
      listOf(
        ChatTopSection.EXECUTIVE_STATUS,
        ChatTopSection.ERROR_BANNER,
        ChatTopSection.MESSAGE_LIST,
      ),
      sections,
    )
  }

  @Test
  fun composerAuxSectionOrderKeepsRunnerStripAheadOfQueuePreview() {
    val sections = composerAuxSectionOrder(
      showRunnerWait = true,
      queueCount = 2,
    )

    assertEquals(
      listOf(
        ComposerAuxSection.RUNNER_WAIT,
        ComposerAuxSection.QUEUE_PREVIEW,
      ),
      sections,
    )
  }

  @Test
  fun stopQuickActionRequiresExplicitConfirmationBeforeDispatch() {
    val requested = reduceStopQuickAction(
      confirmationPending = false,
      event = StopQuickActionEvent.REQUEST_STOP,
    )
    assertTrue(requested.confirmationPending)
    assertFalse(requested.shouldDispatchStop)

    val confirmed = reduceStopQuickAction(
      confirmationPending = requested.confirmationPending,
      event = StopQuickActionEvent.CONFIRM_STOP,
    )
    assertFalse(confirmed.confirmationPending)
    assertTrue(confirmed.shouldDispatchStop)
  }

  @Test
  fun stopQuickActionDismissClearsPendingWithoutDispatch() {
    val dismissed = reduceStopQuickAction(
      confirmationPending = true,
      event = StopQuickActionEvent.DISMISS,
    )

    assertFalse(dismissed.confirmationPending)
    assertFalse(dismissed.shouldDispatchStop)
  }
}
