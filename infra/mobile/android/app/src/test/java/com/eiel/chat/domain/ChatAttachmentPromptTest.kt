package com.eiel.chat.domain

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatAttachmentPromptTest {
  @Test
  fun buildPromptWithAttachedDocumentsInjectsDocumentContextAheadOfPrompt() {
    val prompt = buildPromptWithAttachedDocuments(
      prompt = "Summarize the issue.",
      documents = listOf(
        ChatAttachedDocument(
          filename = "audit.md",
          mimeType = "text/markdown",
          text = "# Audit\n\nFailure mode found.",
          truncated = false,
        ),
      ),
    )

    assertTrue(prompt.contains("Attached documents for implicit context:"))
    assertTrue(prompt.contains("[Document 1] audit.md"))
    assertTrue(prompt.contains("Failure mode found."))
    assertTrue(prompt.endsWith("Summarize the issue."))
  }

  @Test
  fun buildPromptWithAttachedDocumentsLeavesPlainPromptUntouchedWhenNoDocumentsExist() {
    val prompt = buildPromptWithAttachedDocuments(prompt = "Hello", documents = emptyList())

    assertTrue(prompt == "Hello")
    assertFalse(prompt.contains("Attached documents for implicit context"))
  }
}
