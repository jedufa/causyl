package com.eiel.chat.ui

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

class OverviewRouteRefreshSourceTest {
  @Test
  fun overviewRouteRefreshesOperatorSnapshotOnResume() {
    val possibleFiles = listOf(
      "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/OverviewScreen.kt",
      "app/src/main/java/com/eiel/chat/ui/OverviewScreen.kt",
      "src/main/java/com/eiel/chat/ui/OverviewScreen.kt",
      "../app/src/main/java/com/eiel/chat/ui/OverviewScreen.kt",
    )
    val sourceFile = possibleFiles.map(::File).firstOrNull { it.exists() } ?: throw AssertionError(
      "OverviewScreen source path not found",
    )
    val source = sourceFile.readText()

    assertTrue(source.contains("val lifecycleOwner = LocalLifecycleOwner.current"))
    assertTrue(source.contains("DisposableEffect(lifecycleOwner, adminVm)"))
    assertTrue(source.contains("Lifecycle.Event.ON_RESUME"))
    assertTrue(source.contains("adminVm.refreshHealth()"))
    assertTrue(source.contains("adminVm.refreshOperatorSnapshot()"))
  }
}
