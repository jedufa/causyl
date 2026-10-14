package com.eiel.chat.ui

import com.eiel.chat.data.CommitJournalEntry
import com.eiel.chat.data.RestartBlocker
import com.eiel.chat.data.RestartServerResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AdminUiStateTest {
  @Test
  fun terminalTimeoutDefaultsToSevenThousandSeconds() {
    assertEquals("7000", AdminUiState().cmdTimeoutSec)
  }

  @Test
  fun terminalViewDefaultsShownAndDeployStateStartsSeparate() {
    val state = AdminUiState()
    assertEquals("", state.terminalOutput)
    assertEquals("console", state.terminalOutputTitle)
  }

  @Test
  fun trimAdminOutputForDisplayKeepsTailAndAddsNoticeWhenTextIsLarge() {
    val input = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    val trimmed = trimAdminOutputForDisplay(input, maxChars = 10)

    assertTrue(trimmed.startsWith("[showing last 10 of 36 chars; use Copy all for the full transcript]"))
    assertTrue(trimmed.endsWith("QRSTUVWXYZ"))
  }

  @Test
  fun boundAdminConsoleOutputKeepsOnlyTailWindow() {
    val bounded = boundAdminConsoleOutput("abcdefghijklmnopqrstuvwxyz", maxChars = 8)

    assertEquals("stuvwxyz", bounded)
  }

  @Test
  fun appUpdateChannelTracksInstalledPackageFlavor() {
    assertEquals("debug", appUpdateChannelForPackageName("com.eiel.chat.debug"))
    assertEquals("release", appUpdateChannelForPackageName("com.eiel.chat"))
  }

  @Test
  fun appUpdateAvailabilityUsesShaComparisonNotVersionCode() {
    assertEquals(false, isAppUpdateAvailable("abc123", "ABC123"))
    assertEquals(true, isAppUpdateAvailable("abc123", "def456"))
    assertEquals(true, isAppUpdateAvailable("", "def456"))
    assertEquals(false, isAppUpdateAvailable("abc123", ""))
  }

  @Test
  fun gitQuickActionUsesSharedTopInputBeforeFallbackMessage() {
    assertEquals(
      "top message",
      resolveAdminGitMessage(
        sharedInput = "  top message  ",
        fallbackGitMessage = "fallback",
      ),
    )
    assertEquals(
      "fallback",
      resolveAdminGitMessage(
        sharedInput = "   ",
        fallbackGitMessage = " fallback ",
      ),
    )
  }

  @Test
  fun gitJournalStatusReportsReadyAndCoverageProblems() {
    assertEquals(
      "Journal: ready (2 pending)",
      renderAdminGitJournalStatus(
        com.eiel.chat.data.GitStatus(
          branch = "",
          head = "",
          describe = "",
          statusPorcelain = "",
          baselineSuggested = "",
          commitJournalPendingCount = 2,
          commitJournalInvalidCount = 0,
          commitJournalCoverageOk = true,
          commitJournalReady = true,
          commitJournalUncoveredCount = 0,
          commitJournalError = "",
          commitJournalPreviewSubject = "",
          stderr = "",
        ),
      ),
    )
    assertEquals(
      "Journal: 3 pending, 2 uncovered paths",
      renderAdminGitJournalStatus(
        com.eiel.chat.data.GitStatus(
          branch = "",
          head = "",
          describe = "",
          statusPorcelain = "",
          baselineSuggested = "",
          commitJournalPendingCount = 3,
          commitJournalInvalidCount = 0,
          commitJournalCoverageOk = false,
          commitJournalReady = false,
          commitJournalUncoveredCount = 2,
          commitJournalError = "",
          commitJournalPreviewSubject = "",
          stderr = "",
        ),
      ),
    )
  }

  @Test
  fun appUpdateBrowserUrlUsesStableMobileUpdateRoute() {
    assertEquals(
      "https://lab.example/admin/mobile/update?channel=debug&auto=1",
      buildMobileAppUpdateBrowserUrl(
        baseUrl = "https://lab.example/",
        channel = "debug",
      ),
    )
    assertEquals(
      "https://lab.example/admin/mobile/update?channel=release&auto=1",
      buildMobileAppUpdateBrowserUrl(
        baseUrl = "https://lab.example",
        channel = "release",
      ),
    )
  }

  @Test
  fun primaryDeployButtonDefaultsToDeployAndIsEnabled() {
    val state = AdminUiState()

    assertEquals("Deploy fresh", primaryDeployButtonLabel(state))
    assertEquals(true, isPrimaryDeployEnabled(state))
  }

  @Test
  fun primaryDeployButtonReflectsUpdateFlowState() {
    assertEquals(
      "Deploying...",
      primaryDeployButtonLabel(AdminUiState(appUpdateBuilding = true)),
    )
    assertEquals(
      "Downloading...",
      primaryDeployButtonLabel(AdminUiState(appUpdateDownloading = true)),
    )
    assertEquals(false, isPrimaryDeployEnabled(AdminUiState(appUpdateBuilding = true)))
    assertEquals(false, isPrimaryDeployEnabled(AdminUiState(appUpdateDownloading = true)))
  }

  @Test
  fun journalViewerDefaultsToNewestEntryAndClampsNavigation() {
    assertEquals(0, initialJournalViewerIndex(0))
    assertEquals(2, initialJournalViewerIndex(3))
    assertEquals(0, normalizeJournalViewerIndex(-1, 3))
    assertEquals(2, normalizeJournalViewerIndex(9, 3))
    assertEquals(2, stepJournalViewerIndex(1, 1, 3))
    assertEquals(0, stepJournalViewerIndex(0, -1, 3))
    assertEquals(1, resolveJournalViewerIndex(listOf("a", "b", "c"), "b"))
    assertEquals(2, resolveJournalViewerIndex(listOf("a", "b", "c"), "missing"))
  }

  @Test
  fun commitJournalMetaIncludesRoleBindingAndReadableTime() {
    val meta = formatCommitJournalMeta(
      CommitJournalEntry(
        entryId = "cjn_demo",
        createdUtc = "2026-03-11T10:37:43Z",
        title = "demo",
        outcome = "applied",
        scopeHint = "",
        authorRole = "generalist",
        authorSource = "codex",
        bindingType = "task_id",
        bindingId = "mbl",
      ),
    )

    assertTrue(meta.contains("generalist"))
    assertTrue(meta.contains("mbl"))
    assertTrue(meta.contains("2026-03-11"))
  }

  @Test
  fun commitJournalFactsSummarizeEntryDensity() {
    val facts = commitJournalEntryFacts(
      CommitJournalEntry(
        entryId = "cjn_demo",
        createdUtc = "",
        title = "demo",
        outcome = "partial",
        scopeHint = "",
        authorRole = "",
        authorSource = "",
        bindingType = "",
        bindingId = "",
        summaryBullets = listOf("one", "two"),
        files = listOf("a.kt"),
        validation = listOf("pytest x", "pytest y"),
        notes = listOf("note"),
      ),
    )

    assertEquals(listOf("2 summaries", "1 file", "2 checks", "1 note"), facts)
  }

  @Test
  fun commitJournalViewerSummaryCountsOutcomes() {
    val summary = formatCommitJournalViewerSummary(
      listOf(
        CommitJournalEntry(
          entryId = "a",
          createdUtc = "",
          title = "first",
          outcome = "applied",
          scopeHint = "",
          authorRole = "",
          authorSource = "",
          bindingType = "",
          bindingId = "",
        ),
        CommitJournalEntry(
          entryId = "b",
          createdUtc = "",
          title = "second",
          outcome = "partial",
          scopeHint = "",
          authorRole = "",
          authorSource = "",
          bindingType = "",
          bindingId = "",
        ),
        CommitJournalEntry(
          entryId = "c",
          createdUtc = "",
          title = "third",
          outcome = "note_only",
          scopeHint = "",
          authorRole = "",
          authorSource = "",
          bindingType = "",
          bindingId = "",
        ),
      ),
    )

    assertEquals("3 pending · 1 applied · 1 partial · 1 note", summary)
  }

  @Test
  fun restartStatusMessageDoesNotDuplicateBlockerSummaries() {
    val message = renderRestartStatusMessage(
      RestartServerResult(
        accepted = false,
        blocked = true,
        message = "Restart blocked because the plane still has active work or live scheduler claims.",
        healthStatus = "blocked",
        blockers = listOf(
          RestartBlocker(
            kind = "active_tasks_present",
            count = 15,
            summary = "15 active task(s) are still visible to the plane.",
          ),
          RestartBlocker(
            kind = "open_loops_present",
            count = 18,
            summary = "18 open loop(s) are still visible to the plane.",
          ),
        ),
        staleSchedulerJobLockCount = 2,
      ),
    )

    assertEquals(
      "Restart blocked because the plane still has active work or live scheduler claims. Ignored 2 stale scheduler claim(s).",
      message,
    )
  }
}
