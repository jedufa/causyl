package com.eiel.chat.ui

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import org.junit.Rule
import org.junit.Test

class AppShellNavigationTest {
  @get:Rule
  val composeRule = createComposeRule()

  @Test
  fun selectingSchedulerTabRendersSchedulerRoute() {
    val application = ApplicationProvider.getApplicationContext<Application>()
    val chatVm = ChatViewModel(
      application = application,
      startBackgroundWork = false,
      enableHeartbeatPolling = false,
      enableWorkflowStatusPolling = false,
    )

    composeRule.setContent {
      MaterialTheme {
        AppShell(
          chatVm = chatVm,
          adminVm = AdminViewModel(application),
        )
      }
    }

    composeRule.onNodeWithText("Menu").performClick()
    composeRule.onNodeWithText("Scheduler").performClick()
    composeRule.onNodeWithText("Scheduler service").assertIsDisplayed()
    composeRule.onNodeWithText("Menu").assertIsDisplayed()
  }
}
