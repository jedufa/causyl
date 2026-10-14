package com.eiel.chat.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SharedDocumentIntakeTest {
  @Test
  fun inferSharedDocumentTypePrefersPatchExtensionsAndReportNames() {
    assertEquals(
      SharedDocumentType.PATCH,
      inferSharedDocumentType(
        SharedDocumentPayload(
          requestKey = "1",
          filename = "fix.patch",
          mimeType = "text/x-diff",
          text = "diff --git a/file b/file",
        ),
      ),
    )
    assertEquals(
      SharedDocumentType.AUDIT_REPORT,
      inferSharedDocumentType(
        SharedDocumentPayload(
          requestKey = "2",
          filename = "ops-notes.md",
          mimeType = "text/markdown",
          text = "# Audit report\n\nFindings",
        ),
      ),
    )
  }

  @Test
  fun buildSharedImportMarkdownWrapsReportsWithImportMetadata() {
    val markdown = buildSharedImportMarkdown(
      type = SharedDocumentType.PATCH_REPORT,
      title = "Patch report",
      text = "Applied fix and verified behavior.",
      filename = "patch-report.md",
      mimeType = "text/markdown",
    )

    assertTrue(markdown.contains("## Imported document"))
    assertTrue(markdown.contains("- Type: Patch report"))
    assertTrue(markdown.contains("Applied fix and verified behavior."))
  }
}
