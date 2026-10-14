package com.eiel.chat.ui

import android.os.Build
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AdminInstallHelpersTest {
  @Test
  fun requiresUnknownAppSourcesPermissionDoesNotGatePreODevices() {
    assertFalse(requiresUnknownAppSourcesPermission(canRequestPackageInstalls = false, sdkInt = Build.VERSION_CODES.N_MR1))
  }

  @Test
  fun requiresUnknownAppSourcesPermissionRequiresGrantOnOAndAbove() {
    assertTrue(requiresUnknownAppSourcesPermission(canRequestPackageInstalls = false, sdkInt = Build.VERSION_CODES.O))
    assertFalse(requiresUnknownAppSourcesPermission(canRequestPackageInstalls = true, sdkInt = Build.VERSION_CODES.O))
  }

  @Test
  fun buildPackageUriStringUsesPackageScheme() {
    assertEquals("package:com.eiel.chat.debug", buildPackageUriString("com.eiel.chat.debug"))
  }

  @Test
  fun buildUnknownAppSourcesSettingsIntentTargetsCurrentPackage() {
    val spec = buildUnknownAppSourcesSettingsIntentSpec("com.eiel.chat.debug")

    assertEquals(
      UnknownAppSourcesSettingsIntentSpec(
        action = "android.settings.MANAGE_UNKNOWN_APP_SOURCES",
        packageUriString = "package:com.eiel.chat.debug",
        flags = FlagActivityNewTask,
      ),
      spec,
    )
  }

  @Test
  fun buildApkInstallerIntentUsesExplicitInstallAction() {
    val spec = buildApkInstallerIntentSpec()

    assertEquals(
      ApkInstallerIntentSpec(
        action = "android.intent.action.INSTALL_PACKAGE",
        flags = FlagGrantReadUriPermission or FlagActivityNewTask,
      ),
      spec,
    )
  }
}
