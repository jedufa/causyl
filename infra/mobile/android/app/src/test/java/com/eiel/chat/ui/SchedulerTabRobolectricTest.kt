package com.eiel.chat.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class SchedulerTabRobolectricTest {
  @get:Rule
  val composeRule = createComposeRule()

  @Test
  fun renderingSchedulerTabFromInitialRouteDisplaysSchedulerService() {
    val application = RuntimeEnvironment.getApplication()

    composeRule.setContent {
      MaterialTheme {
        AppShell(
          chatVm = ChatViewModel(
            application = application,
            startBackgroundWork = false,
            enableHeartbeatPolling = false,
            enableWorkflowStatusPolling = false,
          ),
          adminVm = AdminViewModel(application),
          initialRouteName = "SCHEDULER",
        )
      }
    }

    composeRule.onNodeWithText("Scheduler service", useUnmergedTree = true).assertIsDisplayed()
    composeRule.onNodeWithTag("app-shell-menu-button").assertIsDisplayed()
  }
}
