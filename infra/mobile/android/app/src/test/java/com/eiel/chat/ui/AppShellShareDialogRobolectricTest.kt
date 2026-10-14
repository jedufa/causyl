package com.eiel.chat.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class AppShellShareDialogRobolectricTest {
  @get:Rule
  val composeRule = createComposeRule()

  @Test
  fun sharedDocumentRendersImportDialog() {
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
          sharedDocument = SharedDocumentPayload(
            requestKey = "shared-1",
            filename = "audit.md",
            mimeType = "text/markdown",
            text = "# Audit report\n\nFindings",
          ),
        )
      }
    }

    composeRule.onNodeWithText("Import shared document").assertIsDisplayed()
    composeRule.onNodeWithText("Audit report").assertIsDisplayed()
    composeRule.onNodeWithText("Process").assertIsDisplayed()
  }
}
