package com.eiel.chat.ui

import org.junit.Assert.assertTrue
import org.junit.Assert.assertFalse
import org.junit.Test
import java.io.File

class AppShellNavigationSourceOfTruthTest {
  @Test
  fun appShellDefinesHomeFrontDoorAndKeepsSchedulerRouteAvailable() {
    val possibleFiles = listOf(
      "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AppShell.kt",
      "app/src/main/java/com/eiel/chat/ui/AppShell.kt",
      "src/main/java/com/eiel/chat/ui/AppShell.kt",
      "../app/src/main/java/com/eiel/chat/ui/AppShell.kt",
    )
    val appShellFile = possibleFiles.map(::File).firstOrNull { it.exists() } ?: throw AssertionError(
      "AppShell source path not found",
    )
    val appShellSource = appShellFile.readText()

    assertTrue(appShellSource.contains("HOME(\"Home\")"))
    assertTrue(appShellSource.contains("initialRouteName: String = RootView.HOME.name"))
    assertTrue(appShellSource.contains("RootView.HOME -> OverviewRoute"))
    assertTrue(appShellSource.contains("BACKLOG(\"The plane\")"))
    assertTrue(appShellSource.contains("SCHEDULER(\"Scheduler\")"))
    assertTrue(appShellSource.contains("RootView.SCHEDULER -> SchedulerRoute"))
    assertTrue(appShellSource.contains("RootView.entries.forEach { item ->"))
    assertFalse(appShellSource.contains("LIFE_LOAD(\"Life Load\")"))
    assertFalse(appShellSource.contains("LifeLoadRoute"))
  }
}
